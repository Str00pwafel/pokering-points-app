import asyncio
import shortuuid
import socketio
import uvicorn

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# Session storage
sessions = {}
SESSION_CLEANUP_INTERVAL = 60  # in seconds

# Session cleanup background task
async def session_cleanup():
    while True:
        await asyncio.sleep(SESSION_CLEANUP_INTERVAL)
        empty_sessions = [sid for sid, s in sessions.items() if not s["users"]]
        for sid in empty_sessions:
            del sessions[sid]

# Lifespan handler
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(session_cleanup())
    yield
    task.cancel()

# Initialize FastAPI and Socket.IO
sio = socketio.AsyncServer(async_mode='asgi', max_http_buffer_size=1_000_000)
app = FastAPI(lifespan=lifespan)

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
        "hostClientId": None
    }
    return RedirectResponse(f"/session/{session_id}")

@app.get("/session/{session_id}", response_class=HTMLResponse)
async def get_session(session_id: str):
    return FileResponse("public/index.html")

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
    session_id = data.get("sessionId")
    username = data.get("username")
    client_id = data.get("clientId")

    if session_id not in sessions:
        await sio.emit("joinFailed", {"reason": "Session not found"}, room=sid)
        return

    if not isinstance(username, str) or not username.isalpha() or len(username) > 24:
        await sio.emit("joinFailed", {"reason": "Invalid username"}, room=sid)
        return

    if sessions[session_id]["hostClientId"] is None:
        sessions[session_id]["hostClientId"] = client_id

    is_host = client_id == sessions[session_id]["hostClientId"]

    duplicate_client = any(user.get("clientId") == client_id for user in sessions[session_id]["users"].values())
    if duplicate_client:
        await sio.emit("joinFailed", {"reason": "Client already connected"}, room=sid)
        return

    sessions[session_id]["users"][sid] = {"username": username, "vote": None, "isHost": is_host, "clientId": client_id}
    await sio.enter_room(sid, session_id)
    await sio.emit("usersUpdate", sessions[session_id]["users"], room=session_id)

@sio.event
async def vote(sid, data):
    session_id = data.get("sessionId")
    value = data.get("value")

    if session_id not in sessions:
        return
    if not isinstance(value, int) or not (1 <= value <= 10):
        return

    user = sessions[session_id]["users"].get(sid)
    if user:
        user["vote"] = value

        users = list(sessions[session_id]["users"].values())
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
                await sio.emit("revealVotes", sessions[session_id]["users"], room=session_id)

            asyncio.create_task(countdown())

@sio.event
async def requestNewRound(sid, data):
    session_id = data.get("sessionId")
    old_session = sessions.get(session_id)

    if old_session is None:
        return

    new_id = shortuuid.ShortUUID().random(length=6)
    sessions[new_id] = {
        "users": {},
        "revealed": False,
        "hostClientId": old_session.get("hostClientId")
    }

    username_map = {sockid: user["username"] for sockid, user in old_session.get("users", {}).items()}

    await sio.emit("redirectToNewSession", {"url": f"/session/{new_id}", "usernames": username_map}, room=session_id)

    # Clean up old session
    del sessions[session_id]

# Start server
if __name__ == "__main__":
    uvicorn.run("server:asgi_app", host="0.0.0.0", port=8000, reload=True)
