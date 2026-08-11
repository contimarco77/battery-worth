"""Tests for the seasonal breakdown.

The seasonal section is the one a reader uses to understand *why* their payback is
what it is, so the aggregates must reconcile exactly with the scenario totals they
are a decomposition of — a section that quietly disagrees with the table above it
is worse than no section.
"""

import numpy as np
import pandas as pd
import pytest

from battery_worth.analysis import recommended_scenario, run_analysis
from battery_worth.models import BatterySpec, Tariff, TariffKind
from tests.test_analysis import FLAT_TARIFF, TEMPLATE, make_report, make_solar_days


def make_year(pv_summer: float = 5.0, pv_winter: float = 1.0) -> pd.DataFrame:
    """A full year whose PV swings hard between summer and winter.

    Deliberately seasonal: the whole point of the section under test is to show a
    difference between months, so a flat synthetic year would test nothing.
    """
    idx = pd.date_range("2025-01-01", periods=365 * 24, freq="h", tz="Europe/Rome")
    hour = np.asarray(idx.hour)
    month = np.asarray(idx.month)

    # Peak in July, trough in January.
    seasonal = (pv_summer - pv_winter) / 2 * (1 - np.cos((month - 1) / 12 * 2 * np.pi))
    amplitude = pv_winter + seasonal
    pv = np.where((hour >= 9) & (hour < 16), amplitude, 0.0)
    load = np.full(len(idx), 1.0)

    net = load - pv
    return pd.DataFrame(
        {
            "grid_import": np.clip(net, 0.0, None),
            "grid_export": np.clip(-net, 0.0, None),
            "pv_production": pv,
        },
        index=idx,
    )


def test_buckets_sum_to_the_scenario_totals() -> None:
    """Every energy aggregate is a partition of the reference scenario's totals."""
    df = make_year()
    result = run_analysis(
        df, make_report(), capacities=[0, 10], battery_template=TEMPLATE, tariff=FLAT_TARIFF
    )
    seasonal = result.seasonal
    assert seasonal is not None

    reference = next(s for s in result.scenarios if s.capacity_kwh == seasonal.capacity_kwh)
    buckets = seasonal.buckets

    assert sum(b.pv_kwh for b in buckets) == pytest.approx(reference.total_pv_kwh)
    assert sum(b.baseline_import_kwh for b in buckets) == pytest.approx(
        reference.baseline_import_kwh
    )
    assert sum(b.baseline_export_kwh for b in buckets) == pytest.approx(
        reference.baseline_export_kwh
    )
    assert sum(b.simulated_import_kwh for b in buckets) == pytest.approx(
        reference.simulated_import_kwh
    )
    assert sum(b.simulated_export_kwh for b in buckets) == pytest.approx(
        reference.simulated_export_kwh
    )
    assert sum(b.savings_eur for b in buckets) == pytest.approx(reference.savings_eur)
    assert sum(b.days for b in buckets) == 365


def test_reference_capacity_is_the_recommended_one() -> None:
    """The section must describe the battery the Verdict recommends, not the biggest.

    Tying it to the largest swept capacity made the report describe two different
    batteries: a Verdict recommending 5 kWh above a seasonal table showing 20 kWh.
    """
    df = make_year()
    result = run_analysis(
        df, make_report(), capacities=[0, 5, 10, 20], battery_template=TEMPLATE,
        tariff=FLAT_TARIFF, battery_cost_per_kwh=600.0,
    )
    seasonal = result.seasonal
    assert seasonal is not None

    recommended = recommended_scenario(result.scenarios)
    assert recommended is not None
    assert seasonal.capacity_kwh == recommended.capacity_kwh
    # The premise of the fix: on this sweep the two genuinely differ.
    assert seasonal.capacity_kwh != max(s.capacity_kwh for s in result.scenarios)


def test_ceiling_is_carried_alongside_the_recommended_capacity() -> None:
    """The largest capacity's unused surplus survives as a figure, not as the framing."""
    df = make_year()
    result = run_analysis(
        df, make_report(), capacities=[0, 5, 10, 20], battery_template=TEMPLATE,
        tariff=FLAT_TARIFF, battery_cost_per_kwh=600.0,
    )
    seasonal = result.seasonal
    assert seasonal is not None

    assert seasonal.largest_capacity_kwh == 20.0
    assert not seasonal.is_ceiling

    largest = next(s for s in result.scenarios if s.capacity_kwh == 20.0)
    assert seasonal.largest_capacity_unused_surplus_kwh == pytest.approx(
        largest.simulated_export_kwh
    )
    # A bigger battery stores strictly more, so it leaves strictly less unused.
    assert seasonal.largest_capacity_unused_surplus_kwh < seasonal.unused_surplus_kwh


def test_ceiling_flag_is_set_when_the_recommendation_is_the_largest() -> None:
    """Nothing to add when the recommended battery *is* the biggest one swept."""
    df = make_year()
    result = run_analysis(
        df, make_report(), capacities=[0, 10], battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
    )
    seasonal = result.seasonal
    assert seasonal is not None

    assert seasonal.capacity_kwh == 10.0
    assert seasonal.is_ceiling
    assert seasonal.largest_capacity_unused_surplus_kwh == pytest.approx(
        seasonal.unused_surplus_kwh
    )


