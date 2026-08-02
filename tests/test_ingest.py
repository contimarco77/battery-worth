"""Hand-verifiable tests for CSV ingestion, using small real CSV files in tmp_path.

No mocking of pandas: every fixture is a real CSV written to disk and read back,
so behavior (parsing, tz localization, resampling) is exercised end to end.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import pytest

from battery_worth.ingest import load_energy_data
from battery_worth.models import ColumnMapping

GRID_MAPPING = ColumnMapping(
    timestamp="ts", grid_import="imp", grid_export="exp", pv_production="pv"
)
METER_MAPPING = ColumnMapping(timestamp="ts", consumption="consumption", pv_production="pv")

MIN_DAYS = 30
QUARTER_HOUR_MINUTES = 15
HOUR_MINUTES = 60


def write_csv(path: Path, header: list[str], rows: Sequence[Sequence[object]]) -> Path:
    file_path = path / "data.csv"
    lines = [",".join(header)]
    lines.extend(",".join(str(v) for v in row) for row in rows)
    file_path.write_text("\n".join(lines) + "\n")
    return file_path


def hourly_timestamps(start: str, n: int) -> list[str]:
    """n sequential hourly wall-clock labels from `start`.

    A real Home Assistant export spanning an autumn DST changeover logs local
    02:00 twice (once per UTC offset), so any 02:00 label inside the requested
    range is duplicated here to match realistic input.
    """
    idx = pd.date_range(start, periods=n, freq="h")
    ts = [t.isoformat() for t in idx]
    for pos in reversed(range(len(ts))):
        if idx[pos].month == 10 and idx[pos].day >= 25 and idx[pos].hour == 2:  # noqa: PLR2004
            ts.insert(pos + 1, ts[pos])
    return ts


def make_grid_csv(
    path: Path, start: str, imp: list[float], exp: list[float], pv: list[float]
) -> Path:
    ts = hourly_timestamps(start, len(imp))
    # ts may be longer than n if a DST duplicate was inserted; pad the value series to match.
    while len(ts) > len(imp):
        dup_at = next(i for i in range(1, len(ts)) if ts[i] == ts[i - 1])
        imp = [*imp[:dup_at], imp[dup_at - 1], *imp[dup_at:]]
        exp = [*exp[:dup_at], exp[dup_at - 1], *exp[dup_at:]]
        pv = [*pv[:dup_at], pv[dup_at - 1], *pv[dup_at:]]
    rows = list(zip(ts, imp, exp, pv, strict=True))
    return write_csv(path, ["ts", "imp", "exp", "pv"], rows)


def days_of_hourly_data(days: int) -> tuple[list[float], list[float], list[float]]:
    """A repeating (non-monotonic) daily pattern, so it never looks like a
    cumulative meter reading by accident."""
    day_imp = [0.5, 0.7, 0.3] * 8
    day_exp = [0.2, 0.0, 0.4] * 8
    day_pv = [0.3, 0.1, 0.6] * 8
    imp = (day_imp * days)[: days * 24]
    exp = (day_exp * days)[: days * 24]
    pv = (day_pv * days)[: days * 24]
    return imp, exp, pv


def test_grid_centric_basic_load(tmp_path: Path) -> None:
    """32 days of hourly grid-centric data: simplest end-to-end path."""
    imp, exp, pv = days_of_hourly_data(32)
    csv = make_grid_csv(tmp_path, "2025-01-01 00:00", imp, exp, pv)

    df, report = load_energy_data(csv, GRID_MAPPING)

    assert list(df.columns) == ["grid_import", "grid_export", "pv_production"]
    assert df["grid_import"].iloc[0] == pytest.approx(0.5)
    assert df["grid_export"].iloc[0] == pytest.approx(0.2)
    assert df["pv_production"].iloc[0] == pytest.approx(0.3)
    assert report.schema_used == "grid_centric"
    assert report.native_resolution_minutes == HOUR_MINUTES
    assert report.days_analyzed == len(imp) // 24
    assert report.cumulative_columns == []
    assert report.seasonality_warning is True


def test_meter_centric_derivation(tmp_path: Path) -> None:
    """consumption - pv derivation, hand-verified per row.

    Row 0: consumption=1.0, pv=0.4 -> net=0.6 -> import=0.6, export=0.0
    Row 1: consumption=0.2, pv=1.0 -> net=-0.8 -> import=0.0, export=0.8
    """
    n = 31 * 24
    consumption = [1.0, 0.2] + [0.5] * (n - 2)
    pv = [0.4, 1.0] + [0.3] * (n - 2)
    ts = hourly_timestamps("2025-01-01 00:00", n)
    rows = list(zip(ts, consumption, pv, strict=True))
    csv = write_csv(tmp_path, ["ts", "consumption", "pv"], rows)

    df, report = load_energy_data(csv, METER_MAPPING)

    assert df["grid_import"].iloc[0] == pytest.approx(0.6)
    assert df["grid_export"].iloc[0] == pytest.approx(0.0)
    assert df["grid_import"].iloc[1] == pytest.approx(0.0)
    assert df["grid_export"].iloc[1] == pytest.approx(0.8)
    assert report.schema_used == "meter_centric"


def test_cumulative_auto_detection(tmp_path: Path) -> None:
    """A monotonically increasing column is auto-detected as cumulative and diffed.

    imp meter readings: 0, 1, 2, 3, ... (cumulative, +1 kWh/h) -> diffs: steady 1.0.
    exp and pv given as a varying (non-monotonic) interval pattern, so they are
    unambiguously not cumulative.
    """
    n = 31 * 24
    imp_cumulative = [float(i) for i in range(n)]  # strictly increasing by 1 kWh/h
    _, exp, pv = days_of_hourly_data(n // 24)
    csv = make_grid_csv(tmp_path, "2025-01-01 00:00", imp_cumulative, exp, pv)

    df, report = load_energy_data(csv, GRID_MAPPING)

    assert "imp" in report.cumulative_columns
    assert "exp" not in report.cumulative_columns
    assert "pv" not in report.cumulative_columns
    # first diffed value forced to 0, then steady 1.0 kWh/h
    assert df["grid_import"].iloc[0] == pytest.approx(0.0)
    assert df["grid_import"].iloc[1] == pytest.approx(1.0)
    assert df["grid_import"].iloc[-1] == pytest.approx(1.0)


def test_mixed_cumulative_and_interval_columns(tmp_path: Path) -> None:
    """Only the cumulative column gets diffed; the interval column is untouched."""
    n = 31 * 24
    imp_cumulative = [float(i) * 2 for i in range(n)]  # increasing by 2 kWh/h
    _, exp_interval, pv = days_of_hourly_data(n // 24)  # varying, non-monotonic
    csv = make_grid_csv(tmp_path, "2025-01-01 00:00", imp_cumulative, exp_interval, pv)

    df, report = load_energy_data(csv, GRID_MAPPING)

    assert report.cumulative_columns == ["imp"]
    assert df["grid_import"].iloc[1] == pytest.approx(2.0)
    assert df["grid_export"].iloc[1] == pytest.approx(exp_interval[1])


def test_cumulative_override_false(tmp_path: Path) -> None:
    """cumulative=False disables auto-detection even for a monotonic column."""
    n = 31 * 24
    imp_cumulative = [float(i) for i in range(n)]
    exp = [0.1] * n
    pv = [0.1] * n
    csv = make_grid_csv(tmp_path, "2025-01-01 00:00", imp_cumulative, exp, pv)

    df, report = load_energy_data(csv, GRID_MAPPING, cumulative=False)

    assert report.cumulative_columns == []
    # untouched: raw increasing values summed into hourly buckets (native res 60 min: unchanged)
    assert df["grid_import"].iloc[1] == pytest.approx(1.0)


def test_dst_autumn_duplicate_hour(tmp_path: Path) -> None:
    """Europe/Rome, 2025-10-26: local 02:00 occurs twice (ambiguous). A CSV row for both
    instances of 02:00 (as a real HA export would contain) must localize via
    ambiguous='infer' without error, and the duplicate exact-timestamp row after
    localization must be dropped rather than crash resampling."""
    n = 31 * 24
    imp, exp, pv = days_of_hourly_data(n // 24)
    csv = make_grid_csv(tmp_path, "2025-10-10 00:00", imp, exp, pv)

    df, report = load_energy_data(csv, GRID_MAPPING)

    tz = pd.DatetimeIndex(df.index).tz
    assert tz is not None
    assert str(tz) == "Europe/Rome"
    assert report.days_analyzed >= MIN_DAYS


def test_dst_spring_missing_hour(tmp_path: Path) -> None:
    """Europe/Rome, 2025-03-30: local 02:00 does not exist (spring forward).
    A naive timestamp of 2025-03-30T02:00 must be shifted forward, not raise."""
    n = 31 * 24
    imp, exp, pv = days_of_hourly_data(n // 24)
    ts = hourly_timestamps("2025-03-15 00:00", n)
    rows = list(zip(ts, imp, exp, pv, strict=True))
    csv = write_csv(tmp_path, ["ts", "imp", "exp", "pv"], rows)

    df, report = load_energy_data(csv, GRID_MAPPING)

    assert pd.DatetimeIndex(df.index).tz is not None
    assert report.days_analyzed >= MIN_DAYS


def test_15min_downsampling_to_hourly(tmp_path: Path) -> None:
    """Four 15-min intervals of [0.1, 0.2, 0.3, 0.4] kWh sum to 1.0 kWh for the hour.

    Values vary within the hour (not constant) so the column isn't mistaken for a
    cumulative meter reading.
    """
    n = 31 * 24 * 4
    imp = [0.1, 0.2, 0.3, 0.4] * (n // 4)
    exp = [0.0] * n
    pv = [0.0] * n
    ts = [t.isoformat() for t in pd.date_range("2025-01-01 00:00", periods=n, freq="15min")]
    rows = list(zip(ts, imp, exp, pv, strict=True))
    csv = write_csv(tmp_path, ["ts", "imp", "exp", "pv"], rows)

    df, report = load_energy_data(csv, GRID_MAPPING)

    assert report.native_resolution_minutes == QUARTER_HOUR_MINUTES
    assert df["grid_import"].iloc[0] == pytest.approx(1.0)
    assert len(df) == 31 * 24


def test_gap_detection(tmp_path: Path) -> None:
    """A single 6-hour gap in an otherwise hourly series is reported."""
    n = 31 * 24
    imp, exp, pv = days_of_hourly_data(n // 24)
    ts_index = pd.date_range("2025-01-01 00:00", periods=n, freq="h")
    # Remove 5 consecutive hourly rows in the middle -> one gap of 6 hours between neighbors.
    gap_start = 100
    gap_len = 5
    keep = list(range(0, gap_start)) + list(range(gap_start + gap_len, n))
    ts = [ts_index[i].isoformat() for i in keep]
    imp_k = [imp[i] for i in keep]
    exp_k = [exp[i] for i in keep]
    pv_k = [pv[i] for i in keep]
    rows = list(zip(ts, imp_k, exp_k, pv_k, strict=True))
    csv = write_csv(tmp_path, ["ts", "imp", "exp", "pv"], rows)

    _, report = load_energy_data(csv, GRID_MAPPING)

    assert report.gaps_count == 1
    assert report.gaps_total_hours == pytest.approx(6.0)


def test_fewer_than_30_days_rejected(tmp_path: Path) -> None:
    """29 days of hourly data must raise a clear, actionable error."""
    imp, exp, pv = days_of_hourly_data(29)
    csv = make_grid_csv(tmp_path, "2025-01-01 00:00", imp, exp, pv)

    with pytest.raises(ValueError, match="30 days"):
        load_energy_data(csv, GRID_MAPPING)


def test_seasonality_warning_boundary(tmp_path: Path) -> None:
    """364 days -> seasonality_warning True; 365 days -> False."""
    imp_364, exp_364, pv_364 = days_of_hourly_data(364)
    csv_364 = make_grid_csv(tmp_path, "2024-11-01 00:00", imp_364, exp_364, pv_364)
    _, report_364 = load_energy_data(csv_364, GRID_MAPPING)
    assert report_364.seasonality_warning is True

    other_dir = tmp_path / "full_year"
    other_dir.mkdir()
    imp_365, exp_365, pv_365 = days_of_hourly_data(365)
    csv_365 = make_grid_csv(other_dir, "2024-11-01 00:00", imp_365, exp_365, pv_365)
    _, report_365 = load_energy_data(csv_365, GRID_MAPPING)
    assert report_365.seasonality_warning is False


def test_negative_values_clipped(tmp_path: Path) -> None:
    """A meter reset produces a negative diff, which must be clipped to 0 and counted."""
    n = 31 * 24
    imp = [float(i) for i in range(n)]
    imp[50] = 5.0  # reset: reading drops well below the previous cumulative value
    exp = [0.1] * n
    pv = [0.1] * n
    csv = make_grid_csv(tmp_path, "2025-01-01 00:00", imp, exp, pv)

    df, report = load_energy_data(csv, GRID_MAPPING, cumulative=True)

    assert report.negative_values_clipped >= 1
    assert (df["grid_import"] >= 0).all()


def test_all_zero_column_warns(tmp_path: Path) -> None:
    """An all-zero export column produces a warning but does not crash."""
    n = 31 * 24
    imp = [0.5] * n
    exp = [0.0] * n
    pv = [0.3] * n
    csv = make_grid_csv(tmp_path, "2025-01-01 00:00", imp, exp, pv)

    _, report = load_energy_data(csv, GRID_MAPPING)

    assert any("exp" in w and "zero" in w for w in report.warnings)


def test_constant_column_not_cumulative(tmp_path: Path) -> None:
    """A constant non-zero column is trivially non-decreasing but must NOT be treated
    as cumulative: diffing it would zero out real interval data."""
    n = 31 * 24
    imp, _, pv = days_of_hourly_data(n // 24)
    exp_constant = [0.5] * n
    csv = make_grid_csv(tmp_path, "2025-01-01 00:00", imp, exp_constant, pv)

    df, report = load_energy_data(csv, GRID_MAPPING)

    assert report.cumulative_columns == []
    assert df["grid_export"].iloc[10] == pytest.approx(0.5)


def test_partial_schema_mix_rejected() -> None:
    """grid_import + consumption (without grid_export) must be rejected, not silently
    resolved to meter-centric with the grid column ignored."""
    with pytest.raises(ValueError, match="ambiguous"):
        ColumnMapping(timestamp="ts", pv_production="pv", grid_import="imp", consumption="cons")


def test_ambiguous_column_mapping_rejected() -> None:
    """Providing both grid_import/export and consumption is ambiguous and must fail fast."""
    with pytest.raises(ValueError, match="ambiguous"):
        ColumnMapping(
            timestamp="ts",
            pv_production="pv",
            grid_import="imp",
            grid_export="exp",
            consumption="cons",
        )


def test_incomplete_column_mapping_rejected() -> None:
    """Providing neither a complete grid-centric nor meter-centric schema must fail fast."""
    with pytest.raises(ValueError, match="incomplete"):
        ColumnMapping(timestamp="ts", pv_production="pv", grid_import="imp")
