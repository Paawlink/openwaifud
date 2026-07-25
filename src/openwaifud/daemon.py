"""OpenWaifuD main daemon - orchestrates all components."""

from __future__ import annotations

import asyncio

from loguru import logger

from openwaifud.api.server import HTTPServer
from openwaifud.ble.client import BLEClient
from openwaifud.chat import ChatService
from openwaifud.config import Config
from openwaifud.models import GlobalEventKind
from openwaifud.state.manager import StateManager


class OpenWaifuDaemon:
    """Main daemon class that coordinates HTTP server, BLE client, and state manager."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._state_manager = StateManager(
            queue_max_size=config.queue_max_size,
            done_linger=config.session_done_linger,
            sweep_interval=config.session_sweep_interval,
        )
        self._ble_client = BLEClient(config)
        self._chat_service = ChatService()
        self._http_server = HTTPServer(
            state_manager=self._state_manager,
            host=config.http_host,
            port=config.http_port,
            ble_client=self._ble_client,
            chat_service=self._chat_service,
        )
        self._shutdown_event = asyncio.Event()

    async def run(self) -> None:
        """Run the daemon until shutdown signal."""
        try:
            await self._start()
            await self._shutdown_event.wait()
        finally:
            await self._stop()

    def request_shutdown(self) -> None:
        """Request daemon shutdown (called from signal handler)."""
        logger.info("Shutdown requested")
        self._shutdown_event.set()

    async def _start(self) -> None:
        """Initialize and start all components."""
        logger.info("Starting OpenWaifuD daemon...")

        # Wire BLE callback into state manager
        self._state_manager.set_ble_callback(self._ble_client.handle_message)
        # BLE（重）连接后由状态管理器重新同步会话看板
        self._ble_client.set_on_connected(self._state_manager.resync_ble)
        # 设备侧“新建会话”请求 -> 定向登记给最新存活的 OpenCode 实例；
        # 没有存活实例时拒绝执行，并用全局 error 事件向设备反馈。
        self._ble_client.set_on_session_create(self._on_device_session_create)

        # Start state consumer (background queue processing)
        await self._state_manager.start_consumer()

        # Start BLE client (connects if address configured)
        await self._ble_client.start()

        # Update BLE connection status in state manager
        self._state_manager.ble_connected = self._ble_client.connected

        # Start HTTP server
        await self._http_server.start()

        logger.info(f"OpenWaifuD running - HTTP: http://{self._config.http_host}:{self._config.http_port}")

    def _on_device_session_create(self, prompt: str) -> None:
        """BLE 侧“新建会话”通知处理：无存活 OpenCode 实例时拒绝并反馈。"""
        pending = self._state_manager.request_session_create(prompt)
        if pending is None:
            # bleak 通知回调运行在事件循环内，可直接调度异步反馈
            try:
                asyncio.get_running_loop().create_task(
                    self._state_manager.emit_global_event(GlobalEventKind.ERROR, "无OpenCode实例")
                )
            except RuntimeError:
                logger.warning("No running loop to report refused session create")

    async def _stop(self) -> None:
        """Stop all components gracefully."""
        logger.info("Stopping OpenWaifuD daemon...")

        # 1. Stop HTTP server (no new requests)
        await self._http_server.stop()

        # 2. Stop state consumer (drain queue with timeout)
        await self._state_manager.stop_consumer()

        # 3. Disconnect BLE
        await self._ble_client.stop()

        logger.info("OpenWaifuD daemon stopped")
