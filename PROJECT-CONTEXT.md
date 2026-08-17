# PROJECT-CONTEXT.md — battery-worth

> Handoff document for session continuity. Update at the end of each work session.
> Same pattern as solar-report.

## What this is

Standalone CLI that answers, from the user's **historical** energy data:
**"Would a home battery have paid off for me?"** — with their real numbers, in minutes.
Retrospective what-if engine. NOT a live simulator (that's battery_sim's territory,
and the README must state the difference — retrospective/instant vs forward/live —
without a dedicated comparison section; see "README (decided, with rationale)").

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
- **Payback**: battery cost (user input) / **annualized** year-1 savings. The
  annualization is load-bearing, not cosmetic: `savings_eur` is a period total, so
  dividing by it yields "periods to pay back" and equals years only on a 365-day
  file. Report must state it ignores degradation and energy price inflation (v2).
- **Report**: fixed 4 sections — Verdict (annual savings, payback, self-consumption
  before/after), Scenario comparison table, Seasonal analysis,
  "Limits & assumptions" (ALWAYS present).
- **LLM is OPTIONAL** (different from solar-report): default output is the full
  numeric report, 100% offline. `--llm` flag adds natural-language commentary.
  Grounding rules identical to solar-report: the LLM comments ONLY on numbers
  computed by the engine, never its own estimates or comparisons.
  **Deferred to v0.2 — out of v0.1 scope.** The design above stands; only the
  timing changed. Five reasons, decided deliberately:
  - The shareable card is the distribution vehicle, and LLM prose adds nothing
    to a screenshot.
  - "100% offline, no LLM" is an asset with the r/homeassistant and
    r/selfhosted audience, and it is consistent with the reason
    `ha_export.py` was built as a standalone script.
  - solar-report is already the grounded-LLM project; keeping battery-worth
    deterministic differentiates the pair instead of selling the same thing
    twice.
  - Grounding is the highest-risk component in the project and has zero effect
    on reach, while the README — which every visitor reads and which the launch
    posts depend on — does not exist yet.
  - Shipping it in v0.2 buys a second launch post on the same channels.
- **Liability position: Apache 2.0 unmodified, a plain-language README disclaimer,
  and one line on the card.** Three parts, decided together
  because each covers a channel the others do not reach:
  - **The licence text is not edited.** Sections 7 and 8 (Disclaimer of Warranty,
    Limitation of Liability) are the industry-standard wording and are the
    substantive protection. Editing them weakens the protection and creates
    licence-compatibility problems; anything project-specific goes in the README
    instead. Same licence as `solar-report`, deliberately.
  - **The README carries a `## Disclaimer` section**, in prose: this is a
    retrospective estimate and not financial or energy advice; results depend
    entirely on the quality of the data the user supplies; the model ignores
    battery degradation, energy price inflation, incentives and installation
    costs; verify with a professional before any purchase. "Limits & assumptions"
    is the technical half of the same statement and stays where it is — the
    Disclaimer is the plain-language half and does not replace it.
  - **One liability line in the card footer.** The card travels without the README,
    without the licence and without context, so it needs its own line. Subject to
    the footer's existing space constraint — see the Summary card section, where
    the 2026-08-12 (3) entry records that the footer gets three seconds and every
    line competes with the verdict.
  **No legal review before launch** — reversed on 17 August. It was listed as a
  fourth part and as an open external dependency; retaining a lawyer for a free
  tool that sells nothing is out of proportion to the exposure. Apache 2.0
  sections 7 and 8 are the substantive protection and are unmodified, and the
  two texts that carry the project-specific part are already written. What
  replaces the review is a maintenance rule: **the Disclaimer section and the
  card footer line are load-bearing and must survive every future edit.** They
  are prose, so nothing fails if they are shortened away during a rewrite —
  which is exactly how they would be lost. Revisit if the project ever takes
  money, ships an LLM layer that phrases its own conclusions, or is redistributed
  by someone else under their name.
  This is locked rather than a nice-to-have because it is the same claim the
  project is positioned on: the numbers are real and the limits are stated, and
  the disclaimer is that claim in plain language. What a disclaimer cannot do
  bounds how much weight it can carry — it cannot exclude criminal liability
  anywhere, and under Italian law art. 1229 c.c. voids any advance exclusion of
  liability for wilful misconduct or gross negligence. The protection that
  actually matters is therefore **the tool not making unverified claims**, which
  is why the counter-spike defect deferred to v0.2 must appear in "Limits &
  assumptions" and not only in the roadmap: a README promising stated limits, over
  a tool with an undocumented silent one, is weaker than no disclaimer at all.
- **Viral vehicle**: shareable summary card (matplotlib PNG):
  "10 kWh → 611 €/year → payback 6.8 years". Designed to be screenshotted.
- **Stack**: Python 3.11, pandas, pydantic v2, typer, jinja2, matplotlib,
  anthropic SDK (optional extra), ruff, mypy strict.

## Definition of done

**No piece of work is complete until all four gates pass.** Not three of four, and
not "the failure is preexisting and unrelated" — session (10) is the record of what
that reasoning costs.

```
pytest
ruff check .
ruff format --check .
mypy src/ --strict
```

`ruff format --check .` is the gate that was missing, and its absence is why this
section exists. Only `ruff check` was being run, and the two are not the same tool:
`check` is the linter (unused imports, undefined names, rule violations) and
`format` is the formatter (line breaks, quote style, trailing commas). Nothing in
`check` reports formatting drift, so it accumulated silently across **12 files** —
every session added a little and every session's report said "ruff clean", which
was true of the command actually run and false of the thing it implied.

Three properties of this failure worth carrying:

- **It reported success while degrading.** The gate that was never run cannot fail,
  so the drift had no signal at all until someone ran a different command. Compare
  session (10)'s missing `py.typed`: same shape, a checker that had quietly stopped
  checking.
- **It is unbounded.** Lint errors are individually visible in a diff; formatting
  drift is invisible per-commit and only legible in aggregate, so it has no natural
  point at which anyone notices.
- **It taxes every future diff.** Reformatting 12 files at once produces a 519-line
  commit that no reviewer can read, and it lands on top of real changes. Running the
  gate per session keeps the formatting delta at zero and the diff about the work.

`mypy` is run as `mypy src/ --strict` for the gate. Note that `mypy src tests` also
passes and is worth running when touching tests — session (10) added `py.typed`
precisely so the test suite is type-checked rather than silently degraded to `Any`.

## Milestones

1. **Engine**: pydantic models, generic CSV parser, greedy vectorized simulator,
   capacity sweep. Tests on hand-verifiable synthetic data.
2. **Economics & report**: tariffs (flat, F1/F2/F3, hourly CSV), savings + payback,
   jinja2 report (4 sections), PNG summary card.
3. **Launch polish**: HA long-term statistics parser, README with real card
   screenshot, Dockerfile (multi-stage), launch posts. (The optional LLM layer
   was originally scoped here; deferred to v0.2 — see "Locked decisions".)

## Current status

- [x] Scope defined, decisions locked
- [x] Repo skeleton (pyproject, models, CLI stub, simulator stub, first tests)
- [x] **Milestone 1 DONE** — the engine runs end to end from the command line:
  - [x] pydantic models (ColumnMapping, IngestReport, BatterySpec, Tariff, ScenarioResult)
  - [x] `ingest.py` — CSV parser, both schemas, cumulative auto-detect, DST, resampling
  - [x] `simulator.py` — greedy simulator + `summarize_scenario`
  - [x] `analysis.py` — `run_analysis` capacity sweep, price series built once, capacity-0 baseline
  - [x] `cli.py` — `battery-worth analyze` wired end to end, plain-text output
- [x] **Milestone 2 DONE**:
  - [x] `tariffs.py` — flat / F1-F2-F3 / hourly CSV → per-interval price series
    (built early — see the 2026-08-11 (2) session log entry)
  - [x] savings + payback (in `ScenarioResult`, surfaced by the sweep and the CLI table)
  - [x] export price sensitivity (`build_export_sensitivity`, `--export-price-sweep`)
  - [x] seasonal aggregates (`SeasonalAnalysis`, per-month or per-season)
  - [x] jinja2 report (4 sections) — `report.py` + `templates/report.md.j2`, `--output`
  - [x] PNG summary card — `card.py`, written beside `--output`, skipped with `--no-card`
