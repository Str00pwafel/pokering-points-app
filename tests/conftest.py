"""Shared fixtures: state isolation and Socket.IO emit recording.

Socket handlers are tested by invoking them directly (they are plain async
functions registered on the AsyncServer) with sio.emit/enter_room replaced by
recorders — no real websocket transport involved.
"""

import asyncio
from datetime import datetime, timezone

import pytest

import app.routes as routes
import app.sockets as sockets
import app.state as state
from app.core import sio

SESSION_ID = "A" * 16
CLIENT_A = "client-aaaaaaa"
CLIENT_B = "client-bbbbbbb"
CLIENT_C = "client-ccccccc"


def make_session(session_id: str = SESSION_ID) -> dict:
    """Insert a session shaped exactly like POST /create builds it."""
    from app.config import DECK_PRESETS, DEFAULT_DECK_TYPE

    state.sessions[session_id] = {
        "users": {},
        "revealed": False,
        "hostClientId": None,
        "createdAt": datetime.now(timezone.utc),
        "lastActivity": datetime.now(timezone.utc),
        "deck": list(DECK_PRESETS[DEFAULT_DECK_TYPE]["values"]),
        "votingEnabled": True,
        "roundCount": 1,
        "totalVotes": 0,
    }
    return state.sessions[session_id]


@pytest.fixture(autouse=True)
def clean_state():
    """Reset all module-level state between tests."""
    dicts = (
        state.sessions,
        state.socket_ip_map,
        state.socket_client_map,
        state.last_join_time,
        state.last_create_time,
        state.socket_rate_limits,
        state.reconnect_tokens,
        sockets._pending_leave_users,
        routes._http_hits,
    )
    for d in dicts:
        d.clear()
    for task in sockets._pending_leave_tasks.values():
        if not task.done():
            task.cancel()
    sockets._pending_leave_tasks.clear()
    routes._maintenance_cache = None
    state.countdown_active = 0
    yield
    for task in sockets._pending_leave_tasks.values():
        if not task.done():
            task.cancel()
    sockets._pending_leave_tasks.clear()
    for d in dicts:
        d.clear()
    routes._maintenance_cache = None


class EmitRecorder:
    """Captures sio.emit calls and answers simple queries about them."""

    def __init__(self):
        self.calls: list[tuple[str, object, str | None]] = []

    async def __call__(self, event, data=None, room=None, skip_sid=None, **kwargs):
        self.calls.append((event, data, room))

    def events(self, name: str) -> list[tuple[str, object, str | None]]:
        return [c for c in self.calls if c[0] == name]

    def last(self, name: str):
        matches = self.events(name)
        return matches[-1][1] if matches else None


@pytest.fixture
def emits(monkeypatch):
    recorder = EmitRecorder()
    monkeypatch.setattr(sio, "emit", recorder)

    async def fake_enter_room(sid, room):
        return None

    monkeypatch.setattr(sio, "enter_room", fake_enter_room)
    return recorder


@pytest.fixture
def fast_countdown(monkeypatch):
    """Make the reveal countdown complete in one no-op sleep."""
    monkeypatch.setattr(sockets, "COUNTDOWN_SECONDS", 0)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _s: real_sleep(0))


async def join(sid: str, client_id: str, username: str, session_id: str = SESSION_ID, **extra):
    payload = {"sessionId": session_id, "clientId": client_id, "username": username, **extra}
    await sockets.join(sid, payload)
