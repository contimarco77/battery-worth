# battery-worth

> Would a home battery have paid off for YOU? Find out in 5 minutes from your real energy data.

Retrospective what-if analysis: feed it your historical import/export/PV data (Home Assistant export or generic CSV) and get annual savings, payback and self-consumption for multiple battery sizes and tariffs — 100% offline by default.

**Status.** The engine is complete and tested: CSV ingest (both schemas, DST, gaps, cumulative meters), the greedy simulator, the capacity sweep, flat / Italian F1-F2-F3 / hourly-price tariffs, export-price sensitivity, seasonal breakdown, the Markdown report and the PNG summary card all work end to end from the command line. 321 tests, `ruff` and `mypy --strict` clean.

Not built yet: the optional `--llm` commentary layer, a Docker image, and any native Home Assistant or inverter parser — HA data comes in through [the standalone export script](#exporting-from-home-assistant), everything else through generic CSV. Nothing is published to PyPI yet. See PROJECT-CONTEXT.md for the full state.

## Why not battery_sim?

Complementary tools: battery_sim simulates a virtual battery *live, going forward* inside Home Assistant. battery-worth answers *instantly* using the year of data you already have.

## Exporting from Home Assistant

battery-worth does **not** connect to Home Assistant. The analysis engine is offline and has no network or authentication code in it at all. Instead, a separate one-shot script pulls your history into a CSV, and the CSV is what you analyse:

```bash
export HA_TOKEN='your-long-lived-token'

python scripts/ha_export.py \
    --url ws://homeassistant.local:8123 \
    --import-sensor sensor.grid_import_energy \
    --export-sensor sensor.grid_export_energy \
    --pv-sensor sensor.solar_energy \
    --start 2024-01-01 --end 2024-12-31 \
    -o my_energy.csv

battery-worth analyze my_energy.csv --flat-price 0.28 --export-price 0.10
```

The script needs only the Python standard library — no extra install, and nothing is added to battery-worth's own dependencies.

**Your token stays on your machine.** It is sent to the `--url` you give and nowhere else, and it is never logged, printed, or written into the CSV. Nothing is uploaded anywhere: both commands run entirely on your computer.

### Creating a long-lived access token

1. In Home Assistant, click your user name at the bottom of the sidebar.
2. Open the **Security** tab.
3. Scroll to **Long-lived access tokens** and click **Create token**.
4. Give it a name (e.g. `battery-worth`) and copy the value — Home Assistant shows it only once.

Prefer `export HA_TOKEN=...` over the `--token` flag: a token typed as a command-line argument is saved in your shell history, which is a real leak. You can revoke the token from the same screen once the export is done.

### Finding your statistic_ids

The script takes *statistic ids*, which for normal sensors are just their entity ids. To find the ones your Energy Dashboard uses:

- **Settings → Devices & Services → Helpers**, or **Developer Tools → Statistics**, which lists every statistic the recorder keeps, or
- **Settings → Dashboards → Energy**, which shows exactly which sensors are configured for grid consumption, return to grid, and solar production.

If you pass an id that does not exist, the script lists the statistic ids your instance *does* have, so you can copy the right one.

`--pv-sensor` is optional but strongly recommended: without PV production the simulation cannot tell surplus from low consumption.

### Alternative: no token at all

Home Assistant exposes the same long-term statistics through the `recorder.get_statistics` action, which you can call from **Developer Tools → Actions** and copy the result out by hand. It returns the same `change` values this script requests, so it is a workable path if you would rather not create a token. battery-worth does not automate it.

> A note on the Energy Dashboard's own CSV download button: it is not a supported input. That file is transposed (timestamps run across the columns), its resolution depends on what you had selected in the UI, and it currently ships with a timezone offset bug in its header. Use the export script instead.
