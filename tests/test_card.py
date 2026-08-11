"""Tests for the PNG summary card.

What is worth testing about a rendered image is not how it looks — that is a
judgement made by looking at it — but the two things that can silently go wrong
without anyone noticing:

- **Every honesty constraint is actually on the card.** The seasonality warning,
  the tariff, the unflattering payback. These are the project's whole positioning
  and the card is the artifact that travels furthest from the report that explains
  them, so each one is asserted against the text the renderer emitted.
- **The degenerate inputs render at all.** No battery cost, no positive savings, a
  single capacity, a payback so long it runs off the axis. Each of these took a
  branch that did not exist for the fixture, and a card that raises — or worse,
  quietly draws an empty panel — is discovered by a user, not by a reviewer.

Text is asserted by walking the figure's artists rather than by reading pixels:
it is the same content the reader sees, and it does not break when a margin moves.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from matplotlib.figure import Figure
from matplotlib.text import Text

from battery_worth import PROJECT_NAME, REPO_DISPLAY_URL, REPO_URL
from battery_worth.analysis import run_analysis
from battery_worth.card import CARD_PX, build_summary_card, render_summary_card
from battery_worth.models import AnalysisResult, Tariff, TariffKind
from battery_worth.report import annualization_years, describe_tariff, render_report
from tests.test_analysis import FLAT_TARIFF, TEMPLATE, make_report, make_solar_days

# An export price above the import price: the battery diverts energy away from a
# feed-in tariff that paid better than the grid charged, so savings go negative at
# every capacity. Rare, real, and the one case the card must not dress up.
LOSING_TARIFF = Tariff(
    kind=TariffKind.FLAT, flat_price_eur_kwh=0.05, export_price_eur_kwh=0.40
)


def build(
    capacities: list[float] | None = None,
    cost_per_kwh: float | None = 600.0,
    days: int = 365,
    tariff: Tariff = FLAT_TARIFF,
) -> AnalysisResult:
    """A sweep over the synthetic solar days, parameterized for the edge cases."""
    return run_analysis(
        make_solar_days(n_days=min(days, 60)),
        make_report(days=days),
        capacities=[0, 5, 10, 15] if capacities is None else capacities,
        battery_template=TEMPLATE,
        tariff=tariff,
        battery_cost_per_kwh=cost_per_kwh,
    )


def card_text(figure: Figure) -> str:
    """Every string the card draws, joined — figure-level text plus each panel's.

    Walks the artists rather than reading pixels: this is the content the reader
    sees, and it survives a layout change that a pixel comparison would not.
    """
    parts = [t.get_text() for t in figure.texts]
    for axes in figure.axes:
        # `loc="left"` stores the title on its own artist, so the default
        # `get_title()` (centre) returns an empty string and would silently drop
        # every panel title from the assertions.
        parts.append(axes.get_title(loc="left"))
        parts.append(axes.get_xlabel())
        parts.append(axes.get_ylabel())
        parts.extend(label.get_text() for label in axes.get_xticklabels())
        parts.extend(child.get_text() for child in axes.texts)
    return "\n".join(parts)


# --- The file itself ---------------------------------------------------------


def test_writes_a_square_png_of_the_declared_size(tmp_path: Path) -> None:
    """1200x1200, because both Reddit and a phone timeline leave a square uncropped."""
    path = tmp_path / "card.png"
    render_summary_card(build(), path, tariff=FLAT_TARIFF)

    assert path.exists()
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    figure = build_summary_card(build())
    width, height = figure.get_size_inches() * figure.dpi
    assert (round(width), round(height)) == (CARD_PX, CARD_PX)


def test_creates_missing_parent_directories(tmp_path: Path) -> None:
    """`--output reports/run.md` should not fail on the card because reports/ is new."""
    path = tmp_path / "nested" / "deeper" / "card.png"
    render_summary_card(build(), path, tariff=FLAT_TARIFF)
    assert path.exists()


# --- Hierarchy: the capacity is the headline ---------------------------------


def test_headline_is_the_recommended_capacity_not_the_payback() -> None:
    """The largest element on the card is a capacity claim, and it is actionable.

    "5 kWh is enough for this house" contradicts a salesperson; "14.2 years" only
    discourages. This asserts the headline exists as a capacity sentence *and*
    that it is the largest text on the card, since a verdict that is not the
    biggest thing is not the verdict.
    """
    result = build()
    figure = build_summary_card(result, tariff=FLAT_TARIFF)

    headline = max(figure.texts, key=lambda t: t.get_fontsize())
    assert "kWh is enough for this house" in headline.get_text()

    best = max(
        (s for s in result.scenarios if s.payback_years() is not None),
        key=lambda s: -(s.payback_years() or 0),
    )
    assert f"{best.capacity_kwh:g} kWh" in headline.get_text()


def test_savings_and_payback_are_subordinate_to_the_headline() -> None:
    """Second in the hierarchy: present, and smaller than the verdict."""
    figure = build_summary_card(build(), tariff=FLAT_TARIFF)
    text = card_text(figure)

    assert "saved per year" in text
    assert "to pay back" in text

    # Every size the card sets is numeric; matplotlib's string aliases ("large")
    # would make the hierarchy unorderable, so their absence is part of the claim.
    sizes = [t.get_fontsize() for t in figure.texts]
    assert all(isinstance(s, int | float) for s in sizes)

    ranked = sorted((s for s in sizes if isinstance(s, int | float)), reverse=True)
    assert ranked[0] > ranked[1], "the headline must outrank the stat values"


# --- Honesty constraints -----------------------------------------------------


def test_seasonality_warning_appears_on_the_card() -> None:
    """A card built from a partial year says so, on the card, not just in the report.

    The card is the artifact that gets screenshotted and posted; the report is the
    one that stays behind. If the warning lived only in the report, a three-month
    result would travel as if it were a year.
    """
    figure = build_summary_card(build(days=60), tariff=FLAT_TARIFF)
    text = card_text(figure)

    assert "60 days" in text
    assert "not a full year" in text
    assert "Seasonality" in text


def test_no_seasonality_warning_on_a_full_year() -> None:
    """The warning is conditional; the caveats in the footer are not."""
    text = card_text(build_summary_card(build(days=365), tariff=FLAT_TARIFF))
    assert "not a full year" not in text


def test_tariff_is_always_visible() -> None:
    """Savings without the prices that produced them cannot be checked by anyone.

    Asserted against `describe_tariff`, the same function the report and the
    terminal use, so the card cannot describe the tariff in different words than
    the report shipped beside it.
    """
    text = card_text(build_summary_card(build(), tariff=FLAT_TARIFF))
    assert describe_tariff(FLAT_TARIFF) in text
    assert "0.3" in text  # the import price itself, not merely the word "flat"


def test_payback_is_not_rounded_flatteringly() -> None:
    """14.2 stays 14.2 — one decimal, truncating nothing in the friendly direction."""
    result = build()
    best = min(
        (s for s in result.scenarios if s.payback_years() is not None),
        key=lambda s: s.payback_years() or 0.0,
    )
    payback = best.payback_years()
    assert payback is not None

    text = card_text(build_summary_card(result, tariff=FLAT_TARIFF))
    assert f"{payback:.1f} years" in text


def test_period_and_attribution_are_in_the_footer() -> None:
    result = build()
    text = card_text(build_summary_card(result, tariff=FLAT_TARIFF))

    assert result.period_start[:10] in text
    assert f"{result.days_analyzed} days" in text
    assert PROJECT_NAME in text
    assert REPO_DISPLAY_URL in text


def test_card_carries_the_real_repo_url() -> None:
    """The URL on the card is the project's only return channel, so it must be right.

    A reader who wants the tool has nothing but this string on a picture — no link
    to click, no page to search from. A wrong or dead URL does not degrade the
    card, it makes it worthless, and the mistake is invisible to everyone who
    already knows where the repo is. Asserted against the literal rather than
    against the constant alone, so renaming the account cannot silently make this
    test agree with a new mistake.
    """
    text = card_text(build_summary_card(build(), tariff=FLAT_TARIFF))

    assert "github.com/contimarco77/battery-worth" in text
    assert "marcoconti" not in text, "the old, wrong account must not survive anywhere"


def test_card_and_report_point_at_the_same_repository() -> None:
    """One constant, two artifacts. They travel together and must not diverge.

    A card and the report it ships beside naming different repositories would make
    both look untrustworthy, and it is exactly what happens when the string is
    retyped in each renderer instead of imported.
    """
    result = build()
    card = card_text(build_summary_card(result, tariff=FLAT_TARIFF))
    report = render_report(result, FLAT_TARIFF)

    assert REPO_DISPLAY_URL in card
    assert REPO_URL in report


def test_card_figures_match_the_report_annualization() -> None:
    """No recomputation: the card's savings figure is the report's, to the digit.

    A card and a report disagreeing about the same run would discredit both, and
    the way that happens is arithmetic living in two places. Both go through
    `annualization_years`.
    """
    result = build(days=200)
    years = annualization_years(result.days_analyzed)
    best = min(
        (s for s in result.scenarios if s.payback_years() is not None),
        key=lambda s: s.payback_years() or 0.0,
    )

    text = card_text(build_summary_card(result, tariff=FLAT_TARIFF))
    assert f"{best.savings_eur / years:,.0f} EUR" in text


# --- Degenerate inputs -------------------------------------------------------


def test_no_battery_cost_omits_payback_entirely() -> None:
    """An absent input must read as absent — never as a zero, never as a blank cell.

    With no cost there is no payback to compute, so the payback panel and the
    payback stat are both dropped rather than drawn empty.
    """
    figure = build_summary_card(build(cost_per_kwh=None), tariff=FLAT_TARIFF)
    text = card_text(figure)

    assert "to pay back" not in text
    assert "Years to pay back" not in text
    assert "Savings per year" in text, "the savings panel still carries the story"
    assert len(figure.axes) == 1


def test_no_battery_cost_still_recommends_on_savings() -> None:
    """Falling back to largest savings, which is what `recommended_scenario` does."""
    result = build(cost_per_kwh=None)
    figure = build_summary_card(result, tariff=FLAT_TARIFF)
    headline = max(figure.texts, key=lambda t: t.get_fontsize()).get_text()

    largest = max(result.scenarios, key=lambda s: s.savings_eur)
    assert f"{largest.capacity_kwh:g} kWh" in headline


def test_no_positive_savings_says_so_instead_of_promoting_a_loser() -> None:
    """The honest negative result, stated plainly.

    Nothing in the sweep saved money, so there is no "best" to recommend and the
    card must not pick the least-bad option and dress it as a verdict.
    """
    figure = build_summary_card(build(tariff=LOSING_TARIFF), tariff=LOSING_TARIFF)
    text = card_text(figure)

    assert "No battery paid off here" in text
    assert "is enough for this house" not in text
    assert "saved money" in text


def test_negative_savings_are_drawn_below_zero(tmp_path: Path) -> None:
    """Losses must be visible as losses, not clipped to an empty panel.

    Anchoring the y-axis at zero would render every losing bar as nothing at all —
    an empty chart beside a headline saying the battery lost money.
    """
    result = build(tariff=LOSING_TARIFF)
    figure = build_summary_card(result, tariff=LOSING_TARIFF)

    savings_axes = figure.axes[0]
    assert savings_axes.get_ylim()[0] < 0, "the axis must extend below zero"

    render_summary_card(result, tmp_path / "loss.png", tariff=LOSING_TARIFF)
    assert (tmp_path / "loss.png").exists()


def test_single_capacity_sweep_renders_a_comparison_not_a_slab() -> None:
    """One bar is still one point in a comparison, drawn at the same width as five.

    Left to matplotlib's own limits, a single category fills the panel edge to edge
    and reads as a progress meter.
    """
    one = build_summary_card(build(capacities=[10]), tariff=FLAT_TARIFF)
    many = build_summary_card(build(capacities=[5, 10, 15, 20]), tariff=FLAT_TARIFF)

    lone_bar = one.axes[0].patches[0]
    crowd_bar = many.axes[0].patches[0]
    assert lone_bar.get_width() <= crowd_bar.get_width()  # type: ignore[attr-defined]

    span = one.axes[0].get_xlim()
    assert span[1] - span[0] >= 3.0, "the panel keeps its slots when the sweep is short"
    assert "10 kWh" in card_text(one)


def test_baseline_row_is_never_drawn_as_a_bar() -> None:
    """Capacity 0 is a table row, not a battery. It has no savings and no payback."""
    figure = build_summary_card(build(capacities=[0, 5, 10]), tariff=FLAT_TARIFF)

    # Matched against whole tick labels, not as a substring: "0 kWh" occurs inside
    # "10 kWh" and the naive check passes for the wrong reason. Read off the last
    # panel, which is the one that labels the shared capacity axis.
    ticks = [label.get_text() for label in figure.axes[-1].get_xticklabels()]
    assert ticks == ["5 kWh", "10 kWh"]
    assert len(figure.axes[0].patches) == 2


def test_very_long_payback_is_clipped_but_labelled_with_the_true_value() -> None:
    """A 90-year payback is capped on the axis and printed in full beside the bar.

    Unclipped, one outlier flattens every other bar into the baseline and destroys
    the comparison. Clipped without the label, the reader is shown a shorter
    payback than the data says — which would be the one unforgivable failure here.
    """
    # 60 days of data at a high cost per kWh: annualized savings stay small
    # against a large battery cost, so payback lands far past the axis cap.
    result = build(days=60, cost_per_kwh=4000.0)
    text = card_text(build_summary_card(result, tariff=FLAT_TARIFF))

    long_paybacks = [
        p for s in result.scenarios if (p := s.payback_years()) is not None and p > 40
    ]
    assert long_paybacks, "the fixture for this test must actually overflow the axis"

    payback_axes = build_summary_card(result, tariff=FLAT_TARIFF).axes[-1]
    assert payback_axes.get_ylim()[1] < max(long_paybacks), "the axis is capped"
    for payback in long_paybacks:
        assert f"{payback:.1f}" in text, "the true value is printed, not the cap"


def test_renders_without_a_tariff() -> None:
    """The tariff is optional on the API, and the card degrades to omitting it."""
    text = card_text(build_summary_card(build(), tariff=None))
    assert "is enough for this house" in text
    assert "EUR/kWh" not in text


def test_packaging_metadata_matches_the_printed_url() -> None:
    """pyproject's URLs and the constant the artifacts print cannot drift apart.

    `[project.urls]` is the one place that genuinely has to repeat the string —
    packaging metadata cannot import from the package it describes — so it is the
    one place a divergence could survive unnoticed. This test is the substitute
    for the import that is not possible there.
    """
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject.open("rb") as handle:
        urls = tomllib.load(handle)["project"]["urls"]

    assert urls["Homepage"] == REPO_URL
    assert urls["Repository"] == REPO_URL
    assert urls["Issues"].startswith(REPO_URL)


# --- Portability -------------------------------------------------------------


def test_only_uses_the_font_matplotlib_bundles() -> None:
    """DejaVu Sans everywhere, so the card renders identically on a stranger's machine.

    Any other family resolves on the author's machine and silently falls back on
    someone else's, so the card that gets posted is not the card that was designed.
    Hierarchy comes from size and weight, which are portable.
    """
    figure = build_summary_card(build(), tariff=FLAT_TARIFF)

    # `findobj` rather than a hand-rolled walk: it reaches every Text the figure
    # will draw, including the ones with no public accessor (a left-aligned title
    # is not `axes.title`), so a future artist cannot slip past this test by
    # living somewhere the walk did not know to look.
    texts = figure.findobj(Text)

    # Only artists that actually draw something: matplotlib keeps empty
    # placeholder labels on every axis, and those legitimately carry the default
    # "sans-serif" alias because they render nothing.
    families = {t.get_fontfamily()[0] for t in texts if t.get_text()}
    assert families == {"DejaVu Sans"}


@pytest.mark.parametrize(
    ("capacities", "cost", "days", "tariff"),
    [
        ([0, 5, 10, 15], 600.0, 365, FLAT_TARIFF),
        ([10], 600.0, 365, FLAT_TARIFF),
        ([0, 5], None, 365, FLAT_TARIFF),
        ([0, 5, 10], 600.0, 45, FLAT_TARIFF),
        ([0, 5, 10], 600.0, 365, LOSING_TARIFF),
        ([5], None, 30, LOSING_TARIFF),
    ],
)
def test_every_combination_writes_a_readable_png(
    tmp_path: Path,
    capacities: list[float],
    cost: float | None,
    days: int,
    tariff: Tariff,
) -> None:
    """The cross-product of the edge cases, each written to disk.

    Cheap insurance against the combination nobody thought about — a single
    capacity with no cost on a partial year under a losing tariff exercises four
    branches at once, and each of them was added for a different reason.
    """
    path = tmp_path / "card.png"
    render_summary_card(
        build(capacities=capacities, cost_per_kwh=cost, days=days, tariff=tariff),
        path,
        tariff=tariff,
    )
    assert path.stat().st_size > 1000
