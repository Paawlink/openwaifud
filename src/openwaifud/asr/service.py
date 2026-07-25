"""Reassemble BLE PCM streams and transcribe them with faster-whisper."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from openwaifud.ble.protocol import AudioDataPacket, AudioPacket, AudioStartPacket

if TYPE_CHECKING:
    from faster_whisper import WhisperModel


@dataclass(frozen=True)
class AudioRecording:
    """A complete PCM recording received from the device."""

    stream_id: int
    sample_rate: int
    sample_bits: int
    channels: int
    pcm: bytes
    dropped_bytes: int = 0


class AudioStreamAssembler:
    """Validate and assemble one ordered BLE audio stream at a time."""

    def __init__(self) -> None:
        self._start: AudioStartPacket | None = None
        self._next_sequence = 0
        self._pcm = bytearray()
        self._invalid = False

    def reset(self) -> None:
        self._start = None
        self._next_sequence = 0
        self._pcm.clear()
        self._invalid = False

    def push(self, packet: AudioPacket) -> AudioRecording | None:
        if isinstance(packet, AudioStartPacket):
            self._start = packet
            self._next_sequence = 0
            self._pcm.clear()
            self._invalid = False
            return None

        if self._start is None or packet.stream_id != self._start.stream_id:
            return None

        if isinstance(packet, AudioDataPacket):
            if packet.sequence != self._next_sequence:
                logger.warning(
                    f"BLE audio sequence gap: stream={packet.stream_id}, "
                    f"expected={self._next_sequence}, received={packet.sequence}"
                )
                self._invalid = True
            self._next_sequence = packet.sequence + 1
            if not self._invalid:
                self._pcm.extend(packet.pcm)
            return None

        start = self._start
        pcm = bytes(self._pcm)
        valid = not self._invalid and len(pcm) == packet.pcm_bytes
        self.reset()
        if not valid:
            logger.warning(
                f"Discarding incomplete BLE audio: stream={packet.stream_id}, "
                f"received={len(pcm)}, expected={packet.pcm_bytes}"
            )
            return None
        return AudioRecording(
            stream_id=start.stream_id,
            sample_rate=start.sample_rate,
            sample_bits=start.sample_bits,
            channels=start.channels,
            pcm=pcm,
            dropped_bytes=packet.dropped_bytes,
        )


class ASRService:
    """Preloadable local faster-whisper transcription service."""

    def __init__(self, model_name: str = "medium", language: str = "zh") -> None:
        self._model_name = model_name
        self._language = language
        self._model: WhisperModel | None = None
        self._model_lock = asyncio.Lock()

    async def prepare(self) -> None:
        """Load and warm up the model before the first real recording."""
        async with self._model_lock:
            if self._model is not None:
                return
            self._model = await asyncio.to_thread(self._load_model)
            await asyncio.to_thread(self._warm_up_sync)

    async def transcribe(self, recording: AudioRecording) -> str:
        """Transcribe a complete recording without blocking the asyncio loop."""
        if not recording.pcm:
            return ""
        if recording.sample_bits != 16 or recording.channels != 1 or recording.sample_rate != 16000:
            logger.error(
                f"ASR unsupported PCM format: {recording.sample_rate} Hz, "
                f"{recording.sample_bits}-bit, {recording.channels} channel(s)"
            )
            return ""

        await self.prepare()
        async with self._model_lock:
            text = await asyncio.to_thread(self._transcribe_sync, recording.pcm)
        if recording.dropped_bytes:
            logger.warning(f"ASR input lost {recording.dropped_bytes} PCM bytes on device")
        logger.info(f'ASR recognized: "{text}"')
        return text

    def _load_model(self) -> WhisperModel:
        from faster_whisper import WhisperModel

        logger.info(f'Loading faster-whisper model "{self._model_name}" (CPU int8)')
        return WhisperModel(self._model_name, device="cpu", compute_type="int8")

    def _warm_up_sync(self) -> None:
        import numpy as np

        logger.info(f'Warming up faster-whisper model "{self._model_name}"')
        segments, _info = self._model.transcribe(
            np.zeros(16000, dtype=np.float32),
            language=self._language,
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        list(segments)
        logger.info(f'faster-whisper model "{self._model_name}" is ready')

    def _transcribe_sync(self, pcm: bytes) -> str:
        import numpy as np

        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        segments, _info = self._model.transcribe(
            audio,
            language=self._language,
            task="transcribe",
            beam_size=8,
            best_of=8,
            temperature=0.0,
            vad_filter=True,
            vad_parameters={
                "threshold": 0.35,
                "min_speech_duration_ms": 100,
                "min_silence_duration_ms": 300,
                "speech_pad_ms": 300,
            },
            condition_on_previous_text=False,
            initial_prompt=(
                "以下是简体中文语音指令，可能包含 OpenWaifu、Claude Code、Codex、"
                "OpenCode、Qoder 和编程任务。"
            ),
        )
        return "".join(segment.text for segment in segments).strip()
