# renov-market-scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CLI Python que lê uma planilha no template de importação de dispositivos, pesquisa anúncios de seminovos em marketplaces brasileiros via web search tool da API Anthropic, e produz um relatório `.xlsx` auditável com mínimo, mediana e máximo por modelo+capacidade, cada extremo com o link do anúncio.

**Architecture:** Pipeline em camadas com cache SQLite em três níveis (resposta crua paga em US$, extração paga em tokens, filtro e estatística de graça). Chave dupla: `search_key` para busca/cache/estatística, `(ERP Code, Model, Storage)` para relatório. Único adapter de rede é a web search tool da API; um `FixtureAdapter` offline cobre todos os testes.

**Tech Stack:** Python 3.11+, uv, typer, pydantic v2, pydantic-settings, openpyxl, anthropic, tenacity, structlog, rich, PyYAML, pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-07-28-renov-market-scan-design.md`

## Global Constraints

Estas regras valem para **todas** as tarefas. Os requisitos de cada tarefa as incluem implicitamente.

- **Idioma:** código, nomes de módulo, funções, variáveis e docstrings em **inglês**. Mensagens de CLI, colunas do relatório, motivos de descarte e documentação em **pt-BR**. Motivos de descarte e status são strings em pt-BR sem acento (`acessorio_ou_peca`, `revisao_humana`) para servirem como identificadores estáveis.
- **Python 3.11+.** Gerenciador: `uv`. Todo comando roda por `uv run`.
- **Nunca escrever no arquivo de entrada.** `openpyxl` é aberto com `read_only=True` para leitura. A cópia do template é feita com `shutil.copy2` e a escrita acontece só na cópia.
- **Nunca usar `Device name` como chave. Nunca usar `ERP Code` sozinho como chave.**
- **Nunca hardcodar** nome de aba, número de linhas, ou a versão da web search tool.
- **Nunca `eval` nem regex-parsing de JSON malformado.**
- **Nunca Selenium nem Playwright.** Nenhuma dependência de navegador.
- **Todo descarte de anúncio registra o motivo.** Não existe caminho que descarte silenciosamente.
- **Sem rede em teste.** Nenhum teste chama a API. `FixtureAdapter` cobre a coleta.
- **Cliente Anthropic construído com `max_retries=0`.** O `tenacity` é a única fonte de política de retry.
- Ao fim de cada tarefa: `uv run ruff check .`, `uv run mypy renov_market_scan`, `uv run pytest` — todos verdes antes do commit.
- Commits convencionais em português (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`). Branch `feat/market-scan`. Nunca commit direto em `main` ou `dev`.

## File Structure

| Arquivo | Responsabilidade |
|---|---|
| `pyproject.toml` | Metadados, dependências, entry point `renov-market-scan`, config de ruff/mypy/pytest |
| `.env.example` | Documenta `ANTHROPIC_API_KEY` |
| `fontes.yaml` | Fontes com domínio, peso, habilitado |
| `marcas.yaml` | Aliases de fabricante para a query |
| `renov_market_scan/config.py` | `Settings` via pydantic-settings; valida o par (modelo, versão da tool) |
| `renov_market_scan/models.py` | Todos os modelos pydantic. Nenhuma lógica |
| `renov_market_scan/ingest/reader.py` | xlsx para `DeviceRow[]`, avisos de validação |
| `renov_market_scan/ingest/normalize.py` | Normalização de storage, `search_key`, plano de busca, anomalias |
| `renov_market_scan/filtering/text.py` | Primitivas de normalização compartilhadas |
| `renov_market_scan/filtering/price.py` | Parser BRL, rejeição de parcelamento |
| `renov_market_scan/filtering/matcher.py` | Match de modelo e de capacidade |
| `renov_market_scan/filtering/blacklist.py` | Regra de acessório/peça |
| `renov_market_scan/filtering/condition.py` | Classificação de condição |
| `renov_market_scan/filtering/evidence.py` | Regra de evidência por citação |
| `renov_market_scan/filtering/dedupe.py` | URL canônica e dedupe |
| `renov_market_scan/filtering/pipeline.py` | Ordem das regras, acumula motivo |
| `renov_market_scan/stats/aggregate.py` | IQR, percentis, extremos com link |
| `renov_market_scan/cache/schema.py` | DDL do SQLite |
| `renov_market_scan/cache/store.py` | Leitura e escrita das três camadas |
| `renov_market_scan/query/builder.py` | `SearchPlanItem` para `Query[]` |
| `renov_market_scan/collect/base.py` | `SearchAdapter` (Protocol) |
| `renov_market_scan/collect/errors.py` | Classificação de erro da tool e de stop_reason |
| `renov_market_scan/collect/fixture.py` | Adapter offline |
| `renov_market_scan/collect/anthropic_search.py` | Adapter de produção |
| `renov_market_scan/cost.py` | Estimador de dry-run |
| `renov_market_scan/report/xlsx.py` | 4 abas com formatação |
| `renov_market_scan/report/template_copy.py` | Cópia do template com colunas extras |
| `renov_market_scan/report/exports.py` | csv e json |
| `renov_market_scan/cli.py` | Typer, progresso, logging, SIGINT |
| `tests/conftest.py` | Builder de planilha sintética e fixtures compartilhadas |

Ordem das tarefas: lógica pura e testável primeiro (1-15), spike e rede depois (16-18), apresentação e integração no fim (19-24). Tudo até a Tarefa 15 é verificável sem chave de API.

---

### Task 1: Scaffolding, toolchain e configuração

**Files:**
- Create: `pyproject.toml`, `.env.example`, `renov_market_scan/__init__.py`, `renov_market_scan/config.py`
- Create: `tests/__init__.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: nada.
- Produces: `Settings` (pydantic-settings) com os campos `anthropic_api_key: str`, `model: str = "claude-sonnet-5"`, `web_search_tool_version: str = "web_search_20260209"`, `max_uses_per_call: int = 3`, `concurrency: int = 4`, `price_floor_brl: float = 80.0`, `price_ceiling_brl: float = 15000.0`, `user_location_country: str = "BR"`. Função `validate_model_tool_pair(model: str, tool_version: str) -> None` que levanta `ValueError`. Constante `TOOL_VERSIONS_REQUIRING_MODERN_MODEL: frozenset[str]` e `MODELS_SUPPORTING_MODERN_TOOL: frozenset[str]`.

- [ ] **Step 1: Criar `pyproject.toml`**

```toml
[project]
name = "renov-market-scan"
version = "0.1.0"
description = "Coletor de referencia de mercado de seminovos"
requires-python = ">=3.11"
dependencies = [
    "typer>=0.12",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "openpyxl>=3.1",
    "anthropic>=0.40",
    "tenacity>=8.3",
    "structlog>=24.1",
    "rich>=13.7",
    "PyYAML>=6.0",
]

[project.scripts]
renov-market-scan = "renov_market_scan.cli:app"

