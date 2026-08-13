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

The cases are the fixture plus every degenerate input the fixture does not
exercise. Each one takes a branch that does not exist for the happy path:

- `ausgrid`               the real thing, 365 days, the reference render
- `no_cost`               no battery cost: no payback panel, no payback stat
- `no_cost_no_knee`       no cost and no flattening: the headline falls back again
- `single_capacity`       one bar, which must still read as a comparison
- `60_days`               partial year: the seasonality band, and clipped paybacks
- `no_positive_savings`   an export price above import: every capacity loses money
- `beyond_lifetime`       savings positive, every payback past the battery's life
- `baseline_only`         a sweep with no battery in it at all

**The list is maintained against the branches, not against past bugs.** It was
built the other way round — one case per defect already seen — and the hole that
left was `beyond_lifetime`: positive savings with every payback beyond 20 years,
the one shape where the headline and the payback panel could disagree. Nobody had
looked at that card, so for as long as it existed the headline said "5 kWh pays
back fastest" above a panel saying no capacity pays back at all. A branch no
sample case renders is a branch whose picture nobody has ever seen, which is the
only class of defect this script exists to catch. When `headline_for` or the
panel-drop logic grows a branch, it gets a case here in the same change.

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
LOSING_TARIFF = Tariff(kind=TariffKind.FLAT, flat_price_eur_kwh=0.05, export_price_eur_kwh=0.40)
# A spread narrow enough that the battery still saves money every year, but so
# little of it that no capacity recovers its cost inside the hardware's life. The
# card must not call any of these a payback.
THIN_SPREAD_TARIFF = Tariff(
    kind=TariffKind.FLAT, flat_price_eur_kwh=0.25, export_price_eur_kwh=0.22
)
TEMPLATE = BatterySpec(usable_capacity_kwh=1.0, max_charge_kw=5.0, max_discharge_kw=5.0)

SHORT_PERIOD_DAYS = 60

# Share of the fixture's export left on the meter for the `beyond_lifetime` case.
# The rest is absorbed as on-site load, which is what turns a 26%-self-consumption
# household into one already using ~89% of its own solar: the shape where the
# honest verdict is that the roof and the load are the constraint, not the battery.
# Derived from the fixture rather than shipped as a second CSV, so the sample set
# stays reproducible from the one fixture the script already requires.
_HIGH_SELF_CONSUMPTION_EXPORT_SHARE = 0.15


def absorb_export(df: pd.DataFrame, keep: float) -> pd.DataFrame:
    """A copy of `df` with most of its export consumed on-site instead.

    Moves export into the household's own load rather than deleting it, so total PV
    is unchanged and only the *split* between self-consumed and exported moves. That
    is the one property the high-self-consumption branch turns on.
    """
    shifted = df["grid_export"] * (1.0 - keep)
    adjusted = df.copy()
    adjusted["grid_export"] = df["grid_export"] * keep
    adjusted["grid_import"] = (df["grid_import"] - shifted).clip(lower=0)
    return adjusted


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
        print("See PROJECT-CONTEXT.md § Test fixture, or pass --fixture.", file=sys.stderr)
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
            # Savings positive at every capacity, every payback past 20 years. The
            # case the set was missing, and the one where the headline used to say
            # "5 kWh pays back fastest" above a panel saying nothing pays back.
            # Self-consumption is high, so the headline states the reason.
            (
                "beyond_lifetime",
                analyze(
                    absorb_export(df, _HIGH_SELF_CONSUMPTION_EXPORT_SHARE),
                    report,
                    [0, 5, 10, 15],
                    600.0,
                    TARIFF,
                ),
                TARIFF,
            ),
            # The same no-payback verdict on a house that is *not* saturated: real
            # surplus, a battery that captures it, and a spread too thin to pay for
            # the hardware. The headline must not blame the roof here.
            (
                "beyond_lifetime_thin_spread",
                analyze(df, report, [0, 5, 10, 15], 600.0, THIN_SPREAD_TARIFF),
                THIN_SPREAD_TARIFF,
            ),
            # Two capacities and no cost: no payback to rank, and too few points for
            # a knee, so the headline falls back to the plain savings figure.
            ("no_cost_no_knee", analyze(df, report, [0, 20], None, TARIFF), TARIFF),
            # A sweep containing no battery at all. Nothing to recommend, nothing to
            # plot; the card must still render rather than raise.
            ("baseline_only", analyze(df, report, [0], 600.0, TARIFF), TARIFF),
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
