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
from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import to_rgba
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.text import Text
from matplotlib.transforms import Bbox

from battery_worth import PROJECT_NAME, REPO_DISPLAY_URL, REPO_URL
from battery_worth.analysis import run_analysis
from battery_worth.card import (
    CARD_PX,
    build_summary_card,
    headline_for,
    no_payback_statement,
    render_summary_card,
)
from battery_worth.models import (
    HIGH_SELF_CONSUMPTION,
    AnalysisResult,
    Tariff,
    TariffKind,
)
from battery_worth.report import annualization_years, describe_tariff, render_report
from tests.test_analysis import FLAT_TARIFF, TEMPLATE, make_report, make_solar_days

# An export price above the import price: the battery diverts energy away from a
# feed-in tariff that paid better than the grid charged, so savings go negative at
# every capacity. Rare, real, and the one case the card must not dress up.
LOSING_TARIFF = Tariff(kind=TariffKind.FLAT, flat_price_eur_kwh=0.05, export_price_eur_kwh=0.40)


# 150 EUR/kWh on this synthetic household puts paybacks in the 12-13 year range —
# inside a plausible battery lifetime, so the default `build()` exercises the
# normal two-panel card. It is deliberately far below a real installed price: the
# synthetic day is small, and the point is to land the *payback* in a realistic
# band, not the price. Tests about long paybacks raise it explicitly.
DEFAULT_COST_PER_KWH = 150.0


def build(
    capacities: list[float] | None = None,
    cost_per_kwh: float | None = DEFAULT_COST_PER_KWH,
    days: int = 365,
    tariff: Tariff = FLAT_TARIFF,
) -> AnalysisResult:
    """A sweep over the synthetic solar days, parameterized for the edge cases.

    The frame is always 60 repeating days; `days` sets the period the analysis is
    *told* it covers, which is what annualization keys off. That split is what lets
    the period-length tests vary one without the other.
    """
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


def test_headline_names_the_fastest_payback_as_an_investment_claim() -> None:
    """The verdict is about the best *investment*, and says only that.

    It is the largest text on the card (a verdict that is not the biggest thing is
    not the verdict) and it names the shortest-payback capacity.
    """
    result = build()
    figure = build_summary_card(result, tariff=FLAT_TARIFF)

    headline = max(figure.texts, key=lambda t: t.get_fontsize())
    fastest = min(
        (s for s in result.scenarios if s.payback_years() is not None),
        key=lambda s: s.payback_years() or 0.0,
    )
    assert headline.get_text() == f"{fastest.capacity_kwh:g} kWh pays back fastest"


def test_headline_never_claims_a_capacity_is_sufficient() -> None:
    """ "Pays back fastest" is not "is enough" — the card must not upgrade the claim.

    The fastest-payback capacity is routinely the *smallest* one, leaving most of
    the PV surplus unused: on the fixture 5 kWh reaches 59% self-consumption where
    20 kWh reaches 98%. Calling it "enough" states a sufficiency the tool never
    measured, and states it directly above a chart that contradicts it.
    """
    # Spelled out per case rather than as a dict of kwargs: the four cases vary
    # different parameters, so a single mapping has a heterogeneous value type and
    # loses every argument's type at the call site.
    variants = [
        build(),
        build(cost_per_kwh=None),
        build(capacities=[10]),
        build(days=60),
    ]
    for result in variants:
        text = headline_for(result.scenarios)
        assert "enough" not in text.lower(), text


def test_headline_with_no_cost_recommends_no_size() -> None:
    """Without a cost there is no payback, so there is no basis for a size at all.

    `recommended_scenario` falls back to the largest absolute savings, which always
    names the biggest battery in the sweep — precisely the trap this tool exists to
    expose. The headline must report the saturation the chart shows instead of
    laundering that fallback into a recommendation.
    """
    # A sweep that actually saturates: savings stop growing past 20 kWh here.
    result = build(cost_per_kwh=None, capacities=[0, 5, 10, 15, 20, 30])
    text = headline_for(result.scenarios)

    largest = max(result.scenarios, key=lambda s: s.capacity_kwh)
    assert "flatten beyond" in text
    assert f"{largest.capacity_kwh:g} kWh" not in text, (
        "the largest capacity must not be presented as the answer"
    )


def test_headline_with_a_single_capacity_makes_no_superlative_claim() -> None:
    """One data point supports no "best" — there is nothing to be best against."""
    text = headline_for(build(capacities=[10]).scenarios)

    assert "fastest" not in text
    assert "flatten" not in text
    assert "10 kWh" in text
    assert "only size analysed" in text


def test_single_capacity_headline_does_not_repeat_a_stat() -> None:
    """The headline must carry something the stats row does not already say.

    It read "10 kWh pays back in 16.5 years" directly above "16.5 years / to pay
    back" — the card's largest text spent restating the figure printed two
    centimetres below it. Headline space is the scarcest resource on an artifact
    the reader gives three seconds to, so it goes to the one thing the stats
    cannot express: that a single size was analysed, and there is therefore no
    comparison standing behind any number on the card.
    """
    result = build(capacities=[10])
    figure = build_summary_card(result, tariff=FLAT_TARIFF)
    headline = max(figure.texts, key=lambda t: t.get_fontsize()).get_text()

    only = next(s for s in result.scenarios if s.capacity_kwh > 0)
    payback = only.payback_years()
    assert payback is not None

    # The payback belongs to the stats row, and only to it.
    assert f"{payback:.1f}" not in headline
    assert f"{payback:.1f} years" in card_text(figure)
    assert "only size analysed" in headline


