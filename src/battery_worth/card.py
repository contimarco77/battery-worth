"""Shareable PNG summary card.

This is the project's viral vehicle, and that fixes the design constraints far
more tightly than "make a chart" would. The card is seen **out of context**, in a
feed, on a phone, by someone who did not read the report and will not open it. It
has about three seconds.

Three consequences run through everything below.

**The headline is the capacity, not the payback** — but only ever as a claim the
numbers underneath can defend. "5 kWh pays back fastest" is actionable and often
contradicts the quote in the reader's inbox; "14.2 years" alone is just
discouraging and gets scrolled past. What it must *not* do is upgrade an
investment finding into a sufficiency one: 5 kWh paying back fastest says nothing
about 5 kWh being *enough*, and on the fixture it plainly is not (59%
self-consumption against 98% at 20 kWh). `headline_for` carries one sentence per
case, and where the data supports no recommendation — no battery cost, so no
payback, so nothing but "biggest saves most" — it recommends nothing and reports
the saturation instead. Savings and payback sit under it as a subordinate pair:
they make the headline checkable, not interesting.

**The chart carries the whole argument of the tool in one glance:** the biggest
battery saves the most money and is the worst investment. Those are two measures
on two scales, and putting them on one plot with two y-axes would be the single
worst thing this file could do — a dual axis invents a correlation by arbitrary
scale alignment, which is exactly the kind of dishonest number this project
exists not to print. They are drawn as two stacked panels sharing one capacity
axis instead: the crossing story reads off the *shapes* (savings rising and
flattening, payback rising away from it), with no implied ratio between them.

**Nothing here is recomputed.** Every figure comes off `AnalysisResult`, through
the same `annualization_years` the report uses. A card that disagreed with the
report it ships beside would discredit both, and the arithmetic living in two
places is how that happens.

Honesty constraints, all non-negotiable and all tested:

- A seasonality warning appears **on the card**, not only in the report. A card
  built from three months of data says so, in the reader's first pass, because
  the card is the artifact that travels and the report is the one that does not.
- Numbers are never rounded in a flattering direction. Payback 14.2 stays 14.2.
- The tariff is always printed. Savings figures are meaningless without the
  import and export prices that produced them, and a card without them is a
  number with no units.

Font policy: **DejaVu Sans only** — the family matplotlib bundles in its own
wheel. Anything else (Helvetica, Arial, a system UI sans) resolves on the
author's machine and silently falls back on a stranger's, so the card that gets
posted is not the card that was designed. Hierarchy is built from size and
weight, which are portable, rather than from typeface variety, which is not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from matplotlib import rc_context
from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.container import BarContainer
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter

from battery_worth import PROJECT_NAME, REPO_DISPLAY_URL
from battery_worth.analysis import recommended_scenario
from battery_worth.models import BATTERY_LIFETIME_YEARS, HIGH_SELF_CONSUMPTION
from battery_worth.report import describe_tariff

if TYPE_CHECKING:
    from pathlib import Path

    from battery_worth.models import AnalysisResult, ScenarioResult, Tariff

    RcParamsLike = dict[str, object]

# --- Canvas -----------------------------------------------------------------
# 1200x1200: square posts survive both Reddit's feed crop and a phone timeline,
# and a square is the one aspect ratio no platform re-crops into something else.
CARD_PX = 1200
_DPI = 100.0
_FIG_INCHES = CARD_PX / _DPI

# --- Palette ----------------------------------------------------------------
# Deliberate and small: two data hues, three inks, two greys. Validated for
# colour-vision deficiency against the card surface (worst adjacent pair
# ΔE 24.7 protan / 33.6 normal vision, OKLab x100) rather than eyeballed.
# Light-only on purpose — a PNG has no theme to follow, and a card that renders
# dark in a light feed reads as a screenshot of something else.
_SURFACE = "#fcfcfb"
_INK = "#0b0b0b"
_INK_SECONDARY = "#52514e"
_INK_MUTED = "#898781"
_GRID = "#e1e0d9"
_RULE = "#c3c2b7"
_SAVINGS = "#2a78d6"  # blue: the money series
_PAYBACK = "#eb6834"  # orange: the cost-of-getting-it-back series
# Money lost, in the savings panel only. Desaturated on purpose: it has to read as
# the *negative* of the savings hue at a glance without becoming the loudest thing
# on a card whose headline is already saying the battery lost money. A saturated
# red would out-shout the verdict it is illustrating.
_LOSS = "#c0503f"
_WARNING_BG = "#fdf1e7"
_WARNING_INK = "#8a3d12"

_FONT = "DejaVu Sans"

# The font is set through the rc, not only per artist, and that is a correctness
# fix rather than a tidy-up. Tick labels are *regenerated* whenever the locator
# reruns — which `set_xticklabels`, `set_ylim` and `axhline` all trigger — so a
# family stamped on the artists that existed at styling time is silently lost by
# the ones actually drawn. Those fall back to the "sans-serif" alias, which
# resolves to DejaVu on a machine that has nothing else and to something else on
# a stranger's: precisely the divergence the font policy exists to prevent.
# `rc_context` scopes it to one render, leaving the importing process's rcParams
# untouched — a library that mutates global matplotlib state changes the look of
# its caller's unrelated plots.
_RC: RcParamsLike = {
    "font.family": _FONT,
    "font.sans-serif": [_FONT],
    # Matplotlib's default minus sign is U+2212, which DejaVu has; pinning ASCII
    # keeps the negative-savings labels identical to the "-598 EUR" the report and
    # the terminal print for the same figure.
    "axes.unicode_minus": False,
}

# --- Layout ------------------------------------------------------------------
# Figure coordinates (0-1). Kept as named constants because the card is tuned as
# a whole: moving one band without the others is what produces overlapping text.
_MARGIN = 0.075
_WIDTH = 1.0 - 2 * _MARGIN
# The plot area is inset further from the left than the text bands are: the y tick
# labels and the axis title are drawn outside the axes box, and a four-digit
# figure (a four-figure annual loss, on the tariffs where a battery loses money)
# needs the room or the "EUR / year" label runs off the card.
_PLOT_LEFT = 0.115
_PLOT_WIDTH = 1.0 - _PLOT_LEFT - _MARGIN

# Text sizes in points at 100 dpi. The headline is sized so it survives the
# thumbnail test: at 400x400 (a third scale) its smallest permitted step is still
# comfortably legible, which is the only size test that matters in a feed.
_SIZE_HEADLINE_MAX = 54
_SIZE_HEADLINE_MIN = 30
_SIZE_KICKER = 17
_SIZE_STAT_VALUE = 32
_SIZE_STAT_LABEL = 14
_SIZE_CHART_TITLE = 16
_SIZE_AXIS = 13
_SIZE_WARNING = 14
_SIZE_FOOTER = 13
_SIZE_BRAND = 15

# The payback horizon is the domain's, imported rather than redefined here. It was
# a private constant in this module, which is how the defect it now guards against
# happened: it existed only to decide whether bars were the right encoding (see
# `_draw_no_payback_statement`), so the panel dropped at 20 years while
# `headline_for` asked the weaker question — does any positive saving exist — and
# answered "5 kWh pays back fastest" directly above a sentence saying nothing pays
# back. One card, two definitions of "pays back", the larger text carrying the
# wrong one. Every element that decides whether a payback exists — here and in the
# report — now goes through `pays_back_within_lifetime`.
_BATTERY_LIFETIME_YEARS = BATTERY_LIFETIME_YEARS

# Within the horizon, a single outlier can still crowd the panel. This caps the
# axis so the shorter bars stay comparable, with the true value labelled.
_PAYBACK_AXIS_CAP_YEARS = 40.0
# Only clip when something is actually off the scale by a margin; clipping a
# 41-year bar to 40 would misrepresent it for no legibility gain.
_PAYBACK_CLIP_TRIGGER = 1.25

# A capacity counts as the saturation knee once it reaches this share of the best
# savings in the sweep: past it, more kWh buy a rounding error.
_SATURATION_FRACTION = 0.90
# Below three points there is no curve to find a knee on — two points are a line.
_MIN_POINTS_FOR_KNEE = 3

# Space between consecutive bands of the card (stats -> warning -> chart).
_BAND_GAP = 0.030
# A panel's title is drawn *above* its axes box, so the chart has to reserve room
# for it rather than starting at the cursor it was handed. Owned by the chart and
# not by whatever precedes it, because what precedes it varies: with a seasonality
# warning present the stats no longer sit directly above the first panel, and a
# gap sized by the stats would be consumed by the warning band.
_PANEL_TITLE_SPACE = 0.048
# Below this much vertical room the chart is dropped rather than squeezed: a panel
# thinner than its own axis labels is worse than the white space it replaces.
_MIN_CHART_HEIGHT = 0.10
# Vertical band reserved for the sentence that replaces the payback panel: its
# own heading, the sentence, and air around both.
_STATEMENT_BAND = 0.115
# The panel is laid out for at least this many capacity slots, so a one- or
# two-capacity sweep produces narrow bars in a wide panel rather than slabs.
_MIN_SLOTS = 4

# Fractions of the data span added beyond the extreme bars. `_LABEL_HEADROOM` is
# the *floor* under the measured allowance computed in `_label_headroom_px`, not
# the allowance itself: it keeps a panel from hugging its tallest bar when the
# labels happen to be short. `_EDGE_MARGIN` is the bare clearance that keeps a bar
# from ending on the frame, where it reads as clipped rather than as finished.
_LABEL_HEADROOM = 0.15
_EDGE_MARGIN = 0.04

# The gap between a bar's top edge and its label, in points, and the clearance
# left between the label and the axis edge beyond it. Both are screen quantities
# because that is what the reader sees: a gap specified as a fraction of the data
# span is a different number of pixels on every panel.
_LABEL_GAP_PT = 7.0
_LABEL_CLEARANCE_PT = 4.0

_BRAND = PROJECT_NAME
_REPO_URL = REPO_DISPLAY_URL


def render_summary_card(
    result: AnalysisResult,
    path: Path,
    tariff: Tariff | None = None,
    repo_url: str = _REPO_URL,
) -> None:
    """Render the shareable summary card to `path` as a 1200x1200 PNG.

    `tariff` is optional only so the card can be rendered from a bare
    `AnalysisResult` in tests and in future callers that do not hold one; when it
    is supplied — which is always, on the CLI path — the import and export prices
    are printed in the footer. They are not decoration: a savings figure without
    the prices that produced it cannot be checked by the reader, and this whole
    tool sells checkability.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Both the build and the save happen inside the rc context: `savefig` is what
    # actually resolves fonts, so writing outside it would undo the whole point of
    # setting them.
    # matplotlib's stubs key the rc mapping on a Literal of every parameter name;
    # the runtime accepts any dict, and a module-level constant widens to `str`.
    with rc_context(_RC):  # type: ignore[arg-type]
        figure = _build(result, tariff, repo_url)
        figure.savefig(path, dpi=_DPI, facecolor=_SURFACE)


