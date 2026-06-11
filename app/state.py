import asyncio
import os
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta, timezone
from typing import TypedDict

from typing_extensions import NotRequired

from app.config import (
    ABSOLUTE_TIMEOUT,
    IDLE_TIMEOUT,
    LOG_DIR,
    LOG_RETENTION_CHECK_INTERVAL,
    LOG_RETENTION_DAYS,
    RATE_LIMIT_CLEANUP_INTERVAL,
    SESSION_CLEANUP_INTERVAL,
)
from app.logging_setup import audit, logger


# ---------------------------------------------------------------------------
# Session / User shapes
# Runtime storage stays plain dicts (TypedDict), but key names and the
# optional-field lifecycle are documented and statically checkable here.
# ---------------------------------------------------------------------------
class User(TypedDict):
    username: str
    vote: int | str | None  # None until cast; deck value after
    isHost: bool
    isSpectator: bool
    clientId: str
    voteChanged: bool
    wantsToVote: NotRequired[bool]  # host participation toggle; absent for regular users


class Session(TypedDict):
    users: dict[str, User]  # keyed by socket SID
    revealed: bool
    hostClientId: str | None
    createdAt: datetime
    lastActivity: datetime
    deck: list[int | str]
    votingEnabled: bool
    roundCount: int
    totalVotes: int
    deckType: NotRequired[str]
    voteStats: NotRequired[dict]  # set on reveal, popped on new round
    countdownActive: NotRequired[bool]  # True from all-voted until revealVotes emitted
    countdownStartedAt: NotRequired[datetime]
    countdownTask: NotRequired[asyncio.Task]


# ---------------------------------------------------------------------------
# Core runtime state
# ---------------------------------------------------------------------------
sessions: dict[str, Session] = {}
socket_ip_map: dict[str, str] = {}
# SID → validated clientId, set on successful join. Lets rate limiting key on
# (ip, clientId) so users behind a shared non-whitelisted NAT don't share limits.
socket_client_map: dict[str, str] = {}
app_start_time: datetime = datetime.now(timezone.utc)

# ---------------------------------------------------------------------------
# Rate-limit tracking dicts
# ---------------------------------------------------------------------------
last_join_time: defaultdict = defaultdict(lambda: datetime.min.replace(tzinfo=timezone.utc))
last_create_time: defaultdict = defaultdict(lambda: datetime.min.replace(tzinfo=timezone.utc))
socket_rate_limits: OrderedDict = OrderedDict()  # {key: {action: [timestamps]}} — LRU order

# ---------------------------------------------------------------------------
# Reconnect tokens — keyed by (session_id, client_id)
# Issued on first join, required on subsequent joins with the same clientId.
# Cleaned up when the session expires.
# ---------------------------------------------------------------------------
reconnect_tokens: dict[tuple[str, str], str] = {}

# ---------------------------------------------------------------------------
# Background-task liveness
# Each task updates its entry every iteration; /health inspects staleness.
# ---------------------------------------------------------------------------
task_last_run: dict[str, datetime | None] = {
    "session_cleanup": None,
    "rate_limit_cleanup": None,
    "log_retention_cleanup": None,
}

# ---------------------------------------------------------------------------
# Gameplay metrics counters
# ---------------------------------------------------------------------------
votes_total: int = 0  # incremented per new vote (not vote changes)
reveals_total: int = 0  # incremented per revealVotes broadcast
countdown_active: int = 0  # gauge: number of countdowns currently running


