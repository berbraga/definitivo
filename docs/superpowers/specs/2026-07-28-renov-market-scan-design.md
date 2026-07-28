# `renov-market-scan` — Design

**Data:** 2026-07-28
**Status:** aprovado para planejamento

## Objetivo

Dada uma planilha no template de importação de dispositivos, pesquisar anúncios
de aparelhos seminovos/usados em marketplaces brasileiros e devolver, por
modelo+capacidade, o **valor mínimo, a mediana e o valor máximo** anunciados,
com o link do anúncio mínimo e do máximo.

O número produzido é **preço de anúncio (pedido), não preço de transação**. O
relatório declara isso em nota de rodapé:

> *"Valores de anúncio (preço pedido), não de transação. Amostra coletada em
> {data} — referência de variação de mercado, não avaliação."*

**Entregável:** pacote Python instalável + CLI + relatório `.xlsx` auditável.
**Idioma:** código e identificadores em inglês; mensagens de CLI, colunas do
relatório e documentação em pt-BR.

## Decisões

| # | Decisão | Valor |
|---|---|---|
| 1 | Fontes v1 | `olx.com.br`, `enjoei.com.br`, `mercadolivre.com.br`. Trocafone e Shopee ficam em `fontes.yaml` desabilitadas |
| 2 | Recorrência | One-off. Cache serve retomada e reprocessamento; sem comparação entre rodadas |
| 3 | Escopo geográfico | Brasil inteiro (`user_location.country = "BR"`, sem cidade) |
| 4 | Modelo de extração | `claude-sonnet-5`, configurável |
| 5 | Anúncios novos | Fora da mediana por padrão; `--incluir-novos` liga |
| 6 | Frases de busca | 2 por (modelo, fonte): genérica + estilo anunciante |
| 7 | Repositório | Novo repo git em `definitivo/`, branch `feat/market-scan` |
| 8 | Entrega da planilha | Cópia do template com colunas extras. **O arquivo de entrada nunca é escrito** |
| 9 | Contrato de extração | Decidido por spike de ~US$ 0,10 (Tarefa 16) |
| 10 | Regra de evidência | Entra na v1, com descarte registrado |
| 11 | Unidade do relatório | `(ERP Code, Model, Storage)` |
| 12 | Tratamento de 5G | Tolerante nos dois sentidos, com flag |
| 13 | Adapter HTTP | **Fora da v1** (ver "Fora de escopo") |

## Fatos verificados nas planilhas

Verificação executada em 2026-07-28 sobre `Template-iPhone.xlsx` e
`RS_Maio_Androids_2026.xlsx`. Todo item abaixo foi medido, não presumido.

### Confirmado

- Linha 1 = texto de ajuda (15 células preenchidas, começando em
  `"Custom name of a device*\nMaximum 255 characters"`); linha 2 = cabeçalho;
  linha 3+ = dados.
- Nome da aba varia: `iPhones` e `Planilha1`. Em ambos é a **primeira** aba.
- 19 colunas, na ordem especificada, match exato nos dois arquivos.
- 1.032 linhas Android, 119 iPhone.
- `Price for In-store == Price for Widget & Mobile` em 100% (1032/1032, 119/119).
- 747 linhas Android e 24 iPhone com `Price for In-store = 10`. Não existe valor
  entre 0 e 10, portanto `<= 10` e `== 10` são equivalentes neste dado.
- `Ram, GB*` vazia em 100%. `Color*` sempre `Any Color`. `Device type*` sempre
  `Phone`.
- 25 fabricantes no Android: Samsung 345, Motorola 238, Xiaomi 237, Redmi 107,
  Realme 29, LG 16, Infinix 12, Jovi 11, e os demais com no máximo 7.
- Exatamente um `Storage, GB* = 1288`: `XIAOMI REDMI 9C`, ERP `10953A0`.
- 100% dos ERP Codes terminam em `A0`.
- 1.150 combos `(manufacturer, model, storage)` únicos somando os dois arquivos
  (1031 + 119); 380 linhas ativas somando os dois (285 + 95).

### Divergências encontradas

**D1 — `ERP Code` não é chave única.** A especificação original o tratava como
chave primária de cache, retomada e join. Medição:

- Android: 118 ERP Codes duplicados, 325 das 1.032 linhas envolvidas, 825 únicos.
- iPhone: 2 duplicados (`20105A0`, `20106A0`), 4 linhas cada.
- Nenhuma duplicata é byte-idêntica nas 19 colunas — sempre difere em
  `Manufacturer*`, `Model*`, `Device name*` ou `Storage, GB*`.

