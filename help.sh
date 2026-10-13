#!/usr/bin/env bash
# Ajuda do CC Dashboard: o que há no kit e os comandos da telemetria de agentes.
#   ./help.sh            esta ajuda
#   ./help.sh status     ajuda + estado atual da telemetria
#   ./help.sh ligar      liga a telemetria (atalho para telemetria/telemetria.sh ligar)
#   ./help.sh desligar   desliga a telemetria
set -euo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"
TEL="$AQUI/telemetria/telemetria.sh"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

case "${1:-}" in
  ligar|desligar) exec "$TEL" "$1" ;;
esac

cat <<EOF
CC Dashboard — ajuda
====================

Kit (Dashboards do Claude)
  PLANO.md                 plano por fases (A negócio, B Claude Code no Supabase, C telemetria local)
  prompts/01..06-*.md      prompts prontos para colar no Claude
  guia/                    guia em PT, EN e ES: https://inematds.github.io/ccdashboard/guia/

Telemetria de agentes (Frente C, tudo local, sem nuvem)
  Mostra ao vivo todas as sessões do Claude Code e do Codex nesta máquina:
  o que está processando, o que espera você, o que falhou, tokens e custo estimado.

  Ligar / desligar / ver estado
    ./help.sh ligar                       hooks + OpenTelemetry no ~/.claude/settings.json, painel e manutenção
    ./help.sh desligar                    tira tudo isso (o banco fica guardado)
    telemetria/telemetria.sh status       o que está ligado agora

  Painel
    http://127.0.0.1:8766/                nesta máquina
    http://${IP:-<ip-da-maquina>}:8766/            na rede local (configurar em TELEMETRIA_HOSTS / TELEMETRIA_REDES)
    abas: Agora · Processamentos · Consumo · Falhas · Uso · Histórico · Saúde

  Só pausar o painel (hooks continuam gravando)
    systemctl --user stop  telemetria-painel.service
    systemctl --user start telemetria-painel.service

  Onde ficam as coisas
    banco      ~/.local/share/telemetria-agentes/eventos.db  (30 dias de eventos + resumo diário)
    serviços   telemetria-painel.service, telemetria-manutencao.timer  (systemd --user)
    detalhes   telemetria/PLANO.md  (testes, custo de máquina, ligar/desligar)

  O que NÃO é guardado: texto de prompt, resposta, comando completo, caminho de arquivo,
  saída de ferramenta. Só nomes (projeto, ferramenta, skill, programa) e números.
EOF

if [ "${1:-}" = "status" ]; then
  echo
  echo "Estado agora"
  echo "------------"
  "$TEL" status
fi
