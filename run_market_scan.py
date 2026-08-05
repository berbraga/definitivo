#!/usr/bin/env python3
"""
Orquestrador de referência de mercado de seminovos.

Dirige o Claude CLI em modo headless (`claude -p`), autenticado pela sessão do
navegador (assinatura Pro/Max/Team/Enterprise) — SEM ANTHROPIC_API_KEY.

Divisão de responsabilidades (proposital):
  - Claude faz o que exige julgamento: buscar anúncios, ler página, decidir se
    o anúncio é do modelo certo e extrair preço/URL.
  - Python faz o que exige determinismo: ler a planilha, lotear, estatística,
    escrever colunas, checkpoint e Slack.

Nunca peça mediana ao modelo. Aritmética é trabalho de Python.

Uso:
    python run_market_scan.py --input RS_Maio_Androids_2026.xlsx --lote 8
    python run_market_scan.py --input Template-iPhone.xlsx --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from openpyxl import load_workbook

# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------

HEADER_ROW = 2          # linha 1 = texto de ajuda do template; linha 2 = cabeçalho
FIRST_DATA_ROW = 3
COL_DEVICE, COL_MANUF, COL_MODEL = 1, 2, 3
COL_STORAGE, COL_PRICE_INSTORE, COL_MAX_PRICE, COL_ERP = 5, 8, 18, 19

PLACEHOLDER_PRICE = 10  # valor de item inativo/não comprado na tabela
MIN_SAMPLE_OK = 5
MIN_SAMPLE_LOW = 3

NEW_COLUMNS = [
    "n_amostras", "preco_minimo", "link_minimo", "mediana",
    "preco_maximo", "link_maximo", "razao_mediana_vs_atual",
    "fontes", "coletado_em", "status",
]

SOURCES = ["trocafy.com.br", "cellularstore.com.br", "mercadolivre.com.br"]

PROMPT_TEMPLATE = """Você é um coletor de referência de preços de celulares seminovos no Brasil.

Para CADA dispositivo, use WebSearch (fontes: {sources}) para achar anúncios
de aparelhos USADOS/SEMINOVOS. Extraia o preço só do snippet da busca — não
abra páginas; sem preço claro no snippet, descarte o anúncio.

No máximo 1 busca por dispositivo/fonte. Não repita busca sem resultado útil.
Não pare de buscar um dispositivo até ter pelo menos 5 anúncios válidos no
total (somando as fontes) ou ter esgotado todas as fontes listadas para ele.
Só pule uma fonte específica (não o dispositivo inteiro) se ela já sozinha
rendeu 5+ anúncios válidos para aquele dispositivo.

REGRAS DE ACEITE:
1. Qualificador do título (pro/max/plus/mini/ultra/neo/fusion/lite/fe/se/
   power/play/air) IGUAL ao alvo. "13" não aceita "13 Pro Max". "5g" opcional.
2. Capacidade explícita (título ou URL) igual ao alvo (1024=1TB); sem isso, descarte.
3. Descarte acessórios e peças: capa, capinha, case, película, vidro, tela,
   display, touch, bateria, placa, flex, conector, carcaça, aro, tampa,
   câmera, alto-falante, botão, "para retirada", "não liga", réplica, clone,
   similar, carregador, fone, cabo, chip, suporte.
4. Preço à vista — rejeite "12x"/"sem juros". "R$ 1.234,56" → 1234.56 (float).
5. Descarte "novo"/"lacrado"; aceite seminovo/usado/vitrine/recondicionado.
6. Máximo 6 anúncios por dispositivo, fontes variadas.

JSON puro, sem markdown:
{{"resultados":[{{"erp_code":"...","anuncios":[{{"fonte":"...","titulo":"...","preco_brl":0.0,"condicao":"usado","url":"..."}}],"fontes_consultadas":["..."],"observacao":""}}]}}

Sem dado válido: "anuncios":[] + "observacao". Não calcule mediana/min/max.
Não edite arquivos. Não faça perguntas.

