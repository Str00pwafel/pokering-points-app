import asyncio
import html
import ipaddress
import json
import logging
import logging.handlers
import os
import re
import secrets
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

import socketio
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from version import __changelog__, __version__

# Per-request trace ID, propagated through the logger via a filter.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return True


# Configure logging with file rotation
LOG_DIR = os.getenv("LOG_DIR", "logs")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", 5 * 1024 * 1024))  # 5MB default
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", 3))  # Keep 3 rotated files

os.makedirs(LOG_DIR, exist_ok=True)

log_format = "%(asctime)s - %(request_id)s - %(name)s - %(levelname)s - %(message)s"
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Console handler (warnings and above to keep terminal clean)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.WARNING)
console_handler.setFormatter(logging.Formatter(log_format))
console_handler.addFilter(RequestIdFilter())

# File handler with rotation (info and above for audit trail)
file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "pokering.log"),
    maxBytes=LOG_MAX_BYTES,
    backupCount=LOG_BACKUP_COUNT,
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter(log_format))
file_handler.addFilter(RequestIdFilter())

logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Audit log format: "text" (human-readable) or "json" (one-line JSON per record).
# JSON mode is intended for log aggregators / SIEM; extras emitted via `audit()` become top-level keys.
LOG_FORMAT = os.getenv("LOG_FORMAT", "text").strip().lower()

_RESERVED_LOG_ATTRS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """One-line JSON per record; passes `extra=` kwargs through as top-level fields."""

    def format(self, record):
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k in _RESERVED_LOG_ATTRS or k.startswith("_") or k in payload:
                continue
            payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


if LOG_FORMAT == "json":
    file_handler.setFormatter(JsonFormatter())


def audit(event, **fields):
    """Emit a structured audit event.
    Text mode: 'event=X k=v k=v'. JSON mode: extras become top-level fields."""
    clean = {k: v for k, v in fields.items() if v is not None}
    parts = [f"{k}={v}" for k, v in clean.items()]
    msg = f"event={event}" + (" " + " ".join(parts) if parts else "")
    logger.info(msg, extra={"event": event, **clean})


# Configuration from environment variables
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
TRUST_PROXY = os.getenv("TRUST_PROXY", "false").lower() in ("true", "1", "yes")
# PROXY_DEPTH: how many reverse proxies sit in front. Takes the Nth-from-right hop of X-Forwarded-For.
# Default 1 = last-hop (rightmost is typically the closest proxy; client is leftmost but can be spoofed when depth=0).
PROXY_DEPTH = max(1, int(os.getenv("PROXY_DEPTH", "1")))
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "30"))

# Fail-loud on unsafe CORS combo: wildcard origin + credentials is rejected by browsers AND
# masks misconfiguration. Require explicit origin list when credentials are enabled.
ALLOW_CREDENTIALS = True
if ALLOW_CREDENTIALS and "*" in CORS_ORIGINS:
    if ENVIRONMENT == "production":
        raise RuntimeError(
            "CORS misconfig: CORS_ORIGINS='*' with credentials enabled is invalid in production. "
            "Set CORS_ORIGINS to explicit origins (e.g., 'https://example.com')."
        )
    logging.warning(
        "CORS_ORIGINS='*' with credentials enabled — browsers will reject. "
        "Acceptable in development; set explicit origins for production."
    )
    ALLOW_CREDENTIALS = False  # Degrade rather than crash dev

# Rate-limit dict bounds — prevents memory growth under IPv6 flood
MAX_RATE_LIMIT_ENTRIES = int(os.getenv("MAX_RATE_LIMIT_ENTRIES", "10000"))

# Rate limit whitelist — comma-separated IPs or CIDR ranges (e.g., "192.168.1.0/24,10.0.0.1")
_raw_whitelist = os.getenv("RATE_LIMIT_WHITELIST", "").strip()
RATE_LIMIT_WHITELIST = []
if _raw_whitelist:
    for entry in _raw_whitelist.split(","):
        entry = entry.strip()
        if entry:
            try:
                RATE_LIMIT_WHITELIST.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                logging.warning(f"Invalid RATE_LIMIT_WHITELIST entry ignored: {entry}")


def is_ip_whitelisted(ip_str):
    """Check if IP matches any whitelisted network/address."""
    if not ip_str or not RATE_LIMIT_WHITELIST:
        return False
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in net for net in RATE_LIMIT_WHITELIST)
    except ValueError:
        return False


# Session storage and limits
sessions = {}
MAX_ACTIVE_SESSIONS = 1000
MAX_USERS_PER_SESSION = 100
app_start_time = datetime.now(timezone.utc)
ABSOLUTE_TIMEOUT = timedelta(hours=24)
IDLE_TIMEOUT = timedelta(hours=2)
JOIN_RATE_LIMIT = timedelta(seconds=5)
SESSION_CLEANUP_INTERVAL = timedelta(minutes=5)  # More frequent cleanup
RATE_LIMIT_CLEANUP_INTERVAL = timedelta(minutes=10)
CREATE_RATE_LIMIT = timedelta(seconds=3)

