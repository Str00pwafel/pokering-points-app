import asyncio
import logging
import math
import secrets
from datetime import datetime, timezone

import app.state as _state
from app.config import (
    CLIENT_ID_RE,
    DECK_PRESETS,
    DEFAULT_DECK_TYPE,
    RECONNECT_GRACE,
    SESSION_ID_RE,
    sanitize_username,
)
from app.core import sio
from app.logging_setup import audit
from app.rate_limit import (
    _bound_dict,
    _is_peer_trusted,
    _pick_forwarded_hop,
    check_socket_rate_limit,
    is_ip_whitelisted,
)
from app.state import sessions, socket_ip_map

logger = logging.getLogger("pokering")

# Tracks in-flight delayed-leave tasks keyed by (session_id, client_id).
# On reconnect within the grace window the old task is cancelled so only
# one userLeft/hostLeft fires per client regardless of disconnect count.
_pending_leave_tasks: dict[tuple[str, str], asyncio.Task] = {}


def _users_payload(session: dict) -> list[dict]:
    """Serialize users as a list, stripping SID keys from the wire payload."""
    return list(session["users"].values())


# ---------------------------------------------------------------------------
# connect / disconnect
# ---------------------------------------------------------------------------
@sio.event
async def connect(sid: str, environ: dict) -> None:
    """Store client IP from the server-side ASGI scope for rate-limit checks."""
    from app.config import TRUST_PROXY  # noqa: PLC0415

    scope = environ.get("asgi.scope", {})
    peer_host = scope["client"][0] if scope.get("client") else None

    ip_addr = None
    if TRUST_PROXY and _is_peer_trusted(peer_host):
        headers = dict(scope.get("headers", []))
        forwarded = headers.get(b"x-forwarded-for", b"").decode()
        if forwarded:
            ip_addr = _pick_forwarded_hop(forwarded)

    if not ip_addr and peer_host:
        ip_addr = peer_host

    if ip_addr:
        socket_ip_map[sid] = ip_addr
        _bound_dict(socket_ip_map)


async def _delayed_leave(session_id: str, client_id: str, username: str, was_host: bool) -> None:
    try:
        await asyncio.sleep(RECONNECT_GRACE)
        session = sessions.get(session_id)
        if session is None:
            return
        if any(u.get("clientId") == client_id for u in session["users"].values()):
            return
        audit(
            "user_left",
            session_id=session_id,
            username=username,
            client_id=(client_id or "")[:12],
            was_host=was_host,
        )
        if was_host:
            await sio.emit("hostLeft", room=session_id)
        else:
            await sio.emit("userLeft", {"username": username}, room=session_id)
    finally:
        _pending_leave_tasks.pop((session_id, client_id), None)


@sio.event
async def disconnect(sid: str) -> None:
    socket_ip_map.pop(sid, None)
    for session_id in sessions:
        if sid in sessions[session_id]["users"]:
            user = sessions[session_id]["users"][sid]
            username = user.get("username", "unknown")
            was_host = user.get("isHost", False)
            client_id = user.get("clientId")
            del sessions[session_id]["users"][sid]
            audit(
                "user_disconnected",
                session_id=session_id,
                username=username,
                client_id=(client_id or "")[:12],
                was_host=was_host,
            )
            if client_id:
                task_key = (session_id, client_id)
                old_task = _pending_leave_tasks.pop(task_key, None)
                if old_task and not old_task.done():
                    old_task.cancel()
                _pending_leave_tasks[task_key] = asyncio.create_task(
                    _delayed_leave(session_id, client_id, username, was_host)
                )
            await sio.emit("usersUpdate", _users_payload(sessions[session_id]), room=session_id)
            break


