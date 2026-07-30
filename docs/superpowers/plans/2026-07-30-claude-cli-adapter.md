# ClaudeCliAdapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Messages-API-based `AnthropicSearchAdapter` (billed per token/search, requires `ANTHROPIC_API_KEY`) with `ClaudeCliAdapter`, which drives `claude -p` in headless mode authenticated by the CLI's own session (browser login or `CLAUDE_CODE_OAUTH_TOKEN`) — no API key anywhere in the flow — plus a Slack notification on completion, failure, or interruption.

**Architecture:** `ClaudeCliAdapter` implements the existing `SearchAdapter` Protocol (`collect/base.py`) unchanged — one subprocess call per (model, source) pair, serial (no semaphore), prompt via stdin, JSON parsed from the CLI's `--output-format json` envelope. Everything downstream (cache, matcher, blacklist, stats, report, template copy) is untouched. `AnthropicSearchAdapter`, `cost.py`, and the `anthropic` dependency are removed. A new `notify.py` module sends a Slack webhook from `cli.py`'s `try/except/finally`, never from the model.

**Tech Stack:** Python 3.11, `subprocess`, `pydantic`, `typer`, `urllib` (stdlib, for Slack), `pytest` + `unittest.mock`.

## Global Constraints

- All code/identifiers/docstrings in English; CLI messages, report columns, discard reasons stay in pt-BR.
- No test may invoke the real `claude` binary or touch the network. Every test mocks `subprocess.run`.
- `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` must never reach a subprocess launched by this adapter, even if present in the parent environment — remove explicitly, never assume absence.
- Prompt payload always goes to `claude -p` via stdin (`input=` kwarg), never via `argv`.
- `ClaudeCliAdapter.search()` is serial: no `asyncio.Semaphore`, ignores `settings.concurrency`.
- `cited_text` from this adapter is model self-report, not an API-verified citation — document this explicitly in the docstring, never claim it is a citation.
- `ruff check .` and `mypy renov_market_scan` (strict mode, per `pyproject.toml`) must stay clean after every task.
- Exact CLI flags (`--allowedTools`, `--max-turns`, etc.) must be confirmed against `claude --help` during Task 1 — do not assume they are stable across versions.

---

### Task 1: Confirm `claude` CLI flags and auth-status contract

This task produces no application code — it produces a short findings file that Task 2 depends on for exact flag names and exit-code behavior. Skipping it means Task 2 guesses at flags that may not exist in the installed CLI version.

**Files:**
- Create: `docs/superpowers/sdd/2026-07-30-claude-cli-adapter/cli-flags-findings.md`

**Interfaces:**
- Consumes: nothing.
- Produces: a markdown file with the exact flag names and auth-status exit codes that Task 2's implementer reads verbatim. Must record:
  - Output of `claude --help` (or the relevant excerpt) confirming: the flag for prompt mode (`-p` or `--print`), `--output-format json` (or its current name), the flag to restrict tools (`--allowedTools` or its current name), `--max-turns`, `--model`.
  - Output of `claude auth status` when logged in (expected exit code 0) and a description of what it prints.
  - Confirmation that `claude auth status` returns non-zero when logged out (this may have to be inferred from `claude --help` or `claude auth status --help` if there is no logged-out environment available to test — in that case, state that assumption explicitly in the file).

- [ ] **Step 1: Run `claude --help` and save relevant excerpts**

```bash
claude --help > /tmp/claude-help.txt 2>&1
claude -p --help > /tmp/claude-p-help.txt 2>&1 || true
cat /tmp/claude-help.txt /tmp/claude-p-help.txt
```

Read the output. Identify the exact flags for: headless prompt mode, JSON output format, tool allowlist, max turns, model selection.

- [ ] **Step 2: Run `claude auth status` and record its behavior**

```bash
claude auth status; echo "exit code: $?"
```

- [ ] **Step 3: Write the findings file**

Write `docs/superpowers/sdd/2026-07-30-claude-cli-adapter/cli-flags-findings.md` with this structure (fill in the real values found in Steps 1-2; do not leave any placeholder):

```markdown
# Claude CLI flags — confirmed 2026-07-30

## Headless prompt mode
Flag: <exact flag, e.g. `-p` / `--print`>

## Output format
Flag: <exact flag and value, e.g. `--output-format json`>
Envelope shape observed: <keys present, e.g. result, session_id, is_error, num_turns>

## Tool allowlist
Flag: <exact flag, e.g. `--allowedTools`>
Value format: <e.g. comma-separated tool names, "WebSearch,WebFetch">

## Other flags used
- Max turns: <exact flag>
- Model: <exact flag>

## `claude auth status`
Exit code when logged in: <0 or observed value>
Exit code when logged out: <observed or "not tested — assumed 1 per CLI docs, confirm before relying on this in production">
Stdout when logged in (first line): <example>
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/sdd/2026-07-30-claude-cli-adapter/cli-flags-findings.md
git commit -m "docs: confirm claude CLI flags and auth status contract for ClaudeCliAdapter"
```

---

### Task 2: `ClaudeCliAdapter` — preflight, subprocess call, JSON extraction

**Files:**
- Create: `renov_market_scan/collect/claude_cli.py`
- Test: `tests/test_claude_cli.py`

