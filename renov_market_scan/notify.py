"""Slack notification for run outcomes. Sent from Python, never from the
model — if a run dies mid-way, this is exactly when a message is needed,
and the model will not be alive to send one."""

import json
import os
import sys
import urllib.error
import urllib.request


def notify_slack(text: str) -> None:
    """Post text to the incoming webhook in SLACK_WEBHOOK_URL, if set.

    Never raises: a broken webhook must not mask the real run outcome that
    the caller is trying to report.
    """
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        print("[slack] SLACK_WEBHOOK_URL nao definida, pulando notificacao.", file=sys.stderr)
        return
    body = json.dumps({"text": text}).encode()
    try:
        request = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            print(f"[slack] enviado ({response.status})", file=sys.stderr)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"[slack] FALHOU: {exc}", file=sys.stderr)
