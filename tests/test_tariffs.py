"""Hand-verifiable tests for tariff resolution.

Every expected band and price here can be checked against the ARERA calendar on
paper. The band calendar is tested independently of prices, so a wrong boundary
fails as a band error rather than as a mysterious cost discrepancy.

Reference week: 2025-06-09 (Mon) .. 2025-06-15 (Sun) — deliberately holiday-free
(2 June, Festa della Repubblica, falls the week before).
"""

import warnings
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from battery_worth.models import Tariff, TariffKind
from battery_worth.tariffs import assign_bands, build_price_series, italian_national_holidays

ROME = "Europe/Rome"


def rome_index(start: str, periods: int, freq: str = "h") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=periods, freq=freq, tz=ROME)


def band_at(timestamp: str) -> str:
    idx = pd.DatetimeIndex([pd.Timestamp(timestamp, tz=ROME)])
    return str(assign_bands(idx).iloc[0])


# --------------------------------------------------------------------------- flat


def test_flat_is_constant_and_aligned() -> None:
    idx = rome_index("2025-06-09", 48)
    prices = build_price_series(idx, Tariff(kind=TariffKind.FLAT, flat_price_eur_kwh=0.25))

    assert len(prices) == 48
    assert prices.index.equals(idx)
    assert (prices == 0.25).all()


def test_flat_works_on_sub_hourly_index() -> None:
    idx = rome_index("2025-06-09", 96, freq="15min")
    prices = build_price_series(idx, Tariff(kind=TariffKind.FLAT, flat_price_eur_kwh=0.31))

    assert len(prices) == 96
    assert (prices == 0.31).all()


def test_empty_index_is_rejected() -> None:
    idx = pd.DatetimeIndex([], tz=ROME)
    with pytest.raises(ValueError, match="empty index"):
        build_price_series(idx, Tariff(kind=TariffKind.FLAT, flat_price_eur_kwh=0.25))


# --------------------------------------------------------------------------- bands


def test_monday_f1_boundary() -> None:
    """Mon 07:59 is still F2 (shoulder); 08:00 opens F1."""
    assert band_at("2025-06-09 07:59") == "F2"
    assert band_at("2025-06-09 08:00") == "F1"


def test_friday_f1_closes_at_19() -> None:
    """Fri 18:59 is the last F1 hour; 19:00 drops back to F2."""
    assert band_at("2025-06-13 18:59") == "F1"
    assert band_at("2025-06-13 19:00") == "F2"


def test_weekday_night_is_f3() -> None:
    """Weekday 23:00-07:00 is F3 on both sides of midnight."""
    assert band_at("2025-06-10 22:59") == "F2"
    assert band_at("2025-06-10 23:00") == "F3"
    assert band_at("2025-06-10 06:59") == "F3"
    assert band_at("2025-06-10 07:00") == "F2"


def test_saturday_is_f2_in_working_window_never_f1() -> None:
    """Sat 06:59 F3, 07:00 F2, and no hour of Saturday is ever F1."""
    assert band_at("2025-06-14 06:59") == "F3"
    assert band_at("2025-06-14 07:00") == "F2"
    assert band_at("2025-06-14 12:00") == "F2"
    assert band_at("2025-06-14 22:59") == "F2"
    assert band_at("2025-06-14 23:00") == "F3"

    saturday = assign_bands(rome_index("2025-06-14", 24))
    assert not (saturday == "F1").any()


def test_sunday_is_f3_all_day() -> None:
    sunday = assign_bands(rome_index("2025-06-15", 24))
    assert (sunday == "F3").all()


def test_reference_week_band_counts() -> None:
    """Whole holiday-free week: 5*11 = 55 F1 hours, 5*5 + 16 = 41 F2, the rest F3."""
    week = assign_bands(rome_index("2025-06-09", 24 * 7))
    counts = week.value_counts()

    assert counts["F1"] == 55
    assert counts["F2"] == 41
    assert counts["F3"] == 24 * 7 - 55 - 41


# ------------------------------------------------------------------------ holidays


def test_christmas_on_a_weekday_is_f3() -> None:
    """25 Dec 2025 is a Thursday: without the holiday rule it would be F1 at midday."""
    assert pd.Timestamp("2025-12-25").day_name() == "Thursday"
    assert band_at("2025-12-25 12:00") == "F3"

    christmas = assign_bands(rome_index("2025-12-25", 24))
    assert (christmas == "F3").all()