**Interfaces:**
- Consumes: `SearchOutcome` and `SearchAdapter` Protocol from `renov_market_scan/collect/base.py` (`SearchOutcome(listings: list[Listing], status: str, payload: dict[str, Any] = {})`); `Settings` from `renov_market_scan/config.py` (Task 5 removes the Messages-API-only fields, but `settings.model` and `settings.concurrency` still exist — this task must not reference `settings.anthropic_api_key`, `settings.web_search_tool_version`, or `settings.max_uses_per_call`); `Query`/`Listing`/`Condition` from `renov_market_scan/models.py`.
- Produces: `class ClaudeCliAdapter` with `async def search(self, queries: list[Query]) -> SearchOutcome`; module-level `def preflight() -> None` (raises `RuntimeError` on failure, never calls `sys.exit` — this runs inside a library module, not a CLI entrypoint); module-level `def child_env() -> dict[str, str]`; module-level `def extract_json(text: str) -> dict[str, Any]` (raises `ValueError` if no JSON object found); module-level constant `PLAN_LIMIT_PATTERNS: tuple[str, ...]` used by Task 3's failure-classification logic (Task 3 imports this from this module).

Use the exact flag names recorded in `docs/superpowers/sdd/2026-07-30-claude-cli-adapter/cli-flags-findings.md` (Task 1) when building the subprocess command — do not guess.

- [ ] **Step 1: Write the failing tests for `preflight()` and `child_env()`**

```python
# tests/test_claude_cli.py
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from renov_market_scan.collect.claude_cli import child_env, preflight


def test_preflight_raises_when_claude_is_not_on_path():
    with patch("shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="claude"):
            preflight()


def test_preflight_raises_when_auth_status_is_not_zero():
    fake_result = MagicMock(returncode=1, stdout="", stderr="not logged in")
    with (
        patch("shutil.which", return_value="/usr/local/bin/claude"),
        patch("subprocess.run", return_value=fake_result),
    ):
        with pytest.raises(RuntimeError, match="autenticado"):
            preflight()


def test_preflight_passes_when_claude_is_present_and_authenticated():
    fake_result = MagicMock(returncode=0, stdout="Logged in as user@example.com", stderr="")
    with (
        patch("shutil.which", return_value="/usr/local/bin/claude"),
        patch("subprocess.run", return_value=fake_result),
    ):
        preflight()  # must not raise


def test_child_env_removes_api_key_variables(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-removed")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-should-be-removed")
    monkeypatch.setenv("SOME_OTHER_VAR", "keep-me")
    env = child_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["SOME_OTHER_VAR"] == "keep-me"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest tests/test_claude_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'renov_market_scan.collect.claude_cli'`

- [ ] **Step 3: Implement `preflight()` and `child_env()`**

```python
# renov_market_scan/collect/claude_cli.py
"""Production adapter: drives the Claude Code CLI (`claude -p`) instead of the
Messages API. Authenticated by the CLI's own session (browser login or
CLAUDE_CODE_OAUTH_TOKEN), never by an API key.

cited_text produced by this adapter is the model's own self-report of what it
read — not an API-verified citation. The Messages API's citation mechanism
(cryptographically tied to search results) has no equivalent exposed through
`claude -p`'s WebSearch tool, so this is a known, accepted reduction in
evidence strength relative to AnthropicSearchAdapter.
"""

import os
import shutil
import subprocess

PLAN_LIMIT_PATTERNS: tuple[str, ...] = ("usage limit", "rate limit", "try again")


def child_env() -> dict[str, str]:
    """Subprocess environment: CLI session only, API key vars stripped."""
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    return env


def preflight() -> None:
    """Fail loudly before any subprocess is spawned for real work."""
    if shutil.which("claude") is None:
        raise RuntimeError(
            "'claude' nao encontrado no PATH. Instale o Claude Code CLI."
        )
    probe = subprocess.run(
        ["claude", "auth", "status"],
        env=child_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if probe.returncode != 0:
        raise RuntimeError(
            "Claude CLI nao autenticado.\n"
            "  Maquina com navegador: rode 'claude' e faca /login.\n"
            "  Servidor sem navegador: gere o token numa maquina com navegador\n"
            "  com 'claude setup-token' e exporte CLAUDE_CODE_OAUTH_TOKEN."
        )
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `uv run pytest tests/test_claude_cli.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add renov_market_scan/collect/claude_cli.py tests/test_claude_cli.py
git commit -m "feat: preflight and child_env for ClaudeCliAdapter"
```

- [ ] **Step 6: Write the failing test for `extract_json`**

```python
def test_extract_json_parses_a_plain_object():
    from renov_market_scan.collect.claude_cli import extract_json
    result = extract_json('{"anuncios": []}')
    assert result == {"anuncios": []}


def test_extract_json_strips_a_markdown_fence():
    from renov_market_scan.collect.claude_cli import extract_json
    fenced = '```json\n{"anuncios": [{"titulo": "x"}]}\n```'
    result = extract_json(fenced)
    assert result == {"anuncios": [{"titulo": "x"}]}


def test_extract_json_raises_on_no_json_found():
    from renov_market_scan.collect.claude_cli import extract_json
    with pytest.raises(ValueError):
        extract_json("no json here at all")
```

- [ ] **Step 7: Run tests, verify they fail**

Run: `uv run pytest tests/test_claude_cli.py -v -k extract_json`
Expected: FAIL with `ImportError` (`extract_json` not defined)

- [ ] **Step 8: Implement `extract_json`**

```python
import json
import re
from typing import Any


