"""Tests for concurrent BLE device discovery and message routing."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openwaifud.ble import client as ble_client_module
from openwaifud.ble.client import BLEClient
from openwaifud.tts import SynthesizedAudio


class FakeConnection:
    instances: list["FakeConnection"] = []

    def __init__(self, config, device_id, target, name, asr_service, **kwargs):
        self.device_id = device_id
        self.target = target
        self.name = name
        self.address = device_id
        self.connected = True
        self.last_error = None
        self.handle_message = AsyncMock()
        self.start = AsyncMock()
        self.stop = AsyncMock()
        self.send_tts_audio = AsyncMock(return_value=True)
        self.__class__.instances.append(self)


@pytest.fixture
def fake_connection(monkeypatch):
    FakeConnection.instances.clear()
    monkeypatch.setattr(ble_client_module, "DeviceConnection", FakeConnection)
    return FakeConnection


async def test_discovery_connects_all_matching_devices(config, monkeypatch, fake_connection):
    devices = [
        SimpleNamespace(address="device-a", name="OpenWaifu"),
        SimpleNamespace(address="device-b", name="OpenWaifu"),
    ]
    advertisements = {device.address: (device, SimpleNamespace(local_name="OpenWaifu")) for device in devices}
    monkeypatch.setattr(
        ble_client_module.BleakScanner,
        "discover",
        AsyncMock(return_value=advertisements),
    )

    client = BLEClient(config)
    await client._discover_once()

    assert [device["device_id"] for device in client.get_devices()] == ["device-a", "device-b"]
    assert all(connection.start.await_count == 1 for connection in fake_connection.instances)


async def test_continuous_discovery_connects_devices_as_they_appear(config, monkeypatch, fake_connection):
    advertisements = [
        (SimpleNamespace(address="device-a", name="OpenWaifu"), SimpleNamespace(local_name="OpenWaifu")),
        (SimpleNamespace(address="device-b", name="OpenWaifu"), SimpleNamespace(local_name="OpenWaifu")),
    ]
    keep_scanning = asyncio.Event()

    class FakeScanner:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def advertisement_data(self):
            for advertisement in advertisements:
                yield advertisement
            await keep_scanning.wait()

    monkeypatch.setattr(ble_client_module, "BleakScanner", FakeScanner)
    client = BLEClient(config)
    client._should_run = True
    task = asyncio.create_task(client._discovery_loop())

    while len(client.get_devices()) < 2:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert [device["device_id"] for device in client.get_devices()] == ["device-a", "device-b"]


async def test_message_target_is_routed_to_one_connection(config, fake_connection):
    client = BLEClient(config)
    await client._add_connection("device-a", "device-a", "OpenWaifu")
    await client._add_connection("device-b", "device-b", "OpenWaifu")

    await client.handle_message({"type": "sync_begin", "target_device_id": "device-b"})

    fake_connection.instances[0].handle_message.assert_not_awaited()
    fake_connection.instances[1].handle_message.assert_awaited_once_with(
        {"type": "sync_begin", "target_device_id": "device-b"}
    )


async def test_message_without_target_is_broadcast(config, fake_connection):
    client = BLEClient(config)
    await client._add_connection("device-a", "device-a", "OpenWaifu")
    await client._add_connection("device-b", "device-b", "OpenWaifu")

    message = {"type": "sync_end"}
    await client.handle_message(message)

    for connection in fake_connection.instances:
        connection.handle_message.assert_awaited_once_with(message)


async def test_tts_is_sent_only_to_origin_device(config, fake_connection):
    client = BLEClient(config)
    await client._add_connection("device-a", "device-a", "OpenWaifu")
    await client._add_connection("device-b", "device-b", "OpenWaifu")
    audio = SynthesizedAudio(pcm=b"voice")

    assert await client.send_tts_audio("device-b", audio) is True

    fake_connection.instances[0].send_tts_audio.assert_not_awaited()
    fake_connection.instances[1].send_tts_audio.assert_awaited_once_with(audio)


async def test_updates_pause_only_for_device_sending_tts(config, fake_connection):
    client = BLEClient(config)
    await client._add_connection("device-a", "device-a", "OpenWaifu")
    await client._add_connection("device-b", "device-b", "OpenWaifu")
    device_a, device_b = fake_connection.instances
    tts_started = asyncio.Event()
    finish_tts = asyncio.Event()
    resync = AsyncMock()
    client.set_on_connected(resync)

    async def send_audio(audio):
        tts_started.set()
        await finish_tts.wait()
        return True

    device_a.send_tts_audio.side_effect = send_audio
    tts_task = asyncio.create_task(client.send_tts_audio("device-a", SynthesizedAudio(pcm=b"voice")))
    await tts_started.wait()

    message = {"type": "sync_end"}
    await client.handle_message(message)

    device_a.handle_message.assert_not_awaited()
    device_b.handle_message.assert_awaited_once_with(message)
    resync.assert_not_awaited()

    finish_tts.set()
    assert await tts_task is True
    resync.assert_awaited_once_with("device-a")
