"""Orchestration: sweep a set of capacities and assemble the full AnalysisResult.

This is the layer that ties ingest -> tariffs -> simulator together. It owns two
decisions worth stating:

- The price series is built ONCE, outside the sweep. It depends only on the
  analysis index and the tariff, never on the battery, so rebuilding it per
  capacity would be both wasteful and a chance for the scenarios to be priced
  inconsistently.
- Capacity 0 is a real, first-class scenario (the "do nothing" baseline) but it
  is NOT simulated: `BatterySpec` requires a positive capacity, and a zero-capacity
  battery has no cycles and no payback. It is constructed directly instead, with
  simulated == baseline by definition.

No LLM calls anywhere in this module.
"""

from __future__ import annotations

import pandas as pd

from battery_worth.models import (
    AnalysisResult,
    BatterySpec,
    IngestReport,
    ScenarioResult,
    Tariff,
)
from battery_worth.simulator import simulate_battery, summarize_scenario
from battery_worth.tariffs import build_price_series

_ZERO_CAPACITY_TOLERANCE = 1e-9
_MIN_POINTS_FOR_STEP = 2


def run_analysis(  # noqa: PLR0913, PLR0917 - the sweep genuinely needs all six inputs
    df: pd.DataFrame,
    ingest_report: IngestReport,
    capacities: list[float],
    battery_template: BatterySpec,
    tariff: Tariff,
    battery_cost_per_kwh: float | None = None,
) -> AnalysisResult:
    """Simulate every requested capacity against one tariff and collect the results.

    `battery_template` supplies the parameters that do NOT vary across the sweep
    (charge/discharge power, round-trip efficiency, min SOC); its own
    `usable_capacity_kwh` is ignored, since capacity is exactly what is being swept.

    A capacity of 0 is accepted and produces the untouched baseline scenario, which
    makes the comparison table self-explanatory: the first row is the user's actual
    situation. It carries no battery cost and therefore no payback.

    Capacities are deduplicated and sorted, so the resulting table always reads
    from smallest to largest regardless of the order they were requested in.
    """
    if not capacities:
        msg = "No capacities to analyze: pass at least one capacity in kWh."
        raise ValueError(msg)
    if any(c < 0 for c in capacities):
        negative = sorted({c for c in capacities if c < 0})
        msg = f"Capacities must be zero or positive, got: {negative}."
        raise ValueError(msg)
    if df.empty:
        msg = "Cannot run the analysis on an empty dataset."
        raise ValueError(msg)

    index = pd.DatetimeIndex(df.index)
    # Built once: the price of an hour does not depend on the battery in front of it.
    import_prices = build_price_series(index, tariff)
    interval_hours = _interval_hours(index)

    unique_capacities = sorted({float(c) for c in capacities})

    scenarios: list[ScenarioResult] = []
    for capacity in unique_capacities:
        if capacity <= _ZERO_CAPACITY_TOLERANCE:
            scenarios.append(
                _baseline_scenario(df, import_prices, tariff.export_price_eur_kwh)
            )
            continue

        spec = battery_template.model_copy(update={"usable_capacity_kwh": capacity})
        sim_df = simulate_battery(df, spec, interval_hours=interval_hours)
        cost = None if battery_cost_per_kwh is None else battery_cost_per_kwh * capacity
        scenarios.append(
            summarize_scenario(
                sim_df,
                spec,
                import_prices,
                export_price=tariff.export_price_eur_kwh,
                battery_cost_eur=cost,
            )
        )

    return AnalysisResult(
        scenarios=scenarios,
        period_start=ingest_report.period_start,
        period_end=ingest_report.period_end,
        days_analyzed=ingest_report.days_analyzed,
        resolution_minutes=ingest_report.native_resolution_minutes,
        seasonality_warning=ingest_report.seasonality_warning,
    )


def _baseline_scenario(
    df: pd.DataFrame, import_prices: pd.Series, export_price: float
) -> ScenarioResult:
    """The no-battery scenario, built directly rather than simulated.

    Everything is the baseline by definition: simulated flows equal baseline flows,
    the cost is the cost the user actually paid, and there are no cycles. Crucially
    `battery_cost_eur` stays None, so `payback_years()` returns None instead of a
    meaningless 0/0 — a "0.0 year payback" row would be the single most misleading
    number the table could print.
    """
    total_pv = float(df["pv_production"].sum())
    baseline_import = float(df["grid_import"].sum())
    baseline_export = float(df["grid_export"].sum())
    consumption = baseline_import + total_pv - baseline_export

    sc_before = (total_pv - baseline_export) / total_pv if total_pv > 0 else 0.0
    baseline_cost = float(
        (df["grid_import"] * import_prices).sum() - baseline_export * export_price
    )

    clamped = max(0.0, min(1.0, sc_before))
    return ScenarioResult(
        capacity_kwh=0.0,
        battery_cost_eur=None,
        total_consumption_kwh=consumption,
        total_pv_kwh=total_pv,
        baseline_import_kwh=baseline_import,
        baseline_export_kwh=baseline_export,
        simulated_import_kwh=baseline_import,
        simulated_export_kwh=baseline_export,
        battery_cycles=0.0,
        self_consumption_before=clamped,
        self_consumption_after=clamped,
        baseline_cost_eur=baseline_cost,
        simulated_cost_eur=baseline_cost,
    )


def _interval_hours(index: pd.DatetimeIndex) -> float:
    """Length of one row, in hours, used to convert battery kW limits into kWh per step.

    `ingest` resamples to hourly, so this is 1.0 on the normal path; it is inferred
    rather than hardcoded so that a caller feeding the simulator a differently-spaced
    frame (tests, or a future native-resolution mode) still gets correct power limits.
    """
    if len(index) < _MIN_POINTS_FOR_STEP:
        return 1.0
    deltas = index.to_series().diff().dropna()
    if deltas.empty:
        return 1.0
    step = deltas.median().total_seconds() / 3600.0
    return float(step) if step > 0 else 1.0
