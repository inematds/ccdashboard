# 04 — Observabilidade do Claude Code (prompt único)

Cole num chat novo do Claude com o conector **Supabase** ligado (com escrita, só para a montagem).
O Claude escreve o SQL e a função a partir desta especificação. Revise cada passo antes de dar OK.

```text
# Montar o painel de observabilidade do meu Claude Code

Objetivo: o Claude Code envia a telemetria dele (OpenTelemetry, http/json) para um coletor no meu
Supabase; o coletor guarda só campos seguros; um resumo diário alimenta um Dashboard do Claude.

## Regras
- Use minha conexão Supabase. Liste meus projetos e pergunte qual usar.
- Pergunte meu fuso (nome IANA, ex.: America/Sao_Paulo) e um nome curto para esta máquina.
- Antes da primeira mudança, liste todos os passos e espere meu OK. Depois execute em ordem,
  dizendo o que cada um fez.
- Se um passo falhar, pare e mostre o erro com a causa provável. Não desligue segurança para contornar.
- Tudo fica no schema cc_obs. Não toque em nada fora dele.
- O painel só lê. Nunca altera dados.

## Passo 1 — Checagem
Confirme (só leitura) que pg_cron e pgcrypto estão disponíveis. Se já existir schema cc_obs, pare e pergunte.

## Passo 2 — Banco (uma migração chamada cc_obs)
- Tabelas brutas: cc_obs.eventos (logs/eventos), cc_obs.metricas (pontos de métrica), cc_obs.spans (traces).
  Colunas comuns: recebido_em timestamptz, maquina text, sessao text, nome text, atributos jsonb.
  Métricas também com valor numeric; spans com inicio, fim, duracao_ms, status.
- cc_obs.chaves: maquina text, hash text (sha256 da chave), criada_em, ativa bool.
- cc_obs.filtrar(atributos jsonb) returns jsonb — LISTA DE PERMITIDOS. Mantém só:
  model, tool_name, mcp_server_name, mcp_tool_name, skill.name, agent.name, plugin.name,
  type (tipo de token), input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
  cost_usd, duration_ms, success, error_type, decision_type, decision_source, speed, effort,
  query_source, host.name, session.id, terminal.type, service.version.
  Para comando de shell guarda só o primeiro termo (nome do programa) num campo programa.
  Todo o resto é descartado: texto de prompt, resposta, comando completo, caminhos, tool_input,
  saída de ferramenta, texto de erro, user.email, user.account_uuid.
- cc_obs.resumo_diario: dia date (no meu fuso), maquina, modelo, ferramenta, servidor_mcp, skill,
  sessoes, chamadas, falhas, tokens_entrada, tokens_saida, tokens_cache_leitura, tokens_cache_criacao,
  custo_usd, duracao_p50_ms, duracao_p95_ms. Chave única nas dimensões.
- cc_obs.recalcular_resumo(): recalcula os últimos 2 dias a partir dos brutos (upsert).
- pg_cron: recalcular_resumo a cada 10 minutos; limpeza diária apagando brutos com mais de 7 dias
  (o resumo fica para sempre). Avise que a limpeza apaga dados antes de criar o job.
- RLS ligado em todas as tabelas. Crie o papel cc_obs_leitor com USAGE no schema e SELECT nas tabelas.

## Passo 3 — Chave da máquina
Gere uma chave aleatória longa, grave só o hash em cc_obs.chaves e me mostre a chave UMA vez.

## Passo 4 — Coletor (Edge Function cc-coletor)
- Rotas POST /v1/logs, /v1/metrics, /v1/traces, aceitando OTLP em JSON.
- Lê o cabeçalho x-cc-key, compara o sha256 com cc_obs.chaves (ativa). Errado → 401.
  Content-Type que não seja JSON → 415. Rota desconhecida → 404.
- Achata os recursos/escopos do OTLP, passa cada registro por cc_obs.filtrar e grava na tabela certa.
- Verificação JWT desligada só nesta função (a autenticação é a chave).
- Responde 200 rápido; erros de gravação vão para o log da função sem conteúdo do usuário.

## Passo 5 — Configuração do Claude Code
Me entregue o bloco "env" para ~/.claude/settings.json, já preenchido com a URL do projeto,
a chave e o nome da máquina, usando: CLAUDE_CODE_ENABLE_TELEMETRY=1,
CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1, exportadores otlp para métricas, logs e traces,
OTEL_EXPORTER_OTLP_PROTOCOL=http/json, endpoints por sinal (/v1/logs, /v1/metrics, /v1/traces),
OTEL_EXPORTER_OTLP_HEADERS=x-cc-key=<chave>, OTEL_RESOURCE_ATTRIBUTES=host.name=<máquina>.
Explique que é preciso fechar e abrir o Claude Code depois. Diga que OTEL_LOG_TOOL_DETAILS=1 é
opcional: mostra nomes de skills e servidores MCP, e o coletor descarta o resto que ele envia.

## Passo 6 — Teste
Eu vou abrir uma sessão nova e mandar um prompt simples. Depois confira se chegaram registros
em cc_obs.eventos nos últimos 5 minutos e rode cc_obs.recalcular_resumo() uma vez.

## Passo 7 — Dashboard
Monte um Dashboard do Claude com cinco abas, consultas só de leitura, cada cartão com consulta e hora:
1. Visão geral — sessões, custo estimado e tokens de hoje e dos últimos 7 dias; linha de 30 dias.
2. Gasto e cache — custo por modelo; tokens de cache lidos × criados; custo por dia.
3. Ferramentas e MCP — chamadas, % de falha e duração p50/p95 por ferramenta e por servidor MCP.
4. Skills e subagentes — uso e custo por skill e por tipo de agente.
5. Saúde do coletor — último registro por máquina, volume por hora nas últimas 24h, tamanho do banco.
Escreva no topo: "Custo = estimativa em preço de API; em Pro/Max vale a assinatura."

No fim me entregue: o link do painel, uma linha sobre o que cada aba responde e o que veio vazio e por quê.
```

## Se nada chegar

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| Nenhuma chamada no log da função | Claude Code não foi reiniciado ou o bloco está no arquivo errado | Fechar tudo, abrir sessão nova, mandar o teste |
| 401 | Chave errada ou JWT ainda ligado | Conferir `x-cc-key=` + chave; desligar JWT na função |
| 415 | Protocolo não é JSON | `OTEL_EXPORTER_OTLP_PROTOCOL=http/json` |
| 404 | Caminho errado | Cada endpoint termina em `/v1/logs`, `/v1/metrics` ou `/v1/traces` |
| Chega, mas cartões vazios | Resumo ainda não rodou | Esperar 10 min ou rodar `select cc_obs.recalcular_resumo();` |
| Sem cartões de duração | Traces só a partir de sessão nova | Abrir sessão nova e usar um pouco |
| Para descobrir o erro | — | `claude --debug-file /tmp/cc.log` e procurar linhas `[3P telemetry]` |
