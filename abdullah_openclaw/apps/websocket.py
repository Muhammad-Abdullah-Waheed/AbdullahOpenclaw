"""Programmatic HTTP + WebSocket access (Stages 11 + 12).

Expose ``ws://…/ws`` like ``build-your-own-openclaw/10-websocket``. Source strings match
regex rows in ``routing.yaml`` → ``routing_table.resolve_agent_for_source``.

Run::

    uv run python websocket_app.py
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
from collections.abc import MutableMapping
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket
from pydantic import BaseModel, Field, ValidationError
from starlette.websockets import WebSocketDisconnect

from abdullah_openclaw.core.agent import Session
from abdullah_openclaw.agents.runtime import get_skill_runtime_for_agent
from abdullah_openclaw.channels.ws_source import WebSocketEventSource, websocket_session_id
from abdullah_openclaw.core.event_bus import EventBus
from abdullah_openclaw.core.events import AssistantReplyFinished, UserTurnReceived
from abdullah_openclaw.repl.observers import wire_default_observers
from abdullah_openclaw.routing.session_stems import websocket_transcript_stem
from abdullah_openclaw.routing.table import resolve_agent_for_source
from abdullah_openclaw.workspace.session_store import load_transcript, persistence_enabled, session_path
from abdullah_openclaw.workspace.skill_runtime import PROJECT_ROOT
from abdullah_openclaw.core.skill_selection import skills_for_turn
from abdullah_openclaw.workspace.skills import build_system_prompt
from abdullah_openclaw.repl.slash import ReplState


class WsInboundMessage(BaseModel):
    """JSON body from websocket clients — mirrors Step 10 ``WebSocketMessage``."""

    source: str = Field(min_length=1, max_length=256, description="Logical client identifier")
    content: str = Field(min_length=1, max_length=65536, description="User text for the agent")
    session_id: str | None = Field(
        default=None,
        max_length=120,
        description="Optional transcript id (default derives from ``source``: ws-<source>)",
    )


def bind_host() -> str:
    return (os.environ.get("WEBSOCKET_BIND") or os.environ.get("WS_BIND") or "127.0.0.1").strip()


def bind_port() -> int:
    raw = (os.environ.get("WEBSOCKET_PORT") or os.environ.get("WS_PORT") or "8000").strip()
    return max(1, min(65535, int(raw)))


class _WsFanout:
    """Best-effort broadcast to all sockets (drops broken clients like the reference)."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    def attach(self, ws: WebSocket) -> None:
        self._clients.add(ws)

    def detach(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    def n_clients(self) -> int:
        return len(self._clients)

    async def broadcast(self, payload: dict[str, object]) -> None:
        dead: list[WebSocket] = []
        snap = list(self._clients)
        for c in snap:
            try:
                await c.send_json(payload)
            except Exception:
                dead.append(c)
        for c in dead:
            self._clients.discard(c)


_SID_LOCK_FACTORY: dict[str, asyncio.Lock] = {}
_LOCK_INIT = asyncio.Lock()


async def _lock_for_session(session_stem: str) -> asyncio.Lock:
    async with _LOCK_INIT:
        if session_stem not in _SID_LOCK_FACTORY:
            _SID_LOCK_FACTORY[session_stem] = asyncio.Lock()
        return _SID_LOCK_FACTORY[session_stem]


logger = logging.getLogger(__name__)


def _json_safe(obj: object) -> object:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        obj = dataclasses.asdict(obj)
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(x) for x in obj]
    return obj


def _event_payload(ev: object) -> dict[str, object]:
    d = _json_safe(ev)
    if not isinstance(d, dict):
        d = {}
    assert isinstance(d, dict)
    d["type"] = type(ev).__name__
    return d


