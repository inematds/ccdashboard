#!/usr/bin/env python3
"""Coletor por hook da telemetria de agentes (Frente C).

Lê o JSON que o Claude Code entrega ao hook, guarda só os campos permitidos num
SQLite local e sai com código 0 sempre. Nunca escreve na saída padrão: em
SessionStart e UserPromptSubmit o que sai ali entra no contexto da sessão.

Nunca grava: prompt, resposta, comando completo, caminho de arquivo, entrada ou
saída de ferramenta, texto de erro.
"""
import json
import os
import signal
import sqlite3
import sys
import time

DB = os.environ.get(
    "TELEMETRIA_DB",
    os.path.expanduser("~/.local/share/telemetria-agentes/eventos.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS eventos (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  evento TEXT NOT NULL,
  sessao TEXT,
  agente TEXT,
  tipo_agente TEXT,
  projeto TEXT,
  origem TEXT,
  ferramenta TEXT,
  mcp TEXT,
  skill TEXT,
  programa TEXT,
  tool_use_id TEXT,
  duracao_ms INTEGER,
  status TEXT,
  erro_tipo TEXT,
  detalhe TEXT
);
CREATE INDEX IF NOT EXISTS eventos_ts ON eventos(ts);
CREATE INDEX IF NOT EXISTS eventos_sessao ON eventos(sessao);
CREATE TABLE IF NOT EXISTS sessoes (
  sessao TEXT PRIMARY KEY,
  inicio REAL,
  ultimo_ts REAL,
  ultimo_evento TEXT,
  ultima_ferramenta TEXT,
  projeto TEXT,
  origem TEXT,
  modelo TEXT,
  pid INTEGER,
  estado TEXT,
  fim REAL
);
CREATE TABLE IF NOT EXISTS pendentes (
  tool_use_id TEXT PRIMARY KEY,
  sessao TEXT,
  agente TEXT,
  ts REAL,
  ferramenta TEXT,
  programa TEXT,
  projeto TEXT
);
CREATE TABLE IF NOT EXISTS resumo_diario (
  dia TEXT,
  projeto TEXT,
  ferramenta TEXT,
  chamadas INTEGER,
  falhas INTEGER,
  duracao_total_ms INTEGER,
  PRIMARY KEY (dia, projeto, ferramenta)
);
"""

ESTADO = {
    "SessionStart": "esperando",
    "UserPromptSubmit": "processando",
    "PreToolUse": "processando",
    "PostToolUse": "processando",
    "PostToolUseFailure": "processando",
    "SubagentStart": "processando",
    "SubagentStop": "processando",
    "PreCompact": "processando",
    "PostCompact": "processando",
    "PermissionRequest": "esperando",
    "Notification": "esperando",
    "Stop": "esperando",
    "SessionEnd": "encerrada",
}

INICIAIS_IGNORADAS = {"cd", "export", "source", ".", "set", "time", "nice", "timeout", "env", "sudo"}


def curto(valor, limite=60):
    """Nome curto e seguro: só texto simples, sem quebras."""
    if not isinstance(valor, str):
        return None
    valor = valor.strip().splitlines()[0] if valor.strip() else ""
    return valor[:limite] or None


def programa_do_bash(comando):
    """Só o nome do primeiro programa útil do comando (ffmpeg, git, python3)."""
    if not isinstance(comando, str):
        return None
    texto = comando.replace("&&", ";").replace("||", ";").replace("|", ";")
    for trecho in texto.split(";"):
        palavras = trecho.split()
        while palavras and ("=" in palavras[0] and not palavras[0].startswith("=")):
            palavras.pop(0)  # VAR=valor antes do comando
        if not palavras:
            continue
        nome = os.path.basename(palavras[0].strip("\"'()"))
        if nome in INICIAIS_IGNORADAS:
            if nome in ("timeout", "nice", "time", "env", "sudo") and len(palavras) > 1:
                for p in palavras[1:]:
                    if not p.startswith("-") and not p[0].isdigit() and "=" not in p:
                        return os.path.basename(p.strip("\"'"))[:40]
            continue
        return nome[:40] or None
    return None


def tipo_do_erro(dados):
    if dados.get("is_interrupt"):
        return "interrompido"
    erro = dados.get("error")
    texto = (erro if isinstance(erro, str) else json.dumps(erro or "")).lower()
    if "timed out" in texto or "timeout" in texto:
        return "timeout"
    if "denied" in texto or "permission" in texto or "rejected" in texto:
        return "negado"
    if "exit code" in texto:
        return "exit≠0"
    if "not found" in texto or "no such file" in texto:
        return "nao-encontrado"
    return "outro"


def ler_proc(pid, nome):
    try:
        with open(f"/proc/{pid}/{nome}", "rb") as f:
            return f.read()
    except OSError:
        return b""


def pai(pid):
    stat = ler_proc(pid, "stat").decode(errors="ignore")
    try:
        return int(stat.rsplit(")", 1)[1].split()[1])
    except (IndexError, ValueError):
        return 0


def processo_claude():
    """Sobe a árvore de processos até achar o Claude Code que chamou o hook."""
    pid = os.getppid()
    for _ in range(6):
        if pid <= 1:
            break
        exe = os.path.realpath(f"/proc/{pid}/exe") if os.path.exists(f"/proc/{pid}/exe") else ""
        cmd = ler_proc(pid, "cmdline").split(b"\0")
        if "/claude/versions/" in exe or (cmd and os.path.basename(cmd[0].decode(errors="ignore")) == "claude"):
            return pid, [c.decode(errors="ignore") for c in cmd if c]
        pid = pai(pid)
    return None, []


def origem_da_sessao(pid, cmd):
    if not pid:
        return "desconhecida"
    if any(c in ("-p", "--print") for c in cmd):
        return "headless"
    ancestral = pai(pid)
    for _ in range(5):
        if ancestral <= 1:
            break
        linha = ler_proc(ancestral, "cmdline").replace(b"\0", b" ").decode(errors="ignore")
        if "bot" in linha.lower():
            return "bot"
        ancestral = pai(ancestral)
    try:
        if os.isatty(0) or os.readlink(f"/proc/{pid}/fd/0").startswith("/dev/pts"):
            return "terminal"
    except OSError:
        pass
    return "outro"


def principal():
    dados = json.loads(sys.stdin.read() or "{}")
    evento = curto(dados.get("hook_event_name"), 40) or "desconhecido"
    agora = time.time()
    sessao = curto(dados.get("session_id"), 80)
    agente = curto(dados.get("agent_id"), 80)
    tipo_agente = curto(dados.get("agent_type"), 60)
    cwd = dados.get("cwd")
    projeto = os.path.basename(os.path.normpath(cwd))[:60] if isinstance(cwd, str) and cwd else None

    ferramenta = curto(dados.get("tool_name"), 120)
    entrada = dados.get("tool_input") if isinstance(dados.get("tool_input"), dict) else {}
    mcp = skill = programa = None
    if ferramenta and ferramenta.startswith("mcp__"):
        partes = ferramenta.split("__")
        mcp = partes[1][:60] if len(partes) > 1 else None
    if ferramenta == "Skill":
        skill = curto(entrada.get("skill"), 60)
    if ferramenta in ("Agent", "Task"):
        tipo_agente = tipo_agente or curto(entrada.get("subagent_type"), 60) or "general-purpose"
    if ferramenta == "Bash":
        programa = programa_do_bash(entrada.get("command"))
    tool_use_id = curto(dados.get("tool_use_id"), 80)

    status = erro_tipo = detalhe = None
    if evento == "PostToolUse":
        status = "ok"
    elif evento == "PostToolUseFailure":
        status = "erro"
        erro_tipo = tipo_do_erro(dados)
    elif evento == "Notification":
        detalhe = curto(dados.get("notification_type"), 40)
    elif evento in ("SessionStart", "SessionEnd"):
        detalhe = curto(dados.get("source") or dados.get("reason"), 40)
    modelo = curto(dados.get("model"), 60) if isinstance(dados.get("model"), str) else None

    os.makedirs(os.path.dirname(DB), exist_ok=True)
    novo = not os.path.exists(DB)
    con = sqlite3.connect(DB, timeout=0.3, isolation_level=None)
    if novo:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("BEGIN IMMEDIATE")

    duracao = None
    if tool_use_id and evento in ("PostToolUse", "PostToolUseFailure"):
        linha = con.execute("SELECT ts FROM pendentes WHERE tool_use_id=?", (tool_use_id,)).fetchone()
        if linha:
            duracao = int((agora - linha[0]) * 1000)
            con.execute("DELETE FROM pendentes WHERE tool_use_id=?", (tool_use_id,))
    if isinstance(dados.get("duration_ms"), (int, float)):
        duracao = int(dados["duration_ms"])

    sessao_existe = sessao and con.execute("SELECT 1 FROM sessoes WHERE sessao=?", (sessao,)).fetchone()
    origem = None
    pid = None
    if sessao and not sessao_existe or evento == "SessionStart":
        pid, cmd = processo_claude()
        origem = origem_da_sessao(pid, cmd)
    if agente:
        origem = "subagente"

    con.execute(
        "INSERT INTO eventos (ts, evento, sessao, agente, tipo_agente, projeto, origem, ferramenta, mcp,"
        " skill, programa, tool_use_id, duracao_ms, status, erro_tipo, detalhe)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (agora, evento, sessao, agente, tipo_agente, projeto, origem, ferramenta, mcp, skill,
         programa, tool_use_id, duracao, status, erro_tipo, detalhe),
    )

    if evento == "PreToolUse" and tool_use_id:
        con.execute(
            "INSERT OR REPLACE INTO pendentes (tool_use_id, sessao, agente, ts, ferramenta, programa, projeto)"
            " VALUES (?,?,?,?,?,?,?)",
            (tool_use_id, sessao, agente, agora, ferramenta, programa, projeto),
        )

    if sessao:
        # Evento de subagente mantém a sessão-mãe "processando", sem trocar a origem dela.
        estado = ESTADO.get(evento, "processando")
        if agente and estado == "esperando":
            estado = "processando"
        rotulo = programa or skill or ferramenta
        if not sessao_existe:
            con.execute(
                "INSERT INTO sessoes (sessao, inicio, ultimo_ts, ultimo_evento, ultima_ferramenta, projeto,"
                " origem, modelo, pid, estado, fim) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (sessao, agora, agora, evento, rotulo, projeto,
                 origem if origem != "subagente" else None, modelo, pid, estado,
                 agora if estado == "encerrada" else None),
            )
        else:
            con.execute(
                "UPDATE sessoes SET ultimo_ts=?, ultimo_evento=?, estado=?,"
                " ultima_ferramenta=COALESCE(?, ultima_ferramenta), projeto=COALESCE(projeto, ?),"
                " modelo=COALESCE(?, modelo), pid=COALESCE(?, pid),"
                " origem=CASE WHEN ? IS NOT NULL AND ? != 'subagente' THEN ? ELSE origem END,"
                " fim=CASE WHEN ?='encerrada' THEN ? ELSE NULL END WHERE sessao=?",
                (agora, evento, estado, rotulo, projeto, modelo, pid,
                 origem, origem, origem, estado, agora, sessao),
            )
        if evento == "SessionEnd":
            con.execute("DELETE FROM pendentes WHERE sessao=?", (sessao,))
    con.execute("COMMIT")
    con.close()


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, lambda *_: os._exit(0))
    signal.alarm(1)
    try:
        sys.stdout = open(os.devnull, "w")
        principal()
    except Exception:
        pass
    os._exit(0)