# ---------------------------------------------------------------------------
# Background task coroutines
# ---------------------------------------------------------------------------
async def session_cleanup() -> None:
    """Periodically expire idle and absolute-timeout sessions.

    Each iteration is exception-guarded so one bad session shape cannot kill
    the task for the process lifetime (it would only show up as /health staleness).
    """
    while True:
        await asyncio.sleep(SESSION_CLEANUP_INTERVAL.total_seconds())
        task_last_run["session_cleanup"] = datetime.now(timezone.utc)
        try:
            now = datetime.now(timezone.utc)
            to_remove: list[str] = []

            for sid, session in list(sessions.items()):
                last_activity = session.get("lastActivity")
                created_at = session.get("createdAt")

                if not last_activity or not created_at:
                    to_remove.append(sid)
                    continue

                if now - last_activity > IDLE_TIMEOUT or now - created_at > ABSOLUTE_TIMEOUT:
                    to_remove.append(sid)

            for sid in to_remove:
                session = sessions.get(sid)
                if session is None:
                    continue
                created = session.get("createdAt")
                if created is None:
                    # Malformed session (flagged above) — remove it rather than
                    # crash here and leave it to crash every iteration.
                    reason = "invalid_state"
                    duration_s = None
                else:
                    reason = (
                        "absolute_timeout" if now - created > ABSOLUTE_TIMEOUT else "idle_timeout"
                    )
                    duration_s = round((now - created).total_seconds(), 1)
                audit(
                    "session_ended",
                    session_id=sid,
                    reason=reason,
                    duration_s=duration_s,
                    round_count=session.get("roundCount", 0),
                    total_votes=session.get("totalVotes", 0),
                    remaining_users=len(session.get("users", {})),
                )
                task = session.get("countdownTask")
                if task and not task.done():
                    task.cancel()
                    audit("countdown_cancelled", session_id=sid, reason="session_ended")
                del sessions[sid]

                # Clean up reconnect tokens for this session
                stale_keys = [k for k in reconnect_tokens if k[0] == sid]
                for k in stale_keys:
                    reconnect_tokens.pop(k, None)

            if to_remove:
                logger.warning(f"Cleaned up {len(to_remove)} expired sessions")
        except Exception:
            logger.exception("session_cleanup iteration failed")


async def rate_limit_cleanup() -> None:
    """Periodically evict stale rate-limit entries.

    Iteration body is exception-guarded — see session_cleanup.
    """
    while True:
        await asyncio.sleep(RATE_LIMIT_CLEANUP_INTERVAL.total_seconds())
        task_last_run["rate_limit_cleanup"] = datetime.now(timezone.utc)
        try:
            now = datetime.now(timezone.utc)
            join_cutoff = now - timedelta(minutes=30)
            create_cutoff = now - timedelta(minutes=1)

            for key in list(last_join_time.keys()):
                if last_join_time[key] < join_cutoff:
                    del last_join_time[key]

            for ip in list(last_create_time.keys()):
                if last_create_time[ip] < create_cutoff:
                    del last_create_time[ip]

            for sid in list(socket_rate_limits.keys()):
                if all(
                    not timestamps or (now - max(timestamps)) > timedelta(hours=2)
                    for timestamps in socket_rate_limits[sid].values()
                ):
                    del socket_rate_limits[sid]
        except Exception:
            logger.exception("rate_limit_cleanup iteration failed")


async def log_retention_cleanup() -> None:
    """Periodically delete rotated log files older than LOG_RETENTION_DAYS.

    Set LOG_RETENTION_DAYS=0 to disable.
    """
    if LOG_RETENTION_DAYS <= 0:
        return
    while True:
        await asyncio.sleep(LOG_RETENTION_CHECK_INTERVAL.total_seconds())
        task_last_run["log_retention_cleanup"] = datetime.now(timezone.utc)
        try:
            cutoff = datetime.now(timezone.utc).timestamp() - LOG_RETENTION_DAYS * 86400
            removed = 0
            for entry in os.listdir(LOG_DIR):
                path = os.path.join(LOG_DIR, entry)
                if not os.path.isfile(path):
                    continue
                if entry == "pokering.log":
                    continue
                if os.path.getmtime(path) < cutoff:
                    try:
                        os.remove(path)
                        removed += 1
                    except OSError as e:
                        logger.warning(f"Failed to delete old log {entry}: {e}")
            if removed:
                logger.info(
                    f"Log retention: removed {removed} file(s) older than {LOG_RETENTION_DAYS}d"
                )
        except Exception as e:
            logger.error(f"Log retention cleanup failed: {e}")
