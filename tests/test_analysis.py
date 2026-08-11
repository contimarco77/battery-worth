"""Tests for the capacity sweep orchestration.

The two structural invariants here (monotonicity and saturation) are the ones a
reader of the comparison table will assume without being told, so they are
asserted rather than trusted.
"""

import numpy as np
import pandas as pd
import pytest

from battery_worth.analysis import run_analysis
from battery_worth.models import (
    BatterySpec,
    IngestReport,
    ScenarioResult,
    Tariff,
    TariffKind,
)


def make_report(days: int = 365, resolution: int = 60) -> IngestReport:
    return IngestReport(
        period_start="2025-01-01 00:00:00+01:00",
        period_end="2025-12-31 23:00:00+01:00",
        days_analyzed=days,
        native_resolution_minutes=resolution,
        schema_used="grid_centric",
        seasonality_warning=days < 365,
    )


def make_solar_days(
    n_days: int = 30, pv_peak: float = 4.0, night_load: float = 1.0
) -> pd.DataFrame:
    """A simple repeating day: PV surplus midday, steady load at night.

    Deliberately regular so the saturation point can be reasoned about on paper:
    each day offers a fixed surplus and asks for a fixed overnight deficit.
    """
    hours = n_days * 24
    idx = pd.date_range("2025-06-01", periods=hours, freq="h", tz="Europe/Rome")
    hour_of_day = np.asarray(idx.hour)

    pv = np.where((hour_of_day >= 9) & (hour_of_day < 15), pv_peak, 0.0)
    load = np.full(hours, night_load)

    net = load - pv
    return pd.DataFrame(
        {
            "grid_import": np.clip(net, 0.0, None),
            "grid_export": np.clip(-net, 0.0, None),
            "pv_production": pv,
        },
        index=idx,
    )


FLAT_TARIFF = Tariff(kind=TariffKind.FLAT, flat_price_eur_kwh=0.30, export_price_eur_kwh=0.10)
TEMPLATE = BatterySpec(
    usable_capacity_kwh=1.0,  # ignored by the sweep
    max_charge_kw=5.0,
    max_discharge_kw=5.0,
    round_trip_efficiency=1.0,
)


def test_savings_monotonically_non_decreasing_with_capacity() -> None:
    """A bigger battery can never save less under a fixed tariff and greedy self-consumption.

    Nothing in the strategy trades present savings for future savings, so extra
    capacity can only ever absorb surplus that a smaller battery had to export.
    """
    df = make_solar_days()
    result = run_analysis(
        df,
        make_report(),
        capacities=[0, 2, 5, 10, 20, 50],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
    )

    savings = [s.savings_eur for s in result.scenarios]
    assert savings == sorted(savings), f"savings not monotonic across capacity: {savings}"
    assert savings[0] == pytest.approx(0.0)  # the no-battery baseline saves nothing


def test_savings_saturate_past_full_surplus_absorption() -> None:
    """Doubling capacity past the point where all surplus is already absorbed adds ~nothing.

    The day offers 6 h * 4 kW = 24 kWh of PV against 24 kWh of load, of which 6 kWh
    is consumed live; the remaining 18 kWh of surplus meets an 18 kWh deficit. Once
    the battery can hold a day's surplus, more capacity has nothing left to store.
    """
    df = make_solar_days()
    result = run_analysis(
        df,
        make_report(),
        capacities=[20, 40, 80],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
    )

    at_20, at_40, at_80 = (s.savings_eur for s in result.scenarios)

    assert at_20 > 0
    assert at_40 == pytest.approx(at_20, rel=1e-9)
    assert at_80 == pytest.approx(at_20, rel=1e-9)


def test_saturated_scenario_absorbs_all_surplus() -> None:
    """Sanity anchor for the saturation test: at 20 kWh nothing is exported anymore."""
    df = make_solar_days()
    result = run_analysis(
        df, make_report(), capacities=[20], battery_template=TEMPLATE, tariff=FLAT_TARIFF
    )

    scenario = result.scenarios[0]
    assert scenario.simulated_export_kwh == pytest.approx(0.0)
    assert scenario.self_consumption_after == pytest.approx(1.0)


def test_baseline_scenario_has_no_payback_and_no_cycles() -> None:
    """Capacity 0 must not produce a meaningless payback: no cost, no savings, no division."""
    df = make_solar_days()
    result = run_analysis(
        df,
        make_report(),
        capacities=[0, 10],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        battery_cost_per_kwh=600.0,
    )

    baseline = result.scenarios[0]
    assert baseline.capacity_kwh == 0.0
    assert baseline.battery_cost_eur is None
    assert baseline.payback_years() is None
    assert baseline.battery_cycles == 0.0
    assert baseline.savings_eur == pytest.approx(0.0)
    assert baseline.self_consumption_before == baseline.self_consumption_after


