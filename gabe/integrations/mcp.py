"""MCP servers as integrations: every tool an MCP server offers becomes a Gabe tool.

    integrations:
      - name: mcp
        settings:
          call_timeout: 60
          servers:
            notion: {command: npx, args: [-y, "@notionhq/notion-mcp-server"],
                     env: {NOTION_TOKEN: "${NOTION_TOKEN}"}}
            linear: {url: https://mcp.linear.app/mcp,
                     headers: {Authorization: "Bearer ${LINEAR_TOKEN}"}}

Tools are named `<server>__<tool>` and belong to the integration `<server>`, so the
usual policy rules apply (`policy.integrations: {notion: ask}`). They are `external`
unless the config says otherwise: a server's own annotations are hints from a third
party, only trusted when `trust_annotations: true`.

Gabe's agent loop is synchronous and rebuilds its tool registry often, so servers are
not started per run. One background asyncio loop (a daemon thread) holds every server's
session open for the life of the process; `setup` reuses live sessions and their tool
lists, reconnects dead ones and closes servers that left the config. A server that fails
to start is logged and skipped: its tools are simply absent.
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gabe.tools.registry import Risk, Tool, ToolRegistry

log = logging.getLogger("gabe.mcp")

DEFAULT_CALL_TIMEOUT = 60.0
DEFAULT_CONNECT_TIMEOUT = 30.0
HEARTBEAT = 30.0     # seconds between pings that notice a server has gone away
RETRY_AFTER = 60.0  # seconds before a failed server is tried again with the same config
_NAME_OK = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_ENV_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_CONNECTION_KEYS = ("command", "args", "env", "cwd", "url", "headers")


# -- config helpers ----------------------------------------------------------------


def expand(value: Any) -> Any:
    """Replace ${VAR} with the environment's value (empty when unset), recursively."""
    if isinstance(value, str):
        return _ENV_VAR.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [expand(v) for v in value]
    if isinstance(value, dict):
        return {str(k): expand(v) for k, v in value.items()}
    return value


def connection_of(spec: dict[str, Any]) -> dict[str, Any]:
    """The part of a server's config that decides the connection, with ${VAR}s expanded."""
    return {k: expand(spec[k]) for k in _CONNECTION_KEYS if spec.get(k) is not None}


def config_key(conn: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(conn, sort_keys=True, default=str).encode()).hexdigest()


def tool_name(server: str, tool: str) -> str:
    """`<server>__<tool>`, made safe for every provider's tool-name rules (≤64 chars)."""
    raw = f"{server}__{tool}"
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", raw)
    if len(name) > 64:
        name = f"{name[:55]}_{hashlib.sha1(raw.encode()).hexdigest()[:8]}"
    assert _NAME_OK.match(name), name
    return name


def _stable(value: Any) -> Any:
    """Dict keys sorted at every level, so schemas are byte-stable between runs."""
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def input_schema_of(raw: dict[str, Any] | None) -> dict[str, Any]:
    s = dict(raw or {})
    s["type"] = "object"
    if not isinstance(s.get("properties"), dict):
        s["properties"] = {}
    return _stable(s)


def risk_of(name: str, annotations: Any, settings: dict[str, Any]) -> Risk:
    """Per-tool override > trusted read-only hint > server default > external."""
    per_tool = settings.get("tools") or {}
    if name in per_tool:
        return Risk(per_tool[name])
    if settings.get("trust_annotations") and getattr(annotations, "read_only_hint", None) is True:
        return Risk.READ
    return Risk(settings.get("risk") or Risk.EXTERNAL.value)


