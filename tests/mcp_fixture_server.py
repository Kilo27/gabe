"""A tiny stdio MCP server for tests/test_mcp.py. Run: python tests/mcp_fixture_server.py"""

import os
import sys
import time

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("fixture")


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def lookup(query: str, filters: dict | None = None) -> str:
    """Look something up."""
    extra = f" {sorted(filters.items())}" if filters else ""
    return f"found {query}{extra}"


@server.tool()
def save_note(text: str) -> str:
    """Save a note somewhere."""
    path = os.environ.get("FIXTURE_NOTES")
    if path:
        with open(path, "a") as f:
            f.write(text + "\n")
    return f"saved {text}"


@server.tool()
def broken() -> str:
    """Always fails."""
    raise ValueError("this tool is broken")


@server.tool()
def slow(seconds: float = 5) -> str:
    """Takes a while."""
    time.sleep(seconds)
    return "done"


@server.tool()
def pid() -> str:
    """The server's process id."""
    return str(os.getpid())


@server.tool()
def die() -> str:
    """Exits the server process."""
    os._exit(1)


if __name__ == "__main__":
    print("fixture starting", file=sys.stderr)
    server.run("stdio")
