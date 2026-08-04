import csv
import json
from unittest.mock import MagicMock, patch

import pytest

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


def test_build_prompt_stays_under_token_budget():
    from run_market_scan import build_prompt

    devices = [make_device(erp=f"E{i}", row=i) for i in range(8)]
    prompt = build_prompt(devices)
    # aproximação grosseira (chars/4) só pra travar regressão de tamanho;
    # a medição real de tokens vem do usage.input_tokens na Task 5/8.
    approx_tokens = len(prompt) / 4
    assert approx_tokens < 3000, f"~{approx_tokens:.0f} tokens, meta é <3000"


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
