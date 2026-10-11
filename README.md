# 📊 CC Dashboard — painéis ao vivo no Claude

[![CC Dashboard](guia/assets/banner.jpg)](https://inematds.github.io/ccdashboard/guia/)

**🇧🇷 [Português](README.md) · 🇺🇸 [English](README.en.md) · 🇪🇸 [Español](README.es.md)**

## O que é

O CC Dashboard é um kit em português para usar os Dashboards do Claude, painéis que guardam as consultas e refazem as contas sempre que você abre. Serve para donos de pequenos negócios e para quem usa o Claude Code todo dia. Ele traz prompts prontos para montar painéis do negócio (dinheiro, atrasos, vazamentos) e um plano para medir o próprio Claude Code (custo estimado, ferramentas, falhas). Para usar, você precisa de um plano pago do Claude e, nas partes com banco de dados, de uma conta gratuita no Supabase.

## 📖 Guia de uso

Guia completo (landing + passo a passo): **https://inematds.github.io/ccdashboard/guia/**

## Como funciona

Um Dashboard do Claude não grava números: grava **consultas**. Toda vez que você abre ou atualiza, ele roda as consultas de novo nos dados conectados. Clicando num número, você vê a consulta e a hora da última atualização.

**Negócio**

```
Apps (CRM, planilha, banco) → Conector no Claude → [Base central no Supabase] → Dashboard → Cópia agendada
```

- Uma pergunta que cabe num app só: conector + prompt e pronto.
- Uma pergunta que cruza apps (horas × faturas, assinaturas × cobranças): os registros precisam ser copiados para uma base central (Supabase = Postgres hospedado), que guarda histórico e permite cruzar.

**Claude Code**

```
Claude Code (OpenTelemetry) → Coletor no Supabase → Tabelas brutas (7 dias) → Resumo diário (10 min) → Dashboard
```

- O Claude Code envia a própria telemetria (modelo, tokens, custo estimado, ferramentas, duração, falhas).
- O coletor guarda **só campos de uma lista permitida** e descarta texto de prompts e respostas, comandos completos, caminhos, entradas e saídas de ferramentas, texto de erro e e-mail. Do shell, guarda só o nome do programa.
- Um resumo diário é recalculado a cada 10 minutos; os brutos são apagados após 7 dias.

## Como usar

| Passo | O que fazer | Arquivo |
|---|---|---|
| 1 | Ligar o conector do app em *Customize → Connectors* | — |
| 2 | Pedir o painel com pergunta, definições e "não altere dados" | `prompts/01-dashboard-negocio.md` |
| 3 | Conferir um número na origem antes de agir | `prompts/02-conferir-numero.md` |
| 4 | Cruzar apps numa base central no Supabase | `prompts/03-base-central.md` |
| 5 | Agendar a atualização da base | `prompts/05-agendar-atualizacao.md` |
| 6 | Diagnóstico do negócio → plano → painel de acompanhamento | `prompts/06-diagnostico-negocio.md` |
| 7 | Montar o painel do Claude Code (banco, coletor, abas) | `prompts/04-observabilidade-claude-code.md` |
| 8 | Ligar a telemetria no `~/.claude/settings.json` e **reiniciar** o Claude Code | `config/claude-code-otel.example.json` |

Supabase só leitura e restrito a um projeto (conector personalizado):

```
https://mcp.supabase.com/mcp?project_ref=SEU_PROJECT_REF&read_only=true
```

Se nada chegar no coletor: `claude --debug-file /tmp/cc.log` e procure linhas `[3P telemetry]`. A tabela de sintomas (401, 404, 415, cartões vazios) está no fim de `prompts/04-observabilidade-claude-code.md`.

## Pré-requisitos

- Plano pago do Claude: Pro, Max, Team ou Enterprise (recurso em beta, conta no uso do plano).
- Conta Supabase grátis (500 MB; projeto grátis pausa após uma semana sem uso) — para base central e Claude Code.
- Claude Code — só para o painel de observabilidade.

## Limites

- **Mostra, não avisa.** Alerta exige uma rotina agendada separada.
- **Custo é estimativa** em preço de API; em Pro/Max vale a assinatura.
- **Cada pessoa precisa da própria conexão** ao projeto Supabase para ver os cartões.
- **Nomes ficam guardados** (skills, repositórios, servidores MCP) — pense em quem vê.
- **Só Claude Code**; o chat do app e o Cowork não entram.
- **Teste no seu sistema** (Linux, Windows, Mac) antes de confiar no fluxo.

## Estrutura

```
PLANO.md                               plano por fases (Frente A negócio, Frente B Claude Code)
prompts/01-dashboard-negocio.md        painel a partir de um app
prompts/02-conferir-numero.md          checklist para conferir um número
prompts/03-base-central.md             base central no Supabase
prompts/04-observabilidade-claude-code.md  prompt único do painel do Claude Code + diagnóstico
prompts/05-agendar-atualizacao.md      cópia agendada
prompts/06-diagnostico-negocio.md      diagnóstico só leitura → plano → acompanhamento
config/claude-code-otel.example.json   bloco env do Claude Code
guia/                                  página landing + guia (PT, EN, ES)
```

## Status

Plano + kit de prompts. O painel do Claude Code ainda não foi montado de ponta a ponta a partir deste kit; trate a primeira execução como piloto e confira cada passo antes de dar OK ao Claude.

---

[INEMA.CLUB](https://inema.club)
