"""Tests for non-blocking background model loading."""

import asyncio
from unittest.mock import AsyncMock

from openwaifud.ble.client import BLEClient


async def test_ble_start_does_not_wait_for_asr(config):
    client = BLEClient(config)
    client._connect = AsyncMock(return_value=False)

    model_loading = asyncio.Event()

    async def blocked_prepare():
        await model_loading.wait()

    client._asr_service.prepare = blocked_prepare
    await asyncio.wait_for(client.start(), timeout=0.1)

    client._connect.assert_awaited_once_with()
