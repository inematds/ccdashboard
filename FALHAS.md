# Falhas — CC Dashboard

| data | o que quebrou | menor correção | prompt \| infra |
|---|---|---|---|
| 2026-10-14 | título/repo na aba Agora não apareceram: a edição do `painel.html` estava no mesmo comando que um `curl`, o context-mode barrou o comando todo e eu commitei sem a página | edição de arquivo nunca no mesmo comando de teste; conferir com `grep -c` + print da página antes do commit | infra + prompt |
| 2026-10-13 | painel da telemetria não subiu: porta 8765 já ocupada por outro serviço local (`rsi.server`) | checar a porta com `ss -ltn` antes de fixar; painel movido para 8766 | infra |