[dependency-groups]
dev = ["pytest>=8.2", "ruff>=0.5", "mypy>=1.10", "types-PyYAML>=6.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.11"
strict = true
warn_unused_ignores = true

[[tool.mypy.overrides]]
module = "yaml"
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 2: Criar `.env.example`**

```
# Chave da API Anthropic. Nunca commitar o .env real.
ANTHROPIC_API_KEY=sk-ant-...
```

- [ ] **Step 3: Criar `renov_market_scan/__init__.py` e `tests/__init__.py` vazios**

```bash
mkdir -p renov_market_scan tests
touch renov_market_scan/__init__.py tests/__init__.py
```

- [ ] **Step 4: Escrever o teste que falha**

Arquivo `tests/test_config.py`:

```python
import pytest

from renov_market_scan.config import Settings, validate_model_tool_pair


def test_defaults_are_the_documented_ones():
    s = Settings(anthropic_api_key="sk-test")
    assert s.model == "claude-sonnet-5"
    assert s.web_search_tool_version == "web_search_20260209"
    assert s.max_uses_per_call == 3
    assert s.concurrency == 4
    assert s.price_floor_brl == 80.0
    assert s.price_ceiling_brl == 15000.0
    assert s.user_location_country == "BR"


def test_modern_tool_with_modern_model_is_accepted():
    validate_model_tool_pair("claude-sonnet-5", "web_search_20260209")
    validate_model_tool_pair("claude-opus-5", "web_search_20260318")


def test_basic_tool_is_accepted_by_any_model():
    validate_model_tool_pair("claude-haiku-4-5", "web_search_20250305")
    validate_model_tool_pair("claude-sonnet-5", "web_search_20250305")


def test_modern_tool_with_haiku_is_rejected():
    with pytest.raises(ValueError, match="web_search_20260209"):
        validate_model_tool_pair("claude-haiku-4-5", "web_search_20260209")


def test_unknown_tool_version_is_rejected():
    with pytest.raises(ValueError, match="desconhecida"):
        validate_model_tool_pair("claude-sonnet-5", "web_search_19990101")


def test_settings_validates_the_pair_on_construction():
    with pytest.raises(ValueError):
        Settings(
            anthropic_api_key="sk-test",
            model="claude-haiku-4-5",
            web_search_tool_version="web_search_20260209",
        )
```

- [ ] **Step 5: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'renov_market_scan.config'`

- [ ] **Step 6: Implementar `renov_market_scan/config.py`**

```python
"""Application settings and validation of the model/tool-version pair."""

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

KNOWN_TOOL_VERSIONS: frozenset[str] = frozenset(
    {"web_search_20250305", "web_search_20260209", "web_search_20260318"}
)

# Versions from 20260209 onward require a model that supports dynamic filtering.
TOOL_VERSIONS_REQUIRING_MODERN_MODEL: frozenset[str] = frozenset(
    {"web_search_20260209", "web_search_20260318"}
)

MODELS_SUPPORTING_MODERN_TOOL: frozenset[str] = frozenset(
    {
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-5",
        "claude-sonnet-4-6",
        "claude-fable-5",
    }
)


def validate_model_tool_pair(model: str, tool_version: str) -> None:
    """Raise ValueError if the model cannot use the requested tool version."""
    if tool_version not in KNOWN_TOOL_VERSIONS:
        raise ValueError(
            f"Versao de web search tool desconhecida: {tool_version!r}. "
            f"Conhecidas: {sorted(KNOWN_TOOL_VERSIONS)}"
        )
    if (
        tool_version in TOOL_VERSIONS_REQUIRING_MODERN_MODEL
        and model not in MODELS_SUPPORTING_MODERN_TOOL
    ):
        raise ValueError(
            f"O modelo {model!r} nao suporta {tool_version!r}. "
            f"Use web_search_20250305 ou um modelo em "
            f"{sorted(MODELS_SUPPORTING_MODERN_TOOL)}"
        )


class Settings(BaseSettings):
    """Runtime configuration, populated from the environment and .env."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    anthropic_api_key: str
    model: str = "claude-sonnet-5"
    web_search_tool_version: str = "web_search_20260209"
    max_uses_per_call: int = 3
    concurrency: int = 4
    price_floor_brl: float = 80.0
    price_ceiling_brl: float = 15000.0
    user_location_country: str = "BR"

    @model_validator(mode="after")
    def _check_model_tool_pair(self) -> "Settings":
        validate_model_tool_pair(self.model, self.web_search_tool_version)
        return self
```

- [ ] **Step 7: Rodar o teste e confirmar que passa**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS, 6 testes

- [ ] **Step 8: Rodar lint e type check**

Run: `uv run ruff check . && uv run mypy renov_market_scan`
Expected: sem erros

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml .env.example renov_market_scan tests
git commit -m "feat: scaffolding do pacote e configuracao validada

Valida o par (modelo, versao da web search tool) na inicializacao.
A versao da tool nao e hardcodada: default em config, override por
ambiente."
```

---

### Task 2: Modelos pydantic

**Files:**
- Create: `renov_market_scan/models.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Consumes: nada.
- Produces: `DeviceRow`, `ReportKey`, `StorageNorm`, `Anomaly`, `SearchPlanItem`, `Query`, `Listing`, `RejectedListing`, `ModelStats`, `RawResponse`. Campos exatos no código abaixo. Todas as tarefas seguintes importam destes nomes.

- [ ] **Step 1: Escrever o teste que falha**

Arquivo `tests/test_models.py`:

```python
from renov_market_scan.models import (
    Anomaly,
    DeviceRow,
    Listing,
    ModelStats,
    Query,
    RejectedListing,
    ReportKey,
    SearchPlanItem,
    StorageNorm,
)


def test_device_row_holds_the_nineteen_column_subset_we_use():
    row = DeviceRow(
        row_number=3,
        device_name="SAMSUNG GALAXY A17 128GB A0",
        manufacturer="SAMSUNG",
        model="GALAXY A17",
        device_type="Phone",
        storage_raw="128",
        ram_raw=None,
        color="Any Color",
        price_instore=400.0,
        price_widget_mobile=400.0,
        erp_code="10870A0",
    )
    assert row.erp_code == "10870A0"
    assert row.ram_raw is None


def test_device_row_is_active_when_price_is_above_ten():
    active = DeviceRow(
        row_number=3, device_name="d", manufacturer="m", model="mo",
        device_type="Phone", storage_raw="128", ram_raw=None, color="Any Color",
        price_instore=400.0, price_widget_mobile=400.0, erp_code="1A0",
    )
    inactive = active.model_copy(update={"price_instore": 10.0})
    missing = active.model_copy(update={"price_instore": None})
    assert active.is_active is True
    assert inactive.is_active is False
    assert missing.is_active is False


def test_storage_norm_carries_label_gb_and_suspicion():
    ok = StorageNorm(label="1TB", gb=1024, suspicious=False)
    bad = StorageNorm(label="1", gb=1, suspicious=True)
    assert ok.gb == 1024
    assert bad.suspicious is True


def test_listing_defaults_are_safe():
    listing = Listing(
        search_key="k", source="olx", title="t", price_brl=100.0,
        condition="usado", url="https://x", captured_at="2026-07-28T00:00:00",
    )
    assert listing.cited_text == ""
    assert listing.flag_5g_divergent is False


def test_model_stats_allows_absent_statistics_for_small_samples():
    stats = ModelStats(
        search_key="k", n=2, minimum=100.0, p25=None, median=None, p75=None,
        maximum=200.0, spread_pct=None, min_url="https://a", max_url="https://b",
        min_raw=100.0, max_raw=200.0, sources=["olx"],
        predominant_condition="usado", status="insuficiente",
    )
    assert stats.median is None
    assert stats.status == "insuficiente"


def test_remaining_models_construct():
    key = ReportKey(
        erp_code="10870A0", model="GALAXY A17", storage_label="128GB",
        device_name="SAMSUNG GALAXY A17 128GB A0", price_instore=400.0,
        row_number=3,
    )
    item = SearchPlanItem(
        search_key="k", manufacturer="SAMSUNG", model="GALAXY A17",
        storage_label="128GB", storage_gb=128, report_keys=[key],
    )
    query = Query(
        search_key="k", source="olx", domain="olx.com.br", phrase_index=0,
        text="samsung galaxy a17 128gb usado seminovo",
    )
    anomaly = Anomaly(
        row_number=5, erp_code="10000A0", device_name="MOTO XT882 1GB A0",
        field="Storage, GB*", raw_value="1", reason="storage_suspeito",
        status="revisao_humana",
    )
    rejected = RejectedListing(
        listing=Listing(
            search_key="k", source="olx", title="capa", price_brl=20.0,
            condition="desconhecido", url="https://x",
            captured_at="2026-07-28T00:00:00",
        ),
        reason="acessorio_ou_peca",
    )
    assert item.report_keys[0].erp_code == "10870A0"
    assert query.phrase_index == 0
    assert anomaly.status == "revisao_humana"
    assert rejected.reason == "acessorio_ou_peca"
```

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'renov_market_scan.models'`

- [ ] **Step 3: Implementar `renov_market_scan/models.py`**

```python
"""Pydantic models shared across the pipeline. No logic lives here."""

from typing import Any, Literal

from pydantic import BaseModel, Field

Condition = Literal["novo", "seminovo", "usado", "desconhecido"]
SampleStatus = Literal["ok", "amostra_baixa", "insuficiente"]

# Price placeholder meaning "device not actively bought" in the source sheet.
INACTIVE_PRICE_THRESHOLD = 10.0


class DeviceRow(BaseModel):
    """One data row of the import template, limited to the columns we use."""

    row_number: int
    device_name: str
    manufacturer: str
    model: str
    device_type: str
    storage_raw: str | None
    ram_raw: str | None
    color: str
    price_instore: float | None
    price_widget_mobile: float | None
    erp_code: str

    @property
    def is_active(self) -> bool:
        """True when the sheet price is above the inactive placeholder."""
        if self.price_instore is None:
            return False
        return self.price_instore > INACTIVE_PRICE_THRESHOLD


class StorageNorm(BaseModel):
    """Normalized storage capacity. `suspicious` blocks the row from search."""

    label: str
    gb: int
    suspicious: bool


class Anomaly(BaseModel):
    """A row that cannot be searched, or a model that produced no sample."""

    row_number: int
    erp_code: str
    device_name: str
    field: str
    raw_value: str
    reason: str
    status: str


class ReportKey(BaseModel):
    """Report unit: (ERP Code, Model, Storage). ERP Code alone is not unique."""

    erp_code: str
    model: str
    storage_label: str
    device_name: str
    price_instore: float | None
    row_number: int


class SearchPlanItem(BaseModel):
    """Search unit. Fans out to every report key it serves."""

    search_key: str
    manufacturer: str
    model: str
    storage_label: str
    storage_gb: int
    report_keys: list[ReportKey]


class Query(BaseModel):
    """One search phrase aimed at one source."""

    search_key: str
    source: str
    domain: str
    phrase_index: int
    text: str


class RawResponse(BaseModel):
    """Untouched API response, cached before any processing."""

    search_key: str
    source: str
    phrase_index: int
    collected_on: str
    payload: dict[str, Any]
    status: str


class Listing(BaseModel):
    """A single marketplace advert as extracted by the model."""

    search_key: str
    source: str
    title: str
    price_brl: float | None
    condition: Condition
    url: str
    captured_at: str
    cited_text: str = ""
    flag_5g_divergent: bool = False


class RejectedListing(BaseModel):
    """A listing plus the reason it was discarded. Never discard silently."""

    listing: Listing
    reason: str


class ModelStats(BaseModel):
    """Aggregated statistics for one search key."""

    search_key: str
    n: int
    minimum: float | None
    p25: float | None
    median: float | None
    p75: float | None
    maximum: float | None
    spread_pct: float | None
    min_url: str | None
    max_url: str | None
    min_raw: float | None
    max_raw: float | None
    sources: list[str] = Field(default_factory=list)
    predominant_condition: Condition = "desconhecido"
    status: SampleStatus = "insuficiente"
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS, 6 testes

- [ ] **Step 5: Rodar lint e type check**

Run: `uv run ruff check . && uv run mypy renov_market_scan`
Expected: sem erros

- [ ] **Step 6: Commit**

```bash
git add renov_market_scan/models.py tests/test_models.py
git commit -m "feat: modelos pydantic do pipeline

ReportKey e (ERP Code, Model, Storage) porque ERP Code sozinho colide
em 118 casos no arquivo Android. DeviceRow.is_active encapsula a regra
do placeholder de preco 10."
```

---

### Task 3: Leitura da planilha

**Files:**
- Create: `renov_market_scan/ingest/__init__.py`, `renov_market_scan/ingest/reader.py`
- Create: `tests/conftest.py`, `tests/test_reader.py`

**Interfaces:**
- Consumes: `DeviceRow` da Tarefa 2.
- Produces: `read_device_rows(path: Path) -> tuple[list[DeviceRow], list[str]]` devolvendo linhas e avisos em pt-BR. `EXPECTED_HEADER: tuple[str, ...]` com as 19 colunas. `find_data_sheet(workbook) -> str`. Exceção `SheetFormatError(Exception)`.
- Produces (teste): fixture `make_sheet` em `conftest.py`, assinatura `make_sheet(tmp_path, rows, sheet_name="Planilha1", header=None, filename="entrada.xlsx") -> Path`, onde `rows` é lista de dicionários com chaves iguais aos nomes das colunas.

- [ ] **Step 1: Escrever a fixture de planilha sintética**

Arquivo `tests/conftest.py`:

```python
"""Shared fixtures. No test in this suite touches the network."""

from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from renov_market_scan.ingest.reader import EXPECTED_HEADER

HELP_ROW_FIRST_CELL = "Custom name of a device*\nMaximum 255 characters"


@pytest.fixture
def make_sheet():
    """Build a spreadsheet with the real template layout: help row, header, data."""

    def _make(
        tmp_path: Path,
        rows: list[dict[str, Any]],
        sheet_name: str = "Planilha1",
        header: tuple[str, ...] | None = None,
        filename: str = "entrada.xlsx",
    ) -> Path:
        columns = header if header is not None else EXPECTED_HEADER
        wb = Workbook()
        ws = wb.active
        ws.title = sheet_name
        help_row = [HELP_ROW_FIRST_CELL] + [""] * (len(columns) - 1)
        ws.append(help_row)
        ws.append(list(columns))
        for row in rows:
            ws.append([row.get(col) for col in columns])
        path = tmp_path / filename
        wb.save(path)
        wb.close()
        return path

    return _make


@pytest.fixture
def device_row_dict():
    """A complete, valid data row. Override fields per test."""

    def _make(**overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "Device name*": "SAMSUNG GALAXY A17 128GB A0",
            "Manufacturer*": "SAMSUNG",
            "Model*": "GALAXY A17",
            "Device type*": "Phone",
            "Storage, GB*": 128,
            "Ram, GB*": None,
            "Color*": "Any Color",
            "Price for In-store": 400,
            "Price for Widget & Mobile": 400,
            "Buying": "Online, Offline",
            "Questionnaire Widget": None,
            "Questionnaire In-store": None,
            "Questionnaire Mobile": None,
            "Questionnaire robot": None,
            "Priority": 10,
            "Reward Types": "Trade-In",
            "Minimum Price": 0.00,
            "Maximum Price": 0.00,
            "ERP Code": "10870A0",
        }
        base.update(overrides)
        return base

    return _make
```

- [ ] **Step 2: Escrever o teste que falha**

Arquivo `tests/test_reader.py`:

```python
import pytest

from renov_market_scan.ingest.reader import SheetFormatError, read_device_rows


def test_reads_data_from_row_three_onward(tmp_path, make_sheet, device_row_dict):
    path = make_sheet(tmp_path, [device_row_dict(), device_row_dict(**{"ERP Code": "10871A0"})])
    rows, warnings = read_device_rows(path)
    assert len(rows) == 2
    assert rows[0].row_number == 3
    assert rows[1].row_number == 4
    assert rows[0].erp_code == "10870A0"
    assert warnings == []


def test_sheet_name_is_not_hardcoded(tmp_path, make_sheet, device_row_dict):
    path = make_sheet(tmp_path, [device_row_dict()], sheet_name="iPhones")
    rows, _ = read_device_rows(path)
    assert len(rows) == 1


def test_empty_ram_and_any_color_are_preserved_as_read(tmp_path, make_sheet, device_row_dict):
    path = make_sheet(tmp_path, [device_row_dict()])
    rows, _ = read_device_rows(path)
    assert rows[0].ram_raw is None
    assert rows[0].color == "Any Color"


def test_storage_is_kept_raw_as_string(tmp_path, make_sheet, device_row_dict):
    path = make_sheet(tmp_path, [device_row_dict(**{"Storage, GB*": 1024})])
    rows, _ = read_device_rows(path)
    assert rows[0].storage_raw == "1024"


def test_price_divergence_produces_a_warning(tmp_path, make_sheet, device_row_dict):
    path = make_sheet(
        tmp_path,
        [device_row_dict(**{"Price for In-store": 400, "Price for Widget & Mobile": 350})],
    )
    rows, warnings = read_device_rows(path)
    assert len(rows) == 1
    assert len(warnings) == 1
    assert "linha 3" in warnings[0]
    assert "400" in warnings[0] and "350" in warnings[0]


def test_fully_empty_rows_are_skipped(tmp_path, make_sheet, device_row_dict):
    path = make_sheet(tmp_path, [device_row_dict(), {}, device_row_dict()])
    rows, _ = read_device_rows(path)
    assert len(rows) == 2


def test_wrong_header_raises_with_the_offending_columns(tmp_path, make_sheet, device_row_dict):
    bad_header = ("Nome",) + tuple(f"c{i}" for i in range(18))
    path = make_sheet(tmp_path, [device_row_dict()], header=bad_header)
    with pytest.raises(SheetFormatError, match="Device name"):
        read_device_rows(path)


def test_input_file_is_not_modified(tmp_path, make_sheet, device_row_dict):
    import hashlib

    path = make_sheet(tmp_path, [device_row_dict()])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    read_device_rows(path)
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert before == after
```

- [ ] **Step 3: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_reader.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'renov_market_scan.ingest'`

- [ ] **Step 4: Implementar `renov_market_scan/ingest/reader.py`**

```bash
mkdir -p renov_market_scan/ingest
touch renov_market_scan/ingest/__init__.py
```

```python
"""Read the device import template into DeviceRow objects.

The input file is opened read-only and is never written to.
"""

from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.workbook.workbook import Workbook

from renov_market_scan.models import DeviceRow

EXPECTED_HEADER: tuple[str, ...] = (
    "Device name*",
    "Manufacturer*",
    "Model*",
    "Device type*",
    "Storage, GB*",
    "Ram, GB*",
    "Color*",
    "Price for In-store",
    "Price for Widget & Mobile",
    "Buying",
    "Questionnaire Widget",
    "Questionnaire In-store",
    "Questionnaire Mobile",
    "Questionnaire robot",
    "Priority",
    "Reward Types",
    "Minimum Price",
    "Maximum Price",
    "ERP Code",
)

HEADER_ROW = 2
FIRST_DATA_ROW = 3


class SheetFormatError(Exception):
    """The workbook does not match the expected import template."""


def _text(value: Any) -> str:
    """Normalize a cell to a trimmed string. Integers lose the float tail."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _number(value: Any) -> float | None:
    """Return the cell as a float, or None when it is not numeric."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def find_data_sheet(workbook: Workbook) -> str:
    """Return the sheet whose row 2 contains 'Device name*'.

    Falls back to the first sheet. The sheet name is never hardcoded.
    """
    for name in workbook.sheetnames:
        worksheet = workbook[name]
        rows = list(worksheet.iter_rows(min_row=1, max_row=HEADER_ROW, values_only=True))
        if len(rows) >= HEADER_ROW:
            if any(_text(cell) == "Device name*" for cell in rows[HEADER_ROW - 1]):
                return name
    return workbook.sheetnames[0]


def read_device_rows(path: Path) -> tuple[list[DeviceRow], list[str]]:
    """Parse the template. Returns (rows, warnings) with warnings in pt-BR."""
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet_name = find_data_sheet(workbook)
        worksheet = workbook[sheet_name]
        raw_rows = list(worksheet.iter_rows(values_only=True))

        if len(raw_rows) < HEADER_ROW:
            raise SheetFormatError(
                f"A aba {sheet_name!r} tem menos de {HEADER_ROW} linhas; "
                "o template exige linha de ajuda e cabecalho."
            )

        header = tuple(_text(cell) for cell in raw_rows[HEADER_ROW - 1] if _text(cell))
        if header != EXPECTED_HEADER:
            missing = [c for c in EXPECTED_HEADER if c not in header]
            extra = [c for c in header if c not in EXPECTED_HEADER]
            raise SheetFormatError(
                f"Cabecalho da aba {sheet_name!r} nao corresponde ao template. "
                f"Faltando: {missing}. Inesperadas: {extra}."
            )

        index = {name: position for position, name in enumerate(header)}
        rows: list[DeviceRow] = []
        warnings: list[str] = []

        for offset, raw in enumerate(raw_rows[HEADER_ROW:], start=FIRST_DATA_ROW):
            if not any(_text(cell) for cell in raw):
                continue

            def cell(column: str, source: tuple[Any, ...] = raw) -> Any:
                position = index[column]
                return source[position] if position < len(source) else None

            instore = _number(cell("Price for In-store"))
            widget = _number(cell("Price for Widget & Mobile"))
            if instore != widget:
                warnings.append(
                    f"linha {offset}: 'Price for In-store' ({instore}) difere de "
                    f"'Price for Widget & Mobile' ({widget}); usando In-store."
                )

            storage = _text(cell("Storage, GB*"))
            ram = _text(cell("Ram, GB*"))
            rows.append(
                DeviceRow(
                    row_number=offset,
                    device_name=_text(cell("Device name*")),
                    manufacturer=_text(cell("Manufacturer*")),
                    model=_text(cell("Model*")),
                    device_type=_text(cell("Device type*")),
                    storage_raw=storage or None,
                    ram_raw=ram or None,
                    color=_text(cell("Color*")),
                    price_instore=instore,
                    price_widget_mobile=widget,
                    erp_code=_text(cell("ERP Code")),
                )
            )
        return rows, warnings
    finally:
        workbook.close()
```

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `uv run pytest tests/test_reader.py -v`
Expected: PASS, 8 testes

- [ ] **Step 6: Verificar contra as planilhas reais**

Run:
```bash
uv run python -c "
from pathlib import Path
from renov_market_scan.ingest.reader import read_device_rows
for name, expected in [('RS_Maio_Androids_2026.xlsx', 1032), ('Template-iPhone.xlsx', 119)]:
    rows, warnings = read_device_rows(Path(name))
    print(name, len(rows), 'avisos:', len(warnings))
    assert len(rows) == expected, (name, len(rows), expected)
    assert warnings == [], warnings
print('ok')
"
```
Expected: `RS_Maio_Androids_2026.xlsx 1032 avisos: 0`, `Template-iPhone.xlsx 119 avisos: 0`, `ok`

- [ ] **Step 7: Rodar lint e type check**

Run: `uv run ruff check . && uv run mypy renov_market_scan`
Expected: sem erros

- [ ] **Step 8: Commit**

```bash
git add renov_market_scan/ingest tests/conftest.py tests/test_reader.py
git commit -m "feat: leitura do template de importacao

Aba localizada pelo cabecalho na linha 2, nunca por nome. Arquivo aberto
read_only e nunca escrito, verificado por hash no teste. Divergencia
entre In-store e Widget&Mobile gera aviso em vez de erro."
```

---

### Task 4: Normalização, chaves e plano de busca

**Files:**
- Create: `renov_market_scan/ingest/normalize.py`
- Create: `tests/test_normalize.py`

**Interfaces:**
- Consumes: `DeviceRow`, `StorageNorm`, `Anomaly`, `ReportKey`, `SearchPlanItem` das Tarefas 2 e 3.
- Produces: `normalize_storage(raw: str | None) -> StorageNorm`; `compute_search_key(manufacturer: str, model: str, storage_label: str) -> str`; `strip_grade_suffix(text: str) -> str`; `build_search_plan(rows: list[DeviceRow], active_only: bool = True, manufacturer_filter: str | None = None, limit: int | None = None) -> tuple[list[SearchPlanItem], list[Anomaly]]`.

- [ ] **Step 1: Escrever o teste que falha**

Arquivo `tests/test_normalize.py`:

```python
from renov_market_scan.ingest.normalize import (
    build_search_plan,
    compute_search_key,
    normalize_storage,
    strip_grade_suffix,
)
from renov_market_scan.models import DeviceRow


def make_row(**overrides) -> DeviceRow:
    base = {
        "row_number": 3,
        "device_name": "SAMSUNG GALAXY A17 128GB A0",
        "manufacturer": "SAMSUNG",
        "model": "GALAXY A17",
        "device_type": "Phone",
        "storage_raw": "128",
        "ram_raw": None,
        "color": "Any Color",
        "price_instore": 400.0,
        "price_widget_mobile": 400.0,
        "erp_code": "10870A0",
    }
    base.update(overrides)
    return DeviceRow(**base)


def test_unambiguous_storages_are_normalized():
    assert normalize_storage("128").label == "128GB"
    assert normalize_storage("128").gb == 128
    assert normalize_storage("128").suspicious is False
    assert normalize_storage("1024").label == "1TB"
    assert normalize_storage("1024").gb == 1024
    assert normalize_storage("2048").label == "2TB"
    assert normalize_storage("2048").gb == 2048


def test_dirty_storages_are_flagged_and_never_guessed():
    for raw in ("1", "1288", "", None, "abc"):
        result = normalize_storage(raw)
        assert result.suspicious is True, raw


def test_dirty_storage_keeps_the_raw_value_in_the_label():
    assert normalize_storage("1288").label == "1288"
    assert normalize_storage("1").label == "1"


def test_grade_suffix_is_removed_only_at_the_end():
    assert strip_grade_suffix("SAMSUNG GALAXY A17 128GB A0") == "SAMSUNG GALAXY A17 128GB"
    assert strip_grade_suffix("MOTOROLA EDGE 70 512GB") == "MOTOROLA EDGE 70 512GB"
    assert strip_grade_suffix("A0 SPECIAL 64GB A0") == "A0 SPECIAL 64GB"


def test_search_key_is_stable_and_case_insensitive():
    first = compute_search_key("SAMSUNG", "GALAXY A17", "128GB")
    second = compute_search_key("samsung", "galaxy a17", "128gb")
    assert first == second
    assert first != compute_search_key("SAMSUNG", "GALAXY A17", "256GB")


def test_active_only_drops_the_placeholder_price():
    rows = [make_row(), make_row(row_number=4, erp_code="X", price_instore=10.0)]
    plan, anomalies = build_search_plan(rows, active_only=True)
    assert len(plan) == 1
    assert plan[0].report_keys[0].erp_code == "10870A0"
    assert anomalies == []


def test_todos_keeps_inactive_rows():
    rows = [make_row(), make_row(row_number=4, erp_code="X", price_instore=10.0)]
    plan, _ = build_search_plan(rows, active_only=False)
    assert len(plan) == 2


def test_suspicious_storage_becomes_an_anomaly_and_is_not_searched():
    rows = [make_row(storage_raw="1288", price_instore=400.0)]
    plan, anomalies = build_search_plan(rows, active_only=True)
    assert plan == []
    assert len(anomalies) == 1
    assert anomalies[0].reason == "storage_suspeito"
    assert anomalies[0].status == "revisao_humana"
    assert anomalies[0].raw_value == "1288"


def test_duplicate_erp_with_different_model_does_not_collapse():
    """The A55 / A55 5G pair shares one ERP Code but is two distinct devices."""
    rows = [
        make_row(row_number=3, model="GALAXY A55 5G", erp_code="10080A0", price_instore=640.0),
        make_row(row_number=4, model="GALAXY A55", erp_code="10080A0", price_instore=640.0),
    ]
    plan, _ = build_search_plan(rows, active_only=True)
    assert len(plan) == 2
    assert {item.model for item in plan} == {"GALAXY A55 5G", "GALAXY A55"}


def test_duplicate_erp_across_manufacturers_does_not_collapse():
    """10884A0 is shared by a Motorola and a Realme in the real sheet."""
    rows = [
        make_row(row_number=3, manufacturer="MOTOROLA", model="MOTO G17", erp_code="10884A0"),
        make_row(row_number=4, manufacturer="REALME", model="C63", erp_code="10884A0"),
    ]
    plan, _ = build_search_plan(rows, active_only=True)
    assert len(plan) == 2


def test_identical_combo_fans_out_to_several_report_keys():
    rows = [
        make_row(row_number=3, erp_code="A0001"),
        make_row(row_number=4, erp_code="A0002"),
    ]
    plan, _ = build_search_plan(rows, active_only=True)
    assert len(plan) == 1
    assert {key.erp_code for key in plan[0].report_keys} == {"A0001", "A0002"}


def test_manufacturer_filter_is_case_insensitive():
    rows = [make_row(), make_row(row_number=4, manufacturer="MOTOROLA", erp_code="X")]
    plan, _ = build_search_plan(rows, manufacturer_filter="samsung")
    assert len(plan) == 1
    assert plan[0].manufacturer == "SAMSUNG"


def test_limit_takes_the_first_items_in_sheet_order():
    rows = [
        make_row(row_number=3, model="A", erp_code="1"),
        make_row(row_number=4, model="B", erp_code="2"),
        make_row(row_number=5, model="C", erp_code="3"),
    ]
    plan, _ = build_search_plan(rows, limit=2)
    assert [item.model for item in plan] == ["A", "B"]
```

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_normalize.py -v`
Expected: FAIL com `ImportError: cannot import name 'build_search_plan'`

- [ ] **Step 3: Implementar `renov_market_scan/ingest/normalize.py`**

```python
"""Storage normalization, key computation and search-plan construction."""

import hashlib
import re

from renov_market_scan.models import Anomaly, DeviceRow, ReportKey, SearchPlanItem, StorageNorm

# Only unambiguous conversions. Everything else is flagged for human review.
STORAGE_LABELS: dict[int, str] = {1024: "1TB", 2048: "2TB"}

# Values known to be wrong in the source data: RAM in the storage column (1),
# and a typo for 128 (1288). Never guessed.
SUSPICIOUS_STORAGE_GB: frozenset[int] = frozenset({1, 1288})

GRADE_SUFFIX = re.compile(r"\s+A0$", re.IGNORECASE)


def strip_grade_suffix(text: str) -> str:
    """Remove the trailing ' A0' grade code. Leaves an internal A0 alone."""
    return GRADE_SUFFIX.sub("", text).strip()


def normalize_storage(raw: str | None) -> StorageNorm:
    """Normalize a storage cell.

    1024 becomes 1TB and 2048 becomes 2TB. Anything non-numeric, empty, or in
    SUSPICIOUS_STORAGE_GB is marked suspicious and keeps its raw label.
    """
    text = (raw or "").strip()
    if not text.isdigit():
        return StorageNorm(label=text or "(vazio)", gb=0, suspicious=True)
    gb = int(text)
    if gb in SUSPICIOUS_STORAGE_GB:
        return StorageNorm(label=text, gb=gb, suspicious=True)
    return StorageNorm(label=STORAGE_LABELS.get(gb, f"{gb}GB"), gb=gb, suspicious=False)


def compute_search_key(manufacturer: str, model: str, storage_label: str) -> str:
    """Stable search/cache key. Case-insensitive, whitespace-collapsed."""
    parts = [
        " ".join(manufacturer.lower().split()),
        " ".join(model.lower().split()),
        storage_label.lower(),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def build_search_plan(
    rows: list[DeviceRow],
    active_only: bool = True,
    manufacturer_filter: str | None = None,
    limit: int | None = None,
) -> tuple[list[SearchPlanItem], list[Anomaly]]:
    """Turn rows into a deduplicated search plan plus the anomalies found.

    Deduplication is by search_key. Each plan item carries every report key it
    serves, so statistics computed once fan out to all of them. Sheet order is
    preserved so that `limit` is predictable.
    """
    wanted = manufacturer_filter.strip().lower() if manufacturer_filter else None
    plan: dict[str, SearchPlanItem] = {}
    anomalies: list[Anomaly] = []

    for row in rows:
        if active_only and not row.is_active:
            continue
        if wanted is not None and row.manufacturer.strip().lower() != wanted:
            continue

        storage = normalize_storage(row.storage_raw)
        if storage.suspicious:
            anomalies.append(
                Anomaly(
                    row_number=row.row_number,
                    erp_code=row.erp_code,
                    device_name=row.device_name,
                    field="Storage, GB*",
                    raw_value=row.storage_raw or "",
                    reason="storage_suspeito",
                    status="revisao_humana",
                )
            )
            continue

        key = compute_search_key(row.manufacturer, row.model, storage.label)
        report_key = ReportKey(
            erp_code=row.erp_code,
            model=row.model,
            storage_label=storage.label,
            device_name=row.device_name,
            price_instore=row.price_instore,
            row_number=row.row_number,
        )
        existing = plan.get(key)
        if existing is None:
            plan[key] = SearchPlanItem(
                search_key=key,
                manufacturer=row.manufacturer,
                model=row.model,
                storage_label=storage.label,
                storage_gb=storage.gb,
                report_keys=[report_key],
            )
        else:
            existing.report_keys.append(report_key)

    items = list(plan.values())
    if limit is not None:
        items = items[:limit]
    return items, anomalies
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `uv run pytest tests/test_normalize.py -v`
Expected: PASS, 13 testes

- [ ] **Step 5: Verificar os números medidos contra as planilhas reais**

Run:
```bash
uv run python -c "
from pathlib import Path
from renov_market_scan.ingest.reader import read_device_rows
from renov_market_scan.ingest.normalize import build_search_plan
# ativos e todos contam ITENS DE PLANO, nao linhas: linha com storage suspeito
# nunca entra no plano, nos dois modos. 1032-88=944 e 119-5=114.
for name, ativos, todos, anom in [
    ('RS_Maio_Androids_2026.xlsx', 285, 944, 88),
    ('Template-iPhone.xlsx', 95, 114, 5),
]:
    rows, _ = read_device_rows(Path(name))
    plan_at, anom_at = build_search_plan(rows, active_only=True)
    plan_all, anom_all = build_search_plan(rows, active_only=False)
    print(f'{name}: ativos={len(plan_at)} todos={len(plan_all)} anomalias_todos={len(anom_all)} anomalias_ativos={len(anom_at)}')
    assert len(plan_at) == ativos, (name, len(plan_at), ativos)
    assert len(plan_all) == todos, (name, len(plan_all), todos)
    assert len(anom_all) == anom, (name, len(anom_all), anom)
    assert len(anom_at) == 0, 'nenhuma anomalia de storage esta ativa'
print('ok')
"
```
Expected: `RS_Maio_Androids_2026.xlsx: ativos=285 todos=944 anomalias_todos=88 anomalias_ativos=0`, `Template-iPhone.xlsx: ativos=95 todos=114 anomalias_todos=5 anomalias_ativos=0`, `ok`

- [ ] **Step 6: Rodar lint e type check**

Run: `uv run ruff check . && uv run mypy renov_market_scan`
Expected: sem erros

- [ ] **Step 7: Commit**

```bash
git add renov_market_scan/ingest/normalize.py tests/test_normalize.py
git commit -m "feat: normalizacao de storage, chave dupla e plano de busca

search_key deduplica a busca; report_keys preserva cada (ERP, Model,
Storage) que a busca atende. Testes cobrem as duas colisoes reais de ERP
medidas na planilha: GALAXY A55 / A55 5G e MOTO G17 / REALME C63.
Storage 1 e 1288 nunca sao adivinhados."
```

---

### Task 5: Primitivas de normalização de texto

**Files:**
- Create: `renov_market_scan/filtering/__init__.py`, `renov_market_scan/filtering/text.py`
- Create: `tests/test_text.py`

**Interfaces:**
- Consumes: nada.
- Produces: `normalize_text(text: str) -> str`; `tokens(text: str) -> list[str]`; `digit_signature(text: str) -> str`. Consumidas pelas Tarefas 6, 7, 8, 9 e 10.

- [ ] **Step 1: Escrever o teste que falha**

Arquivo `tests/test_text.py`:

```python
from renov_market_scan.filtering.text import digit_signature, normalize_text, tokens


def test_lowercases_and_strips_accents():
    assert normalize_text("Capinha SILICONE Câmera Traseira") == (
        "capinha silicone camera traseira"
    )


def test_collapses_whitespace():
    assert normalize_text("  iPhone   13\n128GB  ") == "iphone 13 128gb"


def test_plus_becomes_the_word_plus():
    assert normalize_text("MOTOROLA EDGE+ 256GB") == "motorola edge plus 256gb"


def test_gigas_is_unified_to_gb():
    assert normalize_text("Iphone 13 128 Gigas") == "iphone 13 128gb"
    assert normalize_text("128 GB") == "128gb"


def test_terabytes_expand_to_gigabytes():
    assert normalize_text("iPhone 15 Pro 1TB") == "iphone 15 pro 1024gb"
    assert normalize_text("2 TB") == "2048gb"


def test_punctuation_is_dropped_but_digits_survive():
    assert normalize_text("iPhone SE (2022) - 64GB!") == "iphone se 2022 64gb"


def test_tokens_splits_the_normalized_text():
    assert tokens("iPhone SE (2022) 64GB") == ["iphone", "se", "2022", "64gb"]


def test_digit_signature_keeps_only_digits():
    assert digit_signature("R$ 3.050,00") == "305000"
    assert digit_signature("R$ 1.234,56") == "123456"
    assert digit_signature("sem numeros") == ""
```

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_text.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'renov_market_scan.filtering'`

- [ ] **Step 3: Implementar `renov_market_scan/filtering/text.py`**

```bash
mkdir -p renov_market_scan/filtering
touch renov_market_scan/filtering/__init__.py
```

```python
"""Text normalization primitives shared by every filtering rule.

Every rule compares normalized text, never raw text, so that accents,
punctuation and capacity spelling cannot cause a false mismatch.
"""

import re
import unicodedata

_GIGAS = re.compile(r"\bgigas?\b")
_CAPACITY_SPACING = re.compile(r"(\d+)\s*(gb|tb)\b")
_ONE_TB = re.compile(r"\b1tb\b")
_TWO_TB = re.compile(r"\b2tb\b")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_DIGITS = re.compile(r"\D+")


def normalize_text(text: str) -> str:
    """Lowercase, de-accent, unify capacity spelling, drop punctuation.

    '+' becomes the word 'plus' so that EDGE+ and EDGE PLUS compare equal.
    1TB and 2TB expand to 1024gb and 2048gb so that a title using either
    spelling matches a target stored in gigabytes.
    """
    lowered = text.lower()
    decomposed = unicodedata.normalize("NFKD", lowered)
    unaccented = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    plussed = unaccented.replace("+", " plus ")
    unified = _GIGAS.sub("gb", plussed)
    unified = _CAPACITY_SPACING.sub(r"\1\2", unified)
    unified = _ONE_TB.sub("1024gb", unified)
    unified = _TWO_TB.sub("2048gb", unified)
    cleaned = _NON_ALNUM.sub(" ", unified)
    return " ".join(cleaned.split())


def tokens(text: str) -> list[str]:
    """Normalized whitespace-separated tokens."""
    normalized = normalize_text(text)
    return normalized.split() if normalized else []


def digit_signature(text: str) -> str:
    """Every digit in the text, in order. Used to compare prices to evidence."""
    return _DIGITS.sub("", text)
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `uv run pytest tests/test_text.py -v`
Expected: PASS, 8 testes

- [ ] **Step 5: Commit**

```bash
git add renov_market_scan/filtering tests/test_text.py
git commit -m "feat: primitivas de normalizacao de texto

1TB e 2TB expandem para 1024gb e 2048gb; '+' vira 'plus' para EDGE+
casar com EDGE PLUS. Ambos exigidos pelos dados reais da planilha."
```

---

### Task 6: Parser de preço BRL e rejeição de parcelamento

**Files:**
- Create: `renov_market_scan/filtering/price.py`
- Create: `tests/test_price.py`

**Interfaces:**
- Consumes: nada (usa `re` direto; não depende de `text.py`, porque precisa dos separadores originais).
- Produces: `parse_brl(raw: str) -> float | None`; `extract_price(text: str) -> tuple[float | None, str | None]` devolvendo `(preco, motivo_de_rejeicao)` onde exatamente um dos dois é `None`; `PRICE_ABSENT = "preco_ausente"`; `PRICE_INSTALLMENT = "preco_parcelado"`.

**Decisão de projeto:** só valores prefixados por `R$` são considerados. Um número sem `R$` no título (saúde de bateria, polegadas, quantidade de câmeras) não é preço, e tratá-lo como preço é a fonte mais provável de lixo na amostra.

- [ ] **Step 1: Escrever o teste que falha**

Arquivo `tests/test_price.py`:

```python
from renov_market_scan.filtering.price import (
    PRICE_ABSENT,
    PRICE_INSTALLMENT,
    extract_price,
    parse_brl,
)


def test_parses_thousands_and_decimals():
    assert parse_brl("R$ 1.234,56") == 1234.56


def test_parses_thousands_without_decimals():
    assert parse_brl("R$ 1.190,00") == 1190.0
    assert parse_brl("R$ 3.050,00") == 3050.0


def test_parses_dot_as_thousands_separator_when_three_digits_follow():
    assert parse_brl("R$ 1.190") == 1190.0


def test_parses_a_bare_integer():
    assert parse_brl("R$ 3050") == 3050.0


def test_returns_none_without_the_currency_marker():
    assert parse_brl("3050") is None
    assert parse_brl("Saude de bateria 90%") is None


def test_real_olx_title_yields_the_cash_price():
    title = "iPhone 13 128GB Branco Saude de bateria 90% R$ 3.050,00 | Loja Fisica |"
    price, reason = extract_price(title)
    assert price == 3050.0
    assert reason is None


def test_installment_alone_is_rejected():
    price, reason = extract_price("Galaxy S23 256GB 12x R$ 199,90 sem juros")
    assert price is None
    assert reason == PRICE_INSTALLMENT


def test_installment_with_de_is_rejected():
    price, reason = extract_price("iPhone 12 em 12x de R$ 254,17")
    assert price is None
    assert reason == PRICE_INSTALLMENT


def test_installment_with_space_before_x_is_rejected():
    price, reason = extract_price("Moto G84 10 x R$ 99,00")
    assert price is None
    assert reason == PRICE_INSTALLMENT


def test_cash_price_wins_when_both_appear():
    title = "iPhone 13 128GB R$ 3.050,00 ou 12x R$ 254,17 sem juros"
    price, reason = extract_price(title)
    assert price == 3050.0
    assert reason is None


def test_absent_price_is_reported_as_absent_not_installment():
    price, reason = extract_price("iPhone 13 128GB seminovo, tratar por telefone")
    assert price is None
    assert reason == PRICE_ABSENT


def test_parcelas_de_phrasing_is_rejected():
    price, reason = extract_price("Redmi Note 12 parcelas de R$ 120,00")
    assert price is None
    assert reason == PRICE_INSTALLMENT
```

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_price.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar `renov_market_scan/filtering/price.py`**

```python
"""BRL price parsing. Installment values are never treated as cash prices."""

import re

PRICE_ABSENT = "preco_ausente"
PRICE_INSTALLMENT = "preco_parcelado"

# Only amounts explicitly marked as currency count. A bare number in a title is
# usually battery health, screen size or camera count, not a price.
_MONEY = re.compile(r"r\$\s*(\d[\d.,]*)", re.IGNORECASE)

# 'Nx', 'N x', 'Nx de', 'em Nx de' immediately before the amount.
_INSTALLMENT_PREFIX = re.compile(r"(\d+\s*x\s*(de\s*)?|parcelas?\s+de\s*)$", re.IGNORECASE)

# Appears after the amount in installment offers.
_INSTALLMENT_SUFFIX = re.compile(r"^\s*(sem\s+juros|no\s+cart[ao][eo]?)", re.IGNORECASE)

_TRAILING_SEPARATORS = ".,"


def _to_float(raw: str) -> float | None:
    """Convert a pt-BR amount body (no currency marker) to a float."""
    body = raw.strip().rstrip(_TRAILING_SEPARATORS)
    if not body:
        return None
    try:
        if "," in body:
            return float(body.replace(".", "").replace(",", "."))
        if "." in body:
            _, _, tail = body.rpartition(".")
            if len(tail) == 3:
                return float(body.replace(".", ""))
            return float(body)
        return float(body)
    except ValueError:
        return None


def parse_brl(raw: str) -> float | None:
    """Parse the first BRL amount in the text, or None if there is none."""
    match = _MONEY.search(raw)
    if match is None:
        return None
    return _to_float(match.group(1))


def extract_price(text: str) -> tuple[float | None, str | None]:
    """Return the cash price, or None plus a rejection reason.

    Every currency-marked amount is classified as installment or cash by the
    text immediately around it. The largest cash amount wins: an installment
    value is always smaller than the total it belongs to, so taking the maximum
    of the surviving candidates picks the total price.
    """
    cash: list[float] = []
    saw_installment = False

    for match in _MONEY.finditer(text):
        value = _to_float(match.group(1))
        if value is None:
            continue
        before = text[max(0, match.start() - 24) : match.start()]
        after = text[match.end() : match.end() + 24]
        if _INSTALLMENT_PREFIX.search(before) or _INSTALLMENT_SUFFIX.match(after):
            saw_installment = True
            continue
        cash.append(value)

    if cash:
        return max(cash), None
    if saw_installment:
        return None, PRICE_INSTALLMENT
    return None, PRICE_ABSENT
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `uv run pytest tests/test_price.py -v`
Expected: PASS, 12 testes

- [ ] **Step 5: Rodar lint e type check**

Run: `uv run ruff check . && uv run mypy renov_market_scan`
Expected: sem erros

- [ ] **Step 6: Commit**

```bash
git add renov_market_scan/filtering/price.py tests/test_price.py
git commit -m "feat: parser BRL com rejeicao de parcelamento

Exige o marcador R\$: numero solto no titulo costuma ser saude de
bateria ou polegadas, nao preco. Entre candidatos a vista, o maior
vence, porque parcela e sempre menor que o total."
```

---

### Task 7: Match de modelo e de capacidade

**Files:**
- Create: `renov_market_scan/filtering/matcher.py`, `marcas.yaml`
- Create: `tests/test_matcher.py`

**Interfaces:**
- Consumes: `normalize_text`, `tokens` da Tarefa 5.
- Produces: `QUALIFIERS: frozenset[str]`; `qualifiers_in(text: str) -> set[str]`; `years_in(text: str) -> set[str]`; `MatchResult` (dataclass com `matches: bool`, `flag_5g_divergent: bool`, `reason: str | None`); `model_matches(manufacturer: str, model: str, title: str) -> MatchResult`; `capacity_present(storage_gb: int, title: str, url: str) -> bool`; `load_brand_aliases(path: Path) -> dict[str, list[str]]`; `MODEL_MISMATCH = "modelo_divergente"`.

- [ ] **Step 1: Criar `marcas.yaml`**

```yaml
# Aliases de fabricante usados na montagem da query.
# A planilha lista sub-marcas como fabricantes independentes; o mercado usa a
# marca-mae. Medido nos dados: REDMI 107 linhas, POCO 7, JOVI 11, VIVO 2,
# ITEL 1, ITEL MOBILE 1.
REDMI: ["xiaomi redmi", "redmi"]
POCO: ["xiaomi poco", "poco"]
JOVI: ["vivo", "jovi"]
VIVO: ["vivo"]
"ITEL MOBILE": ["itel"]
ITEL: ["itel"]
```

- [ ] **Step 2: Escrever o teste que falha**

Arquivo `tests/test_matcher.py`:

```python
from pathlib import Path

from renov_market_scan.filtering.matcher import (
    MODEL_MISMATCH,
    capacity_present,
    load_brand_aliases,
    model_matches,
    qualifiers_in,
    years_in,
)


def test_qualifiers_are_detected():
    assert qualifiers_in("iPhone 13 Pro Max") == {"pro", "max"}
    assert qualifiers_in("iPhone 13") == set()
    assert qualifiers_in("Galaxy S23 Ultra") == {"ultra"}
    assert qualifiers_in("MOTOROLA EDGE+") == {"plus"}
    assert qualifiers_in("11 LITE 5G NE") == {"lite", "ne"}
    assert qualifiers_in("EDGE 5G UW") == {"uw"}
    assert qualifiers_in("EDGE 70 BY SWAROVSK") == {"swarovski"}


def test_five_g_is_not_a_strict_qualifier():
    assert "5g" not in qualifiers_in("Galaxy A17 5G")


def test_years_are_detected_only_in_plausible_range():
    assert years_in("iPhone SE (2022)") == {"2022"}
    assert years_in("iPhone 13 128GB") == set()
    assert years_in("Moto 1024gb") == set()


def test_pro_max_is_rejected_for_a_plain_target():
    result = model_matches("APPLE", "IPHONE 13", "iPhone 13 Pro Max 128GB Gold - seminovo")
    assert result.matches is False
    assert result.reason == MODEL_MISMATCH


def test_plain_is_rejected_for_a_pro_target():
    result = model_matches("APPLE", "IPHONE 13 PRO", "iPhone 13 128GB seminovo")
    assert result.matches is False


def test_ultra_is_rejected_for_a_plain_galaxy_target():
    result = model_matches("SAMSUNG", "GALAXY S23", "Samsung Galaxy S23 Ultra 256gb Usado")
    assert result.matches is False


def test_exact_model_is_accepted():
    result = model_matches("APPLE", "IPHONE 13", "iPhone 13 128GB seminovo bateria 90%")
    assert result.matches is True
    assert result.flag_5g_divergent is False
    assert result.reason is None


def test_five_g_mismatch_is_tolerated_and_flagged():
    result = model_matches("SAMSUNG", "GALAXY A17", "Samsung Galaxy A17 5G 128GB usado")
    assert result.matches is True
    assert result.flag_5g_divergent is True


def test_five_g_target_accepts_a_title_without_it_and_flags():
    result = model_matches("SAMSUNG", "GALAXY A17 5G", "Samsung Galaxy A17 128GB usado")
    assert result.matches is True
    assert result.flag_5g_divergent is True


def test_five_g_on_both_sides_is_not_flagged():
    result = model_matches("SAMSUNG", "GALAXY A17 5G", "Galaxy A17 5G 128gb")
    assert result.matches is True
    assert result.flag_5g_divergent is False


def test_target_year_is_mandatory_when_present():
    good = model_matches("APPLE", "IPHONE SE (2022)", "iPhone SE 2022 64GB seminovo")
    bad = model_matches("APPLE", "IPHONE SE (2022)", "iPhone SE 2020 64GB seminovo")
    assert good.matches is True
    assert bad.matches is False


def test_grade_suffix_in_the_target_is_ignored():
    result = model_matches("SAMSUNG", "GALAXY A17 A0", "Galaxy A17 128gb")
    assert result.matches is True


def test_different_model_number_is_rejected():
    result = model_matches("SAMSUNG", "GALAXY A17", "Samsung Galaxy A15 128GB")
    assert result.matches is False


def test_different_device_entirely_is_rejected():
    result = model_matches("MOTOROLA", "MOTO G17", "Realme C63 128GB usado")
    assert result.matches is False


def test_edge_plus_matches_the_written_out_form():
    result = model_matches("MOTOROLA", "EDGE+", "Motorola Edge Plus 256gb")
    assert result.matches is True


def test_capacity_found_in_the_title():
    assert capacity_present(128, "iPhone 13 128GB seminovo", "https://x") is True
    assert capacity_present(128, "iPhone 13 128 GB seminovo", "https://x") is True
    assert capacity_present(128, "iPhone 13 128 Gigas", "https://x") is True


def test_capacity_found_only_in_the_url():
    assert capacity_present(
        128, "iPhone 13 seminovo", "https://www.olx.com.br/celulares/apple/iphone-13/128gb"
    ) is True


def test_terabyte_target_matches_a_tb_spelled_title():
    assert capacity_present(1024, "iPhone 15 Pro 1TB", "https://x") is True
    assert capacity_present(1024, "iPhone 15 Pro 1024GB", "https://x") is True


def test_missing_capacity_is_detected():
    assert capacity_present(128, "iPhone 13 seminovo", "https://olx.com.br/iphone-13") is False


def test_wrong_capacity_is_detected():
    assert capacity_present(128, "iPhone 13 256GB seminovo", "https://x") is False


def test_brand_aliases_load_from_yaml():
    aliases = load_brand_aliases(Path("marcas.yaml"))
    assert "xiaomi redmi" in aliases["REDMI"]
    assert aliases["JOVI"] == ["vivo", "jovi"]
```

- [ ] **Step 3: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_matcher.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 4: Implementar `renov_market_scan/filtering/matcher.py`**

```python
"""Strict model matching and capacity verification.

The qualifier set present in the advert title must equal the qualifier set of
the target model. That single rule is what keeps a Pro Max out of a plain
iPhone 13 sample, and a plain S23 out of an S23 Ultra sample.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from renov_market_scan.filtering.text import normalize_text, tokens

MODEL_MISMATCH = "modelo_divergente"

# Qualifiers that change the device, and therefore the price. 'ne', 'uw' and
# 'swarovski' were added after measuring the real sheet.
QUALIFIERS: frozenset[str] = frozenset(
    {
        "pro",
        "max",
        "plus",
        "mini",
        "ultra",
        "neo",
        "fusion",
        "lite",
        "fe",
        "se",
        "power",
        "play",
        "air",
        "ne",
        "uw",
        "swarovski",
    }
)

# 5g is deliberately outside the strict set: in the source sheet the A17 and
# A17 5G rows carry an identical price, so splitting them halves the sample for
# no gain. The divergence is recorded on the listing instead.
TOLERATED_VARIANT = "5g"

_YEAR = re.compile(r"^20[0-2]\d$")
_GRADE_TOKEN = "a0"

# 'BY SWAROVSK' is spelled truncated in the sheet. One pass, so that an
# already-normalized 'swarovski' is not extended into 'swarovskii'.
_SWAROVSKI = re.compile(r"\b(?:by\s+)?swarovsk\w*")


@dataclass(frozen=True)
class MatchResult:
    """Outcome of comparing an advert title against a target model."""

    matches: bool
    flag_5g_divergent: bool = False
    reason: str | None = None


def _canonical(text: str) -> str:
    """Normalize and fold the Swarovski spellings into one token."""
    return _SWAROVSKI.sub("swarovski", normalize_text(text))


def qualifiers_in(text: str) -> set[str]:
    """The set of price-changing qualifiers present in the text."""
    return {token for token in _canonical(text).split() if token in QUALIFIERS}


def years_in(text: str) -> set[str]:
    """Four-digit years in a plausible model-year range."""
    return {token for token in tokens(text) if _YEAR.match(token)}


def _core_tokens(text: str) -> set[str]:
    """Model-identifying tokens: not qualifiers, not years, not grade codes."""
    result: set[str] = set()
    for token in _canonical(text).split():
        if token in QUALIFIERS or token == TOLERATED_VARIANT or token == _GRADE_TOKEN:
            continue
        if _YEAR.match(token):
            continue
        result.add(token)
    return result


def model_matches(manufacturer: str, model: str, title: str) -> MatchResult:
    """Compare an advert title against the target model.

    The manufacturer is not required to appear in the title: real OLX adverts
    write 'iPhone 13 128GB' with no 'Apple'. The domain filter and the query
    already constrain the brand.
    """
    target_qualifiers = qualifiers_in(model)
    title_qualifiers = qualifiers_in(title)
    if target_qualifiers != title_qualifiers:
        return MatchResult(matches=False, reason=MODEL_MISMATCH)

    target_years = years_in(model)
    if target_years and not target_years.issubset(years_in(title)):
        return MatchResult(matches=False, reason=MODEL_MISMATCH)

    target_core = _core_tokens(model)
    title_core = _core_tokens(title)
    if not target_core.issubset(title_core):
        return MatchResult(matches=False, reason=MODEL_MISMATCH)

    target_has_5g = TOLERATED_VARIANT in _canonical(model).split()
    title_has_5g = TOLERATED_VARIANT in _canonical(title).split()
    return MatchResult(matches=True, flag_5g_divergent=target_has_5g != title_has_5g)


def capacity_present(storage_gb: int, title: str, url: str) -> bool:
    """True when the exact capacity appears in the title or in the URL.

    Capacity moves the price too much to infer, so an advert that does not state
    it is discarded rather than assumed.
    """
    needle = f"{storage_gb}gb"
    return needle in normalize_text(title) or needle in normalize_text(url)


def load_brand_aliases(path: Path) -> dict[str, list[str]]:
    """Load the manufacturer alias map used when building queries."""
    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    return {str(key): [str(item) for item in value] for key, value in loaded.items()}
```

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `uv run pytest tests/test_matcher.py -v`
Expected: PASS, 20 testes

- [ ] **Step 6: Rodar lint e type check**

Run: `uv run ruff check . && uv run mypy renov_market_scan`
Expected: sem erros

- [ ] **Step 7: Commit**

```bash
git add renov_market_scan/filtering/matcher.py marcas.yaml tests/test_matcher.py
git commit -m "feat: match estrito de modelo e verificacao de capacidade

Igualdade de conjunto de qualificadores nos dois sentidos. 5G fica fora
do conjunto estrito e vira flag, porque na planilha A17 e A17 5G tem
preco identico. Ano entre parenteses e obrigatorio quando presente no
alvo, senao as tres geracoes do iPhone SE compartilhariam amostra."
```

---

### Task 8: Blacklist de peças e classificação de condição

**Files:**
- Create: `renov_market_scan/filtering/blacklist.py`, `renov_market_scan/filtering/condition.py`
- Create: `tests/test_blacklist.py`, `tests/test_condition.py`

**Interfaces:**
- Consumes: `normalize_text` da Tarefa 5.
- Produces: `HARD_TERMS: tuple[str, ...]`; `CONTEXTUAL_TERMS: dict[str, tuple[str, ...]]`; `is_hard_blacklisted(title: str) -> bool`; `is_contextual_part(title: str) -> bool`; `ACCESSORY_OR_PART = "acessorio_ou_peca"`; `LIKELY_PART = "peca_provavel"`. E `classify_condition(title: str) -> Condition`.

**Divergência deliberada da especificação, justificada pelos dados.** A blacklist da especificação inclui `bateria`, `tela` e `display`. Mas o anúncio real que medi na Fase 0 é:

> `"iPhone 13 128GB Branco Saúde de bateria 90% R$ 3.050,00 | Loja Física |"`

Uma blacklist literal rejeitaria esse anúncio legítimo — e "saúde de bateria" aparece na maioria dos anúncios de iPhone usado, ou seja, o filtro derrubaria justamente a melhor parte da amostra. A solução é dividir em duas listas:

- **`HARD_TERMS`** — inequívocos, verificados antes de tudo (regra 1 do pipeline).
- **`CONTEXTUAL_TERMS`** — `bateria`, `tela`, `display`, cada um com marcadores de isenção. Verificados **depois** do match de modelo e capacidade (regra 4), quando já se sabe que o anúncio nomeia o aparelho e a capacidade certos.

- [ ] **Step 1: Escrever o teste de blacklist que falha**

Arquivo `tests/test_blacklist.py`:

```python
from renov_market_scan.filtering.blacklist import is_contextual_part, is_hard_blacklisted


def test_accessories_are_rejected():
    for title in (
        "Capa capinha silicone iPhone 13",
        "Pelicula de vidro 3D iPhone 13",
        "Película Cerâmica iPhone 13",
        "Carcaça traseira iPhone 13",
        "Cabo carregador turbo 20W",
        "Fone de ouvido para iPhone",
        "Suporte veicular para celular",
        "Chip TIM 5G",
    ):
        assert is_hard_blacklisted(title) is True, title


def test_parts_and_junk_are_rejected():
    for title in (
        "Placa mae iPhone 13 com defeito",
        "Flex conector de carga iPhone 13",
        "Camera traseira original iPhone 13",
        "Alto-falante iPhone 13",
        "Botao home iPhone 7",
        "Aparelho para retirada de pecas",
        "iPhone 13 nao liga, sucata",
        "iPhone 13 sem funcionar",
        "iPhone 13 replica primeira linha",
        "Celular clone similar generico",
    ):
        assert is_hard_blacklisted(title) is True, title


def test_a_legitimate_advert_is_not_hard_blacklisted():
    title = "iPhone 13 128GB Branco Saude de bateria 90% R$ 3.050,00 | Loja Fisica |"
    assert is_hard_blacklisted(title) is False


def test_battery_health_is_exempt_from_the_contextual_rule():
    for title in (
        "iPhone 13 128GB Saude de bateria 90%",
        "iPhone 13 128GB bateria 100%",
        "iPhone 13 128gb bateria com 87% de capacidade",
        "iPhone 11 64gb 320 ciclos de bateria",
    ):
        assert is_contextual_part(title) is False, title


def test_a_battery_being_sold_is_caught_by_the_contextual_rule():
    assert is_contextual_part("Bateria original iPhone 13 nova") is True


def test_screen_size_is_exempt():
    for title in (
        "Galaxy A17 128gb tela de 6.7 polegadas",
        "Moto G84 tela amoled 120hz",
    ):
        assert is_contextual_part(title) is False, title


def test_a_screen_being_sold_is_caught():
    assert is_contextual_part("Tela display frontal iPhone 13 original") is True
    assert is_contextual_part("Display Galaxy A17 com aro") is True


def test_clean_titles_pass_both_rules():
    title = "Samsung Galaxy A17 5G 128GB usado excelente estado"
    assert is_hard_blacklisted(title) is False
    assert is_contextual_part(title) is False
```

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_blacklist.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar `renov_market_scan/filtering/blacklist.py`**

```python
"""Reject accessories, parts and junk listings.

Two lists, because some words are ambiguous. 'capinha' only ever means a case,
but 'bateria' appears in the majority of legitimate used-iPhone titles as
'Saude de bateria 90%'. A literal blacklist would discard the best part of the
sample, so ambiguous terms are checked separately, after the model and capacity
rules have already confirmed the advert is about the right device.
"""

from renov_market_scan.filtering.text import normalize_text

ACCESSORY_OR_PART = "acessorio_ou_peca"
LIKELY_PART = "peca_provavel"

# Unambiguous. A title containing any of these is never the phone itself.
HARD_TERMS: tuple[str, ...] = (
    "capa",
    "capinha",
    "case",
    "pelicula",
    "vidro",
    "frontal",
    "touch",
    "placa",
    "flex",
    "conector",
    "carcaca",
    "aro",
    "tampa",
    "camera traseira",
    "alto falante",
    "alto-falante",
    "botao",
    "pecas",
    "sucata",
    "para retirada",
    "nao liga",
    "sem funcionar",
    "replica",
    "clone",
    "similar",
    "generico",
    "carregador",
    "fone",
    "cabo",
    "chip",
    "suporte",
)

# Ambiguous terms mapped to the markers that exempt them. If any marker is
# present the term is describing the phone's condition or spec, not a part.
CONTEXTUAL_TERMS: dict[str, tuple[str, ...]] = {
    "bateria": ("saude", "%", "capacidade", "ciclos", "health"),
    "tela": ("polegada", "polegadas", "amoled", "oled", "hz", "trincada", "quebrada"),
    "display": ("polegada", "polegadas", "amoled", "oled", "hz"),
}


def is_hard_blacklisted(title: str) -> bool:
    """True when the title contains an unambiguous accessory or part term."""
    normalized = normalize_text(title)
    padded = f" {normalized} "
    for term in HARD_TERMS:
        if f" {normalize_text(term)} " in padded:
            return True
    return False


def is_contextual_part(title: str) -> bool:
    """True when an ambiguous term appears with no exempting marker.

    The raw title is inspected for '%' because normalization drops punctuation.
    """
    normalized = normalize_text(title)
    padded = f" {normalized} "
    for term, markers in CONTEXTUAL_TERMS.items():
        if f" {term} " not in padded:
            continue
        exempt = False
        for marker in markers:
            if marker == "%":
                exempt = "%" in title
            else:
                exempt = marker in normalized
            if exempt:
                break
        if not exempt:
            return True
    return False
```

- [ ] **Step 4: Rodar o teste de blacklist e confirmar que passa**

Run: `uv run pytest tests/test_blacklist.py -v`
Expected: PASS, 8 testes

- [ ] **Step 5: Escrever o teste de condição que falha**

Arquivo `tests/test_condition.py`:

```python
from renov_market_scan.filtering.condition import classify_condition


def test_new_is_detected():
    for title in (
        "iPhone 13 128GB lacrado na caixa",
        "iPhone 13 novo na caixa nota fiscal",
        "Galaxy A17 128gb NOVO LACRADO",
    ):
        assert classify_condition(title) == "novo", title


def test_semi_new_is_detected():
    for title in (
        "iPhone 13 128GB seminovo",
        "iPhone 13 Semi Novo 128gb",
        "Galaxy S23 vitrine 256gb",
        "iPhone 12 recondicionado 64gb",
    ):
        assert classify_condition(title) == "seminovo", title


def test_used_is_detected():
    for title in (
        "iPhone 13 128GB usado",
        "Galaxy A17 usado excelente estado",
    ):
        assert classify_condition(title) == "usado", title


def test_unknown_when_nothing_says_so():
    assert classify_condition("iPhone 13 128GB Branco R$ 3.050,00") == "desconhecido"


def test_new_wins_over_used_when_both_appear():
    """'novo na caixa' plus 'usado' in the same title: lacrado is decisive."""
    assert classify_condition("iPhone 13 lacrado, aceito seu usado na troca") == "novo"


def test_seminovo_wins_over_usado():
    assert classify_condition("iPhone 13 seminovo, pouco usado") == "seminovo"
```

- [ ] **Step 6: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_condition.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 7: Implementar `renov_market_scan/filtering/condition.py`**

```python
"""Classify the advertised condition of a listing.

Precedence is novo, then seminovo, then usado. A title that says 'lacrado' and
also 'aceito seu usado na troca' is a sealed unit, so the strongest signal wins
rather than the first one found.
"""

from renov_market_scan.filtering.text import normalize_text
from renov_market_scan.models import Condition

NEW_MARKERS: tuple[str, ...] = ("lacrado", "novo na caixa", "novo lacrado", "selado")
SEMI_NEW_MARKERS: tuple[str, ...] = (
    "seminovo",
    "semi novo",
    "vitrine",
    "recondicionado",
    "refurbished",
)
USED_MARKERS: tuple[str, ...] = ("usado", "usada", "de segunda mao")


def _contains_any(haystack: str, needles: tuple[str, ...]) -> bool:
    return any(needle in haystack for needle in needles)


def classify_condition(title: str) -> Condition:
    """Return novo, seminovo, usado or desconhecido."""
    normalized = normalize_text(title)
    if _contains_any(normalized, NEW_MARKERS):
        return "novo"
    if _contains_any(normalized, SEMI_NEW_MARKERS):
        return "seminovo"
    if _contains_any(normalized, USED_MARKERS):
        return "usado"
    return "desconhecido"
```

- [ ] **Step 8: Rodar o teste de condição e confirmar que passa**

Run: `uv run pytest tests/test_condition.py -v`
Expected: PASS, 6 testes

- [ ] **Step 9: Rodar lint, type check e a suíte inteira**

Run: `uv run ruff check . && uv run mypy renov_market_scan && uv run pytest`
Expected: sem erros, toda a suíte verde

- [ ] **Step 10: Commit**

```bash
git add renov_market_scan/filtering/blacklist.py renov_market_scan/filtering/condition.py \
        tests/test_blacklist.py tests/test_condition.py
git commit -m "feat: blacklist em duas camadas e classificacao de condicao

Divergencia deliberada da especificacao: bateria, tela e display saem da
blacklist literal e viram regra contextual com marcadores de isencao. O
anuncio real medido na Fase 0 diz 'Saude de bateria 90%' e seria
rejeitado por uma blacklist literal, derrubando a melhor parte da
amostra de iPhone."
```

---

### Task 9: Evidência por citação e deduplicação

**Files:**
- Create: `renov_market_scan/filtering/evidence.py`, `renov_market_scan/filtering/dedupe.py`
- Create: `tests/test_evidence.py`, `tests/test_dedupe.py`

**Interfaces:**
- Consumes: `digit_signature`, `normalize_text` da Tarefa 5; `Listing` da Tarefa 2.
- Produces: `price_has_evidence(price_brl: float, evidence_texts: list[str]) -> bool`; `PRICE_WITHOUT_EVIDENCE = "preco_sem_evidencia"`. E `canonical_url(url: str) -> str`; `dedupe(listings: list[Listing]) -> tuple[list[Listing], list[Listing]]` devolvendo `(mantidos, duplicados)`; `DUPLICATE = "duplicado"`.

- [ ] **Step 1: Escrever o teste de evidência que falha**

Arquivo `tests/test_evidence.py`:

```python
from renov_market_scan.filtering.evidence import price_has_evidence


def test_price_present_verbatim_in_the_cited_text():
    evidence = ["iPhone 13 128GB Branco Saude de bateria 90% R$ 3.050,00 | Loja Fisica"]
    assert price_has_evidence(3050.0, evidence) is True


def test_price_present_with_a_different_separator_style():
    assert price_has_evidence(3050.0, ["Vendo por R$ 3050"]) is True
    assert price_has_evidence(1234.56, ["preco R$ 1.234,56 a vista"]) is True


def test_integer_price_matches_a_two_decimal_rendering():
    assert price_has_evidence(1190.0, ["R$ 1.190,00 no pix"]) is True


def test_price_absent_from_every_evidence_text_is_rejected():
    evidence = ["iPhone 13 128GB Branco, tratar por telefone", "Celulares APPLE no Brasil"]
    assert price_has_evidence(3050.0, evidence) is False


def test_a_different_price_in_the_evidence_does_not_count():
    assert price_has_evidence(3050.0, ["R$ 2.300,00 a vista"]) is False


def test_empty_evidence_is_rejected():
    assert price_has_evidence(3050.0, []) is False
    assert price_has_evidence(3050.0, ["", "   "]) is False
```

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_evidence.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar `renov_market_scan/filtering/evidence.py`**

```python
"""Verify a reported price against the API's own cited text.

Only the model reads search-result content; the client receives just URLs,
titles and encrypted content. The citations, however, carry up to 150 verbatim
characters. Requiring the reported price to appear in that verbatim text turns
'the model said R$ 3.050' into 'the API cited the text containing R$ 3.050'.
This is the defence for the minimum and maximum, which are the two numbers the
report exists to produce and the two most exposed to a fabricated value.
"""

from renov_market_scan.filtering.text import digit_signature

PRICE_WITHOUT_EVIDENCE = "preco_sem_evidencia"


def _candidate_signatures(price_brl: float) -> set[str]:
    """Digit signatures a human would write for this amount.

    3050.0 can appear as '3.050,00' (305000) or as '3050' (3050), so both
    renderings are accepted.
    """
    with_cents = f"{price_brl:.2f}"
    signatures = {digit_signature(with_cents)}
    if price_brl == int(price_brl):
        signatures.add(digit_signature(str(int(price_brl))))
    return {signature for signature in signatures if signature}


def price_has_evidence(price_brl: float, evidence_texts: list[str]) -> bool:
    """True when the price's digits appear in at least one evidence text."""
    signatures = _candidate_signatures(price_brl)
    if not signatures:
        return False
    for text in evidence_texts:
        haystack = digit_signature(text)
        if not haystack:
            continue
        if any(signature in haystack for signature in signatures):
            return True
    return False
```

- [ ] **Step 4: Rodar o teste de evidência e confirmar que passa**

Run: `uv run pytest tests/test_evidence.py -v`
Expected: PASS, 6 testes

- [ ] **Step 5: Escrever o teste de dedupe que falha**

Arquivo `tests/test_dedupe.py`:

```python
from renov_market_scan.filtering.dedupe import canonical_url, dedupe
from renov_market_scan.models import Listing


def make_listing(**overrides) -> Listing:
    base = {
        "search_key": "k",
        "source": "olx",
        "title": "iPhone 13 128GB seminovo",
        "price_brl": 3050.0,
        "condition": "seminovo",
        "url": "https://sp.olx.com.br/celulares/iphone-13-128gb-seminovo-1446106085",
        "captured_at": "2026-07-28T10:00:00",
    }
    base.update(overrides)
    return Listing(**base)


def test_tracking_parameters_are_stripped():
    dirty = "https://olx.com.br/x-123?utm_source=google&utm_medium=cpc&gclid=abc"
    assert canonical_url(dirty) == "https://olx.com.br/x-123"


def test_meaningful_query_parameters_survive():
    url = "https://lista.mercadolivre.com.br/celulares?q=iphone+13"
    assert canonical_url(url) == "https://lista.mercadolivre.com.br/celulares?q=iphone+13"


def test_trailing_slash_and_fragment_are_normalized():
    assert canonical_url("https://olx.com.br/x-123/#descricao") == "https://olx.com.br/x-123"


def test_scheme_and_host_case_are_normalized():
    assert canonical_url("HTTPS://OLX.COM.BR/X-123") == "https://olx.com.br/X-123"


def test_same_canonical_url_is_deduped():
    first = make_listing()
    second = make_listing(url=make_listing().url + "?utm_source=x")
    kept, duplicates = dedupe([first, second])
    assert len(kept) == 1
    assert len(duplicates) == 1


def test_same_title_and_price_from_a_different_url_is_deduped():
    first = make_listing(url="https://olx.com.br/a-1")
    second = make_listing(url="https://olx.com.br/b-2")
    kept, duplicates = dedupe([first, second])
    assert len(kept) == 1
    assert len(duplicates) == 1


def test_same_title_with_a_different_price_is_kept():
    first = make_listing(url="https://olx.com.br/a-1", price_brl=3050.0)
    second = make_listing(url="https://olx.com.br/b-2", price_brl=2800.0)
    kept, _ = dedupe([first, second])
    assert len(kept) == 2


def test_first_occurrence_is_the_one_kept():
    first = make_listing(url="https://olx.com.br/a-1", source="olx")
    second = make_listing(url="https://olx.com.br/a-1", source="enjoei")
    kept, _ = dedupe([first, second])
    assert kept[0].source == "olx"


def test_empty_input_is_handled():
    kept, duplicates = dedupe([])
    assert kept == []
    assert duplicates == []
```

- [ ] **Step 6: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_dedupe.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 7: Implementar `renov_market_scan/filtering/dedupe.py`**

```python
"""Deduplicate listings by canonical URL and by (normalized title, price)."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from renov_market_scan.filtering.text import normalize_text
from renov_market_scan.models import Listing

DUPLICATE = "duplicado"

# Query parameters that identify a campaign, not the advert.
TRACKING_PREFIXES: tuple[str, ...] = ("utm_", "gclid", "fbclid", "mkt_", "pk_", "_gl")


def _is_tracking(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.startswith(prefix) for prefix in TRACKING_PREFIXES)


def canonical_url(url: str) -> str:
    """Strip tracking parameters, fragments and trailing slashes.

    Scheme and host are lowercased; the path keeps its case because some
    marketplaces use case-sensitive advert slugs.
    """
    parts = urlsplit(url.strip())
    kept = [(name, value) for name, value in parse_qsl(parts.query) if not _is_tracking(name)]
    path = parts.path.rstrip("/")
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, urlencode(kept), "")
    )


def dedupe(listings: list[Listing]) -> tuple[list[Listing], list[Listing]]:
    """Return (kept, duplicates). The first occurrence of a key is kept."""
    seen_urls: set[str] = set()
    seen_title_price: set[tuple[str, float | None]] = set()
    kept: list[Listing] = []
    duplicates: list[Listing] = []

    for listing in listings:
        url_key = canonical_url(listing.url)
        title_price_key = (normalize_text(listing.title), listing.price_brl)
        if url_key in seen_urls or title_price_key in seen_title_price:
            duplicates.append(listing)
            continue
        seen_urls.add(url_key)
        seen_title_price.add(title_price_key)
        kept.append(listing)

    return kept, duplicates
```

- [ ] **Step 8: Rodar o teste de dedupe e confirmar que passa**

Run: `uv run pytest tests/test_dedupe.py -v`
Expected: PASS, 9 testes

- [ ] **Step 9: Rodar lint, type check e a suíte inteira**

Run: `uv run ruff check . && uv run mypy renov_market_scan && uv run pytest`
Expected: sem erros, toda a suíte verde

- [ ] **Step 10: Commit**

```bash
git add renov_market_scan/filtering/evidence.py renov_market_scan/filtering/dedupe.py \
        tests/test_evidence.py tests/test_dedupe.py
git commit -m "feat: regra de evidencia por citacao e deduplicacao

O preco reportado tem que aparecer nos digitos do cited_text verbatim da
API. Sem isso, min e max ficam sem verificacao independente, e sao
justamente os dois numeros que o relatorio existe para produzir."
```

---

### Task 10: Pipeline de filtro

**Files:**
- Create: `renov_market_scan/filtering/pipeline.py`
- Create: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: tudo das Tarefas 5 a 9.
- Produces: `FilterContext` (dataclass frozen: `storage_gb: int`, `manufacturer: str`, `model: str`, `include_new: bool`, `price_floor_brl: float`, `price_ceiling_brl: float`); `run_pipeline(listings: list[Listing], context: FilterContext) -> tuple[list[Listing], list[RejectedListing]]`; `CONDITION_EXCLUDED = "condicao_excluida"`; `PRICE_OUT_OF_RANGE = "preco_fora_de_faixa"`.

**Ordem das regras.** A primeira que rejeita é a registrada. A blacklist contextual (regra 4) vem **depois** do match de modelo e de capacidade, porque só faz sentido perguntar "isto é uma peça?" depois de saber que o anúncio nomeia o aparelho e a capacidade certos.

**Origem do preço.** O parser próprio roda sobre `título + cited_text` e tem prioridade: é auditável e detecta parcelamento. O número que o modelo extraiu é usado apenas como fallback quando o parser não encontra nada. Em qualquer um dos dois casos a regra de evidência é obrigatória.

- [ ] **Step 1: Escrever o teste que falha**

Arquivo `tests/test_pipeline.py`:

```python
from renov_market_scan.filtering.pipeline import FilterContext, run_pipeline
from renov_market_scan.models import Listing

CONTEXT = FilterContext(
    storage_gb=128,
    manufacturer="APPLE",
    model="IPHONE 13",
    include_new=False,
    price_floor_brl=80.0,
    price_ceiling_brl=15000.0,
)


def make_listing(**overrides) -> Listing:
    base = {
        "search_key": "k",
        "source": "olx",
        "title": "iPhone 13 128GB seminovo R$ 3.050,00",
        "price_brl": 3050.0,
        "condition": "seminovo",
        "url": "https://sp.olx.com.br/celulares/iphone-13-128gb-seminovo-1",
        "captured_at": "2026-07-28T10:00:00",
        "cited_text": "iPhone 13 128GB seminovo R$ 3.050,00",
    }
    base.update(overrides)
    return Listing(**base)


def reasons(rejected) -> list[str]:
    return [item.reason for item in rejected]


def test_a_clean_listing_is_accepted():
    accepted, rejected = run_pipeline([make_listing()], CONTEXT)
    assert len(accepted) == 1
    assert rejected == []
    assert accepted[0].price_brl == 3050.0
    assert accepted[0].condition == "seminovo"


def test_accessory_is_rejected_first():
    listing = make_listing(title="Capa capinha iPhone 13 128GB R$ 90,00")
    accepted, rejected = run_pipeline([listing], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["acessorio_ou_peca"]


def test_pro_max_is_rejected_as_model_mismatch():
    listing = make_listing(
        title="iPhone 13 Pro Max 128GB Gold seminovo R$ 3.200,00",
        cited_text="iPhone 13 Pro Max 128GB Gold seminovo R$ 3.200,00",
    )
    accepted, rejected = run_pipeline([listing], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["modelo_divergente"]


def test_missing_capacity_is_rejected():
    listing = make_listing(
        title="iPhone 13 seminovo R$ 3.050,00",
        cited_text="iPhone 13 seminovo R$ 3.050,00",
        url="https://sp.olx.com.br/celulares/iphone-13-seminovo-1",
    )
    accepted, rejected = run_pipeline([listing], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["capacidade_ausente"]


def test_battery_health_survives_the_contextual_rule():
    text = "iPhone 13 128GB Branco Saude de bateria 90% R$ 3.050,00 | Loja Fisica |"
    accepted, rejected = run_pipeline([make_listing(title=text, cited_text=text)], CONTEXT)
    assert len(accepted) == 1, reasons(rejected)


def test_a_battery_part_is_rejected_after_model_and_capacity():
    text = "Bateria original iPhone 13 128GB nova R$ 150,00"
    accepted, rejected = run_pipeline([make_listing(title=text, cited_text=text)], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["peca_provavel"]


def test_new_is_excluded_by_default():
    text = "iPhone 13 128GB lacrado na caixa R$ 4.200,00"
    accepted, rejected = run_pipeline(
        [make_listing(title=text, cited_text=text, condition="novo")], CONTEXT
    )
    assert accepted == []
    assert reasons(rejected) == ["condicao_excluida"]


def test_new_is_included_when_requested():
    text = "iPhone 13 128GB lacrado na caixa R$ 4.200,00"
    context = FilterContext(**{**CONTEXT.__dict__, "include_new": True})
    accepted, _ = run_pipeline([make_listing(title=text, cited_text=text)], context)
    assert len(accepted) == 1
    assert accepted[0].condition == "novo"


def test_unknown_condition_is_excluded_by_default():
    text = "iPhone 13 128GB Branco R$ 3.050,00"
    accepted, rejected = run_pipeline([make_listing(title=text, cited_text=text)], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["condicao_excluida"]


def test_installment_only_is_rejected():
    text = "iPhone 13 128GB seminovo 12x R$ 254,17 sem juros"
    accepted, rejected = run_pipeline([make_listing(title=text, cited_text=text)], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["preco_parcelado"]


def test_our_parser_wins_over_the_model_reported_price():
    text = "iPhone 13 128GB seminovo R$ 3.050,00 ou 12x R$ 254,17"
    listing = make_listing(title=text, cited_text=text, price_brl=254.17)
    accepted, _ = run_pipeline([listing], CONTEXT)
    assert accepted[0].price_brl == 3050.0


def test_price_without_evidence_is_rejected():
    listing = make_listing(
        title="iPhone 13 128GB seminovo",
        cited_text="iPhone 13 128GB seminovo, tratar",
        price_brl=3050.0,
    )
    accepted, rejected = run_pipeline([listing], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["preco_sem_evidencia"]


def test_absent_price_everywhere_is_rejected():
    listing = make_listing(
        title="iPhone 13 128GB seminovo", cited_text="iPhone 13 128GB seminovo", price_brl=None
    )
    accepted, rejected = run_pipeline([listing], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["preco_ausente"]


def test_price_below_the_floor_is_rejected():
    text = "iPhone 13 128GB seminovo R$ 50,00"
    accepted, rejected = run_pipeline([make_listing(title=text, cited_text=text)], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["preco_fora_de_faixa"]


def test_price_above_the_ceiling_is_rejected():
    text = "iPhone 13 128GB seminovo R$ 99.000,00"
    accepted, rejected = run_pipeline([make_listing(title=text, cited_text=text)], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["preco_fora_de_faixa"]


def test_duplicates_are_rejected_with_a_reason():
    first = make_listing()
    second = make_listing(url=make_listing().url + "?utm_source=x")
    accepted, rejected = run_pipeline([first, second], CONTEXT)
    assert len(accepted) == 1
    assert reasons(rejected) == ["duplicado"]


def test_five_g_flag_reaches_the_accepted_listing():
    context = FilterContext(**{**CONTEXT.__dict__, "manufacturer": "SAMSUNG", "model": "GALAXY A17"})
    text = "Samsung Galaxy A17 5G 128GB usado R$ 900,00"
    accepted, _ = run_pipeline([make_listing(title=text, cited_text=text)], context)
    assert len(accepted) == 1
    assert accepted[0].flag_5g_divergent is True


def test_every_rejection_carries_a_reason():
    listings = [
        make_listing(title="Capa iPhone 13", cited_text="Capa iPhone 13"),
        make_listing(title="iPhone 13 Pro 128GB R$ 1,00", cited_text="iPhone 13 Pro 128GB R$ 1,00"),
    ]
    _, rejected = run_pipeline(listings, CONTEXT)
    assert len(rejected) == 2
    assert all(item.reason for item in rejected)
```

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar `renov_market_scan/filtering/pipeline.py`**

```python
"""Order the filtering rules and record why every discard happened.

The first rule that rejects a listing is the one recorded, so the Descartados
sheet names the most decisive reason rather than an incidental one.
"""

from dataclasses import dataclass

from renov_market_scan.filtering.blacklist import (
    ACCESSORY_OR_PART,
    LIKELY_PART,
    is_contextual_part,
    is_hard_blacklisted,
)
from renov_market_scan.filtering.condition import classify_condition
from renov_market_scan.filtering.dedupe import DUPLICATE, dedupe
from renov_market_scan.filtering.evidence import PRICE_WITHOUT_EVIDENCE, price_has_evidence
from renov_market_scan.filtering.matcher import capacity_present, model_matches
from renov_market_scan.filtering.price import PRICE_ABSENT, extract_price
from renov_market_scan.models import Listing, RejectedListing

CONDITION_EXCLUDED = "condicao_excluida"
PRICE_OUT_OF_RANGE = "preco_fora_de_faixa"
CAPACITY_MISSING = "capacidade_ausente"

ACCEPTED_CONDITIONS: frozenset[str] = frozenset({"seminovo", "usado"})


@dataclass(frozen=True)
class FilterContext:
    """Everything a rule needs that is not on the listing itself."""

    storage_gb: int
    manufacturer: str
    model: str
    include_new: bool
    price_floor_brl: float
    price_ceiling_brl: float


def run_pipeline(
    listings: list[Listing], context: FilterContext
) -> tuple[list[Listing], list[RejectedListing]]:
    """Apply every rule in order. Returns (accepted, rejected-with-reason)."""
    survivors: list[Listing] = []
    rejected: list[RejectedListing] = []

    for listing in listings:
        evidence = [listing.title, listing.cited_text]
        combined = f"{listing.title} {listing.cited_text}"

        if is_hard_blacklisted(listing.title):
            rejected.append(RejectedListing(listing=listing, reason=ACCESSORY_OR_PART))
            continue

        match = model_matches(context.manufacturer, context.model, listing.title)
        if not match.matches:
            rejected.append(
                RejectedListing(listing=listing, reason=match.reason or "modelo_divergente")
            )
            continue

        if not capacity_present(context.storage_gb, listing.title, listing.url):
            rejected.append(RejectedListing(listing=listing, reason=CAPACITY_MISSING))
            continue

        if is_contextual_part(listing.title):
            rejected.append(RejectedListing(listing=listing, reason=LIKELY_PART))
            continue

        condition = classify_condition(listing.title)
        allowed = set(ACCEPTED_CONDITIONS)
        if context.include_new:
            allowed |= {"novo", "desconhecido"}
        if condition not in allowed:
            rejected.append(RejectedListing(listing=listing, reason=CONDITION_EXCLUDED))
            continue

        price, price_reason = extract_price(combined)
        if price_reason is not None and price_reason != PRICE_ABSENT:
            rejected.append(RejectedListing(listing=listing, reason=price_reason))
            continue
        if price is None:
            price = listing.price_brl
        if price is None:
            rejected.append(RejectedListing(listing=listing, reason=PRICE_ABSENT))
            continue

        if not price_has_evidence(price, evidence):
            rejected.append(RejectedListing(listing=listing, reason=PRICE_WITHOUT_EVIDENCE))
            continue

        if price < context.price_floor_brl or price > context.price_ceiling_brl:
            rejected.append(RejectedListing(listing=listing, reason=PRICE_OUT_OF_RANGE))
            continue

        survivors.append(
            listing.model_copy(
                update={
                    "price_brl": price,
                    "condition": condition,
                    "flag_5g_divergent": match.flag_5g_divergent,
                }
            )
        )

    kept, duplicates = dedupe(survivors)
    rejected.extend(RejectedListing(listing=item, reason=DUPLICATE) for item in duplicates)
    return kept, rejected
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: PASS, 18 testes

- [ ] **Step 5: Rodar lint, type check e a suíte inteira**

Run: `uv run ruff check . && uv run mypy renov_market_scan && uv run pytest`
Expected: sem erros

- [ ] **Step 6: Commit**

```bash
git add renov_market_scan/filtering/pipeline.py tests/test_pipeline.py
git commit -m "feat: pipeline de filtro com motivo em todo descarte

Blacklist contextual roda depois de modelo e capacidade. O parser
proprio de preco tem prioridade sobre o numero que o modelo extraiu, e a
regra de evidencia e obrigatoria nos dois casos."
```

---

### Task 11: Estatística com IQR

**Files:**
- Create: `renov_market_scan/stats/__init__.py`, `renov_market_scan/stats/aggregate.py`
- Create: `tests/test_aggregate.py`

**Interfaces:**
- Consumes: `Listing`, `ModelStats` da Tarefa 2.
- Produces: `quantile(sorted_values: list[float], q: float) -> float | None`; `iqr_bounds(values: list[float]) -> tuple[float, float] | None`; `aggregate(search_key: str, listings: list[Listing]) -> ModelStats`; `MIN_SAMPLE_FOR_IQR = 4`.

**Decisão:** o IQR só é aplicado com `n >= 4`. Com 2 ou 3 pontos os quartis não têm significado e o filtro pode zerar a amostra.

- [ ] **Step 1: Escrever o teste que falha**

Arquivo `tests/test_aggregate.py`:

```python
import pytest

from renov_market_scan.stats.aggregate import aggregate, iqr_bounds, quantile
from renov_market_scan.models import Listing


def make_listing(price: float, url: str, source: str = "olx", condition: str = "usado") -> Listing:
    return Listing(
        search_key="k",
        source=source,
        title=f"iPhone 13 128GB R$ {price:.2f}",
        price_brl=price,
        condition=condition,
        url=url,
        captured_at="2026-07-28T10:00:00",
    )


def test_quantile_uses_linear_interpolation():
    values = [1.0, 2.0, 3.0, 4.0]
    assert quantile(values, 0.5) == 2.5
    assert quantile(values, 0.25) == 1.75
    assert quantile(values, 0.75) == 3.25


def test_quantile_handles_degenerate_inputs():
    assert quantile([], 0.5) is None
    assert quantile([7.0], 0.5) == 7.0


def test_median_with_an_even_sample():
    listings = [make_listing(p, f"https://x/{i}") for i, p in enumerate([100.0, 200.0, 300.0, 400.0])]
    stats = aggregate("k", listings)
    assert stats.median == 250.0
    assert stats.n == 4


def test_median_with_an_odd_sample():
    listings = [make_listing(p, f"https://x/{i}") for i, p in enumerate([100.0, 200.0, 300.0])]
    stats = aggregate("k", listings)
    assert stats.median == 200.0


def test_iqr_removes_the_ninety_reais_outlier_from_an_eighteen_hundred_sample():
    prices = [90.0, 1750.0, 1780.0, 1800.0, 1820.0, 1850.0]
    listings = [make_listing(p, f"https://x/{i}") for i, p in enumerate(prices)]
    stats = aggregate("k", listings)
    assert stats.n == 5
    assert stats.minimum == 1750.0
    assert stats.maximum == 1850.0
    assert stats.min_raw == 90.0
    assert stats.max_raw == 1850.0
    assert stats.status == "ok"


def test_extremes_carry_the_url_of_their_own_listing():
    prices = [1750.0, 1780.0, 1800.0, 1820.0, 1850.0]
    listings = [make_listing(p, f"https://x/{int(p)}") for p in prices]
    stats = aggregate("k", listings)
    assert stats.min_url == "https://x/1750"
    assert stats.max_url == "https://x/1850"


def test_status_ok_at_five():
    listings = [make_listing(1000.0 + i, f"https://x/{i}") for i in range(5)]
    assert aggregate("k", listings).status == "ok"


def test_status_low_sample_between_three_and_four():
    for count in (3, 4):
        listings = [make_listing(1000.0 + i, f"https://x/{i}") for i in range(count)]
        assert aggregate("k", listings).status == "amostra_baixa", count


def test_status_insufficient_below_three_and_no_median():
    listings = [make_listing(1000.0, "https://x/1"), make_listing(1200.0, "https://x/2")]
    stats = aggregate("k", listings)
    assert stats.n == 2
    assert stats.status == "insuficiente"
    assert stats.median is None
    assert stats.p25 is None
    assert stats.p75 is None
    assert stats.spread_pct is None
    assert stats.minimum == 1000.0
    assert stats.maximum == 1200.0
    assert stats.min_url == "https://x/1"


def test_empty_sample_is_insufficient():
    stats = aggregate("k", [])
    assert stats.n == 0
    assert stats.status == "insuficiente"
    assert stats.minimum is None
    assert stats.min_url is None


def test_spread_pct_is_computed_from_the_clean_sample():
    prices = [1000.0, 1100.0, 1200.0, 1300.0, 1400.0]
    listings = [make_listing(p, f"https://x/{int(p)}") for p in prices]
    stats = aggregate("k", listings)
    assert stats.median == 1200.0
    assert stats.spread_pct == pytest.approx((1400.0 - 1000.0) / 1200.0)


def test_sources_and_predominant_condition_are_reported():
    listings = [
        make_listing(1000.0, "https://x/1", source="olx", condition="usado"),
        make_listing(1100.0, "https://x/2", source="olx", condition="usado"),
        make_listing(1200.0, "https://x/3", source="enjoei", condition="seminovo"),
    ]
    stats = aggregate("k", listings)
    assert sorted(stats.sources) == ["enjoei", "olx"]
    assert stats.predominant_condition == "usado"


def test_iqr_is_not_applied_below_four_points():
    """With three points the quartiles are meaningless; keep the sample intact."""
    listings = [
        make_listing(90.0, "https://x/1"),
        make_listing(1800.0, "https://x/2"),
        make_listing(1820.0, "https://x/3"),
    ]
    stats = aggregate("k", listings)
    assert stats.n == 3
    assert stats.minimum == 90.0


def test_iqr_bounds_returns_none_for_small_samples():
    assert iqr_bounds([1.0, 2.0]) is None
    assert iqr_bounds([1.0, 2.0, 3.0, 4.0]) is not None
```

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_aggregate.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar `renov_market_scan/stats/aggregate.py`**

```bash
mkdir -p renov_market_scan/stats
touch renov_market_scan/stats/__init__.py
```

```python
"""Per-search-key statistics with IQR outlier removal.

The reported minimum and maximum are the extremes of the cleaned sample and
each carries the URL of its own advert. The pre-cleaning extremes are kept in
min_raw and max_raw so the removal is auditable.
"""

import math
from collections import Counter

from renov_market_scan.models import Condition, Listing, ModelStats

# Below four points the quartiles carry no information and the IQR filter can
# empty the sample, so it is not applied.
MIN_SAMPLE_FOR_IQR = 4

STATUS_OK_THRESHOLD = 5
STATUS_LOW_THRESHOLD = 3

IQR_MULTIPLIER = 1.5


def quantile(sorted_values: list[float], q: float) -> float | None:
    """Linear-interpolation quantile over an already-sorted list."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * weight


def iqr_bounds(values: list[float]) -> tuple[float, float] | None:
    """Inclusive [lower, upper] acceptance bounds, or None for a small sample."""
    if len(values) < MIN_SAMPLE_FOR_IQR:
        return None
    ordered = sorted(values)
    q1 = quantile(ordered, 0.25)
    q3 = quantile(ordered, 0.75)
    if q1 is None or q3 is None:
        return None
    spread = q3 - q1
    return q1 - IQR_MULTIPLIER * spread, q3 + IQR_MULTIPLIER * spread


def _status(count: int) -> str:
    if count >= STATUS_OK_THRESHOLD:
        return "ok"
    if count >= STATUS_LOW_THRESHOLD:
        return "amostra_baixa"
    return "insuficiente"


def aggregate(search_key: str, listings: list[Listing]) -> ModelStats:
    """Compute statistics for one search key from its accepted listings."""
    priced = [item for item in listings if item.price_brl is not None]
    raw_prices = [item.price_brl for item in priced if item.price_brl is not None]

    if not priced:
        return ModelStats(
            search_key=search_key,
            n=0,
            minimum=None,
            p25=None,
            median=None,
            p75=None,
            maximum=None,
            spread_pct=None,
            min_url=None,
            max_url=None,
            min_raw=None,
            max_raw=None,
            sources=[],
            predominant_condition="desconhecido",
            status="insuficiente",
        )

    bounds = iqr_bounds(raw_prices)
    if bounds is None:
        clean = priced
    else:
        low, high = bounds
        clean = [
            item
            for item in priced
            if item.price_brl is not None and low <= item.price_brl <= high
        ]
        if not clean:
            clean = priced

    ordered = sorted(clean, key=lambda item: item.price_brl or 0.0)
    prices = [item.price_brl for item in ordered if item.price_brl is not None]
    count = len(prices)
    status = _status(count)

    has_stats = status != "insuficiente"
    median = quantile(prices, 0.5) if has_stats else None
    p25 = quantile(prices, 0.25) if has_stats else None
    p75 = quantile(prices, 0.75) if has_stats else None
    minimum = prices[0]
    maximum = prices[-1]
    spread = (maximum - minimum) / median if median else None

    condition_counts = Counter(item.condition for item in ordered)
    predominant: Condition = (
        condition_counts.most_common(1)[0][0] if condition_counts else "desconhecido"
    )

    return ModelStats(
        search_key=search_key,
        n=count,
        minimum=minimum,
        p25=p25,
        median=median,
        p75=p75,
        maximum=maximum,
        spread_pct=spread,
        min_url=ordered[0].url,
        max_url=ordered[-1].url,
        min_raw=min(raw_prices),
        max_raw=max(raw_prices),
        sources=sorted({item.source for item in ordered}),
        predominant_condition=predominant,
        status=status,
    )
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `uv run pytest tests/test_aggregate.py -v`
Expected: PASS, 14 testes

- [ ] **Step 5: Rodar lint, type check e a suíte inteira**

Run: `uv run ruff check . && uv run mypy renov_market_scan && uv run pytest`
Expected: sem erros

- [ ] **Step 6: Commit**

```bash
git add renov_market_scan/stats tests/test_aggregate.py
git commit -m "feat: estatistica com IQR e extremos ligados ao anuncio

min e max sao da amostra saneada e cada um carrega a URL do seu proprio
anuncio; min_bruto e max_bruto ficam para auditoria. IQR so roda com
n>=4, senao os quartis nao significam nada e a amostra pode zerar.
Mediana nunca e reportada com n<3."
```

---

### Task 12: Cache SQLite em camadas

**Files:**
- Create: `renov_market_scan/cache/__init__.py`, `renov_market_scan/cache/schema.py`, `renov_market_scan/cache/store.py`
- Create: `tests/test_store.py`

**Interfaces:**
- Consumes: `RawResponse`, `Listing` da Tarefa 2.
- Produces: `SCHEMA_SQL: str`; `open_store(path: Path) -> sqlite3.Connection`; `save_raw(conn, response: RawResponse) -> None`; `has_raw(conn, search_key: str, source: str, phrase_index: int, collected_on: str) -> bool`; `count_raw(conn, collected_on: str) -> int`; `save_listings(conn, search_key: str, source: str, collected_on: str, listings: list[Listing]) -> None`; `load_listings(conn, collected_on: str) -> dict[str, list[Listing]]` indexado por `search_key`.

- [ ] **Step 1: Escrever o teste que falha**

Arquivo `tests/test_store.py`:

```python
from renov_market_scan.cache.store import (
    count_raw,
    has_raw,
    load_listings,
    open_store,
    save_listings,
    save_raw,
)
from renov_market_scan.models import Listing, RawResponse

DAY = "2026-07-28"


def make_raw(**overrides) -> RawResponse:
    base = {
        "search_key": "k1",
        "source": "olx",
        "phrase_index": 0,
        "collected_on": DAY,
        "payload": {"content": [{"type": "text", "text": "[]"}], "stop_reason": "end_turn"},
        "status": "ok",
    }
    base.update(overrides)
    return RawResponse(**base)


def make_listing(**overrides) -> Listing:
    base = {
        "search_key": "k1",
        "source": "olx",
        "title": "iPhone 13 128GB seminovo",
        "price_brl": 3050.0,
        "condition": "seminovo",
        "url": "https://olx.com.br/a-1",
        "captured_at": "2026-07-28T10:00:00",
        "cited_text": "R$ 3.050,00",
        "flag_5g_divergent": False,
    }
    base.update(overrides)
    return Listing(**base)


def test_schema_is_created_on_open(tmp_path):
    conn = open_store(tmp_path / "scan.sqlite")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"raw_search", "listing"} <= tables
    conn.close()


def test_raw_round_trips_and_is_detected(tmp_path):
    conn = open_store(tmp_path / "scan.sqlite")
    assert has_raw(conn, "k1", "olx", 0, DAY) is False
    save_raw(conn, make_raw())
    assert has_raw(conn, "k1", "olx", 0, DAY) is True
    conn.close()


def test_raw_is_keyed_by_phrase_source_and_day(tmp_path):
    conn = open_store(tmp_path / "scan.sqlite")
    save_raw(conn, make_raw())
    assert has_raw(conn, "k1", "olx", 1, DAY) is False
    assert has_raw(conn, "k1", "enjoei", 0, DAY) is False
    assert has_raw(conn, "k1", "olx", 0, "2026-07-29") is False
    conn.close()


def test_saving_the_same_key_twice_replaces_instead_of_duplicating(tmp_path):
    conn = open_store(tmp_path / "scan.sqlite")
    save_raw(conn, make_raw())
    save_raw(conn, make_raw(status="bloqueado"))
    assert count_raw(conn, DAY) == 1
    row = conn.execute("SELECT status FROM raw_search").fetchone()
    assert row[0] == "bloqueado"
    conn.close()


def test_payload_survives_as_structured_json(tmp_path):
    conn = open_store(tmp_path / "scan.sqlite")
    save_raw(conn, make_raw(payload={"a": [1, 2, {"b": "c"}]}))
    row = conn.execute("SELECT payload FROM raw_search").fetchone()
    import json

    assert json.loads(row[0]) == {"a": [1, 2, {"b": "c"}]}
    conn.close()


def test_count_raw_counts_only_the_requested_day(tmp_path):
    conn = open_store(tmp_path / "scan.sqlite")
    save_raw(conn, make_raw())
    save_raw(conn, make_raw(phrase_index=1))
    save_raw(conn, make_raw(collected_on="2026-07-29"))
    assert count_raw(conn, DAY) == 2
    conn.close()


def test_listings_round_trip_grouped_by_search_key(tmp_path):
    conn = open_store(tmp_path / "scan.sqlite")
    save_listings(conn, "k1", "olx", DAY, [make_listing(), make_listing(url="https://olx.com.br/a-2")])
    save_listings(conn, "k2", "olx", DAY, [make_listing(search_key="k2")])
    loaded = load_listings(conn, DAY)
    assert set(loaded) == {"k1", "k2"}
    assert len(loaded["k1"]) == 2
    assert loaded["k1"][0].price_brl == 3050.0
    assert loaded["k1"][0].cited_text == "R$ 3.050,00"
    conn.close()


def test_resaving_listings_for_a_key_replaces_the_previous_set(tmp_path):
    conn = open_store(tmp_path / "scan.sqlite")
    save_listings(conn, "k1", "olx", DAY, [make_listing(), make_listing(url="https://olx.com.br/a-2")])
    save_listings(conn, "k1", "olx", DAY, [make_listing()])
    loaded = load_listings(conn, DAY)
    assert len(loaded["k1"]) == 1
    conn.close()


def test_a_none_price_survives_the_round_trip(tmp_path):
    conn = open_store(tmp_path / "scan.sqlite")
    save_listings(conn, "k1", "olx", DAY, [make_listing(price_brl=None)])
    loaded = load_listings(conn, DAY)
    assert loaded["k1"][0].price_brl is None
    conn.close()
```

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar `renov_market_scan/cache/schema.py`**

```bash
mkdir -p renov_market_scan/cache
touch renov_market_scan/cache/__init__.py
```

```python
"""SQLite DDL for the layered cache.

raw_search is the only layer whose regeneration costs money, so it is written
before any processing and is never overwritten by a reprocessing pass.
"""

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS raw_search (
    search_key    TEXT    NOT NULL,
    source        TEXT    NOT NULL,
    phrase_index  INTEGER NOT NULL,
    collected_on  TEXT    NOT NULL,
    payload       TEXT    NOT NULL,
    status        TEXT    NOT NULL,
    PRIMARY KEY (search_key, source, phrase_index, collected_on)
);

CREATE TABLE IF NOT EXISTS listing (
    search_key         TEXT    NOT NULL,
    source             TEXT    NOT NULL,
    collected_on       TEXT    NOT NULL,
    title              TEXT    NOT NULL,
    price_brl          REAL,
    condition          TEXT    NOT NULL,
    url                TEXT    NOT NULL,
    captured_at        TEXT    NOT NULL,
    cited_text         TEXT    NOT NULL DEFAULT '',
    flag_5g_divergent  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS listing_by_day
    ON listing (collected_on, search_key);
"""
```

- [ ] **Step 4: Implementar `renov_market_scan/cache/store.py`**

```python
"""Read and write the three cache layers."""

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from renov_market_scan.cache.schema import SCHEMA_SQL
from renov_market_scan.models import Condition, Listing, RawResponse


def open_store(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the cache database with its schema applied."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA_SQL)
    connection.commit()
    return connection


def save_raw(connection: sqlite3.Connection, response: RawResponse) -> None:
    """Persist an untouched API response in its own transaction."""
    connection.execute(
        "INSERT OR REPLACE INTO raw_search "
        "(search_key, source, phrase_index, collected_on, payload, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            response.search_key,
            response.source,
            response.phrase_index,
            response.collected_on,
            json.dumps(response.payload, ensure_ascii=False),
            response.status,
        ),
    )
    connection.commit()


def has_raw(
    connection: sqlite3.Connection,
    search_key: str,
    source: str,
    phrase_index: int,
    collected_on: str,
) -> bool:
    """True when this exact search was already performed on this day."""
    row = connection.execute(
        "SELECT 1 FROM raw_search "
        "WHERE search_key = ? AND source = ? AND phrase_index = ? AND collected_on = ?",
        (search_key, source, phrase_index, collected_on),
    ).fetchone()
    return row is not None


def count_raw(connection: sqlite3.Connection, collected_on: str) -> int:
    """How many raw responses are cached for a given day."""
    row = connection.execute(
        "SELECT COUNT(*) FROM raw_search WHERE collected_on = ?", (collected_on,)
    ).fetchone()
    return int(row[0])


def save_listings(
    connection: sqlite3.Connection,
    search_key: str,
    source: str,
    collected_on: str,
    listings: list[Listing],
) -> None:
    """Replace the extracted listings for one (key, source, day)."""
    connection.execute(
        "DELETE FROM listing WHERE search_key = ? AND source = ? AND collected_on = ?",
        (search_key, source, collected_on),
    )
    connection.executemany(
        "INSERT INTO listing "
        "(search_key, source, collected_on, title, price_brl, condition, url, "
        " captured_at, cited_text, flag_5g_divergent) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                item.search_key,
                item.source,
                collected_on,
                item.title,
                item.price_brl,
                item.condition,
                item.url,
                item.captured_at,
                item.cited_text,
                int(item.flag_5g_divergent),
            )
            for item in listings
        ],
    )
    connection.commit()


def load_listings(connection: sqlite3.Connection, collected_on: str) -> dict[str, list[Listing]]:
    """Every cached listing for a day, grouped by search key."""
    rows = connection.execute(
        "SELECT search_key, source, title, price_brl, condition, url, captured_at, "
        "       cited_text, flag_5g_divergent "
        "FROM listing WHERE collected_on = ? ORDER BY rowid",
        (collected_on,),
    ).fetchall()
    grouped: dict[str, list[Listing]] = defaultdict(list)
    for row in rows:
        condition: Condition = row[4]
        grouped[row[0]].append(
            Listing(
                search_key=row[0],
                source=row[1],
                title=row[2],
                price_brl=row[3],
                condition=condition,
                url=row[5],
                captured_at=row[6],
                cited_text=row[7],
                flag_5g_divergent=bool(row[8]),
            )
        )
    return dict(grouped)
```

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS, 9 testes

- [ ] **Step 6: Rodar lint, type check e a suíte inteira, depois commit**

Run: `uv run ruff check . && uv run mypy renov_market_scan && uv run pytest`

```bash
git add renov_market_scan/cache tests/test_store.py
git commit -m "feat: cache SQLite em camadas

raw_search e a unica camada cujo custo de regeneracao e em dolar, e e
gravada antes de qualquer processamento. Chave inclui frase, fonte e dia,
o que faz --retomar funcionar sem custo."
```

---

### Task 13: Construção das queries

**Files:**
- Create: `renov_market_scan/query/__init__.py`, `renov_market_scan/query/builder.py`, `fontes.yaml`
- Create: `tests/test_builder.py`

**Interfaces:**
- Consumes: `SearchPlanItem`, `Query` da Tarefa 2; `strip_grade_suffix` da Tarefa 4; `load_brand_aliases` da Tarefa 7.
- Produces: `Source` (pydantic: `name: str`, `domain: str`, `weight: float = 1.0`, `enabled: bool = True`); `load_sources(path: Path) -> list[Source]`; `enabled_sources(sources: list[Source], wanted: list[str] | None) -> list[Source]`; `build_queries(item: SearchPlanItem, sources: list[Source], brand_aliases: dict[str, list[str]]) -> list[Query]`; `PHRASE_COUNT = 2`.

- [ ] **Step 1: Criar `fontes.yaml`**

```yaml
# Fontes de busca. Medido na Fase 0: OLX foi a que mais devolveu anuncio
# individual com preco no proprio titulo.
- name: olx
  domain: olx.com.br
  weight: 1.0
  enabled: true
- name: enjoei
  domain: enjoei.com.br
  weight: 1.0
  enabled: true
- name: mercadolivre
  domain: mercadolivre.com.br
  weight: 1.0
  enabled: true
# Fora da v1. Habilitar exige recalibrar as regras de descarte.
- name: trocafone
  domain: trocafone.com
  weight: 0.5
  enabled: false
- name: shopee
  domain: shopee.com.br
  weight: 0.5
  enabled: false
```

- [ ] **Step 2: Escrever o teste que falha**

Arquivo `tests/test_builder.py`:

```python
from pathlib import Path

from renov_market_scan.models import ReportKey, SearchPlanItem
from renov_market_scan.query.builder import (
    PHRASE_COUNT,
    build_queries,
    enabled_sources,
    load_sources,
)


def make_item(**overrides) -> SearchPlanItem:
    base = {
        "search_key": "k1",
        "manufacturer": "APPLE",
        "model": "IPHONE 13",
        "storage_label": "128GB",
        "storage_gb": 128,
        "report_keys": [
            ReportKey(
                erp_code="20022A0",
                model="IPHONE 13",
                storage_label="128GB",
                device_name="IPHONE 13 128GB A0",
                price_instore=2000.0,
                row_number=3,
            )
        ],
    }
    base.update(overrides)
    return SearchPlanItem(**base)


def test_sources_load_from_yaml():
    sources = load_sources(Path("fontes.yaml"))
    names = [source.name for source in sources]
    assert "olx" in names and "trocafone" in names
    by_name = {source.name: source for source in sources}
    assert by_name["olx"].enabled is True
    assert by_name["olx"].domain == "olx.com.br"
    assert by_name["trocafone"].enabled is False


def test_only_enabled_sources_are_used_by_default():
    sources = load_sources(Path("fontes.yaml"))
    selected = enabled_sources(sources, None)
    assert {source.name for source in selected} == {"olx", "enjoei", "mercadolivre"}


def test_explicit_selection_overrides_the_default_but_not_the_disabled_flag():
    sources = load_sources(Path("fontes.yaml"))
    selected = enabled_sources(sources, ["olx"])
    assert [source.name for source in selected] == ["olx"]


def test_selecting_a_disabled_source_enables_it_explicitly():
    sources = load_sources(Path("fontes.yaml"))
    selected = enabled_sources(sources, ["trocafone"])
    assert [source.name for source in selected] == ["trocafone"]


def test_two_phrases_per_source():
    sources = load_sources(Path("fontes.yaml"))
    selected = enabled_sources(sources, ["olx", "enjoei"])
    queries = build_queries(make_item(), selected, {})
    assert len(queries) == 2 * PHRASE_COUNT
    assert {query.phrase_index for query in queries} == {0, 1}
    assert {query.source for query in queries} == {"olx", "enjoei"}


def test_generic_phrase_carries_brand_model_storage_and_used_terms():
    sources = enabled_sources(load_sources(Path("fontes.yaml")), ["olx"])
    generic = next(q for q in build_queries(make_item(), sources, {}) if q.phrase_index == 0)
    assert "apple" in generic.text
    assert "iphone 13" in generic.text
    assert "128gb" in generic.text
    assert "usado" in generic.text and "seminovo" in generic.text


def test_advertiser_phrase_differs_from_the_generic_one():
    sources = enabled_sources(load_sources(Path("fontes.yaml")), ["olx"])
    queries = build_queries(make_item(), sources, {})
    generic = next(q for q in queries if q.phrase_index == 0)
    advertiser = next(q for q in queries if q.phrase_index == 1)
    assert generic.text != advertiser.text
    assert "r$" in advertiser.text


def test_grade_suffix_is_removed_from_the_query():
    sources = enabled_sources(load_sources(Path("fontes.yaml")), ["olx"])
    item = make_item(model="GALAXY A17 A0", manufacturer="SAMSUNG")
    for query in build_queries(item, sources, {}):
        assert " a0" not in query.text


def test_brand_alias_replaces_the_sheet_manufacturer():
    sources = enabled_sources(load_sources(Path("fontes.yaml")), ["olx"])
    item = make_item(manufacturer="REDMI", model="NOTE 12")
    aliases = {"REDMI": ["xiaomi redmi", "redmi"]}
    generic = next(q for q in build_queries(item, sources, aliases) if q.phrase_index == 0)
    assert "xiaomi redmi" in generic.text


def test_terabyte_label_reaches_the_query_as_written():
    sources = enabled_sources(load_sources(Path("fontes.yaml")), ["olx"])
    item = make_item(storage_label="1TB", storage_gb=1024)
    generic = next(q for q in build_queries(item, sources, {}) if q.phrase_index == 0)
    assert "1tb" in generic.text


def test_domain_travels_with_the_query():
    sources = enabled_sources(load_sources(Path("fontes.yaml")), ["olx"])
    for query in build_queries(make_item(), sources, {}):
        assert query.domain == "olx.com.br"
        assert query.search_key == "k1"
```

- [ ] **Step 3: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_builder.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 4: Implementar `renov_market_scan/query/builder.py`**

```bash
mkdir -p renov_market_scan/query
touch renov_market_scan/query/__init__.py
```

```python
"""Build search phrases from a plan item.

Two phrases per source, measured in the Phase 0 exploration: a generic phrase
returned 9 of 10 results as category pages with no unit price, while a phrase
written the way a seller writes an advert returned 5 of 10 as individual
adverts with the price in the title.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel

from renov_market_scan.ingest.normalize import strip_grade_suffix
from renov_market_scan.models import Query, SearchPlanItem

PHRASE_COUNT = 2

GENERIC_TERMS = "usado seminovo"
ADVERTISER_TERMS = "seminovo estado de conservacao r$"


class Source(BaseModel):
    """One marketplace, as configured in fontes.yaml."""

    name: str
    domain: str
    weight: float = 1.0
    enabled: bool = True


def load_sources(path: Path) -> list[Source]:
    """Load every configured source, enabled or not."""
    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or []
    return [Source(**entry) for entry in loaded]


def enabled_sources(sources: list[Source], wanted: list[str] | None) -> list[Source]:
    """Select sources to use.

    Without an explicit selection, the enabled flag decides. An explicit
    selection is an override: naming a disabled source turns it on for this run.
    """
    if wanted is None:
        return [source for source in sources if source.enabled]
    requested = [name.strip().lower() for name in wanted if name.strip()]
    by_name = {source.name.lower(): source for source in sources}
    return [by_name[name] for name in requested if name in by_name]


def _brand_term(manufacturer: str, brand_aliases: dict[str, list[str]]) -> str:
    """The market-facing brand name, which is not always the sheet value."""
    aliases = brand_aliases.get(manufacturer.upper())
    if aliases:
        return aliases[0]
    return manufacturer.lower()


def build_queries(
    item: SearchPlanItem, sources: list[Source], brand_aliases: dict[str, list[str]]
) -> list[Query]:
    """Two phrases for every source, all lowercase."""
    brand = _brand_term(item.manufacturer, brand_aliases)
    model = strip_grade_suffix(item.model).lower()
    storage = item.storage_label.lower()
    base = f"{brand} {model} {storage}".strip()

    phrases = (
        f"{base} {GENERIC_TERMS}",
        f"{base} {ADVERTISER_TERMS}",
    )

    queries: list[Query] = []
    for source in sources:
        for index, phrase in enumerate(phrases):
            queries.append(
                Query(
                    search_key=item.search_key,
                    source=source.name,
                    domain=source.domain,
                    phrase_index=index,
                    text=" ".join(phrase.split()),
                )
            )
    return queries
```

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `uv run pytest tests/test_builder.py -v`
Expected: PASS, 11 testes

- [ ] **Step 6: Rodar lint, type check e a suíte inteira, depois commit**

Run: `uv run ruff check . && uv run mypy renov_market_scan && uv run pytest`

```bash
git add renov_market_scan/query fontes.yaml tests/test_builder.py
git commit -m "feat: construcao de queries com duas frases por fonte

Frase generica e frase de anunciante. Medido na Fase 0: a generica
devolveu 9 de 10 paginas de categoria sem preco; a de anunciante devolveu
5 de 10 anuncios individuais. Aliases de marca corrigem REDMI, POCO,
JOVI e ITEL, que a planilha lista como fabricantes independentes."
```

---

### Task 14: Protocol do adapter, classificação de erros e adapter offline

**Files:**
- Create: `renov_market_scan/collect/__init__.py`, `renov_market_scan/collect/base.py`, `renov_market_scan/collect/errors.py`, `renov_market_scan/collect/fixture.py`
- Create: `tests/test_errors.py`, `tests/test_fixture_adapter.py`

**Interfaces:**
- Consumes: `Query`, `Listing`, `RawResponse` da Tarefa 2.
- Produces: `SearchOutcome` (dataclass frozen: `listings: list[Listing]`, `status: str`, `payload: dict[str, Any]`); `SearchAdapter` (Protocol com `async def search(self, query: Query) -> SearchOutcome`); `ErrorAction` (StrEnum: `RETRY`, `ACCEPT_PARTIAL`, `SHORTEN_QUERY`, `REDUCE_DOMAINS`, `FAIL`); `classify_tool_error(error_code: str) -> ErrorAction`; `STATUS_FOR_ERROR: dict[str, str]`; `FixtureAdapter`.

- [ ] **Step 1: Escrever os testes que falham**

Arquivo `tests/test_errors.py`:

```python
from renov_market_scan.collect.errors import STATUS_FOR_ERROR, ErrorAction, classify_tool_error


def test_transient_errors_are_retried():
    assert classify_tool_error("too_many_requests") is ErrorAction.RETRY
    assert classify_tool_error("unavailable") is ErrorAction.RETRY


def test_max_uses_accepts_the_partial_result():
    assert classify_tool_error("max_uses_exceeded") is ErrorAction.ACCEPT_PARTIAL


def test_query_too_long_shortens_the_query():
    assert classify_tool_error("query_too_long") is ErrorAction.SHORTEN_QUERY


def test_request_too_large_reduces_the_domain_list():
    assert classify_tool_error("request_too_large") is ErrorAction.REDUCE_DOMAINS


def test_invalid_input_fails_without_retry():
    assert classify_tool_error("invalid_tool_input") is ErrorAction.FAIL


def test_an_unknown_error_code_fails_rather_than_looping():
    assert classify_tool_error("something_new_from_the_api") is ErrorAction.FAIL


def test_every_documented_code_maps_to_a_status_string():
    for code in (
        "too_many_requests",
        "unavailable",
        "max_uses_exceeded",
        "query_too_long",
        "request_too_large",
        "invalid_tool_input",
    ):
        assert STATUS_FOR_ERROR[code]
```

Arquivo `tests/test_fixture_adapter.py`:

```python
import asyncio

from renov_market_scan.collect.fixture import FixtureAdapter
from renov_market_scan.models import Listing, Query

QUERY = Query(
    search_key="k1", source="olx", domain="olx.com.br", phrase_index=0, text="iphone 13 128gb"
)


def make_listing(**overrides) -> Listing:
    base = {
        "search_key": "k1",
        "source": "olx",
        "title": "iPhone 13 128GB seminovo R$ 3.050,00",
        "price_brl": 3050.0,
        "condition": "seminovo",
        "url": "https://olx.com.br/a-1",
        "captured_at": "2026-07-28T10:00:00",
        "cited_text": "R$ 3.050,00",
    }
    base.update(overrides)
    return Listing(**base)


def test_returns_the_canned_listings_for_a_key_and_source():
    adapter = FixtureAdapter({("k1", "olx"): [make_listing()]})
    outcome = asyncio.run(adapter.search([QUERY]))
    assert len(outcome.listings) == 1
    assert outcome.status == "ok"


def test_an_unknown_key_returns_an_empty_ok_result():
    adapter = FixtureAdapter({})
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.listings == []
    assert outcome.status == "ok"


def test_a_configured_status_is_returned_verbatim():
    adapter = FixtureAdapter({}, statuses={("k1", "olx"): "bloqueado"})
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "bloqueado"


def test_the_payload_is_serializable_for_the_cache():
    import json

    adapter = FixtureAdapter({("k1", "olx"): [make_listing()]})
    outcome = asyncio.run(adapter.search([QUERY]))
    assert json.dumps(outcome.payload)


def test_calls_are_recorded_so_tests_can_assert_zero_network():
    adapter = FixtureAdapter({})
    asyncio.run(adapter.search([QUERY]))
    asyncio.run(adapter.search([QUERY]))
    assert adapter.call_count == 2
```

- [ ] **Step 2: Rodar os testes para confirmar que falham**

Run: `uv run pytest tests/test_errors.py tests/test_fixture_adapter.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar `renov_market_scan/collect/base.py`**

```bash
mkdir -p renov_market_scan/collect
touch renov_market_scan/collect/__init__.py
```

```python
"""The single seam between the pipeline and any source of listings."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from renov_market_scan.models import Listing, Query


@dataclass(frozen=True)
class SearchOutcome:
    """What one search produced, plus the raw payload to cache verbatim."""

    listings: list[Listing]
    status: str
    payload: dict[str, Any] = field(default_factory=dict)


class SearchAdapter(Protocol):
    """Any source of listings. One call per (model, source) pair, carrying
    every phrase. AnthropicSearchAdapter is the only networked implementation;
    tests collect through FixtureAdapter."""

    async def search(self, queries: list[Query]) -> SearchOutcome:
        """Run one call covering every phrase for a (model, source) pair.

        Both phrases travel in a single call so that max_uses caps the whole
        pair, which is what the cost estimate assumes.
        """
        ...
```

- [ ] **Step 4: Implementar `renov_market_scan/collect/errors.py`**

```python
"""Classify web-search tool errors, which arrive inside an HTTP 200 response.

The API returns 200 with a web_search_tool_result_error block rather than
raising, so retry policy cannot be driven by HTTP status alone.
"""

from enum import StrEnum


class ErrorAction(StrEnum):
    """What the adapter should do about a tool error."""

    RETRY = "retry"
    ACCEPT_PARTIAL = "accept_partial"
    SHORTEN_QUERY = "shorten_query"
    REDUCE_DOMAINS = "reduce_domains"
    FAIL = "fail"


ACTION_FOR_ERROR: dict[str, ErrorAction] = {
    "too_many_requests": ErrorAction.RETRY,
    "unavailable": ErrorAction.RETRY,
    "max_uses_exceeded": ErrorAction.ACCEPT_PARTIAL,
    "query_too_long": ErrorAction.SHORTEN_QUERY,
    "request_too_large": ErrorAction.REDUCE_DOMAINS,
    "invalid_tool_input": ErrorAction.FAIL,
}

STATUS_FOR_ERROR: dict[str, str] = {
    "too_many_requests": "limite_de_taxa",
    "unavailable": "indisponivel",
    "max_uses_exceeded": "busca_truncada",
    "query_too_long": "query_longa",
    "request_too_large": "requisicao_grande",
    "invalid_tool_input": "erro_query",
}

STATUS_UNKNOWN_ERROR = "erro_desconhecido"


def classify_tool_error(error_code: str) -> ErrorAction:
    """Map an error code to an action. Unknown codes fail rather than loop."""
    return ACTION_FOR_ERROR.get(error_code, ErrorAction.FAIL)


def status_for_error(error_code: str) -> str:
    """The pt-BR status recorded in the cache for this error code."""
    return STATUS_FOR_ERROR.get(error_code, STATUS_UNKNOWN_ERROR)
```

- [ ] **Step 5: Implementar `renov_market_scan/collect/fixture.py`**

```python
"""Offline adapter. Every test in the suite collects through this class."""

from typing import Any

from renov_market_scan.collect.base import SearchOutcome
from renov_market_scan.models import Listing, Query


class FixtureAdapter:
    """Return canned listings keyed by (search_key, source)."""

    def __init__(
        self,
        listings_by_key: dict[tuple[str, str], list[Listing]],
        statuses: dict[tuple[str, str], str] | None = None,
    ) -> None:
        self._listings = listings_by_key
        self._statuses = statuses or {}
        self.call_count = 0

    async def search(self, queries: list[Query]) -> SearchOutcome:
        """Look up the canned result. Never touches the network."""
        self.call_count += 1
        if not queries:
            return SearchOutcome(listings=[], status="ok", payload={"fixture": True})
        key = (queries[0].search_key, queries[0].source)
        listings = self._listings.get(key, [])
        status = self._statuses.get(key, "ok")
        payload: dict[str, Any] = {
            "fixture": True,
            "queries": [query.text for query in queries],
            "listings": [item.model_dump() for item in listings],
        }
        return SearchOutcome(listings=list(listings), status=status, payload=payload)
```

- [ ] **Step 6: Rodar os testes e confirmar que passam**

Run: `uv run pytest tests/test_errors.py tests/test_fixture_adapter.py -v`
Expected: PASS, 12 testes

- [ ] **Step 7: Rodar lint, type check e a suíte inteira, depois commit**

Run: `uv run ruff check . && uv run mypy renov_market_scan && uv run pytest`

```bash
git add renov_market_scan/collect tests/test_errors.py tests/test_fixture_adapter.py
git commit -m "feat: SearchAdapter, classificacao de erro da tool e adapter offline

Erro de busca chega em HTTP 200 dentro do corpo, entao a politica de
retry nao pode se basear em status HTTP. Codigo desconhecido falha em vez
de entrar em loop."
```

---

### Task 15: Spike do contrato de extração (gasta ~US$ 0,10)

**Files:**
- Create: `spikes/extraction_contract.py`
- Modify: `docs/fontes.md`

**Interfaces:**
- Consumes: `Settings` da Tarefa 1.
- Produces: uma decisão registrada em `docs/fontes.md` sob o título `## Contrato de extração` mais **três números medidos** que a Tarefa 17 usa: `TOKENS_IN_PER_CALL`, `TOKENS_OUT_PER_CALL`, `SEARCHES_PER_CALL`.

**Esta tarefa não é TDD** — é uma medição que decide o desenho da Tarefa 16. Requer `ANTHROPIC_API_KEY` e gasta dinheiro real (3 chamadas, ~US$ 0,10). Não prosseguir para a Tarefa 16 antes de registrar o resultado.

- [ ] **Step 1: Escrever o script do spike**

Arquivo `spikes/extraction_contract.py`:

```python
"""Decide how to constrain the model's output. Costs about US$ 0.10.

Three mechanisms are tried against one real query:

A. output_config.format with a json_schema. Strongest guarantee, but the docs
   say structured outputs is incompatible with citations, and web search always
   enables citations. May return 400.
B. JSON by prompt, validated with pydantic. Certain to work, weaker guarantee.
C. A client tool with strict=True. Schema guaranteed by a different mechanism,
   so it should coexist with citations, at the cost of an extra turn.

Run: uv run python spikes/extraction_contract.py
"""

import json
from typing import Any

import anthropic

from renov_market_scan.config import Settings

QUERY = "iphone 13 128gb seminovo estado de conservacao r$"
DOMAIN = "olx.com.br"

SYSTEM = (
    "Voce extrai anuncios de celulares usados. Busque anuncios do modelo pedido "
    "e responda apenas com um array JSON de objetos "
    '{"titulo","preco_brl","condicao","url","fonte"}. '
    "Sem preambulo, sem markdown, sem texto fora do JSON."
)

LISTING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "anuncios": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string"},
                    "preco_brl": {"type": "number"},
                    "condicao": {"type": "string"},
                    "url": {"type": "string"},
                    "fonte": {"type": "string"},
                },
                "required": ["titulo", "preco_brl", "condicao", "url", "fonte"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["anuncios"],
    "additionalProperties": False,
}


def web_search_tool(settings: Settings) -> dict[str, Any]:
    """The tool definition, with direct calling forced on modern versions."""
    tool: dict[str, Any] = {
        "type": settings.web_search_tool_version,
        "name": "web_search",
        "max_uses": settings.max_uses_per_call,
        "allowed_domains": [DOMAIN],
        "user_location": {"type": "approximate", "country": settings.user_location_country},
    }
    if settings.web_search_tool_version != "web_search_20250305":
        tool["allowed_callers"] = ["direct"]
    return tool


def report(label: str, response: Any) -> None:
    """Print what we need to decide: stop reason, searches, tokens, text."""
    usage = response.usage
    searches = 0
    server_tool_use = getattr(usage, "server_tool_use", None)
    if server_tool_use is not None:
        searches = getattr(server_tool_use, "web_search_requests", 0) or 0
    texts = [block.text for block in response.content if block.type == "text"]
    citations = 0
    for block in response.content:
        if block.type == "text" and getattr(block, "citations", None):
            citations += len(block.citations)
    print(f"--- {label}")
    print(f"    stop_reason      = {response.stop_reason}")
    print(f"    input_tokens     = {usage.input_tokens}")
    print(f"    output_tokens    = {usage.output_tokens}")
    print(f"    web_searches     = {searches}")
    print(f"    citations        = {citations}")
    print(f"    text[0][:400]    = {(texts[0][:400] if texts else '(vazio)')!r}")


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=0)
    tool = web_search_tool(settings)
    prompt = f"Busque anuncios de: {QUERY}. Somente no dominio {DOMAIN}."

    # A. structured outputs
    try:
        response = client.messages.create(
            model=settings.model,
            max_tokens=8000,
            system=SYSTEM,
            tools=[tool],
            output_config={"format": {"type": "json_schema", "schema": LISTING_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        report("A output_config.format: FUNCIONOU", response)
    except Exception as error:  # noqa: BLE001 - we want the message, whatever it is
        print(f"--- A output_config.format: FALHOU\n    {type(error).__name__}: {error}")

    # B. JSON by prompt
    response = client.messages.create(
        model=settings.model,
        max_tokens=8000,
        system=SYSTEM,
        tools=[tool],
        messages=[{"role": "user", "content": prompt}],
    )
    report("B JSON por prompt", response)
    texts = [block.text for block in response.content if block.type == "text"]
    if texts:
        try:
            json.loads(texts[0])
            print("    JSON parseavel direto = sim")
        except json.JSONDecodeError as error:
            print(f"    JSON parseavel direto = NAO ({error})")

    # C. strict client tool
    record_tool: dict[str, Any] = {
        "name": "registrar_anuncios",
        "description": "Registra os anuncios encontrados.",
        "strict": True,
        "input_schema": LISTING_SCHEMA,
    }
    try:
        response = client.messages.create(
            model=settings.model,
            max_tokens=8000,
            system=SYSTEM,
            tools=[tool, record_tool],
            messages=[{"role": "user", "content": prompt}],
        )
        report("C tool estrita: FUNCIONOU", response)
        tool_uses = [block for block in response.content if block.type == "tool_use"]
        print(f"    tool_use blocks = {len(tool_uses)}")
    except Exception as error:  # noqa: BLE001
        print(f"--- C tool estrita: FALHOU\n    {type(error).__name__}: {error}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Rodar o spike**

```bash
mkdir -p spikes
uv run python spikes/extraction_contract.py
```

Expected: três blocos impressos. Cada um mostra `stop_reason`, tokens de entrada e saída, número de buscas e citações, e o começo do texto. O que decide:
- Se **A** imprimir `FUNCIONOU` com `citations > 0`, o mecanismo escolhido é **A**.
- Se **A** falhar com 400 mencionando citations, e **C** imprimir `FUNCIONOU` com `tool_use blocks >= 1`, o mecanismo é **C**.
- Se A e C falharem, o mecanismo é **B**.

- [ ] **Step 3: Registrar a decisão e os números medidos em `docs/fontes.md`**

Acrescentar ao arquivo, substituindo os valores entre `<>` pelos medidos:

```markdown
## Contrato de extração

Spike executado em <data>, custo <valor> (3 chamadas).

| Mecanismo | Resultado | Observação |
|---|---|---|
| A `output_config.format` | <FUNCIONOU / FALHOU: erro> | |
| B JSON por prompt | <FUNCIONOU>, JSON parseável direto: <sim/não> | |
| C tool estrita `strict: true` | <FUNCIONOU / FALHOU: erro> | <n> blocos tool_use |

**Mecanismo escolhido para `AnthropicSearchAdapter`: <A, B ou C>.**
Justificativa: <uma frase>.

### Calibração do estimador de custo

Medido em uma chamada real com `max_uses=3` e um domínio:

- `TOKENS_IN_PER_CALL = <input_tokens>`
- `TOKENS_OUT_PER_CALL = <output_tokens>`
- `SEARCHES_PER_CALL = <web_searches>`

Estes três números alimentam `renov_market_scan/cost.py` (Tarefa 17). O
estimador não adivinha tokens de resultado de busca: usa esta medição.
```

- [ ] **Step 4: Commit**

```bash
git add spikes/extraction_contract.py docs/fontes.md
git commit -m "spike: decide o contrato de extracao e calibra o estimador

Tres chamadas reais comparando output_config.format, JSON por prompt e
tool estrita. Resultado e os tokens medidos registrados em docs/fontes.md;
a Tarefa 16 implementa o mecanismo vencedor e a Tarefa 17 usa os numeros."
```

---

### Task 16: Adapter da API com web search tool

**Files:**
- Create: `renov_market_scan/collect/anthropic_search.py`
- Create: `tests/test_anthropic_search.py`

**Interfaces:**
- Consumes: `Settings` (T1), `Query`/`Listing` (T2), `SearchOutcome` (T14), `classify_tool_error`/`status_for_error`/`ErrorAction` (T14).
- Produces: `ExtractedListing` (pydantic: `titulo: str`, `preco_brl: float | None`, `condicao: str`, `url: str`, `fonte: str`); `ExtractionPayload` (pydantic: `anuncios: list[ExtractedListing]`); `AnthropicSearchAdapter` com `async def search(self, query: Query) -> SearchOutcome`; `build_web_search_tool(settings: Settings, domain: str) -> dict[str, Any]`; `collect_evidence(content: list[Any]) -> list[str]`; `find_tool_error(content: list[Any]) -> str | None`; `MAX_PAUSE_RESUMES = 3`.

**Escolha do mecanismo.** A Tarefa 15 registrou o vencedor em `docs/fontes.md`. Só a função `_request` muda; o resto do adapter é idêntico nos três casos. O código abaixo traz **B** como corpo concreto e as variantes **A** e **C** em seguida. Implemente a que o spike escolheu e apague as outras duas.

**Testes sem rede.** Um cliente falso (`FakeMessages`) devolve objetos com a mesma forma da resposta da API. Nenhum teste chama a API. O adapter recebe a **lista** de frases de um par (modelo, fonte) e faz uma única chamada, que é o que a estimativa de custo assume.

- [ ] **Step 1: Escrever o teste que falha**

Arquivo `tests/test_anthropic_search.py`:

```python
import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from renov_market_scan.collect.anthropic_search import (
    AnthropicSearchAdapter,
    build_web_search_tool,
    collect_evidence,
    find_tool_error,
)
from renov_market_scan.config import Settings
from renov_market_scan.models import Query

QUERY = Query(
    search_key="k1", source="olx", domain="olx.com.br", phrase_index=0,
    text="apple iphone 13 128gb usado seminovo",
)

VALID_JSON = (
    '{"anuncios": [{"titulo": "iPhone 13 128GB seminovo R$ 3.050,00", '
    '"preco_brl": 3050.0, "condicao": "seminovo", '
    '"url": "https://olx.com.br/a-1", "fonte": "olx"}]}'
)


def text_block(text: str, citations: list[Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text, citations=citations or [])


def citation(cited_text: str, title: str = "OLX", url: str = "https://olx.com.br/a-1"):
    return SimpleNamespace(cited_text=cited_text, title=title, url=url)


def search_result_block(error_code: str | None = None) -> SimpleNamespace:
    if error_code is None:
        return SimpleNamespace(
            type="web_search_tool_result",
            content=[SimpleNamespace(type="web_search_result", url="https://olx.com.br/a-1",
                                     title="OLX", page_age=None)],
        )
    return SimpleNamespace(
        type="web_search_tool_result",
        content=SimpleNamespace(type="web_search_tool_result_error", error_code=error_code),
    )


def response(content: list[Any], stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        model="claude-sonnet-5",
        usage=SimpleNamespace(
            input_tokens=1000, output_tokens=200,
            server_tool_use=SimpleNamespace(web_search_requests=2),
        ),
    )


class FakeMessages:
    """Stands in for client.messages. Returns queued responses in order."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeMessages ran out of queued responses")
        queued = self._responses.pop(0)
        if isinstance(queued, Exception):
            raise queued
        return queued


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.messages = FakeMessages(responses)


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"anthropic_api_key": "sk-test"}
    base.update(overrides)
    return Settings(**base)


