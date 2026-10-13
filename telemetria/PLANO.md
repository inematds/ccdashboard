# Plano — Telemetria de agentes (Frente C)

Painel **local** que mostra, ao vivo, tudo o que os agentes estão fazendo na máquina: cada sessão do
Claude Code (terminais, bots, `claude -p`, loops longos, subagentes) e do Codex CLI.

Status: **plano**. Nada instalado ainda.

**Regra de ouro:** tudo fica na máquina. Sem nuvem, sem API, sem conta nova. Guarda **nomes e números**,
nunca o texto de prompt, resposta, comando completo ou conteúdo de arquivo.

---

## 1. Perguntas que o painel responde

| Pergunta | De onde sai |
|---|---|
| Quais sessões estão ativas agora, em que pasta, há quanto tempo? | hooks `SessionStart`/`SessionEnd` + último evento |
| Qual está **esperando resposta minha**? | hook `Notification` / `Stop` sem novo `UserPromptSubmit` |
| Qual está **parada** (sem evento há N min, processo vivo)? | último evento + `pgrep` |
| O que cada uma está fazendo agora? | último `PreToolUse` (ferramenta, skill, MCP) |
| Quais processamentos longos estão rodando e há quanto tempo? | `PreToolUse` Bash sem `PostToolUse` correspondente |
| O que falhou hoje, por ferramenta / skill / MCP? | `PostToolUse` com erro |
| Quais skills, MCPs e subagentes eu uso de verdade? | contagens do histórico |
| Quanto cada sessão consumiu (tokens, custo estimado em preço de API)? | OpenTelemetry (Fase T4) |

## 2. Arquitetura

```
Claude Code (todas as sessões) ─┬─ hooks ──────────────┐
                                └─ OpenTelemetry (T4) ─┤   (só localhost)
Codex CLI ── sessões no disco (~/.codex/sessions) ─────┤
                                                       ▼
                         ~/.local/share/telemetria-agentes/eventos.db  (SQLite, modo WAL)
                                                       │
                                     servidor local 127.0.0.1:8765
                                                       │
                                     painel HTML (atualiza a cada 5 s)
```

Por que funciona para **todas** as sessões: toda sessão do Claude Code lê `~/.claude/settings.json` ao
abrir. Um hook registrado ali vale para qualquer terminal, bot, `claude -p` e subagente.

## 3. Fases

### T0 — Medir antes de mexer (sem instalar nada)
- Contar sessões ativas (`pgrep`), eventos por hora no histórico (`~/.claude/projects/*.jsonl`) e
  estimar o volume de eventos por dia → dimensiona o banco e a retenção.
- Listar os hooks já existentes em `settings.json` para o novo **somar**, não substituir.
- Conferir quais eventos de hook a versão instalada entrega e o formato do JSON de entrada.

### T1 — Coletor por hook
- Script `telemetria/hook/registra.py` (Python puro, sem dependência):
  lê o JSON do hook na entrada padrão → filtra pela **lista de campos permitidos** → grava uma linha
  no SQLite → sai com código 0 **sempre** (nunca bloqueia nem altera a sessão).
