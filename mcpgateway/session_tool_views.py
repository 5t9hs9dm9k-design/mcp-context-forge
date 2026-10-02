"""Per-session tool views for ContextForge.

When enabled (settings.session_tool_views), each MCP client session gets an
isolated tool view: tools/list serves only the configured baseline prefixes
plus the tools that session has explicitly summoned. Summon/release state is
in-memory, keyed by the mcp-session-id the platform mints on initialize, and
expires with the session (TTL prune).

This module holds no global state mutation: federation stays as-is, and the
filter is applied at the tools/list dispatch boundary.
"""
import time
import uuid
from threading import Lock

_VIEWS: dict = {}
_LOCK = Lock()
_TTL_SECONDS = 24 * 3600


def mint_session() -> str:
    """Mint a fresh view session id and register it."""
    sid = uuid.uuid4().hex
    now = time.time()
    with _LOCK:
        _prune(now)
        _VIEWS[sid] = {"summons": set(), "last": now}
    return sid


def record_summons(sid: str, names: list, active: bool) -> None:
    """Add (active=True) or remove (active=False) tool names from a session's view."""
    if not sid:
        return
    now = time.time()
    with _LOCK:
        _prune(now)
        view = _VIEWS.setdefault(sid, {"summons": set(), "last": now})
        view["last"] = now
        if active:
            view["summons"].update(n for n in names if n)
        else:
            view["summons"].difference_update(n for n in names if n)


def filter_tools(result: dict, sid: str, baseline_prefixes: tuple) -> dict:
    """Filter a tools/list result dict to baseline prefixes + this session's summons.

    Sessions unknown to the registry (sessionless callers, trusted internals)
    pass through unfiltered.
    """
    if sid not in _VIEWS:
        return result
    view = _VIEWS[sid]["summons"]
    view["last"] = time.time()
    tools = result.get("tools")
    if isinstance(tools, list):
        result["tools"] = [
            t for t in tools
            if str(t.get("name", "")).startswith(tuple(baseline_prefixes))
            or t.get("name") in view
        ]
    return result


def _prune(now: float) -> None:
    for k in [k for k, v in _VIEWS.items() if now - v["last"] > _TTL_SECONDS]:
        _VIEWS.pop(k, None)