- [ ] **Milestone 3 in progress**:
  - [x] `scripts/ha_export.py` — standalone Home Assistant export (see the section
    below; the HA "parser" is a separate script, not an ingest path)
  - [ ] README with real card screenshot
  - [x] Dockerfile (multi-stage) — `bfb5197`
  - [ ] launch posts
  - ~~optional LLM layer~~ — **deferred to v0.2**, not a v0.1 item
    (decision and rationale in "Locked decisions")
  - ~~cumulative counter-spike detection~~ — **deferred to v0.2**, but the defect
    must be visible in v0.1's "Limits & assumptions" (see below)

**Deferred to v0.2, alongside the LLM layer: cumulative counter-spike detection.**
A cumulative meter column can carry a stalled-counter spike — a run of identical
readings followed by a jump that dumps the accumulated remainder into one interval.
Measured on OPSD residential3: **803.94 kWh in a single hour, 13.9% of all import,
no warning.** It moves payback by 0.1 years but distorts the self-consumption ratio
by 3 points (35% → 32%), and self-consumption is a headline figure on the card.

Three candidate signals, none needing an arbitrary constant:

- **Last interval of a series** — catches both large spikes, since both are terminal
  readings.
- **Night-time PV** — a physical impossibility; catches 5 of 7, and structurally
  cannot catch an import feed or a genuine daytime hour.
- **Self-relative ratio against the feed's own p99.9** — a real cliff at
  306x / 46.6x / 20.4x against a next-highest 7.4x across 68 feeds.

An **absolute threshold is ruled out by measurement**: legitimate industrial feeds
peak at 222 kWh/h, at 1.0-1.1x their own p99.9.

This must also reach **"Limits & assumptions" when the README is written**, per the
liability decision in "Locked decisions" — the disclaimer's protection is the tool
not making unverified claims, so a known silent defect cannot live only in the
roadmap.

**Milestone 2 is closed.** Suite: 358 tests passing, all four gates clean
(see "Definition of done").

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

## Annualization (decided, with rationale) — READ BEFORE ADDING ANY DERIVED FIGURE

**Three bugs of the same shape have shipped here. Assume a fourth is possible.**

- Session (5): the report divided by 365.25, so every headline figure sat 0.07% off
  the user's own column sums.
- Session (8): `payback_years()` divided cost by **period** savings instead of
  annual savings, overstating payback by 365/days — 6x on a 60-day file, printed
  directly beside the annualized savings figure that contradicted it.
- The 2026-08-12 (2) session: `_days_analyzed` measured the **calendar span** between the first and
  last timestamp rather than the days actually covered. On a gappy file — 60 days of
  readings, a hole, 5 more days the following January — 65 days of data reported as
  371, so every per-year figure was 5.7x too small and every payback 5.7x too long
  (a 10.1-year payback printed as 57.8). Worse, `371 >= 365` **suppressed the
  seasonality warning**, so the report affirmatively printed "That is a full year, so
  seasonal swings are captured rather than extrapolated" about two winter months.
  The divisor and the warning threshold were the same number, so one wrong count
  broke the figures *and* the caveat that would have qualified them.

Both were invisible to a full suite, for the same reason: **every test compared one
layer of our code against another, and the layers agreed while being uniformly
wrong.** Internal consistency cannot detect a uniform error. The fixture is exactly
365 days long, which is precisely the period where both bugs vanish.

Rules that follow, and that a reviewer should enforce:

- `DAYS_PER_YEAR` and `annualization_years` live in **`models.py`**, the domain
  layer — not in `report.py`, where they started. Annualization is not formatting:
  `payback_years()` needs it to produce a correct number at all, so a presentation
  module cannot own it. `report.py` re-exports the name for its existing callers.
- `ScenarioResult` carries **`days_analyzed`**. Without it the model physically
  cannot annualize, which is how the bug was possible: every other field was a
  period total and nothing on the object knew what the period was.
- Consumers use **`annual_savings_eur`**, never `savings_eur / years` recomputed
  locally. `_payback` in `analysis.py` (the export-price grid) needed the same fix
  separately — it shares neither the model's method nor its period.
- **Any test of a derived figure must anchor to a hand-computed value written out
  in the test**, not to another layer of ours. The payback tests state the
  arithmetic inline (3000 / (32.7 × 365/60) = 15.08) and assert that the same data
  truncated to 60/180/365 days yields the same payback — a period-invariance check
  that no internal-consistency test could have expressed.
- **`days_analyzed` is a measure of coverage, not of extent.** It counts distinct
  days carrying readings, on the **raw** index — never the resampled one, because
  `resample("h")` materializes every missing hour of a gap as a zero row and
  reinstates the calendar-span figure exactly. Any future field that sizes the
  period has the same trap.
- **The fixture cannot see this class of bug, and its shape says why.** Ausgrid is
  365 continuous days, and it has now hidden two bugs through two *different*
  properties. Its **length** hid the 365.25 drift and the period-vs-annual payback:
  365 days is the identity for annualization, so any divisor error vanishes on it.
  Its **continuity** hid the span-vs-coverage count: with no gaps, span == coverage,
  so the two definitions are the same number and the wrong one looks right.

  The generalised rule: **when adding a derived figure, ask what the fixture's
  regularity conceals, not only what its duration conceals.** Every property that
  makes a fixture convenient — a whole year, no gaps, one timezone, a constant
  sampling interval, no meter resets, positive prices throughout — is a degenerate
  case in which some wrong formula agrees with the right one. Enumerate the
  properties, then write the anchor test against a shape the fixture does *not*
  have: gappy, short, irregular, reset mid-series. A test that only runs on the
  convenient fixture cannot distinguish the definitions the convenience collapsed.

## Summary card (decided, with rationale)

- **The headline names the best investment, and claims nothing more.** It is the
  largest element and it is actionable — but the wording is constrained by what the
  numbers support, which took a rewrite to get right. "X kWh is enough for this house"
  was wrong in three of five cases: it asserted *sufficiency* the tool never measured
  (5 kWh gives 59% self-consumption where 20 gives 98% — it is the best **investment**,
  not "enough"); it laundered `recommended_scenario`'s no-cost fallback into a
  recommendation of the *largest* battery, the exact trap this tool exists to expose;
  and it made a superlative claim over a single data point. `headline_for` now carries
  one sentence per case — "5 kWh pays back fastest" with a cost, "Savings flatten
  beyond 15 kWh" without one, a plain statement for a lone capacity, and an explicit
  negative when nothing paid off. **Emphasis obeys the same rule**: with no payback,
  no bar is highlighted, because a lit-up bar under a headline that declined to
  recommend a size is the picture contradicting the sentence.
- Savings, payback and cost are a subordinate stat row underneath — they make the
  headline credible, they are not the headline.
- **A bar panel is dropped when truncation would invert its meaning.** Past
  `_BATTERY_LIFETIME_YEARS` (20 y — the generous end of a home-battery warranty, so a
  payback beyond it is one the hardware is not expected to survive to deliver), the
  payback panel is replaced by a sentence naming the shortest figure. The trigger was
  the 60-day card: 91.7 / 126.6 / 181.0 years drawn as three near-identical stubs,
  implying the paybacks were similar when the longest was double the shortest. **A
  chart that misstates its own values is worse than no chart** — the clipped-bar
  treatment (break + detached stub) rescues one outlier, not a whole panel off-scale.
- **Two stacked panels, never a dual axis.** Savings and payback are different scales,
  and one plot with two y-axes would let the reader read a crossing point that is an
  artifact of how the two axes happened to be aligned. That is a fabricated finding in
  a tool whose only selling point is that its numbers are real. Stacked panels on a
  shared capacity axis tell the same story — savings rise and flatten, payback climbs
  away — off two shapes rather than one invented intersection.
