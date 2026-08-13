"""Tests for Markdown report rendering.

Two things are worth testing about a template and neither is its prose: that the
four fixed sections are always present, and that no number reaches the page except
through the analysis layer. `StrictUndefined` enforces the second at render time,
so a typo'd field is a test failure rather than a blank cell.
"""

from pathlib import Path

import pandas as pd
import pytest

from battery_worth.analysis import recommended_scenario, run_analysis
from battery_worth.card import build_summary_card
from battery_worth.models import (
    AnalysisResult,
    AnalysisTimezone,
    ScenarioResult,
    Tariff,
    TariffKind,
)
from battery_worth.report import annualization_years, render_report, write_report
from tests.test_analysis import FLAT_TARIFF, TEMPLATE, make_report, make_solar_days
from tests.test_card import beyond_lifetime, card_text
from tests.test_seasonal import make_year

FIXED_SECTIONS = (
    "## Verdict",
    "## Scenario comparison",
    "## Seasonal analysis",
    "## Limits & assumptions",
)


def build_result(df: pd.DataFrame | None = None) -> AnalysisResult:
    """The standard report input: a full seasonal year, priced, swept over three capacities."""
    return run_analysis(
        make_year() if df is None else df,
        make_report(),
        capacities=[0, 5, 10],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        battery_cost_per_kwh=600.0,
    )


# The Ausgrid fixture's own totals (customer 1, 2012-07-01 -> 2013-06-30, 365 days).
# PV is the measured figure recorded in PROJECT-CONTEXT.md; import and export are
# derived from it by the balance `consumption = import + pv - export`, using the
# fixture's measured consumption of 7679.201 kWh.
AUSGRID_DAYS = 365
AUSGRID_PV_KWH = 5115.207
AUSGRID_CONSUMPTION_KWH = 7679.201
AUSGRID_EXPORT_KWH = 3801.5
AUSGRID_IMPORT_KWH = AUSGRID_CONSUMPTION_KWH - AUSGRID_PV_KWH + AUSGRID_EXPORT_KWH


def ausgrid_totals_result() -> AnalysisResult:
    """An AnalysisResult carrying the fixture's real totals over exactly 365 days.

    Built directly rather than loaded: the Ausgrid CSV lives outside the repo (see
    PROJECT-CONTEXT.md), and what is under test here is the annualization
    arithmetic, not ingest — which has its own conservation tests.
    """
    scenario = ScenarioResult(
        capacity_kwh=5.0,
        battery_cost_eur=3000.0,
        total_consumption_kwh=AUSGRID_CONSUMPTION_KWH,
        total_pv_kwh=AUSGRID_PV_KWH,
        baseline_import_kwh=AUSGRID_IMPORT_KWH,
        baseline_export_kwh=AUSGRID_EXPORT_KWH,
        simulated_import_kwh=AUSGRID_IMPORT_KWH - 900.0,
        simulated_export_kwh=AUSGRID_EXPORT_KWH - 1000.0,
        battery_cycles=180.0,
        self_consumption_before=0.257,
        self_consumption_after=0.59,
        baseline_cost_eur=1000.0,
        simulated_cost_eur=789.0,
    )
    return AnalysisResult(
        scenarios=[scenario],
        period_start="2012-07-01T00:00:00+10:00",
        period_end="2013-06-30T23:00:00+10:00",
        days_analyzed=AUSGRID_DAYS,
        resolution_minutes=30,
        seasonality_warning=False,
    )


def test_a_full_year_annualizes_to_itself() -> None:
    """At exactly 365 days, every headline energy figure must equal the input total.

    Annualizing over 365.25 (a mean Julian year) scaled every figure by 0.07%, so
    the report printed PV 5,119 where the user's own file summed to 5,115.2. For a
    tool whose whole claim is "your own meter readings, no estimates", a reader who
    checks in a spreadsheet must find their number, not one they cannot explain.
    """
    assert annualization_years(365) == 1.0

    markdown = render_report(ausgrid_totals_result(), FLAT_TARIFF)

    assert f"{AUSGRID_PV_KWH:,.0f} kWh/yr" in markdown  # 5,115 — not 5,119
    assert f"{AUSGRID_CONSUMPTION_KWH:,.0f} kWh/yr" in markdown
    assert f"{AUSGRID_IMPORT_KWH:,.0f} kWh/yr" in markdown  # 6,365 — not 6,370
    assert f"{AUSGRID_EXPORT_KWH:,.0f} kWh/yr" in markdown  # 3,801 — not 3,804

    # The pre-fix Julian figures must not appear anywhere in the report.
    for wrong in ("5,119", "6,370", "3,804"):
        assert wrong not in markdown, f"Julian-year annualization still present: {wrong}"


