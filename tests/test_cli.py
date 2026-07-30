from typer.testing import CliRunner

from renov_market_scan.cli import app

runner = CliRunner()


def test_help_is_in_portuguese():
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--somente-ativos" in result.output
    assert "--dry-run" in result.output
    assert "--retomar" in result.output
    assert "--reprocessar-filtro" in result.output


def test_dry_run_prints_the_plan_and_cost_and_spends_nothing(
    tmp_path, make_sheet, device_row_dict, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    source = make_sheet(tmp_path, [device_row_dict()])
    result = runner.invoke(
        app,
        [
            "run",
            "--input",
            str(source),
            "--output",
            str(tmp_path / "out"),
            "--cache",
            str(tmp_path / "scan.sqlite"),
            "--fontes",
            "olx",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "Chamadas a API" in result.output
    assert "US$" in result.output
    assert not (tmp_path / "out").exists()


def test_dry_run_reports_the_active_model_count(
    tmp_path, make_sheet, device_row_dict, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    rows = [device_row_dict(**{"Model*": f"GALAXY A{i}", "ERP Code": f"E{i}"}) for i in range(3)]
    rows.append(
        device_row_dict(**{"Model*": "INATIVO", "ERP Code": "E9", "Price for In-store": 10})
    )
    source = make_sheet(tmp_path, rows)
    result = runner.invoke(
        app,
        [
            "run",
            "--input",
            str(source),
            "--output",
            str(tmp_path / "out"),
            "--cache",
            str(tmp_path / "scan.sqlite"),
            "--fontes",
            "olx",
            "--dry-run",
        ],
    )
    assert "3" in result.output


def test_a_missing_input_file_fails_with_a_clear_message(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    result = runner.invoke(
        app, ["run", "--input", str(tmp_path / "nao-existe.xlsx"), "--dry-run"]
    )
    assert result.exit_code != 0


def test_a_real_run_requires_confirmation_and_aborts_on_no(
    tmp_path, make_sheet, device_row_dict, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    source = make_sheet(tmp_path, [device_row_dict()])
    result = runner.invoke(
        app,
        [
            "run",
            "--input",
            str(source),
            "--output",
            str(tmp_path / "out"),
            "--cache",
            str(tmp_path / "scan.sqlite"),
            "--fontes",
            "olx",
        ],
        input="n\n",
    )
    assert result.exit_code != 0
    assert not (tmp_path / "out").exists()
