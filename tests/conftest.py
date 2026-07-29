"""Shared fixtures. No test in this suite touches the network."""

from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from renov_market_scan.ingest.reader import EXPECTED_HEADER

HELP_ROW_FIRST_CELL = "Custom name of a device*\nMaximum 255 characters"


@pytest.fixture
def make_sheet():
    """Build a spreadsheet with the real template layout: help row, header, data."""

    def _make(
        tmp_path: Path,
        rows: list[dict[str, Any]],
        sheet_name: str = "Planilha1",
        header: tuple[str, ...] | None = None,
        filename: str = "entrada.xlsx",
    ) -> Path:
        columns = header if header is not None else EXPECTED_HEADER
        wb = Workbook()
        ws = wb.active
        ws.title = sheet_name
        help_row = [HELP_ROW_FIRST_CELL] + [""] * (len(columns) - 1)
        ws.append(help_row)
        ws.append(list(columns))
        for row in rows:
            ws.append([row.get(col) for col in columns])
        path = tmp_path / filename
        wb.save(path)
        wb.close()
        return path

    return _make


@pytest.fixture
def device_row_dict():
    """A complete, valid data row. Override fields per test."""

    def _make(**overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "Device name*": "SAMSUNG GALAXY A17 128GB A0",
            "Manufacturer*": "SAMSUNG",
            "Model*": "GALAXY A17",
            "Device type*": "Phone",
            "Storage, GB*": 128,
            "Ram, GB*": None,
            "Color*": "Any Color",
            "Price for In-store": 400,
            "Price for Widget & Mobile": 400,
            "Buying": "Online, Offline",
            "Questionnaire Widget": None,
            "Questionnaire In-store": None,
            "Questionnaire Mobile": None,
            "Questionnaire robot": None,
            "Priority": 10,
            "Reward Types": "Trade-In",
            "Minimum Price": 0.00,
            "Maximum Price": 0.00,
            "ERP Code": "10870A0",
        }
        base.update(overrides)
        return base

    return _make
