"""Tests for BLE audio assembly and local ASR conversion."""

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from openwaifud.asr import ASRService, AudioStreamAssembler
from openwaifud.ble.protocol import AudioDataPacket, AudioEndPacket, AudioStartPacket


def test_audio_stream_assembler_complete():
    assembler = AudioStreamAssembler()
    assert assembler.push(AudioStartPacket(1, 16000, 16, 1)) is None
    assert assembler.push(AudioDataPacket(1, 0, b"\x00\x01")) is None
    recording = assembler.push(AudioEndPacket(1, 2, 0))

    assert recording is not None
    assert recording.stream_id == 1
    assert recording.pcm == b"\x00\x01"


def test_audio_stream_assembler_discards_sequence_gap():
    assembler = AudioStreamAssembler()
    assembler.push(AudioStartPacket(1, 16000, 16, 1))
    assembler.push(AudioDataPacket(1, 1, b"\x00\x01"))
    assert assembler.push(AudioEndPacket(1, 2, 0)) is None


def test_audio_stream_assembler_discards_size_mismatch():
    assembler = AudioStreamAssembler()
    assembler.push(AudioStartPacket(1, 16000, 16, 1))
    assembler.push(AudioDataPacket(1, 0, b"\x00\x01"))
    assert assembler.push(AudioEndPacket(1, 4, 0)) is None


def test_asr_converts_pcm_to_float32():
    captured = {}

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            captured["audio"] = audio
            captured["kwargs"] = kwargs
            return [SimpleNamespace(text=" 你好"), SimpleNamespace(text="涂鸦 ")], None

    service = ASRService()
    service._model = FakeModel()
    pcm = np.array([-32768, 0, 32767], dtype="<i2").tobytes()

    assert service._transcribe_sync(pcm) == "你好涂鸦"
    assert captured["audio"].dtype == np.float32
    np.testing.assert_allclose(captured["audio"], [-1.0, 0.0, 32767 / 32768])
    assert captured["kwargs"]["language"] == "zh"
    assert captured["kwargs"]["beam_size"] == 8
    assert captured["kwargs"]["vad_parameters"]["speech_pad_ms"] == 300


async def test_asr_prepare_loads_and_warms_model_once():
    calls = []

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            calls.append((audio, kwargs))
            return iter(()), None

    service = ASRService()
    with patch.object(service, "_load_model", return_value=FakeModel()) as load_model:
        await service.prepare()
        await service.prepare()

    load_model.assert_called_once_with()
    assert len(calls) == 1
    assert calls[0][0].dtype == np.float32
    assert calls[0][0].size == 16000
    assert calls[0][1]["beam_size"] == 1
