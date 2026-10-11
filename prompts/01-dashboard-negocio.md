# 01 — Painel de negócio a partir de um app

Use num chat com o conector do app ligado. Troque o que está entre colchetes.

```text
Monte um Dashboard do Claude usando minha conexão com [nome do app ou Supabase].

Pergunta que o painel precisa responder: [a pergunta que muda o que eu faço amanhã —
ex.: quais faturas estão em atraso e há quanto tempo?].

Fonte: [tabela ou objeto do app — ex.: faturas / invoices].

Definições:
- [ex.: saldo em aberto = valor total − valor pago; valor pago vazio conta como zero]
- [ex.: entram só faturas com status "aberta" ou "parcial"]
- [ex.: atraso medido a partir da data de vencimento, contra a data de hoje]
- Moeda: [BRL].

Mostre: o total, a quebra por [faixa de atraso / cliente / mês] e, em cada gráfico,
a consulta usada e a hora da última atualização.

Regras:
- Use consultas de leitura salvas no painel, nunca números escritos à mão.
- Não altere nenhum dado.
- Se faltar tabela, permissão ou definição, me pergunte em vez de supor.
```

Depois, refine conversando: "adicione filtro por faixa de atraso", "mude para visão semanal", "separe por cliente".
