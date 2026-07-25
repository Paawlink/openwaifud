"""Configuration for OpenWaifuD daemon."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    """Daemon configuration with environment variable overrides."""

    # HTTP server
    http_host: str = field(default_factory=lambda: os.getenv("OPENWAIFUD_HTTP_HOST", "127.0.0.1"))
    http_port: int = field(default_factory=lambda: int(os.getenv("OPENWAIFUD_HTTP_PORT", "8765")))

    # BLE connection
    ble_address: str = field(default_factory=lambda: os.getenv("OPENWAIFUD_BLE_ADDRESS", ""))
    # 未配置地址时，按设备名扫描连接（与固件广播名一致）
    ble_device_name: str = field(default_factory=lambda: os.getenv("OPENWAIFUD_BLE_DEVICE_NAME", "OpenWaifu"))
    ble_scan_timeout: float = 5.0  # 与重连间隔匹配，避免单轮扫描拖长实际重试节奏
    ble_connect_timeout: float = 10.0
    ble_write_timeout: float = 5.0
    ble_reconnect_interval: float = 5.0

    # Local speech recognition
    asr_model: str = field(default_factory=lambda: os.getenv("OPENWAIFUD_ASR_MODEL", "small"))
    asr_language: str = field(default_factory=lambda: os.getenv("OPENWAIFUD_ASR_LANGUAGE", "zh"))

    # Logging
    log_level: str = field(default_factory=lambda: os.getenv("OPENWAIFUD_LOG_LEVEL", "INFO"))

    # State queue
    queue_max_size: int = 100

    # 会话生命周期（驱动固件端“情绪”状态机）
    # 会话完成/空闲后先停留展示“✓完成”多少秒，再从列表移除
    session_done_linger: float = 5.0
    # 会话清扫器轮询间隔（秒）
    session_sweep_interval: float = 1.0
