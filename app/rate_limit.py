import ipaddress
from datetime import datetime, timedelta, timezone

from app.config import MAX_RATE_LIMIT_ENTRIES, PROXY_DEPTH, RATE_LIMIT_WHITELIST, TRUST_PROXY
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


def get_client_ip(request: object) -> str | None:
    """Return client IP from the FastAPI Request, honouring TRUST_PROXY."""
    if TRUST_PROXY:
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

    Whitelisted IPs always pass. Modifies socket_rate_limits in-place.
    """
    if is_ip_whitelisted(socket_ip_map.get(sid)):
        return True

    now = datetime.now(timezone.utc)

    socket_rate_limits[sid][action] = [
        t for t in socket_rate_limits[sid][action] if now - t < timedelta(seconds=window)
    ]

    if len(socket_rate_limits[sid][action]) >= limit:
        return False

    socket_rate_limits[sid][action].append(now)
    _bound_dict(socket_rate_limits)
    return True
