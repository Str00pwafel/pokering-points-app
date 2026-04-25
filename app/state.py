import asyncio
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

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
# Core runtime state
# ---------------------------------------------------------------------------
sessions: dict = {}
socket_ip_map: dict[str, str] = {}
app_start_time: datetime = datetime.now(timezone.utc)

# ---------------------------------------------------------------------------
# Rate-limit tracking dicts
# ---------------------------------------------------------------------------
last_join_time: defaultdict = defaultdict(lambda: datetime.min.replace(tzinfo=timezone.utc))
last_create_time: defaultdict = defaultdict(lambda: datetime.min.replace(tzinfo=timezone.utc))
socket_rate_limits: defaultdict = defaultdict(lambda: defaultdict(list))

# Theme configuration cache (mutable module-level, mutated by routes.load_theme_config)
theme_config: dict | None = None
theme_config_mtime: float | None = None

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
countdown_active: int = 0  # gauge: 1 while countdown running, 0 otherwise


# ---------------------------------------------------------------------------
# Background task coroutines
# ---------------------------------------------------------------------------
async def session_cleanup() -> None:
    """Periodically expire idle and absolute-timeout sessions."""
    while True:
        await asyncio.sleep(SESSION_CLEANUP_INTERVAL.total_seconds())
        task_last_run["session_cleanup"] = datetime.now(timezone.utc)
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
            created = session["createdAt"]
            reason = "absolute_timeout" if now - created > ABSOLUTE_TIMEOUT else "idle_timeout"
            audit(
                "session_ended",
                session_id=sid,
                reason=reason,
                duration_s=round((now - created).total_seconds(), 1),
                round_count=session.get("roundCount", 0),
                total_votes=session.get("totalVotes", 0),
                remaining_users=len(session.get("users", {})),
            )
            task = session.get("countdownTask")
            if task and not task.done():
                task.cancel()
                audit("countdown_cancelled", session_id=sid, reason="session_ended")
            del sessions[sid]

        if to_remove:
            logger.warning(f"Cleaned up {len(to_remove)} expired sessions")


async def rate_limit_cleanup() -> None:
    """Periodically evict stale rate-limit entries."""
    while True:
        await asyncio.sleep(RATE_LIMIT_CLEANUP_INTERVAL.total_seconds())
        task_last_run["rate_limit_cleanup"] = datetime.now(timezone.utc)
        now = datetime.now(timezone.utc)
        join_cutoff = now - timedelta(minutes=30)
        create_cutoff = now - timedelta(minutes=1)

        for ip in list(last_join_time.keys()):
            if last_join_time[ip] < join_cutoff:
                del last_join_time[ip]

        for ip in list(last_create_time.keys()):
            if last_create_time[ip] < create_cutoff:
                del last_create_time[ip]

        for sid in list(socket_rate_limits.keys()):
            if all(
                not timestamps or (now - max(timestamps)) > timedelta(minutes=30)
                for timestamps in socket_rate_limits[sid].values()
            ):
                del socket_rate_limits[sid]


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