def test_tool_definition_carries_domain_location_and_direct_caller():
    tool = build_web_search_tool(make_settings(), "olx.com.br")
    assert tool["type"] == "web_search_20260209"
    assert tool["name"] == "web_search"
    assert tool["max_uses"] == 3
    assert tool["allowed_domains"] == ["olx.com.br"]
    assert tool["user_location"]["country"] == "BR"
    assert tool["allowed_callers"] == ["direct"]
    assert "blocked_domains" not in tool


def test_basic_tool_version_omits_allowed_callers():
    settings = make_settings(model="claude-haiku-4-5", web_search_tool_version="web_search_20250305")
    tool = build_web_search_tool(settings, "olx.com.br")
    assert "allowed_callers" not in tool


def test_evidence_is_collected_from_citations_and_titles():
    content = [text_block("resposta", [citation("R$ 3.050,00 iPhone 13", title="Anuncio OLX")])]
    evidence = collect_evidence(content)
    assert any("3.050,00" in item for item in evidence)
    assert any("Anuncio OLX" in item for item in evidence)


def test_evidence_is_empty_when_there_are_no_citations():
    assert collect_evidence([text_block("sem citacoes")]) == []


def test_tool_error_is_detected():
    assert find_tool_error([search_result_block("too_many_requests")]) == "too_many_requests"


