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


def test_process_batch_cached_pending_split(tmp_path):
    """Cobre o núcleo do loop de lotes do main(): split cache/pendente,
    chave de cache correta mesmo com erp_code mal formatado pelo modelo
    (Finding 1), resultado vazio não é cacheado (Finding 2), e erp_code
    sem correspondência não quebra nem cacheia sob chave errada (Finding 1).
    """
    from datetime import date
    from run_market_scan import Stats, compute_stats, device_cache_path, process_batch

    current_week = date(2026, 8, 1)

    device_cached = make_device(erp="20023A0", row=3, name="CACHED DEVICE")
    device_fresh_empty = make_device(erp="30099B1", row=4, name="FRESH EMPTY")
    device_fresh_ok = make_device(erp="ab/cd 12", row=5, name="FRESH OK")
    device_unmatched_target = make_device(erp="99999Z9", row=6, name="UNMATCHED TARGET")

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # device_cached já tem cache desta semana -> não deve disparar claude -p pra ele.
    cached_entry = {
        "anuncios": [{"preco_brl": 900.0, "url": "https://x", "fonte": "trocafy"}],
        "fontes_consultadas": ["trocafy"],
    }
    device_cache_path(cache_dir, device_cached.erp, current_week).write_text(
        json.dumps(cached_entry), encoding="utf-8"
    )

    batch = [device_cached, device_fresh_empty, device_fresh_ok, device_unmatched_target]

    fake_payload = {
        "resultados": [
            # erp_code com casing/formatação diferente da planilha (Finding 1):
            # deve casar com device_fresh_ok via forma normalizada e usar
            # device_fresh_ok.erp como chave, não a string devolvida.
            {"erp_code": "AB-CD-12", "anuncios": [
                {"preco_brl": 500.0, "url": "https://y", "fonte": "trocafy"}
            ], "fontes_consultadas": ["trocafy"]},
            # resultado vazio -> não deve ser cacheado (Finding 2).
            {"erp_code": device_fresh_empty.erp, "anuncios": [], "fontes_consultadas": ["trocafy"]},
            # erp_code que não corresponde a nenhum device pendente -> descartado,
            # sem crash, sem cache sob chave errada (Finding 1).
            {"erp_code": "NAO-EXISTE-NO-LOTE", "anuncios": [
                {"preco_brl": 999.0, "url": "https://z", "fonte": "trocafy"}
            ], "fontes_consultadas": ["trocafy"]},
        ],
        "_meta": {"session_id": "s1", "input_tokens": 1, "output_tokens": 1},
    }

    with patch("run_market_scan.run_claude_batch", return_value=fake_payload) as mocked:
        results, meta, called_claude = process_batch(
            batch, cache_dir, current_week, model="claude-sonnet-5", timeout=900,
        )
        # (a) device já cacheado não deve ir para a chamada do claude -p.
        called_devices = mocked.call_args[0][0]
        assert device_cached not in called_devices
        assert {d.erp for d in called_devices} == {
            device_fresh_empty.erp, device_fresh_ok.erp, device_unmatched_target.erp,
        }

    assert called_claude is True
    assert meta == fake_payload["_meta"]

    # device_cached: veio do cache, presente nos resultados.
    assert results[device_cached.erp].n == 1

    # device_fresh_ok: chave correta é a do Device (planilha), não "AB-CD-12".
    assert device_fresh_ok.erp in results
    assert results[device_fresh_ok.erp].n == 1
    assert "AB-CD-12" not in results

    # (b) device_fresh_empty: resultado vazio processado, mas SEM cache escrito.
    assert results[device_fresh_empty.erp].status == "sem_dados"
    assert not device_cache_path(cache_dir, device_fresh_empty.erp, current_week).exists()

    # (c) device_unmatched_target: nunca recebeu resposta -> não está em results
    # nem tem arquivo de cache (nem sob a chave certa, nem sob "NAO-EXISTE-NO-LOTE").
    assert device_unmatched_target.erp not in results
    assert not device_cache_path(cache_dir, device_unmatched_target.erp, current_week).exists()
    assert not device_cache_path(cache_dir, "NAO-EXISTE-NO-LOTE", current_week).exists()

    # Cache foi de fato gravado em disco para device_fresh_ok, sob a chave certa.
    written = device_cache_path(cache_dir, device_fresh_ok.erp, current_week)
    assert written.exists()
    written_entry = json.loads(written.read_text(encoding="utf-8"))
    assert written_entry["erp_code"] == "AB-CD-12"  # conteúdo é o que o modelo mandou
