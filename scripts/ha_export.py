#!/usr/bin/env python3
"""Export Home Assistant long-term statistics to a battery-worth CSV.

This is a STANDALONE tool, deliberately not part of the `battery_worth` package:
the analysis engine stays offline and dependency-free, with no network or auth
surface anywhere near it. This script talks to your own Home Assistant instance,
writes a plain CSV, and then gets out of the way.

It reads hourly long-term statistics through the WebSocket API's
`recorder/statistics_during_period` command, asking for `types: ["change"]` —
the per-interval delta, already in kWh — so no cumulative diffing is needed
downstream. The output is the grid-centric schema `battery-worth analyze`
accepts directly:

    timestamp,grid_import,grid_export,pv_production

Usage (token from the environment — see the note on shell history below):

    export HA_TOKEN='...'
    python scripts/ha_export.py \\
        --url ws://homeassistant.local:8123 \\
        --import-sensor sensor.grid_import_energy \\
        --export-sensor sensor.grid_export_energy \\
        --pv-sensor sensor.solar_energy \\
        --start 2024-01-01 --end 2024-12-31 \\
        -o my_energy.csv

    battery-worth analyze my_energy.csv

Standard library only: no dependency is added to battery-worth for this, and no
optional extra has to be installed before the script will run. The WebSocket
client below is a minimal RFC 6455 implementation covering exactly what this
one request/response conversation needs.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import socket
import ssl
import struct
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

TOKEN_ENV_VAR: Final = "HA_TOKEN"
OUTPUT_COLUMNS: Final = ["timestamp", "grid_import", "grid_export", "pv_production"]
MILLISECONDS_PER_SECOND: Final = 1000

_GUID: Final = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_DEFAULT_TIMEOUT_SECONDS: Final = 60.0
_RECEIVE_CHUNK: Final = 65536

# RFC 6455 section 5.2 opcodes and the payload-length sentinels that select a
# 16- or 64-bit extended length field.
_OP_CONTINUATION: Final = 0x0
_OP_TEXT: Final = 0x1
_OP_BINARY: Final = 0x2
_OP_CLOSE: Final = 0x8
_OP_PING: Final = 0x9
_OP_PONG: Final = 0xA
_LEN_16BIT: Final = 126
_LEN_64BIT: Final = 127
_MAX_7BIT_LEN: Final = 125
_MAX_16BIT_LEN: Final = 0xFFFF
_MASK_KEY_BYTES: Final = 4
_MIN_HEADER_BYTES: Final = 2  # opcode byte + length byte, before any extended length
_HTTP_SWITCHING_PROTOCOLS: Final = 101

_DECEMBER: Final = 12
# A big instance has hundreds of statistic_ids; an error message that dumps all of
# them scrolls the actual error off the screen.
_MAX_SUGGESTIONS: Final = 40


class HaExportError(Exception):
    """A user-facing failure: printed as a message, never as a traceback."""


# --------------------------------------------------------------------------
# WebSocket framing (RFC 6455) — pure functions, tested directly
# --------------------------------------------------------------------------


def encode_frame(payload: bytes, opcode: int, mask_key: bytes) -> bytes:
    """Encode one final, masked frame. Clients MUST mask (RFC 6455 §5.3)."""
    if len(mask_key) != _MASK_KEY_BYTES:
        msg = f"mask key must be {_MASK_KEY_BYTES} bytes, got {len(mask_key)}"
        raise ValueError(msg)

    header = bytearray()
    header.append(0x80 | opcode)  # FIN set, no reserved bits, no fragmentation
    length = len(payload)
    if length <= _MAX_7BIT_LEN:
        header.append(0x80 | length)
    elif length <= _MAX_16BIT_LEN:
        header.append(0x80 | _LEN_16BIT)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | _LEN_64BIT)
        header.extend(struct.pack("!Q", length))
    header.extend(mask_key)
    masked = bytes(b ^ mask_key[i % _MASK_KEY_BYTES] for i, b in enumerate(payload))
    return bytes(header) + masked


@dataclass(frozen=True)
class Frame:
    """One decoded WebSocket frame."""

    fin: bool
    opcode: int
    payload: bytes


def decode_frame(buffer: bytes) -> tuple[Frame, int] | None:
    """Decode the first frame in `buffer`.

    Returns the frame and the number of bytes consumed, or None when the buffer
    does not yet hold a complete frame (the caller then reads more from the
    socket). Server-to-client frames are unmasked, but a masked frame is
    decoded correctly anyway rather than being silently mis-read.
    """
    if len(buffer) < _MIN_HEADER_BYTES:
        return None
    first, second = buffer[0], buffer[1]
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    offset = 2

    if length == _LEN_16BIT:
        if len(buffer) < offset + 2:
            return None
        length = struct.unpack("!H", buffer[offset : offset + 2])[0]
        offset += 2
    elif length == _LEN_64BIT:
        if len(buffer) < offset + 8:
            return None
        length = struct.unpack("!Q", buffer[offset : offset + 8])[0]
        offset += 8

    mask_key = b""
    if masked:
        if len(buffer) < offset + _MASK_KEY_BYTES:
            return None
        mask_key = buffer[offset : offset + _MASK_KEY_BYTES]
        offset += _MASK_KEY_BYTES

    if len(buffer) < offset + length:
        return None

    payload = buffer[offset : offset + length]
    if masked:
        payload = bytes(b ^ mask_key[i % _MASK_KEY_BYTES] for i, b in enumerate(payload))
    return Frame(fin=fin, opcode=opcode, payload=payload), offset + length


class WebSocket:
    """A minimal client for one request/response conversation.

    Deliberately not a general WebSocket implementation: no compression, no
    subprotocols, no concurrent readers. It connects, sends text frames, and
    reads text frames, answering pings so a long export is not dropped.
    """

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buffer = b""

    @classmethod
    def connect(cls, url: str, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> WebSocket:
        host, port, path, use_tls = _parse_ws_url(url)
        try:
            sock: socket.socket = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            msg = (
                f"Could not reach Home Assistant at {host}:{port} ({exc}).\n"
                "Check that:\n"
                f"  * the host name is right and resolves from this machine "
                f"(try: ping {host})\n"
                "  * Home Assistant is running and reachable on that port\n"
                "  * the --url scheme matches your setup (ws:// for plain HTTP, "
                "wss:// for HTTPS)"
            )
            raise HaExportError(msg) from exc

        if use_tls:
            context = ssl.create_default_context()
            try:
                sock = context.wrap_socket(sock, server_hostname=host)
            except ssl.SSLError as exc:
                msg = (
                    f"TLS handshake with {host}:{port} failed ({exc}). If your Home "
                    "Assistant uses a self-signed certificate, connect over ws:// on "
                    "the local network instead."
                )
                raise HaExportError(msg) from exc

        ws = cls(sock)
        ws._handshake(host, port, path)
        return ws

    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._sock.sendall(request.encode())

        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self._sock.recv(_RECEIVE_CHUNK)
            if not chunk:
                msg = (
                    "Home Assistant closed the connection during the WebSocket "
                    "handshake. Check that --url points at Home Assistant itself and "
                    "not at a proxy that strips Upgrade headers."
                )
                raise HaExportError(msg)
            response += chunk

        head, _, rest = response.partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n")[0].decode(errors="replace")
        if str(_HTTP_SWITCHING_PROTOCOLS) not in status_line:
            msg = (
                f"Home Assistant did not accept the WebSocket upgrade: {status_line!r}.\n"
                f"Check that --url points at the base URL (e.g. ws://homeassistant.local:8123) "
                "and not at a specific page."
            )
            raise HaExportError(msg)
        _verify_accept_key(key, head)
        self._buffer = rest

    def send_json(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message).encode()
        self._sock.sendall(encode_frame(payload, _OP_TEXT, os.urandom(_MASK_KEY_BYTES)))

    def receive_json(self) -> dict[str, Any]:
        """Read the next text message, transparently handling control frames."""
        pending = b""
        while True:
            frame = self._next_frame()
            if frame.opcode == _OP_PING:
                self._sock.sendall(
                    encode_frame(frame.payload, _OP_PONG, os.urandom(_MASK_KEY_BYTES))
                )
                continue
            if frame.opcode == _OP_PONG:
                continue
            if frame.opcode == _OP_CLOSE:
                msg = (
                    "Home Assistant closed the WebSocket connection unexpectedly. "
                    "If this happened part-way through a long export, try a shorter "
                    "period with --start / --end."
                )
                raise HaExportError(msg)
            if frame.opcode in (_OP_TEXT, _OP_CONTINUATION):
                pending += frame.payload
                if frame.fin:
                    return _parse_json_message(pending)
                continue
            if frame.opcode == _OP_BINARY:
                msg = "Unexpected binary frame from Home Assistant; expected JSON text."
                raise HaExportError(msg)

    def _next_frame(self) -> Frame:
        while True:
            decoded = decode_frame(self._buffer)
            if decoded is not None:
                frame, consumed = decoded
                self._buffer = self._buffer[consumed:]
                return frame
            try:
                chunk = self._sock.recv(_RECEIVE_CHUNK)
            except TimeoutError as exc:
                msg = (
                    "Timed out waiting for Home Assistant to answer. Long periods can "
                    "take a while to aggregate; try a shorter --start/--end range."
                )
                raise HaExportError(msg) from exc
            if not chunk:
                msg = "Home Assistant closed the connection before sending a full response."
                raise HaExportError(msg)
            self._buffer += chunk

    def close(self) -> None:
        try:
            self._sock.sendall(encode_frame(b"", _OP_CLOSE, os.urandom(_MASK_KEY_BYTES)))
        except OSError:
            pass  # already gone; nothing useful to report while shutting down
        finally:
            self._sock.close()


def _parse_json_message(payload: bytes) -> dict[str, Any]:
    try:
        message = json.loads(payload.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = f"Home Assistant sent a message that is not valid JSON: {exc}"
        raise HaExportError(msg) from exc
    if not isinstance(message, dict):
        msg = f"Expected a JSON object from Home Assistant, got {type(message).__name__}."
        raise HaExportError(msg)
    return message


def _verify_accept_key(key: str, head: bytes) -> None:
    """Check Sec-WebSocket-Accept, which proves we reached a real WebSocket peer."""
    # SHA-1 is not a security choice here: RFC 6455 §4.2.2 specifies exactly this
    # digest for the handshake, and the peer computes the same one. It authenticates
    # nothing — the access token does that, over TLS when the URL says wss://.
    digest = hashlib.sha1((key + _GUID).encode(), usedforsecurity=False).digest()
    expected = base64.b64encode(digest).decode()
    for line in head.decode(errors="replace").split("\r\n")[1:]:
        name, _, value = line.partition(":")
        if name.strip().lower() == "sec-websocket-accept" and value.strip() == expected:
            return
    msg = (
        "The server completed an upgrade but did not return the expected "
        "Sec-WebSocket-Accept header. This usually means something other than Home "
        "Assistant answered on that URL."
    )
    raise HaExportError(msg)


def _parse_ws_url(url: str) -> tuple[str, int, str, bool]:
    """Split a base URL into (host, port, websocket path, TLS?).

    Accepts http/https as well as ws/wss, because that is what users copy out of
    their browser's address bar.
    """
    parsed = urlparse(url if "://" in url else f"ws://{url}")
    scheme = parsed.scheme.lower()
    if scheme not in ("ws", "wss", "http", "https"):
        msg = (
            f"Unsupported URL scheme '{parsed.scheme}' in --url. Use ws:// (plain) or "
            "wss:// (TLS), e.g. ws://homeassistant.local:8123"
        )
        raise HaExportError(msg)
    use_tls = scheme in ("wss", "https")
    if not parsed.hostname:
        msg = f"Could not read a host name from --url '{url}'."
        raise HaExportError(msg)
    port = parsed.port or (443 if use_tls else 80)
    base = parsed.path.rstrip("/")
    path = f"{base}/api/websocket" if not base.endswith("/api/websocket") else base
    return parsed.hostname, port, path, use_tls


# --------------------------------------------------------------------------
# Home Assistant conversation
# --------------------------------------------------------------------------


class HaClient:
    """Authenticated WebSocket session against one Home Assistant instance."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._next_id = 1

    @classmethod
    def connect(cls, url: str, token: str, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> HaClient:
        ws = WebSocket.connect(url, timeout=timeout)
        client = cls(ws)
        try:
            client._authenticate(token)
        except HaExportError:
            ws.close()
            raise
        return client

    def _authenticate(self, token: str) -> None:
        greeting = self._ws.receive_json()
        if greeting.get("type") != "auth_required":
            msg = (
                "Home Assistant did not ask for authentication as expected "
                f"(got type={greeting.get('type')!r}). Check that --url points at a "
                "Home Assistant instance."
            )
            raise HaExportError(msg)

        # The token is sent, never logged: no message on this path is ever echoed.
        self._ws.send_json({"type": "auth", "access_token": token})
        result = self._ws.receive_json()
        if result.get("type") == "auth_invalid":
            msg = (
                "Home Assistant rejected the access token.\n"
                "Create a fresh long-lived access token: click your user name in the "
                "Home Assistant sidebar -> Security tab -> 'Create token' at the bottom, "
                f"then pass it with --token or set {TOKEN_ENV_VAR}.\n"
                "Tokens are long strings; check that the whole value was copied and "
                "that it has not been revoked."
            )
            raise HaExportError(msg)
        if result.get("type") != "auth_ok":
            msg = f"Unexpected authentication reply from Home Assistant: {result.get('type')!r}"
            raise HaExportError(msg)

    def command(self, payload: dict[str, Any]) -> Any:
        """Send one command and return its `result`, raising on an error reply."""
        message_id = self._next_id
        self._next_id += 1
        self._ws.send_json({**payload, "id": message_id})

        while True:
            reply = self._ws.receive_json()
            if reply.get("id") != message_id:
                continue  # events or replies to other ids: not ours
            if not reply.get("success", False):
                error = reply.get("error") or {}
                code = error.get("code", "unknown")
                message = error.get("message", "no message")
                msg = f"Home Assistant rejected the request ({code}): {message}"
                raise HaExportError(msg)
            return reply.get("result")

    def statistics_during_period(
        self,
        statistic_ids: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        """One `recorder/statistics_during_period` call, hourly `change` in kWh.

        `change` is the per-interval delta (`sum` is the running cumulative total
        at the end of each period), so the caller needs no cumulative diffing.
        """
        result = self.command(
            {
                "type": "recorder/statistics_during_period",
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "statistic_ids": list(statistic_ids),
                "period": "hour",
                "units": {"energy": "kWh"},
                "types": ["change"],
            }
        )
        if result is None:
            return {}
        if not isinstance(result, dict):
            msg = (
                "Unexpected response shape from recorder/statistics_during_period: "
                f"expected an object keyed by statistic_id, got {type(result).__name__}."
            )
            raise HaExportError(msg)
        return result

    def available_statistic_ids(self) -> list[str]:
        """Energy statistic_ids this instance knows about, for error messages."""
        try:
            result = self.command({"type": "recorder/list_statistic_ids", "statistic_type": "sum"})
        except HaExportError:
            return []  # best-effort: this call only ever improves an error message
        if not isinstance(result, list):
            return []
        ids: list[str] = []
        for entry in result:
            if isinstance(entry, dict):
                statistic_id = entry.get("statistic_id")
                if isinstance(statistic_id, str):
                    ids.append(statistic_id)
        return sorted(ids)

    def close(self) -> None:
        self._ws.close()


# --------------------------------------------------------------------------
# Response parsing — pure, and where the format assumptions are pinned
# --------------------------------------------------------------------------


def epoch_ms_to_datetime(value: object) -> datetime:
    """Convert a statistics `start` to an aware UTC datetime.

    Home Assistant reports `start` and `end` as integer MILLISECONDS since the
    UNIX epoch, not as ISO strings. Older cores emitted ISO strings, so those are
    accepted too rather than failing on an instance that has not been updated.
    """
    if isinstance(value, bool):  # bool is an int subclass; never a timestamp
        msg = f"Invalid statistics timestamp: {value!r}"
        raise HaExportError(msg)
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / MILLISECONDS_PER_SECOND, tz=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            msg = f"Could not parse statistics timestamp {value!r}"
            raise HaExportError(msg) from exc
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    msg = f"Could not parse statistics timestamp {value!r}"
    raise HaExportError(msg)


@dataclass(frozen=True)
class Row:
    """One hourly output row, keyed by the START of the hour."""

    timestamp: datetime
    grid_import: float
    grid_export: float
    pv_production: float


def rows_from_response(
    response: dict[str, Any],
    import_sensor: str,
    export_sensor: str,
    pv_sensor: str | None,
) -> list[Row]:
    """Merge per-sensor statistics into hourly rows on a shared timestamp index.

    Statistic timestamps mark the START of their period, which is the same
    interval-starting convention `ingest.py` uses, so they are NOT shifted here.
    Sensors need not cover identical ranges: a missing hour for one sensor is
    zero energy for that sensor, which is what a gap in the recorder means.
    """
    series = {
        "grid_import": _series_for(response, import_sensor),
        "grid_export": _series_for(response, export_sensor),
        "pv_production": _series_for(response, pv_sensor) if pv_sensor else {},
    }
    timestamps = sorted({ts for values in series.values() for ts in values})
    return [
        Row(
            timestamp=ts,
            grid_import=series["grid_import"].get(ts, 0.0),
            grid_export=series["grid_export"].get(ts, 0.0),
            pv_production=series["pv_production"].get(ts, 0.0),
        )
        for ts in timestamps
    ]


def _series_for(response: dict[str, Any], statistic_id: str) -> dict[datetime, float]:
    entries = response.get(statistic_id, [])
    if not isinstance(entries, list):
        msg = (
            f"Unexpected statistics payload for '{statistic_id}': expected a list of "
            f"periods, got {type(entries).__name__}."
        )
        raise HaExportError(msg)

    values: dict[datetime, float] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            msg = f"Unexpected statistics entry for '{statistic_id}': {entry!r}"
            raise HaExportError(msg)
        if "start" not in entry:
            msg = f"Statistics entry for '{statistic_id}' has no 'start' field: {entry!r}"
            raise HaExportError(msg)
        timestamp = epoch_ms_to_datetime(entry["start"])
        change = entry.get("change")
        if change is None:
            continue  # a period the recorder has no delta for is not zero energy
        if isinstance(change, bool) or not isinstance(change, int | float):
            msg = (
                f"Statistics entry for '{statistic_id}' at {timestamp.isoformat()} has a "
                f"non-numeric 'change' value: {change!r}"
            )
            raise HaExportError(msg)
        # Later chunks win on overlap: month boundaries are half-open, but a
        # duplicate must not double-count if an instance returns one anyway.
        values[timestamp] = float(change)
    return values


def merge_rows(chunks: Sequence[Sequence[Row]]) -> list[Row]:
    """Concatenate per-month row lists, de-duplicating shared boundary hours."""
    merged: dict[datetime, Row] = {}
    for chunk in chunks:
        for row in chunk:
            merged[row.timestamp] = row
    return [merged[ts] for ts in sorted(merged)]


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------


def month_chunks(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Split [start, end) into calendar-month windows.

    Home Assistant can cap or time out on a full year in a single call, so the
    request is chunked. The windows are half-open and contiguous — each one ends
    exactly where the next begins — so no hour is requested twice and none is
    skipped. `merge_rows` de-duplicates anyway, in case an instance treats its
    own boundary as inclusive.
    """
    if end <= start:
        msg = (
            f"--end ({end.date().isoformat()}) must be after --start ({start.date().isoformat()})."
        )
        raise HaExportError(msg)

    chunks: list[tuple[datetime, datetime]] = []
    window_start = start
    while window_start < end:
        window_end = min(_next_month_start(window_start), end)
        chunks.append((window_start, window_end))
        window_start = window_end
    return chunks


def _next_month_start(moment: datetime) -> datetime:
    year, month = (
        (moment.year + 1, 1) if moment.month == _DECEMBER else (moment.year, moment.month + 1)
    )
    return moment.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


# --------------------------------------------------------------------------
# CSV output
# --------------------------------------------------------------------------


def write_csv(rows: Sequence[Row], path: Path) -> None:
    """Write the grid-centric schema `battery-worth analyze` accepts directly."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(OUTPUT_COLUMNS)
        writer.writerows(
            [
                row.timestamp.isoformat(),
                f"{row.grid_import:.6f}",
                f"{row.grid_export:.6f}",
                f"{row.pv_production:.6f}",
            ]
            for row in rows
        )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def resolve_token(flag_value: str | None, environ: dict[str, str] | None = None) -> str:
    """Take the token from --token or the environment, preferring the flag.

    Never logged, never echoed, never written to the output file. The env var is
    the documented path precisely because a token typed as a flag lands in shell
    history, which is a real leak.
    """
    env = os.environ if environ is None else environ
    token = flag_value or env.get(TOKEN_ENV_VAR, "")
    token = token.strip()
    if not token:
        msg = (
            "No Home Assistant access token supplied.\n"
            f"Preferred: export {TOKEN_ENV_VAR}='your-long-lived-token' and re-run.\n"
            "Alternative: pass --token, but note that this writes the token into your "
            "shell history."
        )
        raise HaExportError(msg)
    return token


def parse_day(value: str, *, end_of_day: bool = False) -> datetime:
    """Parse a YYYY-MM-DD boundary into an aware UTC datetime.

    `--end` is inclusive to the user ("give me December 31st"), so its window
    runs to the start of the following day.
    """
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        msg = f"Could not read a date from '{value}'. Use YYYY-MM-DD, e.g. 2024-01-01."
        raise HaExportError(msg) from exc
    moment = datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)
    return moment + timedelta(days=1) if end_of_day else moment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ha_export.py",
        description=(
            "Export Home Assistant long-term statistics to a battery-worth CSV. "
            "Runs against your own instance; nothing is sent anywhere else."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The access token stays on this machine: it is sent only to the --url you "
            "give, and is never logged or written to the output file.\n\n"
            f"Prefer the {TOKEN_ENV_VAR} environment variable over --token — a token "
            "passed as a command-line flag is saved in your shell history, which is a "
            "real leak.\n\n"
            "Example:\n"
            f"  export {TOKEN_ENV_VAR}='your-long-lived-token'\n"
            "  python scripts/ha_export.py --url ws://homeassistant.local:8123 \\\n"
            "      --import-sensor sensor.grid_import_energy \\\n"
            "      --export-sensor sensor.grid_export_energy \\\n"
            "      --pv-sensor sensor.solar_energy \\\n"
            "      --start 2024-01-01 --end 2024-12-31 -o my_energy.csv\n"
            "  battery-worth analyze my_energy.csv\n"
        ),
    )
    parser.add_argument(
        "--url",
        default="ws://homeassistant.local:8123",
        help="Base URL of your Home Assistant instance (default: %(default)s)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help=(
            f"Long-lived access token. Prefer the {TOKEN_ENV_VAR} environment variable: "
            "a token passed here is written to your shell history."
        ),
    )
    parser.add_argument(
        "--import-sensor", required=True, help="statistic_id for energy imported from the grid"
    )
    parser.add_argument(
        "--export-sensor", required=True, help="statistic_id for energy exported to the grid"
    )
    parser.add_argument(
        "--pv-sensor",
        default=None,
        help="statistic_id for PV production (optional, but strongly recommended)",
    )
    parser.add_argument("--start", required=True, help="First day to export, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Last day to export, inclusive, YYYY-MM-DD")
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("ha_energy.csv"), help="Output CSV path"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_DEFAULT_TIMEOUT_SECONDS,
        help="Socket timeout in seconds (default: %(default)s)",
    )
    return parser


