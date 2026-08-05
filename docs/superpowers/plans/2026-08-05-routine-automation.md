# Automação do Market Scan como Claude Code Routine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatizar a execução de `run_market_scan.py` como Claude Code Routine semanal: ao final de uma rodada bem-sucedida, commitar e enviar (push) o xlsx atualizado para `feat/market-scan`, e enviar o xlsx como anexo real no canal Slack `pricing-trade-in` com um resumo da rodada.

**Architecture:** Duas funções novas adicionadas ao fim do fluxo existente em `run_market_scan.py` — `git_commit_and_push()` e uma extensão de `notify_slack()` que sobe o arquivo via Slack Web API (upload real, não só texto de webhook). Chamadas em `main()` nesta ordem: gera xlsx (já existe) → commit+push → Slack. Falha no commit/push impede a notificação Slack. Um novo `docs/routine.md` documenta comando, frequência e variáveis de ambiente para quem configurar a Routine.

**Tech Stack:** Python 3.11, stdlib apenas (`subprocess`, `urllib.request`, `json`) — sem novas dependências de terceiros, seguindo o padrão já usado no arquivo (a função `notify_slack` já existente usa `urllib.request` puro, não `requests`/`slack_sdk`).

## Global Constraints

- Nenhuma mudança na arquitetura de busca: `PROMPT_TEMPLATE`, `run_claude_batch`, `process_batch` permanecem intocados.
- Sem guardrails de aborto automático (teto de gasto, kill switch) — fora de escopo por decisão explícita do usuário.
- Commit direto na branch `feat/market-scan`, sem PR intermediário.
- Nenhuma credencial em código ou commit: `SLACK_BOT_TOKEN` e credencial de push do git só via variável de ambiente.
- Falha no Slack (rede/API) não derruba a rodada nem impede o commit já feito — só é logada.
- Falha no commit/push interrompe antes do Slack ser chamado.
- Zero dependências novas de terceiros — usar `urllib.request`/`subprocess`/`json` da stdlib, como o resto do arquivo já faz.
- Testes não tocam rede real nem git real: mockar `subprocess.run` e `urllib.request.urlopen`, seguindo o padrão já estabelecido em `tests/test_run_market_scan.py` e `tests/test_claude_cli.py`.

---

## Contexto do arquivo existente

`run_market_scan.py` já tem uma função `notify_slack(text: str) -> None` (linha ~439) que envia só texto via `SLACK_WEBHOOK_URL` (webhook simples). Essa função **será substituída** por uma versão que sobe o arquivo xlsx como anexo real via Slack Web API, usando `SLACK_BOT_TOKEN` (bot token, escopos `files:write`/`chat:write`) em vez do webhook. As duas chamadas existentes a `notify_slack(...)` em `main()` (sucesso, `KeyboardInterrupt`, exceção genérica) precisam ser ajustadas para a nova assinatura.

A API relevante do Slack (sem SDK, só HTTP):
1. `POST https://slack.com/api/files.getUploadURLExternal` (form-encoded: `filename`, `length`) com header `Authorization: Bearer <token>` → devolve `upload_url` e `file_id`.
2. `POST <upload_url>` com o conteúdo binário do arquivo no corpo (multipart) → sobe o arquivo.
3. `POST https://slack.com/api/files.completeUploadExternal` (JSON: `files: [{"id": file_id, "title": ...}]`, `channel_id` ou `channels`, `initial_comment`) com o mesmo header → finaliza e posta no canal.

---

## Task 1: `git_commit_and_push` — commit e push automático do xlsx

**Files:**
- Modify: `run_market_scan.py` (nova função, seção "Slack" renomeada/reorganizada como "Slack e Git" ou seção nova "Git" antes da seção Slack)
- Test: `tests/test_run_market_scan.py`

