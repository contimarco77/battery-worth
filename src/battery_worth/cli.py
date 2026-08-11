"""battery-worth CLI.

v0 surface (locked scope):
    battery-worth analyze data.csv --capacities 5,10,15,20 --flat-price 0.28 \
        --export-price 0.10 --battery-cost-per-kwh 500

Defaults to a fully offline numeric report. `--llm` adds natural-language
commentary (requires the `llm` extra and ANTHROPIC_API_KEY).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    name="battery-worth",
    help="Would a home battery have paid off for YOU? Retrospective analysis "
    "from your real energy data.",
    no_args_is_help=True,
)


@app.command()
def analyze(  # noqa: PLR0913, PLR0917 - Typer derives the CLI surface from these parameters
    data: Annotated[Path, typer.Argument(help="CSV with timestamp, import, export, PV columns")],
    capacities: Annotated[
        str, typer.Option(help="Comma-separated usable capacities in kWh to sweep")
    ] = "5,10,15",
    flat_price: Annotated[
        float | None, typer.Option(help="Flat import price, EUR/kWh")
    ] = None,
    export_price: Annotated[float, typer.Option(help="Export remuneration, EUR/kWh")] = 0.10,
    battery_cost_per_kwh: Annotated[
        float | None, typer.Option(help="Installed cost per usable kWh, for payback")
    ] = None,
    output: Annotated[Path, typer.Option(help="Report output directory")] = Path("report"),
    llm: Annotated[bool, typer.Option(help="Add LLM commentary (needs ANTHROPIC_API_KEY)")] = False,
) -> None:
    """Run the retrospective what-if analysis and write the report + summary card."""
    # TODO(M1): load + validate CSV (ingest.py), resample to hourly, data-quality checks
    # TODO(M1): sweep capacities -> simulate_battery + summarize_scenario per capacity
    # TODO(M2): build price series from tariff config (tariffs.py)
    # TODO(M2): render jinja2 report (4 fixed sections) + matplotlib summary card
    # TODO(M3): --llm commentary layer with solar-report grounding rules
    typer.echo("battery-worth v0 skeleton — engine not wired yet. See PROJECT-CONTEXT.md.")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
