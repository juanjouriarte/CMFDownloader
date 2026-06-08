import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)

from src.mcp_server import mcp

if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=8081)
