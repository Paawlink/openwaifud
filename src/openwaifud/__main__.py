"""CLI entry point for OpenWaifuD daemon."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from contextlib import suppress

from loguru import logger

from openwaifud.config import Config
from openwaifud.daemon import OpenWaifuDaemon


def setup_logging(log_level: str) -> None:
    """Configure loguru logging."""
    logger.remove()  # Remove default handler
    logger.add(
        sys.stderr,
        level=log_level.upper(),
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        colorize=True,
    )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="openwaifud",
        description="OpenWaifuD - Daemon syncing agent status to Tuya T5AI Board via BLE",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="HTTP server host (default: 127.0.0.1, env: OPENWAIFUD_HTTP_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="HTTP server port (default: 8765, env: OPENWAIFUD_HTTP_PORT)",
    )
    parser.add_argument(
        "--ble-address",
        default=None,
        help="BLE device address, e.g. AA:BB:CC:DD:EE:FF (env: OPENWAIFUD_BLE_ADDRESS)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO, env: OPENWAIFUD_LOG_LEVEL)",
    )
    parser.add_argument(
        "--no-realtime",
        action="store_true",
        default=False,
        help="Disable realtime file watching (env: OPENWAIFUD_REALTIME_ENABLED=false)",
    )
    parser.add_argument(
        "--realtime-debounce",
        type=int,
        default=None,
        help="Realtime debounce in ms (default: 300, env: OPENWAIFUD_REALTIME_DEBOUNCE_MS)",
    )
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> Config:
    """Build Config from CLI args (override env vars)."""
    config = Config()
    if args.host is not None:
        config.http_host = args.host
    if args.port is not None:
        config.http_port = args.port
    if args.ble_address is not None:
        config.ble_address = args.ble_address
    if args.log_level is not None:
        config.log_level = args.log_level
    if args.no_realtime:
        config.realtime_enabled = False
    if args.realtime_debounce is not None:
        config.realtime_debounce_ms = args.realtime_debounce
    return config


async def async_main(config: Config) -> None:
    """Async entry point."""
    daemon = OpenWaifuDaemon(config)

    # Register signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, daemon.request_shutdown)

    await daemon.run()


def main() -> None:
    """CLI main entry point."""
    args = parse_args()
    config = build_config(args)
    setup_logging(config.log_level)

    logger.info("OpenWaifuD starting...")

    with suppress(KeyboardInterrupt):
        asyncio.run(async_main(config))

    logger.info("Goodbye!")


if __name__ == "__main__":
    main()
