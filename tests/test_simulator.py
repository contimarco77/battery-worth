"""Hand-verifiable tests for the greedy simulator.

Every expected value here can be checked on paper — this is deliberate:
the community will judge the engine, so the tests must be transparent.
"""

import pandas as pd
import pytest

from battery_worth.models import BatterySpec
from battery_worth.simulator import simulate_battery, summarize_scenario


def make_df(imports: list[float], exports: list[float], pv: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2025-06-01", periods=len(imports), freq="h")
    return pd.DataFrame(
        {"grid_import": imports, "grid_export": exports, "pv_production": pv}, index=idx
    )


def test_perfect_shift_lossless() -> None:
    """With 100% efficiency, 2 kWh exported at noon fully covers 2 kWh imported at night."""
    df = make_df(imports=[0.0, 0.0, 1.0, 1.0], exports=[1.0, 1.0, 0.0, 0.0], pv=[1.0, 1.0, 0.0, 0.0])
    spec = BatterySpec(usable_capacity_kwh=10, round_trip_efficiency=1.0)
    out = simulate_battery(df, spec)

    assert out["sim_export"].sum() == pytest.approx(0.0)
    assert out["sim_import"].sum() == pytest.approx(0.0)


def test_capacity_limits_charge() -> None:
    """A 1 kWh battery can only absorb 1 kWh of a 3 kWh surplus (at 100% efficiency)."""
    df = make_df(imports=[0.0, 2.0], exports=[3.0, 0.0], pv=[3.0, 0.0])
    spec = BatterySpec(usable_capacity_kwh=1, round_trip_efficiency=1.0)
    out = simulate_battery(df, spec)

    assert out["sim_export"].iloc[0] == pytest.approx(2.0)  # 3 - 1 absorbed
    assert out["sim_import"].iloc[1] == pytest.approx(1.0)  # 2 - 1 delivered


def test_round_trip_efficiency_accounting() -> None:
    """Charging 1 kWh of surplus at 81% round-trip delivers 0.81 kWh to the house.

    one-way eff = sqrt(0.81) = 0.9: 1 kWh surplus -> 0.9 kWh stored -> 0.81 kWh delivered.
    """
    df = make_df(imports=[0.0, 2.0], exports=[1.0, 0.0], pv=[1.0, 0.0])
    spec = BatterySpec(usable_capacity_kwh=10, round_trip_efficiency=0.81)
    out = simulate_battery(df, spec)

    assert out["sim_export"].iloc[0] == pytest.approx(0.0)
    assert out["sim_import"].iloc[1] == pytest.approx(2.0 - 0.81)


def test_summary_self_consumption_and_savings() -> None:
    """Flat price 0.30, export 0.10, perfect shift of 2 kWh: savings = 2*(0.30-0.10)... wait —

    baseline cost = 2 kWh import * 0.30 - 2 kWh export * 0.10 = 0.40
    simulated cost = 0 import - 0 export = 0.00
    savings = 0.40 EUR. Self-consumption: before (2-2)/2 = 0%, after (2-0)/2 = 100%.
    """
    df = make_df(imports=[0.0, 0.0, 1.0, 1.0], exports=[1.0, 1.0, 0.0, 0.0], pv=[1.0, 1.0, 0.0, 0.0])
    spec = BatterySpec(usable_capacity_kwh=10, round_trip_efficiency=1.0)
    out = simulate_battery(df, spec)
    prices = pd.Series(0.30, index=df.index)

    result = summarize_scenario(out, spec, prices, export_price=0.10)

    assert result.self_consumption_before == pytest.approx(0.0)
    assert result.self_consumption_after == pytest.approx(1.0)
    assert result.baseline_cost_eur == pytest.approx(0.40)
    assert result.simulated_cost_eur == pytest.approx(0.0)
    assert result.savings_eur == pytest.approx(0.40)


def test_min_soc_reserved() -> None:
    """With min_soc=0.5 on a 2 kWh battery, only 1 kWh is usable for discharge."""
    df = make_df(imports=[0.0, 3.0], exports=[3.0, 0.0], pv=[3.0, 0.0])
    spec = BatterySpec(usable_capacity_kwh=2, round_trip_efficiency=1.0, min_soc=0.5)
    out = simulate_battery(df, spec)

    # Battery starts at floor (1 kWh), charges to full (2 kWh), can discharge 1 kWh
    assert out["sim_import"].iloc[1] == pytest.approx(2.0)