def test_annualization_is_the_identity_everywhere_it_is_applied() -> None:
    """Savings and cycles annualize by the same rule as the energy figures.

    The drift was a single shared constant, so a fix that only corrected the
    visible energy totals would leave savings and cycles quietly scaled.
    """
    result = ausgrid_totals_result()
    scenario = result.scenarios[0]
    markdown = render_report(result, FLAT_TARIFF)

    assert f"{scenario.savings_eur:,.0f} EUR/year" in markdown
    assert f"{scenario.battery_cycles:,.0f}/yr" in markdown

    # Payback is cost / year-1 savings, so it inherits the same scaling.
    payback = scenario.payback_years()
    assert payback is not None
    assert f"payback {payback:.1f} y" in markdown


def test_partial_periods_still_scale_up_to_a_year() -> None:
    """365 being the identity must not mean annualization stopped happening."""
    assert annualization_years(730) == pytest.approx(2.0)
    assert annualization_years(a_half_year := 182) == pytest.approx(a_half_year / 365)

    df = make_solar_days(n_days=60)
    result = run_analysis(
        df,
        make_report(days=60),
        capacities=[10],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        battery_cost_per_kwh=600.0,
    )
    markdown = render_report(result, FLAT_TARIFF)
    scenario = result.scenarios[0]

    expected = scenario.total_pv_kwh / (60 / 365)
    assert f"{expected:,.0f} kWh/yr" in markdown
    assert expected > scenario.total_pv_kwh  # genuinely scaled up, not passed through


def test_all_four_fixed_sections_are_present() -> None:
    markdown = render_report(build_result(), FLAT_TARIFF)
    for section in FIXED_SECTIONS:
        assert section in markdown, f"missing fixed section: {section}"


def test_limits_section_is_present_even_when_the_verdict_is_great() -> None:
    """Limits & assumptions is unconditional: a report that drops its caveats when
    the numbers look good is exactly the failure mode this tool exists to avoid."""
    cheap_battery = run_analysis(
        make_year(),
        make_report(),
        capacities=[10],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        battery_cost_per_kwh=1.0,
    )
    markdown = render_report(cheap_battery, FLAT_TARIFF)

    assert "## Limits & assumptions" in markdown
    assert "Naive payback" in markdown
    assert "No battery degradation" in markdown


def test_limits_states_every_required_caveat_in_plain_language() -> None:
    markdown = render_report(build_result(), FLAT_TARIFF)
    limits = markdown.split("## Limits & assumptions")[1]

    for phrase in (
        "Greedy self-consumption",
        "buys cheap grid energy",  # the plain-language form of "no tariff arbitrage"
        "degradation",
        "inflation",
        "Naive payback",
        "Hourly netting",
        "Period analysed",
        "Missing periods count as zero energy",
    ):
        assert phrase in limits, f"limits section does not state: {phrase}"


def test_limits_states_the_zero_fill_rule_even_when_the_data_has_no_gaps() -> None:
    """The gap treatment is a modelling assumption, not an incident report.

    Ingest warns about it only when it finds a gap, and that warning is attached to a
    run. Limits & assumptions describes how the tool works regardless — a reader
    deciding whether to trust these numbers on *their* data needs to know that missing
    intervals count as zero before they have a file with a hole in it. This fixture is
    continuous and the sentence must still be there.
    """
    markdown = render_report(build_result(), FLAT_TARIFF)
    limits = markdown.split("## Limits & assumptions")[1]

    assert "Warnings" not in markdown, "this fixture is continuous — no gap warning to lean on"
    assert "zero consumption and zero production" in limits


def test_limits_names_the_analysis_timezone_and_flags_it_as_the_default() -> None:
    """The timezone caveat is a modelling assumption, not an incident report.

    `_warn_if_not_italian` inspects the index *after* ingest, and ingest localizes with
    the default zone when none is declared — so on a default run the index is always
    Europe/Rome and the conditional warning cannot fire, however foreign the data. The
    reader who most needs telling is therefore the one the warning never reaches, which
    is why the statement lives here, where it is unconditional. It must name the zone in
    effect *and* say the tool assumed rather than detected it.
    """
    markdown = render_report(build_result(), FLAT_TARIFF)
    limits = markdown.split("## Limits & assumptions")[1]

    assert "Europe/Rome" in limits
    assert "the assumed default" in limits
    assert "no timezone was declared" in limits
    # The Italian-bands consequence must hold for this run, which is priced flat.
    assert "ARERA holiday calendar" in limits
    assert "whichever tariff this run was priced with" in limits


