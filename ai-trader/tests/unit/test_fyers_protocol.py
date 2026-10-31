"""FYERS V3 binary protocol: framing, parsing, and known-vector checks."""

from pathlib import Path

import pytest

from ai_trader.integrations.fyers.auth import extract_hsm_key
from ai_trader.integrations.fyers.protocol import (
    DATA_VAL_FIELDS,
    LITE_VAL_FIELDS,
    DataType,
    Envelope,
    Mode,
    ProtocolError,
    ReqType,
    ScripFeed,
    build_ack_message,
    build_auth_message,
    build_channel_bitmap_message,
    build_subscribe_message,
    build_unsubscribe_message,
    parse_datafeed,
    parse_envelope,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_auth_frame_layout_matches_reference() -> None:
    hsm = "5defeeb4dda186d360dcf3cfd35216088f49ef5d4fc1b09aaccdc374"
    frame = build_auth_message(hsm, Mode.FULL, "fyers-rs/0.1.0")
    assert len(frame) == 88
    assert frame[0:2] == (86).to_bytes(2, "big")
    assert frame[2] == ReqType.AUTH
    assert frame[3] == 4
    assert frame[4] == 1
    assert int.from_bytes(frame[5:7], "big") == len(hsm)
    offset = 7 + len(hsm)
    assert frame[offset] == 2
    assert frame[offset + 3] == Mode.FULL


def test_channel_bitmap_sets_requested_bit() -> None:
    frame = build_channel_bitmap_message(ReqType.FULL_MODE, 11)
    assert frame[2] == ReqType.FULL_MODE
    assert frame[3] == 2
    bitmap = int.from_bytes(frame[7:15], "big")
    assert bitmap == 1 << 11
    assert frame[15] == 2
    assert frame[18] == Mode.FULL_HEADER


def test_channel_bitmap_lite_marker() -> None:
    frame = build_channel_bitmap_message(ReqType.FULL_MODE, 11, Mode.LITE_HEADER)
    assert frame[18] == Mode.LITE_HEADER


def test_subscribe_frame_encodes_hsm_symbols() -> None:
    frame = build_subscribe_message(["sf|nse_cm|3045"], 11, "TOK", "src")
    assert frame[2] == ReqType.SUBSCRIBE
    assert frame[3] == 2
    assert frame[4] == 1
    field_len = int.from_bytes(frame[5:7], "big")
    assert field_len == 17
    assert int.from_bytes(frame[7:9], "big") == 1
    assert frame[9] == 14
    assert frame[10:24] == b"sf|nse_cm|3045"
    offset = 7 + field_len
    assert frame[offset] == 2
    assert frame[offset + 3] == 11


def test_unsubscribe_uses_its_own_request_type() -> None:
    frame = build_unsubscribe_message(["sf|nse_cm|3045"], 11, "TOK", "src")
    assert frame[2] == ReqType.UNSUBSCRIBE


def test_ack_frame_layout() -> None:
    frame = build_ack_message(0x12345678)
    assert len(frame) == 11
    assert frame[0:2] == (9).to_bytes(2, "big")
    assert frame[2] == ReqType.ACK
    assert frame[7:11] == (0x12345678).to_bytes(4, "big")


def test_parse_envelope_reads_status_and_ack_count() -> None:
    frame = bytearray((13).to_bytes(2, "big"))
    frame.append(0x01)
    frame.append(3)
    frame.extend(b"\x01\x00\x01K")
    frame.extend(b"\x02\x00\x02\x00\x02")
    frame.extend(b"\x03\x00\x00")
    envelope = parse_envelope(bytes(frame))
    assert isinstance(envelope, Envelope)
    assert envelope.is_ok is True
    assert envelope.ack_count() == 2


def test_parse_envelope_rejects_truncated_field() -> None:
    frame = bytes([0x00, 0x05, 0x04, 0x01, 0x01, 0x00, 0x64, 0x00])
    with pytest.raises(ProtocolError, match="exceeds frame"):
        parse_envelope(frame)


def test_parse_envelope_rejects_short_frame() -> None:
    with pytest.raises(ProtocolError, match="too short"):
        parse_envelope(b"\x00\x01")


def test_snapshot_fixture_decodes_all_documented_fields() -> None:
    message_num, feeds = parse_datafeed((FIXTURES / "fyers_snapshot.bin").read_bytes())
    assert message_num == 1
    assert len(feeds) == 1
    feed = feeds[0]
    assert feed.data_type == DataType.SNAPSHOT
    assert feed.topic_name == "sf|nse_cm|3045"
    assert feed.values == (
        150025,
        1_250_000,
        1_757_000_000,
        1_757_000_000,
        120,
        95,
        150020,
        150030,
        42,
        620_000,
        610_000,
        150010,
        0,
        148500,
        151000,
        165000,
        120000,
        134110,
        163910,
        149100,
        149010,
    )
    assert len(feed.values) == len(DATA_VAL_FIELDS)


def test_price_fields_are_unscaled_and_volumes_are_not() -> None:
    _, feeds = parse_datafeed((FIXTURES / "fyers_snapshot.bin").read_bytes())
    fields = feeds[0].as_mapping()
    assert fields["ltp"] == pytest.approx(1500.25)
    assert fields["prev_close_price"] == pytest.approx(1490.10)
    assert fields["open_price"] == pytest.approx(1491.00)
    assert fields["high_price"] == pytest.approx(1510.00)
    assert fields["low_price"] == pytest.approx(1485.00)
    assert fields["bid_price"] == pytest.approx(1500.20)
    assert fields["ask_price"] == pytest.approx(1500.30)
    # Volume and times are not price-scaled.
    assert fields["vol_traded_today"] == 1_250_000
    assert fields["exch_feed_time"] == 1_757_000_000


def test_update_fixture_carries_topic_id_without_name() -> None:
    _, snapshot_feeds = parse_datafeed((FIXTURES / "fyers_snapshot.bin").read_bytes())
    _, update_feeds = parse_datafeed((FIXTURES / "fyers_update.bin").read_bytes())
    snapshot, update = snapshot_feeds[0], update_feeds[0]
    assert snapshot.topic_name == "sf|nse_cm|3045"
    # Live update frames omit the topic name; the numeric id links them to the
    # snapshot that introduced it.
    assert update.topic_name is None
    assert update.topic_id == snapshot.topic_id


def test_update_fixture_decodes_partial_field_set() -> None:
    message_num, feeds = parse_datafeed((FIXTURES / "fyers_update.bin").read_bytes())
    assert message_num == 2
    assert feeds[0].data_type == DataType.UPDATE
    fields = feeds[0].as_mapping()
    assert fields["ltp"] == pytest.approx(1501.80)
    assert "open_price" not in fields


def test_multi_snapshot_frame_resolves_every_scrip() -> None:
    message_num, feeds = parse_datafeed((FIXTURES / "fyers_multi_snapshot.bin").read_bytes())
    assert message_num == 4
    assert len(feeds) == 2
    assert [feed.topic_name for feed in feeds] == ["sf|nse_cm|3045", "sf|nse_cm|1594"]
    assert all(feed.data_type == DataType.SNAPSHOT for feed in feeds)
    assert {feed.topic_id for feed in feeds} == {1, 2}
    assert feeds[0].as_mapping()["ltp"] == pytest.approx(1500.25)
    assert feeds[1].as_mapping()["ltp"] == pytest.approx(1500.25)


def test_lite_fixture_uses_lite_schema() -> None:
    _, feeds = parse_datafeed((FIXTURES / "fyers_lite.bin").read_bytes())
    feed = feeds[0]
    assert feed.data_type == DataType.LITE
    assert feed.schema() == LITE_VAL_FIELDS
    assert feed.as_mapping() == {"ltp": pytest.approx(1501.80)}


def test_index_topic_uses_index_schema() -> None:
    feed = ScripFeed(DataType.SNAPSHOT, 0, "if|nse_cm|26000", (1, 2, 3, 4, 5, 6))
    assert feed.schema()[0] == "ltp"
    assert len(feed.schema()) == 6


def test_parse_datafeed_rejects_short_frame() -> None:
    with pytest.raises(ProtocolError):
        parse_datafeed(b"\x00\x01\x06")


def test_parse_datafeed_rejects_truncated_scrip() -> None:
    with pytest.raises(ProtocolError, match="truncated header"):
        parse_datafeed(
            (11).to_bytes(2, "big") + b"\x06" + (1).to_bytes(4, "big") + (1).to_bytes(2, "big")
        )


def test_extract_hsm_key_from_jwt_with_app_id_prefix() -> None:
    import base64
    import json

    def seg(data: dict[str, str]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    token = f"{seg({'alg': 'none'})}.{seg({'sub': 'access_token', 'hsm_key': 'deadbeef'})}.sig"
    assert extract_hsm_key(f"APPID-100:{token}") == "deadbeef"
    assert extract_hsm_key(token) == "deadbeef"


def test_extract_hsm_key_rejects_token_without_claim() -> None:
    import base64
    import json

    def seg(data: dict[str, str]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    token = f"{seg({'alg': 'none'})}.{seg({'sub': 'auth_code'})}.sig"
    with pytest.raises(ValueError, match="no hsm_key"):
        extract_hsm_key(token)