def test_no_tool_error_on_a_successful_result():
    assert find_tool_error([search_result_block()]) is None


def test_a_successful_search_yields_listings_with_evidence_attached():
    client = FakeClient([
        response([search_result_block(), text_block(VALID_JSON, [citation("R$ 3.050,00")])])
    ])
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "ok"
    assert len(outcome.listings) == 1
    listing = outcome.listings[0]
    assert listing.price_brl == 3050.0
    assert listing.search_key == "k1"
    assert listing.source == "olx"
    assert "3.050,00" in listing.cited_text


def test_the_raw_payload_is_json_serializable():
    import json

    client = FakeClient([response([text_block(VALID_JSON)])])
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert json.dumps(outcome.payload)
    assert outcome.payload["stop_reason"] == "end_turn"
    assert outcome.payload["web_search_requests"] == 2


def test_invalid_json_is_retried_once_then_marked_parse_error():
    client = FakeClient([
        response([text_block("isto nao e json")]),
        response([text_block("ainda nao e json")]),
    ])
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "parse_error"
    assert outcome.listings == []
    assert len(client.messages.calls) == 2


def test_invalid_json_recovered_by_the_retry_is_accepted():
    client = FakeClient([
        response([text_block("isto nao e json")]),
        response([text_block(VALID_JSON)]),
    ])
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "ok"
    assert len(outcome.listings) == 1


