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
    return Settings()


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
    asyncio.run(
        execute(options(tmp_path, source), settings(), FixtureAdapter({(key, "olx"): canned(key)}))
    )

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
        search_key=key,
        source="olx",
        title="Capa capinha Galaxy A17 128GB",
        price_brl=30.0,
        condition="desconhecido",
        url="https://olx.com.br/c-1",
        captured_at=f"{DAY}T10:00:00",
        cited_text="R$ 30,00",
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
    assert adapter.call_count == 2


def test_progress_callback_is_invoked_once_per_call(tmp_path, make_sheet, device_row_dict):
    rows = [
        device_row_dict(**{"Model*": f"GALAXY A{index}", "ERP Code": f"E{index}"})
        for index in range(3)
    ]
    source = make_sheet(tmp_path, rows)
    seen: list[tuple[int, int]] = []

    def on_progress(done: int, total: int) -> None:
        seen.append((done, total))

    asyncio.run(
        execute(options(tmp_path, source), settings(), FixtureAdapter({}), progress=on_progress)
    )
    assert len(seen) == 3
    assert seen[-1] == (3, 3)
