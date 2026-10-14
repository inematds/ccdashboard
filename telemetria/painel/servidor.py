#!/usr/bin/env python3
"""Painel da telemetria de agentes.

Escuta em 127.0.0.1 e nos endereços de TELEMETRIA_HOSTS (padrão: a rede local
192.168.2.99) e só atende clientes de TELEMETRIA_REDES. Lê o SQLite em modo
somente leitura e entrega a página (painel.html) e as rotas /api/*.
Também sobe os coletores da T4 (OTLP em 127.0.0.1:4318) e da T5 (Codex).
"""
import ipaddress
import json
import os
import threading
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = os.environ.get(
    "TELEMETRIA_DB",
    os.path.expanduser("~/.local/share/telemetria-agentes/eventos.db"),
)
PORTA = int(os.environ.get("TELEMETRIA_PORTA", "8766"))
PARADA_MIN = int(os.environ.get("TELEMETRIA_PARADA_MIN", "10"))
SETTINGS = os.path.expanduser("~/.claude/settings.json")
PAGINA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "painel.html")
HOOK = "telemetria/hook/registra.py"
HOSTS = [h.strip() for h in os.environ.get("TELEMETRIA_HOSTS", "127.0.0.1,192.168.2.99").split(",") if h.strip()]
REDES = [ipaddress.ip_network(r.strip()) for r in
         os.environ.get("TELEMETRIA_REDES", "127.0.0.0/8,192.168.2.0/24").split(",") if r.strip()]
PORTA_OTLP = int(os.environ.get("TELEMETRIA_PORTA_OTLP", "4318"))


def conectar():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)
    con.row_factory = sqlite3.Row
    return con