def _load_cached_session(
    transcript_stem: str,
    cache: MutableMapping[str, Session],
    *,
    rt,
    spath,
) -> Session:
    if transcript_stem in cache:
        return cache[transcript_stem]
    loaded = load_transcript(spath) if persistence_enabled() else None
    if loaded:
        sess = Session.from_transcript(loaded)
        sess.set_system_prompt(rt.initial)
        logger.info("websocket session: resumed id=%s messages=%s", transcript_stem, len(sess.messages))
    else:
        sess = Session(system_prompt=rt.initial)
        logger.info("websocket session: new id=%s", transcript_stem)
    cache[transcript_stem] = sess
    return cache[transcript_stem]


_fanout = _WsFanout()


@asynccontextmanager
async def lifespan(app: FastAPI):
    dot = PROJECT_ROOT / ".env"
    if dot.is_file():
        from dotenv import load_dotenv

        load_dotenv(dot)
    app.state.ws_sessions_cache = {}  # type: ignore[attr-defined]
    logger.info("websocket: ready — resolve agents per message via routing.yaml")
    yield


app = FastAPI(title="AbdullahOpenclaw WebSocket", lifespan=lifespan)


@app.get("/")
def root() -> dict[str, object]:
    return {"ok": True, "websocket": "/ws", "stage": "11-ws-and-12-routing"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    _fanout.attach(websocket)
    logger.info(
        "websocket: client connected (total=%s)",
        _fanout.n_clients(),
    )

    cache: dict[str, Session] = app.state.ws_sessions_cache

    try:
        while True:
            raw = await websocket.receive_json()
            try:
                msg = WsInboundMessage(**raw)
            except ValidationError as e:
                await websocket.send_json(
                    {"type": "error", "message": str(e)},
                )
                logger.warning("websocket: validation error %s", e)
                continue

            ev_src = WebSocketEventSource(client_source=msg.source)
            source_str = str(ev_src)

            agent_id = resolve_agent_for_source(source_str)
            rt = get_skill_runtime_for_agent(agent_id)

            ws_logical_key = websocket_session_id(client_source=msg.source, session_id=msg.session_id)
            sid_stem = websocket_transcript_stem(agent_id, ws_logical_key)
            spath = session_path(sid_stem)

            ws_event_payload: dict[str, object] = {
                "type": "ws_inbound",
                "source": source_str,
                "session_id": sid_stem,
                "agent_id": agent_id,
            }
            await _fanout.broadcast(ws_event_payload)

            lock = await _lock_for_session(sid_stem)
            async with lock:
                session = _load_cached_session(sid_stem, cache, rt=rt, spath=spath)
                state = ReplState(session=session, sid=sid_stem, spath=spath, initial=rt.initial)
                turn_bus = EventBus()
                wire_default_observers(turn_bus, state)

                deduped, auto_skill_ids = skills_for_turn(rt, msg.content)
                ut = UserTurnReceived(sid_stem, msg.content, auto_skill_ids)
                await _fanout.broadcast(_event_payload(ut))
                turn_bus.publish(ut)

                session.set_system_prompt(build_system_prompt(rt.foundation_prompt, deduped))

                reply = await asyncio.to_thread(session.chat, msg.content)
                af = AssistantReplyFinished(sid_stem, reply, len(session.messages))

                await _fanout.broadcast(_event_payload(af))
                turn_bus.publish(af)

                logger.debug(
                    "websocket turn done stem=%s agent=%s reply_len=%s",
                    sid_stem,
                    agent_id,
                    len(reply),
                )

    except WebSocketDisconnect:
        logger.info("websocket: client disconnected cleanly")
    except Exception:
        logger.exception("websocket: connection error")
    finally:
        _fanout.detach(websocket)
        logger.info(
            "websocket: client socket removed total=%s",
            _fanout.n_clients(),
        )


def main() -> None:
    dot = PROJECT_ROOT / ".env"
    if dot.is_file():
        from dotenv import load_dotenv

        load_dotenv(dot)
    host = bind_host()
    port = bind_port()
    logger.info(
        "websocket: listening ws://%s:%s/ws (HTTP GET /)",
        host,
        port,
    )
    log_level = (os.environ.get("LOG_LEVEL") or "info").lower()
    uvicorn.run(app, host=host, port=port, log_level=log_level)


if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, (os.environ.get("LOG_LEVEL") or "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    main()
