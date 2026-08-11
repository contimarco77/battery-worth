"""CLI tests: argument handling, error messages and exit codes.

The column-mapping error gets disproportionate coverage on purpose — it is the
first thing a new user hits when their CSV is named differently, and a bad
message there is the difference between a fixed flag and a closed tab.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from battery_worth.cli import USER_ERROR_EXIT_CODE, app

runner = CliRunner()


def write_csv(path: Path, columns: dict[str, str], days: int = 60) -> Path:
    """Write a synthetic CSV with the given column NAMES mapped to roles.

    `columns` maps role -> desired column name, roles being 'timestamp',
    'consumption', 'pv_production', 'grid_import', 'grid_export'.
    """
    hours = days * 24
    idx = pd.date_range("2025-01-01", periods=hours, freq="h")
    hour_of_day = np.asarray(idx.hour)
    pv = np.where((hour_of_day >= 9) & (hour_of_day < 15), 3.0, 0.0)
    load = np.full(hours, 1.0)
    net = load - pv

    data: dict[str, object] = {columns["timestamp"]: idx.strftime("%Y-%m-%d %H:%M:%S")}
    if "consumption" in columns:
        data[columns["consumption"]] = load
    if "grid_import" in columns:
        data[columns["grid_import"]] = np.clip(net, 0.0, None)
    if "grid_export" in columns:
        data[columns["grid_export"]] = np.clip(-net, 0.0, None)
    data[columns["pv_production"]] = pv

    pd.DataFrame(data).to_csv(path, index=False)
    return path


@pytest.fixture
def meter_csv(tmp_path: Path) -> Path:
    return write_csv(
        tmp_path / "meter.csv",
        {"timestamp": "timestamp", "consumption": "consumption", "pv_production": "pv_production"},
    )


@pytest.fixture
def grid_csv(tmp_path: Path) -> Path:
    return write_csv(
        tmp_path / "grid.csv",
        {
            "timestamp": "timestamp",
            "grid_import": "grid_import",
            "grid_export": "grid_export",
            "pv_production": "pv_production",
        },
    )


def test_analyze_runs_end_to_end_meter_schema(meter_csv: Path) -> None:
    result = runner.invoke(
        app, ["analyze", str(meter_csv), "--capacities", "0,5,10", "--flat-price", "0.25"]
    )

    assert result.exit_code == 0, result.output
    assert "SCENARIO COMPARISON" in result.output
    assert "LIMITS & ASSUMPTIONS" in result.output
    assert "meter_centric" in result.output
    assert "baseline" in result.output


def test_analyze_runs_end_to_end_grid_schema(grid_csv: Path) -> None:
    result = runner.invoke(
        app, ["analyze", str(grid_csv), "--capacities", "5", "--flat-price", "0.25"]
    )

    assert result.exit_code == 0, result.output
    assert "grid_centric" in result.output


def test_payback_shown_when_cost_given(meter_csv: Path) -> None:
    result = runner.invoke(
        app,
        [
            "analyze", str(meter_csv), "--capacities", "5", "--flat-price", "0.25",
            "--battery-cost-per-kwh", "600",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "3,000" in result.output  # 5 kWh * 600
    assert "Best payback" in result.output


def test_missing_file_is_user_error(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["analyze", str(tmp_path / "nope.csv"), "--flat-price", "0.25"]
    )

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "not found" in result.output


def test_unrecognised_columns_list_header_and_both_schemas(tmp_path: Path) -> None:
    """The key first-run failure: the message must show what was found AND what is accepted."""
    path = write_csv(
        tmp_path / "weird.csv",
        {"timestamp": "Date", "consumption": "Usage", "pv_production": "Generation"},
    )

    result = runner.invoke(app, ["analyze", str(path), "--flat-price", "0.25"])

    assert result.exit_code == USER_ERROR_EXIT_CODE
    # the columns actually found
    assert "'Date'" in result.output
    assert "'Usage'" in result.output
    assert "'Generation'" in result.output
    # both accepted schemas
    assert "grid-centric" in result.output
    assert "meter-centric" in result.output
    # and how to fix it
    assert "--col-timestamp" in result.output


def test_column_overrides_make_a_nonstandard_csv_work(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "weird.csv",
        {"timestamp": "Date", "consumption": "Usage", "pv_production": "Generation"},
    )

    result = runner.invoke(
        app,
        [
            "analyze", str(path), "--flat-price", "0.25",
            "--col-timestamp", "Date",
            "--col-consumption", "Usage",
            "--col-pv-production", "Generation",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "SCENARIO COMPARISON" in result.output


def test_wrong_override_names_missing_column_and_header(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "weird.csv",
        {"timestamp": "Date", "consumption": "Usage", "pv_production": "Generation"},
    )

    result = runner.invoke(
        app,
        [
            "analyze", str(path), "--flat-price", "0.25",
            "--col-timestamp", "Date",
            "--col-consumption", "Consumo",
            "--col-pv-production", "Generation",
        ],
    )

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "'Consumo'" in result.output
    assert "'Usage'" in result.output  # what was actually there


def test_no_tariff_is_rejected(meter_csv: Path) -> None:
    result = runner.invoke(app, ["analyze", str(meter_csv)])

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "No tariff specified" in result.output


def test_flat_and_bands_together_rejected(meter_csv: Path) -> None:
    result = runner.invoke(
        app,
        [
            "analyze", str(meter_csv), "--flat-price", "0.25",
            "--f1", "0.35", "--f2", "0.30", "--f3", "0.25",
        ],
    )

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "Incompatible tariff options" in result.output
    assert "--flat-price" in result.output


def test_flat_and_prices_csv_together_rejected(meter_csv: Path, tmp_path: Path) -> None:
    prices = tmp_path / "prices.csv"
    prices.write_text("timestamp,price\n2025-01-01 00:00:00,0.25\n")

    result = runner.invoke(
        app,
        ["analyze", str(meter_csv), "--flat-price", "0.25", "--prices-csv", str(prices)],
    )

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "Incompatible tariff options" in result.output


def test_partial_bands_rejected(meter_csv: Path) -> None:
    result = runner.invoke(
        app, ["analyze", str(meter_csv), "--f1", "0.35", "--f2", "0.30"]
    )

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "--f3" in result.output


def test_band_tariff_runs(meter_csv: Path) -> None:
    result = runner.invoke(
        app,
        [
            "analyze", str(meter_csv), "--capacities", "5",
            "--f1", "0.35", "--f2", "0.30", "--f3", "0.25",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Italian bands" in result.output


def test_hourly_price_csv_runs(meter_csv: Path, tmp_path: Path) -> None:
    """A price file covering the whole analysis period prices every hour."""
    idx = pd.date_range("2025-01-01", periods=60 * 24, freq="h")
    prices = tmp_path / "prices.csv"
    pd.DataFrame(
        {"timestamp": idx.strftime("%Y-%m-%d %H:%M:%S"), "price": 0.25}
    ).to_csv(prices, index=False)

    result = runner.invoke(
        app, ["analyze", str(meter_csv), "--capacities", "5", "--prices-csv", str(prices)]
    )

    assert result.exit_code == 0, result.output
    assert "hourly prices from" in result.output


def test_missing_price_csv_is_user_error(meter_csv: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["analyze", str(meter_csv), "--prices-csv", str(tmp_path / "nope.csv")]
    )

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "not found" in result.output


def test_price_csv_not_covering_period_is_user_error(meter_csv: Path, tmp_path: Path) -> None:
    """A short price file must fail loudly rather than silently pricing part of the year."""
    idx = pd.date_range("2025-01-01", periods=48, freq="h")
    prices = tmp_path / "short.csv"
    pd.DataFrame(
        {"timestamp": idx.strftime("%Y-%m-%d %H:%M:%S"), "price": 0.25}
    ).to_csv(prices, index=False)

    result = runner.invoke(
        app, ["analyze", str(meter_csv), "--prices-csv", str(prices)]
    )

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "does not cover" in result.output


def test_bad_capacities_rejected(meter_csv: Path) -> None:
    result = runner.invoke(
        app, ["analyze", str(meter_csv), "--flat-price", "0.25", "--capacities", "5,abc"]
    )

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "not a number" in result.output


def test_negative_capacity_rejected(meter_csv: Path) -> None:
    result = runner.invoke(
        app, ["analyze", str(meter_csv), "--flat-price", "0.25", "--capacities", "5,-2"]
    )

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "zero or positive" in result.output


def test_invalid_battery_parameters_rejected(meter_csv: Path) -> None:
    result = runner.invoke(
        app, ["analyze", str(meter_csv), "--flat-price", "0.25", "--efficiency", "1.5"]
    )

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "Invalid battery parameters" in result.output


def test_too_short_dataset_rejected(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "short.csv",
        {"timestamp": "timestamp", "consumption": "consumption", "pv_production": "pv_production"},
        days=10,
    )

    result = runner.invoke(app, ["analyze", str(path), "--flat-price", "0.25"])

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "30 days" in result.output


def test_seasonality_warning_shown_prominently(meter_csv: Path) -> None:
    """60 days of data must trigger the loud block, not just a line in the warning list."""
    result = runner.invoke(
        app, ["analyze", str(meter_csv), "--capacities", "5", "--flat-price", "0.25"]
    )

    assert result.exit_code == 0, result.output
    assert "SEASONALITY WARNING" in result.output


def test_all_ingest_warnings_are_printed_verbatim(tmp_path: Path) -> None:
    """Warnings are contractually never swallowed: every ingest warning must appear."""
    path = write_csv(
        tmp_path / "zeros.csv",
        {"timestamp": "timestamp", "consumption": "consumption", "pv_production": "pv_production"},
    )
    df = pd.read_csv(path)
    df["pv_production"] = 0.0  # triggers the all-zero column warning
    df.to_csv(path, index=False)

    result = runner.invoke(
        app, ["analyze", str(path), "--capacities", "5", "--flat-price", "0.25"]
    )

    assert result.exit_code == 0, result.output
    assert "WARNINGS" in result.output
    assert "is all zero for the whole period" in result.output


def test_mixed_column_schemas_rejected(meter_csv: Path) -> None:
    result = runner.invoke(
        app,
        [
            "analyze", str(meter_csv), "--flat-price", "0.25",
            "--col-consumption", "consumption", "--col-grid-import", "grid_import",
        ],
    )

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "Incompatible column options" in result.output


def test_empty_csv_is_user_error(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("")

    result = runner.invoke(app, ["analyze", str(path), "--flat-price", "0.25"])

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "empty" in result.output


def test_analyze_is_an_explicit_subcommand(meter_csv: Path) -> None:
    """The locked surface is `battery-worth analyze <file>`, not `battery-worth <file>`."""
    result = runner.invoke(app, [str(meter_csv), "--flat-price", "0.25"])
    assert result.exit_code != 0


def test_output_writes_a_markdown_report(meter_csv: Path, tmp_path: Path) -> None:
    """--output writes the file; the terminal output must be unchanged by it."""
    target = tmp_path / "report.md"
    result = runner.invoke(
        app,
        ["analyze", str(meter_csv), "--flat-price", "0.25", "--capacities", "0,5,10",
         "--battery-cost-per-kwh", "600", "--output", str(target)],
    )

    assert result.exit_code == 0, result.output
    assert target.exists()
    written = target.read_text(encoding="utf-8")
    for section in ("## Verdict", "## Scenario comparison", "## Limits & assumptions"):
        assert section in written
    # Terminal output is untouched: still the plain-text report, plus a confirmation.
    assert "SCENARIO COMPARISON" in result.output
    assert f"Report written to {target}" in result.output


def test_terminal_output_identical_with_and_without_output(
    meter_csv: Path, tmp_path: Path
) -> None:
    """--output is purely additive: it must not reshape what lands on stdout.

    Both artifacts it produces — the report and the summary card beside it —
    announce themselves as trailing lines and change nothing above them.
    """
    args = ["analyze", str(meter_csv), "--flat-price", "0.25", "--capacities", "0,5"]
    without = runner.invoke(app, args)
    with_file = runner.invoke(app, [*args, "--output", str(tmp_path / "r.md")])

    assert without.exit_code == 0
    assert with_file.exit_code == 0
    trailers = (
        f"Report written to {tmp_path / 'r.md'}\n"
        f"Summary card written to {tmp_path / 'r.png'}\n"
    )
    assert with_file.output.replace(trailers, "") == without.output


def test_card_is_written_alongside_the_report(meter_csv: Path, tmp_path: Path) -> None:
    """The card ships with the report by default — it is the artifact people share."""
    target = tmp_path / "report.md"
    result = runner.invoke(
        app,
        ["analyze", str(meter_csv), "--flat-price", "0.25", "--capacities", "0,5",
         "--battery-cost-per-kwh", "600", "--output", str(target)],
    )

    card = tmp_path / "report.png"
    assert result.exit_code == 0, result.output
    assert card.exists()
    assert card.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert f"Summary card written to {card}" in result.output


def test_no_card_skips_it(meter_csv: Path, tmp_path: Path) -> None:
    """--no-card leaves the report untouched and writes no PNG."""
    target = tmp_path / "report.md"
    result = runner.invoke(
        app,
        ["analyze", str(meter_csv), "--flat-price", "0.25", "--capacities", "0,5",
         "--output", str(target), "--no-card"],
    )

    assert result.exit_code == 0, result.output
    assert target.exists()
    assert not (tmp_path / "report.png").exists()
    assert "Summary card" not in result.output


def test_no_card_without_output_writes_nothing(meter_csv: Path, tmp_path: Path) -> None:
    """The card rides on --output; without it, nothing is written anywhere.

    `--card` defaults to true, so a plain `analyze` run must not start dropping
    PNGs into the working directory on the strength of a default the user never
    typed.
    """
    result = runner.invoke(
        app, ["analyze", str(meter_csv), "--flat-price", "0.25", "--capacities", "0,5"]
    )

    assert result.exit_code == 0, result.output
    assert "Summary card" not in result.output
    assert not list(tmp_path.glob("*.png"))


def test_card_reports_its_own_write_failure(meter_csv: Path, tmp_path: Path) -> None:
    """A card that cannot be written is a user error naming the card, not the report."""
    target = tmp_path / "sub" / "report.md"
    target.parent.mkdir()
    # A directory where the PNG wants to be: the report writes fine, the card cannot.
    (tmp_path / "sub" / "report.png").mkdir()

    result = runner.invoke(
        app,
        ["analyze", str(meter_csv), "--flat-price", "0.25", "--capacities", "0,5",
         "--output", str(target)],
    )

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "Could not write the summary card" in result.output
    assert target.exists(), "the report still landed before the card failed"


def test_output_to_an_unwritable_path_is_a_user_error(meter_csv: Path, tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    result = runner.invoke(
        app,
        ["analyze", str(meter_csv), "--flat-price", "0.25",
         "--output", str(blocker / "report.md")],
    )

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "Could not write the report" in result.output


def test_export_price_sweep_reaches_the_report(meter_csv: Path, tmp_path: Path) -> None:
    target = tmp_path / "report.md"
    result = runner.invoke(
        app,
        ["analyze", str(meter_csv), "--flat-price", "0.25", "--capacities", "5",
         "--export-price-sweep", "0,0.2", "--output", str(target)],
    )

    assert result.exit_code == 0, result.output
    written = target.read_text(encoding="utf-8")
    assert "0 EUR/kWh" in written
    assert "0.2 EUR/kWh" in written


def test_export_price_sweep_rejects_non_numbers(meter_csv: Path) -> None:
    result = runner.invoke(
        app,
        ["analyze", str(meter_csv), "--flat-price", "0.25",
         "--export-price-sweep", "0.1,cheap"],
    )

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "'cheap'" in result.output


def test_export_price_sweep_rejects_negatives(meter_csv: Path) -> None:
    result = runner.invoke(
        app,
        ["analyze", str(meter_csv), "--flat-price", "0.25",
         "--export-price-sweep", "0.1,-0.2"],
    )

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "zero or positive" in result.output


def test_empty_export_price_sweep_is_a_user_error(meter_csv: Path) -> None:
    result = runner.invoke(
        app,
        ["analyze", str(meter_csv), "--flat-price", "0.25", "--export-price-sweep", " , "],
    )

    assert result.exit_code == USER_ERROR_EXIT_CODE
    assert "--export-price-sweep is empty" in result.output


def test_terminal_and_report_print_the_same_annual_savings(
    meter_csv: Path, tmp_path: Path
) -> None:
    """One run, two outputs: the annualized figures must agree exactly.

    The terminal table and the Markdown report annualize the same scenarios by
    the same rule. When each owned its own copy of the constant, a change to one
    would have silently printed two different annual savings for a single run.
    """
    report_path = tmp_path / "report.md"
    result = runner.invoke(
        app,
        ["analyze", str(meter_csv), "--capacities", "5,10", "--flat-price", "0.25",
         "--battery-cost-per-kwh", "600", "--output", str(report_path)],
    )

    assert result.exit_code == 0, result.output
    markdown = report_path.read_text(encoding="utf-8")

    # The terminal prints "  5 kWh   <savings>€ ..."; the report "| 5 kWh | <savings> EUR |".
    for capacity in ("5", "10"):
        terminal_row = next(
            line for line in result.output.splitlines()
            if line.strip().startswith(f"{capacity} kWh")
        )
        report_row = next(
            line for line in markdown.splitlines() if line.startswith(f"| {capacity} kWh |")
        )
        terminal_savings = terminal_row.split("€")[0].split()[-1]
        report_savings = report_row.split("|")[2].strip().removesuffix(" EUR")

        assert terminal_savings == report_savings, (
            f"{capacity} kWh: terminal says {terminal_savings}, report says {report_savings}"
        )