def linhas(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def processo_vivo(pid):
    if not pid:
        return None
    try:
        exe = os.path.realpath(f"/proc/{pid}/exe")
    except OSError:
        return False
    return os.path.exists(f"/proc/{pid}") and ("claude" in exe or "node" in exe)


def pids_claude():
    """Processos do Claude Code vivos na máquina (para contar os que ainda não deram sinal)."""
    pids = set()
    for nome in os.listdir("/proc"):
        if nome.isdigit():
            try:
                if "/claude/versions/" in os.path.realpath(f"/proc/{nome}/exe"):
                    pids.add(int(nome))
            except OSError:
                pass
    return pids


PROJETOS_CLAUDE = os.path.expanduser("~/.claude/projects")
_titulos = {}  # sessao -> (caminho, tamanho lido, título)


def titulo_da_sessao(sessao):
    """Título da sessão (o mesmo do /resume), lido na hora do transcript; nada vai para o banco."""
    if not sessao:
        return None
    caminho, lido, titulo = _titulos.get(sessao, (None, 0, None))
    if not caminho:
        import glob
        achados = glob.glob(os.path.join(PROJETOS_CLAUDE, "*", sessao + ".jsonl"))
        if not achados:
            return None
        caminho = achados[0]
    try:
        tamanho = os.path.getsize(caminho)
        if tamanho < lido:
            lido = 0
        with open(caminho, "rb") as f:
            f.seek(lido)
            for linha in f:
                if b'"custom-title"' in linha or b'"ai-title"' in linha:
                    try:
                        d = json.loads(linha)
                    except ValueError:
                        continue
                    t = d.get("customTitle") or d.get("aiTitle")
                    if isinstance(t, str) and t.strip():
                        if d.get("type") == "custom-title" or not titulo or not titulo[1]:
                            titulo = (t.strip()[:90], d.get("type") == "custom-title")
        _titulos[sessao] = (caminho, tamanho, titulo)
    except OSError:
        return None
    return titulo[0] if titulo else None


def inicio_do_dia():
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def api_agora(con):
    agora = time.time()
    sessoes = linhas(
        con,
        "SELECT * FROM sessoes WHERE estado!='encerrada' AND ultimo_ts > ? ORDER BY ultimo_ts DESC",
        (agora - 3 * 86400,),
    )
    pend = linhas(con, "SELECT * FROM pendentes ORDER BY ts")
    subag = linhas(
        con,
        "SELECT sessao, COUNT(DISTINCT agente) n FROM eventos WHERE agente IS NOT NULL AND ts > ? GROUP BY sessao",
        (agora - 300,),
    )
    sub_por = {r["sessao"]: r["n"] for r in subag}
    saida = []
    for s in sessoes:
        vivo = processo_vivo(s["pid"])
        if vivo is False:
            continue  # fechada sem SessionEnd
        rodando = [p for p in pend if p["sessao"] == s["sessao"]]
        parada_ha = agora - s["ultimo_ts"]
        if s["estado"] == "esperando":
            selo = "pede permissão" if s["ultimo_evento"] == "PermissionRequest" else "esperando você"
        elif rodando:
            selo = "processando"
        elif parada_ha > PARADA_MIN * 60:
            selo = "parada"
        else:
            selo = "processando"
        if vivo is None and parada_ha > 3600:
            continue  # sem pid conhecido e sem sinal há 1 h
        saida.append({
            "sessao": s["sessao"][:8],
            "projeto": s["projeto"],
            "repo": s["repo"] if "repo" in s.keys() else None,
            "titulo": titulo_da_sessao(s["sessao"]),
            "origem": s["origem"] or "?",
            "modelo": s["modelo"],
            "selo": selo,
            "desde_s": int(agora - s["inicio"]),
            "ultimo_s": int(parada_ha),
            "ultima": s["ultima_ferramenta"],
            "rodando": [{"o_que": p["programa"] or p["ferramenta"], "ha_s": int(agora - p["ts"]),
                         "subagente": bool(p["agente"])} for p in rodando],
            "subagentes": sub_por.get(s["sessao"], 0),
        })
    conhecidos = {s["pid"] for s in sessoes if s["pid"]}
    sem_sinal = len(pids_claude() - conhecidos)
    contagem = {}
    for s in saida:
        contagem[s["selo"]] = contagem.get(s["selo"], 0) + 1
    return {"sessoes": saida, "contagem": contagem, "total": len(saida), "sem_sinal": sem_sinal}


def api_processamentos(con):
    agora = time.time()
    rodando = linhas(
        con,
        "SELECT p.ts, p.ferramenta, p.programa, p.projeto, p.agente FROM pendentes p ORDER BY p.ts",
    )
    for r in rodando:
        r["ha_s"] = int(agora - r.pop("ts"))
    longos = linhas(
        con,
        "SELECT ts, projeto, ferramenta, programa, duracao_ms, status, erro_tipo FROM eventos"
        " WHERE evento IN ('PostToolUse','PostToolUseFailure') AND ts > ? AND duracao_ms >= 30000"
        " ORDER BY ts DESC LIMIT 100",
        (inicio_do_dia(),),
    )
    return {"rodando": rodando, "longos_hoje": longos}


def api_falhas(con):
    desde = inicio_do_dia()
    por = linhas(
        con,
        "SELECT COALESCE(mcp, ferramenta) alvo, programa, erro_tipo, COUNT(*) n, MAX(ts) ultima FROM eventos"
        " WHERE status='erro' AND ts > ? GROUP BY 1,2,3 ORDER BY n DESC LIMIT 50",
        (desde,),
    )
    taxa = linhas(
        con,
        "SELECT COALESCE(mcp, ferramenta) alvo, COUNT(*) total, SUM(status='erro') erros,"
        " ROUND(100.0*SUM(status='erro')/COUNT(*),1) taxa FROM eventos"
        " WHERE evento IN ('PostToolUse','PostToolUseFailure') AND ts > ? GROUP BY 1"
        " HAVING erros > 0 ORDER BY taxa DESC",
        (desde,),
    )
    return {"por_alvo": por, "taxa": taxa}


def api_uso(con):
    desde = time.time() - 7 * 86400
    base = "SELECT {c} nome, COUNT(*) n FROM eventos WHERE evento='PreToolUse' AND {c} IS NOT NULL AND ts > ? GROUP BY 1 ORDER BY n DESC LIMIT 25"
    return {
        "skills": linhas(con, base.format(c="skill"), (desde,)),
        "mcps": linhas(con, base.format(c="mcp"), (desde,)),
        "subagentes": linhas(
            con,
            "SELECT tipo_agente nome, COUNT(*) n FROM eventos WHERE evento='PreToolUse'"
            " AND ferramenta IN ('Agent','Task') AND ts > ? GROUP BY 1 ORDER BY n DESC LIMIT 25",
            (desde,),
        ),
        "ferramentas": linhas(con, base.format(c="ferramenta"), (desde,)),
        "programas": linhas(con, base.format(c="programa"), (desde,)),
        "projetos": linhas(con, base.format(c="projeto"), (desde,)),
    }


def api_historico(con):
    desde = time.time() - 30 * 86400
    dias = linhas(
        con,
        "SELECT date(ts,'unixepoch','localtime') dia,"
        " COUNT(DISTINCT sessao) sessoes,"
        " SUM(evento IN ('PostToolUse','PostToolUseFailure')) chamadas,"
        " SUM(status='erro') falhas,"
        " SUM(evento='UserPromptSubmit') pedidos"
        " FROM eventos WHERE ts > ? GROUP BY 1 ORDER BY 1",
        (desde,),
    )
    antigos = linhas(
        con,
        "SELECT dia, SUM(chamadas) chamadas, SUM(falhas) falhas FROM resumo_diario"
        " WHERE dia < date('now','localtime','-30 day') GROUP BY dia ORDER BY dia",
    )
    return {"dias": dias, "resumo_antigo": antigos}


def api_saude(con):
    ultimo = con.execute("SELECT MAX(ts) FROM eventos").fetchone()[0]
    por_hora = linhas(
        con,
        "SELECT strftime('%H',ts,'unixepoch','localtime') hora, COUNT(*) n FROM eventos WHERE ts > ? GROUP BY 1 ORDER BY 1",
        (time.time() - 86400,),
    )
    instalados = []
    try:
        with open(SETTINGS) as f:
            hooks = json.load(f).get("hooks", {})
        for evento, grupos in hooks.items():
            if any(HOOK in h.get("command", "") for g in grupos for h in g.get("hooks", [])):
                instalados.append(evento)
    except (OSError, ValueError):
        pass
    tam = lambda p: os.path.getsize(p) if os.path.exists(p) else 0
    return {
        "ultimo_evento_s": int(time.time() - ultimo) if ultimo else None,
        "eventos": con.execute("SELECT COUNT(*) FROM eventos").fetchone()[0],
        "sessoes": con.execute("SELECT COUNT(*) FROM sessoes").fetchone()[0],
        "banco_mb": round((tam(DB) + tam(DB + "-wal")) / 1e6, 2),
        "por_hora": por_hora,
        "hooks_instalados": sorted(instalados),
        "banco": DB,
    }


def api_consumo(con):
    hoje = inicio_do_dia()
    semana = time.time() - 7 * 86400
    soma = ("SUM(COALESCE(entrada,0)) entrada, SUM(COALESCE(saida,0)) saida,"
            " SUM(COALESCE(cache_leitura,0)) cache_leitura, SUM(COALESCE(cache_escrita,0)) cache_escrita,"
            " ROUND(SUM(COALESCE(custo_usd,0)),2) custo_usd, COUNT(*) chamadas")
    return {
        "hoje": linhas(con, f"SELECT fonte, {soma} FROM consumo WHERE ts > ? GROUP BY fonte", (hoje,)),
        "modelos_7d": linhas(con, f"SELECT fonte, COALESCE(modelo,'?') modelo, {soma} FROM consumo WHERE ts > ?"
                                  " GROUP BY 1,2 ORDER BY custo_usd DESC, entrada DESC", (semana,)),
        "projetos_hoje": linhas(con, f"SELECT c.fonte, COALESCE(s.projeto,'?') projeto, {soma} FROM consumo c"
                                     " LEFT JOIN sessoes s ON s.sessao=c.sessao WHERE c.ts > ? GROUP BY 1,2"
                                     " ORDER BY custo_usd DESC, entrada DESC LIMIT 30", (hoje,)),
        "dias": linhas(con, f"SELECT date(ts,'unixepoch','localtime') dia, fonte, {soma} FROM consumo"
                            " WHERE ts > ? GROUP BY 1,2 ORDER BY 1 DESC", (time.time() - 30 * 86400,)),
    }


ROTAS = {
    "/api/consumo": api_consumo,
    "/api/agora": api_agora,
    "/api/processamentos": api_processamentos,
    "/api/falhas": api_falhas,
    "/api/uso": api_uso,
    "/api/historico": api_historico,
    "/api/saude": api_saude,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def responder(self, codigo, corpo, tipo):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        try:
            cliente = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            cliente = None
        if cliente is None or not any(cliente in r for r in REDES):
            return self.responder(403, b"fora da rede permitida", "text/plain")
        caminho = self.path.split("?", 1)[0]
        if caminho in ("/", "/index.html"):
            with open(PAGINA, "rb") as f:
                return self.responder(200, f.read(), "text/html; charset=utf-8")
        if caminho in ROTAS:
            if not os.path.exists(DB):
                return self.responder(503, b'{"erro":"banco ainda nao existe"}', "application/json")
            try:
                con = conectar()
                try:
                    dados = ROTAS[caminho](con)
                finally:
                    con.close()
                return self.responder(200, json.dumps(dados, ensure_ascii=False).encode(), "application/json")
            except sqlite3.Error as e:
                return self.responder(500, json.dumps({"erro": str(e)}).encode(), "application/json")
        self.responder(404, b"nao encontrado", "text/plain")


if __name__ == "__main__":
    import coletores

    if os.environ.get("TELEMETRIA_OTLP", "1") == "1":
        coletores.servidor_otlp(DB, PORTA_OTLP)
    if os.environ.get("TELEMETRIA_CODEX", "1") == "1":
        coletores.laco_codex(DB)
    servidores = []
    for host in HOSTS:
        try:
            servidores.append(ThreadingHTTPServer((host, PORTA), Handler))
            print(f"painel em http://{host}:{PORTA}/", flush=True)
        except OSError as e:
            print(f"não consegui escutar em {host}:{PORTA}: {e}", flush=True)
    if not servidores:
        raise SystemExit(1)
    for srv in servidores[1:]:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    servidores[0].serve_forever()
