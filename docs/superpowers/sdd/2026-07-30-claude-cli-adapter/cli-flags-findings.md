# Claude CLI flags — confirmed 2026-07-30

Confirmed against installed `claude --version`: `2.1.220 (Claude Code)`.
Flags vary between CLI versions — re-confirm with `claude --help` if the
installed version differs materially from this one.

## Headless prompt mode

Flag: `-p, --print` — "Print response and exit (useful for pipes)". The
prompt is passed either as a positional `[prompt]` argument or via stdin
when no positional argument is given (confirmed by `claude --help`'s
`Arguments: prompt  Your prompt`, combined with the documented stdin
support for `--input-format text`, the default).

## Output format

Flag: `--output-format <format>` — only works with `--print`. Values:
`"text"` (default), `"json"` (single result), `"stream-json"`. Use
`--output-format json`.

Envelope shape: not independently re-derived in this pass (no live
`-p --output-format json` call was made to avoid unnecessary spend during
flag discovery) — the plan's assumed shape (`result`, `session_id`,
`is_error`, `num_turns`) matches the shape already used and tested against
in `renov_market_scan/collect/anthropic_search.py`'s sibling script
`run_market_scan.py` (which parses `envelope.get("result")` and
`envelope.get("is_error")`, `envelope.get("session_id")`,
`envelope.get("num_turns")`). Task 2/3's implementer should treat this as
carried over, not independently re-verified — if `--output-format json`'s
actual envelope differs, this will surface immediately as a JSON parse
failure in Task 3's manual smoke test (Task 6, Step 3) or in the first
real acceptance run, not silently.

## Tool allowlist

Flag: `--allowedTools, --allowed-tools <tools...>` — "Comma or
space-separated list of tool names to allow (e.g. 'Bash(git *) Edit')".
Use `--allowedTools WebSearch,WebFetch`.

## Other flags used

- **Max turns**: **no `--max-turns` flag exists in this CLI version.**
  The plan's original brief (and `run_market_scan.py`) assumed one; it is
  not present in `claude --help`'s output for `2.1.220`. Task 2/3's
  implementer must **drop `--max-turns` from the subprocess command
  entirely** — do not pass a flag that does not exist. Turn budget in this
  version is controlled by `--effort` (reasoning effort, not turn count) and
  natural task completion; there is no direct equivalent. This is a
  deviation from the plan text and is called out explicitly here so the
  implementer does not silently drop it without noting why.
- **Model**: `--model <model>` — "Provide an alias for the latest model
  (e.g. 'fable', 'opus', or 'sonnet') or a model's full name (e.g.
  'claude-fable-5')." `settings.model` already stores a full name
  (`claude-sonnet-5`), which this flag accepts directly.

## `claude auth status`

Command is `claude auth status` (a subcommand of `auth`, not a top-level
flag — confirmed via `claude auth --help`, which lists `status [options]
Show authentication status`).

Exit code when logged in: **0**, confirmed live:

```
$ claude auth status
{
  "loggedIn": true,
  "authMethod": "claude.ai",
  "apiProvider": "firstParty",
  "apiKeySource": "ANTHROPIC_API_KEY",
  "email": "...",
  "orgId": "...",
  "orgName": "...",
  "subscriptionType": "team"
}
exit code: 0
```

**Critical finding, directly relevant to `child_env()`/preflight design in
Task 2/3:** with `ANTHROPIC_API_KEY` present in the environment, `auth
status` reports `"apiKeySource": "ANTHROPIC_API_KEY"` — i.e., the CLI is
actively using the env var key, not the browser session, even though the
browser session is also valid. Re-running with the var unset:

```
$ env -u ANTHROPIC_API_KEY claude auth status
{
  "loggedIn": true,
  "authMethod": "claude.ai",
  "apiProvider": "firstParty",
  "email": "...",
  "orgId": "...",
  "orgName": "...",
  "subscriptionType": "team"
}
exit code: 0
```

`apiKeySource` disappears and the session remains `loggedIn: true` via
`authMethod: "claude.ai"`. This **confirms** the plan's `child_env()`
requirement is load-bearing, not defensive boilerplate: without stripping
`ANTHROPIC_API_KEY` from the subprocess environment, `claude -p` would
silently bill the API key instead of consuming the subscription's usage
window, even while `auth status` reports success either way. Task 2/3's
`preflight()`/`child_env()` must run `claude auth status` through
`child_env()` (API-key-stripped), not through the raw parent environment —
otherwise the preflight check itself would pass even in a misconfigured
environment, defeating its purpose.

Exit code when logged out: **not tested** — deliberately not exercised in
this pass, since doing so would require actually logging out of the
real, active team subscription (`orgName: "Pitzi"`, `subscriptionType:
"team"`) used for this session, which is a disruptive, hard-to-reverse
action out of scope for a flag-discovery task. Assumed per CLI convention
(and the plan's own text) to be non-zero. **Task 2's implementer must not
assume a specific non-zero value** — the test suite (per the plan) only
asserts `probe.returncode != 0` triggers the failure path, never a
specific code, so this gap does not block implementation. If this
assumption turns out to be wrong in a future real run, `preflight()` will
fail closed (raise `RuntimeError`) rather than silently proceeding
unauthenticated, since the check is `!= 0`, not `== 1`.

## `--max-turns` deviation carried into Task 2/3

Summary for the implementer: build the subprocess command as

```python
cmd = [
    "claude", "-p",
    "--output-format", "json",
    "--allowedTools", "WebSearch,WebFetch",
    "--model", self._settings.model,
]
```

with no `--max-turns` argument. The plan's Task 3 code sample includes
`MAX_TURNS`/`"--max-turns", str(MAX_TURNS)` — drop that line and the
constant, and note the removal in the Task 3 report as a deviation
resolved by this findings file, not a silent change.