def extract_json(text: str) -> dict[str, Any]:
    """Parse the model's JSON answer, tolerating a markdown fence around it.

    The model is instructed to answer with bare JSON but sometimes wraps it
    in ```json ... ``` anyway.
    """
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"resposta sem JSON reconhecivel: {stripped[:300]}")
    return dict(json.loads(stripped[start : end + 1]))
```

- [ ] **Step 9: Run tests, verify they pass**

Run: `uv run pytest tests/test_claude_cli.py -v -k extract_json`
Expected: PASS (3 tests)

- [ ] **Step 10: Commit**

```bash
git add renov_market_scan/collect/claude_cli.py tests/test_claude_cli.py
git commit -m "feat: extract_json tolerates a markdown fence around the CLI's JSON answer"
```

---

### Task 3: `ClaudeCliAdapter.search()` — subprocess call, listing extraction, error handling

**Files:**
- Modify: `renov_market_scan/collect/claude_cli.py`
- Modify: `tests/test_claude_cli.py`

**Interfaces:**
- Consumes: `preflight`, `child_env`, `extract_json`, `PLAN_LIMIT_PATTERNS` from Task 2 (same file); `SearchOutcome`, `SearchAdapter` from `collect/base.py`; `Query`, `Listing`, `Condition` from `models.py`; `Settings` from `config.py` (`settings.model: str`).
- Produces: `class ClaudeCliAdapter:` with `def __init__(self, settings: Settings) -> None` (calls `preflight()`) and `async def search(self, queries: list[Query]) -> SearchOutcome`. Status strings used: `"ok"`, `"parse_error"`, `"erro_subprocess"`, `"limite_de_plano"`. Task 4 (CLI wiring) instantiates this class exactly as `ClaudeCliAdapter(settings)`.

Status conventions to reuse from `AnthropicSearchAdapter` (`collect/anthropic_search.py`) for consistency across the codebase: `STATUS_OK = "ok"`, `STATUS_PARSE_ERROR = "parse_error"`. This task adds two new statuses specific to the CLI path: `STATUS_SUBPROCESS_ERROR = "erro_subprocess"` (non-zero exit, timeout, or `is_error` in the envelope, with no recognized plan-limit pattern) and `STATUS_PLAN_LIMIT = "limite_de_plano"` (a recognized plan-limit pattern was found in stderr/stdout).

- [ ] **Step 1: Write the failing tests for a successful call and JSON parse failure**

```python
# Added to tests/test_claude_cli.py
import asyncio
import json
from unittest.mock import MagicMock, patch

from renov_market_scan.collect.claude_cli import ClaudeCliAdapter
from renov_market_scan.config import Settings
from renov_market_scan.models import Query

QUERY = Query(
    search_key="k1",
    source="olx",
    domain="olx.com.br",
    phrase_index=0,
    text="apple iphone 13 128gb usado seminovo",
)


def make_settings(**overrides):
    return Settings(**overrides)


def fake_envelope(result_text: str, is_error: bool = False) -> str:
    return json.dumps(
        {"result": result_text, "session_id": "sess-1", "is_error": is_error, "num_turns": 3}
    )


VALID_RESULT = (
    '{"anuncios": [{"titulo": "iPhone 13 128GB seminovo R$ 3.050,00", '
    '"preco_brl": 3050.0, "condicao": "seminovo", '
    '"url": "https://olx.com.br/a-1", "fonte": "olx", '
    '"cited_text": "R$ 3.050,00 iPhone 13 seminovo"}]}'
)


def test_a_successful_call_yields_listings():
    fake_proc = MagicMock(returncode=0, stdout=fake_envelope(VALID_RESULT), stderr="")
    with (
        patch("shutil.which", return_value="/usr/local/bin/claude"),
        patch(
            "subprocess.run",
            side_effect=[
                MagicMock(returncode=0, stdout="Logged in", stderr=""),  # preflight
                fake_proc,  # the actual search call
            ],
        ),
    ):
        adapter = ClaudeCliAdapter(make_settings())
        outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "ok"
    assert len(outcome.listings) == 1
    listing = outcome.listings[0]
    assert listing.price_brl == 3050.0
    assert listing.search_key == "k1"
    assert listing.source == "olx"
    assert listing.cited_text == "R$ 3.050,00 iPhone 13 seminovo"


def test_invalid_json_in_the_result_is_a_parse_error():
    fake_proc = MagicMock(returncode=0, stdout=fake_envelope("isto nao e json"), stderr="")
    with (
        patch("shutil.which", return_value="/usr/local/bin/claude"),
        patch(
            "subprocess.run",
            side_effect=[
                MagicMock(returncode=0, stdout="Logged in", stderr=""),
                fake_proc,
            ],
        ),
    ):
        adapter = ClaudeCliAdapter(make_settings())
        outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "parse_error"
    assert outcome.listings == []


def test_a_nonzero_exit_code_is_a_subprocess_error():
    fake_proc = MagicMock(returncode=1, stdout="", stderr="something broke")
    with (
        patch("shutil.which", return_value="/usr/local/bin/claude"),
        patch(
            "subprocess.run",
            side_effect=[
                MagicMock(returncode=0, stdout="Logged in", stderr=""),
                fake_proc,
            ],
        ),
    ):
        adapter = ClaudeCliAdapter(make_settings())
        outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "erro_subprocess"
    assert outcome.listings == []


