"""Coletores que rodam dentro do serviço do painel (T4 e T5).

T4 — receptor OpenTelemetry (OTLP http/json) só em 127.0.0.1:4318. O Claude Code
     manda um evento `api_request` por chamada ao modelo; daqui sai tokens, cache,
     custo estimado (preço de API) e modelo. Erros de API viram evento `ApiError`.
T5 — leitor das sessões do Codex em ~/.codex/sessions (JSONL), lido aos poucos
     (guarda a posição de cada arquivo). Sessão, turnos, comandos e tokens.

Mesma regra do hook: só nomes e números. Nunca texto de prompt, resposta,
comando completo, caminho de arquivo ou saída de ferramenta.
"""
import glob
import importlib.util
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("registra", os.path.join(AQUI, "..", "hook", "registra.py"))
registra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(registra)

CODEX_DIR = os.path.expanduser(os.environ.get("TELEMETRIA_CODEX_DIR", "~/.codex/sessions"))
CODEX_JANELA_DIAS = 2

SCHEMA_EXTRA = """
CREATE TABLE IF NOT EXISTS consumo (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  fonte TEXT NOT NULL,
  sessao TEXT,
  modelo TEXT,
  entrada INTEGER,
  saida INTEGER,
  cache_leitura INTEGER,
  cache_escrita INTEGER,
  raciocinio INTEGER,
  custo_usd REAL,
  duracao_ms INTEGER
);
CREATE INDEX IF NOT EXISTS consumo_ts ON consumo(ts);
CREATE INDEX IF NOT EXISTS consumo_sessao ON consumo(sessao);
CREATE TABLE IF NOT EXISTS codex_arquivos (
  caminho TEXT PRIMARY KEY,
  posicao INTEGER,
  sessao TEXT,
  projeto TEXT,
  modelo TEXT
);
"""


def abrir(db):
    os.makedirs(os.path.dirname(db), exist_ok=True)
    novo = not os.path.exists(db)
    con = sqlite3.connect(db, timeout=5, isolation_level=None)
    if novo:
        con.execute("PRAGMA journal_mode=WAL")
    con.executescript(registra.SCHEMA + SCHEMA_EXTRA)
    return con


def inteiro(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- T4 · OTLP

def atributos(lista):
    saida = {}
    for a in lista or []:
        v = a.get("value", {})
        for tipo in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if tipo in v:
                saida[a.get("key")] = v[tipo]
                break
    return saida


def ingerir_logs(con, corpo):
    linhas = erros = 0
    for rl in corpo.get("resourceLogs", []):
        recurso = atributos(rl.get("resource", {}).get("attributes"))
        if "claude" not in str(recurso.get("service.name", "")).lower():
            continue  # outro programa herdou as variáveis OTEL_*: ignora
        for sl in rl.get("scopeLogs", []):
            for reg in sl.get("logRecords", []):
                at = atributos(reg.get("attributes"))
                nome = str(at.get("event.name") or reg.get("body", {}).get("stringValue") or "")
                nome = nome.rsplit(".", 1)[-1]
                ts = inteiro(reg.get("timeUnixNano")) or 0
                ts = ts / 1e9 if ts else time.time()
                sessao = registra.curto(str(at.get("session.id") or ""), 80)
                modelo = registra.curto(str(at.get("model") or ""), 60)
                if nome == "api_request":
                    try:
                        custo = float(at.get("cost_usd")) if at.get("cost_usd") is not None else None
                    except ValueError:
                        custo = None
                    con.execute(
                        "INSERT INTO consumo (ts, fonte, sessao, modelo, entrada, saida, cache_leitura,"
                        " cache_escrita, raciocinio, custo_usd, duracao_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (ts, "claude", sessao, modelo, inteiro(at.get("input_tokens")),
                         inteiro(at.get("output_tokens")), inteiro(at.get("cache_read_tokens")),
                         inteiro(at.get("cache_creation_tokens")), None, custo, inteiro(at.get("duration_ms"))),
                    )
                    if sessao and modelo:
                        con.execute("UPDATE sessoes SET modelo=? WHERE sessao=?", (modelo, sessao))
                    linhas += 1
                elif nome == "api_error":
                    codigo = registra.curto(str(at.get("status_code") or "?"), 10)
                    con.execute(
                        "INSERT INTO eventos (ts, evento, sessao, ferramenta, status, erro_tipo, duracao_ms)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (ts, "ApiError", sessao, "API " + (modelo or ""), "erro", f"api {codigo}",
                         inteiro(at.get("duration_ms"))),
                    )
                    erros += 1
    return linhas, erros


