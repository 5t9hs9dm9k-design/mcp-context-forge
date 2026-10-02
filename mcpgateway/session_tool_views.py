"""Per-session tool views for ContextForge (stateless, multi-worker safe).

A lightweight ASGI middleware mints a view-session id on the first request
that lacks one, returns it as a response header, and expects MCP clients to
echo it on subsequent requests (streamable-http clients echo mcp-session-id).
The echoed id keys a redis-backed summons registry: the tools a session has
explicitly summoned via the librarian.

tools/list is filtered per session to: configured baseline prefixes + that
session's summons. Everything else stays stateless: no USE_STATEFUL_SESSIONS,
no global tool state changes, invocation unfiltered. Sessionless internal
callers get a fresh baseline-only view per request, which is why trusted
internals that need the full catalog bypass via the internal marker header.
"""
import contextvars
import json
import os
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_VAR_NAME = "mcp-session-view"
_TTL_SECONDS = 24 * 3600
_KEY_PREFIX = "session_tool_views:"

view_session_var: contextvars.ContextVar = contextvars.ContextVar("session_tool_views_id", default=None)


def _redis():
    if os.environ.get("CACHE_TYPE", "").lower() != "redis":
        return None
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    try:
        import redis  # pylint: disable=import-outside-toplevel
        return redis.Redis.from_url(url, socket_connect_timeout=5)
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def _key(sid: str) -> str:
    return _KEY_PREFIX + sid


def store_summons(sid: str, names: list, active: bool) -> None:
    """Add/remove tool names in a view session's summons set (redis, TTL'd)."""
    r = _redis()
    if r is None:
        return
    try:
        current = json.loads(r.get(_key(sid)) or "[]")
        if active:
            current.extend(n for n in names if n and n not in current)
        else:
            current = [n for n in current if n not in names]
        r.set(_key(sid), json.dumps(current), ex=_TTL_SECONDS)
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def baseline_prefixes() -> tuple:
    try:
        from mcpgateway.config import settings  # pylint: disable=import-outside-toplevel
        return tuple(p.strip() for p in settings.session_view_baseline_prefixes.split(",") if p.strip())
    except Exception:  # pylint: disable=broad-exception-caught
        return ()


def get_view_filter():
    """(prefixes, summoned_set) for the current request's view, or None when the
    request carries no view session (trusted internals bypassing the middleware)."""
    sid = view_session_var.get()
    if not sid:
        return None
    r = _redis()
    if r is None:
        return None
    try:
        raw = r.get(_key(sid))
        if raw is None:
            return None
        return (baseline_prefixes(), set(json.loads(raw)))
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def list_sessions() -> list:
    """All live view sessions (for the admin UI): [{sid, summons, ttl}]."""
    r = _redis()
    if r is None:
        return []
    out = []
    try:
        for k in r.scan_iter(_KEY_PREFIX + "*"):
            sid = k.decode().split(":", 1)[1]
            out.append({"session": sid,
                        "summons": json.loads(r.get(k) or "[]"),
                        "ttl": r.ttl(k)})
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return out


class SessionViewMiddleware:
    """Mint/echo the view-session id and expose it via a ContextVar for the
    tools/list filter. Zero global state: the id lives in the client's echo
    plus a TTL'd redis record."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        req_headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                       for k, v in scope.get("headers", [])}
        internal = req_headers.get("x-boonestack-internal") == "1"
        sid = req_headers.get(_VAR_NAME)
        minted = False
        token = None
        if not internal:
            if not sid:
                sid = uuid.uuid4().hex
                minted = True
                store_summons(sid, [], True)  # register the view (baseline-only)
            token = view_session_var.set(sid)

        async def send_wrapped(message: Message) -> None:
            if minted and message.get("type") == "http.response.start":
                h = list(message.get("headers") or [])
                h.append((_VAR_NAME.encode("latin-1"), sid.encode("latin-1")))
                message["headers"] = h
            await send(message)

        try:
            await self.app(scope, receive, send_wrapped)
        finally:
            if token is not None:
                view_session_var.reset(token)