Duas classes de colisão:

- **ERP-lixo `10000A0`:** 90 linhas, storages `{1: 87, 256: 3}`, preço 10 em
  todas. Contém os 87 storages `1` do Android. Todas inativas.
- **Colisões reais:** 28 grupos, 12 tocando linha ativa. Exemplos:
  - `10080A0` = `GALAXY A55 5G` e `GALAXY A55`, ambos 128GB e R$ 640
  - `10870A0` = `GALAXY A17` e `GALAXY A17 5G`, ambos 128GB e R$ 400
  - `10884A0` = `MOTOROLA MOTO G17 128GB` **e** `REALME C63 128GB` — fabricantes
    diferentes, mesmo ERP

**Unicidade medida entre as linhas ativas:**

| Chave candidata | Android (285) | iPhone (95) |
|---|---|---|
| `ERP Code` | 275/285 falha | 95/95 |
| `Device name` | 275/285 falha | 95/95 |
| `(ERP Code, Model, Storage)` | **285/285 ok** | **95/95 ok** |
| `(Manufacturer, Model, Storage)` | **285/285 ok** | **95/95 ok** |

**D2 — Critério de aceite 7 estava numericamente errado.** Dizia que o arquivo
Android inteiro com `--somente-ativos` cabe em "~380 modelos". O Android inteiro
dá **285**. Os 380 são a soma dos dois arquivos.

**D3 — Os "92 storages `1`" são a soma dos dois arquivos**, não do Android:
87 Android + 5 iPhone.

**D4 — Nenhuma linha com storage sujo está ativa.** 0 de 88 no Android, 0 de 5
no iPhone. Como `--somente-ativos` é o default, a aba `Anomalias` vem vazia no
fluxo padrão; ela só recebe conteúdo com `--todos`.

### Achados adicionais

- **`5G` aparece no `Model*` do alvo em 160 linhas Android.** Existem pares em
  que 5G é o único diferenciador (`GALAXY A17` / `GALAXY A17 5G`) e nesses pares
  o preço da planilha é idêntico.
- **Qualificadores fora da lista original:** `NE` (`11 LITE 5G NE`), `UW`
  (`EDGE 5G UW`), `+` como sinônimo de `plus` (`EDGE+`), `BY SWAROVSK`, e ano
  entre parênteses (`IPHONE SE (2022)`, `EDGE 5G UW (2021)`).
- **`Device name` é inconsistente.** No iPhone só 15 de 119 começam com `APPLE`.
  O sufixo ` A0` não é universal: 904/913 nomes distintos no Android,
  108/115 no iPhone.
- **Cinco colunas são constantes e devem ser ignoradas:** `Minimum Price` e
  `Maximum Price` são `0.00` em 100% dos dois arquivos; `Buying` =
  `Online, Offline`; `Priority` = `10`; `Reward Types` = `Trade-In`.
- **Fabricantes que exigem mapa de alias:** `REDMI` (107 linhas) é fabricante
  separado mas "Xiaomi Redmi" no mercado; `POCO` é Xiaomi; `JOVI` (11) é
  sub-marca da `VIVO` (2); `ITEL` e `ITEL MOBILE` são a mesma marca em dois
  registros.

## Fatos verificados da API

Verificado na documentação em 2026-07-28.

- **Três versões da web search tool:** `web_search_20250305` (básica),
  `web_search_20260209` (filtragem dinâmica), `web_search_20260318`
  (mais `response_inclusion`).
- **Da `20260209` em diante, `allowed_callers` tem default
  `["code_execution_20260120"]`** — roda dentro de code execution. Para chamada
  direta é obrigatório `allowed_callers: ["direct"]`.
- **Versões novas exigem** Opus 5/4.8/4.7/4.6 ou Sonnet 5/4.6. Haiku 4.5 só
  aceita `web_search_20250305`. A validação de inicialização precisa checar o
  par **(modelo, versão)**, não só a string da versão.
- **Preço:** US$ 10 por 1.000 buscas, independente do modelo. Erro de busca não
  é cobrado.
- **O cliente não recebe o texto do snippet.** O bloco `web_search_result` traz
  só `url`, `title`, `page_age` e `encrypted_content`. Apenas o modelo lê o
  conteúdo. As `citations` trazem `cited_text` de até 150 caracteres verbatim, e
  `cited_text`/`title`/`url` não contam como tokens.
