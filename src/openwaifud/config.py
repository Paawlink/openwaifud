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
    ble_connect_timeout: float = 10.0
    ble_write_timeout: float = 5.0
    ble_reconnect_initial_delay: float = 1.0
    ble_reconnect_max_delay: float = 30.0

    # Logging
    log_level: str = field(default_factory=lambda: os.getenv("OPENWAIFUD_LOG_LEVEL", "INFO"))

    # State queue
    queue_max_size: int = 100
