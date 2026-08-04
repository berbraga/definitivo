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

Para CADA dispositivo da lista abaixo, use WebSearch para encontrar anúncios
de aparelhos USADOS ou SEMINOVOS à venda no Brasil, em: {sources}.

Extraia o preço APENAS do snippet/resumo retornado pela busca. Não abra
páginas. Se o snippet não mostrar preço claro, descarte o anúncio.

Faça NO MÁXIMO 1 busca (WebSearch) por dispositivo em cada uma das fontes
listadas — no máximo {max_buscas_por_lote} chamadas de WebSearch no total
para este lote inteiro. Não repita uma busca que já não trouxe resultado
útil. Assim que tiver anúncios suficientes para um dispositivo, pare de
buscar por ele e siga para o próximo.

REGRAS DE ACEITE DE ANÚNCIO (aplicar antes de incluir):
1. Modelo exato. O conjunto de qualificadores do título (pro, max, plus, mini,
   ultra, neo, fusion, lite, fe, se, power, play, air) deve ser IGUAL ao do
   modelo alvo. "iPhone 13" NÃO aceita "iPhone 13 Pro Max" e vice-versa.
   "5g" é tolerado como opcional.
2. Capacidade explícita no título ou na URL, igual à do alvo (1024 = 1TB).
   Sem capacidade explícita, descarte.
3. Descarte acessórios e peças: capa, capinha, case, película, vidro, tela,
   display, touch, bateria, placa, flex, conector, carcaça, aro, tampa,
   câmera, alto-falante, botão, "para retirada", "não liga", réplica, clone,
   similar, carregador, fone, cabo, chip, suporte.
4. Preço À VISTA. Rejeite valor precedido de "12x", "10 x", "sem juros".
   Converta "R$ 1.234,56" para 1234.56 (número, ponto decimal).
5. Descarte "novo"/"lacrado". Aceite: seminovo, usado, vitrine, recondicionado.
6. No máximo {max_por_dispositivo} anúncios por dispositivo, de fontes variadas.

RESPONDA APENAS COM JSON, sem markdown, sem code fence, sem preâmbulo:
{{"resultados":[{{"erp_code":"...","anuncios":[{{"fonte":"olx","titulo":"...",
"preco_brl":1234.56,"condicao":"usado","url":"https://..."}}],"observacao":""}}]}}

Se não achar nada válido para um dispositivo, devolva "anuncios": [] e explique
em "observacao". NÃO calcule mediana, mínimo ou máximo. NÃO edite arquivos.
NÃO faça perguntas — não há ninguém para responder.

DISPOSITIVOS:
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

def run_claude_batch(devices: list[Device], model: str, timeout: int) -> dict:
    """Uma invocação headless do CLI para um lote. Prompt vai por stdin.

    Sem --max-turns: a versão instalada do CLI (2.1.220) nao expõe essa
    flag (confirmado em `claude --help`) — passá-la é ignorado em silêncio.
    O limite de buscas por lote é imposto por instrução no próprio prompt.
    """
    prompt = PROMPT_TEMPLATE.format(
        sources=", ".join(SOURCES),
        max_por_dispositivo=6,
        max_buscas_por_lote=len(devices) * len(SOURCES),
        devices=json.dumps([d.as_query_dict() for d in devices],
                           ensure_ascii=False, indent=2),
    )

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

def compute_stats(listings: list[dict]) -> Stats:
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
        return Stats(status="sem_dados")

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
    )


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
# Slack
# --------------------------------------------------------------------------

def notify_slack(text: str) -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        print("[slack] SLACK_WEBHOOK_URL não definida — pulando notificação.")
        return
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"[slack] enviado ({resp.status})")
    except urllib.error.URLError as exc:
        print(f"[slack] FALHOU: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------
# Cache semanal (renova todo sábado)
# --------------------------------------------------------------------------

CACHE_WEEK_MARKER = ".cache_week"


def _last_saturday(today: date) -> date:
    """Sábado da semana corrente (hoje, se hoje já for sábado)."""
    days_since_saturday = (today.weekday() - 5) % 7  # Monday=0 .. Saturday=5
    return today - timedelta(days=days_since_saturday)


def reset_cache_if_new_week(partials_dir: Path) -> None:
    """Limpa os lotes em cache (partials/lote_*.json) uma vez por semana,
    na virada de sábado — assim o scraper não fica reaproveitando preço
    de mercado de semanas atrás indefinidamente."""
    marker_path = partials_dir / CACHE_WEEK_MARKER
    current_week = _last_saturday(datetime.now(timezone.utc).date())

    stored_week = None
    if marker_path.exists():
        try:
            stored_week = date.fromisoformat(marker_path.read_text(encoding="utf-8").strip())
        except ValueError:
            stored_week = None

    if stored_week is not None and stored_week < current_week:
        removed = 0
        for partial in partials_dir.glob("lote_*.json"):
            partial.unlink()
            removed += 1
        print(f"[cache] nova semana (sábado {current_week}) — {removed} lote(s) em cache limpo(s)")

    marker_path.write_text(current_week.isoformat(), encoding="utf-8")


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
    partials_dir = args.output_dir / "partials"
    partials_dir.mkdir(exist_ok=True)
    (args.output_dir / "metrics").mkdir(exist_ok=True)
    reset_cache_if_new_week(partials_dir)

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
            partial = partials_dir / f"lote_{i:04d}.json"

            if partial.exists():  # retomada
                payload = json.loads(partial.read_text(encoding="utf-8"))
                print(f"[{i}/{len(batches)}] cache")
            else:
                print(f"[{i}/{len(batches)}] {', '.join(d.name for d in batch)}")
                try:
                    payload = run_claude_batch(batch, args.model, args.timeout)
                    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
                    row = {"lote_idx": i, "n_dispositivos": len(batch), **payload["_meta"]}
                    metric_rows.append(row)
                    write_lote_metrics(
                        args.output_dir / "metrics" / f"rodada_{collected_at}.csv",
                        lote_idx=i, n_dispositivos=len(batch), meta=payload["_meta"],
                    )
                except Exception as exc:  # noqa: BLE001 — um lote ruim não derruba a rodada
                    msg = f"lote {i}: {exc}"
                    print(f"    FALHA: {msg}", file=sys.stderr)
                    failures.append(msg)
                    time.sleep(args.pausa * 4)
                    continue

            for entry in payload.get("resultados", []):
                st = compute_stats(entry.get("anuncios", []))
                results[str(entry.get("erp_code"))] = st

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
        notify_slack(summary)
        return 1 if failures else 0

    except KeyboardInterrupt:
        notify_slack(f":warning: Rodada interrompida manualmente "
                     f"({len(results)} dispositivos já processados, checkpoint salvo).")
        return 130
    except Exception as exc:  # noqa: BLE001
        notify_slack(f":rotating_light: Rodada ABORTOU: `{exc}`\n"
                     f"Checkpoint em `{partials_dir}` — rode de novo para retomar.")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
