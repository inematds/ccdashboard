#!/usr/bin/env python3
"""Acrescenta ou remove os hooks da telemetria no settings.json do Claude Code.

Uso: ajusta_hooks.py instalar|remover <settings.json> <caminho-do-registra.py>
Só mexe em grupos cujo comando contém telemetria/hook/registra.py; o resto do
arquivo fica igual.
"""
import json
import sys

EVENTOS = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse",
           "PostToolUseFailure", "PermissionRequest", "Notification", "Stop",
           "SubagentStart", "SubagentStop", "PreCompact"]
MARCA = "telemetria/hook/registra.py"


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
    with open(caminho, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")


if __name__ == "__main__":
    main()
