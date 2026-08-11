"""Render the summary card on the real fixture plus every edge case, for inspection.

The card is the one artifact in this project whose defects are invisible to the
test suite. Tests pin what it *says* — the warning is present, the payback is not
rounded, the URL is right — but a clipped headline, a title landing on a label, or
three capped bars reading as three equal batteries are all things you can only see
by looking. This script exists so that looking is one command rather than a
throwaway snippet retyped each session.

    python scripts/render_sample_cards.py

Output goes to `scratchpad/cards/` (git-ignored), one self-describing filename per
case, with the absolute paths printed at the end so they can be opened directly.

The five cases are the fixture plus the four degenerate inputs the fixture does
not exercise. Each one took a branch that did not exist for the happy path:

- `ausgrid`               the real thing, 365 days, the reference render
- `no_cost`               no battery cost: no payback panel, no payback stat
- `single_capacity`       one bar, which must still read as a comparison
- `60_days`               partial year: the seasonality band, and clipped paybacks
- `no_positive_savings`   an export price above import: every capacity loses money

Requires the Ausgrid fixture (see PROJECT-CONTEXT.md § Test fixture). The raw
dataset is not in the repo; `scripts/extract_ausgrid_customer.py` regenerates it.
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import pandas as pd

from battery_worth.analysis import run_analysis
from battery_worth.card import render_summary_card
from battery_worth.ingest import load_energy_data
from battery_worth.models import (
    AnalysisResult,
    BatterySpec,
    ColumnMapping,
    IngestReport,
    Tariff,
    TariffKind,
)

DEFAULT_FIXTURE = (
    Path.home()
    / "personal-projects/_datasets/ausgrid/Ausgrid_solar_home_data"
    / "customer_1_2012-2013.csv"
)
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "scratchpad" / "cards"

MAPPING = ColumnMapping(
    timestamp="timestamp", consumption="consumption", pv_production="pv_production"
)
TARIFF = Tariff(kind=TariffKind.FLAT, flat_price_eur_kwh=0.25, export_price_eur_kwh=0.10)
# Export paid better than the grid charged: the battery diverts energy away from a
# feed-in tariff worth more than the import it avoids, so savings go negative at
# every capacity. Rare, real, and the case the card must not dress up.
LOSING_TARIFF = Tariff(
    kind=TariffKind.FLAT, flat_price_eur_kwh=0.05, export_price_eur_kwh=0.40
)
TEMPLATE = BatterySpec(usable_capacity_kwh=1.0, max_charge_kw=5.0, max_discharge_kw=5.0)

SHORT_PERIOD_DAYS = 60


def analyze(
    df: pd.DataFrame,
    report: IngestReport,
    capacities: list[float],
    cost_per_kwh: float | None,
    tariff: Tariff,
) -> AnalysisResult:
    return run_analysis(
        df,
        report,
        capacities=capacities,
        battery_template=TEMPLATE,
        tariff=tariff,
        battery_cost_per_kwh=cost_per_kwh,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if not args.fixture.exists():
        print(f"Fixture not found: {args.fixture}", file=sys.stderr)
        print(
            "See PROJECT-CONTEXT.md § Test fixture, or pass --fixture.", file=sys.stderr
        )
        return 1

    output: Path = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    # Tariff and ingest warnings are the CLI's business, not this script's; it
    # renders pictures, and interleaving warnings would bury the paths it prints.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df, report = load_energy_data(args.fixture, MAPPING, timezone="Australia/Sydney")

        short_df = df.loc[df.index < df.index[0] + pd.Timedelta(days=SHORT_PERIOD_DAYS)]
        short_report = report.model_copy(
            update={
                "days_analyzed": SHORT_PERIOD_DAYS,
                "seasonality_warning": True,
                "period_end": str(short_df.index[-1]),
            }
        )

        cases: list[tuple[str, AnalysisResult, Tariff]] = [
            (
                "ausgrid",
                analyze(df, report, [0, 5, 10, 15, 20], 600.0, TARIFF),
                TARIFF,
            ),
            (
                "no_cost",
                analyze(df, report, [0, 5, 10, 15, 20], None, TARIFF),
                TARIFF,
            ),
            ("single_capacity", analyze(df, report, [10], 600.0, TARIFF), TARIFF),
            (
                "60_days",
                analyze(short_df, short_report, [0, 5, 10, 15], 600.0, TARIFF),
                TARIFF,
            ),
            (
                "no_positive_savings",
                analyze(df, report, [0, 5, 10, 15], 600.0, LOSING_TARIFF),
                LOSING_TARIFF,
            ),
        ]

        written: list[Path] = []
        for name, result, tariff in cases:
            path = output / f"{name}.png"
            render_summary_card(result, path, tariff=tariff)
            written.append(path)

    print(f"\n{len(written)} cards written to {output}\n")
    for path in written:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
