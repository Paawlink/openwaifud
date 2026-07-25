"""Extensible local TTS service backed by Kokoro ONNX."""

from __future__ import annotations

import asyncio
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from kokoro_onnx import Kokoro
    from misaki.zh import ZHG2P

_MODEL_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.1/kokoro-v1.1-zh.onnx"
)
_VOICES_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.1/voices-v1.1-zh.bin"
)
_CONFIG_URL = "https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh/raw/main/config.json"
_EN_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
_EN_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
_TARGET_SAMPLE_RATE = 16000


@dataclass(frozen=True)
class SynthesizedAudio:
    """PCM audio ready for the T5AI speaker."""

    pcm: bytes
    sample_rate: int = _TARGET_SAMPLE_RATE
    sample_bits: int = 16
    channels: int = 1


class TTSService:
    """Lazy, local bilingual TTS with replaceable models and voices."""

    def __init__(
        self,
        model_dir: str = "",
        voice: str = "zf_001",
        en_voice: str = "af_heart",
        speed: float = 1.0,
    ) -> None:
        self._model_dir = Path(model_dir).expanduser() if model_dir else Path.home() / ".cache/openwaifud/tts"
        self._voice = voice
        self._en_voice = en_voice
        self._speed = speed
        self._engine: Kokoro | None = None
        self._en_engine: Kokoro | None = None
        self._g2p: ZHG2P | None = None
        self._lock = asyncio.Lock()

    async def synthesize(self, text: str) -> SynthesizedAudio:
        """Synthesize text to 16 kHz signed 16-bit mono PCM."""
        if not text.strip():
            return SynthesizedAudio(b"")
        await self.prepare()
        if _is_english(text) and self._en_engine is None:
            await self._prepare_english()
        async with self._lock:
            return await asyncio.to_thread(self._synthesize_sync, text.strip())

    async def prepare(self) -> None:
        """Load the model in a worker thread, publishing it only when ready."""
        async with self._lock:
            if self._engine is not None:
                return
            engine, g2p = await asyncio.to_thread(self._prepare_sync)
            self._engine = engine
            self._g2p = g2p

    async def _prepare_english(self) -> None:
        async with self._lock:
            if self._en_engine is None:
                self._en_engine = await asyncio.to_thread(self._prepare_english_sync)

    def _prepare_sync(self) -> tuple[Kokoro, ZHG2P]:
        from kokoro_onnx import Kokoro
        from misaki import en, zh

        self._model_dir.mkdir(parents=True, exist_ok=True)
        model = self._model_dir / "kokoro-v1.1-zh.onnx"
        voices = self._model_dir / "voices-v1.1-zh.bin"
        config = self._model_dir / "config.json"
        self._download_if_missing(_MODEL_URL, model)
        self._download_if_missing(_VOICES_URL, voices)
        self._download_if_missing(_CONFIG_URL, config)

        logger.info(f"Loading Kokoro TTS model from {model}")
        engine = Kokoro(str(model), str(voices), vocab_config=str(config))
        en_g2p = en.G2P(trf=False, british=False, fallback=None)
        g2p = zh.ZHG2P(version="1.1", en_callable=lambda text: en_g2p(text)[0])
        if self._voice not in engine.get_voices():
            raise RuntimeError(f'Kokoro voice "{self._voice}" is not available')
        logger.info(f'Kokoro TTS ready: voice="{self._voice}", speed={self._speed}')
        return engine, g2p

    def _prepare_english_sync(self) -> Kokoro:
        from kokoro_onnx import Kokoro

        self._model_dir.mkdir(parents=True, exist_ok=True)
        model = self._model_dir / "kokoro-v1.0.onnx"
        voices = self._model_dir / "voices-v1.0.bin"
        self._download_if_missing(_EN_MODEL_URL, model)
        self._download_if_missing(_EN_VOICES_URL, voices)
        engine = Kokoro(str(model), str(voices))
        if self._en_voice not in engine.get_voices():
            raise RuntimeError(f'Kokoro English voice "{self._en_voice}" is not available')
        return engine

    @staticmethod
    def _download_if_missing(url: str, destination: Path) -> None:
        if destination.is_file() and destination.stat().st_size > 0:
            return
        temporary = destination.with_suffix(destination.suffix + ".part")
        logger.info(f"Downloading TTS asset: {destination.name}")
        try:
            urllib.request.urlretrieve(url, temporary)
            temporary.replace(destination)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _synthesize_sync(self, text: str) -> SynthesizedAudio:
        import numpy as np

        english = _is_english(text)
        engine = self._en_engine if english else self._engine
        g2p = self._g2p
        if engine is None or (not english and g2p is None):
            raise RuntimeError("Kokoro TTS model is not ready")
        if english:
            samples, source_rate = engine.create(text, voice=self._en_voice, speed=self._speed, lang="en-us")
        else:
            assert g2p is not None
            phonemes, _ = g2p(text)
            samples, source_rate = engine.create(
                phonemes,
                voice=self._voice,
                speed=self._speed,
                is_phonemes=True,
            )
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        if source_rate != _TARGET_SAMPLE_RATE and samples.size:
            output_size = round(samples.size * _TARGET_SAMPLE_RATE / source_rate)
            source_x = np.arange(samples.size, dtype=np.float64)
            target_x = np.arange(output_size, dtype=np.float64) * source_rate / _TARGET_SAMPLE_RATE
            samples = np.interp(target_x, source_x, samples).astype(np.float32)
        pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        logger.debug(f"TTS synthesized: chars={len(text)}, pcm_bytes={len(pcm)}")
        return SynthesizedAudio(pcm)


def _is_english(text: str) -> bool:
    return bool(text) and not any("\u3400" <= char <= "\u9fff" for char in text)
