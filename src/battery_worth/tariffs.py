"""Tariff resolution: a `Tariff` config becomes a per-interval import price series.

Turns the three supported tariff kinds (flat, Italian F1/F2/F3 bands, hourly CSV)
into a EUR/kWh series aligned to the analysis index, ready for
`simulator.summarize_scenario`. Export remuneration stays a scalar in v0
(`Tariff.export_price_eur_kwh`) and is not handled here.

No LLM calls anywhere in this module. Fully vectorized: no row loops.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from dateutil.easter import easter

from battery_worth.ingest import localize_index
from battery_worth.models import Tariff, TariffKind

_ITALIAN_TIMEZONES = frozenset({"Europe/Rome", "Europe/Vatican", "Europe/San_Marino"})

# Plausible EUR/kWh retail range. Outside it, the user almost certainly passed
# EUR/MWh (~1000x too large) or a per-Wh figure.
_PRICE_SANITY_MIN = 0.001
_PRICE_SANITY_MAX = 5.0

# ARERA band boundaries, local wall-clock hours.
_F1_START_HOUR = 8
_F1_END_HOUR = 19
_SHOULDER_START_HOUR = 7
_NIGHT_START_HOUR = 23

_FRIDAY = 4
_SATURDAY = 5
_SUNDAY = 6

_MIN_ROWS_FOR_STEP = 2

_UNCOVERED_SAMPLE_LIMIT = 3


def build_price_series(index: pd.DatetimeIndex, tariff: Tariff) -> pd.Series:
    """Build the per-interval import price series (EUR/kWh) for `index`.

    The result is indexed identically to `index` (same length, same order, same
    tz-awareness), so it can be multiplied against an energy column directly.

    Raises ValueError if the tariff configuration cannot price every timestamp in
    `index` — a missing price is never silently filled, because a wrong price
    applied quietly corrupts the entire economic result.
    """
    if len(index) == 0:
        msg = "Cannot build a price series for an empty index: there is nothing to price."
        raise ValueError(msg)

    if tariff.kind is TariffKind.FLAT:
        assert tariff.flat_price_eur_kwh is not None
        return pd.Series(float(tariff.flat_price_eur_kwh), index=index, name="import_price")

    if tariff.kind is TariffKind.F1_F2_F3:
        return _build_band_prices(index, tariff)

    assert tariff.hourly_prices_csv is not None
    return _build_hourly_csv_prices(index, tariff)


def assign_bands(index: pd.DatetimeIndex) -> pd.Series:
    """Map each timestamp to its ARERA band label: 'F1', 'F2' or 'F3'.

    Bands are defined on **local wall-clock time**, so they are derived from the
    tz-aware index as-is (never converted to UTC first — that would shift every
    boundary by the local offset and silently mis-price two hours a day).

        F1: Mon-Fri 08:00-19:00
        F2: Mon-Fri 07:00-08:00 and 19:00-23:00; Sat 07:00-23:00
        F3: Mon-Sat 00:00-07:00 and 23:00-24:00; all Sundays and national holidays

    National holidays are demoted to F3 for the whole day. Exposed separately from
    pricing so the calendar can be tested on its own.
    """
    hour = np.asarray(index.hour)
    weekday = np.asarray(index.dayofweek)

    is_weekday = weekday <= _FRIDAY
    is_saturday = weekday == _SATURDAY
    # A holiday makes the whole day behave like a Sunday.
    is_sunday_like = (weekday == _SUNDAY) | _is_national_holiday(index)

    daytime = (hour >= _F1_START_HOUR) & (hour < _F1_END_HOUR)
    shoulder = (hour >= _SHOULDER_START_HOUR) & (hour < _NIGHT_START_HOUR)

    f1 = is_weekday & ~is_sunday_like & daytime
    # Weekday shoulder hours (07-08, 19-23) plus the whole Saturday working window.
    f2 = ~is_sunday_like & shoulder & (is_weekday | is_saturday) & ~f1

    bands = np.full(len(index), "F3", dtype="<U2")
    bands[f2] = "F2"
    bands[f1] = "F1"
    return pd.Series(bands, index=index, name="band")


def italian_national_holidays(years: list[int]) -> set[pd.Timestamp]:
    """The Italian NATIONAL holiday dates for the given years, as naive date-normalized
    Timestamps. Local patron saints (e.g. Milan's 7 Dec) are deliberately excluded:
    they are not national and the ARERA calendar does not apply them country-wide.

    Easter Monday is movable and is computed per year; every other date is fixed.
    """
    fixed = [
        (1, 1),  # Capodanno
        (1, 6),  # Epifania
        (4, 25),  # Liberazione
        (5, 1),  # Festa del Lavoro
        (6, 2),  # Festa della Repubblica
        (8, 15),  # Ferragosto
        (11, 1),  # Ognissanti
        (12, 8),  # Immacolata
        (12, 25),  # Natale
        (12, 26),  # Santo Stefano
    ]
    dates: set[pd.Timestamp] = set()
    for year in years:
        dates.update(pd.Timestamp(year=year, month=m, day=d) for m, d in fixed)
        dates.add(pd.Timestamp(easter(year)) + pd.Timedelta(days=1))  # Lunedì dell'Angelo
    return dates


def _is_national_holiday(index: pd.DatetimeIndex) -> np.ndarray:
    """Boolean mask: is each timestamp's LOCAL calendar date a national holiday?"""
    years = sorted({int(y) for y in index.year})
    holidays = italian_national_holidays(years)
    # tz_localize(None) drops the offset without shifting the wall clock, so the
    # comparison stays on local calendar dates.
    local_dates = index.tz_localize(None).normalize() if index.tz is not None else index.normalize()
    return np.asarray(pd.DatetimeIndex(local_dates).isin(holidays))


def _build_band_prices(index: pd.DatetimeIndex, tariff: Tariff) -> pd.Series:
    """Price each interval by its ARERA band.

    The F1/F2/F3 calendar is Italy-specific: the bands are ARERA's, and the holiday
    set is the Italian national one. Applying it to a non-Italian timezone is a user
    error — the bands would be computed against a wall clock the calendar was never
    written for. That warns rather than failing, so a user with e.g. Europe/Madrid
    data can still get a rough number if they know what they are doing, but is told.
    """
    assert tariff.f1_price is not None
    assert tariff.f2_price is not None
    assert tariff.f3_price is not None

    _warn_if_not_italian(index)

    bands = assign_bands(index)
    prices = bands.map(
        {"F1": float(tariff.f1_price), "F2": float(tariff.f2_price), "F3": float(tariff.f3_price)}
    )
    return pd.Series(prices.to_numpy(dtype=float), index=index, name="import_price")


def _warn_if_not_italian(index: pd.DatetimeIndex) -> None:
    tz_name = str(index.tz) if index.tz is not None else None
    if tz_name is None:
        warnings.warn(
            "The F1/F2/F3 tariff uses the Italian ARERA time bands, which are defined on "
            "local wall-clock time, but the analysis timestamps have no timezone. The bands "
            "will be read off the timestamps as-is; if they are not Italian local time, the "
            "band assignment will be wrong.",
            UserWarning,
            stacklevel=3,
        )
        return
    if tz_name not in _ITALIAN_TIMEZONES:
        warnings.warn(
            f"The F1/F2/F3 tariff uses the Italian ARERA time bands (and Italian national "
            f"holidays), but this data is in timezone '{tz_name}', not Italian local time. "
            "The bands and holidays almost certainly do not match your actual tariff — use a "
            "flat or hourly-CSV tariff instead, or convert your data to Europe/Rome if it is "
            "Italian.",
            UserWarning,
            stacklevel=3,
        )


def _build_hourly_csv_prices(index: pd.DatetimeIndex, tariff: Tariff) -> pd.Series:
    """Load per-hour prices from CSV and align them onto the analysis index.

    The price file is localized with the same helper `ingest` uses, so a price CSV
    and an energy CSV covering the same DST changeover are treated identically.
    """
    assert tariff.hourly_prices_csv is not None
    path = Path(tariff.hourly_prices_csv)
    timestamp_col = tariff.hourly_prices_timestamp_column
    price_col = tariff.hourly_prices_price_column

    prices = _read_price_csv(path, timestamp_col, price_col)
    prices = _localize_prices(prices, index, path)

    aligned = _align_prices(prices, index)

    _check_full_coverage(aligned, path)
    _warn_if_implausible_units(aligned, path)

    return pd.Series(aligned.to_numpy(dtype=float), index=index, name="import_price")


def _read_price_csv(path: Path, timestamp_col: str, price_col: str) -> pd.Series:
    try:
        df = pd.read_csv(path, usecols=[timestamp_col, price_col])
    except FileNotFoundError as exc:
        msg = f"Hourly price CSV not found: '{path}'. Check the path and try again."
        raise ValueError(msg) from exc
    except ValueError as exc:
        msg = (
            f"Could not find the configured columns in the hourly price CSV '{path}'. "
            f"Expected a timestamp column '{timestamp_col}' and a price column '{price_col}', "
            f"but the CSV header didn't match. Original error: {exc}"
        )
        raise ValueError(msg) from exc

    if df.empty:
        msg = f"The hourly price CSV '{path}' has no data rows."
        raise ValueError(msg)

    parsed = pd.to_datetime(df[timestamp_col], errors="coerce")
    if parsed.isna().any():
        n_bad = int(parsed.isna().sum())
        msg = (
            f"Column '{timestamp_col}' in the hourly price CSV '{path}' has {n_bad} "
            "timestamp(s) that could not be parsed. Make sure it contains ISO-8601 "
            "timestamps (e.g. '2025-06-01T14:00:00')."
        )
        raise ValueError(msg)

    values = pd.to_numeric(df[price_col], errors="coerce")
    if values.isna().any():
        n_bad = int(values.isna().sum())
        msg = (
            f"Column '{price_col}' in the hourly price CSV '{path}' has {n_bad} value(s) "
            "that aren't valid numbers. Check for empty cells, text, or a comma used as "
            "the decimal separator."
        )
        raise ValueError(msg)

    series = pd.Series(values.to_numpy(dtype=float), index=pd.DatetimeIndex(parsed))
    series.index.name = "timestamp"
    return series.sort_index()


def _localize_prices(prices: pd.Series, index: pd.DatetimeIndex, path: Path) -> pd.Series:
    """Put the price series on the same timezone as the analysis index.

    Naive price timestamps are localized to the analysis timezone with the shared
    ingest helper (same autumn/spring DST treatment as the energy data); aware ones
    are converted. Duplicate timestamps are averaged, not summed: prices are an
    intensive quantity, so two readings for one instant are the same price stated
    twice, never two prices to add up.
    """
    price_index = pd.DatetimeIndex(prices.index)
    if price_index.tz is None:
        if index.tz is None:
            localized = price_index
        else:
            localized, _ = localize_index(price_index, str(index.tz))
        prices = pd.Series(prices.to_numpy(dtype=float), index=localized)
    elif index.tz is not None:
        prices = pd.Series(prices.to_numpy(dtype=float), index=price_index.tz_convert(index.tz))
    else:
        msg = (
            f"The hourly price CSV '{path}' has timezone-aware timestamps but the analysis "
            "data does not, so the two cannot be aligned unambiguously. Provide energy data "
            "with a timezone, or price timestamps in plain local time."
        )
        raise ValueError(msg)

    prices = prices.sort_index()
    if bool(pd.DatetimeIndex(prices.index).duplicated().any()):
        prices = prices.groupby(level=0).mean()
    return prices


def _align_prices(prices: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Carry each price forward over its own step, and no further.

    A price row at time `t` with a step of one hour is the price for `[t, t + 1h)`,
    so it covers every analysis timestamp inside that window — this is the
    forward-fill within the hour that lets an hourly price file price 15-minute
    energy data. It must NOT cover anything at or after `t + step`: that region
    either belongs to the next price row or is a hole in the file, and a hole must
    surface as missing coverage rather than silently inherit the previous price.

    Expressed as a time tolerance rather than a row count, because a row limit
    cannot tell "inside this row's own step" from "past the end of the file" — both
    are just N rows ahead. `merge_asof` with a tolerance says exactly what is meant.

    The step is the SMALLEST gap between price rows, not the median: a file with a
    missing hour has a median step inflated by the very hole the coverage check
    exists to catch.
    """
    step = _price_step(pd.DatetimeIndex(prices.index))
    # `tolerance` is inclusive, but the validity window is half-open: a timestamp
    # exactly one step after a price row belongs to the NEXT row, not this one. Backing
    # the tolerance off by one nanosecond makes the bound strict, so the hour after the
    # last price row stays uncovered instead of inheriting it.
    tolerance = step - pd.Timedelta(nanoseconds=1)

    left = pd.DataFrame({"ts": index})
    right = pd.DataFrame({"ts": pd.DatetimeIndex(prices.index), "price": prices.to_numpy(float)})
    merged = pd.merge_asof(
        left, right, on="ts", direction="backward", tolerance=tolerance, allow_exact_matches=True
    )
    return pd.Series(merged["price"].to_numpy(dtype=float), index=index)


def _price_step(price_index: pd.DatetimeIndex) -> pd.Timedelta:
    """The validity window of a single price row: the file's own finest resolution.

    A single-row price file has no inferable step; one hour is assumed, matching the
    "hourly prices" contract of this tariff kind.
    """
    if len(price_index) < _MIN_ROWS_FOR_STEP:
        return pd.Timedelta(hours=1)
    deltas = price_index.to_series().diff().dropna()
    if deltas.empty:
        return pd.Timedelta(hours=1)
    step = deltas.min()
    return pd.Timedelta(hours=1) if step <= pd.Timedelta(0) else pd.Timedelta(step)


def _check_full_coverage(aligned: pd.Series, path: Path) -> None:
    missing = aligned.isna()
    if not bool(missing.any()):
        return

    n_missing = int(missing.sum())
    uncovered = pd.DatetimeIndex(aligned.index[missing.to_numpy()])
    first, last = uncovered[0], uncovered[-1]
    sample = ", ".join(str(ts) for ts in uncovered[:_UNCOVERED_SAMPLE_LIMIT])
    if n_missing > _UNCOVERED_SAMPLE_LIMIT:
        sample += ", ..."
    msg = (
        f"The hourly price CSV '{path}' does not cover the whole analysis period: "
        f"{n_missing} timestamp(s) have no price, from {first} to {last} (e.g. {sample}). "
        "Prices are never guessed or filled in, because a wrong price applied silently "
        "would corrupt every cost and savings figure. Extend the price file to cover the "
        "full period, or trim the energy data to the period the prices cover."
    )
    raise ValueError(msg)


def _warn_if_implausible_units(aligned: pd.Series, path: Path) -> None:
    median = float(aligned.median())
    if median > _PRICE_SANITY_MAX:
        warnings.warn(
            f"The median price in '{path}' is {median:.3f}, which is high for EUR/kWh. "
            "Prices must be in EUR per kWh, not EUR per MWh — day-ahead market data "
            "(e.g. PUN) is usually published in EUR/MWh, which is 1000x larger. Divide "
            "by 1000 if that is the case, otherwise every cost figure will be inflated.",
            UserWarning,
            stacklevel=4,
        )
    elif 0 < median < _PRICE_SANITY_MIN:
        warnings.warn(
            f"The median price in '{path}' is {median:.6f}, which is very low for EUR/kWh. "
            "Check the units: prices must be in EUR per kWh, not EUR per Wh.",
            UserWarning,
            stacklevel=4,
        )