def test_liberation_day_on_a_weekday_is_f3() -> None:
    """25 Apr 2025 is a Friday."""
    assert pd.Timestamp("2025-04-25").day_name() == "Friday"
    assert band_at("2025-04-25 10:00") == "F3"


def test_easter_monday_is_computed_for_multiple_years() -> None:
    """Movable feast: 2025-04-21 and 2024-04-01, both Mondays that would otherwise be F1."""
    assert band_at("2025-04-21 12:00") == "F3"
    assert band_at("2024-04-01 12:00") == "F3"

    assert pd.Timestamp("2025-04-21") in italian_national_holidays([2025])
    assert pd.Timestamp("2024-04-01") in italian_national_holidays([2024])
    # The Tuesday after Easter Monday is a normal working day.
    assert band_at("2025-04-22 12:00") == "F1"


def test_holiday_set_is_national_only() -> None:
    """No local patron saints: 7 Dec (Milan, Sant'Ambrogio) is not a national holiday."""
    holidays_2025 = italian_national_holidays([2025])

    assert pd.Timestamp("2025-12-08") in holidays_2025  # Immacolata, national
    assert pd.Timestamp("2025-12-07") not in holidays_2025  # Sant'Ambrogio, Milan only
    assert len(holidays_2025) == 11


def test_holiday_spanning_multiple_years() -> None:
    idx = pd.DatetimeIndex(
        [pd.Timestamp("2024-12-25 12:00", tz=ROME), pd.Timestamp("2025-01-01 12:00", tz=ROME)]
    )
    assert list(assign_bands(idx)) == ["F3", "F3"]


# ----------------------------------------------------------------------- DST bands


def test_bands_correct_across_spring_changeover() -> None:
    """2025-03-30 (Sun) springs forward: 23 real hours, all F3 because it is a Sunday.

    The day after (Mon 31 Mar) must resume normal weekday banding at the new offset.
    """
    day = rome_index("2025-03-30", 24)
    day = day[day.day == 30]
    bands = assign_bands(day)

    assert len(day) == 23
    assert (bands == "F3").all()
    assert band_at("2025-03-31 08:00") == "F1"
    assert band_at("2025-03-31 07:59") == "F2"


def test_bands_correct_across_autumn_changeover() -> None:
    """2025-10-26 (Sun) falls back: 25 real hours, all F3.

    The repeated 02:00 wall-clock hour occurs at two distinct instants; both are
    Sunday 02:00 local, so both must read F3.
    """
    day = pd.date_range("2025-10-26", periods=25, freq="h", tz=ROME)
    bands = assign_bands(day)

    assert len(day) == 25
    assert (bands == "F3").all()
    # Both passes of the repeated hour are 02:00 local.
    repeated = [ts for ts in day if ts.hour == 2]
    assert len(repeated) == 2
    assert list(assign_bands(pd.DatetimeIndex(repeated))) == ["F3", "F3"]


def test_bands_use_wall_clock_not_utc() -> None:
    """In summer Rome is UTC+2: 08:00 local is 06:00 UTC.

    Reading bands off UTC would call this F3 (before the 07:00 shoulder); it is F1.
    """
    ts = pd.Timestamp("2025-06-09 08:00", tz=ROME)
    assert ts.tz_convert("UTC").hour == 6
    assert band_at("2025-06-09 08:00") == "F1"


def test_band_prices_map_to_configured_values() -> None:
    idx = rome_index("2025-06-09", 24)
    tariff = Tariff(kind=TariffKind.F1_F2_F3, f1_price=0.30, f2_price=0.20, f3_price=0.10)
    prices = build_price_series(idx, tariff)
    bands = assign_bands(idx)

    assert prices.index.equals(idx)
    assert prices[bands == "F1"].eq(0.30).all()
    assert prices[bands == "F2"].eq(0.20).all()
    assert prices[bands == "F3"].eq(0.10).all()
    # Monday: 11 F1 + 5 F2 + 8 F3 hours.
    assert prices.sum() == pytest.approx(11 * 0.30 + 5 * 0.20 + 8 * 0.10)