# ---------------------------------------------------------------------------
# join
# ---------------------------------------------------------------------------
@sio.event
async def join(sid: str, data: object) -> None:
    # Rate limiting: 5 joins per minute
    if not check_socket_rate_limit(sid, "join", limit=5, window=60):
        logger.warning(f"Join rate limit exceeded for socket {sid}")
        await sio.emit("joinFailed", {"reason": "Too many join attempts"}, room=sid)
        return

    if not isinstance(data, dict):
        await sio.emit("joinFailed", {"reason": "Invalid request format"}, room=sid)
        return

    client_id = data.get("clientId")
    session_id = data.get("sessionId")
    username = data.get("username")

    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        await sio.emit("joinFailed", {"reason": "Invalid session ID"}, room=sid)
        return

    if not isinstance(client_id, str) or not CLIENT_ID_RE.fullmatch(client_id):
        logger.warning(f"Invalid client ID format: {str(client_id)[:20]}")
        await sio.emit("joinFailed", {"reason": "Invalid client ID"}, room=sid)
        return

    # --- Reconnect token validation (SEC-12) ---
    submitted_token = data.get("reconnectToken")
    if not isinstance(submitted_token, str):
        submitted_token = None

    stored_token = _state.reconnect_tokens.get((session_id, client_id))
    is_new_client = stored_token is None
    new_token: str | None = None

    if stored_token is not None and submitted_token != stored_token:
        logger.warning(
            f"Reconnect token mismatch: session={session_id} client={client_id[:12]} sid={sid}"
        )
        await sio.emit("joinFailed", {"reason": "Invalid reconnect token"}, room=sid)
        return

    if is_new_client:
        new_token = secrets.token_urlsafe(32)
        _state.reconnect_tokens[(session_id, client_id)] = new_token

    ip_addr = socket_ip_map.get(sid)
    now = datetime.now(timezone.utc)

    from app.config import JOIN_RATE_LIMIT  # noqa: PLC0415
    from app.state import last_join_time  # noqa: PLC0415

    if (
        ip_addr
        and not is_ip_whitelisted(ip_addr)
        and now - last_join_time[ip_addr] < JOIN_RATE_LIMIT
    ):
        await sio.emit("joinFailed", {"reason": "Too many join attempts. Please wait."}, room=sid)
        return

    if ip_addr:
        last_join_time[ip_addr] = now
        _bound_dict(last_join_time)

    if session_id not in sessions:
        await sio.emit("joinFailed", {"reason": "Session not found"}, room=sid)
        return

    from app.config import MAX_USERS_PER_SESSION  # noqa: PLC0415

    if len(sessions[session_id]["users"]) >= MAX_USERS_PER_SESSION:
        logger.warning(f"Session {session_id} full: {MAX_USERS_PER_SESSION} users")
        await sio.emit("joinFailed", {"reason": "Session is full"}, room=sid)
        return

    username = sanitize_username(username)
    if username is None:
        await sio.emit(
            "joinFailed",
            {"reason": "Invalid username (letters, digits, spaces; max 30)."},
            room=sid,
        )
        return

    if sessions[session_id]["hostClientId"] is None:
        sessions[session_id]["hostClientId"] = client_id
        deck_type = data.get("deckType", DEFAULT_DECK_TYPE)
        if deck_type in DECK_PRESETS:
            sessions[session_id]["deck"] = list(DECK_PRESETS[deck_type]["values"])
            sessions[session_id]["deckType"] = deck_type

    is_host = client_id == sessions[session_id]["hostClientId"]

    preserved_vote = None
    preserved_wants_to_vote = None
    preserved_is_spectator = None
    preserved_vote_changed = False
    old_sid = None
    for existing_sid, u in sessions[session_id]["users"].items():
        if u.get("clientId") == client_id:
            old_sid = existing_sid
            preserved_vote = u.get("vote")
            preserved_wants_to_vote = u.get("wantsToVote")
            preserved_is_spectator = u.get("isSpectator")
            preserved_vote_changed = bool(u.get("voteChanged"))
            break
    if old_sid:
        sessions[session_id]["users"].pop(old_sid, None)
        socket_ip_map.pop(old_sid, None)
        audit(
            "user_reconnected",
            session_id=session_id,
            username=username,
            client_id=client_id[:12],
            ip=ip_addr,
        )

    is_spectator = False
    if not is_host:
        if isinstance(data.get("isSpectator"), bool):
            is_spectator = data["isSpectator"]
        elif preserved_is_spectator is not None:
            is_spectator = bool(preserved_is_spectator)

    user_data: dict = {
        "username": username,
        "vote": preserved_vote,
        "isHost": is_host,
        "isSpectator": is_spectator,
        "clientId": client_id,
        "voteChanged": preserved_vote_changed,
    }

    if preserved_wants_to_vote is not None:
        user_data["wantsToVote"] = preserved_wants_to_vote
    if "wantsToVote" in data:
        user_data["wantsToVote"] = data["wantsToVote"]

    sessions[session_id]["users"][sid] = user_data
    sessions[session_id]["lastActivity"] = datetime.now(timezone.utc)

    role = "host" if is_host else ("spectator" if is_spectator else "user")
    if not old_sid:
        audit(
            "user_joined",
            session_id=session_id,
            username=username,
            client_id=client_id[:12],
            ip=ip_addr,
            role=role,
        )

    await sio.enter_room(sid, session_id)

    session_deck_type = sessions[session_id].get("deckType", DEFAULT_DECK_TYPE)
    await sio.emit("deckChanged", {"deckType": session_deck_type}, room=sid)

    await sio.emit("usersUpdate", _users_payload(sessions[session_id]), room=session_id)
    await sio.emit(
        "sessionState",
        {"votingEnabled": sessions[session_id].get("votingEnabled", True)},
        room=session_id,
    )

    # Sync countdown for mid-countdown joiners
    if sessions[session_id].get("countdownActive"):
        started = sessions[session_id].get("countdownStartedAt")
        if started:
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            remaining = max(0, 3 - int(elapsed))
            await sio.emit("countdown", remaining, room=sid)

    # Sync reveal state for late joiners
    if sessions[session_id].get("revealed") and not sessions[session_id].get("countdownActive"):
        await sio.emit(
            "revealVotes",
            {
                "users": _users_payload(sessions[session_id]),
                "stats": sessions[session_id].get("voteStats", {}),
            },
            room=sid,
        )
        await sio.emit("usersUpdate", _users_payload(sessions[session_id]), room=sid)

    if not old_sid:
        # Skip the joining socket — they don't need a toast for their own join.
        await sio.emit("userJoined", {"username": username}, room=session_id, skip_sid=sid)

    # Issue private reconnect token to new clients only (SEC-12)
    if is_new_client and new_token:
        await sio.emit("reconnectToken", {"token": new_token}, room=sid)


