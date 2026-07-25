"""Local text-to-speech service backed by MeloTTS."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from melo.api import TTS

_TARGET_SAMPLE_RATE = 16000


@dataclass(frozen=True)
class SynthesizedAudio:
    """PCM audio ready for the T5AI speaker."""

    pcm: bytes
    sample_rate: int = _TARGET_SAMPLE_RATE
    sample_bits: int = 16
    channels: int = 1


class TTSService:
    """Lazy local TTS powered by one multilingual MeloTTS model."""

    def __init__(
        self,
        language: str = "ZH",
        speaker: str = "ZH",
        device: str = "cpu",
        speed: float = 1.0,
    ) -> None:
        if speed <= 0:
            raise ValueError("TTS speed must be greater than zero")
        self._language = language.upper()
        self._speaker = speaker
        self._device = device
        self._speed = speed
        self._engine: TTS | None = None
        self._speaker_id: int | None = None
        self._source_rate: int | None = None
        self._lock = asyncio.Lock()

    async def synthesize(self, text: str) -> SynthesizedAudio:
        """Synthesize text to 16 kHz signed 16-bit mono PCM."""
        text = text.strip()
        if not text:
            return SynthesizedAudio(b"")
        await self.prepare()
        async with self._lock:
            return await asyncio.to_thread(self._synthesize_sync, text)

    async def prepare(self) -> None:
        """Load and validate the model in a worker thread."""
        async with self._lock:
            if self._engine is not None:
                return
            engine, speaker_id, source_rate = await asyncio.to_thread(self._prepare_sync)
            self._engine = engine
            self._speaker_id = speaker_id
            self._source_rate = source_rate

    def _prepare_sync(self) -> tuple[TTS, int, int]:
        import cmudict
        import nltk
        import nltk.corpus
        import unidic
        import unidic_lite

        # Melo imports its Japanese frontend for every language. Point MeCab at
        # the bundled lite dictionary so Chinese TTS does not require a separate
        # `python -m unidic download` installation step.
        unidic.DICDIR = unidic_lite.DICDIR
        nltk.corpus.cmudict = cmudict
        original_find = nltk.data.find

        def find_nltk_resource(resource_name: str, *args: object, **kwargs: object) -> object:
            if resource_name == "corpora/cmudict.zip":
                return cmudict.__file__
            return original_find(resource_name, *args, **kwargs)

        nltk.data.find = find_nltk_resource
        try:
            from melo.api import TTS
        finally:
            nltk.data.find = original_find

        logger.info(f'Loading MeloTTS model: language="{self._language}", device="{self._device}"')
        engine = TTS(language=self._language, device=self._device)
        if self._device == "cpu":
            import torch

            # Melo's Chinese BERT frontend changes the literal string "cpu" to
            # MPS on macOS after loading its model on CPU. A torch device keeps
            # all inference tensors on CPU without affecting checkpoint loading.
            engine.device = torch.device("cpu")
        speakers = engine.hps.data.spk2id
        if self._speaker not in speakers:
            available = ", ".join(sorted(speakers))
            raise RuntimeError(f'MeloTTS speaker "{self._speaker}" is not available; choose one of: {available}')
        source_rate = int(engine.hps.data.sampling_rate)
        if source_rate <= 0:
            raise RuntimeError(f"MeloTTS returned invalid sample rate: {source_rate}")
        logger.info(
            f'MeloTTS ready: language="{self._language}", speaker="{self._speaker}", '
            f"sample_rate={source_rate}, speed={self._speed}"
        )
        return engine, int(speakers[self._speaker]), source_rate

    def _synthesize_sync(self, text: str) -> SynthesizedAudio:
        import numpy as np

        if self._engine is None or self._speaker_id is None or self._source_rate is None:
            raise RuntimeError("MeloTTS model is not ready")
        samples = self._engine.tts_to_file(
            text,
            self._speaker_id,
            output_path=None,
            speed=self._speed,
            quiet=True,
        )
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        if self._source_rate != _TARGET_SAMPLE_RATE and samples.size:
            output_size = round(samples.size * _TARGET_SAMPLE_RATE / self._source_rate)
            source_x = np.arange(samples.size, dtype=np.float64)
            target_x = np.arange(output_size, dtype=np.float64) * self._source_rate / _TARGET_SAMPLE_RATE
            samples = np.interp(target_x, source_x, samples).astype(np.float32)
        pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        logger.debug(f"TTS synthesized: chars={len(text)}, pcm_bytes={len(pcm)}")
        return SynthesizedAudio(pcm)
