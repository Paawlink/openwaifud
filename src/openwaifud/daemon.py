"""OpenWaifuD main daemon - orchestrates all components."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from loguru import logger

from openwaifud.api.server import HTTPServer
from openwaifud.ble.client import BLEClient
from openwaifud.chat import ChatNotConfiguredError, ChatService, ChatUpstreamError
from openwaifud.config import Config
from openwaifud.state.manager import StateManager
from openwaifud.tts import TTSService


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
        # 对话服务接入状态快照与会话详情，使「涂鸦」能在语音对话中播报
        # Agent 工作状态及工作目录、对话上下文等细节
        self._chat_service = ChatService(
            state_provider=self._state_manager.get_current_state,
            details_provider=self._state_manager.list_session_details,
        )
        self._tts_service = TTSService(
            model_dir=config.tts_model_dir,
            voice=config.tts_voice,
            speed=config.tts_speed,
        )
        self._http_server = HTTPServer(
            state_manager=self._state_manager,
            host=config.http_host,
            port=config.http_port,
            ble_client=self._ble_client,
            chat_service=self._chat_service,
        )
        self._shutdown_event = asyncio.Event()
        self._model_load_tasks: set[asyncio.Task[None]] = set()

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

        # Wire state output into the BLE device manager. Messages without a target
        # are broadcast; reconnect snapshots carry the connection's device ID.
        self._state_manager.set_ble_callback(self._ble_client.handle_message)
        # BLE（重）连接后由状态管理器只重新同步刚连接的设备
        self._ble_client.set_on_connected(self._state_manager.resync_ble)
        self._ble_client.set_on_transcript(self._handle_voice_message)
        self._ble_client.set_on_connection_changed(self._update_ble_status)

        # Start state consumer (background queue processing)
        await self._state_manager.start_consumer()

        # Start BLE client (connects if address configured)
        await self._ble_client.start()

        self._update_ble_status()

        # Start HTTP server
        await self._http_server.start()

        # Model downloads and warm-up are intentionally outside the startup
        # critical path. First use still waits for the same guarded prepare call.
        self._start_model_preload("ASR", self._ble_client.prepare_asr)
        self._start_model_preload("TTS", self._tts_service.prepare)

        logger.info(f"OpenWaifuD running - HTTP: http://{self._config.http_host}:{self._config.http_port}")

    async def _stop(self) -> None:
        """Stop all components gracefully."""
        logger.info("Stopping OpenWaifuD daemon...")

        # 1. Stop HTTP server (no new requests)
        await self._http_server.stop()

        # 2. Stop state consumer (drain queue with timeout)
        await self._state_manager.stop_consumer()

        # 3. Disconnect BLE
        await self._ble_client.stop()

        for task in self._model_load_tasks:
            task.cancel()
        if self._model_load_tasks:
            await asyncio.gather(*self._model_load_tasks, return_exceptions=True)

        logger.info("OpenWaifuD daemon stopped")

    def _start_model_preload(self, name: str, prepare: Callable[[], Awaitable[None]]) -> None:
        task = asyncio.create_task(self._preload_model(name, prepare), name=f"preload-{name.lower()}")
        self._model_load_tasks.add(task)
        task.add_done_callback(self._model_load_tasks.discard)

    @staticmethod
    async def _preload_model(name: str, prepare: Callable[[], Awaitable[None]]) -> None:
        try:
            await prepare()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"{name} model preload failed; first use will retry: {e}")

    def _update_ble_status(self) -> None:
        self._state_manager.ble_connected = self._ble_client.connected

    async def _handle_voice_message(self, device_id: str | None, message: str | None = None) -> None:
        """Run recognized speech through the internal Agent, TTS, and device speaker."""
        # Accept the old one-argument callback shape for callers/tests predating
        # multi-device voice routing.
        if message is None:
            message = device_id or ""
            device_id = None
        logger.info(f'Voice message: "{message}"')
        try:
            reply = await self._chat_service.chat(message)
            logger.info(f'Agent voice reply: "{reply}"')
            audio = await self._tts_service.synthesize(reply)
            if device_id is None:
                sent = await self._ble_client.send_tts_audio(audio)
            else:
                sent = await self._ble_client.send_tts_audio(device_id, audio)
            if not sent:
                logger.error("Unable to send TTS audio to device")
        except (ChatNotConfiguredError, ChatUpstreamError) as e:
            logger.error(f"Voice Agent unavailable: {e}")
        except Exception as e:
            logger.exception(f"Voice response pipeline failed: {e}")
