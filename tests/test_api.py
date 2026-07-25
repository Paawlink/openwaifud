"""Tests for openwaifud.api (HTTP handlers)."""

import asyncio

import pytest
from aiohttp import web

from openwaifud.api.handlers import setup_routes


@pytest.fixture
def app(state_manager):
    """Create an aiohttp app with routes registered."""
    application = web.Application()
    setup_routes(application, state_manager)
    return application


@pytest.fixture
def client(aiohttp_client, app):
    """Create a test client for the app."""
    return aiohttp_client(app)


class TestStatusEndpoint:
    """Tests for POST /api/v1/status."""

    async def test_valid_status_returns_200(self, client):
        """POST /api/v1/status with valid data returns 200."""
        cli = await client
        resp = await cli.post("/api/v1/status", json={"status": "coding"})
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["status"] == "coding"

    async def test_invalid_json_returns_400(self, client):
        """POST /api/v1/status with invalid JSON returns 400."""
        cli = await client
        resp = await cli.post(
            "/api/v1/status",
            data=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400
        data = await resp.json()
        assert "error" in data

    async def test_invalid_field_returns_422(self, client):
        """POST /api/v1/status with invalid status value returns 422."""
        cli = await client
        resp = await cli.post("/api/v1/status", json={"status": "invalid_value"})
        assert resp.status == 422
        data = await resp.json()
        assert "error" in data
        assert data["error"] == "Validation failed"


class TestContextEndpoint:
    """Tests for POST /api/v1/context."""

    async def test_valid_context_returns_200(self, client):
        """POST /api/v1/context with valid data returns 200."""
        cli = await client
        resp = await cli.post(
            "/api/v1/context",
            json={
                "plugin_type": "opencode",
                "session_id": "test-session-123",
                "current_task": "Fix the bug",
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["session_id"] == "test-session-123"


class TestEventEndpoint:
    """Tests for POST /api/v1/event（泳道 2：全局事件）。"""

    async def test_valid_event_returns_200(self, client):
        """POST /api/v1/event with valid data returns 200."""
        cli = await client
        resp = await cli.post(
            "/api/v1/event",
            json={"event": "error", "session_id": "s1", "message": "boom"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["event"] == "error"

    async def test_cancel_event_returns_200(self, client):
        """cancel 事件同样被接受。"""
        cli = await client
        resp = await cli.post("/api/v1/event", json={"event": "cancel"})
        assert resp.status == 200
        data = await resp.json()
        assert data["event"] == "cancel"

    async def test_done_event_returns_200(self, client):
        """正常完成可由上报端显式发送 done 事件。"""
        cli = await client
        resp = await cli.post("/api/v1/event", json={"event": "done", "session_id": "s1"})
        assert resp.status == 200
        data = await resp.json()
        assert data["event"] == "done"

    async def test_invalid_json_returns_400(self, client):
        """POST /api/v1/event with invalid JSON returns 400."""
        cli = await client
        resp = await cli.post(
            "/api/v1/event",
            data=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400
        data = await resp.json()
        assert "error" in data

    async def test_invalid_field_returns_422(self, client):
        """POST /api/v1/event with invalid event value returns 422."""
        cli = await client
        resp = await cli.post("/api/v1/event", json={"event": "nope"})
        assert resp.status == 422
        data = await resp.json()
        assert data["error"] == "Validation failed"


class TestStateEndpoint:
    """Tests for GET /api/v1/state."""

    async def test_get_state_returns_200(self, client):
        """GET /api/v1/state returns 200 with correct structure."""
        cli = await client
        resp = await cli.get("/api/v1/state")
        assert resp.status == 200
        data = await resp.json()
        assert "agent_status" in data
        assert "ble_connected" in data
        assert "uptime_seconds" in data
        assert "timestamp" in data
        assert data["agent_status"] == "idle"
        assert data["ble_connected"] is False


class TestStateResetEndpoint:
    """Tests for POST /api/v1/state/reset."""

    async def test_reset_clears_all_sessions(self, client):
        """重置后会话列表为空，整体状态回到 idle。"""
        cli = await client
        await cli.post("/api/v1/status", json={"status": "coding", "session_id": "s1"})
        await cli.post("/api/v1/status", json={"status": "thinking", "session_id": "s2"})

        resp = await cli.post("/api/v1/state/reset")
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["cleared_sessions"] == 2

        state_resp = await cli.get("/api/v1/state")
        state = await state_resp.json()
        assert state["sessions"] == []
        assert state["agent_status"] == "idle"
        assert state["context"] is None

    async def test_reset_with_no_sessions_returns_zero(self, client):
        """无会话时重置也正常返回，清除数为 0。"""
        cli = await client
        resp = await cli.post("/api/v1/state/reset")
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["cleared_sessions"] == 0


class TestDetailEndpoint:
    """Tests for POST /api/v1/session/detail and GET /api/v1/session/{id}/detail."""

    async def test_post_detail_returns_200(self, client):
        """POST /api/v1/session/detail with valid data returns 200."""
        cli = await client
        resp = await cli.post(
            "/api/v1/session/detail",
            json={
                "session_id": "detail-test-1",
                "metadata": {"source": "test", "directory": "/tmp"},
                "chat_context": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi there"},
                ],
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["session_id"] == "detail-test-1"

    async def test_post_detail_invalid_json_returns_400(self, client):
        """POST /api/v1/session/detail with invalid JSON returns 400."""
        cli = await client
        resp = await cli.post(
            "/api/v1/session/detail",
            data=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400

    async def test_post_detail_missing_session_id_returns_422(self, client):
        """POST /api/v1/session/detail without session_id returns 422."""
        cli = await client
        resp = await cli.post(
            "/api/v1/session/detail",
            json={"metadata": {"key": "value"}},
        )
        assert resp.status == 422

    async def test_get_detail_returns_200(self, client):
        """GET /api/v1/session/{id}/detail returns 200 after posting detail."""
        cli = await client
        # First post some detail data
        await cli.post(
            "/api/v1/session/detail",
            json={
                "session_id": "get-detail-test",
                "metadata": {"source": "test"},
                "chat_context": [{"role": "user", "content": "hi"}],
            },
        )
        # Then GET it
        resp = await cli.get("/api/v1/session/get-detail-test/detail")
        assert resp.status == 200
        data = await resp.json()
        assert data["session_id"] == "get-detail-test"
        assert data["metadata"]["source"] == "test"
        assert len(data["chat_context"]) == 1
        assert data["chat_context"][0]["role"] == "user"

    async def test_get_detail_not_found_returns_404(self, client):
        """GET /api/v1/session/{id}/detail for unknown session returns 404."""
        cli = await client
        resp = await cli.get("/api/v1/session/nonexistent/detail")
        assert resp.status == 404


class TestHealthEndpoint:
    """Tests for GET /api/v1/health."""

    async def test_health_returns_200(self, client):
        """GET /api/v1/health returns 200 with expected fields."""
        cli = await client
        resp = await cli.get("/api/v1/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert "ble_connected" in data
        assert "uptime_seconds" in data


class TestDevicesEndpoint:
    """Tests for GET /api/v1/devices."""

    async def test_no_ble_client_returns_empty_list(self, client):
        """未提供 ble_client 时设备列表为空。"""
        cli = await client
        resp = await cli.get("/api/v1/devices")
        assert resp.status == 200
        data = await resp.json()
        assert data["devices"] == []


class TestMockAgentEndpoint:
    """Tests for the in-memory Mock Agent controls."""

    async def test_default_config_is_disabled(self, client):
        cli = await client
        resp = await cli.get("/api/v1/mock-agent/config")
        assert resp.status == 200
        data = await resp.json()
        assert data == {
            "enabled": False,
            "interval": 5.0,
            "repeat": 0,
            "sessions": 1,
            "task": "",
            "status": None,
            "error_message": "请求上游接口超时，重试 3 次后仍未恢复",
            "seed": None,
            "running": False,
        }

    async def test_enable_and_disable_mock_agent(self, client):
        cli = await client
        resp = await cli.put(
            "/api/v1/mock-agent/config",
            json={"enabled": True, "interval": 30, "repeat": 0, "sessions": 2, "task": "演示任务", "seed": 7},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["enabled"] is True
        assert data["running"] is True
        assert data["sessions"] == 2

        resp = await cli.put(
            "/api/v1/mock-agent/config",
            json={"enabled": False, "interval": 3, "repeat": 2, "sessions": 1, "task": "", "seed": None},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["enabled"] is False
        assert data["running"] is False

    async def test_invalid_config_returns_422(self, client):
        cli = await client
        resp = await cli.put("/api/v1/mock-agent/config", json={"enabled": True, "sessions": 0})
        assert resp.status == 422
        data = await resp.json()
        assert data["error"] == "Validation failed"

    async def test_one_shot_error_publishes_session_detail(self, client):
        cli = await client
        resp = await cli.put(
            "/api/v1/mock-agent/config",
            json={
                "enabled": True,
                "status": "error",
                "task": "模拟失败任务",
                "error_message": "模拟上游失败",
            },
        )
        assert resp.status == 200
        await asyncio.sleep(0)

        state_resp = await cli.get("/api/v1/state")
        state = await state_resp.json()
        assert len(state["sessions"]) == 1
        session = state["sessions"][0]
        assert session["status"] == "error"
        assert session["current_task"] == "模拟失败任务"
        assert session["error_message"] == "模拟上游失败"

        detail_resp = await cli.get(f"/api/v1/session/{session['session_id']}/detail")
        detail = await detail_resp.json()
        assert detail["metadata"]["demo"] is True
        assert detail["chat_context"][-1]["content"] == "模拟上游失败"
