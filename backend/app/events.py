"""
Real-time event broadcast over WebSocket.

Plays the role of a pub/sub channel using plain in-process fan-out: one
backend process keeps the list of open sockets and pushes each event to
all of them. That is identical, from the dashboard's point of view, to
Redis pub/sub for a single backend instance, without running a broker.

To scale to several backend instances later, swap this class's internals
for Redis pub/sub; the `broadcast()` call sites do not change.

Payloads must already be JSON-safe (the API builds them with
`model_dump(mode="json")`), so REST and WebSocket show the SAME format:
ISO-8601 timestamps and plain enum values.
"""
import json
from typing import Any

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, event_type: str, payload: dict[str, Any]) -> None:
        """Send {"event": event_type, "data": payload} to every client."""
        message = json.dumps({"event": event_type, "data": payload}, default=str)
        dead: list[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except Exception:
                dead.append(connection)
        for d in dead:
            self.disconnect(d)


manager = ConnectionManager()
