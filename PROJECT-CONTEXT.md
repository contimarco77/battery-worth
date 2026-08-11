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
  - [x] export price sensitivity (`build_export_sensitivity`, `--export-price-sweep`)
  - [x] seasonal aggregates (`SeasonalAnalysis`, per-month or per-season)
  - [x] jinja2 report (4 sections) — `report.py` + `templates/report.md.j2`, `--output`
  - [ ] PNG summary card
- [ ] Milestone 3

Suite: 171 tests passing, ruff clean, mypy strict clean.

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

## Export price sensitivity (decided, with rationale)

- **Re-costing, never re-simulation.** Greedy self-consumption never reads a price,
  so the energy flows are identical at every export price and only the costing
  changes — linearly. `build_export_sensitivity` works from the stored scalars on
  a finished `ScenarioResult`:
  `savings(p') = savings(p) - (baseline_export - simulated_export) * (p' - p)`.
  No dataframe is touched. `test_recosting_agrees_with_a_full_rerun_at_that_price`
  pins the shortcut against an actual second `run_analysis`, so if v2's tariff
  arbitrage ever makes the strategy price-aware, that test fails rather than the
  numbers silently going wrong.
- **Invariant: savings are non-increasing as the export price rises.** A battery can
  only reduce export, so the bracket above is non-negative. Economically: the
  battery's value is the *spread* it captures by keeping a kWh rather than selling
  it, and a better feed-in tariff shrinks that spread. Verified against the cost
  equation, not against intuition, and asserted under flat and banded import prices.
- **The default sweep includes the configured price** (0.5x / 1x / 1.5x), so the
  user's own case sits inside the trend instead of beside it. A zero configured
  price steps absolutely (0 / 0.05 / 0.10) rather than scaling, which would
  collapse all three points onto zero.

## Seasonal analysis (decided, with rationale)

- **One reference capacity: the one the Verdict recommends.** Originally the largest
  simulated, on the reasoning that surplus still exported there is surplus *no* battery
  in the sweep could have used. That was true but described the wrong battery: the
  Verdict recommended 5 kWh while the seasonal table showed 20 kWh at 94-100%
  self-consumption where the recommended unit gives 59%. A reader skimming takes the
  table as their result, so the two sections must name the same battery. A grid of
  every capacity against every month would bury the finding either way.
