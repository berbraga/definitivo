"""Copy the input template and append result columns to the copy.

The input file is never opened for writing. shutil.copy2 duplicates it byte for
byte, preserving the help row, the 19 original columns and the sheet name, and
only the copy is edited.
"""

import shutil
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from renov_market_scan.ingest.reader import EXPECTED_HEADER, HEADER_ROW, find_data_sheet

EXTRA_COLUMNS: tuple[str, ...] = (
    "n_amostras_mercado",
    "preco_minimo_mercado",
    "mediana_mercado",
    "preco_maximo_mercado",
    "link_minimo_mercado",
    "link_maximo_mercado",
    "razao_mediana_vs_atual",
    "status_mercado",
)

# Maps an extra column to the summary-row field that feeds it.
EXTRA_COLUMN_SOURCES: dict[str, str] = {
    "n_amostras_mercado": "n_amostras",
    "preco_minimo_mercado": "preco_minimo",
    "mediana_mercado": "mediana",
    "preco_maximo_mercado": "preco_maximo",
    "link_minimo_mercado": "link_minimo",
    "link_maximo_mercado": "link_maximo",
    "razao_mediana_vs_atual": "razao_mediana_vs_atual",
    "status_mercado": "status",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def write_template_copy(
    source_path: Path,
    destination_path: Path,
    summary_by_key: dict[tuple[str, str, str], dict[str, Any]],
) -> Path:
    """Duplicate the template and fill the appended columns.

    summary_by_key is indexed by (ERP Code, Model, Storage label) - the report
    key - because ERP Code alone is not unique in the source data.
    """
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination_path)

    workbook = load_workbook(destination_path)
    try:
        sheet = workbook[find_data_sheet(workbook)]

        # Inserted right after "Maximum Price" (not appended past "ERP Code")
        # so the market-research columns sit next to the template's own
        # Minimum/Maximum Price columns for easy visual comparison. Those two
        # columns keep their original meaning (buyback price floor/ceiling)
        # untouched — only columns to their right shift over.
        first_extra = EXPECTED_HEADER.index("Maximum Price") + 2
        sheet.insert_cols(first_extra, amount=len(EXTRA_COLUMNS))

        for offset, name in enumerate(EXTRA_COLUMNS):
            sheet.cell(row=HEADER_ROW, column=first_extra + offset).value = name

        erp_index = EXPECTED_HEADER.index("ERP Code") + 1 + len(EXTRA_COLUMNS)
        model_index = EXPECTED_HEADER.index("Model*") + 1
        storage_index = EXPECTED_HEADER.index("Storage, GB*") + 1

        for row_index in range(HEADER_ROW + 1, sheet.max_row + 1):
            erp = _text(sheet.cell(row=row_index, column=erp_index).value)
            model = _text(sheet.cell(row=row_index, column=model_index).value)
            storage_raw = _text(sheet.cell(row=row_index, column=storage_index).value)
            if not erp and not model:
                continue

            summary = None
            for label in (storage_raw, f"{storage_raw}GB", "1TB", "2TB"):
                summary = summary_by_key.get((erp, model, label))
                if summary is not None:
                    break
            if summary is None:
                continue

            for offset, name in enumerate(EXTRA_COLUMNS):
                field = EXTRA_COLUMN_SOURCES[name]
                sheet.cell(row=row_index, column=first_extra + offset).value = summary.get(field)

        workbook.save(destination_path)
    finally:
        workbook.close()
    return destination_path