- **Erros de busca chegam como HTTP 200**, dentro de
  `web_search_tool_result_error`, com `error_code` entre `too_many_requests`,
  `invalid_tool_input`, `max_uses_exceeded`, `query_too_long`,
  `request_too_large`, `unavailable`. Busca sem resultado devolve `content` lista
  vazia, não erro.
- **`stop_reason: "pause_turn"`** pode interromper turnos longos; a retomada é
  reenviar a mensagem do assistente inalterada.
- **`allowed_domains` e `blocked_domains` são mutuamente exclusivos** — enviar os
  dois é 400. Entradas são domínio nu, sem esquema.
- **Reprocessar extração sem custo de busca é possível:** reenviar os blocos do
  assistente incluindo `encrypted_content` restaura os resultados no contexto.

## Fatos verificados dos marketplaces

Três buscas exploratórias em 2026-07-28. Registro completo em `docs/fontes.md`.

- **A frase decide o que retorna.** `"iphone 13 128gb usado seminovo"` em
  `olx.com.br` devolveu 9 de 10 resultados como páginas de categoria
  (`/celulares/apple/iphone-13/128gb/estado-sp`), que não têm preço unitário.
  `"iphone 13 128gb seminovo bateria 100% R$ vendo"` devolveu 5 de 10 como
  anúncios individuais.
- **O preço vem no próprio título do anúncio.** Exemplo real:
  `"iPhone 13 128GB Branco Saúde de bateria 90% R$ 3.050,00 | Loja Física"`.
- **Contaminação Pro/Max é imediata:** 2 dos 10 resultados de uma busca por
  iPhone 13 puro eram `iphone-13-pro` e `iPhone 13 Pro Max`.
- **Parcelamento confirmado:** página de catálogo do Mercado Livre com
  `"Parcelamento sem juros"` no título.
- **O storage aparece no path da URL da OLX** (`/iphone-13/128gb/`), o que
  sustenta a regra de capacidade.
- **OLX, Enjoei e Mercado Livre bloqueiam acesso direto.** Survey anterior do
  usuário (`../Playwright/.superpowers/sdd/`) mediu 403 Cloudflare WAF em todo
  path, incluindo homepage, com Chromium headless limpo. Reconfirmado com
  WebFetch em OLX: 403.

## Arquitetura

### Chave dupla

`ERP Code` não é único (D1), e o plano de busca precisa deduplicar. Duas chaves,
ambas necessárias:

- **`search_key` = `sha1(manufacturer|model|storage_normalizado)`** — unidade de
  busca, de cache e de estatística.
- **`report_key` = `(ERP Code, Model, Storage)`** — unidade da aba `Resumo` e das
  anomalias. Mantém `ERP Code` visível como coluna de join com o ERP.

`Device name` nunca é chave, em nenhuma das pontas.

No caminho padrão (`--somente-ativos`) as duas são 1:1 com as linhas: 285 linhas
ativas Android resultam em 285 search keys. A deduplicação só colapsa algo com
`--todos`.

### Cache em camadas

| Camada | Tabela | Custo para regenerar |
|---|---|---|
| Resposta crua (JSON completo, citations, usage, stop_reason) | `raw_search` | **US$** — buscas novas |
| Anúncios extraídos pelo modelo | `listing` | Só tokens, zero taxa de busca |
| Filtro e estatística | funções puras sobre `listing` | **Zero** |

`raw_search` é gravado **antes** de qualquer processamento, em transação própria,
chaveado por `(search_key, fonte, frase_idx, data)`.

`--retomar` pula o que já está em `raw_search` no dia. `--reprocessar-filtro`
regrava o relatório sem tocar a rede. Re-extração a partir de `raw_search` é
possível pelo schema mas não exposta na CLI (YAGNI).

### Módulos