def build_summary_card(
    result: AnalysisResult,
    tariff: Tariff | None = None,
    repo_url: str = _REPO_URL,
) -> Figure:
    """Build the card figure without writing it, so tests can inspect its content."""
    # matplotlib's stubs key the rc mapping on a Literal of every parameter name;
    # the runtime accepts any dict, and a module-level constant widens to `str`.
    with rc_context(_RC):  # type: ignore[arg-type]
        return _build(result, tariff, repo_url)


def _build(result: AnalysisResult, tariff: Tariff | None, repo_url: str) -> Figure:
    figure = Figure(figsize=(_FIG_INCHES, _FIG_INCHES), dpi=_DPI, facecolor=_SURFACE)
    # An explicit Agg canvas, rather than going through pyplot: the figure is
    # never shown, and pyplot's global registry would hold every card a
    # long-running caller renders. It also gives `_fit_headline_size` a renderer
    # to measure text against, which a bare Figure does not have.
    FigureCanvasAgg(figure)

    best = recommended_scenario(result.scenarios)
    battery_scenarios = [s for s in result.scenarios if s.capacity_kwh > 0]

    cursor = 1.0 - _MARGIN
    cursor = _draw_headline(figure, result.scenarios, cursor)
    cursor = _draw_stats(figure, best, battery_scenarios, cursor)
    cursor = _draw_warning(figure, result, cursor)
    _draw_chart(figure, battery_scenarios, cursor)
    _draw_footer(figure, result, tariff, repo_url)
    return figure


# --- Headline ----------------------------------------------------------------


def _within_lifetime(scenarios: list[ScenarioResult]) -> list[ScenarioResult]:
    """The scenarios that pay back inside the battery's working life.

    The single definition of "pays back" for the whole card. Every element that
    branches on whether a payback exists asks this — the headline, the emphasis and
    the panel-drop — because the defect this replaces was two of them asking
    different questions and printing contradictory answers one above the other.

    A payback of 76.5 years is not a slow payback. It is a payback the hardware is
    not expected to live to deliver, which makes it arithmetic rather than a
    finding, and no element of the card may treat it as one.
    """
    return [s for s in scenarios if s.pays_back_within_lifetime()]