# Input validation patterns
# Username: 1-30 chars of letters (any script), digits, spaces, hyphens, apostrophes, underscores.
# Control chars (incl \n, \t) stripped before regex check; leading/trailing whitespace trimmed.
USERNAME_RE = re.compile(r"^[\w\s\-']{1,30}$", re.UNICODE)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1F\x7F]")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{16}$")  # matches token_urlsafe(12) length
CLIENT_ID_RE = re.compile(r"^[a-zA-Z0-9\-_]{7,36}$")


def sanitize_username(raw):
    """Strip control chars, trim whitespace, return None if empty or invalid."""
    if not isinstance(raw, str):
        return None
    cleaned = _CONTROL_CHARS_RE.sub("", raw).strip()
    if not cleaned or not USERNAME_RE.fullmatch(cleaned):
        return None
    return cleaned


# Deck presets (? is always valid as "unsure")
DECK_PRESETS = {
    "fibonacci": [1, 2, 3, 5, 8, 13, 21, "?"],
    "hours": [1, 2, 4, 8, 16, 24, 40, "?"],
    "tshirt": ["XS", "S", "M", "L", "XL", "XXL", "?"],
}
DEFAULT_DECK_TYPE = "fibonacci"

last_join_time = defaultdict(lambda: datetime.min.replace(tzinfo=timezone.utc))
last_create_time = defaultdict(lambda: datetime.min.replace(tzinfo=timezone.utc))

# Socket.IO rate limiting per socket
socket_rate_limits = defaultdict(lambda: defaultdict(list))

# Theme configuration cache
theme_config = None
theme_config_mtime = None

# Background-task liveness: each task updates its entry every iteration.
# /health inspects staleness to detect a silently dead task.
task_last_run: dict = {
    "session_cleanup": None,
    "rate_limit_cleanup": None,
    "log_retention_cleanup": None,
}


# Session cleanup background task
async def session_cleanup():
    """Clean up expired sessions more efficiently"""
    while True:
        await asyncio.sleep(SESSION_CLEANUP_INTERVAL.total_seconds())
        task_last_run["session_cleanup"] = datetime.now(timezone.utc)
        now = datetime.now(timezone.utc)
        to_remove = []

        for sid, session in list(sessions.items()):
            # Datetimes are already datetime objects (no string parsing needed)
            last_activity = session.get("lastActivity")
            created_at = session.get("createdAt")

            if not last_activity or not created_at:
                # Invalid session, remove it
                to_remove.append(sid)
                continue

            # Check timeouts
            if now - last_activity > IDLE_TIMEOUT or now - created_at > ABSOLUTE_TIMEOUT:
                to_remove.append(sid)

        # Remove expired sessions
        for sid in to_remove:
            session = sessions.get(sid)
            if session is None:
                continue
            created = session.get("createdAt")
            reason = (
                "absolute_timeout"
                if created and now - created > ABSOLUTE_TIMEOUT
                else "idle_timeout"
            )
            duration_s = round((now - created).total_seconds(), 1) if created else None
            audit(
                "session_ended",
                session_id=sid,
                reason=reason,
                duration_s=duration_s,
                round_count=session.get("roundCount", 0),
                total_votes=session.get("totalVotes", 0),
                remaining_users=len(session.get("users", {})),
            )
            # Cancel any in-flight countdown task to prevent orphan emits post-cleanup.
            task = session.get("countdownTask")
            if task and not task.done():
                task.cancel()
                audit("countdown_cancelled", session_id=sid, reason="session_ended")
            del sessions[sid]

        # Log only if sessions were removed
        if to_remove:
            logger.warning(f"Cleaned up {len(to_remove)} expired sessions")


# IP detection helper (proxy-aware)
def _pick_forwarded_hop(xff_value):
    """Pick the Nth-from-right hop of X-Forwarded-For per PROXY_DEPTH.
    Rightmost hop is the closest proxy; PROXY_DEPTH=1 = last-hop (safe default)."""
    hops = [h.strip() for h in xff_value.split(",") if h.strip()]
    if not hops:
        return None
    idx = max(0, len(hops) - PROXY_DEPTH)
    return hops[idx]


def get_client_ip(request):
    """Get client IP, checking X-Forwarded-For when behind a trusted proxy."""
    if TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            picked = _pick_forwarded_hop(forwarded)
            if picked:
                return picked
    return request.client.host if request.client else None


def _bound_dict(d, max_entries=None):
    """Evict oldest entries (by insertion order) when dict exceeds cap."""
    if max_entries is None:
        max_entries = MAX_RATE_LIMIT_ENTRIES
    while len(d) > max_entries:
        try:
            oldest_key = next(iter(d))
            del d[oldest_key]
        except (StopIteration, KeyError):
            break


