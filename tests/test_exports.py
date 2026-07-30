import csv
import json

from renov_market_scan.report.exports import write_csv, write_json


def test_csv_writes_the_declared_columns_in_order(tmp_path):
    path = tmp_path / "resumo.csv"
    write_csv(path, [{"b": 2, "a": 1}], ("a", "b"))
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["a", "b"]
    assert rows[1] == ["1", "2"]


def test_csv_writes_only_the_header_for_an_empty_list(tmp_path):
    path = tmp_path / "resumo.csv"
    write_csv(path, [], ("a", "b"))
    assert path.read_text(encoding="utf-8").strip() == "a,b"


def test_csv_renders_none_as_an_empty_field(tmp_path):
    path = tmp_path / "resumo.csv"
    write_csv(path, [{"a": None, "b": 2}], ("a", "b"))
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[1] == ["", "2"]


def test_json_round_trips_with_accents_intact(tmp_path):
    path = tmp_path / "resultado.json"
    write_json(path, {"observacao": "condição não avaliada"})
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["observacao"] == "condição não avaliada"