- **Emphasis, not a second hue.** One colour per panel; the recommended bar at full
  strength, the rest at 0.32 alpha. The two hues (blue savings / orange payback) were
  validated for colour-vision deficiency against the card surface rather than eyeballed
  (worst pair ΔE 24.7 protan, 33.6 normal vision, OKLab x100).
- **DejaVu Sans only, set through an `rc_context`.** It is the one family matplotlib
  bundles in its own wheel, so the card renders identically on a stranger's machine.
  The rc matters and is not tidiness: matplotlib *regenerates* tick labels whenever the
  locator reruns (`set_xticklabels`, `set_ylim`, `axhline` all trigger it), so a family
  stamped on the artists that exist at styling time is silently lost by the ones
  actually drawn — they fall back to the "sans-serif" alias and resolve to whatever the
  reader has installed. `rc_context` also keeps the fix off the importing process's
  global rcParams. Caught by a test, not by looking at the picture.
- **Honesty constraints, each pinned by a mutation-checked test:** the seasonality
  warning is drawn *on the card* as a filled band (the card travels without the report,
  so a 60-day result must not be screenshottable as a year); payback keeps one decimal
  and is never rounded into a friendlier number; the tariff is always printed, via the
  shared `describe_tariff`, because savings without the prices that produced them cannot
  be checked by anyone.
- **Negative savings are drawn below zero, not clipped to an empty panel.** Under a
  feed-in tariff more generous than the import price the battery loses money at every
  capacity — real, and exactly the result this tool exists to be willing to report.
  A zero-anchored axis would render those bars as nothing at all, i.e. an empty chart
  beside a headline saying the battery lost money. The zero rule is then drawn and the
  bottom spine demoted to gridline weight, so the emphasis follows the meaning.
- **Very long paybacks are clipped with a visible break, never a flat top.** Left
  unclipped, one 300-year bar flattens the rest into the baseline. Clipped flat, three
  capped bars read as *equal* — a worse misreading than the crowding. The bar is cut by
  a surface-coloured band with a detached stub above it, and the true figure is
  labelled. Only triggers past 1.25x the cap, so a 41-year bar is not distorted for
  nothing.
- **Fixed x-slots (`_MIN_SLOTS = 4`).** Left to matplotlib's own limits a
  single-capacity sweep draws one bar spanning the whole panel, which reads as a
  progress meter rather than one point in a comparison.
- **The payback panel is dropped when there is no payback to draw** — no battery cost,
  or no positive savings anywhere. Either way the savings panel takes the full height
  instead of sharing it with a title over a row of "never".
- **No recomputation.** Every figure comes off `AnalysisResult` through the same
  `annualization_years` the report uses. A card and a report disagreeing about the same
  run would discredit both, and arithmetic in two places is how that happens.
- Built entirely on matplotlib's OO API with an explicit `FigureCanvasAgg`; pyplot's
  global figure registry is never touched, so a long-running caller leaks nothing.
- **Project identity is one constant, in `__init__.py`.** `REPO_URL` /
  `REPO_DISPLAY_URL` / `PROJECT_NAME` are imported by the card footer and the report
  header; neither retypes them. The URL is the project's *only return channel* — a
  reader holding a screenshot has nothing else — so a wrong one does not degrade the
  artifact, it makes it worthless, and the mistake is invisible to everyone who already
  knows where the repo is. `[project.urls]` in pyproject is the one place that must
  repeat the string (packaging metadata cannot import from its own package), and a test
  pins it against the constant. Tests also assert the literal correct URL, so renaming
  the account cannot make them quietly agree with a new mistake.
- **Bars are always labelled — a rule, not a per-case decision.** Labelling only the
  recommended bar left the others mute, so the reader had to walk each one back to a
  gridline to find its value. That is the arithmetic the card exists to have already
  done, and worse, the chart's actual argument is the *gaps* between capacities
  (+79 EUR from 10→15 kWh, +20 from 15→20), which cannot be read off two bars when
  neither states its number. Emphasis then does what it was always for: the
  recommended label is bold and full-ink so it still reads first, rather than being
  the only label that exists.
- **Headroom is sized for the labels, and only on the side that has them.** Every bar
  being labelled makes the tallest bar's clearance the ordinary case, not a special
  one; without it the number is drawn outside the axes and clipped, and a bar landing
  on the frame reads as truncated. `_LABEL_HEADROOM` (0.15) goes on the labelled side,
  `_EDGE_MARGIN` (0.04) on the other. **Zero anchors the empty side**: the losing card's
  axis used to run to +200 with nothing in it, a fifth of the panel spent where the
  finding is not, flattening the losses it existed to show. Positive and negative
  panels are therefore *not* padded symmetrically — a test asserting they were was
  wrong and re-introduced the dead band.
- **Colour carries the sign.** Bars descending to −1,254 EUR were drawn in the same
  light blue as bars earning +462, leaving direction to be read off the axis, which is
  the slowest thing on the panel. Below zero the bars use a desaturated red (`_LOSS`);
  it is deliberately not saturated, because the headline is already saying the battery
  lost money and a loud red would out-shout the verdict it illustrates. Sign is the one
  thing colour encodes here — it is not a category, it is the threshold the card is about.
- **With nothing recommended, alpha depends on the case, and one rule was wrong for
  both.** Neither the losing card nor the no-cost card emphasizes a bar. On the losing
  card the bars *are* the finding, and 0.32 alpha leaves a row of ghosts reading as
  tentative about a result stated outright — full strength. On the saturating card the
  finding is the *shape* of the curve; four bars at full strength say nothing more and
  start competing with the headline — receded.
- **The stat under the headline must support it, never undercut it.** With no cost the
  headline says "Savings flatten beyond 15 kWh" and the stat row printed 462 EUR — the
  20 kWh figure, i.e. the size the headline was implicitly advising against. The two sit
  two centimetres apart and are read as one statement, so whichever the reader believed,
  the card had told them the other. `saturation_stat` now prints the marginal gain
  ("+20 EUR / per year from 15 kWh to 20 kWh"): the flattening-point's own savings would
  merely be *consistent*, whereas the marginal gain **is** the flattening and is a number
  no other element carries. A gain that rounds to zero is printed as the word "Nothing",
  because "+0 EUR" in the card's second-largest text reads as a figure that failed to
  compute rather than as the strongest form of the finding.
- **The headline never spends its space on a repeat.** The single-capacity card read
  "10 kWh pays back in 16.5 years" directly above "16.5 years / to pay back". Headline
  space is the scarcest resource on an artifact that gets three seconds, so it goes to
  what the stats cannot say: "10 kWh — the only size analysed", i.e. that no comparison
  stands behind any number on the card. That caveat is invisible in a stat row and
  changes how everything below it should be read.
- **`bars_of()` in the tests reads bars off `axes.containers`, not `axes.patches`.**
  A panel's patches include the clipped-bar break and its detached stub, so measuring
  patches as bars silently corrupts any geometry assertion; `patches` is also typed as
  `Patch`, which has neither `get_height` nor `get_width`, and mypy and Pyright disagree
  about whether an ignore for that is warranted. One narrowing helper removes both
  problems. A first attempt filtered on `get_label() == "_child0"` — matplotlib actually
  labels them `_nolegend_`, so it returned an empty list and every assertion passed
  vacuously. **A filter that silently matches nothing is how a green suite tests nothing.**
- **`scripts/render_sample_cards.py` renders the fixture plus all four edge cases** into
  the git-ignored `scratchpad/cards/`, printing absolute paths. It exists because the
  card's layout defects are the class of bug the suite structurally cannot catch:
  clipped headlines, colliding labels, bars that read as equal. Looking has to be one
  command, or it silently stops happening.

## README (decided, with rationale)

Constraints fixed before the README exists, gathered here because they were
scattered across session log entries and an untracked handoff document — and
whoever writes the README will read neither.

- **solar-report's README is the house style.** The two projects cross-promote
  and must read as written by the same hand. Only Configuration differs: YAML +
  pydantic there, CLI flags here.
- **A `## Disclaimer` section**, in prose: retrospective estimate, not financial
  or energy advice; results depend entirely on the quality of the user's data;
  the model ignores degradation, price inflation, incentives and installation
  costs; verify with a professional before any purchase. "Limits & assumptions"
  is the technical half of the same statement and stays where it is.