# Socket.IO rate limiting helper
def check_socket_rate_limit(sid, action, limit=30, window=60):
    """Check if socket action is rate limited. Whitelisted IPs skip limits."""
    if is_ip_whitelisted(socket_ip_map.get(sid)):
        return True

    now = datetime.now(timezone.utc)

    # Clean old entries for this socket/action
    socket_rate_limits[sid][action] = [
        t for t in socket_rate_limits[sid][action] if now - t < timedelta(seconds=window)
    ]

    # Check limit
    if len(socket_rate_limits[sid][action]) >= limit:
        return False

    # Record this action
    socket_rate_limits[sid][action].append(now)
    _bound_dict(socket_rate_limits)
    return True


# Rate limit cleanup background task
async def rate_limit_cleanup():
    while True:
        await asyncio.sleep(RATE_LIMIT_CLEANUP_INTERVAL.total_seconds())
        task_last_run["rate_limit_cleanup"] = datetime.now(timezone.utc)
        now = datetime.now(timezone.utc)
        join_cutoff = now - timedelta(minutes=30)
        # create cooldown is 3s; short retention prevents unnecessary memory growth
        create_cutoff = now - timedelta(minutes=1)

        removed_join = 0
        for ip in list(last_join_time.keys()):
            if last_join_time[ip] < join_cutoff:
                del last_join_time[ip]
                removed_join += 1

        removed_create = 0
        for ip in list(last_create_time.keys()):
            if last_create_time[ip] < create_cutoff:
                del last_create_time[ip]
                removed_create += 1

        # Clean up disconnected socket rate limits
        removed_sockets = 0
        for sid in list(socket_rate_limits.keys()):
            # Remove if no recent activity in any action
            if all(
                not timestamps or (now - max(timestamps)) > timedelta(minutes=30)
                for timestamps in socket_rate_limits[sid].values()
            ):
                del socket_rate_limits[sid]
                removed_sockets += 1


# Log retention cleanup — deletes rotated log files older than LOG_RETENTION_DAYS.
# Set LOG_RETENTION_DAYS=0 to disable.
LOG_RETENTION_CHECK_INTERVAL = timedelta(hours=6)


async def log_retention_cleanup():
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
                # Never delete the active log file
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


# Lifespan handler
@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_task = asyncio.create_task(session_cleanup())
    rate_limit_task = asyncio.create_task(rate_limit_cleanup())
    log_retention_task = asyncio.create_task(log_retention_cleanup())
    yield
    # Graceful shutdown: warn connected clients before cancelling background work.
    try:
        await sio.emit("serverShutdown", {"reason": "Server is restarting"})
        await asyncio.sleep(0.2)  # brief flush window so websocket frames hit the wire
    except Exception as e:
        logger.warning(f"serverShutdown broadcast failed: {e}")
    cleanup_task.cancel()
    rate_limit_task.cancel()
    log_retention_task.cancel()


# Initialize FastAPI and Socket.IO
# Socket.IO origin lock: wildcard "*" accepted as string by socketio; explicit list otherwise.
_sio_cors = "*" if "*" in CORS_ORIGINS else CORS_ORIGINS
sio = socketio.AsyncServer(
    async_mode="asgi", max_http_buffer_size=1_000_000, cors_allowed_origins=_sio_cors
)
app = FastAPI(lifespan=lifespan, title="Pokering Points", version=__version__)

# CORS configuration
# Configure via CORS_ORIGINS environment variable (comma-separated)
# Example: CORS_ORIGINS="http://localhost:3000,https://example.com"
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=ALLOW_CREDENTIALS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    max_age=3600,
)


# Routes first
@app.get("/", response_class=HTMLResponse)
async def get_welcome():
    return FileResponse("public/welcome.html")


@app.post("/create")
async def create_session(request: Request):
    # Check global session limit
    if len(sessions) >= MAX_ACTIVE_SESSIONS:
        logger.warning(f"Session creation rejected: max limit reached ({MAX_ACTIVE_SESSIONS})")
        return HTMLResponse(
            content="<html><body><h1>Server Full</h1><p>Maximum number of active sessions reached. Please try again later.</p></body></html>",
            status_code=503,
        )

    # Rate limiting based on IP
    client_ip = get_client_ip(request)
    now = datetime.now(timezone.utc)

    if (
        client_ip
        and not is_ip_whitelisted(client_ip)
        and now - last_create_time[client_ip] < CREATE_RATE_LIMIT
    ):
        logger.warning(f"Rate limit exceeded for session creation: {client_ip}")
        return HTMLResponse(
            content="<html><body><h1>Too Many Requests</h1><p>Please wait before creating another session.</p></body></html>",
            status_code=429,
        )

    if client_ip:
        last_create_time[client_ip] = now
        _bound_dict(last_create_time)

    # token_urlsafe(12) produces exactly 16 URL-safe characters (12 bytes base64url-encoded)
    session_id = secrets.token_urlsafe(12)
    sessions[session_id] = {
        "users": {},
        "revealed": False,
        "hostClientId": None,
        "createdAt": datetime.now(timezone.utc),
        "lastActivity": datetime.now(timezone.utc),
        "deck": list(DECK_PRESETS[DEFAULT_DECK_TYPE]),
        "votingEnabled": True,
        "roundCount": 1,
        "totalVotes": 0,
    }
    audit("session_created", session_id=session_id, ip=client_ip)
    # 303 See Other ensures browser issues GET to the session URL after POST /create
    return RedirectResponse(f"/session/{session_id}", status_code=303)


