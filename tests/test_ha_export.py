"""Tests for the standalone Home Assistant export script.

The script cannot be tested against a live instance, so nothing here mocks the
socket library. Every test drives a PURE function with a recorded or synthetic
payload of the shape Home Assistant actually returns, which is where the format
assumptions worth pinning live:

  * `start` is integer MILLISECONDS since the epoch, not an ISO string
  * statistics timestamps mark the START of the period (interval-starting,
    matching ingest.py) and must NOT be shifted
  * `types: ["change"]` gives the per-interval delta, so no cumulative diffing

The WebSocket frame codec is tested directly for the same reason: it is
hand-written, so its correctness cannot be assumed from a passing end-to-end run
that never happens in CI.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

# `scripts` is on the import path via [tool.pytest.ini_options] pythonpath in
# pyproject.toml: ha_export.py is a standalone tool, deliberately not part of the
# battery_worth package.
from ha_export import (
    TOKEN_ENV_VAR,
    Frame,
    HaExportError,
    Row,
    _parse_ws_url,
    decode_frame,
    encode_frame,
    epoch_ms_to_datetime,
    merge_rows,
    month_chunks,
    parse_day,
    resolve_token,
    rows_from_response,
    write_csv,
)

IMPORT_SENSOR = "sensor.grid_import_energy"
EXPORT_SENSOR = "sensor.grid_export_energy"
PV_SENSOR = "sensor.solar_energy"

# A recorded-shape response: two hours for each of the three sensors.
# 1704067200000 ms = 2024-01-01T00:00:00Z, 1704070800000 ms = 2024-01-01T01:00:00Z.
HOUR_0_MS = 1704067200000
HOUR_1_MS = 1704070800000

RECORDED_RESPONSE: dict[str, Any] = {
    IMPORT_SENSOR: [
        {"start": HOUR_0_MS, "end": HOUR_1_MS, "change": 0.42},
        {"start": HOUR_1_MS, "end": HOUR_1_MS + 3600000, "change": 0.31},
    ],
    EXPORT_SENSOR: [
        {"start": HOUR_0_MS, "end": HOUR_1_MS, "change": 0.0},
        {"start": HOUR_1_MS, "end": HOUR_1_MS + 3600000, "change": 1.75},
    ],
    PV_SENSOR: [
        {"start": HOUR_0_MS, "end": HOUR_1_MS, "change": 0.0},
        {"start": HOUR_1_MS, "end": HOUR_1_MS + 3600000, "change": 2.06},
    ],
}


def utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


# --------------------------------------------------------------------------
# Epoch-milliseconds conversion
# --------------------------------------------------------------------------


def test_start_is_parsed_as_epoch_milliseconds_not_seconds() -> None:
    """1704067200000 is 2024-01-01T00:00:00Z in ms; read as seconds it is year 55943."""
    assert epoch_ms_to_datetime(HOUR_0_MS) == utc(2024, 1, 1, 0)


def test_consecutive_periods_are_exactly_one_hour_apart() -> None:
    delta = epoch_ms_to_datetime(HOUR_1_MS) - epoch_ms_to_datetime(HOUR_0_MS)
    assert delta.total_seconds() == 3600


def test_epoch_conversion_returns_aware_utc() -> None:
    assert epoch_ms_to_datetime(HOUR_0_MS).tzinfo is UTC


def test_float_milliseconds_are_accepted() -> None:
    assert epoch_ms_to_datetime(float(HOUR_0_MS)) == utc(2024, 1, 1, 0)


def test_iso_strings_are_accepted_for_older_cores() -> None:
    assert epoch_ms_to_datetime("2024-01-01T00:00:00+00:00") == utc(2024, 1, 1, 0)


def test_naive_iso_string_is_read_as_utc() -> None:
    assert epoch_ms_to_datetime("2024-01-01T00:00:00") == utc(2024, 1, 1, 0)


@pytest.mark.parametrize("value", [None, True, "not-a-date", {"start": 1}, []])
def test_unparseable_timestamps_raise_a_user_facing_error(value: object) -> None:
    with pytest.raises(HaExportError):
        epoch_ms_to_datetime(value)


# --------------------------------------------------------------------------
# Response -> rows
# --------------------------------------------------------------------------


def test_recorded_response_becomes_one_row_per_hour() -> None:
    rows = rows_from_response(RECORDED_RESPONSE, IMPORT_SENSOR, EXPORT_SENSOR, PV_SENSOR)
    assert [r.timestamp for r in rows] == [utc(2024, 1, 1, 0), utc(2024, 1, 1, 1)]
    assert rows[0].grid_import == 0.42
    assert rows[1].grid_export == 1.75
    assert rows[1].pv_production == 2.06


def test_timestamps_are_interval_starting_and_never_shifted() -> None:
    """HA statistic timestamps mark the START of their period, which is the
    convention ingest.py uses. The 00:00 row must stay 00:00, not become 01:00."""
    rows = rows_from_response(RECORDED_RESPONSE, IMPORT_SENSOR, EXPORT_SENSOR, PV_SENSOR)
    assert rows[0].timestamp == utc(2024, 1, 1, 0)
    # And the value on that row is the one whose `start` was that hour.
    assert rows[0].grid_import == 0.42


def test_change_is_used_verbatim_with_no_cumulative_diffing() -> None:
    """`change` is already the per-interval delta. If the script diffed it, the
    second row would be 0.31 - 0.42 = -0.11 rather than 0.31."""
    rows = rows_from_response(RECORDED_RESPONSE, IMPORT_SENSOR, EXPORT_SENSOR, PV_SENSOR)
    assert [r.grid_import for r in rows] == [0.42, 0.31]


def test_sum_field_is_ignored_when_present() -> None:
    """A response carrying `sum` (the cumulative total) alongside `change` must
    not tempt the parser: only `change` is read."""
    response = {
        IMPORT_SENSOR: [
            {"start": HOUR_0_MS, "change": 0.42, "sum": 1000.42},
            {"start": HOUR_1_MS, "change": 0.31, "sum": 1000.73},
        ],
        EXPORT_SENSOR: [],
    }
    rows = rows_from_response(response, IMPORT_SENSOR, EXPORT_SENSOR, None)
    assert [r.grid_import for r in rows] == [0.42, 0.31]


def test_sensors_with_different_coverage_align_on_the_union_of_hours() -> None:
    """PV reports nothing at night; that hour is zero PV, not a dropped row."""
    response = {
        IMPORT_SENSOR: [
            {"start": HOUR_0_MS, "change": 0.5},
            {"start": HOUR_1_MS, "change": 0.6},
        ],
        EXPORT_SENSOR: [{"start": HOUR_1_MS, "change": 0.2}],
        PV_SENSOR: [{"start": HOUR_1_MS, "change": 1.1}],
    }
    rows = rows_from_response(response, IMPORT_SENSOR, EXPORT_SENSOR, PV_SENSOR)
    assert len(rows) == 2
    assert rows[0].grid_export == 0.0
    assert rows[0].pv_production == 0.0
    assert rows[1].pv_production == 1.1


def test_rows_are_sorted_by_timestamp_even_if_the_payload_is_not() -> None:
    response = {
        IMPORT_SENSOR: [
            {"start": HOUR_1_MS, "change": 0.31},
            {"start": HOUR_0_MS, "change": 0.42},
        ],
        EXPORT_SENSOR: [],
    }
    rows = rows_from_response(response, IMPORT_SENSOR, EXPORT_SENSOR, None)
    assert [r.grid_import for r in rows] == [0.42, 0.31]


def test_omitting_the_pv_sensor_yields_zero_pv() -> None:
    rows = rows_from_response(RECORDED_RESPONSE, IMPORT_SENSOR, EXPORT_SENSOR, None)
    assert all(r.pv_production == 0.0 for r in rows)


def test_a_null_change_is_skipped_rather_than_counted_as_zero() -> None:
    """A period the recorder has no delta for is unknown, not zero energy."""
    response = {
        IMPORT_SENSOR: [
            {"start": HOUR_0_MS, "change": None},
            {"start": HOUR_1_MS, "change": 0.31},
        ],
        EXPORT_SENSOR: [],
    }
    rows = rows_from_response(response, IMPORT_SENSOR, EXPORT_SENSOR, None)
    assert [r.timestamp for r in rows] == [utc(2024, 1, 1, 1)]


def test_empty_response_yields_no_rows() -> None:
    assert rows_from_response({}, IMPORT_SENSOR, EXPORT_SENSOR, PV_SENSOR) == []


# --------------------------------------------------------------------------
# Malformed responses
# --------------------------------------------------------------------------


def test_entries_that_are_not_a_list_raise_with_the_statistic_id_named() -> None:
    response = {IMPORT_SENSOR: {"start": HOUR_0_MS}, EXPORT_SENSOR: []}
    with pytest.raises(HaExportError, match=IMPORT_SENSOR):
        rows_from_response(response, IMPORT_SENSOR, EXPORT_SENSOR, None)


def test_an_entry_without_start_raises() -> None:
    response = {IMPORT_SENSOR: [{"change": 0.4}], EXPORT_SENSOR: []}
    with pytest.raises(HaExportError, match="start"):
        rows_from_response(response, IMPORT_SENSOR, EXPORT_SENSOR, None)


def test_a_non_numeric_change_raises_rather_than_being_coerced() -> None:
    response = {IMPORT_SENSOR: [{"start": HOUR_0_MS, "change": "0.42"}], EXPORT_SENSOR: []}
    with pytest.raises(HaExportError, match="non-numeric"):
        rows_from_response(response, IMPORT_SENSOR, EXPORT_SENSOR, None)


def test_a_non_dict_entry_raises() -> None:
    response = {IMPORT_SENSOR: [[HOUR_0_MS, 0.42]], EXPORT_SENSOR: []}
    with pytest.raises(HaExportError):
        rows_from_response(response, IMPORT_SENSOR, EXPORT_SENSOR, None)


# --------------------------------------------------------------------------
# Monthly chunking
# --------------------------------------------------------------------------


def test_a_full_year_splits_into_twelve_calendar_months() -> None:
    chunks = month_chunks(utc(2024, 1, 1), utc(2025, 1, 1))
    assert len(chunks) == 12
    assert chunks[0] == (utc(2024, 1, 1), utc(2024, 2, 1))
    assert chunks[-1] == (utc(2024, 12, 1), utc(2025, 1, 1))


def test_chunks_are_contiguous_and_half_open() -> None:
    """Each window ends exactly where the next begins: no hour requested twice,
    none skipped."""
    chunks = month_chunks(utc(2024, 1, 1), utc(2025, 1, 1))
    for (_, first_end), (second_start, _) in zip(chunks, chunks[1:], strict=False):
        assert first_end == second_start


def test_chunks_exactly_span_the_requested_range() -> None:
    chunks = month_chunks(utc(2023, 6, 15), utc(2024, 3, 2))
    assert chunks[0][0] == utc(2023, 6, 15)
    assert chunks[-1][1] == utc(2024, 3, 2)


def test_a_partial_first_month_starts_on_the_requested_day() -> None:
    chunks = month_chunks(utc(2024, 1, 15), utc(2024, 3, 1))
    assert chunks[0] == (utc(2024, 1, 15), utc(2024, 2, 1))
    assert chunks[1] == (utc(2024, 2, 1), utc(2024, 3, 1))


def test_a_range_inside_one_month_is_a_single_chunk() -> None:
    chunks = month_chunks(utc(2024, 1, 5), utc(2024, 1, 20))
    assert chunks == [(utc(2024, 1, 5), utc(2024, 1, 20))]


def test_chunking_crosses_the_year_boundary() -> None:
    chunks = month_chunks(utc(2024, 11, 10), utc(2025, 2, 3))
    assert [(s.year, s.month) for s, _ in chunks] == [(2024, 11), (2024, 12), (2025, 1), (2025, 2)]


def test_february_in_a_leap_year_is_one_chunk_ending_on_march_first() -> None:
    chunks = month_chunks(utc(2024, 2, 1), utc(2024, 3, 1))
    assert chunks == [(utc(2024, 2, 1), utc(2024, 3, 1))]


def test_an_end_before_the_start_is_rejected() -> None:
    with pytest.raises(HaExportError, match="must be after"):
        month_chunks(utc(2024, 6, 1), utc(2024, 1, 1))


def test_an_empty_range_is_rejected() -> None:
    with pytest.raises(HaExportError, match="must be after"):
        month_chunks(utc(2024, 6, 1), utc(2024, 6, 1))


# --------------------------------------------------------------------------
# Merging chunks
# --------------------------------------------------------------------------


def make_row(hour: int, value: float) -> Row:
    return Row(
        timestamp=utc(2024, 1, 1, hour),
        grid_import=value,
        grid_export=0.0,
        pv_production=0.0,
    )


def test_merging_concatenates_chunks_in_time_order() -> None:
    merged = merge_rows([[make_row(2, 0.3)], [make_row(0, 0.1), make_row(1, 0.2)]])
    assert [r.grid_import for r in merged] == [0.1, 0.2, 0.3]


def test_a_boundary_hour_returned_by_two_chunks_is_not_double_counted() -> None:
    """Windows are half-open, but an instance treating its own end as inclusive
    must not produce a duplicated hour in the CSV."""
    merged = merge_rows([[make_row(0, 0.1), make_row(1, 0.2)], [make_row(1, 0.2)]])
    assert len(merged) == 2
    assert [r.grid_import for r in merged] == [0.1, 0.2]


def test_merging_no_chunks_yields_no_rows() -> None:
    assert merge_rows([]) == []


# --------------------------------------------------------------------------
# CSV output
# --------------------------------------------------------------------------


def test_csv_header_matches_the_schema_ingest_accepts(tmp_path: Path) -> None:
    out = tmp_path / "energy.csv"
    write_csv(rows_from_response(RECORDED_RESPONSE, IMPORT_SENSOR, EXPORT_SENSOR, PV_SENSOR), out)
    header = out.read_text().splitlines()[0]
    assert header == "timestamp,grid_import,grid_export,pv_production"


def test_csv_rows_carry_iso_timestamps_and_the_expected_values(tmp_path: Path) -> None:
    out = tmp_path / "energy.csv"
    write_csv(rows_from_response(RECORDED_RESPONSE, IMPORT_SENSOR, EXPORT_SENSOR, PV_SENSOR), out)
    with out.open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert rows[0]["timestamp"] == "2024-01-01T00:00:00+00:00"
    assert float(rows[0]["grid_import"]) == 0.42
    assert float(rows[1]["pv_production"]) == 2.06


def test_csv_timestamps_round_trip_through_the_ingest_parser(tmp_path: Path) -> None:
    """The output must be readable by the same pandas parsing ingest.py uses,
    including the explicit UTC offset."""
    out = tmp_path / "energy.csv"
    write_csv(rows_from_response(RECORDED_RESPONSE, IMPORT_SENSOR, EXPORT_SENSOR, PV_SENSOR), out)
    parsed = pd.to_datetime(pd.read_csv(out)["timestamp"])
    assert parsed.iloc[0] == pd.Timestamp("2024-01-01T00:00:00+00:00")
    assert parsed.dt.tz is not None


def test_written_values_are_not_rounded_away(tmp_path: Path) -> None:
    out = tmp_path / "energy.csv"
    write_csv([Row(utc(2024, 1, 1), 0.000123, 0.0, 0.0)], out)
    with out.open() as handle:
        row = next(iter(csv.DictReader(handle)))
    assert float(row["grid_import"]) == 0.000123


def test_an_empty_row_list_still_writes_a_header(tmp_path: Path) -> None:
    out = tmp_path / "energy.csv"
    write_csv([], out)
    assert out.read_text().strip() == "timestamp,grid_import,grid_export,pv_production"


# --------------------------------------------------------------------------
# Token handling
# --------------------------------------------------------------------------


def test_the_flag_supplies_the_token() -> None:
    assert resolve_token("abc123", environ={}) == "abc123"


def test_the_environment_variable_supplies_the_token() -> None:
    assert resolve_token(None, environ={TOKEN_ENV_VAR: "from-env"}) == "from-env"


def test_the_flag_wins_over_the_environment() -> None:
    assert resolve_token("from-flag", environ={TOKEN_ENV_VAR: "from-env"}) == "from-flag"


def test_a_token_is_stripped_of_surrounding_whitespace() -> None:
    """Copying a token out of the HA dialog easily picks up a trailing newline."""
    assert resolve_token("  abc123\n", environ={}) == "abc123"


def test_a_missing_token_names_the_environment_variable_as_the_preferred_path() -> None:
    with pytest.raises(HaExportError) as excinfo:
        resolve_token(None, environ={})
    message = str(excinfo.value)
    assert TOKEN_ENV_VAR in message
    assert "shell history" in message


def test_an_empty_environment_variable_is_treated_as_missing() -> None:
    with pytest.raises(HaExportError):
        resolve_token(None, environ={TOKEN_ENV_VAR: "   "})


def test_the_error_message_never_contains_the_token() -> None:
    """A token is only ever sent; it is never echoed into output the user might
    paste into a bug report."""
    with pytest.raises(HaExportError) as excinfo:
        resolve_token("   ", environ={TOKEN_ENV_VAR: "secret-token-value"})
    assert "secret-token-value" not in str(excinfo.value)


# --------------------------------------------------------------------------
# Date parsing
# --------------------------------------------------------------------------


def test_start_is_the_first_instant_of_the_day() -> None:
    assert parse_day("2024-01-01") == utc(2024, 1, 1, 0)


def test_end_is_inclusive_so_its_window_runs_to_the_next_midnight() -> None:
    """--end 2024-12-31 must include December 31st, i.e. run to Jan 1st 00:00."""
    assert parse_day("2024-12-31", end_of_day=True) == utc(2025, 1, 1, 0)


def test_a_documented_full_year_produces_twelve_chunks_covering_it() -> None:
    """The README's exact example: --start 2024-01-01 --end 2024-12-31."""
    chunks = month_chunks(parse_day("2024-01-01"), parse_day("2024-12-31", end_of_day=True))
    assert len(chunks) == 12
    assert chunks[0][0] == utc(2024, 1, 1)
    assert chunks[-1][1] == utc(2025, 1, 1)


