#!/usr/bin/env bash
# Instala a telemetria de agentes: hooks no ~/.claude/settings.json (com backup),
# painel local (127.0.0.1:8766) e manutenção de hora em hora (systemd --user).
set -euo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"
SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
BACKUP="$SETTINGS.bak-telemetria-$(date +%Y%m%d-%H%M%S)"
UNID="$HOME/.config/systemd/user"

cp -p "$SETTINGS" "$BACKUP"
python3 "$AQUI/ajusta_hooks.py" instalar "$SETTINGS" "$AQUI/hook/registra.py"
python3 -m json.tool "$SETTINGS" >/dev/null || { cp -p "$BACKUP" "$SETTINGS"; echo "settings.json inválido, backup restaurado"; exit 1; }
echo "hooks instalados; backup em $BACKUP"

if [ "${SEM_SYSTEMD:-0}" != 1 ]; then
  mkdir -p "$UNID"
  for f in "$AQUI"/systemd/*; do
    sed "s#@AQUI@#$AQUI#g" "$f" > "$UNID/$(basename "$f")"
  done
  systemctl --user daemon-reload
  systemctl --user enable --now telemetria-painel.service telemetria-manutencao.timer
  echo "painel: http://127.0.0.1:8766/"
fi
echo "Sessões já abertas só aparecem depois de reabertas."
