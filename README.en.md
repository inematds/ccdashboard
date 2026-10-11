# 📊 CC Dashboard — live dashboards in Claude

[![CC Dashboard](guia/assets/banner-en.jpg)](https://inematds.github.io/ccdashboard/guia/en/)

**🇧🇷 [Português](README.md) · 🇺🇸 [English](README.en.md) · 🇪🇸 [Español](README.es.md)**

## What it is

CC Dashboard is a kit for using Claude Dashboards, panels that save their queries and redo the math every time you open them. It is meant for small business owners and for people who use Claude Code every day. It brings ready-made prompts to build business dashboards (money, overdue items, leaks) and a plan to measure Claude Code itself (estimated cost, tools, failures). To use it, you need a paid Claude plan and, for the parts with a database, a free Supabase account.

> Note: the prompt files (`prompts/*.md`) and `PLANO.md` are written in Portuguese.

## 📖 User guide

Full guide (landing + step by step): **https://inematds.github.io/ccdashboard/guia/en/**

## How it works

A Claude Dashboard does not store numbers: it stores **queries**. Every time you open or refresh it, it reruns the queries on the connected data. By clicking a number, you see the query and the time of the last update.

**Business**

```
Apps (CRM, spreadsheet, database) → Connector in Claude → [Central database in Supabase] → Dashboard → Scheduled copy
```

- A question that fits in a single app: connector + prompt and you are done.
- A question that combines apps (hours × invoices, subscriptions × charges): the records must be copied into a central database (Supabase = hosted Postgres), which keeps history and allows combining them.

**Claude Code**

```
Claude Code (OpenTelemetry) → Collector in Supabase → Raw tables (7 days) → Daily summary (10 min) → Dashboard
```

- Claude Code sends its own telemetry (model, tokens, estimated cost, tools, duration, failures).
- The collector keeps **only fields from an allowed list** and discards prompt and response text, full commands, paths, tool inputs and outputs, error text and email. From the shell, it keeps only the program name.
- A daily summary is recalculated every 10 minutes; raw data is deleted after 7 days.

## How to use

| Step | What to do | File |
|---|---|---|
| 1 | Turn on the app's connector in *Customize → Connectors* | — |
| 2 | Ask for the dashboard with a question, definitions and "do not change data" | `prompts/01-dashboard-negocio.md` |
| 3 | Check a number at its source before acting | `prompts/02-conferir-numero.md` |
| 4 | Combine apps in a central database in Supabase | `prompts/03-base-central.md` |
| 5 | Schedule the database refresh | `prompts/05-agendar-atualizacao.md` |
| 6 | Business diagnosis → plan → follow-up dashboard | `prompts/06-diagnostico-negocio.md` |
| 7 | Build the Claude Code dashboard (database, collector, tabs) | `prompts/04-observabilidade-claude-code.md` |
| 8 | Turn on telemetry in `~/.claude/settings.json` and **restart** Claude Code | `config/claude-code-otel.example.json` |

Read-only Supabase restricted to a single project (custom connector):

```
https://mcp.supabase.com/mcp?project_ref=SEU_PROJECT_REF&read_only=true
```

If nothing reaches the collector: `claude --debug-file /tmp/cc.log` and look for `[3P telemetry]` lines. The symptoms table (401, 404, 415, empty cards) is at the end of `prompts/04-observabilidade-claude-code.md`.

## Prerequisites

- Paid Claude plan: Pro, Max, Team or Enterprise (beta feature, counts toward plan usage).
- Free Supabase account (500 MB; a free project pauses after a week without use) — for the central database and Claude Code.
- Claude Code — only for the observability dashboard.

## Limits

- **Shows, doesn't warn.** Alerts require a separate scheduled routine.
- **Cost is an estimate** at API prices; on Pro/Max the subscription applies.
- **Each person needs their own connection** to the Supabase project to see the cards.
- **Names are stored** (skills, repositories, MCP servers) — think about who sees them.
- **Claude Code only**; the app chat and Cowork are not included.
- **Test on your system** (Linux, Windows, Mac) before relying on the flow.

## Structure

```
PLANO.md                               phased plan (Track A business, Track B Claude Code)
prompts/01-dashboard-negocio.md        dashboard from a single app
prompts/02-conferir-numero.md          checklist for verifying a number
prompts/03-base-central.md             central database in Supabase
prompts/04-observabilidade-claude-code.md  single prompt for the Claude Code dashboard + diagnosis
prompts/05-agendar-atualizacao.md      scheduled copy
prompts/06-diagnostico-negocio.md      read-only diagnosis → plan → follow-up
config/claude-code-otel.example.json   Claude Code env block
guia/                                  landing + guide page (PT, EN, ES)
```

## Status

Plan + prompt kit. The Claude Code dashboard has not yet been built end to end from this kit; treat the first run as a pilot and check each step before giving Claude your OK.

---

[INEMA.CLUB](https://inema.club)