def test_non_italian_timezone_warns() -> None:
    idx = pd.date_range("2025-06-09", periods=24, freq="h", tz="Australia/Sydney")
    tariff = Tariff(kind=TariffKind.F1_F2_F3, f1_price=0.30, f2_price=0.20, f3_price=0.10)

    with pytest.warns(UserWarning, match="Australia/Sydney"):
        build_price_series(idx, tariff)


def test_naive_index_warns_for_bands() -> None:
    idx = pd.date_range("2025-06-09", periods=24, freq="h")
    tariff = Tariff(kind=TariffKind.F1_F2_F3, f1_price=0.30, f2_price=0.20, f3_price=0.10)

    with pytest.warns(UserWarning, match="no timezone"):
        build_price_series(idx, tariff)


# -------------------------------------------------------------------- hourly CSV


def write_prices(tmp_path: Path, timestamps: list[str], prices: list[float], **names: str) -> Path:
    path = tmp_path / "prices.csv"
    ts_col = names.get("ts_col", "timestamp")
    price_col = names.get("price_col", "price")
    pd.DataFrame({ts_col: timestamps, price_col: prices}).to_csv(path, index=False)
    return path


def test_hourly_csv_aligns_exactly(tmp_path: Path) -> None:
    idx = rome_index("2025-06-09", 3)
    path = write_prices(
        tmp_path,
        ["2025-06-09 00:00", "2025-06-09 01:00", "2025-06-09 02:00"],
        [0.10, 0.20, 0.30],
    )
    tariff = Tariff(kind=TariffKind.HOURLY_CSV, hourly_prices_csv=str(path))
    prices = build_price_series(idx, tariff)

    assert prices.index.equals(idx)
    assert list(prices) == [0.10, 0.20, 0.30]


def test_hourly_csv_forward_fills_within_the_hour(tmp_path: Path) -> None:
    """Hourly prices against 15-min analysis data: each hour's price covers its 4 slots."""
    idx = rome_index("2025-06-09", 8, freq="15min")
    path = write_prices(tmp_path, ["2025-06-09 00:00", "2025-06-09 01:00"], [0.10, 0.20])
    tariff = Tariff(kind=TariffKind.HOURLY_CSV, hourly_prices_csv=str(path))
    prices = build_price_series(idx, tariff)

    assert list(prices) == [0.10, 0.10, 0.10, 0.10, 0.20, 0.20, 0.20, 0.20]


def test_hourly_csv_custom_column_names(tmp_path: Path) -> None:
    idx = rome_index("2025-06-09", 2)
    path = write_prices(
        tmp_path,
        ["2025-06-09 00:00", "2025-06-09 01:00"],
        [0.11, 0.22],
        ts_col="ora",
        price_col="prezzo",
    )
    tariff = Tariff(
        kind=TariffKind.HOURLY_CSV,
        hourly_prices_csv=str(path),
        hourly_prices_timestamp_column="ora",
        hourly_prices_price_column="prezzo",
    )
    prices = build_price_series(idx, tariff)

    assert list(prices) == [0.11, 0.22]


def test_hourly_csv_incomplete_coverage_raises(tmp_path: Path) -> None:
    """Analysis runs 4 hours, prices stop after 2: must name the uncovered range."""
    idx = rome_index("2025-06-09", 4)
    path = write_prices(tmp_path, ["2025-06-09 00:00", "2025-06-09 01:00"], [0.10, 0.20])
    tariff = Tariff(kind=TariffKind.HOURLY_CSV, hourly_prices_csv=str(path))

    with pytest.raises(ValueError) as excinfo:
        build_price_series(idx, tariff)

    msg = str(excinfo.value)
    assert "2 timestamp(s) have no price" in msg
    assert "2025-06-09 02:00" in msg  # first uncovered
    assert "2025-06-09 03:00" in msg  # last uncovered


def test_hourly_csv_gap_before_start_raises(tmp_path: Path) -> None:
    """Prices starting after the analysis period leave the leading hours unpriced."""
    idx = rome_index("2025-06-09", 3)
    path = write_prices(tmp_path, ["2025-06-09 01:00", "2025-06-09 02:00"], [0.10, 0.20])
    tariff = Tariff(kind=TariffKind.HOURLY_CSV, hourly_prices_csv=str(path))

    with pytest.raises(ValueError, match="does not cover the whole analysis period"):
        build_price_series(idx, tariff)


