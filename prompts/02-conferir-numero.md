# 02 — Conferir um número antes de agir

Clique no número no painel e leia a consulta. Confira quatro coisas:

1. **Data de referência** — mede a partir de hoje ou da data que você pediu?
2. **Quais registros entram** — ex.: só faturas abertas e parciais? canceladas ficaram fora?
3. **Descontos** — subtrai pagamentos parciais, estornos e créditos?
4. **Bordas das faixas** — o dia 30 cai em "1–30" ou em "31–60"?

Depois compare **um** número com o app de origem. Ver a consulta não garante que ela está certa:
data errada, junção duplicada ou definição errada ainda dão número errado.

Prompt para pedir a conferência ao Claude:

```text
Para o cartão "[nome do cartão]": explique em português simples a consulta, diga qual data
de referência usa, quais registros inclui e exclui, como trata pagamentos parciais e onde
ficam as bordas das faixas. Depois me diga como eu confiro esse mesmo número direto no
[app de origem], passo a passo.
```