- **No dedicated `## Why not battery_sim?` section.** Reversed on 17 August,
  after the drafted section read like a commercial comparison page — a table of
  four axes and a closing recommendation, a register the rest of the project
  does not use. The comparison instead sits inside the "Retrospective, not
  predictive" bullet of `## Why`, which is the axis of the difference anyway:
  one mention, one link, no verdict. Two things must survive there, because
  they are the load-bearing parts. That battery_sim runs **forward and live**
  while this runs backwards over data already collected — the reason someone
  picks one over the other. And that both model the **same physics** (usable
  capacity, power limits, round-trip efficiency), which is what stops this
  reading as the toy next to the serious integration. **Known cost of the
  reversal:** the question still gets asked on r/homeassistant, and now the
  answer is not findable by skimming headings or by searching "battery_sim
  alternative". Accepted deliberately; do not re-add the section without
  revisiting this.
- **The card screenshot uses a flat tariff, not the Italian bands.** The footer
  names the tariff scheme but never the timezone, so "Italian bands" on foreign
  data suggests a frame the run may not have had.
- **Same reason, stated rather than avoided: the README must say the example
  data is from Konstanz, southern Germany, in `Europe/Berlin`** (OPSD households
  residential4 and residential6). The footer cannot carry it and is not being
  changed.
- **OPSD attribution is required** (CC BY 4.0), in the README as well as beside
  the images in `docs/assets/README.md`.
- **Docker**: the terminal prints *container* paths, so the README must state the
  volume mapping and show `--user $(id -u):$(id -g)`, without which the user gets
  root-owned files.
- **Image size, honest line**: multi-stage buys no pip and no compiler in the
  runtime, not megabytes — 419 MB in both stages, because the scientific wheels
  are 289 MB of it.
- **No `pip install battery-worth`**: it is not on PyPI. Source only.
- **Limit to declare**: self-consumption can be distorted by corrupt meter
  readings, and v0.1 does not detect them.

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

## Home Assistant export (decided, with rationale)

- **battery-worth does NOT integrate with Home Assistant. It ships a separate
  script.** `scripts/ha_export.py` runs once against the user's own instance and
  writes the canonical CSV `ingest.py` already accepts. Three reasons, in order of
  weight: the "100% offline" claim stays true **without asterisks**, which is a
  positioning claim the README makes and must not have to qualify; `ingest.py`
  gains no network and no auth surface; and if HA's API changes, **one script
  breaks rather than the tool**. If direct integration is ever asked for, this
  script is the logic ready to be promoted — the decision is reversible in that
  direction and not in the other.
- **The Energy Dashboard CSV export was evaluated and rejected as a target.** It is
  transposed (timestamps as columns), its resolution depends on what the user had
  selected in the UI, and it carries two confirmed upstream bugs (a UTC/local offset
  in the header, and monthly exports off by one day). Do not build against it. The
  README says so explicitly, because it is the button a user will find first.
- **Standard library only — no new dependency, and no optional extra either.** The
  two allowed options were stdlib or `battery-worth[ha]`; stdlib won because the
  needed client surface is tiny and one-directional (connect, auth, send N requests,
  read N replies, close), and none of the hard parts of WebSocket apply — no
  streaming backpressure, no permessage-deflate, no subprotocol negotiation, no
  concurrent readers. An extra would have put an install step in front of exactly
  the users this script exists to serve. The cost is ~120 lines of hand-written
  RFC 6455 framing, which is contained by keeping `encode_frame`/`decode_frame` pure
  and testing them directly rather than trusting an end-to-end run that never
  happens in CI.
- **`types: ["change"]`, so there is no cumulative diffing anywhere.** `change` is
  the per-interval delta; `sum` is the cumulative total at period end. A test feeds
  a payload carrying **both** and asserts only `change` is read, so a future edit
  cannot quietly start diffing a running total.
- **Statistic timestamps are epoch MILLISECONDS and mark the START of the period.**
  Both are pinned by tests, and both are the kind of assumption that fails silently:
  ms read as seconds lands in the year 55943, and an interval-ENDING reading would
  shift every row by an hour against `ingest.py`'s interval-starting convention.
  ISO strings are also accepted, for cores old enough to emit them.
- **Chunked by calendar month, because HA can cap or time out on a year in one
  call.** Windows are half-open and contiguous — each ends exactly where the next
  begins — and `merge_rows` de-duplicates on top of that, so an instance that treats
  its own `end_time` as inclusive cannot double-count a boundary hour.
- **The token is never logged, echoed, or written to disk.** `HA_TOKEN` is the
  documented path and `--token` the fallback, because a token passed as a flag lands
  in shell history. The help text says that outright rather than leaving it implied,
  and a test asserts the token never appears in an error message.
- **A wrong statistic_id and an empty period look identical from the response**, so
  on an empty result the script asks HA for `recorder/list_statistic_ids` and prints
  what the instance actually has. That call is best-effort: it only ever improves an
  error message, so it never turns a failed export into a second failure.
- **Two output defects were found by running the script, not by the suite** — the
  same split as the card. Multi-line errors were prefixed `error:` on every line, so
  one failure read as five; and progress printed *after* the error that stopped it,
  because stdout is block-buffered when piped while stderr is not. Both are invisible
  to a test that inspects an exception, and both are what the user actually reads.

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

> Entries are chronological. The first session of a day is unnumbered;
> later sessions the same day are (2), (3), ... References below name the
> date when the number alone is ambiguous.

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
- **2026-08-11 (6)** — **Milestone 2 closed.** PNG summary card (`card.py`, 25 tests) +
  `--card/--no-card` wiring. Suite 171 → 200, ruff and mypy strict clean. Design
  rationale is in the new section above.

  *Every layout defect was found by rendering and looking, not by a test.* The first
  render clipped the headline at "5 kWh is enough for this" — the fitting rule was a
  character-count guess, and a character budget is a guess about average glyph width
  that guesses wrong on exactly the strings that matter (bold 54pt "12.5 kWh…" is far
  wider than three extra characters). It now measures the rendered width and steps down.
  Four more of the same kind followed: the panel title landing on the stat labels, the
  x-axis title on the footer rule, the single-capacity bar filling the panel, the
  clipped payback bars reading as three equal 40-year batteries. **A test suite can pin
  what the card says; only looking at it pins what it shows.** Both got used here, and
  the split is worth carrying into M3's README screenshot work.

  *One real portability bug came the other way — from a test the picture could not
  show.* The font assertion, once widened to `figure.findobj(Text)`, found the visible
  "5 kWh" tick labels rendering in the `sans-serif` alias rather than DejaVu: matplotlib
  regenerates tick artists on every locator pass, so the per-artist family set during
  styling was discarded before the draw. It looked correct on this machine precisely
  because the alias resolves to DejaVu here — it would have diverged on a reader's.
  Fixed with `rc_context`. The lesson pairs with session (5)'s: **an assertion that only
  walks the artists you remembered to create cannot catch the ones the library creates
  for you.** The narrow walk passed; `findobj` failed.

  Verified on the Ausgrid fixture through the CLI: card and report agree to the digit
  (5 kWh · 211 EUR/yr · 14.2 y), reproducing session (3)'s figures again. Thumbnail
  test at 400x400 passes — headline and both stat values still legible. Degenerate
  cases rendered and inspected individually: no battery cost (payback panel and stat
  both dropped, recommendation falls back to largest savings at 20 kWh), single capacity
  (centred, slot-width bar), 60-day period (warning band + all three paybacks clipped
  with true values labelled), and a losing tariff (−598/−1,031/−1,254 EUR/yr drawn below
  a zero rule under "No battery paid off here"). The seasonality warning and the
  no-flattering-rounding rule were mutation-checked.

  **Next: Milestone 3** — HA long-term statistics parser, optional LLM layer, README
  with the real card screenshot, Dockerfile, launch posts. Per session (3)'s note, check
  what M3's *last* step needs before assuming the milestone is self-contained: the
  README screenshot depends on the card, which now exists, but the launch posts depend
  on the README.
