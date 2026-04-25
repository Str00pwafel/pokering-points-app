# Pokering Points — CLAUDE.md

Planning poker estimation app. Real-time, stateless. FastAPI + Socket.IO backend, vanilla JS frontend.

## Tech Stack

- **Python 3.13** (supports 3.10+)
- **FastAPI 0.135** + **Uvicorn 0.42** — ASGI server
- **python-socketio 5.16** — WebSocket/Socket.IO real-time layer
- **Pydantic 2.12** — input validation
- **Vanilla JS** frontend (no build step, no npm)
- **No database** — all state is in-memory

## Project Structure

```
server.py               # Thin entrypoint — imports app.core, runs uvicorn
version.py              # Version + changelog data
requirements.txt        # Python dependencies
app/
  config.py             # Env vars, constants, regex patterns
  logging_setup.py      # Logger, audit(), JSON formatter, request_id_var
  state.py              # sessions dict, rate-limit dicts, background tasks
  rate_limit.py         # IP whitelist, socket rate limiting, IP extraction
  core.py               # Creates sio (AsyncServer), FastAPI app, asgi_app
  routes.py             # HTTP routes + middleware (/, /create, /health, /decks, …)
  sockets.py            # Socket.IO event handlers (join, vote, changeDeck, …)
config/
  themes.json           # Theme definitions + date-based schedules
public/
  index.html            # Game session page
  welcome.html          # Landing page
  css/                  # Stylesheets
  javascript/
    index.js            # Main entry — imports all modules, wires event handlers
    state.js            # Shared mutable game state (S object), storage key migration
    connection.js       # Socket instance, reconnect logic, connection indicator
    cards.js            # Card rendering, vote selection, deck loading (/decks fetch)
    ui.js               # User list, status, toggle button label, version badge
    host.js             # Host settings modal, confirmHostSettings
    modal.js            # showModal, trapFocus
    toast.js            # showToast
    utils.js            # isValidUsername, escapeHTML, postCreate, setDocTitle
    theme-loader.js     # Dynamic theme application
    welcome.js          # Welcome page logic
  images/
logs/                   # Audit logs (auto-created, gitignored)
```

