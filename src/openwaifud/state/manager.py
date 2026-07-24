"""Async state manager with event queue for BLE sync."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from openwaifud.models import AgentStatus, ConversationContext, DaemonState, StatusUpdate


class StateManager:
    """Manages agent state with async queue for BLE synchronization.

    Architecture:
    - HTTP handlers call update_status/update_context to enqueue changes
    - A background consumer coroutine dequeues and invokes the BLE send callback
    - Current state is cached in memory for GET /api/v1/state queries
    """

    def __init__(self, queue_max_size: int = 100) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_max_size)
        self._current_status: AgentStatus = AgentStatus.IDLE
        self._current_context: ConversationContext | None = None
        self._ble_connected: bool = False
        self._start_time: datetime = datetime.now(UTC)
        self._consumer_task: asyncio.Task | None = None
        self._ble_callback: Callable[..., Coroutine] | None = None

    @property
    def ble_connected(self) -> bool:
        """Whether BLE device is currently connected."""
        return self._ble_connected

    @ble_connected.setter
    def ble_connected(self, value: bool) -> None:
        self._ble_connected = value

    async def update_status(self, update: StatusUpdate) -> None:
        """Enqueue a status update. Non-blocking for HTTP handlers."""
        self._current_status = update.status
        message = {"type": "status", "data": update}
        await self._enqueue(message)

    async def update_context(self, context: ConversationContext) -> None:
        """Enqueue a context update. Non-blocking for HTTP handlers."""
        self._current_context = context
        message = {"type": "context", "data": context}
        await self._enqueue(message)

    def get_current_state(self) -> DaemonState:
        """Get current cached state (for GET queries)."""
        now = datetime.now(UTC)
        uptime = (now - self._start_time).total_seconds()
        return DaemonState(
            agent_status=self._current_status,
            context=self._current_context,
            ble_connected=self._ble_connected,
            uptime_seconds=uptime,
            timestamp=now,
        )

    def set_ble_callback(self, callback: Callable[..., Coroutine]) -> None:
        """Register BLE send callback. Called by daemon during initialization."""
        self._ble_callback = callback

    async def start_consumer(self) -> None:
        """Start the background consumer task."""
        self._consumer_task = asyncio.create_task(self._consume_loop())
        logger.info("State consumer started")

    async def stop_consumer(self) -> None:
        """Stop the consumer task gracefully."""
        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
        logger.info("State consumer stopped")

    async def _enqueue(self, message: dict[str, Any]) -> None:
        """Enqueue message, dropping oldest if full."""
        if self._queue.full():
            try:
                self._queue.get_nowait()  # Drop oldest
                logger.warning("State queue full, dropped oldest message")
            except asyncio.QueueEmpty:
                pass
        await self._queue.put(message)

    async def _consume_loop(self) -> None:
        """Background consumer: dequeue and send via BLE callback."""
        logger.debug("Consumer loop running")
        try:
            while True:
                message = await self._queue.get()
                if self._ble_callback is not None:
                    try:
                        await self._ble_callback(message)
                    except Exception as e:
                        logger.error(f"BLE callback error: {e}")
                self._queue.task_done()
        except asyncio.CancelledError:
            logger.debug("Consumer loop cancelled")
            raise
