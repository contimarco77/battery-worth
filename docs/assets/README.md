# Example cards

The two summary cards used in the project README, and the only card images
tracked in this repository. Everything under `scratchpad/` is regenerated
output and is not versioned.

| File | Case | Period | Tariff | Battery cost |
|---|---|---|---|---|
| `residential4.png` | Primary example — the larger battery saves more and is the worse investment | 2015-10-10 → 2018-02-05 (850 days) | flat 0.25 EUR/kWh, export 0.10 | 3,000 EUR |
| `residential6.png` | A household already self-consuming 80% of its own solar: no capacity pays back | 2016-05-13 → 2018-04-09 (697 days) | flat 0.25 EUR/kWh, export 0.10 | 3,000 EUR |

## Data source and attribution

Both cards are derived from the Open Power System Data household dataset,
published under CC BY 4.0. Attribution is required wherever derived work is
distributed — these images included.

> Open Power System Data. 2020. Data Package Household Data. Version
> 2020-04-15.
> https://data.open-power-system-data.org/household_data/2020-04-15/.
> (Primary data from various sources, for a complete list see URL).

The source CSVs are **not** in this repository. Placing them here would be a
separate decision with its own licensing considerations, and it has not been
made.

## Regenerating

`scripts/render_sample_cards.py` renders these two alongside the nine
renderer-coverage cases. It reads the OPSD household CSVs from
`scratchpad/opsd/` and writes the cards to `scratchpad/cards/opsd/`. Both
paths are overridable:

| Purpose | Env var | Flag |
|---|---|---|
| Input CSVs | `BATTERY_WORTH_OPSD_DIR` | `--opsd-input` |
| Card output | `BATTERY_WORTH_OPSD_CARDS` | `--opsd-output` |

Without the CSVs the script renders the nine coverage cards, names the files
it could not find, and exits cleanly.

The producer used to be an ad-hoc command line kept nowhere, which is why
these images drifted out of step with the code twice without it being
visible. Regenerate through the script, not by hand.

## Why a flat tariff and not the Italian F1/F2/F3 bands

The card footer names the tariff scheme but never the timezone. On non-Italian
data, a footer reading "Italian bands" suggests a frame the run may not have
had — a correct number rendered into a misleading claim. The footer is
deliberately minimal and is not being changed; these screenshots avoid the
ambiguous case instead.
