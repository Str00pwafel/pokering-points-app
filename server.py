import asyncio
import json
import logging
import logging.handlers
import os
import re
import secrets
import socketio
import uvicorn

from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from version import __version__, __changelog__

# Configure logging with file rotation
LOG_DIR = os.getenv("LOG_DIR", "logs")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", 5 * 1024 * 1024))  # 5MB default
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", 3))  # Keep 3 rotated files

os.makedirs(LOG_DIR, exist_ok=True)

log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Console handler (warnings and above to keep terminal clean)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.WARNING)
console_handler.setFormatter(logging.Formatter(log_format))

# File handler with rotation (info and above for audit trail)
file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "pokering.log"),
    maxBytes=LOG_MAX_BYTES,
    backupCount=LOG_BACKUP_COUNT,
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter(log_format))

logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Configuration from environment variables
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
TRUST_PROXY = os.getenv("TRUST_PROXY", "false").lower() in ("true", "1", "yes")

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
CREATE_RATE_LIMIT = timedelta(seconds=10)

# Input validation patterns
USERNAME_RE = re.compile(r"^[A-Za-z]{1,20}$")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{16}$")  # 16 chars for new format
CLIENT_ID_RE = re.compile(r"^[a-zA-Z0-9\-_]{7,36}$")

# Deck validation limits
DECK_VALUE_MIN = 1
DECK_VALUE_MAX = 1000
DECK_SIZE_MIN = 2
DECK_SIZE_MAX = 20

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

# Session cleanup background task
async def session_cleanup():
    """Clean up expired sessions more efficiently"""
    while True:
        await asyncio.sleep(SESSION_CLEANUP_INTERVAL.total_seconds())
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
            if sid in sessions:
                del sessions[sid]

        # Log only if sessions were removed
        if to_remove:
            logger.warning(f"Cleaned up {len(to_remove)} expired sessions")

# IP detection helper (proxy-aware)
def get_client_ip(request):
    """Get client IP, checking X-Forwarded-For when behind a trusted proxy."""
    if TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None

# Socket.IO rate limiting helper
def check_socket_rate_limit(sid, action, limit=30, window=60):
    """Check if socket action is rate limited"""
    now = datetime.now(timezone.utc)

    # Clean old entries for this socket/action
    socket_rate_limits[sid][action] = [
        t for t in socket_rate_limits[sid][action]
        if now - t < timedelta(seconds=window)
    ]

    # Check limit
    if len(socket_rate_limits[sid][action]) >= limit:
        return False

    # Record this action
    socket_rate_limits[sid][action].append(now)
    return True

# Rate limit cleanup background task
async def rate_limit_cleanup():
    while True:
        await asyncio.sleep(RATE_LIMIT_CLEANUP_INTERVAL.total_seconds())
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=30)

        # Clean up old IP-based rate limit entries
        removed_join = 0
        for ip in list(last_join_time.keys()):
            if last_join_time[ip] < cutoff:
                del last_join_time[ip]
                removed_join += 1

        removed_create = 0
        for ip in list(last_create_time.keys()):
            if last_create_time[ip] < cutoff:
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


# Lifespan handler
@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_task = asyncio.create_task(session_cleanup())
    rate_limit_task = asyncio.create_task(rate_limit_cleanup())
    yield
    cleanup_task.cancel()
    rate_limit_task.cancel()

# Initialize FastAPI and Socket.IO
sio = socketio.AsyncServer(
    async_mode='asgi',
    max_http_buffer_size=1_000_000,
    cors_allowed_origins=[]  # Socket.IO CORS handled by FastAPI middleware
)
app = FastAPI(lifespan=lifespan, title="Pokering Points", version=__version__)

# CORS configuration
# Configure via CORS_ORIGINS environment variable (comma-separated)
# Example: CORS_ORIGINS="http://localhost:3000,https://example.com"
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    max_age=3600,
)

# Routes first
@app.get("/", response_class=HTMLResponse)
async def get_welcome():
    return FileResponse("public/welcome.html")