def test_hourly_csv_interior_hole_is_not_filled(tmp_path: Path) -> None:
    """A missing hour in the middle must raise, not inherit the previous hour's price."""
    idx = rome_index("2025-06-09", 4)
    path = write_prices(
        tmp_path,
        ["2025-06-09 00:00", "2025-06-09 01:00", "2025-06-09 03:00"],
        [0.10, 0.20, 0.40],
    )
    tariff = Tariff(kind=TariffKind.HOURLY_CSV, hourly_prices_csv=str(path))

    with pytest.raises(ValueError, match="1 timestamp\\(s\\) have no price"):
        build_price_series(idx, tariff)


def test_hourly_csv_warns_on_eur_per_mwh(tmp_path: Path) -> None:
    """PUN data is published in EUR/MWh (~120), a 1000x unit error if used as-is."""
    idx = rome_index("2025-06-09", 3)
    path = write_prices(
        tmp_path,
        ["2025-06-09 00:00", "2025-06-09 01:00", "2025-06-09 02:00"],
        [110.0, 120.0, 130.0],
    )
    tariff = Tariff(kind=TariffKind.HOURLY_CSV, hourly_prices_csv=str(path))

    with pytest.warns(UserWarning, match="EUR/MWh"):
        build_price_series(idx, tariff)


def test_hourly_csv_warns_on_eur_per_wh(tmp_path: Path) -> None:
    idx = rome_index("2025-06-09", 3)
    path = write_prices(
        tmp_path,
        ["2025-06-09 00:00", "2025-06-09 01:00", "2025-06-09 02:00"],
        [0.00025, 0.00025, 0.00025],
    )
    tariff = Tariff(kind=TariffKind.HOURLY_CSV, hourly_prices_csv=str(path))

    with pytest.warns(UserWarning, match="EUR per Wh"):
        build_price_series(idx, tariff)


def test_hourly_csv_plausible_prices_do_not_warn(tmp_path: Path) -> None:
    idx = rome_index("2025-06-09", 3)
    path = write_prices(
        tmp_path,
        ["2025-06-09 00:00", "2025-06-09 01:00", "2025-06-09 02:00"],
        [0.22, 0.25, 0.31],
    )
    tariff = Tariff(kind=TariffKind.HOURLY_CSV, hourly_prices_csv=str(path))

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        prices = build_price_series(idx, tariff)

    assert list(prices) == [0.22, 0.25, 0.31]


def test_hourly_csv_missing_file_raises() -> None:
    tariff = Tariff(kind=TariffKind.HOURLY_CSV, hourly_prices_csv="/nonexistent/prices.csv")

    with pytest.raises(ValueError, match="not found"):
        build_price_series(rome_index("2025-06-09", 2), tariff)


def test_hourly_csv_mixed_offset_timestamps_raise_an_actionable_message(tmp_path: Path) -> None:
    """Same defect as the energy CSV path, same obligation to the user.

    The price file is the other place a locally-timestamped export lands, so it must
    fail with the same explanation rather than with pandas' `utc=True` advice.
    """
    n = 6
    idx = pd.date_range("2025-10-26 00:00", periods=n, freq="h", tz="UTC").tz_convert(ROME)
    timestamps = [t.isoformat() for t in idx]
    assert "+02:00" in timestamps[0] and "+01:00" in timestamps[-1], (
        "fixture must span the changeover"
    )
    path = write_prices(tmp_path, timestamps, [0.10] * n)
    tariff = Tariff(kind=TariffKind.HOURLY_CSV, hourly_prices_csv=str(path))

    with pytest.raises(ValueError) as excinfo:
        build_price_series(rome_index("2025-10-26 00:00", n), tariff)

    msg = str(excinfo.value)
    assert "'timestamp'" in msg
    assert str(path) in msg
    assert "mixes UTC offsets" in msg
    assert "daylight-saving changeover" in msg
    assert "--prices-timestamp-col" in msg
    assert "utc=True" not in msg
    assert "to_datetime" not in msg
    assert "DatetimeIndex" not in msg


def test_hourly_csv_wrong_column_names_raise(tmp_path: Path) -> None:
    path = write_prices(tmp_path, ["2025-06-09 00:00"], [0.10])
    tariff = Tariff(
        kind=TariffKind.HOURLY_CSV,
        hourly_prices_csv=str(path),
        hourly_prices_price_column="cost",
    )

    with pytest.raises(ValueError, match="header didn't match"):
        build_price_series(rome_index("2025-06-09", 1), tariff)


