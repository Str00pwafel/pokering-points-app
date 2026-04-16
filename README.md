# Pokering Points

Real-time planning poker estimation app. No accounts, no database — create a session, share the link, vote.

Built with FastAPI + Socket.IO backend and vanilla JavaScript frontend.

## Quick Start

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 server.py
```

App starts at **http://localhost:8000**.

## How It Works

1. Open the app and click **Start a Poker Session**
2. Share the session link with your team
3. Everyone picks an estimate from the card deck
4. Votes auto-reveal with a 3-second countdown when everyone has voted
5. See average, median, and outlier highlights
6. Host clicks **Start New Round** to re-vote (same session, no redirect)

### Roles

- **Host** (first to join): controls rounds, deck type, voting lock, and participation toggle
- **Participants**: join via shared link, pick cards, see results

### Deck Types

| Deck | Values |
|------|--------|
| Fibonacci | 1, 2, 3, 5, 8, 13, 21, ? |
| Hours | 1, 2, 4, 8, 16, 24, 40, ? |
| T-Shirt | XS, S, M, L, XL, XXL, ? |

Host can switch decks before any votes are cast.

## Environment Variables

All optional. Defaults work out of the box for local development.

| Variable | Default | Description |
|---|---|---|
| `SERVER_HOST` | `0.0.0.0` | Bind address |
| `SERVER_PORT` | `8000` | Port |
| `ENVIRONMENT` | `development` | Set `production` to disable auto-reload |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins |
| `TRUST_PROXY` | `false` | Enable `X-Forwarded-For` IP parsing (set `true` behind nginx/Caddy) |
| `LOG_DIR` | `logs` | Directory for audit log files |
| `LOG_MAX_BYTES` | `5242880` | Max size per log file (bytes, default 5MB) |
| `LOG_BACKUP_COUNT` | `3` | Number of rotated log files to keep |
| `RATE_LIMIT_WHITELIST` | *(empty)* | Comma-separated IPs/CIDRs to bypass all rate limits (e.g., `192.168.1.0/24,10.0.0.1`) |

No API keys or database credentials needed.

## Production Deployment

```bash
ENVIRONMENT=production TRUST_PROXY=true python3 server.py
```

When running behind a reverse proxy (nginx, Caddy, Traefik):
- Set `TRUST_PROXY=true` so rate limiting uses the real client IP
- Proxy WebSocket connections to the same port (Socket.IO needs both HTTP and WS)
- HSTS headers are automatically added when served over HTTPS

## Monitoring

| Endpoint | Description |
|---|---|
| `GET /health` | JSON — uptime, active sessions, rate limit stats |
| `GET /metrics` | Prometheus text format — sessions, users, rate limits |
| `GET /version` | Current version + last 2 changelogs |

## Themes

Date-activated themes defined in `config/themes.json`:

| Theme | Active | Visual |
|---|---|---|
| Default | Year-round | Blue tones |
| Christmas | Dec 1 - 31 | Green/red, snowflakes, Santa hat on logo |
| Koningsdag | Apr 23 - 30 | Orange/blue, crown on logo, Dutch flags |

Add custom themes by editing `themes.json` — no code changes needed.

## Session Limits

| Limit | Value |
|---|---|
| Max active sessions | 1,000 |
| Max users per session | 100 |
| Session idle timeout | 2 hours |
| Session absolute timeout | 24 hours |
| Session cleanup interval | 5 minutes |

## Rate Limits

| Action | Limit |
|---|---|
| Create session | 3s cooldown per IP |
| Join session | 5s cooldown per IP |
| Vote | 30/min per socket |
| Change deck | 20/min per socket |
| New round | 30/hour per socket |

## Tech Stack

- **Python 3.13** (supports 3.10+)
- **FastAPI** + **Uvicorn** — ASGI server
- **python-socketio** — real-time WebSocket layer
- **Vanilla JS** frontend — no build step, no npm
- **In-memory** sessions — no database required

## Security

- CSP headers (no unsafe-inline for scripts)
- X-Frame-Options DENY, X-Content-Type-Options nosniff
- HSTS when served over HTTPS
- Crypto-secure session IDs (16-char URL-safe tokens)
- Input validation via regex on all user inputs
- Rate limiting on all Socket.IO events and HTTP endpoints
