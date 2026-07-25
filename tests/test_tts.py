"""Tests for the extensible Kokoro TTS service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np

from openwaifud.tts import SynthesizedAudio, TTSService


def test_tts_resamples_and_converts_to_pcm():
    class FakeG2P:
        def __call__(self, text):
            assert text == "你好"
            return "phonemes", None

    class FakeEngine:
        def create(self, phonemes, **kwargs):
            assert phonemes == "phonemes"
            assert kwargs == {"voice": "zf_001", "speed": 1.0, "is_phonemes": True}
            return np.array([-1.0, 0.0, 1.0], dtype=np.float32), 24000

    service = TTSService()
    service._g2p = FakeG2P()
    service._engine = FakeEngine()
    audio = service._synthesize_sync("你好")

    samples = np.frombuffer(audio.pcm, dtype="<i2")
    assert audio.sample_rate == 16000
    assert samples.size == 2
    assert samples[0] == -32767


def test_chinese_g2p_preserves_english_words():
    from misaki import zh

    g2p = zh.ZHG2P(version="1.1", en_callable=lambda text: f"<{text}>")

    phonemes, _ = g2p("你好 OpenWaifu TTS")

    assert "<OpenWaifu TTS>" in phonemes


async def test_tts_synthesize_uses_worker_thread():
    service = TTSService()
    service._engine = SimpleNamespace()
    service._g2p = SimpleNamespace()
    service._synthesize_sync = lambda text: text
    assert await service.synthesize(" hello ") == "hello"


async def test_tts_prepare_loads_model_once():
    service = TTSService()
    engine = SimpleNamespace()
    g2p = SimpleNamespace()
    with patch.object(service, "_prepare_sync", return_value=(engine, g2p)) as prepare_sync:
        await service.prepare()
        await service.prepare()

    prepare_sync.assert_called_once_with()
    assert service._engine is engine
    assert service._g2p is g2p


async def test_daemon_voice_pipeline(config):
    from openwaifud.daemon import OpenWaifuDaemon

    daemon = OpenWaifuDaemon(config)
    calls = []

    async def fake_chat(message):
        calls.append(("chat", message))
        return "任务正在运行"

    async def fake_synthesize(text):
        calls.append(("tts", text))
        return SynthesizedAudio(b"\x00\x00")

    async def fake_send(audio):
        calls.append(("ble", audio.pcm))
        return True

    daemon._chat_service.chat = fake_chat
    daemon._tts_service.synthesize = fake_synthesize
    daemon._ble_client.send_tts_audio = fake_send

    await daemon._handle_voice_message("当前任务怎么样")
    assert calls == [
        ("chat", "当前任务怎么样"),
        ("tts", "任务正在运行"),
        ("ble", b"\x00\x00"),
    ]


async def test_daemon_voice_pipeline_replies_to_origin_device(config):
    from openwaifud.daemon import OpenWaifuDaemon

    daemon = OpenWaifuDaemon(config)
    daemon._chat_service.chat = AsyncMock(return_value="收到")
    audio = SynthesizedAudio(b"\x00\x00")
    daemon._tts_service.synthesize = AsyncMock(return_value=audio)
    daemon._ble_client.send_tts_audio = AsyncMock(return_value=True)

    await daemon._handle_voice_message("device-b", "你好")

    daemon._ble_client.send_tts_audio.assert_awaited_once_with("device-b", audio)
