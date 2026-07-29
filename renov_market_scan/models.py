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