def headline_for(scenarios: list[ScenarioResult]) -> str:
    """The verdict sentence. Every word of it must be defensible from the card.

    The rule this function exists to enforce: **say what the number means, not the
    strongest thing it could be made to imply.** An earlier version read
    "5 kWh is enough for this house" in every case, which was three separate
    overclaims:

    - *Sufficiency it had not measured.* On the fixture, 5 kWh gives 59%
      self-consumption and 20 kWh gives 98%. 5 kWh is the best **investment**, not
      "enough" — those are different claims and the card was making the stronger
      one, against its own chart.
    - *A recommendation with no basis.* With no battery cost there is no payback,
      so `recommended_scenario` falls back to the largest absolute savings — which
      recommends the biggest battery, precisely the trap this tool exists to
      expose. The honest answer is to recommend no size at all and report the
      saturation the chart already shows.
    - *A superlative over a single data point.* One capacity in the sweep makes
      "best" meaningless; there is nothing for it to be best against.

    Each case therefore gets its own sentence, and none of them claims more than
    the panels underneath can support.
    """
    batteries = [s for s in scenarios if s.capacity_kwh > 0]
    if not batteries:
        return "No battery was worth it here"

    earning = [s for s in batteries if s.annual_savings_eur > 0]
    if not earning:
        return "No battery paid off here"

    priced = [s for s in batteries if s.payback_years() is not None]
    with_payback = _within_lifetime(batteries)

    # Priced, earning, and still nothing pays back inside the battery's life. The
    # panel below already says so and names the shortest figure, so the headline
    # must not repeat it: the no-repeat rule that makes the single-capacity case
    # say "the only size analysed" applies with more force here, because the
    # sentence underneath is the element carrying the number.
    #
    # What the headline can say that the sentence cannot is *why*. A house already
    # self-consuming 80% of its PV has almost nothing left for a battery to
    # capture, so the binding constraint is the roof and the load rather than the
    # capacity — a different fact from "no payback", not derivable from anything
    # else on the card (self-consumption appears nowhere else on it), and the more
    # useful half of the answer to a reader deciding what to do next.
    if priced and not with_payback:
        return _no_payback_reason(priced)

    # No cost supplied: no payback exists, so no size can be recommended. What the
    # data *does* show is where extra capacity stops buying anything, which is the
    # useful half of the answer and is visible in the panel below.
    if not with_payback:
        return _saturation_headline(earning)

    # A single capacity supports no superlative — there is nothing to be best
    # against — and the headline says exactly that, which is the one thing about
    # this card the stats row cannot.
    #
    # It used to read "10 kWh pays back in 16.5 years", which spent the card's
    # largest text on a number printed again, verbatim, two centimetres below it as
    # "16.5 years / to pay back". Headline space is the scarcest resource here: the
    # reader gives the card three seconds and the top line gets most of them, so a
    # repeat costs the only slot available for something the reader would not
    # otherwise learn. What they would not otherwise learn is that there is no
    # comparison behind this number — one size was analysed, so nothing on the card
    # says whether a different one would have done better. That caveat is invisible
    # in a stat row and changes how every figure below it should be read.
    #
    # Counted over the capacities *analysed*, not the ones that clear the lifetime
    # threshold. Those were the same list until the threshold arrived, and keying
    # this off the filtered one made the card claim a sweep that did not happen:
    # OPSD residential4 analysed 5/10/15 kWh, of which only 5 kWh pays back inside
    # 20 years, and the headline read "5 kWh — the only size analysed" above a
    # chart plainly showing three. The two comparisons this branch is about — was
    # there a sweep, and does anything pay back — are different questions.
    if len(priced) == 1:
        only = priced[0]
        return f"{_capacity_label(only.capacity_kwh)} — the only size analysed"

    fastest = min(with_payback, key=lambda s: s.payback_years() or 0.0)
    return f"{_capacity_label(fastest.capacity_kwh)} pays back fastest"


def _no_payback_reason(priced: list[ScenarioResult]) -> str:
    """Why this house cannot pay a battery back, when none of them can.

    Two shapes, because the reason genuinely differs and asserting the wrong one
    would be a fabricated finding in the card's largest text.

    **Already self-consuming most of its PV.** There is barely any surplus left for
    a battery to capture, so the ceiling is the roof and the load. This is the
    project's own third headline finding, and stating it is more useful than
    repeating the negative the sentence below already carries.

    **Not self-consuming much, and still no payback.** Then the surplus exists and
    the battery does capture it — the savings are simply too small against the cost
    for the spread on this tariff. Claiming saturation here would be false, so the
    headline names the horizon instead. The sentence below still supplies the
    shortest figure, so this is not the repeat the first branch avoids: it says the
    battery outlives its own return, which is the meaning of the threshold rather
    than the number that tripped it.
    """
    baseline = max(s.self_consumption_before for s in priced)
    if baseline >= HIGH_SELF_CONSUMPTION:
        return f"Already using {baseline:.0%} of its own solar"
    return "No size pays back before the battery wears out"


def _saturation_headline(earning: list[ScenarioResult]) -> str:
    """What to say when savings exist but no payback can rank them.

    Reached when no battery cost was supplied. `recommended_scenario` would fall
    back to the largest absolute savings, which recommends the biggest battery —
    the exact trap this tool exists to expose — so the headline names no size and
    reports where the curve flattens instead. With too few points to find a knee,
    it falls back again to the plain best figure.
    """
    knee = _saturation_knee(earning)
    if knee is not None:
        return f"Savings flatten beyond {_capacity_label(knee)}"
    return f"Up to {_earning_max(earning):,.0f} EUR/year in savings"


def _earning_max(earning: list[ScenarioResult]) -> float:
    return max(s.annual_savings_eur for s in earning)


def _saturation_knee(earning: list[ScenarioResult]) -> float | None:
    """The capacity past which extra kWh stop buying meaningful savings.

    Defined as the smallest capacity already within `_SATURATION_FRACTION` of the
    best savings in the sweep — i.e. the point where the curve has flattened. It is
    only claimed when a *larger* capacity was actually simulated, because "savings
    flatten beyond X" is a statement about what lies past X: asserting it from the
    last point in the sweep would be extrapolating off the end of the data, which
    is the same overclaim in a different costume.
    """
    if len(earning) < _MIN_POINTS_FOR_KNEE:
        return None
    ranked = sorted(earning, key=lambda s: s.capacity_kwh)
    ceiling = max(s.annual_savings_eur for s in ranked)
    if ceiling <= 0:
        return None
    for scenario in ranked[:-1]:
        if scenario.annual_savings_eur >= ceiling * _SATURATION_FRACTION:
            return scenario.capacity_kwh
    return None


def saturation_stat(scenarios: list[ScenarioResult]) -> tuple[str, str] | None:
    """The stat that backs "savings flatten beyond X" — the marginal gain past X.

    Exists because the headline and the figure under it were arguing with each
    other. With no cost supplied the headline says savings flatten beyond 15 kWh
    and the stat row printed 462 EUR: the 20 kWh figure, i.e. the *largest* value
    in the sweep, sitting directly beneath a sentence implicitly advising against
    buying that size. Whichever of the two the reader believed, the card had told
    them the other.

    The marginal gain is the stronger repair of the two available. The flattening
    point's own savings (442 EUR) would at least be consistent, but it is only
    another absolute figure and leaves the reader to find the difference that makes
    the headline true. "+20 EUR/year" *is* the flattening — it states the size of
    what the extra 5 kWh buys, which is the finding, and it is a number no other
    part of the card carries.

    Returns `(value, label)` for the stat row, or `None` when there is no knee to
    describe — in which case the caller keeps the plain savings figure.
    """
    earning = [s for s in scenarios if s.annual_savings_eur > 0]
    knee = _saturation_knee(earning)
    if knee is None:
        return None
    ranked = sorted(earning, key=lambda s: s.capacity_kwh)
    at_knee = next(s for s in ranked if s.capacity_kwh == knee)
    largest = ranked[-1]
    gain = largest.annual_savings_eur - at_knee.annual_savings_eur
    span = f"from {_capacity_label(knee)} to {_capacity_label(largest.capacity_kwh)}"
    # A gain that rounds to nothing is the strongest possible version of the
    # headline, and "+0 EUR" is the weakest possible way to print it: a zero in the
    # card's second-largest text reads as a figure that failed to compute, not as a
    # finding. Saturation gets said in words when the number has nothing left to say.
    if round(gain) == 0:
        return ("Nothing", f"more saved per year {span}")
    return (f"+{gain:,.0f} EUR", f"per year {span}")