def result_text(result: Any) -> str:
    """Flatten an MCP CallToolResult into text for the model."""
    parts: list[str] = []
    for block in result.content or []:
        kind = getattr(block, "type", "")
        if kind == "text":
            parts.append(block.text)
        elif kind in ("image", "audio"):
            parts.append(f"[{kind}: {getattr(block, 'mime_type', None) or 'unknown type'}]")
        elif kind == "resource_link":
            parts.append(f"[link: {block.uri}]")
        elif kind == "resource":
            res = block.resource
            text = getattr(res, "text", None)
            parts.append(text if text is not None else f"[resource: {res.uri}]")
        else:
            parts.append(f"[{kind or 'content'}]")
    has_text = any(getattr(b, "type", "") == "text" for b in result.content or [])
    if not has_text and result.structured_content is not None:
        parts.append(json.dumps(result.structured_content, sort_keys=True, default=str))
    return "\n".join(parts) if parts else "(no output)"


# -- the connection manager ----------------------------------------------------------


@dataclass
class _Server:
    name: str
    key: str
    conn: dict[str, Any]
    log_path: Path | None = None
    session: Any = None
    tools: list[Any] = field(default_factory=list)
    error: str | None = None
    failed_at: float = 0.0
    registered: int = 0
    ready: asyncio.Event | None = None
    stop: asyncio.Event | None = None
    task: concurrent.futures.Future | None = None

    @property
    def alive(self) -> bool:
        return (self.session is not None and self.task is not None and not self.task.done()
                and self.error is None)

    @property
    def transport(self) -> str:
        return "http" if self.conn.get("url") else "stdio"