def test_limits_does_not_call_a_declared_timezone_an_assumption() -> None:
    """Naming the zone is not enough: a reader comparing two runs has to be able to tell
    whether the tool was told or guessed, and a declared zone must not be labelled
    'default'. The band caveat stays either way — it describes the F1/F2/F3 scheme, not
    a verdict on this run's data."""
    markdown = render_report(
        build_result(),
        FLAT_TARIFF,
        timezone=AnalysisTimezone(name="Australia/Sydney", declared=True),
    )
    limits = markdown.split("## Limits & assumptions")[1]

    assert "Australia/Sydney" in limits
    assert "the assumed default" not in limits
    assert "no timezone was declared" not in limits
    assert "whichever tariff this run was priced with" in limits


def test_full_year_limits_says_seasonality_is_captured() -> None:
    markdown = render_report(build_result(), FLAT_TARIFF)
    assert "That is a full year" in markdown
    assert "less than a\n  full year" not in markdown


def test_partial_year_triggers_the_seasonality_caveat_in_both_places() -> None:
    """A short period must warn in the Verdict AND qualify the period line in Limits."""
    df = make_solar_days(n_days=60)
    result = run_analysis(
        df,
        make_report(days=60),
        capacities=[10],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        battery_cost_per_kwh=600.0,
    )
    markdown = render_report(result, FLAT_TARIFF)

    assert "less than a full year" in markdown  # Verdict callout
    assert "seasonality is not fully captured" in markdown  # Limits
    assert "60 days" in markdown


def test_scenario_table_has_one_row_per_capacity() -> None:
    markdown = render_report(build_result(), FLAT_TARIFF)
    table = markdown.split("## Scenario comparison")[1].split("###")[0]

    assert "| baseline |" in table
    assert "| 5 kWh |" in table
    assert "| 10 kWh |" in table


def test_baseline_row_shows_no_payback_and_no_cycles() -> None:
    """A '0.0 y' payback on the do-nothing row would be the worst number in the report."""
    markdown = render_report(build_result(), FLAT_TARIFF)
    baseline_row = next(line for line in markdown.splitlines() if line.startswith("| baseline |"))
    assert "0.0 y" not in baseline_row
    assert baseline_row.count("—") >= 2  # payback and cycles both dashed


def test_capacities_render_without_a_trailing_zero() -> None:
    """'5 kWh', not '5.0 kWh' — the user typed 5 and expects to read 5 back."""
    markdown = render_report(build_result(), FLAT_TARIFF)
    assert "5.0 kWh" not in markdown
    assert "5 kWh" in markdown


def test_sensitivity_table_renders_as_valid_markdown_rows() -> None:
    """Regression: inline jinja loops once collapsed this whole table onto one line."""
    markdown = render_report(build_result(), FLAT_TARIFF)
    # Bounded at the next heading: the seasonal table below has a different column
    # count, and letting it leak in here would make the width check meaningless.
    section = markdown.split("How much this depends on your export price")[1].split("## ")[0]
    lines = [ln for ln in section.splitlines() if ln.startswith("|")]

    assert len(lines) >= 4  # header, separator, and one row per positive capacity
    assert lines[1].startswith("|---")
    widths = {ln.count("|") for ln in lines}
    assert len(widths) == 1, f"ragged sensitivity table: {widths}"


def test_seasonal_table_renders_one_row_per_bucket() -> None:
    result = build_result()
    assert result.seasonal is not None
    markdown = render_report(result, FLAT_TARIFF)
    section = markdown.split("## Seasonal analysis")[1].split("## ")[0]
    rows = [ln for ln in section.splitlines() if ln.startswith("| 2025-")]

    assert len(rows) == len(result.seasonal.buckets)


def test_seasonal_section_describes_the_capacity_the_verdict_recommends() -> None:
    """The report must not describe two different batteries in adjacent sections.

    It once did: a Verdict recommending 5 kWh above a seasonal table showing the
    20 kWh unit's 94-100% self-consumption, which a skimming reader takes as theirs.
    """
    result = run_analysis(
        make_year(),
        make_report(),
        capacities=[0, 5, 10, 20],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        battery_cost_per_kwh=600.0,
    )
    recommended = recommended_scenario(result.scenarios)
    assert recommended is not None
    assert result.seasonal is not None
    assert result.seasonal.capacity_kwh == recommended.capacity_kwh

    markdown = render_report(result, FLAT_TARIFF)
    verdict = markdown.split("## Verdict")[1].split("## Scenario")[0]
    seasonal = markdown.split("## Seasonal analysis")[1].split("## ")[0]

    label = f"{recommended.capacity_kwh:g} kWh"
    assert label in verdict
    assert f"**{label}** battery — the one recommended above" in " ".join(seasonal.split())
    # The premise: the largest swept capacity is a different battery entirely.
    assert result.seasonal.capacity_kwh != 20.0