def _draw_headline(figure: Figure, scenarios: list[ScenarioResult], top: float) -> float:
    """Draw the verdict, as the largest thing on the card."""
    kicker = "Would a home battery have paid off?"
    headline = headline_for(scenarios)

    figure.text(
        _MARGIN,
        top,
        kicker,
        fontfamily=_FONT,
        fontsize=_SIZE_KICKER,
        color=_INK_MUTED,
        va="top",
        ha="left",
    )
    size = _fit_headline_size(figure, headline)
    figure.text(
        _MARGIN,
        top - 0.042,
        headline,
        fontfamily=_FONT,
        fontsize=size,
        fontweight="bold",
        color=_INK,
        va="top",
        ha="left",
    )
    return top - 0.042 - _headline_height(size)


def _fit_headline_size(figure: Figure, headline: str) -> int:
    """Largest headline size at which the text actually fits the card's width.

    **Measured, not estimated from the character count.** A character budget is a
    guess about average glyph width, and it guesses wrong on exactly the strings
    that matter: "12.5 kWh is enough for this house" is only three characters
    longer than the 5 kWh version but far wider in a bold face at 54pt. Getting
    it wrong clips the single most important word on the card, and a clipped
    headline is worse than a small one. So the renderer asks matplotlib for the
    rendered width and steps down until it fits.
    """
    canvas = figure.canvas
    assert isinstance(canvas, FigureCanvasAgg)  # attached in `build_summary_card`
    # matplotlib ships no stubs, so `get_renderer` is untyped to mypy strict.
    renderer = canvas.get_renderer()  # type: ignore[no-untyped-call]
    limit = _WIDTH * CARD_PX
    for size in range(_SIZE_HEADLINE_MAX, _SIZE_HEADLINE_MIN - 1, -2):
        probe = figure.text(0, 0, headline, fontfamily=_FONT, fontsize=size, fontweight="bold")
        width = probe.get_window_extent(renderer=renderer).width
        probe.remove()
        if width <= limit:
            return size
    return _SIZE_HEADLINE_MIN


def _headline_height(size: int) -> float:
    """Vertical space one headline line occupies, in figure fraction.

    Derived from the point size rather than hardcoded, so the fitted size step
    does not leave a hole under a short headline or a collision under a long one.
    """
    return (size * 1.5) / CARD_PX


def _capacity_label(capacity_kwh: float) -> str:
    """A nameplate capacity: '5 kWh', never '5.0 kWh'.

    Matches the report's `cap` filter exactly. The user typed this number on the
    command line and must see it echoed back unchanged.
    """
    return f"{capacity_kwh:g} kWh"


# --- The subordinate pair ----------------------------------------------------


def _draw_stats(
    figure: Figure,
    best: ScenarioResult | None,
    scenarios: list[ScenarioResult],
    top: float,
) -> float:
    """Savings per year and payback, as a pair, under the headline.

    Second in the hierarchy on purpose: they qualify the verdict rather than
    being it. Payback renders as "never" when there are no positive savings and
    is omitted entirely when no battery cost was supplied — an absent input must
    read as absent, never as a zero or a blank the reader can misinterpret.

    **The first stat must support the headline, never undercut it.** This row sits
    two centimetres below the largest text on the card, so the reader takes the two
    as one statement; a figure that argues with the sentence above it does more
    damage than no figure at all. That is why the whole scenario list is passed in
    and not only `best`: in the no-cost case the headline is about *flattening*,
    which is a property of the curve, and no single scenario can express it.
    """
    if best is None:
        figure.text(
            _MARGIN,
            top - 0.01,
            "No capacity in this sweep saved money against the current tariff.",
            fontfamily=_FONT,
            fontsize=_SIZE_STAT_LABEL + 3,
            color=_INK_SECONDARY,
            va="top",
            ha="left",
        )
        return top - 0.075

    payback = best.payback_years()
    # Without a cost the headline recommends no size and reports the saturation
    # instead, so the lead stat follows it there: the marginal gain past the knee,
    # which is what "flatten" means in numbers. Where there is no knee to describe,
    # the headline is back to a plain savings figure and so is this — labelled with
    # the capacity that achieved it, rather than letting a bare number imply a size
    # the card declined to name.
    saturation = None if best.battery_cost_eur is not None else saturation_stat(scenarios)
    if saturation is not None:
        stats: list[tuple[str, str]] = [saturation]
    else:
        savings_label = (
            "saved per year"
            if best.battery_cost_eur is not None
            else f"saved per year at {_capacity_label(best.capacity_kwh)}"
        )
        stats = [(f"{best.annual_savings_eur:,.0f} EUR", savings_label)]
    if best.battery_cost_eur is not None:
        # The label carries the verdict, because the number alone cannot. "42.1
        # years / to pay back" states as a payback the very figure the panel below
        # is declaring not to be one — the same contradiction as the headline, one
        # element further down, and found by rendering the case rather than by
        # reading the code. The figure itself stays: it is real, the reader wants
        # it, and suppressing it would invite the suspicion that nothing was
        # computed. Only the claim attached to it changes.
        # Kept inside its column. The stat labels sit on a 0.30 fixed grid, so a
        # label long enough to spell the reasoning runs straight through the next
        # column's — which is how the first attempt at this rendered, printing
        # "past the battery's 20-year life" over "battery cost". The short form
        # carries the whole claim: "never pays back" is the verdict, and the
        # sentence below the savings panel supplies the horizon it is measured
        # against.
        payback_label = "to pay back" if best.pays_back_within_lifetime() else "never pays back"
        stats.append((_payback_label(payback), payback_label))
        stats.append((f"{best.battery_cost_eur:,.0f} EUR", "battery cost"))

    baseline = top - 0.012
    for column, (value, label) in enumerate(stats):
        x = _MARGIN + column * 0.30
        figure.text(
            x,
            baseline,
            value,
            fontfamily=_FONT,
            fontsize=_SIZE_STAT_VALUE,
            fontweight="bold",
            color=_INK,
            va="top",
            ha="left",
        )
        figure.text(
            x,
            baseline - 0.041,
            label,
            fontfamily=_FONT,
            fontsize=_SIZE_STAT_LABEL,
            color=_INK_MUTED,
            va="top",
            ha="left",
        )
    return baseline - 0.041 - _BAND_GAP


def _payback_label(payback: float | None) -> str:
    """Payback in years, one decimal, never rounded into a friendlier number.

    14.2 stays 14.2. `None` means either no cost or no positive savings, and both
    are printed as "never" — a blank would invite the reader to assume it simply
    was not computed.
    """
    return "never" if payback is None else f"{payback:.1f} years"