# --- A payback past the battery's life is not a payback ----------------------
#
# The card carried two definitions of "pays back". The panel dropped at 20 years,
# because bars that long invert their own encoding; the headline asked only whether
# any positive saving existed. On the OPSD residential6 house — savings of
# 39/46/49 EUR against paybacks of 76.5/130.3/184.5 years — the card's largest text
# said "5 kWh pays back fastest" directly above a sentence saying no capacity pays
# back within 20 years, with the 5 kWh bar lit at full strength underneath.
#
# That house is the project's third headline finding, the one where the honest
# verdict is that no battery helps and the constraint is the roof and the load. The
# card is the artifact that travels without the README, and it stated the opposite
# of the finding in the biggest text on the image.
#
# These tests assert on the emitted sentence and on the emphasis state, not on
# which branch ran: the defect was a correct branch attached to a wrong sentence.


def beyond_lifetime(pv_peak: float = 4.0, cost_per_kwh: float = 600.0) -> AnalysisResult:
    """A sweep where every capacity saves money and none pays back inside 20 years.

    `pv_peak` selects which of the two no-payback shapes is built. Left at the
    default the household self-consumes a quarter of its PV, so real surplus exists
    and the thin spread is what defeats the battery; lowered to 1.2 it already uses
    83% of a small roof, which is the residential6 shape the headline explains.
    """
    return run_analysis(
        make_solar_days(n_days=60, pv_peak=pv_peak),
        make_report(days=365),
        capacities=[0, 5, 10, 15],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        battery_cost_per_kwh=cost_per_kwh,
    )


def test_headline_does_not_recommend_a_size_that_never_pays_back() -> None:
    """The exact defect: "pays back fastest" above "no capacity pays back".

    The headline and the payback panel must apply one threshold. Reverting either
    the `_within_lifetime` gate in `headline_for` or the shared predicate it calls
    brings back "5 kWh pays back fastest" on a card whose own panel says otherwise.
    """
    result = beyond_lifetime()
    batteries = [s for s in result.scenarios if s.capacity_kwh > 0]
    assert all(s.annual_savings_eur > 0 for s in batteries), "savings must be positive"
    assert not any(s.pays_back_within_lifetime() for s in batteries), (
        "and nothing may pay back inside the horizon, or this tests the wrong shape"
    )

    headline = headline_for(result.scenarios)

    assert "pays back fastest" not in headline
    # No capacity may be named as a recommendation, in any wording.
    for scenario in batteries:
        assert f"{scenario.capacity_kwh:g} kWh" not in headline


def test_headline_states_the_reason_rather_than_repeating_the_panel() -> None:
    """The design decision, pinned: the headline explains, the sentence counts.

    Both elements could carry the negative, and only one may. The sentence keeps it,
    because it is the element carrying the *number* — dropping it to avoid a repeat
    would cost the reader the figure that makes the verdict checkable. The headline
    therefore says what the sentence cannot: why this house cannot pay a battery
    back. Self-consumption appears nowhere else on the card, so this is new
    information rather than the repeat the no-repeat rule forbids.
    """
    result = beyond_lifetime(pv_peak=1.2)
    figure = build_summary_card(result, tariff=FLAT_TARIFF)
    headline = max(figure.texts, key=lambda t: t.get_fontsize()).get_text()

    baseline = next(s for s in result.scenarios if s.capacity_kwh > 0).self_consumption_before
    assert f"{baseline:.0%}" in headline
    assert "solar" in headline

    # The two elements say different things, and the sentence keeps the figure.
    statement = no_payback_statement([s for s in result.scenarios if s.capacity_kwh > 0])
    assert statement is not None
    assert statement != headline
    assert "pays back within 20 years" in statement
    assert "20 years" not in headline, "the horizon belongs to the sentence"


def test_headline_does_not_blame_the_roof_when_the_house_is_not_saturated() -> None:
    """The reason must be true, not merely available.

    A household self-consuming a quarter of its PV has surplus the battery does
    capture; the sums fail on the tariff spread instead. Claiming saturation here
    would be a fabricated cause in the card's largest text — the same class of
    overclaim as "5 kWh is enough for this house".
    """
    result = beyond_lifetime()
    baseline = next(s for s in result.scenarios if s.capacity_kwh > 0).self_consumption_before
    assert baseline < HIGH_SELF_CONSUMPTION, "this fixture must not be saturated"

    headline = headline_for(result.scenarios)

    assert "own solar" not in headline
    assert f"{baseline:.0%}" not in headline
    assert "wears out" in headline


def test_a_partly_paying_sweep_is_not_described_as_a_single_capacity() -> None:
    """ "The only size analysed" is a claim about the sweep, not about the threshold.

    A regression introduced by the fix itself and caught by looking at the
    regenerated cards, not by the suite. The single-capacity branch counted the
    capacities that *pay back*; once that list was narrowed to those inside the
    battery's life, a sweep of 5/10/15 kWh where only 5 kWh clears 20 years took the
    branch and the headline read "5 kWh — the only size analysed" above a chart
    showing three bars. The card contradicted its own picture again, one branch
    over.

    Mirrors OPSD residential4's shape: paybacks spread across the horizon rather
    than clustered, so exactly one of the three clears it.
    """
    result = run_analysis(
        make_solar_days(n_days=60, pv_peak=1.5),
        make_report(days=365),
        capacities=[0, 5, 10, 15],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        battery_cost_per_kwh=120.0,
    )
    batteries = [s for s in result.scenarios if s.capacity_kwh > 0]
    within = [s for s in batteries if s.pays_back_within_lifetime()]
    assert len(within) == 1, "one capacity inside the horizon"
    assert len(batteries) > 1, "out of several analysed — the shape that broke"

    headline = headline_for(result.scenarios)

    assert "only size analysed" not in headline
    assert headline == f"{within[0].capacity_kwh:g} kWh pays back fastest"


