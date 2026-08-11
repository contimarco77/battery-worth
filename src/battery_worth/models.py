"""Core domain models for battery-worth.

All models are pydantic v2, strict where it matters. The simulation engine
(simulator.py) is pure pandas and deterministic; these models define its
inputs and outputs.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

# 365, not the 365.25 of a mean Julian year. On a dataset of exactly one year the
# Julian constant scales every figure by 0.07% — a correction that buys nothing and
# breaks the one property this tool sells: that a user who sums their own CSV in a
# spreadsheet finds the report's number. With 365, a full year is the identity.
#
# It lives here, in the domain layer, rather than in `report.py`, because
# annualization is not a formatting concern: `payback_years()` needs it to produce a
# correct number at all, and a presentation module cannot be a dependency of the
# model it presents.
DAYS_PER_YEAR = 365.0


def annualization_years(days: int) -> float:
    """The divisor that turns a whole-period total into a per-year figure.

    Exactly 365 days returns exactly 1.0, so on a full year every annualized figure
    equals the sum of the user's own input column.
    """
    return max(days / DAYS_PER_YEAR, 1e-9)


class ColumnMapping(BaseModel):
    """Maps user CSV columns to the source energy series (kWh per interval).

    Two mutually exclusive schemas are supported:
    - grid-centric: grid_import + grid_export + pv_production (Home Assistant style)
    - meter-centric: consumption + pv_production (gross metering, net = consumption - pv)
    Exactly one complete schema must be present.
    """

    timestamp: str = Field(description="Column with ISO timestamps (local time)")
    pv_production: str = Field(description="PV production, kWh per interval")
    grid_import: str | None = Field(
        default=None, description="Energy imported from grid, kWh per interval"
    )
    grid_export: str | None = Field(
        default=None, description="Energy exported to grid, kWh per interval"
    )
    consumption: str | None = Field(
        default=None, description="Total home consumption (gross), kWh per interval"
    )

    @model_validator(mode="after")
    def _check_one_complete_schema(self) -> ColumnMapping:
        any_grid = self.grid_import is not None or self.grid_export is not None
        grid_centric = self.grid_import is not None and self.grid_export is not None
        meter_centric = self.consumption is not None
        if any_grid and meter_centric:
            msg = (
                "ColumnMapping is ambiguous: both grid columns (grid_import/grid_export) "
                "and consumption are set. Provide either grid_import+grid_export "
                "(grid-centric) or consumption (meter-centric), not a mix."
            )
            raise ValueError(msg)
        if not grid_centric and not meter_centric:
            msg = (
                "ColumnMapping is incomplete: provide either grid_import+grid_export "
                "(grid-centric schema) or consumption (meter-centric schema), "
                "alongside pv_production."
            )
            raise ValueError(msg)
        return self

    @property
    def schema_kind(self) -> str:
        """Which of the two supported input schemas this mapping uses."""
        return "grid_centric" if self.grid_import is not None else "meter_centric"


class IngestReport(BaseModel):
    """Data-quality metadata produced while loading and validating a user CSV."""

    period_start: str
    period_end: str
    days_analyzed: int
    native_resolution_minutes: int
    schema_used: str = Field(description="'grid_centric' or 'meter_centric'")
    cumulative_columns: list[str] = Field(
        default_factory=list, description="Source columns detected as cumulative meter readings"
    )
    gaps_count: int = 0
    gaps_total_hours: float = 0.0
    negative_values_clipped: int = 0
    seasonality_warning: bool = Field(
        description="True when < 365 days of data: results may not capture seasonality"
    )
    warnings: list[str] = Field(default_factory=list)


class BatterySpec(BaseModel):
    """Physical parameters of a simulated battery."""

    usable_capacity_kwh: float = Field(gt=0)
    max_charge_kw: float = Field(default=5.0, gt=0)
    max_discharge_kw: float = Field(default=5.0, gt=0)
    round_trip_efficiency: float = Field(default=0.90, gt=0, le=1.0)
    min_soc: float = Field(default=0.0, ge=0, lt=1.0, description="Fraction of usable capacity")

    @property
    def one_way_efficiency(self) -> float:
        """Round-trip efficiency split evenly between charge and discharge."""
        return float(self.round_trip_efficiency**0.5)


class TariffKind(StrEnum):
    FLAT = "flat"
    F1_F2_F3 = "f123"
    HOURLY_CSV = "hourly_csv"


class Tariff(BaseModel):
    """Import tariff + export remuneration.

    - flat: `flat_price_eur_kwh` applies to every hour.
    - f123: Italian time bands (F1 peak / F2 mid / F3 off-peak, ARERA calendar).
    - hourly_csv: per-hour prices loaded from a CSV (enables PUN / dynamic pricing).
    """

    kind: TariffKind = TariffKind.FLAT
    flat_price_eur_kwh: float | None = Field(default=None, gt=0)
    f1_price: float | None = Field(default=None, gt=0)
    f2_price: float | None = Field(default=None, gt=0)
    f3_price: float | None = Field(default=None, gt=0)
    hourly_prices_csv: str | None = None
    hourly_prices_timestamp_column: str = Field(
        default="timestamp", description="Timestamp column name in the hourly price CSV"
    )
    hourly_prices_price_column: str = Field(
        default="price", description="Price column name (EUR/kWh) in the hourly price CSV"
    )
    export_price_eur_kwh: float = Field(default=0.10, ge=0)

    @model_validator(mode="after")
    def _check_required_fields(self) -> Tariff:
        if self.kind is TariffKind.FLAT and self.flat_price_eur_kwh is None:
            msg = "flat tariff requires flat_price_eur_kwh"
            raise ValueError(msg)
        if self.kind is TariffKind.F1_F2_F3 and None in (
            self.f1_price,
            self.f2_price,
            self.f3_price,
        ):
            msg = "f123 tariff requires f1_price, f2_price and f3_price"
            raise ValueError(msg)
        if self.kind is TariffKind.HOURLY_CSV and self.hourly_prices_csv is None:
            msg = "hourly_csv tariff requires hourly_prices_csv path"
            raise ValueError(msg)
        return self


class ScenarioResult(BaseModel):
    """Output of one simulated battery scenario over the full dataset."""

    capacity_kwh: float
    battery_cost_eur: float | None = None

    # How long the analyzed period is. Carried on the scenario itself, not left to
    # the caller, because `payback_years()` cannot be correct without it: every
    # other figure here is a period total, and dividing a cost by a *period* saving
    # yields years-of-that-period, not years. See the docstring below.
    days_analyzed: int = Field(default=365, gt=0)

    # Energy balance (kWh, over the analyzed period)
    total_consumption_kwh: float
    total_pv_kwh: float
    baseline_import_kwh: float
    baseline_export_kwh: float
    simulated_import_kwh: float
    simulated_export_kwh: float
    battery_cycles: float

    # Self-consumption: PV energy used on-site / PV production
    self_consumption_before: float = Field(ge=0, le=1)
    self_consumption_after: float = Field(ge=0, le=1)

    # Economics (EUR, over the analyzed period)
    baseline_cost_eur: float
    simulated_cost_eur: float

    @property
    def savings_eur(self) -> float:
        """Savings over the analyzed period, whatever its length."""
        return self.baseline_cost_eur - self.simulated_cost_eur

    @property
    def annual_savings_eur(self) -> float:
        """Savings scaled to a full year — the figure every consumer should show.

        Exposed on the model rather than recomputed per call site: the card, the
        report and the terminal all need it, and three copies of one division is
        how they end up disagreeing.
        """
        return self.savings_eur / annualization_years(self.days_analyzed)

    def payback_years(self) -> float | None:
        """Naive payback: cost / **annual** savings. Years, not periods.

        The division is against `annual_savings_eur`, and the distinction is a
        correctness one rather than a stylistic one. `savings_eur` is a total over
        whatever period was analyzed, so dividing a cost by it yields "how many of
        *these periods* until it pays back" — which equals years only when the
        period happens to be a year. On a 60-day file it overstated payback by
        365/60, i.e. 6x: a 3,000 EUR battery saving 199 EUR/year was reported as
        91.7 years rather than 15.1, printed directly beside the annualized savings
        figure that contradicted it.

        The bug was invisible on the project's own fixture because it is exactly
        365 days long, where the factor is 1.

        Ignores degradation and price inflation, both stated in the report's
        "Limits & assumptions" section.
        """
        if self.battery_cost_eur is None or self.annual_savings_eur <= 0:
            return None
        return self.battery_cost_eur / self.annual_savings_eur


class ExportPricePoint(BaseModel):
    """One (capacity, export price) cell of the sensitivity grid.

    Savings here are for the whole analyzed period, exactly like `ScenarioResult`;
    the report annualizes them at render time.
    """

    capacity_kwh: float
    export_price_eur_kwh: float = Field(ge=0)
    savings_eur: float
    payback_years: float | None = None


class ExportPriceSensitivity(BaseModel):
    """Savings and payback re-costed across a range of export remuneration prices.

    The spread between import price and export remuneration is the dominant lever
    on battery ROI: every kWh the battery keeps on-site is worth
    `import_price - export_price`, so a generous feed-in tariff is what makes a
    battery *not* pay off. This grid makes that visible instead of hiding it inside
    a single configured number.
    """

    export_prices: list[float]
    baseline_export_price_eur_kwh: float = Field(
        ge=0, description="The configured export price the analysis itself was costed at"
    )
    points: list[ExportPricePoint]

    def for_capacity(self, capacity_kwh: float) -> list[ExportPricePoint]:
        """The row of the grid for one capacity, in ascending export-price order."""
        return [p for p in self.points if p.capacity_kwh == capacity_kwh]


class SeasonalBucket(BaseModel):
    """Aggregates for one month or season of the analyzed period.

    This is where a reader learns *why* their payback is what it is: a summer of
    wasted surplus and a winter of uncovered deficit produce the same annual
    average by very different routes, and only one of them is fixed by a bigger
    battery.
    """

    label: str = Field(description="Human label, e.g. '2012-07' or 'Summer'")
    sort_key: int = Field(description="Chronological order within the period")
    days: int

    pv_kwh: float
    consumption_kwh: float

    baseline_import_kwh: float
    baseline_export_kwh: float
    simulated_import_kwh: float
    simulated_export_kwh: float

    self_consumption_before: float = Field(ge=0, le=1)
    self_consumption_after: float = Field(ge=0, le=1)
    savings_eur: float

    @property
    def unused_surplus_kwh(self) -> float:
        """PV exported even *with* the battery: surplus the battery could not absorb.

        The headline number of this section. Large in summer means the battery is
        capacity- or power-bound against real available energy; near zero means
        more capacity would buy nothing in that period.
        """
        return self.simulated_export_kwh

    @property
    def uncovered_deficit_kwh(self) -> float:
        """Grid import still needed with the battery: demand PV+battery never met."""
        return self.simulated_import_kwh


class SeasonalAnalysis(BaseModel):
    """Per-period breakdown for one reference capacity: the recommended one.

    Tied to a single capacity on purpose: the seasonal story is about *this*
    battery against the user's own year, and a grid of every capacity against
    every month would bury it. That capacity is the one the Verdict recommends,
    so the report never describes two different batteries in adjacent sections.

    The largest swept capacity is carried alongside as a *ceiling*, not as the
    subject of the section: it answers "would a bigger battery have helped?" in
    one figure, which is the only claim the largest capacity can honestly support
    once it is no longer the battery being described.
    """

    capacity_kwh: float = Field(description="The recommended capacity, described by the buckets")
    granularity: str = Field(description="'month' or 'season'")
    buckets: list[SeasonalBucket]

    largest_capacity_kwh: float = Field(
        description="Largest capacity in the sweep, the ceiling reference"
    )
    largest_capacity_unused_surplus_kwh: float = Field(
        ge=0,
        description="Surplus still exported at the largest swept capacity: energy no "
        "battery in this sweep could have stored",
    )

    @property
    def unused_surplus_kwh(self) -> float:
        """Surplus the *recommended* battery could not store, over the whole period."""
        return sum(b.unused_surplus_kwh for b in self.buckets)

    @property
    def is_ceiling(self) -> bool:
        """True when the recommended capacity is also the largest swept.

        The two ceiling fields then say nothing the table does not already show,
        and the report drops the extra sentence rather than restating the section.
        """
        return self.capacity_kwh == self.largest_capacity_kwh


class AnalysisResult(BaseModel):
    """Full analysis: one entry per swept capacity, plus data-quality metadata."""

    scenarios: list[ScenarioResult]
    period_start: str
    period_end: str
    days_analyzed: int
    resolution_minutes: int
    seasonality_warning: bool = Field(
        description="True when < 365 days of data: results may not capture seasonality"
    )
    export_sensitivity: ExportPriceSensitivity | None = None
    seasonal: SeasonalAnalysis | None = None