```
renov_market_scan/
  cli.py                      Typer: flags, barra de progresso, SIGINT
  config.py                   pydantic-settings; valida o par (modelo, versao da tool)
  cost.py                     estimador de dry-run
  models.py                   DeviceRow, SearchPlanItem, Query, RawResponse,
                              Listing, RejectedListing, ModelStats, RunReport
  ingest/reader.py            openpyxl read_only -> DeviceRow[]
  ingest/normalize.py         storage, chaves, anomalias
  query/builder.py            SearchPlanItem -> 2 frases x N fontes
  collect/base.py             SearchAdapter (Protocol)
  collect/errors.py           web_search_tool_result_error + pause_turn
  collect/anthropic_search.py adapter default (API + web search tool)
  collect/fixture.py          adapter offline para testes
  filtering/text.py           normalizacao compartilhada
  filtering/blacklist.py      acessorios e pecas
  filtering/matcher.py        match de modelo e de capacidade
  filtering/condition.py      novo | seminovo | usado | desconhecido
  filtering/price.py          parsing BRL, rejeicao de parcelamento
  filtering/evidence.py       regra de evidencia por citacao
  filtering/dedupe.py         URL canonica e (titulo, preco)
  filtering/pipeline.py       orquestra as regras, acumula motivo
  stats/aggregate.py          IQR, percentis, extremos com link
  report/xlsx.py              4 abas, formatacao, HYPERLINK
  report/template_copy.py     copy2 + colunas extras
  report/exports.py           resumo.csv e resultado.json
  cache/schema.py             DDL
  cache/store.py              SQLite em camadas
fontes.yaml                   fontes, peso, habilitado
marcas.yaml                   aliases de fabricante
```

`filtering/` em vez de `filter/`: `filter` é builtin do Python.

### Fluxo

```
xlsx de entrada  (read_only — nunca aberto para escrita)
  -> DeviceRow[]              valida In-store == Widget&Mobile, avisa se divergir
  -> --somente-ativos         descarta valor <= 10
  -> normalize storage        1024->1TB, 2048->2TB; 1 e 1288 -> revisao_humana
  -> plano                    dedupe por search_key + juncao para report_keys
  -> queries                  2 frases x N fontes
  -> DRY-RUN                  buscas, custo, tempo, PEDE CONFIRMACAO
  -> adapter                  Semaphore(4) -> grava raw_search ANTES de processar
  -> extracao                 modelo -> listing
  -> filtering/pipeline       aceitos + descartados (sempre com motivo)
  -> stats por search_key     -> fan-out para cada report_key
  -> report                   xlsx 4 abas, copia do template, csv, json
```

## Regras de filtro

Primeira regra que rejeita é a que fica registrada.

| # | Regra | Motivo |
|---|---|---|
| 1 | Blacklist de acessórios/peças | `acessorio_ou_peca` |
| 2 | Match de modelo (igualdade estrita de qualificadores) | `modelo_divergente` |
| 3 | Capacidade no título ou na URL | `capacidade_ausente` |
| 4 | Condição em {seminovo, usado}, mais novo com `--incluir-novos` | `condicao_excluida` |
| 5 | Preço parseável e não parcelado | `preco_ausente` · `preco_parcelado` |
| 6 | Evidência por citação | `preco_sem_evidencia` |
| 7 | Faixa de sanidade | `preco_fora_de_faixa` |
| 8 | Dedupe por URL canônica e `(titulo_normalizado, preco)` | `duplicado` |

### Normalização de texto

lowercase · NFKD sem acento · espaços colapsados · `+` para ` plus ` ·
`gigas`/`gb` unificados · `1tb` para `1024gb` · `2tb` para `2048gb`.

### Qualificadores

```
{pro, max, plus, mini, ultra, neo, fusion, lite, fe, se, power, play, air, ne, uw, swarovski}
```

O conjunto presente no título tem que ser **igual** ao do modelo alvo. Três
regras adicionais exigidas pelos dados:

- **`5g` fora do conjunto estrito.** Tolerado nos dois sentidos, gravando
  `flag_5g_divergente` na aba `Amostras`. Justificado pelos achados: nos pares
  `A17`/`A17 5G` o preço da planilha é idêntico.
- **Ano entre parênteses é obrigatório quando presente no alvo.**
  `IPHONE SE (2022)` só aceita anúncio com `2022` e rejeita anúncio com outro
  ano. Sem isso as três gerações do SE compartilham amostra.
- **`by swarovsk` é qualificador** (normalizado para `swarovski`). `EDGE 70` e
  `EDGE 70 BY SWAROVSK` são report keys distintas e não compartilham amostra.

### Blacklist

capa, capinha, case, película, pelicula, vidro, tela, display, frontal, touch,
bateria, placa, flex, conector, carcaça, aro, tampa, câmera traseira,
alto-falante, botão, peças, sucata, "para retirada", "não liga",
"sem funcionar", réplica, clone, similar, genérico, carregador, fone, cabo,
chip, suporte.

### Preço

Rejeita qualquer valor precedido por `Nx`, `N x`, `Nx de`, `em Nx`, ou próximo de
`parcelas de` / `sem juros`. Parser pt-BR: `R$ 1.234,56` para `1234.56`.

