"""Greedy self-consumption battery simulator.

Deterministic, no LLM involved. Strategy (v0, locked):
- PV surplus (export in baseline) charges the battery, limited by max charge
  power and remaining capacity, with one-way charge efficiency applied.
- Grid deficit (import in baseline) is served from the battery first, limited
  by max discharge power and available energy, with one-way discharge
  efficiency applied.
- Tariff arbitrage (charging from grid when cheap) is explicitly out of scope
  for v0.

Note on vectorization: the SOC propagation is inherently sequential (each
hour depends on the previous SOC), so the core loop runs over numpy arrays
row by row. Everything around it (pre/post processing, economics) is
vectorized pandas. For hourly data this is fast enough (1 year = 8760 rows).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from battery_worth.models import BatterySpec, ScenarioResult


def simulate_battery(
    df: pd.DataFrame,
    spec: BatterySpec,
    interval_hours: float = 1.0,
) -> pd.DataFrame:
    """Run the greedy simulation over a prepared dataframe.

    Expects columns: `grid_import`, `grid_export`, `pv_production` (kWh per interval),
    indexed by timestamp. Returns a copy with added columns:
    `soc_kwh`, `battery_charge_kwh`, `battery_discharge_kwh`,
    `sim_import`, `sim_export`.
    """
    imp = df["grid_import"].to_numpy(dtype=float)
    exp = df["grid_export"].to_numpy(dtype=float)
    n = len(df)

    eff = spec.one_way_efficiency
    max_charge = spec.max_charge_kw * interval_hours
    max_discharge = spec.max_discharge_kw * interval_hours
    cap = spec.usable_capacity_kwh
    soc_floor = spec.min_soc * cap

    soc = np.zeros(n)
    charge = np.zeros(n)  # kWh drawn from surplus into the battery (pre-efficiency)
    discharge = np.zeros(n)  # kWh delivered to the house (post-efficiency)
    sim_import = np.zeros(n)
    sim_export = np.zeros(n)

    current_soc = soc_floor
    for i in range(n):
        surplus = exp[i]
        deficit = imp[i]

        # Charge from surplus
        room = (cap - current_soc) / eff  # surplus kWh needed to fill the room
        c = min(surplus, max_charge, room)
        current_soc += c * eff
        sim_export[i] = surplus - c

        # Discharge to cover deficit
        available = (current_soc - soc_floor) * eff  # deliverable kWh
        d = min(deficit, max_discharge, available)
        current_soc -= d / eff
        sim_import[i] = deficit - d

        soc[i] = current_soc
        charge[i] = c
        discharge[i] = d

    out = df.copy()
    out["soc_kwh"] = soc
    out["battery_charge_kwh"] = charge
    out["battery_discharge_kwh"] = discharge
    out["sim_import"] = sim_import
    out["sim_export"] = sim_export
    return out


def summarize_scenario(
    sim_df: pd.DataFrame,
    spec: BatterySpec,
    import_prices: pd.Series,
    export_price: float,
    battery_cost_eur: float | None = None,
) -> ScenarioResult:
    """Compute the energy balance and economics for one simulated scenario.

    `import_prices` must be a per-interval EUR/kWh series aligned with sim_df's index
    (built by tariffs.py from the configured Tariff).

    Cycle definition (stated explicitly, because cycle counts appear in warranty
    terms): `battery_cycles` is **equivalent full cycles**, defined as the total
    energy actually stored in the battery divided by its usable capacity. Energy
    stored is `battery_charge_kwh * one_way_efficiency` — the charge column holds
    energy taken FROM the PV surplus, before charge losses, so using it directly
    would overstate cycles by roughly 5% at the default 0.90 round-trip.
    Counting on the charge side means a cycle is booked when energy goes in;
    energy still sitting in the battery at the end of the period is included.
    """
    total_pv = float(sim_df["pv_production"].sum())
    baseline_import = float(sim_df["grid_import"].sum())
    baseline_export = float(sim_df["grid_export"].sum())
    sim_import = float(sim_df["sim_import"].sum())
    sim_export = float(sim_df["sim_export"].sum())
    consumption = baseline_import + total_pv - baseline_export

    sc_before = (total_pv - baseline_export) / total_pv if total_pv > 0 else 0.0
    sc_after = (total_pv - sim_export) / total_pv if total_pv > 0 else 0.0

    baseline_cost = float(
        (sim_df["grid_import"] * import_prices).sum() - baseline_export * export_price
    )
    sim_cost = float((sim_df["sim_import"] * import_prices).sum() - sim_export * export_price)

    energy_stored = float(sim_df["battery_charge_kwh"].sum()) * spec.one_way_efficiency
    cycles = energy_stored / spec.usable_capacity_kwh

    return ScenarioResult(
        capacity_kwh=spec.usable_capacity_kwh,
        battery_cost_eur=battery_cost_eur,
        total_consumption_kwh=consumption,
        total_pv_kwh=total_pv,
        baseline_import_kwh=baseline_import,
        baseline_export_kwh=baseline_export,
        simulated_import_kwh=sim_import,
        simulated_export_kwh=sim_export,
        battery_cycles=cycles,
        self_consumption_before=max(0.0, min(1.0, sc_before)),
        self_consumption_after=max(0.0, min(1.0, sc_after)),
        baseline_cost_eur=baseline_cost,
        simulated_cost_eur=sim_cost,
    )
