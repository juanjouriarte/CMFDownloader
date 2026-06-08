import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)

from src.mcp_server import mcp

if __name__ == "__main__":
    # stdio transport — used by Claude Code locally
    # For remote Claude.ai integration, HTTPS via Cloudflare is required
    mcp.run()