DISPOSITIVOS: (máx {max_buscas_por_lote} buscas totais neste lote)
{devices}
"""


# --------------------------------------------------------------------------
# Modelos
# --------------------------------------------------------------------------

@dataclass
class Device:
    row: int
    erp: str
    name: str
    manufacturer: str
    model: str
    storage: int | None
    current_price: float | None

    @property
    def storage_label(self) -> str:
        if self.storage in (1024, 2048):
            return f"{self.storage // 1024}TB"
        return f"{self.storage}GB"

    def as_query_dict(self) -> dict:
        return {
            "erp_code": self.erp,
            "fabricante": self.manufacturer,
            "modelo": self.model,
            "capacidade": self.storage_label,
        }


@dataclass
class Stats:
    n: int = 0
    minimum: float | None = None
    link_min: str = ""
    median: float | None = None
    maximum: float | None = None
    link_max: str = ""
    sources: str = ""
    status: str = "sem_dados"
    listings: list[dict] = field(default_factory=list)
    sources_queried: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Pré-voo: garantir que rodamos na assinatura, não em API key
# --------------------------------------------------------------------------

def preflight() -> None:
    """Falha cedo e alto. Erro de auth aqui vira fatura no fim do mês."""
    if shutil.which("claude") is None:
        sys.exit("ERRO: 'claude' não está no PATH. Instale o Claude Code CLI.")

    if os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "AVISO: ANTHROPIC_API_KEY está definida no ambiente.\n"
            "       Em modo -p o Claude Code usa a API key quando ela existe,\n"
            "       o que cobra por token em vez de usar a assinatura.\n"
            "       Ela será REMOVIDA do ambiente dos subprocessos.",
            file=sys.stderr,
        )

    # `claude auth status` sai 0 quando logado, 1 quando não.
    probe = subprocess.run(
        ["claude", "auth", "status"],
        env=child_env(), capture_output=True, text=True, timeout=60,
    )
    if probe.returncode != 0:
        sys.exit(
            "ERRO: Claude CLI não autenticado.\n"
            "  Máquina com navegador: rode 'claude' e faça /login.\n"
            "  Servidor sem navegador: gere o token numa máquina com navegador\n"
            "  com 'claude setup-token' e exporte CLAUDE_CODE_OAUTH_TOKEN."
        )
    print(f"[preflight] auth ok — {probe.stdout.strip().splitlines()[:2]}")


def child_env() -> dict[str, str]:
    """Ambiente dos subprocessos: assinatura, nunca API key."""
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    return env


# --------------------------------------------------------------------------
# Planilha
# --------------------------------------------------------------------------

def read_devices(path: Path, somente_ativos: bool, limite: int | None) -> list[Device]:
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    devices: list[Device] = []

    for idx, row in enumerate(ws.iter_rows(min_row=FIRST_DATA_ROW, values_only=True),
                              start=FIRST_DATA_ROW):
        if not row or not row[COL_DEVICE - 1]:
            continue
        storage = row[COL_STORAGE - 1]
        price = row[COL_PRICE_INSTORE - 1]

        # Capacidade suja (1 = RAM na coluna errada, 1288 = typo): não adivinhar.
        if not isinstance(storage, int) or storage not in (16, 32, 64, 128, 256, 512, 1024, 2048):
            continue
        if somente_ativos and (price is None or float(price) <= PLACEHOLDER_PRICE):
            continue

        devices.append(Device(
            row=idx,
            erp=str(row[COL_ERP - 1] or f"LINHA{idx}"),
            name=str(row[COL_DEVICE - 1]),
            manufacturer=str(row[COL_MANUF - 1] or ""),
            model=str(row[COL_MODEL - 1] or ""),
            storage=storage,
            current_price=float(price) if price is not None else None,
        ))
        if limite and len(devices) >= limite:
            break

    wb.close()
    return devices


# --------------------------------------------------------------------------
# Execução do Claude CLI
# --------------------------------------------------------------------------

def build_prompt(devices: list[Device]) -> str:
    return PROMPT_TEMPLATE.format(
        sources=", ".join(SOURCES),
        max_buscas_por_lote=len(devices) * len(SOURCES),
        devices=json.dumps([d.as_query_dict() for d in devices],
                           ensure_ascii=False, indent=2),
    )


def run_claude_batch(devices: list[Device], model: str, timeout: int) -> dict:
    """Uma invocação headless do CLI para um lote. Prompt vai por stdin.

    Sem --max-turns: a versão instalada do CLI (2.1.220) nao expõe essa
    flag (confirmado em `claude --help`) — passá-la é ignorado em silêncio.
    O limite de buscas por lote é imposto por instrução no próprio prompt.
    """
    prompt = build_prompt(devices)

    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--allowedTools", "WebSearch",
        "--model", model,
        "--effort", "medium",
    ]

    started = time.monotonic()
    proc = subprocess.run(
        cmd, input=prompt, env=child_env(),
        capture_output=True, text=True, timeout=timeout,
    )
    elapsed = time.monotonic() - started

    if proc.returncode != 0:
        raise RuntimeError(
            f"claude saiu com código {proc.returncode} após {elapsed:.0f}s: "
            f"{(proc.stderr or proc.stdout or '')[:500]}"
        )

    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude reportou erro: {str(envelope)[:500]}")

    payload = extract_json(envelope.get("result", ""))
    usage = envelope.get("usage") or {}
    payload["_meta"] = {
        "session_id": envelope.get("session_id"),
        "duracao_s": round(elapsed, 1),
        "num_turns": envelope.get("num_turns"),
        "total_cost_usd": envelope.get("total_cost_usd"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
    }
    return payload


def extract_json(text: str) -> dict:
    """O modelo às vezes embrulha em code fence, mesmo proibido."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"resposta sem JSON reconhecível: {text[:300]}")
    return json.loads(text[start:end + 1])


