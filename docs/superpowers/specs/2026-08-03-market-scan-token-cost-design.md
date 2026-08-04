# Refactor: reduzir consumo de token do market scan

## Contexto

Repositório: `berbraga/definitivo`, caminho local
`/home/bbraga/Documentos/workspaces/scraper/definitivo`.

Script: `run_market_scan.py` — varredura de referência de mercado para
trade-in. Standalone, não usa o pacote `renov_market_scan/` (que existe no
mesmo repo mas é um caminho de código diferente, documentado em
`docs/superpowers/specs/2026-07-28-renov-market-scan-design.md`).

Hoje: 95 dispositivos → 12 lotes de 8 (flag `--lote`, já existe) → 1
subprocess `claude -p` por lote → WebSearch em trocafy.com.br,
cellularstore.com.br, mercadolivre.com.br → xlsx de saída.

Rodada real medida (95 dispositivos, modelo `claude-sonnet-5`, effort
`medium`, só WebSearch): ~26 turnos/lote, ~94s/lote, ~18,8 min total, 0
falhas. Fonte: `out/partials/lote_0001.json` .. `lote_0012.json`.

Não existe `CLAUDE.md` na raiz do repo. Não existe branch `dev` — só `main`
e `feat/market-scan`. Reviewer do PR: `marcelo-maciel`.

## Diagnóstico

O custo dominante não é o overhead de sessão repetido 12×. É o acúmulo de
contexto dentro de cada lote de ~26 turnos: a cada turno o modelo relê o
prompt de sistema + definição de ferramenta + todos os snippets de busca já
retornados. O `cache_read` cresce com o quadrado do número de turnos dentro
do lote, não linearmente com o número de lotes.

Estimativa (não medida ainda — corrigida por este spec no passo 1): ~890k
tokens de `cache_read` por lote, ~10,6M por rodada de 12 lotes. Overhead de
sessão repetido 12×: ~115k, ~1% do total.

Verificado por sonda direta ao `claude -p` (chamada trivial, sem busca real,
modelo Haiku): o envelope de retorno com `--output-format json` já contém
`usage.input_tokens`, `usage.output_tokens`,
`usage.cache_creation_input_tokens`, `usage.cache_read_input_tokens` e
`total_cost_usd`. Não é streaming — um único envelope consolidado por
chamada, sem múltiplas mensagens para somar.

Na mesma sonda, `cache_creation_input_tokens` = 10.488 apareceu mesmo com um
prompt de 3 palavras e nenhuma ferramenta permitida — isso é overhead do
próprio Claude Code CLI (prompt de sistema + definição de ferramentas
internas), não do `PROMPT_TEMPLATE` deste script. Se esse overhead se repete
por lote ou é reaproveitado é o que o baseline (passo 1) vai confirmar.

Contaminação real já identificada no prompt próprio: `max_buscas_por_lote`
(`run_market_scan.py:239`, `len(devices) * len(SOURCES)`) varia por lote (24
para lotes de 8 dispositivos, 21 para o último lote de 7) e fica no meio do
texto, antes da lista de dispositivos — quebra qualquer prefixo estável que
pudesse existir entre lotes.

## Restrições (não negociável nesta sessão)

1. **Não adicionar `--resume` entre lotes.** Faria o lote 12 carregar os
   snippets dos 11 anteriores — o termo quadrático passaria a rodar sobre
   ~310 turnos em vez de 26. Ausência de continuidade entre lotes é
   proposital, confirmado com o usuário.
2. **Não trocar o modelo.** `claude-sonnet-5` fica fixo nesta sessão —
   confirmado com o usuário, não é negociável aqui (diferente do que o brief
   original sugeria como aberto).
3. Não reescrever a arquitetura de busca (tirar WebSearch de dentro do
   modelo) — fora de escopo, seria outra sessão.
4. Não alterar as regras de aceite de anúncio que garantem qualidade do JSON
   de saída (linhas 75-89) além do necessário para enxugar tokens (passo 4)
   — mudança de forma, não de critério.

## Decisões já tomadas (perguntas resolvidas com o usuário)

