"""Discover and manage concurrent BLE connections to OpenWaifu devices."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from bleak import BleakClient as BleakTransport
from bleak import BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from loguru import logger

from openwaifud.asr import ASRService, AudioRecording, AudioStreamAssembler
from openwaifud.ble.protocol import (
    NOTIFY_CHAR_UUID,
    TTS_PCM_CHUNK_SIZE,
    WRITE_CHAR_UUID,
    BLEProtocolError,
    encode_global_event,
    encode_session_detail,
    encode_session_upsert,
    encode_sync_begin,
    encode_sync_end,
    encode_tts_data,
    encode_tts_end,
    encode_tts_start,
    parse_audio_notification,
)
from openwaifud.config import Config
from openwaifud.models import AgentStatus, GlobalEventKind
from openwaifud.tts import SynthesizedAudio

ConnectedCallback = Callable[[str], Awaitable[None]]
ConnectionChangedCallback = Callable[[], None]
TranscriptCallback = Callable[[str, str], Awaitable[None]]

_TTS_PACING_FACTOR = 1.05


def _encode_message(message: dict[str, Any]) -> bytes | None:
    """Encode one StateManager message for the firmware text protocol."""
    msg_type = message.get("type")
    if msg_type == "session_upsert":
        data = message["data"]
        status: AgentStatus = data["status"]
        task = data.get("current_task") or ""
        if status == AgentStatus.ERROR and data.get("error_message"):
            task = data["error_message"]
        return encode_session_upsert(
            session_id=data["session_id"],
            status=status,
            elapsed_seconds=data.get("elapsed_seconds", 0),
            plugin_type=data.get("plugin_type", "agent"),
            task=task,
        )
    if msg_type == "session_detail":
        data = message["data"]
        return encode_session_detail(
            session_id=data["session_id"],
            kind=data["kind"],
            seq=data["seq"],
            text=data.get("text", ""),
        )
    if msg_type == "global_event":
        data = message["data"]
        return encode_global_event(kind=GlobalEventKind(data["kind"]), detail=data.get("detail", ""))
    if msg_type == "sync_begin":
        return encode_sync_begin()
    if msg_type == "sync_end":
        return encode_sync_end()
    logger.warning(f"Unknown message type: {msg_type}")
    return None


class DeviceConnection:
    """Connection state and I/O for one fixed physical BLE target."""

    def __init__(
        self,
        config: Config,
        device_id: str,
        target: str | BLEDevice,
        name: str,
        asr_service: ASRService,
        *,
        on_connected: ConnectedCallback | None = None,
        on_connection_changed: ConnectionChangedCallback | None = None,
        on_transcript: TranscriptCallback | None = None,
    ) -> None:
        self._config = config
        self.device_id = device_id
        self.target = target
        self.name = name
        self.address = target.address if isinstance(target, BLEDevice) else target
        self._asr_service = asr_service
        self._on_connected_callback = on_connected
        self._on_connection_changed = on_connection_changed
        self._on_transcript = on_transcript
        self._client: BleakTransport | None = None
        self._connected = False
        self._write_char: BleakGATTCharacteristic | None = None
        self._write_response = True
        self._write_lock = asyncio.Lock()
        self._tts_pending = 0
        self._state_writes_allowed = asyncio.Event()
        self._state_writes_allowed.set()
        self._message_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=config.queue_max_size)
        self._writer_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._should_run = False
        self._audio_assembler = AudioStreamAssembler()
        self._asr_tasks: set[asyncio.Task[str]] = set()
        self._tts_stream_id = 0
        self.last_error: str | None = None

    @property
    def connected(self) -> bool:
        return self._connected and self._client is not None and self._client.is_connected

    async def start(self) -> None:
        self._should_run = True
        self._writer_task = asyncio.create_task(self._writer_loop(), name=f"ble-writer-{self.device_id}")
        if not await self._connect_once():
            self._schedule_reconnect()

    async def stop(self) -> None:
        self._should_run = False
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reconnect_task
        if self._writer_task and not self._writer_task.done():
            self._writer_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._writer_task
        await self._disconnect()
        if self._asr_tasks:
            await asyncio.gather(*self._asr_tasks, return_exceptions=True)

    async def handle_message(self, message: dict[str, Any]) -> None:
        if not self.connected:
            return
        if self._message_queue.full():
            with suppress(asyncio.QueueEmpty):
                self._message_queue.get_nowait()
                self._message_queue.task_done()
            logger.warning(f"BLE queue full, dropped oldest message for {self.device_id}")
        await self._message_queue.put(message)

    async def _writer_loop(self) -> None:
        try:
            while True:
                message = await self._message_queue.get()
                try:
                    payload = _encode_message(message)
                    if payload is not None:
                        await self._write_payload(payload)
                except Exception as e:
                    logger.error(f"BLE message handling error for {self.device_id}: {e}")
                finally:
                    self._message_queue.task_done()
        except asyncio.CancelledError:
            raise

    async def send_tts_audio(self, audio: SynthesizedAudio) -> bool:
        if not self.connected or not audio.pcm:
            return False
        if audio.sample_rate <= 0 or audio.sample_bits <= 0 or audio.channels <= 0:
            logger.error(
                f"Invalid TTS audio format for {self.device_id}: "
                f"{audio.sample_rate} Hz, {audio.sample_bits}-bit, {audio.channels} channel(s)"
            )
            return False
        self._tts_stream_id = (self._tts_stream_id + 1) & 0xFFFFFFFF
        stream_id = self._tts_stream_id
        bytes_per_second = audio.sample_rate * audio.channels * audio.sample_bits / 8
        self._tts_pending += 1
        self._state_writes_allowed.clear()
        try:
            async with self._write_lock:
                try:
                    start = encode_tts_start(
                        stream_id, len(audio.pcm), audio.sample_rate, audio.sample_bits, audio.channels
                    )
                    if not await self._write_payload_locked(start):
                        return False
                    loop = asyncio.get_running_loop()
                    stream_started_at = loop.time()
                    for sequence, offset in enumerate(range(0, len(audio.pcm), TTS_PCM_CHUNK_SIZE)):
                        chunk = audio.pcm[offset : offset + TTS_PCM_CHUNK_SIZE]
                        if not await self._write_payload_locked(encode_tts_data(stream_id, sequence, chunk)):
                            return False
                        deadline = stream_started_at + (offset + len(chunk)) / bytes_per_second * _TTS_PACING_FACTOR
                        await asyncio.sleep(max(0, deadline - loop.time()))
                    if not await self._write_payload_locked(encode_tts_end(stream_id, len(audio.pcm))):
                        return False
                    logger.info(f"TTS audio sent to {self.device_id}: stream={stream_id}, pcm_bytes={len(audio.pcm)}")
                    return True
                except Exception as e:
                    logger.error(f"TTS BLE stream failed for {self.device_id}: {e}")
                    return False
        finally:
            self._tts_pending -= 1
            if self._tts_pending == 0:
                self._state_writes_allowed.set()

    async def _connect_once(self) -> bool:
        try:
            if self._client is not None:
                await self._disconnect()
            self._client = BleakTransport(self.target, disconnected_callback=self._on_disconnect)
            await asyncio.wait_for(self._client.connect(), timeout=self._config.ble_connect_timeout)
            self._locate_write_char()
            if self._write_char is None:
                raise BleakError(f"Write characteristic {WRITE_CHAR_UUID} not found")
            self._connected = True
            self.last_error = None
            self.address = str(self._client.address)
            self._clear_message_queue()
            logger.info(f"BLE connected: {self.name} [{self.address}], MTU={self._client.mtu_size}")
            await self._subscribe_notifications()
            self._notify_connection_changed()
            if self._on_connected_callback is not None:
                try:
                    await self._on_connected_callback(self.device_id)
                except Exception as e:
                    logger.error(f"BLE on_connected callback failed for {self.device_id}: {e}")
            return True
        except (TimeoutError, BleakError, OSError) as e:
            self.last_error = str(e)
            self._connected = False
            await self._disconnect()
            logger.debug(f"BLE connection failed for {self.device_id}: {e}")
            return False
        except Exception as e:
            self.last_error = str(e)
            self._connected = False
            await self._disconnect()
            logger.exception(f"Unexpected BLE connection failure for {self.device_id}: {e}")
            return False

    async def _disconnect(self) -> None:
        client = self._client
        self._client = None
        self._connected = False
        self._write_char = None
        if client and client.is_connected:
            try:
                await client.disconnect()
            except Exception as e:
                logger.debug(f"BLE disconnect error for {self.device_id} (ignored): {e}")
        self._notify_connection_changed()

    def _on_disconnect(self, client: BleakTransport) -> None:
        was_connected = self._connected
        self._connected = False
        self._write_char = None
        self._audio_assembler.reset()
        self._notify_connection_changed()
        if self._should_run and was_connected:
            logger.warning(f"BLE device disconnected unexpectedly: {self.device_id}")
        if self._should_run:
            self._schedule_reconnect()

    def _notify_connection_changed(self) -> None:
        if self._on_connection_changed is not None:
            self._on_connection_changed()

    def _clear_message_queue(self) -> None:
        """Discard stale commands before a reconnect snapshot is enqueued."""
        while True:
            try:
                self._message_queue.get_nowait()
                self._message_queue.task_done()
            except asyncio.QueueEmpty:
                return

    def _schedule_reconnect(self) -> None:
        if not self._should_run or (self._reconnect_task and not self._reconnect_task.done()):
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_loop(), name=f"ble-reconnect-{self.device_id}")

    async def _reconnect_loop(self) -> None:
        interval = self._config.ble_reconnect_interval
        while self._should_run and not self.connected:
            await asyncio.sleep(interval)
            if self._should_run and await self._connect_once():
                break

    def _locate_write_char(self) -> None:
        self._write_char = None
        if self._client is None:
            return
        target = WRITE_CHAR_UUID.lower()
        for service in self._client.services:
            for char in service.characteristics:
                if char.uuid and char.uuid.lower() == target:
                    self._write_char = char
                    self._write_response = "write" in char.properties
                    return

    async def _subscribe_notifications(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.start_notify(NOTIFY_CHAR_UUID, self._on_notification)
        except (BleakError, OSError) as e:
            logger.warning(f"BLE notify unavailable for {self.device_id}: {e}")

    def _on_notification(self, char: BleakGATTCharacteristic, data: bytearray) -> None:
        payload = bytes(data)
        try:
            packet = parse_audio_notification(payload)
        except BLEProtocolError as e:
            logger.warning(f"Invalid BLE audio notification from {self.device_id}: {e}")
            self._audio_assembler.reset()
            return
        if packet is None:
            return
        recording = self._audio_assembler.push(packet)
        if recording is not None:
            task = asyncio.create_task(self._transcribe_recording(recording))
            self._asr_tasks.add(task)
            task.add_done_callback(self._asr_tasks.discard)

    async def _transcribe_recording(self, recording: AudioRecording) -> str:
        try:
            text = await self._asr_service.transcribe(recording)
            if text and self._on_transcript is not None:
                await self._on_transcript(self.device_id, text)
            return text
        except Exception as e:
            logger.exception(f"ASR transcription failed for {self.device_id}: {e}")
            return ""

    async def _write_payload(self, payload: bytes) -> bool:
        # TTS is a multi-packet stream and must not be delayed by queued state
        # packets once a speech response is ready to send.
        while True:
            await self._state_writes_allowed.wait()
            await self._write_lock.acquire()
            if self._tts_pending:
                self._write_lock.release()
                continue
            try:
                return await self._write_payload_locked(payload)
            finally:
                self._write_lock.release()

    async def _write_payload_locked(self, payload: bytes) -> bool:
        if not self.connected or not self._client or self._write_char is None:
            return False
        try:
            await asyncio.wait_for(
                self._client.write_gatt_char(self._write_char, payload, response=self._write_response),
                timeout=self._config.ble_write_timeout,
            )
            return True
        except (TimeoutError, BleakError, OSError) as e:
            self.last_error = str(e)
            self._connected = False
            logger.error(f"BLE write failed for {self.device_id}: {e}")
            self._notify_connection_changed()
            self._schedule_reconnect()
            return False


class BLEClient:
    """Discover OpenWaifu peripherals and fan out work to independent connections."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._connections: dict[str, DeviceConnection] = {}
        self._asr_service = ASRService(model_name=config.asr_model, language=config.asr_language)
        self._on_connected: ConnectedCallback | None = None
        self._on_connection_changed: ConnectionChangedCallback | None = None
        self._on_transcript: TranscriptCallback | None = None
        self._discovery_task: asyncio.Task[None] | None = None
        self._connection_tasks: set[asyncio.Task[None]] = set()
        self._pending_device_ids: set[str] = set()
        self._tts_pause_counts: dict[str, int] = {}
        self._tts_dirty_devices: set[str] = set()
        self._should_run = False

    @property
    def connected(self) -> bool:
        return any(connection.connected for connection in self._connections.values())

    def get_devices(self) -> list[dict[str, Any]]:
        return [
            {
                "device_id": connection.device_id,
                "name": connection.name,
                "address": connection.address,
                "ble_connected": connection.connected,
                "last_error": connection.last_error,
            }
            for connection in self._connections.values()
        ]

    def set_on_connected(self, callback: ConnectedCallback) -> None:
        self._on_connected = callback

    def set_on_connection_changed(self, callback: ConnectionChangedCallback) -> None:
        self._on_connection_changed = callback

    def set_on_transcript(self, callback: TranscriptCallback) -> None:
        self._on_transcript = callback

    async def start(self) -> None:
        self._should_run = True
        await self._connect()

    async def _connect(self) -> bool:
        """Compatibility hook for callers that used the old single-client API."""
        if self._config.ble_address:
            await self._add_connection(
                self._config.ble_address,
                self._config.ble_address,
                self._config.ble_device_name or "OpenWaifu",
            )
            return self.connected
        if self._discovery_task is None or self._discovery_task.done():
            self._discovery_task = asyncio.create_task(self._discovery_loop(), name="ble-discovery")
        return self.connected

    async def stop(self) -> None:
        self._should_run = False
        if self._discovery_task and not self._discovery_task.done():
            self._discovery_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._discovery_task
        for task in self._connection_tasks:
            task.cancel()
        if self._connection_tasks:
            await asyncio.gather(*self._connection_tasks, return_exceptions=True)
        if self._connections:
            await asyncio.gather(*(connection.stop() for connection in self._connections.values()))

    async def prepare_asr(self) -> None:
        await self._asr_service.prepare()

    async def handle_message(self, message: dict[str, Any]) -> None:
        target_device_id = message.get("target_device_id")
        if target_device_id is not None:
            connection = self._connections.get(target_device_id)
            connected = [connection] if connection is not None and connection.connected else []
        else:
            connected = [connection for connection in self._connections.values() if connection.connected]
        writable = []
        for connection in connected:
            if self._tts_pause_counts.get(connection.device_id, 0):
                self._tts_dirty_devices.add(connection.device_id)
            else:
                writable.append(connection)
        if writable:
            await asyncio.gather(*(connection.handle_message(message) for connection in writable))

    async def send_message(self, device_id: str, message: dict[str, Any]) -> bool:
        connection = self._connections.get(device_id)
        if connection is None or not connection.connected:
            return False
        if self._tts_pause_counts.get(device_id, 0):
            self._tts_dirty_devices.add(device_id)
            return True
        await connection.handle_message(message)
        return True

    async def send_tts_audio(
        self,
        device_id_or_audio: str | SynthesizedAudio,
        audio: SynthesizedAudio | None = None,
    ) -> bool:
        if audio is None:
            if not isinstance(device_id_or_audio, SynthesizedAudio):
                return False
            target_audio = device_id_or_audio
            connection = next((item for item in self._connections.values() if item.connected), None)
        else:
            if not isinstance(device_id_or_audio, str):
                return False
            target_audio = audio
            connection = self._connections.get(device_id_or_audio)
        if connection is None:
            return False
        device_id = connection.device_id
        self._tts_pause_counts[device_id] = self._tts_pause_counts.get(device_id, 0) + 1
        try:
            return await connection.send_tts_audio(target_audio)
        finally:
            remaining = self._tts_pause_counts[device_id] - 1
            if remaining:
                self._tts_pause_counts[device_id] = remaining
            else:
                self._tts_pause_counts.pop(device_id, None)
                if device_id in self._tts_dirty_devices:
                    self._tts_dirty_devices.discard(device_id)
                    if connection.connected and self._on_connected is not None:
                        await self._on_connected(device_id)

    async def _discover_once(self) -> None:
        name = self._config.ble_device_name
        if not name:
            return
        try:
            discovered = await BleakScanner.discover(timeout=self._config.ble_scan_timeout, return_adv=True)
        except (BleakError, OSError) as e:
            logger.warning(f"BLE discovery failed: {e}")
            return
        matches = [
            device
            for device, advertisement in discovered.values()
            if advertisement.local_name == name or device.name == name
        ]
        if matches:
            logger.info(f"Discovered {len(matches)} OpenWaifu BLE device(s)")
        await asyncio.gather(*(self._add_connection(device.address, device, device.name or name) for device in matches))

    async def _add_connection(self, device_id: str, target: str | BLEDevice, name: str) -> None:
        if device_id in self._connections:
            return
        connection = DeviceConnection(
            self._config,
            device_id,
            target,
            name,
            self._asr_service,
            on_connected=self._on_connected,
            on_connection_changed=self._connection_changed,
            on_transcript=self._on_transcript,
        )
        self._connections[device_id] = connection
        await connection.start()

    async def _discovery_loop(self) -> None:
        while self._should_run:
            try:
                logger.info(f"Continuously scanning for BLE devices named {self._config.ble_device_name}")
                async with BleakScanner() as scanner:
                    async for device, advertisement in scanner.advertisement_data():
                        if not self._should_run:
                            return
                        if (
                            advertisement.local_name == self._config.ble_device_name
                            or device.name == self._config.ble_device_name
                        ):
                            self._schedule_connection(device, device.name or self._config.ble_device_name)
            except asyncio.CancelledError:
                raise
            except (BleakError, OSError) as e:
                logger.warning(f"Continuous BLE discovery stopped unexpectedly: {e}")
            if self._should_run:
                await asyncio.sleep(self._config.ble_discovery_interval)

    def _schedule_connection(self, device: BLEDevice, name: str) -> None:
        device_id = device.address
        if device_id in self._connections or device_id in self._pending_device_ids:
            return
        self._pending_device_ids.add(device_id)
        task = asyncio.create_task(
            self._connect_discovered_device(device_id, device, name),
            name=f"ble-connect-{device_id}",
        )
        self._connection_tasks.add(task)
        task.add_done_callback(self._connection_tasks.discard)

    async def _connect_discovered_device(self, device_id: str, device: BLEDevice, name: str) -> None:
        try:
            await self._add_connection(device_id, device, name)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"Failed to register discovered BLE device {device_id}: {e}")
        finally:
            self._pending_device_ids.discard(device_id)

    def _connection_changed(self) -> None:
        if self._on_connection_changed is not None:
            self._on_connection_changed()
