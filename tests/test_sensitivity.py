"""Tests for the export-price sensitivity grid.

The headline invariant — savings are non-increasing as the export price rises —
is checked against the cost equation directly rather than against intuition:

    savings(p) = sum((base_import - sim_import) * p_import)
                 - (base_export - sim_export) * p

The battery can only ever reduce export, so `(base_export - sim_export) >= 0` and
the whole expression is a non-increasing (indeed affine, decreasing) function of p.
"""

import pytest

from battery_worth.analysis import build_export_sensitivity, run_analysis
from battery_worth.models import ScenarioResult, Tariff, TariffKind
from tests.test_analysis import FLAT_TARIFF, TEMPLATE, make_report, make_solar_days


def test_savings_non_increasing_as_export_price_rises() -> None:
    """The core invariant, on every capacity in a real sweep.

    A rising export price makes the energy you keep at home worth less to displace,
    because the alternative — selling it — pays better. Nothing about the physical
    simulation changes; only what the diverted kWh are worth.
    """
    df = make_solar_days()
    result = run_analysis(
        df,
        make_report(),
        capacities=[0, 5, 10, 20],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        export_price_sweep=[0.0, 0.05, 0.10, 0.20, 0.30],
    )
    sensitivity = result.export_sensitivity
    assert sensitivity is not None

    for scenario in result.scenarios:
        row = sensitivity.for_capacity(scenario.capacity_kwh)
        savings = [p.savings_eur for p in row]
        assert savings == sorted(savings, reverse=True), (
            f"savings rose with export price at {scenario.capacity_kwh} kWh: {savings}"
        )


def test_savings_strictly_decrease_when_the_battery_diverts_export() -> None:
    """Non-increasing is the guarantee; strictly decreasing is the real behaviour here.

    Separated from the invariant test on purpose: a scenario that diverts nothing
    would satisfy monotonicity trivially, so this pins that the fixture actually
    exercises the interesting case.
    """
    df = make_solar_days()
    result = run_analysis(
        df,
        make_report(),
        capacities=[10],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        export_price_sweep=[0.05, 0.15],
    )
    scenario = result.scenarios[0]
    assert scenario.baseline_export_kwh > scenario.simulated_export_kwh

    sensitivity = result.export_sensitivity
    assert sensitivity is not None
    cheap, dear = sensitivity.for_capacity(10.0)
    assert cheap.savings_eur > dear.savings_eur


def test_recosting_matches_the_cost_equation_exactly() -> None:
    """Re-costing must reproduce what a full re-simulation would have produced.

    Checked against the closed form rather than against a second `run_analysis`
    call, so this fails if the shortcut ever drifts from the equation it claims
    to implement.
    """
    df = make_solar_days()
    result = run_analysis(
        df,
        make_report(),
        capacities=[10],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        export_price_sweep=[0.30],
    )
    scenario = result.scenarios[0]
    sensitivity = result.export_sensitivity
    assert sensitivity is not None

    diverted = scenario.baseline_export_kwh - scenario.simulated_export_kwh
    expected = scenario.savings_eur - diverted * (0.30 - FLAT_TARIFF.export_price_eur_kwh)

    assert sensitivity.for_capacity(10.0)[0].savings_eur == pytest.approx(expected)


def test_recosting_agrees_with_a_full_rerun_at_that_price() -> None:
    """The end-to-end proof that skipping re-simulation loses nothing.

    Re-runs the whole analysis with the sweep price configured as THE export price
    and checks the re-costed number against it. If greedy self-consumption ever
    became price-aware, this is the test that would catch it.
    """
    df = make_solar_days()
    report = make_report()
    other_price = 0.22

    fast = run_analysis(
        df, report, capacities=[10], battery_template=TEMPLATE, tariff=FLAT_TARIFF,
        export_price_sweep=[other_price],
    )
    rerun_tariff = FLAT_TARIFF.model_copy(update={"export_price_eur_kwh": other_price})
    slow = run_analysis(
        df, report, capacities=[10], battery_template=TEMPLATE, tariff=rerun_tariff
    )

    assert fast.export_sensitivity is not None
    recosted = fast.export_sensitivity.for_capacity(10.0)[0]
    assert recosted.savings_eur == pytest.approx(slow.scenarios[0].savings_eur)


def test_payback_tracks_the_recosted_savings() -> None:
    df = make_solar_days()
    result = run_analysis(
        df,
        make_report(),
        capacities=[10],
        battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
        battery_cost_per_kwh=600.0,
        export_price_sweep=[0.05, 0.15],
    )
    sensitivity = result.export_sensitivity
    assert sensitivity is not None
    cheap, dear = sensitivity.for_capacity(10.0)

    assert cheap.payback_years is not None
    assert dear.payback_years is not None
    # Lower export price -> more savings -> faster payback.
    assert cheap.payback_years < dear.payback_years
    assert cheap.payback_years == pytest.approx(6000.0 / cheap.savings_eur)


def test_no_payback_without_a_battery_cost() -> None:
    df = make_solar_days()
    result = run_analysis(
        df, make_report(), capacities=[10], battery_template=TEMPLATE, tariff=FLAT_TARIFF,
        export_price_sweep=[0.05],
    )
    sensitivity = result.export_sensitivity
    assert sensitivity is not None
    assert sensitivity.for_capacity(10.0)[0].payback_years is None