def test_baseline_matches_simulated_scenario_baseline_figures() -> None:
    """The hand-built capacity-0 row must agree with what the simulator reports as baseline.

    The baseline scenario bypasses `simulate_battery`, so this pins the two paths
    together: if the simulator's baseline accounting ever changes, this fails.
    """
    df = make_solar_days()
    result = run_analysis(
        df, make_report(), capacities=[0, 10], battery_template=TEMPLATE, tariff=FLAT_TARIFF
    )
    baseline, with_battery = result.scenarios

    assert baseline.baseline_cost_eur == pytest.approx(with_battery.baseline_cost_eur)
    assert baseline.baseline_import_kwh == pytest.approx(with_battery.baseline_import_kwh)
    assert baseline.baseline_export_kwh == pytest.approx(with_battery.baseline_export_kwh)
    assert baseline.total_consumption_kwh == pytest.approx(with_battery.total_consumption_kwh)
    assert baseline.self_consumption_before == pytest.approx(with_battery.self_consumption_before)


def test_battery_cost_scales_with_capacity() -> None:
    df = make_solar_days()
    result = run_analysis(
        df,
        make_report(),
        capacities=[5, 10],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        battery_cost_per_kwh=600.0,
    )

    assert result.scenarios[0].battery_cost_eur == pytest.approx(3000.0)
    assert result.scenarios[1].battery_cost_eur == pytest.approx(6000.0)


def test_capacities_deduplicated_and_sorted() -> None:
    df = make_solar_days()
    result = run_analysis(
        df, make_report(), capacities=[10, 5, 10, 0], battery_template=TEMPLATE, tariff=FLAT_TARIFF
    )

    assert [s.capacity_kwh for s in result.scenarios] == [0.0, 5.0, 10.0]


def test_template_capacity_is_ignored_but_other_params_are_kept() -> None:
    """The sweep overrides capacity only; power/efficiency/min_soc come from the template."""
    df = make_solar_days()
    template = BatterySpec(
        usable_capacity_kwh=999.0, max_charge_kw=1.0, max_discharge_kw=1.0,
        round_trip_efficiency=0.81, min_soc=0.1,
    )
    result = run_analysis(
        df, make_report(), capacities=[10], battery_template=template, tariff=FLAT_TARIFF
    )

    scenario = result.scenarios[0]
    assert scenario.capacity_kwh == 10.0
    # A 1 kW charge limit cannot absorb the 3 kW midday surplus, so export survives.
    assert scenario.simulated_export_kwh > 0


def test_metadata_carried_from_ingest_report() -> None:
    df = make_solar_days()
    report = make_report(days=90, resolution=30)
    result = run_analysis(
        df, report, capacities=[10], battery_template=TEMPLATE, tariff=FLAT_TARIFF
    )

    assert result.period_start == report.period_start
    assert result.period_end == report.period_end
    assert result.days_analyzed == 90
    assert result.resolution_minutes == 30
    assert result.seasonality_warning is True


def test_seasonality_warning_false_for_full_year() -> None:
    df = make_solar_days()
    result = run_analysis(
        df, make_report(days=365), capacities=[10], battery_template=TEMPLATE, tariff=FLAT_TARIFF
    )
    assert result.seasonality_warning is False


def test_empty_capacities_rejected() -> None:
    df = make_solar_days()
    with pytest.raises(ValueError, match="at least one capacity"):
        run_analysis(
            df, make_report(), capacities=[], battery_template=TEMPLATE, tariff=FLAT_TARIFF
        )


def test_negative_capacity_rejected() -> None:
    df = make_solar_days()
    with pytest.raises(ValueError, match="zero or positive"):
        run_analysis(
            df, make_report(), capacities=[5, -1], battery_template=TEMPLATE, tariff=FLAT_TARIFF
        )


def test_empty_dataframe_rejected() -> None:
    empty = pd.DataFrame(
        {"grid_import": [], "grid_export": [], "pv_production": []},
        index=pd.DatetimeIndex([], tz="Europe/Rome"),
    )
    with pytest.raises(ValueError, match="empty dataset"):
        run_analysis(
            empty, make_report(), capacities=[10], battery_template=TEMPLATE, tariff=FLAT_TARIFF
        )


def test_lossy_battery_saves_less_than_lossless() -> None:
    """Round-trip losses must show up as strictly lower savings at the same capacity."""
    df = make_solar_days()
    lossy = TEMPLATE.model_copy(update={"round_trip_efficiency": 0.81})

    lossless_result = run_analysis(
        df, make_report(), capacities=[10], battery_template=TEMPLATE, tariff=FLAT_TARIFF
    )
    lossy_result = run_analysis(
        df, make_report(), capacities=[10], battery_template=lossy, tariff=FLAT_TARIFF
    )

    assert lossy_result.scenarios[0].savings_eur < lossless_result.scenarios[0].savings_eur


def test_monotonicity_holds_under_f123_bands() -> None:
    """Monotonicity is a property of the strategy, not of the flat tariff: check it on bands too."""
    df = make_solar_days()
    banded = Tariff(
        kind=TariffKind.F1_F2_F3, f1_price=0.35, f2_price=0.30, f3_price=0.25,
        export_price_eur_kwh=0.10,
    )
    result = run_analysis(
        df, make_report(), capacities=[0, 3, 6, 12, 24], battery_template=TEMPLATE, tariff=banded
    )

    savings = [s.savings_eur for s in result.scenarios]
    assert savings == sorted(savings), f"savings not monotonic under F1/F2/F3: {savings}"