def test_no_bar_is_emphasized_when_nothing_pays_back_in_time() -> None:
    """Emphasis is a recommendation in ink, and follows the same threshold.

    A lit bar under a headline that declined to recommend a size is the picture
    contradicting the sentence — and the picture is what a reader takes in first.
    Asserted on both panels, in the alpha the renderer actually set.
    """
    result = beyond_lifetime(pv_peak=1.2)
    figure = build_summary_card(result, tariff=FLAT_TARIFF)

    panels = [axes for axes in figure.axes if bars_of(axes)]
    assert panels, "the savings panel must still be drawn"
    for axes in panels:
        assert all(bar.get_alpha() != 1.0 for bar in bars_of(axes)), (
            "no capacity is recommended, so no bar may be at full strength"
        )
        assert not [t for t in axes.texts if t.get_fontweight() == "bold"], (
            "and no bar label may carry the emphasis either"
        )


def test_the_stat_row_does_not_call_a_dead_payback_a_payback() -> None:
    """ "42.1 years / to pay back" states as a payback what the panel denies.

    The third element on the same card, found by rendering the case rather than by
    reading the code. The figure stays — it is real, and suppressing it would invite
    the suspicion that nothing was computed — but the label carries the verdict.
    """
    figure = build_summary_card(beyond_lifetime(pv_peak=1.2), tariff=FLAT_TARIFF)
    text = card_text(figure)

    assert "never pays back" in text
    assert "to pay back" not in text.replace("Years to pay back", "").replace(
        "never pays back", ""
    ), "the panel heading and the negative label are the only 'pay back' phrases left"


def test_card_and_report_agree_on_whether_a_run_pays_back() -> None:
    """One run, two artifacts, one verdict.

    The report's Verdict said "5 kWh · payback 76.5 y / That is the shortest payback
    in the sweep" for the same data whose card said no capacity pays back. Two
    artifacts of one run disagreeing is worse than either being wrong alone, because
    the reader has no way to tell which to believe.
    """
    result = beyond_lifetime(pv_peak=1.2)
    card = card_text(build_summary_card(result, tariff=FLAT_TARIFF))
    report = render_report(result, FLAT_TARIFF)

    assert "shortest payback in the sweep" not in report
    assert "No capacity pays back within 20 years" in report
    # Both name the negative, and neither recommends a size.
    assert "pays back within 20 years" in card
    assert "pays back fastest" not in card


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


def test_no_cost_stat_supports_the_flattening_headline() -> None:
    """The stat under a "savings flatten" headline must be about the flattening.

    The card said "Savings flatten beyond 15 kWh" and printed 462 EUR underneath —
    the 20 kWh figure, i.e. the size the headline was implicitly advising against.
    The two are read as one statement, so whichever the reader believed, the card
    had told them the other. The marginal gain is what makes the headline true and
    is a number no other element carries.
    """
    # A sweep whose saturation is partial rather than exact, so the marginal gain
    # is a real figure: this household stops gaining at 20 kWh, so a knee at 17 has
    # a little left to buy. The fully-saturated case is covered separately below.
    result = build(cost_per_kwh=None, capacities=[0, 5, 10, 17, 25])
    figure = build_summary_card(result, tariff=FLAT_TARIFF)
    text = card_text(figure)

    knee, largest = 17.0, 25.0
    at_knee = next(s for s in result.scenarios if s.capacity_kwh == knee)
    at_largest = next(s for s in result.scenarios if s.capacity_kwh == largest)
    gain = at_largest.annual_savings_eur - at_knee.annual_savings_eur
    assert round(gain) > 0, "this fixture must have a non-zero marginal gain"

    assert f"Savings flatten beyond {knee:g} kWh" in text
    assert f"+{gain:,.0f} EUR" in text
    # The label names both ends, so the figure cannot be mistaken for a total.
    assert f"per year from {knee:g} kWh to {largest:g} kWh" in text


def test_a_fully_saturated_sweep_says_so_instead_of_printing_zero() -> None:
    """ "+0 EUR" in the card's second-largest text reads as a failure to compute.

    It is the strongest form of the headline — the extra capacity bought literally
    nothing — and a bare zero is the weakest way to say it. Saturation gets words
    when the number has nothing left to add.
    """
    result = build(cost_per_kwh=None, capacities=[0, 5, 10, 15, 20, 30])
    text = card_text(build_summary_card(result, tariff=FLAT_TARIFF))

    assert "Nothing" in text
    assert "more saved per year from 20 kWh to 30 kWh" in text
    assert "+0 EUR" not in text


