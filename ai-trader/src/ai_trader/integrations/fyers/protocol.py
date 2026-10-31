"""FYERS V3 Data WebSocket binary wire protocol.

The data socket at ``wss://socket.fyers.in/hsm/v1-5/prod`` speaks a framed binary
protocol, not JSON. Every request and control response uses one envelope::

    [2B BE u16: data_len] [1B: req_type] [1B: field_count]
    { [1B: field_id] [2B BE u16: field_len] [field_len bytes] } * field_count

Datafeed frames (``req_type = 0x06``) carry a message number and scrip count, then
one payload per scrip. Price fields are big-endian ``i32`` scaled by 100.

FYERS does not publish a packet-structure specification; this module follows the
field ordering and byte layout of the reference ``fyers_apiv3`` SDK's ``map.json``
(``data_val``, ``lite_val``, ``index_val``) and the HSM message builders. Treat the
constants below as protocol-coupled: re-verify them if the upstream schema changes.
"""

from dataclasses import dataclass
from enum import IntEnum

PROTOCOL_VERSION = "fyers-data-ws-v1-5"


class ReqType(IntEnum):
    AUTH = 0x01
    ACK = 0x03
    SUBSCRIBE = 0x04
    UNSUBSCRIBE = 0x05
    DATAFEED = 0x06
    CHANNEL_PAUSE = 0x07
    CHANNEL_RESUME = 0x08
    FULL_MODE = 0x0C
    CHANNEL_BUFFER = 0x0D


class DataType(IntEnum):
    SNAPSHOT = 0x53
    UPDATE = 0x55
    LITE = 0x4C
    FULL_HEADER = 0x46


class Mode(IntEnum):
    """Channel mode marker. Auth uses the ASCII byte; mode-set uses the header byte."""

    FULL = ord("P")
    LITE = ord("L")
    FULL_HEADER = 0x46
    LITE_HEADER = 0x4C


class ProtocolError(ValueError):
    """The frame does not conform to the documented FYERS data-socket layout."""


DATA_VAL_FIELDS: tuple[str, ...] = (
    "ltp",
    "vol_traded_today",
    "last_traded_time",
    "exch_feed_time",
    "bid_size",
    "ask_size",
    "bid_price",
    "ask_price",
    "last_traded_qty",
    "tot_buy_qty",
    "tot_sell_qty",
    "avg_trade_price",
    "open_interest",
    "low_price",
    "high_price",
    "year_high",
    "year_low",
    "lower_circuit",
    "upper_circuit",
    "open_price",
    "prev_close_price",
)

LITE_VAL_FIELDS: tuple[str, ...] = ("ltp",)

INDEX_VAL_FIELDS: tuple[str, ...] = (
    "ltp",
    "prev_close_price",
    "exch_feed_time",
    "high_price",
    "low_price",
    "open_price",
)

PRICE_SCALED_FIELDS: frozenset[str] = frozenset(
    {
        "ltp",
        "bid_price",
        "ask_price",
        "avg_trade_price",
        "low_price",
        "high_price",
        "year_high",
        "year_low",
        "lower_circuit",
        "upper_circuit",
        "open_price",
        "prev_close_price",
    }
)

PRICE_SCALE = 100

# INT32_MIN marks a missing value on the wire. Values stay positional so the
# field mapping never shifts; as_mapping() drops these instead.
NULL_VALUE = -(2**31)


def _push_field(buffer: bytearray, field_id: int, value: bytes) -> None:
    buffer.append(field_id)
    buffer.extend(len(value).to_bytes(2, "big"))
    buffer.extend(value)


def _frame(req_type: int, fields: list[tuple[int, bytes]], declared_len: int) -> bytes:
    """Assemble an envelope. ``declared_len`` follows the SDK's fixed 18-byte overhead."""
    buffer = bytearray(declared_len.to_bytes(2, "big"))
    buffer.append(req_type)
    buffer.append(len(fields))
    for field_id, value in fields:
        _push_field(buffer, field_id, value)
    return bytes(buffer)


