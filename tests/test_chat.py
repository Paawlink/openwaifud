"""Tests for openwaifud.chat（即时对话服务、system prompt 与 HTTP 端点）。"""

import pytest
from aiohttp import web

from openwaifud.api.handlers import setup_routes
from openwaifud.chat import ChatService, build_system_prompt
from openwaifud.models import (
    AgentStatus,
    ChatConfig,
    ChatMessage,
    DaemonState,
    SessionDetail,
    SessionInfo,
)


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
        """配置好上游后，同步返回模型回复，并带上 Bearer 认证与 system prompt。"""
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
        messages = captured["body"]["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "涂鸦" in messages[0]["content"]
        assert messages[1] == {"role": "user", "content": "你好呀"}

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


class TestSystemPrompt:
    """「涂鸦」的 system prompt 构建与状态注入。"""

    def test_without_state_contains_persona_and_fallback(self):
        prompt = build_system_prompt(None)
        assert "涂鸦" in prompt
        assert "暂时读取不到工作状态" in prompt

    def test_idle_state_renders_overview(self):
        state = DaemonState(ble_connected=True, uptime_seconds=90.0)
        prompt = build_system_prompt(state)
        assert "总体状态：空闲" in prompt
        assert "蓝牙连接" not in prompt
        assert "守护进程已运行" not in prompt
        assert "1分钟" not in prompt
        assert "暂无" in prompt

    def test_prompt_requires_matching_language_and_short_reply(self):
        prompt = build_system_prompt(None)
        assert "使用用户的语言" in prompt
        assert "64 个字符" in prompt

    def test_sessions_rendered_with_task_and_error(self):
        state = DaemonState(
            agent_status=AgentStatus.CODING,
            sessions=[
                SessionInfo(
                    session_id="s1",
                    plugin_type="opencode",
                    status=AgentStatus.CODING,
                    current_task="重构登录模块",
                    elapsed_seconds=125.0,
                ),
                SessionInfo(
                    session_id="s2",
                    plugin_type="claudecode",
                    status=AgentStatus.ERROR,
                    error_message="编译失败",
                    elapsed_seconds=30.0,
                ),
            ],
        )
        prompt = build_system_prompt(state)
        assert "总体状态：编写代码中" in prompt
        assert "共 2 个" in prompt
        assert "来自 opencode 的会话" in prompt
        assert "已运行" not in prompt
        assert "当前任务：重构登录模块" in prompt
        assert "错误信息：编译失败" in prompt

    def test_details_render_metadata_and_chat_context(self):
        """传入会话详情时，工作目录、元数据与对话摘要全量注入 prompt。"""
        state = DaemonState(
            agent_status=AgentStatus.CODING,
            sessions=[
                SessionInfo(session_id="s1", plugin_type="opencode", status=AgentStatus.CODING),
            ],
        )
        details = [
            SessionDetail(
                session_id="s1",
                plugin_type="opencode",
                metadata={"directory": "/home/me/proj", "agent": "build"},
                chat_context=[
                    ChatMessage(role="user", content="帮我修登录 bug"),
                    ChatMessage(role="assistant", content="好的，正在排查 auth 模块"),
                ],
            ),
        ]
        prompt = build_system_prompt(state, details)
        assert "工作目录：/home/me/proj" in prompt
        assert "Agent：build" in prompt
        assert "该会话最近的对话摘要" in prompt
        assert "[主人] 帮我修登录 bug" in prompt
        assert "[Agent] 好的，正在排查 auth 模块" in prompt

    def test_details_missing_session_only_renders_overview(self):
        """详情中缺失的会话仅展示概览行，不报错。"""
        state = DaemonState(
            sessions=[SessionInfo(session_id="s1", plugin_type="codex")],
        )
        prompt = build_system_prompt(state, [])
        assert "来自 codex 的会话" in prompt
        assert "该会话最近的对话摘要" not in prompt

    async def test_chat_injects_state_from_provider(self, tmp_path, aiohttp_server):
        """接入 state_provider 后，上游收到的 system prompt 包含实时状态。"""
        captured: dict = {}

        async def completions(request: web.Request) -> web.Response:
            captured["body"] = await request.json()
            return web.json_response({"choices": [{"message": {"content": "ok"}}]})

        upstream = web.Application()
        upstream.router.add_post("/v1/chat/completions", completions)
        server = await aiohttp_server(upstream)

        state = DaemonState(
            agent_status=AgentStatus.TESTING,
            sessions=[
                SessionInfo(session_id="s1", status=AgentStatus.TESTING, current_task="跑单测")
            ],
        )
        service = ChatService(config_path=tmp_path / "chat.json", state_provider=lambda: state)
        service.save_config(
            ChatConfig(base_url=f"http://{server.host}:{server.port}/v1", api_key="", model="m")
        )
        assert await service.chat("在忙什么？") == "ok"
        system_content = captured["body"]["messages"][0]["content"]
        assert "总体状态：运行测试中" in system_content
        assert "跑单测" in system_content

    async def test_chat_injects_details_from_provider(self, tmp_path, aiohttp_server):
        """接入 details_provider 后，上游收到的 system prompt 包含工作目录与对话上下文。"""
        captured: dict = {}

        async def completions(request: web.Request) -> web.Response:
            captured["body"] = await request.json()
            return web.json_response({"choices": [{"message": {"content": "ok"}}]})

        upstream = web.Application()
        upstream.router.add_post("/v1/chat/completions", completions)
        server = await aiohttp_server(upstream)

        state = DaemonState(
            agent_status=AgentStatus.CODING,
            sessions=[SessionInfo(session_id="s1", status=AgentStatus.CODING)],
        )
        details = [
            SessionDetail(
                session_id="s1",
                metadata={"directory": "/tmp/demo"},
                chat_context=[ChatMessage(role="user", content="重构登录模块")],
            ),
        ]
        service = ChatService(
            config_path=tmp_path / "chat.json",
            state_provider=lambda: state,
            details_provider=lambda: details,
        )
        service.save_config(
            ChatConfig(base_url=f"http://{server.host}:{server.port}/v1", api_key="", model="m")
        )
        assert await service.chat("在哪个项目干活？") == "ok"
        system_content = captured["body"]["messages"][0]["content"]
        assert "工作目录：/tmp/demo" in system_content
        assert "[主人] 重构登录模块" in system_content

    async def test_details_provider_failure_keeps_overview(self, tmp_path, aiohttp_server):
        """details_provider 抛异常时降级为仅注入概览，对话不受影响。"""
        captured: dict = {}

        async def completions(request: web.Request) -> web.Response:
            captured["body"] = await request.json()
            return web.json_response({"choices": [{"message": {"content": "ok"}}]})

        upstream = web.Application()
        upstream.router.add_post("/v1/chat/completions", completions)
        server = await aiohttp_server(upstream)

        def broken_details() -> list[SessionDetail]:
            raise RuntimeError("boom")

        state = DaemonState(
            sessions=[SessionInfo(session_id="s1", current_task="跑单测")],
        )
        service = ChatService(
            config_path=tmp_path / "chat.json",
            state_provider=lambda: state,
            details_provider=broken_details,
        )
        service.save_config(
            ChatConfig(base_url=f"http://{server.host}:{server.port}/v1", api_key="", model="m")
        )
        assert await service.chat("hi") == "ok"
        system_content = captured["body"]["messages"][0]["content"]
        assert "跑单测" in system_content

    async def test_provider_failure_falls_back(self, tmp_path, aiohttp_server):
        """state_provider 抛异常时降级为纯人设 prompt，对话不受影响。"""
        captured: dict = {}

        async def completions(request: web.Request) -> web.Response:
            captured["body"] = await request.json()
            return web.json_response({"choices": [{"message": {"content": "ok"}}]})

        upstream = web.Application()
        upstream.router.add_post("/v1/chat/completions", completions)
        server = await aiohttp_server(upstream)

        def broken_provider() -> DaemonState:
            raise RuntimeError("boom")

        service = ChatService(config_path=tmp_path / "chat.json", state_provider=broken_provider)
        service.save_config(
            ChatConfig(base_url=f"http://{server.host}:{server.port}/v1", api_key="", model="m")
        )
        assert await service.chat("hi") == "ok"
        system_content = captured["body"]["messages"][0]["content"]
        assert "涂鸦" in system_content
        assert "暂时读取不到工作状态" in system_content


class TestChatHistory:
    """短期多轮历史：支撑自然的多轮闲聊。"""

    async def test_history_carried_to_next_turn(self, tmp_path, aiohttp_server):
        requests_seen: list[dict] = []

        async def completions(request: web.Request) -> web.Response:
            requests_seen.append(await request.json())
            return web.json_response(
                {"choices": [{"message": {"content": f"回复{len(requests_seen)}"}}]}
            )

        upstream = web.Application()
        upstream.router.add_post("/v1/chat/completions", completions)
        server = await aiohttp_server(upstream)

        service = ChatService(config_path=tmp_path / "chat.json")
        service.save_config(
            ChatConfig(base_url=f"http://{server.host}:{server.port}/v1", api_key="", model="m")
        )

        assert await service.chat("第一句") == "回复1"
        assert await service.chat("第二句") == "回复2"

        # 第二次请求带上了第一轮的 user/assistant 历史
        second_messages = requests_seen[1]["messages"]
        assert [m["role"] for m in second_messages] == ["system", "user", "assistant", "user"]
        assert second_messages[1]["content"] == "第一句"
        assert second_messages[2]["content"] == "回复1"
        assert second_messages[3]["content"] == "第二句"

    async def test_reply_is_trimmed_to_64_characters(self, tmp_path, aiohttp_server):
        async def completions(request: web.Request) -> web.Response:
            return web.json_response({"choices": [{"message": {"content": "x" * 100}}]})

        upstream = web.Application()
        upstream.router.add_post("/v1/chat/completions", completions)
        server = await aiohttp_server(upstream)
        service = ChatService(config_path=tmp_path / "chat.json")
        service.save_config(ChatConfig(base_url=f"http://{server.host}:{server.port}/v1", model="m"))

        assert await service.chat("hi") == "x" * 64


class TestChatDisabled:
    """未注入 ChatService 时对话相关接口返回 503。"""

    async def test_endpoints_return_503(self, aiohttp_client, state_manager):
        application = web.Application()
        setup_routes(application, state_manager)
        cli = await aiohttp_client(application)
        assert (await cli.get("/api/v1/chat/config")).status == 503
        assert (await cli.post("/api/v1/chat", json={"message": "hi"})).status == 503