@pytest.mark.parametrize("value", ["01/01/2024", "2024-13-01", "yesterday", ""])
def test_bad_dates_are_rejected_with_the_expected_format(value: str) -> None:
    with pytest.raises(HaExportError, match="YYYY-MM-DD"):
        parse_day(value)


# --------------------------------------------------------------------------
# URL parsing
# --------------------------------------------------------------------------


def test_the_websocket_path_is_appended_to_a_base_url() -> None:
    assert _parse_ws_url("ws://homeassistant.local:8123") == (
        "homeassistant.local",
        8123,
        "/api/websocket",
        False,
    )


def test_https_urls_select_tls_and_the_default_port() -> None:
    host, port, path, use_tls = _parse_ws_url("https://ha.example.com")
    assert (host, port, path, use_tls) == ("ha.example.com", 443, "/api/websocket", True)


def test_a_url_already_naming_the_websocket_path_is_not_doubled() -> None:
    assert _parse_ws_url("ws://ha.local:8123/api/websocket")[2] == "/api/websocket"


def test_a_bare_host_defaults_to_plain_websocket() -> None:
    host, _, _, use_tls = _parse_ws_url("homeassistant.local:8123")
    assert (host, use_tls) == ("homeassistant.local", False)


def test_an_unsupported_scheme_is_rejected() -> None:
    with pytest.raises(HaExportError, match="scheme"):
        _parse_ws_url("ftp://ha.local")


