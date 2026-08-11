# PROJECT-CONTEXT.md — battery-worth

> Handoff document for session continuity. Update at the end of each work session.
> Same pattern as solar-report.

## What this is

Standalone CLI that answers, from the user's **historical** energy data:
**"Would a home battery have paid off for me?"** — with their real numbers, in minutes.
Retrospective what-if engine. NOT a live simulator (that's battery_sim's territory,
and the README must contain a "Why not battery_sim?" section explaining the
complementarity: retrospective/instant vs forward/live).

Strategic goal: second open source project after solar-report, same audience
(r/homeassistant, r/solar), consolidates the "energy data guy" positioning for
inbound senior-rate consulting. No active selling.

## Locked decisions (do not reopen without strong reason)

- **Name**: repo `battery-worth`, package `battery_worth`, command `battery-worth`.
- **Input**: hourly time series (accept 15-min, downsample to hourly):
  grid import (kWh), grid export (kWh), PV production (kWh).
  Parsers: generic CSV with configurable column mapping (v0),
  Home Assistant long-term statistics export (milestone 3).
- **Minimum data**: 30 days to run at all; transparency warning if < 12 months
  (seasonality dominates ROI — same honesty pattern as solar-report's baseline warning).
- **Simulation engine**: deterministic, vectorized pandas, NO LLM inside.
  v0 strategy: greedy self-consumption only (charge from PV surplus, discharge on
  deficit). Tariff arbitrage = v2, explicitly out of scope.
- **Battery params**: usable capacity, max charge power, max discharge power,
  round-trip efficiency (default 0.90), min SOC.
- **Capacity sweep**: one run simulates multiple capacities (e.g. 5/10/15/20 kWh).
  The comparison table is half the product's value.
- **Tariffs**: flat price, Italian F1/F2/F3 bands, export remuneration price,
  and hourly price series from CSV (this gives dynamic pricing / PUN support
  for free, zero API integrations).
- **Payback**: battery cost (user input) / year-1 savings. Report must state it
  ignores degradation and energy price inflation (v2).
- **Report**: fixed 4 sections — Verdict (annual savings, payback, self-consumption
  before/after), Scenario comparison table, Seasonal analysis,
  "Limits & assumptions" (ALWAYS present).
- **LLM is OPTIONAL** (different from solar-report): default output is the full
  numeric report, 100% offline. `--llm` flag adds natural-language commentary.
  Grounding rules identical to solar-report: the LLM comments ONLY on numbers
  computed by the engine, never its own estimates or comparisons.
- **Viral vehicle**: shareable summary card (matplotlib PNG):
  "10 kWh → 611 €/year → payback 6.8 years". Designed to be screenshotted.
- **Stack**: Python 3.11, pandas, pydantic v2, typer, jinja2, matplotlib,
  anthropic SDK (optional extra), ruff, mypy strict.

## Milestones

1. **Engine**: pydantic models, generic CSV parser, greedy vectorized simulator,
   capacity sweep. Tests on hand-verifiable synthetic data.
2. **Economics & report**: tariffs (flat, F1/F2/F3, hourly CSV), savings + payback,
   jinja2 report (4 sections), PNG summary card.
3. **Launch polish**: HA long-term statistics parser, optional LLM layer,
   README with real card screenshot, Dockerfile (multi-stage), launch posts.

## Current status

- [x] Scope defined, decisions locked
- [x] Repo skeleton (pyproject, models, CLI stub, simulator stub, first tests)
- [x] **Milestone 1 DONE** — the engine runs end to end from the command line:
  - [x] pydantic models (ColumnMapping, IngestReport, BatterySpec, Tariff, ScenarioResult)
  - [x] `ingest.py` — CSV parser, both schemas, cumulative auto-detect, DST, resampling
  - [x] `simulator.py` — greedy simulator + `summarize_scenario`
  - [x] `analysis.py` — `run_analysis` capacity sweep, price series built once, capacity-0 baseline
  - [x] `cli.py` — `battery-worth analyze` wired end to end, plain-text output
- [ ] **Milestone 2 in progress**:
  - [x] `tariffs.py` — flat / F1-F2-F3 / hourly CSV → per-interval price series
    (built early — see the 2026-08-11 (2) session log entry)
  - [x] savings + payback (in `ScenarioResult`, surfaced by the sweep and the CLI table)
  - [ ] jinja2 report (4 sections), PNG summary card
- [ ] Milestone 3

Suite: 103 tests passing, ruff clean, mypy strict clean.

## Validated invariants

Energy is conserved across localization, deduplication and hourly resampling.
Verified on synthetic DST cases (tests/test_ingest.py) and on 365 days of real
30-minute data (see Test fixture): PV total in == PV total out to the milli-kWh,
and `import + pv - export` reproduces the source consumption total exactly.

Simulator: `sim_import <= baseline_import`, `sim_export <= baseline_export`,
SOC stays within `[min_soc*cap, cap]`, and round-trip loss equals
`(1 - round_trip_efficiency)` of throughput.

Cycle count is **equivalent full cycles on energy actually stored**
(`charge * one_way_efficiency / usable_capacity`), not raw energy taken from
surplus. The definition is in the `summarize_scenario` docstring because cycle
counts appear in warranty terms and must be stated, not implied.

Capacity sweep: savings are **monotonically non-decreasing in capacity** and
**saturate** once the battery can absorb all available surplus. Both are asserted
in `tests/test_analysis.py`, under a flat tariff and under F1/F2/F3 — monotonicity
is a property of greedy self-consumption (nothing trades present savings for
future savings), not of the flat price, so it must hold under banded prices too.

The capacity-0 baseline row is **not simulated**: `BatterySpec` requires positive
capacity, and it carries `battery_cost_eur = None` so `payback_years()` returns
None. A "0.0 year payback" on the do-nothing row would be the single most
misleading number the comparison table could print. A test pins the hand-built
baseline against the simulator's own baseline figures so the two paths cannot drift.

Prices are never guessed: an hourly price CSV that does not cover every analysis
timestamp raises, naming the uncovered range. A price row is valid for its own
step only (half-open `[t, t+step)`), so a hole in the price file surfaces as an
error instead of inheriting the previous hour's price.

Known, accepted: netting at hourly resolution instead of native 30-min moves
~55 kWh/yr out of both import and export on the fixture (intra-hour surplus
cancels intra-hour deficit). It cancels out of the energy balance and makes the
battery look slightly *less* valuable, so it is a conservative direction. Direct
consequence of the locked "downsample to hourly" decision.

## DST handling (decided, with rationale)

- **Autumn / ambiguous hour**: `ambiguous="infer"` is the primary path — it
  resolves the repeated hour from the data when the hour genuinely appears twice
  (Home Assistant style). When it appears only once (public datasets, inverter
  CSVs, anything written against a naive local clock with fixed slots/day),
  infer raises; the fallback reads it as the first, pre-changeover pass and warns.
  Worst case shifts one hour of energy per year.
- **Spring / nonexistent hour**: `nonexistent="shift_forward"` collides the
  missing hour onto the next one. Colliding rows are **summed** — they are two
  separate intervals of real energy that happen to share a timestamp.
- **Byte-identical rows are dropped, not summed.** The asymmetry is deliberate:
  an exact repeat of timestamp *and* values is the same reading exported twice.
  Under-counting one hour per year is noise; inventing energy that never flowed
  is a false number, and this tool's whole value is that its numbers are real.
- Duplicates are resolved **after** localization, because the two DST cases are
  indistinguishable before it (autumn produces two distinct instants and never
  reaches the dedup step; spring produces a true collision).

## Tariffs (decided, with rationale)

- **`build_price_series(index, tariff) -> pd.Series`** is the whole public API,
  plus `assign_bands()` and `italian_national_holidays()` exposed so the ARERA
  calendar is testable without touching prices.
- **Bands are read off local wall-clock time**, never UTC: in summer Rome is
  UTC+2, so banding on UTC would shift every boundary by two hours.
- **F1/F2/F3 is Italy-specific.** A non-Italian (or naive) index **warns** rather
  than failing — the bands are still computable, the user just needs to know they
  probably don't match their real tariff.
- **Holidays are national only** (no local patron saints; Milan's 7 Dec is
  deliberately absent). Easter Monday computed via `dateutil.easter`.
- **`python-dateutil`** declared explicitly in dependencies: already a hard pandas
  dep so it installs nothing new, but `tariffs.py` imports it directly. Chosen over
  the `holidays` package — one function vs. a new wheel.
- **Hourly CSV localized with the shared `ingest.localize_index`** (renamed from
  `_localize`), not a copy: a price file and an energy file crossing the same DST
  changeover must be treated identically or they misalign for an hour a year.
  Consequence, tested: a *naive* price file cannot cover the 25-hour autumn day
  (24 wall-clock stamps, 25 real hours) and correctly raises — supply prices with
  explicit offsets/UTC to cross a changeover, which is how PUN data arrives anyway.
- Duplicate price timestamps are **averaged, not summed** — opposite of the energy
  path, because price is intensive and energy is extensive.
- Unit sanity: warns if median price > 5 or < 0.001 EUR/kWh, since day-ahead data
  (PUN) is published in EUR/MWh and pasting it raw is a silent 1000x error.

## Test fixture

Ausgrid "Solar home electricity data", customer 1, 2012-07-01 → 2013-06-30
(Australia/Sydney, meter-centric schema: consumption + pv_production).

- Path: `~/personal-projects/_datasets/ausgrid/Ausgrid_solar_home_data/customer_1_2012-2013.csv`
- 17520 rows, 365 days, 30-min intervals, no missing slots
- PV 5115.207 kWh, consumption 7679.201 kWh
- Fixed 48 slots/day, so both changeover days exercise the DST paths: autumn
  triggers the single-occurrence fallback, spring triggers both the
  identical-drop and the sum branch on one timestamp (net loss 0.000 kWh)
- `scripts/extract_ausgrid_customer.py` regenerates it from the raw dataset
  (`Solar home 2012-2013.csv`); the raw files are not in the repo

## Session log

- **2026-08-11** — Field-tested `ingest.py` against the real Ausgrid fixture (first
  contact with data it wasn't written against): all report fields correct, energy
  conserved exactly, both DST branches fired and read clearly. Ran the 10 kWh
  simulation end to end (self-consumption 25.7% → 82.5%, 290 cycles/yr, balance
  coherent). No code changes needed. Documented invariants, DST rationale and the
  fixture above. **Next: `tariffs.py`** (flat / F1-F2-F3 / hourly CSV → per-hour
  price series), which unblocks `summarize_scenario` and the capacity sweep.
- **2026-08-11 (2)** — Fixed `battery_cycles`: was counting energy taken from surplus
  rather than energy stored, overstating cycles ~5% at 0.90 round-trip (290.4 → 275.5
  on the fixture). Definition now documented in the docstring. Implemented `tariffs.py`
  (all three kinds) + 34 tests; renamed `ingest._localize` → `localize_index` for reuse.
  Verified end to end on the fixture: flat 0.25 EUR/kWh → 363 EUR/yr savings, 16.5y
  payback on a 6000 EUR battery. Suite 64 passed, ruff clean, mypy strict clean.
  **Next: capacity sweep + CLI wiring** to close Milestone 1, then the jinja2 report.
- **2026-08-11 (3)** — **Milestone 1 closed.** Added `analysis.py` (`run_analysis`:
  sweep capacities, build the price series once outside the loop, capacity-0 baseline)
  and rewrote `cli.py` so `battery-worth analyze` runs end to end in plain text.
  Suite 64 → 103, ruff and mypy strict clean.

  *Plan deviation, recorded rather than retconned:* `tariffs.py` is a Milestone 2
  item but was built in session (2), before the Milestone 1 sweep and CLI. The
  milestone list above is deliberately left as originally written. The reason it
  happened: `summarize_scenario` takes a price series, so there was no way to
  produce a scenario — let alone a comparison table — without tariffs existing
  first. The milestone boundary put the economics in M2 while M1's own capacity
  sweep already depended on them; the dependency, not the plan, decided the order.
  Worth remembering when planning M3: check what the milestone's *last* step needs
  before assuming the milestone is self-contained.

  Three CLI decisions worth carrying forward:
  - An empty `@app.callback()` keeps Typer in multi-command mode. Without it Typer
    promotes a lone command to the top level and the locked surface would silently
    become `battery-worth <file>` instead of `battery-worth analyze <file>`.
  - The unrecognised-columns error is handled in the CLI, not in `ingest`, because
    it must list the header actually found alongside *both* accepted schemas and a
    copy-pasteable `--col-*` example. This is the most likely first-run failure for
    a new user, and it is covered by its own tests.
  - `warnings.catch_warnings(record=True)` wraps ingest + analysis so tariff
    warnings (non-Italian timezone, EUR/MWh units) land in the report's WARNINGS
    block instead of interleaving with stdout. Ingest warnings and captured runtime
    warnings are printed together, verbatim, numbered, and never summarized.

  Verified on the Ausgrid fixture (365 d, flat 0.25, export 0.10, 600 EUR/kWh):
  5 kWh → 211 EUR/yr, 14.2 y · 10 kWh → 363 EUR/yr, 16.5 y · 15 kWh → 442 EUR/yr,
  20.4 y · 20 kWh → 462 EUR/yr, 26.0 y. Self-consumption 26% → 59/82/95/98%. The
  10 kWh row reproduces session (2)'s figure exactly, which cross-checks the sweep
  against the earlier manual run. Savings saturate visibly (+79 EUR from 10→15 kWh,
  +20 EUR from 15→20) and shortest payback is the *smallest* battery — exactly the
  tension the comparison table exists to show.

  **Next: Milestone 2 proper** — jinja2 report (4 fixed sections) + matplotlib
  summary card. The CLI's plain-text sections map 1:1 onto the report's, so the
  renderer can consume `AnalysisResult` + `IngestReport` without new engine work.
