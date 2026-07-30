from unittest.mock import MagicMock, patch

from renov_market_scan.notify import notify_slack


def test_notify_slack_posts_to_the_webhook_url(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.example/abc")
    fake_response = MagicMock()
    fake_response.status = 200
    fake_response.__enter__ = MagicMock(return_value=fake_response)
    fake_response.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=fake_response) as urlopen_mock:
        notify_slack("hello")
    assert urlopen_mock.call_count == 1
    request = urlopen_mock.call_args[0][0]
    assert request.full_url == "https://hooks.slack.example/abc"


def test_notify_slack_does_nothing_without_a_webhook_url(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    with patch("urllib.request.urlopen") as urlopen_mock:
        notify_slack("hello")
    assert urlopen_mock.call_count == 0


def test_notify_slack_never_raises_when_the_request_fails(monkeypatch):
    import urllib.error

    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.example/abc")
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        notify_slack("hello")  # must not raise


def test_notify_slack_never_raises_on_a_bare_timeout_error(monkeypatch):
    # TimeoutError is NOT a subclass of urllib.error.URLError, so it must be
    # caught separately (it is a subtype of OSError).
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.example/abc")
    with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        notify_slack("hello")  # must not raise


def test_notify_slack_never_raises_on_a_malformed_webhook_url(monkeypatch):
    # A malformed URL raises ValueError out of Request(...) construction,
    # which must happen inside the try block to be swallowed.
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "not a valid url with spaces")
    notify_slack("hello")  # must not raise
