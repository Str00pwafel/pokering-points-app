import asyncio
import re
import shortuuid
import socketio
import uvicorn

from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from version import __version__

# Session storage
sessions = {}
ABSOLUTE_TIMEOUT = timedelta(hours=24)
IDLE_TIMEOUT = timedelta(hours=2)
JOIN_RATE_LIMIT = timedelta(seconds=5)
SESSION_CLEANUP_INTERVAL = timedelta(hours=1)
USERNAME_RE = re.compile(r"^[A-Za-z]{1,20}$")

last_join_time = defaultdict(lambda: datetime.min)

# Session cleanup background task
async def session_cleanup():
    while True:
        await asyncio.sleep(SESSION_CLEANUP_INTERVAL.total_seconds())
        now = datetime.now(timezone.utc)
        to_remove = []
        for sid, session in list(sessions.items()):
            last_activity = session.get("lastActivity", now)
            created_at = session.get("createdAt", now)
            if isinstance(last_activity, str):
                last_activity = datetime.fromisoformat(last_activity)
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
            if now - last_activity > IDLE_TIMEOUT or now - created_at > ABSOLUTE_TIMEOUT:
                to_remove.append(sid)
        for sid in to_remove:
            del sessions[sid]

# Lifespan handler
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(session_cleanup())
    yield
    task.cancel()

# Initialize FastAPI and Socket.IO
sio = socketio.AsyncServer(async_mode='asgi', max_http_buffer_size=1_000_000)
app = FastAPI(lifespan=lifespan, title="Pokering Points", version=__version__)

# Routes first
@app.get("/", response_class=HTMLResponse)
async def get_welcome():
    return FileResponse("public/welcome.html")

@app.get("/create")
async def create_session():
    session_id = shortuuid.ShortUUID().random(length=6)
    sessions[session_id] = {
        "users": {},
        "revealed": False,
        "hostClientId": None,
        "createdAt": datetime.now(timezone.utc),
        "lastActivity": datetime.now(timezone.utc),
        "deck": [1, 2, 3, 5, 8, 13, 21],
    }
    return RedirectResponse(f"/session/{session_id}")

@app.get("/session/{session_id}", response_class=HTMLResponse)
async def get_session(session_id: str):
    return FileResponse("public/index.html")

@app.middleware("http")
async def add_version_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-App-Version"] = __version__
    return response

@app.get("/version")
async def get_version():
    return {"version": __version__}

app.mount("/", StaticFiles(directory="public"), name="static")

# Combine FastAPI and Socket.IO
asgi_app = socketio.ASGIApp(sio, app)

# Socket.IO handlers
@sio.event
async def connect(sid, environ):
    print(f"Client connected: {sid}")

@sio.event
async def disconnect(sid):
    for session_id in sessions:
        if sid in sessions[session_id]["users"]:
            del sessions[session_id]["users"][sid]
            await sio.emit('usersUpdate', sessions[session_id]["users"], room=session_id)
            break

@sio.event
async def join(sid, data):
    deck = data.get("deck")
    client_id = data.get("clientId")
    ip_addr = None
    session_id = data.get("sessionId")
    username = data.get("username")

    if "asgi.scope" in data and data["asgi.scope"].get("client"):
        ip_addr, _ = data["asgi.scope"]["client"]

    now = datetime.now()

    if ip_addr and now - last_join_time[ip_addr] < JOIN_RATE_LIMIT:
        await sio.emit("joinFailed", {"reason": "Too many join attempts. Please wait."}, room=sid)
        return

    if ip_addr:
        last_join_time[ip_addr] = now

    if session_id not in sessions:
        await sio.emit("joinFailed", {"reason": "Session not found"}, room=sid)
        return

    if not isinstance(username, str) or not USERNAME_RE.fullmatch(username):
        await sio.emit("joinFailed", {"reason": "Invalid username (letters only, max 20)."}, room=sid)
        return

    if sessions[session_id]["hostClientId"] is None:
        sessions[session_id]["hostClientId"] = client_id
        if isinstance(deck, list):
            try:
                clean = [int(v) for v in deck if isinstance(v, (int, float))]
                seen = set()
                clean = [v for v in clean if not (v in seen or seen.add(v))]
                if clean:
                    sessions[session_id]["deck"] = clean
            except Exception:
                pass

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

    await sio.enter_room(sid, session_id)
    await sio.emit("usersUpdate", sessions[session_id]["users"], room=session_id)

    if is_host and "wantsToVote" not in data:
        await sio.emit('askHostToJoinVoting', room=sid)

@sio.event
async def vote(sid, data):
    session_id = data.get("sessionId")
    value = data.get("value")

    if session_id not in sessions:
        return
    if not (isinstance(value, (int, float)) or value == "?"):
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

                deck = sessions[session_id].get("deck", [1, 2, 3, 5, 8, 13, 21])
                index_of = {v: i for i, v in enumerate(deck)}

                voted = [
                    (u["username"], int(u["vote"]))
                    for u in sessions[session_id]["users"].values()
                    if isinstance(u["vote"], (int, float)) and int(u["vote"]) in index_of
                ]

                vote_stats = {}
                if voted:
                    avg = sum(v for _, v in voted) / len(voted)
                    vote_stats["average"] = round(avg, 2)

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
    session_id = data.get("sessionId")
    old_session = sessions.get(session_id)

    if old_session is None:
        return

    user = old_session["users"].get(sid)
    if not user or not user.get("isHost"):
        await sio.emit("joinFailed", {"reason": "Only host can request new round"}, room=sid)
        return

    new_id = shortuuid.ShortUUID().random(length=6)
    sessions[new_id] = {
        "users": {},
        "revealed": False,
        "hostClientId": old_session.get("hostClientId"),
        "createdAt": datetime.now(timezone.utc),
        "lastActivity": datetime.now(timezone.utc),
        "deck": old_session.get("deck", [1, 2, 3, 5, 8, 13, 21]),
    }

    username_map = {sockid: u["username"] for sockid, u in old_session.get("users", {}).items()}
    wants_to_vote_map = {sockid: u.get("wantsToVote") for sockid, u in old_session.get("users", {}).items()}

    await sio.emit("redirectToNewSession", {
        "url": f"/session/{new_id}",
        "usernames": username_map,
        "wantsToVote": wants_to_vote_map
    }, room=session_id)

    del sessions[session_id]

@sio.event
async def hostVotingDecision(sid, data):
    session_id = data.get("sessionId")
    wants_to_vote = data.get("wantsToVote")

    if session_id not in sessions:
        return

    user = sessions[session_id]["users"].get(sid)
    if user and user.get("isHost"):
        user["wantsToVote"] = wants_to_vote
        await sio.emit("usersUpdate", sessions[session_id]["users"], room=session_id)

# Start server
if __name__ == "__main__":
    uvicorn.run("server:asgi_app", host="0.0.0.0", port=8000, reload=False)