- **2026-08-11 (7)** — **Wrong repo URL on the card, fixed before first commit.** The
  card shipped `github.com/marcoconti/battery-worth`; the account is `contimarco77`.
  Suite 200 → 203.

  Worth recording because of *how* it nearly shipped: I wrote the placeholder, flagged
  it in the handoff as "worth a look", and moved on — treating a wrong string in the
  one artifact designed to be seen by strangers as a cosmetic loose end rather than as
  a defect. It is the opposite: the URL is the project's only return channel, a dead
  link makes every posted card worthless, and it is invisible to everyone who already
  knows the repo. **Flagging a known-wrong value is not the same as fixing it, and
  "placeholder" is not a severity.**

  Now one constant in `__init__.py`, imported by both renderers (the report template's
  header carried the same wrong URL — the grep found a second copy I had not put on the
  list). `[project.urls]` added to pyproject, which had no URL metadata at all. Three
  tests: the literal correct URL on the card, card and report agreeing, and pyproject
  matching the constant. Mutation-checked by reintroducing the old account — two tests
  fail.

  Also from the same review: **artifact paths must be stated, not implied.** The cards
  had been rendered into a session-temp directory and described in prose, which is not
  a deliverable anyone can look at. `scripts/render_sample_cards.py` now writes all five
  to `scratchpad/cards/` with self-describing names and prints absolute paths.
  Confirmed by direct CLI runs that `--card` is on by default, `--no-card` suppresses it
  silently, and both artifact paths are named in the terminal.
- **2026-08-11 (8)** — **Payback was never annualized.** Suite 203 → 213.

  The card printed "199 EUR saved per year" beside "91.7 years to pay back" for a
  3,000 EUR battery, where 3000/199 is 15.1. `payback_years()` divided cost by
  *period* savings while the savings figure beside it was annualized, so on any file
  shorter than a year payback was overstated by 365/days. The report and the terminal
  shared the model and had it too; the export-price grid computed its own and had it
  independently. Fixed in the domain layer — see the new "Annualization" section
  above, which is the durable lesson and should be read before adding any derived
  figure.

  Two things about how it was found. It was **spotted by dividing two numbers printed
  side by side on the card**, not by a test — the card put savings and payback next to
  each other and made the contradiction arithmetic a reader could do in their head.
  And it is the second uniform-error bug here, so the suite's shape is now the
  documented risk, not just this instance.

  Same review also corrected two overclaims in the card's own language (headline
  wording, and dropping the payback panel when truncation inverts it) — rationale in
  the Summary card section. Notable that both defects were *arguments the picture was
  making*, not defects in what it computed: "5 kWh is enough" and three equal-looking
  bars were each a true dataset rendered into a false claim. **Rendering is where a
  correct number becomes a wrong statement, and the suite does not look there.**

  All five cards regenerated to `scratchpad/cards/`; mutation-checked by reintroducing
  the period-savings division (4 tests fail, including the period-invariance one).
- **2026-08-11 (9)** — **Card polish: five defects, all in what the picture says rather
  than in what it computes.** Suite 213 → 232, ruff and mypy strict clean. No engine
  changes — `analysis.py`, `simulator.py` and `models.py` untouched. Rationale for each
  is in the Summary card section above.

  Mute bars, no headroom, losses in the savings colour, and two cards whose headline
  argued with the figure directly beneath it (the flattening headline over the largest
  battery's savings; a payback headline over the same payback restated as a stat).
  Every one of them was a *correct number rendered into a misleading statement*, which
  is the failure mode session (8) had just finished documenting — and none of the 213
  existing tests could see any of them, for the same reason as before.

  Two things worth carrying forward, both about the tests rather than the card.

  *Symmetric assertions encode assumptions the design deliberately breaks.* The first
  headroom test demanded clearance above **and** below every panel, which fails on an
  all-positive panel — correctly, because item 3 had just removed exactly that padding
  as dead space. The test was asserting the bug. Clearance belongs on the side that
  carries the labels, and nowhere else.

  *A filter that matches nothing turns a green test into no test.* `bars_of()` first
  selected patches by `get_label() == "_child0"`; matplotlib labels them `_nolegend_`,
  so it returned an empty list and every bar assertion passed vacuously. Caught by
  printing the labels instead of trusting the guess. It now reads bars off
  `axes.containers`, which is where `bar()` actually registers them.

  All five cards regenerated and inspected individually: no unlabelled bar, nothing
  touching an axis edge, and each headline consistent with the figure under it.
  Mutation-checked on three independent reversions — re-muting the non-recommended
  bars (2 fail), restoring the single savings hue (1 fail), and zeroing the headroom
  (6 fail).

  **Next: Milestone 3** — unchanged by this session. HA long-term statistics parser,
  optional LLM layer, README with the real card screenshot, Dockerfile, launch posts.
- **2026-08-11 (10)** — **`py.typed` was missing: the tests were never type-checked.**
  Suite 232 → 233, and `mypy src tests scripts` is now clean where `mypy tests scripts`
  reported 25 errors.

  All 25 were one cause. PEP 561 requires a `py.typed` marker for a package's
  annotations to be honoured by type checkers, and `src/battery_worth/` had none, so
  every `from battery_worth... import` in the tests raised `import-untyped`. The error
  count was the harmless half. **The damaging half was silent: mypy degraded every
  imported symbol to `Any`**, so a strict-mode project's own test suite was effectively
  unchecked — wrong argument types, wrong return types and misuse of the engine's API
  from the tests would all have passed. Verified after the fix by probing that
  `headline_for` now resolves as `list[ScenarioResult] -> str` and that passing a `str`
  is rejected.

  Worth recording as a pattern, because it is the same shape as sessions (5) and (8):
  **the signal was visible and had been read as noise.** The 25 errors were dismissed as
  "preexisting, unrelated to my changes" — true of their origin and irrelevant to their
  cost. A checker that reports a wall of identical import errors is not a checker with
  25 small problems; it is a checker that has stopped looking, and the thing it stops
  looking at is everything downstream.

  The marker is one empty file, ships in the wheel via the existing
  `packages = ["src/battery_worth"]` (verified by building one and inspecting its
  contents), and is pinned by a test — an empty file is exactly what a packaging
  refactor deletes without anything failing. Mutation-checked both ways: removing it
  fails the new test and restores all 25 mypy errors.

  *Known and left alone, deliberately:* the `pydantic.mypy` plugin runs without
  `init_typed`, so model constructors accept coercible-but-wrong argument types
  (`BatterySpec(usable_capacity_kwh="grande")` type-checks). Preexisting, out of scope
  for this fix, and worth a decision of its own — enabling it will surface real call
  sites rather than being a no-op.
- **2026-08-11 (11)** — **Bar labels drawn outside their panel; `ruff format` added as
  a gate.** Suite 233 → 242, all four gates clean.

  *The label overrun was a unit mismatch, not tick rounding.* The 60-day card's savings
  axis ran to 347.9 against a 303 bar — the headroom was there — yet "303 EUR" was drawn
  3px past the top of the panel. `_pad_range` reserved headroom as a fraction of the
  **data span** while labels are placed at a fixed 7pt offset in **screen space**, so how
  many pixels 15% buys depends on the panel's pixel height. The seasonality band makes a
  partial-year card's panels shorter (208px vs the fixture's 259px), which is what turned
  a 3.5px clearance on the fixture into a 3px overrun. Fixed by measuring the rendered
  label extent and converting to data units per panel, with the fraction kept as a floor.
  The payback panel's `29.7` had the same defect on the same card, and the clipped-bar
  label — which sits at double the gap to clear the break stub — needed its own allowance
  or the fix would have reintroduced the bug on the bar most likely to hit it.

  *Two reported defects turned out not to exist, and measuring first is why.* Bold vs
  regular label offsets were reported as inconsistent; they measure identically (9.72px
  on every ausgrid label), because DejaVu Bold and Regular share vertical metrics and
  `va="bottom"` puts the baseline in the same place. The negative-label gridline overlap
  was likewise absent — the nearest gridline clears each label by 13-36px on the real
  fixture, before any change. I had already written a `_NEGATIVE_LABEL_EXTRA_PT` nudge
  and a test for it before the measurement came back, and reverted both. **A plausible
  mechanism stated in a bug report is a hypothesis, not a finding**; shipping the fix
  anyway would have left an unexplained magic constant defended by a test that could
  never fail.

  *The new test measures pixels, because that is where the defect lives.* The existing
  headroom test compares bar heights to y-limits in data units and passed throughout —
  the bar was inside the limits while the text above it was not. `axis_overruns()` draws
  the figure and compares every bar's and every label's rendered extent against the axes
  box. Mutation-checked: three tests fail on the old renderer. The 303-against-300 case
  needed the 60-day period pinned as well as the value — at 365 days it passes on the
  broken code, since the awkward maximum alone does not reproduce it without the shorter
  panel. **A regression test for a geometry bug has to pin the whole geometry.**

  *`ruff format --check` added as the fourth gate* — see the new "Definition of done"
  section. Formatting drift had accumulated across 12 files because only `ruff check`
  was ever run. Applied in its own commit (`7cfad6a`, 519 lines) kept separate from the
  card fix; verified formatting-only by comparing the AST before and after per file,
  which is stronger than reading the diff. Five of six flagged files are AST-identical;
  the sixth differs only in three docstrings where the formatter inserted a space after
  the opening `"""` on strings whose text begins with a quote character (`""""Pays` →
  `""" "Pays`), a required disambiguation that touches no assertion.
