# Plano — CC Dashboard

Plano para montar um sistema que qualquer pessoa consiga usar para ter **painéis ao vivo dentro do Claude** (Claude Dashboards), em duas frentes:

- **Frente A — Negócio:** onde está o dinheiro, o que está atrasado, onde há vazamento.
- **Frente B — Observabilidade do Claude Code:** o que o Claude Code está fazendo, o que falha e quanto custaria em preço de API.
- **Frente C — Telemetria de agentes (local):** painel na própria máquina com todas as sessões do Claude Code e do Codex ao vivo, sem nuvem. Plano em [`telemetria/PLANO.md`](telemetria/PLANO.md).

Status: **plano + kit de prompts**. Nada aqui cria projeto, função ou tabela sozinho; cada fase diz o que a pessoa faz e o que pede ao Claude.

---

## 0. Conceitos que sustentam o plano

| Conceito | O que significa na prática |
|---|---|
| **Dashboard do Claude** | Painel salvo que guarda as **consultas**, não só os números. Ao abrir ou atualizar, ele roda as consultas de novo nos dados conectados. Cada número mostra a consulta e a hora da última atualização. |
| **Conector** | A ligação que deixa o Claude ler um app ou banco (CRM, planilha, Gmail, Supabase...). Sem conector, sem número. |
| **Base central** | Um banco (aqui, Supabase = Postgres hospedado) onde se copiam os registros de vários apps. Só ela permite cruzar dados entre ferramentas e guardar histórico. |
| **Coletor** | Uma função na nuvem que recebe dados enviados por outro sistema e grava no banco. Na Frente B, recebe a telemetria do Claude Code. |
| **Resumo diário (rollup)** | Tabela com totais por dia, recalculada de tempos em tempos. O painel lê o resumo, que é leve e rápido. |

Requisitos: plano pago do Claude (Pro, Max, Team ou Enterprise — o recurso está em beta e conta no uso do plano), uma conta Supabase gratuita para as fases com base central, e Claude Code para a Frente B.

---

## Frente A — Painéis do negócio

### Fase A1 — Painel direto de um app (sem banco)
**Quando:** a resposta está em um único app.
1. Ligar o conector do app em *Customize → Connectors*.
2. Abrir um chat e escolher a saída *Dashboard* (ou pedir "monte um dashboard").
3. Usar o prompt `prompts/01-dashboard-negocio.md`, preenchendo a pergunta, a tabela/objeto e as definições.

**Limite:** lê o estado atual do app; não guarda histórico nem cruza com outros apps.

### Fase A2 — Conferir o número antes de agir
Todo número relevante passa pelo checklist `prompts/02-conferir-numero.md`: data de referência, quais registros entram, se desconta pagamentos parciais/estornos, onde ficam as bordas das faixas. Conferir **um** número contra o app de origem antes de confiar no resto.

### Fase A3 — Base central no Supabase
**Quando:** a pergunta cruza ferramentas (horas trabalhadas × faturas, assinaturas ativas × cobranças, lucro contábil × dinheiro no banco).
1. Criar projeto gratuito no Supabase (500 MB; projeto gratuito pausa após uma semana sem uso).
2. Ligar o conector Supabase no Claude. Para o painel, preferir conector **somente leitura e restrito a um projeto** (endereço do conector com `project_ref=<ref>&read_only=true`).
3. Começar pequeno: **uma pergunta, duas tabelas de origem, uma cópia agendada, um número conferido**.
4. Escrever as definições (como os registros casam, quais datas valem, moeda) num arquivo de conhecimento do Projeto do Claude.
5. Prompt: `prompts/03-base-central.md`.

### Fase A4 — Atualização agendada
O painel só é tão atual quanto a última cópia para o banco. Criar uma **tarefa agendada** (diária ou semanal) que recopia os dados. Prompt: `prompts/05-agendar-atualizacao.md`.

### Fase A5 — Diagnóstico + plano de ação + painel de acompanhamento
Uma rodada de diagnóstico **somente leitura** sobre todos os apps conectados:
1. **Mapa:** lista os sistemas conectados, a conta de cada um (confirmar que não é conta de teste) e os pontos cegos.
2. **Evidência:** varre cada sistema só com leitura, grava o que achou, sem ler corpo de mensagens.
3. **Cruzamento:** só depois de terminar a varredura, procura problemas que se somam entre áreas.
4. **Números:** só custeia o que foi observado; cada valor tem fórmula reproduzível.
5. **Previsão de caixa** (se houver dado financeiro real).
6. **Relatório + plano** priorizado. Para aqui.
7. **Painel de acompanhamento** — só com o "sim" explícito do dono, num schema próprio do banco; os itens do plano viram "feito" quando os números se mexem.

Prompt: `prompts/06-diagnostico-negocio.md`.

---

## Frente B — Observabilidade do Claude Code