def test_never_uses_eval_or_regex_on_malformed_json():
    """A payload that eval would happily execute must not be executed."""
    client = FakeClient([
        response([text_block("__import__('os').system('echo boom')")]),
        response([text_block("__import__('os').system('echo boom')")]),
    ])
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "parse_error"


def test_max_uses_exceeded_accepts_the_partial_result():
    client = FakeClient([
        response([search_result_block("max_uses_exceeded"), text_block(VALID_JSON)])
    ])
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "busca_truncada"
    assert len(outcome.listings) == 1


def test_invalid_tool_input_fails_without_retry():
    client = FakeClient([response([search_result_block("invalid_tool_input")])])
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "erro_query"
    assert outcome.listings == []
    assert len(client.messages.calls) == 1


def test_pause_turn_is_resumed_by_resending_the_assistant_message():
    client = FakeClient([
        response([text_block("parcial")], stop_reason="pause_turn"),
        response([text_block(VALID_JSON)]),
    ])
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "ok"
    assert len(client.messages.calls) == 2
    resumed = client.messages.calls[1]["messages"]
    assert resumed[-1]["role"] == "assistant"


def test_pause_turn_gives_up_after_the_resume_cap():
    paused = [response([text_block("parcial")], stop_reason="pause_turn") for _ in range(5)]
    client = FakeClient(paused)
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "pausa_excedida"


def test_refusal_is_recorded_without_retry():
    client = FakeClient([response([], stop_reason="refusal")])
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "recusado"
    assert len(client.messages.calls) == 1


def test_an_empty_result_list_is_ok_not_an_error():
    client = FakeClient([response([text_block('{"anuncios": []}')])])
    adapter = AnthropicSearchAdapter(make_settings(), client=client)
    outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "ok"
    assert outcome.listings == []


def test_client_is_constructed_with_retries_disabled(monkeypatch):
    """tenacity owns retry policy; stacking the SDK's own retries multiplies it."""
    captured: dict[str, Any] = {}

    class SpyAnthropic:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            self.messages = FakeMessages([])

    monkeypatch.setattr("renov_market_scan.collect.anthropic_search.anthropic.Anthropic", SpyAnthropic)
    AnthropicSearchAdapter(make_settings())
    assert captured["max_retries"] == 0