def test_no_cost_stat_never_leads_with_the_largest_capacity_savings() -> None:
    """The figure that undercut the headline must not come back as the lead stat.

    Pinned separately from the positive assertion above because this is the actual
    defect: the largest battery's savings, printed prominently beneath a sentence
    saying the extra capacity is not worth buying.
    """
    result = build(cost_per_kwh=None, capacities=[0, 5, 10, 15, 20, 30])
    figure = build_summary_card(result, tariff=FLAT_TARIFF)

    largest = max(result.scenarios, key=lambda s: s.capacity_kwh)
    stat_values = sorted(figure.texts, key=lambda t: t.get_fontsize(), reverse=True)
    lead = stat_values[1].get_text()  # [0] is the headline

    assert f"{largest.annual_savings_eur:,.0f} EUR" != lead


# --- Bars always state their own value ---------------------------------------


def bars_of(axes: Axes) -> list[Rectangle]:
    """A panel's bars, read off the containers `bar()` registered.

    Two reasons not to walk `axes.patches` instead. It is typed as `Patch`, which
    carries neither `get_height` nor `get_width` — the bars are `Rectangle`s and do
    — so every call site would need an ignore that mypy and Pyright disagree about.
    And it holds more than the bars: the clipped-bar break and its detached stub
    are patches too, and measuring those as if they were bars would quietly
    corrupt any assertion about bar geometry.
    """
    return [bar for container in axes.containers for bar in container]


def bar_labels(axes: Axes) -> list[str]:
    """The direct labels drawn on a panel's bars, left to right."""
    return [t.get_text() for t in axes.texts]


@pytest.mark.parametrize(
    ("capacities", "cost", "days", "tariff"),
    [
        ([0, 5, 10, 15], DEFAULT_COST_PER_KWH, 365, FLAT_TARIFF),
        ([0, 5, 10, 15], None, 365, FLAT_TARIFF),
        ([10], DEFAULT_COST_PER_KWH, 365, FLAT_TARIFF),
        ([0, 5, 10, 15], DEFAULT_COST_PER_KWH, 60, FLAT_TARIFF),
        ([0, 5, 10, 15], DEFAULT_COST_PER_KWH, 365, LOSING_TARIFF),
    ],
)
def test_every_bar_carries_its_own_value(
    capacities: list[float], cost: float | None, days: int, tariff: Tariff
) -> None:
    """No mute bars, on any variant. The rule is uniform, not per-case.

    Labelling only the recommended bar left the reader to walk every other one back
    to a gridline, which is the arithmetic the card exists to have already done —
    and the argument the chart makes is about the *gaps* between capacities, which
    cannot be read off two bars when neither states its value. Emphasis still ranks
    the labels; it is no longer what decides whether one exists.
    """
    figure = build_summary_card(
        build(capacities=capacities, cost_per_kwh=cost, days=days, tariff=tariff),
        tariff=tariff,
    )
    for axes in figure.axes:
        assert len(bar_labels(axes)) == len(bars_of(axes)), (
            f"{axes.get_title(loc='left')}: one label per bar, no exceptions"
        )


def test_a_clipped_bar_is_labelled_like_every_other() -> None:
    """The clipped-bar break and its stub are patches, not bars, and carry no label.

    Worth its own case because it is where "one label per bar" could go wrong in
    both directions: the extra patches could be counted as unlabelled bars, or the
    clipped bar could keep the label it always had while the rest went mute. The
    true figure is what the label must state — the height is capped, so the bar
    alone understates it.
    """
    result = build(capacities=[0, 5, 10, 15], cost_per_kwh=DEFAULT_COST_PER_KWH)
    inflated = [
        s.model_copy(update={"battery_cost_eur": s.battery_cost_eur * 8})
        if s.capacity_kwh == 15 and s.battery_cost_eur is not None
        else s
        for s in result.scenarios
    ]
    result = result.model_copy(update={"scenarios": inflated})

    figure = build_summary_card(result, tariff=FLAT_TARIFF)
    payback_axes = figure.axes[1]
    assert len(payback_axes.patches) > len(bars_of(payback_axes)), (
        "this fixture must actually clip a bar"
    )

    labels = bar_labels(payback_axes)
    assert len(labels) == len(bars_of(payback_axes))

    clipped = max(
        (p for s in result.scenarios if (p := s.payback_years()) is not None),
    )
    assert f"{clipped:.1f}" in labels, "the true figure, not the capped height"


def test_losing_bars_are_not_left_as_ghosts_but_saturating_ones_are() -> None:
    """With nothing recommended, whether the bars carry weight depends on the case.

    Both cards emphasize no bar, so a single rule would have to treat them alike,
    and they are not alike. On the losing card the bars *are* the finding, and a
    row of 0.32-alpha ghosts reads as tentative about a result the headline states
    outright. On the saturating card the finding is the shape of the curve; four
    bars at full strength say nothing more than four receded ones and start
    competing with the headline they exist to support.
    """
    losing = build_summary_card(build(tariff=LOSING_TARIFF), tariff=LOSING_TARIFF)
    saturating = build_summary_card(build(cost_per_kwh=None), tariff=FLAT_TARIFF)

    assert all(bar.get_alpha() == 1.0 for bar in bars_of(losing.axes[0]))
    assert all(bar.get_alpha() != 1.0 for bar in bars_of(saturating.axes[0]))


