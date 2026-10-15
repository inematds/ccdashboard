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


def comandos_em_fundo():
    """pid do Claude -> comandos que ele deixou em segundo plano (run_in_background).

    Assinatura: filho direto do processo do Claude com a saída indo para .../tasks/<id>.output.
    """
    import re
    try:
        with open("/proc/stat") as f:
            btime = next(int(l.split()[1]) for l in f if l.startswith("btime"))
        tick = os.sysconf("SC_CLK_TCK")
    except (OSError, StopIteration, ValueError):
        return {}
    agora = time.time()
    saida = {}
    for nome in os.listdir("/proc"):
        if not nome.isdigit():
            continue
        try:
            saida_fd = os.readlink(f"/proc/{nome}/fd/1")
            if "/tasks/" not in saida_fd or not saida_fd.endswith(".output"):
                continue
            with open(f"/proc/{nome}/stat") as f:
                campos = f.read().rsplit(")", 1)[1].split()
            ppid, inicio = int(campos[1]), btime + int(campos[19]) / tick
            with open(f"/proc/{nome}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode(errors="replace")
        except (OSError, ValueError, IndexError):
            continue
        m = re.search(r"eval '(.*?)' < /dev/null", cmd, re.S)
        cmd = m.group(1) if m else cmd.split("eval '", 1)[-1]
        cmd = cmd.replace("'\\''", "'").replace(HOME, "~").strip()
        saida.setdefault(ppid, []).append({"o_que": cmd[:200], "ha_s": int(agora - inicio), "tipo": "comando"})
    return saida


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


def trabalho_em_fundo(con, agora):
    """O que cada sessão deixou trabalhando sozinho: comandos em segundo plano (por pid),
    subagentes ainda sem SubagentStop e runs do systemd-run lançados da pasta da sessão (por sessão)."""
    por_sessao = {}
    ultimo = {}
    for r in linhas(con, "SELECT sessao, agente, tipo_agente, evento, ts FROM eventos"
                         " WHERE agente IS NOT NULL AND ts > ? ORDER BY ts", (agora - 3600,)):
        a = ultimo.setdefault(r["agente"], {"sessao": r["sessao"], "tipo": None, "inicio": r["ts"]})
        a.update(evento=r["evento"], ts=r["ts"], tipo=r["tipo_agente"] or a["tipo"])
    for a in ultimo.values():
        if a["evento"] != "SubagentStop" and agora - a["ts"] < 600:
            por_sessao.setdefault(a["sessao"], []).append(
                {"o_que": "subagente " + (a["tipo"] or ""), "ha_s": int(agora - a["inicio"]), "tipo": "subagente"})
    try:
        for r in api_rotinas(None)["rodando"]:
            if r.get("run") and r.get("sessao_id"):
                por_sessao.setdefault(r["sessao_id"], []).append(
                    {"o_que": r["unidade"] + " · " + r["comando"], "ha_s": r["ha_s"], "tipo": "systemd-run"})
    except Exception:
        pass
    return {"pid": comandos_em_fundo(), "sessao": por_sessao}


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
    fundo = trabalho_em_fundo(con, agora)
    saida = []
    for s in sessoes:
        vivo = processo_vivo(s["pid"])
        if vivo is False:
            continue  # fechada sem SessionEnd
        rodando = [p for p in pend if p["sessao"] == s["sessao"]]
        parada_ha = agora - s["ultimo_ts"]
        cmds = sorted(fundo["pid"].get(s["pid"], []), key=lambda f: -f["ha_s"])
        n_bash = sum(1 for p in rodando if p["ferramenta"] == "Bash")  # Bash em primeiro plano também cai em tasks/
        em_fundo = cmds[:max(0, len(cmds) - n_bash)] + fundo["sessao"].get(s["sessao"], [])
        if s["estado"] == "esperando" and s["ultimo_evento"] == "PermissionRequest":
            selo = "pede permissão"
        elif s["estado"] == "esperando" and em_fundo:
            selo = "em segundo plano"
        elif s["estado"] == "esperando":
            selo = "esperando você"
        elif rodando:
            selo = "processando"
        elif parada_ha > PARADA_MIN * 60:
            selo = "parada"
        else:
            selo = "processando"
        if vivo is None and parada_ha > 3600:
            continue  # sem pid conhecido e sem sinal há 1 h
        saida.append({
            "id": s["sessao"],
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
            "fundo": em_fundo,
        })
    conhecidos = {s["pid"] for s in sessoes if s["pid"]}
    sem_sinal = len(pids_claude() - conhecidos)
    contagem = {}
    for s in saida:
        contagem[s["selo"]] = contagem.get(s["selo"], 0) + 1
    return {"sessoes": saida, "contagem": contagem, "total": len(saida), "sem_sinal": sem_sinal}


def info_processo(pid):
    """cwd e linha de comando do processo da sessão, se ainda vivo."""
    if not pid or not os.path.exists(f"/proc/{pid}"):
        return None
    info = {"pid": pid}
    try:
        info["cwd"] = os.readlink(f"/proc/{pid}/cwd").replace(HOME, "~")
    except OSError:
        pass
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            info["cmd"] = f.read().replace(b"\0", b" ").decode(errors="replace").strip()[:300]
    except OSError:
        pass
    try:
        with open(f"/proc/{pid}/status") as f:
            for linha in f:
                if linha.startswith("VmRSS:"):
                    info["rss_mb"] = round(int(linha.split()[1]) / 1024)
    except (OSError, ValueError):
        pass
    return info


def api_sessao(con, sid=""):
    """Tudo o que o banco sabe de uma sessão (id completo ou prefixo)."""
    agora = time.time()
    if not sid or len(sid) < 8:
        return {"erro": "id da sessão ausente"}
    s = con.execute("SELECT * FROM sessoes WHERE sessao LIKE ? ORDER BY ultimo_ts DESC LIMIT 1",
                    (sid + "%",)).fetchone()
    if not s:
        return {"erro": "sessão não encontrada"}
    s = dict(s)
    sid = s["sessao"]
    um = lambda sql, a=(): con.execute(sql, a).fetchone()
    tot = dict(um(
        "SELECT SUM(evento='UserPromptSubmit') pedidos,"
        " SUM(evento IN ('PostToolUse','PostToolUseFailure')) chamadas,"
        " SUM(status='erro') erros, SUM(COALESCE(duracao_ms,0)) duracao_ms,"
        " COUNT(DISTINCT agente) subagentes, MIN(ts) primeiro, COUNT(*) eventos"
        " FROM eventos WHERE sessao=?", (sid,)))
    soma = ("SUM(COALESCE(entrada,0)) entrada, SUM(COALESCE(saida,0)) saida,"
            " SUM(COALESCE(cache_leitura,0)) cache_leitura, SUM(COALESCE(cache_escrita,0)) cache_escrita,"
            " ROUND(SUM(COALESCE(custo_usd,0)),2) custo_usd, COUNT(*) chamadas")
    consumo = linhas(con, f"SELECT COALESCE(modelo,'?') modelo, {soma} FROM consumo WHERE sessao=?"
                          " GROUP BY 1 ORDER BY custo_usd DESC, entrada DESC", (sid,))
    ferr = linhas(con, "SELECT ferramenta nome, COUNT(*) n, SUM(status='erro') erros,"
                       " ROUND(SUM(COALESCE(duracao_ms,0))/1000.0) seg FROM eventos"
                       " WHERE sessao=? AND evento IN ('PostToolUse','PostToolUseFailure') AND ferramenta IS NOT NULL"
                       " GROUP BY 1 ORDER BY n DESC LIMIT 20", (sid,))
    base = ("SELECT {c} nome, COUNT(*) n FROM eventos WHERE sessao=? AND evento='PreToolUse'"
            " AND {c} IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT 15")
    pend = linhas(con, "SELECT ts, ferramenta, programa, agente FROM pendentes WHERE sessao=? ORDER BY ts", (sid,))
    for p in pend:
        p["ha_s"] = int(agora - p.pop("ts"))
    eventos = linhas(con, "SELECT ts, evento, ferramenta, programa, skill, mcp, tipo_agente, duracao_ms, status,"
                          " erro_tipo, detalhe, agente FROM eventos WHERE sessao=? ORDER BY ts DESC LIMIT 60", (sid,))
    erros = linhas(con, "SELECT ts, COALESCE(mcp, ferramenta) alvo, programa, erro_tipo FROM eventos"
                        " WHERE sessao=? AND status='erro' ORDER BY ts DESC LIMIT 20", (sid,))
    vivo = processo_vivo(s["pid"])
    titulo_da_sessao(sid)
    transcript = _titulos.get(sid, (None,))[0]
    return {
        "sessao": s, "titulo": titulo_da_sessao(sid), "agora": agora,
        "vivo": vivo, "processo": info_processo(s["pid"]) if vivo else None,
        "transcript": transcript.replace(HOME, "~") if transcript else None,
        "transcript_mb": round(os.path.getsize(transcript) / 1e6, 1) if transcript and os.path.exists(transcript) else None,
        "totais": tot, "consumo": consumo, "ferramentas": ferr,
        "programas": linhas(con, base.format(c="programa"), (sid,)),
        "skills": linhas(con, base.format(c="skill"), (sid,)),
        "mcps": linhas(con, base.format(c="mcp"), (sid,)),
        "subagentes": linhas(con, "SELECT tipo_agente nome, COUNT(*) n FROM eventos WHERE sessao=? AND evento='PreToolUse'"
                                  " AND ferramenta IN ('Agent','Task') GROUP BY 1 ORDER BY n DESC", (sid,)),
        "rodando": pend, "eventos": eventos, "erros": erros,
        "fundo": (lambda f, n: sorted(f["pid"].get(s["pid"], []), key=lambda x: -x["ha_s"])[:max(0, len(f["pid"].get(s["pid"], [])) - n)]
                  + f["sessao"].get(sid, []))(trabalho_em_fundo(con, agora), sum(1 for p in pend if p["ferramenta"] == "Bash")),
    }


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


HOME = os.path.expanduser("~")
PROJETOS = os.path.join(HOME, "projetos") + "/"
_rotinas_cache = [0, None]


def projeto_do_texto(texto):
    """Primeira pasta de ~/projetos citada no texto (ExecStart, WorkingDirectory, linha do cron)."""
    if not texto or PROJETOS not in texto:
        return None
    return texto.split(PROJETOS, 1)[1].split("/", 1)[0].split()[0][:60] or None


def sessao_do_texto(texto):
    """Caminho de scratchpad de sessão do Claude (/tmp/claude-N/-home-...-projetos-X/<uuid>/) -> (projeto, título)."""
    import re
    m = re.search(r"/tmp/claude-\d+/-[^/]*?-projetos-([^/]+)/([0-9a-f]{8}-[0-9a-f-]{27})/", texto or "")
    if not m:
        return None, None
    return m.group(1)[:60], titulo_da_sessao(m.group(2))


def systemctl(*args):
    import subprocess
    r = subprocess.run(["systemctl", "--user", "--no-pager", *args], capture_output=True, text=True, timeout=10)
    return r.stdout


def propriedades(unidades, campos):
    if not unidades:
        return {}
    saida, atual = {}, {}
    for linha in (systemctl("show", "-p", ",".join(campos), *unidades) + "\n").splitlines():
        if not linha.strip():
            if atual.get("Id"):
                saida[atual["Id"]] = atual
            atual = {}
            continue
        k, _, v = linha.partition("=")
        atual[k] = atual[k] + "\n" + v if k in atual and k.startswith("Timers") else v
    return saida


def ts_systemd(valor):
    """'Wed 2026-10-14 00:59:23 -03' -> epoch."""
    try:
        return time.mktime(time.strptime(" ".join(valor.split()[1:3]), "%Y-%m-%d %H:%M:%S"))
    except (ValueError, IndexError):
        return None


def proximo_cron(expr, agora):
    """Próxima execução para linhas 'm h * * dow' (o que existe no crontab). Outras formas: None."""
    try:
        m, h, dia, mes, dow = expr.split()[:5]
        if dia != "*" or mes != "*" or not m.isdigit() or not h.isdigit():
            return None
        dias = None if dow == "*" else {int(x) % 7 for x in dow.split(",")}
        t = time.localtime(agora)
        for d in range(8):
            base = time.mktime((t.tm_year, t.tm_mon, t.tm_mday + d, int(h), int(m), 0, 0, 0, -1))
            semana = (time.localtime(base).tm_wday + 1) % 7  # cron: 0 = domingo
            if base > agora and (dias is None or semana in dias):
                return base
    except (ValueError, IndexError):
        pass
    return None


def api_rotinas(_con):
    agora = time.time()
    if _rotinas_cache[1] and agora - _rotinas_cache[0] < 10:
        return _rotinas_cache[1]
    # Serviços rodando que são nossos (unit em ~/.config/systemd/user ou transitória), não os do desktop.
    rodando_ids = [json_u["unit"] for json_u in json.loads(
        systemctl("list-units", "--type=service", "--state=running", "-o", "json") or "[]")]
    props = propriedades(rodando_ids, ["Id", "Description", "FragmentPath", "ExecStart", "WorkingDirectory",
                                       "ActiveEnterTimestamp", "MainPID"])
    rodando = []
    for uid, p in props.items():
        frag = p.get("FragmentPath", "")
        if not (frag.startswith(os.path.join(HOME, ".config/systemd/user")) or frag.startswith("/run/user/")):
            continue
        cmd = p.get("ExecStart", "")
        argv = cmd.split("argv[]=", 1)[1].split(" ; ", 1)[0] if "argv[]=" in cmd else cmd
        inicio = ts_systemd(p.get("ActiveEnterTimestamp", ""))
        proj_sessao, titulo = sessao_do_texto(argv)
        import re
        m_sid = re.search(r"/tmp/claude-\d+/[^/]+/([0-9a-f]{8}-[0-9a-f-]{27})/", argv)
        rodando.append({
            "sessao_id": m_sid.group(1) if m_sid else None,
            "unidade": uid.removesuffix(".service"),
            "o_que": p.get("Description", "")[:160],
            "comando": argv.replace(HOME, "~")[:200],
            "projeto": projeto_do_texto(p.get("WorkingDirectory", "")) or projeto_do_texto(argv) or proj_sessao,
            "sessao_titulo": titulo,
            "run": frag.startswith("/run/user/"),
            "desde": inicio,
            "ha_s": int(agora - inicio) if inicio else None,
        })
    rodando.sort(key=lambda r: -(r["desde"] or 0))
    # Timers do systemd.
    timers = json.loads(systemctl("list-timers", "--all", "-o", "json") or "[]")
    tprops = propriedades([t["activates"] for t in timers if t.get("activates")],
                          ["Id", "Description", "ExecStart", "WorkingDirectory", "ActiveState", "FragmentPath"])
    agendados = []
    for t in timers:
        sp = tprops.get(t.get("activates"), {})
        if sp.get("FragmentPath", "").startswith("/usr/") or t["unit"].startswith("snap."):
            continue
        nxt = t["next"] / 1e6 if t.get("next") else None
        ult = t["last"] / 1e6 if t.get("last") else None
        agendados.append({
            "unidade": t["unit"].removesuffix(".timer"),
            "o_que": sp.get("Description", "")[:140],
            "projeto": projeto_do_texto(sp.get("WorkingDirectory", "")) or projeto_do_texto(sp.get("ExecStart", "")),
            "proxima": nxt, "ultima": ult,
            "rodando_agora": sp.get("ActiveState") == "activating" or sp.get("ActiveState") == "active",
            "fonte": "systemd",
        })
    # Crontab.
    import subprocess
    cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5).stdout
    for linha in cron.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" in linha.split()[0]:
            continue
        partes = linha.split(None, 5)
        if len(partes) < 6:
            continue
        expr, comando = " ".join(partes[:5]), partes[5]
        script = next((os.path.basename(w) for w in comando.split()
                       if "/" in w and w.endswith((".sh", ".py", ".mjs", ".js"))), None)
        agendados.append({
            "unidade": script or comando.split()[0][-40:],
            "o_que": "cron " + expr,
            "projeto": projeto_do_texto(comando),
            "proxima": proximo_cron(expr, agora), "ultima": None,
            "rodando_agora": False, "fonte": "cron",
        })
    agendados.sort(key=lambda r: r["proxima"] or 9e12)
    resultado = {"rodando": rodando, "agendados": agendados, "agora": agora}
    _rotinas_cache[:] = [agora, resultado]
    return resultado


