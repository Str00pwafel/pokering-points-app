import html
import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import app.state as _state
from app.config import (
    CHANGELOG_EXPANDED_COUNT,
    CREATE_RATE_LIMIT,
    DECK_PRESETS,
    DEFAULT_DECK_TYPE,
    ENVIRONMENT,
    LOG_RETENTION_DAYS,
    MAX_ACTIVE_SESSIONS,
    RATE_LIMIT_CLEANUP_INTERVAL,
    SESSION_CLEANUP_INTERVAL,
    SESSION_ID_RE,
    THEME_TZ,
)
from app.config import LOG_RETENTION_CHECK_INTERVAL as _LOG_RETENTION_CHECK_INTERVAL
from app.core import app
from app.logging_setup import audit, request_id_var
from app.rate_limit import _bound_dict, get_client_ip, is_ip_whitelisted
from app.state import (
    app_start_time,
    last_create_time,
    last_join_time,
    sessions,
    task_last_run,
)
from version import __changelog__, __version__

logger = logging.getLogger("pokering")

# ---------------------------------------------------------------------------
# Theme config cache (module-level so load_theme_config can mutate it)
# ---------------------------------------------------------------------------
_theme_config: dict | None = None
_theme_config_mtime: float | None = None


def load_theme_config() -> dict | None:
    """Load theme config with caching and file modification check."""
    global _theme_config, _theme_config_mtime

    theme_file = "config/themes.json"

    try:
        if not os.path.exists(theme_file):
            return None

        current_mtime = os.path.getmtime(theme_file)

        if _theme_config is not None and _theme_config_mtime == current_mtime:
            return _theme_config

        with open(theme_file) as f:
            config = json.load(f)

        _theme_config = config
        _theme_config_mtime = current_mtime

        logger.info(f"Theme config loaded and cached (mtime: {current_mtime})")
        return config

    except Exception as e:
        logger.error(f"Error loading theme config: {e}")
        return None


# ---------------------------------------------------------------------------
# Staleness thresholds for /health
# ---------------------------------------------------------------------------
_TASK_STALE_THRESHOLDS = {
    "session_cleanup": SESSION_CLEANUP_INTERVAL * 2,
    "rate_limit_cleanup": RATE_LIMIT_CLEANUP_INTERVAL * 2,
    "log_retention_cleanup": _LOG_RETENTION_CHECK_INTERVAL * 2,
}

# ---------------------------------------------------------------------------
# Changelog HTML shell
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
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

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

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
    if request.url.scheme == "https":
        csp_directives.append("upgrade-insecure-requests")
    response.headers["Content-Security-Policy"] = "; ".join(csp_directives)

    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def get_welcome():
    return FileResponse("public/welcome.html")


@app.post("/create")
async def create_session(request: Request):
    if len(sessions) >= MAX_ACTIVE_SESSIONS:
        logger.warning(f"Session creation rejected: max limit reached ({MAX_ACTIVE_SESSIONS})")
        return HTMLResponse(
            content="<html><body><h1>Server Full</h1><p>Maximum number of active sessions reached. Please try again later.</p></body></html>",
            status_code=503,
        )

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

    # token_urlsafe(12) produces exactly 16 URL-safe characters
    session_id = secrets.token_urlsafe(12)
    sessions[session_id] = {
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
    audit("session_created", session_id=session_id, ip=client_ip)
    # 303 See Other ensures browser issues GET to the session URL after POST /create
    return RedirectResponse(f"/session/{session_id}", status_code=303)


@app.get("/session/{session_id}", response_class=HTMLResponse)
async def get_session(session_id: str):
    if not SESSION_ID_RE.fullmatch(session_id):
        return HTMLResponse(
            content="<html><body><h1>Invalid Session ID</h1><p>Session ID must be 16 alphanumeric characters.</p></body></html>",
            status_code=400,
        )
    return FileResponse("public/index.html")


@app.get("/changelog.html", response_class=HTMLResponse)
async def get_changelog():
    """Server-rendered changelog. Source of truth: version.py __changelog__.

    Top N versions render with <details open>; older ones collapsed.
    """
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


@app.get("/theme")
async def get_theme():
    """Return the active theme based on today's date in THEME_TZ."""
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
        config = load_theme_config()
        if not config:
            return default_theme

        # Timezone-aware date for schedule comparison
        now = datetime.now(tz=ZoneInfo(THEME_TZ))
        current_date = now.strftime("%m-%d")

        active_theme_name = "default"
        for schedule_entry in config.get("schedule", []):
            start = schedule_entry.get("start")
            end = schedule_entry.get("end")
            theme = schedule_entry.get("theme")

            if start and end and theme:
                if start <= current_date <= end:
                    active_theme_name = theme
                    break

        theme_data = config["themes"].get(active_theme_name, config["themes"]["default"])

        result = {
            "name": theme_data["name"],
            "colors": theme_data["colors"],
            "logo": theme_data["logo"],
        }

        if "decorations" in theme_data:
            result["decorations"] = theme_data["decorations"]

        return result

    except Exception as e:
        logger.error(f"Error loading theme: {e}")
        return default_theme


@app.get("/decks")
async def get_decks():
    """Return deck presets so the frontend doesn't hardcode them."""
    return {
        "default": DEFAULT_DECK_TYPE,
        "decks": {
            name: {"label": preset["label"], "values": preset["values"]}
            for name, preset in DECK_PRESETS.items()
        },
    }


@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring."""
    now = datetime.now(timezone.utc)
    uptime = (now - app_start_time).total_seconds()

    tasks_report: dict = {}
    any_stale = False
    for name, threshold in _TASK_STALE_THRESHOLDS.items():
        last = task_last_run.get(name)
        if last is None:
            stale = uptime > threshold.total_seconds()
            tasks_report[name] = {"last_run_s_ago": None, "stale": stale}
        else:
            age = (now - last).total_seconds()
            stale = age > threshold.total_seconds()
            tasks_report[name] = {"last_run_s_ago": round(age, 1), "stale": stale}
        if stale:
            any_stale = True

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
    """Prometheus text-format metrics endpoint."""
    now = datetime.now(timezone.utc)
    uptime = (now - app_start_time).total_seconds()
    total_users = sum(len(session.get("users", {})) for session in sessions.values())

    votes_total = _state.votes_total
    reveals_total = _state.reveals_total
    countdown_active_gauge = _state.countdown_active

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

# HELP pokering_votes_total Total votes cast (excludes vote changes)
# TYPE pokering_votes_total counter
pokering_votes_total {votes_total}

# HELP pokering_reveals_total Total rounds revealed
# TYPE pokering_reveals_total counter
pokering_reveals_total {reveals_total}

# HELP pokering_countdown_active Whether a countdown is currently running
# TYPE pokering_countdown_active gauge
pokering_countdown_active {countdown_active_gauge}
"""
    return PlainTextResponse(content=metrics_text)


@app.get("/javascript/vendor/socket.io.min.js")
async def vendored_socket_io():
    # Vendored, version-pinned: long-cache to skip the round-trip on repeat visits.
    return FileResponse(
        "public/javascript/vendor/socket.io.min.js",
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# Static files — must be mounted last (catch-all)
app.mount("/", StaticFiles(directory="public"), name="static")