METRICS_FIELDS = [
    "lote_idx", "n_dispositivos", "input_tokens", "output_tokens",
    "cache_creation_input_tokens", "cache_read_input_tokens",
    "total_cost_usd", "num_turns", "duracao_s",
]


def write_lote_metrics(csv_path: Path, lote_idx: int, n_dispositivos: int, meta: dict) -> None:
    """Uma linha por lote. Cria o header na primeira chamada da rodada."""
    is_new = not csv_path.exists()
    row = {"lote_idx": lote_idx, "n_dispositivos": n_dispositivos, **{
        k: meta.get(k) for k in METRICS_FIELDS if k not in ("lote_idx", "n_dispositivos")
    }}
    with csv_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=METRICS_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def summarize_costs(metric_rows: list[dict]) -> dict:
    """Agrega as linhas de metrics/rodada_*.csv (ou os _meta em memória)
    de uma rodada inteira num resumo único."""
    total_input = sum(r.get("input_tokens") or 0 for r in metric_rows)
    total_output = sum(r.get("output_tokens") or 0 for r in metric_rows)
    total_cache_creation = sum(r.get("cache_creation_input_tokens") or 0 for r in metric_rows)
    total_cache_read = sum(r.get("cache_read_input_tokens") or 0 for r in metric_rows)
    total_cost = sum(r.get("total_cost_usd") or 0 for r in metric_rows)
    denom = total_input + total_output
    return {
        "total_cost_usd": round(total_cost, 4),
        "total_input": total_input,
        "total_output": total_output,
        "total_cache_creation": total_cache_creation,
        "total_cache_read": total_cache_read,
        "cache_read_ratio": (total_cache_read / denom) if denom else 0.0,
    }


# --------------------------------------------------------------------------
# Estatística (Python, nunca o modelo)
# --------------------------------------------------------------------------

def compute_stats(entry: dict) -> Stats:
    listings = entry.get("anuncios", [])
    sources_queried = entry.get("fontes_consultadas", [])
    valid = []
    for item in listings:
        try:
            price = float(item["preco_brl"])
        except (KeyError, TypeError, ValueError):
            continue
        if price < 80 or not str(item.get("url", "")).startswith("http"):
            continue
        valid.append({**item, "preco_brl": price})

    if not valid:
        return Stats(status="sem_dados", sources_queried=sources_queried)

    prices = sorted(v["preco_brl"] for v in valid)
    if len(prices) >= 4:
        q1, q3 = statistics.quantiles(prices, n=4)[0], statistics.quantiles(prices, n=4)[2]
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        valid = [v for v in valid if lo <= v["preco_brl"] <= hi] or valid

    valid.sort(key=lambda v: v["preco_brl"])
    n = len(valid)
    status = "ok" if n >= MIN_SAMPLE_OK else (
        "amostra_baixa" if n >= MIN_SAMPLE_LOW else "insuficiente")

    return Stats(
        n=n,
        minimum=valid[0]["preco_brl"],
        link_min=valid[0]["url"],
        median=round(statistics.median(v["preco_brl"] for v in valid), 2),
        maximum=valid[-1]["preco_brl"],
        link_max=valid[-1]["url"],
        sources=", ".join(sorted({str(v.get("fonte", "?")) for v in valid})),
        status=status,
        listings=valid,
        sources_queried=sources_queried,
    )