Piso default R$ 80, teto default R$ 15.000, ambos configuráveis. O teto é
deliberadamente **absoluto, não relativo ao valor da planilha** — um teto do tipo
"6x o valor atual" ancoraria a referência de mercado na tabela que ela existe
para calibrar. O IQR cuida do resto.

### Evidência

Para cada anúncio, reúne os `cited_text` e `title` dos resultados citados no
bloco de texto que o produziu. Normaliza a sequência de dígitos do preço
reportado e exige que ela apareça na evidência. Sem match resulta em descarte
`preco_sem_evidencia`, com o trecho gravado na aba `Descartados`.

Esta é a defesa contra min/max alucinado — os dois números que o relatório existe
para produzir, e os mais expostos por serem extremos.

## Estatística

Sobre a amostra válida por `search_key`:

- Remove outliers por IQR, fora de `[Q1 - 1,5*IQR, Q3 + 1,5*IQR]`.
- Calcula `n`, `min`, `p25`, `mediana`, `p75`, `max`,
  `spread_pct = (max-min)/mediana`.
- `min` e `max` são extremos da amostra **saneada**, cada um com a URL do seu
  anúncio. `min_bruto` e `max_bruto` ficam em colunas auxiliares.
- Status: `n >= 5` resulta em `ok`; `3 <= n < 5` em `amostra_baixa`; `n < 3` em
  `insuficiente` (sem mediana).
- `razao_mediana_vs_atual = mediana / valor_atual_planilha`.

Resultado é calculado uma vez por `search_key` e replicado para cada
`report_key` que aquela busca atende.

## Saídas

`out/referencia-mercado_<AAAA-MM-DD>.xlsx`:

- **`Resumo`** — uma linha por `report_key`: `erp_code`, `device_name`,
  `fabricante`, `modelo`, `capacidade`, `valor_atual_planilha`, `n_amostras`,
  `n_fontes`, `preco_minimo`, `link_minimo`, `mediana`, `preco_maximo`,
  `link_maximo`, `p25`, `p75`, `spread_pct`, `razao_mediana_vs_atual`,
  `condicao_predominante`, `fontes`, `coletado_em`, `status`, `observacoes`,
  `min_bruto`, `max_bruto`.
- **`Amostras`** — todo anúncio aceito: `erp_code`, `fonte`, `titulo`, `preco`,
  `condicao`, `url`, `capturado_em`, `flag_5g_divergente`, `cited_text`.
- **`Descartados`** — anúncio mais `motivo_descarte` e `cited_text`.
- **`Anomalias`** — storage suspeito e modelos sem amostra.

Formatação: freeze na linha 1, autofilter, `R$ #,##0.00`, largura ajustada,
`=HYPERLINK` nos links, formatação condicional em `razao_mediana_vs_atual`.

Também: `out/resumo.csv`, `out/resultado.json`, e
`out/<nome-original>_com-referencia.xlsx` — cópia via `shutil.copy2` do arquivo
de entrada com colunas novas anexadas à direita. As 19 colunas originais, a
linha 1 de ajuda e o nome da aba ficam preservados. **O arquivo de entrada é
aberto somente-leitura e nunca escrito.**

## CLI

```bash
renov-market-scan run \
  --input planilha.xlsx --output out/ \
  --fontes olx,enjoei,mercadolivre \
  --somente-ativos/--todos \
  --marca SAMSUNG --limite 50 \
  --incluir-novos/--sem-novos \
  --concorrencia 4 --cache .cache/scan.sqlite \
  --retomar --dry-run \
  --reprocessar-filtro
```

`--limite N` pega os N primeiros na ordem da planilha.

`--dry-run` imprime número de buscas, custo mínimo/esperado/teto e tempo
previsto, e **pede confirmação** antes de gastar. Usa
`client.messages.count_tokens` para o prompt exato; a estimativa de tokens de
resultado de busca vem calibrada pela medição do spike.

Progresso com `rich`, log estruturado em `out/run.log`. `SIGINT` para de agendar
tarefas novas, aguarda as em voo, faz flush e commit do SQLite.

## Custo estimado

Uma chamada `messages.create` por `(search_key, fonte)`, com as 2 frases no
prompt e `max_uses=3`. Buscas no máximo chamadas vezes 3.

