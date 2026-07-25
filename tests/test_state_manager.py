"""Tests for terminal session state handling."""

from openwaifud.models import AgentStatus


async def test_idle_status_does_not_imply_success_event(state_manager):
    """IDLE is shared by success, cancellation and error cleanup."""
    await state_manager.update_session("s1", status=AgentStatus.CODING)
    await state_manager.update_session("s1", status=AgentStatus.IDLE)

    assert state_manager._queue.empty()
    session = state_manager.get_current_state().sessions[0]
    assert session.status == AgentStatus.IDLE
    assert session.is_done is True


async def test_resync_targets_only_newly_connected_device(state_manager):
    await state_manager.update_session("s1", status=AgentStatus.CODING)

    await state_manager.resync_ble("device-b")

    messages = []
    while not state_manager._queue.empty():
        messages.append(state_manager._queue.get_nowait())
    assert [message["type"] for message in messages] == [
        "sync_begin",
        "session_upsert",
        "session_detail",
        "sync_end",
    ]
    assert all(message["target_device_id"] == "device-b" for message in messages)


async def test_full_queue_drops_state_before_tts_message(state_manager):
    for index in range(10):
        await state_manager._enqueue({"type": "session_upsert", "data": {"session_id": str(index)}})

    await state_manager._enqueue({"type": "tts_start", "priority": "high"})

    messages = []
    while not state_manager._queue.empty():
        messages.append(state_manager._queue.get_nowait())
    assert messages[-1]["type"] == "tts_start"
    assert len(messages) == 10


async def test_full_priority_queue_rejects_incoming_state(state_manager):
    for index in range(10):
        await state_manager._enqueue({"type": f"tts_data_{index}"})

    await state_manager._enqueue({"type": "session_upsert"})

    messages = []
    while not state_manager._queue.empty():
        messages.append(state_manager._queue.get_nowait())
    assert all(message["type"].startswith("tts") for message in messages)
