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

    return {
        "is_setup": True,
        "path": str(ws),
        "data_dir": str(ws / "data"),
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