def test_falls_back_to_the_largest_when_nothing_is_recommended() -> None:
    """No cost and no positive savings means no recommendation; still show a real battery."""
    df = make_year()
    free_export = Tariff(
        kind=TariffKind.FLAT, flat_price_eur_kwh=0.25, export_price_eur_kwh=0.25
    )
    result = run_analysis(
        df, make_report(), capacities=[0, 5, 10], battery_template=TEMPLATE,
        tariff=free_export,
    )
    seasonal = result.seasonal
    assert seasonal is not None

    assert recommended_scenario(result.scenarios) is None
    assert seasonal.capacity_kwh == 10.0


def test_monthly_granularity_for_a_full_year() -> None:
    df = make_year()
    result = run_analysis(
        df, make_report(), capacities=[10], battery_template=TEMPLATE, tariff=FLAT_TARIFF
    )
    seasonal = result.seasonal
    assert seasonal is not None
    assert seasonal.granularity == "month"
    assert len(seasonal.buckets) == 12
    assert seasonal.buckets[0].label == "2025-01"
    assert seasonal.buckets[-1].label == "2025-12"


def test_short_period_falls_back_to_seasons() -> None:
    """Three months of data as three monthly rows reads as noise; bucket it coarsely."""
    df = make_solar_days(n_days=60)
    result = run_analysis(
        df, make_report(days=60), capacities=[10], battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
    )
    seasonal = result.seasonal
    assert seasonal is not None
    assert seasonal.granularity == "season"


def test_buckets_are_chronologically_ordered_across_a_year_boundary() -> None:
    """The Ausgrid fixture runs July->June, so ordering must not reset at new year."""
    idx = pd.date_range("2024-11-01", periods=180 * 24, freq="h", tz="Europe/Rome")
    df = pd.DataFrame(
        {
            "grid_import": np.full(len(idx), 1.0),
            "grid_export": np.full(len(idx), 1.0),
            "pv_production": np.full(len(idx), 2.0),
        },
        index=idx,
    )
    result = run_analysis(
        df, make_report(days=180), capacities=[10], battery_template=TEMPLATE,
        tariff=FLAT_TARIFF,
    )
    seasonal = result.seasonal
    assert seasonal is not None

    labels = [b.label for b in seasonal.buckets]
    assert labels == sorted(labels), f"buckets out of chronological order: {labels}"
    assert labels[0].startswith("2024-11")


def test_summer_wastes_more_surplus_than_winter() -> None:
    """The narrative the section exists to support, asserted rather than assumed."""
    df = make_year()
    result = run_analysis(
        df, make_report(), capacities=[5], battery_template=TEMPLATE, tariff=FLAT_TARIFF
    )
    seasonal = result.seasonal
    assert seasonal is not None

    by_label = {b.label: b for b in seasonal.buckets}
    july = by_label["2025-07"]
    january = by_label["2025-01"]

    assert july.unused_surplus_kwh > january.unused_surplus_kwh
    assert january.uncovered_deficit_kwh > july.uncovered_deficit_kwh


def test_unused_surplus_and_uncovered_deficit_are_the_simulated_flows() -> None:
    """These two properties are the section's headline numbers; pin their meaning."""
    df = make_year()
    result = run_analysis(
        df, make_report(), capacities=[10], battery_template=TEMPLATE, tariff=FLAT_TARIFF
    )
    seasonal = result.seasonal
    assert seasonal is not None

    for bucket in seasonal.buckets:
        assert bucket.unused_surplus_kwh == bucket.simulated_export_kwh
        assert bucket.uncovered_deficit_kwh == bucket.simulated_import_kwh


def test_self_consumption_never_falls_with_a_battery() -> None:
    df = make_year()
    result = run_analysis(
        df, make_report(), capacities=[10], battery_template=TEMPLATE, tariff=FLAT_TARIFF
    )
    seasonal = result.seasonal
    assert seasonal is not None

    for bucket in seasonal.buckets:
        assert bucket.self_consumption_after >= bucket.self_consumption_before


def test_no_seasonal_section_without_a_simulated_capacity() -> None:
    """A baseline-only sweep has no battery to describe seasonally."""
    df = make_solar_days()
    result = run_analysis(
        df, make_report(), capacities=[0], battery_template=TEMPLATE, tariff=FLAT_TARIFF
    )
    assert result.seasonal is None


def test_seasonal_savings_respect_banded_prices() -> None:
    """Bucket costing must use the same per-interval price series as the scenario total."""
    df = make_year()
    banded = Tariff(
        kind=TariffKind.F1_F2_F3, f1_price=0.35, f2_price=0.30, f3_price=0.25,
        export_price_eur_kwh=0.10,
    )
    result = run_analysis(
        df, make_report(), capacities=[10], battery_template=TEMPLATE, tariff=banded
    )
    seasonal = result.seasonal
    assert seasonal is not None

    assert sum(b.savings_eur for b in seasonal.buckets) == pytest.approx(
        result.scenarios[0].savings_eur
    )


def test_lossy_battery_leaves_more_surplus_unused() -> None:
    df = make_year()
    lossy = BatterySpec(
        usable_capacity_kwh=1.0, max_charge_kw=5.0, max_discharge_kw=5.0,
        round_trip_efficiency=0.64,
    )
    lossless = run_analysis(
        df, make_report(), capacities=[10], battery_template=TEMPLATE, tariff=FLAT_TARIFF
    )
    inefficient = run_analysis(
        df, make_report(), capacities=[10], battery_template=lossy, tariff=FLAT_TARIFF
    )

    assert lossless.seasonal is not None
    assert inefficient.seasonal is not None
    assert sum(b.savings_eur for b in inefficient.seasonal.buckets) < sum(
        b.savings_eur for b in lossless.seasonal.buckets
    )