AGENDA_HORAS = 24
_agenda_cache = [0, None]
_calendario_cache = {}  # spec -> (ts, [epochs])


def proximas_calendario(spec, agora, limite):
    """Execuções de um OnCalendar até `limite`, pelo systemd-analyze (cache de 5 min por expressão)."""
    import re
    import subprocess
    em_cache = _calendario_cache.get(spec)
    if not em_cache or agora - em_cache[0] > 300:
        r = subprocess.run(["systemd-analyze", "calendar", "--iterations=60", spec],
                           capture_output=True, text=True, timeout=10)
        lista = []
        for d in re.findall(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", r.stdout):
            try:
                lista.append(time.mktime(time.strptime(d, "%Y-%m-%d %H:%M:%S")))
            except ValueError:
                pass
        em_cache = (agora, sorted(set(lista)))
        _calendario_cache[spec] = em_cache
    return [t for t in em_cache[1] if agora < t <= limite]


def segundos_systemd(valor):
    """'1h 30min' / '10min' / '45s' -> segundos."""
    import re
    fat = {"us": 1e-6, "ms": 1e-3, "s": 1, "sec": 1, "min": 60, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    total = 0
    for n, u in re.findall(r"([\d.]+)\s*([a-z]+)", valor or ""):
        total += float(n) * fat.get(u, 0)
    return total or None


DIAS_PT = {"Mon": "seg", "Tue": "ter", "Wed": "qua", "Thu": "qui", "Fri": "sex", "Sat": "sáb", "Sun": "dom"}


def horario_legivel(spec):
    """OnCalendar do systemd -> 'todo dia 05:30', 'seg–sex 08:00', 'dom 15:00', '15/10 10:05 (uma vez)'."""
    import re
    m = re.match(r"^(?:([A-Za-z.,]+)\s+)?(\S+)\s+([\d,]+):(\d\d)(?::\d\d)?$", spec.strip())
    if not m:
        return spec
    dow, data, hs, mi = m.groups()
    horas = ", ".join(f"{int(h):02d}:{mi}" for h in hs.split(","))
    if dow:
        dias = "–".join(DIAS_PT.get(d, d) for d in dow.split("..")) if ".." in dow else ", ".join(DIAS_PT.get(d, d) for d in dow.split(","))
        return f"{dias} {horas}"
    if data in ("*-*-*", "*-*-* "):
        return f"todo dia {horas}"
    md = re.match(r"\d{4}-(\d\d)-(\d\d)$", data)
    return f"{md.group(2)}/{md.group(1)} {horas} (uma vez)" if md else f"{data} {horas}"


def cron_legivel(expr):
    """'30 8 * * *' -> 'todo dia 08:30'; '0 9 * * 1-5' -> 'seg–sex 09:00'."""
    p = expr.split()
    if len(p) != 5 or not p[0].isdigit() or not all(h.isdigit() for h in p[1].split(",")) or p[2] != "*" or p[3] != "*":
        return "cron " + expr
    horas = ", ".join(f"{int(h):02d}:{int(p[0]):02d}" for h in p[1].split(","))
    nomes = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
    if p[4] == "*":
        return f"todo dia {horas}"
    if "-" in p[4]:
        a, b = p[4].split("-")
        return f"{nomes[int(a)]}–{nomes[int(b)]} {horas}"
    return ", ".join(nomes[int(d)] for d in p[4].split(",")) + f" {horas}"


def nome_curto(descr, projeto):
    """'eventosextremospub — Clima do DIA (boletim...), diário…' -> 'Clima do DIA'."""
    import re
    d = descr
    if projeto and d.lower().startswith(projeto.lower()):
        d = d[len(projeto):].lstrip(" —-:")
    return re.split(r"\s\(|,\s|:\s", d, maxsplit=1)[0].strip()[:60] or descr[:60]


def chave_hora(texto):
    import re
    m = re.search(r"(\d\d):(\d\d)", texto)
    return m.group(0) if m else "99"


def api_agenda(_con):
    """Só o que tem execução prevista: cada disparo das próximas AGENDA_HORAS, em ordem de hora."""
    import re
    import subprocess
    agora = time.time()
    if _agenda_cache[1] and agora - _agenda_cache[0] < 30:
        return _agenda_cache[1]
    limite = agora + AGENDA_HORAS * 3600
    previstos, recorrentes, grade = [], [], []
    timers = json.loads(systemctl("list-timers", "--all", "-o", "json") or "[]")
    tprops = propriedades([t["activates"] for t in timers if t.get("activates")],
                          ["Id", "Description", "ExecStart", "WorkingDirectory", "FragmentPath", "ActiveState"])
    tcal = propriedades([t["unit"] for t in timers], ["Id", "TimersCalendar", "TimersMonotonic"])
    for t in timers:
        sp = tprops.get(t.get("activates"), {})
        if sp.get("FragmentPath", "").startswith("/usr/") or t["unit"].startswith("snap."):
            continue
        item = {
            "unidade": t["unit"].removesuffix(".timer"),
            "o_que": sp.get("Description", "")[:140],
            "projeto": projeto_do_texto(sp.get("WorkingDirectory", "")) or projeto_do_texto(sp.get("ExecStart", "")),
            "fonte": "systemd",
        }
        tp = tcal.get(t["unit"], {})
        specs = [s.strip() for s in re.findall(r"OnCalendar=([^;]+?)\s;", tp.get("TimersCalendar", ""))]
        if specs:
            hl = "; ".join(horario_legivel(x) for x in specs)
            grade.append({"projeto": item["projeto"], "unidade": item["unidade"], "horario": hl,
                          "nome": nome_curto(item["o_que"], item["projeto"]), "parado": not t.get("next") and sp.get("ActiveState") != "activating",
                          "rodando": sp.get("ActiveState") in ("activating", "active"),
                          "proxima": t["next"] / 1e6 if t.get("next") else None})
        vezes = set()
        for spec in specs:
            vezes.update(proximas_calendario(spec, agora, limite))
            item.setdefault("regra", spec)
        intervalo = None
        for chave, valor in re.findall(r"(OnUnitActiveUSec|OnUnitInactiveUSec|OnActiveUSec)=([^;]+?)\s;",
                                       tp.get("TimersMonotonic", "")):
            intervalo = segundos_systemd(valor)
            item.setdefault("regra", "a cada " + valor.strip())
        nxt = t["next"] / 1e6 if t.get("next") else None
        if intervalo and intervalo <= 3600:
            recorrentes.append({**item, "proxima": nxt, "intervalo_s": intervalo})
            continue
        if intervalo and nxt:
            k = nxt
            while k <= limite:
                vezes.add(k)
                k += intervalo
        if not vezes and nxt and agora < nxt <= limite:
            vezes.add(nxt)
        for v in vezes:
            previstos.append({**item, "quando": v})
    cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5).stdout
    for linha in cron.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" in linha.split()[0]:
            continue
        partes = linha.split(None, 5)
        if len(partes) < 6:
            continue
        expr, comando = " ".join(partes[:5]), partes[5]
        script = next((os.path.basename(w) for w in comando.split()
                       if "/" in w and w.endswith((".sh", ".py", ".mjs", ".js"))), None)
        item = {"unidade": script or comando.split()[0][-40:], "o_que": comando.replace(HOME, "~")[:140],
                "projeto": projeto_do_texto(comando), "fonte": "cron", "regra": "cron " + expr}
        k = proximo_cron(expr, agora)
        grade.append({"projeto": item["projeto"], "unidade": item["unidade"], "horario": cron_legivel(expr),
                      "nome": item["unidade"], "parado": False, "proxima": k, "cron": True})
        if k is None:
            recorrentes.append({**item, "proxima": None, "intervalo_s": None})
        while k and k <= limite:
            previstos.append({**item, "quando": k})
            k = proximo_cron(expr, k)
    previstos.sort(key=lambda r: r["quando"])
    recorrentes.sort(key=lambda r: (r["intervalo_s"] or 9e9, r["unidade"]))
    grade.sort(key=lambda r: ((r["projeto"] or "~").lower(), chave_hora(r["horario"]), r["unidade"]))
    resultado = {"agora": agora, "horas": AGENDA_HORAS, "previstos": previstos, "recorrentes": recorrentes, "grade": grade}
    _agenda_cache[:] = [agora, resultado]
    return resultado


ROTAS = {
    "/api/agenda": api_agenda,
    "/api/sessao": api_sessao,
    "/api/consumo": api_consumo,
    "/api/agora": api_agora,
    "/api/processamentos": api_processamentos,
    "/api/falhas": api_falhas,
    "/api/uso": api_uso,
    "/api/historico": api_historico,
    "/api/saude": api_saude,
    "/api/rotinas": api_rotinas,
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
        from urllib.parse import parse_qs
        caminho, _, consulta = self.path.partition("?")
        params = {k: v[0] for k, v in parse_qs(consulta).items()}
        if caminho in ("/", "/index.html"):
            with open(PAGINA, "rb") as f:
                return self.responder(200, f.read(), "text/html; charset=utf-8")
        if caminho in ROTAS:
            if not os.path.exists(DB):
                return self.responder(503, b'{"erro":"banco ainda nao existe"}', "application/json")
            try:
                con = conectar()
                try:
                    if caminho == "/api/sessao":
                        dados = api_sessao(con, params.get("id", ""))
                    else:
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