# --- Payback annualization ----------------------------------------------------
#
# Payback must be `cost / ANNUAL savings`, not `cost / period savings`. The two
# coincide only on a dataset exactly one year long — which the project's own
# fixture is, which is why this shipped unnoticed: on a 60-day file payback was
# overstated by 365/60, i.e. 6x, and printed directly beside the annualized
# savings figure that contradicted it.
#
# Every assertion below is anchored to a value computed *by hand in the test*,
# never to another layer of our own code. This is the second bug of exactly this
# shape (see PROJECT-CONTEXT.md, session 5's annualization drift), and both times
# the whole suite agreed with itself while being uniformly wrong.


def test_payback_divides_by_annualized_savings_not_period_savings() -> None:
    """The arithmetic, pinned against a hand-computed number.

    A battery costing 3,000 EUR that saves 32.7 EUR over 60 days is saving
    32.7 * 365/60 = 198.9 EUR/year, so it pays back in 3000/198.9 = 15.08 years —
    not the 91.7 that dividing by the period total produces.
    """
    scenario = ScenarioResult(
        capacity_kwh=5.0,
        battery_cost_eur=3000.0,
        days_analyzed=60,
        total_consumption_kwh=1000.0,
        total_pv_kwh=800.0,
        baseline_import_kwh=600.0,
        baseline_export_kwh=400.0,
        simulated_import_kwh=500.0,
        simulated_export_kwh=300.0,
        battery_cycles=50.0,
        self_consumption_before=0.5,
        self_consumption_after=0.6,
        baseline_cost_eur=132.7,
        simulated_cost_eur=100.0,
    )

    assert scenario.savings_eur == pytest.approx(32.7)
    assert scenario.annual_savings_eur == pytest.approx(32.7 * 365 / 60)
    assert scenario.payback_years() == pytest.approx(3000.0 / (32.7 * 365 / 60))
    assert scenario.payback_years() == pytest.approx(15.083, abs=0.01)


def test_payback_is_the_identity_on_a_full_year() -> None:
    """At 365 days annualization is a no-op, so the naive division is correct there.

    This is the case that hid the bug; it is pinned so a future "fix" cannot
    reintroduce a scaling factor on the one period length users check by hand.
    """
    scenario = ScenarioResult(
        capacity_kwh=5.0,
        battery_cost_eur=3000.0,
        days_analyzed=365,
        total_consumption_kwh=1000.0,
        total_pv_kwh=800.0,
        baseline_import_kwh=600.0,
        baseline_export_kwh=400.0,
        simulated_import_kwh=500.0,
        simulated_export_kwh=300.0,
        battery_cycles=50.0,
        self_consumption_before=0.5,
        self_consumption_after=0.6,
        baseline_cost_eur=300.0,
        simulated_cost_eur=100.0,
    )

    assert scenario.annual_savings_eur == pytest.approx(200.0)
    assert scenario.payback_years() == pytest.approx(15.0)


def test_payback_is_near_invariant_to_the_length_of_the_period() -> None:
    """The same repeating data truncated to 60/180/365 days pays back the same.

    This is the whole point of annualizing, and it is the property the bug broke:
    before the fix these three came out as roughly 6x, 2x and 1x of each other.
    `make_solar_days` repeats an identical day, so any residual difference is
    arithmetic, not seasonality — the tolerance is tight on purpose.
    """
    paybacks = []
    for days in (60, 180, 365):
        result = run_analysis(
            make_solar_days(n_days=days),
            make_report(days=days),
            capacities=[5],
            battery_template=TEMPLATE,
            tariff=FLAT_TARIFF,
            battery_cost_per_kwh=600.0,
        )
        payback = result.scenarios[0].payback_years()
        assert payback is not None
        paybacks.append(payback)

    assert max(paybacks) - min(paybacks) < 0.05, (
        f"payback should not depend on how much data was analyzed: {paybacks}"
    )


def test_sensitivity_paybacks_are_annualized_too() -> None:
    """The export-price grid computes its own payback and had the same defect.

    It shares neither the model's method nor its period, so it needs its own
    assertion: a grid whose paybacks were 6x the table's would contradict the very
    table it sits under.
    """
    days = 60
    result = run_analysis(
        make_solar_days(n_days=days),
        make_report(days=days),
        capacities=[5],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        battery_cost_per_kwh=600.0,
    )
    sensitivity = result.export_sensitivity
    assert sensitivity is not None

    configured = [
        p
        for p in sensitivity.for_capacity(5.0)
        if p.export_price_eur_kwh == pytest.approx(FLAT_TARIFF.export_price_eur_kwh)
    ]
    assert configured, "the configured export price must be in the default sweep"

    scenario = result.scenarios[0]
    assert configured[0].payback_years == pytest.approx(scenario.payback_years())
    # And against hand arithmetic, not only against the model it must agree with.
    assert configured[0].payback_years == pytest.approx(
        3000.0 / (configured[0].savings_eur * 365 / days)
    )
