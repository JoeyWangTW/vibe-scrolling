"""Workspace API — status, setup, reveal.

There is no default workspace. The user explicitly sets one up via
`POST /api/workspace/setup`, which creates the folder (if missing) and
bootstraps the curation structure into it.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.paths import get_workspace_dir, suggested_workspace_dir
from app.workspace import bootstrap_workspace, reveal_in_finder, save_workspace_dir, skill_status

router = APIRouter()


@router.get("")
async def get_workspace():
    """Return workspace status. `is_setup` is false until the user picks a folder."""
    ws = get_workspace_dir()
    if ws is None:
        return {
            "is_setup": False,
            "path": None,
            "suggested_path": str(suggested_workspace_dir()),
        }

    exports = ws / "exports"
    pack_count = 0
    recent = []
    if exports.exists():
        items = sorted(
            [p for p in exports.iterdir() if p.is_dir() or p.suffix == ".zip"],
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        pack_count = len(items)
        for item in items[:5]:
            size_b = sum(f.stat().st_size for f in item.rglob("*") if f.is_file()) if item.is_dir() else item.stat().st_size
            recent.append({
                "name": item.name,
                "is_dir": item.is_dir(),
                "size_mb": round(size_b / 1024 / 1024, 1),
                "modified": item.stat().st_mtime,
            })

    return {
        "is_setup": True,
        "path": str(ws),
        "data_dir": str(ws / "data"),
        "exports_dir": str(exports),
        "pack_count": pack_count,
        "recent_packs": recent,
        "goals_exists": (ws / "goals.md").exists(),
        "skill_exists": (ws / "skills" / "focus-lab-curator" / "SKILL.md").exists(),
    }


class SetupRequest(BaseModel):
    path: str
    update_app_files: bool = False  # refresh skill + docs if already present


@router.post("/setup")
async def setup(request: SetupRequest):
    """Create (if missing) and bootstrap the user's chosen workspace folder.

    If the folder already has a workspace, `goals.md` is never overwritten.
    App-managed files (the curator skill, CLAUDE.md, AGENTS.md, README.md)
    are only refreshed when `update_app_files` is True.
    """
    raw = request.path.strip() if request.path else ""
    if not raw:
        raise HTTPException(400, "A folder path is required.")

    target = Path(raw).expanduser().resolve()

    # Sanity: refuse obvious nonsense like root, home dir, or a file path.
    if target == Path("/") or target == Path.home():
        raise HTTPException(400, "Pick a specific folder, not the home or root directory.")
    if target.exists() and not target.is_dir():
        raise HTTPException(400, f"{target} exists and is not a directory.")

    target.mkdir(parents=True, exist_ok=True)
    result = bootstrap_workspace(target, update_app_files=request.update_app_files)
    save_workspace_dir(target)
    return {"success": True, **result}


@router.get("/skill-status")
async def get_skill_status():
    """Is the workspace's curator skill in sync with the shipped version?

    Used by the UI to offer a one-click update when a new app version ships
    a newer skill. `outdated` is false when versions match OR when the
    workspace's skill path resolves to the shipped source (dev mode).
    """
    return skill_status()


@router.post("/skill-update")
async def update_skill():
    """Force-refresh the workspace's curator skill to the shipped version.

    Thin wrapper around `bootstrap_workspace(workspace, update_app_files=True)`
    — doesn't touch `goals.md` or anything else user-owned.
    """
    ws = get_workspace_dir()
    if ws is None:
        raise HTTPException(412, "Workspace not set up yet.")
    result = bootstrap_workspace(ws, update_app_files=True)
    # Return both the bootstrap result and the new status so the UI can
    # hide the banner in the same round-trip.
    return {"success": True, **result, "status": skill_status()}


@router.get("/auto-export")
async def get_auto_export():
    """Return whether auto-export after collection is enabled."""
    import json
    from app.paths import CONFIG_PATH
    enabled = False
    if CONFIG_PATH.exists():
        try:
            enabled = bool(json.loads(CONFIG_PATH.read_text()).get("auto_export"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"enabled": enabled}


class AutoExportRequest(BaseModel):
    enabled: bool


@router.post("/auto-export")
async def set_auto_export(request: AutoExportRequest):
    """Toggle auto-export. When true, every completed collection triggers a
    curation export into `<workspace>/exports/`."""
    import json
    from app.paths import CONFIG_PATH

    cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            cfg = {}
    cfg["auto_export"] = bool(request.enabled)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    return {"success": True, "enabled": cfg["auto_export"]}


@router.get("/goals")
async def get_goals():
    ws = get_workspace_dir()
    if ws is None:
        raise HTTPException(412, "Workspace not set up yet.")
    goals = ws / "goals.md"
    if not goals.exists():
        return {"content": "", "path": str(goals), "exists": False}
    return {"content": goals.read_text(), "path": str(goals), "exists": True}


class SaveGoalsRequest(BaseModel):
    content: str


@router.post("/goals")
async def save_goals(request: SaveGoalsRequest):
    ws = get_workspace_dir()
    if ws is None:
        raise HTTPException(412, "Workspace not set up yet.")
    goals = ws / "goals.md"
    goals.write_text(request.content)
    return {"success": True, "path": str(goals), "size": len(request.content)}


@router.post("/reveal")
async def reveal(body: dict | None = None):
    """Open the workspace (or a specific sub-path) in the OS file manager."""
    ws = get_workspace_dir()
    if ws is None:
        raise HTTPException(412, "Workspace not set up yet.")

    sub_path = (body or {}).get("path")
    target = Path(sub_path).expanduser().resolve() if sub_path else ws
    # Keep reveals inside the workspace.
    try:
        target.resolve().relative_to(ws.resolve())
    except ValueError:
        if target.resolve() != ws.resolve():
            raise HTTPException(400, "Path is outside the workspace")
    if not reveal_in_finder(target):
        raise HTTPException(404, f"Path not found: {target}")
    return {"ok": True, "revealed": str(target)}
