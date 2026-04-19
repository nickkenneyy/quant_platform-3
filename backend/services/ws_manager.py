"""WebSocket connection manager — broadcasts scan results to all connected clients."""

from __future__ import annotations

import json
import asyncio
from typing import Any

from fastapi import WebSocket
import structlog

logger = structlog.get_logger(__name__)


class WebSocketManager:
    """Thread-safe manager for active WebSocket connections."""

    def __init__(self):
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.append(ws)
        logger.info("ws_connected", total=len(self._connections))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self._connections:
                self._connections.remove(ws)
        logger.info("ws_disconnected", total=len(self._connections))

    async def broadcast(self, message: dict | Any) -> None:
        """Send message to all connected clients.  Drops stale connections."""
        if isinstance(message, dict):
            payload = json.dumps(message, default=str)
        else:
            payload = json.dumps(message.dict(), default=str)

        dead: list[WebSocket] = []
        async with self._lock:
            connections = list(self._connections)

        for ws in connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    if ws in self._connections:
                        self._connections.remove(ws)

    async def send_to(self, ws: WebSocket, message: dict) -> None:
        """Send to a specific client."""
        try:
            await ws.send_text(json.dumps(message, default=str))
        except Exception:
            await self.disconnect(ws)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# Singleton
ws_manager = WebSocketManager()
