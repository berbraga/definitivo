# Refactor: reduzir consumo de token do market scan — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Instrumentar o custo real de token/USD do `run_market_scan.py` e cortar o consumo dominante — crescimento quadrático de contexto dentro de cada lote — sem trocar modelo, sem `--resume` entre lotes, sem baixar a qualidade do JSON de saída.

**Architecture:** Um único script standalone (`run_market_scan.py`, sem pacote). Cada mudança é uma função pura ou um ponto de extensão testável isoladamente (extração de `usage`/`custo` do envelope, escrita de métricas em CSV, geração de chave de cache, montagem do prompt) chamada de dentro do `main()` existente. Nenhum arquivo novo de produção é criado — só um arquivo de teste novo, seguindo o padrão já usado em `tests/test_claude_cli.py` (mockar `subprocess.run`).

**Tech Stack:** Python 3.11+, `openpyxl`, `pytest` (mock via `unittest.mock`), `uv run` para tudo. Sem dependência nova.

## Global Constraints

- Não adicionar `--resume` entre lotes — pioraria o custo (spec, Restrições #1).
- Não trocar o modelo — `claude-sonnet-5` fixo (spec, Restrições #2).
- Não tirar WebSearch de dentro do modelo — fora de escopo (spec, Restrições #3).
- Não afrouxar as regras de aceite de anúncio em `PROMPT_TEMPLATE` (linhas 75-89 do arquivo original) — só forma, nunca critério (spec, Restrições #4).
- Todo achado sobre `cache_creation_input_tokens` precisa vir de medição real, nunca de suposição (spec, Diagnóstico).
- Commits conventional em português, um por task (spec, Passo 8 / Global).
- PR final contra `main`, reviewer `marcelo-maciel`, nunca commit direto em `main` ou `feat/market-scan` a partir do worktree.

---

## Task 1: Worktree isolado

**Files:** nenhum arquivo de código — só operação git.

- [ ] **Step 1: Confirmar que a branch atual está limpa**

```bash
git status
```
Expected: `nothing to commit, working tree clean` (as mudanças de hoje em `run_market_scan.py` já foram commitadas em `b39e921`, e o spec em `3ae5443`).

- [ ] **Step 2: Criar o worktree**

```bash
git worktree add ~/Documentos/workspaces/scraper/definitivo-token-cost \
  -b refactor/token-cost-instrumentacao feat/market-scan
cd ~/Documentos/workspaces/scraper/definitivo-token-cost
```

- [ ] **Step 3: Confirmar**

```bash
pwd
git branch --show-current
git log -1 --oneline
```
Expected: caminho novo, branch `refactor/token-cost-instrumentacao`, último commit `3ae5443`.

- [ ] **Step 4: Sincronizar ambiente uv no worktree**

```bash
uv sync
cp ../definitivo/.env .env   # mesmo .env do repo original, não versionado
```

Nenhum commit nesta task — é só setup, sem mudança de código.

---

## Task 2: Capturar `usage`/`custo` do envelope do CLI

**Files:**
- Modify: `run_market_scan.py:229-275` (`run_claude_batch`)
- Test: `tests/test_run_market_scan.py` (novo arquivo)

**Interfaces:**
- Produces: `run_claude_batch(devices, model, timeout) -> dict` continua igual na assinatura; o `dict` retornado passa a ter `payload["_meta"]` com as chaves novas `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `total_cost_usd`, além das já existentes `session_id`, `duracao_s`, `num_turns`.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/test_run_market_scan.py`:

```python
import json
from unittest.mock import MagicMock, patch

from run_market_scan import Device, run_claude_batch


def fake_envelope(result_text: str, **usage_overrides) -> str:
    usage = {
        "input_tokens": 9,
        "output_tokens": 185,
        "cache_creation_input_tokens": 10488,
        "cache_read_input_tokens": 20736,
    }
    usage.update(usage_overrides)
    return json.dumps({
        "result": result_text,
        "session_id": "sess-1",
        "is_error": False,
        "num_turns": 3,
        "total_cost_usd": 0.0239836,
        "usage": usage,
    })


def make_device(**overrides) -> Device:
    base = dict(
        row=3, erp="20023A0", name="IPHONE XR 64GB A0",
        manufacturer="APPLE", model="IPHONE XR",
        storage=64, current_price=370.0,
    )
    base.update(overrides)
    return Device(**base)


def test_run_claude_batch_captures_usage_and_cost_in_meta():
    fake_result = json.dumps({"resultados": []})
    fake_proc = MagicMock(returncode=0, stdout=fake_envelope(fake_result), stderr="")
    with patch("subprocess.run", return_value=fake_proc):
        payload = run_claude_batch([make_device()], model="claude-sonnet-5", timeout=900)

    meta = payload["_meta"]
    assert meta["input_tokens"] == 9
    assert meta["output_tokens"] == 185
    assert meta["cache_creation_input_tokens"] == 10488
    assert meta["cache_read_input_tokens"] == 20736
    assert meta["total_cost_usd"] == 0.0239836
    # campos já existentes não podem desaparecer
    assert meta["session_id"] == "sess-1"
    assert meta["num_turns"] == 3
```

- [ ] **Step 2: Rodar e verificar que falha**

```bash
uv run pytest tests/test_run_market_scan.py -v
```
Expected: FAIL — `KeyError: 'input_tokens'` (a chave não existe ainda em `_meta`).

- [ ] **Step 3: Implementar**

Em `run_market_scan.py`, dentro de `run_claude_batch` (linha 269-274), trocar:

```python
    payload = extract_json(envelope.get("result", ""))
    payload["_meta"] = {
        "session_id": envelope.get("session_id"),
        "duracao_s": round(elapsed, 1),
        "num_turns": envelope.get("num_turns"),
    }
    return payload
```

por:

```python
    payload = extract_json(envelope.get("result", ""))
    usage = envelope.get("usage") or {}
    payload["_meta"] = {
        "session_id": envelope.get("session_id"),
        "duracao_s": round(elapsed, 1),
        "num_turns": envelope.get("num_turns"),
        "total_cost_usd": envelope.get("total_cost_usd"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
    }
    return payload
```

Não há streaming em `--output-format json` — `envelope["usage"]` já vem consolidado num único objeto, não é preciso somar múltiplas mensagens (confirmado no spec, Diagnóstico).

- [ ] **Step 4: Rodar e verificar que passa**

```bash
uv run pytest tests/test_run_market_scan.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add run_market_scan.py tests/test_run_market_scan.py
git commit -m "refactor(scan): captura usage e custo real do envelope do claude -p"
```

---

## Task 3: Persistir métricas por lote em CSV

**Files:**
- Modify: `run_market_scan.py` (nova função + chamada em `main()`)
- Test: `tests/test_run_market_scan.py`

**Interfaces:**
- Consumes: `payload["_meta"]` produzido pela Task 2.
- Produces: `write_lote_metrics(csv_path: Path, lote_idx: int, n_dispositivos: int, meta: dict) -> None` — cria o CSV com header na primeira chamada, faz append nas seguintes.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar a `tests/test_run_market_scan.py`:

```python
import csv


def test_write_lote_metrics_creates_header_once_and_appends(tmp_path):
    from run_market_scan import write_lote_metrics

    csv_path = tmp_path / "rodada.csv"
    meta_1 = {
        "input_tokens": 9, "output_tokens": 185,
        "cache_creation_input_tokens": 10488, "cache_read_input_tokens": 20736,
        "total_cost_usd": 0.0239836, "num_turns": 3, "duracao_s": 2.9,
    }
    meta_2 = {**meta_1, "total_cost_usd": 0.05, "num_turns": 26}

    write_lote_metrics(csv_path, lote_idx=1, n_dispositivos=8, meta=meta_1)
    write_lote_metrics(csv_path, lote_idx=2, n_dispositivos=8, meta=meta_2)

    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 2
    assert rows[0]["lote_idx"] == "1"
    assert rows[0]["n_dispositivos"] == "8"
    assert rows[0]["total_cost_usd"] == "0.0239836"
    assert rows[1]["lote_idx"] == "2"
    assert rows[1]["num_turns"] == "26"
```

- [ ] **Step 2: Rodar e verificar que falha**

```bash
uv run pytest tests/test_run_market_scan.py -v -k write_lote_metrics
```
Expected: FAIL — `ImportError: cannot import name 'write_lote_metrics'`

- [ ] **Step 3: Implementar**

Adicionar em `run_market_scan.py`, depois de `run_claude_batch`/`extract_json` (por volta da linha 288, antes da seção "Estatística"):

```python
METRICS_FIELDS = [
    "lote_idx", "n_dispositivos", "input_tokens", "output_tokens",
    "cache_creation_input_tokens", "cache_read_input_tokens",
    "total_cost_usd", "num_turns", "duracao_s",
]


def write_lote_metrics(csv_path: Path, lote_idx: int, n_dispositivos: int, meta: dict) -> None:
    """Uma linha por lote. Cria o header na primeira chamada da rodada."""
    is_new = not csv_path.exists()
    row = {"lote_idx": lote_idx, "n_dispositivos": n_dispositivos, **{
        k: meta.get(k) for k in METRICS_FIELDS if k not in ("lote_idx", "n_dispositivos")
    }}
    with csv_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=METRICS_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)
```

Adicionar `import csv` no topo do arquivo, junto dos outros imports (linha 24, ordem alfabética: depois de `import argparse`, antes de `import json`).

Em `main()`, dentro do loop de lotes (depois de `partial.write_text(...)`, por volta da linha 488), adicionar a chamada:

```python
                    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
                    write_lote_metrics(
                        args.output_dir / "metrics" / f"rodada_{collected_at}.csv",
                        lote_idx=i, n_dispositivos=len(batch), meta=payload["_meta"],
                    )
```

E garantir que o diretório existe — logo depois de `partials_dir.mkdir(exist_ok=True)` (linha 456):

```python
    (args.output_dir / "metrics").mkdir(exist_ok=True)
```

Nota: quando o lote vem do cache (`partial.exists()`), não há `_meta` novo pra métrica — está certo não escrever linha nesse caso, é reexecução sem custo.

- [ ] **Step 4: Rodar e verificar que passa**

```bash
uv run pytest tests/test_run_market_scan.py -v
```
Expected: PASS (todos os testes até aqui)

- [ ] **Step 5: Commit**

```bash
git add run_market_scan.py tests/test_run_market_scan.py
git commit -m "refactor(scan): persiste metricas de custo por lote em out/metrics/"
```

---

## Task 4: Resumo agregado de custo ao fim da rodada

**Files:**
- Modify: `run_market_scan.py` (nova função + wire em `main()`)
- Test: `tests/test_run_market_scan.py`

**Interfaces:**
- Consumes: lista de `dict` no formato de uma linha de métrica (mesmas chaves de `METRICS_FIELDS`, Task 3).
- Produces: `summarize_costs(metric_rows: list[dict]) -> dict` com `total_cost_usd`, `total_cache_read`, `total_cache_creation`, `total_input`, `total_output`, `cache_read_ratio` (cache_read / (input+output), 0.0 se input+output for 0).

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_summarize_costs_aggregates_and_computes_ratio():
    from run_market_scan import summarize_costs

    rows = [
        {"total_cost_usd": 0.02, "input_tokens": 10, "output_tokens": 190,
         "cache_creation_input_tokens": 10000, "cache_read_input_tokens": 20000},
        {"total_cost_usd": 0.05, "input_tokens": 20, "output_tokens": 210,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 800000},
    ]
    summary = summarize_costs(rows)

    assert summary["total_cost_usd"] == 0.07
    assert summary["total_input"] == 30
    assert summary["total_output"] == 400
    assert summary["total_cache_creation"] == 10000
    assert summary["total_cache_read"] == 820000
    assert summary["cache_read_ratio"] == 820000 / 430


def test_summarize_costs_handles_empty_list():
    from run_market_scan import summarize_costs
    summary = summarize_costs([])
    assert summary["total_cost_usd"] == 0
    assert summary["cache_read_ratio"] == 0.0
```

- [ ] **Step 2: Rodar e verificar que falha**

```bash
uv run pytest tests/test_run_market_scan.py -v -k summarize_costs
```
Expected: FAIL — `ImportError: cannot import name 'summarize_costs'`

- [ ] **Step 3: Implementar**

Adicionar em `run_market_scan.py`, depois de `write_lote_metrics`:

```python
def summarize_costs(metric_rows: list[dict]) -> dict:
    """Agrega as linhas de metrics/rodada_*.csv (ou os _meta em memória)
    de uma rodada inteira num resumo único."""
    total_input = sum(r.get("input_tokens") or 0 for r in metric_rows)
    total_output = sum(r.get("output_tokens") or 0 for r in metric_rows)
    total_cache_creation = sum(r.get("cache_creation_input_tokens") or 0 for r in metric_rows)
    total_cache_read = sum(r.get("cache_read_input_tokens") or 0 for r in metric_rows)
    total_cost = sum(r.get("total_cost_usd") or 0 for r in metric_rows)
    denom = total_input + total_output
    return {
        "total_cost_usd": round(total_cost, 4),
        "total_input": total_input,
        "total_output": total_output,
        "total_cache_creation": total_cache_creation,
        "total_cache_read": total_cache_read,
        "cache_read_ratio": (total_cache_read / denom) if denom else 0.0,
    }
```

Em `main()`, coletar as linhas de métrica ao longo do loop (uma lista simples ao lado de `results`/`failures`, por volta da linha 474):

```python
    results: dict[str, Stats] = {}
    failures: list[str] = []
    metric_rows: list[dict] = []
```

E, no ponto onde `write_lote_metrics` é chamado (Task 3), guardar a mesma linha na lista:

```python
                    row = {"lote_idx": i, "n_dispositivos": len(batch), **payload["_meta"]}
                    metric_rows.append(row)
                    write_lote_metrics(
                        args.output_dir / "metrics" / f"rodada_{collected_at}.csv",
                        lote_idx=i, n_dispositivos=len(batch), meta=payload["_meta"],
                    )
```

Por fim, no resumo impresso (antes de `notify_slack(summary)`, por volta da linha 516), acrescentar:

```python
        cost = summarize_costs(metric_rows)
        n_devices_for_cost = len(devices) or 1
        print(
            f"[custo] total US$ {cost['total_cost_usd']:.4f} · "
            f"US$ {cost['total_cost_usd'] / n_devices_for_cost:.4f}/dispositivo · "
            f"input {cost['total_input']} · output {cost['total_output']} · "
            f"cache_creation {cost['total_cache_creation']} · "
            f"cache_read {cost['total_cache_read']} · "
            f"razao cache_read/(input+output) {cost['cache_read_ratio']:.1f}"
        )
```

- [ ] **Step 4: Rodar e verificar que passa**

```bash
uv run pytest tests/test_run_market_scan.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add run_market_scan.py tests/test_run_market_scan.py
git commit -m "refactor(scan): imprime resumo agregado de custo ao fim da rodada"
```

---

## Task 5: Rodada baseline real (bloqueante — checkpoint, sem código)

**Files:** nenhum — só execução e leitura de `out/metrics/*.csv`.

Esta task não avança para a Task 6 sem reportar os números reais. É o gate do spec (Passo 1).

- [ ] **Step 1: Rodar a rodada completa com a instrumentação nova**

No worktree, com o `Template-iPhone.xlsx` do repo original copiado para lá (ou apontando `--input` para o caminho do repo original):

```bash
rm -f out/partials/lote_*.json  # forçar busca real, não cache de rodadas antigas
uv run python run_market_scan.py --input Template-iPhone.xlsx
```

- [ ] **Step 2: Ler o CSV de métricas gerado**

```bash
uv run python -c "
import csv
with open([caminho do rodada_<data>.csv gerado]) as f:
    rows = list(csv.DictReader(f))
for r in rows:
    print(r['lote_idx'], r['cache_creation_input_tokens'], r['cache_read_input_tokens'], r['total_cost_usd'])
"
```

- [ ] **Step 3: Reportar**

Reportar ao usuário, antes de seguir para a Task 6:
- `cache_creation_input_tokens` do lote 1 vs lotes 2-12 (é o critério de verificação da Task 6/spec Passo 2).
- `total_cost_usd` da rodada inteira e por dispositivo.
- `cache_read_ratio` (via `summarize_costs`, ou calculado à mão a partir do CSV).
- Comparar com a estimativa do spec (~890k cache_read/lote, ~10,6M/rodada) — dizer se bate, é maior ou menor.

Sem commit — nenhum código muda aqui, só se roda o que já foi commitado nas Tasks 2-4.

---

## Task 6: Estabilizar prefixo do prompt entre lotes

**Files:**
- Modify: `run_market_scan.py:61-101` (`PROMPT_TEMPLATE`), `run_market_scan.py:236-242` (chamada de `.format`)
- Test: `tests/test_run_market_scan.py`

**Interfaces:**
- Produces: a função que monta o prompt (extraída de dentro de `run_claude_batch`) vira testável isoladamente: `build_prompt(devices: list[Device]) -> str`.

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_build_prompt_stable_prefix_is_identical_regardless_of_batch_size():
    from run_market_scan import build_prompt

    devices_8 = [make_device(erp=f"E{i}", row=i) for i in range(8)]
    devices_7 = [make_device(erp=f"E{i}", row=i) for i in range(7)]

    prompt_8 = build_prompt(devices_8)
    prompt_7 = build_prompt(devices_7)

    marker = "DISPOSITIVOS:"
    prefix_8 = prompt_8[: prompt_8.index(marker)]
    prefix_7 = prompt_7[: prompt_7.index(marker)]

    assert prefix_8 == prefix_7
```

- [ ] **Step 2: Rodar e verificar que falha**

```bash
uv run pytest tests/test_run_market_scan.py -v -k stable_prefix
```
Expected: FAIL — hoje `prefix_8 != prefix_7` porque a frase "no máximo {max_buscas_por_lote} chamadas" (linha 69-71) varia com o tamanho do lote (24 vs 21), e fica antes de `DISPOSITIVOS:`. Também falha por `ImportError` até o `build_prompt` existir.

- [ ] **Step 3: Implementar**

Reescrever o fim do `PROMPT_TEMPLATE` (linhas 69-73 e 99-100) movendo o limite de buscas para junto da lista de dispositivos:

```python
PROMPT_TEMPLATE = """Você é um coletor de referência de preços de celulares seminovos no Brasil.

Para CADA dispositivo da lista abaixo, use WebSearch para encontrar anúncios
de aparelhos USADOS ou SEMINOVOS à venda no Brasil, em: {sources}.

Extraia o preço APENAS do snippet/resumo retornado pela busca. Não abra
páginas. Se o snippet não mostrar preço claro, descarte o anúncio.

Faça NO MÁXIMO 1 busca (WebSearch) por dispositivo em cada uma das fontes
listadas. Não repita uma busca que já não trouxe resultado útil. Assim que
tiver anúncios suficientes para um dispositivo, pare de buscar por ele e
siga para o próximo.

REGRAS DE ACEITE DE ANÚNCIO (aplicar antes de incluir):
1. Modelo exato. O conjunto de qualificadores do título (pro, max, plus, mini,
   ultra, neo, fusion, lite, fe, se, power, play, air) deve ser IGUAL ao do
   modelo alvo. "iPhone 13" NÃO aceita "iPhone 13 Pro Max" e vice-versa.
   "5g" é tolerado como opcional.
2. Capacidade explícita no título ou na URL, igual à do alvo (1024 = 1TB).
   Sem capacidade explícita, descarte.
3. Descarte acessórios e peças: capa, capinha, case, película, vidro, tela,
   display, touch, bateria, placa, flex, conector, carcaça, aro, tampa,
   câmera, alto-falante, botão, "para retirada", "não liga", réplica, clone,
   similar, carregador, fone, cabo, chip, suporte.
4. Preço À VISTA. Rejeite valor precedido de "12x", "10 x", "sem juros".
   Converta "R$ 1.234,56" para 1234.56 (número, ponto decimal).
5. Descarte "novo"/"lacrado". Aceite: seminovo, usado, vitrine, recondicionado.
6. No máximo {max_por_dispositivo} anúncios por dispositivo, de fontes variadas.

RESPONDA APENAS COM JSON, sem markdown, sem code fence, sem preâmbulo:
{{"resultados":[{{"erp_code":"...","anuncios":[{{"fonte":"olx","titulo":"...",
"preco_brl":1234.56,"condicao":"usado","url":"https://..."}}],"observacao":""}}]}}

Se não achar nada válido para um dispositivo, devolva "anuncios": [] e explique
em "observacao". NÃO calcule mediana, mínimo ou máximo. NÃO edite arquivos.
NÃO faça perguntas — não há ninguém para responder.

DISPOSITIVOS (máximo {max_buscas_por_lote} chamadas de WebSearch no total
para este lote):
{devices}
"""
```

(`{sources}` e `{max_por_dispositivo}` continuam constantes entre lotes — só `{max_buscas_por_lote}` e `{devices}` variam, e os dois agora ficam depois do marcador `DISPOSITIVOS:`.)

Extrair a montagem do prompt para uma função própria, e usá-la dentro de `run_claude_batch` (linhas 229-242):

```python
def build_prompt(devices: list[Device]) -> str:
    return PROMPT_TEMPLATE.format(
        sources=", ".join(SOURCES),
        max_por_dispositivo=6,
        max_buscas_por_lote=len(devices) * len(SOURCES),
        devices=json.dumps([d.as_query_dict() for d in devices],
                           ensure_ascii=False, indent=2),
    )


def run_claude_batch(devices: list[Device], model: str, timeout: int) -> dict:
    """Uma invocação headless do CLI para um lote. Prompt vai por stdin.

    Sem --max-turns: a versão instalada do CLI (2.1.220) nao expõe essa
    flag (confirmado em `claude --help`) — passá-la é ignorado em silêncio.
    O limite de buscas por lote é imposto por instrução no próprio prompt.
    """
    prompt = build_prompt(devices)

    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--allowedTools", "WebSearch",
        "--model", model,
        "--effort", "medium",
    ]
    ...
```

(resto de `run_claude_batch` sem mudança.)

- [ ] **Step 4: Rodar e verificar que passa**

```bash
uv run pytest tests/test_run_market_scan.py -v
```
Expected: PASS (todos)

- [ ] **Step 5: Commit**

```bash
git add run_market_scan.py tests/test_run_market_scan.py
git commit -m "perf(scan): estabiliza prefixo do prompt entre lotes de tamanhos diferentes"
```

- [ ] **Step 6: Verificação real (usa a instrumentação da Task 2-4)**

```bash
rm -f out/partials/lote_*.json
uv run python run_market_scan.py --input Template-iPhone.xlsx --limite 16 --lote 8
```

Ler o CSV novo em `out/metrics/` e comparar `cache_creation_input_tokens` do lote 1 vs lote 2:
- Se lote 2 caiu para ~0: o prefixo já estava sendo aproveitado pelo CLI automaticamente (overhead do CLI, fora do nosso prompt) — ESSE PASSO não mudou nada nesse número, e é isso que se espera: a contaminação que corrigimos era só a nossa, o overhead do próprio CLI segue sendo o mesmo item do diagnóstico, resolvido ou não à parte.
- Se lote 2 continuar ~igual ao lote 1: documentar como limitação do CLI, fora do nosso controle (spec, Passo 2, segundo cenário).

Reportar o resultado ao usuário antes de seguir para a Task 7.

---

## Task 7: Enxugar prompt de sistema

**Files:**
- Modify: `run_market_scan.py:61-98` (`PROMPT_TEMPLATE`, versão da Task 6)
- Test: `tests/test_run_market_scan.py`

**Interfaces:** nenhuma nova — `build_prompt` (Task 6) continua com a mesma assinatura, só o texto que ela formata fica mais curto.

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_build_prompt_stays_under_token_budget():
    from run_market_scan import build_prompt

    devices = [make_device(erp=f"E{i}", row=i) for i in range(8)]
    prompt = build_prompt(devices)
    # aproximação grosseira (chars/4) só pra travar regressão de tamanho;
    # a medição real de tokens vem do usage.input_tokens na Task 5/8.
    approx_tokens = len(prompt) / 4
    assert approx_tokens < 3000, f"~{approx_tokens:.0f} tokens, meta é <3000"
```

- [ ] **Step 2: Rodar e verificar que falha**

```bash
uv run pytest tests/test_run_market_scan.py -v -k token_budget
```
Expected: FAIL — prompt atual (regras + schema + instruções) passa de 3000 tokens estimados.

- [ ] **Step 3: Implementar**

Enxugar `PROMPT_TEMPLATE`, mantendo todo critério de aceite, só cortando redundância de frase e formatação do schema de exemplo:

```python
PROMPT_TEMPLATE = """Você é um coletor de referência de preços de celulares seminovos no Brasil.

Para CADA dispositivo, use WebSearch (fontes: {sources}) para achar anúncios
de aparelhos USADOS/SEMINOVOS. Extraia o preço só do snippet da busca — não
abra páginas; sem preço claro no snippet, descarte o anúncio.

No máximo 1 busca por dispositivo/fonte. Não repita busca sem resultado útil;
com anúncios suficientes para um dispositivo, siga para o próximo.

REGRAS DE ACEITE:
1. Qualificador do título (pro/max/plus/mini/ultra/neo/fusion/lite/fe/se/
   power/play/air) IGUAL ao alvo. "13" não aceita "13 Pro Max". "5g" opcional.
2. Capacidade explícita (título ou URL) igual ao alvo (1024=1TB); sem isso, descarte.
3. Descarte acessórios e peças: capa, capinha, case, película, vidro, tela,
   display, touch, bateria, placa, flex, conector, carcaça, aro, tampa,
   câmera, alto-falante, botão, "para retirada", "não liga", réplica, clone,
   similar, carregador, fone, cabo, chip, suporte.
4. Preço à vista — rejeite "12x"/"sem juros". "R$ 1.234,56" → 1234.56 (float).
5. Descarte "novo"/"lacrado"; aceite seminovo/usado/vitrine/recondicionado.
6. Máximo 6 anúncios por dispositivo, fontes variadas.

JSON puro, sem markdown:
{{"resultados":[{{"erp_code":"...","anuncios":[{{"fonte":"...","titulo":"...","preco_brl":0.0,"condicao":"usado","url":"..."}}],"observacao":""}}]}}

Sem dado válido: "anuncios":[] + "observacao". Não calcule mediana/min/max.
Não edite arquivos. Não faça perguntas.

DISPOSITIVOS (máx {max_buscas_por_lote} buscas totais neste lote):
{devices}
"""
```

Atualizar `build_prompt` — `max_por_dispositivo` deixou de ser parâmetro do template (o "6" virou literal na regra 6); ajustar a chamada:

```python
def build_prompt(devices: list[Device]) -> str:
    return PROMPT_TEMPLATE.format(
        sources=", ".join(SOURCES),
        max_buscas_por_lote=len(devices) * len(SOURCES),
        devices=json.dumps([d.as_query_dict() for d in devices],
                           ensure_ascii=False, indent=2),
    )
```

- [ ] **Step 4: Rodar e verificar que passa**

```bash
uv run pytest tests/test_run_market_scan.py -v
```
Expected: PASS (todos, incluindo o teste de prefixo estável da Task 6 — o marcador `DISPOSITIVOS:` continua existindo)

- [ ] **Step 5: Verificação de equivalência (real, não automatizada)**

```bash
rm -f out/partials/lote_0001.json
uv run python run_market_scan.py --input Template-iPhone.xlsx --limite 8 --lote 8
```

Comparar `out/partials/lote_0001.json` novo com o da Task 5 (baseline) para os mesmos 8 dispositivos: contagem de anúncios por `erp_code` e valores de preço devem ficar equivalentes (não precisam ser idênticos — é busca ao vivo — mas na mesma faixa de status `ok`/`amostra_baixa`/`insuficiente`). Reportar `usage.input_tokens` real do lote (via CSV de métricas) confirmando queda para ≤3000.

- [ ] **Step 6: Commit**

```bash
git add run_market_scan.py tests/test_run_market_scan.py
git commit -m "perf(scan): enxuga prompt de sistema para caber no orcamento de token"
```

---

## Task 8: A/B de tamanho de lote (operacional, sem código novo)

**Files:** nenhum — `--lote` já existe (`run_market_scan.py:445`).

- [ ] **Step 1: Rodar o mesmo conjunto de 8 dispositivos em 4 configurações**

```bash
for n in 1 2 4 8; do
  rm -f out/partials/lote_*.json
  uv run python run_market_scan.py --input Template-iPhone.xlsx --limite 8 --lote "$n"
  mv out/metrics/rodada_*.csv "out/metrics/ab_lote_${n}.csv"
done
```

- [ ] **Step 2: Tabular**

```bash
uv run python -c "
import csv, glob
for path in sorted(glob.glob('out/metrics/ab_lote_*.csv')):
    rows = list(csv.DictReader(open(path)))
    cache_read = sum(int(r['cache_read_input_tokens'] or 0) for r in rows)
    cost = sum(float(r['total_cost_usd'] or 0) for r in rows)
    turns = sum(int(r['num_turns'] or 0) for r in rows)
    print(path, 'cache_read=', cache_read, 'custo=', round(cost,4), 'turnos=', turns)
"
```

- [ ] **Step 3: Reportar**

Tabela lote-size × cache_read × custo USD × turnos totais, confirmando (ou refutando) a expectativa do spec de que lotes menores custam menos no total. Sem commit — nenhum código muda; se o resultado sugerir trocar o `--lote` default (linha 445, hoje 8), abrir isso como decisão explícita com o usuário antes de mudar.

---

## Task 9: Cache por dispositivo + semana

**Files:**
- Modify: `run_market_scan.py:404-434` (seção de cache semanal), `run_market_scan.py:454-500` (`main()`, loop de lotes)
- Test: `tests/test_run_market_scan.py`

**Interfaces:**
- Produces: `device_cache_path(cache_dir: Path, erp: str, week: date) -> Path`; `slugify_erp(erp: str) -> str`.

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_device_cache_path_uses_slug_and_iso_week(tmp_path):
    from datetime import date
    from run_market_scan import device_cache_path

    path = device_cache_path(tmp_path, erp="20023A0", week=date(2026, 8, 1))
    assert path.parent == tmp_path
    assert "20023A0" in path.name
    assert "2026-W" in path.name
    assert path.suffix == ".json"


def test_slugify_erp_keeps_alphanumeric_only():
    from run_market_scan import slugify_erp
    assert slugify_erp("20023A0") == "20023A0"
    assert slugify_erp("AB/CD 12") == "AB-CD-12"
```

- [ ] **Step 2: Rodar e verificar que falha**

```bash
uv run pytest tests/test_run_market_scan.py -v -k "device_cache_path or slugify_erp"
```
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implementar**

Em `run_market_scan.py`, substituir a seção "Cache semanal" (linhas 404-434) por:

```python
# --------------------------------------------------------------------------
# Cache por dispositivo + semana (renova todo sábado)
# --------------------------------------------------------------------------

def _last_saturday(today: date) -> date:
    """Sábado da semana corrente (hoje, se hoje já for sábado)."""
    days_since_saturday = (today.weekday() - 5) % 7  # Monday=0 .. Saturday=5
    return today - timedelta(days=days_since_saturday)


def slugify_erp(erp: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", erp).strip("-")


def device_cache_path(cache_dir: Path, erp: str, week: date) -> Path:
    iso_year, iso_week, _ = week.isocalendar()
    return cache_dir / f"{slugify_erp(erp)}_{iso_year}-W{iso_week:02d}.json"


def prune_stale_weeks(cache_dir: Path, current_week: date) -> int:
    """Remove entradas de semanas anteriores à atual. Retorna quantas."""
    _, current_iso_week, _ = current_week.isocalendar()
    current_year, _, _ = current_week.isocalendar()
    current_tag = f"{current_year}-W{current_iso_week:02d}"
    removed = 0
    for cached in cache_dir.glob("*.json"):
        if current_tag not in cached.name:
            cached.unlink()
            removed += 1
    return removed
```

Em `main()`, trocar (linha 455-457):

```python
    partials_dir = args.output_dir / "partials"
    partials_dir.mkdir(exist_ok=True)
    reset_cache_if_new_week(partials_dir)
```

por:

```python
    cache_dir = args.output_dir / "cache"
    cache_dir.mkdir(exist_ok=True)
    current_week = _last_saturday(datetime.now(timezone.utc).date())
    removed = prune_stale_weeks(cache_dir, current_week)
    if removed:
        print(f"[cache] nova semana (sábado {current_week}) — {removed} entrada(s) antiga(s) removida(s)")
```

E o loop de lotes (linhas 477-494) passa a checar/gravar cache por dispositivo em vez de por lote — cada dispositivo do lote é resolvido individualmente contra `device_cache_path`, e só os que não têm cache entram numa chamada real ao `claude -p` (agrupados como hoje, em lotes de `--lote` dispositivos, mas filtrando quem já tem cache da semana):

```python
        for i, batch in enumerate(batches, start=1):
            pending = [d for d in batch
                       if not device_cache_path(cache_dir, d.erp, current_week).exists()]
            cached_devices = [d for d in batch if d not in pending]

            for device in cached_devices:
                cache_path = device_cache_path(cache_dir, device.erp, current_week)
                entry = json.loads(cache_path.read_text(encoding="utf-8"))
                results[device.erp] = compute_stats(entry.get("anuncios", []))
                # Nota (revisada na Task 10): compute_stats ainda recebe a lista de
                # anúncios aqui, não o `entry` inteiro — a assinatura só muda na Task 10.

            if not pending:
                print(f"[{i}/{len(batches)}] cache (todos os {len(batch)} dispositivos)")
                continue

            print(f"[{i}/{len(batches)}] {', '.join(d.name for d in pending)}")
            try:
                payload = run_claude_batch(pending, args.model, args.timeout)
                row = {"lote_idx": i, "n_dispositivos": len(pending), **payload["_meta"]}
                metric_rows.append(row)
                write_lote_metrics(
                    args.output_dir / "metrics" / f"rodada_{collected_at}.csv",
                    lote_idx=i, n_dispositivos=len(pending), meta=payload["_meta"],
                )
            except Exception as exc:  # noqa: BLE001
                msg = f"lote {i}: {exc}"
                print(f"    FALHA: {msg}", file=sys.stderr)
                failures.append(msg)
                time.sleep(args.pausa * 4)
                continue

            for entry in payload.get("resultados", []):
                erp = str(entry.get("erp_code"))
                st = compute_stats(entry.get("anuncios", []))  # Task 10 troca para compute_stats(entry)
                results[erp] = st
                device_cache_path(cache_dir, erp, current_week).write_text(
                    json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8"
                )

            time.sleep(args.pausa)
```

(remove-se também o antigo `partials_dir`/`.cache_week` — sem camada de compatibilidade, como decidido com o usuário.)

- [ ] **Step 4: Rodar e verificar que passa**

```bash
uv run pytest tests/test_run_market_scan.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add run_market_scan.py tests/test_run_market_scan.py
git commit -m "refactor(scan): cache por dispositivo e semana, substitui cache por lote"
```

---

## Task 10: Parada antecipada por fonte + autorrelato de fontes consultadas

**Files:**
- Modify: `run_market_scan.py` (`PROMPT_TEMPLATE`, `Stats`, `compute_stats`, `write_report`)
- Test: `tests/test_run_market_scan.py`

**Interfaces:**
- Modifica `Stats` (linhas 133-143): novo campo `sources_queried: list[str] = field(default_factory=list)`.
- `compute_stats` passa a ler `entry.get("fontes_consultadas")` além de `anuncios` — assinatura muda para `compute_stats(entry: dict) -> Stats` (antes recebia só a lista de anúncios).

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_compute_stats_captures_sources_queried_from_model_self_report():
    from run_market_scan import compute_stats

    entry = {
        "anuncios": [
            {"preco_brl": 900.0, "url": "https://x", "fonte": "mercadolivre"},
        ],
        "fontes_consultadas": ["mercadolivre"],
    }
    stats = compute_stats(entry)
    assert stats.sources_queried == ["mercadolivre"]


def test_compute_stats_defaults_sources_queried_to_empty_list_when_absent():
    from run_market_scan import compute_stats
    stats = compute_stats({"anuncios": []})
    assert stats.sources_queried == []
```

- [ ] **Step 2: Rodar e verificar que falha**

```bash
uv run pytest tests/test_run_market_scan.py -v -k sources_queried
```
Expected: FAIL — `compute_stats` hoje recebe uma lista (`entry.get("anuncios", [])` já desembrulhada no `main()`), não o `entry` inteiro; `Stats` não tem `sources_queried`.

- [ ] **Step 3: Implementar**

Em `Stats` (linhas 133-143), acrescentar o campo:

```python
@dataclass
class Stats:
    n: int = 0
    minimum: float | None = None
    link_min: str = ""
    median: float | None = None
    maximum: float | None = None
    link_max: str = ""
    sources: str = ""
    status: str = "sem_dados"
    listings: list[dict] = field(default_factory=list)
    sources_queried: list[str] = field(default_factory=list)
```

Trocar a assinatura de `compute_stats` (linha 294) para receber o `entry` completo em vez da lista de anúncios já extraída:

```python
def compute_stats(entry: dict) -> Stats:
    listings = entry.get("anuncios", [])
    sources_queried = entry.get("fontes_consultadas", [])
    valid = []
    for item in listings:
        try:
            price = float(item["preco_brl"])
        except (KeyError, TypeError, ValueError):
            continue
        if price < 80 or not str(item.get("url", "")).startswith("http"):
            continue
        valid.append({**item, "preco_brl": price})

    if not valid:
        return Stats(status="sem_dados", sources_queried=sources_queried)

    prices = sorted(v["preco_brl"] for v in valid)
    if len(prices) >= 4:
        q1, q3 = statistics.quantiles(prices, n=4)[0], statistics.quantiles(prices, n=4)[2]
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        valid = [v for v in valid if lo <= v["preco_brl"] <= hi] or valid

    valid.sort(key=lambda v: v["preco_brl"])
    n = len(valid)
    status = "ok" if n >= MIN_SAMPLE_OK else (
        "amostra_baixa" if n >= MIN_SAMPLE_LOW else "insuficiente")

    return Stats(
        n=n,
        minimum=valid[0]["preco_brl"],
        link_min=valid[0]["url"],
        median=round(statistics.median(v["preco_brl"] for v in valid), 2),
        maximum=valid[-1]["preco_brl"],
        link_max=valid[-1]["url"],
        sources=", ".join(sorted({str(v.get("fonte", "?")) for v in valid})),
        status=status,
        listings=valid,
        sources_queried=sources_queried,
    )
```

Atualizar as duas chamadas de `compute_stats` (uma na Task 9, dentro do loop de cache; outra no processamento de `payload.get("resultados", [])`) para passar `entry` inteiro em vez de `entry.get("anuncios", [])`:

```python
                st = compute_stats(entry)
```

E salvar o `entry` inteiro no cache por dispositivo (já é o caso na Task 9 — `device_cache_path(...).write_text(json.dumps(entry, ...))` já grava o dicionário completo, incluindo `fontes_consultadas` se existir).

Atualizar `PROMPT_TEMPLATE` (versão da Task 7) acrescentando a instrução de parada por fonte e o campo novo no schema:

```python
No máximo 1 busca por dispositivo/fonte. Não repita busca sem resultado útil;
com anúncios suficientes para um dispositivo, siga para o próximo. Se uma
fonte já rendeu anúncios válidos suficientes (5+) pra esse dispositivo, não
precisa consultar as outras fontes dele.
```

```python
JSON puro, sem markdown:
{{"resultados":[{{"erp_code":"...","anuncios":[{{"fonte":"...","titulo":"...","preco_brl":0.0,"condicao":"usado","url":"..."}}],"fontes_consultadas":["..."],"observacao":""}}]}}
```

- [ ] **Step 4: Rodar e verificar que passa**

```bash
uv run pytest tests/test_run_market_scan.py -v
```
Expected: PASS (todos)

- [ ] **Step 5: Commit**

```bash
git add run_market_scan.py tests/test_run_market_scan.py
git commit -m "perf(scan): para busca por fonte quando amostra ja esta boa"
```

---

## Task 11: Validação final vs baseline

**Files:**
- Test/script: `tests/test_run_market_scan.py` (função de comparação testável) + execução real.

**Interfaces:**
- Produces: `median_divergence_pct(baseline: dict[str, float], current: dict[str, float]) -> dict[str, float]` — recebe `{erp_code: mediana}` de duas rodadas, retorna `{erp_code: pct_diff}` só para chaves presentes nas duas.

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_median_divergence_pct_flags_devices_over_threshold():
    from run_market_scan import median_divergence_pct

    baseline = {"A": 1000.0, "B": 500.0, "C": 200.0}
    current = {"A": 1030.0, "B": 800.0, "C": 200.0}  # +3%, +60%, +0%

    diffs = median_divergence_pct(baseline, current)

    assert diffs["A"] == pytest.approx(3.0, abs=0.1)
    assert diffs["B"] == pytest.approx(60.0, abs=0.1)
    assert diffs["C"] == 0.0


def test_median_divergence_pct_skips_devices_missing_in_either_side():
    from run_market_scan import median_divergence_pct
    diffs = median_divergence_pct({"A": 100.0}, {"B": 100.0})
    assert diffs == {}
```

- [ ] **Step 2: Rodar e verificar que falha**

```bash
uv run pytest tests/test_run_market_scan.py -v -k median_divergence
```
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implementar**

```python
def median_divergence_pct(baseline: dict[str, float], current: dict[str, float]) -> dict[str, float]:
    diffs = {}
    for erp, base_value in baseline.items():
        if erp not in current or not base_value:
            continue
        diffs[erp] = abs(current[erp] - base_value) / base_value * 100
    return diffs
```

- [ ] **Step 4: Rodar e verificar que passa**

```bash
uv run pytest tests/test_run_market_scan.py -v
```
Expected: PASS (todos)

- [ ] **Step 5: Commit**

```bash
git add run_market_scan.py tests/test_run_market_scan.py
git commit -m "test(scan): adiciona comparacao de divergencia de mediana vs baseline"
```

- [ ] **Step 6: Rodar a validação real**

```bash
rm -f out/cache/*.json
uv run python run_market_scan.py --input Template-iPhone.xlsx
```

Extrair medianas por `erp_code` do xlsx gerado (aba principal, coluna `mediana`) desta rodada e da rodada baseline (Task 5), aplicar `median_divergence_pct`, reportar:
- Total tokens/custo desta rodada vs baseline (tabela antes/depois, pedida no spec Passo 7).
- Qualquer `erp_code` com divergência > 5% — investigar causa (amostra diferente por ser busca ao vivo, ou regressão real) antes de declarar a task concluída. Não ajustar número pra "passar".

---

## Task 12: PR

**Files:** nenhum — git/GitHub apenas.

- [ ] **Step 1: Push da branch**

```bash
git push -u origin refactor/token-cost-instrumentacao
```

- [ ] **Step 2: Abrir o PR**

```bash
gh pr create --base main --reviewer marcelo-maciel --title "perf(scan): reduz consumo de token do market scan" --body "$(cat <<'EOF'
## O que mudou
- Instrumenta usage/custo real do claude -p (antes descartado)
- Estabiliza prefixo do prompt entre lotes
- Enxuga prompt de sistema
- Cache por dispositivo+semana (antes por lote)
- Para busca por fonte quando amostra já está boa

## Antes / depois (baseline real, Template-iPhone.xlsx)
[preencher com a tabela da Task 11 — tokens por categoria, custo USD, custo/dispositivo, duração]

## Validação
- Medianas por dispositivo dentro de 5% do baseline (Task 11)
- Suite de testes: `uv run pytest tests/test_run_market_scan.py -v`
EOF
)"
```

- [ ] **Step 3: Confirmar**

```bash
gh pr view --web
```

---

## Self-Review

**Cobertura do spec:** Passo 1 (worktree) → Task 1. Instrumentação → Tasks 2-4. Baseline bloqueante → Task 5. Prefixo de cache → Task 6. Enxugar prompt → Task 7. Lote configurável (A/B) → Task 8. Cache por dispositivo+semana → Task 9. Parada antecipada por fonte → Task 10. Validação → Task 11. Git/PR → Task 12. Todos os 9 passos do spec, mais a separação do baseline como gate próprio (Task 5) e validação/git como tasks finais separadas.

**Placeholders:** nenhum `TBD`/`TODO` — a única lacuna intencional é o corpo do PR (Task 12, Step 2), marcado explicitamente como "preencher com a tabela da Task 11" porque esse número só existe depois da Task 11 rodar de verdade; não é um placeholder de plano incompleto, é um valor que literalmente não existe até a Task 11 terminar.

**Consistência de tipos:** `compute_stats` muda de assinatura na Task 10 (recebia lista de anúncios, passa a receber o `entry` completo). Encontrado e corrigido durante a auto-revisão: a Task 9 originalmente antecipava a assinatura nova (`compute_stats(entry)`) nos dois pontos onde chama a função — corrigido para a forma antiga (`compute_stats(entry.get("anuncios", []))`) nas duas ocorrências, com comentário no código apontando que a Task 10 é quem faz a troca.