- Worktree parte de `feat/market-scan` **depois** de commitar as mudanças
  já feitas nesta branch hoje (remoção de WebFetch, troca de fontes,
  remoção de `--max-turns` morto, cache semanal). Caminho:
  `~/Documentos/workspaces/scraper/definitivo-token-cost`, branch
  `refactor/token-cost-instrumentacao`.
- PR final contra `main` (não existe `dev`).
- Cache por dispositivo+semana (passo 5) **substitui** `out/partials/` sem
  camada de compatibilidade — cache antigo pode ser apagado manualmente
  depois da troca.
- Escopo: spec cobre os 9 passos do brief original de uma vez, mesmo sem
  números reais ainda — passos que dependem do baseline (3 em diante) podem
  precisar de ajuste depois que os números reais chegarem; isso é esperado,
  não é falha do spec.

## Passo 1 — Preparação + instrumentação + baseline (bloqueante)

### Worktree

```bash
git add -A && git commit -m "refactor(scan): remove WebFetch, ajusta fontes, cache semanal, tira --max-turns morto"
git worktree add ~/Documentos/workspaces/scraper/definitivo-token-cost \
  -b refactor/token-cost-instrumentacao feat/market-scan
```

Confirmar depois: `pwd`, `git branch --show-current`, `git log -1 --oneline`.

### Instrumentação

Em `run_claude_batch` (`run_market_scan.py:229-275`), o `envelope` do
`claude -p` já é parseado (`json.loads(proc.stdout)`) mas só
`session_id`/`num_turns` são extraídos. Passa a capturar também de
`envelope["usage"]` e `envelope["total_cost_usd"]`:

