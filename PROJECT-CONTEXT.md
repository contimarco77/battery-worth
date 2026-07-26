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
- [ ] Milestone 1 in progress: simulator implementation
- [ ] Milestone 2
- [ ] Milestone 3

## Session log

- (add entries here: date — what was done — next step)
