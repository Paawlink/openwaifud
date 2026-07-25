"""Tests for openwaifud.api (HTTP handlers)."""

from pathlib import Path

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


class TestSessionCreatePending:
    """Tests for GET /api/v1/session/create/pending（指令由对话技能登记）。"""

    @staticmethod
    async def _register_instance(cli, instance_id="inst-1", directory=""):
        """用一次轮询登记插件实例心跳（模拟存活的 OpenCode 实例）。"""
        params = {"instance_id": instance_id}
        if directory:
            params["directory"] = directory
        resp = await cli.get("/api/v1/session/create/pending", params=params)
        assert resp.status == 200

    async def test_request_without_live_instance_returns_none(self, client, state_manager):
        """目标实例不存在或已离线时，request_session_create 返回 None。"""
        await client
        assert state_manager.request_session_create("ghost", "hi") is None

    async def test_request_uses_instance_directory(self, client, state_manager):
        """指令定向给指定实例，目录为该实例自己上报的工作区。"""
        cli = await client
        await self._register_instance(cli, "inst-1", "/tmp/workspace-a")
        pending = state_manager.request_session_create("inst-1", "帮我修 bug")
        assert pending is not None
        assert pending.instance_id == "inst-1"
        assert pending.directory == "/tmp/workspace-a"
        assert pending.prompt == "帮我修 bug"

    async def test_request_without_directory_falls_back_to_home(self, client, state_manager):
        """实例未上报工作区时，目录回退到用户主目录。"""
        cli = await client
        await self._register_instance(cli, "inst-1")
        pending = state_manager.request_session_create("inst-1")
        assert pending is not None
        assert pending.directory == str(Path.home())

    async def test_only_target_instance_claims_request(self, client, state_manager):
        """多实例时指令只定向给用户确认的那一个，其他实例领不到。"""
        cli = await client
        await self._register_instance(cli, "inst-a", "/tmp/a")
        await self._register_instance(cli, "inst-b", "/tmp/b")
        pending = state_manager.request_session_create("inst-a", "hi")
        assert pending is not None
        assert pending.instance_id == "inst-a"

        # 非目标实例领不到定向指令
        resp_b = await cli.get(
            "/api/v1/session/create/pending", params={"instance_id": "inst-b"}
        )
        assert (await resp_b.json())["requests"] == []
        # 目标实例能领取
        resp_a = await cli.get(
            "/api/v1/session/create/pending", params={"instance_id": "inst-a"}
        )
        requests = (await resp_a.json())["requests"]
        assert len(requests) == 1
        assert requests[0]["prompt"] == "hi"

    async def test_pending_is_consumed_once(self, client, state_manager):
        """pending 为消费式读取：每条指令只返回一次。"""
        cli = await client
        await self._register_instance(cli, "inst-1", "/tmp/ws")
        assert state_manager.request_session_create("inst-1", "hello") is not None
        resp = await cli.get(
            "/api/v1/session/create/pending", params={"instance_id": "inst-1"}
        )
        assert resp.status == 200
        data = await resp.json()
        assert len(data["requests"]) == 1
        assert data["requests"][0]["prompt"] == "hello"

        resp2 = await cli.get(
            "/api/v1/session/create/pending", params={"instance_id": "inst-1"}
        )
        data2 = await resp2.json()
        assert data2["requests"] == []

    async def test_pending_without_instance_id_returns_400(self, client):
        """缺 instance_id 时返回 400（旧版插件不再兼容）。"""
        cli = await client
        resp = await cli.get("/api/v1/session/create/pending")
        assert resp.status == 400

    async def test_list_live_instances_reflects_heartbeats(self, client, state_manager):
        """list_live_instances 按注册顺序返回存活实例（供 system prompt 注入）。"""
        cli = await client
        await self._register_instance(cli, "inst-a", "/tmp/a")
        await self._register_instance(cli, "inst-b", "/tmp/b")
        instances = state_manager.list_live_instances()
        assert [i.instance_id for i in instances] == ["inst-a", "inst-b"]
        assert instances[0].directory == "/tmp/a"


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


class TestWifiProvisionEndpoint:
    """Tests for POST /api/v1/wifi/provision."""

    async def test_invalid_json_returns_400(self, client):
        cli = await client
        resp = await cli.post(
            "/api/v1/wifi/provision",
            data=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400

    async def test_empty_ssid_returns_422(self, client):
        cli = await client
        resp = await cli.post("/api/v1/wifi/provision", json={"ssid": "", "password": "x"})
        assert resp.status == 422
        data = await resp.json()
        assert data["error"] == "Validation failed"

    async def test_no_ble_client_returns_503(self, client):
        """未提供 ble_client（或未连接）时返回 503。"""
        cli = await client
        resp = await cli.post("/api/v1/wifi/provision", json={"ssid": "MyWiFi", "password": "secret"})
        assert resp.status == 503
        data = await resp.json()
        assert "error" in data


class TestWifiForgetEndpoint:
    """Tests for POST /api/v1/wifi/forget."""

    async def test_no_ble_client_returns_503(self, client):
        """未提供 ble_client（或未连接）时返回 503。"""
        cli = await client
        resp = await cli.post("/api/v1/wifi/forget")
        assert resp.status == 503
        data = await resp.json()
        assert "error" in data