@app.get("/create")
async def create_session(request: Request):
    # Check global session limit
    if len(sessions) >= MAX_ACTIVE_SESSIONS:
        logger.warning(f"Session creation rejected: max limit reached ({MAX_ACTIVE_SESSIONS})")
        return HTMLResponse(
            content="<html><body><h1>Server Full</h1><p>Maximum number of active sessions reached. Please try again later.</p></body></html>",
            status_code=503
        )

    # Rate limiting based on IP
    client_ip = get_client_ip(request)
    now = datetime.now(timezone.utc)

    if client_ip and now - last_create_time[client_ip] < CREATE_RATE_LIMIT:
        logger.warning(f"Rate limit exceeded for session creation: {client_ip}")
        return HTMLResponse(
            content="<html><body><h1>Too Many Requests</h1><p>Please wait before creating another session.</p></body></html>",
            status_code=429
        )

    if client_ip:
        last_create_time[client_ip] = now

    # Use cryptographically secure session ID generation
    session_id = secrets.token_urlsafe(12)[:16]  # 16 URL-safe characters
    sessions[session_id] = {
        "users": {},
        "revealed": False,
        "hostClientId": None,
        "createdAt": datetime.now(timezone.utc),
        "lastActivity": datetime.now(timezone.utc),
        "deck": list(DECK_PRESETS[DEFAULT_DECK_TYPE]),
    }
    logger.info(f"Session created: {session_id} by {client_ip}")
    return RedirectResponse(f"/session/{session_id}")

@app.get("/session/{session_id}", response_class=HTMLResponse)
async def get_session(session_id: str):
    # Validate session ID format to prevent path traversal or injection
    if not SESSION_ID_RE.fullmatch(session_id):
        return HTMLResponse(
            content="<html><body><h1>Invalid Session ID</h1><p>Session ID must be 16 alphanumeric characters.</p></body></html>",
            status_code=400
        )
    return FileResponse("public/index.html")

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-App-Version"] = __version__

    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # Content Security Policy
    csp_directives = [
        "default-src 'self'",
        "script-src 'self' https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "connect-src 'self' ws: wss: http://localhost:* http://127.0.0.1:*",
        "font-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'"
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
        with open(theme_file, "r") as f:
            config = json.load(f)

        # Cache config and mtime
        theme_config = config
        theme_config_mtime = current_mtime

        logger.info(f"Theme config loaded and cached (mtime: {current_mtime})")
        return config

    except Exception as e:
        logger.error(f"Error loading theme config: {e}")
        return None

@app.get("/version")
async def get_version():
    versions = list(__changelog__.keys())[:2]
    changelog = {v: __changelog__[v] for v in versions}
    return {
        "version": __version__,
        "changelog": changelog,
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
            "text-secondary": "#cccccc"
        },
        "logo": "beardcraft.png"
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
            "logo": theme_data["logo"]
        }

        # Include decorations config if present
        if "decorations" in theme_data:
            result["decorations"] = theme_data["decorations"]

        return result

    except Exception as e:
        logger.error(f"Error loading theme: {e}")
        return default_theme

