"""Tests for openwaifud.ble.protocol."""

import pytest

from openwaifud.ble.protocol import (
    MAX_CONTEXT_PAYLOAD,
    BLEProtocolError,
    decode_context_packet,
    decode_status_packet,
    encode_context_packet,
    encode_status_packet,
)
from openwaifud.models import AgentStatus


class TestStatusPacket:
    """Tests for status packet encode/decode."""

    def test_encode_returns_4_bytes(self):
        """encode_status_packet returns exactly 4 bytes."""
        data = encode_status_packet(AgentStatus.IDLE)
        assert len(data) == 4

    def test_roundtrip(self):
        """Encoding then decoding yields the same status."""
        original_status = AgentStatus.CODING
        encoded = encode_status_packet(original_status, error_code=42)
        version, decoded_status, error_code = decode_status_packet(encoded)
        assert decoded_status == original_status
        assert error_code == 42
        assert version == 0x01

    @pytest.mark.parametrize("status", list(AgentStatus))
    def test_all_statuses_encode_decode(self, status):
        """All AgentStatus values can be correctly encoded and decoded."""
        encoded = encode_status_packet(status)
        version, decoded_status, error_code = decode_status_packet(encoded)
        assert decoded_status == status
        assert error_code == 0

    def test_decode_too_short_raises_error(self):
        """decode_status_packet raises BLEProtocolError on short data."""
        with pytest.raises(BLEProtocolError, match="too short"):
            decode_status_packet(b"\x01\x00")

    def test_decode_invalid_status_code_raises_error(self):
        """decode_status_packet raises BLEProtocolError on unknown status code."""
        # Construct an invalid packet: version=1, status_code=0xFF, error=0, reserved=0
        invalid_data = b"\x01\xff\x00\x00"
        with pytest.raises(BLEProtocolError, match="Unknown status code"):
            decode_status_packet(invalid_data)


class TestContextPacket:
    """Tests for context packet encode/decode."""

    def test_encode_returns_tlv_format(self):
        """encode_context_packet returns correct TLV header format."""
        data = encode_context_packet("opencode", "session-1", "fix bug")
        # Header: 1B version + 1B msg_type + 2B length = 4 bytes
        assert len(data) >= 4
        # Check msg_type byte
        assert data[1] == 0x02

    def test_roundtrip(self):
        """Encoding then decoding yields the same context data."""
        plugin_type = "claudecode"
        session_id = "sess-abc"
        current_task = "Implement feature X"

        encoded = encode_context_packet(plugin_type, session_id, current_task)
        version, payload = decode_context_packet(encoded)

        assert version == 0x01
        assert payload["plugin"] == plugin_type
        assert payload["session_id"] == session_id
        assert payload["task"] == current_task

    def test_long_task_truncated(self):
        """Super long task string is truncated to fit MAX_CONTEXT_PAYLOAD."""
        long_task = "A" * 500  # Way longer than MAX_CONTEXT_PAYLOAD
        encoded = encode_context_packet("opencode", "s1", long_task)

        # Total payload (after header) should not exceed MAX_CONTEXT_PAYLOAD
        payload_length = len(encoded) - 4
        assert payload_length <= MAX_CONTEXT_PAYLOAD

        # Decode and verify truncation happened
        _, payload = decode_context_packet(encoded)
        assert payload["task"].endswith("...")
        assert len(payload["task"]) < len(long_task)

    def test_decode_too_short_raises_error(self):
        """decode_context_packet raises BLEProtocolError on short data."""
        with pytest.raises(BLEProtocolError, match="too short"):
            decode_context_packet(b"\x01")

    def test_decode_wrong_msg_type_raises_error(self):
        """decode_context_packet raises BLEProtocolError on wrong msg_type."""
        # Header with msg_type=0x01 (status) instead of 0x02 (context)
        invalid_data = b"\x01\x01\x00\x05hello"
        with pytest.raises(BLEProtocolError, match="Unexpected message type"):
            decode_context_packet(invalid_data)

    def test_decode_truncated_packet_raises_error(self):
        """decode_context_packet raises BLEProtocolError when packet is truncated."""
        # Header says payload_length=100 but only 3 bytes of payload
        import struct

        header = struct.pack("!BBH", 0x01, 0x02, 100)
        truncated = header + b"abc"
        with pytest.raises(BLEProtocolError, match="truncated"):
            decode_context_packet(truncated)
