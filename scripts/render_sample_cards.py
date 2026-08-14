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

Two kinds of card come out of this, and the table says which is which:

- **coverage** — the nine below, one per renderer branch. Read to check a branch
  still draws something sane.
- **launch** — the two OPSD households whose cards are the README screenshots.
  Read to check the picture a stranger sees is current with HEAD. They need the
  git-ignored OPSD extract (`scratchpad/opsd/`, overridable via
  `BATTERY_WORTH_OPSD_DIR` / `--opsd-input`); without it they are skipped with a
  message naming the missing path, and the nine still render.

The cases are the fixture plus every degenerate input the fixture does not
exercise. Each one takes a branch that does not exist for the happy path:

- `ausgrid`               the real thing, 365 days, the reference render
- `no_cost`               no battery cost: no payback panel, no payback stat
- `no_cost_no_knee`       no cost and no flattening: the headline falls back again
- `single_capacity`       one bar, which must still read as a comparison
- `60_days`               partial year: the seasonality band, and clipped paybacks
- `no_positive_savings`   an export price above import: every capacity loses money
- `beyond_lifetime`       savings positive, every payback past the battery's life
- `beyond_lifetime_thin_spread`  the same, on a house that is *not* saturated
- `baseline_only`         a sweep with no battery in it at all

**Rendering the case is half the job; looking at it is the other half.** Four
defects shipped on cards this script had already written — a headline clipped at
the card's edge, an axis whose labels named values its gridlines were not at, a
verdict about batteries on a run that analysed none, and a headline repeating the
stat beneath it. The script did its job in every case: the picture existed. Nobody
opened it. `tests/test_card.py` now asserts the two properties that are visible
only in the render — headline extent against the drawable width, and every tick
label against its own location — across this same case list, so the ones that can
be checked mechanically no longer depend on somebody remembering to look.

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
import os
import sys
import warnings
from pathlib import Path

import pandas as pd
from matplotlib.axes import Axes

from battery_worth.analysis import run_analysis
from battery_worth.card import build_summary_card, render_summary_card
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

_SCRATCHPAD = Path(__file__).resolve().parent.parent / "scratchpad"
# The two OPSD households whose cards are the README screenshots. They are not
# coverage cases — every branch they take is already covered by the nine — and they
# are here for the opposite reason: they are the cards a stranger sees first, so
# they must be reproducible from a command rather than from a shell invocation
# somebody typed once and did not keep. Both were rendered before the tick cap
# landed, which is exactly the failure mode an unreproducible artifact has.
#
# Opt-in, and silent-skip-free: the OPSD extract is git-ignored and nobody who
# clones this repo has it, so the script must still run for them, and must say
# plainly which paths it looked for rather than quietly rendering nine cards.
OPSD_INPUT = Path(os.environ.get("BATTERY_WORTH_OPSD_DIR", _SCRATCHPAD / "opsd"))
OPSD_OUTPUT = Path(os.environ.get("BATTERY_WORTH_OPSD_CARDS", _SCRATCHPAD / "cards" / "opsd"))
OPSD_HOUSEHOLDS = ("residential4", "residential6")
OPSD_TIMEZONE = "Europe/Berlin"
# The dataset ships raw cumulative meter readings under its own column names; the
# extraction script preserves both rather than normalising, so the tool's own
# --cumulative auto-detection is what gets exercised on the launch cards too.
OPSD_MAPPING = ColumnMapping(
    timestamp="utc_timestamp",
    grid_import="grid_import",
    grid_export="grid_export",
    pv_production="pv",
)

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


DROPPED = "dropped"


def drawn_ticks(axes: Axes) -> list[float]:
    """The y tick locations actually drawn on `axes`, in order.

    Keeps only the ticks *inside* the y-limits and carrying a label, which is what
    the reader sees: the locator can place one past the axis end, and that one is
    never drawn.
    """
    low, high = axes.get_ylim()
    return [
        float(location)
        for location, label in zip(axes.get_yticks(), axes.get_yticklabels(), strict=True)
        if label.get_text() and low <= location <= high
    ]