def _fetch_all(
    client: HaClient,
    statistic_ids: list[str],
    chunks: Sequence[tuple[datetime, datetime]],
    args: argparse.Namespace,
) -> list[Row]:
    collected: list[list[Row]] = []
    for index, (window_start, window_end) in enumerate(chunks, start=1):
        label = window_start.strftime("%Y-%m")
        print(f"  [{index}/{len(chunks)}] {label} ...", end=" ", flush=True)
        response = client.statistics_during_period(statistic_ids, window_start, window_end)
        rows = rows_from_response(response, args.import_sensor, args.export_sensor, args.pv_sensor)
        collected.append(rows)
        print(f"{len(rows)} hour(s)")
    return merge_rows(collected)


def _check_sensors_exist(client: HaClient, statistic_ids: list[str], rows: list[Row]) -> None:
    """Explain an empty result: a wrong id looks exactly like an empty period."""
    if rows:
        return
    available = client.available_statistic_ids()
    unknown = [s for s in statistic_ids if s not in available] if available else []
    if unknown:
        energy_like = [s for s in available if "energy" in s or "power" in s]
        suggestions = energy_like or available
        listed = "\n".join(f"  {s}" for s in suggestions[:_MAX_SUGGESTIONS])
        remaining = len(suggestions) - _MAX_SUGGESTIONS
        more = f"\n  ... and {remaining} more" if remaining > 0 else ""
        msg = (
            f"These statistic_ids do not exist on this Home Assistant instance: "
            f"{', '.join(unknown)}\n\n"
            f"Statistics this instance does have:\n{listed}{more}"
        )
        raise HaExportError(msg)
    msg = (
        "Home Assistant returned no statistics for this period.\n"
        "The statistic_ids exist, so the likely causes are:\n"
        "  * the period predates the recorder's long-term statistics for these sensors\n"
        "  * the sensors were added, renamed or reset after the requested period\n"
        "Try a more recent --start / --end range."
    )
    raise HaExportError(msg)


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    token = resolve_token(args.token)
    start = parse_day(args.start)
    end = parse_day(args.end, end_of_day=True)
    chunks = month_chunks(start, end)

    statistic_ids = [args.import_sensor, args.export_sensor]
    if args.pv_sensor:
        statistic_ids.append(args.pv_sensor)

    print(f"Connecting to {args.url} ...")
    client = HaClient.connect(args.url, token, timeout=args.timeout)
    try:
        print(f"Fetching hourly statistics in {len(chunks)} monthly chunk(s):")
        rows = _fetch_all(client, statistic_ids, chunks, args)
        _check_sensors_exist(client, statistic_ids, rows)
    finally:
        client.close()

    write_csv(rows, args.output)
    _print_summary(rows, args.output)
    return 0


def _print_summary(rows: Sequence[Row], output: Path) -> None:
    print(f"\nWritten {output.resolve()}  ({len(rows)} hourly rows)")
    print(f"  period      : {rows[0].timestamp.isoformat()} -> {rows[-1].timestamp.isoformat()}")
    print(f"  grid import : {sum(r.grid_import for r in rows):.1f} kWh")
    print(f"  grid export : {sum(r.grid_export for r in rows):.1f} kWh")
    print(f"  PV          : {sum(r.pv_production for r in rows):.1f} kWh")
    print(f"\nNext step:\n  battery-worth analyze {output}")


def main() -> int:
    try:
        return run()
    except HaExportError as exc:
        # One "error:" for one error. Prefixing every line turns a single
        # multi-line explanation into what reads as several separate failures.
        # stdout is flushed first: it is block-buffered when piped while stderr is
        # not, so progress output otherwise lands *after* the error that stopped it.
        sys.stdout.flush()
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        sys.stdout.flush()
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
