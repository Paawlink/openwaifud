"""HTTP route handlers for OpenWaifuD API."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiohttp_jinja2
from aiohttp import web
from loguru import logger
from pydantic import ValidationError

from openwaifud.ble.protocol import BLEProtocolError
from openwaifud.chat import ChatNotConfiguredError, ChatService, ChatUpstreamError
from openwaifud.models import (
    ChatConfig,
    ChatRequest,
    ConversationContext,
    DetailUpdate,
    GlobalEvent,
    StatusUpdate,
    WifiProvisionRequest,
)
from openwaifud.state.manager import StateManager

if TYPE_CHECKING:
    from openwaifud.ble.client import BLEClient


def setup_routes(
    app: web.Application,
    state_manager: StateManager,
    ble_client: BLEClient | None = None,
    chat_service: ChatService | None = None,
) -> None:
    """Register all API routes.

    :param ble_client: BLE 客户端（可选）。提供时启用设备列表与 WiFi 配网接口；
        未提供时（如单元测试）相关接口返回 503。
    :param chat_service: 即时对话服务（可选）。未提供时对话相关接口返回 503。
    """
    handlers = APIHandlers(state_manager, ble_client, chat_service)
    app.router.add_get("/", handlers.handle_index)
    app.router.add_post("/api/v1/status", handlers.handle_status)
    app.router.add_post("/api/v1/context", handlers.handle_context)
    app.router.add_post("/api/v1/event", handlers.handle_event)
    app.router.add_post("/api/v1/session/detail", handlers.handle_detail_post)
    app.router.add_get("/api/v1/session/{session_id}/detail", handlers.handle_detail_get)
    app.router.add_get("/api/v1/chat/config", handlers.handle_chat_config_get)
    app.router.add_put("/api/v1/chat/config", handlers.handle_chat_config_put)
    app.router.add_post("/api/v1/chat", handlers.handle_chat)
    app.router.add_get("/api/v1/state", handlers.handle_state)
    app.router.add_get("/api/v1/health", handlers.handle_health)
    app.router.add_get("/api/v1/devices", handlers.handle_devices)
    app.router.add_post("/api/v1/wifi/provision", handlers.handle_wifi_provision)
    app.router.add_post("/api/v1/wifi/forget", handlers.handle_wifi_forget)


class APIHandlers:
    """HTTP request handlers."""

    def __init__(
        self,
        state_manager: StateManager,
        ble_client: BLEClient | None = None,
        chat_service: ChatService | None = None,
    ) -> None:
        self._state_manager = state_manager
        self._ble_client = ble_client
        self._chat_service = chat_service

    async def handle_index(self, request: web.Request) -> web.Response:
        """GET / - 网页端设备管理页（蓝牙设备列表 + WiFi 配网）。"""
        return aiohttp_jinja2.render_template("index.html", request, {})

    async def handle_devices(self, request: web.Request) -> web.Response:
        """GET /api/v1/devices - 已知硬件设备列表（含 BLE/WiFi 状态）。"""
        devices = self._ble_client.get_devices() if self._ble_client is not None else []
        return web.json_response({"devices": devices})

    async def handle_wifi_provision(self, request: web.Request) -> web.Response:
        """POST /api/v1/wifi/provision - 向已连接设备下发 WiFi 凭据。"""
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"error": "Invalid JSON body"},
                status=400,
            )

        try:
            req = WifiProvisionRequest(**data)
        except ValidationError as e:
            return web.json_response(
                {"error": "Validation failed", "details": e.errors()},
                status=422,
            )

        if self._ble_client is None or not self._ble_client.connected:
            return web.json_response(
                {"error": "设备未连接，请先等待蓝牙连接成功"},
                status=503,
            )

        try:
            ok = await self._ble_client.send_wifi_provision(req.ssid, req.password)
        except BLEProtocolError as e:
            return web.json_response(
                {"error": str(e)},
                status=422,
            )
        if not ok:
            return web.json_response(
                {"error": "BLE 写入失败，请重试"},
                status=502,
            )

        logger.info(f'WiFi provision accepted: ssid="{req.ssid}"')
        return web.json_response(
            {"success": True, "ssid": req.ssid},
            status=200,
        )

    async def handle_wifi_forget(self, request: web.Request) -> web.Response:
        """POST /api/v1/wifi/forget - 让设备断开 WiFi 并清除已保存的凭据。"""
        if self._ble_client is None or not self._ble_client.connected:
            return web.json_response(
                {"error": "设备未连接，请先等待蓝牙连接成功"},
                status=503,
            )

        ok = await self._ble_client.send_wifi_forget()
        if not ok:
            return web.json_response(
                {"error": "BLE 写入失败，请重试"},
                status=502,
            )

        logger.info("WiFi forget accepted")
        return web.json_response({"success": True}, status=200)

    async def handle_status(self, request: web.Request) -> web.Response:
        """POST /api/v1/status - Receive agent status update."""
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"error": "Invalid JSON body"},
                status=400,
            )

        try:
            update = StatusUpdate(**data)
        except ValidationError as e:
            return web.json_response(
                {"error": "Validation failed", "details": e.errors()},
                status=422,
            )

        await self._state_manager.update_status(update)
        logger.debug(f"Status updated: {update.status.value}")

        return web.json_response(
            {"success": True, "status": update.status.value},
            status=200,
        )

    async def handle_context(self, request: web.Request) -> web.Response:
        """POST /api/v1/context - Receive conversation context."""
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"error": "Invalid JSON body"},
                status=400,
            )

        try:
            context = ConversationContext(**data)
        except ValidationError as e:
            return web.json_response(
                {"error": "Validation failed", "details": e.errors()},
                status=422,
            )

        await self._state_manager.update_context(context)
        logger.debug(f"Context updated: plugin={context.plugin_type}, session={context.session_id}")

        return web.json_response(
            {"success": True, "session_id": context.session_id},
            status=200,
        )

    async def handle_event(self, request: web.Request) -> web.Response:
        """POST /api/v1/event - 接收全局事件（泳道 2：出错 / 被用户取消等）。"""
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"error": "Invalid JSON body"},
                status=400,
            )

        try:
            event = GlobalEvent(**data)
        except ValidationError as e:
            return web.json_response(
                {"error": "Validation failed", "details": e.errors()},
                status=422,
            )

        await self._state_manager.emit_global_event(event.event, event.message)
        logger.debug(f"Global event received: {event.event.value}")

        return web.json_response(
            {"success": True, "event": event.event.value},
            status=200,
        )

    async def handle_detail_post(self, request: web.Request) -> web.Response:
        """POST /api/v1/session/detail - Receive session detail update."""
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"error": "Invalid JSON body"},
                status=400,
            )

        try:
            update = DetailUpdate(**data)
        except ValidationError as e:
            return web.json_response(
                {"error": "Validation failed", "details": e.errors()},
                status=422,
            )

        await self._state_manager.update_session_detail(update)
        logger.debug(f"Detail updated: session={update.session_id}")

        return web.json_response(
            {"success": True, "session_id": update.session_id},
            status=200,
        )

    async def handle_detail_get(self, request: web.Request) -> web.Response:
        """GET /api/v1/session/{session_id}/detail - Get session detail."""
        session_id = request.match_info.get("session_id", "")
        if not session_id:
            return web.json_response(
                {"error": "Missing session_id"},
                status=400,
            )

        detail = self._state_manager.get_session_detail(session_id)
        if detail is None:
            return web.json_response(
                {"error": "Session not found", "session_id": session_id},
                status=404,
            )
        return web.json_response(detail.model_dump(mode="json"))

    async def handle_chat_config_get(self, request: web.Request) -> web.Response:
        """GET /api/v1/chat/config - 读取对话模型配置（api_key 不回显）。"""
        if self._chat_service is None:
            return web.json_response({"error": "对话服务未启用"}, status=503)
        return web.json_response(self._chat_service.get_public_config())

    async def handle_chat_config_put(self, request: web.Request) -> web.Response:
        """PUT /api/v1/chat/config - 保存对话模型配置（网页端）。

        空 api_key 表示保留已保存的 Key，便于只改 base_url / model。
        """
        if self._chat_service is None:
            return web.json_response({"error": "对话服务未启用"}, status=503)
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"error": "Invalid JSON body"},
                status=400,
            )

        try:
            cfg = ChatConfig(**data)
        except ValidationError as e:
            return web.json_response(
                {"error": "Validation failed", "details": e.errors()},
                status=422,
            )

        self._chat_service.save_config(cfg)
        return web.json_response(
            {"success": True, **self._chat_service.get_public_config()},
        )

    async def handle_chat(self, request: web.Request) -> web.Response:
        """POST /api/v1/chat - 即时对话：单次提问，同步返回模型回复。"""
        if self._chat_service is None:
            return web.json_response({"error": "对话服务未启用"}, status=503)
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"error": "Invalid JSON body"},
                status=400,
            )

        try:
            req = ChatRequest(**data)
        except ValidationError as e:
            return web.json_response(
                {"error": "Validation failed", "details": e.errors()},
                status=422,
            )

        try:
            reply = await self._chat_service.chat(req.message)
        except ChatNotConfiguredError as e:
            return web.json_response({"error": str(e)}, status=503)
        except ChatUpstreamError as e:
            logger.warning(f"Chat upstream error: {e}")
            return web.json_response({"error": str(e)}, status=502)

        return web.json_response({"reply": reply})

    async def handle_state(self, request: web.Request) -> web.Response:
        """GET /api/v1/state - Get current daemon state."""
        state = self._state_manager.get_current_state()
        return web.json_response(state.model_dump(mode="json"))

    async def handle_health(self, request: web.Request) -> web.Response:
        """GET /api/v1/health - Health check."""
        state = self._state_manager.get_current_state()
        return web.json_response(
            {
                "status": "ok",
                "ble_connected": state.ble_connected,
                "uptime_seconds": round(state.uptime_seconds, 2),
            }
        )
