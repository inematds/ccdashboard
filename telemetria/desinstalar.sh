#!/usr/bin/env bash
# Remove só o que a telemetria instalou. O banco fica em ~/.local/share/telemetria-agentes/.
set -euo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"
SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
cp -p "$SETTINGS" "$SETTINGS.bak-telemetria-remocao-$(date +%Y%m%d-%H%M%S)"
python3 "$AQUI/ajusta_hooks.py" remover "$SETTINGS" "$AQUI/hook/registra.py"
python3 -m json.tool "$SETTINGS" >/dev/null
echo "hooks removidos"
if [ "${SEM_SYSTEMD:-0}" != 1 ]; then
  systemctl --user disable --now telemetria-painel.service telemetria-manutencao.timer 2>/dev/null || true
  rm -f "$HOME/.config/systemd/user/telemetria-painel.service" "$HOME/.config/systemd/user/telemetria-manutencao."{service,timer}
  systemctl --user daemon-reload
  echo "painel e manutenção removidos"
fi