def test_no_payback_when_recosting_wipes_out_the_savings() -> None:
    """A high enough export price can make a battery worthless; that must read as
    'never', not as a negative or zero payback."""
    scenario = ScenarioResult(
        capacity_kwh=10.0,
        battery_cost_eur=6000.0,
        total_consumption_kwh=1000.0,
        total_pv_kwh=1000.0,
        baseline_import_kwh=500.0,
        baseline_export_kwh=500.0,
        simulated_import_kwh=400.0,
        simulated_export_kwh=400.0,
        battery_cycles=10.0,
        self_consumption_before=0.5,
        self_consumption_after=0.6,
        baseline_cost_eur=100.0,
        simulated_cost_eur=90.0,
    )
    # 100 kWh diverted; at +0.50 EUR/kWh the 10 EUR of savings is far more than erased.
    sensitivity = build_export_sensitivity(
        [scenario], configured_export_price=0.10, export_prices=[0.60]
    )

    point = sensitivity.points[0]
    assert point.savings_eur < 0
    assert point.payback_years is None


def test_baseline_row_is_flat_across_export_prices() -> None:
    """The no-battery row diverts nothing, so re-costing cannot move it off zero."""
    df = make_solar_days()
    result = run_analysis(
        df, make_report(), capacities=[0, 10], battery_template=TEMPLATE, tariff=FLAT_TARIFF,
        export_price_sweep=[0.0, 0.10, 0.50],
    )
    sensitivity = result.export_sensitivity
    assert sensitivity is not None

    for point in sensitivity.for_capacity(0.0):
        assert point.savings_eur == pytest.approx(0.0)


def test_default_sweep_brackets_the_configured_price() -> None:
    """Three points, and the configured price is one of them, so the user's own case
    sits inside the trend rather than beside it."""
    df = make_solar_days()
    result = run_analysis(
        df, make_report(), capacities=[10], battery_template=TEMPLATE, tariff=FLAT_TARIFF
    )
    sensitivity = result.export_sensitivity
    assert sensitivity is not None

    assert sensitivity.export_prices == [0.05, 0.10, 0.15]
    assert sensitivity.baseline_export_price_eur_kwh == pytest.approx(0.10)


def test_default_sweep_steps_absolutely_when_export_price_is_zero() -> None:
    """Scaling by 0.5/1.5 around zero would collapse to a single point at 0."""
    df = make_solar_days()
    free_export = Tariff(
        kind=TariffKind.FLAT, flat_price_eur_kwh=0.30, export_price_eur_kwh=0.0
    )
    result = run_analysis(
        df, make_report(), capacities=[10], battery_template=TEMPLATE, tariff=free_export
    )
    sensitivity = result.export_sensitivity
    assert sensitivity is not None
    assert len(sensitivity.export_prices) == 3
    assert sensitivity.export_prices[0] == pytest.approx(0.0)


def test_at_the_configured_price_savings_match_the_scenario_exactly() -> None:
    """The sweep must not disagree with the table it sits under."""
    df = make_solar_days()
    result = run_analysis(
        df, make_report(), capacities=[5, 10], battery_template=TEMPLATE, tariff=FLAT_TARIFF,
        export_price_sweep=[0.10],
    )
    sensitivity = result.export_sensitivity
    assert sensitivity is not None

    for scenario in result.scenarios:
        point = sensitivity.for_capacity(scenario.capacity_kwh)[0]
        assert point.savings_eur == pytest.approx(scenario.savings_eur)


def test_explicit_prices_deduplicated_and_sorted() -> None:
    df = make_solar_days()
    result = run_analysis(
        df, make_report(), capacities=[10], battery_template=TEMPLATE, tariff=FLAT_TARIFF,
        export_price_sweep=[0.2, 0.05, 0.2, 0.1],
    )
    sensitivity = result.export_sensitivity
    assert sensitivity is not None
    assert sensitivity.export_prices == [0.05, 0.1, 0.2]


def test_empty_sweep_rejected() -> None:
    df = make_solar_days()
    with pytest.raises(ValueError, match="at least one price"):
        run_analysis(
            df, make_report(), capacities=[10], battery_template=TEMPLATE, tariff=FLAT_TARIFF,
            export_price_sweep=[],
        )


def test_negative_export_price_rejected() -> None:
    df = make_solar_days()
    with pytest.raises(ValueError, match="zero or positive"):
        run_analysis(
            df, make_report(), capacities=[10], battery_template=TEMPLATE, tariff=FLAT_TARIFF,
            export_price_sweep=[0.1, -0.05],
        )


def test_monotonicity_holds_under_banded_prices() -> None:
    """The invariant comes from the strategy, not from a flat import price."""
    df = make_solar_days()
    banded = Tariff(
        kind=TariffKind.F1_F2_F3, f1_price=0.35, f2_price=0.30, f3_price=0.25,
        export_price_eur_kwh=0.10,
    )
    result = run_analysis(
        df, make_report(), capacities=[5, 15], battery_template=TEMPLATE, tariff=banded,
        export_price_sweep=[0.0, 0.1, 0.2, 0.4],
    )
    sensitivity = result.export_sensitivity
    assert sensitivity is not None

    for scenario in result.scenarios:
        savings = [p.savings_eur for p in sensitivity.for_capacity(scenario.capacity_kwh)]
        assert savings == sorted(savings, reverse=True)
