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

from typing import cast

import pandas as pd

from battery_worth.models import (
    AnalysisResult,
    BatterySpec,
    ExportPricePoint,
    ExportPriceSensitivity,
    IngestReport,
    ScenarioResult,
    SeasonalAnalysis,
    SeasonalBucket,
    Tariff,
    annualization_years,
)
from battery_worth.simulator import simulate_battery, summarize_scenario
from battery_worth.tariffs import build_price_series

_ZERO_CAPACITY_TOLERANCE = 1e-9
_MIN_POINTS_FOR_STEP = 2

# Below this many months, per-month buckets read as noise rather than seasonality;
# at or above it, months are more informative than four coarse seasons.
_MIN_MONTHS_FOR_MONTHLY = 4
_DEFAULT_SENSITIVITY_FACTORS = (0.5, 1.0, 1.5)


def run_analysis(  # noqa: PLR0913, PLR0917 - the sweep genuinely needs all six inputs
    df: pd.DataFrame,
    ingest_report: IngestReport,
    capacities: list[float],
    battery_template: BatterySpec,
    tariff: Tariff,
    battery_cost_per_kwh: float | None = None,
    export_price_sweep: list[float] | None = None,
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

    `export_price_sweep` re-costs the finished scenarios at other export
    remuneration prices; it never re-runs the simulation (see
    `build_export_sensitivity` for why that is exact, not an approximation).
    Defaults to three points bracketing the configured export price.
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
    # Kept so the seasonal breakdown can aggregate an already-simulated frame
    # instead of running the simulation a second time for its reference capacity.
    simulated_frames: dict[float, pd.DataFrame] = {}
    for capacity in unique_capacities:
        if capacity <= _ZERO_CAPACITY_TOLERANCE:
            scenarios.append(
                _baseline_scenario(
                    df,
                    import_prices,
                    tariff.export_price_eur_kwh,
                    days_analyzed=ingest_report.days_analyzed,
                )
            )
            continue

        spec = battery_template.model_copy(update={"usable_capacity_kwh": capacity})
        sim_df = simulate_battery(df, spec, interval_hours=interval_hours)
        simulated_frames[capacity] = sim_df
        cost = None if battery_cost_per_kwh is None else battery_cost_per_kwh * capacity
        scenarios.append(
            summarize_scenario(
                sim_df,
                spec,
                import_prices,
                export_price=tariff.export_price_eur_kwh,
                battery_cost_eur=cost,
                # From the ingest report, not counted off the frame: it is the
                # authoritative period length, and every scenario in one sweep must
                # annualize against the same number.
                days_analyzed=ingest_report.days_analyzed,
            )
        )

    sensitivity = build_export_sensitivity(
        scenarios,
        configured_export_price=tariff.export_price_eur_kwh,
        export_prices=export_price_sweep,
    )
    seasonal = _build_seasonal(
        simulated_frames,
        import_prices,
        tariff.export_price_eur_kwh,
        recommended=recommended_scenario(scenarios),
    )

    return AnalysisResult(
        scenarios=scenarios,
        period_start=ingest_report.period_start,
        period_end=ingest_report.period_end,
        days_analyzed=ingest_report.days_analyzed,
        resolution_minutes=ingest_report.native_resolution_minutes,
        seasonality_warning=ingest_report.seasonality_warning,
        export_sensitivity=sensitivity,
        seasonal=seasonal,
    )


def recommended_scenario(scenarios: list[ScenarioResult]) -> ScenarioResult | None:
    """The scenario the report recommends: shortest payback, else largest savings.

    Shortest payback rather than largest savings, because the largest battery
    almost always saves the most in absolute terms while being the worse
    investment — that gap is the entire point of the comparison table.

    Defined here, in the engine, rather than in each consumer: the Verdict, the
    terminal summary and the seasonal breakdown must all name the *same* battery.
    When they disagreed, the report described two different batteries in adjacent
    sections and left the reader to guess which one was theirs.
    """
    priced = [(s.payback_years(), s) for s in scenarios]
    with_payback = [(p, s) for p, s in priced if p is not None]
    if with_payback:
        return min(with_payback, key=lambda pair: pair[0])[1]
    earning = [s for s in scenarios if s.savings_eur > 0]
    if not earning:
        return None
    return max(earning, key=lambda s: s.savings_eur)


def build_export_sensitivity(
    scenarios: list[ScenarioResult],
    configured_export_price: float,
    export_prices: list[float] | None = None,
) -> ExportPriceSensitivity:
    """Re-cost finished scenarios at a range of export prices. No re-simulation.

    This is exact rather than an approximation, and the reason is worth stating:
    the greedy self-consumption strategy never looks at a price. It charges from
    whatever surplus exists and discharges into whatever deficit exists, so the
    energy flows — and therefore every kWh figure in a `ScenarioResult` — are
    identical at every export price. Only the costing changes, and it changes
    linearly:

        cost(p') = cost(p) + exported_kwh * (p - p')

    for both the baseline and the simulated leg. Subtracting the two legs gives

        savings(p') = savings(p) - (baseline_export - simulated_export) * (p' - p)

    which is where the invariant comes from: a battery can only ever *reduce*
    export (`simulated_export <= baseline_export`), so the bracket is non-negative
    and savings are **non-increasing** as the export price rises. Economically:
    the battery's value is the spread it captures by keeping a kWh at home rather
    than selling it, and a better feed-in tariff shrinks that spread.

    Would this still hold under a price-aware strategy? No — and that is precisely
    why re-simulation would be required in v2 when tariff arbitrage arrives.
    """
    prices = _resolve_export_prices(export_prices, configured_export_price)

    points: list[ExportPricePoint] = []
    for scenario in scenarios:
        diverted = scenario.baseline_export_kwh - scenario.simulated_export_kwh
        for price in prices:
            savings = scenario.savings_eur - diverted * (price - configured_export_price)
            points.append(
                ExportPricePoint(
                    capacity_kwh=scenario.capacity_kwh,
                    export_price_eur_kwh=price,
                    savings_eur=savings,
                    payback_years=_payback(
                        scenario.battery_cost_eur, savings, scenario.days_analyzed
                    ),
                )
            )

    return ExportPriceSensitivity(
        export_prices=prices,
        baseline_export_price_eur_kwh=configured_export_price,
        points=points,
    )


def _resolve_export_prices(requested: list[float] | None, configured: float) -> list[float]:
    """Pick the price points: the user's list, or three bracketing the configured price.

    The default deliberately includes the configured price itself, so the grid
    always contains the row the rest of the report is built on and the user can
    see their own case sitting inside the trend rather than beside it.
    """
    if requested is not None:
        if not requested:
            msg = "Export price sweep is empty: pass at least one price in EUR/kWh."
            raise ValueError(msg)
        if any(p < 0 for p in requested):
            negative = sorted({p for p in requested if p < 0})
            msg = f"Export prices must be zero or positive, got: {negative}."
            raise ValueError(msg)
        return sorted({float(p) for p in requested})

    # A zero configured price has no meaningful "half" or "one and a half"; step
    # around it in absolute terms instead so the sweep still shows the trend.
    if configured <= _ZERO_CAPACITY_TOLERANCE:
        return [0.0, 0.05, 0.10]
    return sorted({round(configured * f, 6) for f in _DEFAULT_SENSITIVITY_FACTORS})


def _payback(
    battery_cost_eur: float | None, savings_eur: float, days_analyzed: int
) -> float | None:
    """Naive payback, matching `ScenarioResult.payback_years` exactly.

    `savings_eur` here is a **period** total, exactly like the model's, so it is
    annualized before the division for the same reason: dividing a cost by a period
    saving yields years only when the period is a year. Kept consistent with the
    model's other rule too — no cost or no positive savings means no payback, never
    a zero or a negative number that would read as a good result.
    """
    if battery_cost_eur is None or savings_eur <= 0:
        return None
    return battery_cost_eur / (savings_eur / annualization_years(days_analyzed))


def _build_seasonal(
    simulated_frames: dict[float, pd.DataFrame],
    import_prices: pd.Series,
    export_price: float,
    recommended: ScenarioResult | None,
) -> SeasonalAnalysis | None:
    """Break the analyzed period down for ONE reference capacity: the recommended one.

    The reference used to be the largest swept capacity, which made the section
    describe a battery the reader was not being recommended — a 20 kWh unit at
    94-100% self-consumption sitting directly under a Verdict recommending 5 kWh
    at 59%. A reader skimming takes the table as their result, so the two must
    name the same battery.

    The ceiling that the largest capacity expressed is kept, but as a single
    figure (`ceiling_*`) rather than as the framing of the whole section: it is
    genuinely useful to know how much surplus even the biggest swept battery
    would have left unused, and it is the honest home for the "no battery in this
    sweep could have used it" claim, which stops being true of a smaller unit.

    A sweep with no positive capacity (baseline only) has no seasonal story to
    tell and returns None.
    """
    if not simulated_frames:
        return None

    # The recommended capacity may be the baseline (capacity 0, never simulated) or
    # absent entirely when nothing saved money; fall back to the largest simulated
    # frame so the section still describes a real battery rather than vanishing.
    capacity = max(simulated_frames)
    if recommended is not None and recommended.capacity_kwh in simulated_frames:
        capacity = recommended.capacity_kwh
    sim_df = simulated_frames[capacity]

    ceiling_capacity = max(simulated_frames)
    ceiling_surplus = float(simulated_frames[ceiling_capacity]["sim_export"].sum())

    index = pd.DatetimeIndex(sim_df.index)
    months = index.year * 12 + index.month
    granularity = "month" if months.nunique() >= _MIN_MONTHS_FOR_MONTHLY else "season"

    keys, labels = _bucket_keys(index, granularity)
    frame = sim_df.assign(
        _key=keys,
        _label=labels,
        _day=index.normalize(),
        _import_cost=sim_df["grid_import"] * import_prices,
        _sim_import_cost=sim_df["sim_import"] * import_prices,
    )

    buckets: list[SeasonalBucket] = []
    for key, group in frame.groupby("_key", sort=True):
        # `_key` is built by this module and is always an integer (year*12+month, or
        # the quarter ordinal); pandas types a groupby label as the union of every
        # label type it could produce, so the narrowing has to be stated explicitly.
        buckets.append(
            _seasonal_bucket(group, sort_key=cast("int", key), export_price=export_price)
        )

    return SeasonalAnalysis(
        capacity_kwh=capacity,
        granularity=granularity,
        buckets=buckets,
        largest_capacity_kwh=ceiling_capacity,
        largest_capacity_unused_surplus_kwh=ceiling_surplus,
    )


def _bucket_keys(index: pd.DatetimeIndex, granularity: str) -> tuple[pd.Index, pd.Index]:
    """Sortable key + display label per row, for month or meteorological-season buckets.

    Seasons are labelled by hemisphere-neutral month ranges rather than by name:
    the fixture is Australian, and calling June "Summer" for a Sydney household
    would be a factual error printed in a report whose whole selling point is that
    its numbers are real.
    """
    if granularity == "month":
        keys = pd.Index(index.year * 12 + index.month)
        labels = pd.Index(index.strftime("%Y-%m"))
        return keys, labels

    # Meteorological quarters: Dec-Feb, Mar-May, Jun-Aug, Sep-Nov.
    quarter = pd.Index((index.month % 12) // 3)
    names = {0: "Dec-Feb", 1: "Mar-May", 2: "Jun-Aug", 3: "Sep-Nov"}
    return quarter, pd.Index([names[q] for q in quarter])


def _seasonal_bucket(group: pd.DataFrame, sort_key: int, export_price: float) -> SeasonalBucket:
    """Aggregate one bucket, costing it exactly as `summarize_scenario` costs the whole period."""
    pv = float(group["pv_production"].sum())
    baseline_import = float(group["grid_import"].sum())
    baseline_export = float(group["grid_export"].sum())
    sim_import = float(group["sim_import"].sum())
    sim_export = float(group["sim_export"].sum())

    baseline_cost = float(group["_import_cost"].sum()) - baseline_export * export_price
    sim_cost = float(group["_sim_import_cost"].sum()) - sim_export * export_price

    sc_before = (pv - baseline_export) / pv if pv > 0 else 0.0
    sc_after = (pv - sim_export) / pv if pv > 0 else 0.0

    return SeasonalBucket(
        label=str(group["_label"].iloc[0]),
        sort_key=sort_key,
        days=int(group["_day"].nunique()),
        pv_kwh=pv,
        consumption_kwh=baseline_import + pv - baseline_export,
        baseline_import_kwh=baseline_import,
        baseline_export_kwh=baseline_export,
        simulated_import_kwh=sim_import,
        simulated_export_kwh=sim_export,
        self_consumption_before=max(0.0, min(1.0, sc_before)),
        self_consumption_after=max(0.0, min(1.0, sc_after)),
        savings_eur=baseline_cost - sim_cost,
    )


def _baseline_scenario(
    df: pd.DataFrame,
    import_prices: pd.Series,
    export_price: float,
    days_analyzed: int,
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
        days_analyzed=days_analyzed,
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