def test_a_recognized_plan_limit_message_is_classified_distinctly():
    fake_proc = MagicMock(
        returncode=1, stdout="", stderr="Error: usage limit reached, try again later"
    )
    with (
        patch("shutil.which", return_value="/usr/local/bin/claude"),
        patch(
            "subprocess.run",
            side_effect=[
                MagicMock(returncode=0, stdout="Logged in", stderr=""),
                fake_proc,
            ],
        ),
    ):
        adapter = ClaudeCliAdapter(make_settings())
        outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "limite_de_plano"


def test_an_empty_query_list_returns_ok_without_a_subprocess_call():
    with (
        patch("shutil.which", return_value="/usr/local/bin/claude"),
        patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout="Logged in", stderr=""),
        ) as run_mock,
    ):
        adapter = ClaudeCliAdapter(make_settings())
        outcome = asyncio.run(adapter.search([]))
    assert outcome.status == "ok"
    assert outcome.listings == []
    # Only the preflight call happened, no search subprocess.
    assert run_mock.call_count == 1
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest tests/test_claude_cli.py -v -k "successful_call or parse_error or subprocess_error or plan_limit or empty_query"`
Expected: FAIL with `ImportError: cannot import name 'ClaudeCliAdapter'`

- [ ] **Step 3: Implement `ClaudeCliAdapter`**

```python
# Appended to renov_market_scan/collect/claude_cli.py
import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from renov_market_scan.collect.base import SearchOutcome
from renov_market_scan.config import Settings
from renov_market_scan.models import Condition, Listing, Query

STATUS_OK = "ok"
STATUS_PARSE_ERROR = "parse_error"
STATUS_SUBPROCESS_ERROR = "erro_subprocess"
STATUS_PLAN_LIMIT = "limite_de_plano"

MAX_TURNS = 40
SUBPROCESS_TIMEOUT_S = 900

VALID_CONDITIONS: frozenset[str] = frozenset({"novo", "seminovo", "usado", "desconhecido"})

SEARCH_PROMPT_TEMPLATE = (
    "Voce e um coletor de referencia de precos de celulares seminovos no "
    "Brasil.\n\n"
    "Use WebSearch (e WebFetch apenas quando precisar confirmar o preco na "
    "pagina) para encontrar anuncios de aparelhos USADOS ou SEMINOVOS a "
    "venda no dominio {domain}, usando estas frases de busca: {phrases}.\n\n"
    "REGRAS DE ACEITE DE ANUNCIO (aplicar antes de incluir):\n"
    "1. Descarte acessorios e pecas: capa, capinha, case, pelicula, vidro, "
    "tela, display, touch, bateria, placa, flex, conector, carcaca, aro, "
    "tampa, camera, alto-falante, botao, carregador, fone, cabo, chip, "
    'suporte, "para retirada", "nao liga", replica, clone, similar.\n'
    "2. Preco A VISTA. Rejeite valor precedido de \"12x\", \"10 x\", \"sem "
    "juros\". Converta \"R$ 1.234,56\" para 1234.56 (numero, ponto decimal).\n"
    "3. Descarte \"novo\"/\"lacrado\". Aceite: seminovo, usado, vitrine, "
    "recondicionado.\n\n"
    "RESPONDA APENAS COM JSON, sem markdown, sem code fence, sem preambulo:\n"
    '{{"anuncios":[{{"titulo":"...","preco_brl":1234.56,"condicao":"usado",'
    '"url":"https://...","fonte":"...",'
    '"cited_text":"trecho verbatim do texto que evidencia o preco"}}]}}\n\n'
    "Se nao achar nada valido, devolva \"anuncios\": []. Nao calcule "
    "mediana, minimo ou maximo. Nao faca perguntas."
)


class ExtractedListing(BaseModel):
    """One advert as reported by the model, before any filtering."""

    titulo: str
    preco_brl: float | None = None
    condicao: str = "desconhecido"
    url: str
    fonte: str = ""
    cited_text: str = ""


class ExtractionPayload(BaseModel):
    anuncios: list[ExtractedListing] = []


def _normalize_condition(value: str) -> Condition:
    lowered = value.strip().lower()
    if lowered in VALID_CONDITIONS:
        return lowered  # type: ignore[return-value]
    return "desconhecido"


def _classify_failure(stdout: str, stderr: str) -> str:
    combined = (stdout + stderr).lower()
    if any(pattern in combined for pattern in PLAN_LIMIT_PATTERNS):
        return STATUS_PLAN_LIMIT
    return STATUS_SUBPROCESS_ERROR


