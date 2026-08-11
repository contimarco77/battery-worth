"""battery-worth CLI.

v0 surface (locked scope):
    battery-worth analyze data.csv --capacities 5,10,15,20 --flat-price 0.28 \
        --export-price 0.10 --battery-cost-per-kwh 500

Output is plain text on stdout — no rich, no tables dependency. The jinja2 report
and the PNG summary card are milestone 2; `--llm` commentary is milestone 3.

Exit codes: 0 on success, 2 on user error (bad file, bad columns, bad config).
Anything the user can fix by changing a flag or their CSV is a user error and is
reported as a single readable message, never a traceback.
"""

from __future__ import annotations

import sys
import textwrap
import warnings
from pathlib import Path
from typing import Annotated, NoReturn

import pandas as pd
import typer

from battery_worth.analysis import run_analysis
from battery_worth.ingest import load_energy_data
from battery_worth.models import (
    AnalysisResult,
    BatterySpec,
    ColumnMapping,
    IngestReport,
    ScenarioResult,
    Tariff,
    TariffKind,
)

app = typer.Typer(
    name="battery-worth",
    help="Would a home battery have paid off for YOU? Retrospective analysis "
    "from your real energy data.",
    no_args_is_help=True,
)


@app.callback()
def _root() -> None:
    """Keep `analyze` an explicit subcommand.

    Typer promotes a single command to the top level, which would make the locked
    surface `battery-worth <file>` instead of `battery-worth analyze <file>`. An
    empty root callback keeps the app in multi-command mode, leaving room for the
    milestone-3 subcommands without a breaking change.
    """

USER_ERROR_EXIT_CODE = 2

DEFAULT_TIMESTAMP_COL = "timestamp"
DEFAULT_GRID_IMPORT_COL = "grid_import"
DEFAULT_GRID_EXPORT_COL = "grid_export"
DEFAULT_PV_COL = "pv_production"
DEFAULT_CONSUMPTION_COL = "consumption"

_DAYS_PER_YEAR = 365.25
_HEADER_SAMPLE_LIMIT = 40


def _fail(message: str) -> NoReturn:
    """Report a user-fixable problem and exit non-zero, without a traceback."""
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=USER_ERROR_EXIT_CODE)


@app.command()
def analyze(  # noqa: PLR0913, PLR0917 - Typer derives the CLI surface from these parameters
    data: Annotated[Path, typer.Argument(help="CSV with timestamp, import, export, PV columns")],
    capacities: Annotated[
        str, typer.Option(help="Comma-separated usable capacities in kWh to sweep (0 = baseline)")
    ] = "0,5,10,15",
    # --- tariff ---
    flat_price: Annotated[
        float | None, typer.Option(help="Flat import price, EUR/kWh")
    ] = None,
    f1: Annotated[float | None, typer.Option(help="Italian F1 (peak) price, EUR/kWh")] = None,
    f2: Annotated[float | None, typer.Option(help="Italian F2 (mid) price, EUR/kWh")] = None,
    f3: Annotated[float | None, typer.Option(help="Italian F3 (off-peak) price, EUR/kWh")] = None,
    prices_csv: Annotated[
        Path | None, typer.Option(help="CSV of hourly import prices (PUN / dynamic tariff)")
    ] = None,
    prices_timestamp_col: Annotated[
        str, typer.Option(help="Timestamp column in the hourly price CSV")
    ] = "timestamp",
    prices_price_col: Annotated[
        str, typer.Option(help="Price column (EUR/kWh) in the hourly price CSV")
    ] = "price",
    export_price: Annotated[float, typer.Option(help="Export remuneration, EUR/kWh")] = 0.10,
    # --- battery ---
    battery_cost_per_kwh: Annotated[
        float | None, typer.Option(help="Installed cost per usable kWh, for payback")
    ] = None,
    charge_power: Annotated[float, typer.Option(help="Max charge power, kW")] = 5.0,
    discharge_power: Annotated[float, typer.Option(help="Max discharge power, kW")] = 5.0,
    efficiency: Annotated[float, typer.Option(help="Round-trip efficiency, 0-1")] = 0.90,
    min_soc: Annotated[float, typer.Option(help="Minimum state of charge, fraction 0-1")] = 0.0,
    # --- data ---
    timezone: Annotated[str, typer.Option(help="IANA timezone of the data")] = "Europe/Rome",
    col_timestamp: Annotated[str, typer.Option(help="Timestamp column name")] = (
        DEFAULT_TIMESTAMP_COL
    ),
    col_grid_import: Annotated[str | None, typer.Option(help="Grid import column name")] = None,
    col_grid_export: Annotated[str | None, typer.Option(help="Grid export column name")] = None,
    col_pv_production: Annotated[str, typer.Option(help="PV production column name")] = (
        DEFAULT_PV_COL
    ),
    col_consumption: Annotated[str | None, typer.Option(help="Consumption column name")] = None,
) -> None:
    """Run the retrospective what-if analysis and print the results."""
    tariff = _build_tariff(
        flat_price=flat_price,
        f1=f1,
        f2=f2,
        f3=f3,
        prices_csv=prices_csv,
        prices_timestamp_col=prices_timestamp_col,
        prices_price_col=prices_price_col,
        export_price=export_price,
    )
    capacity_list = _parse_capacities(capacities)

    try:
        template = BatterySpec(
            usable_capacity_kwh=1.0,  # placeholder: the sweep overrides it per scenario
            max_charge_kw=charge_power,
            max_discharge_kw=discharge_power,
            round_trip_efficiency=efficiency,
            min_soc=min_soc,
        )
    except ValueError as exc:
        _fail(f"Invalid battery parameters: {exc}")

    header = _read_header(data)
    mapping = _resolve_mapping(
        header=header,
        path=data,
        timestamp=col_timestamp,
        grid_import=col_grid_import,
        grid_export=col_grid_export,
        pv_production=col_pv_production,
        consumption=col_consumption,
    )

    # Ingest and tariff resolution both warn through the warnings module; capture
    # them so they can be printed in the report body instead of interleaving with it.
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        try:
            df, report = load_energy_data(data, mapping, timezone=timezone)
            result = run_analysis(
                df,
                report,
                capacities=capacity_list,
                battery_template=template,
                tariff=tariff,
                battery_cost_per_kwh=battery_cost_per_kwh,
            )
        except ValueError as exc:
            _fail(str(exc))
        except (OSError, KeyError) as exc:
            _fail(f"Could not read '{data}': {exc}")
    runtime_warnings = [str(w.message) for w in captured]

    _print_report(result, report, tariff, runtime_warnings)