def test_the_recommended_bar_label_still_reads_first() -> None:
    """Every bar is labelled, but not equally: the recommendation keeps the weight.

    Uniform labelling would flatten the hierarchy and cost the card its verdict.
    The emphasized label is bold and full-ink; the rest are normal weight.
    """
    result = build()
    figure = build_summary_card(result, tariff=FLAT_TARIFF)
    best = min(
        (s for s in result.scenarios if s.payback_years() is not None),
        key=lambda s: s.payback_years() or 0.0,
    )

    payback = best.payback_years()
    assert payback is not None

    for axes in figure.axes:
        bold = [t for t in axes.texts if t.get_fontweight() == "bold"]
        assert len(bold) == 1, "exactly one label carries the emphasis"
        label = bold[0].get_text()
        # The emphasized label belongs to the recommended capacity, whichever
        # measure the panel happens to be plotting.
        assert f"{best.annual_savings_eur:,.0f}" in label or f"{payback:.1f}" in label


@pytest.mark.parametrize(
    ("capacities", "cost", "days", "tariff"),
    [
        ([0, 5, 10, 15], DEFAULT_COST_PER_KWH, 365, FLAT_TARIFF),
        ([0, 5, 10, 15], None, 365, FLAT_TARIFF),
        ([10], DEFAULT_COST_PER_KWH, 365, FLAT_TARIFF),
        ([0, 5, 10, 15], DEFAULT_COST_PER_KWH, 60, FLAT_TARIFF),
        ([0, 5, 10, 15], DEFAULT_COST_PER_KWH, 365, LOSING_TARIFF),
    ],
)
def test_no_bar_touches_the_top_of_its_panel(
    capacities: list[float], cost: float | None, days: int, tariff: Tariff
) -> None:
    """Headroom above the tallest bar, on every panel of every variant.

    A bar whose top lands on the frame reads as clipped — as though the panel could
    not contain its own value — and its label, drawn above it, is clipped for real.
    The 60-day card topped out at 300 with two bars sitting exactly on the edge.
    """
    figure = build_summary_card(
        build(capacities=capacities, cost_per_kwh=cost, days=days, tariff=tariff),
        tariff=tariff,
    )
    for axes in figure.axes:
        bottom, top = axes.get_ylim()
        span = top - bottom
        heights = [bar.get_height() for bar in bars_of(axes)]
        panel = axes.get_title(loc="left")
        # Clearance is required on the side that carries the labels, and only
        # there: labels sit above positive bars and below negative ones, so an
        # all-positive panel is *supposed* to end at zero underneath. Requiring
        # room on both sides would re-introduce the dead band item 3 removed.
        tallest, lowest = max(heights), min(heights)
        if tallest > 0:
            assert top - tallest >= span * 0.10, (
                f"{panel}: the tallest bar needs room for its label"
            )
        if lowest < 0:
            assert lowest - bottom >= span * 0.10, (
                f"{panel}: the lowest bar needs room for its label"
            )


def axis_overruns(figure: Figure) -> list[str]:
    """Every bar or bar label that crosses one of its panel's horizontal edges.

    Measured in *pixels*, against the rendered extent of the artists, because that
    is the space the defect lives in. `test_no_bar_touches_the_top_of_its_panel`
    checks bar heights against the y-limits in data units, and that check passed on
    a card whose label was visibly outside the axis: the bar was comfortably inside
    the limits while the text drawn above it was not. Only the drawn extent can
    catch that, so the figure is drawn and each artist asked where it landed.
    """
    figure.canvas.draw()
    canvas = figure.canvas
    assert isinstance(canvas, FigureCanvasAgg)
    renderer = canvas.get_renderer()  # type: ignore[no-untyped-call]

    faults: list[str] = []
    for axes in figure.axes:
        box = axes.get_window_extent()
        panel = axes.get_title(loc="left")
        artists: list[tuple[str, Bbox]] = [
            (f"bar {bar.get_height():.4g}", bar.get_window_extent(renderer))
            for bar in bars_of(axes)
        ]
        artists += [
            (f"label {text.get_text()!r}", text.get_window_extent(renderer)) for text in axes.texts
        ]
        for name, extent in artists:
            if extent.y1 > box.y1:
                faults.append(f"{panel}: {name} overruns the top by {extent.y1 - box.y1:.1f}px")
            if extent.y0 < box.y0:
                faults.append(f"{panel}: {name} underruns the bottom by {box.y0 - extent.y0:.1f}px")
    return faults


@pytest.mark.parametrize(
    ("capacities", "cost", "days", "tariff"),
    [
        ([0, 5, 10, 15], DEFAULT_COST_PER_KWH, 365, FLAT_TARIFF),
        ([0, 5, 10, 15], None, 365, FLAT_TARIFF),
        ([10], DEFAULT_COST_PER_KWH, 365, FLAT_TARIFF),
        ([0, 5, 10, 15], DEFAULT_COST_PER_KWH, 60, FLAT_TARIFF),
        ([0, 5, 10, 15], DEFAULT_COST_PER_KWH, 365, LOSING_TARIFF),
        ([0, 5, 10, 15, 20], 600.0, 365, FLAT_TARIFF),
    ],
)
def test_no_bar_or_label_is_drawn_outside_its_panel(
    capacities: list[float], cost: float | None, days: int, tariff: Tariff
) -> None:
    """The invariant, stated where it can actually be violated: rendered pixels.

    A label drawn past the axis edge is clipped by the panel or collides with the
    band above it, and either way the card ships a number the reader cannot read.
    The data-unit check next door cannot see this, because the padding it verifies
    is a fraction of the *data span* while the label is placed at a fixed offset in
    *points* — so the same 15% is a different number of pixels on every panel, and
    whether it covered the text depended on how tall that panel happened to be.
    """
    figure = build_summary_card(
        build(capacities=capacities, cost_per_kwh=cost, days=days, tariff=tariff),
        tariff=tariff,
    )
    assert axis_overruns(figure) == []


