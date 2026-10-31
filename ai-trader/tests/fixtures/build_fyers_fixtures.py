"""Build serialized FYERS data-socket fixtures for protocol and normalization tests.

Run: uv run python tests/fixtures/build_fyers_fixtures.py
"""

import struct
from pathlib import Path

from ai_trader.integrations.fyers.protocol import (
    DATA_VAL_FIELDS,
    LITE_VAL_FIELDS,
    DataType,
)

HERE = Path(__file__).parent
TOPIC = "sf|nse_cm|3045"

# Realistic NSE equity values: ltp 1500.25, prev close 1490.10.
SNAPSHOT_VALUES: dict[str, int] = {
    "ltp": 150025,
    "vol_traded_today": 1_250_000,
    "last_traded_time": 1_757_000_000,
    "exch_feed_time": 1_757_000_000,
    "bid_size": 120,
    "ask_size": 95,
    "bid_price": 150020,
    "ask_price": 150030,
    "last_traded_qty": 42,
    "tot_buy_qty": 620_000,
    "tot_sell_qty": 610_000,
    "avg_trade_price": 150010,
    "open_interest": 0,
    "low_price": 148500,
    "high_price": 151000,
    "year_high": 165000,
    "year_low": 120000,
    "lower_circuit": 134110,
    "upper_circuit": 163910,
    "open_price": 149100,
    "prev_close_price": 149010,
}

# A live update carries only the changed fields (prefix of the schema).
UPDATE_VALUES: dict[str, int] = {
    "ltp": 150180,
    "vol_traded_today": 1_260_000,
    "last_traded_time": 1_757_000_600,
    "exch_feed_time": 1_757_000_600,
    "bid_size": 60,
    "ask_size": 55,
    "bid_price": 150175,
    "ask_price": 150185,
}

LITE_VALUE = {"ltp": 150180}


def _scrip_payload(
    data_type: int,
    values: list[int],
    topic: str = TOPIC,
    topic_id: int = 1,
    with_name: bool = True,
) -> bytearray:
    """Assemble one scrip payload: type, topic id, optional name, then i32 values."""
    payload = bytearray()
    payload.append(data_type)
    payload.extend(struct.pack("<H", topic_id))
    if with_name:
        topic_bytes = topic.encode()
        payload.append(len(topic_bytes))
        payload.extend(topic_bytes)
    payload.append(len(values))
    for value in values:
        payload.extend(struct.pack(">i", value))
    if data_type == DataType.SNAPSHOT:
        # Live snapshots append a multiplier, a precision, two reserved bytes,
        # and three labelled strings (exchange, exchange token, symbol).
        payload.extend(struct.pack(">H", 1))
        payload.append(2)
        payload.extend(b"\x01\x02")
        for label in ("NSE", str(topic_id), topic):
            encoded = label.encode()
            payload.append(len(encoded))
            payload.extend(encoded)
    return payload


def _datafeed_frame(
    message_num: int,
    data_type: int,
    values: list[int],
    topic: str = TOPIC,
    topic_id: int = 1,
    with_name: bool = True,
) -> bytes:
    """Assemble one datafeed frame: header, scrip payload, then i32 values.

    Only snapshot frames carry the topic name; update and lite frames carry the
    numeric topic id (little-endian), exactly as the live server sends them.
    """
    payload = _scrip_payload(data_type, values, topic, topic_id, with_name)

    frame = bytearray()
    frame.extend((1).to_bytes(2, "big"))  # declared length (server-defined)
    frame.append(0x06)  # req_type = DATAFEED
    frame.extend(message_num.to_bytes(4, "big"))  # bytes 3..7
    frame.extend((1).to_bytes(2, "big"))  # scrip count
    frame.extend(payload)
    return bytes(frame)


def _multi_snapshot_frame() -> bytes:
    """Two snapshot scrips in one frame, as the live server sends on subscribe."""
    payload = _scrip_payload(
        DataType.SNAPSHOT, _values_for(DATA_VAL_FIELDS, SNAPSHOT_VALUES),
        TOPIC, 1, True,
    )
    payload.extend(
        _scrip_payload(
            DataType.SNAPSHOT, _values_for(DATA_VAL_FIELDS, SNAPSHOT_VALUES),
            "sf|nse_cm|1594", 2, True,
        )
    )
    frame = bytearray()
    frame.extend((1).to_bytes(2, "big"))
    frame.append(0x06)
    frame.extend((4).to_bytes(4, "big"))
    frame.extend((2).to_bytes(2, "big"))
    frame.extend(payload)
    return bytes(frame)


def _values_for(schema: tuple[str, ...], source: dict[str, int]) -> list[int]:
    return [source[name] for name in schema if name in source]


def main() -> None:
    snapshot = _datafeed_frame(1, DataType.SNAPSHOT, _values_for(DATA_VAL_FIELDS, SNAPSHOT_VALUES))
    update = _datafeed_frame(
        2, DataType.UPDATE, _values_for(DATA_VAL_FIELDS, UPDATE_VALUES), with_name=False
    )
    lite = _datafeed_frame(
        3, DataType.LITE, _values_for(LITE_VAL_FIELDS, LITE_VALUE), with_name=False
    )
    multi = _multi_snapshot_frame()

    for name, payload in (
        ("fyers_snapshot.bin", snapshot),
        ("fyers_update.bin", update),
        ("fyers_lite.bin", lite),
        ("fyers_multi_snapshot.bin", multi),
    ):
        (HERE / name).write_bytes(payload)
        print(f"wrote {name} ({len(payload)} bytes)")
    print(f"snapshot field count: {len(_values_for(DATA_VAL_FIELDS, SNAPSHOT_VALUES))}")
    print(f"update field count: {len(_values_for(DATA_VAL_FIELDS, UPDATE_VALUES))}")


if __name__ == "__main__":
    main()
