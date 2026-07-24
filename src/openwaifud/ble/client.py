"""BLE client for connecting to Tuya T5AI Board."""

from __future__ import annotations

import asyncio
from typing import Any

from bleak import BleakClient, BleakError
from loguru import logger

from openwaifud.ble.protocol import (
    CHAR_CONTEXT_UUID,
    CHAR_STATUS_UUID,
    encode_context_packet,
    encode_status_packet,
)
from openwaifud.config import Config
from openwaifud.models import AgentStatus, ConversationContext, StatusUpdate


class BLEClient:
    """BLE Central client for communicating with T5AI Board.

    Features:
    - Auto-reconnect with exponential backoff
    - asyncio.Lock for serialized writes
    - Graceful degradation (write failures logged, not raised)
    - Disconnect detection and background reconnection
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._client: BleakClient | None = None
        self._connected: bool = False
        self._write_lock: asyncio.Lock = asyncio.Lock()
        self._reconnect_task: asyncio.Task[None] | None = None
        self._should_run: bool = False
        self._current_backoff: float = config.ble_reconnect_initial_delay

    @property
    def connected(self) -> bool:
        return self._connected and self._client is not None and self._client.is_connected

    async def start(self) -> None:
        """Start BLE client and attempt initial connection."""
        if not self._config.ble_address:
            logger.warning("No BLE device address configured, BLE disabled")
            return
        self._should_run = True
        await self._connect()

    async def stop(self) -> None:
        """Stop BLE client and disconnect."""
        self._should_run = False
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        await self._disconnect()

    async def handle_message(self, message: dict[str, Any]) -> None:
        """Handle a message from StateManager queue (BLE callback).

        This is registered as the BLE callback in StateManager.
        Exceptions are caught internally - never raises to caller.
        """
        if not self.connected:
            logger.debug("BLE not connected, skipping message")
            return

        try:
            msg_type = message.get("type")
            if msg_type == "status":
                update: StatusUpdate = message["data"]
                await self._write_status(
                    update.status, error_code=1 if update.status == AgentStatus.ERROR else 0
                )
            elif msg_type == "context":
                context: ConversationContext = message["data"]
                await self._write_context(context)
            else:
                logger.warning(f"Unknown message type: {msg_type}")
        except Exception as e:
            logger.error(f"BLE message handling error: {e}")

    async def _connect(self) -> bool:
        """Attempt to connect to BLE device."""
        if not self._config.ble_address:
            return False

        try:
            logger.info(f"Connecting to BLE device: {self._config.ble_address}")
            self._client = BleakClient(
                self._config.ble_address,
                disconnected_callback=self._on_disconnect,
            )
            await asyncio.wait_for(
                self._client.connect(),
                timeout=self._config.ble_connect_timeout,
            )
            self._connected = True
            self._current_backoff = self._config.ble_reconnect_initial_delay
            logger.info(f"BLE connected to {self._config.ble_address}")
            return True
        except TimeoutError:
            logger.warning(f"BLE connection timeout ({self._config.ble_connect_timeout}s)")
            self._connected = False
            self._schedule_reconnect()
            return False
        except (BleakError, OSError) as e:
            logger.warning(f"BLE connection failed: {e}")
            self._connected = False
            self._schedule_reconnect()
            return False

    async def _disconnect(self) -> None:
        """Disconnect from BLE device."""
        if self._client and self._client.is_connected:
            try:
                await self._client.disconnect()
            except Exception as e:
                logger.debug(f"BLE disconnect error (ignored): {e}")
        self._connected = False
        self._client = None
        logger.info("BLE disconnected")

    def _on_disconnect(self, client: BleakClient) -> None:
        """Callback when BLE device disconnects unexpectedly."""
        self._connected = False
        logger.warning("BLE device disconnected unexpectedly")
        if self._should_run:
            self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt with exponential backoff."""
        if not self._should_run:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return  # Already scheduled
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Reconnect with exponential backoff: 1s -> 2s -> 4s -> ... -> 30s max."""
        while self._should_run and not self.connected:
            logger.info(f"Reconnecting in {self._current_backoff:.1f}s...")
            await asyncio.sleep(self._current_backoff)

            if not self._should_run:
                break

            success = await self._connect_once()
            if success:
                logger.info("BLE reconnected successfully")
                break

            # Exponential backoff
            self._current_backoff = min(
                self._current_backoff * 2,
                self._config.ble_reconnect_max_delay,
            )

    async def _connect_once(self) -> bool:
        """Single connection attempt without scheduling reconnect."""
        if not self._config.ble_address:
            return False
        try:
            self._client = BleakClient(
                self._config.ble_address,
                disconnected_callback=self._on_disconnect,
            )
            await asyncio.wait_for(
                self._client.connect(),
                timeout=self._config.ble_connect_timeout,
            )
            self._connected = True
            self._current_backoff = self._config.ble_reconnect_initial_delay
            return True
        except (TimeoutError, BleakError, OSError) as e:
            logger.debug(f"Reconnect attempt failed: {e}")
            self._connected = False
            return False

    async def _write_status(self, status: AgentStatus, error_code: int = 0) -> None:
        """Write status to BLE characteristic with lock and timeout."""
        data = encode_status_packet(status, error_code)
        await self._write_characteristic(CHAR_STATUS_UUID, data)

    async def _write_context(self, context: ConversationContext) -> None:
        """Write context to BLE characteristic with lock and timeout."""
        data = encode_context_packet(
            plugin_type=context.plugin_type,
            session_id=context.session_id,
            current_task=context.current_task,
        )
        await self._write_characteristic(CHAR_CONTEXT_UUID, data)

    async def _write_characteristic(self, char_uuid: str, data: bytes) -> None:
        """Write data to a BLE characteristic, serialized by lock."""
        if not self.connected or not self._client:
            return

        async with self._write_lock:
            try:
                await asyncio.wait_for(
                    self._client.write_gatt_char(char_uuid, data),
                    timeout=self._config.ble_write_timeout,
                )
                logger.debug(f"BLE write OK: {char_uuid} ({len(data)} bytes)")
            except TimeoutError:
                logger.error(f"BLE write timeout on {char_uuid}")
                self._connected = False
            except (BleakError, OSError) as e:
                logger.error(f"BLE write failed on {char_uuid}: {e}")
                self._connected = False