@app.get("/session/{session_id}", response_class=HTMLResponse)
async def get_session(session_id: str):
    # Validate session ID format to prevent path traversal or injection
    if not SESSION_ID_RE.fullmatch(session_id):
        return HTMLResponse(
            content="<html><body><h1>Invalid Session ID</h1><p>Session ID must be 16 alphanumeric characters.</p></body></html>",
            status_code=400,
        )
    return FileResponse("public/index.html")


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    token = request_id_var.set(rid)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid
    return response


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-App-Version"] = __version__

    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # Content Security Policy
    # connect-src: ws/wss for Socket.IO; add localhost origins only in development
    connect_src = "connect-src 'self' ws: wss:"
    if ENVIRONMENT != "production":
        connect_src += " http://localhost:* http://127.0.0.1:* ws://localhost:* ws://127.0.0.1:*"

    csp_directives = [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        connect_src,
        "font-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
    # Only add upgrade-insecure-requests in production (HTTPS)
    if request.url.scheme == "https":
        csp_directives.append("upgrade-insecure-requests")
    response.headers["Content-Security-Policy"] = "; ".join(csp_directives)

    # HSTS (only if using HTTPS)
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response


def load_theme_config():
    """Load theme config with caching and file modification check"""
    global theme_config, theme_config_mtime

    theme_file = "config/themes.json"

    try:
        # Check if file exists
        if not os.path.exists(theme_file):
            return None

        # Get file modification time
        current_mtime = os.path.getmtime(theme_file)

        # Return cached config if file hasn't changed
        if theme_config is not None and theme_config_mtime == current_mtime:
            return theme_config

        # Load fresh config
        with open(theme_file) as f:
            config = json.load(f)

        # Cache config and mtime
        theme_config = config
        theme_config_mtime = current_mtime

        logger.info(f"Theme config loaded and cached (mtime: {current_mtime})")
        return config

    except Exception as e:
        logger.error(f"Error loading theme config: {e}")
        return None


_CHANGELOG_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
   <meta charset="UTF-8" />
   <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
   <title>Changelog — Pokering Points</title>
   <link rel="stylesheet" href="/css/theme-variables.css" />
   <link rel="stylesheet" href="/css/changelog.css" />
   <script src="/javascript/theme-loader.js"></script>
</head>
<body>
   <a class="back" href="/">← Back</a>
   <h1>Changelog</h1>
   <div class="subtitle">All versions</div>
   <div id="changelog">{body}</div>
</body>
</html>
"""


CHANGELOG_EXPANDED_COUNT = 5  # most-recent N versions start expanded; older ones collapsed


@app.get("/changelog.html", response_class=HTMLResponse)
async def get_changelog():
    """Server-rendered changelog. Source of truth: version.py __changelog__.
    Top N versions render with <details open>; older ones collapsed."""
    blocks = []
    for idx, (v, items) in enumerate(__changelog__.items()):
        tag = ' <span class="current">(current)</span>' if v == __version__ else ""
        items_html = "".join(f"<li>{html.escape(item)}</li>" for item in items)
        open_attr = " open" if idx < CHANGELOG_EXPANDED_COUNT else ""
        blocks.append(
            f'<details class="version-block"{open_attr}>'
            f"<summary>v{html.escape(v)}{tag}</summary>"
            f"<ul>{items_html}</ul></details>"
        )
    return HTMLResponse(content=_CHANGELOG_SHELL.format(body="\n".join(blocks)))


@app.get("/version")
async def get_version():
    versions = list(__changelog__.keys())[:2]
    changelog = {v: __changelog__[v] for v in versions}
    return {
        "version": __version__,
        "changelog": changelog,
    }


@app.get("/version/full")
async def get_version_full():
    return {
        "version": __version__,
        "changelog": __changelog__,
    }


@app.get("/theme")
async def get_theme():
    """Get active theme based on current date"""
    # Default theme fallback
    default_theme = {
        "name": "Default",
        "colors": {
            "primary-bg": "#001f3f",
            "primary-action": "#0074D9",
            "primary-hover": "#005fa3",
            "success": "#2ECC40",
            "card-bg": "#003366",
            "modal-bg": "#003366",
            "error": "#FF4136",
            "secondary-action": "#FF851B",
            "secondary-hover": "#cc6c16",
            "text-primary": "#ffffff",
            "text-secondary": "#cccccc",
        },
        "logo": "beardcraft_v1.png",
    }

    try:
        # Load config from cache or file
        config = load_theme_config()
        if not config:
            return default_theme

        # Get current date (month-day format)
        now = datetime.now()
        current_date = now.strftime("%m-%d")

        # Check schedule for active theme
        active_theme_name = "default"
        for schedule_entry in config.get("schedule", []):
            start = schedule_entry.get("start")
            end = schedule_entry.get("end")
            theme = schedule_entry.get("theme")

            if start and end and theme:
                # Simple date range check (assumes same year)
                if start <= current_date <= end:
                    active_theme_name = theme
                    break

        # Return the active theme data
        theme_data = config["themes"].get(active_theme_name, config["themes"]["default"])

        # Add decorations if present
        result = {
            "name": theme_data["name"],
            "colors": theme_data["colors"],
            "logo": theme_data["logo"],
        }

        # Include decorations config if present
        if "decorations" in theme_data:
            result["decorations"] = theme_data["decorations"]

        return result

    except Exception as e:
        logger.error(f"Error loading theme: {e}")
        return default_theme


# Staleness threshold = 2x the task interval (plus a small buffer for the first-run window).
_TASK_STALE_THRESHOLDS = {
    "session_cleanup": SESSION_CLEANUP_INTERVAL * 2,
    "rate_limit_cleanup": RATE_LIMIT_CLEANUP_INTERVAL * 2,
    "log_retention_cleanup": LOG_RETENTION_CHECK_INTERVAL * 2,
}


@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring"""
    now = datetime.now(timezone.utc)
    uptime = (now - app_start_time).total_seconds()

    tasks_report = {}
    any_stale = False
    for name, threshold in _TASK_STALE_THRESHOLDS.items():
        last = task_last_run.get(name)
        if last is None:
            # Not yet run — only consider stale if uptime exceeds threshold.
            stale = uptime > threshold.total_seconds()
            tasks_report[name] = {"last_run_s_ago": None, "stale": stale}
        else:
            age = (now - last).total_seconds()
            stale = age > threshold.total_seconds()
            tasks_report[name] = {"last_run_s_ago": round(age, 1), "stale": stale}
        if stale:
            any_stale = True
    # log_retention_cleanup is skipped entirely when LOG_RETENTION_DAYS<=0; don't mark unhealthy.
    if LOG_RETENTION_DAYS <= 0:
        tasks_report["log_retention_cleanup"]["stale"] = False
        any_stale = any(t["stale"] for t in tasks_report.values())

    return {
        "status": "unhealthy" if any_stale else "healthy",
        "version": __version__,
        "uptime_seconds": round(uptime, 2),
        "sessions": {
            "active": len(sessions),
            "max": MAX_ACTIVE_SESSIONS,
            "usage_percent": round((len(sessions) / MAX_ACTIVE_SESSIONS) * 100, 2),
        },
        "rate_limits": {
            "tracked_ips_join": len(last_join_time),
            "tracked_ips_create": len(last_create_time),
        },
        "background_tasks": tasks_report,
    }


@app.get("/metrics")
async def metrics():
    """Simple metrics endpoint (text format)"""
    now = datetime.now(timezone.utc)
    uptime = (now - app_start_time).total_seconds()

    # Calculate session stats
    total_users = sum(len(session.get("users", {})) for session in sessions.values())

    metrics_text = f"""# HELP pokering_uptime_seconds Application uptime in seconds
# TYPE pokering_uptime_seconds gauge
pokering_uptime_seconds {uptime}

# HELP pokering_sessions_active Current number of active sessions
# TYPE pokering_sessions_active gauge
pokering_sessions_active {len(sessions)}

# HELP pokering_sessions_max Maximum allowed sessions
# TYPE pokering_sessions_max gauge
pokering_sessions_max {MAX_ACTIVE_SESSIONS}

# HELP pokering_users_total Total users across all sessions
# TYPE pokering_users_total gauge
pokering_users_total {total_users}

# HELP pokering_rate_limit_ips_join IPs tracked for join rate limiting
# TYPE pokering_rate_limit_ips_join gauge
pokering_rate_limit_ips_join {len(last_join_time)}

# HELP pokering_rate_limit_ips_create IPs tracked for create rate limiting
# TYPE pokering_rate_limit_ips_create gauge
pokering_rate_limit_ips_create {len(last_create_time)}
"""
    return HTMLResponse(content=metrics_text, media_type="text/plain")


@app.get("/javascript/vendor/socket.io.min.js")
async def vendored_socket_io():
    # Vendored, version-pinned: long-cache to skip the round-trip on repeat visits.
    return FileResponse(
        "public/javascript/vendor/socket.io.min.js",
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


app.mount("/", StaticFiles(directory="public"), name="static")

# Combine FastAPI and Socket.IO
asgi_app = socketio.ASGIApp(sio, app)

# Track socket-to-IP mapping for whitelist lookups
socket_ip_map = {}


# Socket.IO handlers
@sio.event
async def connect(sid, environ):
    # Store client IP for rate limit whitelist checks. Read from server-side environ only;
    # clients cannot spoof this.
    ip_addr = None
    if TRUST_PROXY:
        headers = dict(environ.get("asgi.scope", {}).get("headers", []))
        forwarded = headers.get(b"x-forwarded-for", b"").decode()
        if forwarded:
            ip_addr = _pick_forwarded_hop(forwarded)
    if not ip_addr:
        scope = environ.get("asgi.scope", {})
        if scope.get("client"):
            ip_addr = scope["client"][0]
    if ip_addr:
        socket_ip_map[sid] = ip_addr
        _bound_dict(socket_ip_map)


RECONNECT_GRACE = 2  # seconds — delay leave broadcasts to tolerate brief disconnects


async def _delayed_leave(session_id, client_id, username, was_host):
    await asyncio.sleep(RECONNECT_GRACE)
    session = sessions.get(session_id)
    if session is None:
        return
    # User reconnected within grace window — abort
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


@sio.event
async def disconnect(sid):
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
                asyncio.create_task(_delayed_leave(session_id, client_id, username, was_host))
            await sio.emit("usersUpdate", sessions[session_id]["users"], room=session_id)
            break


@sio.event
async def join(sid, data):
    # Rate limiting: 5 joins per minute
    if not check_socket_rate_limit(sid, "join", limit=5, window=60):
        logger.warning(f"Join rate limit exceeded for socket {sid}")
        await sio.emit("joinFailed", {"reason": "Too many join attempts"}, room=sid)
        return

    # Validate data structure
    if not isinstance(data, dict):
        await sio.emit("joinFailed", {"reason": "Invalid request format"}, room=sid)
        return

    client_id = data.get("clientId")
    session_id = data.get("sessionId")
    username = data.get("username")

    # Validate session_id format
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        await sio.emit("joinFailed", {"reason": "Invalid session ID"}, room=sid)
        return

    # Validate client_id format with strict regex
    if not isinstance(client_id, str) or not CLIENT_ID_RE.fullmatch(client_id):
        logger.warning(f"Invalid client ID format: {str(client_id)[:20]}")
        await sio.emit("joinFailed", {"reason": "Invalid client ID"}, room=sid)
        return

    # IP is server-stored from `connect` handler — never trust client-supplied scope.
    ip_addr = socket_ip_map.get(sid)

    now = datetime.now(timezone.utc)

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

    # Check user limit per session
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
        # Set deck from preset if provided
        deck_type = data.get("deckType", DEFAULT_DECK_TYPE)
        if deck_type in DECK_PRESETS:
            sessions[session_id]["deck"] = DECK_PRESETS[deck_type]
            sessions[session_id]["deckType"] = deck_type

    is_host = client_id == sessions[session_id]["hostClientId"]

    # Detect reconnect: if clientId already present, replace old sid and preserve state
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

    # Spectator flag: host cannot be spectator. Client-supplied value wins; otherwise
    # preserve prior value across reconnects so F5/network drops don't silently opt back in.
    is_spectator = False
    if not is_host:
        if isinstance(data.get("isSpectator"), bool):
            is_spectator = data["isSpectator"]
        elif preserved_is_spectator is not None:
            is_spectator = bool(preserved_is_spectator)

    user_data = {
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

    # Tell the joining client which deck this session uses
    session_deck_type = sessions[session_id].get("deckType", DEFAULT_DECK_TYPE)
    await sio.emit("deckChanged", {"deckType": session_deck_type}, room=sid)

    await sio.emit("usersUpdate", sessions[session_id]["users"], room=session_id)
    await sio.emit(
        "sessionState",
        {"votingEnabled": sessions[session_id].get("votingEnabled", True)},
        room=session_id,
    )

    # Sync countdown for mid-countdown joiners so they see remaining time
    if sessions[session_id].get("countdownActive"):
        started = sessions[session_id].get("countdownStartedAt")
        if started:
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            remaining = max(0, 3 - int(elapsed))
            await sio.emit("countdown", remaining, room=sid)

    # Sync reveal state for late joiners so cards are disabled and results render.
    # Skip during countdown — stats not yet computed; room-wide revealVotes fires soon.
    if sessions[session_id].get("revealed") and not sessions[session_id].get("countdownActive"):
        await sio.emit(
            "revealVotes",
            {
                "users": sessions[session_id]["users"],
                "stats": sessions[session_id].get("voteStats", {}),
            },
            room=sid,
        )
        # Re-emit usersUpdate to this sid so vote chips render (client needs votesRevealed=true first)
        await sio.emit("usersUpdate", sessions[session_id]["users"], room=sid)

    # Broadcast join notification only for fresh joins (not reconnects)
    if not old_sid:
        await sio.emit("userJoined", {"username": username, "clientId": client_id}, room=session_id)


@sio.event
async def vote(sid, data):
    # Rate limiting: 30 votes per minute
    if not check_socket_rate_limit(sid, "vote", limit=30, window=60):
        return

    # Validate data structure
    if not isinstance(data, dict):
        return

    session_id = data.get("sessionId")
    value = data.get("value")

    # Validate session_id format
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        return

    if session_id not in sessions:
        return

    if not sessions[session_id].get("votingEnabled", True):
        return

    # Validate vote is a value in the session's deck
    deck = sessions[session_id].get("deck", DECK_PRESETS[DEFAULT_DECK_TYPE])
    vote_check = int(value) if isinstance(value, (int, float)) else value
    if vote_check not in deck:
        logger.warning(f"Vote {value} not in deck for session {session_id}")
        return

    user = sessions[session_id]["users"].get(sid)
    if user:
        if user.get("isSpectator"):
            return
        old_vote = user.get("vote")
        if old_vote == value:
            return
        if old_vote is not None and user.get("voteChanged"):
            await sio.emit(
                "actionFailed",
                {"action": "vote", "reason": "Vote can only be changed once per round"},
                room=sid,
            )
            return

        user["vote"] = value
        if old_vote is None:
            sessions[session_id]["totalVotes"] = sessions[session_id].get("totalVotes", 0) + 1
        else:
            user["voteChanged"] = True

        if old_vote is not None:
            audit(
                "vote_changed",
                session_id=session_id,
                username=user["username"],
                client_id=(user.get("clientId") or "")[:12],
                value=value,
                previous=old_vote,
                ip=socket_ip_map.get(sid),
            )
        else:
            audit(
                "vote_cast",
                session_id=session_id,
                username=user["username"],
                client_id=(user.get("clientId") or "")[:12],
                value=value,
                ip=socket_ip_map.get(sid),
            )

        users = [
            u
            for u in sessions[session_id]["users"].values()
            if not u.get("isSpectator") and not (u.get("isHost") and u.get("wantsToVote") is False)
        ]

        all_voted = len(users) > 0 and all(u["vote"] is not None for u in users)

        # Lightweight diff event — avoids broadcasting the full user dict on every vote.
        # Clients patch their local snapshot; real vote values arrive via revealVotes.
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

            async def countdown():
                nonlocal count
                while count >= 0:
                    if session_id not in sessions:
                        return
                    await sio.emit("countdown", count, room=session_id)
                    await asyncio.sleep(1)
                    count -= 1

                session = sessions.get(session_id)
                if session is None:
                    return
                deck = session.get("deck", DECK_PRESETS[DEFAULT_DECK_TYPE])
                # Build index lookup, excluding "?" from stats
                index_of = {v: i for i, v in enumerate(deck) if v != "?"}

                # Collect votes that are in the deck (excluding "?")
                voted = []
                for u in session["users"].values():
                    v = u["vote"]
                    if v == "?" or v is None:
                        continue
                    # Normalize numeric votes to int for lookup
                    check = int(v) if isinstance(v, (int, float)) else v
                    if check in index_of:
                        voted.append((u["username"], check))

                vote_stats = {}
                if voted:
                    # Average only makes sense for numeric decks
                    numeric_votes = [v for _, v in voted if isinstance(v, (int, float))]
                    if numeric_votes:
                        avg = sum(numeric_votes) / len(numeric_votes)
                        vote_stats["average"] = round(avg, 2)

                    # Median and outliers work for all deck types via index position.
                    # For even vote counts this picks the upper-middle (no interpolation —
                    # decks are ordinal, not strictly numeric, so interpolation isn't safe).
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
                session["countdownActive"] = False
                session["voteStats"] = vote_stats

                # Consensus = all counted votes identical (excluding "?" and None, which were filtered above)
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

                await sio.emit(
                    "revealVotes", {"users": session["users"], "stats": vote_stats}, room=session_id
                )

            sessions[session_id]["countdownTask"] = asyncio.create_task(countdown())


@sio.event
async def requestNewRound(sid, data):
    # Rate limiting: 30 new rounds per hour
    if not check_socket_rate_limit(sid, "requestNewRound", limit=30, window=3600):
        logger.warning(f"New round rate limit exceeded for socket {sid}")
        await sio.emit(
            "actionFailed",
            {"action": "newRound", "reason": "Too many new round requests"},
            room=sid,
        )
        return

    # Validate data structure
    if not isinstance(data, dict):
        return

    session_id = data.get("sessionId")

    # Validate session_id format
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

    # Block during countdown (revealed still False but countdown task running)
    if old_session.get("countdownActive"):
        return

    # Cancel any stale countdown task defensively (countdownActive gate above should prevent this path)
    stale_task = old_session.pop("countdownTask", None)
    if stale_task and not stale_task.done():
        stale_task.cancel()
    old_session.pop("countdownStartedAt", None)

    # Determine deck for new round
    deck_type = data.get("deckType", DEFAULT_DECK_TYPE)
    if deck_type not in DECK_PRESETS:
        deck_type = DEFAULT_DECK_TYPE

    # Host may override votingEnabled for the new round
    voting_enabled_override = data.get("votingEnabled")
    if isinstance(voting_enabled_override, bool):
        new_voting_enabled = voting_enabled_override
    else:
        new_voting_enabled = old_session.get("votingEnabled", True)

    # Clear votes in place — no new session, no redirect
    votes_cleared = sum(1 for u in old_session["users"].values() if u.get("vote") is not None)
    for u in old_session["users"].values():
        u["vote"] = None
        u["voteChanged"] = False
    old_session["revealed"] = False
    old_session.pop("voteStats", None)
    old_session["deck"] = list(DECK_PRESETS[deck_type])
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
    await sio.emit("usersUpdate", old_session["users"], room=session_id)


@sio.event
async def changeDeck(sid, data):
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

    # Only host can change deck
    user = session["users"].get(sid)
    if not user or not user.get("isHost"):
        return

    # Only allow if no votes have been cast
    has_votes = any(u["vote"] is not None for u in session["users"].values())
    if has_votes:
        return

    # Validate deck type
    if deck_type not in DECK_PRESETS:
        return

    session["deck"] = DECK_PRESETS[deck_type]
    session["deckType"] = deck_type
    session["revealed"] = False  # defensive — changeDeck only runs with no votes cast
    session.pop("voteStats", None)
    audit(
        "deck_changed",
        session_id=session_id,
        deck=deck_type,
        host=user.get("username", "unknown"),
        ip=socket_ip_map.get(sid),
    )

    await sio.emit("deckChanged", {"deckType": deck_type}, room=session_id)


@sio.event
async def hostVotingDecision(sid, data):
    # Rate limiting: 10 voting decisions per minute
    if not check_socket_rate_limit(sid, "hostVotingDecision", limit=10, window=60):
        return

    # Validate data structure
    if not isinstance(data, dict):
        return

    session_id = data.get("sessionId")
    wants_to_vote = data.get("wantsToVote")

    # Validate session_id format
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        return

    # Validate wantsToVote is boolean
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
        )
        await sio.emit("usersUpdate", sessions[session_id]["users"], room=session_id)


@sio.event
async def setSpectator(sid, data):
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

    # Host uses hostVotingDecision; keep paths separate to avoid state-conflict bugs.
    if user.get("isHost"):
        await sio.emit(
            "actionFailed",
            {"action": "setSpectator", "reason": "Host manages voting opt-out separately"},
            room=sid,
        )
        return

    # Gate mirrors deck-change / setVotingEnabled: only between rounds with no votes cast,
    # not during reveal, not mid-countdown.
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
        return  # no-op

    user["isSpectator"] = is_spectator
    if is_spectator:
        user["vote"] = None  # defensive — gate should prevent this
    audit(
        "user_spectator_toggled",
        session_id=session_id,
        username=user.get("username"),
        client_id=(user.get("clientId") or "")[:12],
        previous=old,
        is_spectator=is_spectator,
    )

    await sio.emit("usersUpdate", session["users"], room=session_id)


@sio.event
async def setVotingEnabled(sid, data):
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

    # Only allow when no votes cast
    has_votes = any(u["vote"] is not None for u in session["users"].values())
    if has_votes:
        logger.warning(
            f"setVotingEnabled rejected: votes already cast in session {session_id} by host {user.get('username', 'unknown')}"
        )
        return

    session["votingEnabled"] = voting_enabled
    session["revealed"] = False  # defensive — setVotingEnabled only runs with no votes cast
    audit(
        "voting_unlocked" if voting_enabled else "voting_locked",
        session_id=session_id,
        host=user.get("username", "unknown"),
    )

    await sio.emit("sessionState", {"votingEnabled": voting_enabled}, room=session_id)


# Start server
if __name__ == "__main__":
    # Only enable reload in development
    reload_enabled = ENVIRONMENT == "development"
    uvicorn.run("server:asgi_app", host=SERVER_HOST, port=SERVER_PORT, reload=reload_enabled)