def test_a_maximum_just_above_a_round_tick_still_fits_its_label() -> None:
    """303 against a 300 gridline — the geometry that actually broke the card.

    The 60-day card's savings axis ran to a 300 tick while its tallest bar was 303,
    and the "303 EUR" label was drawn past the top of the panel. It is the worst
    case for the padding because the bar sits as close to the top gridline as it can
    without passing it, leaving the label the least room, and it is exactly the
    arrangement a fractional allowance is most likely to get wrong. Pinned with the
    savings driven to that value rather than left to whichever fixture happens to
    produce it, so the case cannot quietly stop being tested when a number moves.

    **Both halves of the geometry are needed.** The awkward value alone does not
    reproduce it: on a full-year card, whose panels are the tallest the layout
    produces, the old fractional padding covered the label with a few pixels to
    spare. The seasonality band on a partial-year card takes a slice of the chart
    height, and it is the shorter panel that turns those few pixels negative — so
    this fixture carries the 60-day period as well as the 303.
    """
    result = build(days=60)
    # The card plots *annual* savings, so the raw period savings are scaled back
    # through the same `annualization_years` the renderer divides by — setting
    # `simulated_cost_eur` naively would land these targets 6x too high.
    years = annualization_years(result.days_analyzed)
    targets = {5.0: 199.0, 10.0: 288.0, 15.0: 303.0}
    pinned = [
        s.model_copy(
            update={"simulated_cost_eur": s.baseline_cost_eur - targets[s.capacity_kwh] * years}
        )
        if s.capacity_kwh in targets
        else s
        for s in result.scenarios
    ]
    result = result.model_copy(update={"scenarios": pinned})

    figure = build_summary_card(result, tariff=FLAT_TARIFF)
    savings_axes = figure.axes[0]

    # The fixture has to actually be the awkward case, or the test is vacuous.
    assert max(bar.get_height() for bar in bars_of(savings_axes)) == pytest.approx(303.0)
    ticks = [t for t in savings_axes.get_yticks() if t <= 303.0]
    assert max(ticks) == pytest.approx(300.0), "the bar must sit just above a tick"

    assert axis_overruns(figure) == []


def test_a_clipped_bar_label_is_inside_the_panel_too() -> None:
    """The clipped bar's label sits at double the usual gap, and must still fit.

    It is the one label the padding could most easily miss: the bar is already at
    the axis cap by construction, and the label is pushed further out than any
    other to clear the break stub drawn above it.
    """
    result = build(capacities=[0, 5, 10, 15], cost_per_kwh=DEFAULT_COST_PER_KWH)
    inflated = [
        s.model_copy(update={"battery_cost_eur": s.battery_cost_eur * 8})
        if s.capacity_kwh == 15 and s.battery_cost_eur is not None
        else s
        for s in result.scenarios
    ]
    result = result.model_copy(update={"scenarios": inflated})

    figure = build_summary_card(result, tariff=FLAT_TARIFF)
    payback_axes = figure.axes[1]
    assert len(payback_axes.patches) > len(bars_of(payback_axes)), (
        "this fixture must actually clip a bar"
    )
    assert axis_overruns(figure) == []


def test_bar_label_gaps_are_uniform_regardless_of_weight() -> None:
    """The bold label and the regular ones sit the same distance from their bars.

    The emphasized label carries the recommendation, so it is the one that must not
    look misplaced — and a gap that varied with font weight would put the card's
    most important number at a different distance from its bar than every other,
    which reads as a mistake rather than as emphasis.
    """
    figure = build_summary_card(build(), tariff=FLAT_TARIFF)
    figure.canvas.draw()
    canvas = figure.canvas
    assert isinstance(canvas, FigureCanvasAgg)
    renderer = canvas.get_renderer()  # type: ignore[no-untyped-call]

    for axes in figure.axes:
        bars = bars_of(axes)
        assert any(t.get_fontweight() == "bold" for t in axes.texts), (
            "this panel must actually mix weights, or the test proves nothing"
        )
        gaps = [
            text.get_window_extent(renderer).y0 - bar.get_window_extent(renderer).y1
            for bar, text in zip(bars, axes.texts, strict=True)
        ]
        assert max(gaps) - min(gaps) < 0.5, (
            f"{axes.get_title(loc='left')}: label gaps differ by weight: {gaps}"
        )


def test_the_losing_panel_is_not_padded_into_empty_space_above_zero() -> None:
    """Zero anchors the empty side: no axis running to +200 with nothing in it.

    A fifth of the losing card's panel was spent on the region where the finding
    is not, which flattened the losses it exists to show. Nothing is ever drawn or
    written above zero when every bar is below it, so nothing is reserved there.
    """
    result = build(tariff=LOSING_TARIFF)
    axes = build_summary_card(result, tariff=LOSING_TARIFF).axes[0]

    bottom, top = axes.get_ylim()
    span = top - bottom
    assert 0.0 <= top <= span * 0.06, "zero is the top, give or take a hairline"
    assert bottom < min(s.annual_savings_eur for s in result.scenarios)


# --- Colour carries the sign -------------------------------------------------


