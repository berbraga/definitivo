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
            # active_only=True is the operational plan (what actually gets
            # searched): suspicious storage is always excluded from it. In
            # the full "todos" listing (active_only=False) the row still
            # shows up as its own plan entry (keyed off its raw, suspicious
            # label) alongside the Anomaly, so the full-listing count
            # reflects every distinct unit in the sheet.
            if active_only:
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
