import ipaddress
from datetime import datetime, timedelta, timezone

from app.config import (
    MAX_RATE_LIMIT_ENTRIES,
    PROXY_DEPTH,
    RATE_LIMIT_WHITELIST,
    TRUST_PROXY,
    TRUSTED_PROXY_IPS,
)
from app.state import socket_ip_map, socket_rate_limits


def _pick_forwarded_hop(xff_value: str) -> str | None:
    """Pick the Nth-from-right hop of X-Forwarded-For per PROXY_DEPTH.

    Rightmost hop is the closest proxy; PROXY_DEPTH=1 = last-hop (safe default).
    """
    hops = [h.strip() for h in xff_value.split(",") if h.strip()]
    if not hops:
        return None
    idx = max(0, len(hops) - PROXY_DEPTH)
    return hops[idx]


def _is_peer_trusted(peer_ip: str | None) -> bool:
    """Return True if the TCP peer is allowed to supply X-Forwarded-For.

    When TRUSTED_PROXY_IPS is empty, all peers are trusted (backward compat with
    existing TRUST_PROXY=true deployments that don't set the allowlist).
    """
    if not peer_ip:
        return False
    if not TRUSTED_PROXY_IPS:
        return True
    try:
        addr = ipaddress.ip_address(peer_ip)
        return any(addr in net for net in TRUSTED_PROXY_IPS)
    except ValueError:
        return False


def get_client_ip(request: object) -> str | None:
    """Return client IP from the FastAPI Request, honouring TRUST_PROXY + TRUSTED_PROXY_IPS."""
    if TRUST_PROXY:
        peer = getattr(getattr(request, "client", None), "host", None)
        if _is_peer_trusted(peer):
            forwarded = request.headers.get("x-forwarded-for")  # type: ignore[union-attr]
            if forwarded:
                picked = _pick_forwarded_hop(forwarded)
                if picked:
                    return picked
    client = getattr(request, "client", None)
    return client.host if client else None


def _bound_dict(d: dict, max_entries: int | None = None) -> None:
    """Evict oldest entries (by insertion order) when dict exceeds cap."""
    if max_entries is None:
        max_entries = MAX_RATE_LIMIT_ENTRIES
    while len(d) > max_entries:
        try:
            oldest_key = next(iter(d))
            del d[oldest_key]
        except (StopIteration, KeyError):
            break


def is_ip_whitelisted(ip_str: str | None) -> bool:
    """Return True if ip_str matches any whitelisted network/address."""
    if not ip_str or not RATE_LIMIT_WHITELIST:
        return False
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in net for net in RATE_LIMIT_WHITELIST)
    except ValueError:
        return False


def check_socket_rate_limit(sid: str, action: str, limit: int = 30, window: int = 60) -> bool:
    """Return True if the action is within rate limits for this socket.

    Keyed by IP when available so rate limits persist across reconnections.
    Whitelisted IPs always pass. Modifies socket_rate_limits in-place.
    Uses LRU order: recently-active keys are moved to the end so eviction
    removes the least recently used key, not an arbitrary insertion-order entry.
    """
    ip = socket_ip_map.get(sid)
    if is_ip_whitelisted(ip):
        return True

    key = ip if ip else sid
    now = datetime.now(timezone.utc)

    if key not in socket_rate_limits:
        socket_rate_limits[key] = {}
    if action not in socket_rate_limits[key]:
        socket_rate_limits[key][action] = []

    socket_rate_limits[key][action] = [
        t for t in socket_rate_limits[key][action] if now - t < timedelta(seconds=window)
    ]

    if len(socket_rate_limits[key][action]) >= limit:
        return False

    # Move to end BEFORE _bound_dict so this key is never the eviction target.
    socket_rate_limits.move_to_end(key)
    _bound_dict(socket_rate_limits)
    socket_rate_limits[key][action].append(now)
    return True