def median_divergence_pct(baseline: dict[str, float], current: dict[str, float]) -> dict[str, float]:
    diffs = {}
    for erp, base_value in baseline.items():
        if erp not in current or not base_value:
            continue
        diffs[erp] = abs(current[erp] - base_value) / base_value * 100
    return diffs


# --------------------------------------------------------------------------
# Escrita do resultado
# --------------------------------------------------------------------------

def write_report(src: Path, dst: Path, devices: list[Device],
                 results: dict[str, Stats], collected_at: str) -> None:
    """Escreve numa CÓPIA. O arquivo original é o de importação — não mexer."""
    shutil.copy(src, dst)
    wb = load_workbook(dst)
    ws = wb[wb.sheetnames[0]]

    # Inserido logo depois de "Maximum Price" (não no fim da tabela) para
    # ficar visualmente ao lado das colunas Minimum/Maximum Price do
    # template, sem sobrescrever seu significado original (piso/teto de
    # preço de recompra).
    first_new = COL_MAX_PRICE + 1
    ws.insert_cols(first_new, amount=len(NEW_COLUMNS))
    for offset, title in enumerate(NEW_COLUMNS):
        ws.cell(row=HEADER_ROW, column=first_new + offset, value=title)

    by_row = {d.row: d for d in devices}
    for row, device in by_row.items():
        st = results.get(device.erp, Stats())
        ratio = (st.median / device.current_price
                 if st.median and device.current_price else None)
        values = [
            st.n, st.minimum, st.link_min, st.median,
            st.maximum, st.link_max,
            round(ratio, 3) if ratio else None,
            st.sources, collected_at, st.status,
        ]
        for offset, value in enumerate(values):
            cell = ws.cell(row=row, column=first_new + offset, value=value)
            if offset in (1, 3, 4) and value is not None:
                cell.number_format = 'R$ #,##0.00'

    samples = wb.create_sheet("Amostras")
    samples.append(["erp_code", "fonte", "titulo", "preco_brl", "condicao", "url"])
    for erp, st in results.items():
        for item in st.listings:
            samples.append([erp, item.get("fonte"), item.get("titulo"),
                            item.get("preco_brl"), item.get("condicao"),
                            item.get("url")])

    ws.freeze_panes = ws.cell(row=FIRST_DATA_ROW, column=1)
    wb.save(dst)


# --------------------------------------------------------------------------
# Git — commit e push automático do resultado
# --------------------------------------------------------------------------