# --- Seasonality warning -----------------------------------------------------


def _draw_warning(figure: Figure, result: AnalysisResult, top: float) -> float:
    """The partial-year warning, on the card itself.

    The report has room to explain seasonality in a paragraph; the card has a
    strip. It gets one, because the card is the artifact that travels without the
    report and a reader must not be able to screenshot a three-month result as if
    it were a year. Drawn as a filled band rather than a footnote so it cannot be
    skimmed past — it is the one caveat that can invalidate every other number
    above it.
    """
    if not result.seasonality_warning:
        return top

    height = 0.052
    band_top = top - 0.004
    figure.patches.extend(
        [
            Rectangle(
                (_MARGIN, band_top - height),
                _WIDTH,
                height,
                transform=figure.transFigure,
                facecolor=_WARNING_BG,
                edgecolor="none",
                zorder=0,
            )
        ]
    )
    figure.text(
        _MARGIN + 0.018,
        band_top - height / 2,
        f"Only {result.days_analyzed} days of data — not a full year. "
        "Seasonality is not captured; treat these figures as indicative.",
        fontfamily=_FONT,
        fontsize=_SIZE_WARNING,
        color=_WARNING_INK,
        va="center",
        ha="left",
    )
    return band_top - height - _BAND_GAP


# --- The chart ---------------------------------------------------------------


def _draw_chart(  # noqa: PLR0914 - a two-panel layout genuinely needs its coordinates named
    figure: Figure, scenarios: list[ScenarioResult], cursor: float
) -> None:
    """Savings and payback against capacity, as two panels on one shared x-axis.

    **Not a dual-axis plot, and the distinction is the point.** Two y-scales on
    one set of bars would let the reader infer a crossing point that is an
    artifact of how the two axes happened to be aligned — a fabricated finding in
    a tool whose only selling point is that its numbers are real. Stacked panels
    show the same tension honestly: savings rise and flatten in the upper panel
    while payback climbs in the lower one, so "more capacity, more money saved,
    worse investment" is read off two shapes rather than off one invented
    intersection.

    The recommended capacity is the only bar at full colour in each panel; the
    rest are the same hue at low opacity. Emphasis, not eight hues for one story.
    """
    # The bottom of the plotting area, not of the axes: the x tick labels and the
    # axis title are drawn *below* the axes box, so the box has to stop well clear
    # of the footer rule or the capacity labels land on top of it.
    bottom = 0.185
    top = cursor - _PANEL_TITLE_SPACE
    available = top - bottom
    if not scenarios or available <= _MIN_CHART_HEIGHT:
        return

    paybacks = [p for s in scenarios if (p := s.payback_years()) is not None]
    # Three states, not two. A bar panel is only the right encoding for the first.
    #
    # - Something pays back inside a battery's working life: draw the bars.
    # - Paybacks exist but all lie beyond that horizon: draw a *sentence*. Bars
    #   here would have to be truncated so hard that the encoding inverts — on the
    #   60-day card, 91.7 / 126.6 / 181.0 years rendered as three near-identical
    #   stubs, implying the paybacks were similar when the longest was double the
    #   shortest. A chart that misstates its own values is worse than no chart.
    # - No payback at all (no cost, or no positive savings): nothing to say that
    #   the headline has not already said, so the savings panel takes the height.
    mode = "bars" if _within_lifetime(scenarios) else ("sentence" if paybacks else "none")

    # Between the panels: the lower panel's own title, plus a breathing gap.
    gap = _PANEL_TITLE_SPACE + 0.022 if mode == "bars" else 0.0
    panels = 2 if mode == "bars" else 1
    # The replacement sentence is not free space: it needs a band of its own below
    # the savings panel, or it is drawn over the bars.
    reserved = _STATEMENT_BAND if mode == "sentence" else 0.0
    panel_height = (available - gap - reserved) / panels

    savings_axes = figure.add_axes((_PLOT_LEFT, top - panel_height, _PLOT_WIDTH, panel_height))
    _draw_savings_panel(savings_axes, scenarios, label_x=mode != "bars")

    if mode == "bars":
        payback_axes = figure.add_axes((_PLOT_LEFT, bottom, _PLOT_WIDTH, panel_height))
        _draw_payback_panel(payback_axes, scenarios)
    elif mode == "sentence":
        # The savings panel now ends `_STATEMENT_BAND` higher; the sentence fills
        # that band, starting below the panel's own x-axis labels and title.
        _draw_no_payback_statement(
            figure, scenarios, band_top=top - panel_height - _PANEL_TITLE_SPACE
        )


def _emphasized_scenario(scenarios: list[ScenarioResult]) -> ScenarioResult | None:
    """Which bar is drawn at full strength — or none, when nothing is recommended.

    Emphasis is a recommendation made in ink, so it has to obey the same rule the
    headline does. `recommended_scenario` falls back to the largest absolute
    savings when no payback exists, which would light up the biggest battery in the
    sweep directly under a headline that has just declined to recommend a size —
    the picture contradicting the sentence, and picking the louder of the two.
    With no payback anywhere, no bar is emphasized.

    "No payback" means `_within_lifetime`, not merely "the division returned a
    number". A 76.5-year payback lit a bar at full strength while the panel beside
    it said nothing pays back — the same split as the headline, in the encoding the
    reader takes in first.
    """
    if not _within_lifetime(scenarios):
        return None
    return recommended_scenario(scenarios)


def no_payback_statement(scenarios: list[ScenarioResult]) -> str | None:
    """The sentence that replaces the payback panel when nothing pays back in time.

    Names the shortest payback and the capacity that achieves it, so the reader
    gets the actual figure rather than only the verdict — "no capacity pays back"
    without a number invites the suspicion that the tool simply failed to compute
    one.
    """
    priced = [(p, s) for s in scenarios if (p := s.payback_years()) is not None]
    if not priced:
        return None
    payback, scenario = min(priced, key=lambda pair: pair[0])
    return (
        f"No capacity pays back within {_BATTERY_LIFETIME_YEARS:.0f} years — "
        f"shortest is {payback:.1f} y at {_capacity_label(scenario.capacity_kwh)}"
    )


def _draw_no_payback_statement(
    figure: Figure, scenarios: list[ScenarioResult], band_top: float
) -> None:
    """Render the replacement sentence where the payback panel would have been."""
    statement = no_payback_statement(scenarios)
    if statement is None:
        return

    # Anchored to the top of its reserved band and drawn downwards, so the heading
    # sits clear of the savings panel's x-axis labels above it.
    figure.text(
        _MARGIN,
        band_top,
        "Years to pay back",
        fontfamily=_FONT,
        fontsize=_SIZE_CHART_TITLE,
        fontweight="bold",
        color=_INK_SECONDARY,
        va="top",
        ha="left",
    )
    figure.text(
        _MARGIN,
        band_top - 0.038,
        statement,
        fontfamily=_FONT,
        fontsize=_SIZE_STAT_LABEL + 2,
        color=_INK,
        va="top",
        ha="left",
    )


