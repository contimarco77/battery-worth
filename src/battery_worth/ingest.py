"""CSV ingestion, validation and resampling for battery-worth.

Loads a user-supplied CSV of historical energy data, normalizes it to the
hourly `grid_import` / `grid_export` / `pv_production` contract expected by
`simulator.simulate_battery`, and reports data-quality issues so the user can
trust (or fix) their input. No LLM calls anywhere in this module.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from battery_worth.models import ColumnMapping, IngestReport

_MIN_DAYS = 30
_SEASONALITY_DAYS = 365
_GAP_THRESHOLD_HOURS = 3.0
_CUMULATIVE_TOLERANCE = 1e-6
_CUMULATIVE_DROP_FRACTION_LIMIT = 0.02
_MIN_POINTS_FOR_TREND = 2


def load_energy_data(
    path: Path,
    mapping: ColumnMapping,
    timezone: str = "Europe/Rome",
    cumulative: bool | None = None,
) -> tuple[pd.DataFrame, IngestReport]:
    """Load, validate and hourly-resample a user CSV of historical energy data.

    Returns a DataFrame indexed by tz-aware, hourly timestamps with float
    columns `grid_import`, `grid_export`, `pv_production` (kWh per interval),
    plus an `IngestReport` describing data quality and the decisions made
    while loading (schema detected, cumulative columns, gaps, clipping).
    """
    source_columns = _source_columns(mapping)
    raw = _read_csv(path, mapping, source_columns)
    raw, warnings = _localize_and_sort(raw, timezone, mapping.timestamp)

    cumulative_columns: list[str] = []
    for col in source_columns:
        is_cumulative = _is_cumulative(raw[col]) if cumulative is None else cumulative
        if is_cumulative:
            cumulative_columns.append(col)
            raw[col] = raw[col].diff()
            raw.loc[raw.index[0], col] = 0.0
            warnings.append(
                f"Column '{col}' looks like a cumulative meter reading (values only ever "
                "increase) — converted to per-interval energy with a running difference. "
                "Pass cumulative=False if this column is already per-interval energy."
            )

    raw_index = pd.DatetimeIndex(raw.index)
    native_resolution_minutes = _infer_resolution_minutes(raw_index)
    if native_resolution_minutes <= 0:
        msg = (
            "Could not infer a native sampling interval from the timestamps in "
            f"'{path}'. Check that the timestamp column ('{mapping.timestamp}') has at "
            "least two distinct, correctly parsed timestamps."
        )
        raise ValueError(msg)
    warnings.extend(_check_irregular_resolution(raw_index, native_resolution_minutes))

    gaps_count, gaps_total_hours = _detect_gaps(raw_index, native_resolution_minutes)
    if gaps_count > 0:
        warnings.append(
            f"Found {gaps_count} gap(s) longer than {_GAP_THRESHOLD_HOURS:g} hours in the "
            f"data, totalling {gaps_total_hours:.1f} hours. Missing periods are treated as "
            "zero energy in the simulation, which can understate consumption/production "
            "during those gaps."
        )

    # Clip at native resolution, before the hourly sum: a large negative diff (meter
    # reset) must not silently cancel out real positive energy within the same hour.
    negative_values_clipped = 0
    for col in source_columns:
        n_negative = int((raw[col] < 0).sum())
        if n_negative > 0:
            negative_values_clipped += n_negative
            raw[col] = raw[col].clip(lower=0.0)
            warnings.append(
                f"Column '{col}' had {n_negative} negative value(s) after processing "
                "(likely a meter reset or reading error) — clipped to 0."
            )

    hourly = raw[source_columns].resample("h").sum()

    for col in source_columns:
        series = hourly[col]
        if (series == 0).all():
            warnings.append(
                f"Column '{col}' is all zero for the whole period. Double-check the column "
                f"mapping and the source data for '{col}'."
            )
        elif series.nunique() == 1:
            warnings.append(
                f"Column '{col}' has a constant, non-zero value for the whole period, which "
                "is unusual for energy data. Double-check the source data."
            )

    result = _derive_grid_columns(hourly, mapping)

    days_analyzed = _days_analyzed(pd.DatetimeIndex(hourly.index))
    if days_analyzed < _MIN_DAYS:
        msg = (
            f"Only {days_analyzed} day(s) of data found in '{path}', but at least "
            f"{_MIN_DAYS} days are required for a meaningful analysis. Please provide a "
            "longer export from Home Assistant (or your data source) and try again."
        )
        raise ValueError(msg)

    seasonality_warning = days_analyzed < _SEASONALITY_DAYS
    if seasonality_warning:
        warnings.append(
            f"Only {days_analyzed} day(s) of data (less than a full year). Results may not "
            "capture seasonal variation in PV production and consumption — treat the ROI "
            "estimate as indicative, not final."
        )

    report = IngestReport(
        period_start=str(hourly.index[0]),
        period_end=str(hourly.index[-1]),
        days_analyzed=days_analyzed,
        native_resolution_minutes=native_resolution_minutes,
        schema_used=mapping.schema_kind,
        cumulative_columns=cumulative_columns,
        gaps_count=gaps_count,
        gaps_total_hours=gaps_total_hours,
        negative_values_clipped=negative_values_clipped,
        seasonality_warning=seasonality_warning,
        warnings=warnings,
    )
    return result, report


def _source_columns(mapping: ColumnMapping) -> list[str]:
    """The raw CSV columns to read, per the schema selected by the mapping."""
    if mapping.schema_kind == "grid_centric":
        assert mapping.grid_import is not None
        assert mapping.grid_export is not None
        return [mapping.grid_import, mapping.grid_export, mapping.pv_production]
    assert mapping.consumption is not None
    return [mapping.consumption, mapping.pv_production]


def _read_csv(path: Path, mapping: ColumnMapping, source_columns: list[str]) -> pd.DataFrame:
    needed = [mapping.timestamp, *source_columns]
    try:
        df = pd.read_csv(path, usecols=needed)
    except FileNotFoundError as exc:
        msg = f"CSV file not found: '{path}'. Check the path and try again."
        raise ValueError(msg) from exc
    except ValueError as exc:
        msg = (
            f"Could not find the configured columns in '{path}'. Expected columns "
            f"{needed}, but the CSV header didn't match. Check your column mapping "
            f"against the CSV's actual header row. Original error: {exc}"
        )
        raise ValueError(msg) from exc

    if df.empty:
        msg = f"'{path}' has no data rows. Provide a CSV with at least {_MIN_DAYS} days of data."
        raise ValueError(msg)

    for col in [*source_columns]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
        if df[col].isna().any():
            n_bad = int(df[col].isna().sum())
            msg = (
                f"Column '{col}' in '{path}' has {n_bad} value(s) that aren't valid numbers. "
                f"Check for empty cells, text, or thousands separators in that column."
            )
            raise ValueError(msg)

    return df


def localize_index(index: pd.DatetimeIndex, timezone: str) -> tuple[pd.DatetimeIndex, list[str]]:
    """Localize naive timestamps, handling both shapes of autumn DST data.

    Public because `tariffs` localizes hourly price CSVs the same way: a price file
    and an energy file covering the same changeover must be treated identically, or
    they would silently misalign for one hour a year.

    `ambiguous="infer"` resolves the repeated hour from the data itself, but it only
    works when that hour actually appears twice (a Home Assistant export logs local
    02:00 once per UTC offset). Many real exports — public datasets, inverter CSVs,
    anything written against a naive local clock — contain it only once, and infer
    then raises. Fall back to reading the repeated hour as the first (pre-changeover,
    DST) pass, which is the correct choice for a single-occurrence series.
    """
    try:
        return index.tz_localize(timezone, ambiguous="infer", nonexistent="shift_forward"), []
    except ValueError:
        localized = index.tz_localize(timezone, ambiguous=True, nonexistent="shift_forward")
        return localized, [
            "The autumn daylight-saving hour appears only once in this data, so it could "
            "not be resolved from the timestamps themselves; it was read as the first "
            "(pre-changeover) pass. This shifts at most one hour of energy per year and "
            "does not meaningfully affect the results."
        ]


def _localize_and_sort(
    df: pd.DataFrame, timezone: str, timestamp_col: str
) -> tuple[pd.DataFrame, list[str]]:
    parsed = pd.to_datetime(df[timestamp_col], errors="coerce")
    if parsed.isna().any():
        n_bad = int(parsed.isna().sum())
        msg = (
            f"Column '{timestamp_col}' has {n_bad} timestamp(s) that could not be parsed. "
            "Make sure it contains ISO-8601 timestamps (e.g. '2025-06-01T14:00:00')."
        )
        raise ValueError(msg)

    df = df.drop(columns=[timestamp_col]).set_index(parsed)
    df.index.name = "timestamp"
    # Sort before localizing: ambiguous="infer" reads the repeated autumn hour from the
    # order of the timestamps, so an out-of-order export must be fixed up first.
    df = df.sort_index()
    index = pd.DatetimeIndex(df.index)
    warnings: list[str] = []

    if index.tz is None:
        localized, dst_warnings = localize_index(index, timezone)
        warnings.extend(dst_warnings)
    else:
        localized = index.tz_convert(timezone)
    df.index = localized

    df = df.sort_index()

    # Duplicate timestamps are only resolved here, AFTER localization, because the two
    # DST cases look identical beforehand but are not:
    #   * Autumn: the repeated wall-clock hour localizes to two DISTINCT instants
    #     (+02:00 and +01:00), so it never reaches this point as a duplicate at all.
    #   * Spring: nonexistent="shift_forward" moves the non-existent local hour onto the
    #     next hour, colliding with a row that already exists. Those are two separate
    #     intervals of real energy on one timestamp.
    # A collision is therefore extra energy, not a redundant export, so it is SUMMED —
    # except when the colliding rows are identical, which indicates the same reading was
    # written to the file twice and summing it would invent energy that never flowed.
    duplicated = pd.DatetimeIndex(df.index).duplicated()
    if bool(duplicated.any()):
        # Scope the value comparison to the timestamp: df.duplicated() alone ignores the
        # index, so ordinary repeating daily patterns would look like exact repeats.
        identical = df.reset_index().duplicated().to_numpy()
        n_identical = int(identical.sum())
        if n_identical > 0:
            df = df[~identical]
            warnings.append(
                f"Dropped {n_identical} row(s) that repeat both the timestamp and the "
                "values of an earlier row. Exact repeats are treated as the same reading "
                "exported twice, not as extra energy."
            )
        if bool(pd.DatetimeIndex(df.index).duplicated().any()):
            df = df.groupby(level=0).sum()
            warnings.append(
                "Some rows share a timestamp after conversion to local time (this happens "
                "at the spring daylight-saving change, when the missing hour is moved "
                "forward onto the next one). Their energy was added together so none of "
                "it is lost."
            )
    return df, warnings


def _is_cumulative(series: pd.Series) -> bool:
    """A column is treated as cumulative if it is monotonically non-decreasing overall,
    tolerating small floating-point noise and occasional meter resets (drops)."""
    values = series.to_numpy(dtype=float)
    if len(values) < _MIN_POINTS_FOR_TREND:
        return False

    span = float(np.nanmax(values) - np.nanmin(values))
    if span <= _CUMULATIVE_TOLERANCE:
        # Constant (or all-zero) column: a meter that never moves is indistinguishable
        # from flat interval data, and diffing it would zero out real interval values.
        return False

    diffs = np.diff(values)
    tolerance = max(_CUMULATIVE_TOLERANCE, span * _CUMULATIVE_TOLERANCE)
    n_drops = int(np.sum(diffs < -tolerance))
    drop_fraction = n_drops / len(diffs)

    # A handful of resets are expected in a real cumulative meter; if a large
    # fraction of steps decrease, this is almost certainly already an interval series.
    return drop_fraction < _CUMULATIVE_DROP_FRACTION_LIMIT and float(values[-1]) >= float(values[0])


def _infer_resolution_minutes(index: pd.DatetimeIndex) -> int:
    if len(index) < _MIN_POINTS_FOR_TREND:
        return 0
    deltas = index.to_series().diff().dropna()
    if deltas.empty:
        return 0
    median_delta = deltas.median()
    return round(median_delta.total_seconds() / 60)


def _check_irregular_resolution(index: pd.DatetimeIndex, resolution_minutes: int) -> list[str]:
    deltas = index.to_series().diff().dropna()
    expected = pd.Timedelta(minutes=resolution_minutes)
    irregular = deltas[(deltas != expected) & (deltas <= pd.Timedelta(hours=_GAP_THRESHOLD_HOURS))]
    if len(irregular) > max(3, int(0.01 * len(deltas))):
        return [
            f"Timestamps aren't evenly spaced: inferred a native resolution of "
            f"{resolution_minutes} minute(s), but {len(irregular)} interval(s) differ from "
            "that. This is fine occasionally, but if it's widespread, check your export "
            "for missing or duplicated rows."
        ]
    return []


def _detect_gaps(index: pd.DatetimeIndex, resolution_minutes: int) -> tuple[int, float]:
    deltas = index.to_series().diff().dropna()
    threshold = pd.Timedelta(hours=_GAP_THRESHOLD_HOURS)
    gaps = deltas[deltas > threshold]
    gaps_count = int(len(gaps))
    gaps_total_hours = float(gaps.sum().total_seconds() / 3600) if gaps_count else 0.0
    return gaps_count, gaps_total_hours


def _derive_grid_columns(hourly: pd.DataFrame, mapping: ColumnMapping) -> pd.DataFrame:
    if mapping.schema_kind == "grid_centric":
        assert mapping.grid_import is not None
        assert mapping.grid_export is not None
        return pd.DataFrame(
            {
                "grid_import": hourly[mapping.grid_import],
                "grid_export": hourly[mapping.grid_export],
                "pv_production": hourly[mapping.pv_production],
            },
            index=hourly.index,
        )

    assert mapping.consumption is not None
    net = hourly[mapping.consumption] - hourly[mapping.pv_production]
    return pd.DataFrame(
        {
            "grid_import": net.clip(lower=0.0),
            "grid_export": (-net).clip(lower=0.0),
            "pv_production": hourly[mapping.pv_production],
        },
        index=hourly.index,
    )


def _days_analyzed(index: pd.DatetimeIndex) -> int:
    span = index[-1] - index[0]
    return int(span.total_seconds() // 86400) + 1
