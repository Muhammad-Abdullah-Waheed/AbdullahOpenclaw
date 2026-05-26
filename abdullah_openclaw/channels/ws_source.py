"""WebSocket-side EventSource naming (aligned with Step 10 ``WebSocketEventSource``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WebSocketEventSource:
    """Pinned format ``platform-ws:<client_source>`` (OpenClaw reference convention)."""

    client_source: str

    def __str__(self) -> str:
        return f"platform-ws:{self.client_source}"

    @classmethod
    def from_string(cls, s: str) -> WebSocketEventSource:
        prefix = "platform-ws:"
        if not s.startswith(prefix):
            raise ValueError(f"not a ws source string: {s!r}")
        return cls(client_source=s[len(prefix) :])


def websocket_session_id(*, client_source: str | None = None, session_id: str | None = None) -> str:
    """Disk session id under ``sessions/*.json``: explicit id wins, else derived from ``source``."""
    from abdullah_openclaw.workspace.session_store import sanitize_session_id

    if session_id and session_id.strip():
        return sanitize_session_id(session_id.strip())
    if client_source is None:
        raise ValueError("websocket_session_id needs client_source or session_id")
    return sanitize_session_id(f"ws-{client_source.strip()}")
