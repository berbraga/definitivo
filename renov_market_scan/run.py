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
        return _emit(
            plan,
            anomalies,
            stats_by_key,
            listings_by_key,
            rejected_by_key,
            options,
            searches_performed,
        )
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