def test_losses_are_not_drawn_in_the_savings_colour() -> None:
    """A bar that loses money must not look like a bar that earns it.

    -1,254 EUR and +462 EUR were drawn in the same light blue, leaving the reader
    to take the direction from the axis — the slowest thing on the panel to read.
    Sign is not a category here, it is the threshold the whole card is about.
    """
    losing = build_summary_card(build(tariff=LOSING_TARIFF), tariff=LOSING_TARIFF)
    earning = build_summary_card(build(), tariff=FLAT_TARIFF)

    # Through `to_rgba` rather than comparing whatever `get_facecolor` returns:
    # the same colour can come back as a name, a hex string or a tuple, and two
    # spellings of one hue would make this test pass by accident.
    loss_colours = {to_rgba(bar.get_facecolor()) for bar in bars_of(losing.axes[0])}
    savings_colours = {to_rgba(bar.get_facecolor()) for bar in bars_of(earning.axes[0])}

    assert loss_colours.isdisjoint(savings_colours)
    # Red rather than merely different: the hue has to mean "lost", not "other".
    for red, green, blue, _ in loss_colours:
        assert red > green and red > blue


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


def test_card_savings_and_payback_are_mutually_consistent() -> None:
    """The two headline stats must agree with each other, on any period length.

    The card printed "199 EUR saved per year" beside "91.7 years to pay back" for a
    3,000 EUR battery, where 3000/199 is 15.1 — the savings figure annualized and
    the payback one did not. Two numbers side by side that a reader can divide in
    their head must survive that division, and on a 60-day card they did not.
    """
    result = build(days=60, cost_per_kwh=600.0)
    best = min(
        (s for s in result.scenarios if s.payback_years() is not None),
        key=lambda s: s.payback_years() or 0.0,
    )
    payback = best.payback_years()
    assert payback is not None
    assert best.battery_cost_eur is not None

    text = card_text(build_summary_card(result, tariff=FLAT_TARIFF))
    assert f"{best.annual_savings_eur:,.0f} EUR" in text

    # The division a reader would do, against the two figures actually printed.
    implied = best.battery_cost_eur / best.annual_savings_eur
    assert payback == pytest.approx(implied)


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


def test_no_battery_cost_reports_saturation_instead_of_a_recommendation() -> None:
    """The headline switches to what the data supports: where savings stop growing."""
    result = build(cost_per_kwh=None, capacities=[0, 5, 10, 15, 20, 30])
    figure = build_summary_card(result, tariff=FLAT_TARIFF)
    headline = max(figure.texts, key=lambda t: t.get_fontsize()).get_text()

    assert "flatten beyond" in headline
    assert "pays back" not in headline, "there is no payback without a cost"


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

    lone_bar = bars_of(one.axes[0])[0]
    crowd_bar = bars_of(many.axes[0])[0]
    assert lone_bar.get_width() <= crowd_bar.get_width()

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


def test_paybacks_beyond_a_battery_lifetime_become_a_sentence_not_bars() -> None:
    """When nothing pays back in time, bars are the wrong encoding and are dropped.

    Truncating them destroys the very thing a bar chart is for: on the 60-day card,
    91.7 / 126.6 / 181.0 years rendered as three near-identical stubs, implying the
    paybacks were similar when the longest was double the shortest. A chart that
    misstates its own values is worse than no chart, so the panel is replaced by a
    plain statement carrying the real shortest figure.
    """
    result = build(days=60, cost_per_kwh=6000.0)
    paybacks = [p for s in result.scenarios if (p := s.payback_years()) is not None]
    assert paybacks and min(paybacks) > 20, "this fixture must overflow the horizon"

    figure = build_summary_card(result, tariff=FLAT_TARIFF)
    text = card_text(figure)

    assert len(figure.axes) == 1, "only the savings panel is plotted"
    assert "No capacity pays back within 20 years" in text
    assert f"{min(paybacks):.1f} y" in text, "the real shortest payback is named"


def test_the_replacement_sentence_names_the_capacity_that_achieves_it() -> None:
    """ "No capacity pays back" without a number reads as a failure to compute."""
    result = build(days=60, cost_per_kwh=6000.0)
    statement = no_payback_statement(result.scenarios)
    assert statement is not None

    fastest = min(
        (s for s in result.scenarios if s.payback_years() is not None),
        key=lambda s: s.payback_years() or 0.0,
    )
    assert f"at {fastest.capacity_kwh:g} kWh" in statement