# --------------------------------------------------------------------------
# WebSocket framing (RFC 6455)
# --------------------------------------------------------------------------

MASK = b"\x01\x02\x03\x04"


def test_a_short_frame_round_trips() -> None:
    encoded = encode_frame(b"hello", 0x1, MASK)
    decoded = decode_frame(encoded)
    assert decoded is not None
    frame, consumed = decoded
    assert frame == Frame(fin=True, opcode=0x1, payload=b"hello")
    assert consumed == len(encoded)


def test_client_frames_are_masked_as_the_spec_requires() -> None:
    """RFC 6455 §5.3: a client MUST mask. HA drops unmasked frames."""
    encoded = encode_frame(b"hello", 0x1, MASK)
    assert encoded[1] & 0x80
    assert encoded[2:6] == MASK
    assert encoded[6:] != b"hello"


def test_a_medium_frame_uses_the_sixteen_bit_length() -> None:
    payload = b"x" * 200
    encoded = encode_frame(payload, 0x1, MASK)
    assert encoded[1] & 0x7F == 126
    decoded = decode_frame(encoded)
    assert decoded is not None
    assert decoded[0].payload == payload


def test_a_large_frame_uses_the_sixty_four_bit_length() -> None:
    """A year of hourly statistics is far past 64 KiB in one message."""
    payload = b"y" * 70000
    encoded = encode_frame(payload, 0x1, MASK)
    assert encoded[1] & 0x7F == 127
    decoded = decode_frame(encoded)
    assert decoded is not None
    assert decoded[0].payload == payload