class ClaudeCliAdapter:
    """Search and extract by driving `claude -p` as a subprocess.

    Serial by construction: no semaphore, settings.concurrency is ignored.
    claude -p draws from the plan's shared 5h/weekly usage window, not a
    per-token rate limit, so concurrent CLI processes risk exhausting that
    window faster and interleaving sessions unpredictably.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        preflight()

    async def search(self, queries: list[Query]) -> SearchOutcome:
        if not queries:
            return SearchOutcome(listings=[], status=STATUS_OK, payload={})
        return await asyncio.to_thread(self._search_blocking, queries)

    def _search_blocking(self, queries: list[Query]) -> SearchOutcome:
        first = queries[0]
        phrases = "; ".join(f'"{query.text}"' for query in queries)
        prompt = SEARCH_PROMPT_TEMPLATE.format(domain=first.domain, phrases=phrases)

        cmd = [
            "claude", "-p",
            "--output-format", "json",
            "--allowedTools", "WebSearch,WebFetch",
            "--max-turns", str(MAX_TURNS),
            "--model", self._settings.model,
        ]

        proc = subprocess.run(
            cmd,
            input=prompt,
            env=child_env(),
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
        )

        payload: dict[str, Any] = {
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-2000:],
            "stderr_tail": proc.stderr[-2000:],
        }

        if proc.returncode != 0:
            status = _classify_failure(proc.stdout, proc.stderr)
            return SearchOutcome(listings=[], status=status, payload=payload)

        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return SearchOutcome(listings=[], status=STATUS_PARSE_ERROR, payload=payload)

        if envelope.get("is_error"):
            status = _classify_failure(str(envelope), "")
            return SearchOutcome(listings=[], status=status, payload=payload)

        payload["session_id"] = envelope.get("session_id")
        payload["num_turns"] = envelope.get("num_turns")

        try:
            parsed = extract_json(envelope.get("result", ""))
            extraction = ExtractionPayload.model_validate(parsed)
        except (ValueError, ValidationError):
            return SearchOutcome(listings=[], status=STATUS_PARSE_ERROR, payload=payload)

        captured_at = datetime.now(UTC).isoformat()
        listings = [
            Listing(
                search_key=first.search_key,
                source=first.source,
                title=item.titulo,
                price_brl=item.preco_brl,
                condition=_normalize_condition(item.condicao),
                url=item.url,
                captured_at=captured_at,
                cited_text=item.cited_text,
            )
            for item in extraction.anuncios
        ]
        return SearchOutcome(listings=listings, status=STATUS_OK, payload=payload)
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `uv run pytest tests/test_claude_cli.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Run ruff and mypy**

Run: `uv run ruff check renov_market_scan/collect/claude_cli.py tests/test_claude_cli.py`
Run: `uv run mypy renov_market_scan/collect/claude_cli.py`
Expected: both clean. Fix any typing issue (e.g. explicit `dict[str, Any]` casts) before proceeding.

- [ ] **Step 6: Commit**

```bash
git add renov_market_scan/collect/claude_cli.py tests/test_claude_cli.py
git commit -m "feat: ClaudeCliAdapter.search runs claude -p and extracts listings"
```

---

### Task 4: `notify.py` — Slack webhook notification

**Files:**
- Create: `renov_market_scan/notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: nothing from this codebase (stdlib only: `os`, `json`, `urllib.request`, `urllib.error`).
- Produces: `def notify_slack(text: str) -> None`. Never raises — a failed Slack call is logged to stderr, not propagated (a broken webhook must never mask or replace the real run outcome). Task 6 (`cli.py` wiring) imports this exact name from `renov_market_scan.notify`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_notify.py
from unittest.mock import MagicMock, patch

from renov_market_scan.notify import notify_slack


def test_notify_slack_posts_to_the_webhook_url(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.example/abc")
    fake_response = MagicMock()
    fake_response.status = 200
    fake_response.__enter__ = MagicMock(return_value=fake_response)
    fake_response.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=fake_response) as urlopen_mock:
        notify_slack("hello")
    assert urlopen_mock.call_count == 1
    request = urlopen_mock.call_args[0][0]
    assert request.full_url == "https://hooks.slack.example/abc"


def test_notify_slack_does_nothing_without_a_webhook_url(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    with patch("urllib.request.urlopen") as urlopen_mock:
        notify_slack("hello")
    assert urlopen_mock.call_count == 0


def test_notify_slack_never_raises_when_the_request_fails(monkeypatch):
    import urllib.error

    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.example/abc")
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        notify_slack("hello")  # must not raise
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest tests/test_notify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'renov_market_scan.notify'`

- [ ] **Step 3: Implement `notify.py`**

```python
"""Slack notification for run outcomes. Sent from Python, never from the
model — if a run dies mid-way, this is exactly when a message is needed,
and the model will not be alive to send one."""

import json
import os
import sys
import urllib.error
import urllib.request


def notify_slack(text: str) -> None:
    """Post text to the incoming webhook in SLACK_WEBHOOK_URL, if set.

    Never raises: a broken webhook must not mask the real run outcome that
    the caller is trying to report.
    """
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        print("[slack] SLACK_WEBHOOK_URL nao definida, pulando notificacao.", file=sys.stderr)
        return
    body = json.dumps({"text": text}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            print(f"[slack] enviado ({response.status})", file=sys.stderr)
    except urllib.error.URLError as exc:
        print(f"[slack] FALHOU: {exc}", file=sys.stderr)
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `uv run pytest tests/test_notify.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run ruff and mypy**

Run: `uv run ruff check renov_market_scan/notify.py tests/test_notify.py`
Run: `uv run mypy renov_market_scan/notify.py`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add renov_market_scan/notify.py tests/test_notify.py
git commit -m "feat: notify_slack posts run outcomes to an incoming webhook"
```

---

### Task 5: Remove `Settings` fields specific to the Messages API

**Files:**
- Modify: `renov_market_scan/config.py`
- Modify: `.env.example`

**Interfaces:**
- Consumes: nothing new.
- Produces: `class Settings` retains `model: str = "claude-sonnet-5"`, `concurrency: int = 4`, `price_floor_brl: float = 80.0`, `price_ceiling_brl: float = 15000.0`, `user_location_country: str = "BR"`. Removed: `anthropic_api_key`, `web_search_tool_version`, `max_uses_per_call`, the `_check_model_tool_pair` validator, `KNOWN_TOOL_VERSIONS`, `TOOL_VERSIONS_REQUIRING_MODERN_MODEL`, `MODELS_SUPPORTING_MODERN_TOOL`, `validate_model_tool_pair`. Every later task that constructs `Settings()` must do so without `anthropic_api_key=...`.

This task will break `tests/test_anthropic_search.py`, `tests/test_cost.py`, and any `Settings(anthropic_api_key=...)` call across the test suite — Task 7 removes those files, and Task 8 fixes the remaining callers. Do not fix other files' `Settings()` calls in this task; that is Task 8's job, so the fix-up stays scoped and reviewable.

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_config.py (create the file if it does not exist)
import pytest

from renov_market_scan.config import Settings


def test_settings_no_longer_require_an_api_key():
    settings = Settings()  # type: ignore[call-arg]
    assert settings.model == "claude-sonnet-5"
    assert settings.concurrency == 4


def test_settings_has_no_api_key_field():
    settings = Settings()  # type: ignore[call-arg]
    assert not hasattr(settings, "anthropic_api_key")
    assert not hasattr(settings, "web_search_tool_version")
    assert not hasattr(settings, "max_uses_per_call")
```

- [ ] **Step 2: Run test, verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `Settings()` currently requires `anthropic_api_key` (no default), so construction without it raises `pydantic.ValidationError`.

- [ ] **Step 3: Rewrite `config.py`**

```python
"""Application settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, populated from the environment and .env."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    model: str = "claude-sonnet-5"
    concurrency: int = 4
    price_floor_brl: float = 80.0
    price_ceiling_brl: float = 15000.0
    user_location_country: str = "BR"
