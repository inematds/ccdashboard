# Falhas — CC Dashboard

| data | o que quebrou | menor correção | prompt \| infra |
|---|---|---|---|
| 2026-10-13 | painel da telemetria não subiu: porta 8765 já ocupada por outro serviço local (`rsi.server`) | checar a porta com `ss -ltn` antes de fixar; painel movido para 8766 | infra |
