# Pokering Points — Security & Bug Fix Plan

Source: security audit + bug review, Apr 25 2026. All fixes ship under v2.0.0.

---

## Critical / High — v2.0.0 ✅

### SEC-11: Rate limit bypass via reconnect
- SID changes on every reconnect → per-SID limits reset → effective rate = unlimited
- Fix: key `socket_rate_limits` by IP (fall back to SID when IP unknown)
- Files: `app/rate_limit.py`

### SEC-12: client_id impersonation
- clientId is client-generated, unauthenticated, and broadcast via `usersUpdate`
- Any room member can steal another user's identity (and host role) with their clientId
- Fix: server issues a `reconnectToken` (token_urlsafe(32)) on first join, private to that socket
- Reconnect requires matching token; mismatch → `joinFailed`
- Remove clientId from `userJoined` broadcast; use `skip_sid` so joining user never receives own join
- Files: `app/sockets.py`, `app/state.py`, `public/javascript/connection.js`, `public/javascript/index.js`, `public/javascript/host.js`

### SEC-7: XFF IP spoofing bypasses all IP rate limits
- When `TRUST_PROXY=true`, crafted `X-Forwarded-For` header bypasses `CREATE_RATE_LIMIT` / `JOIN_RATE_LIMIT`
- Fix: add `TRUSTED_PROXY_IPS` env var; only trust XFF when TCP peer is in that allowlist
- If `TRUSTED_PROXY_IPS` is unset, all peers trusted (backward compat with existing `TRUST_PROXY=true` deploys)
- Files: `app/config.py`, `app/rate_limit.py`, `app/sockets.py`

### SEC-9: Socket.IO CORS + credentials mismatch
- `AsyncServer` defaults `cors_credentials=True` regardless of `ALLOW_CREDENTIALS`
- In dev (default `CORS_ORIGINS=*`), Socket.IO allows any origin with credentials
- Fix: pass `cors_credentials=ALLOW_CREDENTIALS` to `AsyncServer`
- File: `app/core.py`

### BUG-1: `countdown_active` stuck at 1 after cancel
- `requestNewRound` cancels countdown task; `CancelledError` propagates out of `asyncio.sleep`
- Cleanup code after the loop never runs: `_state.countdown_active` stays 1, `session["countdownActive"]` stays True
- Late joiners believe countdown is running; `requestNewRound` incorrectly blocked
- Fix: `except asyncio.CancelledError` clears session state; `finally` resets `_state.countdown_active`
- File: `app/sockets.py`

### BUG-3: Float vote crashes stats with KeyError
- `vote_check = int(value)` used for deck validation, but `user["vote"] = value` stores original float
- `index_of[1.5]` → KeyError in countdown → `revealVotes` never emitted; session stuck
- Also: `isinstance(True, int)` is True in Python → bool votes slip through; `NaN`/`Inf` cause `ValueError`
- Fix: store `vote_check` (not `value`); add `isinstance(value, bool)` guard; add `math.isfinite` guard
- File: `app/sockets.py`

---

## Medium — v2.0.0 ✅

| ID | Description | File(s) |
|----|-------------|---------|
| SEC-2 ✅ | Log injection via `X-Request-ID` — sanitize to `[A-Za-z0-9_-]` max 32 chars | `app/routes.py` |
| SEC-3 ✅ | Modal `innerHTML` XSS — CSP blocks JS exec but not HTML injection / phishing | `public/javascript/modal.js` |
| SEC-6 ✅ | Unauth `/health` + `/metrics` expose version, uptime, session counts | `app/routes.py`, `app/config.py` |
| SEC-10 ✅ | FIFO `_bound_dict` eviction lets attackers erase legitimate rate-limit windows | `app/rate_limit.py`, `app/state.py` |
| SEC-15 ✅ | `max_http_buffer_size=1_000_000` oversized for vote payloads → memory amplification | `app/core.py` |
| BUG-6 ✅ | `_bound_dict` called after append → can evict the just-written entry | `app/rate_limit.py` |
| BUG-9 ✅ | Double-disconnect within grace window emits duplicate `userLeft` events | `app/sockets.py` |

---

## Low

| ID | Description | File(s) |
|----|-------------|---------|
| BUG-2/8 | `changeDeck` + `join` store deck as reference — missing `list()` copy | `app/sockets.py` |
| BUG-10 | Stale `voteChanged` flag not cleared in `requestNewRound` | `app/sockets.py` |
| BUG-11 | SIDs exposed as keys in `usersUpdate` / `revealVotes` payloads | `app/sockets.py` |
| SEC-1 | CSRF on `POST /create` — no `Origin`/`Referer` check | `app/routes.py` |
| SEC-16 | Audit field injection in text log mode — values not quoted | `app/logging_setup.py` |
| SEC-21 | Unicode homograph usernames — zero-width chars permitted by `\w` | `app/config.py` |
