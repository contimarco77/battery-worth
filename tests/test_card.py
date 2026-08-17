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

import re
import tomllib
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import to_rgba
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.text import Text
from matplotlib.transforms import Bbox
from PIL import Image

from battery_worth import PROJECT_NAME, REPO_DISPLAY_URL, REPO_URL
from battery_worth import card as card_module
from battery_worth.analysis import run_analysis
from battery_worth.card import (
    _BAND_GAP as CARD_BAND_GAP,
)
from battery_worth.card import (
    _MARGIN as CARD_MARGIN,
)

# `_MARGIN` is the card's own text margin, imported rather than copied: it is the
# edge the headline must stay inside, and a second copy of the number here would let
# the two drift apart silently — leaving the test measuring against a boundary the
# card no longer uses. Private because nothing outside the renderer sets layout; the
# test asserts on the renderer's own contract, which is the one case for reaching in.
from battery_worth.card import (
    _MAX_SAVINGS_TICKS as CARD_MAX_SAVINGS_TICKS,
)
from battery_worth.card import (
    CARD_PX,
    build_summary_card,
    headline_for,
    no_payback_statement,
    render_summary_card,
)
from battery_worth.models import (
    BATTERY_LIFETIME_YEARS,
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

# Figure fraction one wrapped headline line costs everything below it. The card is
# 1200px and headlines wrap at up to 54pt, so a second line pushes the bands under it
# down by roughly this much. Stated as an absolute fraction rather than derived from
# the renderer's sizes for the same reason the layout bounds are: a yardstick computed
# from the thing under test moves with it, and stops being able to see the defect.
_HEADLINE_LINE = 0.07


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

    assert "to pay back" not in text.replace("Years to pay back", ""), (
        "the panel heading is the only 'to pay back' phrase left"
    )


def test_the_stat_caption_claims_no_more_than_the_engine_computed() -> None:
    """The caption may deny the horizon; it may not deny that a payback exists.

    The label read "never pays back" above a stat reading "76.5 years" — the engine
    computed a finite figure and the caption asserted an infinite one. "Never" is
    not a rounding of 76.5, it is a different claim, and it is the one claim on the
    card the reader cannot check against the number printed directly above it.

    Asserted as a shape rather than as the replacement string, because pinning the
    new wording would pass just as happily on the next caption that overstates the
    arithmetic. What must hold: the caption is bounded by the *horizon*, and the
    finite figure the engine produced is still on the card beside it.
    """
    result = beyond_lifetime(pv_peak=1.2)
    text = card_text(build_summary_card(result, tariff=FLAT_TARIFF))

    shortest = min(
        p for s in result.scenarios if (p := s.payback_years()) is not None and s.capacity_kwh > 0
    )
    # The engine's own figure is still shown: the caption qualifies it, not hides it.
    assert f"{shortest:.1f} years" in text

    # No unbounded claim anywhere on a card whose engine returned a finite payback.
    assert "never" not in text.lower(), (
        f"the shortest payback is a finite {shortest:.1f} years, "
        "so no element may claim the battery never pays back"
    )

    # And the bound it does state is the domain's horizon, not a number typed here.
    assert f"{BATTERY_LIFETIME_YEARS:.0f} years" in text


def test_the_stat_caption_states_the_same_horizon_as_the_statement_band() -> None:
    """Caption and band cannot drift: both read the horizon from one constant.

    The band already said "within 20 years" while the caption said "never". Two
    elements two centimetres apart describing one threshold in incompatible terms
    is the defect this pair guards against — so the test moves the constant and
    requires both to follow. Hard-coding 20 in the caption passes today's card and
    fails here the moment the domain's horizon changes.
    """
    result = beyond_lifetime(pv_peak=1.2)

    with patch.object(card_module, "_BATTERY_LIFETIME_YEARS", 30.0):
        text = card_text(build_summary_card(result, tariff=FLAT_TARIFF))

    horizons = {int(match) for match in re.findall(r"within (\d+) years", text)}
    assert horizons == {30}, (
        f"every element naming the horizon must read the constant, got {horizons}"
    )


def test_the_statement_heading_clears_the_panel_above_it() -> None:
    """The dropped-panel path put two labels 1.5px apart on a card with 120px spare.

    "Usable battery capacity" is drawn *below* the savings panel's axes box, and the
    statement heading was placed by subtracting `_PANEL_TITLE_SPACE` — the room a
    heading needs *above* a box, a different quantity that happened to land within
    1.5px of the furniture's depth. The two labels touched while the band below them
    went unused.

    This is a layout defect, so passing here is not evidence the card looks right —
    that is settled by looking at it. What the assertion is worth is the direction:
    it fails if the heading is ever again placed by a constant that does not measure
    what it has to clear. Asserted against the card's own inter-band spacing rather
    than a pixel count chosen to match today's render.
    """
    figure = build_summary_card(beyond_lifetime(pv_peak=1.2), tariff=FLAT_TARIFF)
    canvas = figure.canvas
    assert isinstance(canvas, FigureCanvasAgg)
    canvas.draw()
    renderer = canvas.get_renderer()
    height = figure.bbox.height

    panels = [ax for ax in figure.axes if ax.get_xlabel() == "Usable battery capacity"]
    assert len(panels) == 1, "the dropped-panel path labels exactly one shared x-axis"
    furniture_bottom = min(
        label.get_window_extent(renderer=renderer).y0
        for label in [*panels[0].get_xticklabels(), panels[0].xaxis.get_label()]
        if label.get_text()
    )

    headings = [t for t in figure.texts if t.get_text() == "Years to pay back"]
    assert len(headings) == 1, "the statement band draws exactly one heading"
    heading_top = headings[0].get_window_extent(renderer=renderer).y1

    gap = (furniture_bottom - heading_top) / height
    assert gap >= CARD_BAND_GAP * 0.9, (
        f"the heading clears the x-axis furniture by {gap:.4f} of the card, "
        f"less than the {CARD_BAND_GAP} gap used between every other band"
    )


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


# A spread narrow enough that the battery saves money every year but so little of it
# that nothing recovers its cost — the `beyond_lifetime_thin_spread` sample case, and
# the one whose savings land in single-digit EUR where the axis locator misbehaved.
THIN_SPREAD_TARIFF = Tariff(
    kind=TariffKind.FLAT, flat_price_eur_kwh=0.25, export_price_eur_kwh=0.22
)


def sample_cards() -> dict[str, Figure]:
    """One built figure per case in `scripts/render_sample_cards.py`, by name.

    The sample script exists because the card's defects are invisible to assertions
    about *content* — a clipped headline and a mislabelled axis are both perfectly
    correct strings drawn in the wrong place. The script renders every branch so a
    human can look; this mirrors its case list so the properties a human would have
    to notice by eye are checked on every one of them automatically.

    Kept as a parallel list rather than importing the script, which needs the Ausgrid
    fixture the suite deliberately does not depend on. The shapes are what matter and
    they are reproduced from the synthetic fixture: what must not drift is the set of
    *branches* covered, which is what both lists are maintained against.
    """
    return {
        "ausgrid": build_summary_card(build(), tariff=FLAT_TARIFF),
        "no_cost": build_summary_card(build(cost_per_kwh=None), tariff=FLAT_TARIFF),
        "no_cost_no_knee": build_summary_card(
            build(capacities=[0, 20], cost_per_kwh=None), tariff=FLAT_TARIFF
        ),
        "single_capacity": build_summary_card(build(capacities=[10]), tariff=FLAT_TARIFF),
        "60_days": build_summary_card(build(days=60), tariff=FLAT_TARIFF),
        "no_positive_savings": build_summary_card(
            build(tariff=LOSING_TARIFF), tariff=LOSING_TARIFF
        ),
        # The two no-payback shapes: a saturated roof, and a thin spread.
        "beyond_lifetime": build_summary_card(beyond_lifetime(pv_peak=1.2), tariff=FLAT_TARIFF),
        "beyond_lifetime_thin_spread": build_summary_card(
            build(tariff=THIN_SPREAD_TARIFF, cost_per_kwh=600.0), tariff=THIN_SPREAD_TARIFF
        ),
        "baseline_only": build_summary_card(build(capacities=[0]), tariff=FLAT_TARIFF),
    }


def pinned_savings(result: AnalysisResult, targets: dict[float, float]) -> AnalysisResult:
    """`result` with the named capacities' annual savings driven to exact figures.

    Lets a test state the geometry it is about — "a bar at 202 against a 200 tick",
    "savings in single digits" — instead of hunting for a fixture that happens to
    produce it, which is how a case quietly stops being tested when a number moves.

    The card plots *annual* savings, so the targets are scaled back through the same
    `annualization_years` the renderer divides by; setting `simulated_cost_eur`
    naively would land them wrong by that factor.
    """
    years = annualization_years(result.days_analyzed)
    scenarios = [
        s.model_copy(
            update={"simulated_cost_eur": s.baseline_cost_eur - targets[s.capacity_kwh] * years}
        )
        if s.capacity_kwh in targets
        else s
        for s in result.scenarios
    ]
    return result.model_copy(update={"scenarios": scenarios})


def headline_of(figure: Figure) -> Text:
    """The headline artist: the topmost bold text the card draws.

    Identified by *position* rather than by matching its string, because the string
    is what varies per case and the whole point is to check every case's headline.

    Emphatically not "the largest bold text", which is what this helper tried first
    and which quietly broke the test that uses it. The headline shrinks to fit, so on
    the one case that shrinks furthest it can end up *smaller* than the 32pt stat
    values below it — and a size-based lookup then measures a stat instead, reports it
    as comfortably inside the margin, and passes while the real headline runs off the
    card. That is precisely the defect under test, hidden by the helper meant to
    expose it. The headline's position is invariant; its size is the variable.
    """
    candidates = [t for t in figure.texts if t.get_fontweight() == "bold"]
    assert candidates, "the card always draws a bold headline"
    return max(candidates, key=lambda t: t.get_position()[1])


# The clearance every drawn string must leave between its own edge and the drawable
# boundary. Picked from the measured distribution, not from a round number: on the
# current sample set the tightest card (`beyond_lifetime_thin_spread`) clears by
# ~5px, `baseline_only` by ~11px, and everything else by 28-42px. So the choice is
# between the two failure modes on either side of that 5px.
#
# A bare "does not exceed the boundary" — the previous assertion — passes at 0.1px
# of clearance, which is a headline touching the edge it is supposed to stay inside.
# Setting the floor at the tightest card's own 5px is the opposite failure: the test
# then passes by a hair and the next wording change breaks it for being 1px wider,
# which trains the reader to raise the number rather than to look at the card.
#
# 4px sits just under the tightest real card. It is above the ±1px of rounding the
# renderer's own extent measurement carries, so it cannot flap; and it is far enough
# below 5px that the thin-spread headline is not sitting on the threshold. What it
# catches is the case worth catching: a string that consumes the remaining gap
# entirely, which is the shape every clipping defect on this card has had.
_MIN_TEXT_CLEARANCE_PX = 4.0

# The fewest gridlines a savings panel can draw and still be a scale. Two is the
# locator's own floor — it returns the endpoints even when asked for one bin — and
# an axis of nothing but its endpoints brackets the data rather than measuring it.
# Three is the first count with a gridline *between* them, which is the one a reader
# steps a bar against.
_MIN_SAVINGS_TICKS = 3


def test_no_text_is_drawn_within_a_few_pixels_of_the_drawable_edge() -> None:
    """Every drawn string must clear the *drawable* boundary, not merely the card.

    **The boundary is x=90..1110, not 0..1200.** The card is 1200px wide but the
    text margin insets it by `_MARGIN` on each side, and that inset edge is the one
    a headline is laid out against — `_fit_headline` shrinks and wraps to
    `_WIDTH * CARD_PX`, so a string that reached the card edge would already be
    ~90px past the layout's own limit. Asserting against 1200 would give the
    tightest card 99px of imaginary room and protect nothing.

    **And clearance is asserted explicitly**, rather than "does not exceed". A
    boundary test with no margin passes at a fraction of a pixel of daylight, which
    on this card is indistinguishable from the clipping it exists to prevent. See
    `_MIN_TEXT_CLEARANCE_PX` for how the figure was chosen.

    **The defect this exists to catch shipped.** `beyond_lifetime_thin_spread` drew
    "No size pays back before the battery wears out" to x=1199 on a 1200px card — the
    headline ran to the last pixel column and was cut. The renderer's shrink-to-fit
    loop stepped down to its minimum size and then returned that minimum
    unconditionally, so a string still too wide at the floor was reported as fitting.
    A fit loop whose last step gives up silently is not a fit.

    It survived because the suite could only see the *string*, which was correct, and
    the sample-card script that renders the picture had nobody assert on its output.
    So the property is measured here in the terms the defect lives in: the rendered
    extent of the drawn text against the card's own margins.

    **Every text artist, not only the headline.** Scoped to the headline first, this
    test went green while the fix for it pushed the *subtitle* off the same edge:
    `baseline_only`'s replacement sentence was rewritten longer, and unlike the
    headline it is drawn at a fixed size with no fitting at all, so its length was
    the only thing holding it on the card. Checking one artist licenses the identical
    defect in every other — so the invariant is stated over all of them.
    """
    left_edge = CARD_MARGIN * CARD_PX
    right_edge = CARD_PX - CARD_MARGIN * CARD_PX

    faults: list[str] = []
    for name, figure in sample_cards().items():
        figure.canvas.draw()
        canvas = figure.canvas
        assert isinstance(canvas, FigureCanvasAgg)
        renderer = canvas.get_renderer()  # type: ignore[no-untyped-call]
        for text in figure.texts:
            if not text.get_text():
                continue
            extent = text.get_window_extent(renderer=renderer)
            # The clearance rule applies to the edge the string is free to grow
            # toward, which is the one its length can push past. A left-aligned
            # string grows rightward; the footer's right-aligned URL grows leftward.
            # Its anchored edge sits *on* the margin by construction, so demanding
            # clearance there would fail the layout for being correct — that edge is
            # checked for alignment instead, within the renderer's rounding.
            if text.get_horizontalalignment() == "right":
                free, anchored = right_edge - extent.x0, extent.x1 - right_edge
            else:
                free, anchored = extent.x1 - left_edge, left_edge - extent.x0
            clearance = (1.0 - 2 * CARD_MARGIN) * CARD_PX - free
            if clearance < _MIN_TEXT_CLEARANCE_PX:
                faults.append(
                    f"{name}: {text.get_text()[:50]!r} spans "
                    f"x={extent.x0:.1f}..{extent.x1:.1f} and clears the drawable "
                    f"edge by only {clearance:.1f}px "
                    f"(minimum {_MIN_TEXT_CLEARANCE_PX:.1f}px)"
                )
            if anchored > 1:
                faults.append(
                    f"{name}: {text.get_text()[:50]!r} starts at x={extent.x0:.1f}, "
                    f"outside the x={left_edge:.0f}..{right_edge:.0f} drawable area"
                )
    assert faults == []


def test_every_savings_tick_label_names_the_value_it_sits_at() -> None:
    """A gridline labelled "2" must be at 2, not at 2.5.

    **The defect this exists to catch shipped.** On `beyond_lifetime_thin_spread`,
    whose savings are 8/15/18 EUR, the locator chose a step of 2.5 and the formatter
    printed integers: the axis read 0, 2, 5, 8, 10, 12, 15, 18, 20 for gridlines
    actually at 0, 2.5, 5, 7.5, 10 ... Every reader checking a bar against a gridline
    got a wrong number off an axis that was misstating itself.

    It had never been seen because every earlier card had savings in the hundreds,
    where the chosen step happens to be integral — so this is checked across the whole
    sample set rather than on the one case that broke, since the next range to pick a
    fractional step is the one nobody has looked at yet.

    **The test compares a label against a position**, which is the shape of the
    defect: a formatter-was-called assertion would have passed throughout, because the
    formatter was called and did exactly what it said. Only reading the tick's own
    location back out of the axis can see the disagreement.

    The single-digit case is pinned explicitly rather than left to the sample set.
    None of the synthetic fixtures happens to land on a fractional step — they all
    produce integral ticks with or without the fix — so a sweep of them alone asserts
    a property that is true for the wrong reason and cannot fail. The 8/15/18 EUR
    shape that actually shipped the defect is built here so the test can see it.
    """
    cases = sample_cards()
    cases["single digit savings"] = build_summary_card(
        pinned_savings(build(), {5.0: 8.0, 10.0: 15.0, 15.0: 18.0}), tariff=FLAT_TARIFF
    )

    faults: list[str] = []
    for name, figure in cases.items():
        for axes in figure.axes:
            if axes.get_ylabel() != "EUR / year":
                continue
            figure.canvas.draw()
            low, high = axes.get_ylim()
            for location, label in zip(axes.get_yticks(), axes.get_yticklabels(), strict=True):
                text = label.get_text()
                # Only the ticks actually inside the panel are drawn, and only those
                # are what the reader can misread.
                if not text or not low <= location <= high:
                    continue
                named = float(text.replace(",", "").replace("−", "-"))
                if named != pytest.approx(location):
                    faults.append(f"{name}: gridline at {location} is labelled {text!r}")
    assert faults == []


def test_the_savings_axis_stays_a_scale_rather_than_becoming_texture() -> None:
    """A savings panel may not draw more gridlines than the card can afford.

    **This is the other half of the tick-label fix, and it exists because the fix
    had a cost that went unmeasured.** Constraining tick *locations* to integers made
    the labels honest, and as a side effect took the locator off its preferred steps
    onto finer admissible ones: the gridline count roughly doubled across the sample
    set — 60_days 4 to 10, ausgrid 6 to 9, residential6 6 to 10 — on panels that had
    nothing wrong with them. The edge case was repaired on the normal path's budget.

    Density is a correctness property here, not a preference, which is why it is
    pinned rather than left to the eye. The card gets about three seconds and every
    element competes with the verdict; gridlines compete directly with the bars,
    which are the argument. Ten of them read as texture behind the data where four
    read as a scale against it.

    Asserted over the whole sample set for the same reason the label test is: the
    count is a function of the data range, so the range that next drives it up is the
    one nobody has looked at.

    **The lower bound is the half that keeps the cap honest.** An upper bound alone
    is satisfied by tightening the cap arbitrarily far, and the axis degrades long
    before the count reaches zero: at `nbins=1` the locator still returns two ticks,
    but they are the endpoints — the 60-day panel reads 0 and 1,000 for bars in the
    low hundreds, an axis that brackets the data instead of measuring it. Three is
    the smallest count with an interior gridline, which is what a reader steps a bar
    against, so that is where the floor goes.
    """
    faults: list[str] = []
    for name, figure in sample_cards().items():
        figure.canvas.draw()
        for axes in figure.axes:
            if axes.get_ylabel() != "EUR / year":
                continue
            low, high = axes.get_ylim()
            drawn = [
                location
                for location, label in zip(axes.get_yticks(), axes.get_yticklabels(), strict=True)
                if label.get_text() and low <= location <= high
            ]
            if len(drawn) > CARD_MAX_SAVINGS_TICKS:
                faults.append(
                    f"{name}: savings panel draws {len(drawn)} gridlines "
                    f"({', '.join(f'{t:g}' for t in drawn)}), "
                    f"more than the {CARD_MAX_SAVINGS_TICKS} the card affords"
                )
            if len(drawn) < _MIN_SAVINGS_TICKS:
                faults.append(
                    f"{name}: savings panel draws {len(drawn)} gridlines "
                    f"({', '.join(f'{t:g}' for t in drawn)}) — fewer than the "
                    f"{_MIN_SAVINGS_TICKS} it takes to have an interior one"
                )
    assert faults == []


def test_a_negative_savings_panel_still_draws_zero_as_a_gridline() -> None:
    """Zero survives the tick cap, because zero is the meaning of that chart.

    On the losing card the whole finding is *which side of zero the bars are on*, so
    the crossing point has to be a labelled gridline and not an inference from the
    two ticks either side of it. Capping the tick count is exactly the kind of change
    that could take it away — fewer ticks over a range running from -1,254 to 0 could
    land on -1,300/-650 and skip the one value that matters.

    Checked as its own property rather than folded into the density test: that one
    would pass on an axis of four evenly spaced gridlines none of which is zero.
    """
    figure = build_summary_card(build(tariff=LOSING_TARIFF), tariff=LOSING_TARIFF)
    figure.canvas.draw()

    panels = [axes for axes in figure.axes if axes.get_ylabel() == "EUR / year"]
    assert panels, "the losing card still draws a savings panel"
    for axes in panels:
        low, high = axes.get_ylim()
        drawn = [
            location
            for location, label in zip(axes.get_yticks(), axes.get_yticklabels(), strict=True)
            if label.get_text() and low <= location <= high
        ]
        assert 0.0 in drawn, f"zero must be a drawn gridline, got {drawn}"


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


def _bar_just_above_a_gridline() -> tuple[float, Figure]:
    """A 60-day card whose tallest bar sits barely above a gridline, and that bar.

    Sweeps candidate maxima and keeps the first whose tallest bar clears its own
    gridline by under 5% of its height — the geometry that leaves a bar label the
    least room, and the one that actually broke the card. Which value produces it is
    a function of whatever step the locator currently chooses, so it is discovered
    per run rather than written down; that is the whole point of searching.

    Raises if no candidate reproduces the shape, because a test that silently falls
    back to an easy fixture is worse than one that fails: it reports the awkward case
    as passing while no longer containing it.
    """
    for top in range(60, 400):
        result = pinned_savings(
            build(days=60), {5.0: top * 0.65, 10.0: top * 0.94, 15.0: float(top)}
        )
        figure = build_summary_card(result, tariff=FLAT_TARIFF)
        tallest = max(bar.get_height() for bar in bars_of(figure.axes[0]))
        below = [t for t in figure.axes[0].get_yticks() if t <= tallest]
        if below and 0 < tallest - max(below) < 0.05 * tallest:
            return tallest, figure
    raise AssertionError("no candidate maximum reproduced a bar just above a gridline")


def test_a_maximum_just_above_a_round_tick_still_fits_its_label() -> None:
    """A bar just above a gridline — the geometry that actually broke the card.

    The 60-day card's savings axis ran to a round tick while its tallest bar sat
    barely above it, and that bar's label was drawn past the top of the panel. It is
    the worst case for the padding because the bar sits as close to the gridline as
    it can without passing it, leaving the label the least room, and it is exactly
    the arrangement a fractional allowance is most likely to get wrong. Pinned with
    the savings driven to that value rather than left to whichever fixture happens to
    produce it, so the case cannot quietly stop being tested when a number moves.

    **Both halves of the geometry are needed.** The awkward value alone does not
    reproduce it: on a full-year card, whose panels are the tallest the layout
    produces, the old fractional padding covered the label with a few pixels to
    spare. The seasonality band on a partial-year card takes a slice of the chart
    height, and it is the shorter panel that turns those few pixels negative — so
    this fixture carries the 60-day period as well as the awkward maximum.
    """
    # **The awkward maximum is searched for, not hardcoded.** It has been pinned to a
    # literal twice and invalidated twice by changes to the locator — 303 against a
    # 300 tick, which the integer locator stepped past at 280; then 202 against 200,
    # which the tick cap stepped past at 160. Each time the constant survived, the
    # geometry it was chosen to produce quietly did not, and the test went on passing
    # against a bar sitting comfortably mid-span. A fixture that names the *shape* it
    # needs and finds a value producing it cannot be invalidated that way: when the
    # locator moves, the search moves with it.
    tallest, figure = _bar_just_above_a_gridline()
    savings_axes = figure.axes[0]

    ticks = [t for t in savings_axes.get_yticks() if t <= tallest]
    # Just above, and only just: the bar must clear its gridline by a small
    # fraction of the span, which is what leaves the label the least room.
    assert tallest - max(ticks) < 0.05 * tallest, "the bar must sit just above a tick"

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


def test_a_sweep_with_no_battery_states_that_nothing_was_analysed() -> None:
    """A run that analysed no capacity must not report a verdict about capacities.

    **The defect this exists to catch shipped.** `baseline_only` — a sweep containing
    only capacity 0 — rendered the headline "No battery was worth it here" over the
    subtitle "No capacity in this sweep saved money against the current tariff". Both
    are claims about batteries, on a run that simulated none. The subtitle was
    byte-identical to the `no_positive_savings` card's, which is the mechanism: with
    no capacities there are no *positive* savings either, so the baseline-only sweep
    fell through into the no-positive-savings branch and inherited its finding.

    This is the same trap as a superlative over a single data point, which this card
    already guards: an absence of evidence printed as evidence of absence, in the
    largest text on the artifact that travels furthest. The card is allowed to be
    nearly empty here — that is the honest rendering of a degenerate input — but not
    to answer a question it never asked.
    """
    result = build(capacities=[0])
    assert not [s for s in result.scenarios if s.capacity_kwh > 0], (
        "the fixture must contain no battery, or this tests the wrong shape"
    )

    headline = headline_for(result.scenarios)
    text = card_text(build_summary_card(result, tariff=FLAT_TARIFF))

    # It must say that nothing was analysed, rather than that nothing was worth it.
    assert "analysed" in headline
    assert "worth it" not in headline
    assert "paid off" not in headline

    # And the sentence under it must not be the no-positive-savings finding, which
    # is the specific false claim that shipped.
    assert "No capacity in this sweep saved money" not in text
    for verdict in ("saved money against", "was worth it", "paid off here"):
        assert verdict not in text, f"a baseline-only sweep must not claim {verdict!r}"


def test_the_no_knee_headline_does_not_repeat_the_stat_below_it() -> None:
    """With a cost absent and no knee, the headline must not restate the savings.

    **The defect this exists to catch shipped.** `no_cost_no_knee` drew "Up to 462
    EUR/year in savings" directly above a stat row reading "462 EUR / saved per year
    at 20 kWh" — the same figure twice, two centimetres apart. Headline space is the
    scarcest thing on the card, and spending it on a number the element below already
    carries is the no-repeat rule this card enforces everywhere else; it is the rule
    the single-capacity headline was written to obey, so breaking it here would
    undercut that decision.

    "Up to" was independently wrong: it promises a range, and this card draws one bar.
    """
    result = build(capacities=[0, 20], cost_per_kwh=None)
    headline = headline_for(result.scenarios)
    figure = build_summary_card(result, tariff=FLAT_TARIFF)

    savings = max(s.annual_savings_eur for s in result.scenarios if s.capacity_kwh > 0)
    assert savings > 0, "the fixture must have savings to repeat, or this is vacuous"

    # The figure appears exactly where it belongs — the stat row — and not above it.
    assert f"{savings:,.0f}" in card_text(figure)
    assert f"{savings:,.0f}" not in headline
    # And no range is implied over a sweep the reader sees one bar of.
    assert "Up to" not in headline


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

    **Compared against the top of each chart, not the raw panel height.** The panel
    starts wherever the bands above it end, and those legitimately differ: a headline
    that wraps to two lines pushes everything below it down, which is the layout
    working rather than failing. Comparing raw heights conflated "the drop paths
    share one rule" with "their headlines are the same length" — so when the
    beyond-lifetime headline grew a second line, the rule was still being obeyed and
    the test failed anyway. What the rule actually claims is that every drop path
    takes the chart down to the *same floor*, and that is what is asserted.
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

    # The floor each panel reaches down to. The paths that draw a replacement
    # sentence reserve a band below the panel for it, so they stop one band higher;
    # every path within a group must agree closely.
    floors = {name: f.axes[0].get_position().y0 for name, f in drops.items()}
    silent = [floors["no cost"], floors["no positive savings"]]
    assert abs(silent[0] - silent[1]) < 0.005, (
        f"the silent drop paths must share one floor, got {floors}"
    )
    # The sentence path sits above the others by the band it reserves, and by no
    # more: that band is two lines of text, and anything beyond it is the empty
    # strip this test exists to catch.
    two_lines = 0.075
    assert 0 < floors["beyond lifetime"] - silent[0] <= two_lines, (
        f"the sentence path must clear only its own band, got {floors}"
    )

    # And each must reclaim the dropped panel: roughly the two-panel height twice
    # over, less the gap that separated them. Anchored to what a reclaiming panel
    # actually reaches, because a layout that shrank every drop path uniformly would
    # still clear the two-panel figure while leaving the card empty.
    #
    # The allowance covers both bands a drop path may legitimately give up: the
    # statement band, and a second headline line when the verdict wraps. Neither is
    # empty surface — they are occupied by text — which is what separates them from
    # the defect, and the floor assertions above are what pin that distinction.
    shared = two_panel.axes[0].get_position().height
    allowance = two_lines + _HEADLINE_LINE
    for name, height in heights.items():
        assert height >= 2 * shared - allowance, (
            f"{name}: dropping the payback panel must reclaim its height "
            f"({height:.4f} against {shared:.4f} per panel when both are drawn)"
        )


def panel_floor_row(path: Path) -> int:
    """The PNG row where the savings panel's bottom rule is drawn.

    Reads the written image rather than the figure. The quantity is the **floor of
    the plotting area** — the horizontal rule the bars stand on and the x-axis labels
    hang below — because that is what the drop-path rule actually fixes in place.

    Deliberately *not* the lowest bar pixel, and not the zero line. Both of those
    move with the data rather than with the layout: a card whose capacities lose
    money draws bars below zero, so its lowest ink sits lower, and its zero line
    sits far higher because most of the axis is negative. Measuring either one
    compares three different quantities and calls the difference a defect.

    Found as the widest near-full-width horizontal rule in the lower half of the
    card, excluding the footer rule below it.
    """
    with Image.open(path) as image:
        pixels = np.asarray(image.convert("RGB"), dtype=int)
    height, width = pixels.shape[0], pixels.shape[1]
    surface = np.array([252, 252, 251])
    ink = np.abs(pixels - surface).sum(axis=2) > 20
    # The panel's bottom spine spans the plot area, which is far wider than any text
    # and narrower than the full card. The footer rule is excluded by stopping above
    # it; `_draw_footer` puts it at figure y=0.106, i.e. row 1200*(1-0.106).
    footer_row = int(height * (1 - 0.106))
    counts = ink[:footer_row].sum(axis=1)
    candidates = np.nonzero(counts > width * 0.6)[0]
    return int(candidates.max()) if candidates.size else 0


def test_every_drop_path_puts_the_savings_baseline_on_the_same_pixel_row(
    tmp_path: Path,
) -> None:
    """The drop paths agree in the written image, not merely in their axes objects.

    **Measured on the rendered PNG on purpose.** The neighbouring test asserts on
    `get_position()`, which is the layout's own vocabulary — and a test expressed in
    the same terms as the thing it checks cannot detect a defect in those terms. If
    the geometry were ever computed correctly and drawn wrongly, every
    `get_position()` assertion in this file would pass while the card shipped broken.

    This is also the check that would have caught a *stale* card. The OPSD renders
    were measured 54px away from the sample cases on exactly this quantity, which
    looked like a layout divergence between fixtures; rebuilding both from source put
    their baselines on the same row, because the OPSD PNGs on disk simply predated the
    layout fix and had never been regenerated. Nothing about the axes could show that
    — only the pixels could.
    """
    rows: dict[str, int] = {}
    for name, result, tariff in [
        ("no cost", build(cost_per_kwh=None), FLAT_TARIFF),
        ("no positive savings", build(tariff=LOSING_TARIFF), LOSING_TARIFF),
        ("beyond lifetime", beyond_lifetime(), FLAT_TARIFF),
    ]:
        path = tmp_path / f"{name.replace(' ', '_')}.png"
        render_summary_card(result, path, tariff=tariff)
        rows[name] = panel_floor_row(path)

    assert all(row > 0 for row in rows.values()), f"every card must draw a panel, got {rows}"

    # The paths that draw a replacement sentence stop one reserved band higher, so
    # they are compared as a group against the paths that draw none, exactly as the
    # `get_position()` test does — the difference being that these numbers come out
    # of the file rather than out of the layout objects that produced it.
    silent = [rows["no cost"], rows["no positive savings"]]
    assert abs(silent[0] - silent[1]) <= 1, (
        f"the silent drop paths must share one panel floor, got {rows}"
    )
    # A band of two lines of text on a 1200px card, and no more: the defect this
    # guards left ~15% of the card (180px) empty above the footer.
    gap = silent[0] - rows["beyond lifetime"]
    assert 0 < gap <= 0.075 * CARD_PX, f"the sentence path must clear only its own band, got {rows}"


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
    # `get_tightbbox` is typed as optional — it returns None for an axes with
    # nothing drawn in it. This one has bars and labels, so the narrowing is a
    # statement about the fixture rather than a guard against a real case.
    tightbbox = axes.get_tightbbox(renderer)
    assert tightbbox is not None, "the savings panel must have drawn something"
    furniture = tightbbox.transformed(figure.transFigure.inverted())
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
