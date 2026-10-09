"""Unified MCP gateway for Lee Associates South Florida.

Mounts four independent MCP servers under one Starlette application:
  /enformion     - EnformionGO people/contact lookups
  /zoominfo      - ZoomInfo enrich and search
  /parcelscraper - Parcel scraper automation proxy
  /adminsite     - Admin site property intelligence proxy
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import os

from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

# Load .env before importing servers so module-level getenv() sees credentials.
load_dotenv()

from servers import adminsite_mcp, enformion_mcp, parcelscraper_mcp, zoominfo_mcp

MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))

# One bearer secret per MCP server. Clients send Authorization: Bearer <key>.
MCP_KEY_ENV = {
    "enformion": "ENFORMION_MCP_KEY",
    "zoominfo": "ZOOMINFO_MCP_KEY",
    "parcelscraper": "PARCELSCRAPER_MCP_KEY",
    "adminsite": "ADMINSITE_MCP_KEY",
}

MCP_SERVERS = [
    ("enformion", enformion_mcp),
    ("zoominfo", zoominfo_mcp),
    ("parcelscraper", parcelscraper_mcp),
    ("adminsite", adminsite_mcp),
]


def _load_mcp_key(env_name: str) -> bytes:
    key = os.getenv(env_name, "").strip()
    if len(key) < 32:
        raise RuntimeError(
            f"{env_name} must be set to a random secret of at least 32 characters"
        )
    return hashlib.sha256(key.encode()).digest()


class BearerAuth:
    """Reject HTTP requests that do not present this server's bearer key.

    The key is accepted only from the Authorization header. Query-string
    credentials are ignored so the secret never works in a URL.
    """

    def __init__(self, app: ASGIApp, token_digest: bytes) -> None:
        self.app = app
        self._token_digest = token_digest

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not self._authorized(scope):
            await self._reject(send)
            return
        await self.app(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        header = ""
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                header = value.decode("latin-1")
                break
        scheme, _, presented = header.strip().partition(" ")
        if scheme.lower() != "bearer" or not presented or any(ch.isspace() for ch in presented):
            return False
        digest = hashlib.sha256(presented.encode()).digest()
        return hmac.compare_digest(digest, self._token_digest)

    async def _reject(self, send: Send) -> None:
        body = b'{"error":"Unauthorized"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


async def health_check(_request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "servers": [name for name, _ in MCP_SERVERS],
        }
    )


@contextlib.asynccontextmanager
async def lifespan(_app: Starlette):
    async with contextlib.AsyncExitStack() as stack:
        for _, server in MCP_SERVERS:
            await stack.enter_async_context(server.session_manager.run())
        yield


routes = [
    Route("/health", health_check),
]

for name, server in MCP_SERVERS:
    server.settings.streamable_http_path = "/"
    server.settings.json_response = True
    routes.append(
        Mount(
            f"/{name}",
            app=BearerAuth(
                server.streamable_http_app(),
                _load_mcp_key(MCP_KEY_ENV[name]),
            ),
        )
    )

app = Starlette(routes=routes, lifespan=lifespan)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=MCP_HOST, port=MCP_PORT)
