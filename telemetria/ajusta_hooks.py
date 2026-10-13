#!/usr/bin/env python3
"""Acrescenta ou remove a telemetria no settings.json do Claude Code.

Uso: ajusta_hooks.py instalar|remover <settings.json> <caminho-do-registra.py>
Hooks: só mexe em grupos cujo comando contém telemetria/hook/registra.py.
OpenTelemetry (T4): acrescenta as variáveis de OTEL_ENV no bloco `env` e, ao
remover, apaga só as que ainda têm o valor posto aqui. O resto fica igual.
"""
import json
import sys

EVENTOS = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse",
           "PostToolUseFailure", "PermissionRequest", "Notification", "Stop",
           "SubagentStart", "SubagentStop", "PreCompact"]
MARCA = "telemetria/hook/registra.py"
OTEL_ENV = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_METRICS_EXPORTER": "none",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318",
    "OTEL_LOGS_EXPORT_INTERVAL": "5000",
}


def eh_nosso(grupo):
    return any(MARCA in h.get("command", "") for h in grupo.get("hooks", []))


def main():
    acao, caminho, script = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(caminho) as f:
        cfg = json.load(f)
    hooks = cfg.setdefault("hooks", {})
    for evento in list(hooks):
        hooks[evento] = [g for g in hooks[evento] if not eh_nosso(g)]
        if not hooks[evento]:
            del hooks[evento]
    if acao == "instalar":
        for evento in EVENTOS:
            hooks.setdefault(evento, []).append(
                {"hooks": [{"type": "command", "command": f'python3 "{script}"', "timeout": 3}]}
            )
    if not hooks:
        del cfg["hooks"]
    env = cfg.setdefault("env", {})
    if acao == "instalar" and "--sem-otel" not in sys.argv:
        for k, v in OTEL_ENV.items():
            env.setdefault(k, v)
    else:
        for k, v in OTEL_ENV.items():
            if env.get(k) == v:
                del env[k]
    if not env:
        del cfg["env"]
    with open(caminho, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")


if __name__ == "__main__":
    main()