- **2026-08-12** — **Home Assistant export, as a standalone script rather than an
  integration.** `scripts/ha_export.py` + 76 tests. Suite 242 → 318, all four gates
  clean, and `mypy --strict` clean on the script and its tests as well as on `src/`.
  The decision and its rationale are in the new "Home Assistant export" section; the
  short version is that the offline claim stays unqualified and `ingest.py` gains no
  network or auth surface.

  *No new dependency, not even an optional extra.* The WebSocket client is ~120 lines
  of stdlib RFC 6455. That is the part of this change most likely to be wrong, so the
  framing is kept pure (`encode_frame` / `decode_frame` over bytes, no socket) and
  tested directly — including the 7/16/64-bit length boundaries, partial buffers, and
  the unmasked server-to-client direction. Nothing in the suite mocks a socket, per
  the brief: every test drives a pure function with a recorded-shape payload.

  *The format assumptions are the tests worth having.* Epoch **milliseconds** (read as
  seconds, 2024-01-01 becomes the year 55943), interval-**starting** timestamps that
  must not be shifted, and `change` rather than `sum` — with one payload carrying both
  fields to pin that no cumulative diffing creeps back in. Each of these fails
  silently rather than loudly, which is the argument for asserting them at all.

  *Verified end to end without an instance*, by generating a synthetic year in HA's
  exact wire shape and pushing it through the real parsing and CSV writer: 12 monthly
  chunks → 8784 rows (2024 is a leap year), every consecutive pair exactly 3600 s
  apart, no gaps and no duplicated boundary hour. The resulting CSV feeds
  `battery-worth analyze` with no intermediate step — schema auto-detected as
  `grid_centric`, 366 days, native resolution read as 60 min. That round trip is what
  the whole design rests on, so it was worth running rather than assuming.

  *Two defects came from running the script, not from the suite.* Multi-line errors
  were prefixed `error:` on every line, so a single failure read as five separate
  ones; and progress output landed *after* the error that stopped it, because stdout
  is block-buffered when piped while stderr is not. Both are in what the user reads
  rather than in what the code computes — the same blind spot sessions (6) and (9)
  documented for the card, now confirmed to apply to terminal output too. **A test
  that inspects an exception object cannot see how the message is printed.**

  **Next: the rest of Milestone 3** — optional LLM layer, README card screenshot,
  Dockerfile, launch posts. Per session (3)'s standing note, the launch posts depend
  on the README, which now has the HA section but still not the screenshot.
- **2026-08-12 (2)** — **Pre-launch audit.** Suite 318 → 321, all four gates clean.
  Two fixes, both cheap; everything else reported rather than changed.

  *`days_analyzed` measured calendar span, not coverage* — the third bug of the shape
  documented in the Annualization section, found by asking what the fixture's
  *regularity* hides rather than what its length hides. Details and the durable rule
  are in that section. Mutation-checked: reverting to the span count fails the new
  test with 371 == 65. All four Ausgrid capacity rows still reproduce session (3)'s
  figures exactly (211/14.2 · 363/16.5 · 442/20.4 · 462/26.0), so the fix moved no
  correct result.

  *An unknown `--timezone` blamed the CSV.* `ZoneInfoNotFoundError` subclasses
  `KeyError`, so it fell through to the ingest handler and printed "Could not read
  '<file>'", sending the user to inspect a file that was fine. Validated up front
  where the message can name the flag.

  *The "anchored to the input file" claim was weaker than written.* No test loads the
  Ausgrid CSV — the fixture lives outside the repo, so `test_a_full_year_annualizes_to_itself`
  asserts against hand-typed constants, and `AUSGRID_IMPORT_KWH` is itself derived from
  the other two through our own balance equation. That is still a real anchor (the
  numbers came from the file once, by hand) but it is a *transcribed* one: it cannot
  detect ingest drift, and nothing re-checks the transcription. Left as-is deliberately
  — the honest fix is a committed small real-data fixture, which is a decision, not a
  cleanup.
- **2026-08-12 (3)** — **Four pre-launch fixes from the audit.** Suite 321 → 328, all
  four gates clean.

  *`--cumulative` / `--no-cumulative` added to the CLI.* The auto-detector's warning
  told the user to "Pass cumulative=False" — a Python keyword argument, from a tool
  almost everyone meets through the command line. **An escape hatch that exists only
  in an API the user is not using is not an escape hatch; naming a flag that does not
  exist is worse than naming none.** The flag is three-state (`None` auto-detects per
  column, `True`/`False` force it), and the warning now differs by *who decided*:
  detection names the flag that undoes it, an explicit `--cumulative` says it overrode
  detection rather than telling the user their data "looks like" something they had
  just asserted it was.

  The case that justifies the override is worth stating precisely, because it is not a
  detector weakness that could be engineered away: **a per-interval column that never
  decreases is mathematically indistinguishable from a meter reading.** Both are
  non-decreasing sequences of positive numbers. No heuristic separates them, so only
  the person who exported the file can. `test_a_rising_interval_column_is_indistinguishable_without_the_override`
  pins it as a property of the data — the same file yields ~1.0 kWh/h with the override
  and ~0.0007 without, three orders of magnitude decided entirely by the flag.

  *`_scenario_row` recomputed `savings_eur / years`* where the report used
  `annual_savings_eur`. Identical today; it is the exact duplication shape that produced
  the session (8) payback bug, and the Annualization section's own rule forbids it.
  Fixed by using the model property.

  *README's status line disclaimed a finished engine* ("pre-alpha, engine under
  construction") — a launch post pointing at a README that undercuts its own subject.
  Rewritten to state what exists and what does not, in both directions: no LLM layer,
  no Docker image, no native HA/inverter parser, nothing on PyPI. An earlier draft said
  "install from source", which the README has no instructions for — a status line must
  not create a second false claim while fixing the first.

  *Gaps-as-zero was warned about at ingest but absent from "Limits & assumptions".* The
  ingest warning fires only when a run *has* a gap; the Limits section describes how the
  tool works regardless, and a reader deciding whether to trust these numbers on their
  own data needs it before they hold a file with a hole in it. Added to the report and
  the terminal, with a test in each — the two caveat lists mirror each other and drifting
  apart is how a caveat ends up documented only in the artifact nobody generated.

  *The card footer was checked and deliberately left alone.* It carries period, tariff
  and one honesty line, and its constraint is space: the card gets three seconds and
  every line competes with the verdict. Gaps-as-zero is a conditional caveat about a
  data shape most files do not have, where the seasonality warning it would sit beside
  is drawn only when it actually applies. A permanent line about a hypothetical gap
  would cost the same room for less. The card's period line already states the days
  analysed, which — since the 2026-08-12 (2) fix — counts *covered* days, so a gappy export
  shows a smaller number there rather than silently spanning the hole.

  *One gate finding worth keeping:* `ruff` flagged `load_energy_data` at 13 branches
  after the two-message split. That is the linter noticing the function had accumulated
  a second concern, not a threshold to appease — the cumulative handling moved to
  `_difference_cumulative_columns`, which is where the reasoning about the three states
  now lives.

  *Recorded, not started (post-launch decisions, not cleanups):* the trimmed real-data
  fixture (audit section 2 — the honest fix for the transcribed-anchor problem noted in
  the 2026-08-12 (2) audit above), the duplicate "days" definition shared by simulator and ingest,
  and the `OSError` handler in `ha_export`.

  **Next: Dockerfile (multi-stage), then the README** with the real card screenshot
  and the "Why not battery_sim?" section, then launch posts. The optional LLM layer
  is deferred to v0.2 — decision and rationale in "Locked decisions". Per the
  standing note from 2026-08-11 (3), the launch posts depend on the README.