```

- [ ] **Step 4: Run test, verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Update `.env.example`**

```
# Sem API key: a coleta usa a sessao do Claude Code CLI (login no navegador
# ou CLAUDE_CODE_OAUTH_TOKEN). Veja o README para os dois caminhos.
MODEL=claude-sonnet-5

# Webhook do Slack para notificacao de fim de rodada. Opcional: sem isso a
# notificacao e pulada com um aviso no stderr.
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

- [ ] **Step 6: Commit**

```bash
git add renov_market_scan/config.py tests/test_config.py .env.example
git commit -m "feat: remove Messages-API-only settings, no API key required"
```

---

### Task 6: Wire `ClaudeCliAdapter` and Slack into `cli.py`

**Files:**
- Modify: `renov_market_scan/cli.py`

**Interfaces:**
- Consumes: `ClaudeCliAdapter` from `renov_market_scan.collect.claude_cli` (Task 3); `notify_slack` from `renov_market_scan.notify` (Task 4); `Settings` from `renov_market_scan.config` (Task 5, no longer takes `anthropic_api_key`); `RunOptions`, `RunResult`, `execute` from `renov_market_scan.run` (unchanged).
- Produces: the `run` command builds a Slack summary string and calls `notify_slack` on success, on `KeyboardInterrupt`, and on any other exception, before re-raising/returning. No new public function — this task only rewires the existing `run` command body.

Replace the two current import lines:

```python
from renov_market_scan.collect.anthropic_search import AnthropicSearchAdapter
from renov_market_scan.cost import estimate, format_estimate
```

with:

```python
from renov_market_scan.collect.claude_cli import ClaudeCliAdapter
from renov_market_scan.notify import notify_slack
```

Remove the `console.print(format_estimate(estimate(plan, sources, settings)))` line (around `cli.py:103`) — there is no cost estimate anymore. Replace it with a plain batch/pair count:

```python
console.print(f"Pares (modelo x fonte): {len(plan) * len(sources)}")
```

Replace `adapter = AnthropicSearchAdapter(settings)` (around `cli.py:128`) with `adapter = ClaudeCliAdapter(settings)`.

Wrap the existing `asyncio.run(_main())` call (`cli.py:166`) with Slack notification:

```python
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        notify_slack(":warning: Rodada de referencia de mercado interrompida manualmente.")
        raise
    except Exception as exc:
        notify_slack(f":rotating_light: Rodada de referencia de mercado ABORTOU: `{exc}`")
        raise
```

The success case needs the run's result (device/search/sample counts, report path) available outside `_main()`'s closure to build the summary. Restructure `_main()` to return the `RunResult` instead of `None`, and capture it in the outer scope:

```python
    async def _main() -> RunResult:
        # ... unchanged body up to and including:
        result = await execute(options, settings, adapter, progress=on_progress)
        # ... unchanged logging/console.print lines ...
        return result

    try:
        result = asyncio.run(_main())
    except KeyboardInterrupt:
        notify_slack(":warning: Rodada de referencia de mercado interrompida manualmente.")
        raise
    except Exception as exc:
        notify_slack(f":rotating_light: Rodada de referencia de mercado ABORTOU: `{exc}`")
        raise
    else:
        ok = sum(1 for row in result.summary_rows if row["status"] == "ok")
        summary = (
            f":bar_chart: *Referencia de mercado — {input_path.name}*\n"
            f"• {len(result.summary_rows)} modelo(s), {result.searches_performed} busca(s)\n"
            f"• {ok} com amostra boa, {len(result.summary_rows) - ok} com amostra fraca ou sem dados\n"
            f"• {len(result.discarded_rows)} anuncio(s) descartado(s)\n"
            f"• Arquivo: `{result.report_path}`"
        )
        notify_slack(summary)
```