- `input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
  `cache_read_input_tokens`, `total_cost_usd`.

Esses campos entram em `payload["_meta"]` junto do que já existe
(`session_id`, `duracao_s`, `num_turns`) — continuam dentro de cada
`out/partials/lote_NNNN.json`, sem quebrar o formato existente.

Além disso, gravar uma linha por lote em
`out/metrics/rodada_<ISO8601>.csv` (uma rodada = um arquivo), com as mesmas
colunas + `lote_idx`, `n_dispositivos`.

Ao fim da rodada (`main()`, antes do `notify_slack`), imprimir no stdout:
total por categoria de token, custo total USD, custo por dispositivo, razão
`cache_read / (input + output)`.

### Baseline

Rodar uma vez, completa, 95 dispositivos, com a instrumentação nova. Reportar
os números antes de tocar em qualquer coisa dos passos 2-9 — os valores
deste baseline substituem as estimativas deste spec nos passos seguintes.

## Passo 2 — Prefixo de cache estável

Mover `max_buscas_por_lote` para o final do `PROMPT_TEMPLATE`, junto do
bloco `DISPOSITIVOS:` — o texto de regras/instruções fica idêntico byte a
byte entre todos os lotes, independente de quantos dispositivos ou buscas
aquele lote específico tem.

Critério de verificação (usa a instrumentação do passo 1): olhar
`cache_creation_input_tokens` nos lotes 2..12 do baseline.
- Se já estiver ~0: overhead de sessão do CLI já é reaproveitado
  automaticamente entre subprocessos — este passo era preventivo, documentar
  e seguir.
- Se continuar ~10.488 em todos: a causa não é o nosso prompt (que passou a
  ser byte-idêntico) — é algo do próprio CLI (ex.: nonce de sessão, cwd,
  timestamp interno) fora do nosso controle. Documentar como limitação
  conhecida, não tentar contornar.

## Passo 3 — Enxugar prompt de sistema

Meta: cair de ~10.500 para ≤3.000 tokens (contagem real via
`usage.input_tokens` do baseline, não caracteres).

Candidatos a corte, sem mudar critério de aceite:
- Lista de qualificadores de modelo (linha 76-79): manter a lista, encurtar
  a frase em volta.
- Exemplo de schema JSON (linha 92-93): remover indentação/espaço
  decorativo, é lido por máquina, não por humano.
- Consolidar regras 3-5 (acessórios, preço à vista, novo/lacrado) se
  houver repetição de frase.

Validação: rodar os mesmos 8 dispositivos do primeiro lote com prompt antigo
e novo, comparar contagem de anúncios e valores por dispositivo — resultado
tem que ser equivalente, só o tamanho do prompt muda.

## Passo 4 — Tamanho de lote (A/B, sem código novo)

`--lote` já existe (`run_market_scan.py:445`, default 8) — não é preciso
criar `--devices-per-batch`. O passo é só experimental: rodar o mesmo
conjunto de 8 dispositivos com `--lote 1`, `--lote 2`, `--lote 4`, `--lote 8`,
usando a instrumentação do passo 1 (Passo 1) para tabular
`cache_read` × custo USD × duração × turnos por configuração.

Contraintuitivo mas esperado: lotes menores devem custar menos no total,
porque limitam a profundidade de contexto (o termo quadrático do
diagnóstico), mesmo fazendo mais chamadas de subprocess.

## Passo 5 — Cache por dispositivo + semana

Troca de chave: `out/partials/lote_NNNN.json` (por lote) vira
`out/cache/<slug-dispositivo>_<ano>-W<semana-ISO>.json` (por dispositivo).
Substitui de vez — sem fallback pro formato antigo.

- `slug-dispositivo`: normalização do `erp_code` (já único por dispositivo).
- Recompor lotes (mudar `--lote`) ou lidar com falha parcial deixa de
  invalidar tudo — cada dispositivo é uma entrada de cache independente.
- Mantém a renovação automática de sábado que já existe
  (`reset_cache_if_new_week`), adaptada para apagar chaves da semana
  anterior em vez de `lote_*.json`.

## Passo 6 — Parada antecipada por fonte

Reforçar a instrução do prompt: se uma fonte já rendeu anúncios suficientes
para atingir amostra "boa" (`MIN_SAMPLE_OK = 5`), não consultar as outras
fontes daquele dispositivo.

Limitação técnica: Python não observa as chamadas de WebSearch
individualmente — só o JSON final devolvido pelo modelo. Para "registrar por
dispositivo quantas fontes foram efetivamente consultadas" (pedido
original), o próprio modelo precisa devolver isso — novo campo no schema de
saída, `"fontes_consultadas": ["trocafy", "mercadolivre"]`, por
`erp_code`. É autorrelato do modelo, mesma natureza do campo `observacao`
que já existe — não é uma medição instrumentada, é confiança na resposta.

Ordem das fontes em `SOURCES` (`run_market_scan.py:59`) permanece fixa nesta
sessão; reordenar por taxa de acerto fica documentado como próximo passo,
usando dados do baseline, não decidido agora.

## Passo 7 — Validação

Rodada completa pós-mudanças (passos 2-6 aplicados), comparada ao baseline
do passo 1.

Critério de bloqueio: mediana por dispositivo no xlsx de saída não pode
divergir mais que 5% do baseline. Divergência acima disso é reportada
(quais dispositivos, causa provável) — não é motivo para ajustar o número
até "passar".

Relatório final: tabela antes/depois de tokens por categoria, custo USD,
custo por dispositivo, duração.

## Passo 8 — Git e PR

Commits conventional em português, um por fase:
- `refactor(scan): instrumenta usage e custo real por lote`
- `perf(scan): estabiliza prefixo de cache entre lotes`
- `perf(scan): enxuga prompt de sistema`
- `refactor(scan): cache por dispositivo e semana`
- `perf(scan): parada antecipada por fonte`

PR contra `main`, reviewer `marcelo-maciel`. Descrição do PR inclui a tabela
antes/depois do passo 7. Nunca commit direto em `main` ou `feat/market-scan`
a partir do worktree isolado.

## Critérios de aceite

- `out/metrics/*.csv` existe e traz custo e tokens reais por lote.
- Baseline reportado (números reais, não estimativa) antes de qualquer
  mudança dos passos 2-6.
- Achado sobre `cache_creation_input_tokens` nos lotes 2..12 documentado
  (zero, ou causa externa identificada) — não assumido.
- Prompt de sistema ≤3.000 tokens (contagem real via `usage.input_tokens`).
- Tabela A/B de tamanho de lote entregue com números medidos, usando o
  `--lote` já existente.
- Cache por dispositivo funcionando; recomposição de lote não invalida cache
  de dispositivos não afetados.
- Medianas do xlsx dentro de 5% do baseline, com relatório de qualquer
  divergência.
- Redução de custo total medida e reportada em USD, baseline vs pós-mudança.