- **The ceiling survives as a figure, not as the framing.** `SeasonalAnalysis` carries
  `largest_capacity_kwh` + `largest_capacity_unused_surplus_kwh`, rendered as one
  sentence ("Even the largest battery in this sweep (20 kWh) would have left 107 kWh
  of surplus unused"). That is the only claim the largest capacity can honestly support
  once it is not the battery being described, and `is_ceiling` suppresses the sentence
  when the recommendation *is* the largest, where it would just restate the table.
- **"Unused surplus" changed meaning with the reference and the wording changed with it.**
  Against the largest capacity it meant "no battery in this sweep could have used it";
  against the recommended one it means "*this* battery could not store it — a larger one
  might have", which is a weaker claim and is now worded as such.
- **`recommended_scenario` lives in `analysis.py`**, not in each consumer. The Verdict,
  the terminal summary and the seasonal breakdown all call it, so they cannot name three
  different batteries. It was duplicated in `report.py` and `cli.py` before.
- **Aggregated from the already-simulated frame**, kept in `run_analysis` for exactly
  this purpose — the seasonal section costs no extra simulation.
- **Monthly at >= 4 months of data, meteorological quarters below that**: three
  monthly rows read as noise rather than seasonality.
- **Season labels are month ranges ("Jun-Aug"), not names.** The fixture is
  Australian; printing "Summer" for a Sydney June would be a factual error in a
  report whose whole selling point is that its numbers are real.
- Bucket sort keys are `year*12 + month`, so a July→June dataset (the fixture)
  orders correctly across the new year instead of resetting.

## Report (decided, with rationale)

- **Markdown is the primary format.** Renders on GitHub, pastes into Reddit and the
  HA forums with tables intact, diffs cleanly, needs no browser — which is exactly
  where this audience shares results.
- **The template contains no computation.** Every number comes from `AnalysisResult`;
  jinja2 only formats, through a small filter vocabulary (`annual`, `eur`, `kwh`,
  `cap`, `pct`, `price`, `years`, `round0`). `annual` is the only filter that does
  arithmetic, and it does the one conversion (period total → per year) that would
  otherwise be repeated at every call site.
- **`StrictUndefined`**: a typo'd field name fails at render time rather than printing
  an empty cell into a report someone is about to spend money on.
- **`cap` is separate from `kwh`** so capacities render "5 kWh", not "5.0 kWh" — a
  nameplate the user typed and expects echoed back, versus a measured quantity where
  a decimal carries information.
- **Whitespace control is the real hazard in a Markdown template.** Inline `{% for %}`
  loops swallow the newlines Markdown needs and silently collapse a table onto one
  line; it renders as a paragraph of pipes and is easy to miss in a diff.
  `test_sensitivity_table_renders_as_valid_markdown_rows` checks column counts are
  uniform, which is what catches it.
- **"Limits & assumptions" is unconditional** — a report that drops its caveats when
  the numbers look good is the exact failure mode this tool exists to avoid. Tested.
- **Annualization divides by 365, not 365.25.** The Julian year scaled every headline
  figure by 0.07%, so the report printed PV 5,119 where the fixture's own column sums
  to 5,115.2. A 0.07% correction buys nothing and costs the one property this tool
  sells: a user who sums their CSV in a spreadsheet must find the report's number.
  **Invariant, tested:** at `days_analyzed == 365` annualization is the identity, and
  every headline energy figure equals the sum of the input column to the precision
  printed. It applies to savings and cycles too, not just the visible energy totals —
  it was one shared constant, so a partial fix would have left those quietly scaled.
- **`annualization_years` is defined once, in `report.py`, and imported by `cli.py`.**
  Both annualize the same scenarios; two copies of the constant meant one run could
  print two different annual savings depending on where you read it. A CLI test renders
  both outputs from a single run and compares the figures.

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
- **2026-08-11 (4)** — **Milestone 2, part 1.** Export price sensitivity + Markdown
  report. Suite 103 → 160, ruff and mypy strict clean.

  Added `build_export_sensitivity` (pure re-costing, no re-simulation), per-month/season
  aggregates, `report.py` + `templates/report.md.j2`, and the `--export-price-sweep` /
  `--output` flags. Rationale for all three is in the new sections above. `cli.py`'s
  `_describe_tariff` moved to `report.py` and is now shared, since terminal output and
  report must never describe the same tariff differently.

  *That last prediction was half right.* The renderer did consume `AnalysisResult`
  without new engine work — but only for the two sections that already existed.
  Seasonal analysis had no engine support at all, so it needed new models and a new
  aggregation path before a single line of template could be written. Worth carrying
  into M3: "the report just formats existing numbers" is true per-section, and a
  section that has never been computed is not a formatting task. Check each section
  against the engine, not the report as a whole.

  Verified on the Ausgrid fixture: all four capacity rows reproduce session (3)'s
  figures exactly, which cross-checks the whole new layer against the old path. The
  export sweep is the section that earns its place — at 0.05 EUR/kWh export the 5 kWh
  battery pays back in 10.2 y, at 0.15 it takes 23.7 y. Same battery, same data, same
  simulation; the export price more than doubles the payback. The seasonal table shows
  why the fixture's payback is long: self-consumption after the battery is 94-100%
  every single month, so unused surplus is already near zero and no larger battery can
  help — the ceiling is the roof, not the storage.

  One implementation trap, now pinned by a test: the sensitivity table collapsed onto
  a single line because inline `{% for %}` loops eat the newlines Markdown needs.
  It renders as a wall of pipes and would have shipped unnoticed.

  **Next: Milestone 2 part 2** — the matplotlib PNG summary card.
- **2026-08-11 (5)** — **Two report corrections**, both found by reading the generated
  report against the source data rather than by a failing test. Suite 160 → 171.

  *Annualization drift.* The report divided by 365.25 and printed PV 5,119 / import
  6,370 / export 3,804 where the fixture's own columns sum to 5,115.2 / 6,365.5 /
  3,801.5. Now 365, so a full year is the identity. The lesson worth carrying: the
  bug was invisible to every existing test because all 160 of them compared the report
  against the engine, and the engine and the report agreed — both were 0.07% away from
  the user's file. **A test suite that only checks internal consistency cannot catch a
  layer that is uniformly wrong.** The new test asserts against the *input* totals.

  *Seasonal section described the wrong battery.* Verdict said 5 kWh, seasonal table
  said 20 kWh, and its 94-100% self-consumption column read as the reader's result when
  the recommended unit gives 59%. Both now follow `recommended_scenario`, moved into
  `analysis.py` from the two places that had copied it. Rationale in the sections above.

  Verified end to end on the Ausgrid fixture: all four capacity rows still reproduce
  session (3)'s figures exactly, PV/consumption now match the raw column sums to the
  digit, and `import + pv - export` reconciles (6,366 + 5,115 − 3,802 = 7,679). The
  ~55 kWh gap against the raw 30-minute derived import/export is the known, documented
  hourly-netting effect and cancels out of the balance. Both fixes were mutation-checked
  by reintroducing the old constant and the old reference capacity.
