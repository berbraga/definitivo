"""Production adapter: drives the Claude Code CLI (`claude -p`) instead of the
Messages API. Authenticated by the CLI's own session (browser login or
CLAUDE_CODE_OAUTH_TOKEN), never by an API key.

cited_text produced by this adapter is the model's own self-report of what it
read — not an API-verified citation. The Messages API's citation mechanism
(cryptographically tied to search results) has no equivalent exposed through
`claude -p`'s WebSearch tool, so this is a known, accepted reduction in
evidence strength relative to AnthropicSearchAdapter.
"""

import json
import os
import re
import shutil
import subprocess
from typing import Any

PLAN_LIMIT_PATTERNS: tuple[str, ...] = ("usage limit", "rate limit", "try again")


def child_env() -> dict[str, str]:
    """Subprocess environment: CLI session only, API key vars stripped."""
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    return env


def preflight() -> None:
    """Fail loudly before any subprocess is spawned for real work."""
    if shutil.which("claude") is None:
        raise RuntimeError(
            "'claude' nao encontrado no PATH. Instale o Claude Code CLI."
        )
    probe = subprocess.run(
        ["claude", "auth", "status"],
        env=child_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if probe.returncode != 0:
        raise RuntimeError(
            "Claude CLI nao autenticado.\n"
            "  Maquina com navegador: rode 'claude' e faca /login.\n"
            "  Servidor sem navegador: gere o token numa maquina com navegador\n"
            "  com 'claude setup-token' e exporte CLAUDE_CODE_OAUTH_TOKEN."
        )


def extract_json(text: str) -> dict[str, Any]:
    """Parse the model's JSON answer, tolerating a markdown fence around it.

    The model is instructed to answer with bare JSON but sometimes wraps it
    in ```json ... ``` anyway.
    """
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"resposta sem JSON reconhecivel: {stripped[:300]}")
    return dict(json.loads(stripped[start : end + 1]))