def servidor_otlp(db, porta=4318):
    class Otlp(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            tamanho = int(self.headers.get("Content-Length") or 0)
            corpo = self.rfile.read(min(tamanho, 20_000_000))
            caminho = self.path.split("?", 1)[0]
            if "json" not in (self.headers.get("Content-Type") or ""):
                self.send_response(415)
                self.end_headers()
                return
            if caminho == "/v1/logs":
                try:
                    con = abrir(db)
                    con.execute("BEGIN IMMEDIATE")
                    ingerir_logs(con, json.loads(corpo or b"{}"))
                    con.execute("COMMIT")
                    con.close()
                except (ValueError, sqlite3.Error):
                    pass  # perder um lote é melhor que travar o Claude Code
            # /v1/metrics e /v1/traces: aceitos e descartados
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

    srv = ThreadingHTTPServer(("127.0.0.1", porta), Otlp)
    threading.Thread(target=srv.serve_forever, daemon=True, name="otlp").start()
    return srv


# ---------------------------------------------------------------- T5 · Codex

CMD_RE = re.compile(r'\bcmd\s*:\s*"((?:[^"\\]|\\.)*)"')


def quando(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return time.time()


def programa_codex(p):
    entrada = p.get("input") if p.get("type") == "custom_tool_call" else p.get("arguments")
    if not isinstance(entrada, str):
        return None
    if p.get("type") == "function_call":
        try:
            args = json.loads(entrada)
            cmd = args.get("cmd") or args.get("command")
            if isinstance(cmd, list):
                cmd = " ".join(str(c) for c in cmd)
            return registra.programa_do_bash(cmd)
        except ValueError:
            return None
    m = CMD_RE.search(entrada)
    return registra.programa_do_bash(m.group(1)) if m else None


def ler_codex_arquivo(con, caminho):
    linha_reg = con.execute("SELECT posicao, sessao, projeto, modelo FROM codex_arquivos WHERE caminho=?",
                            (caminho,)).fetchone()
    posicao, sessao, projeto, modelo = linha_reg or (0, None, None, None)
    tamanho = os.path.getsize(caminho)
    if tamanho <= posicao:
        return 0
    n = 0
    with open(caminho, "rb") as f:
        f.seek(posicao)
        for bruto in f:
            if not bruto.endswith(b"\n"):
                break  # linha ainda sendo escrita
            posicao += len(bruto)
            try:
                d = json.loads(bruto)
            except ValueError:
                continue
            tipo = d.get("type")
            p = d.get("payload") or {}
            ts = quando(d.get("timestamp"))
            sub = p.get("type")
            if tipo == "session_meta":
                sessao = registra.curto(p.get("session_id") or p.get("id"), 80)
                cwd = p.get("cwd")
                projeto = os.path.basename(os.path.normpath(cwd))[:60] if isinstance(cwd, str) and cwd else None
                origem = "codex-" + (p.get("source") if isinstance(p.get("source"), str) else "cli")
                con.execute(
                    "INSERT OR IGNORE INTO sessoes (sessao, inicio, ultimo_ts, ultimo_evento, projeto, origem, estado)"
                    " VALUES (?,?,?,?,?,?,?)", (sessao, ts, ts, "SessionStart", projeto, origem[:30], "esperando"))
                registrar(con, ts, "SessionStart", sessao, projeto, origem[:30])
            elif not sessao:
                continue
            elif tipo == "turn_context":
                modelo = registra.curto(p.get("model"), 60) or modelo
                con.execute("UPDATE sessoes SET modelo=? WHERE sessao=?", (modelo, sessao))
            elif sub == "task_started":
                registrar(con, ts, "UserPromptSubmit", sessao, projeto, estado="processando")
            elif sub in ("task_complete", "turn_aborted"):
                # `codex exec` acaba no fim da tarefa; o Codex interativo fica esperando você
                origem = (con.execute("SELECT origem FROM sessoes WHERE sessao=?", (sessao,)).fetchone() or [""])[0]
                registrar(con, ts, "Stop", sessao, projeto,
                          estado="encerrada" if origem == "codex-exec" else "esperando",
                          status="erro" if sub == "turn_aborted" else None,
                          erro_tipo="interrompido" if sub == "turn_aborted" else None)
            elif sub in ("custom_tool_call", "function_call"):
                nome = "codex:" + (registra.curto(p.get("name"), 40) or "?")
                prog = programa_codex(p)
                cid = registra.curto(p.get("call_id"), 80)
                registrar(con, ts, "PreToolUse", sessao, projeto, ferramenta=nome, programa=prog,
                          tool_use_id=cid, estado="processando")
                if cid:
                    con.execute("INSERT OR REPLACE INTO pendentes (tool_use_id, sessao, ts, ferramenta, programa, projeto)"
                                " VALUES (?,?,?,?,?,?)", (cid, sessao, ts, nome, prog, projeto))
            elif sub in ("custom_tool_call_output", "function_call_output"):
                cid = registra.curto(p.get("call_id"), 80)
                pend = con.execute("SELECT ts, ferramenta, programa FROM pendentes WHERE tool_use_id=?", (cid,)).fetchone()
                if pend:
                    con.execute("DELETE FROM pendentes WHERE tool_use_id=?", (cid,))
                    registrar(con, ts, "PostToolUse", sessao, projeto, ferramenta=pend[1], programa=pend[2],
                              tool_use_id=cid, duracao=int((ts - pend[0]) * 1000), status="ok", estado="processando")
            elif tipo == "token_usage_record":
                u = p.get("usage") or {}
                con.execute(
                    "INSERT INTO consumo (ts, fonte, sessao, modelo, entrada, saida, cache_leitura, cache_escrita,"
                    " raciocinio, custo_usd) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (ts, "codex", sessao, modelo, inteiro(u.get("input_tokens")), inteiro(u.get("output_tokens")),
                     inteiro(u.get("cached_input_tokens")), inteiro(u.get("cache_write_input_tokens")),
                     inteiro(u.get("reasoning_output_tokens")), None))
            n += 1
    con.execute("INSERT OR REPLACE INTO codex_arquivos (caminho, posicao, sessao, projeto, modelo) VALUES (?,?,?,?,?)",
                (caminho, posicao, sessao, projeto, modelo))
    return n


def registrar(con, ts, evento, sessao, projeto, origem=None, ferramenta=None, programa=None,
              tool_use_id=None, duracao=None, status=None, erro_tipo=None, estado=None):
    con.execute(
        "INSERT INTO eventos (ts, evento, sessao, projeto, origem, ferramenta, programa, tool_use_id,"
        " duracao_ms, status, erro_tipo) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (ts, evento, sessao, projeto, origem, ferramenta, programa, tool_use_id, duracao, status, erro_tipo))
    if estado:
        con.execute(
            "UPDATE sessoes SET ultimo_ts=MAX(ultimo_ts, ?), ultimo_evento=?, estado=?,"
            " ultima_ferramenta=COALESCE(?, ultima_ferramenta),"
            " fim=CASE WHEN ?='encerrada' THEN ? ELSE NULL END WHERE sessao=?",
            (ts, evento, estado, programa or ferramenta, estado, ts, sessao))


def varrer_codex(db):
    limite = time.time() - CODEX_JANELA_DIAS * 86400
    arquivos = [f for f in glob.glob(os.path.join(CODEX_DIR, "**", "*.jsonl"), recursive=True)
                if os.path.getmtime(f) >= limite]
    if not arquivos:
        return 0
    con = abrir(db)
    total = 0
    for caminho in arquivos:
        try:
            con.execute("BEGIN IMMEDIATE")
            total += ler_codex_arquivo(con, caminho)
            con.execute("COMMIT")
        except (OSError, sqlite3.Error):
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
    # sessão `codex exec` terminada: some da aba Agora depois de 2 min sem sinal
    con.execute("UPDATE sessoes SET estado='encerrada', fim=ultimo_ts WHERE origem='codex-exec'"
                " AND estado='esperando' AND ultimo_ts < ?", (time.time() - 120,))
    con.close()
    return total


def laco_codex(db, intervalo=15):
    def rodar():
        while True:
            try:
                varrer_codex(db)
            except Exception:
                pass
            time.sleep(intervalo)
    threading.Thread(target=rodar, daemon=True, name="codex").start()
