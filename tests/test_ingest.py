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
HALF_HOUR_MINUTES = 30
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


def distinct_sawtooth_values(n: int) -> tuple[list[float], list[float], list[float]]:
    """Per-row values that are distinct within each 24h block (so a dropped row changes
    the total detectably) but reset every day, so no column ever looks monotonically
    non-decreasing and trips the cumulative-meter detector."""
    imp = [round(0.10 + (i % 24) * 0.001, 4) for i in range(n)]
    exp = [round(0.20 + (i % 24) * 0.002, 4) for i in range(n)]
    pv = [round(0.30 + (i % 24) * 0.003, 4) for i in range(n)]
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


def test_cumulative_warning_names_the_cli_flag(tmp_path: Path) -> None:
    """The escape hatch offered by the warning must be one the user can actually take.

    The message used to say "Pass cumulative=False", which is the Python keyword
    argument — invisible from the command line, where nearly everyone meets this
    warning. Naming a flag that does not exist is worse than naming none.
    """
    n = 31 * 24
    imp_cumulative = [float(i) for i in range(n)]
    _, exp, pv = days_of_hourly_data(n // 24)
    csv = make_grid_csv(tmp_path, "2025-01-01 00:00", imp_cumulative, exp, pv)

    _, report = load_energy_data(csv, GRID_MAPPING)

    warning = next(w for w in report.warnings if "cumulative meter reading" in w)
    assert "--no-cumulative" in warning
    assert "cumulative=False" not in warning


def test_cumulative_true_forces_differencing_and_says_who_decided(tmp_path: Path) -> None:
    """cumulative=True overrides a detector that (correctly) sees interval data.

    Hand-computed: a column repeating 0.5, 0.7, 0.3 hourly is not monotonic, so the
    detector leaves it alone. Forced, it is differenced: row 1 becomes 0.7-0.5 = 0.2,
    row 2 becomes 0.3-0.7 = -0.4, which is then clipped to 0 as a meter reset. The
    warning must attribute the choice to the flag rather than to the data.
    """
    imp, exp, pv = days_of_hourly_data(31)
    csv = make_grid_csv(tmp_path, "2025-01-01 00:00", imp, exp, pv)

    df, report = load_energy_data(csv, GRID_MAPPING, cumulative=True)

    assert report.cumulative_columns == ["imp", "exp", "pv"]
    assert df["grid_import"].iloc[1] == pytest.approx(0.2)
    assert df["grid_import"].iloc[2] == pytest.approx(0.0)  # -0.4 clipped

    warning = next(w for w in report.warnings if "'imp'" in w and "cumulative" in w)
    assert "--cumulative was passed" in warning
    assert "looks like a cumulative meter reading" not in warning


def test_a_rising_interval_column_is_indistinguishable_without_the_override(
    tmp_path: Path,
) -> None:
    """The precise case the override exists for, pinned as a property of the data.

    A per-interval column that never decreases is *mathematically identical in shape*
    to a meter reading: both are non-decreasing sequences. The detector cannot resolve
    it — and must not be blamed for that — so the only correct fix is the user saying
    which one it is. Here the same file yields ~1.0 kWh/h with the override and a
    ~0.0007 kWh/h step without it: three orders of magnitude, decided entirely by the
    flag.
    """
    n = 31 * 24
    step = 1.0 / (n - 1)
    rising = [1.0 + i * step for i in range(n)]  # per-interval kWh, slowly creeping up
    csv = make_grid_csv(tmp_path, "2025-01-01 00:00", rising, list(rising), list(rising))

    auto_df, auto_report = load_energy_data(csv, GRID_MAPPING)
    forced_df, forced_report = load_energy_data(csv, GRID_MAPPING, cumulative=False)

    assert auto_report.cumulative_columns == ["imp", "exp", "pv"], (
        "the detector is expected to misfire here — that is the premise of the test"
    )
    assert auto_df["grid_import"].iloc[1] == pytest.approx(step, rel=1e-6)

    assert forced_report.cumulative_columns == []
    assert forced_df["grid_import"].iloc[1] == pytest.approx(1.0 + step, rel=1e-6)


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


def test_dst_spring_conserves_energy(tmp_path: Path) -> None:
    """Spring forward must not lose energy.

    Europe/Rome 2025-03-30: local 02:00 does not exist, so nonexistent='shift_forward'
    moves that row onto 03:00 — where a row already exists. Those are two distinct
    intervals of real energy landing on one timestamp: they must be SUMMED. Dropping
    the collision silently deletes an hour of readings from the totals.

    Every input value is distinct and known, so total kWh in == total kWh out is
    hand-checkable per column.
    """
    n = 31 * 24
    imp, exp, pv = distinct_sawtooth_values(n)
    ts = [t.isoformat() for t in pd.date_range("2025-03-15 00:00", periods=n, freq="h")]
    rows = list(zip(ts, imp, exp, pv, strict=True))
    csv = write_csv(tmp_path, ["ts", "imp", "exp", "pv"], rows)

    df, _ = load_energy_data(csv, GRID_MAPPING)

    assert df["grid_import"].sum() == pytest.approx(sum(imp))
    assert df["grid_export"].sum() == pytest.approx(sum(exp))
    assert df["pv_production"].sum() == pytest.approx(sum(pv))


def test_dst_autumn_conserves_energy(tmp_path: Path) -> None:
    """The autumn changeover (repeated 02:00, each pass a real interval) also conserves
    energy: both passes are distinct readings and neither may be discarded."""
    n = 31 * 24
    ts = hourly_timestamps("2025-10-10 00:00", n)  # duplicates local 02:00, as HA does
    imp, exp, pv = distinct_sawtooth_values(len(ts))
    rows = list(zip(ts, imp, exp, pv, strict=True))
    csv = write_csv(tmp_path, ["ts", "imp", "exp", "pv"], rows)

    df, _ = load_energy_data(csv, GRID_MAPPING)

    assert df["grid_import"].sum() == pytest.approx(sum(imp))
    assert df["grid_export"].sum() == pytest.approx(sum(exp))
    assert df["pv_production"].sum() == pytest.approx(sum(pv))


def test_duplicate_source_rows_are_dropped_not_summed(tmp_path: Path) -> None:
    """A genuinely repeated source row (same wall-clock timestamp exported twice, away
    from any DST boundary) is a duplicate export of one reading, not extra energy:
    it is dropped, so the total excludes it. This is the opposite rule from the
    DST-shift collision above, and the two must not be conflated."""
    n = 31 * 24
    imp, exp, pv = days_of_hourly_data(n // 24)
    ts = [t.isoformat() for t in pd.date_range("2025-01-01 00:00", periods=n, freq="h")]
    rows = list(zip(ts, imp, exp, pv, strict=True))
    duplicated_row = rows[100]
    rows.insert(101, duplicated_row)  # exact repeat of the 100th timestamp
    csv = write_csv(tmp_path, ["ts", "imp", "exp", "pv"], rows)

    df, report = load_energy_data(csv, GRID_MAPPING)

    assert df["grid_import"].sum() == pytest.approx(sum(imp))  # the repeat added nothing
    assert any("exported twice" in w for w in report.warnings)


def test_dst_autumn_single_occurrence_hour(tmp_path: Path) -> None:
    """The autumn DST hour present only ONCE must not crash.

    ambiguous='infer' can only resolve the repeated hour when it physically appears
    twice (as in a Home Assistant export). Public datasets and inverter CSVs written
    against a naive local clock contain it once; that must fall back to the first
    (pre-changeover) pass and warn, not raise out of pandas.
    """
    n = 31 * 24
    imp, exp, pv = days_of_hourly_data(n // 24)
    # date_range gives each wall-clock hour exactly once, including the repeated 02:00.
    ts = [t.isoformat() for t in pd.date_range("2025-10-10 00:00", periods=n, freq="h")]
    rows = list(zip(ts, imp, exp, pv, strict=True))
    csv = write_csv(tmp_path, ["ts", "imp", "exp", "pv"], rows)

    df, report = load_energy_data(csv, GRID_MAPPING)

    assert str(pd.DatetimeIndex(df.index).tz) == "Europe/Rome"
    assert report.days_analyzed >= MIN_DAYS
    assert any("daylight-saving" in w for w in report.warnings)


def test_unsorted_timestamps_are_sorted_before_localizing(tmp_path: Path) -> None:
    """Rows out of chronological order must still localize and come back ascending."""
    n = 31 * 24
    imp, exp, pv = days_of_hourly_data(n // 24)
    ts = hourly_timestamps("2025-01-01 00:00", n)
    rows = list(zip(ts, imp, exp, pv, strict=True))
    shuffled = [rows[i] for i in (*range(10, len(rows)), *range(10))]
    csv = write_csv(tmp_path, ["ts", "imp", "exp", "pv"], shuffled)

    df, _ = load_energy_data(csv, GRID_MAPPING)

    assert df.index.is_monotonic_increasing


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


def test_30min_downsampling_to_hourly(tmp_path: Path) -> None:
    """Two 30-min intervals of [0.4, 0.6] kWh sum to 1.0 kWh for the hour."""
    n = 31 * 24 * 2
    imp = [0.4, 0.6] * (n // 2)
    exp = [0.1, 0.2] * (n // 2)
    pv = [0.0] * n
    ts = [t.isoformat() for t in pd.date_range("2025-01-01 00:00", periods=n, freq="30min")]
    rows = list(zip(ts, imp, exp, pv, strict=True))
    csv = write_csv(tmp_path, ["ts", "imp", "exp", "pv"], rows)

    df, report = load_energy_data(csv, GRID_MAPPING)

    assert report.native_resolution_minutes == HALF_HOUR_MINUTES
    assert df["grid_import"].iloc[0] == pytest.approx(1.0)
    assert df["grid_export"].iloc[0] == pytest.approx(0.3)
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


def test_days_analyzed_counts_covered_days_not_calendar_span(tmp_path: Path) -> None:
    """A gap must not inflate the annualization divisor.

    Hand-computed, and deliberately not compared against another layer of ours:
    60 days of hourly readings starting 2024-01-01, then a hole, then 5 more days
    starting 2025-01-01. That is **65 days of data** covering a **371-day span**.

    Counting the span made every per-year figure 371/65 = 5.7x too small and every
    payback 5.7x too long, and — because 371 >= 365 — it also suppressed the
    seasonality warning, so the report affirmatively called two winter months
    "a full year". Invisible on the project's own fixture, which has no gaps and
    where span and coverage are the same number.
    """
    rows: list[Sequence[object]] = []
    for start, n_days in (("2024-01-01", 60), ("2025-01-01", 5)):
        for ts in hourly_timestamps(start, n_days * 24):
            rows.append([ts, 1.0, 0.5, 2.0])  # noqa: PERF401
    path = write_csv(tmp_path, ["ts", "imp", "exp", "pv"], rows)

    _, report = load_energy_data(path, GRID_MAPPING, timezone="Europe/Rome")

    assert report.days_analyzed == 65, "days must count readings, not the span they straddle"
    assert report.seasonality_warning is True, "65 days is not a year and must say so"
    assert report.gaps_count == 1


def test_days_analyzed_is_unchanged_on_continuous_data(tmp_path: Path) -> None:
    """The fix must move no correct result: with no gap, coverage == span."""
    rows: list[Sequence[object]] = [
        [ts, 1.0, 0.5, 2.0] for ts in hourly_timestamps("2024-03-01", 90 * 24)
    ]
    path = write_csv(tmp_path, ["ts", "imp", "exp", "pv"], rows)

    _, report = load_energy_data(path, GRID_MAPPING, timezone="Europe/Rome")

    assert report.days_analyzed == 90