def _parse_capacities(raw: str) -> list[float]:
    """Parse the comma-separated --capacities list into floats."""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        _fail("--capacities is empty. Pass something like --capacities 0,5,10,15.")

    values: list[float] = []
    for part in parts:
        try:
            values.append(float(part))
        except ValueError:
            _fail(
                f"--capacities contains '{part}', which is not a number. "
                "Expected a comma-separated list of kWh values, e.g. 0,5,10,15."
            )
    if any(v < 0 for v in values):
        _fail("--capacities must be zero or positive kWh values.")
    return values


def _build_tariff(  # noqa: PLR0913, PLR0917 - one parameter per CLI tariff flag
    *,
    flat_price: float | None,
    f1: float | None,
    f2: float | None,
    f3: float | None,
    prices_csv: Path | None,
    prices_timestamp_col: str,
    prices_price_col: str,
    export_price: float,
) -> Tariff:
    """Turn the mutually exclusive tariff flags into exactly one Tariff.

    The three tariff kinds are alternatives, not layers: silently letting one win
    over another would price the whole analysis on a tariff the user did not think
    they had chosen, so any combination is refused outright.
    """
    band_flags = [f1, f2, f3]
    uses_flat = flat_price is not None
    uses_bands = any(v is not None for v in band_flags)
    uses_csv = prices_csv is not None

    selected = [
        name
        for name, used in (("--flat-price", uses_flat), ("--f1/--f2/--f3", uses_bands),
                           ("--prices-csv", uses_csv))
        if used
    ]
    if len(selected) > 1:
        _fail(
            f"Incompatible tariff options: {' and '.join(selected)} were all given. "
            "Choose exactly one tariff: a flat price (--flat-price), Italian time bands "
            "(--f1 --f2 --f3), or an hourly price CSV (--prices-csv)."
        )
    if not selected:
        _fail(
            "No tariff specified. Choose one: --flat-price 0.28, or --f1 0.35 --f2 0.30 "
            "--f3 0.25, or --prices-csv prices.csv."
        )

    if uses_bands and any(v is None for v in band_flags):
        missing = [
            name for name, value in (("--f1", f1), ("--f2", f2), ("--f3", f3)) if value is None
        ]
        _fail(
            f"The Italian band tariff needs all three prices; missing: {', '.join(missing)}. "
            "Pass --f1, --f2 and --f3 together."
        )

    try:
        if uses_flat:
            return Tariff(
                kind=TariffKind.FLAT,
                flat_price_eur_kwh=flat_price,
                export_price_eur_kwh=export_price,
            )
        if uses_bands:
            return Tariff(
                kind=TariffKind.F1_F2_F3,
                f1_price=f1,
                f2_price=f2,
                f3_price=f3,
                export_price_eur_kwh=export_price,
            )
        assert prices_csv is not None
        if not prices_csv.exists():
            _fail(f"Hourly price CSV not found: '{prices_csv}'. Check the path and try again.")
        return Tariff(
            kind=TariffKind.HOURLY_CSV,
            hourly_prices_csv=str(prices_csv),
            hourly_prices_timestamp_column=prices_timestamp_col,
            hourly_prices_price_column=prices_price_col,
            export_price_eur_kwh=export_price,
        )
    except ValueError as exc:
        _fail(f"Invalid tariff configuration: {exc}")


