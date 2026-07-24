"""BLE GATT protocol encoding/decoding for OpenWaifuD."""

from __future__ import annotations

import json
import struct

from openwaifud.models import AgentStatus

# ---------------------------------------------------------------------------
# GATT Service / Characteristic UUIDs
# ---------------------------------------------------------------------------

SERVICE_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
CHAR_STATUS_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"  # 状态特征 (Write)
CHAR_CONTEXT_UUID = "0000ff03-0000-1000-8000-00805f9b34fb"  # 上下文特征 (Write)

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

PROTOCOL_VERSION: int = 0x01
MSG_TYPE_STATUS: int = 0x01
MSG_TYPE_CONTEXT: int = 0x02

# AgentStatus -> byte code 映射
STATUS_CODE_MAP: dict[AgentStatus, int] = {
    AgentStatus.IDLE: 0x00,
    AgentStatus.THINKING: 0x01,
    AgentStatus.CODING: 0x02,
    AgentStatus.TESTING: 0x03,
    AgentStatus.ERROR: 0x04,
}

MAX_CONTEXT_PAYLOAD: int = 240  # BLE MTU 安全限制


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BLEProtocolError(Exception):
    """BLE protocol encoding/decoding error."""


class BLEConnectionError(Exception):
    """BLE connection failure."""


class BLEWriteError(Exception):
    """BLE characteristic write failure."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _code_to_status(code: int) -> AgentStatus:
    """将字节码映射回 AgentStatus。"""
    reverse_map = {v: k for k, v in STATUS_CODE_MAP.items()}
    if code not in reverse_map:
        raise BLEProtocolError(f"Unknown status code: 0x{code:02x}")
    return reverse_map[code]


# ---------------------------------------------------------------------------
# Status packet: fixed 4 bytes
# [1B: protocol_version] [1B: status_code] [1B: error_code] [1B: reserved=0x00]
# ---------------------------------------------------------------------------


def encode_status_packet(status: AgentStatus, error_code: int = 0) -> bytes:
    """编码状态包为 4 字节。"""
    status_code = STATUS_CODE_MAP[status]
    return struct.pack("!BBBB", PROTOCOL_VERSION, status_code, error_code & 0xFF, 0x00)


def decode_status_packet(data: bytes) -> tuple[int, AgentStatus, int]:
    """解码 4 字节状态包，返回 (version, status, error_code)。"""
    if len(data) < 4:
        raise BLEProtocolError(f"Status packet too short: {len(data)} bytes, expected 4")
    version, status_code, error_code, _ = struct.unpack("!BBBB", data[:4])
    status = _code_to_status(status_code)
    return version, status, error_code


# ---------------------------------------------------------------------------
# Context packet: variable length, TLV format (max 240 bytes payload)
# [1B: protocol_version] [1B: msg_type=0x02] [2B: payload_length (big-endian)] [payload: UTF-8 JSON]
# ---------------------------------------------------------------------------


def encode_context_packet(plugin_type: str, session_id: str, current_task: str = "") -> bytes:
    """编码上下文包。如果 payload 超过 240 字节，truncate task 字段。"""
    payload_dict = {"plugin": plugin_type, "session_id": session_id, "task": current_task}
    payload_bytes = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")

    # 如果超长，截断 task
    if len(payload_bytes) > MAX_CONTEXT_PAYLOAD:
        payload_dict["task"] = current_task[:50] + "..."
        payload_bytes = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")

    if len(payload_bytes) > MAX_CONTEXT_PAYLOAD:
        raise BLEProtocolError(f"Context payload too large: {len(payload_bytes)} bytes")

    header = struct.pack("!BBH", PROTOCOL_VERSION, MSG_TYPE_CONTEXT, len(payload_bytes))
    return header + payload_bytes


def decode_context_packet(data: bytes) -> tuple[int, dict]:
    """解码上下文包，返回 (version, payload_dict)。"""
    if len(data) < 4:
        raise BLEProtocolError(f"Context packet too short: {len(data)} bytes")
    version, msg_type, payload_length = struct.unpack("!BBH", data[:4])
    if msg_type != MSG_TYPE_CONTEXT:
        raise BLEProtocolError(f"Unexpected message type: 0x{msg_type:02x}")
    if len(data) < 4 + payload_length:
        raise BLEProtocolError(f"Packet truncated: expected {4 + payload_length}, got {len(data)}")
    payload_bytes = data[4 : 4 + payload_length]
    payload_dict: dict = json.loads(payload_bytes.decode("utf-8"))
    return version, payload_dict
