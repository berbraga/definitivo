# Fontes — registro da Fase 0

Medições feitas em 2026-07-28, antes de escrever qualquer parser.

## Acesso direto às fontes

| Fonte | Acesso direto | Medido em |
|---|---|---|
| olx.com.br | 403 Cloudflare WAF em todo path, inclusive homepage | Chromium headless limpo e WebFetch |
| enjoei.com.br | 403 Cloudflare WAF em todo path, inclusive homepage | Chromium headless limpo |
| mercadolivre.com.br | bloqueado | confirmado antes do survey |

Conclusão: raspagem direta de HTML não é viável nas três fontes escolhidas sem
contornar proteção anti-bot, o que as regras de conduta proíbem. A web search
tool da API é o único motor viável, e `collect/http_source.py` fica fora da v1.

## Formato de URL de busca e de anúncio

OLX expõe a capacidade no path, o que sustenta a regra de capacidade:

- Categoria: `https://www.olx.com.br/celulares/apple/iphone-13/128gb/estado-sp`
- Anúncio: `https://sp.olx.com.br/sao-paulo-e-regiao/celulares/iphone-13-128gb-seminovo--1446106085`

Mercado Livre devolve sobretudo páginas de busca (`lista.mercadolivre.com.br/...`)
e páginas de catálogo (`/p/MLB...`).

## A frase decide o que retorna

| Frase | Anúncios individuais | Páginas de categoria |
|---|---|---|
| `iphone 13 128gb usado seminovo` | 1 de 10 | 9 de 10 |
| `iphone 13 128gb seminovo bateria 100% R$ vendo` | 5 de 10 | 5 de 10 |

Página de categoria não tem preço unitário. Por isso o construtor de queries
emite duas frases por fonte: uma genérica e uma no estilo de quem anuncia.

## Como o preço aparece

No próprio título do anúncio. Exemplos verbatim medidos:

- `iPhone 13 128GB Branco Saúde de bateria 90% R$ 3.050,00 | Loja Física |`
- `iPhone 13 Pro Max 128GB Gold - seminovo` (sem preço no título)
- Mercado Livre: `Samsung Galaxy S23 256 GB 5G Preto 8 GB RAM | Parcelamento sem juros`

Três consequências para o parser:

1. O preço vem com `R$` e separador pt-BR. Número sem `R$` no título costuma ser
   saúde de bateria ou polegadas, então só valores com o marcador contam.
2. "Saúde de bateria 90%" aparece na maioria dos anúncios legítimos de iPhone.
   Uma blacklist literal com `bateria` derrubaria a melhor parte da amostra —
   daí a blacklist em duas camadas.
3. `Parcelamento sem juros` confirma a armadilha de parcelamento.

## Quanto lixo vem junto

Numa busca por iPhone 13 puro, 2 dos 10 resultados eram `iphone-13-pro` e
`iPhone 13 Pro Max`. O match estrito de qualificadores é obrigatório, não
opcional.

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

## Fontes v2 — Trocafy e Celular Store

Spike planejado em 2026-07-30 via `spikes/source_survey_v2.py` (4 domínios
novos, query `apple iphone 13 128gb seminovo recondicionado r$`).

| Domínio | Fonte em `fontes.yaml` | Status do spike | Observação |
|---|---|---|---|
| trocafy.com.br | trocafy | BLOQUEADO | `401 authentication_error` — `ANTHROPIC_API_KEY` inválida no `.env` |
| redecellstore.com.br | rede_cell_store | BLOQUEADO | Mesmo bloqueio de API |
| celularseminovo.redecellstore.com.br | celular_seminovo_rede | BLOQUEADO | Mesmo bloqueio de API |
| cellularstore.com.br | cellularstore | BLOQUEADO | Mesmo bloqueio de API |

**Critérios a medir após corrigir a chave:**

1. A web search tool encontra anúncios no domínio?
2. Títulos trazem preço com `R$`?
3. Citações (`cited_text`) aparecem na etapa 1?
4. Condição no título é legível pelo classificador atualizado?

**Decisão de configuração (independente do spike):** Opção A — duas entradas
separadas para Rede Cell Store (`rede_cell_store` + `celular_seminovo_rede`).
Fontes v1 (`olx`, `enjoei`) permanecem em `fontes.yaml` com `enabled: false`.

**Recomendação de cache:** usar arquivo novo (ex. `.cache/scan-v2.sqlite`) ao
trocar fontes — entradas antigas de OLX/Enjoei não devem misturar com a nova
rodada.

```bash
uv run python spikes/source_survey_v2.py
```