- **2026-08-13** — **Docker image, and a timezone default that hid a total platform
  failure, and an entry that named four commits for a ten-commit day.**
  **Ten commits**, in two halves. Docker and timezone: `dbd405e` (session
  cross-references), `18a430e` (`tzdata` declared), `bfb5197` (the image),
  `2d8fba3` (the analysis timezone stated as a caveat), `b629c81` (this entry, as
  first written). Then the afternoon: `a3677a5` (the liability position),
  `c949a8a` (pandas' mixed-offset error replaced with one the user can act on),
  and three rounds on the summary card — `54878ab`, `416938a`, `1295d50`,
  described at the end of this entry. Suite 328 → 334 across the Docker half,
  354 by the end of the day, all four gates clean.

  *This entry named four of the ten, because it was written at 11:46 and the day
  continued until 17:52.* It was amended on 14 August, after `git log`
  contradicted the handoff document that had carried the same count forward.
  **A log entry written mid-session records a session that has not happened
  yet**; one entry per day survives as a convention, but the entry is written
  last.

  *The brief's premise about slim images was false, and measurement caught it.* The
  Docker task asserted that Debian slim images ship no tz database, and gave that as
  the reason to declare `tzdata`. `python:3.11-slim-bookworm` ships one — the path is
  present, `dpkg -l` lists the package, and an image built without the PyPI wheel
  resolved Europe/Rome anyway. The dependency was kept because it is right, and its
  stated justification in `pyproject.toml` was rewritten to what actually holds: belt
  and braces in the image, load-bearing off it. The durable point is the alternative.
  Had the premise gone unchecked, the repository would carry a comment asserting
  something the project's own image contradicts, and **no test could have noticed,
  because it is a claim about an environment rather than about a value.**

  *The first counterfactual measured the opposite of what it was cited for.* The
  initial check deleted the Debian `tzdata` package while the PyPI one was still in
  the venv, so `zoneinfo` used the fallback. That run was reported as a third proof
  that the base image carries a tz database; it is in fact a proof that the newly
  added dependency works. Two variables moved and only one was named. Same shape as
  2026-08-11 (11), but from the writing side rather than the reading side: there, a
  plausible mechanism in a bug report was mistaken for a finding; here, a plausible
  mechanism was written up as a measurement. **A counterfactual that leaves a second
  path to the same result measures nothing.**

  *`tzdata` was never about the Italian bands.* The brief scoped it to F1/F2/F3
  because the bands are defined in Europe/Rome. But `ingest` localizes with the
  default timezone on *every* run, so `ZoneInfo("Europe/Rome")` is called whatever
  tariff is chosen: on Windows every invocation raised, flat tariffs included, and
  the CLI reported it as an unknown `--timezone` for a flag the user never passed. A
  total platform failure was scoped as a degraded regional feature. The evidence was
  in this session's own output — `index tz after ingest: Europe/Rome` on a run with
  no flag and no bands — and was read past twice. **The same shape as 2026-08-11 (10):
  the signal was visible and had been read as noise.**

  *A guard that could not fire on the path that needed it.* `_warn_if_not_italian`
  inspects `index.tz` after ingest, but a defaulted run has already localized to
  Europe/Rome, so the check sees an Italian zone whatever the data is. It caught
  users who correctly declared a foreign timezone — the ones who already knew — and
  was silent for everyone who declared nothing. Confirmed: Australian fixture, flat
  tariff, no flag, zero warnings. **The default overwrote the fact the check
  inspected.** Fifth instance of a check reporting success because it had stopped
  looking, after 2026-08-11 (10)'s `py.typed`, the `ruff format` gate, and
  `bars_of()` matching nothing.

  The fix's shape is the part worth carrying. Making the warning fire on an
  undeclared timezone would have fired on the happy path, and a warning that lights
  up on the normal path stops being read. So the conditional warning is untouched and
  a permanent statement went into "Limits & assumptions" instead, following the
  gaps-as-zero precedent from 2026-08-12 (3). Expressing "assumed" required moving
  the `--timezone` default off parse time, so that "the user chose Europe/Rome" and
  "nobody said" stay distinguishable. That refactor is in the same commit as the
  caveat deliberately, rather than split into two: it has no justification
  independent of it.

  *Container verification, in order of how much it carries.* The container-rendered
  card is byte-identical to the host's, 1200x1200, DejaVu Sans — worth stating
  because 2026-08-11 (6) found a font that resolved correctly only on this machine,
  and this is the first evidence from a different one. `MPLCONFIGDIR` is
  load-bearing, confirmed by counterfactual: without it matplotlib warns on stderr
  outside the CLI's `warnings.catch_warnings` block, bypassing the report's WARNINGS
  section. `PYTHONUNBUFFERED=1` turned out **not** to be load-bearing — ordering
  holds without it because `_fail` already flushes — and was kept as defence rather
  than reported as the thing holding the order. Recording the negative matters: the
  2026-08-12 entry documents that ordering bug, and a future reader must not conclude
  the env var fixed it.

  Multi-stage saves 0.3 MB, not megabytes: 419.09 runtime against 419.39 builder,
  because the scientific wheels are 289 MB of the total. The win is no pip and no
  compiler in the runtime — the README will repeat it in those terms. The OCI source
  label would have been a third hand-written copy of the repo URL; it was added with
  the existing URL test extended to pin it, per 2026-08-11 (7). `ha_export.py` ships
  in the image as `ha-export` via `--entrypoint`, without moving into the package or
  into `[project.scripts]`.

  *Two constraints for the README, recorded as constraints rather than as tasks.*
  Terminal output prints **container** paths: `--output /out/report.md` reports
  `/out/report.md`, which lands wherever `-v` mapped it, so the README must state the
  mapping and must show `--user $(id -u):$(id -g)`, without which the user gets
  root-owned files. And **the card screenshot must use a flat tariff, not the
  bands**: the footer names the band scheme but never the timezone, so on the
  Australian fixture "Italian bands" actively suggests a frame a defaulted run may
  not have had — a correct number rendered into a misleading claim, the failure mode
  of 2026-08-11 (8) and (9). The footer is deliberately minimal and is not being
  changed; the screenshot avoids the ambiguous case instead. Revisit when the Italian
  PVGIS fixture lands.

  *The F1/F2/F3 figures produced during Docker verification (269/11.2 · 423/14.2 ·
  503/17.9 · 524/22.9) are Australian data priced on Italian bands with timestamps
  relabelled as Rome.* They are valid as a portability check and as nothing else.
  They must not be quoted anywhere as results.

  **Next: the README** — real card screenshot (flat tariff), the "Why not
  battery_sim?" section, the Docker path mapping and `--user` line. Then launch
  posts, which depend on it. The optional LLM layer remains deferred to v0.2.

  *Three rounds on the summary card, appended on 14 August.* The headline
  contradicted its own payback panel: two rules held different definitions of
  "pays back", the twenty-year threshold governing the panel while the headline
  was gated on savings being positive. On `residential6` the card read "5 kWh
  pays back fastest" above "No capacity pays back within 20 years". The split
  reached the headline, the emphasis, the stat row, the report's Verdict, the
  terminal summary, the Seasonal analysis intro, and a line advising a larger
  battery underneath a negative verdict. `54878ab` gives every verdict one
  threshold. **A definition duplicated across seven sites is not a duplication;
  it is seven definitions that happen to agree today.**

  *Two tests that passed under mutation.* The first expressed both of its bounds
  in terms of `_STATEMENT_BAND`, so raising the constant widened the tolerance
  exactly as fast as the defect; the second was a bare "does not exceed" that
  passed with 0.1px to spare. Both were found by measuring, not by reading the
  green. Panel height was unified across the drop paths and `_STATEMENT_BAND`
  resized, 0.115 → 0.068. Separately, `_fit_headline_size` returned the minimum
  after measuring that it did not fit — a silent surrender dressed as a
  guarantee — and `headline_of` in the tests took "the largest bold text", which
  grabbed a 32pt stat whenever the headline shrank: a helper that blinded the
  test in precisely the case it existed to cover.

  *A discrepancy attributed to the code was a stale file.* `residential6.png`
  predated the layout commit by sixteen minutes. `get_position()` was not lying.
  First of three stale-artifact diagnoses in two days; see 2026-08-14 for the
  other two and for the fix. A test fixture was also silently invalidated by a
  locator change, for the second time; it now searches for a value that
  reproduces the geometry and raises if it finds none, instead of trusting a
  hand-written constant.

  *A correct fix with an unmeasured side effect on the healthy path.* The
  integer locator made the axis labels honest — the gridline labelled "2" had
  been sitting at 2.5 — and doubled the gridlines on every sound card, from 4 to
  10 on `60_days` and 6 to 10 on `residential6`. The doubling was not reported.
  Same shape as the `AnalysisTimezone` refactor: the fix was right and its blast
  radius was not measured. `1295d50` then made the card's own render print its
  gridline counts, so that what only the eye could check became a number.
  **That instrument shipped with the same blind spot as the code it was
  watching** — see 2026-08-14.

- **2026-08-14** — **The instrument built to end the card's blind spots had one,
  in the place it was built for.** Commits: `ef59b80` (the tick cap), `02fe6bb`
  (the instrument), `c7f47aa` (example cards into the repo), then the OPSD
  attribution, the gathered README constraints, and the export sweep intro.
  Suite 354 → 358, all four gates clean.

  *The tick decision closed at 4, and none of the options on the table touched
  the card that needed one.* The open question was whether to raise the cap from
  4 to 5. Measured per card: `no_cost` renders four gridlines at both values,
  `residential4` and `residential6` — the two launch cards — render four at
  both, and only `single_capacity` moves, from three to five. The handoff's
  strongest argument for raising it was `no_cost`, on the grounds that both
  flattening bars float above the last labelled line. Looking at the card: 442
  sits below the 450 gridline and 462 above it, so the line passes between
  exactly the two bars the headline is about. It is the best reference the axis
  could have produced. **The one card where the complaint is true is
  `residential6`, where 46 and 49 both clear the 45 line — and neither 5 nor a
  minimum-tick floor moves it. Only 6 does, which degrades everything else.**
  `_MAX_SAVINGS_TICKS = 4` stands.

  *The gridline counter walked `figure.axes` matching the label "EUR / year", so
  it reported the savings panel and was blind to the payback panel below it.*
  Demonstrated rather than argued: the old matcher returns `4` for `ausgrid`,
  which has a payback panel, and `4` for `no_cost`, which does not. **The same
  number for two different states is how a green instrument reports nothing** —
  third instance of that shape after `bars_of()` matching an empty list and the
  `ruff format` gate. It also returned `0` for both "panel with no ticks" and
  "no panel at all". `02fe6bb` identifies the panels structurally by position
  rather than by label text, prints tick values rather than counts, and reports
  `dropped` as distinct from any number.

  *`ef59b80`'s message says "every healthy panel"; the cap reaches half of
  them.* `_format_money_ticks` has one call site, in `_draw_savings_panel`. The
  payback panel never touches `set_major_locator` and uses matplotlib's default.
  The constant is named `_MAX_SAVINGS_TICKS` and the name is accurate — **the
  defect was not a lie in the name but a gap in the coverage, and the accurate
  name is what made the gap invisible.** Measured consequence, visible for the
  first time: `ausgrid` draws six gridlines on the payback panel against four on
  the savings panel above it, 50% more density on the lower half of the same
  card. `single_capacity`, `60_days` and `residential4` land on four by the
  arithmetic of their ranges, not by constraint. Left as a known limit; capping
  an axis that runs to 34.5 years at four ticks is a separate measurement.

  *`baseline_only` renders zero axes, not one.* The structural hypothesis behind
  the new counter was two panels or one; running it found three states, because
  a sweep with only the zero-capacity baseline is filtered to empty and the
  chart is skipped entirely. Corrected by measurement rather than defended.

  *The two launch cards were produced by a command line kept nowhere, and were
  stale by mtime twice while being identical in content both times.* Neither
  `residential4.png` nor `residential6.png` was in
  `scripts/render_sample_cards.py`'s nine cases; they came from an ad-hoc
  invocation that lived only in a shell history. Both re-renders came back
  byte-identical to what was already on disk. **The defect was never drift — it
  was that drift could not be ruled out without an investigation, twice, on the
  two images the launch depends on.** The script now renders them behind an
  opt-in path (`BATTERY_WORTH_OPSD_DIR` / `--opsd-input`, output via
  `BATTERY_WORTH_OPSD_CARDS` / `--opsd-output`), skipping with the missing
  filenames named rather than silently, so it still runs for a stranger with no
  OPSD data. The nine coverage cases stay: they exercise renderer branches —
  dropped panel, negative savings, clipped bars, no chart at all — that no real
  household produces.

  *The cards moved into `docs/assets/` and the CC BY 4.0 attribution arrived one
  commit later.* `c7f47aa` put two images derived from OPSD data into the
  repository; the attribution notice the licence requires followed separately.
  Nothing was distributed in between and the history is not being rewritten, but
  the ordering is worth recording: **the obligation attaches to the artifact, so
  it belongs in the commit that introduces it.** A one-line `.gitignore` removal
  would have committed the source CSVs along with the cards, making the deferred
  "committed real fixture" decision by accident and without the attribution; it
  was reverted and `scratchpad/` stays ignored.

  *The README's constraints were scattered across a session log entry and an
  untracked handoff document, neither of which the person writing the README
  would open.* Gathered into the `## README (decided, with rationale)` section
  alongside the other decided sections. It now also records what the card footer
  structurally cannot: the example data is from Konstanz, southern Germany, in
  `Europe/Berlin`. That is the same reason the screenshots use a flat tariff
  instead of the Italian bands — but stated rather than avoided.

  *A replacement sentence that was false in the same way as the one it
  replaced.* The report's sensitivity section introduced its table with "At an
  export price of 0.10 EUR/kWh (the one used above), here is the same sweep
  re-costed", above columns that run across the whole sweep. The wording
  specified to fix it ended "…is one of the columns", which holds for the
  default 0.5x/1x/1.5x sweep and fails under `--export-price-sweep 0.20,0.30
  --export-price 0.10`. **A convenient property of the default configuration was
  read as a guarantee of the code** — the fixture-regularity failure again, one
  layer up. The shipped sentence branches on membership, tested on the rendered
  label rather than on float equality. Two tests assert what the table is rather
  than what the prose says, since a test pinning the replacement string would
  pass on any future sentence equally false; the first draft of the second test
  did exactly that and passed under mutation.

  **Open, recorded rather than fixed.** The configured export price is not marked
  in the sensitivity table, so the prose now promises a column the reader has to
  find by eye. On `residential6`, three bars stand at near-full height for 39, 46
  and 49 EUR, because the axis scales to the data — nothing is false and the
  impression is the opposite of the verdict, on the card carrying the project's
  strongest message. On the same card the caption reads `never pays back`
  beneath a computed `76.5 years`: the twenty-year threshold is correct and its
  three-word rendering says more than the engine did. And in the dropped-panel
  path, "Usable battery capacity" and "Years to pay back" sit about twenty pixels
  apart.

  **Next: the README.** It is the last block before launch and its constraints
  are now in one section.