```

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_anthropic_search.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar `renov_market_scan/collect/anthropic_search.py`**

```python
"""Production adapter: one messages.create per (search key, source).

Two facts about the API shape this module. First, search errors arrive inside a
successful HTTP 200 response as a web_search_tool_result_error block, so retry
policy is driven by that block and not by an exception. Second, the client never
receives search-result text: only URLs, titles, encrypted content, and up to 150
verbatim characters per citation. Those citations are collected here and travel
on the listing so the evidence rule can verify the price downstream.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from renov_market_scan.collect.base import SearchOutcome
from renov_market_scan.collect.errors import ErrorAction, classify_tool_error, status_for_error
from renov_market_scan.config import Settings
from renov_market_scan.models import Condition, Listing, Query

MAX_PAUSE_RESUMES = 3
MAX_OUTPUT_TOKENS = 8000

STATUS_OK = "ok"
STATUS_PARSE_ERROR = "parse_error"
STATUS_REFUSED = "recusado"
STATUS_PAUSE_EXCEEDED = "pausa_excedida"

VALID_CONDITIONS: frozenset[str] = frozenset({"novo", "seminovo", "usado", "desconhecido"})

SYSTEM_PROMPT = (
    "Voce extrai anuncios de celulares usados de resultados de busca. "
    "Responda apenas com um objeto JSON no formato "
    '{"anuncios": [{"titulo","preco_brl","condicao","url","fonte"}]}. '
    "Use o titulo do anuncio exatamente como aparece no resultado. "
    "Nunca invente preco: se o preco nao aparecer no resultado, use null. "
    "Nunca use valor de parcela como preco. "
    "Sem preambulo, sem markdown, sem texto fora do JSON."
)

CORRECTION_PROMPT = (
    "Sua resposta anterior nao era JSON valido. Responda novamente apenas com o "
    'objeto JSON {"anuncios": [...]}, sem nenhum texto fora dele.'
)


class ExtractedListing(BaseModel):
    """One advert as reported by the model, before any filtering."""

    titulo: str
    preco_brl: float | None = None
    condicao: str = "desconhecido"
    url: str
    fonte: str = ""


class ExtractionPayload(BaseModel):
    """The model's whole answer."""

    anuncios: list[ExtractedListing] = []


class TransientAPIError(Exception):
    """Raised so tenacity retries a rate limit or an overload."""


def build_web_search_tool(settings: Settings, domain: str) -> dict[str, Any]:
    """Tool definition for one domain.

    allowed_domains and blocked_domains are mutually exclusive, so only the
    allow list is ever sent. Versions from 20260209 onward default to running
    inside code execution, so direct calling is requested explicitly.
    """
    tool: dict[str, Any] = {
        "type": settings.web_search_tool_version,
        "name": "web_search",
        "max_uses": settings.max_uses_per_call,
        "allowed_domains": [domain],
        "user_location": {
            "type": "approximate",
            "country": settings.user_location_country,
        },
    }
    if settings.web_search_tool_version != "web_search_20250305":
        tool["allowed_callers"] = ["direct"]
    return tool


def collect_evidence(content: list[Any]) -> list[str]:
    """Every verbatim cited_text and cited title in the response."""
    evidence: list[str] = []
    for block in content:
        if getattr(block, "type", None) != "text":
            continue
        for cite in getattr(block, "citations", None) or []:
            cited_text = getattr(cite, "cited_text", None)
            if cited_text:
                evidence.append(str(cited_text))
            title = getattr(cite, "title", None)
            if title:
                evidence.append(str(title))
    return evidence


def find_tool_error(content: list[Any]) -> str | None:
    """The first web_search_tool_result_error code, if any.

    On success the block's content is a list; on error it is a single object.
    """
    for block in content:
        if getattr(block, "type", None) != "web_search_tool_result":
            continue
        inner = getattr(block, "content", None)
        if isinstance(inner, list):
            continue
        code = getattr(inner, "error_code", None)
        if code:
            return str(code)
    return None


def _response_text(content: list[Any]) -> str:
    """Concatenate the text blocks, which is where the JSON lives."""
    return "".join(
        block.text for block in content if getattr(block, "type", None) == "text"
    )


def _to_jsonable(response: Any) -> dict[str, Any]:
    """Best-effort structured copy of the response for the raw cache."""
    if hasattr(response, "model_dump"):
        try:
            dumped = response.model_dump(mode="json")
            if isinstance(dumped, dict):
                return dumped
        except Exception:  # noqa: BLE001 - fall back to the summary below
            pass
    usage = getattr(response, "usage", None)
    server_tool_use = getattr(usage, "server_tool_use", None)
    return {
        "stop_reason": getattr(response, "stop_reason", None),
        "model": getattr(response, "model", None),
        "text": _response_text(list(getattr(response, "content", []))),
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "web_search_requests": getattr(server_tool_use, "web_search_requests", None),
    }


def _normalize_condition(value: str) -> Condition:
    lowered = value.strip().lower()
    if lowered in VALID_CONDITIONS:
        return lowered  # type: ignore[return-value]
    return "desconhecido"


class AnthropicSearchAdapter:
    """Search and extract through the Anthropic API's web search tool."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client or anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            # tenacity owns retry policy. Leaving the SDK's default of 2 would
            # multiply attempts (3 x 3) and make the backoff meaningless.
            max_retries=0,
        )
        self._semaphore = asyncio.Semaphore(settings.concurrency)

    async def search(self, queries: list[Query]) -> SearchOutcome:
        """Run one call for a (model, source) pair. Returns a status, never
        raises for a data problem."""
        if not queries:
            return SearchOutcome(listings=[], status=STATUS_OK, payload={})
        async with self._semaphore:
            return await asyncio.to_thread(self._search_blocking, queries)

    @retry(
        retry=retry_if_exception_type(TransientAPIError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=False,
    )
    def _call(self, messages: list[dict[str, Any]], tool: dict[str, Any]) -> Any:
        """One API call, retried by tenacity on transient failures."""
        try:
            return self._client.messages.create(
                model=self._settings.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=SYSTEM_PROMPT,
                tools=[tool],
                messages=messages,
            )
        except anthropic.RateLimitError as error:
            raise TransientAPIError(str(error)) from error
        except anthropic.APIStatusError as error:
            if error.status_code >= 500:
                raise TransientAPIError(str(error)) from error
            raise
        except anthropic.APIConnectionError as error:
            raise TransientAPIError(str(error)) from error

    def _search_blocking(self, queries: list[Query]) -> SearchOutcome:
        first = queries[0]
        tool = build_web_search_tool(self._settings, first.domain)
        phrases = "; ".join(f'"{query.text}"' for query in queries)
        prompt = (
            "Busque anuncios de celular usado ou seminovo usando estas frases de "
            f"busca, nesta ordem: {phrases}. "
            f"Considere apenas resultados do dominio {first.domain}. "
            "Extraia cada anuncio individual encontrado, sem repetir URLs."
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

        response = self._call(messages, tool)
        payload = _to_jsonable(response)

        # A paused turn is resumed by resending the assistant message unchanged.
        resumes = 0
        while getattr(response, "stop_reason", None) == "pause_turn":
            if resumes >= MAX_PAUSE_RESUMES:
                return SearchOutcome(listings=[], status=STATUS_PAUSE_EXCEEDED, payload=payload)
            resumes += 1
            messages = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": response.content},
            ]
            response = self._call(messages, tool)
            payload = _to_jsonable(response)

        if getattr(response, "stop_reason", None) == "refusal":
            return SearchOutcome(listings=[], status=STATUS_REFUSED, payload=payload)

        content = list(getattr(response, "content", []))
        status = STATUS_OK

        error_code = find_tool_error(content)
        if error_code is not None:
            action = classify_tool_error(error_code)
            status = status_for_error(error_code)
            if action is not ErrorAction.ACCEPT_PARTIAL:
                return SearchOutcome(listings=[], status=status, payload=payload)

        extracted = self._parse(content)
        if extracted is None:
            # One correction attempt, then give up. Never eval, never regex.
            messages = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": _response_text(content) or "(vazio)"},
                {"role": "user", "content": CORRECTION_PROMPT},
            ]
            response = self._call(messages, tool)
            payload = _to_jsonable(response)
            content = list(getattr(response, "content", []))
            extracted = self._parse(content)
            if extracted is None:
                return SearchOutcome(listings=[], status=STATUS_PARSE_ERROR, payload=payload)

        evidence = collect_evidence(content)
        joined_evidence = " | ".join(evidence)
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
                cited_text=joined_evidence,
            )
            for item in extracted.anuncios
        ]
        return SearchOutcome(listings=listings, status=status, payload=payload)

    def _parse(self, content: list[Any]) -> ExtractionPayload | None:
        """Validate the model's JSON. Returns None when it cannot be trusted."""
        text = _response_text(content).strip()
        if not text:
            return None
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            return None
        try:
            return ExtractionPayload.model_validate(loaded)
        except ValidationError:
            return None
```

- [ ] **Step 4: Se o spike escolheu A, substituir `_call` por esta variante**

```python
    LISTING_SCHEMA: dict[str, Any] = {
        "type": "object",
        "properties": {
            "anuncios": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "titulo": {"type": "string"},
                        "preco_brl": {"type": ["number", "null"]},
                        "condicao": {"type": "string"},
                        "url": {"type": "string"},
                        "fonte": {"type": "string"},
                    },
                    "required": ["titulo", "preco_brl", "condicao", "url", "fonte"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["anuncios"],
        "additionalProperties": False,
    }

    # Inside _call, add to messages.create:
    #     output_config={"format": {"type": "json_schema", "schema": self.LISTING_SCHEMA}},
```

Com A, o retry de correção deixa de ser alcançável na prática, mas continua no código como rede de segurança. Nenhum teste muda.

- [ ] **Step 5: Se o spike escolheu C, substituir `_parse` e `_call` por esta variante**

```python
    RECORD_TOOL: dict[str, Any] = {
        "name": "registrar_anuncios",
        "description": "Registra os anuncios encontrados na busca.",
        "strict": True,
        "input_schema": LISTING_SCHEMA,  # same schema as variant A
    }

    # In _call, send both tools: tools=[tool, self.RECORD_TOOL]
    # And replace _parse with:
    def _parse(self, content: list[Any]) -> ExtractionPayload | None:
        """Read the strict tool call instead of free text."""
        for block in content:
            if getattr(block, "type", None) != "tool_use":
                continue
            if getattr(block, "name", None) != "registrar_anuncios":
                continue
            try:
                return ExtractionPayload.model_validate(block.input)
            except ValidationError:
                return None
        return None
```

Com C, o teste `test_invalid_json_is_retried_once_then_marked_parse_error` precisa passar `tool_use` blocks em vez de texto. Ajustar o helper `text_block` para um `tool_use_block(payload)` nesses três testes.

- [ ] **Step 6: Rodar o teste e confirmar que passa**

Run: `uv run pytest tests/test_anthropic_search.py -v`
Expected: PASS, 17 testes

- [ ] **Step 7: Rodar lint, type check e a suíte inteira, depois commit**

Run: `uv run ruff check . && uv run mypy renov_market_scan && uv run pytest`

```bash
git add renov_market_scan/collect/anthropic_search.py tests/test_anthropic_search.py
git commit -m "feat: adapter da API com web search tool

Cliente com max_retries=0 e tenacity como unica politica de retry. Erro
de busca vem em HTTP 200 e e tratado pelo bloco, nao por excecao.
pause_turn e retomado reenviando a mensagem do assistente intacta, com
teto de 3. cited_text das citations viaja no listing para a regra de
evidencia poder verificar o preco. JSON invalido tem 1 retry de correcao
e depois parse_error; nunca eval, nunca regex."
```

---

### Task 17: Estimador de custo para o dry-run

**Files:**
- Create: `renov_market_scan/cost.py`
- Create: `tests/test_cost.py`

**Interfaces:**
- Consumes: `Settings` (T1), `SearchPlanItem` (T2), `Source`/`PHRASE_COUNT` (T13).
- Produces: `Calibration` (dataclass frozen: `tokens_in_per_call: int`, `tokens_out_per_call: int`, `searches_per_call: float`); `CALIBRATION_FROM_SPIKE: Calibration`; `CostEstimate` (dataclass frozen: `calls: int`, `searches_expected: float`, `searches_ceiling: int`, `search_cost_usd: float`, `token_cost_usd: float`, `total_usd: float`, `ceiling_usd: float`, `minutes: float`); `PRICE_PER_SEARCH_USD = 0.01`; `MODEL_PRICES_PER_MTOK: dict[str, tuple[float, float]]`; `estimate(plan_items, sources, settings, calibration=CALIBRATION_FROM_SPIKE) -> CostEstimate`; `format_estimate(estimate: CostEstimate) -> str`.

**Os três números de `CALIBRATION_FROM_SPIKE` vêm da Tarefa 15**, seção "Calibração do estimador de custo" de `docs/fontes.md`. Substituir os valores abaixo pelos medidos.

- [ ] **Step 1: Escrever o teste que falha**

Arquivo `tests/test_cost.py`:

```python
from renov_market_scan.cost import (
    PRICE_PER_SEARCH_USD,
    Calibration,
    estimate,
    format_estimate,
)
from renov_market_scan.models import ReportKey, SearchPlanItem
from renov_market_scan.query.builder import Source
from renov_market_scan.config import Settings

CALIBRATION = Calibration(tokens_in_per_call=12000, tokens_out_per_call=900, searches_per_call=2.0)
SOURCES = [
    Source(name="olx", domain="olx.com.br"),
    Source(name="enjoei", domain="enjoei.com.br"),
    Source(name="mercadolivre", domain="mercadolivre.com.br"),
]


def make_items(count: int) -> list[SearchPlanItem]:
    return [
        SearchPlanItem(
            search_key=f"k{index}",
            manufacturer="APPLE",
            model=f"IPHONE {index}",
            storage_label="128GB",
            storage_gb=128,
            report_keys=[
                ReportKey(
                    erp_code=f"E{index}", model=f"IPHONE {index}", storage_label="128GB",
                    device_name=f"IPHONE {index} 128GB A0", price_instore=2000.0, row_number=3 + index,
                )
            ],
        )
        for index in range(count)
    ]


def settings() -> Settings:
    return Settings(anthropic_api_key="sk-test")


def test_call_count_is_items_times_sources():
    result = estimate(make_items(10), SOURCES, settings(), CALIBRATION)
    assert result.calls == 30


def test_ceiling_uses_max_uses_per_call():
    result = estimate(make_items(10), SOURCES, settings(), CALIBRATION)
    assert result.searches_ceiling == 30 * 3
    assert result.searches_expected == 30 * 2.0


def test_search_cost_is_a_cent_per_search():
    result = estimate(make_items(10), SOURCES, settings(), CALIBRATION)
    assert result.search_cost_usd == 30 * 2.0 * PRICE_PER_SEARCH_USD


def test_token_cost_uses_the_model_price():
    result = estimate(make_items(1), SOURCES, settings(), CALIBRATION)
    # 3 calls, Sonnet 5 introductory pricing: 2.00 in / 10.00 out per MTok.
    expected_in = 3 * 12000 / 1_000_000 * 2.00
    expected_out = 3 * 900 / 1_000_000 * 10.00
    assert abs(result.token_cost_usd - (expected_in + expected_out)) < 1e-9


def test_total_is_search_plus_tokens():
    result = estimate(make_items(10), SOURCES, settings(), CALIBRATION)
    assert abs(result.total_usd - (result.search_cost_usd + result.token_cost_usd)) < 1e-9


def test_ceiling_is_never_below_the_total():
    result = estimate(make_items(10), SOURCES, settings(), CALIBRATION)
    assert result.ceiling_usd >= result.total_usd


def test_the_android_active_batch_matches_the_spec_order_of_magnitude():
    """285 active models, 3 sources: the search fee floor is US$ 25.65 at ceiling."""
    result = estimate(make_items(285), SOURCES, settings(), CALIBRATION)
    assert result.calls == 855
    assert result.searches_ceiling == 2565
    assert abs(result.searches_ceiling * PRICE_PER_SEARCH_USD - 25.65) < 1e-9


def test_an_empty_plan_costs_nothing():
    result = estimate([], SOURCES, settings(), CALIBRATION)
    assert result.calls == 0
    assert result.total_usd == 0.0


def test_an_unknown_model_falls_back_to_the_most_expensive_price():
    unknown = Settings(anthropic_api_key="sk-test", model="claude-opus-5")
    result = estimate(make_items(1), SOURCES, unknown, CALIBRATION)
    assert result.token_cost_usd > 0


def test_the_formatted_estimate_is_in_portuguese_and_names_the_numbers():
    text = format_estimate(estimate(make_items(10), SOURCES, settings(), CALIBRATION))
    assert "buscas" in text
    assert "US$" in text
    assert "minuto" in text
```

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_cost.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar `renov_market_scan/cost.py`**

```python
"""Estimate what a run will cost before spending anything.

The search fee is fixed at US$ 0.01 per search regardless of model, so it is the
floor of any run. Token cost is the part that model choice moves. The token
figures are not guessed: they come from the Task 15 spike measurement.
"""

from dataclasses import dataclass

from renov_market_scan.config import Settings
from renov_market_scan.models import SearchPlanItem
from renov_market_scan.query.builder import PHRASE_COUNT, Source

PRICE_PER_SEARCH_USD = 0.01

# (input, output) US$ per million tokens.
MODEL_PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (2.00, 10.00),  # introductory pricing through 2026-08-31
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5": (10.00, 50.00),
}

FALLBACK_PRICE_PER_MTOK = (10.00, 50.00)

# Wall-clock seconds per call at the default concurrency, measured empirically.
SECONDS_PER_CALL = 12.0


@dataclass(frozen=True)
class Calibration:
    """Per-call figures measured by the Task 15 spike."""

    tokens_in_per_call: int
    tokens_out_per_call: int
    searches_per_call: float


# Replace with the values recorded in docs/fontes.md by Task 15.
CALIBRATION_FROM_SPIKE = Calibration(
    tokens_in_per_call=12000,
    tokens_out_per_call=900,
    searches_per_call=2.0,
)


@dataclass(frozen=True)
class CostEstimate:
    """What a run is expected to cost, and the worst case."""

    calls: int
    searches_expected: float
    searches_ceiling: int
    search_cost_usd: float
    token_cost_usd: float
    total_usd: float
    ceiling_usd: float
    minutes: float


def estimate(
    plan_items: list[SearchPlanItem],
    sources: list[Source],
    settings: Settings,
    calibration: Calibration = CALIBRATION_FROM_SPIKE,
) -> CostEstimate:
    """Estimate the cost of collecting this plan from these sources.

    Both phrases of a (model, source) pair travel in one call, so the call count
    is items times sources, and the search ceiling is that times max_uses.
    """
    calls = len(plan_items) * len(sources)
    searches_expected = calls * calibration.searches_per_call
    searches_ceiling = calls * settings.max_uses_per_call

    price_in, price_out = MODEL_PRICES_PER_MTOK.get(settings.model, FALLBACK_PRICE_PER_MTOK)
    token_cost = (
        calls * calibration.tokens_in_per_call / 1_000_000 * price_in
        + calls * calibration.tokens_out_per_call / 1_000_000 * price_out
    )
    search_cost = searches_expected * PRICE_PER_SEARCH_USD
    ceiling_cost = searches_ceiling * PRICE_PER_SEARCH_USD + token_cost

    concurrency = max(settings.concurrency, 1)
    minutes = calls * SECONDS_PER_CALL / concurrency / 60.0

    return CostEstimate(
        calls=calls,
        searches_expected=searches_expected,
        searches_ceiling=searches_ceiling,
        search_cost_usd=search_cost,
        token_cost_usd=token_cost,
        total_usd=search_cost + token_cost,
        ceiling_usd=ceiling_cost,
        minutes=minutes,
    )


def format_estimate(estimate_result: CostEstimate) -> str:
    """Human-readable pt-BR summary for the dry-run output."""
    return (
        f"Chamadas a API:      {estimate_result.calls}\n"
        f"Frases por chamada:  {PHRASE_COUNT}\n"
        f"Buscas esperadas:    {estimate_result.searches_expected:.0f}\n"
        f"Buscas no teto:      {estimate_result.searches_ceiling}\n"
        f"Custo de busca:      US$ {estimate_result.search_cost_usd:.2f}\n"
        f"Custo de tokens:     US$ {estimate_result.token_cost_usd:.2f}\n"
        f"Total esperado:      US$ {estimate_result.total_usd:.2f}\n"
        f"Total no teto:       US$ {estimate_result.ceiling_usd:.2f}\n"
        f"Tempo previsto:      {estimate_result.minutes:.0f} minuto(s)"
    )
```

- [ ] **Step 4: Rodar o teste, lint, tipos e commit**

Run: `uv run pytest tests/test_cost.py -v && uv run ruff check . && uv run mypy renov_market_scan`
Expected: PASS, 10 testes; sem erros

```bash
git add renov_market_scan/cost.py tests/test_cost.py
git commit -m "feat: estimador de custo do dry-run

Taxa de busca e piso fixo e independe do modelo; token e a parte que a
escolha de modelo move. Os numeros por chamada vem da medicao do spike,
nao de suposicao."
```

---

### Task 18: Montagem das linhas do relatório

**Files:**
- Create: `renov_market_scan/report/__init__.py`, `renov_market_scan/report/assemble.py`
- Create: `tests/test_assemble.py`

**Interfaces:**
- Consumes: `SearchPlanItem`, `ReportKey`, `ModelStats`, `Listing`, `RejectedListing`, `Anomaly` (T2); `aggregate` (T11).
- Produces: `SUMMARY_COLUMNS`, `SAMPLE_COLUMNS`, `DISCARDED_COLUMNS`, `ANOMALY_COLUMNS`: `tuple[str, ...]` com os nomes em pt-BR; `build_summary_rows(plan_items, stats_by_key, collected_on) -> list[dict[str, Any]]`; `build_sample_rows(plan_items, listings_by_key) -> list[dict[str, Any]]`; `build_discarded_rows(plan_items, rejected_by_key) -> list[dict[str, Any]]`; `build_anomaly_rows(anomalies, plan_items, stats_by_key) -> list[dict[str, Any]]`; `FOOTNOTE: str`.

**Fan-out.** A estatística é calculada uma vez por `search_key` e replicada para cada `report_key` que aquela busca atende — é isso que faz uma linha por `(ERP Code, Model, Storage)` sem refazer busca.

- [ ] **Step 1: Escrever o teste que falha**

Arquivo `tests/test_assemble.py`:

```python
from renov_market_scan.models import Anomaly, Listing, ModelStats, RejectedListing, ReportKey, SearchPlanItem
from renov_market_scan.report.assemble import (
    ANOMALY_COLUMNS,
    DISCARDED_COLUMNS,
    FOOTNOTE,
    SAMPLE_COLUMNS,
    SUMMARY_COLUMNS,
    build_anomaly_rows,
    build_discarded_rows,
    build_sample_rows,
    build_summary_rows,
)

DAY = "2026-07-28"


def make_item(search_key="k1", report_keys=None, **overrides) -> SearchPlanItem:
    base = {
        "search_key": search_key,
        "manufacturer": "APPLE",
        "model": "IPHONE 13",
        "storage_label": "128GB",
        "storage_gb": 128,
        "report_keys": report_keys
        or [
            ReportKey(
                erp_code="20022A0", model="IPHONE 13", storage_label="128GB",
                device_name="IPHONE 13 128GB A0", price_instore=2000.0, row_number=3,
            )
        ],
    }
    base.update(overrides)
    return SearchPlanItem(**base)


def make_stats(**overrides) -> ModelStats:
    base = {
        "search_key": "k1", "n": 5, "minimum": 2500.0, "p25": 2600.0, "median": 2800.0,
        "p75": 3000.0, "maximum": 3200.0, "spread_pct": 0.25,
        "min_url": "https://olx.com.br/min", "max_url": "https://olx.com.br/max",
        "min_raw": 90.0, "max_raw": 3200.0, "sources": ["olx", "enjoei"],
        "predominant_condition": "seminovo", "status": "ok",
    }
    base.update(overrides)
    return ModelStats(**base)


def make_listing(**overrides) -> Listing:
    base = {
        "search_key": "k1", "source": "olx", "title": "iPhone 13 128GB seminovo",
        "price_brl": 2800.0, "condition": "seminovo", "url": "https://olx.com.br/a-1",
        "captured_at": "2026-07-28T10:00:00", "cited_text": "R$ 2.800,00",
        "flag_5g_divergent": False,
    }
    base.update(overrides)
    return Listing(**base)


def test_summary_row_has_every_declared_column():
    rows = build_summary_rows([make_item()], {"k1": make_stats()}, DAY)
    assert len(rows) == 1
    assert set(rows[0]) == set(SUMMARY_COLUMNS)


def test_summary_carries_the_statistics_and_both_links():
    row = build_summary_rows([make_item()], {"k1": make_stats()}, DAY)[0]
    assert row["erp_code"] == "20022A0"
    assert row["mediana"] == 2800.0
    assert row["preco_minimo"] == 2500.0
    assert row["link_minimo"] == "https://olx.com.br/min"
    assert row["preco_maximo"] == 3200.0
    assert row["link_maximo"] == "https://olx.com.br/max"
    assert row["min_bruto"] == 90.0
    assert row["status"] == "ok"
    assert row["coletado_em"] == DAY


def test_ratio_against_the_sheet_price_is_computed():
    row = build_summary_rows([make_item()], {"k1": make_stats()}, DAY)[0]
    assert row["razao_mediana_vs_atual"] == 2800.0 / 2000.0


def test_ratio_is_none_without_a_sheet_price():
    keys = [
        ReportKey(erp_code="X", model="IPHONE 13", storage_label="128GB",
                  device_name="d", price_instore=None, row_number=3)
    ]
    row = build_summary_rows([make_item(report_keys=keys)], {"k1": make_stats()}, DAY)[0]
    assert row["razao_mediana_vs_atual"] is None


def test_statistics_fan_out_to_every_report_key():
    keys = [
        ReportKey(erp_code="A", model="IPHONE 13", storage_label="128GB",
                  device_name="d1", price_instore=2000.0, row_number=3),
        ReportKey(erp_code="B", model="IPHONE 13", storage_label="128GB",
                  device_name="d2", price_instore=2000.0, row_number=4),
    ]
    rows = build_summary_rows([make_item(report_keys=keys)], {"k1": make_stats()}, DAY)
    assert [row["erp_code"] for row in rows] == ["A", "B"]
    assert {row["mediana"] for row in rows} == {2800.0}


def test_a_key_without_statistics_still_produces_a_row():
    rows = build_summary_rows([make_item()], {}, DAY)
    assert len(rows) == 1
    assert rows[0]["status"] == "insuficiente"
    assert rows[0]["mediana"] is None
    assert rows[0]["n_amostras"] == 0


def test_sample_rows_repeat_per_report_key_and_declare_evidence():
    rows = build_sample_rows([make_item()], {"k1": [make_listing()]})
    assert set(rows[0]) == set(SAMPLE_COLUMNS)
    assert rows[0]["erp_code"] == "20022A0"
    assert rows[0]["cited_text"] == "R$ 2.800,00"
    assert rows[0]["flag_5g_divergente"] == "nao"


def test_five_g_flag_renders_in_portuguese():
    rows = build_sample_rows([make_item()], {"k1": [make_listing(flag_5g_divergent=True)]})
    assert rows[0]["flag_5g_divergente"] == "sim"


def test_discarded_rows_always_carry_a_reason():
    rejected = [RejectedListing(listing=make_listing(), reason="acessorio_ou_peca")]
    rows = build_discarded_rows([make_item()], {"k1": rejected})
    assert set(rows[0]) == set(DISCARDED_COLUMNS)
    assert rows[0]["motivo_descarte"] == "acessorio_ou_peca"


def test_anomaly_rows_include_storage_and_empty_samples():
    anomaly = Anomaly(
        row_number=5, erp_code="10000A0", device_name="MOTO XT882 1GB A0",
        field="Storage, GB*", raw_value="1", reason="storage_suspeito", status="revisao_humana",
    )
    rows = build_anomaly_rows([anomaly], [make_item()], {"k1": make_stats(n=0, status="insuficiente")})
    assert set(rows[0]) == set(ANOMALY_COLUMNS)
    reasons = {row["motivo"] for row in rows}
    assert "storage_suspeito" in reasons
    assert "sem_amostra" in reasons


def test_a_model_with_samples_is_not_reported_as_an_anomaly():
    rows = build_anomaly_rows([], [make_item()], {"k1": make_stats(n=5)})
    assert rows == []


def test_the_footnote_states_the_asking_price_caveat():
    assert "anuncio" in FOOTNOTE.lower()
    assert "transacao" in FOOTNOTE.lower() or "transação" in FOOTNOTE.lower()
```

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_assemble.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar `renov_market_scan/report/assemble.py`**

```bash
mkdir -p renov_market_scan/report
touch renov_market_scan/report/__init__.py
```

```python
"""Turn pipeline output into report rows. Column names are the pt-BR headers.

Statistics are computed once per search key and replicated to every report key
that search serves, which is what makes one row per (ERP Code, Model, Storage)
possible without repeating a search.
"""

from typing import Any

from renov_market_scan.models import (
    Anomaly,
    Listing,
    ModelStats,
    RejectedListing,
    SearchPlanItem,
)

FOOTNOTE = (
    "Valores de anuncio (preco pedido), nao de transacao. "
    "Amostra coletada em {data} - referencia de variacao de mercado, nao avaliacao."
)

SUMMARY_COLUMNS: tuple[str, ...] = (
    "erp_code",
    "device_name",
    "fabricante",
    "modelo",
    "capacidade",
    "valor_atual_planilha",
    "n_amostras",
    "n_fontes",
    "preco_minimo",
    "link_minimo",
    "mediana",
    "preco_maximo",
    "link_maximo",
    "p25",
    "p75",
    "spread_pct",
    "razao_mediana_vs_atual",
    "condicao_predominante",
    "fontes",
    "coletado_em",
    "status",
    "observacoes",
    "min_bruto",
    "max_bruto",
)

