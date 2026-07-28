"""Unified MCP gateway for Lee Associates South Florida.

Mounts four independent MCP servers under one Starlette application:
  /enformion     - EnformionGO people/contact lookups
  /zoominfo      - ZoomInfo enrich and search
  /parcelscraper - Parcel scraper automation proxy
  /adminsite     - Admin site property intelligence proxy
"""

from __future__ import annotations

import contextlib
import os

from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from servers import adminsite_mcp, enformion_mcp, parcelscraper_mcp, zoominfo_mcp

load_dotenv()

MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))

MCP_SERVERS = [
    ("enformion", enformion_mcp),
    ("zoominfo", zoominfo_mcp),
    ("parcelscraper", parcelscraper_mcp),
    ("adminsite", adminsite_mcp),
]


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
            app=server.streamable_http_app(),
        )
    )

app = Starlette(routes=routes, lifespan=lifespan)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=MCP_HOST, port=MCP_PORT)