def git_commit_and_push(xlsx_path: Path, branch: str = "feat/market-scan") -> None:
    """Commita e envia o xlsx gerado direto na branch ativa.

    Sem PR intermediário — decisão explícita para esta automação. Levanta
    RuntimeError na primeira falha real; quem chama decide se prossegue para
    o Slack (não deve, numa rodada real: commit falho = nada para notificar).

    "Nada para commitar" (xlsx idêntico ao da última rodada, ex.: segunda
    rodada na mesma semana de cache) NÃO é falha real — é retorno normal,
    sem push, sem exceção.
    """
    repo_dir = Path(__file__).resolve().parent

    def _run(cmd: list[str], step_name: str) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, cwd=repo_dir,
        )
        if proc.returncode not in (0, 1):
            raise RuntimeError(
                f"{step_name} falhou (código {proc.returncode}): "
                f"{(proc.stderr or proc.stdout or '').strip()[:500]}"
            )
        return proc

    def _extract_date(path: Path) -> str:
        """Extrai a data ISO (YYYY-MM-DD) do stem do arquivo.

        Busca por padrão ISO no stem; cai para o último segmento separado
        por underscore se não encontrado (compatibilidade com filenames
        não-convencionais).
        """
        match = re.search(r'\d{4}-\d{2}-\d{2}', path.stem)
        if match:
            return match.group()
        return path.stem.split('_')[-1]

    add_proc = subprocess.run(
        ["git", "add", str(xlsx_path)], capture_output=True, text=True,
        timeout=60, cwd=repo_dir,
    )
    if add_proc.returncode != 0:
        raise RuntimeError(
            f"git add falhou (código {add_proc.returncode}): "
            f"{(add_proc.stderr or add_proc.stdout or '').strip()[:500]}"
        )

    # exit 0 = nada staged (diff vazio), exit 1 = algo staged. Qualquer outro
    # código é falha real do próprio git diff, não um resultado válido.
    staged_check = _run(["git", "diff", "--cached", "--quiet"], "git diff --cached")
    if staged_check.returncode == 0:
        print("[git] nada para commitar — xlsx idêntico ao da última rodada")
        return

    date_str = _extract_date(xlsx_path)
    commit_proc = subprocess.run(
        ["git", "commit", "-m", f"chore(scan): atualiza referência de mercado {date_str}"],
        capture_output=True, text=True, timeout=60, cwd=repo_dir,
    )
    if commit_proc.returncode != 0:
        raise RuntimeError(
            f"git commit falhou (código {commit_proc.returncode}): "
            f"{(commit_proc.stderr or commit_proc.stdout or '').strip()[:500]}"
        )

    push_proc = subprocess.run(
        ["git", "push", "origin", branch], capture_output=True, text=True,
        timeout=60, cwd=repo_dir,
    )
    if push_proc.returncode != 0:
        raise RuntimeError(
            f"git push falhou (código {push_proc.returncode}): "
            f"{(push_proc.stderr or push_proc.stdout or '').strip()[:500]}"
        )


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
            urllib.parse.urlencode(
                {"filename": xlsx_path.name, "length": len(file_bytes)}
            ).encode(),
            "application/x-www-form-urlencoded",
        )
        if not meta.get("ok"):
            raise RuntimeError(str(meta))

        # Slack's upload_url accepts POST, not PUT, despite common REST
        # convention for pre-signed upload URLs — verified against Slack's
        # actual API behavior (their docs/examples use POST here).
        upload_req = urllib.request.Request(
            meta["upload_url"], data=file_bytes, method="POST",
        )
        with urllib.request.urlopen(upload_req, timeout=60) as resp:
            resp.read()
            if not (200 <= resp.status < 300):
                raise RuntimeError(
                    f"upload do xlsx falhou com status HTTP {resp.status}"
                )

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


# --------------------------------------------------------------------------
# Cache por dispositivo + semana (renova todo sábado)
# --------------------------------------------------------------------------

def _last_saturday(today: date) -> date:
    """Sábado da semana corrente (hoje, se hoje já for sábado)."""
    days_since_saturday = (today.weekday() - 5) % 7  # Monday=0 .. Saturday=5
    return today - timedelta(days=days_since_saturday)