- Eventos: `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
  `Notification`, `Stop`, `SubagentStop`, `PreCompact`.
- Tetos: `timeout` 2 s no hook; gravação com `busy_timeout` curto; se o banco estiver travado,
  descarta o evento (perder uma linha é melhor que travar uma sessão).
- Instalação: script `telemetria/instalar.sh` que **acrescenta** os hooks em `settings.json`
  (com backup datado) e `desinstalar.sh` que remove só os dele.

**Campos permitidos** (o resto é descartado):

| Campo | Exemplo |
|---|---|
| hora, evento | `2026-10-13T10:02:11`, `PostToolUse` |
| sessão, sessão-mãe | id da sessão; subagente aponta para a mãe |
| pasta do projeto | só o nome da pasta (`ccdashboard`), não o caminho |
| origem | `terminal`, `headless`, `bot`, `subagente` (pela variável de ambiente / pai do processo) |
| ferramenta | `Bash`, `Edit`, `Skill`, `mcp__<servidor>__<ferramenta>` |
| programa do Bash | só a primeira palavra (`ffmpeg`, `git`, `python3`) |
| skill / tipo de subagente | nome |
| duração, sucesso/falha | ms; `ok`/`erro` |
| tipo do erro | categoria curta (`timeout`, `exit≠0`, `negado`), sem o texto |

**Nunca grava:** prompt, resposta, comando completo, caminho de arquivo, entrada/saída de ferramenta,
texto de erro, e-mail, tokens/chaves.

### T2 — Banco local
- Tabelas: `eventos` (bruto, 30 dias), `sessoes` (uma linha por sessão, estado atual),
  `resumo_diario` (permanente).
- Limpeza diária dos brutos com mais de 30 dias (timer `systemd --user`).
- Tamanho esperado: medido na T0; alvo < 200 MB.

### T3 — Painel local
- `telemetria/painel/servidor.py` (Python, só `http.server` + `sqlite3`) escutando **só em
  127.0.0.1**, serviço `systemd --user` com `MemoryMax`.
- Página única, padrão INEMA (escuro/âmbar), atualização a cada 5 s:

| Aba | Mostra |
|---|---|
| **Agora** | sessões ativas: pasta, origem, tempo, ferramenta atual; selo **esperando você** / **parada há N min** / **processando** |
| **Processamentos** | comandos longos em andamento e concluídos hoje (programa, duração, resultado) |
| **Falhas** | erros do dia por ferramenta, skill e MCP; taxa de falha; ligação com `FALHAS.md` |
| **Uso** | skills, MCPs e subagentes mais usados; o que nunca é usado |
| **Histórico** | sessões e ferramentas por dia (30 dias) |
| **Saúde** | último evento recebido, tamanho do banco, hooks instalados |

### T4 — OpenTelemetry local (tokens e custo)
- Receptor OTLP em `127.0.0.1:4318` dentro do mesmo servidor, gravando no mesmo SQLite.
- Bloco `env` em `~/.claude/settings.json` apontando para `http://127.0.0.1:4318`
  (mesmas variáveis de `config/claude-code-otel.example.json`, sem chave e sem nuvem).
- Vale só para sessões abertas **depois** da mudança.
- Custo exibido = **estimativa em preço de API**; na assinatura quem manda é o plano.

### T5 — Codex
- Leitor das sessões do Codex em `~/.codex/sessions/` (arquivos JSONL), mesma lista de campos.
- Verificar se a versão instalada do Codex CLI exporta OpenTelemetry; se exportar, usar o mesmo
  receptor da T4.

### T6 — Histórico antigo (opcional)
- Importar `~/.claude/projects/*.jsonl` (só números) para o painel já nascer com meses de dados.

### T7 — Avisos (opcional)
- Rotina a cada 5 min: sessão esperando resposta há mais de 15 min, ferramenta com taxa de falha
  acima de 30%, processamento rodando há mais de 1 h → mensagem no bot.

### T8 — Ponte para o Claude Dashboards (opcional)
- Mesmo esquema no Supabase (Frente B do `PLANO.md`) quando quiser ver o painel no app do Claude ou de
  outra máquina. Exige autorização explícita de uso do Supabase.

## 4. Critérios de pronto (cada fase)

| Fase | Comando → resultado esperado |
|---|---|
| T1 | abrir sessão nova, rodar 1 `ls` → `sqlite3 eventos.db "select evento,ferramenta from eventos order by id desc limit 3"` mostra `PostToolUse Bash` |
| T1 | hook com banco travado (`flock` no arquivo) → a sessão segue normal, evento descartado, sem atraso > 2 s |
| T1 | `grep` no banco por um trecho de prompt digitado no teste → **zero** ocorrências |
| T1 | `desinstalar.sh` → `settings.json` igual ao backup, hooks antigos intactos |
| T3 | `curl -s 127.0.0.1:8765/api/agora` lista as sessões ativas; contagem bate com `pgrep` (±1) |
| T3 | o painel não abre por outro IP da rede |
| T4 | sessão nova → aparecem tokens e modelo no painel em até 60 s |
| T5 | uma execução `codex exec` aparece na aba **Agora** / **Histórico** |

## 5. Riscos e limites
- **Hook em todo evento:** precisa ser rápido (alvo < 50 ms). Medir na T1 com 100 eventos.
- **Hooks globais valem para todos os projetos:** instalação só com "sim" do dono; desinstalador testado.
- **Nomes de skills, MCPs e pastas ficam no banco:** é local, mas não compartilhar o arquivo.
- **Eventos de hook mudam entre versões do Claude Code:** o coletor ignora campo desconhecido e
  registra o evento mesmo assim.
- **"Parada" é inferência:** sem evento + processo vivo pode ser só um comando longo; a aba mostra o
  último comando para a pessoa julgar.

## 6. Ordem sugerida
T0 → T1 → T2 → T3 (uma tarde, já entrega o painel "Agora") · T4 · T5 · depois T6–T8 conforme a necessidade.
