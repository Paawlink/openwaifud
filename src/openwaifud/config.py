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
    ble_scan_timeout: float = 10.0
    ble_connect_timeout: float = 10.0
    ble_write_timeout: float = 5.0
    ble_reconnect_initial_delay: float = 1.0
    ble_reconnect_max_delay: float = 30.0

    # Logging
    log_level: str = field(default_factory=lambda: os.getenv("OPENWAIFUD_LOG_LEVEL", "INFO"))

    # State queue
    queue_max_size: int = 100

    # Realtime file watching
    realtime_enabled: bool = field(
        default_factory=lambda: os.getenv("OPENWAIFUD_REALTIME_ENABLED", "true").lower() == "true"
    )
    realtime_debounce_ms: int = field(default_factory=lambda: int(os.getenv("OPENWAIFUD_REALTIME_DEBOUNCE_MS", "300")))
    realtime_idle_timeout: int = 60  # seconds before marking IDLE
    # 轮询兜底间隔（秒）。watchdog/FSEvents 对 SQLite WAL 写入不可靠，
    # 因此额外按此间隔轮询各解析器的活跃会话；<=0 关闭轮询。
    realtime_poll_interval: float = field(
        default_factory=lambda: float(os.getenv("OPENWAIFUD_REALTIME_POLL_INTERVAL", "2.0"))
    )

    # 会话生命周期（驱动固件端“情绪”状态机）
    # 会话完成/空闲后先停留展示“✓完成”多少秒，再从列表移除
    session_done_linger: float = 5.0
    # 待命会话多久无更新则自动移除（秒），默认与 realtime_idle_timeout 一致
    session_idle_timeout: float = 60.0
    # 会话清扫器轮询间隔（秒）
    session_sweep_interval: float = 1.0