## Running Locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 server.py
```

App starts at http://localhost:8000. Auto-reloads when `ENVIRONMENT=development` (default).

## Environment Variables

| Variable           | Default            | Notes                                       |
| ------------------ | ------------------ | ------------------------------------------- |
| `SERVER_HOST`      | `0.0.0.0`          | Bind address                                |
| `SERVER_PORT`      | `8000`             | Port                                        |
| `ENVIRONMENT`      | `development`      | Set `production` to disable reload          |
| `CORS_ORIGINS`     | `*`                | Comma-separated list                        |
| `TRUST_PROXY`      | `false`            | Enable X-Forwarded-For parsing              |
| `LOG_DIR`          | `logs`             | Audit log directory                         |
| `LOG_MAX_BYTES`    | `5242880`          | 5MB per log file                            |
| `LOG_BACKUP_COUNT` | `3`                | Rotated log files to keep                   |
| `THEME_TZ`         | `Europe/Amsterdam` | IANA timezone for date-based theme schedule |

No API keys or DB credentials needed.

## Architecture Notes

**Session management** — sessions stored in a plain `dict`. Auto-expire: 2h idle, 24h absolute. Background cleanup runs every 5 min. Session IDs are 16-char cryptographically secure strings.

**Client identity** — 7-char crypto-random client ID stored in `sessionStorage`. Username persisted in `localStorage`.

**Host** — first user to join becomes host. Host controls: start new round, change deck (before votes cast), toggle own voting participation.

**Voting flow** — auto-reveal triggers after all eligible users vote (3-second countdown). Deck types: Fibonacci, Hours, T-Shirt. `?` always valid.

**Rate limiting** — per-socket (vote: 30/min, join: 5/min, changeDeck: 20/min, requestNewRound: 30/hr) and per-IP (session create: 3s cooldown, join: 5s cooldown).

**Security** — CSP headers, no unsafe-inline, HTTPS HSTS in production, X-Frame-Options DENY, input regex validation.

## Monitoring Endpoints

| Endpoint       | Description                                                      |
| -------------- | ---------------------------------------------------------------- |
| `GET /health`  | JSON — uptime, session count, rate limit stats                   |
| `GET /metrics` | Prometheus text format (includes votes/reveals/countdown gauges) |
| `GET /version` | Current version + last 2 changelogs                              |
| `GET /decks`   | JSON — deck presets + labels (fetched by frontend on load)       |

## Themes

Defined in `config/themes.json`. Date-range activated:

- `default` — blue, always active
- `christmas` — Dec 1–31, green/red + snowflakes
- `koningsdag` — Apr 23–30, orange/blue + crown

## Socket.IO Events

**Client → Server:** `join`, `vote`, `changeDeck`, `requestNewRound`, `hostVotingDecision`, `setVotingEnabled`

**Server → Client:** `usersUpdate`, `deckChanged`, `countdown`, `revealVotes`, `roundReset`, `hostLeft`, `joinFailed`, `sessionState`

## Commit Convention

Format: `PATCH{n}-MM/DD description` where `n` resets to 1 each day.
Example: `PATCH2-04/14 fix session cleanup race condition`

## Rules

### Version & changelog

- **Don't bump version or touch [version.py](version.py) / changelog for trivial changes** (CSS tweaks, theme config, copy edits). Only bump when user asks, or change is a user-facing feature/fix worth announcing.
- Changelog drift risk: `/changelog.html` is hand-maintained alongside `version.py` (Phase 4 plans to generate it).

### Planning

- **[plan.md](plan.md) is the source of truth for upcoming work.** Reference phase numbers when user asks about future features.
- **Don't preempt phase work in unrelated commits.** Example: don't split `server.py` into modules during a CSS fix — that's Phase 9. Stay scoped to the task.
- `plan.md` + `CLAUDE.md` are untracked locally; don't `git add` them unless user asks.

### Themes

- **Theme overrides use CSS variables with fallback** — `var(--theme-key, default-value)` in CSS. Add key under a theme's `colors` object in [config/themes.json](config/themes.json); `theme-loader.js` sets it on `:root`. Don't hardcode theme-specific values in stylesheets.
- **Dark overlay pattern** — bright-bg themes (koningsdag) should set `--user-list-bg: rgba(0,0,0,0.25)` for readable panels. Reuse this pattern for future themes with bright primary colors.
- **Testing a theme locally** — temporarily widen its schedule range in `themes.json` to include today's `MM-DD`. Schedule loop `break`s on first match — to test a later-listed theme, shrink earlier-listed ones or reorder. **Always revert schedule dates before committing.**
- **Theme config cache** — server reloads `themes.json` automatically via mtime check; no restart needed. Browser needs hard refresh (Ctrl+Shift+R) to re-run `theme-loader.js`.
- **Timezone caveat** — `datetime.now()` in server.py is naive (Phase 9 item). Koningsdag (04-23) assumes Europe/Amsterdam.

### Code style

- **No build step** — vanilla JS, edit `public/javascript/*.js` directly. No npm, no bundler, no transpile.
- **No inline styles/scripts in HTML** — CSP forbids `unsafe-inline`. Use classes + CSS vars, not `style="..."` on HTML-served elements. Runtime `element.style.x = y` from JS is fine.
- **No database / no persistence layer** — state is in-memory dicts. Don't add SQLite/Redis/etc. without explicit ask.
- **No tests yet** (Phase 9 plans them). Don't write tests unsolicited; if adding, use `pytest` + `pytest-asyncio` + `python-socketio` test client.

### Safety

- **Never skip hooks** (`--no-verify`) or force-push without explicit ask.
