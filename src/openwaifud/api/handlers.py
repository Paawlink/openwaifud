"""HTTP route handlers for OpenWaifuD API."""

from __future__ import annotations

from aiohttp import web
from loguru import logger
from pydantic import ValidationError

from openwaifud.models import ConversationContext, GlobalEvent, StatusUpdate
from openwaifud.state.manager import StateManager


def setup_routes(app: web.Application, state_manager: StateManager) -> None:
    """Register all API routes."""
    handlers = APIHandlers(state_manager)
    app.router.add_post("/api/v1/status", handlers.handle_status)
    app.router.add_post("/api/v1/context", handlers.handle_context)
    app.router.add_post("/api/v1/event", handlers.handle_event)
    app.router.add_get("/api/v1/state", handlers.handle_state)
    app.router.add_get("/api/v1/health", handlers.handle_health)


class APIHandlers:
    """HTTP request handlers."""

    def __init__(self, state_manager: StateManager) -> None:
        self._state_manager = state_manager

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
