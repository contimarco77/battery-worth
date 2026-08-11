"""Core domain models for battery-worth.

All models are pydantic v2, strict where it matters. The simulation engine
(simulator.py) is pure pandas and deterministic; these models define its
inputs and outputs.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


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
        return self.baseline_cost_eur - self.simulated_cost_eur

    def payback_years(self) -> float | None:
        """Naive payback: cost / year-1 savings. Ignores degradation and inflation
        (stated explicitly in the report's Limits & assumptions section)."""
        if self.battery_cost_eur is None or self.savings_eur <= 0:
            return None
        return self.battery_cost_eur / self.savings_eur


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