# ---------------------------------------------------------------------------
# vote
# ---------------------------------------------------------------------------
@sio.event
async def vote(sid: str, data: object) -> None:
    # Rate limiting: 30 votes per minute
    if not check_socket_rate_limit(sid, "vote", limit=30, window=60):
        return

    if not isinstance(data, dict):
        return

    session_id = data.get("sessionId")
    value = data.get("value")

    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        return

    if session_id not in sessions:
        return

    if not sessions[session_id].get("votingEnabled", True):
        return

    # Reject bools (isinstance(True, int) is True in Python) and non-finite floats
    if isinstance(value, bool):
        return
    if isinstance(value, float) and not math.isfinite(value):
        return

    deck = sessions[session_id].get("deck", DECK_PRESETS[DEFAULT_DECK_TYPE]["values"])
    vote_check = int(value) if isinstance(value, (int, float)) else value
    if vote_check not in deck:
        logger.warning(f"Vote {value} not in deck for session {session_id}")
        return

    user = sessions[session_id]["users"].get(sid)
    if user:
        if user.get("isSpectator"):
            return
        old_vote = user.get("vote")
        if old_vote == vote_check:
            return
        if old_vote is not None and user.get("voteChanged"):
            await sio.emit(
                "actionFailed",
                {"action": "vote", "reason": "Vote can only be changed once per round"},
                room=sid,
            )
            return

        user["vote"] = vote_check  # store normalised value, not raw client value
        if old_vote is None:
            sessions[session_id]["totalVotes"] = sessions[session_id].get("totalVotes", 0) + 1
            # Increment gameplay counter for new votes only
            _state.votes_total += 1
        else:
            user["voteChanged"] = True

        if old_vote is not None:
            audit(
                "vote_changed",
                session_id=session_id,
                username=user["username"],
                client_id=(user.get("clientId") or "")[:12],
                value=vote_check,
                previous=old_vote,
                ip=socket_ip_map.get(sid),
            )
        else:
            audit(
                "vote_cast",
                session_id=session_id,
                username=user["username"],
                client_id=(user.get("clientId") or "")[:12],
                value=vote_check,
                ip=socket_ip_map.get(sid),
            )

        users = [
            u
            for u in sessions[session_id]["users"].values()
            if not u.get("isSpectator") and not (u.get("isHost") and u.get("wantsToVote") is False)
        ]

        all_voted = len(users) > 0 and all(u["vote"] is not None for u in users)

        await sio.emit(
            "userVoted",
            {
                "clientId": user.get("clientId"),
                "voteChanged": bool(user.get("voteChanged")),
            },
            room=session_id,
        )

        if all_voted and not sessions[session_id]["revealed"]:
            sessions[session_id]["revealed"] = True
            sessions[session_id]["countdownActive"] = True
            sessions[session_id]["countdownStartedAt"] = datetime.now(timezone.utc)
            count = 3
            audit(
                "countdown_started",
                session_id=session_id,
                duration_s=count,
                round=sessions[session_id].get("roundCount", 1),
            )

            async def countdown() -> None:
                nonlocal count
                _state.countdown_active = 1
                try:
                    while count >= 0:
                        if session_id not in sessions:
                            return
                        await sio.emit("countdown", count, room=session_id)
                        await asyncio.sleep(1)
                        count -= 1

                    session = sessions.get(session_id)
                    if session is None:
                        return
                    deck = session.get("deck", DECK_PRESETS[DEFAULT_DECK_TYPE]["values"])
                    index_of = {v: i for i, v in enumerate(deck) if v != "?"}

                    voted = []
                    for u in session["users"].values():
                        v = u["vote"]
                        if v == "?" or v is None:
                            continue
                        if v in index_of:
                            voted.append((u["username"], v))

                    vote_stats: dict = {}
                    if voted:
                        numeric_votes = [v for _, v in voted if isinstance(v, (int, float))]
                        if numeric_votes:
                            avg = sum(numeric_votes) / len(numeric_votes)
                            vote_stats["average"] = round(avg, 2)

                        idxs = sorted(index_of[v] for _, v in voted)
                        median_idx = idxs[len(idxs) // 2]
                        vote_stats["median"] = deck[median_idx]

                        STEP_THRESHOLD = 2
                        vote_stats["outliers"] = [
                            name
                            for (name, v) in voted
                            if abs(index_of[v] - median_idx) >= STEP_THRESHOLD
                        ]

                    if session_id not in sessions:
                        return

                    distinct = {v for _, v in voted}
                    consensus = len(voted) > 0 and len(distinct) == 1
                    vote_stats["consensus"] = consensus

                    vote_map = {name: v for name, v in voted}
                    audit(
                        "round_revealed",
                        session_id=session_id,
                        round=session.get("roundCount", 1),
                        votes=vote_map,
                        average=vote_stats.get("average"),
                        median=vote_stats.get("median"),
                        outliers=vote_stats.get("outliers", []),
                        consensus=consensus,
                        voter_count=len(voted),
                    )

                    session["voteStats"] = vote_stats
                    # Clear countdownActive before emitting reveal so late joiners
                    # get the reveal-sync branch in join, not the countdown-sync branch.
                    session["countdownActive"] = False

                    _state.reveals_total += 1
                    await sio.emit(
                        "revealVotes",
                        {"users": _users_payload(session), "stats": vote_stats},
                        room=session_id,
                    )

                except asyncio.CancelledError:
                    # requestNewRound cancelled us — clear session state so the new round
                    # can proceed and late joiners don't see a phantom countdown.
                    s = sessions.get(session_id)
                    if s is not None:
                        s["countdownActive"] = False
                        s.pop("countdownStartedAt", None)
                    raise

                finally:
                    _state.countdown_active = 0

            sessions[session_id]["countdownTask"] = asyncio.create_task(countdown())


# ---------------------------------------------------------------------------
# requestNewRound
# ---------------------------------------------------------------------------
@sio.event
async def requestNewRound(sid: str, data: object) -> None:
    # Rate limiting: 30 new rounds per hour
    if not check_socket_rate_limit(sid, "requestNewRound", limit=30, window=3600):
        logger.warning(f"New round rate limit exceeded for socket {sid}")
        await sio.emit(
            "actionFailed",
            {"action": "newRound", "reason": "Too many new round requests"},
            room=sid,
        )
        return

    if not isinstance(data, dict):
        return

    session_id = data.get("sessionId")

    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        return

    old_session = sessions.get(session_id)
    if old_session is None:
        return

    user = old_session["users"].get(sid)
    if not user or not user.get("isHost"):
        await sio.emit(
            "actionFailed",
            {"action": "newRound", "reason": "Only host can request new round"},
            room=sid,
        )
        return

    # Block during countdown
    if old_session.get("countdownActive"):
        return

    stale_task = old_session.pop("countdownTask", None)
    if stale_task and not stale_task.done():
        stale_task.cancel()
    old_session.pop("countdownStartedAt", None)

    deck_type = data.get("deckType", DEFAULT_DECK_TYPE)
    if deck_type not in DECK_PRESETS:
        deck_type = DEFAULT_DECK_TYPE

    voting_enabled_override = data.get("votingEnabled")
    if isinstance(voting_enabled_override, bool):
        new_voting_enabled = voting_enabled_override
    else:
        new_voting_enabled = old_session.get("votingEnabled", True)

    votes_cleared = sum(1 for u in old_session["users"].values() if u.get("vote") is not None)
    for u in old_session["users"].values():
        u["vote"] = None
        u["voteChanged"] = False
    old_session["revealed"] = False
    old_session.pop("voteStats", None)
    old_session["deck"] = list(DECK_PRESETS[deck_type]["values"])
    old_session["deckType"] = deck_type
    old_session["votingEnabled"] = new_voting_enabled
    old_session["roundCount"] = old_session.get("roundCount", 1) + 1
    old_session["lastActivity"] = datetime.now(timezone.utc)

    audit(
        "round_started",
        session_id=session_id,
        round=old_session["roundCount"],
        host=user.get("username", "unknown"),
        deck=deck_type,
        votes_cleared=votes_cleared,
        voting_enabled=new_voting_enabled,
    )

    await sio.emit(
        "roundReset", {"deckType": deck_type, "votingEnabled": new_voting_enabled}, room=session_id
    )
    await sio.emit("usersUpdate", _users_payload(old_session), room=session_id)


# ---------------------------------------------------------------------------
# changeDeck
# ---------------------------------------------------------------------------
@sio.event
async def changeDeck(sid: str, data: object) -> None:
    # Rate limiting: 20 deck changes per minute
    if not check_socket_rate_limit(sid, "changeDeck", limit=20, window=60):
        return

    if not isinstance(data, dict):
        return

    session_id = data.get("sessionId")
    deck_type = data.get("deckType")

    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        return

    session = sessions.get(session_id)
    if session is None:
        return

    user = session["users"].get(sid)
    if not user or not user.get("isHost"):
        return

    has_votes = any(u["vote"] is not None for u in session["users"].values())
    if has_votes:
        return

    if deck_type not in DECK_PRESETS:
        return

    session["deck"] = list(DECK_PRESETS[deck_type]["values"])
    session["deckType"] = deck_type
    session["revealed"] = False
    session.pop("voteStats", None)
    audit(
        "deck_changed",
        session_id=session_id,
        deck=deck_type,
        host=user.get("username", "unknown"),
        ip=socket_ip_map.get(sid),
    )

    await sio.emit("deckChanged", {"deckType": deck_type}, room=session_id)


# ---------------------------------------------------------------------------
# hostVotingDecision
# ---------------------------------------------------------------------------
@sio.event
async def hostVotingDecision(sid: str, data: object) -> None:
    # Rate limiting: 10 voting decisions per minute
    if not check_socket_rate_limit(sid, "hostVotingDecision", limit=10, window=60):
        return

    if not isinstance(data, dict):
        return

    session_id = data.get("sessionId")
    wants_to_vote = data.get("wantsToVote")

    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        return

    if not isinstance(wants_to_vote, bool):
        return

    if session_id not in sessions:
        return

    user = sessions[session_id]["users"].get(sid)
    if user and user.get("isHost"):
        user["wantsToVote"] = wants_to_vote
        audit(
            "host_voting_decision",
            session_id=session_id,
            host=user.get("username", "unknown"),
            wants_to_vote=wants_to_vote,
            ip=socket_ip_map.get(sid),
        )
        await sio.emit("usersUpdate", _users_payload(sessions[session_id]), room=session_id)


# ---------------------------------------------------------------------------
# setSpectator
# ---------------------------------------------------------------------------
@sio.event
async def setSpectator(sid: str, data: object) -> None:
    if not check_socket_rate_limit(sid, "setSpectator", limit=10, window=60):
        return

    if not isinstance(data, dict):
        return

    session_id = data.get("sessionId")
    is_spectator = data.get("isSpectator")

    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        return
    if not isinstance(is_spectator, bool):
        return

    session = sessions.get(session_id)
    if session is None:
        return

    user = session["users"].get(sid)
    if not user:
        return

    if user.get("isHost"):
        await sio.emit(
            "actionFailed",
            {"action": "setSpectator", "reason": "Host manages voting opt-out separately"},
            room=sid,
        )
        return

    has_votes = any(u.get("vote") is not None for u in session["users"].values())
    if has_votes or session.get("revealed") or session.get("countdownActive"):
        await sio.emit(
            "actionFailed",
            {"action": "setSpectator", "reason": "Cannot change spectator mode mid-round"},
            room=sid,
        )
        return

    old = bool(user.get("isSpectator"))
    if old == is_spectator:
        return

    user["isSpectator"] = is_spectator
    if is_spectator:
        user["vote"] = None
    audit(
        "user_spectator_toggled",
        session_id=session_id,
        username=user.get("username"),
        client_id=(user.get("clientId") or "")[:12],
        previous=old,
        is_spectator=is_spectator,
    )

    await sio.emit("usersUpdate", _users_payload(session), room=session_id)


# ---------------------------------------------------------------------------
# setVotingEnabled
# ---------------------------------------------------------------------------
@sio.event
async def setVotingEnabled(sid: str, data: object) -> None:
    if not check_socket_rate_limit(sid, "setVotingEnabled", limit=20, window=60):
        return

    if not isinstance(data, dict):
        return

    session_id = data.get("sessionId")
    voting_enabled = data.get("votingEnabled")

    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        return

    if not isinstance(voting_enabled, bool):
        return

    session = sessions.get(session_id)
    if session is None:
        logger.warning(f"setVotingEnabled rejected: session not found ({session_id}) sid={sid}")
        return

    user = session["users"].get(sid)
    if not user or not user.get("isHost"):
        logger.warning(
            f"setVotingEnabled rejected: non-host attempt in session {session_id} sid={sid}"
        )
        return

    has_votes = any(u["vote"] is not None for u in session["users"].values())
    if has_votes:
        logger.warning(
            f"setVotingEnabled rejected: votes already cast in session {session_id} by host {user.get('username', 'unknown')}"
        )
        return

    session["votingEnabled"] = voting_enabled
    session["revealed"] = False
    audit(
        "voting_unlocked" if voting_enabled else "voting_locked",
        session_id=session_id,
        host=user.get("username", "unknown"),
        ip=socket_ip_map.get(sid),
    )

    await sio.emit("sessionState", {"votingEnabled": voting_enabled}, room=session_id)