def _style_panel(axes: Axes, title: str) -> None:
    """Shared chrome: hairline horizontal grid, no box, recessive axis text.

    Everything here is subtractive. The bars are the only thing allowed to carry
    weight, so the frame goes, the vertical grid goes, and the ticks go.
    """
    axes.set_facecolor(_SURFACE)
    # Aligned to the card's text margin, not to the plot area: the panel titles
    # belong to the same left edge as the headline, the stats and the footer. The
    # plot is inset further to make room for its tick labels, and letting the
    # titles follow it would break the one vertical line the eye tracks down the
    # whole card.
    axes.set_title(
        title,
        fontfamily=_FONT,
        fontsize=_SIZE_CHART_TITLE,
        fontweight="bold",
        color=_INK_SECONDARY,
        loc="left",
        pad=10,
        x=(_MARGIN - _PLOT_LEFT) / _PLOT_WIDTH,
    )
    axes.grid(axis="y", color=_GRID, linewidth=1.0, linestyle="-")
    axes.set_axisbelow(True)
    for side in ("top", "right", "left"):
        axes.spines[side].set_visible(False)
    axes.spines["bottom"].set_color(_RULE)
    axes.spines["bottom"].set_linewidth(1.0)
    axes.tick_params(axis="both", length=0, labelsize=_SIZE_AXIS, colors=_INK_MUTED, pad=6)
    # Tick label fonts come from the rc context (`_RC`), not from a loop over the
    # current artists: matplotlib regenerates them on every locator pass, so
    # anything stamped here is discarded before the figure is drawn.


def _draw_savings_panel(axes: Axes, scenarios: list[ScenarioResult], label_x: bool) -> None:
    """Upper panel: annual savings per capacity. Taller is better, and it saturates."""
    _style_panel(axes, "Savings per year")

    values = [s.annual_savings_eur for s in scenarios]
    positions = list(range(len(scenarios)))
    best = _emphasized_scenario(scenarios)

    # One hue for the whole series, emphasis by opacity: the recommended bar at
    # full strength, the rest receded. Eight categorical hues for what is a single
    # measure would be the most common way a chart misses its own point.
    #
    # The one thing colour *does* encode here is the sign, because that is not a
    # category — it is the threshold the whole card is about. Drawing a bar that
    # loses 1,254 EUR in the same blue as one that earns 462 makes the reader take
    # the direction from the axis alone, and the axis is the slowest thing on the
    # panel to read. Below zero the bars turn red; the emphasis rule is unchanged.
    bars = axes.bar(
        positions,
        values,
        width=_bar_width(len(scenarios)),
        color=[_LOSS if v < 0 else _SAVINGS for v in values],
        edgecolor="none",
    )
    # With nothing recommended there is no bar for the others to recede *behind*,
    # and the right answer differs by case rather than being one rule.
    #
    # Losing: the bars *are* the finding. Held at 0.32 the whole panel is a row of
    # ghosts, and pale red reads as tentative about a result the headline states
    # outright, so every bar goes to full strength.
    #
    # Saturating (no cost supplied): the finding is the *shape* of the curve, not
    # any one bar. Four bars at full strength shout without saying more than four
    # receded ones, and the panel stops being the quiet evidence under a headline
    # and starts competing with it. The series stays receded.
    losing = any(v < 0 for v in values)
    for bar, scenario in zip(bars, scenarios, strict=True):
        emphasized = losing if best is None else scenario.capacity_kwh == best.capacity_kwh
        bar.set_alpha(1.0 if emphasized else 0.32)

    axes.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    axes.set_ylabel(
        "EUR / year", fontfamily=_FONT, fontsize=_SIZE_AXIS, color=_INK_MUTED, labelpad=8
    )
    _set_capacity_ticks(axes, scenarios, visible=label_x)

    # **Every bar carries its number.** Labelling only the recommended one left
    # the rest mute: the reader could see that the 20 kWh bar was taller but had
    # to walk each one back to a gridline to find out by how much, which is the
    # arithmetic the card exists to have already done. The chart's whole argument
    # is the *gaps* between the capacities — +79 EUR from 10 to 15, +20 from 15 to
    # 20 — and a gap cannot be read off two bars when neither states its value.
    # Emphasis then does the job it was always meant to do: rank the labels rather
    # than be the only one, so the recommendation still reads first.
    for position, value, scenario in zip(positions, values, scenarios, strict=True):
        emphasized = best is not None and scenario.capacity_kwh == best.capacity_kwh
        below = value < 0
        # One gap, stated once, applied in whichever direction the label sits.
        offset = -_LABEL_GAP_PT if below else _LABEL_GAP_PT
        axes.annotate(
            f"{value:,.0f} EUR",
            xy=(position, value),
            xytext=(0, offset),
            textcoords="offset points",
            ha="center",
            va="top" if below else "bottom",
            fontfamily=_FONT,
            fontsize=_SIZE_AXIS + 1,
            fontweight="bold" if emphasized else "normal",
            color=_INK if emphasized else _INK_SECONDARY,
        )

    _pad_range(axes, values)
    _draw_zero_line(axes, values)


def _draw_payback_panel(axes: Axes, scenarios: list[ScenarioResult]) -> None:
    """Lower panel: payback per capacity. Shorter is better — the opposite direction.

    Two cases have no bar to draw and must not be silently skipped, because a
    missing bar reads as zero: a capacity with no positive savings, and one whose
    cost was never supplied. Both are marked "never" on the baseline instead.

    Very long paybacks are clipped to a capped axis with the true figure labelled
    above the bar. Left unclipped, one 300-year outlier compresses every other
    bar into the baseline and destroys the comparison the panel exists to make;
    clipped and labelled, the reader loses nothing — the real number is right
    there in text, and the bar is visibly running off the top.
    """
    _style_panel(axes, "Years to pay back")

    paybacks = [s.payback_years() for s in scenarios]
    finite = [p for p in paybacks if p is not None]
    cap = _payback_cap(finite)

    positions = list(range(len(scenarios)))
    # The same emphasis rule as the savings panel, through the same helper, rather
    # than `recommended_scenario` directly. This panel only draws when something
    # pays back inside the horizon, so the two agree today — but they agreed by
    # coincidence of the caller, and one bar lit under a headline recommending no
    # size is exactly the contradiction being fixed here.
    best = _emphasized_scenario(scenarios)
    drawn = [min(p, cap) if p is not None else 0.0 for p in paybacks]

    bars = axes.bar(
        positions,
        drawn,
        width=_bar_width(len(scenarios)),
        color=_PAYBACK,
        edgecolor="none",
    )
    for bar, scenario in zip(bars, scenarios, strict=True):
        emphasized = best is not None and scenario.capacity_kwh == best.capacity_kwh
        bar.set_alpha(1.0 if emphasized else 0.32)

    _mark_clipped_bars(axes, bars, paybacks, cap)

    axes.set_ylabel("years", fontfamily=_FONT, fontsize=_SIZE_AXIS, color=_INK_MUTED, labelpad=8)
    _set_capacity_ticks(axes, scenarios, visible=True)

    # Every bar states its own figure, for the same reason as the savings panel:
    # the comparison here is between paybacks, and a bar the reader has to measure
    # against a gridline is a number the card declined to print. Clipped bars had
    # to be labelled anyway — their height is a lie without the text — so this
    # makes the rule uniform instead of "labelled when the bar cannot be trusted".
    for position, payback, scenario in zip(positions, paybacks, scenarios, strict=True):
        emphasized = best is not None and scenario.capacity_kwh == best.capacity_kwh
        clipped = payback is not None and payback > cap
        if payback is None:
            text, y = "never", 0.0
        else:
            text, y = f"{payback:.1f}", min(payback, cap)
        axes.annotate(
            text,
            # Clipped bars carry a stub above the cap; the label clears it.
            xy=(position, y),
            xytext=(0, _LABEL_GAP_PT * (2 if clipped else 1)),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontfamily=_FONT,
            fontsize=_SIZE_AXIS + 1,
            fontweight="bold" if emphasized else "normal",
            color=_INK if emphasized else _INK_SECONDARY,
        )

    # Against `drawn`, not `paybacks`: the clipped heights are what the axis has to
    # contain. Padding to a 300-year true value would collapse the panel to a strip.
    #
    # A clipped bar's label sits at double the usual gap so it clears the stub, so
    # the panel has to reserve that much: padding for the ordinary offset would put
    # the tallest label back outside the axis, which is the defect this padding
    # exists to prevent, reintroduced by the one bar most likely to hit it.
    clipped_any = any(p is not None and p > cap for p in paybacks)
    _pad_range(axes, drawn, extra_offset_pt=_LABEL_GAP_PT if clipped_any else 0.0)