def _read_header(path: Path) -> list[str]:
    """Read just the CSV header, so column problems are reported before a full parse."""
    try:
        head = pd.read_csv(path, nrows=0)
    except FileNotFoundError:
        _fail(f"CSV file not found: '{path}'. Check the path and try again.")
    except pd.errors.EmptyDataError:
        _fail(f"'{path}' is empty — it has no header row and no data.")
    except (OSError, UnicodeDecodeError, pd.errors.ParserError) as exc:
        _fail(f"Could not read '{path}' as CSV: {exc}")
    return [str(c) for c in head.columns]


def _resolve_mapping(  # noqa: PLR0913, PLR0917 - one parameter per --col-* override
    *,
    header: list[str],
    path: Path,
    timestamp: str,
    grid_import: str | None,
    grid_export: str | None,
    pv_production: str,
    consumption: str | None,
) -> ColumnMapping:
    """Pick the input schema from the CSV header, honouring any explicit --col-* overrides.

    Explicit overrides win and are never second-guessed: if the user names a
    consumption column, they get the meter-centric schema even when grid columns
    happen to be present too. Only when nothing is overridden does the header
    decide, preferring grid-centric because it is the more informative of the two
    (it carries the real import/export split rather than a derived one).
    """
    explicit_grid = grid_import is not None or grid_export is not None
    explicit_meter = consumption is not None

    if explicit_grid and explicit_meter:
        _fail(
            "Incompatible column options: --col-consumption cannot be combined with "
            "--col-grid-import/--col-grid-export. Choose one schema: grid-centric "
            "(import + export + PV) or meter-centric (consumption + PV)."
        )

    if explicit_grid:
        resolved_import = grid_import or DEFAULT_GRID_IMPORT_COL
        resolved_export = grid_export or DEFAULT_GRID_EXPORT_COL
        candidate = ColumnMapping(
            timestamp=timestamp,
            grid_import=resolved_import,
            grid_export=resolved_export,
            pv_production=pv_production,
        )
    elif explicit_meter:
        candidate = ColumnMapping(
            timestamp=timestamp, consumption=consumption, pv_production=pv_production
        )
    else:
        candidate = _detect_mapping(header, path, timestamp, pv_production)

    missing = [c for c in _mapping_columns(candidate) if c not in header]
    if missing:
        _fail(_columns_error(path, header, missing))
    return candidate


def _detect_mapping(
    header: list[str], path: Path, timestamp: str, pv_production: str
) -> ColumnMapping:
    """Choose a schema from the conventional column names present in the header."""
    has_grid = DEFAULT_GRID_IMPORT_COL in header and DEFAULT_GRID_EXPORT_COL in header
    has_meter = DEFAULT_CONSUMPTION_COL in header

    if has_grid:
        return ColumnMapping(
            timestamp=timestamp,
            grid_import=DEFAULT_GRID_IMPORT_COL,
            grid_export=DEFAULT_GRID_EXPORT_COL,
            pv_production=pv_production,
        )
    if has_meter:
        return ColumnMapping(
            timestamp=timestamp, consumption=DEFAULT_CONSUMPTION_COL, pv_production=pv_production
        )
    _fail(_columns_error(path, header, missing=None))


def _mapping_columns(mapping: ColumnMapping) -> list[str]:
    columns = [mapping.timestamp, mapping.pv_production]
    if mapping.grid_import is not None:
        columns.append(mapping.grid_import)
    if mapping.grid_export is not None:
        columns.append(mapping.grid_export)
    if mapping.consumption is not None:
        columns.append(mapping.consumption)
    return columns


