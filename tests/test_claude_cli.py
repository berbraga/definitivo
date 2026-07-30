import asyncio
import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from renov_market_scan.collect.claude_cli import ClaudeCliAdapter, child_env, preflight
from renov_market_scan.config import Settings
from renov_market_scan.models import Query


def test_preflight_raises_when_claude_is_not_on_path():
    with (
        patch("shutil.which", return_value=None),
        pytest.raises(RuntimeError, match="claude"),
    ):
        preflight()


def test_preflight_raises_when_auth_status_is_not_zero():
    fake_result = MagicMock(returncode=1, stdout="", stderr="not logged in")
    with (
        patch("shutil.which", return_value="/usr/local/bin/claude"),
        patch("subprocess.run", return_value=fake_result),
        pytest.raises(RuntimeError, match="autenticado"),
    ):
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


def test_a_timeout_is_a_subprocess_error():
    with (
        patch("shutil.which", return_value="/usr/local/bin/claude"),
        patch(
            "subprocess.run",
            side_effect=[
                MagicMock(returncode=0, stdout="Logged in", stderr=""),
                subprocess.TimeoutExpired(cmd=["claude"], timeout=900),
            ],
        ),
    ):
        adapter = ClaudeCliAdapter(make_settings())
        outcome = asyncio.run(adapter.search([QUERY]))
    assert outcome.status == "erro_subprocess"
    assert outcome.listings == []


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