class Manager:
    """Holds every MCP session open on one background event loop."""

    def __init__(self) -> None:
        self.servers: dict[str, _Server] = {}
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.spawned = 0  # connections opened, for tests

    # -- the loop ---------------------------------------------------------------

    def loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None or self._loop.is_closed():
                loop = asyncio.new_event_loop()
                t = threading.Thread(target=loop.run_forever, name="gabe-mcp", daemon=True)
                t.start()
                self._loop = loop
            return self._loop

    def run(self, coro, timeout: float | None):
        return asyncio.run_coroutine_threadsafe(coro, self.loop()).result(timeout)

    # -- servers ----------------------------------------------------------------

    def sync(self, wanted: dict[str, dict[str, Any]], log_dir: Path | None,
             connect_timeout: float = DEFAULT_CONNECT_TIMEOUT) -> None:
        """Make the open servers match `wanted` (name -> connection config)."""
        with self._lock:
            for name in [n for n in self.servers if n not in wanted]:
                self._close(self.servers.pop(name))
            starting: list[_Server] = []
            for name, conn in wanted.items():
                key = config_key(conn)
                old = self.servers.get(name)
                if old and old.key == key:
                    if old.alive:
                        continue
                    if old.error and time.monotonic() - old.failed_at < RETRY_AFTER:
                        continue
                if old:
                    self._close(old)
                srv = _Server(name, key, conn, log_dir / f"{name}.log" if log_dir else None)
                self.servers[name] = srv
                self._start(srv)
                starting.append(srv)
            deadline = time.monotonic() + connect_timeout
            for srv in starting:
                self._await_ready(srv, max(0.1, deadline - time.monotonic()))

    def reconnect(self, name: str, connect_timeout: float = DEFAULT_CONNECT_TIMEOUT) -> _Server:
        with self._lock:
            old = self.servers.get(name)
            if old is None:
                raise RuntimeError(f"MCP server '{name}' is no longer configured")
            self._close(old)
            srv = _Server(name, old.key, old.conn, old.log_path, registered=old.registered)
            self.servers[name] = srv
            self._start(srv)
            self._await_ready(srv, connect_timeout)
            return srv

    def _start(self, srv: _Server) -> None:
        self.spawned += 1
        loop = self.loop()

        async def make_events():
            srv.ready, srv.stop = asyncio.Event(), asyncio.Event()

        asyncio.run_coroutine_threadsafe(make_events(), loop).result(5)
        srv.task = asyncio.run_coroutine_threadsafe(self._hold(srv), loop)

    def _await_ready(self, srv: _Server, timeout: float) -> None:
        async def wait():
            await asyncio.wait_for(srv.ready.wait(), timeout)

        try:
            self.run(wait(), timeout + 1)
        except Exception:
            srv.error = srv.error or f"didn't start within {timeout:.0f}s"
        if srv.error:
            srv.failed_at = time.monotonic()
            log.warning("MCP server %r unavailable: %s", srv.name, srv.error)
            self._close(srv)

    async def _hold(self, srv: _Server) -> None:
        """Open the session, list its tools, then keep it open until told to stop."""
        from mcp import ClientSession
        from mcp.types import PaginatedRequestParams

        errlog = None
        try:
            if srv.log_path:
                srv.log_path.parent.mkdir(parents=True, exist_ok=True)
                errlog = open(srv.log_path, "a")
            async with _transport(srv.conn, errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools, cursor = [], None
                    while True:
                        page = await session.list_tools(
                            params=PaginatedRequestParams(cursor=cursor) if cursor else None)
                        tools.extend(page.tools)
                        cursor = page.next_cursor
                        if not cursor:
                            break
                    srv.tools = sorted(tools, key=lambda t: t.name)
                    srv.session = session
                    srv.error = None
                    srv.ready.set()
                    await _watch(srv, session)
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # noqa: BLE001 - one server's failure never breaks Gabe
            srv.error = _describe(e)
        finally:
            srv.session = None
            if errlog:
                errlog.close()
            if not srv.ready.is_set():
                srv.error = srv.error or "closed before it was ready"
                srv.ready.set()

    def _close(self, srv: _Server, wait: float = 5.0) -> None:
        if srv.task is None or srv.task.done() or self._loop is None or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(srv.stop.set)
        try:
            srv.task.result(wait)
        except Exception:
            srv.task.cancel()

    def close_all(self) -> None:
        with self._lock:
            for srv in list(self.servers.values()):
                self._close(srv, wait=3.0)
            self.servers.clear()
            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._loop.stop)

    # -- calls ------------------------------------------------------------------

    def call(self, server: str, tool: str, args: dict[str, Any], timeout: float) -> str:
        srv = self.servers.get(server)
        if srv is None or not srv.alive:
            srv = self.reconnect(server)
            if not srv.alive:
                raise RuntimeError(f"MCP server '{server}' isn't available: {srv.error}")
        try:
            result = self._call(srv, tool, args, timeout)
        except TimeoutError:
            raise
        except Exception as e:
            if _is_closed(e):
                # Not retried: the call may have run before the server went away. The next
                # call (or registry rebuild) reconnects.
                srv.error = "connection closed"
                self._close(srv)
                raise RuntimeError(f"Lost the connection to {server} ({tool} may not have run). "
                                   "It reconnects on the next try.") from e
            raise RuntimeError(f"{server} {tool} failed: {_describe(e)}") from e
        text = result_text(result)
        if result.is_error:
            raise RuntimeError(text)
        return text

    def _call(self, srv: _Server, tool: str, args: dict[str, Any], timeout: float):
        fut = asyncio.run_coroutine_threadsafe(srv.session.call_tool(tool, args), self.loop())
        try:
            return fut.result(timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise TimeoutError(f"{srv.name} {tool} didn't finish within {timeout:g}s") from None

    # -- status -----------------------------------------------------------------

    def status(self) -> dict[str, dict[str, Any]]:
        out = {}
        for name, srv in sorted(self.servers.items()):
            out[name] = {
                "state": "connected" if srv.alive else "error",
                "error": None if srv.alive else (srv.error or "not connected")[:300],
                "tools": srv.registered if srv.alive else 0,
                "transport": srv.transport,
            }
        return out


async def _watch(srv: _Server, session) -> None:
    """Wait until told to stop, pinging now and then so a dead server is noticed."""
    while not srv.stop.is_set():
        try:
            await asyncio.wait_for(srv.stop.wait(), HEARTBEAT)
        except asyncio.TimeoutError:
            try:
                await asyncio.wait_for(session.send_ping(), 10)
            except Exception as e:  # noqa: BLE001
                if _is_closed(e):
                    srv.error = "connection closed"
                    return


def _is_closed(e: BaseException) -> bool:
    """The connection itself is gone (as opposed to one request failing)."""
    import anyio
    from mcp import MCPError

    if isinstance(e, MCPError):
        return e.code == -32000  # CONNECTION_CLOSED
    return isinstance(e, (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream,
                          BrokenPipeError, ConnectionError))


def _describe(e: BaseException) -> str:
    """Readable message, unwrapping anyio/asyncio exception groups."""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    if isinstance(e, FileNotFoundError) and e.filename:
        return f"Command not found: {e.filename}"
    msg = str(e).strip()
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


def _transport(conn: dict[str, Any], errlog):
    if conn.get("url"):
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        headers = {str(k): str(v) for k, v in (conn.get("headers") or {}).items()}
        return _http(streamable_http_client, create_mcp_http_client(headers=headers), conn["url"])
    if not conn.get("command"):
        raise ValueError("needs a `command` (stdio) or a `url` (HTTP)")
    from mcp import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=str(conn["command"]),
        args=[str(a) for a in conn.get("args") or []],
        env={str(k): str(v) for k, v in (conn.get("env") or {}).items()} or None,
        cwd=conn.get("cwd"),
    )
    return stdio_client(params, errlog=errlog or sys.stderr)