def test_every_drop_path_gives_the_savings_panel_the_same_height() -> None:
    """One rule, one code path: the panel dropped means the height is reallocated.

    Three separate reasons drop the payback panel — no battery cost, no positive
    savings anywhere, and every payback past the battery's life — and the rule is
    the same for all of them. The beyond-lifetime drop was added last and reallocated
    nothing: it subtracted a whole statement band from the panel *and* left it pinned
    above the footer, so ~15% of the card was empty surface while the other two drop
    paths filled it.

    Asserted on the axes' measured extents rather than on a mode flag, because the
    flag was right in the defect: the branch was taken and the layout was not.
    """
    two_panel = build_summary_card(build(), tariff=FLAT_TARIFF)
    drops = {
        "no cost": build_summary_card(build(cost_per_kwh=None), tariff=FLAT_TARIFF),
        "no positive savings": build_summary_card(
            build(tariff=LOSING_TARIFF), tariff=LOSING_TARIFF
        ),
        "beyond lifetime": build_summary_card(beyond_lifetime(), tariff=FLAT_TARIFF),
    }

    for name, figure in drops.items():
        assert len(figure.axes) == 1, f"{name}: the payback panel must be dropped"

    heights = {name: f.axes[0].get_position().height for name, f in drops.items()}
    tallest, shortest = max(heights.values()), min(heights.values())

    # Both bounds below are stated in absolute figure fractions rather than in terms
    # of `_STATEMENT_BAND`. Deriving them from the band makes the yardstick move with
    # the thing under test: widening the band widens the tolerance exactly as fast as
    # it widens the gap, so the defect stays inside its own allowance and the test
    # cannot see it. The band is an implementation detail; the card is 1200px and the
    # reader sees fractions of it.
    #
    # A sentence occupies two lines of text, so the paths that draw one may sit at
    # most that much lower than the paths that do not.
    two_lines = 0.075
    assert tallest - shortest <= two_lines, f"the drop paths must share one layout, got {heights}"

    # And each must reclaim the dropped panel: roughly the two-panel height twice
    # over, less the gap that separated them. Anchored to what a reclaiming panel
    # actually reaches, because a layout that shrank every drop path uniformly would
    # still clear the two-panel figure while leaving the card empty.
    shared = two_panel.axes[0].get_position().height
    for name, height in heights.items():
        assert height >= 2 * shared - two_lines, (
            f"{name}: dropping the payback panel must reclaim its height "
            f"({height:.4f} against {shared:.4f} per panel when both are drawn)"
        )


def test_the_dropped_panel_does_not_leave_the_card_empty_above_the_footer() -> None:
    """The defect as the reader saw it: a short panel over a strip of blank surface.

    A height assertion alone would pass if the panel merely floated higher, so this
    pins the ink: whatever the lowest thing the chart draws is — the sentence when
    there is one, the axis labels otherwise — it must come down near the footer rule
    rather than stopping a sixth of a card short of it.
    """
    figure = build_summary_card(beyond_lifetime(), tariff=FLAT_TARIFF)
    figure.canvas.draw()
    canvas = figure.canvas
    assert isinstance(canvas, FigureCanvasAgg)  # attached when the card is built
    renderer = canvas.get_renderer()  # type: ignore[no-untyped-call]

    axes = figure.axes[0]
    furniture = axes.get_tightbbox(renderer).transformed(figure.transFigure.inverted())
    statement = [t for t in figure.texts if "pays back within" in t.get_text()]
    assert statement, "the beyond-lifetime card carries the replacement sentence"

    lowest = min([furniture.y0, *(t.get_position()[1] for t in statement)])
    footer_rule = 0.106
    assert lowest - footer_rule < 0.09, (
        f"the chart stops {lowest - footer_rule:.3f} above the footer rule — "
        "the dropped panel's height was not reclaimed"
    )


def test_paybacks_within_a_lifetime_are_still_drawn_as_bars() -> None:
    """The sentence is the exception, not a replacement for the panel."""
    figure = build_summary_card(build(), tariff=FLAT_TARIFF)
    text = card_text(figure)

    assert len(figure.axes) == 2
    assert "No capacity pays back" not in text


def test_renders_without_a_tariff() -> None:
    """The tariff is optional on the API, and the card degrades to omitting it."""
    text = card_text(build_summary_card(build(), tariff=None))
    assert "pays back fastest" in text
    assert "EUR/kWh" not in text


def test_the_package_ships_its_py_typed_marker() -> None:
    """Without it, every type hint in this strict-mode package is invisible downstream.

    PEP 561: a package's annotations are only honoured by type checkers if it ships
    a `py.typed` marker. Its absence cost nothing visible here — the suite passed,
    ruff passed, `mypy src` passed — while `mypy tests` reported 25 `import-untyped`
    errors and, far worse, silently degraded every symbol the tests imported to
    `Any`. A strict-mode project whose own tests are unchecked is the failure this
    marker prevents, and the same is true for anyone installing the package.

    Pinned as a test because an empty file is exactly what a packaging refactor
    drops without anything failing.
    """
    marker = Path(__file__).resolve().parent.parent / "src" / "battery_worth" / "py.typed"
    assert marker.exists(), "PEP 561 marker missing: annotations stop at the package edge"


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


def test_the_image_label_names_the_same_repository() -> None:
    """The Dockerfile's OCI source label is the third copy, and is pinned like the rest.

    `org.opencontainers.image.source` is what a registry and every image-provenance
    tool read to link a published image back to its source, so it earns its place —
    but adding it made a third hand-written copy of a URL that already exists in
    `battery_worth.REPO_URL` and `[project.urls]`, and a Dockerfile is not
    importable any more than packaging metadata is. An unpinned copy is precisely
    how the wrong account survived in one artifact while the others were corrected.

    Parsed with a narrow reader rather than a Dockerfile library: this asserts one
    label's value, and a dependency for that would be a strange thing to install
    into a project whose analysis engine is deliberately dependency-light.
    """
    dockerfile = Path(__file__).resolve().parent.parent / "Dockerfile"
    assert dockerfile.exists(), "the image is part of the shipped surface"

    prefix = "LABEL org.opencontainers.image.source="
    values = [
        line.strip().removeprefix(prefix).strip().strip('"')
        for line in dockerfile.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith(prefix)
    ]

    assert values == [REPO_URL], (
        "the image's source label must name the repository exactly once, and "
        "must be the same URL the card and the report print"
    )


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
