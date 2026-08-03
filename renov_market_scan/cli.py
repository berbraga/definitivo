"""Typer CLI. Messages are in pt-BR; identifiers stay in English."""

import asyncio
import contextlib
import logging
import signal
from datetime import date
from pathlib import Path

import structlog
import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from renov_market_scan.collect.claude_cli import ClaudeCliAdapter
from renov_market_scan.config import Settings
from renov_market_scan.ingest.normalize import build_search_plan
from renov_market_scan.ingest.reader import read_device_rows
from renov_market_scan.notify import notify_slack
from renov_market_scan.query.builder import enabled_sources, load_sources
from renov_market_scan.run import SOURCES_FILE, RunOptions, RunResult, execute

app = typer.Typer(add_completion=False, help="Coletor de referencia de mercado de seminovos.")
console = Console()


@app.callback()
def main() -> None:
    """Coletor de referencia de mercado de seminovos."""


def _configure_logging(output_dir: Path) -> None:
    """Structured log to out/run.log, human output to the console."""
    output_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(output_dir / "run.log"),
        level=logging.INFO,
        format="%(message)s",
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


@app.command()
def run(
    input_path: Path = typer.Option(..., "--input", help="Planilha no template de importacao."),
    output_dir: Path = typer.Option(Path("out"), "--output", help="Diretorio de saida."),
    fontes: str | None = typer.Option(
        None,
        "--fontes",
        help="Fontes separadas por virgula. Default: as habilitadas em fontes.yaml.",
    ),
    somente_ativos: bool = typer.Option(
        True, "--somente-ativos/--todos", help="Ignora linhas com Price for In-store <= 10."
    ),
    marca: str | None = typer.Option(None, "--marca", help="Filtra por fabricante."),
    limite: int | None = typer.Option(None, "--limite", help="Primeiros N modelos da planilha."),
    incluir_novos: bool = typer.Option(
        False, "--incluir-novos/--sem-novos", help="Inclui anuncios novos e lacrados na mediana."
    ),
    concorrencia: int = typer.Option(
        4,
        "--concorrencia",
        help="Paralelismo do pipeline (a coleta via CLI e serializada).",
    ),
    cache: Path = typer.Option(Path(".cache/scan.sqlite"), "--cache", help="Banco de cache."),
    retomar: bool = typer.Option(False, "--retomar", help="Pula o que ja esta no cache do dia."),
    dry_run: bool = typer.Option(False, "--dry-run", help="So mostra o plano, sem coletar."),
    reprocessar_filtro: bool = typer.Option(
        False, "--reprocessar-filtro", help="Regrava o relatorio do cache, sem rede."
    ),
) -> None:
    """Coleta referencia de mercado para os modelos da planilha."""
    if not input_path.is_file():
        console.print(f"[red]Arquivo de entrada nao encontrado:[/red] {input_path}")
        raise typer.Exit(code=2)

    settings = Settings(concurrency=concorrencia)
    selected = [name.strip() for name in fontes.split(",")] if fontes else None
    sources = enabled_sources(load_sources(SOURCES_FILE), selected)
    if not sources:
        console.print("[red]Nenhuma fonte selecionada.[/red]")
        raise typer.Exit(code=2)

    rows, warnings = read_device_rows(input_path)
    for warning in warnings:
        console.print(f"[yellow]aviso:[/yellow] {warning}")

    plan, anomalies = build_search_plan(
        rows, active_only=somente_ativos, manufacturer_filter=marca, limit=limite
    )

    console.print(f"Linhas lidas:        {len(rows)}")
    console.print(f"Modelos a pesquisar: {len(plan)}")
    console.print(f"Anomalias:           {len(anomalies)}")
    console.print(f"Fontes:              {', '.join(source.name for source in sources)}")
    console.print(f"Modelo:              {settings.model}")
    console.print("")
    console.print(f"Pares (modelo x fonte): {len(plan) * len(sources)}")

    if dry_run:
        console.print("\n[green]--dry-run: plano exibido, nada foi coletado.[/green]")
        raise typer.Exit(code=0)

    if not reprocessar_filtro and not typer.confirm("\nConfirma a execucao?"):
        console.print("[yellow]Cancelado. Nada foi coletado.[/yellow]")
        raise typer.Exit(code=1)

    _configure_logging(output_dir)
    log = structlog.get_logger()
    options = RunOptions(
        input_path=input_path,
        output_dir=output_dir,
        cache_path=cache,
        sources=selected,
        active_only=somente_ativos,
        manufacturer=marca,
        limit=limite,
        include_new=incluir_novos,
        resume=retomar,
        reprocess_only=reprocessar_filtro,
        collected_on=date.today().isoformat(),
    )
    adapter = ClaudeCliAdapter(settings)

    async def _main() -> RunResult:
        loop = asyncio.get_running_loop()

        def _request_stop() -> None:
            console.print("\n[yellow]SIGINT recebido: encerrando de forma limpa.[/yellow]")

        with contextlib.suppress(NotImplementedError):  # pragma: no cover - Windows
            loop.add_signal_handler(signal.SIGINT, _request_stop)

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress_bar:
            task_id = progress_bar.add_task("coletando", total=max(len(plan) * len(sources), 1))

            def on_progress(done: int, total: int) -> None:
                progress_bar.update(task_id, completed=done, total=total)

            result = await execute(options, settings, adapter, progress=on_progress)

        log.info(
            "run_concluido",
            modelos=len(plan),
            buscas=result.searches_performed,
            amostras=len(result.sample_rows),
            descartados=len(result.discarded_rows),
        )
        console.print(f"\nRelatorio:        {result.report_path}")
        console.print(f"Copia do template: {result.template_copy_path}")
        console.print(f"Buscas realizadas: {result.searches_performed}")
        console.print(f"Anuncios aceitos:  {len(result.sample_rows)}")
        console.print(f"Descartados:       {len(result.discarded_rows)}")
        if result.search_status_counts:
            status_line = ", ".join(
                f"{status}: {count}"
                for status, count in sorted(result.search_status_counts.items())
            )
            console.print(f"Status das buscas: {status_line}")
        failed = sum(
            count
            for status, count in result.search_status_counts.items()
            if status != "ok"
        )
        if failed and not result.sample_rows:
            console.print(
                "\n[yellow]Nenhum anuncio coletado — as buscas falharam ou devolveram vazio.[/yellow]"
            )
            if result.collection_error_hint:
                console.print(f"[yellow]Motivo:[/yellow] {result.collection_error_hint}")
            if any(
                status == "limite_de_plano"
                for status in result.search_status_counts
            ):
                console.print(
                    "[yellow]Limite do plano/sessao do Claude Code. "
                    "Aguarde o reset e rode com --retomar.[/yellow]"
                )
        return result

    try:
        result = asyncio.run(_main())
    except KeyboardInterrupt:
        notify_slack(":warning: Rodada de referencia de mercado interrompida manualmente.")
        raise
    except Exception as exc:
        notify_slack(f":rotating_light: Rodada de referencia de mercado ABORTOU: `{exc}`")
        raise
    else:
        ok = sum(1 for row in result.summary_rows if row["status"] == "ok")
        summary = (
            f":bar_chart: *Referencia de mercado — {input_path.name}*\n"
            f"• {len(result.summary_rows)} modelo(s), {result.searches_performed} busca(s)\n"
            f"• {ok} com amostra boa, "
            f"{len(result.summary_rows) - ok} com amostra fraca ou sem dados\n"
            f"• {len(result.discarded_rows)} anuncio(s) descartado(s)\n"
            f"• Arquivo: `{result.report_path}`"
        )
        notify_slack(summary)


if __name__ == "__main__":
    app()