**Interfaces:**
- Produces: `git_commit_and_push(xlsx_path: Path, branch: str = "feat/market-scan") -> None` — levanta `RuntimeError` se `git add`, `git commit`, ou `git push` falhar (código de saída != 0). Não retorna nada em sucesso.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_run_market_scan.py — adicionar ao final do arquivo

from pathlib import Path
from run_market_scan import git_commit_and_push


def test_git_commit_and_push_runs_add_commit_push_in_order():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        git_commit_and_push(Path("out/referencia-mercado_2026-08-05.xlsx"))

    assert calls[0][:2] == ["git", "add"]
    assert calls[1][:2] == ["git", "commit"]
    assert calls[2][:2] == ["git", "push"]
    assert "feat/market-scan" in calls[2]


def test_git_commit_and_push_raises_on_add_failure():
    fake_proc = MagicMock(returncode=1, stdout="", stderr="fatal: pathspec")
    with patch("subprocess.run", return_value=fake_proc):
        with pytest.raises(RuntimeError, match="git add"):
            git_commit_and_push(Path("out/referencia-mercado_2026-08-05.xlsx"))


def test_git_commit_and_push_raises_on_push_failure():
    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        if cmd[:2] == ["git", "push"]:
            return MagicMock(returncode=1, stdout="", stderr="rejected")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="git push"):
            git_commit_and_push(Path("out/referencia-mercado_2026-08-05.xlsx"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_run_market_scan.py -k git_commit_and_push -v`
Expected: FAIL with `ImportError: cannot import name 'git_commit_and_push'`

- [ ] **Step 3: Write minimal implementation**

Add to `run_market_scan.py`, right before the `# Slack` section header:

```python
# --------------------------------------------------------------------------
# Git — commit e push automático do resultado
# --------------------------------------------------------------------------

def git_commit_and_push(xlsx_path: Path, branch: str = "feat/market-scan") -> None:
    """Commita e envia o xlsx gerado direto na branch ativa.

    Sem PR intermediário — decisão explícita para esta automação. Levanta
    RuntimeError na primeira falha; quem chama decide se prossegue para o
    Slack (não deve, numa rodada real: commit falho = nada para notificar).
    """
    def _run(cmd: list[str], step_name: str) -> None:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(
                f"{step_name} falhou (código {proc.returncode}): "
                f"{(proc.stderr or proc.stdout or '').strip()[:500]}"
            )

    _run(["git", "add", str(xlsx_path)], "git add")
    _run(
        ["git", "commit", "-m", f"chore(scan): atualiza referência de mercado {xlsx_path.stem.split('_')[-1]}"],
        "git commit",
    )
    _run(["git", "push", "origin", branch], "git push")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_run_market_scan.py -k git_commit_and_push -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add run_market_scan.py tests/test_run_market_scan.py
git commit -m "feat(scan): adiciona commit e push automático do xlsx gerado"
```

---

## Task 2: Slack file upload — substituir `notify_slack` por upload real de arquivo

**Files:**
- Modify: `run_market_scan.py:439-451` (função `notify_slack` existente) e as 3 chamadas em `main()`
- Test: `tests/test_run_market_scan.py`

**Interfaces:**
- Consumes: nada de tasks anteriores (independente de Task 1, mas ambas compõem a Task 3)
- Produces: `notify_slack(xlsx_path: Path | None, text: str, channel: str = "pricing-trade-in") -> None`. Quando `xlsx_path` é `None` (usado nos caminhos de erro/interrupção, onde pode não haver arquivo consolidado), envia só uma mensagem de texto via `chat.postMessage`. Quando `xlsx_path` existe, sobe o arquivo com `text` como `initial_comment`. Não levanta exceção — loga falha e retorna, igual ao comportamento atual.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_run_market_scan.py — adicionar ao final do arquivo

from run_market_scan import notify_slack


def test_notify_slack_posts_text_only_when_no_file(monkeypatch, capsys):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake")
    calls = []

    def fake_urlopen(req, timeout=15):
        calls.append(req.full_url)
        response = MagicMock()
        response.read.return_value = b'{"ok": true}'
        response.status = 200
        return response.__enter__() if hasattr(response, "__enter__") else response

    class FakeCtx:
        def __enter__(self):
            resp = MagicMock()
            resp.read.return_value = b'{"ok": true}'
            resp.status = 200
            return resp

        def __exit__(self, *a):
            return False

    with patch("urllib.request.urlopen", return_value=FakeCtx()):
        notify_slack(None, "rodada ok")

    out = capsys.readouterr().out
    assert "enviado" in out.lower() or "slack" in out.lower()


def test_notify_slack_skips_when_token_missing(monkeypatch, capsys):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    notify_slack(None, "rodada ok")
    out = capsys.readouterr().out
    assert "SLACK_BOT_TOKEN" in out


def test_notify_slack_uploads_file_when_xlsx_path_given(monkeypatch, tmp_path):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake")
    xlsx = tmp_path / "referencia-mercado_2026-08-05.xlsx"
    xlsx.write_bytes(b"fake xlsx bytes")

    responses = [
        b'{"ok": true, "upload_url": "https://upload.example/put", "file_id": "F123"}',
        b'{"ok": true}',  # resposta do PUT no upload_url
        b'{"ok": true, "files": [{"id": "F123"}]}',  # completeUploadExternal
    ]
    call_urls = []

    class FakeCtx:
        def __init__(self, body):
            self._body = body

        def __enter__(self):
            resp = MagicMock()
            resp.read.return_value = self._body
            resp.status = 200
            return resp

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=15):
        call_urls.append(req.full_url)
        return FakeCtx(responses.pop(0))

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        notify_slack(xlsx, "rodada ok")

    assert "files.getUploadURLExternal" in call_urls[0]
    assert call_urls[1] == "https://upload.example/put"
    assert "files.completeUploadExternal" in call_urls[2]


def test_notify_slack_logs_failure_without_raising(monkeypatch, capsys):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake")
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
        notify_slack(None, "rodada ok")  # não deve levantar
    err = capsys.readouterr().err
    assert "FALHOU" in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_run_market_scan.py -k notify_slack -v`
Expected: FAIL — assinatura antiga de `notify_slack` só aceita 1 argumento posicional (`text`); chamadas com `xlsx_path` quebram com `TypeError`.

- [ ] **Step 3: Write minimal implementation**

Replace the existing `notify_slack` function (`run_market_scan.py:439-451`) with:

```python
# --------------------------------------------------------------------------
# Slack — upload do resultado via Bot Token (Web API, sem SDK externo)
# --------------------------------------------------------------------------

SLACK_API = "https://slack.com/api"


def _slack_api_call(method: str, token: str, data: bytes, content_type: str) -> dict:
    req = urllib.request.Request(
        f"{SLACK_API}/{method}",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def notify_slack(xlsx_path: Path | None, text: str, channel: str = "pricing-trade-in") -> None:
    """Envia o resumo da rodada ao Slack, com o xlsx anexado quando houver.

    Sem xlsx_path (erro/interrupção, sem arquivo consolidado): só mensagem de
    texto via chat.postMessage. Com xlsx_path: upload real do arquivo via
    Web API (getUploadURLExternal -> PUT do binário -> completeUploadExternal),
    já que um webhook simples não sobe arquivo, só texto.
    """
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print("[slack] SLACK_BOT_TOKEN não definida — pulando notificação.")
        return

    try:
        if xlsx_path is None:
            body = json.dumps({"channel": channel, "text": text}).encode()
            result = _slack_api_call("chat.postMessage", token, body, "application/json")
            if not result.get("ok"):
                raise RuntimeError(str(result))
            print(f"[slack] mensagem enviada ao canal {channel}")
            return

        file_bytes = xlsx_path.read_bytes()
        meta = _slack_api_call(
            "files.getUploadURLExternal", token,
            f"filename={xlsx_path.name}&length={len(file_bytes)}".encode(),
            "application/x-www-form-urlencoded",
        )
        if not meta.get("ok"):
            raise RuntimeError(str(meta))

        upload_req = urllib.request.Request(
            meta["upload_url"], data=file_bytes, method="POST",
        )
        with urllib.request.urlopen(upload_req, timeout=60) as resp:
            resp.read()

        complete_body = json.dumps({
            "files": [{"id": meta["file_id"], "title": xlsx_path.name}],
            "channel_id": channel,
            "initial_comment": text,
        }).encode()
        result = _slack_api_call(
            "files.completeUploadExternal", token, complete_body, "application/json",
        )
        if not result.get("ok"):
            raise RuntimeError(str(result))
        print(f"[slack] arquivo {xlsx_path.name} enviado ao canal {channel}")

    except (urllib.error.URLError, RuntimeError) as exc:
        print(f"[slack] FALHOU: {exc}", file=sys.stderr)
```

Update the three call sites in `main()`:

```python
# sucesso (era: notify_slack(summary))
notify_slack(out_xlsx, summary)

# KeyboardInterrupt (era: notify_slack(f":warning: ..."))
notify_slack(None, f":warning: Rodada interrompida manualmente "
             f"({len(results)} dispositivos já processados, checkpoint salvo).")

# exceção genérica (era: notify_slack(f":rotating_light: ..."))
notify_slack(None, f":rotating_light: Rodada ABORTOU: `{exc}`\n"
             f"Checkpoint em `{cache_dir}` — rode de novo para retomar.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_run_market_scan.py -k notify_slack -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add run_market_scan.py tests/test_run_market_scan.py
git commit -m "feat(scan): envia xlsx como anexo real no Slack via bot token"
```

---

## Task 3: Integrar commit+push e Slack no fluxo de `main()`

**Files:**
- Modify: `run_market_scan.py` (dentro de `main()`, bloco de sucesso após `write_report`)
- Test: `tests/test_run_market_scan.py`

**Interfaces:**
- Consumes: `git_commit_and_push(xlsx_path: Path, branch: str = "feat/market-scan") -> None` (Task 1), `notify_slack(xlsx_path: Path | None, text: str, channel: str = "pricing-trade-in") -> None` (Task 2)
- Produces: comportamento integrado de `main()` — não expõe função nova, mas fixa a ordem: commit+push antes de Slack, falha no primeiro impede o segundo.

Esta task não introduz função nova — ajusta o bloco de sucesso em `main()` para chamar `git_commit_and_push` antes de `notify_slack`, e propaga a falha corretamente. Como `main()` já tem um `try/except` externo que chama `notify_slack(None, ...)` em caso de exceção genérica, uma falha em `git_commit_and_push` (que levanta `RuntimeError`) já cai nesse `except` existente e dispara a notificação de erro — sem duplicar lógica de tratamento.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_run_market_scan.py — adicionar ao final do arquivo

def test_main_success_path_calls_commit_before_slack_upload(tmp_path, monkeypatch):
    """Não roda main() inteiro (precisaria de xlsx real e Claude) — verifica
    a ordem de chamada isolando as duas funções que a Task 3 conecta."""
    from run_market_scan import git_commit_and_push, notify_slack

    call_order = []

    def fake_commit(xlsx_path, branch="feat/market-scan"):
        call_order.append("commit")

    def fake_notify(xlsx_path, text, channel="pricing-trade-in"):
        call_order.append("slack")

    with (
        patch("run_market_scan.git_commit_and_push", side_effect=fake_commit),
        patch("run_market_scan.notify_slack", side_effect=fake_notify),
    ):
        import run_market_scan
        run_market_scan.git_commit_and_push(tmp_path / "x.xlsx")
        run_market_scan.notify_slack(tmp_path / "x.xlsx", "resumo")

    assert call_order == ["commit", "slack"]


def test_main_skips_slack_when_commit_fails(tmp_path, monkeypatch):
    """git_commit_and_push levantando RuntimeError não deve, por si, chamar
    notify_slack no bloco de sucesso — a integração real em main() delega
    esse caso ao except genérico existente, que chama notify_slack(None, ...)."""
    from run_market_scan import git_commit_and_push

    def fake_run(cmd, **kwargs):
        return MagicMock(returncode=1, stdout="", stderr="fatal")

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError):
            git_commit_and_push(tmp_path / "x.xlsx")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_run_market_scan.py -k "commit_before_slack or skips_slack_when_commit_fails" -v`
Expected: FAIL — `main()` ainda não chama `git_commit_and_push`, e `notify_slack` ainda tem a assinatura antiga (já corrigida na Task 2; esta task só liga os fios em `main()`).

- [ ] **Step 3: Wire the calls into `main()`**

In `run_market_scan.py`, inside `main()`'s success block, right after `write_report(...)` and before building `summary`, no change needed there. Locate the line `notify_slack(summary)` (now already updated by Task 2 to `notify_slack(out_xlsx, summary)`) and insert the commit/push call immediately before it:

```python
        out_xlsx = args.output_dir / f"referencia-mercado_{collected_at}.xlsx"
        write_report(args.input, out_xlsx, devices, results, collected_at)

        ok = sum(1 for s in results.values() if s.status == "ok")
        minutes = (datetime.now(timezone.utc) - started).total_seconds() / 60
        summary = (
            f":bar_chart: *Referência de mercado — {args.input.name}*\n"
            f"• {len(devices)} dispositivos em {len(batches)} lotes, {minutes:.0f} min\n"
            f"• {ok} com amostra boa (n≥{MIN_SAMPLE_OK}), "
            f"{len(results) - ok} com amostra fraca ou sem dados\n"
            f"• {len(failures)} lote(s) com falha\n"
            f"• Arquivo: `{out_xlsx}`\n"
            f"_Valores de anúncio (preço pedido), não de transação._"
        )
        print(summary)
        cost = summarize_costs(metric_rows)
        n_devices_for_cost = len(devices) or 1
        print(
            f"[custo] total US$ {cost['total_cost_usd']:.4f} · "
            f"US$ {cost['total_cost_usd'] / n_devices_for_cost:.4f}/dispositivo · "
            f"input {cost['total_input']} · output {cost['total_output']} · "
            f"cache_creation {cost['total_cache_creation']} · "
            f"cache_read {cost['total_cache_read']} · "
            f"razao cache_read/(input+output) {cost['cache_read_ratio']:.1f}"
        )
        git_commit_and_push(out_xlsx)
        notify_slack(out_xlsx, summary)
        return 1 if failures else 0
```

Esta ordem garante: se `git_commit_and_push` levantar `RuntimeError`, a execução sai do bloco `try` sem chamar `notify_slack(out_xlsx, summary)`, cai no `except Exception as exc` externo já existente, que chama `notify_slack(None, f":rotating_light: Rodada ABORTOU: ...")` — reusa o tratamento de erro que já existe, sem duplicar lógica.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_run_market_scan.py -v`
Expected: all tests pass (full suite, since this task touches `main()`'s control flow shared with existing tests)

- [ ] **Step 5: Commit**

```bash
git add run_market_scan.py tests/test_run_market_scan.py
git commit -m "feat(scan): liga commit automático e upload Slack ao fim da rodada"
```

---

## Task 4: `docs/routine.md` — documentação para configurar a Routine

**Files:**
- Create: `docs/routine.md`

**Interfaces:**
- Consumes: nada (documentação, não código)
- Produces: nada consumido por outras tasks

- [ ] **Step 1: Write the document**

```markdown
# Rodando o Market Scan como Claude Code Routine

Este documento descreve o que configurar na interface da Routine (fora
deste repositório) para rodar `run_market_scan.py` de forma automática e
semanal, sem supervisão.

## Comando

\`\`\`bash
uv run python run_market_scan.py --input Template-iPhone.xlsx
\`\`\`

Ajuste `--input` para a planilha de dispositivos correta a cada execução,
se houver mais de uma fonte de entrada.

## Frequência

Semanal. Recomendado rodar aos sábados: o cache por dispositivo já renova
toda semana no sábado (`_last_saturday`, em `run_market_scan.py`), então
rodar nesse dia garante que a rodada sempre trabalha com cache fresco.

## Variáveis de ambiente exigidas

| Variável | Propósito | Onde obter |
|---|---|---|
| `SLACK_BOT_TOKEN` | Autentica o upload do xlsx e o post no canal `pricing-trade-in` via Slack Web API | Gerado em `api.slack.com/apps` → app do bot → **OAuth & Permissions** → Bot User OAuth Token (escopos `files:write`, `chat:write`) |
| Credencial de push do GitHub | Permite `git push` para `feat/market-scan` a partir do ambiente da Routine, que clona o repositório do zero e não tem a chave SSH pessoal do usuário | Token de acesso pessoal (PAT) com permissão de push neste repositório, ou GitHub App configurado para a Routine — a decidir no momento de configurar o ambiente |

Nenhuma das duas credenciais deve ser commitada ou hardcoded em nenhum
arquivo do repositório.

## Autenticação do Claude CLI dentro da Routine

O script chama `claude -p` internamente (busca via WebSearch, arquitetura
inalterada por esta automação). A própria sessão da Routine já roda
autenticada como Claude Code — não é necessária nenhuma credencial adicional
para essas chamadas internas.

## O que a rodada faz, nesta ordem

1. Lê a planilha de entrada e monta os lotes de dispositivos
2. Para cada lote, consulta o cache por dispositivo+semana; só chama
   `claude -p` para os dispositivos pendentes
3. Gera `out/referencia-mercado_<data>.xlsx`
4. Commita e dá push desse arquivo direto na branch `feat/market-scan`
   (sem PR intermediário)
5. Sobe o mesmo arquivo como anexo no canal Slack `pricing-trade-in`,
   com um resumo da rodada (dispositivos processados, custo, falhas)

Falha no passo 4 impede o passo 5 — a Routine não deve notificar sucesso
sem ter persistido o resultado. Falha no passo 5 é só logada; não desfaz
o commit já feito.

## Fora de escopo desta automação

Não há teto de gasto automático, kill switch, nem revisão via PR — decisão
explícita para manter a arquitetura de busca e o fluxo de aprovação
inalterados. Ver `docs/superpowers/specs/2026-08-05-routine-automation-design.md`
para o racional completo.
\`\`\`
```

(Write the file content above, without the outer triple-backtick fence — that fence exists only to delimit this plan step.)

- [ ] **Step 2: Commit**

```bash
git add docs/routine.md
git commit -m "docs(routine): documenta configuracao da Claude Code Routine"
```

---

## Self-Review Notes

- **Spec coverage:** `git_commit_and_push` (Task 1) ✅, Slack upload real via bot token (Task 2) ✅, ordem commit-antes-de-Slack (Task 3) ✅, `docs/routine.md` com comando/frequência/variáveis (Task 4) ✅. Nenhuma seção do spec ficou sem task correspondente.
- **Placeholder scan:** nenhum "TBD"/"similar to Task N" — cada step tem código completo.
- **Type consistency:** `notify_slack(xlsx_path: Path | None, text: str, channel: str = "pricing-trade-in")` usado de forma consistente nas Tasks 2 e 3; `git_commit_and_push(xlsx_path: Path, branch: str = "feat/market-scan")` idem nas Tasks 1 e 3.
- **Desvio do spec registrado:** o spec original mencionava `slack_sdk` como biblioteca; o plano usa `urllib.request` puro (stdlib) para não introduzir dependência nova, seguindo o padrão já existente no arquivo (a função `notify_slack` anterior já usava `urllib.request`). Mesmo resultado funcional (upload real de arquivo), implementação mais consistente com o resto do código.
