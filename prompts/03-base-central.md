# 03 — Base central no Supabase (perguntas que cruzam apps)

Use quando a resposta depende de dois apps ao mesmo tempo (horas × faturas, assinaturas × cobranças,
contabilidade × banco). Conector Supabase ligado, com escrita só para a montagem.

```text
Quero responder: [pergunta que cruza dois apps — ex.: que trabalho foi entregue e nunca faturado?].

Fontes que autorizo: [app 1 — tabela/objeto] e [app 2 — tabela/objeto].

Antes de copiar qualquer coisa:
1. Liste os campos de cada fonte que você precisa e por quê.
2. Proponha como os registros casam entre os dois (chave, nome normalizado, data).
3. Proponha um schema próprio no meu projeto Supabase (não use o public) com uma tabela por fonte
   e uma visão que faz o cruzamento.
4. Proponha um piloto pequeno (últimos 90 dias) e a frequência da cópia.
Espere meu OK.

Depois do OK: crie o schema, copie o piloto, rode a visão e me mostre 5 linhas de exemplo
para eu conferir na mão. Só então monte o Dashboard em cima da visão, com consultas de leitura.
Não altere nada fora desse schema.
```

Guarde as definições (como casa, quais datas valem, moeda) num arquivo de conhecimento do seu Projeto no Claude.
