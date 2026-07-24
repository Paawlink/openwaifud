"""Realtime file system watcher for AI agent conversations."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

from loguru import logger
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from openwaifud.config import Config
from openwaifud.realtime.extractors import StatusExtractor
from openwaifud.realtime.parsers import (
    BaseParser,
    ClaudeCodeParser,
    CodexParser,
    OpenCodeParser,
)
from openwaifud.state.manager import StateManager


class _JSONLFileHandler(FileSystemEventHandler):
    """Watchdog event handler that forwards .jsonl file changes to asyncio loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop, callback: Callable[[Path], None]) -> None:
        super().__init__()
        self._loop = loop
        self._callback = callback

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if str(event.src_path).endswith(".jsonl"):
            self._loop.call_soon_threadsafe(self._callback, Path(os.fsdecode(event.src_path)))

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if str(event.src_path).endswith(".jsonl"):
            self._loop.call_soon_threadsafe(self._callback, Path(os.fsdecode(event.src_path)))


class RealtimeWatcher:
    """Watches AI agent conversation files and syncs status to StateManager.

    Uses watchdog for OS-level file watching with debouncing.
    """

    def __init__(self, config: Config, state_manager: StateManager) -> None:
        self._config = config
        self._state_manager = state_manager
        self._extractor = StatusExtractor()
        self._parsers: list[BaseParser] = [
            ClaudeCodeParser(),
            CodexParser(),
            OpenCodeParser(),
        ]
        self._observer: BaseObserver | None = None
        self._offsets: dict[Path, int] = {}
        self._debounce_tasks: dict[Path, asyncio.TimerHandle] = {}
        self._running = False

    async def start(self) -> None:
        """Start watching configured directories."""
        if not self._config.realtime_enabled:
            logger.info("Realtime watcher disabled by config")
            return

        self._running = True
        loop = asyncio.get_running_loop()

        # Create watchdog observer
        observer = Observer()
        self._observer = observer
        handler = _JSONLFileHandler(loop, self._on_file_event)

        # Register watch paths from each parser
        watch_count = 0
        for parser in self._parsers:
            for watch_path in parser.get_watch_paths():
                try:
                    observer.schedule(handler, str(watch_path), recursive=True)
                    watch_count += 1
                    logger.debug(f"Watching: {watch_path}")
                except Exception as e:
                    logger.warning(f"Failed to watch {watch_path}: {e}")

        if watch_count == 0:
            logger.info("No agent directories found, realtime watcher idle")
            self._observer = None
            return

        # Start observer thread
        observer.start()
        logger.info(f"Realtime watcher started, monitoring {watch_count} path(s)")

        # Initial scan for active sessions
        await self._initial_scan()

    async def stop(self) -> None:
        """Stop watching and clean up."""
        self._running = False

        # Cancel pending debounce timers
        for handle in self._debounce_tasks.values():
            handle.cancel()
        self._debounce_tasks.clear()

        # Stop watchdog observer
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5.0)
            self._observer = None

        logger.info("Realtime watcher stopped")

    def _on_file_event(self, file_path: Path) -> None:
        """Called from watchdog thread via call_soon_threadsafe. Schedules debounced processing."""
        if not self._running:
            return

        # Cancel existing debounce timer for this file
        if file_path in self._debounce_tasks:
            self._debounce_tasks[file_path].cancel()

        # Schedule processing after debounce delay
        loop = asyncio.get_running_loop()
        delay = self._config.realtime_debounce_ms / 1000.0
        handle = loop.call_later(delay, self._schedule_process, file_path)
        self._debounce_tasks[file_path] = handle

    def _schedule_process(self, file_path: Path) -> None:
        """Create async task to process a file after debounce."""
        self._debounce_tasks.pop(file_path, None)
        asyncio.create_task(self._process_file(file_path))

    async def _process_file(self, file_path: Path) -> None:
        """Parse file and update state manager."""
        if not self._running:
            return

        # Find matching parser
        parser = self._find_parser_for_path(file_path)
        if parser is None:
            return

        # Get current offset
        offset = self._offsets.get(file_path, 0)

        # Parse
        result = parser.parse_latest(file_path, offset)
        if result is None:
            return

        # Update offset
        self._offsets[file_path] = result.new_offset

        # Extract status and context
        try:
            update, context = self._extractor.extract(result)
            await self._state_manager.update_status(update)
            await self._state_manager.update_context(context)
            logger.debug(
                f"Realtime update: {result.plugin_type} [{update.status.value}] session={result.session_id[:8]}..."
            )
        except Exception as e:
            logger.error(f"Realtime extraction/update error: {e}")

    def _find_parser_for_path(self, file_path: Path) -> BaseParser | None:
        """Find which parser handles the given file path."""
        file_str = str(file_path)
        for parser in self._parsers:
            for watch_path in parser.get_watch_paths():
                if file_str.startswith(str(watch_path)):
                    return parser
        return None

    async def _initial_scan(self) -> None:
        """Scan for currently active sessions on startup."""
        for parser in self._parsers:
            try:
                result = parser.find_active_session()
                if result is not None:
                    file_path, offset = result
                    self._offsets.setdefault(file_path, offset)
                    await self._process_file(file_path)
            except Exception as e:
                logger.debug(f"Initial scan error for {type(parser).__name__}: {e}")
