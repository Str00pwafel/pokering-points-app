import ipaddress
import logging
import os
import re

# ---------------------------------------------------------------------------
# Server / environment
# ---------------------------------------------------------------------------
SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT: int = int(os.getenv("SERVER_PORT", "8000"))
CORS_ORIGINS: list[str] = [
    o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()
]
ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
TRUST_PROXY: bool = os.getenv("TRUST_PROXY", "false").lower() in ("true", "1", "yes")
# PROXY_DEPTH: how many reverse proxies sit in front. Takes the Nth-from-right hop of
# X-Forwarded-For. Default 1 = last-hop (rightmost is typically the closest proxy;
# client is leftmost but can be spoofed when depth=0).
PROXY_DEPTH: int = max(1, int(os.getenv("PROXY_DEPTH", "1")))
LOG_RETENTION_DAYS: int = int(os.getenv("LOG_RETENTION_DAYS", "30"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR: str = os.getenv("LOG_DIR", "logs")
LOG_MAX_BYTES: int = int(os.getenv("LOG_MAX_BYTES", 5 * 1024 * 1024))  # 5 MB default
LOG_BACKUP_COUNT: int = int(os.getenv("LOG_BACKUP_COUNT", 3))
# "text" (human-readable) or "json" (one-line JSON per record).
LOG_FORMAT: str = os.getenv("LOG_FORMAT", "text").strip().lower()

# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------
# IANA timezone name used for date-schedule comparisons.
THEME_TZ: str = os.getenv("THEME_TZ", "Europe/Amsterdam")

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
# Prevents memory growth under IPv6 flood
MAX_RATE_LIMIT_ENTRIES: int = int(os.getenv("MAX_RATE_LIMIT_ENTRIES", "10000"))

_config_logger = logging.getLogger(__name__)

# Comma-separated IPs or CIDR ranges (e.g. "192.168.1.0/24,10.0.0.1")
_raw_whitelist: str = os.getenv("RATE_LIMIT_WHITELIST", "").strip()
RATE_LIMIT_WHITELIST: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
if _raw_whitelist:
    for _entry in _raw_whitelist.split(","):
        _entry = _entry.strip()
        if _entry:
            try:
                RATE_LIMIT_WHITELIST.append(ipaddress.ip_network(_entry, strict=False))
            except ValueError:
                _config_logger.warning(f"Invalid RATE_LIMIT_WHITELIST entry ignored: {_entry}")

# Comma-separated IPs or CIDR ranges of trusted reverse proxies.
# Only honoured when TRUST_PROXY=true. If empty, all peers are trusted (backward compat).
# Set this to your proxy's IP(s) to prevent XFF spoofing from direct clients.
_raw_trusted_proxies: str = os.getenv("TRUSTED_PROXY_IPS", "").strip()
TRUSTED_PROXY_IPS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
if _raw_trusted_proxies:
    for _entry in _raw_trusted_proxies.split(","):
        _entry = _entry.strip()
        if _entry:
            try:
                TRUSTED_PROXY_IPS.append(ipaddress.ip_network(_entry, strict=False))
            except ValueError:
                _config_logger.warning(f"Invalid TRUSTED_PROXY_IPS entry ignored: {_entry}")

# ---------------------------------------------------------------------------
# Session limits & timeouts
# ---------------------------------------------------------------------------
from datetime import timedelta  # noqa: E402  (after stdlib constants)

MAX_ACTIVE_SESSIONS: int = 1000
MAX_USERS_PER_SESSION: int = 100
ABSOLUTE_TIMEOUT: timedelta = timedelta(hours=24)
IDLE_TIMEOUT: timedelta = timedelta(hours=2)
JOIN_RATE_LIMIT: timedelta = timedelta(seconds=5)
SESSION_CLEANUP_INTERVAL: timedelta = timedelta(minutes=5)
RATE_LIMIT_CLEANUP_INTERVAL: timedelta = timedelta(minutes=10)
CREATE_RATE_LIMIT: timedelta = timedelta(seconds=3)
LOG_RETENTION_CHECK_INTERVAL: timedelta = timedelta(hours=6)
RECONNECT_GRACE: int = 2  # seconds — delay leave broadcasts to tolerate brief disconnects

# ---------------------------------------------------------------------------
# Input validation patterns
# ---------------------------------------------------------------------------
# Username: 1-30 chars of letters (any script), digits, spaces, hyphens, apostrophes,
# underscores. Control chars (incl \n, \t) stripped before regex check; leading/trailing
# whitespace trimmed.
USERNAME_RE: re.Pattern[str] = re.compile(r"^[\w\s\-']{1,30}$", re.UNICODE)
_CONTROL_CHARS_RE: re.Pattern[str] = re.compile(
    r"[\x00-\x1F\x7F"       # ASCII control chars
    r"\u00AD"                # soft hyphen
    r"\u200B-\u200F"         # ZW space, ZWNJ, ZWJ, LRM, RLM
    r"\u2028-\u202F"         # line/paragraph separators + bidi formatting
    r"\u2060-\u206F"         # word joiner + invisible formatting
    r"\uFEFF"                # BOM / zero-width no-break space
    r"]"
)
SESSION_ID_RE: re.Pattern[str] = re.compile(
    r"^[A-Za-z0-9_\-]{16}$"
)  # matches token_urlsafe(12) length
CLIENT_ID_RE: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9\-_]{7,36}$")


def sanitize_username(raw: object) -> str | None:
    """Strip control chars, trim whitespace, return None if empty or invalid."""
    if not isinstance(raw, str):
        return None
    cleaned = _CONTROL_CHARS_RE.sub("", raw).strip()
    if not cleaned or not USERNAME_RE.fullmatch(cleaned):
        return None
    return cleaned


# ---------------------------------------------------------------------------
# Deck presets
# ---------------------------------------------------------------------------
DECK_PRESETS: dict[str, dict] = {
    "fibonacci": {
        "label": "Fibonacci (1-21)",
        "values": [1, 2, 3, 5, 8, 13, 21, "?"],
    },
    "hours": {
        "label": "Hours (1-40)",
        "values": [1, 2, 4, 8, 16, 24, 40, "?"],
    },
    "tshirt": {
        "label": "T-Shirt (XS-XXL)",
        "values": ["XS", "S", "M", "L", "XL", "XXL", "?"],
    },
}
DEFAULT_DECK_TYPE: str = "fibonacci"

# ---------------------------------------------------------------------------
# CORS credential handling
# ---------------------------------------------------------------------------
ALLOW_CREDENTIALS: bool = True
if ALLOW_CREDENTIALS and "*" in CORS_ORIGINS:
    if ENVIRONMENT == "production":
        raise RuntimeError(
            "CORS misconfig: CORS_ORIGINS='*' with credentials enabled is invalid in production. "
            "Set CORS_ORIGINS to explicit origins (e.g., 'https://example.com')."
        )
    # Degrade rather than crash dev; warning emitted after logging is configured
    ALLOW_CREDENTIALS = False

# ---------------------------------------------------------------------------
# Misc constants
# ---------------------------------------------------------------------------
CHANGELOG_EXPANDED_COUNT: int = 5  # most-recent N versions start expanded; older collapsed

# ---------------------------------------------------------------------------
# Metrics auth
# ---------------------------------------------------------------------------
# Set to a non-empty value to require "Authorization: Bearer <token>" on /health and /metrics.
# Leave empty to keep endpoints open (backward compat).
METRICS_TOKEN: str = os.getenv("METRICS_TOKEN", "").strip()
