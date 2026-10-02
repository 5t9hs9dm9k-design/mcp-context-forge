"""Per-session tool views for ContextForge.

When enabled (settings.session_tool_views), each MCP client session gets an
isolated tool view: tools/list serves only the configured baseline prefixes
plus the tools that session has explicitly summoned. Summon/release state is
keyed by the mcp-session-id the platform mints on initialize and expires with
the session (TTL prune).

State store: redis when CACHE_TYPE=redis and REDIS_URL are configured (shared
across gunicorn workers), otherwise in-process memory (single-worker use).
This module holds no global state mutation: federation stays as-is, and the
filter is applied at the tools/list dispatch boundary.
"""
import json
import os
import time
import uuid
from threading import Lock

_VIEWS: dict = {}
_LOCK = Lock()
_TTL_SECONDS = 24 * 3600
_PREFIX = "session_tool_views:"


def _redis():
    """Return a redis client when the redis store is configured, else None."""
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


def mint_session() -> str:
    """Mint a fresh view session id and register it."""
    sid = uuid.uuid4().hex
    r = _redis()
    if r is not None:
        try:
            r.set(_PREFIX + sid, json.dumps([]), ex=_TTL_SECONDS)
            return sid
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    now = time.time()
    with _LOCK:
        _prune(now)
        _VIEWS[sid] = {"summons": set(), "last": now}
    return sid


def record_summons(sid: str, names: list, active: bool) -> None:
    """Add (active=True) or remove (active=False) tool names from a session's view."""
    if not sid:
        return
    r = _redis()
    if r is not None:
        try:
            key = _PREFIX + sid
            current = json.loads(r.get(key) or "[]")
            if active:
                current.extend(n for n in names if n and n not in current)
            else:
                current = [n for n in current if n not in names]
            r.set(key, json.dumps(current), ex=_TTL_SECONDS)
            return
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    now = time.time()
    with _LOCK:
        _prune(now)
        view = _VIEWS.setdefault(sid, {"summons": set(), "last": now})
        view["last"] = now
        if active:
            view["summons"].update(n for n in names if n)
        else:
            view["summons"].difference_update(n for n in names if n)


def _summons_for(sid: str):
    """Return the session's summons set, or None when the session is unknown."""
    r = _redis()
    if r is not None:
        try:
            raw = r.get(_PREFIX + sid)
            if raw is None:
                return None
            return set(json.loads(raw))
        except Exception:  # pylint: disable=broad-exception-caught
            return None
    with _LOCK:
        if sid not in _VIEWS:
            return None
        _VIEWS[sid]["last"] = time.time()
        return set(_VIEWS[sid]["summons"])


def filter_tools(result: dict, sid: str, baseline_prefixes: tuple) -> dict:
    """Filter a tools/list result dict to baseline prefixes + this session's summons.

    Sessions unknown to the registry (sessionless callers, trusted internals)
    pass through unfiltered.
    """
    view = _summons_for(sid)
    if view is None:
        return result
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

def release_session(sid: str) -> None:
    with _LOCK:
        _VIEWS.pop(sid, None)


def release_all_views() -> int:
    with _LOCK:
        n = len(_VIEWS)
        _VIEWS.clear()
        return n
