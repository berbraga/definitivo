# Fontes de dados

## Contrato de extração

Spike executado em 2026-07-30. Custo real acima do estimado: o spike original
(3 chamadas) rodou limpo, mas revelou uma contradição de projeto que exigiu
mais 4 chamadas de diagnóstico e validação antes de fechar a decisão. Total
~7 chamadas reais, custo aproximado US$ 0,20-0,25 (não os US$ 0,10 previstos).

| Mecanismo | Resultado | Observação |
|---|---|---|
| A `output_config.format` | FUNCIONOU | Não deu 400 como esperado — mas ver achado abaixo. |
| B JSON por prompt | FUNCIONOU, JSON parseável direto | |
| C tool estrita `strict: true` | FUNCIONOU, mas `tool_use blocks = 0` | Modelo respondeu texto direto, ignorou a tool. |

### Achado crítico: JSON puro e citações são mutuamente exclusivos

Os três mecanismos, testados com o `SYSTEM` prompt do spike ("responda apenas
com JSON, sem texto fora do JSON"), retornaram **`citations = 0`** em todos os
casos. Investigação confirmou a causa: **forçar saída em JSON puro suprime as
citações do modelo por completo** — não é falha de A, B ou C entre si, é que
o próprio pedido "sem texto fora do JSON" é incompatível com preservar
citações no texto (citações só existem anexadas a blocos de texto livre).

Confirmado por comparação controlada: a mesma busca, sem o `SYSTEM` de
JSON-puro, retornou 9 citações reais com `cited_text` verbatim de anúncios
da OLX (preço, condição, estado de conservação).

Isso quebra a regra de decisão original do spike (que dependia de "A funciona
com `citations > 0`") e é uma contradição direta com a Tarefa 9 (regra de
evidência por citação), que exige `cited_text` para aceitar um preço.

**Mecanismo escolhido para `AnthropicSearchAdapter`: duas etapas.**
Justificativa: etapa 1 faz a busca real sem restringir o formato de saída
(preserva citações no texto); etapa 2 é uma chamada separada, sem tool de
busca disponível, que recebe o texto da etapa 1 (citações incluídas como
contexto) e extrai o array JSON estruturado com `cited_text` verbatim por
item — validado com pydantic (mecanismo B aplicado sobre o resultado da
etapa 1, não sobre uma busca nova).

Validado com uma chamada real: etapa 1 preservou 9 citações; etapa 2
extraiu JSON válido com `preco_brl` numérico e `cited_text` fiel à citação
original, após remover a cerca de markdown (` ```json ... ``` `) que o
modelo às vezes envolve a resposta — a Tarefa 16 precisa desse strip antes
de `json.loads`.

### Calibração do estimador de custo

Medido na chamada real de busca (etapa 1, com `max_uses=3`, um domínio):

- `TOKENS_IN_PER_CALL = 25808`
- `TOKENS_OUT_PER_CALL = 1521`
- `SEARCHES_PER_CALL = 2`

A etapa 2 (extração, sem busca) é muito mais barata em entrada e não conta
para `SEARCHES_PER_CALL`: `input_tokens = 2492`, `output_tokens = 3809`
(saída maior porque inclui o array completo). A Tarefa 17 deve modelar o
custo por par `(modelo, fonte)` como a soma das duas etapas, não apenas a
etapa 1.

Estes números alimentam `renov_market_scan/cost.py` (Tarefa 17). O
estimador não adivinha tokens de resultado de busca: usa esta medição.
