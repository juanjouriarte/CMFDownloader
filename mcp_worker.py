import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)

import uvicorn
from starlette.applications import Starlette
from starlette.types import ASGIApp, Receive, Scope, Send
from src.mcp_server import mcp

# Leave host as default "localhost" so FastMCP's SSE validation passes.
# We bind uvicorn to 0.0.0.0 ourselves and rewrite the Host header before
# FastMCP sees it — otherwise it expects Host: 0.0.0.0:8081 which nothing sends.
mcp.settings.port = 8081
mcp.settings.streamable_http_path = "/"


class HostRewriteMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = [(k, v) for k, v in scope.get("headers", []) if k != b"host"]
            headers.append((b"host", b"localhost:8081"))
            scope = {**scope, "headers": headers}
        await self.app(scope, receive, send)


def build_app() -> Starlette:
    """Serve modern Streamable HTTP and legacy SSE from one MCP process.

    nginx strips the external ``/mcp/`` prefix, so Streamable HTTP is mounted
    internally at ``/``. The existing ``/sse`` and ``/messages/`` routes stay
    available for clients that still use the older SSE transport.
    """
    sse_app = mcp.sse_app()
    streamable_app = mcp.streamable_http_app()
    return Starlette(
        routes=[*sse_app.routes, *streamable_app.routes],
        lifespan=lambda _app: mcp.session_manager.run(),
    )


if __name__ == "__main__":
    uvicorn.run(HostRewriteMiddleware(build_app()), host="0.0.0.0", port=8081)
