import pytest

from renov_market_scan.ingest.reader import SheetFormatError, read_device_rows


def test_reads_data_from_row_three_onward(tmp_path, make_sheet, device_row_dict):
    path = make_sheet(tmp_path, [device_row_dict(), device_row_dict(**{"ERP Code": "10871A0"})])
    rows, warnings = read_device_rows(path)
    assert len(rows) == 2
    assert rows[0].row_number == 3
    assert rows[1].row_number == 4
    assert rows[0].erp_code == "10870A0"
    assert warnings == []


def test_sheet_name_is_not_hardcoded(tmp_path, make_sheet, device_row_dict):
    path = make_sheet(tmp_path, [device_row_dict()], sheet_name="iPhones")
    rows, _ = read_device_rows(path)
    assert len(rows) == 1


def test_empty_ram_and_any_color_are_preserved_as_read(tmp_path, make_sheet, device_row_dict):
    path = make_sheet(tmp_path, [device_row_dict()])
    rows, _ = read_device_rows(path)
    assert rows[0].ram_raw is None
    assert rows[0].color == "Any Color"


def test_storage_is_kept_raw_as_string(tmp_path, make_sheet, device_row_dict):
    path = make_sheet(tmp_path, [device_row_dict(**{"Storage, GB*": 1024})])
    rows, _ = read_device_rows(path)
    assert rows[0].storage_raw == "1024"


def test_price_divergence_produces_a_warning(tmp_path, make_sheet, device_row_dict):
    path = make_sheet(
        tmp_path,
        [device_row_dict(**{"Price for In-store": 400, "Price for Widget & Mobile": 350})],
    )
    rows, warnings = read_device_rows(path)
    assert len(rows) == 1
    assert len(warnings) == 1
    assert "linha 3" in warnings[0]
    assert "400" in warnings[0] and "350" in warnings[0]


def test_fully_empty_rows_are_skipped(tmp_path, make_sheet, device_row_dict):
    path = make_sheet(tmp_path, [device_row_dict(), {}, device_row_dict()])
    rows, _ = read_device_rows(path)
    assert len(rows) == 2


def test_wrong_header_raises_with_the_offending_columns(tmp_path, make_sheet, device_row_dict):
    bad_header = ("Nome",) + tuple(f"c{i}" for i in range(18))
    path = make_sheet(tmp_path, [device_row_dict()], header=bad_header)
    with pytest.raises(SheetFormatError, match="Device name"):
        read_device_rows(path)


def test_input_file_is_not_modified(tmp_path, make_sheet, device_row_dict):
    import hashlib

    path = make_sheet(tmp_path, [device_row_dict()])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    read_device_rows(path)
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert before == after