def build_auth_message(hsm_key: str, channel_mode: int, source: str) -> bytes:
    total = 18 + len(hsm_key) + len(source)
    return _frame(
        ReqType.AUTH,
        [(1, hsm_key.encode()), (2, bytes([channel_mode])), (3, b"\x01"), (4, source.encode())],
        total - 2,
    )


def build_channel_bitmap_message(
    req_type: int, channel: int, mode_marker: int = Mode.FULL_HEADER
) -> bytes:
    bitmap = 1 << channel if 1 <= channel < 64 else 0
    fields: list[tuple[int, bytes]] = [(1, bitmap.to_bytes(8, "big"))]
    if req_type == ReqType.FULL_MODE:
        fields.append((2, bytes([mode_marker])))
    return _frame(int(req_type), fields, 0)


def build_subscribe_message(
    hsm_symbols: list[str], channel: int, access_token: str, source: str
) -> bytes:
    scrip_data = bytearray(len(hsm_symbols).to_bytes(2, "big"))
    for symbol in hsm_symbols:
        encoded = symbol.encode()
        scrip_data.append(len(encoded))
        scrip_data.extend(encoded)
    declared = 18 + len(scrip_data) + len(access_token) + len(source)
    return _frame(ReqType.SUBSCRIBE, [(1, bytes(scrip_data)), (2, bytes([channel]))], declared)


def build_unsubscribe_message(
    hsm_symbols: list[str], channel: int, access_token: str, source: str
) -> bytes:
    scrip_data = bytearray(len(hsm_symbols).to_bytes(2, "big"))
    for symbol in hsm_symbols:
        encoded = symbol.encode()
        scrip_data.append(len(encoded))
        scrip_data.extend(encoded)
    declared = 18 + len(scrip_data) + len(access_token) + len(source)
    return _frame(ReqType.UNSUBSCRIBE, [(1, bytes(scrip_data)), (2, bytes([channel]))], declared)


def build_ack_message(message_num: int) -> bytes:
    """Echo the latest datafeed sequence number so the server keeps sending updates."""
    return _frame(ReqType.ACK, [(1, message_num.to_bytes(4, "big"))], 9)


@dataclass(frozen=True)
class EnvelopeField:
    id: int
    value: bytes


@dataclass(frozen=True)
class Envelope:
    req_type: int
    fields: tuple[EnvelopeField, ...]

    def field(self, field_id: int) -> bytes | None:
        for field in self.fields:
            if field.id == field_id:
                return field.value
        return None

    def status_text(self) -> str | None:
        raw = self.field(1)
        if raw is None:
            return None
        try:
            return raw.decode()
        except UnicodeDecodeError:
            return None

    @property
    def is_ok(self) -> bool:
        return self.status_text() == "K"

    def ack_count(self) -> int | None:
        raw = self.field(2)
        if raw is None or len(raw) not in (2, 4):
            return None
        return int.from_bytes(raw, "big")


def parse_envelope(data: bytes) -> Envelope:
    if len(data) < 4:
        raise ProtocolError(f"frame too short: {len(data)} bytes")
    req_type = data[2]
    field_count = data[3]
    offset = 4
    fields: list[EnvelopeField] = []
    for index in range(field_count):
        if offset + 3 > len(data):
            raise ProtocolError(f"truncated at field {index} header")
        field_id = data[offset]
        length = int.from_bytes(data[offset + 1 : offset + 3], "big")
        offset += 3
        if offset + length > len(data):
            raise ProtocolError(f"field {index} length {length} exceeds frame")
        fields.append(EnvelopeField(field_id, data[offset : offset + length]))
        offset += length
    return Envelope(req_type, tuple(fields))


