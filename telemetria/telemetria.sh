#!/usr/bin/env bash
# Liga, desliga e mostra o estado da telemetria de agentes.
#   ./telemetria.sh ligar     hooks + OpenTelemetry no settings.json, painel e manutenção
#   ./telemetria.sh desligar  tira tudo isso (o banco fica guardado)
#   ./telemetria.sh status    o que está ligado agora
set -euo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"
SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
DB="$HOME/.local/share/telemetria-agentes/eventos.db"

case "${1:-status}" in
  ligar)    "$AQUI/instalar.sh" ;;
  desligar) "$AQUI/desinstalar.sh" ;;
  status)
    python3 - "$SETTINGS" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1]))
hooks = [e for e, gs in cfg.get("hooks", {}).items()
         if any("telemetria/hook/registra.py" in h.get("command", "") for g in gs for h in g.get("hooks", []))]
otel = cfg.get("env", {}).get("OTEL_EXPORTER_OTLP_ENDPOINT") == "http://127.0.0.1:4318"
print(f"hooks no settings.json: {len(hooks)} eventos" if hooks else "hooks no settings.json: desligados")
print("OpenTelemetry (tokens/custo): ligado" if otel else "OpenTelemetry (tokens/custo): desligado")
PY
    for u in telemetria-painel.service telemetria-manutencao.timer; do
      printf '%-30s %s\n' "$u" "$(systemctl --user is-active "$u" 2>/dev/null || true)"
    done
    if [ -f "$DB" ]; then
      echo "banco: $(du -h "$DB" | cut -f1)  eventos na última hora: $(sqlite3 "$DB" "select count(*) from eventos where ts > strftime('%s','now')-3600" 2>/dev/null || echo ?)"
    fi
    echo "painel: http://127.0.0.1:8766/  e  http://192.168.2.99:8766/"
    ;;
  *) echo "uso: $0 ligar|desligar|status"; exit 2 ;;
esac