def slugify_erp(erp: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", erp).strip("-")


def device_cache_path(cache_dir: Path, erp: str, week: date) -> Path:
    iso_year, iso_week, _ = week.isocalendar()
    return cache_dir / f"{slugify_erp(erp)}_{iso_year}-W{iso_week:02d}.json"


def prune_stale_weeks(cache_dir: Path, current_week: date) -> int:
    """Remove entradas de semanas anteriores à atual. Retorna quantas."""
    _, current_iso_week, _ = current_week.isocalendar()
    current_year, _, _ = current_week.isocalendar()
    current_tag = f"{current_year}-W{current_iso_week:02d}"
    removed = 0
    for cached in cache_dir.glob("*.json"):
        if current_tag not in cached.name:
            cached.unlink()
            removed += 1
    return removed


def match_pending_device(erp_code: str, pending: list[Device]) -> Device | None:
    """Casa o erp_code devolvido pelo modelo com o Device pendente correto.

    O modelo às vezes ecoa o erp_code com capitalização/formatação diferente
    da planilha. Comparamos formas normalizadas (slugify_erp, case-insensitive)
    para achar o Device original — cuja .erp (fonte confiável) deve ser usado
    como chave, nunca a string devolvida pelo modelo.
    """
    target = slugify_erp(str(erp_code)).lower()
    for device in pending:
        if slugify_erp(device.erp).lower() == target:
            return device
    return None


def process_batch(
    batch: list[Device],
    cache_dir: Path,
    current_week: date,
    model: str,
    timeout: int,
) -> tuple[dict[str, Stats], dict | None, bool]:
    """Processa um lote: separa cache/pendentes, roda os pendentes, funde
    resultados e grava cache (pulando resultados vazios — Finding 2).

    Retorna (results_deste_lote, meta_da_chamada_ou_None, houve_chamada_claude).
    Não grava métricas nem lança em caso de falha do claude — quem chama trata.
    """
    results: dict[str, Stats] = {}

    pending = [d for d in batch
               if not device_cache_path(cache_dir, d.erp, current_week).exists()]
    cached_devices = [d for d in batch if d not in pending]

    for device in cached_devices:
        cache_path = device_cache_path(cache_dir, device.erp, current_week)
        entry = json.loads(cache_path.read_text(encoding="utf-8"))
        results[device.erp] = compute_stats(entry)

    if not pending:
        return results, None, False

    payload = run_claude_batch(pending, model, timeout)

    for entry in payload.get("resultados", []):
        returned_erp = entry.get("erp_code")
        device = match_pending_device(returned_erp, pending)
        if device is None:
            print(
                f"    AVISO: erp_code '{returned_erp}' devolvido pelo modelo não "
                f"corresponde a nenhum dispositivo pendente deste lote — descartado.",
                file=sys.stderr,
            )
            continue

        erp = device.erp
        results[erp] = compute_stats(entry)

        if entry.get("anuncios"):
            device_cache_path(cache_dir, erp, current_week).write_text(
                json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    return results, payload["_meta"], True


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Referência de mercado via Claude CLI")
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output-dir", type=Path, default=Path("out"))
    ap.add_argument("--lote", type=int, default=8, help="dispositivos por invocação")
    ap.add_argument("--limite", type=int, default=None)
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--timeout", type=int, default=900, help="segundos por lote")
    ap.add_argument("--pausa", type=float, default=5.0, help="segundos entre lotes")
    ap.add_argument("--todos", action="store_true", help="inclui itens inativos (preço 10)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = args.output_dir / "cache"
    cache_dir.mkdir(exist_ok=True)
    (args.output_dir / "metrics").mkdir(exist_ok=True)
    current_week = _last_saturday(datetime.now(timezone.utc).date())
    removed = prune_stale_weeks(cache_dir, current_week)
    if removed:
        print(f"[cache] nova semana (sábado {current_week}) — {removed} entrada(s) antiga(s) removida(s)")

    devices = read_devices(args.input, somente_ativos=not args.todos, limite=args.limite)
    batches = [devices[i:i + args.lote] for i in range(0, len(devices), args.lote)]

    print(f"{len(devices)} dispositivos -> {len(batches)} lotes de até {args.lote}")
    if args.dry_run:
        for b in batches[:3]:
            print("  lote:", [d.name for d in b])
        print("  (dry-run: nada executado)")
        return 0

    preflight()

    started = datetime.now(timezone.utc)
    collected_at = started.strftime("%Y-%m-%d")
    results: dict[str, Stats] = {}
    failures: list[str] = []
    metric_rows: list[dict] = []

    try:
        for i, batch in enumerate(batches, start=1):
            pending_names = [
                d.name for d in batch
                if not device_cache_path(cache_dir, d.erp, current_week).exists()
            ]
            if not pending_names:
                print(f"[{i}/{len(batches)}] cache (todos os {len(batch)} dispositivos)")
            else:
                print(f"[{i}/{len(batches)}] {', '.join(pending_names)}")

            try:
                batch_results, meta, called_claude = process_batch(
                    batch, cache_dir, current_week, args.model, args.timeout,
                )
            except Exception as exc:  # noqa: BLE001 — um lote ruim não derruba a rodada
                msg = f"lote {i}: {exc}"
                print(f"    FALHA: {msg}", file=sys.stderr)
                failures.append(msg)
                time.sleep(args.pausa * 4)
                continue

            results.update(batch_results)

            if called_claude:
                n_pending = len(pending_names)
                row = {"lote_idx": i, "n_dispositivos": n_pending, **meta}
                metric_rows.append(row)
                write_lote_metrics(
                    args.output_dir / "metrics" / f"rodada_{collected_at}.csv",
                    lote_idx=i, n_dispositivos=n_pending, meta=meta,
                )
                time.sleep(args.pausa)

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

    except KeyboardInterrupt:
        notify_slack(None, f":warning: Rodada interrompida manualmente "
                     f"({len(results)} dispositivos já processados, checkpoint salvo).")
        return 130
    except Exception as exc:  # noqa: BLE001
        notify_slack(None, f":rotating_light: Rodada ABORTOU: `{exc}`\n"
                     f"Checkpoint em `{cache_dir}` — rode de novo para retomar.")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
