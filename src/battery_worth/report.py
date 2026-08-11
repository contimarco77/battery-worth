"""Markdown report rendering.

Format decision: **Markdown is the primary output**, not HTML and not PDF. It
renders natively on GitHub, pastes into Reddit and the Home Assistant forums with
its tables intact, diffs cleanly, and needs no browser to read. The audience for
this tool shares results in exactly those places.

Division of labour, enforced rather than merely intended: **the template contains
no computation.** Every number it prints comes from `AnalysisResult` already
computed by the analysis layer; jinja2 only formats. The filters below are the
whole formatting vocabulary — `annual` is the one that does arithmetic, and it
does the single conversion (period total -> per year) that would otherwise have
to be repeated at every call site in the template.

`StrictUndefined` is deliberate: a typo'd field name in the template must fail
loudly at render time rather than printing an empty cell into a report someone
is about to make a purchase decision on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from battery_worth import PROJECT_NAME, REPO_URL
from battery_worth.analysis import recommended_scenario
from battery_worth.models import (
    AnalysisResult,
    ScenarioResult,
    Tariff,
    TariffKind,
    annualization_years,
)

# Re-exported, not redefined. It used to be defined here, which was wrong in a way
# that mattered: `payback_years()` needs annualization to be *correct*, not merely
# formatted, so the rule belongs to the domain model. Kept importable from this
# module because the CLI and the tests already reach for it here.
__all__ = ["annualization_years", "describe_tariff", "render_report", "write_report"]

if TYPE_CHECKING:
    from pathlib import Path

_TEMPLATE_NAME = "report.md.j2"

# Below this, a season's savings are too small for its share of the year to be
# worth calling out as the reason the payback looks the way it does.
_NOTE_MIN_SAVINGS_EUR = 1.0


def render_report(
    result: AnalysisResult,
    tariff: Tariff,
    warnings: list[str] | None = None,
) -> str:
    """Render the full four-section Markdown report.

    Sections are fixed and always present, in order: Verdict, Scenario comparison,
    Seasonal analysis, Limits & assumptions. "Limits & assumptions" in particular
    is never conditional — a report that omits its own caveats when the numbers
    look good is exactly the failure mode this tool exists to avoid.
    """
    env = _build_environment()
    template = env.get_template(_TEMPLATE_NAME)

    years = annualization_years(result.days_analyzed)
    best = recommended_scenario(result.scenarios)
    reference = best if best is not None else _largest_scenario(result.scenarios)

    return template.render(
        r=result,
        years=years,
        best=best,
        totals=_totals(reference),
        sensitivity=result.export_sensitivity,
        seasonal=result.seasonal,
        seasonal_note=_seasonal_note(result),
        has_payback=any(s.battery_cost_eur is not None for s in result.scenarios),
        tariff_description=describe_tariff(tariff),
        warnings=warnings or [],
        project_name=PROJECT_NAME,
        repo_url=REPO_URL,
    ).strip() + "\n"


def write_report(
    path: Path,
    result: AnalysisResult,
    tariff: Tariff,
    warnings: list[str] | None = None,
) -> None:
    """Render the report and write it to `path`, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(result, tariff, warnings), encoding="utf-8")


def _build_environment() -> Environment:
    env = Environment(
        loader=PackageLoader("battery_worth", "templates"),
        undefined=StrictUndefined,
        autoescape=select_autoescape(default=False, default_for_string=False),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters.update(
        annual=_filter_annual,
        eur=_filter_eur,
        kwh=_filter_kwh,
        cap=_filter_cap,
        pct=_filter_pct,
        price=_filter_price,
        years=_filter_years,
        round0=_filter_round0,
    )
    return env


def _filter_annual(value: float, years: float) -> float:
    """Scale a whole-period total to a per-year figure.

    The only arithmetic the template is allowed to trigger, and it is here rather
    than inline because the alternative — precomputing an annualized twin of every
    field — would double the model surface for a single division.
    """
    return value / years


def _filter_eur(value: float) -> str:
    """EUR with thousands separators and no decimals: report figures are never
    precise to the cent, and printing cents would imply they are."""
    return f"{value:,.0f} EUR"


def _filter_kwh(value: float) -> str:
    """kWh with no decimals above 100, one below — keeps small monthly figures readable."""
    return f"{value:,.1f}" if abs(value) < 100 else f"{value:,.0f}"  # noqa: PLR2004


def _filter_cap(value: float) -> str:
    """A capacity label, not an energy total: '5 kWh', never '5.0 kWh'.

    Separate from `kwh` because capacities are nameplate figures the user typed on
    the command line and expects to see echoed back unchanged, while energy totals
    are measured quantities where a decimal carries information.
    """
    return f"{value:g}"


def _filter_pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def _filter_price(value: float) -> str:
    """Prices need cents: the whole export-sensitivity section moves in 0.05 steps."""
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _filter_years(value: float | None) -> str:
    """Payback in years, or 'never' — never a number when there is no payback.

    `None` reaching this filter means either no battery cost or non-positive
    savings. Both are honestly rendered as "never" rather than a blank cell,
    because a blank invites the reader to assume it was simply not computed.
    """
    return "never" if value is None else f"{value:.1f} y"


def _filter_round0(value: float) -> str:
    return f"{value:,.0f}"


def _largest_scenario(scenarios: list[ScenarioResult]) -> ScenarioResult:
    """Fallback source for the PV/consumption totals when nothing saved money.

    Those totals are properties of the household, identical in every scenario, so
    any row serves; picking one keeps the Verdict section's data line populated
    even in the "not worth it" case.
    """
    return max(scenarios, key=lambda s: s.capacity_kwh)


def _totals(scenario: ScenarioResult) -> dict[str, float]:
    """Household totals, which are the same in every scenario by construction."""
    return {"pv": scenario.total_pv_kwh, "consumption": scenario.total_consumption_kwh}


def _seasonal_note(result: AnalysisResult) -> str | None:
    """One plain sentence naming where the savings actually came from.

    The seasonal table shows the shape; this says what the shape means, which is
    the difference between a reader skimming past the section and understanding
    why their payback is what it is.

    Every figure here is the recommended capacity's, matching the table above it:
    a best/worst month computed against a different battery than the one being
    recommended would be the same mismatch the section itself used to have.
    """
    seasonal = result.seasonal
    if seasonal is None or not seasonal.buckets:
        return None

    ranked = sorted(seasonal.buckets, key=lambda b: b.savings_eur, reverse=True)
    top = ranked[0]
    bottom = ranked[-1]
    if top.savings_eur < _NOTE_MIN_SAVINGS_EUR:
        return None

    unit = "month" if seasonal.granularity == "month" else "season"
    wasted = max(seasonal.buckets, key=lambda b: b.unused_surplus_kwh)

    note = (
        f"The best {unit} was **{top.label}** ({_filter_eur(top.savings_eur)}), the worst "
        f"**{bottom.label}** ({_filter_eur(bottom.savings_eur)})."
    )
    if wasted.unused_surplus_kwh > 0:
        note += (
            f" Most unused surplus fell in **{wasted.label}** "
            f"({_filter_kwh(wasted.unused_surplus_kwh)} kWh exported despite the "
            f"{_filter_cap(seasonal.capacity_kwh)} kWh battery) — that is the energy a "
            "larger battery, or shifting load into daylight hours, would have to capture."
        )
    return note


def describe_tariff(tariff: Tariff) -> str:
    """Plain-language tariff description, shared between the report and the CLI."""
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