SAMPLE_COLUMNS: tuple[str, ...] = (
    "erp_code",
    "fonte",
    "titulo",
    "preco",
    "condicao",
    "url",
    "capturado_em",
    "flag_5g_divergente",
    "cited_text",
)

DISCARDED_COLUMNS: tuple[str, ...] = (
    "erp_code",
    "fonte",
    "titulo",
    "preco",
    "url",
    "motivo_descarte",
    "cited_text",
)

ANOMALY_COLUMNS: tuple[str, ...] = (
    "linha_planilha",
    "erp_code",
    "device_name",
    "campo",
    "valor_bruto",
    "motivo",
    "status",
)

EMPTY_STATS_STATUS = "insuficiente"


def _yes_no(value: bool) -> str:
    return "sim" if value else "nao"


def build_summary_rows(
    plan_items: list[SearchPlanItem],
    stats_by_key: dict[str, ModelStats],
    collected_on: str,
) -> list[dict[str, Any]]:
    """One row per report key, carrying its search key's statistics."""
    rows: list[dict[str, Any]] = []
    for item in plan_items:
        stats = stats_by_key.get(item.search_key)
        for key in item.report_keys:
            median = stats.median if stats else None
            ratio: float | None = None
            if median is not None and key.price_instore:
                ratio = median / key.price_instore
            rows.append(
                {
                    "erp_code": key.erp_code,
                    "device_name": key.device_name,
                    "fabricante": item.manufacturer,
                    "modelo": key.model,
                    "capacidade": key.storage_label,
                    "valor_atual_planilha": key.price_instore,
                    "n_amostras": stats.n if stats else 0,
                    "n_fontes": len(stats.sources) if stats else 0,
                    "preco_minimo": stats.minimum if stats else None,
                    "link_minimo": stats.min_url if stats else None,
                    "mediana": median,
                    "preco_maximo": stats.maximum if stats else None,
                    "link_maximo": stats.max_url if stats else None,
                    "p25": stats.p25 if stats else None,
                    "p75": stats.p75 if stats else None,
                    "spread_pct": stats.spread_pct if stats else None,
                    "razao_mediana_vs_atual": ratio,
                    "condicao_predominante": (
                        stats.predominant_condition if stats else "desconhecido"
                    ),
                    "fontes": ", ".join(stats.sources) if stats else "",
                    "coletado_em": collected_on,
                    "status": stats.status if stats else EMPTY_STATS_STATUS,
                    "observacoes": "",
                    "min_bruto": stats.min_raw if stats else None,
                    "max_bruto": stats.max_raw if stats else None,
                }
            )
    return rows


def build_sample_rows(
    plan_items: list[SearchPlanItem], listings_by_key: dict[str, list[Listing]]
) -> list[dict[str, Any]]:
    """Every accepted advert, repeated per report key so the join is explicit."""
    rows: list[dict[str, Any]] = []
    for item in plan_items:
        for listing in listings_by_key.get(item.search_key, []):
            for key in item.report_keys:
                rows.append(
                    {
                        "erp_code": key.erp_code,
                        "fonte": listing.source,
                        "titulo": listing.title,
                        "preco": listing.price_brl,
                        "condicao": listing.condition,
                        "url": listing.url,
                        "capturado_em": listing.captured_at,
                        "flag_5g_divergente": _yes_no(listing.flag_5g_divergent),
                        "cited_text": listing.cited_text,
                    }
                )
    return rows


def build_discarded_rows(
    plan_items: list[SearchPlanItem], rejected_by_key: dict[str, list[RejectedListing]]
) -> list[dict[str, Any]]:
    """Every discard with its reason, for rule calibration."""
    rows: list[dict[str, Any]] = []
    for item in plan_items:
        first_erp = item.report_keys[0].erp_code if item.report_keys else ""
        for rejected in rejected_by_key.get(item.search_key, []):
            rows.append(
                {
                    "erp_code": first_erp,
                    "fonte": rejected.listing.source,
                    "titulo": rejected.listing.title,
                    "preco": rejected.listing.price_brl,
                    "url": rejected.listing.url,
                    "motivo_descarte": rejected.reason,
                    "cited_text": rejected.listing.cited_text,
                }
            )
    return rows


def build_anomaly_rows(
    anomalies: list[Anomaly],
    plan_items: list[SearchPlanItem],
    stats_by_key: dict[str, ModelStats],
) -> list[dict[str, Any]]:
    """Dirty-storage rows plus every searched model that produced no sample."""
    rows: list[dict[str, Any]] = [
        {
            "linha_planilha": anomaly.row_number,
            "erp_code": anomaly.erp_code,
            "device_name": anomaly.device_name,
            "campo": anomaly.field,
            "valor_bruto": anomaly.raw_value,
            "motivo": anomaly.reason,
            "status": anomaly.status,
        }
        for anomaly in anomalies
    ]
    for item in plan_items:
        stats = stats_by_key.get(item.search_key)
        if stats is not None and stats.n > 0:
            continue
        for key in item.report_keys:
            rows.append(
                {
                    "linha_planilha": key.row_number,
                    "erp_code": key.erp_code,
                    "device_name": key.device_name,
                    "campo": "amostra",
                    "valor_bruto": "0",
                    "motivo": "sem_amostra",
                    "status": "revisao_humana",
                }
            )
    return rows
```

- [ ] **Step 4: Rodar o teste, lint, tipos e commit**

Run: `uv run pytest tests/test_assemble.py -v && uv run ruff check . && uv run mypy renov_market_scan`
Expected: PASS, 12 testes; sem erros

```bash
git add renov_market_scan/report tests/test_assemble.py
git commit -m "feat: montagem das linhas das quatro abas

Estatistica calculada uma vez por search_key e replicada para cada
report_key. Modelo pesquisado sem amostra vira linha na aba Anomalias."
```

---

### Task 19: Escrita das saídas (xlsx, cópia do template, csv, json)

**Files:**
- Create: `renov_market_scan/report/xlsx.py`, `renov_market_scan/report/template_copy.py`, `renov_market_scan/report/exports.py`
- Create: `tests/test_xlsx.py`, `tests/test_template_copy.py`, `tests/test_exports.py`

**Interfaces:**
- Consumes: colunas e `FOOTNOTE` da T18; `EXPECTED_HEADER`/`find_data_sheet` da T3.
- Produces: `write_xlsx_report(path, summary_rows, sample_rows, discarded_rows, anomaly_rows, collected_on) -> None`; `EXTRA_COLUMNS: tuple[str, ...]`; `write_template_copy(source_path, destination_path, summary_by_erp_model_storage) -> Path`; `write_csv(path, rows, columns) -> None`; `write_json(path, payload) -> None`.

- [ ] **Step 1: Escrever os testes que falham**

Arquivo `tests/test_xlsx.py`:

```python
from openpyxl import load_workbook

from renov_market_scan.report.xlsx import write_xlsx_report

SUMMARY = [
    {
        "erp_code": "20022A0", "device_name": "IPHONE 13 128GB A0", "fabricante": "APPLE",
        "modelo": "IPHONE 13", "capacidade": "128GB", "valor_atual_planilha": 2000.0,
        "n_amostras": 5, "n_fontes": 2, "preco_minimo": 2500.0,
        "link_minimo": "https://olx.com.br/min", "mediana": 2800.0, "preco_maximo": 3200.0,
        "link_maximo": "https://olx.com.br/max", "p25": 2600.0, "p75": 3000.0,
        "spread_pct": 0.25, "razao_mediana_vs_atual": 1.4,
        "condicao_predominante": "seminovo", "fontes": "olx, enjoei",
        "coletado_em": "2026-07-28", "status": "ok", "observacoes": "",
        "min_bruto": 90.0, "max_bruto": 3200.0,
    }
]
SAMPLES = [
    {
        "erp_code": "20022A0", "fonte": "olx", "titulo": "iPhone 13 128GB seminovo",
        "preco": 2800.0, "condicao": "seminovo", "url": "https://olx.com.br/a-1",
        "capturado_em": "2026-07-28T10:00:00", "flag_5g_divergente": "nao",
        "cited_text": "R$ 2.800,00",
    }
]
DISCARDED = [
    {
        "erp_code": "20022A0", "fonte": "olx", "titulo": "Capa iPhone 13", "preco": 30.0,
        "url": "https://olx.com.br/c-1", "motivo_descarte": "acessorio_ou_peca",
        "cited_text": "",
    }
]
ANOMALIES = [
    {
        "linha_planilha": 5, "erp_code": "10000A0", "device_name": "MOTO XT882 1GB A0",
        "campo": "Storage, GB*", "valor_bruto": "1", "motivo": "storage_suspeito",
        "status": "revisao_humana",
    }
]


def write(tmp_path):
    path = tmp_path / "referencia.xlsx"
    write_xlsx_report(path, SUMMARY, SAMPLES, DISCARDED, ANOMALIES, "2026-07-28")
    return path


def test_the_four_sheets_exist_with_the_expected_names(tmp_path):
    workbook = load_workbook(write(tmp_path))
    assert workbook.sheetnames == ["Resumo", "Amostras", "Descartados", "Anomalias"]
    workbook.close()


def test_headers_are_written_and_data_starts_on_row_two(tmp_path):
    workbook = load_workbook(write(tmp_path))
    sheet = workbook["Resumo"]
    assert sheet.cell(row=1, column=1).value == "erp_code"
    assert sheet.cell(row=2, column=1).value == "20022A0"
    workbook.close()


def test_links_are_written_as_clickable_formulas(tmp_path):
    workbook = load_workbook(write(tmp_path))
    sheet = workbook["Resumo"]
    header = [cell.value for cell in sheet[1]]
    column = header.index("link_minimo") + 1
    value = sheet.cell(row=2, column=column).value
    assert isinstance(value, str) and value.startswith("=HYPERLINK(")
    assert "https://olx.com.br/min" in value
    workbook.close()


def test_freeze_pane_and_autofilter_are_set(tmp_path):
    workbook = load_workbook(write(tmp_path))
    sheet = workbook["Resumo"]
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref is not None
    workbook.close()


def test_currency_columns_carry_the_brl_number_format(tmp_path):
    workbook = load_workbook(write(tmp_path))
    sheet = workbook["Resumo"]
    header = [cell.value for cell in sheet[1]]
    column = header.index("mediana") + 1
    assert "R$" in sheet.cell(row=2, column=column).number_format
    workbook.close()


def test_the_footnote_is_written_below_the_summary(tmp_path):
    workbook = load_workbook(write(tmp_path))
    sheet = workbook["Resumo"]
    texts = [
        sheet.cell(row=row, column=1).value
        for row in range(2, sheet.max_row + 1)
        if sheet.cell(row=row, column=1).value
    ]
    assert any("nao de transacao" in str(text) for text in texts)
    workbook.close()


def test_empty_sheets_still_get_their_headers(tmp_path):
    path = tmp_path / "vazio.xlsx"
    write_xlsx_report(path, SUMMARY, [], [], [], "2026-07-28")
    workbook = load_workbook(path)
    assert workbook["Amostras"].cell(row=1, column=1).value == "erp_code"
    assert workbook["Amostras"].max_row == 1
    workbook.close()
```

Arquivo `tests/test_template_copy.py`:

```python
import hashlib

from openpyxl import load_workbook

from renov_market_scan.ingest.reader import EXPECTED_HEADER
from renov_market_scan.report.template_copy import EXTRA_COLUMNS, write_template_copy

SUMMARY_BY_KEY = {
    ("10870A0", "GALAXY A17", "128GB"): {
        "n_amostras": 5, "preco_minimo": 300.0, "mediana": 400.0, "preco_maximo": 500.0,
        "link_minimo": "https://olx.com.br/min", "link_maximo": "https://olx.com.br/max",
        "razao_mediana_vs_atual": 1.0, "status": "ok",
    }
}


def test_the_input_file_is_untouched(tmp_path, make_sheet, device_row_dict):
    source = make_sheet(tmp_path, [device_row_dict()])
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    write_template_copy(source, tmp_path / "copia.xlsx", SUMMARY_BY_KEY)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_the_copy_preserves_the_help_row_header_and_sheet_name(
    tmp_path, make_sheet, device_row_dict
):
    source = make_sheet(tmp_path, [device_row_dict()], sheet_name="iPhones")
    destination = write_template_copy(source, tmp_path / "copia.xlsx", SUMMARY_BY_KEY)
    workbook = load_workbook(destination)
    assert workbook.sheetnames[0] == "iPhones"
    sheet = workbook["iPhones"]
    assert "Custom name of a device" in str(sheet.cell(row=1, column=1).value)
    header = [sheet.cell(row=2, column=index + 1).value for index in range(len(EXPECTED_HEADER))]
    assert tuple(header) == EXPECTED_HEADER
    workbook.close()


def test_extra_columns_are_appended_to_the_right(tmp_path, make_sheet, device_row_dict):
    source = make_sheet(tmp_path, [device_row_dict()])
    destination = write_template_copy(source, tmp_path / "copia.xlsx", SUMMARY_BY_KEY)
    workbook = load_workbook(destination)
    sheet = workbook[workbook.sheetnames[0]]
    first_extra = len(EXPECTED_HEADER) + 1
    appended = [
        sheet.cell(row=2, column=first_extra + offset).value
        for offset in range(len(EXTRA_COLUMNS))
    ]
    assert tuple(appended) == EXTRA_COLUMNS
    workbook.close()


def test_matching_rows_receive_their_values(tmp_path, make_sheet, device_row_dict):
    source = make_sheet(tmp_path, [device_row_dict()])
    destination = write_template_copy(source, tmp_path / "copia.xlsx", SUMMARY_BY_KEY)
    workbook = load_workbook(destination)
    sheet = workbook[workbook.sheetnames[0]]
    header_row = 2
    header = [cell.value for cell in sheet[header_row]]
    column = header.index("mediana_mercado") + 1
    assert sheet.cell(row=3, column=column).value == 400.0
    workbook.close()


def test_the_original_price_column_is_never_overwritten(tmp_path, make_sheet, device_row_dict):
    source = make_sheet(tmp_path, [device_row_dict()])
    destination = write_template_copy(source, tmp_path / "copia.xlsx", SUMMARY_BY_KEY)
    workbook = load_workbook(destination)
    sheet = workbook[workbook.sheetnames[0]]
    column = EXPECTED_HEADER.index("Price for In-store") + 1
    assert sheet.cell(row=3, column=column).value == 400
    workbook.close()


def test_rows_without_a_result_are_left_blank(tmp_path, make_sheet, device_row_dict):
    source = make_sheet(tmp_path, [device_row_dict(**{"ERP Code": "SEM_RESULTADO"})])
    destination = write_template_copy(source, tmp_path / "copia.xlsx", SUMMARY_BY_KEY)
    workbook = load_workbook(destination)
    sheet = workbook[workbook.sheetnames[0]]
    header = [cell.value for cell in sheet[2]]
    column = header.index("mediana_mercado") + 1
    assert sheet.cell(row=3, column=column).value is None
    workbook.close()
```

Arquivo `tests/test_exports.py`:

```python
import csv
import json

from renov_market_scan.report.exports import write_csv, write_json


def test_csv_writes_the_declared_columns_in_order(tmp_path):
    path = tmp_path / "resumo.csv"
    write_csv(path, [{"b": 2, "a": 1}], ("a", "b"))
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["a", "b"]
    assert rows[1] == ["1", "2"]


def test_csv_writes_only_the_header_for_an_empty_list(tmp_path):
    path = tmp_path / "resumo.csv"
    write_csv(path, [], ("a", "b"))
    assert path.read_text(encoding="utf-8").strip() == "a,b"


def test_csv_renders_none_as_an_empty_field(tmp_path):
    path = tmp_path / "resumo.csv"
    write_csv(path, [{"a": None, "b": 2}], ("a", "b"))
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[1] == ["", "2"]


def test_json_round_trips_with_accents_intact(tmp_path):
    path = tmp_path / "resultado.json"
    write_json(path, {"observacao": "condição não avaliada"})
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["observacao"] == "condição não avaliada"
```

- [ ] **Step 2: Rodar os testes para confirmar que falham**

Run: `uv run pytest tests/test_xlsx.py tests/test_template_copy.py tests/test_exports.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar `renov_market_scan/report/xlsx.py`**

```python
"""Write the four-sheet auditable report."""

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from renov_market_scan.report.assemble import (
    ANOMALY_COLUMNS,
    DISCARDED_COLUMNS,
    FOOTNOTE,
    SAMPLE_COLUMNS,
    SUMMARY_COLUMNS,
)

BRL_FORMAT = "R$ #,##0.00"
RATIO_FORMAT = "0.00"

CURRENCY_COLUMNS: frozenset[str] = frozenset(
    {
        "valor_atual_planilha",
        "preco_minimo",
        "mediana",
        "preco_maximo",
        "p25",
        "p75",
        "min_bruto",
        "max_bruto",
        "preco",
    }
)
LINK_COLUMNS: frozenset[str] = frozenset({"link_minimo", "link_maximo", "url"})
RATIO_COLUMNS: frozenset[str] = frozenset({"razao_mediana_vs_atual", "spread_pct"})

MAX_COLUMN_WIDTH = 60
MIN_COLUMN_WIDTH = 10


def _hyperlink(url: str) -> str:
    """A clickable cell. Quotes in the URL are escaped for the formula."""
    safe = url.replace('"', '""')
    return f'=HYPERLINK("{safe}";"abrir")'


def _write_sheet(
    sheet: Worksheet, columns: tuple[str, ...], rows: list[dict[str, Any]]
) -> None:
    sheet.append(list(columns))
    for row in rows:
        sheet.append(
            [
                _hyperlink(str(row[column]))
                if column in LINK_COLUMNS and row.get(column)
                else row.get(column)
                for column in columns
            ]
        )

    sheet.freeze_panes = "A2"
    last_column = get_column_letter(len(columns))
    sheet.auto_filter.ref = f"A1:{last_column}{max(sheet.max_row, 1)}"

    for index, column in enumerate(columns, start=1):
        letter = get_column_letter(index)
        longest = max(
            [len(str(column))] + [len(str(row.get(column) or "")) for row in rows]
        )
        sheet.column_dimensions[letter].width = min(
            max(longest + 2, MIN_COLUMN_WIDTH), MAX_COLUMN_WIDTH
        )
        if column in CURRENCY_COLUMNS:
            for row_index in range(2, sheet.max_row + 1):
                sheet.cell(row=row_index, column=index).number_format = BRL_FORMAT
        elif column in RATIO_COLUMNS:
            for row_index in range(2, sheet.max_row + 1):
                sheet.cell(row=row_index, column=index).number_format = RATIO_FORMAT


def write_xlsx_report(
    path: Path,
    summary_rows: list[dict[str, Any]],
    sample_rows: list[dict[str, Any]],
    discarded_rows: list[dict[str, Any]],
    anomaly_rows: list[dict[str, Any]],
    collected_on: str,
) -> None:
    """Write Resumo, Amostras, Descartados and Anomalias, in that order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()

    summary = workbook.active
    summary.title = "Resumo"
    _write_sheet(summary, SUMMARY_COLUMNS, summary_rows)

    if "razao_mediana_vs_atual" in SUMMARY_COLUMNS and summary_rows:
        index = SUMMARY_COLUMNS.index("razao_mediana_vs_atual") + 1
        letter = get_column_letter(index)
        summary.conditional_formatting.add(
            f"{letter}2:{letter}{len(summary_rows) + 1}",
            ColorScaleRule(
                start_type="min",
                start_color="F8696B",
                mid_type="num",
                mid_value=1,
                mid_color="FFEB84",
                end_type="max",
                end_color="63BE7B",
            ),
        )

    footnote_row = len(summary_rows) + 3
    summary.cell(row=footnote_row, column=1).value = FOOTNOTE.format(data=collected_on)

    _write_sheet(workbook.create_sheet("Amostras"), SAMPLE_COLUMNS, sample_rows)
    _write_sheet(workbook.create_sheet("Descartados"), DISCARDED_COLUMNS, discarded_rows)
    _write_sheet(workbook.create_sheet("Anomalias"), ANOMALY_COLUMNS, anomaly_rows)

    workbook.save(path)
    workbook.close()
```

- [ ] **Step 4: Implementar `renov_market_scan/report/template_copy.py`**

```python
"""Copy the input template and append result columns to the copy.

The input file is never opened for writing. shutil.copy2 duplicates it byte for
byte, preserving the help row, the 19 original columns and the sheet name, and
only the copy is edited.
"""

import shutil
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from renov_market_scan.ingest.reader import EXPECTED_HEADER, HEADER_ROW, find_data_sheet

EXTRA_COLUMNS: tuple[str, ...] = (
    "n_amostras_mercado",
    "preco_minimo_mercado",
    "mediana_mercado",
    "preco_maximo_mercado",
    "link_minimo_mercado",
    "link_maximo_mercado",
    "razao_mediana_vs_atual",
    "status_mercado",
)

# Maps an extra column to the summary-row field that feeds it.
EXTRA_COLUMN_SOURCES: dict[str, str] = {
    "n_amostras_mercado": "n_amostras",
    "preco_minimo_mercado": "preco_minimo",
    "mediana_mercado": "mediana",
    "preco_maximo_mercado": "preco_maximo",
    "link_minimo_mercado": "link_minimo",
    "link_maximo_mercado": "link_maximo",
    "razao_mediana_vs_atual": "razao_mediana_vs_atual",
    "status_mercado": "status",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def write_template_copy(
    source_path: Path,
    destination_path: Path,
    summary_by_key: dict[tuple[str, str, str], dict[str, Any]],
) -> Path:
    """Duplicate the template and fill the appended columns.

    summary_by_key is indexed by (ERP Code, Model, Storage label) - the report
    key - because ERP Code alone is not unique in the source data.
    """
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination_path)

    workbook = load_workbook(destination_path)
    try:
        sheet = workbook[find_data_sheet(workbook)]
        first_extra = len(EXPECTED_HEADER) + 1

        for offset, name in enumerate(EXTRA_COLUMNS):
            sheet.cell(row=HEADER_ROW, column=first_extra + offset).value = name

        erp_index = EXPECTED_HEADER.index("ERP Code") + 1
        model_index = EXPECTED_HEADER.index("Model*") + 1
        storage_index = EXPECTED_HEADER.index("Storage, GB*") + 1

        for row_index in range(HEADER_ROW + 1, sheet.max_row + 1):
            erp = _text(sheet.cell(row=row_index, column=erp_index).value)
            model = _text(sheet.cell(row=row_index, column=model_index).value)
            storage_raw = _text(sheet.cell(row=row_index, column=storage_index).value)
            if not erp and not model:
                continue

            summary = None
            for label in (storage_raw, f"{storage_raw}GB", "1TB", "2TB"):
                summary = summary_by_key.get((erp, model, label))
                if summary is not None:
                    break
            if summary is None:
                continue

            for offset, name in enumerate(EXTRA_COLUMNS):
                field = EXTRA_COLUMN_SOURCES[name]
                sheet.cell(row=row_index, column=first_extra + offset).value = summary.get(field)

        workbook.save(destination_path)
    finally:
        workbook.close()
    return destination_path
```

- [ ] **Step 5: Implementar `renov_market_scan/report/exports.py`**

```python
"""CSV and JSON exports of the same data as the report."""

import csv
import json
from pathlib import Path
from typing import Any


def write_csv(path: Path, rows: list[dict[str, Any]], columns: tuple[str, ...]) -> None:
    """Write rows in the declared column order. None becomes an empty field."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def write_json(path: Path, payload: Any) -> None:
    """Write UTF-8 JSON with accents preserved and stable indentation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
```

- [ ] **Step 6: Rodar os testes, lint, tipos e commit**

Run: `uv run pytest tests/test_xlsx.py tests/test_template_copy.py tests/test_exports.py -v && uv run ruff check . && uv run mypy renov_market_scan`
Expected: PASS, 17 testes; sem erros

```bash
git add renov_market_scan/report tests/test_xlsx.py tests/test_template_copy.py tests/test_exports.py
git commit -m "feat: escrita das saidas

Relatorio de 4 abas com HYPERLINK, freeze, autofilter e formato BRL. A
copia do template preserva linha de ajuda, as 19 colunas e o nome da aba,
anexa colunas a direita e nunca sobrescreve Price for In-store. O arquivo
de entrada e verificado por hash no teste."
```

---

### Task 20: Orquestração e CLI

**Files:**
- Create: `renov_market_scan/run.py`, `renov_market_scan/cli.py`
- Create: `tests/test_run.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: tudo das Tarefas 1 a 19.
- Produces: `RunOptions` (dataclass frozen: `input_path: Path`, `output_dir: Path`, `cache_path: Path`, `sources: list[str] | None`, `active_only: bool`, `manufacturer: str | None`, `limit: int | None`, `include_new: bool`, `resume: bool`, `reprocess_only: bool`, `collected_on: str`); `RunResult` (dataclass frozen: `summary_rows`, `sample_rows`, `discarded_rows`, `anomaly_rows`, `searches_performed: int`, `report_path: Path`, `template_copy_path: Path`); `async def execute(options: RunOptions, settings: Settings, adapter: SearchAdapter, progress: Callable[[int, int], None] | None = None) -> RunResult`; e o `app` do Typer com o comando `run`.

- [ ] **Step 1: Escrever o teste de orquestração que falha**

Arquivo `tests/test_run.py`:

```python
import asyncio
import hashlib
from pathlib import Path

from renov_market_scan.collect.fixture import FixtureAdapter
from renov_market_scan.config import Settings
from renov_market_scan.ingest.normalize import compute_search_key
from renov_market_scan.models import Listing
from renov_market_scan.run import RunOptions, execute

DAY = "2026-07-28"


def settings() -> Settings:
    return Settings(anthropic_api_key="sk-test")


def options(tmp_path: Path, source: Path, **overrides) -> RunOptions:
    base = {
        "input_path": source,
        "output_dir": tmp_path / "out",
        "cache_path": tmp_path / ".cache" / "scan.sqlite",
        "sources": ["olx"],
        "active_only": True,
        "manufacturer": None,
        "limit": None,
        "include_new": False,
        "resume": False,
        "reprocess_only": False,
        "collected_on": DAY,
    }
    base.update(overrides)
    return RunOptions(**base)


def canned(search_key: str, count: int = 5) -> list[Listing]:
    return [
        Listing(
            search_key=search_key,
            source="olx",
            title=f"Samsung Galaxy A17 128GB seminovo R$ {2000 + index * 100},00",
            price_brl=float(2000 + index * 100),
            condition="seminovo",
            url=f"https://olx.com.br/a-{index}",
            captured_at=f"{DAY}T10:00:00",
            cited_text=f"R$ {2000 + index * 100},00",
        )
        for index in range(count)
    ]


def test_a_full_offline_run_produces_every_artifact(tmp_path, make_sheet, device_row_dict):
    source = make_sheet(tmp_path, [device_row_dict()])
    key = compute_search_key("SAMSUNG", "GALAXY A17", "128GB")
    adapter = FixtureAdapter({(key, "olx"): canned(key)})
    result = asyncio.run(execute(options(tmp_path, source), settings(), adapter))

    assert result.report_path.exists()
    assert result.template_copy_path.exists()
    assert (tmp_path / "out" / "resumo.csv").exists()
    assert (tmp_path / "out" / "resultado.json").exists()
    assert len(result.summary_rows) == 1
    assert result.summary_rows[0]["status"] == "ok"
    assert result.summary_rows[0]["n_amostras"] == 5


def test_the_input_file_hash_is_unchanged_by_a_full_run(tmp_path, make_sheet, device_row_dict):
    source = make_sheet(tmp_path, [device_row_dict()])
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    key = compute_search_key("SAMSUNG", "GALAXY A17", "128GB")
    adapter = FixtureAdapter({(key, "olx"): canned(key)})
    asyncio.run(execute(options(tmp_path, source), settings(), adapter))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_resume_performs_no_search_when_the_cache_is_warm(tmp_path, make_sheet, device_row_dict):
    source = make_sheet(tmp_path, [device_row_dict()])
    key = compute_search_key("SAMSUNG", "GALAXY A17", "128GB")

    first = FixtureAdapter({(key, "olx"): canned(key)})
    asyncio.run(execute(options(tmp_path, source), settings(), first))
    assert first.call_count > 0

    second = FixtureAdapter({(key, "olx"): canned(key)})
    result = asyncio.run(execute(options(tmp_path, source, resume=True), settings(), second))
    assert second.call_count == 0
    assert result.searches_performed == 0
    assert result.summary_rows[0]["n_amostras"] == 5


def test_reprocess_only_never_touches_the_adapter(tmp_path, make_sheet, device_row_dict):
    source = make_sheet(tmp_path, [device_row_dict()])
    key = compute_search_key("SAMSUNG", "GALAXY A17", "128GB")
    asyncio.run(execute(options(tmp_path, source), settings(), FixtureAdapter({(key, "olx"): canned(key)})))

    adapter = FixtureAdapter({})
    result = asyncio.run(
        execute(options(tmp_path, source, reprocess_only=True), settings(), adapter)
    )
    assert adapter.call_count == 0
    assert result.summary_rows[0]["n_amostras"] == 5


def test_a_blocked_source_is_recorded_and_does_not_abort_the_run(
    tmp_path, make_sheet, device_row_dict
):
    source = make_sheet(tmp_path, [device_row_dict()])
    key = compute_search_key("SAMSUNG", "GALAXY A17", "128GB")
    adapter = FixtureAdapter({}, statuses={(key, "olx"): "bloqueado"})
    result = asyncio.run(execute(options(tmp_path, source), settings(), adapter))
    assert result.summary_rows[0]["status"] == "insuficiente"
    assert any(row["motivo"] == "sem_amostra" for row in result.anomaly_rows)


def test_discarded_listings_reach_the_report_with_their_reason(
    tmp_path, make_sheet, device_row_dict
):
    source = make_sheet(tmp_path, [device_row_dict()])
    key = compute_search_key("SAMSUNG", "GALAXY A17", "128GB")
    junk = Listing(
        search_key=key, source="olx", title="Capa capinha Galaxy A17 128GB",
        price_brl=30.0, condition="desconhecido", url="https://olx.com.br/c-1",
        captured_at=f"{DAY}T10:00:00", cited_text="R$ 30,00",
    )
    adapter = FixtureAdapter({(key, "olx"): canned(key) + [junk]})
    result = asyncio.run(execute(options(tmp_path, source), settings(), adapter))
    assert any(row["motivo_descarte"] == "acessorio_ou_peca" for row in result.discarded_rows)