def test_seasonal_framing_does_not_recommend_what_the_verdict_rejected() -> None:
    """The sixth site of one split: `recommended_scenario` read as an endorsement.

    Past the lifetime threshold it is only the least-bad size, so the Verdict says
    the battery is not worth it at any size analysed while the Seasonal analysis
    opened "the one recommended above, so these are the figures that would actually
    be yours" — inviting the reader to treat as theirs a table the Verdict has just
    told them not to buy.

    Asserted on the rendered sentence, not on which branch ran: the last two rounds
    of this defect were both a correct branch attached to a wrong sentence.
    """
    result = beyond_lifetime()
    assert result.seasonal is not None
    markdown = render_report(result, FLAT_TARIFF)
    seasonal = " ".join(markdown.split("## Seasonal analysis")[1].split("## ")[0].split())

    assert "the one recommended above" not in seasonal
    assert "the figures that would actually be yours" not in seasonal
    # The section itself stays: the monthly breakdown is what makes the verdict
    # checkable period by period, so only the framing may change.
    assert f"**{result.seasonal.capacity_kwh:g} kWh**" in seasonal
    assert "not a size being recommended" in seasonal


def test_seasonal_framing_still_recommends_when_the_battery_pays_back() -> None:
    """The honest negative must not leak into the case that does pay back."""
    result = run_analysis(
        make_year(),
        make_report(),
        capacities=[0, 5, 10, 20],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        battery_cost_per_kwh=600.0,
    )
    seasonal = " ".join(
        render_report(result, FLAT_TARIFF).split("## Seasonal analysis")[1].split("## ")[0].split()
    )

    assert "the one recommended above" in seasonal
    assert "not a size being recommended" not in seasonal


def test_report_and_card_agree_on_the_seasonal_framing_too() -> None:
    """Extends the existing one-run-one-verdict pin to the section that drifted.

    The Verdict, the card and the seasonal intro are three statements of one
    finding. Two of them were locked together in the last round; this is the third,
    so the pair cannot drift apart again on the section nobody had listed.
    """
    result = beyond_lifetime()
    markdown = render_report(result, FLAT_TARIFF)
    card = card_text(build_summary_card(result, tariff=FLAT_TARIFF))
    seasonal = " ".join(markdown.split("## Seasonal analysis")[1].split("## ")[0].split())

    # All three carry the same negative, and none of them names a recommendation.
    assert "No capacity pays back within 20 years" in markdown
    assert "pays back within 20 years" in card
    assert "the one recommended above" not in seasonal
    assert "pays back fastest" not in card


def test_seasonal_section_keeps_the_ceiling_as_one_sentence() -> None:
    """The ceiling survives the demotion — as a computed figure, never hardcoded."""
    result = run_analysis(
        make_year(),
        make_report(),
        capacities=[0, 5, 10, 20],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        battery_cost_per_kwh=600.0,
    )
    assert result.seasonal is not None
    markdown = render_report(result, FLAT_TARIFF)
    seasonal = markdown.split("## Seasonal analysis")[1].split("## ")[0]

    # Line breaks are the template's business; the sentence is what is under test.
    unwrapped = " ".join(seasonal.split())
    assert "Even the largest battery in this sweep (20 kWh) would have left" in unwrapped
    expected = f"{result.seasonal.largest_capacity_unused_surplus_kwh:,.0f} kWh of surplus unused"
    assert expected in unwrapped

    # The old framing claimed the *described* battery's leftovers were unusable by
    # any battery in the sweep. That is false of a smaller unit and must be gone.
    assert "no** battery in the sweep could have used" not in unwrapped
    assert "this** battery could not store" in unwrapped


def test_seasonal_ceiling_sentence_is_dropped_when_it_adds_nothing() -> None:
    """When the recommendation *is* the largest, the sentence would restate the table."""
    result = run_analysis(
        make_year(),
        make_report(),
        capacities=[0, 10],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
    )
    assert result.seasonal is not None
    assert result.seasonal.is_ceiling
    markdown = render_report(result, FLAT_TARIFF)

    assert "Even the largest battery in this sweep" not in markdown


