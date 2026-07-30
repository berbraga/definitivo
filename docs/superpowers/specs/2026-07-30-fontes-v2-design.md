# Fontes v2 — Design

**Data:** 2026-07-30
**Status:** aprovado para implementação
**Substitui:** decisão #1 do spec `2026-07-28-renov-market-scan-design.md` (fontes v1)

## Motivação

Na rodada real de 2026-07-30 com fontes v1 (`olx`, `enjoei`, `mercadolivre`),
quase todos os anúncios aceitos vieram do Mercado Livre. OLX e Enjoei são
marketplaces C2C com condição descrita de forma heterogênea por particulares,
o que polui a amostra e dificulta calibrar trade-in.

O objetivo da v2 é priorizar **lojas especializadas** com grades de condição
padronizados, mantendo Mercado Livre como referência de volume.

## Decisão

| # | Decisão | Valor |
|---|---|---|
| 1 | Fontes habilitadas por default | `mercadolivre`, `trocafy`, `rede_cell_store`, `celular_seminovo_rede`, `cellularstore` |
| 2 | Fontes desabilitadas (histórico) | `olx`, `enjoei` — `enabled: false`, permanecem em `fontes.yaml` |
| 3 | Trocafone vs Trocafy | Marcas distintas. `trocafone.com` permanece disabled; `trocafy.com.br` é a nova fonte |
| 4 | Rede Cell Store | Opção A: duas entradas em `fontes.yaml` (domínio raiz + subdomínio de catálogo) |
| 5 | Classificação de condição | Expandir `SEMI_NEW_MARKERS` com grades de loja (Trocafy, ML recondicionado, Cell Store) |
| 6 | Frases de busca | Manter as duas frases atuais até o spike provar insuficiência |
| 7 | Cache | Recomendar cache novo ou limpar ao trocar fontes; `--retomar` por `(search_key, source, dia)` |

## Domínios

| name | domain |
|---|---|
| mercadolivre | mercadolivre.com.br |
| trocafy | trocafy.com.br |
| rede_cell_store | redecellstore.com.br |
| celular_seminovo_rede | celularseminovo.redecellstore.com.br |
| cellularstore | cellularstore.com.br |

## Impacto de custo

- v1: 3 fontes por modelo
- v2: 5 fontes por modelo (+67% de chamadas API por modelo)
- Dry-run obrigatório antes de rodadas grandes

## Fora de escopo

- Remover Mercado Livre
- Múltiplos domínios por entrada `Source` (Opção B do plano)
- Regras de filtro específicas por fonte
- Frases de busca por fonte (sem evidência do spike)

## Validação

1. Spike em cada domínio novo registrado em `docs/fontes.md`
2. `uv run pytest` verde
3. Dry-run: 10 modelos × 5 fontes = 50 pares
4. Rodada real com `--limite 3` e inspeção manual de `Amostras`/`Descartados`
