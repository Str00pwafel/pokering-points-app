# Thin entrypoint — all logic lives in the app/ package.
# The string form "server:asgi_app" is required for uvicorn --reload to work.
import uvicorn

import app.routes  # noqa: F401 — registers HTTP handlers on app.core.app
import app.sockets  # noqa: F401 — registers Socket.IO handlers on app.core.sio
from app.config import ENVIRONMENT, SERVER_HOST, SERVER_PORT
from app.core import asgi_app  # noqa: F401 — exported for "server:asgi_app" string ref

if __name__ == "__main__":
    reload_enabled = ENVIRONMENT == "development"
    uvicorn.run("server:asgi_app", host=SERVER_HOST, port=SERVER_PORT, reload=reload_enabled)