def test_seasonal_note_names_the_recommended_capacity() -> None:
    """Best/worst month sentences inherited the old mismatch; they must not now."""
    result = run_analysis(
        make_year(),
        make_report(),
        capacities=[0, 5, 10, 20],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        battery_cost_per_kwh=600.0,
    )
    assert result.seasonal is not None
    markdown = render_report(result, FLAT_TARIFF)
    seasonal = markdown.split("## Seasonal analysis")[1].split("## ")[0]

    label = f"{result.seasonal.capacity_kwh:g} kWh battery"
    assert f"exported despite the {label}" in " ".join(seasonal.split())


def test_seasonal_section_omitted_when_there_is_nothing_to_show() -> None:
    """Baseline-only sweep: no battery, so no seasonal story and no empty table."""
    result = run_analysis(
        make_year(),
        make_report(),
        capacities=[0],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
    )
    markdown = render_report(result, FLAT_TARIFF)

    assert "## Seasonal analysis" not in markdown
    assert "## Limits & assumptions" in markdown  # the fixed section still survives


def test_verdict_names_the_shortest_payback_not_the_largest_battery() -> None:
    markdown = render_report(build_result(), FLAT_TARIFF)
    verdict = markdown.split("## Verdict")[1].split("## Scenario")[0]

    assert "shortest payback" in verdict
    assert "payback" in verdict


def test_verdict_handles_a_battery_that_never_pays_off() -> None:
    """An absurd battery cost must produce an honest verdict, not a blank or a crash."""
    result = run_analysis(
        make_year(),
        make_report(),
        capacities=[10],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        battery_cost_per_kwh=10_000_000.0,
    )
    markdown = render_report(result, FLAT_TARIFF)

    assert "## Verdict" in markdown
    assert "never" in markdown


def test_verdict_without_a_battery_cost_asks_for_one() -> None:
    result = run_analysis(
        make_year(),
        make_report(),
        capacities=[10],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
    )
    markdown = render_report(result, FLAT_TARIFF)

    assert "--battery-cost-per-kwh" in markdown


def test_warnings_are_rendered_verbatim_and_numbered() -> None:
    """Warnings describe decisions made about the user's data; never summarized."""
    warning = "The autumn daylight-saving hour appears only once in this data."
    markdown = render_report(build_result(), FLAT_TARIFF, warnings=[warning])

    assert "## Warnings" in markdown
    assert f"1. {warning}" in markdown


def test_no_warnings_section_when_there_are_none() -> None:
    markdown = render_report(build_result(), FLAT_TARIFF)
    assert "## Warnings" not in markdown


def test_tariff_description_reaches_the_report() -> None:
    banded = Tariff(
        kind=TariffKind.F1_F2_F3,
        f1_price=0.35,
        f2_price=0.30,
        f3_price=0.25,
        export_price_eur_kwh=0.10,
    )
    result = run_analysis(
        make_year(), make_report(), capacities=[10], battery_template=TEMPLATE, tariff=banded
    )
    markdown = render_report(result, banded)

    assert "Italian bands F1 0.35" in markdown


def test_no_unrendered_jinja_syntax_survives() -> None:
    """StrictUndefined catches missing names; this catches malformed delimiters."""
    markdown = render_report(build_result(), FLAT_TARIFF, warnings=["a warning"])
    for token in ("{{", "}}", "{%", "%}"):
        assert token not in markdown, f"unrendered jinja token {token!r} in output"


def test_report_ends_with_exactly_one_newline() -> None:
    markdown = render_report(build_result(), FLAT_TARIFF)
    assert markdown.endswith("\n")
    assert not markdown.endswith("\n\n")


def test_write_report_creates_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deeper" / "report.md"
    write_report(target, build_result(), FLAT_TARIFF)

    assert target.exists()
    written = target.read_text(encoding="utf-8")
    assert written == render_report(build_result(), FLAT_TARIFF)
    for section in FIXED_SECTIONS:
        assert section in written


def test_render_is_deterministic() -> None:
    result = build_result()
    assert render_report(result, FLAT_TARIFF) == render_report(result, FLAT_TARIFF)


@pytest.mark.parametrize("capacities", [[10], [0, 10], [0, 5, 10, 15, 20]])
def test_renders_for_any_sweep_shape(capacities: list[float]) -> None:
    result = run_analysis(
        make_year(),
        make_report(),
        capacities=capacities,
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        battery_cost_per_kwh=600.0,
    )
    markdown = render_report(result, FLAT_TARIFF)
    assert "## Verdict" in markdown
    assert "## Limits & assumptions" in markdown