@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring"""
    now = datetime.now(timezone.utc)
    uptime = (now - app_start_time).total_seconds()

    return {
        "status": "healthy",
        "version": __version__,
        "uptime_seconds": round(uptime, 2),
        "sessions": {
            "active": len(sessions),
            "max": MAX_ACTIVE_SESSIONS,
            "usage_percent": round((len(sessions) / MAX_ACTIVE_SESSIONS) * 100, 2)
        },
        "rate_limits": {
            "tracked_ips_join": len(last_join_time),
            "tracked_ips_create": len(last_create_time)
        }
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

app.mount("/", StaticFiles(directory="public"), name="static")

# Combine FastAPI and Socket.IO
asgi_app = socketio.ASGIApp(sio, app)

# Socket.IO handlers
@sio.event
async def connect(sid, environ):
    pass  # Connection established

@sio.event
async def disconnect(sid):
    for session_id in sessions:
        if sid in sessions[session_id]["users"]:
            user = sessions[session_id]["users"][sid]
            username = user.get("username", "unknown")
            was_host = user.get("isHost", False)
            del sessions[session_id]["users"][sid]
            logger.info(f"User disconnected: {username} from session {session_id}")
            if was_host:
                await sio.emit('hostLeft', room=session_id)
            await sio.emit('usersUpdate', sessions[session_id]["users"], room=session_id)
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
    ip_addr = None
    session_id = data.get("sessionId")
    username = data.get("username")

    # Validate session_id format
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        await sio.emit("joinFailed", {"reason": "Invalid session ID"}, room=sid)
        return

    # Validate client_id format with strict regex
    if not isinstance(client_id, str) or not CLIENT_ID_RE.fullmatch(client_id):
        logger.warning(f"Invalid client ID format: {client_id[:20]}")
        await sio.emit("joinFailed", {"reason": "Invalid client ID"}, room=sid)
        return

    if "asgi.scope" in data:
        scope = data["asgi.scope"]
        if TRUST_PROXY:
            headers = dict(scope.get("headers", []))
            forwarded = headers.get(b"x-forwarded-for", b"").decode()
            if forwarded:
                ip_addr = forwarded.split(",")[0].strip()
        if not ip_addr and scope.get("client"):
            ip_addr, _ = scope["client"]

    now = datetime.now(timezone.utc)

    if ip_addr and now - last_join_time[ip_addr] < JOIN_RATE_LIMIT:
        await sio.emit("joinFailed", {"reason": "Too many join attempts. Please wait."}, room=sid)
        return

    if ip_addr:
        last_join_time[ip_addr] = now

    if session_id not in sessions:
        await sio.emit("joinFailed", {"reason": "Session not found"}, room=sid)
        return

    # Check user limit per session
    if len(sessions[session_id]["users"]) >= MAX_USERS_PER_SESSION:
        logger.warning(f"Session {session_id} full: {MAX_USERS_PER_SESSION} users")
        await sio.emit("joinFailed", {"reason": "Session is full"}, room=sid)
        return

    if not isinstance(username, str) or not USERNAME_RE.fullmatch(username):
        await sio.emit("joinFailed", {"reason": "Invalid username (letters only, max 20)."}, room=sid)
        return

    if sessions[session_id]["hostClientId"] is None:
        sessions[session_id]["hostClientId"] = client_id
        # Set deck from preset if provided
        deck_type = data.get("deckType", DEFAULT_DECK_TYPE)
        if deck_type in DECK_PRESETS:
            sessions[session_id]["deck"] = DECK_PRESETS[deck_type]
            sessions[session_id]["deckType"] = deck_type

    is_host = client_id == sessions[session_id]["hostClientId"]

    duplicate_client = any(u.get("clientId") == client_id for u in sessions[session_id]["users"].values())
    if duplicate_client:
        await sio.emit("joinFailed", {"reason": "Client already connected"}, room=sid)
        return

    user_data = {
        "username": username,
        "vote": None,
        "isHost": is_host,
        "clientId": client_id
    }

    if "wantsToVote" in data:
        user_data["wantsToVote"] = data["wantsToVote"]

    sessions[session_id]["users"][sid] = user_data
    sessions[session_id]["lastActivity"] = datetime.now(timezone.utc)

    logger.info(f"User joined: {username} -> session {session_id} (host={is_host})")

    await sio.enter_room(sid, session_id)

    # Tell the joining client which deck this session uses
    session_deck_type = sessions[session_id].get("deckType", DEFAULT_DECK_TYPE)
    await sio.emit("deckChanged", {"deckType": session_deck_type}, room=sid)

    await sio.emit("usersUpdate", sessions[session_id]["users"], room=session_id)

    if is_host and "wantsToVote" not in data:
        await sio.emit('askHostToJoinVoting', room=sid)

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

    # Validate vote is a value in the session's deck
    deck = sessions[session_id].get("deck", DECK_PRESETS[DEFAULT_DECK_TYPE])
    vote_check = int(value) if isinstance(value, (int, float)) else value
    if vote_check not in deck:
        logger.warning(f"Vote {value} not in deck for session {session_id}")
        return

    user = sessions[session_id]["users"].get(sid)
    if user:
        user["vote"] = value

        users = [
            u for u in sessions[session_id]["users"].values()
            if not (u.get("isHost") and u.get("wantsToVote") is False)
        ]

        all_voted = len(users) > 0 and all(u["vote"] is not None for u in users)

        await sio.emit("usersUpdate", sessions[session_id]["users"], room=session_id)

        if all_voted and not sessions[session_id]["revealed"]:
            sessions[session_id]["revealed"] = True

            count = 3
            async def countdown():
                nonlocal count
                while count >= 0:
                    await sio.emit("countdown", count, room=session_id)
                    await asyncio.sleep(1)
                    count -= 1

                deck = sessions[session_id].get("deck", DECK_PRESETS[DEFAULT_DECK_TYPE])
                # Build index lookup, excluding "?" from stats
                index_of = {v: i for i, v in enumerate(deck) if v != "?"}

                # Collect votes that are in the deck (excluding "?")
                voted = []
                for u in sessions[session_id]["users"].values():
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

                    # Median and outliers work for all deck types via index position
                    idxs = sorted(index_of[v] for _, v in voted)
                    median_idx = idxs[len(idxs) // 2]
                    vote_stats["median"] = deck[median_idx]

                    STEP_THRESHOLD = 2
                    vote_stats["outliers"] = [
                        name for (name, v) in voted
                        if abs(index_of[v] - median_idx) >= STEP_THRESHOLD
                    ]

                await sio.emit("revealVotes", {
                    "users": sessions[session_id]["users"],
                    "stats": vote_stats
                }, room=session_id)

            asyncio.create_task(countdown())

@sio.event
async def requestNewRound(sid, data):
    # Rate limiting: 3 new rounds per hour
    if not check_socket_rate_limit(sid, "requestNewRound", limit=3, window=3600):
        logger.warning(f"New round rate limit exceeded for socket {sid}")
        await sio.emit("joinFailed", {"reason": "Too many new round requests"}, room=sid)
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
        await sio.emit("joinFailed", {"reason": "Only host can request new round"}, room=sid)
        return

    # Determine deck for new round
    deck_type = data.get("deckType", DEFAULT_DECK_TYPE)
    if deck_type not in DECK_PRESETS:
        deck_type = DEFAULT_DECK_TYPE
    new_deck = DECK_PRESETS[deck_type]

    new_id = secrets.token_urlsafe(12)[:16]  # 16 URL-safe characters
    sessions[new_id] = {
        "users": {},
        "revealed": False,
        "hostClientId": old_session.get("hostClientId"),
        "createdAt": datetime.now(timezone.utc),
        "lastActivity": datetime.now(timezone.utc),
        "deck": new_deck,
        "deckType": deck_type,
    }

    username_map = {sockid: u["username"] for sockid, u in old_session.get("users", {}).items()}
    wants_to_vote_map = {sockid: u.get("wantsToVote") for sockid, u in old_session.get("users", {}).items()}

    logger.info(f"New round: {session_id} -> {new_id} by host {user.get('username', 'unknown')} (deck: {deck_type})")

    await sio.emit("redirectToNewSession", {
        "url": f"/session/{new_id}",
        "usernames": username_map,
        "wantsToVote": wants_to_vote_map,
        "deckType": deck_type
    }, room=session_id)

    del sessions[session_id]

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
    logger.info(f"Deck changed to {deck_type} in session {session_id}")

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
        await sio.emit("usersUpdate", sessions[session_id]["users"], room=session_id)

# Start server
if __name__ == "__main__":
    # Only enable reload in development
    reload_enabled = ENVIRONMENT == "development"
    uvicorn.run(
        "server:asgi_app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        reload=reload_enabled
    )
