"""HTTP server for OpenWaifuD API."""

from __future__ import annotations

from aiohttp import web
from loguru import logger

from openwaifud.api.handlers import setup_routes
from openwaifud.state.manager import StateManager


class HTTPServer:
    """aiohttp-based HTTP server for receiving IDE plugin updates."""

    def __init__(self, state_manager: StateManager, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._state_manager = state_manager
        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        setup_routes(self._app, state_manager)

    async def start(self) -> None:
        """Start the HTTP server."""
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        logger.info(f"HTTP server started on http://{self._host}:{self._port}")

    async def stop(self) -> None:
        """Stop the HTTP server gracefully."""
        if self._runner:
            await self._runner.cleanup()
            logger.info("HTTP server stopped")

    @property
    def app(self) -> web.Application:
        """Expose app for testing."""
        return self._app
