"""Boonestack Sessions UI - live view-session inspector for the admin UI.

Standalone router so it overlays cleanly on any ContextForge version:
  GET  /admin/boonestack-sessions          page (auto-refreshing)
  POST /admin/boonestack-sessions/release/{sid}   release one view (-> baseline)
  POST /admin/boonestack-sessions/release-all     release every view
Auth: admin.dashboard permission (same as the overview dashboard).
"""
import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

# First-Party
from mcpgateway.middleware.rbac import require_permission

# First-Party
from mcpgateway.session_tool_views import list_sessions, release_all_views, release_session


def release_all_views() -> None:
    for s in list_sessions():
        release_session(str(s.get("session", "")))


router = APIRouter(prefix="/admin/boonestack-sessions", tags=["Boonestack Sessions"])


def _render(sessions: list) -> str:
    rows = []
    for s in sessions:
        sid = html.escape(str(s.get("session", ""))[:16])
        ttl = s.get("ttl", 0)
        summons = s.get("summons") or []
        tools = "<br>".join(html.escape(t) for t in summons) if summons else "<i>baseline only</i>"
        safe_sid = html.escape(str(s.get("session", "")))
        rows.append(f"""
        <tr>
          <td><code>{sid}&hellip;</code></td>
          <td>{ttl}s</td>
          <td>{len(summons)}</td>
          <td>{tools}</td>
          <td><button onclick="releaseView('{safe_sid}')">Release</button></td>
        </tr>""")
    rows_html = "".join(rows) or "<tr><td colspan='5'>No live session views.</td></tr>"
    return f"""<!DOCTYPE html>
<html><head><title>Boonestack Session Views</title>
<meta http-equiv="refresh" content="30">
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; background: #0d1117; color: #e6edf3; padding: 2rem; }}
  h1 {{ font-size: 1.4rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
  th, td {{ border: 1px solid #30363d; padding: 0.5rem 0.75rem; text-align: left; vertical-align: top; font-size: 0.9rem; }}
  th {{ background: #161b22; }}
  code {{ color: #79c0ff; }}
  button {{ background: #21262d; color: #e6edf3; border: 1px solid #30363d; padding: 0.25rem 0.6rem; cursor: pointer; }}
  button:hover {{ background: #30363d; }}
  .meta {{ color: #8b949e; font-size: 0.85rem; }}
</style></head>
<body>
<h1>Boonestack — Live Session Tool Views</h1>
<p class="meta">Auto-refreshes every 30s. Each view = baseline tools + that session's summons.
Releasing returns a session to baseline. New sessions start at baseline automatically.</p>
<table>
<tr><th>Session</th><th>View TTL</th><th>Summons</th><th>Summoned tools</th><th>Action</th></tr>
{rows_html}
</table>
<form onsubmit="releaseAll(); return false;"><button type="submit">Release ALL views</button></form>
<script>
async function releaseView(sid) {{
  await fetch('/admin/boonestack-sessions/release/' + sid, {{ method: 'POST' }});
  location.reload();
}}
async function releaseAll() {{
  await fetch('/admin/boonestack-sessions/release-all', {{ method: 'POST' }});
  location.reload();
}}
</script>
</body></html>"""


@router.get("", response_class=HTMLResponse)
@require_permission("admin.dashboard", allow_admin_bypass=True)
async def sessions_page(request: Request) -> HTMLResponse:
    """Live session-view inspector page."""
    return HTMLResponse(_render(list_sessions()))


@router.post("/release/{sid}")
@require_permission("admin.dashboard", allow_admin_bypass=True)
async def release_one(sid: str) -> dict:
    """Release one view session back to baseline."""
    release_session(sid)
    return {"released": sid}


@router.post("/release-all")
@require_permission("admin.dashboard", allow_admin_bypass=True)
async def release_all_endpoint() -> dict:
    """Release every view session back to baseline."""
    return {"released": release_all_views()}
