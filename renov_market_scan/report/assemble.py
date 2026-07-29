"""Turn pipeline output into report rows. Column names are the pt-BR headers.

Statistics are computed once per search key and replicated to every report key
that search serves, which is what makes one row per (ERP Code, Model, Storage)
possible without repeating a search.
"""

from typing import Any

from renov_market_scan.models import (
    Anomaly,
    Listing,
    ModelStats,
    RejectedListing,
    SearchPlanItem,
)

FOOTNOTE = (
    "Valores de anuncio (preco pedido), nao de transacao. "
    "Amostra coletada em {data} - referencia de variacao de mercado, nao avaliacao."
)

SUMMARY_COLUMNS: tuple[str, ...] = (
    "erp_code",
    "device_name",
    "fabricante",
    "modelo",
    "capacidade",
    "valor_atual_planilha",
    "n_amostras",
    "n_fontes",
    "preco_minimo",
    "link_minimo",
    "mediana",
    "preco_maximo",
    "link_maximo",
    "p25",
    "p75",
    "spread_pct",
    "razao_mediana_vs_atual",
    "condicao_predominante",
    "fontes",
    "coletado_em",
    "status",
    "observacoes",
    "min_bruto",
    "max_bruto",
)

SAMPLE_COLUMNS: tuple[str, ...] = (
    "erp_code",
    "fonte",
    "titulo",
    "preco",
    "condicao",
    "url",
    "capturado_em",
    "flag_5g_divergente",
    "cited_text",
)

DISCARDED_COLUMNS: tuple[str, ...] = (
    "erp_code",
    "fonte",
    "titulo",
    "preco",
    "url",
    "motivo_descarte",
    "cited_text",
)

ANOMALY_COLUMNS: tuple[str, ...] = (
    "linha_planilha",
    "erp_code",
    "device_name",
    "campo",
    "valor_bruto",
    "motivo",
    "status",
)

EMPTY_STATS_STATUS = "insuficiente"


def _yes_no(value: bool) -> str:
    return "sim" if value else "nao"


def build_summary_rows(
    plan_items: list[SearchPlanItem],
    stats_by_key: dict[str, ModelStats],
    collected_on: str,
) -> list[dict[str, Any]]:
    """One row per report key, carrying its search key's statistics."""
    rows: list[dict[str, Any]] = []
    for item in plan_items:
        stats = stats_by_key.get(item.search_key)
        for key in item.report_keys:
            median = stats.median if stats else None
            ratio: float | None = None
            if median is not None and key.price_instore:
                ratio = median / key.price_instore
            rows.append(
                {
                    "erp_code": key.erp_code,
                    "device_name": key.device_name,
                    "fabricante": item.manufacturer,
                    "modelo": key.model,
                    "capacidade": key.storage_label,
                    "valor_atual_planilha": key.price_instore,
                    "n_amostras": stats.n if stats else 0,
                    "n_fontes": len(stats.sources) if stats else 0,
                    "preco_minimo": stats.minimum if stats else None,
                    "link_minimo": stats.min_url if stats else None,
                    "mediana": median,
                    "preco_maximo": stats.maximum if stats else None,
                    "link_maximo": stats.max_url if stats else None,
                    "p25": stats.p25 if stats else None,
                    "p75": stats.p75 if stats else None,
                    "spread_pct": stats.spread_pct if stats else None,
                    "razao_mediana_vs_atual": ratio,
                    "condicao_predominante": (
                        stats.predominant_condition if stats else "desconhecido"
                    ),
                    "fontes": ", ".join(stats.sources) if stats else "",
                    "coletado_em": collected_on,
                    "status": stats.status if stats else EMPTY_STATS_STATUS,
                    "observacoes": "",
                    "min_bruto": stats.min_raw if stats else None,
                    "max_bruto": stats.max_raw if stats else None,
                }
            )
    return rows


def build_sample_rows(
    plan_items: list[SearchPlanItem], listings_by_key: dict[str, list[Listing]]
) -> list[dict[str, Any]]:
    """Every accepted advert, repeated per report key so the join is explicit."""
    rows: list[dict[str, Any]] = []
    for item in plan_items:
        for listing in listings_by_key.get(item.search_key, []):
            for key in item.report_keys:
                rows.append(
                    {
                        "erp_code": key.erp_code,
                        "fonte": listing.source,
                        "titulo": listing.title,
                        "preco": listing.price_brl,
                        "condicao": listing.condition,
                        "url": listing.url,
                        "capturado_em": listing.captured_at,
                        "flag_5g_divergente": _yes_no(listing.flag_5g_divergent),
                        "cited_text": listing.cited_text,
                    }
                )
    return rows


def build_discarded_rows(
    plan_items: list[SearchPlanItem], rejected_by_key: dict[str, list[RejectedListing]]
) -> list[dict[str, Any]]:
    """Every discard with its reason, for rule calibration."""
    rows: list[dict[str, Any]] = []
    for item in plan_items:
        first_erp = item.report_keys[0].erp_code if item.report_keys else ""
        for rejected in rejected_by_key.get(item.search_key, []):
            rows.append(
                {
                    "erp_code": first_erp,
                    "fonte": rejected.listing.source,
                    "titulo": rejected.listing.title,
                    "preco": rejected.listing.price_brl,
                    "url": rejected.listing.url,
                    "motivo_descarte": rejected.reason,
                    "cited_text": rejected.listing.cited_text,
                }
            )
    return rows


def build_anomaly_rows(
    anomalies: list[Anomaly],
    plan_items: list[SearchPlanItem],
    stats_by_key: dict[str, ModelStats],
) -> list[dict[str, Any]]:
    """Dirty-storage rows plus every searched model that produced no sample."""
    rows: list[dict[str, Any]] = [
        {
            "linha_planilha": anomaly.row_number,
            "erp_code": anomaly.erp_code,
            "device_name": anomaly.device_name,
            "campo": anomaly.field,
            "valor_bruto": anomaly.raw_value,
            "motivo": anomaly.reason,
            "status": anomaly.status,
        }
        for anomaly in anomalies
    ]
    for item in plan_items:
        stats = stats_by_key.get(item.search_key)
        if stats is not None and stats.n > 0:
            continue
        for key in item.report_keys:
            rows.append(
                {
                    "linha_planilha": key.row_number,
                    "erp_code": key.erp_code,
                    "device_name": key.device_name,
                    "campo": "amostra",
                    "valor_bruto": "0",
                    "motivo": "sem_amostra",
                    "status": "revisao_humana",
                }
            )
    return rows
