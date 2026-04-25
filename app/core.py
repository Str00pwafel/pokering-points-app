import asyncio
import logging
from contextlib import asynccontextmanager

import socketio
from fastapi import FastAPI

from app.config import ALLOW_CREDENTIALS, CORS_ORIGINS
from app.state import log_retention_cleanup, rate_limit_cleanup, session_cleanup
from version import __version__

logger = logging.getLogger("pokering")

# Socket.IO origin lock: wildcard "*" accepted as string by socketio;
# explicit list otherwise.
_sio_cors = "*" if "*" in CORS_ORIGINS else CORS_ORIGINS
sio: socketio.AsyncServer = socketio.AsyncServer(
    async_mode="asgi",
    max_http_buffer_size=1_000_000,
    cors_allowed_origins=_sio_cors,
)


@asynccontextmanager
async def lifespan(application: FastAPI):
    cleanup_task = asyncio.create_task(session_cleanup())
    rate_limit_task = asyncio.create_task(rate_limit_cleanup())
    log_retention_task = asyncio.create_task(log_retention_cleanup())
    yield
    # Graceful shutdown: warn connected clients before cancelling background work.
    try:
        await sio.emit("serverShutdown", {"reason": "Server is restarting"})
        await asyncio.sleep(0.2)  # brief flush window so websocket frames hit the wire
    except Exception as e:
        logger.warning(f"serverShutdown broadcast failed: {e}")
    cleanup_task.cancel()
    rate_limit_task.cancel()
    log_retention_task.cancel()


app: FastAPI = FastAPI(lifespan=lifespan, title="Pokering Points", version=__version__)

from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=ALLOW_CREDENTIALS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    max_age=3600,
)

# Combine FastAPI and Socket.IO — this is what uvicorn serves.
asgi_app = socketio.ASGIApp(sio, app)
