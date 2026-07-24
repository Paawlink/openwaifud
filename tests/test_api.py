"""Tests for openwaifud.api (HTTP handlers)."""

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