def _draw_zero_line(axes: Axes, values: list[float]) -> None:
    """Mark zero when any bar goes below it, and demote the spine that is not zero.

    Normally the bottom spine *is* zero and needs nothing. Once a bar goes
    negative the spine drops to the bottom of the range while still looking like
    the baseline, so the bars appear to hang from an arbitrary floor and the line
    between saving money and losing it — the only threshold this card is about —
    is not drawn at all. The zero rule is added and the spine steps back to a
    gridline weight, so the emphasis follows the meaning.
    """
    if all(v >= 0 for v in values):
        return
    # A separate artist rather than repositioning the bottom spine: moving the
    # spine drags the x tick labels and the axis title up with it, and they land
    # inside the negative bars. The spine stays at the foot of the axes carrying
    # the capacity labels; this line carries the zero.
    axes.axhline(0.0, color=_RULE, linewidth=1.0, zorder=1)
    axes.spines["bottom"].set_color(_GRID)


def _mark_clipped_bars(
    axes: Axes, bars: BarContainer, paybacks: list[float | None], cap: float
) -> None:
    """Break the top edge of any bar that runs off the axis, so it reads as clipped.

    Without this the clipped bars all stop dead flat at exactly the cap and read
    as *equal* — three 40-year batteries — which is a worse misreading than the
    crowding the cap was introduced to avoid. The numeric label alone does not fix
    it: the whole premise of the card is that the shapes are read first and the
    text second.

    The break is a row of surface-coloured notches across the bar's top edge,
    drawn in the surface colour rather than as a stroke, keeping to the same rule
    the rest of the card follows: white does the separating.
    """
    for bar, payback in zip(bars, paybacks, strict=True):
        if payback is None or payback <= cap:
            continue
        x, width = bar.get_x(), bar.get_width()
        band = cap * 0.05

        # A surface-coloured band cuts the bar, and a detached stub continues
        # above it: the bar visibly does not end where it stops.
        axes.add_patch(
            Rectangle(
                (x, cap - band * 1.6),
                width,
                band,
                facecolor=_SURFACE,
                edgecolor="none",
                zorder=3,
                clip_on=False,
            )
        )
        axes.add_patch(
            Rectangle(
                (x, cap - band * 0.6),
                width,
                band * 1.4,
                facecolor=bar.get_facecolor(),
                alpha=bar.get_alpha(),
                edgecolor="none",
                zorder=2,
                clip_on=False,
            )
        )


def _payback_cap(finite: list[float]) -> float:
    """The y-limit for the payback panel, clipping only genuine outliers.

    Returns the largest payback when everything fits comfortably, so the common
    case is never distorted; falls back to the fixed cap only when a value runs
    far enough past it that keeping it would flatten the rest of the panel.
    """
    if not finite:
        return _PAYBACK_AXIS_CAP_YEARS
    largest = max(finite)
    if largest <= _PAYBACK_AXIS_CAP_YEARS * _PAYBACK_CLIP_TRIGGER:
        return largest
    return _PAYBACK_AXIS_CAP_YEARS


def _bar_width(count: int) -> float:
    """Bar width in category units. Never fills its slot — the leftover is air.

    A bar that fills its band reads as a heavy block and makes the panel loud,
    which is wrong for a card whose data is supposed to be the only loud thing on
    it. Constant across sweep sizes because `_set_capacity_ticks` pins the x-limits
    to fixed-width slots, so one bar and six bars are drawn the same width — the
    panel gets emptier, never chunkier.
    """
    return 0.46 if count > 1 else 0.34


def _set_capacity_ticks(axes: Axes, scenarios: list[ScenarioResult], visible: bool) -> None:
    """Capacity labels on the x-axis, shown only on the bottom panel of the pair.

    Repeating identical tick labels on both panels would restate the shared axis
    and add a row of text between the two shapes the reader is comparing. The
    axis title rides with the labels for the same reason, which is why it belongs
    here rather than in whichever panel happens to be last.
    """
    # Explicit limits, so a bar occupies a *slot* rather than the whole panel.
    # Left implicit, matplotlib fits the x-axis tightly around the categories it
    # was given, which makes the one-capacity sweep draw a single bar spanning the
    # full width — a progress meter, not a comparison. With the limits pinned, the
    # width cap in `_bar_width` means what it says at every sweep size.
    # Padded symmetrically so a short sweep stays centred rather than hugging the
    # left edge: the slots widen, the bars do not.
    count = len(scenarios)
    pad = max(0.5, (_MIN_SLOTS - count) / 2.0)
    axes.set_xlim(-pad, (count - 1) + pad)
    axes.set_xticks(list(range(count)))
    if not visible:
        axes.set_xticklabels([])
        return
    axes.set_xticklabels([_capacity_label(s.capacity_kwh) for s in scenarios])
    axes.set_xlabel(
        "Usable battery capacity",
        fontfamily=_FONT,
        fontsize=_SIZE_AXIS,
        color=_INK_MUTED,
        labelpad=8,
    )


