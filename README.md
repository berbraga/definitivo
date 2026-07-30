# renov-market-scan

Coletor de referência de mercado de seminovos. Dada uma planilha no template de
importação de dispositivos, pesquisa anúncios de aparelhos usados em
marketplaces brasileiros e devolve, por modelo e capacidade, o valor mínimo, a
mediana e o valor máximo anunciados — cada extremo com o link do seu anúncio.

> **O número que sai daqui é preço de anúncio (pedido), não preço de transação.**
> Serve como referência de variação de mercado para calibrar valores de trade-in,
> não como avaliação.

## Setup

```bash
uv sync
cp .env.example .env      # e preencha ANTHROPIC_API_KEY
```

## Uso

Sempre comece com `--dry-run`, que mostra o plano e o custo estimado sem gastar
nada:

```bash
uv run renov-market-scan run --input Template-iPhone.xlsx --limite 10 --dry-run
```

Execução real (pede confirmação antes de gastar):

```bash
uv run renov-market-scan run \
  --input Template-iPhone.xlsx --output out/ \
  --fontes mercadolivre,trocafy,cellularstore \
  --limite 10 --concorrencia 4
```

Reexecutar aproveitando o cache do dia, sem custo:

```bash
uv run renov-market-scan run --input Template-iPhone.xlsx --retomar
```

Regravar o relatório sem tocar a rede:

```bash
uv run renov-market-scan run --input Template-iPhone.xlsx --reprocessar-filtro
```

### Flags

| Flag | Default | Efeito |
|---|---|---|
| `--input` | obrigatório | Planilha de entrada. **Nunca é escrita.** |
| `--output` | `out` | Diretório das saídas. |
| `--fontes` | mercadolivre, trocafy, cellularstore | Lista separada por vírgula. Outras fontes via override explícito. |
| `--somente-ativos` / `--todos` | `--somente-ativos` | Ignora linhas com `Price for In-store` ≤ 10. |
| `--marca` | todas | Filtra por fabricante. |
| `--limite` | sem limite | Primeiros N modelos, na ordem da planilha. |
| `--incluir-novos` / `--sem-novos` | `--sem-novos` | Inclui anúncios novos e lacrados na mediana. |
| `--concorrencia` | 4 | Chamadas simultâneas à API. |
| `--cache` | `.cache/scan.sqlite` | Banco de cache. |
| `--retomar` | desligado | Pula o que já está no cache do dia. |
| `--dry-run` | desligado | Só mostra plano e custo. |
| `--reprocessar-filtro` | desligado | Regrava o relatório do cache, sem rede. |

## Saídas

| Arquivo | Conteúdo |
|---|---|
| `out/referencia-mercado_<data>.xlsx` | Relatório de 4 abas |
| `out/<entrada>_com-referencia.xlsx` | Cópia da planilha original com colunas de mercado anexadas à direita |
| `out/resumo.csv` | A aba Resumo em CSV |
| `out/resultado.json` | Tudo, em JSON |
| `out/run.log` | Log estruturado |

### Aba `Resumo` — uma linha por `(ERP Code, Model, Storage)`

| Coluna | Significado |
|---|---|
| `erp_code` | Código do ERP. **Não é único**: 118 códigos aparecem em mais de uma linha no arquivo Android, e dois aparelhos de fabricantes diferentes chegam a compartilhar código. Por isso a linha é identificada pela tripla, não pelo ERP sozinho. |
| `device_name` | Nome da planilha. Nunca usado como chave: existem duplicados. |
| `fabricante`, `modelo`, `capacidade` | Do alvo, como está na planilha. |
| `valor_atual_planilha` | `Price for In-store` da linha. |
| `n_amostras` | Anúncios que passaram por todas as regras de filtro. |
| `n_fontes` | Quantas fontes contribuíram com pelo menos um anúncio. |
| `preco_minimo`, `link_minimo` | Menor preço da amostra saneada e o link do seu anúncio. |
| `mediana` | Mediana da amostra saneada. **Vazia quando `n < 3`.** |
| `preco_maximo`, `link_maximo` | Maior preço da amostra saneada e o link do seu anúncio. |
| `p25`, `p75` | Quartis da amostra saneada. |
| `spread_pct` | `(max − min) / mediana`. Quanto o mercado varia. |
| `razao_mediana_vs_atual` | `mediana / valor_atual_planilha`. Acima de 1 significa que o mercado pede mais do que a tabela paga. |
| `condicao_predominante` | Condição mais frequente na amostra. |
| `fontes` | Fontes que contribuíram. |
| `coletado_em` | Data da coleta. |
| `status` | `ok` (n ≥ 5), `amostra_baixa` (3–4), `insuficiente` (n < 3, sem mediana). |
| `observacoes` | Espaço para anotação manual. |
| `min_bruto`, `max_bruto` | Extremos **antes** da remoção de outliers por IQR, para auditar a limpeza. |

### Aba `Amostras`

Todo anúncio aceito, com `cited_text` — o trecho verbatim que a API citou e que
comprova o preço. É a evidência auditável de cada linha do Resumo.
`flag_5g_divergente` marca quando o alvo e o anúncio divergem apenas no 5G.

### Aba `Descartados`

Todo anúncio rejeitado, com `motivo_descarte`. Serve para calibrar as regras.
Motivos possíveis: `acessorio_ou_peca`, `modelo_divergente`,
`capacidade_ausente`, `peca_provavel`, `condicao_excluida`, `preco_ausente`,
`preco_parcelado`, `preco_sem_evidencia`, `preco_fora_de_faixa`, `duplicado`.

### Aba `Anomalias`

Linhas com `Storage, GB*` suspeito (`1`, que é RAM na coluna errada, e `1288`,
que é `128` digitado errado) e modelos pesquisados que não produziram amostra.
Nada aqui é adivinhado: entra com `status = revisao_humana`.

## Como as regras funcionam

Ordem do filtro. A primeira regra que rejeita é a registrada:

1. Blacklist de acessórios e peças inequívocos.
2. Match de modelo: o conjunto de qualificadores do título tem que ser **igual**
   ao do alvo. `iPhone 13` não aceita `iPhone 13 Pro Max`, e o inverso também
   vale. `5g` é tolerado nos dois sentidos e vira flag.
3. Capacidade tem que aparecer no título ou na URL.
4. Blacklist contextual: `bateria`, `tela`, `display` sem marcador de isenção.
5. Condição: `seminovo` e `usado` por padrão.
6. Preço: parser BRL, rejeitando qualquer valor precedido por `Nx`.
7. Evidência: os dígitos do preço têm que aparecer no `cited_text` da API.
8. Faixa de sanidade: piso R$ 80, teto R$ 15.000.
9. Deduplicação por URL canônica e por `(título, preço)`.

Depois, por modelo: outliers removidos por IQR (só com n ≥ 4), e min, mediana e
max calculados sobre a amostra saneada.

## Conduta de coleta

Respeita `robots.txt` e os termos de uso. Não contorna captcha, rate limit ou
proteção anti-bot; fonte que bloqueia recebe `status=bloqueado` e a execução
segue. Sem Selenium, sem Playwright.

## Desenvolvimento

```bash
uv run pytest
uv run ruff check .
uv run mypy renov_market_scan
```

Nenhum teste toca a rede: a coleta em teste passa pelo `FixtureAdapter`.

Design: `docs/superpowers/specs/2026-07-28-renov-market-scan-design.md`
Medições da Fase 0: `docs/fontes.md`
