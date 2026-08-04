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