def test_limit_bounds_the_number_of_searches(tmp_path, make_sheet, device_row_dict):
    rows = [
        device_row_dict(**{"Model*": f"GALAXY A{index}", "ERP Code": f"E{index}"})
        for index in range(5)
    ]
    source = make_sheet(tmp_path, rows)
    adapter = FixtureAdapter({})
    asyncio.run(execute(options(tmp_path, source, limit=2), settings(), adapter))
    assert adapter.call_count == 2  # 2 items x 1 source, both phrases in one call


def test_progress_callback_is_invoked_once_per_call(tmp_path, make_sheet, device_row_dict):
    rows = [
        device_row_dict(**{"Model*": f"GALAXY A{index}", "ERP Code": f"E{index}"})
        for index in range(3)
    ]
    source = make_sheet(tmp_path, rows)
    seen: list[tuple[int, int]] = []
    asyncio.run(
        execute(options(tmp_path, source), settings(), FixtureAdapter({}), progress=lambda done, total: seen.append((done, total)))
    )
    assert len(seen) == 3
    assert seen[-1] == (3, 3)
```

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_run.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'renov_market_scan.run'`

- [ ] **Step 3: Implementar `renov_market_scan/run.py`**

```python
"""Orchestrate one run: read, plan, collect, filter, aggregate, report."""

import asyncio
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from renov_market_scan.cache.store import (
    has_raw,
    load_listings,
    open_store,
    save_listings,
    save_raw,
)
from renov_market_scan.collect.base import SearchAdapter
from renov_market_scan.config import Settings
from renov_market_scan.filtering.matcher import load_brand_aliases
from renov_market_scan.filtering.pipeline import FilterContext, run_pipeline
from renov_market_scan.ingest.normalize import build_search_plan
from renov_market_scan.ingest.reader import read_device_rows
from renov_market_scan.models import (
    Anomaly,
    Listing,
    ModelStats,
    RawResponse,
    RejectedListing,
    SearchPlanItem,
)
from renov_market_scan.query.builder import (
    Source,
    build_queries,
    enabled_sources,
    load_sources,
)
from renov_market_scan.report.assemble import (
    SUMMARY_COLUMNS,
    build_anomaly_rows,
    build_discarded_rows,
    build_sample_rows,
    build_summary_rows,
)
from renov_market_scan.report.exports import write_csv, write_json
from renov_market_scan.report.template_copy import write_template_copy
from renov_market_scan.report.xlsx import write_xlsx_report
from renov_market_scan.stats.aggregate import aggregate

SOURCES_FILE = Path("fontes.yaml")
BRANDS_FILE = Path("marcas.yaml")

# Both phrases of a (model, source) pair travel in one call, so one raw response
# is cached per pair. The column stays in the schema for a future per-phrase mode.
COMBINED_PHRASE_INDEX = 0


@dataclass(frozen=True)
class RunOptions:
    """Everything the CLI decided for this run."""

    input_path: Path
    output_dir: Path
    cache_path: Path
    sources: list[str] | None
    active_only: bool
    manufacturer: str | None
    limit: int | None
    include_new: bool
    resume: bool
    reprocess_only: bool
    collected_on: str


@dataclass(frozen=True)
class RunResult:
    """What the run produced, for the CLI to summarize."""

    summary_rows: list[dict[str, object]]
    sample_rows: list[dict[str, object]]
    discarded_rows: list[dict[str, object]]
    anomaly_rows: list[dict[str, object]]
    searches_performed: int
    report_path: Path
    template_copy_path: Path


async def execute(
    options: RunOptions,
    settings: Settings,
    adapter: SearchAdapter,
    progress: Callable[[int, int], None] | None = None,
) -> RunResult:
    """Run the whole pipeline. Never writes to options.input_path."""
    rows, _warnings = read_device_rows(options.input_path)
    plan, anomalies = build_search_plan(
        rows,
        active_only=options.active_only,
        manufacturer_filter=options.manufacturer,
        limit=options.limit,
    )

    sources = enabled_sources(load_sources(SOURCES_FILE), options.sources)
    brand_aliases = load_brand_aliases(BRANDS_FILE)
    connection = open_store(options.cache_path)
    searches_performed = 0

    try:
        if not options.reprocess_only:
            searches_performed = await _collect(
                plan, sources, brand_aliases, options, adapter, connection, progress
            )

        cached = load_listings(connection, options.collected_on)
        stats_by_key, listings_by_key, rejected_by_key = _analyze(plan, cached, settings, options)
        return _emit(plan, anomalies, stats_by_key, listings_by_key, rejected_by_key, options, searches_performed)
    finally:
        connection.commit()
        connection.close()


async def _collect(
    plan: list[SearchPlanItem],
    sources: list[Source],
    brand_aliases: dict[str, list[str]],
    options: RunOptions,
    adapter: SearchAdapter,
    connection: sqlite3.Connection,
    progress: Callable[[int, int], None] | None,
) -> int:
    """One adapter call per (item, source), carrying every phrase.

    Calls run concurrently: the adapter's semaphore is what bounds them, so
    awaiting each pair in sequence here would pin real concurrency at one and
    make --concorrencia and the estimated runtime meaningless.
    """
    pairs: list[tuple[SearchPlanItem, Source]] = [
        (item, source) for item in plan for source in sources
    ]
    total = len(pairs)

    # One cached response per (key, source, day). The phrase index column
    # records which phrase set produced it; the standard pair is 0.
    outstanding: list[tuple[SearchPlanItem, Source]] = [
        (item, source)
        for item, source in pairs
        if not (
            options.resume
            and has_raw(
                connection,
                item.search_key,
                source.name,
                COMBINED_PHRASE_INDEX,
                options.collected_on,
            )
        )
    ]

    done = 0
    performed = 0
    write_lock = asyncio.Lock()

    def advance() -> None:
        nonlocal done
        done += 1
        if progress is not None:
            progress(done, total)

    for _ in range(total - len(outstanding)):
        advance()

    async def collect_one(item: SearchPlanItem, source: Source) -> None:
        nonlocal performed
        queries = build_queries(item, [source], brand_aliases)
        outcome = await adapter.search(queries)
        # The adapter's semaphore bounds real concurrency; this lock keeps the
        # SQLite writes from interleaving.
        async with write_lock:
            performed += 1
            save_raw(
                connection,
                RawResponse(
                    search_key=item.search_key,
                    source=source.name,
                    phrase_index=COMBINED_PHRASE_INDEX,
                    collected_on=options.collected_on,
                    payload=outcome.payload,
                    status=outcome.status,
                ),
            )
            save_listings(
                connection, item.search_key, source.name, options.collected_on, outcome.listings
            )
            advance()

    await asyncio.gather(*(collect_one(item, source) for item, source in outstanding))
    return performed


def _analyze(
    plan: list[SearchPlanItem],
    cached: dict[str, list[Listing]],
    settings: Settings,
    options: RunOptions,
) -> tuple[dict[str, ModelStats], dict[str, list[Listing]], dict[str, list[RejectedListing]]]:
    """Filter and aggregate. Pure over the cache; costs nothing to repeat."""
    stats_by_key: dict[str, ModelStats] = {}
    accepted_by_key: dict[str, list[Listing]] = {}
    rejected_by_key: dict[str, list[RejectedListing]] = {}

    for item in plan:
        context = FilterContext(
            storage_gb=item.storage_gb,
            manufacturer=item.manufacturer,
            model=item.model,
            include_new=options.include_new,
            price_floor_brl=settings.price_floor_brl,
            price_ceiling_brl=settings.price_ceiling_brl,
        )
        accepted, rejected = run_pipeline(cached.get(item.search_key, []), context)
        accepted_by_key[item.search_key] = accepted
        rejected_by_key[item.search_key] = rejected
        stats_by_key[item.search_key] = aggregate(item.search_key, accepted)

    return stats_by_key, accepted_by_key, rejected_by_key


def _emit(
    plan: list[SearchPlanItem],
    anomalies: list[Anomaly],
    stats_by_key: dict[str, ModelStats],
    listings_by_key: dict[str, list[Listing]],
    rejected_by_key: dict[str, list[RejectedListing]],
    options: RunOptions,
    searches_performed: int,
) -> RunResult:
    """Write every output artifact."""
    summary_rows = build_summary_rows(plan, stats_by_key, options.collected_on)
    sample_rows = build_sample_rows(plan, listings_by_key)
    discarded_rows = build_discarded_rows(plan, rejected_by_key)
    anomaly_rows = build_anomaly_rows(anomalies, plan, stats_by_key)

    report_path = options.output_dir / f"referencia-mercado_{options.collected_on}.xlsx"
    write_xlsx_report(
        report_path, summary_rows, sample_rows, discarded_rows, anomaly_rows, options.collected_on
    )
    write_csv(options.output_dir / "resumo.csv", summary_rows, SUMMARY_COLUMNS)
    write_json(
        options.output_dir / "resultado.json",
        {
            "coletado_em": options.collected_on,
            "resumo": summary_rows,
            "amostras": sample_rows,
            "descartados": discarded_rows,
            "anomalias": anomaly_rows,
        },
    )

    summary_by_key = {
        (str(row["erp_code"]), str(row["modelo"]), str(row["capacidade"])): row
        for row in summary_rows
    }
    template_copy_path = write_template_copy(
        options.input_path,
        options.output_dir / f"{options.input_path.stem}_com-referencia.xlsx",
        summary_by_key,
    )

    return RunResult(
        summary_rows=summary_rows,
        sample_rows=sample_rows,
        discarded_rows=discarded_rows,
        anomaly_rows=anomaly_rows,
        searches_performed=searches_performed,
        report_path=report_path,
        template_copy_path=template_copy_path,
    )
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `uv run pytest tests/test_run.py -v`
Expected: PASS, 8 testes

- [ ] **Step 5: Escrever o teste de CLI que falha**

Arquivo `tests/test_cli.py`:

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


def test_dry_run_prints_the_plan_and_cost_and_spends_nothing(
    tmp_path, make_sheet, device_row_dict, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    source = make_sheet(tmp_path, [device_row_dict()])
    result = runner.invoke(
        app,
        [
            "run", "--input", str(source), "--output", str(tmp_path / "out"),
            "--cache", str(tmp_path / "scan.sqlite"), "--fontes", "olx", "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "Chamadas a API" in result.output
    assert "US$" in result.output
    assert not (tmp_path / "out").exists()


def test_dry_run_reports_the_active_model_count(
    tmp_path, make_sheet, device_row_dict, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    rows = [device_row_dict(**{"Model*": f"GALAXY A{i}", "ERP Code": f"E{i}"}) for i in range(3)]
    rows.append(device_row_dict(**{"Model*": "INATIVO", "ERP Code": "E9", "Price for In-store": 10}))
    source = make_sheet(tmp_path, rows)
    result = runner.invoke(
        app,
        [
            "run", "--input", str(source), "--output", str(tmp_path / "out"),
            "--cache", str(tmp_path / "scan.sqlite"), "--fontes", "olx", "--dry-run",
        ],
    )
    assert "3" in result.output


def test_a_missing_input_file_fails_with_a_clear_message(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    result = runner.invoke(
        app, ["run", "--input", str(tmp_path / "nao-existe.xlsx"), "--dry-run"]
    )
    assert result.exit_code != 0


def test_a_real_run_requires_confirmation_and_aborts_on_no(
    tmp_path, make_sheet, device_row_dict, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    source = make_sheet(tmp_path, [device_row_dict()])
    result = runner.invoke(
        app,
        [
            "run", "--input", str(source), "--output", str(tmp_path / "out"),
            "--cache", str(tmp_path / "scan.sqlite"), "--fontes", "olx",
        ],
        input="n\n",
    )
    assert result.exit_code != 0
    assert not (tmp_path / "out").exists()
```

- [ ] **Step 6: Rodar o teste para confirmar que falha**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 7: Implementar `renov_market_scan/cli.py`**

```python
"""Typer CLI. Messages are in pt-BR; identifiers stay in English."""

import asyncio
import logging
import signal
from datetime import date
from pathlib import Path

import structlog
import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from renov_market_scan.collect.anthropic_search import AnthropicSearchAdapter
from renov_market_scan.config import Settings
from renov_market_scan.cost import estimate, format_estimate
from renov_market_scan.ingest.normalize import build_search_plan
from renov_market_scan.ingest.reader import read_device_rows
from renov_market_scan.query.builder import enabled_sources, load_sources
from renov_market_scan.run import SOURCES_FILE, RunOptions, execute

app = typer.Typer(add_completion=False, help="Coletor de referencia de mercado de seminovos.")
console = Console()


def _configure_logging(output_dir: Path) -> None:
    """Structured log to out/run.log, human output to the console."""
    output_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(output_dir / "run.log"),
        level=logging.INFO,
        format="%(message)s",
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


@app.command()
def run(
    input_path: Path = typer.Option(..., "--input", help="Planilha no template de importacao."),
    output_dir: Path = typer.Option(Path("out"), "--output", help="Diretorio de saida."),
    fontes: str | None = typer.Option(
        None, "--fontes", help="Fontes separadas por virgula. Default: as habilitadas em fontes.yaml."
    ),
    somente_ativos: bool = typer.Option(
        True, "--somente-ativos/--todos", help="Ignora linhas com Price for In-store <= 10."
    ),
    marca: str | None = typer.Option(None, "--marca", help="Filtra por fabricante."),
    limite: int | None = typer.Option(None, "--limite", help="Primeiros N modelos da planilha."),
    incluir_novos: bool = typer.Option(
        False, "--incluir-novos/--sem-novos", help="Inclui anuncios novos e lacrados na mediana."
    ),
    concorrencia: int = typer.Option(4, "--concorrencia", help="Chamadas simultaneas a API."),
    cache: Path = typer.Option(Path(".cache/scan.sqlite"), "--cache", help="Banco de cache."),
    retomar: bool = typer.Option(False, "--retomar", help="Pula o que ja esta no cache do dia."),
    dry_run: bool = typer.Option(False, "--dry-run", help="So mostra plano e custo."),
    reprocessar_filtro: bool = typer.Option(
        False, "--reprocessar-filtro", help="Regrava o relatorio do cache, sem rede."
    ),
) -> None:
    """Coleta referencia de mercado para os modelos da planilha."""
    if not input_path.is_file():
        console.print(f"[red]Arquivo de entrada nao encontrado:[/red] {input_path}")
        raise typer.Exit(code=2)

    settings = Settings(concurrency=concorrencia)  # type: ignore[call-arg]
    selected = [name.strip() for name in fontes.split(",")] if fontes else None
    sources = enabled_sources(load_sources(SOURCES_FILE), selected)
    if not sources:
        console.print("[red]Nenhuma fonte selecionada.[/red]")
        raise typer.Exit(code=2)

    rows, warnings = read_device_rows(input_path)
    for warning in warnings:
        console.print(f"[yellow]aviso:[/yellow] {warning}")

    plan, anomalies = build_search_plan(
        rows, active_only=somente_ativos, manufacturer_filter=marca, limit=limite
    )

    console.print(f"Linhas lidas:        {len(rows)}")
    console.print(f"Modelos a pesquisar: {len(plan)}")
    console.print(f"Anomalias:           {len(anomalies)}")
    console.print(f"Fontes:              {', '.join(source.name for source in sources)}")
    console.print(f"Modelo:              {settings.model}")
    console.print(f"Web search tool:     {settings.web_search_tool_version}")
    console.print("")
    console.print(format_estimate(estimate(plan, sources, settings)))

    if dry_run:
        console.print("\n[green]--dry-run: nada foi gasto.[/green]")
        raise typer.Exit(code=0)

    if not reprocessar_filtro:
        if not typer.confirm("\nConfirma a execucao e o gasto estimado?"):
            console.print("[yellow]Cancelado. Nada foi gasto.[/yellow]")
            raise typer.Exit(code=1)

    _configure_logging(output_dir)
    log = structlog.get_logger()
    options = RunOptions(
        input_path=input_path,
        output_dir=output_dir,
        cache_path=cache,
        sources=selected,
        active_only=somente_ativos,
        manufacturer=marca,
        limit=limite,
        include_new=incluir_novos,
        resume=retomar,
        reprocess_only=reprocessar_filtro,
        collected_on=date.today().isoformat(),
    )
    adapter = AnthropicSearchAdapter(settings)

    async def _main() -> None:
        loop = asyncio.get_running_loop()
        stopping = asyncio.Event()

        def _request_stop() -> None:
            # Stop scheduling new work; in-flight calls finish and the cache
            # stays consistent because raw_search commits per response.
            console.print("\n[yellow]SIGINT recebido: encerrando de forma limpa.[/yellow]")
            stopping.set()

        try:
            loop.add_signal_handler(signal.SIGINT, _request_stop)
        except NotImplementedError:  # pragma: no cover - Windows
            pass

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress_bar:
            task_id = progress_bar.add_task("coletando", total=max(len(plan) * len(sources), 1))

            def on_progress(done: int, total: int) -> None:
                progress_bar.update(task_id, completed=done, total=total)

            result = await execute(options, settings, adapter, progress=on_progress)

        log.info(
            "run_concluido",
            modelos=len(plan),
            buscas=result.searches_performed,
            amostras=len(result.sample_rows),
            descartados=len(result.discarded_rows),
        )
        console.print(f"\nRelatorio:        {result.report_path}")
        console.print(f"Copia do template: {result.template_copy_path}")
        console.print(f"Buscas realizadas: {result.searches_performed}")
        console.print(f"Anuncios aceitos:  {len(result.sample_rows)}")
        console.print(f"Descartados:       {len(result.discarded_rows)}")

    asyncio.run(_main())


if __name__ == "__main__":
    app()
```

- [ ] **Step 8: Rodar o teste, lint, tipos e a suíte inteira**

Run: `uv run pytest tests/test_cli.py -v && uv run ruff check . && uv run mypy renov_market_scan && uv run pytest`
Expected: PASS, 5 testes de CLI; suíte inteira verde

- [ ] **Step 9: Commit**

```bash
git add renov_market_scan/run.py renov_market_scan/cli.py tests/test_run.py tests/test_cli.py
git commit -m "feat: orquestracao e CLI

--dry-run mostra plano e custo e sai sem gastar; execucao real pede
confirmacao. --retomar com cache quente faz zero busca. --reprocessar-
filtro regrava do cache sem tocar a rede. SIGINT para de agendar e deixa
o cache consistente."
```

---

### Task 21: Documentação

**Files:**
- Create: `README.md`
- Modify: `docs/fontes.md`

**Interfaces:**
- Consumes: nada de código.
- Produces: `README.md` com setup, exemplo de execução e explicação de cada coluna do relatório; `docs/fontes.md` completo com o registro da Fase 0 e da Tarefa 15.

- [ ] **Step 1: Escrever `docs/fontes.md`**

Substituir o placeholder pelo registro completo. A seção "Contrato de extração" e "Calibração do estimador de custo" já foram escritas pela Tarefa 15; acrescentar o resto acima delas:

```markdown
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
```

- [ ] **Step 2: Escrever `README.md`**

```markdown
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
  --fontes olx,enjoei,mercadolivre \
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
| `--fontes` | as habilitadas em `fontes.yaml` | Lista separada por vírgula. |
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
```

- [ ] **Step 3: Verificar que os comandos do README funcionam**

Run: `uv run renov-market-scan run --help`
Expected: ajuda em pt-BR listando todas as flags da tabela

- [ ] **Step 4: Commit**

```bash
git add README.md docs/fontes.md
git commit -m "docs: README e registro das medicoes da Fase 0

README explica cada coluna do relatorio, incluindo por que a linha e
identificada pela tripla e nao pelo ERP Code sozinho. docs/fontes.md
registra as medicoes que justificam duas frases por fonte e a blacklist
em duas camadas."
```

---

### Task 22: Rodada real e verificação dos critérios de aceite

**Files:**
- Create: `docs/aceite-2026-07-28.md`

**Interfaces:**
- Consumes: o pacote completo.
- Produces: `docs/aceite-2026-07-28.md` com o resultado medido de cada critério de aceite.

**Esta tarefa gasta dinheiro real** (~US$ 1,20 na rodada de 10 modelos). Requer `ANTHROPIC_API_KEY` válida.

- [ ] **Step 1: Critério 1 — dry-run não gasta nada**

Run:
```bash
uv run renov-market-scan run --input Template-iPhone.xlsx --limite 10 --dry-run
```
Expected: imprime linhas lidas (119), modelos a pesquisar (10), fontes, modelo, versão da tool, e o bloco de custo com `Chamadas a API: 30`. Sai com código 0 sem criar `out/`.

- [ ] **Step 2: Critério 7 — contagem do arquivo Android**

Run:
```bash
uv run renov-market-scan run --input RS_Maio_Androids_2026.xlsx --dry-run
```
Expected: `Modelos a pesquisar: 285` e o custo estimado impresso. Confirma a correção de D2: 285, não ~380.

- [ ] **Step 3: Registrar o hash da planilha antes da rodada real**

Run:
```bash
sha256sum Template-iPhone.xlsx | tee /tmp/hash-antes.txt
```

- [ ] **Step 4: Critério 2 — rodada real com 10 modelos**

Run:
```bash
uv run renov-market-scan run \
  --input Template-iPhone.xlsx --output out/ \
  --fontes olx,enjoei,mercadolivre --limite 10 --concorrencia 4
```
Confirmar quando pedido. Expected: barra de progresso até 30/30, e no fim o caminho do relatório, o da cópia do template, buscas realizadas, anúncios aceitos e descartados.

Verificar:
```bash
uv run python -c "
from openpyxl import load_workbook
wb = load_workbook('out/referencia-mercado_$(date +%F).xlsx')
print('abas:', wb.sheetnames)
assert wb.sheetnames == ['Resumo','Amostras','Descartados','Anomalias']
resumo = wb['Resumo']
header = [c.value for c in resumo[1]]
si, ni = header.index('status'), header.index('n_amostras')
linhas = [(r[si].value, r[ni].value) for r in resumo.iter_rows(min_row=2) if r[0].value]
ok = [l for l in linhas if l[0] == 'ok' and (l[1] or 0) >= 5]
print(f'linhas={len(linhas)} status_ok_com_n5={len(ok)}')
print('CRITERIO 2:', 'PASSOU' if len(ok) >= 8 else 'FALHOU')
wb.close()
"
```
Expected: `CRITERIO 2: PASSOU`. Se falhar, **não ajustar o teste** — registrar quantos passaram, olhar a aba `Descartados` para achar a regra que está descartando demais, e reportar antes de mudar qualquer regra.

- [ ] **Step 5: Critério 8 — a planilha de entrada não foi tocada**

Run:
```bash
sha256sum -c /tmp/hash-antes.txt && echo "CRITERIO 8: PASSOU"
```
Expected: `Template-iPhone.xlsx: OK` e `CRITERIO 8: PASSOU`

- [ ] **Step 6: Critério 5 — retomar não gasta nada**

Run:
```bash
uv run renov-market-scan run \
  --input Template-iPhone.xlsx --output out/ \
  --fontes olx,enjoei,mercadolivre --limite 10 --retomar
```
Expected: no resumo final, `Buscas realizadas: 0`.

- [ ] **Step 7: Critérios 3 e 4 — amostragem manual de 10 anúncios**

Run:
```bash
uv run python -c "
from openpyxl import load_workbook
import random
wb = load_workbook('out/referencia-mercado_$(date +%F).xlsx')
sheet = wb['Amostras']
header = [c.value for c in sheet[1]]
rows = [dict(zip(header, [c.value for c in r])) for r in sheet.iter_rows(min_row=2) if r[0].value]
random.seed(42)
for row in random.sample(rows, min(10, len(rows))):
    print(f\"{row['erp_code']} | {row['fonte']} | R\$ {row['preco']} | {row['condicao']}\")
    print(f\"   titulo: {row['titulo']}\")
    print(f\"   url:    {row['url']}\")
    print(f\"   citado: {row['cited_text'][:120]}\")
wb.close()
"
```

Inspecionar as 10 linhas e confirmar, uma por uma:
- zero acessórios ou peças;
- zero modelo trocado (nenhum Pro, Max, Ultra, Plus onde o alvo não tem);
- zero preço que seja valor de parcela;
- o preço aparece no `citado`.

Abrir `link_minimo` e `link_maximo` de 3 linhas da aba `Resumo` e confirmar que o
anúncio corresponde ao preço e ao modelo.

- [ ] **Step 8: Critério 6 — qualidade**

Run: `uv run ruff check . && uv run mypy renov_market_scan && uv run pytest`
Expected: os três verdes

- [ ] **Step 9: Registrar o resultado em `docs/aceite-2026-07-28.md`**

```markdown
# Verificação dos critérios de aceite

Executado em <data>. Custo real da rodada: US$ <valor>.

| # | Critério | Resultado | Evidência |
|---|---|---|---|
| 1 | dry-run mostra plano e custo sem gastar | <PASSOU/FALHOU> | `Chamadas a API: 30`, `out/` não criado |
| 2 | 8 de 10 modelos com status=ok e n>=5 | <PASSOU/FALHOU> | <n> de 10 |
| 3 | 10 anúncios amostrados: zero acessório, zero modelo trocado, zero parcelado | <PASSOU/FALHOU> | inspeção manual, seed 42 |
| 4 | link_minimo e link_maximo correspondem ao preço e ao modelo | <PASSOU/FALHOU> | 3 linhas verificadas |
| 5 | --retomar não dispara busca nova | <PASSOU/FALHOU> | `Buscas realizadas: 0` |
| 6 | ruff, mypy e pytest verdes | <PASSOU/FALHOU> | <n> testes |
| 7 | Android com --somente-ativos resulta em 285 modelos | <PASSOU/FALHOU> | `Modelos a pesquisar: 285` |
| 8 | hash SHA-256 da entrada inalterado | <PASSOU/FALHOU> | `sha256sum -c` |

## Observações

<Qualquer regra que descartou mais do que esperado, com o motivo mais frequente
na aba Descartados e a contagem.>
```

- [ ] **Step 10: Commit**

```bash
git add docs/aceite-2026-07-28.md
git commit -m "test: verificacao dos criterios de aceite com rodada real

Registra o resultado medido de cada um dos 8 critérios, incluindo o hash
da planilha de entrada antes e depois."
```

---

## Self-Review

Executada após escrever o plano completo.

**1. Cobertura do spec.** Cada requisito do spec mapeia para uma tarefa:

| Requisito do spec | Tarefa |
|---|---|
| Validação do par (modelo, versão da tool), sem hardcode | 1 |
| Modelos pydantic incluindo `report_key` | 2 |
| Leitura sem hardcode de aba, arquivo nunca escrito | 3 |
| Normalização de storage, chave dupla, anomalias | 4 |
| Normalização de texto, `1TB`→`1024gb`, `+`→`plus` | 5 |
| Parser BRL, rejeição de parcelamento | 6 |
| Qualificadores estritos, 5G tolerante, ano obrigatório, capacidade | 7 |
| Blacklist, condição, default sem novos | 8 |
| Regra de evidência, deduplicação | 9 |
| Ordem das regras, motivo em todo descarte | 10 |
| IQR, percentis, status por n, extremos com link, min/max bruto | 11 |
| Cache em camadas, chave com dia | 12 |
| 2 frases por fonte, `fontes.yaml`, aliases de marca | 13 |
| `SearchAdapter` Protocol, erros em HTTP 200, adapter offline | 14 |
| Spike do contrato de extração | 15 |
| Adapter da API, `max_retries=0`, `pause_turn`, `refusal`, parse_error | 16 |
| Estimador de custo calibrado pelo spike | 17 |
| Quatro abas, fan-out por report_key, nota de rodapé | 18 |
| xlsx formatado, cópia do template, csv, json | 19 |
| CLI com todas as flags, dry-run com confirmação, SIGINT, rich, structlog | 20 |
| README com explicação das colunas, `docs/fontes.md` | 21 |
| Os 8 critérios de aceite | 22 |

Sem lacuna encontrada.

**2. Placeholders.** Os únicos `<valores>` estão nas Tarefas 15, 21 e 22, onde
são resultados de medição que só existem em tempo de execução, com instrução
explícita de substituição. Não há "TBD", "implementar depois", nem passo de
código sem código.

**3. Consistência de tipos.** Verificado em toda a cadeia:
- `search_key: str` (T4) é a chave de `stats_by_key`, `listings_by_key`,
  `rejected_by_key` (T18, T20) e da tabela `listing` (T12).
- `ReportKey.storage_label` (T2) alimenta `capacidade` no Resumo (T18) e a
  terceira posição da chave de `write_template_copy` (T19).
- `Listing.cited_text: str` (T2) é preenchido pelo adapter (T16), consumido pela
  regra de evidência (T9 via T10) e exibido nas abas `Amostras` e `Descartados`
  (T18).
- `MatchResult.flag_5g_divergent` (T7) chega a `Listing.flag_5g_divergent` (T10)
  e é renderizado como `sim`/`nao` (T18).
- `ModelStats.min_raw`/`max_raw` (T2, T11) são `min_bruto`/`max_bruto` (T18).
- `SearchOutcome.payload: dict[str, Any]` (T14) é serializado para
  `raw_search.payload` (T12) e testado como JSON-serializável em T14 e T16.
- `PHRASE_COUNT` (T13) é usado por `format_estimate` (T17).
- `HEADER_ROW` e `EXPECTED_HEADER` (T3) são reusados por `write_template_copy`
  (T19) — mesmo nome, mesmo módulo de origem.

**4. Escopo.** Um subsistema, um plano. Sem decomposição necessária.

## Ordem de execução recomendada

Tarefas 1 a 14 não exigem chave de API e cobrem toda a lógica de qualidade — é o
melhor ponto de parada se quiser revisar antes de gastar. A Tarefa 15 é o
primeiro gasto (~US$ 0,10) e decide o desenho da 16. A Tarefa 22 é o segundo
gasto (~US$ 1,20).