@asynccontextmanager
async def _http(client_factory, http_client, url):
    async with http_client:
        async with client_factory(url, http_client=http_client) as streams:
            yield streams[0], streams[1]


_manager: Manager | None = None
_manager_lock = threading.Lock()


def manager() -> Manager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = Manager()
            atexit.register(_manager.close_all)
        return _manager


def status() -> dict[str, dict[str, Any]]:
    """Each configured server's state, error and tool count (no secrets)."""
    return manager().status() if _manager is not None else {}


# -- the integration -----------------------------------------------------------------


def setup(registry: ToolRegistry, ctx) -> None:
    servers: dict[str, dict[str, Any]] = {
        str(k): (v or {}) for k, v in (ctx.settings.get("servers") or {}).items()
    }
    call_timeout = float(ctx.settings.get("call_timeout") or DEFAULT_CALL_TIMEOUT)
    connect_timeout = float(ctx.settings.get("connect_timeout") or DEFAULT_CONNECT_TIMEOUT)
    try:
        import mcp  # noqa: F401
    except ImportError:
        if servers:
            log.warning("MCP servers are configured but the `mcp` package isn't installed "
                        "(pip install 'gabe[mcp]'); skipping them.")
        return

    mgr = manager()
    mgr.sync({n: connection_of(s) for n, s in servers.items()}, ctx.data_dir / ctx.name / "logs",
             connect_timeout)

    for server, spec in sorted(servers.items()):
        srv = mgr.servers.get(server)
        if srv is None or not srv.alive:
            continue
        include = set(spec.get("include") or [])
        exclude = set(spec.get("exclude") or [])
        count = 0
        for t in srv.tools:
            if (include and t.name not in include) or t.name in exclude:
                continue
            name = tool_name(server, t.name)
            if registry.get(name) is not None:
                name = tool_name(server, f"{t.name}_{hashlib.sha1(t.name.encode()).hexdigest()[:6]}")
                if registry.get(name) is not None:
                    log.warning("MCP tool %s/%s clashes with an existing tool; skipped", server, t.name)
                    continue
            try:
                risk = risk_of(t.name, t.annotations, spec)
            except ValueError:
                log.warning("MCP tool %s/%s has an unknown risk in config; skipped", server, t.name)
                continue
            description = (t.description or t.title or t.name).strip()
            registry.register(Tool(
                name=name,
                description=f"[{server}] {description}",
                input_schema=input_schema_of(t.input_schema),
                handler=_handler(mgr, server, t.name, call_timeout),
                risk=risk,
                integration=server,
            ))
            count += 1
        srv.registered = count


def _handler(mgr: Manager, server: str, tool: str, timeout: float):
    def call(**args: Any) -> str:
        return mgr.call(server, tool, args, timeout)

    call.__name__ = f"{server}__{tool}"
    return call
