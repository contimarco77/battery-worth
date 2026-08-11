"""Shareable PNG summary card.

This is the project's viral vehicle, and that fixes the design constraints far
more tightly than "make a chart" would. The card is seen **out of context**, in a
feed, on a phone, by someone who did not read the report and will not open it. It
has about three seconds.

Three consequences run through everything below.

**The headline is the capacity, not the payback.** "5 kWh is enough for this
house" is actionable and contradicts what a salesperson told the reader; "14.2
years" alone is just discouraging and gets scrolled past. Savings and payback are
a subordinate pair directly under it, because they are what makes the headline
credible, not what makes it interesting.

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
from battery_worth.report import annualization_years, describe_tariff

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

# Paybacks longer than this are drawn clipped, with the true value labelled. A
# single 300-year bar would flatten every other bar into the baseline and hide
# the shape that is the whole point of the panel.
_PAYBACK_AXIS_CAP_YEARS = 40.0
# Only clip when something is actually off the scale by a margin; clipping a
# 41-year bar to 40 would misrepresent it for no legibility gain.
_PAYBACK_CLIP_TRIGGER = 1.25

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
# The panel is laid out for at least this many capacity slots, so a one- or
# two-capacity sweep produces narrow bars in a wide panel rather than slabs.
_MIN_SLOTS = 4

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


def _build(
    result: AnalysisResult, tariff: Tariff | None, repo_url: str
) -> Figure:
    figure = Figure(figsize=(_FIG_INCHES, _FIG_INCHES), dpi=_DPI, facecolor=_SURFACE)
    # An explicit Agg canvas, rather than going through pyplot: the figure is
    # never shown, and pyplot's global registry would hold every card a
    # long-running caller renders. It also gives `_fit_headline_size` a renderer
    # to measure text against, which a bare Figure does not have.
    FigureCanvasAgg(figure)

    years = annualization_years(result.days_analyzed)
    best = recommended_scenario(result.scenarios)
    battery_scenarios = [s for s in result.scenarios if s.capacity_kwh > 0]

    cursor = 1.0 - _MARGIN
    cursor = _draw_headline(figure, best, cursor)
    cursor = _draw_stats(figure, best, years, cursor)
    cursor = _draw_warning(figure, result, cursor)
    _draw_chart(figure, battery_scenarios, years, cursor)
    _draw_footer(figure, result, tariff, repo_url)
    return figure


# --- Headline ----------------------------------------------------------------


def _draw_headline(figure: Figure, best: ScenarioResult | None, top: float) -> float:
    """The verdict, as the largest thing on the card.

    Phrased as a capacity claim ("5 kWh is enough for this house") because that is
    the sentence a reader can act on and, often, the sentence that contradicts the
    quote in their inbox. When nothing in the sweep saved money, the headline says
    so plainly rather than promoting the least-bad option — an honest negative
    result is still a result, and dressing it up would be the one failure this
    tool cannot afford.
    """
    kicker = "Would a home battery have paid off?"
    if best is None or best.capacity_kwh <= 0:
        headline = "No battery paid off here"
    else:
        headline = f"{_capacity_label(best.capacity_kwh)} is enough for this house"

    figure.text(
        _MARGIN, top, kicker,
        fontfamily=_FONT, fontsize=_SIZE_KICKER, color=_INK_MUTED,
        va="top", ha="left",
    )
    size = _fit_headline_size(figure, headline)
    figure.text(
        _MARGIN, top - 0.042, headline,
        fontfamily=_FONT, fontsize=size, fontweight="bold", color=_INK,
        va="top", ha="left",
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
        probe = figure.text(
            0, 0, headline, fontfamily=_FONT, fontsize=size, fontweight="bold"
        )
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
    figure: Figure, best: ScenarioResult | None, years: float, top: float
) -> float:
    """Savings per year and payback, as a pair, under the headline.

    Second in the hierarchy on purpose: they qualify the verdict rather than
    being it. Payback renders as "never" when there are no positive savings and
    is omitted entirely when no battery cost was supplied — an absent input must
    read as absent, never as a zero or a blank the reader can misinterpret.
    """
    if best is None:
        figure.text(
            _MARGIN, top - 0.01,
            "No capacity in this sweep saved money against the current tariff.",
            fontfamily=_FONT, fontsize=_SIZE_STAT_LABEL + 3, color=_INK_SECONDARY,
            va="top", ha="left",
        )
        return top - 0.075

    payback = best.payback_years()
    stats: list[tuple[str, str]] = [
        (f"{best.savings_eur / years:,.0f} EUR", "saved per year"),
    ]
    if best.battery_cost_eur is not None:
        stats.append((_payback_label(payback), "to pay back"))
        stats.append((f"{best.battery_cost_eur:,.0f} EUR", "battery cost"))

    baseline = top - 0.012
    for column, (value, label) in enumerate(stats):
        x = _MARGIN + column * 0.30
        figure.text(
            x, baseline, value,
            fontfamily=_FONT, fontsize=_SIZE_STAT_VALUE, fontweight="bold",
            color=_INK, va="top", ha="left",
        )
        figure.text(
            x, baseline - 0.041, label,
            fontfamily=_FONT, fontsize=_SIZE_STAT_LABEL, color=_INK_MUTED,
            va="top", ha="left",
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
    figure.patches.extend([
        Rectangle(
            (_MARGIN, band_top - height), _WIDTH, height,
            transform=figure.transFigure, facecolor=_WARNING_BG, edgecolor="none",
            zorder=0,
        )
    ])
    figure.text(
        _MARGIN + 0.018, band_top - height / 2,
        f"Only {result.days_analyzed} days of data — not a full year. "
        "Seasonality is not captured; treat these figures as indicative.",
        fontfamily=_FONT, fontsize=_SIZE_WARNING, color=_WARNING_INK,
        va="center", ha="left",
    )
    return band_top - height - _BAND_GAP


# --- The chart ---------------------------------------------------------------


def _draw_chart(  # noqa: PLR0914 - a two-panel layout genuinely needs its coordinates named
    figure: Figure, scenarios: list[ScenarioResult], years: float, cursor: float
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

    # The payback panel earns its space only when there is a payback to draw.
    # Without a battery cost there is nothing to compute; with a cost but no
    # positive savings anywhere, every bar is absent and the panel is a title over
    # a row of "never" — which the headline has already said, louder. Either way
    # the savings panel takes the full height instead of sharing it with an empty
    # one.
    has_payback = any(s.payback_years() is not None for s in scenarios)

    # Between the panels: the lower panel's own title, plus a breathing gap.
    gap = _PANEL_TITLE_SPACE + 0.022 if has_payback else 0.0
    panels = 2 if has_payback else 1
    panel_height = (available - gap) / panels

    savings_axes = figure.add_axes((
        _PLOT_LEFT, top - panel_height, _PLOT_WIDTH, panel_height
    ))
    _draw_savings_panel(savings_axes, scenarios, years, label_x=not has_payback)

    if has_payback:
        payback_axes = figure.add_axes((_PLOT_LEFT, bottom, _PLOT_WIDTH, panel_height))
        _draw_payback_panel(payback_axes, scenarios)


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
        title, fontfamily=_FONT, fontsize=_SIZE_CHART_TITLE, fontweight="bold",
        color=_INK_SECONDARY, loc="left", pad=10,
        x=(_MARGIN - _PLOT_LEFT) / _PLOT_WIDTH,
    )
    axes.grid(axis="y", color=_GRID, linewidth=1.0, linestyle="-")
    axes.set_axisbelow(True)
    for side in ("top", "right", "left"):
        axes.spines[side].set_visible(False)
    axes.spines["bottom"].set_color(_RULE)
    axes.spines["bottom"].set_linewidth(1.0)
    axes.tick_params(
        axis="both", length=0, labelsize=_SIZE_AXIS, colors=_INK_MUTED, pad=6
    )
    # Tick label fonts come from the rc context (`_RC`), not from a loop over the
    # current artists: matplotlib regenerates them on every locator pass, so
    # anything stamped here is discarded before the figure is drawn.


def _draw_savings_panel(
    axes: Axes, scenarios: list[ScenarioResult], years: float, label_x: bool
) -> None:
    """Upper panel: annual savings per capacity. Taller is better, and it saturates."""
    _style_panel(axes, "Savings per year")

    values = [s.savings_eur / years for s in scenarios]
    positions = list(range(len(scenarios)))
    best = recommended_scenario(scenarios)

    # One hue for the whole series, emphasis by opacity: the recommended bar at
    # full strength, the rest receded. Eight categorical hues for what is a single
    # measure would be the most common way a chart misses its own point.
    bars = axes.bar(
        positions, values, width=_bar_width(len(scenarios)),
        color=_SAVINGS, edgecolor="none",
    )
    for bar, scenario in zip(bars, scenarios, strict=True):
        emphasized = best is not None and scenario.capacity_kwh == best.capacity_kwh
        bar.set_alpha(1.0 if emphasized else 0.32)

    axes.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    axes.set_ylabel(
        "EUR / year", fontfamily=_FONT, fontsize=_SIZE_AXIS, color=_INK_MUTED, labelpad=8
    )
    _set_capacity_ticks(axes, scenarios, visible=label_x)

    # Direct-label the recommended bar; a number on every bar is noise and the
    # axis carries the rest. With nothing recommended — every capacity losing
    # money — every bar is labelled instead, because then the losses *are* the
    # finding and burying them behind an axis would soften it.
    for position, value, scenario in zip(positions, values, scenarios, strict=True):
        emphasized = best is not None and scenario.capacity_kwh == best.capacity_kwh
        if best is not None and not emphasized:
            continue
        below = value < 0
        axes.annotate(
            f"{value:,.0f} EUR",
            xy=(position, value), xytext=(0, -7 if below else 7),
            textcoords="offset points",
            ha="center", va="top" if below else "bottom",
            fontfamily=_FONT, fontsize=_SIZE_AXIS + 1,
            fontweight="bold" if emphasized else "normal",
            color=_INK if emphasized else _INK_SECONDARY,
        )

    _pad_top(axes, values, headroom=0.28)
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
    best = recommended_scenario(scenarios)
    drawn = [min(p, cap) if p is not None else 0.0 for p in paybacks]

    bars = axes.bar(
        positions, drawn, width=_bar_width(len(scenarios)),
        color=_PAYBACK, edgecolor="none",
    )
    for bar, scenario in zip(bars, scenarios, strict=True):
        emphasized = best is not None and scenario.capacity_kwh == best.capacity_kwh
        bar.set_alpha(1.0 if emphasized else 0.32)

    _mark_clipped_bars(axes, bars, paybacks, cap)

    axes.set_ylabel(
        "years", fontfamily=_FONT, fontsize=_SIZE_AXIS, color=_INK_MUTED, labelpad=8
    )
    _set_capacity_ticks(axes, scenarios, visible=True)

    for position, payback, scenario in zip(positions, paybacks, scenarios, strict=True):
        emphasized = best is not None and scenario.capacity_kwh == best.capacity_kwh
        clipped = payback is not None and payback > cap
        if payback is None:
            text, y = "never", 0.0
        elif emphasized or clipped:
            text, y = f"{payback:.1f}", min(payback, cap)
        else:
            continue
        axes.annotate(
            text,
            # Clipped bars carry a stub above the cap; the label clears it.
            xy=(position, y), xytext=(0, 14 if clipped else 7),
            textcoords="offset points",
            ha="center", va="bottom", fontfamily=_FONT, fontsize=_SIZE_AXIS + 1,
            fontweight="bold" if emphasized else "normal",
            color=_INK if emphasized else _INK_SECONDARY,
        )

    _pad_top(axes, drawn, headroom=0.28)


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
                (x, cap - band * 1.6), width, band,
                facecolor=_SURFACE, edgecolor="none", zorder=3, clip_on=False,
            )
        )
        axes.add_patch(
            Rectangle(
                (x, cap - band * 0.6), width, band * 1.4,
                facecolor=bar.get_facecolor(), alpha=bar.get_alpha(),
                edgecolor="none", zorder=2, clip_on=False,
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


def _set_capacity_ticks(
    axes: Axes, scenarios: list[ScenarioResult], visible: bool
) -> None:
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
        "Usable battery capacity", fontfamily=_FONT, fontsize=_SIZE_AXIS,
        color=_INK_MUTED, labelpad=8,
    )


def _pad_top(axes: Axes, values: list[float], headroom: float) -> None:
    """Set the y-range with room above the tallest bar for its direct label.

    Two things to get right, and the second is an honesty requirement rather than
    a cosmetic one.

    Headroom: without it the annotation on the tallest bar is drawn outside the
    axes and clipped — the label survives on every bar except the one that matters
    most.

    **Negative values must be visible as negative.** Savings go below zero under a
    feed-in tariff more generous than the import price, which is a real and
    increasingly common case, and it is exactly the result this tool exists to be
    willing to report. Anchoring the axis at zero would draw those bars as nothing
    at all — an empty panel reading as "no data" beside a headline that says the
    battery lost money. So the range follows the data in both directions, and the
    zero line stays inside it.
    """
    tallest = max([*values, 0.0])
    lowest = min([*values, 0.0])
    span = tallest - lowest
    if span <= 0:
        axes.set_ylim(0.0, 1.0)
        return
    axes.set_ylim(lowest - span * 0.08, tallest + span * headroom)


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
            [_MARGIN, 1 - _MARGIN], [0.106, 0.106],
            transform=figure.transFigure, color=_RULE, linewidth=1.0,
        )
    )

    period = (
        f"{_date_only(result.period_start)} to {_date_only(result.period_end)}  ·  "
        f"{result.days_analyzed} days"
    )
    if tariff is not None:
        period += f"  ·  {describe_tariff(tariff)}"

    figure.text(
        _MARGIN, 0.084, period,
        fontfamily=_FONT, fontsize=_SIZE_FOOTER, color=_INK_SECONDARY,
        va="top", ha="left",
    )
    figure.text(
        _MARGIN, 0.056, "Retrospective analysis of real metered data. "
        "No degradation, no price inflation, no incentives.",
        fontfamily=_FONT, fontsize=_SIZE_FOOTER, color=_INK_MUTED,
        va="top", ha="left",
    )
    figure.text(
        _MARGIN, 0.024, _BRAND,
        fontfamily=_FONT, fontsize=_SIZE_BRAND, fontweight="bold", color=_INK,
        va="baseline", ha="left",
    )
    figure.text(
        1 - _MARGIN, 0.024, repo_url,
        fontfamily=_FONT, fontsize=_SIZE_FOOTER, color=_INK_MUTED,
        va="baseline", ha="right",
    )


def _date_only(timestamp: str) -> str:
    """The date part of an ISO timestamp.

    The card has no room for offsets and no need for them: the reader is being
    told which months the analysis covers, not which instant it started.
    """
    return timestamp[:10]
