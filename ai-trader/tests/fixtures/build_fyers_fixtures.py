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


def _datafeed_frame(
    message_num: int, data_type: int, values: list[int], topic: str = TOPIC
) -> bytes:
    """Assemble one datafeed frame: header, scrip payload, then i32 values."""
    topic_bytes = topic.encode()
    payload = bytearray()
    payload.append(data_type)
    payload.extend(struct.pack(">H", 3045))
    payload.append(len(topic_bytes))
    payload.extend(topic_bytes)
    payload.append(len(values))
    for value in values:
        payload.extend(struct.pack(">i", value))

    frame = bytearray()
    frame.extend((1).to_bytes(2, "big"))  # declared length (server-defined)
    frame.append(0x06)  # req_type = DATAFEED
    frame.extend(message_num.to_bytes(4, "big"))  # bytes 3..7
    frame.extend((1).to_bytes(2, "big"))  # scrip count
    frame.extend(payload)
    return bytes(frame)


def _values_for(schema: tuple[str, ...], source: dict[str, int]) -> list[int]:
    return [source[name] for name in schema if name in source]


def main() -> None:
    snapshot = _datafeed_frame(1, DataType.SNAPSHOT, _values_for(DATA_VAL_FIELDS, SNAPSHOT_VALUES))
    update = _datafeed_frame(2, DataType.UPDATE, _values_for(DATA_VAL_FIELDS, UPDATE_VALUES))
    lite = _datafeed_frame(3, DataType.LITE, _values_for(LITE_VAL_FIELDS, LITE_VALUE))

    for name, payload in (
        ("fyers_snapshot.bin", snapshot),
        ("fyers_update.bin", update),
        ("fyers_lite.bin", lite),
    ):
        (HERE / name).write_bytes(payload)
        print(f"wrote {name} ({len(payload)} bytes)")
    print(f"snapshot field count: {len(_values_for(DATA_VAL_FIELDS, SNAPSHOT_VALUES))}")
    print(f"update field count: {len(_values_for(DATA_VAL_FIELDS, UPDATE_VALUES))}")


if __name__ == "__main__":
    main()