def _columns_error(path: Path, header: list[str], missing: list[str] | None) -> str:
    """The single most likely first-run failure, so it spells out both accepted schemas.

    Always lists the header actually found: telling a user their columns are wrong
    without showing what was read leaves them guessing at whitespace, casing or a
    wrong delimiter.
    """
    shown = header[:_HEADER_SAMPLE_LIMIT]
    found = ", ".join(repr(c) for c in shown) if shown else "(no columns)"
    if len(header) > _HEADER_SAMPLE_LIMIT:
        found += f", ... ({len(header)} columns total)"

    lead = (
        f"Column(s) not found in '{path}': {', '.join(repr(c) for c in missing)}."
        if missing
        else f"Could not recognise the columns in '{path}'."
    )
    return (
        f"{lead}\n\n"
        f"  Columns found in the file:\n    {found}\n\n"
        "  battery-worth accepts either of these two schemas:\n\n"
        f"    grid-centric   {DEFAULT_TIMESTAMP_COL}, {DEFAULT_GRID_IMPORT_COL}, "
        f"{DEFAULT_GRID_EXPORT_COL}, {DEFAULT_PV_COL}\n"
        f"    meter-centric  {DEFAULT_TIMESTAMP_COL}, {DEFAULT_CONSUMPTION_COL}, "
        f"{DEFAULT_PV_COL}\n\n"
        "  If your columns are named differently, map them explicitly, e.g.:\n"
        "    --col-timestamp Date --col-consumption Usage --col-pv-production Generation\n"
        "    --col-timestamp Date --col-grid-import Imported --col-grid-export Exported "
        "--col-pv-production PV"
    )


def _print_report(
    result: AnalysisResult,
    report: IngestReport,
    tariff: Tariff,
    runtime_warnings: list[str],
) -> None:
    """Print the whole plain-text result: data summary, warnings, table, caveats."""
    echo = typer.echo
    echo("")
    echo("=" * 78)
    echo("battery-worth — retrospective battery analysis")
    echo("=" * 78)

    echo("")
    echo("DATA")
    echo(f"  Period            {result.period_start}  ->  {result.period_end}")
    echo(f"  Days analyzed     {result.days_analyzed}")
    echo(f"  Native resolution {report.native_resolution_minutes} min (analyzed hourly)")
    echo(f"  Input schema      {report.schema_used}")
    if report.cumulative_columns:
        echo(f"  Cumulative cols   {', '.join(report.cumulative_columns)} (differenced)")
    if report.gaps_count:
        echo(f"  Gaps              {report.gaps_count} ({report.gaps_total_hours:.1f} h total)")
    if report.negative_values_clipped:
        echo(f"  Negatives clipped {report.negative_values_clipped}")
    echo(f"  Tariff            {_describe_tariff(tariff)}")

    _print_warnings(report.warnings, runtime_warnings)
    _print_scenarios(result)
    _print_seasonality(result)
    _print_limits()


def _print_warnings(ingest_warnings: list[str], runtime_warnings: list[str]) -> None:
    """Print every warning verbatim. Warnings are never summarized or dropped:
    each one describes a decision made about the user's data on their behalf."""
    all_warnings = [*ingest_warnings, *runtime_warnings]
    if not all_warnings:
        return
    echo = typer.echo
    echo("")
    echo(f"WARNINGS ({len(all_warnings)})")
    for i, message in enumerate(all_warnings, start=1):
        wrapped = _wrap(message, width=72, indent="      ")
        echo(f"  {i:>2}. {wrapped.lstrip()}")


def _print_scenarios(result: AnalysisResult) -> None:
    echo = typer.echo
    echo("")
    echo("SCENARIO COMPARISON")
    echo("")
    header = (
        f"  {'Capacity':>9}  {'Savings/yr':>11}  {'Payback':>9}  "
        f"{'Cycles/yr':>10}  {'Self-cons.':>19}  {'Cost':>9}"
    )
    echo(header)
    echo(f"  {'-' * (len(header) - 2)}")

    years = max(result.days_analyzed / _DAYS_PER_YEAR, 1e-9)
    for scenario in result.scenarios:
        echo(f"  {_scenario_row(scenario, years)}")

    echo("")
    echo(f"  Savings and cycles are annualized over {result.days_analyzed} days of data.")
    best = _best_scenario(result.scenarios)
    if best is not None:
        payback = best.payback_years()
        verdict = (
            f"  Best payback: {best.capacity_kwh:g} kWh at {payback:.1f} years"
            if payback is not None
            else f"  Best savings: {best.capacity_kwh:g} kWh"
        )
        echo(verdict)