def _label_headroom_px(axes: Axes, extra_offset_pt: float = 0.0) -> float:
    """Pixels a bar label needs beyond its bar: the gap, the text, and clearance.

    Measured rather than assumed, because the quantity being reserved is a screen
    quantity. The label is placed at a fixed point offset from the bar top and is a
    fixed number of points tall, so the room it needs is a constant in *pixels* —
    while `_pad_range` reserves room as a fraction of the *data span*, which is a
    different number of pixels on every panel. Whether the fraction covered the
    label was therefore a coincidence of how tall that panel happened to be laid
    out, and on the 60-day card, whose panels are shorter than the fixture's
    because the seasonality band takes a slice of the height, the coincidence
    failed: the 303 EUR label crossed the axis top by 3 px, and the 29.7 payback
    label by the same. The fixture's own tallest label cleared by 3.5 px, which is
    the same defect that had not yet crossed zero.

    Reading the rendered extent of an actual label removes the coincidence: the
    caller converts these pixels into data units for the panel it is padding, so
    every panel reserves the room its labels really occupy.
    """
    figure = axes.get_figure()
    assert figure is not None  # an axes added via `add_axes` always has one
    canvas = figure.canvas
    assert isinstance(canvas, FigureCanvasAgg)  # attached in `_build`
    # matplotlib ships no stubs, so `get_renderer` is untyped to mypy strict.
    renderer = canvas.get_renderer()  # type: ignore[no-untyped-call]

    # Any label string measures the same height — DejaVu's vertical metrics do not
    # depend on the glyphs, or on the weight — so a probe stands in for all of them.
    probe = figure.text(0, 0, "0", fontfamily=_FONT, fontsize=_SIZE_AXIS + 1)
    height = probe.get_window_extent(renderer=renderer).height
    probe.remove()

    points_to_px = figure.dpi / 72.0
    gap = _LABEL_GAP_PT + extra_offset_pt + _LABEL_CLEARANCE_PT
    return height + gap * points_to_px


def _data_units_per_px(axes: Axes, span: float) -> float:
    """How many data units one vertical pixel is worth on this panel.

    Derived from the axes' own height in pixels against the data span it currently
    shows, which is what lets a pixel allowance be converted into the units
    `set_ylim` speaks.
    """
    height_px = axes.get_window_extent().height
    if height_px <= 0:
        return 0.0
    return span / height_px


def _pad_range(axes: Axes, values: list[float], extra_offset_pt: float = 0.0) -> None:
    """Set the y-range so every bar clears the axis edges and every label fits.

    Three things to get right, and only the first is cosmetic.

    **Headroom on the side the labels are on.** Now that every bar is labelled and
    not just the recommended one, the tallest bar's label is no longer a special
    case — it is the ordinary case, and without room above it the number is drawn
    outside the axes and clipped. Just as bad without any clipping: a bar whose top
    lands on the frame reads as *truncated*, as though the panel could not contain
    its own value.

    The allowance is the **measured** one from `_label_headroom_px`, converted into
    data units for this panel, with `_LABEL_HEADROOM` kept only as a floor. A bare
    fraction of the data span was the actual defect: it is a different number of
    pixels on every panel, so whether it covered the label depended on how tall the
    panel happened to be laid out rather than on how tall the label is.

    **The padding follows the labels, not the axis.** Labels sit above positive
    bars and below negative ones, so a panel with no negative bar gets no room
    below zero and a panel with no positive bar gets none above it. That is what
    removes the dead band the losing card used to draw: its axis ran to +200 with
    nothing in it, a fifth of the panel spent on the region where the finding
    *isn't*, which flattened the losses it was supposed to show.

    **Negative values must stay visible as negative.** Savings go below zero under
    a feed-in tariff more generous than the import price — real, increasingly
    common, and exactly the result this tool exists to be willing to report.
    Anchoring the axis at zero would draw those bars as nothing at all: an empty
    panel reading as "no data" beside a headline saying the battery lost money. So
    zero is always *inside* the range, and the range follows the data in whichever
    directions the data actually goes.
    """
    tallest = max([*values, 0.0])
    lowest = min([*values, 0.0])
    span = tallest - lowest
    if span <= 0:
        axes.set_ylim(0.0, 1.0)
        return

    # The measured allowance, in this panel's data units. Taken against the padded
    # span rather than the raw one — adding headroom stretches the axis, which
    # shrinks the data value of a pixel, which would leave the allowance slightly
    # short of what it was computed to cover. Solving that feedback directly is not
    # worth it; one pass at the fractional floor is enough to converge.
    provisional = span * (1.0 + _LABEL_HEADROOM)
    per_px = _data_units_per_px(axes, provisional)
    label_room = _label_headroom_px(axes, extra_offset_pt) * per_px
    # The fraction is the floor, not the answer: it keeps short labels from letting
    # the axis close right up on the tallest bar.
    headroom = max(label_room, span * _LABEL_HEADROOM)

    # Zero is an endpoint whenever no bar crosses it, and it gets the small edge
    # margin rather than the label allowance: nothing is ever written past zero on
    # the empty side.
    top = tallest + (headroom if tallest > 0 else span * _EDGE_MARGIN)
    bottom = lowest - (headroom if lowest < 0 else span * _EDGE_MARGIN)
    axes.set_ylim(bottom, top)


# --- Footer ------------------------------------------------------------------


def _draw_footer(
    figure: Figure, result: AnalysisResult, tariff: Tariff | None, repo_url: str
) -> None:
    """Period, tariff, and attribution.

    The tariff line is load-bearing, not a credit: every EUR figure above it is a
    function of the import and export prices, and a reader who cannot see those
    prices cannot tell whether the result transfers to their own house. It is
    printed in full, in the same words the report uses, via the shared
    `describe_tariff` — the card and the report describing the same tariff
    differently would be worse than either one alone.
    """
    figure.add_artist(
        Line2D(
            [_MARGIN, 1 - _MARGIN],
            [0.106, 0.106],
            transform=figure.transFigure,
            color=_RULE,
            linewidth=1.0,
        )
    )

    period = (
        f"{_date_only(result.period_start)} to {_date_only(result.period_end)}  ·  "
        f"{result.days_analyzed} days"
    )
    if tariff is not None:
        period += f"  ·  {describe_tariff(tariff)}"

    figure.text(
        _MARGIN,
        0.084,
        period,
        fontfamily=_FONT,
        fontsize=_SIZE_FOOTER,
        color=_INK_SECONDARY,
        va="top",
        ha="left",
    )
    figure.text(
        _MARGIN,
        0.056,
        "Retrospective analysis of real metered data. "
        "No degradation, no price inflation, no incentives.",
        fontfamily=_FONT,
        fontsize=_SIZE_FOOTER,
        color=_INK_MUTED,
        va="top",
        ha="left",
    )
    figure.text(
        _MARGIN,
        0.024,
        _BRAND,
        fontfamily=_FONT,
        fontsize=_SIZE_BRAND,
        fontweight="bold",
        color=_INK,
        va="baseline",
        ha="left",
    )
    figure.text(
        1 - _MARGIN,
        0.024,
        repo_url,
        fontfamily=_FONT,
        fontsize=_SIZE_FOOTER,
        color=_INK_MUTED,
        va="baseline",
        ha="right",
    )


def _date_only(timestamp: str) -> str:
    """The date part of an ISO timestamp.

    The card has no room for offsets and no need for them: the reader is being
    told which months the analysis covers, not which instant it started.
    """
    return timestamp[:10]