Import `RunResult` alongside the existing `RunOptions` import from `renov_market_scan.run`.

- [ ] **Step 1: Apply the edits above to `cli.py`**

- [ ] **Step 2: Run ruff and mypy**

Run: `uv run ruff check renov_market_scan/cli.py`
Run: `uv run mypy renov_market_scan/cli.py`
Expected: both clean.

- [ ] **Step 3: Manually smoke-test `--dry-run` with a fixture-backed run**

This step is exploratory, not a pytest run — Task 7 rewrites the real `test_cli.py` assertions. Just confirm the command doesn't crash:

```bash
uv run renov-market-scan run --input Template-iPhone.xlsx --limite 2 --dry-run
```

Expected: prints the plan, "Pares (modelo x fonte): N", and exits 0 without calling `claude` (dry-run returns before `ClaudeCliAdapter` is constructed — confirm this is still the case by reading the `if dry_run:` branch, which must remain before the `adapter = ClaudeCliAdapter(settings)` line).

- [ ] **Step 4: Commit**

```bash
git add renov_market_scan/cli.py
git commit -m "feat: wire ClaudeCliAdapter and Slack notifications into the run command"
```

---

### Task 7: Remove `AnthropicSearchAdapter`, `cost.py`, and the `anthropic` dependency

**Files:**
- Delete: `renov_market_scan/collect/anthropic_search.py`
- Delete: `renov_market_scan/cost.py`
- Delete: `tests/test_anthropic_search.py`
- Delete: `tests/test_cost.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: nothing (this task only removes code; Task 6 already stopped importing from these modules).
- Produces: nothing new. `grep -rn "anthropic_search\|from renov_market_scan.cost\|import anthropic" renov_market_scan/ tests/` must return zero results after this task (the `anthropic` package name may still appear in comments/docs referring to "the Anthropic API" in prose — that is fine; only import statements and the dependency line must be gone).

- [ ] **Step 1: Delete the files**

```bash
git rm renov_market_scan/collect/anthropic_search.py renov_market_scan/cost.py
git rm tests/test_anthropic_search.py tests/test_cost.py
```

- [ ] **Step 2: Remove the `anthropic` dependency from `pyproject.toml`**

Edit the `dependencies` list in `pyproject.toml` — remove the line `"anthropic>=0.40",`.

- [ ] **Step 3: Sync the environment**

```bash
uv sync
```

- [ ] **Step 4: Confirm no references remain**

```bash
grep -rn "anthropic_search\|from renov_market_scan.cost\|import anthropic" renov_market_scan/ tests/
```

Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: remove AnthropicSearchAdapter, cost.py, and the anthropic dependency"
```

---

### Task 8: Fix remaining test fixtures and update `test_cli.py`

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `tests/conftest.py` (if it constructs `Settings(anthropic_api_key=...)` anywhere — check with grep before editing)

**Interfaces:**
- Consumes: `ClaudeCliAdapter` behavior from Task 3 (via `FixtureAdapter` substitution — the CLI tests must not spawn a real `claude` process); `Settings` from Task 5.
- Produces: a green `tests/test_cli.py` with no `ANTHROPIC_API_KEY` references and no cost-estimate assertions.

- [ ] **Step 1: Find every remaining reference to the old API-key/cost contract**

```bash
grep -rn "ANTHROPIC_API_KEY\|anthropic_api_key" tests/
```

- [ ] **Step 2: Rewrite `tests/test_cli.py`**

The current file sets `monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")` in four tests and asserts `"Chamadas a API"` / `"US$"` in the dry-run output. Since `--dry-run` returns before `ClaudeCliAdapter` is constructed (confirmed in Task 6), these tests no longer need any API-key or CLI-auth mocking at all — dry-run never touches `claude`. Replace the file:

```python
from typer.testing import CliRunner

from renov_market_scan.cli import app

runner = CliRunner()


def test_help_is_in_portuguese():
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--somente-ativos" in result.output
    assert "--dry-run" in result.output
    assert "--retomar" in result.output
    assert "--reprocessar-filtro" in result.output


def test_dry_run_prints_the_plan_and_pair_count_and_spawns_nothing(
    tmp_path, make_sheet, device_row_dict
):
    source = make_sheet(tmp_path, [device_row_dict()])
    result = runner.invoke(
        app,
        [
            "run",
            "--input",
            str(source),
            "--output",
            str(tmp_path / "out"),
            "--cache",
            str(tmp_path / "scan.sqlite"),
            "--fontes",
            "olx",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "Pares (modelo x fonte)" in result.output
    assert not (tmp_path / "out").exists()


def test_dry_run_reports_the_active_model_count(tmp_path, make_sheet, device_row_dict):
    rows = [device_row_dict(**{"Model*": f"GALAXY A{i}", "ERP Code": f"E{i}"}) for i in range(3)]
    rows.append(
        device_row_dict(**{"Model*": "INATIVO", "ERP Code": "E9", "Price for In-store": 10})
    )
    source = make_sheet(tmp_path, rows)
    result = runner.invoke(
        app,
        [
            "run",
            "--input",
            str(source),
            "--output",
            str(tmp_path / "out"),
            "--cache",
            str(tmp_path / "scan.sqlite"),
            "--fontes",
            "olx",
            "--dry-run",
        ],
    )
    assert "3" in result.output


def test_a_missing_input_file_fails_with_a_clear_message(tmp_path):
    result = runner.invoke(app, ["run", "--input", str(tmp_path / "nao-existe.xlsx"), "--dry-run"])
    assert result.exit_code != 0
```