def panel_ticks(result: AnalysisResult, tariff: Tariff) -> dict[str, list[float] | str]:
    """Both panels' drawn gridlines, keyed by panel, one entry each.

    **Panels are identified by position, not by axis label.** Reading the y-label —
    matching `"EUR / year"` — is what this function used to do, and it is why the
    payback panel went unmeasured for as long as it did: the string only ever
    matched the savings panel, so the counter walked past the one panel whose
    locator is uncapped and reported a single number as if it were the card's.
    Worse, it returned `0` both for "the payback panel is drawn with no ticks" and
    for "there is no payback panel", making the two indistinguishable in the output.

    The structural fact is `_draw_chart`: it is the only place in `card.py` that
    creates axes, it does so with `figure.add_axes` in exactly one order, and the
    figure carries no other axes at all. So `figure.axes[0]` is the savings panel on
    every path, and `figure.axes[1]` is the payback panel on the one path that draws
    it. `tests/test_card.py` already pins both halves of that invariant — the drop
    paths assert `len(figure.axes) == 1`, and the bars-mode tests index `axes[1]` for
    payback — so position is a checked property of the layout here, not a guess.

    There are three axes counts, not two, and the third is why this indexes
    defensively rather than assuming a savings panel exists: `_draw_chart` returns
    before adding any axes when the sweep has no scenarios to plot or the card has
    no room for a chart, so `baseline_only` renders a figure with *zero* axes. That
    card's savings panel is as dropped as its payback panel.

    An absent panel is reported as `DROPPED`, never as an empty list and never as a
    count of zero — a real panel drawn with no labelled gridlines is a different
    thing from a panel the layout never created, and collapsing the two is the
    defect this function was rewritten to remove.

    Rebuilds the figure rather than reading it back off the PNG. The numbers wanted
    are the locator's decisions, and the axis objects state them directly; recovering
    them from pixels would mean re-deriving them from rendered hairlines.
    """
    figure = build_summary_card(result, tariff=tariff)
    figure.canvas.draw()
    return {
        panel: drawn_ticks(figure.axes[index]) if len(figure.axes) > index else DROPPED
        for index, panel in enumerate(("savings", "payback"))
    }


def opsd_cases(source: Path) -> tuple[list[tuple[str, AnalysisResult, Tariff]], list[str]]:
    """The launch cards, plus one message per household whose CSV is missing.

    Returns the cases it could build and the notes about the ones it could not, so
    the caller reports both together. Absence is a message naming the path, never a
    silent short sweep: the OPSD extract is git-ignored, so for anyone but its author
    the normal outcome is that these two are skipped, and a skip that prints nothing
    is indistinguishable from a script that never had the feature.
    """
    cases: list[tuple[str, AnalysisResult, Tariff]] = []
    notes: list[str] = []
    for household in OPSD_HOUSEHOLDS:
        csv = source / f"{household}.csv"
        if not csv.exists():
            notes.append(f"  skipped {household}: no CSV at {csv}")
            continue
        df, report = load_energy_data(csv, OPSD_MAPPING, timezone=OPSD_TIMEZONE)
        cases.append((household, analyze(df, report, [0, 5, 10, 15], 600.0, TARIFF), TARIFF))
    return cases, notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--opsd-input",
        type=Path,
        default=OPSD_INPUT,
        help="Directory holding the OPSD household CSVs (env: BATTERY_WORTH_OPSD_DIR)",
    )
    parser.add_argument(
        "--opsd-output",
        type=Path,
        default=OPSD_OUTPUT,
        help="Where the launch cards are written (env: BATTERY_WORTH_OPSD_CARDS)",
    )
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

        launch, skipped = opsd_cases(args.opsd_input.resolve())

        # (kind, name, destination directory, result, tariff). The kind travels with
        # the row because the two sets are read for different reasons: a coverage row
        # answers "does this branch still draw something sane", a launch row answers
        # "is the picture a stranger sees current with HEAD". Mixing them unlabelled
        # in one table invites reading a launch regression as a coverage case.
        launch_output = args.opsd_output.resolve()
        if launch:
            launch_output.mkdir(parents=True, exist_ok=True)
        rows: list[tuple[str, str, Path, AnalysisResult, Tariff]] = [
            ("coverage", name, output, result, tariff) for name, result, tariff in cases
        ]
        rows += [("launch", name, launch_output, result, tariff) for name, result, tariff in launch]

        written: list[tuple[str, str, Path, dict[str, list[float] | str]]] = []
        for kind, name, destination, result, tariff in rows:
            path = destination / f"{name}.png"
            render_summary_card(result, path, tariff=tariff)
            written.append((kind, name, path, panel_ticks(result, tariff)))

    print(f"\n{len(cases)} coverage cards written to {output}")
    if launch:
        print(f"{len(launch)} launch cards written to {launch_output}")
    if skipped:
        print("\nOPSD launch cards not rendered (the coverage cards are unaffected):")
        for note in skipped:
            print(note)

    # Both panels are printed, with their tick *values* and not merely a count. The
    # count alone doubled across this whole set once — a locator fix for a mislabelled
    # axis, correct in itself, quietly taking every healthy panel from four gridlines
    # to ten — and the change was invisible in a list of filenames. The values catch
    # the other half of that: a panel can keep its count and move its scale, which
    # reads as a different chart and shows up nowhere in a number.
    print()
    for kind, name, _path, panels in written:
        for panel in ("savings", "payback"):
            ticks = panels[panel]
            state = (
                ticks
                if isinstance(ticks, str)
                else f"{len(ticks):>2} | [{', '.join(f'{t:g}' for t in ticks)}]"
            )
            print(f"  {kind:<8} | {name:<28} | {panel:<7} | {state}")

    print()
    for _kind, _name, path, _panels in written:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
