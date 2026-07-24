"""连接 OpenWaifu 固件（BLE 从机）并推送 Agent 文本信息的 BLE 主机客户端。

守护进程作为 BLE Central，连接固件设备后把 :class:`StateManager` 队列中的
状态/上下文消息格式化为一行行 UTF-8 文本，写入固件的 Write 特征
(:data:`~openwaifud.ble.protocol.WRITE_CHAR_UUID`)，固件会将文本追加到屏幕上的
消息列表中显示。

特性：
- 支持按 MAC/UUID 地址直连，或未配置地址时按设备名扫描；
- 固定间隔自动重连（默认每 5 秒一次，不使用退避策略）；
- 使用 asyncio.Lock 串行化写入；
- 优雅降级：写入失败仅记录日志，不向上抛出异常。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from contextlib import suppress
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from loguru import logger

from openwaifud.ble.protocol import (
    WRITE_CHAR_UUID,
    encode_global_event,
    encode_session_detail,
    encode_session_upsert,
    encode_sync_begin,
    encode_sync_end,
)
from openwaifud.config import Config
from openwaifud.models import AgentStatus, GlobalEventKind


class BLEClient:
    """与 T5AI 开发板通信的 BLE Central 客户端。"""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._client: BleakClient | None = None
        self._connected: bool = False
        self._write_char: BleakGATTCharacteristic | None = None
        self._write_response: bool = True
        self._write_lock: asyncio.Lock = asyncio.Lock()
        self._reconnect_task: asyncio.Task[None] | None = None
        self._should_run: bool = False
        # BLE（重）连接成功后触发的回调（通常为 StateManager.resync_ble）
        self._on_connected: Callable[[], Coroutine[Any, Any, None]] | None = None

    @property
    def connected(self) -> bool:
        return self._connected and self._client is not None and self._client.is_connected

    def set_on_connected(self, callback: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """注册连接成功回调，用于在（重）连后重新同步会话看板。"""
        self._on_connected = callback

    async def start(self) -> None:
        """启动 BLE 客户端并尝试首次连接。"""
        self._should_run = True
        await self._connect()

    async def stop(self) -> None:
        """停止 BLE 客户端并断开连接。"""
        self._should_run = False
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reconnect_task
        await self._disconnect()

    async def handle_message(self, message: dict[str, Any]) -> None:
        """处理来自 StateManager 队列的会话命令（注册为 BLE 回调）。

        内部捕获所有异常，绝不向调用方抛出。
        """
        if not self.connected:
            logger.debug("BLE not connected, skipping message")
            return

        try:
            msg_type = message.get("type")
            if msg_type == "session_upsert":
                data = message["data"]
                status: AgentStatus = data["status"]
                # 出错时优先把错误摘要显示为任务文本，便于屏幕上直接看到原因
                task = data.get("current_task") or ""
                if status == AgentStatus.ERROR and data.get("error_message"):
                    task = data["error_message"]
                payload = encode_session_upsert(
                    session_id=data["session_id"],
                    status=status,
                    elapsed_seconds=data.get("elapsed_seconds", 0),
                    plugin_type=data.get("plugin_type", "agent"),
                    task=task,
                )
            elif msg_type == "session_detail":
                data = message["data"]
                payload = encode_session_detail(
                    session_id=data["session_id"],
                    kind=data["kind"],
                    seq=data["seq"],
                    text=data.get("text", ""),
                )
            elif msg_type == "global_event":
                data = message["data"]
                payload = encode_global_event(
                    kind=GlobalEventKind(data["kind"]),
                    detail=data.get("detail", ""),
                )
            elif msg_type == "sync_begin":
                payload = encode_sync_begin()
            elif msg_type == "sync_end":
                payload = encode_sync_end()
            else:
                logger.warning(f"Unknown message type: {msg_type}")
                return
            await self._write_payload(payload)
        except Exception as e:
            logger.error(f"BLE message handling error: {e}")

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    async def _resolve_target(self) -> str | BLEDevice | None:
        """确定连接目标：优先使用配置地址，否则按设备名扫描。

        返回地址字符串或 BLEDevice 对象；找不到返回 None。
        """
        if self._config.ble_address:
            return self._config.ble_address

        name = self._config.ble_device_name
        if not name:
            logger.warning("Neither BLE address nor device name configured, BLE disabled")
            return None

        logger.info(f"Scanning for BLE device by name: {name}")
        device = await BleakScanner.find_device_by_name(name, timeout=self._config.ble_scan_timeout)
        if device is None:
            logger.warning(f'BLE device "{name}" not found within {self._config.ble_scan_timeout:.0f}s')
            return None
        logger.info(f"Found BLE device: {device.name} [{device.address}]")
        return device

    async def _connect(self) -> bool:
        """尝试连接 BLE 设备（失败时安排重连）。"""
        success = await self._connect_once()
        if not success:
            self._schedule_reconnect()
        return success

    async def _disconnect(self) -> None:
        """断开 BLE 设备连接。"""
        if self._client and self._client.is_connected:
            try:
                await self._client.disconnect()
            except Exception as e:
                logger.debug(f"BLE disconnect error (ignored): {e}")
        self._connected = False
        self._client = None
        self._write_char = None
        logger.info("BLE disconnected")

    def _on_disconnect(self, client: BleakClient) -> None:
        """设备意外断开时的回调。"""
        self._connected = False
        self._write_char = None
        logger.warning("BLE device disconnected unexpectedly")
        if self._should_run:
            self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        """安排一次固定间隔重连。"""
        if not self._should_run:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return  # 已在重连中
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """固定间隔重连：每隔 ble_reconnect_interval 秒尝试一次，不使用退避策略。"""
        interval = self._config.ble_reconnect_interval
        while self._should_run and not self.connected:
            logger.info(f"Reconnecting in {interval:.1f}s...")
            await asyncio.sleep(interval)

            if not self._should_run:
                break

            if await self._connect_once():
                logger.info("BLE reconnected successfully")
                break

    async def _connect_once(self) -> bool:
        """单次连接尝试（不安排重连），成功后定位 Write 特征。"""
        target = await self._resolve_target()
        if target is None:
            return False

        try:
            self._client = BleakClient(target, disconnected_callback=self._on_disconnect)
            await asyncio.wait_for(self._client.connect(), timeout=self._config.ble_connect_timeout)
            self._locate_write_char()
            if self._write_char is None:
                logger.error(f"Write characteristic {WRITE_CHAR_UUID} not found on device")
                await self._disconnect()
                return False
            self._connected = True
            logger.info(f"BLE connected, write char={self._write_char.uuid}, MTU={self._client.mtu_size}")
            # 连接成功后重新同步会话看板（先清空，再下发当前所有会话）
            if self._on_connected is not None:
                try:
                    await self._on_connected()
                except Exception as e:
                    logger.error(f"BLE on_connected callback error: {e}")
            return True
        except (TimeoutError, BleakError, OSError) as e:
            logger.warning(f"BLE connection failed: {e}")
            self._connected = False
            return False

    def _locate_write_char(self) -> None:
        """在已连接的 client 上定位 Write 特征并缓存其写入模式。"""
        self._write_char = None
        if self._client is None:
            return
        target = WRITE_CHAR_UUID.lower()
        for service in self._client.services:
            for char in service.characteristics:
                if char.uuid and char.uuid.lower() == target:
                    self._write_char = char
                    # 优先使用 write-without-response（更快、不阻塞）
                    self._write_response = "write-without-response" not in char.properties
                    return

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    async def _write_payload(self, payload: bytes) -> None:
        """将已编码的命令字节写入 Write 特征（串行化 + 超时保护）。"""
        if not self.connected or not self._client or self._write_char is None:
            return

        async with self._write_lock:
            try:
                await asyncio.wait_for(
                    self._client.write_gatt_char(self._write_char, payload, response=self._write_response),
                    timeout=self._config.ble_write_timeout,
                )
                logger.debug(f"BLE write OK ({len(payload)} bytes): {payload.decode('utf-8', 'replace')}")
            except TimeoutError:
                logger.error("BLE write timeout")
                self._connected = False
            except (BleakError, OSError) as e:
                logger.error(f"BLE write failed: {e}")
                self._connected = False
