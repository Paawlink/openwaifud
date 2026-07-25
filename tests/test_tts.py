"""Tests for the MeloTTS service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np

from openwaifud.tts import SynthesizedAudio, TTSService


def test_tts_resamples_and_converts_to_pcm():
    class FakeEngine:
        def tts_to_file(self, text, speaker_id, **kwargs):
            assert text == "你好 OpenWaifu"
            assert speaker_id == 0
            assert kwargs == {"output_path": None, "speed": 1.0, "quiet": True}
            return np.array([-1.0, 0.0, 1.0], dtype=np.float32)

    service = TTSService()
    service._engine = FakeEngine()
    service._speaker_id = 0
    service._source_rate = 24000
    audio = service._synthesize_sync("你好 OpenWaifu")

    samples = np.frombuffer(audio.pcm, dtype="<i2")
    assert audio.sample_rate == 16000
    assert samples.size == 2
    assert samples[0] == -32767
async def test_tts_synthesize_uses_worker_thread():
    service = TTSService()
    service._engine = SimpleNamespace()
    service._speaker_id = 0
    service._source_rate = 22050
    service._synthesize_sync = lambda text: text
    assert await service.synthesize(" hello ") == "hello"


async def test_tts_prepare_loads_model_once():
    service = TTSService()
    engine = SimpleNamespace()
    with patch.object(service, "_prepare_sync", return_value=(engine, 3, 22050)) as prepare_sync:
        await service.prepare()
        await service.prepare()

    prepare_sync.assert_called_once_with()
    assert service._engine is engine
    assert service._speaker_id == 3
    assert service._source_rate == 22050


def test_tts_rejects_non_positive_speed():
    for speed in (0, -1):
        try:
            TTSService(speed=speed)
        except ValueError as error:
            assert str(error) == "TTS speed must be greater than zero"
        else:
            raise AssertionError("non-positive TTS speed was accepted")


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