def _scenario_row(scenario: ScenarioResult, years: float) -> str:
    label = "baseline" if scenario.capacity_kwh == 0 else f"{scenario.capacity_kwh:g} kWh"
    savings_per_year = scenario.savings_eur / years
    cycles_per_year = scenario.battery_cycles / years

    payback = scenario.payback_years()
    if scenario.capacity_kwh == 0:
        payback_text = "-"
    elif payback is None:
        payback_text = "never"
    else:
        payback_text = f"{payback:.1f} y"

    cycles_text = "-" if scenario.capacity_kwh == 0 else f"{cycles_per_year:.0f}"
    cost_text = (
        "-" if scenario.battery_cost_eur is None else f"{scenario.battery_cost_eur:,.0f}"
    )
    self_cons = (
        f"{scenario.self_consumption_before * 100:.0f}% -> "
        f"{scenario.self_consumption_after * 100:.0f}%"
    )

    return (
        f"{label:>9}  {savings_per_year:>10,.0f}€  {payback_text:>9}  "
        f"{cycles_text:>10}  {self_cons:>19}  {cost_text:>9}"
    )


def _best_scenario(scenarios: list[ScenarioResult]) -> ScenarioResult | None:
    """The scenario to highlight: shortest payback if costs are known, else best savings.

    Shortest payback rather than largest savings, because the largest battery
    always saves the most in absolute terms while often being the worst investment
    — that gap is the entire point of the comparison table.
    """
    with_payback = [(s.payback_years(), s) for s in scenarios]
    priced = [(p, s) for p, s in with_payback if p is not None]
    if priced:
        return min(priced, key=lambda pair: pair[0])[1]
    earning = [s for s in scenarios if s.savings_eur > 0]
    if not earning:
        return None
    return max(earning, key=lambda s: s.savings_eur)


def _print_seasonality(result: AnalysisResult) -> None:
    if not result.seasonality_warning:
        return
    echo = typer.echo
    echo("")
    echo("!" * 78)
    echo("!! SEASONALITY WARNING")
    echo(
        _wrap(
            f"Only {result.days_analyzed} days of data were analyzed — less than a full "
            "year. PV production and consumption both swing strongly with the seasons, so "
            "annualizing a partial year can be badly wrong in either direction. Treat these "
            "figures as indicative, not as a basis for a purchase decision.",
            width=74,
            indent="!! ",
        )
    )
    echo("!" * 78)


def _print_limits() -> None:
    echo = typer.echo
    echo("")
    echo("LIMITS & ASSUMPTIONS")
    for line in (
        "Greedy self-consumption only: the battery charges from PV surplus and "
        "discharges to cover deficits. No tariff arbitrage (charging from a cheap "
        "grid to discharge later) is modelled.",
        "No battery degradation: capacity is assumed constant for the whole life. "
        "Real batteries lose capacity, so long paybacks are optimistic.",
        "No energy price inflation: today's tariff is applied to every year. Rising "
        "prices would shorten the payback, falling prices lengthen it.",
        "Naive payback: battery cost divided by year-1 savings. No discounting, no "
        "financing cost, no incentives or tax deductions.",
        "Hourly netting: sub-hourly data is summed to hourly before simulating, so "
        "surplus and deficit within the same hour cancel out. This slightly "
        "understates what a battery would do — a conservative direction.",
    ):
        echo(_wrap(f"  - {line}", width=76, indent="    "))
    echo("")


def _describe_tariff(tariff: Tariff) -> str:
    if tariff.kind is TariffKind.FLAT:
        base = f"flat {tariff.flat_price_eur_kwh:g} EUR/kWh"
    elif tariff.kind is TariffKind.F1_F2_F3:
        base = (
            f"Italian bands F1 {tariff.f1_price:g} / F2 {tariff.f2_price:g} / "
            f"F3 {tariff.f3_price:g} EUR/kWh"
        )
    else:
        base = f"hourly prices from {tariff.hourly_prices_csv}"
    return f"{base}, export {tariff.export_price_eur_kwh:g} EUR/kWh"


def _wrap(text: str, width: int, indent: str) -> str:
    """Wrap to `width`, indenting continuation lines. Kept local so the output stays
    dependency-free plain text."""
    lines = textwrap.wrap(text, width=width)
    if not lines:
        return ""
    return "\n".join([lines[0], *(indent + line for line in lines[1:])])


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(app())
