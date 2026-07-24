"""会话感知的异步状态管理器，带事件队列用于 BLE 同步。

架构（周期性全量同步）：
- HTTP 处理器 / 实时监听器调用 :meth:`update_session`（或兼容包装
  :meth:`update_status` / :meth:`update_context`）只更新内存中的 **活跃会话注册表**
  （``session_id -> _SessionRuntime``），不做任何增量下发；
- 后台清扫器协程周期性地把“已完成后停留超时”或“长时间无更新”的会话从注册表移除，
  使注册表始终等于 GET /api/v1/state 的权威快照；
- 后台全量同步协程每隔 ``sync_interval`` 秒，把当前注册表整帧下发给固件
  （``B`` -> 全量 ``S`` -> ``E``），固件据此强制刷屏，使屏幕与权威状态完全一致；
- 后台消费者协程从队列取出命令并交给 BLE 发送回调（串行写入）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from openwaifud.models import (
    AgentStatus,
    ConversationContext,
    DaemonState,
    GlobalEventKind,
    SessionInfo,
    StatusUpdate,
)


@dataclass
class _SessionRuntime:
    """单个会话的内部运行时记录（使用单调时钟计算耗时）。"""

    session_id: str
    plugin_type: str = "agent"
    status: AgentStatus = AgentStatus.THINKING
    current_task: str = ""
    error_message: str | None = None
    started_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)
    done: bool = False
    done_since: float | None = None

    def elapsed(self, now: float | None = None) -> float:
        """返回该会话已运行的秒数。"""
        return max(0.0, (now if now is not None else time.monotonic()) - self.started_at)


class StateManager:
    """管理 Agent 会话状态，并通过异步队列与 BLE 同步。"""

    def __init__(
        self,
        queue_max_size: int = 100,
        *,
        done_linger: float = 5.0,
        idle_timeout: float = 60.0,
        sweep_interval: float = 1.0,
        sync_interval: float = 2.0,
    ) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_max_size)
        self._sessions: dict[str, _SessionRuntime] = {}
        self._last_session_id: str | None = None
        self._current_context: ConversationContext | None = None
        self._ble_connected: bool = False
        self._start_time: datetime = datetime.now(UTC)
        self._consumer_task: asyncio.Task | None = None
        self._sweep_task: asyncio.Task | None = None
        self._sync_task: asyncio.Task | None = None
        self._ble_callback: Callable[..., Coroutine] | None = None

        self._done_linger = done_linger
        self._idle_timeout = idle_timeout
        self._sweep_interval = sweep_interval
        self._sync_interval = sync_interval

    @property
    def ble_connected(self) -> bool:
        """Whether BLE device is currently connected."""
        return self._ble_connected

    @ble_connected.setter
    def ble_connected(self, value: bool) -> None:
        self._ble_connected = value

    # ------------------------------------------------------------------
    # 对外更新接口
    # ------------------------------------------------------------------

    async def update_session(
        self,
        session_id: str,
        *,
        plugin_type: str | None = None,
        status: AgentStatus | None = None,
        current_task: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """新增或更新一个会话（仅更新内存注册表，实际下发由周期性全量同步负责）。

        仅传入的（非 None）字段会被更新，便于状态与上下文分别到达时增量合并。
        """
        now = time.monotonic()
        rt = self._sessions.get(session_id)
        if rt is None:
            rt = _SessionRuntime(session_id=session_id, started_at=now)
            self._sessions[session_id] = rt

        if plugin_type is not None:
            rt.plugin_type = plugin_type
        if status is not None:
            rt.status = status
        if current_task is not None:
            rt.current_task = current_task
        rt.error_message = error_message
        rt.updated_at = now

        # 任意一次上报都视为活跃；仅当最终状态为 IDLE 时标记为“已完成/待移除”。
        if rt.status == AgentStatus.IDLE:
            if not rt.done:
                rt.done = True
                rt.done_since = now
        else:
            rt.done = False
            rt.done_since = None

        self._last_session_id = session_id
        self._current_context = ConversationContext(
            plugin_type=rt.plugin_type,
            session_id=rt.session_id,
            current_task=rt.current_task,
        )

    async def update_status(self, update: StatusUpdate) -> None:
        """（兼容接口）上报一次状态更新。

        若未携带 session_id，则应用到最近一个活跃会话；都没有时使用 ``default``。
        """
        session_id = update.session_id or self._last_session_id or "default"
        await self.update_session(
            session_id,
            status=update.status,
            error_message=update.error_message,
        )

    async def update_context(self, context: ConversationContext) -> None:
        """（兼容接口）上报一次会话上下文更新。"""
        await self.update_session(
            context.session_id,
            plugin_type=context.plugin_type,
            current_task=context.current_task,
        )

    async def emit_global_event(self, event: GlobalEventKind, message: str | None = None) -> None:
        """发起一次全局事件（泳道 2）：立即入队，事件驱动地即时下发给固件。

        与会话列表（泳道 1）互不干扰——全局事件不进入全量快照循环，也不改变
        任何会话注册表状态；固件收到后驱动左下角全局状态机（瞬时展示后自动回落）。
        """
        await self._enqueue(
            {
                "type": "global_event",
                "data": {"kind": event, "detail": message or ""},
            }
        )
        logger.debug(f"Global event emitted: {event}")

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_current_state(self) -> DaemonState:
        """Get current cached state (for GET queries)."""
        now = datetime.now(UTC)
        mono = time.monotonic()
        uptime = (now - self._start_time).total_seconds()
        sessions = [self._to_session_info(rt, mono) for rt in self._sessions.values()]
        return DaemonState(
            agent_status=self._derive_agent_status(),
            context=self._current_context,
            sessions=sessions,
            ble_connected=self._ble_connected,
            uptime_seconds=uptime,
            timestamp=now,
        )

    def _derive_agent_status(self) -> AgentStatus:
        """综合当前会话推断整体状态：取最近更新的活跃会话，无则 IDLE。"""
        active = [rt for rt in self._sessions.values() if not rt.done]
        if not active:
            return AgentStatus.IDLE
        latest = max(active, key=lambda rt: rt.updated_at)
        return latest.status

    @staticmethod
    def _to_session_info(rt: _SessionRuntime, now: float) -> SessionInfo:
        return SessionInfo(
            session_id=rt.session_id,
            plugin_type=rt.plugin_type,
            status=rt.status,
            current_task=rt.current_task,
            error_message=rt.error_message,
            elapsed_seconds=round(rt.elapsed(now), 1),
            is_done=rt.done,
        )

    # ------------------------------------------------------------------
    # BLE 回调 / 生命周期
    # ------------------------------------------------------------------

    def set_ble_callback(self, callback: Callable[..., Coroutine]) -> None:
        """Register BLE send callback. Called by daemon during initialization."""
        self._ble_callback = callback

    async def resync_ble(self) -> None:
        """BLE（重）连接后立即下发一帧完整快照，避免等待下一个同步周期。"""
        await self._push_snapshot()
        logger.info(f"Resync BLE with {len(self._sessions)} active session(s)")

    async def start_consumer(self) -> None:
        """启动后台消费者、清扫器与全量同步任务。"""
        self._consumer_task = asyncio.create_task(self._consume_loop())
        self._sweep_task = asyncio.create_task(self._sweep_loop())
        self._sync_task = asyncio.create_task(self._full_sync_loop())
        logger.info("State consumer started")

    async def stop_consumer(self) -> None:
        """优雅停止后台任务。"""
        for task in (self._consumer_task, self._sweep_task, self._sync_task):
            if task and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        logger.info("State consumer stopped")

    # ------------------------------------------------------------------
    # 队列 / 清扫
    # ------------------------------------------------------------------

    async def _enqueue_upsert(self, rt: _SessionRuntime, now: float) -> None:
        message = {
            "type": "session_upsert",
            "data": {
                "session_id": rt.session_id,
                "plugin_type": rt.plugin_type,
                "status": rt.status,
                "current_task": rt.current_task,
                "error_message": rt.error_message,
                "elapsed_seconds": int(rt.elapsed(now)),
            },
        }
        await self._enqueue(message)

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

    async def _sweep_loop(self) -> None:
        """定期移除“完成后停留超时”或“长时间无更新”的会话。"""
        logger.debug("Sweep loop running")
        try:
            while True:
                await asyncio.sleep(self._sweep_interval)
                await self._sweep_once()
        except asyncio.CancelledError:
            logger.debug("Sweep loop cancelled")
            raise

    async def _sweep_once(self) -> None:
        now = time.monotonic()
        expired = [sid for sid, rt in self._sessions.items() if self._is_expired(rt, now)]

        for sid in expired:
            self._sessions.pop(sid, None)
            if self._last_session_id == sid:
                self._last_session_id = None
            logger.debug(f"Session expired and removed: {sid}")

    def _is_expired(self, rt: _SessionRuntime, now: float) -> bool:
        """完成后停留超时，或长时间无更新，则视为可移除。"""
        if rt.done and rt.done_since is not None and (now - rt.done_since) >= self._done_linger:
            return True
        return (now - rt.updated_at) >= self._idle_timeout

    async def _full_sync_loop(self) -> None:
        """周期性全量同步：每隔 ``sync_interval`` 秒把整帧快照下发给固件。

        不做任何增量或省流优化：无论状态是否变化，都按 ``B`` -> 全量 ``S`` -> ``E``
        下发当前所有会话，固件据此强制刷屏，使屏幕始终等于权威状态。
        """
        logger.debug("Full-sync loop running")
        try:
            while True:
                await asyncio.sleep(self._sync_interval)
                await self._push_snapshot()
        except asyncio.CancelledError:
            logger.debug("Full-sync loop cancelled")
            raise

    async def _push_snapshot(self) -> None:
        """下发一帧完整快照（``B`` -> 全量 ``S`` -> ``E``）。

        未连接时消费者会在发送环节自动丢弃，故此处不做连接判断，保持逻辑简单。
        """
        now = time.monotonic()
        await self._enqueue({"type": "sync_begin"})
        for rt in self._sessions.values():
            await self._enqueue_upsert(rt, now)
        await self._enqueue({"type": "sync_end"})
