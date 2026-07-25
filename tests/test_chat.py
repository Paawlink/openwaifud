"""Tests for openwaifud.chat（即时对话服务与 HTTP 端点）。"""

import pytest
from aiohttp import web

from openwaifud.api.handlers import setup_routes
from openwaifud.chat import ChatService
from openwaifud.models import ChatConfig


@pytest.fixture
def chat_service(tmp_path):
    """使用临时配置文件的 ChatService。"""
    return ChatService(config_path=tmp_path / "chat.json")


@pytest.fixture
def app(state_manager, chat_service):
    """带对话服务的 aiohttp app。"""
    application = web.Application()
    setup_routes(application, state_manager, chat_service=chat_service)
    return application


@pytest.fixture
def client(aiohttp_client, app):
    return aiohttp_client(app)


@pytest.fixture
def fake_upstream(aiohttp_server):
    """OpenAI 兼容的假上游：记录请求并返回固定回复。"""

    captured: dict = {}

    async def completions(request: web.Request) -> web.Response:
        captured["auth"] = request.headers.get("Authorization", "")
        captured["body"] = await request.json()
        return web.json_response(
            {"choices": [{"message": {"role": "assistant", "content": "喵～你好！"}}]}
        )

    upstream = web.Application()
    upstream.router.add_post("/v1/chat/completions", completions)

    async def start():
        server = await aiohttp_server(upstream)
        return server, captured

    return start


class TestChatConfig:
    """配置的持久化与公开视图。"""

    def test_defaults_unconfigured(self, chat_service):
        assert not chat_service.configured
        cfg = chat_service.get_public_config()
        assert cfg == {"base_url": "", "model": "", "api_key_set": False}

    def test_save_and_reload(self, tmp_path):
        path = tmp_path / "chat.json"
        service = ChatService(config_path=path)
        service.save_config(
            ChatConfig(base_url="https://api.example.com/v1/", api_key="sk-test", model="gpt-x")
        )
        # 末尾斜杠被规范化掉
        assert service.get_public_config()["base_url"] == "https://api.example.com/v1"

        # 重新加载后配置仍在（含 api_key）
        reloaded = ChatService(config_path=path)
        assert reloaded.configured
        assert reloaded.get_public_config() == {
            "base_url": "https://api.example.com/v1",
            "model": "gpt-x",
            "api_key_set": True,
        }

    def test_empty_api_key_keeps_existing(self, tmp_path):
        service = ChatService(config_path=tmp_path / "chat.json")
        service.save_config(ChatConfig(base_url="https://a/v1", api_key="sk-1", model="m1"))
        service.save_config(ChatConfig(base_url="https://a/v1", api_key="", model="m2"))
        cfg = service.get_public_config()
        assert cfg["model"] == "m2"
        assert cfg["api_key_set"] is True


class TestChatConfigEndpoint:
    """GET/PUT /api/v1/chat/config。"""

    async def test_get_returns_masked_config(self, client):
        cli = await client
        resp = await cli.get("/api/v1/chat/config")
        assert resp.status == 200
        data = await resp.json()
        assert data["api_key_set"] is False
        assert "api_key" not in data

    async def test_put_saves_config(self, client):
        cli = await client
        resp = await cli.put(
            "/api/v1/chat/config",
            json={"base_url": "https://api.example.com/v1", "api_key": "sk-1", "model": "gpt-x"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["base_url"] == "https://api.example.com/v1"
        assert data["api_key_set"] is True

    async def test_put_invalid_json_returns_400(self, client):
        cli = await client
        resp = await cli.put(
            "/api/v1/chat/config",
            data=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400


class TestChatEndpoint:
    """POST /api/v1/chat。"""

    async def test_unconfigured_returns_503(self, client):
        cli = await client
        resp = await cli.post("/api/v1/chat", json={"message": "你好"})
        assert resp.status == 503

    async def test_empty_message_returns_422(self, client):
        cli = await client
        resp = await cli.post("/api/v1/chat", json={"message": ""})
        assert resp.status == 422

    async def test_chat_returns_upstream_reply(self, client, chat_service, fake_upstream):
        """配置好上游后，同步返回模型回复，并带上 Bearer 认证。"""
        server, captured = await fake_upstream()
        chat_service.save_config(
            ChatConfig(
                base_url=f"http://{server.host}:{server.port}/v1",
                api_key="sk-test",
                model="gpt-x",
            )
        )
        cli = await client
        resp = await cli.post("/api/v1/chat", json={"message": "你好呀"})
        assert resp.status == 200
        data = await resp.json()
        assert data["reply"] == "喵～你好！"
        assert captured["auth"] == "Bearer sk-test"
        assert captured["body"]["model"] == "gpt-x"
        assert captured["body"]["messages"] == [{"role": "user", "content": "你好呀"}]

    async def test_upstream_error_returns_502(self, client, chat_service, aiohttp_server):
        """上游返回非 200 时映射为 502。"""

        async def broken(request: web.Request) -> web.Response:
            return web.json_response({"error": "boom"}, status=500)

        upstream = web.Application()
        upstream.router.add_post("/v1/chat/completions", broken)
        server = await aiohttp_server(upstream)

        chat_service.save_config(
            ChatConfig(base_url=f"http://{server.host}:{server.port}/v1", api_key="", model="m")
        )
        cli = await client
        resp = await cli.post("/api/v1/chat", json={"message": "hi"})
        assert resp.status == 502


class TestChatDisabled:
    """未注入 ChatService 时对话相关接口返回 503。"""

    async def test_endpoints_return_503(self, aiohttp_client, state_manager):
        application = web.Application()
        setup_routes(application, state_manager)
        cli = await aiohttp_client(application)
        assert (await cli.get("/api/v1/chat/config")).status == 503
        assert (await cli.post("/api/v1/chat", json={"message": "hi"})).status == 503