@pytest.mark.parametrize("size", [0, 1, 125, 126, 127, 65535, 65536])
def test_length_boundaries_round_trip(size: int) -> None:
    payload = b"z" * size
    decoded = decode_frame(encode_frame(payload, 0x1, MASK))
    assert decoded is not None
    assert decoded[0].payload == payload


def test_an_incomplete_frame_decodes_to_none_so_the_caller_reads_more() -> None:
    encoded = encode_frame(b"hello world", 0x1, MASK)
    for cut in range(len(encoded)):
        assert decode_frame(encoded[:cut]) is None
    assert decode_frame(encoded) is not None


def test_only_the_first_frame_is_consumed_from_a_buffer_holding_two() -> None:
    buffer = encode_frame(b"one", 0x1, MASK) + encode_frame(b"two", 0x1, MASK)
    first = decode_frame(buffer)
    assert first is not None
    frame, consumed = first
    assert frame.payload == b"one"
    second = decode_frame(buffer[consumed:])
    assert second is not None
    assert second[0].payload == b"two"


def test_an_unmasked_server_frame_decodes_unchanged() -> None:
    """Server-to-client frames are not masked; the decoder must not unmask them."""
    payload = b'{"type":"auth_required"}'
    unmasked = bytes([0x81, len(payload)]) + payload
    decoded = decode_frame(unmasked)
    assert decoded is not None
    assert decoded[0].payload == payload


def test_a_non_final_frame_reports_fin_false_for_reassembly() -> None:
    encoded = bytearray(encode_frame(b"part", 0x1, MASK))
    encoded[0] &= 0x7F
    decoded = decode_frame(bytes(encoded))
    assert decoded is not None
    assert decoded[0].fin is False


def test_a_mask_key_of_the_wrong_length_is_rejected() -> None:
    with pytest.raises(ValueError, match="4 bytes"):
        encode_frame(b"hello", 0x1, b"\x01\x02")