@dataclass(frozen=True)
class ScripFeed:
    data_type: int
    topic_id: int
    topic_name: str | None
    values: tuple[int, ...]

    def schema(self) -> tuple[str, ...]:
        if self.data_type == DataType.LITE:
            return LITE_VAL_FIELDS
        prefix = (self.topic_name or "").split("|", 1)[0]
        if prefix == "if":
            return INDEX_VAL_FIELDS
        return DATA_VAL_FIELDS

    def as_mapping(self) -> dict[str, int | float]:
        """Map the scrip's i32 values onto named fields, un-scaling prices."""
        mapping: dict[str, int | float] = {}
        for index, raw in enumerate(self.values):
            if index >= len(self.schema()) or raw == NULL_VALUE:
                continue
            name = self.schema()[index]
            if name in PRICE_SCALED_FIELDS:
                mapping[name] = raw / PRICE_SCALE
            else:
                mapping[name] = raw
        return mapping


def parse_datafeed(data: bytes) -> tuple[int, tuple[ScripFeed, ...]]:
    """Return ``(message_num, feeds)`` for a datafeed frame.

    Snapshot frames (``0x53``) carry the HSM topic name; update and lite frames
    (``0x55``/``0x4C``) carry only the numeric topic id, which the caller resolves
    against the names seen on snapshots. Topic ids are little-endian, matching the
    reference SDK. INT32_MIN (``-2147483648``) marks a missing value and is dropped.
    """
    if len(data) < 9:
        raise ProtocolError(f"datafeed frame under 9 bytes: {len(data)}")
    message_num = int.from_bytes(data[3:7], "big")
    scrip_count = int.from_bytes(data[7:9], "big")
    offset = 9
    feeds: list[ScripFeed] = []
    for index in range(scrip_count):
        if offset + 3 > len(data):
            raise ProtocolError(f"datafeed scrip {index}: truncated header")
        data_type = data[offset]
        offset += 1
        topic_id = int.from_bytes(data[offset : offset + 2], "little")
        offset += 2
        topic_name: str | None = None
        if data_type == DataType.SNAPSHOT:
            if offset + 1 > len(data):
                raise ProtocolError(f"datafeed scrip {index}: truncated topic name length")
            name_len = data[offset]
            offset += 1
            if offset + name_len + 1 > len(data):
                raise ProtocolError(f"datafeed scrip {index}: truncated topic name")
            try:
                topic_name = data[offset : offset + name_len].decode()
            except UnicodeDecodeError as exc:
                raise ProtocolError(f"datafeed scrip {index}: non-UTF8 topic name") from exc
            offset += name_len
        if offset + 1 > len(data):
            raise ProtocolError(f"datafeed scrip {index}: truncated field count")
        field_count = data[offset]
        offset += 1
        if offset + field_count * 4 > len(data):
            raise ProtocolError(
                f"datafeed scrip {index}: declared {field_count} fields exceed frame"
            )
        values = tuple(
            int.from_bytes(data[offset + i * 4 : offset + i * 4 + 4], "big", signed=True)
            for i in range(field_count)
        )
        offset += field_count * 4
        if data_type == DataType.SNAPSHOT:
            # Snapshots append a trailer after the values: a multiplier, a
            # precision, two reserved bytes, and three labelled strings
            # (exchange, exchange token, symbol). The values are not needed
            # downstream, but the bytes must be skipped so the next scrip in
            # a multi-symbol frame starts at the right offset.
            offset = _skip_snapshot_trailer(data, offset, index)
        feeds.append(ScripFeed(data_type, topic_id, topic_name, values))
    return message_num, tuple(feeds)


def _skip_snapshot_trailer(data: bytes, offset: int, index: int) -> int:
    """Skip the post-values trailer of a snapshot scrip. Returns the new offset."""
    if offset + 5 > len(data):
        raise ProtocolError(f"datafeed scrip {index}: truncated snapshot trailer")
    offset += 5  # multiplier u16 + precision u8 + 2 reserved bytes
    for _ in range(3):
        if offset + 1 > len(data):
            raise ProtocolError(f"datafeed scrip {index}: truncated snapshot label")
        string_len = data[offset]
        offset += 1
        if offset + string_len > len(data):
            raise ProtocolError(f"datafeed scrip {index}: truncated snapshot label")
        offset += string_len
    return offset


def ticker_from_topic(topic: str) -> str:
    """The wire carries the HSM form (``sf|nse_cm|3045``); callers map it back."""
    return topic