Drop `test_a_real_run_requires_confirmation_and_aborts_on_no`: exercising the confirmation path now requires either a real `ClaudeCliAdapter` construction (which runs `preflight()` and would try to spawn `claude`) or mocking `ClaudeCliAdapter` at the CLI-test boundary. Confirming a typer prompt abort is already covered structurally by typer itself; re-adding this behavior with a mocked adapter is out of scope for this plan (note it in the PR description as a follow-up, not a silent drop — see this task's Step 4).

- [ ] **Step 3: Run the CLI tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS (4 tests)

- [ ] **Step 4: Note the dropped test as a follow-up**

Add a one-line comment above the last test in `tests/test_cli.py`:

```python
# Follow-up (not in this plan's scope): a test for the confirm-before-run
# prompt aborting on "n" needs a mocked ClaudeCliAdapter injected at the CLI
# boundary, since the real adapter's __init__ calls preflight().
```

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -q`
Expected: all tests pass, none touch the network or spawn `claude`.

- [ ] **Step 6: Run ruff and mypy on the whole package**

Run: `uv run ruff check .`
Run: `uv run mypy renov_market_scan`
Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add tests/test_cli.py
git commit -m "test: rewrite test_cli.py for the no-API-key dry-run contract"
```

---

### Task 9: README — document the two auth paths and drop the API-key setup

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing (documentation only).
- Produces: an updated Setup section with no `ANTHROPIC_API_KEY` step, and a new "Autenticacao" section documenting both auth paths.

- [ ] **Step 1: Replace the Setup section**

Replace:

```markdown
## Setup

```bash
uv sync
cp .env.example .env      # e preencha ANTHROPIC_API_KEY
```
```

with:

```markdown
## Setup

```bash
uv sync
cp .env.example .env
```

A coleta usa a sessao do Claude Code CLI, nao uma API key. Antes do primeiro
uso, autentique o CLI — veja "Autenticacao" abaixo.

## Autenticacao

**Maquina com navegador** (time, uso local): instale o
[Claude Code CLI](https://claude.com/claude-code), rode `claude` e faca
`/login`. `renov-market-scan run` confere `claude auth status` antes de cada
execucao e para com erro claro se a sessao expirou.

**Servidor sem navegador**: gere um token de longa duracao numa maquina com
navegador,

```bash
claude setup-token
```

e exporte `CLAUDE_CODE_OAUTH_TOKEN` no ambiente do servidor (nunca no
`.env` do repositorio). `claude auth status` reconhece o token sem
necessidade de `/login`.

Em ambos os casos, `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` sao removidas
do ambiente de todo subprocesso lancado pela ferramenta — se uma delas
estiver definida, o CLI a usaria em vez da assinatura, cobrando por token
sem avisar.
```

- [ ] **Step 2: Add Slack to the Setup/Saidas section**

Add a short paragraph after the Setup section:

```markdown
### Notificacao no Slack

Defina `SLACK_WEBHOOK_URL` (webhook incoming) para receber uma mensagem ao
fim de cada rodada — sucesso (contagens e caminho do relatorio), falha
(erro e onde esta o cache para retomar), ou interrupcao manual. Sem essa
variavel, a notificacao e apenas pulada com um aviso no log.
```

- [ ] **Step 3: Update the "Aba `Amostras`" description**

The current text says `cited_text` is "o trecho verbatim que a API citou". Replace with:

```markdown
### Aba `Amostras`

Todo anuncio aceito, com `cited_text` — o trecho que o modelo relata ter
lido na pagina, evidenciando o preco. Note que isso e auto-relato do
modelo, nao uma citacao verificada pela API (a assinatura do CLI nao expoe
o mecanismo de citacoes criptografadas da Messages API). E a evidencia
auditavel de cada linha do Resumo, com a garantia mais fraca dessa mudanca
documentada aqui.
`flag_5g_divergente` marca quando o alvo e o anuncio divergem apenas no 5G.
```

- [ ] **Step 4: Update the design doc pointer at the bottom**

Replace the last two lines:

```markdown
Design: `docs/superpowers/specs/2026-07-28-renov-market-scan-design.md`
Medições da Fase 0: `docs/fontes.md`
```

with:

```markdown
Design original: `docs/superpowers/specs/2026-07-28-renov-market-scan-design.md`
Design do adapter via CLI: `docs/superpowers/specs/2026-07-30-claude-cli-adapter-design.md`
Medições da Fase 0: `docs/fontes.md`
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document CLI auth paths and Slack webhook, drop API key setup"
```

---

## Final check (not a task — run before handing off for review)

```bash
uv run pytest -q
uv run ruff check .
uv run mypy renov_market_scan
grep -rn "ANTHROPIC_API_KEY\|anthropic_search\|from renov_market_scan.cost" renov_market_scan/ tests/ README.md .env.example
```

The `grep` must return nothing except, at most, prose mentioning "a API da
Anthropic" in a comment that is not an import or an env var reference.