def test_hourly_csv_naive_file_cannot_cover_autumn_changeover(tmp_path: Path) -> None:
    """A naive price file has 24 wall-clock stamps but the autumn day has 25 real hours.

    The repeated 02:00 exists once in the file, so the second pass (+01:00) has no
    price. That must raise rather than inherit the pre-changeover price: which of the
    two passes a single 02:00 row refers to is genuinely unknowable, and guessing it
    would put a wrong price on a real hour of energy.
    """
    idx = pd.date_range("2025-10-26", periods=25, freq="h", tz=ROME)
    naive = [str(ts.tz_localize(None)) for ts in idx]
    seen: set[str] = set()
    unique_naive = [ts for ts in naive if not (ts in seen or seen.add(ts))]  # type: ignore[func-returns-value]
    assert len(unique_naive) == 24

    path = write_prices(tmp_path, unique_naive, [0.20] * len(unique_naive))
    tariff = Tariff(kind=TariffKind.HOURLY_CSV, hourly_prices_csv=str(path))

    with pytest.raises(ValueError, match="1 timestamp\\(s\\) have no price"):
        build_price_series(idx, tariff)


def test_hourly_csv_utc_file_covers_autumn_changeover(tmp_path: Path) -> None:
    """A price file with explicit offsets prices all 25 hours of the autumn day.

    This is the correct way to supply prices across a changeover (and how PUN/day-ahead
    data actually arrives): unambiguous instants, so both passes of 02:00 are distinct.
    """
    idx = pd.date_range("2025-10-26", periods=25, freq="h", tz=ROME)
    stamps = [ts.tz_convert("UTC").isoformat() for ts in idx]
    path = write_prices(tmp_path, stamps, [0.20] * 25)
    tariff = Tariff(kind=TariffKind.HOURLY_CSV, hourly_prices_csv=str(path))

    prices = build_price_series(idx, tariff)

    assert len(prices) == 25
    assert prices.notna().all()
    assert (prices == 0.20).all()


def test_hourly_csv_covers_spring_changeover(tmp_path: Path) -> None:
    """The spring day has only 23 real hours; a file with all 23 prices them exactly."""
    idx = pd.date_range("2025-03-30", periods=23, freq="h", tz=ROME)
    stamps = [ts.tz_convert("UTC").isoformat() for ts in idx]
    path = write_prices(tmp_path, stamps, [float(i) / 100 for i in range(23)])
    tariff = Tariff(kind=TariffKind.HOURLY_CSV, hourly_prices_csv=str(path))

    prices = build_price_series(idx, tariff)

    assert len(prices) == 23
    assert prices.notna().all()
    assert prices.iloc[0] == pytest.approx(0.0)
    assert prices.iloc[-1] == pytest.approx(0.22)


# ------------------------------------------------------------------- portability


def test_the_italian_timezone_resolves_without_an_os_tz_database() -> None:
    """Europe/Rome must be resolvable, whatever the platform ships.

    `zoneinfo` reads the operating system's tz database and falls back to the
    `tzdata` PyPI package when there is none. Windows has none at all; a slim or
    distroless container base may drop its own. Where the database is missing,
    `ZoneInfo("Europe/Rome")` raises `ZoneInfoNotFoundError`, which the CLI catches
    as an unknown `--timezone` and reports by naming a flag the user never passed —
    for what is in fact the default zone. The whole F1/F2/F3 tariff is defined here,
    so a missing database takes out the band tariff entirely.

    This passes trivially on any machine with a system tz database, including this
    project's own `python:3.11-slim-bookworm` image, which does ship one. Its value
    is entirely in the environments that do not — so it is a guard against a
    platform, not an assertion about this one.
    """
    zone = ZoneInfo(ROME)
    assert str(zone) == ROME

    # Constructed and then actually used to price: resolving the zone is necessary
    # but not sufficient, and the bands are what the zone exists to serve.
    tariff = Tariff(kind=TariffKind.F1_F2_F3, f1_price=0.35, f2_price=0.30, f3_price=0.25)
    prices = build_price_series(rome_index("2025-06-09", 24), tariff)

    assert len(prices) == 24
    assert set(prices.unique()) <= {0.35, 0.30, 0.25}