| Cenário | Chamadas | Buscas (teto) | Taxa de busca | Total (Sonnet 5) |
|---|---|---|---|---|
| `--limite 10` no iPhone | 30 | 90 | $0,90 | ~$1,20 |
| Android ativo completo (285) | 855 | 2.565 | $25,65 | ~$75 |
| Ambos os arquivos (380) | 1.140 | 3.420 | $34,20 | ~$100 |

## Erros

| Situação | Tratamento |
|---|---|
| `too_many_requests`, `unavailable` (em HTTP 200) | tenacity, backoff exponencial |
| `max_uses_exceeded` | aceita parcial, status `busca_truncada` |
| `query_too_long` | encurta a query, registra, 1 retry |
| `request_too_large` | reduz a lista de `allowed_domains` |
| `invalid_tool_input` | status `erro_query`, sem retry |
| `stop_reason: "pause_turn"` | reenvia a mensagem do assistente intacta, máx. 3 |
| `stop_reason: "refusal"` | status `recusado`, sem retry |
| HTTP 429/5xx | tenacity |
| JSON inválido na extração | 1 retry com mensagem de correção, depois `parse_error` |

O SDK faz `max_retries=2` por conta própria. O cliente é construído com
**`max_retries=0`** e o tenacity é a única fonte de política de retry — senão as
tentativas se multiplicam (3 vezes 3 = 9) e o backoff perde sentido.

Nunca `eval` nem regex-parsing de JSON malformado.

## Conduta de coleta

- Respeitar `robots.txt` e os termos de uso. **Não** contornar captcha, rate
  limit ou proteção anti-bot. Fonte que bloqueia recebe `status=bloqueado`.
- Preferir a web search tool a raspagem direta de HTML.
- Cache agressivo para não repetir requisição.
- Sem Selenium, sem Playwright.

## Fora de escopo (v1)

- **`collect/http_source.py`.** OLX, Enjoei e Mercado Livre devolvem 403
  Cloudflare WAF a acesso direto (medido). `httpx` é mais fraco que headless
  Chromium contra Cloudflare, e as regras de conduta proíbem contornar. O
  `SearchAdapter` (Protocol) fica como ponto de extensão.
- Comparação entre rodadas e série histórica.
- `--reextrair` (habilitado pelo schema, não exposto).
- Escrita no arquivo de entrada, em qualquer circunstância.

## Critérios de aceite

1. `renov-market-scan run --input Template-iPhone.xlsx --limite 10 --dry-run`
   mostra plano e custo sem gastar nada.
2. Rodada real com `--limite 10` produz xlsx com as 4 abas e, para pelo menos 8
   dos 10 modelos, `status=ok` com `n >= 5`.
3. Amostragem manual de 10 anúncios da aba `Amostras`: zero acessórios, zero
   modelo trocado (Pro/Max/Ultra), zero preço parcelado.
4. `link_minimo` e `link_maximo` abrem anúncios que correspondem ao preço e ao
   modelo da linha, e o preço aparece no `cited_text` gravado.
5. Reexecutar com `--retomar` não dispara nenhuma busca nova (custo zero).
6. `ruff check`, `mypy` e `pytest` verdes.
7. `RS_Maio_Androids_2026.xlsx` com `--somente-ativos` resulta em **285**
   modelos, e o custo estimado é impresso antes. *(Corrigido de "~380", que era
   a soma dos dois arquivos — ver D2.)*
8. **O arquivo de entrada tem o mesmo hash SHA-256 antes e depois da execução.**

## Anti-padrões

1. Usar `Device name` como chave. Usar `ERP Code` **sozinho** como chave.
2. Hardcodar o nome da aba, o número de linhas, ou a versão da web search tool.
3. Adivinhar capacidade quando o dado está sujo (`1`, `1288`).
4. Aceitar anúncio sem capacidade no título ou na URL.
5. Confundir preço parcelado com preço à vista.
6. Reportar mediana com `n < 3`.
7. Reportar min/max sem o link correspondente.
8. Descartar anúncio sem registrar o motivo.
9. Buscar de novo o que já está no cache do dia.
10. Rodar o lote inteiro sem `--dry-run` antes.
11. Contornar bloqueio anti-bot de qualquer site.
12. Selenium ou Playwright.
13. Escrever no arquivo de entrada.
14. Empilhar retry do SDK com retry do tenacity.

## Itens operacionais abertos

- O repo é local e não tem remote nem branch `dev`. Abrir o PR com base `dev` e
  reviewer `marcelo-maciel` exige um remote no GitHub. Resolver na entrega.
- A identidade git foi definida apenas neste repo, com nome inferido do e-mail.
