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
