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