### Arquitetura
```
Claude Code ──(OpenTelemetry, http/json)──▶ Coletor (Supabase Edge Function)
                                              │  checa a chave, filtra campos sensíveis
                                              ▼
                                   schema cc_obs: eventos, métricas, spans (brutos, 7 dias)
                                              │  pg_cron a cada 10 min
                                              ▼
                                   cc_obs.resumo_diario (permanente)
                                              │
                                              ▼
                                   Claude Dashboard (consultas só de leitura)
```

### Fase B0 — Pré-requisitos e validação no Linux
- Projeto Supabase dedicado (ou schema isolado `cc_obs`).
- Conector Supabase com **escrita só durante a montagem**; depois trocar para somente leitura.
- **Validar no Linux** (e no Windows): as variáveis são as mesmas, mas o fluxo deve ser testado em cada sistema antes de divulgar como pronto.

### Fase B1 — Banco
- Schema `cc_obs` com três tabelas brutas (`eventos`, `metricas`, `spans`), uma tabela `chaves` (hash da chave de envio por máquina) e `resumo_diario`.
- Função `cc_obs.filtrar()` — **lista de campos permitidos** (não lista de bloqueio): modelo, ferramenta, nome de skill/servidor MCP, contagem de tokens, custo estimado, duração, sucesso/falha, tipo de erro, máquina.
- Descarte obrigatório: texto de prompt, resposta, comando completo, caminho de arquivo, entrada/saída de ferramenta, texto de erro, e-mail do usuário. De comandos de shell guarda só o nome do programa (`git`, `npm`).
- `pg_cron`: rollup a cada 10 min; limpeza diária dos brutos com mais de 7 dias.
- RLS ligado; leitura do painel por um papel só-leitura com `SELECT` em `cc_obs`.

### Fase B2 — Coletor
- Edge Function `cc-coletor` com rotas `/v1/logs`, `/v1/metrics`, `/v1/traces` (OTLP em JSON).
- Autenticação por cabeçalho próprio (`x-cc-key`), comparando o hash; verificação JWT desligada só nessa função.
- Respostas: 200 aceito, 401 chave errada, 415 formato não-JSON.

### Fase B3 — Ligar o Claude Code
Bloco `env` em `~/.claude/settings.json` (modelo em `config/claude-code-otel.example.json`). Depois **fechar e abrir o Claude Code** (ele lê as variáveis só na partida). Teste: um prompt simples numa sessão nova e conferir no log da função.

### Fase B4 — O painel
Cinco abas, todas lendo `resumo_diario` (e os brutos só para "últimas 24h"):

| Aba | Responde |
|---|---|
| Visão geral | Sessões, custo estimado e tokens hoje/semana; tendência de 30 dias |
| Gasto e cache | Custo por modelo; proporção de leitura de cache × criação de cache |
| Ferramentas e MCP | Chamadas por ferramenta/servidor, taxa de falha, duração p50/p95 |
| Skills e subagentes | Uso e custo por skill e por tipo de agente |
| Saúde do coletor | Último envio por máquina, volume por hora, tamanho do banco |

Prompt único da Frente B: `prompts/04-observabilidade-claude-code.md`.

### Fase B5 — Mais máquinas e compartilhamento
- Uma chave por máquina (`host.name` diferente em cada uma).
- Quem recebe o painel precisa de **conexão própria** ao mesmo projeto Supabase; sem isso os cartões ficam vazios.
- Nomes de skills, repositórios e servidores MCP ficam guardados: pensar em quem vê.

### Fase B6 (opcional) — Importar o histórico local
O Claude Code guarda as sessões em `~/.claude/projects/*.jsonl`. Um script local pode extrair só números (modelo, tokens, ferramenta, duração) e enviar ao mesmo `resumo_diario`, para o painel já nascer com meses de histórico. Mesma lista de campos permitidos.

### Fase B7 (opcional) — Alertas
O painel mostra, não avisa. Para aviso (ex.: taxa de falha de uma ferramenta acima de 30%), criar uma rotina agendada que consulta o resumo e manda mensagem (Telegram, e-mail).

---

## Relógios (o que "ao vivo" quer dizer)
1. Claude Code envia a cada ~5–60 s (configurável).
2. Resumo diário recalcula a cada 10 min.
3. Painel roda as consultas quando é aberto/atualizado.
4. Dados de outros apps só estão tão novos quanto a última cópia agendada.

## Riscos e limites
- Recurso em **beta**; nomes de menus podem mudar.
- Custo mostrado é **estimativa em preço de API**; em Pro/Max quem manda é a assinatura.
- Plano grátis do Supabase: 500 MB — vigiar o cartão de tamanho do banco.
- Só Claude Code; o chat do app e o Cowork não entram.
- Painel não substitui ferramenta de BI para análises pesadas.

## Ordem sugerida
B0 → B1 → B2 → B3 → B4 (uma tarde) · depois A1 → A2 → A3 → A4 · A5 quando houver 3+ sistemas conectados · B5–B7 conforme a necessidade.
