"""Curated-pack API — list packs that have been through the curator skill,
return their filtered posts, and serve their media files.

A pack is "curated" when it contains `posts.filtered.json` produced by the
focus-lab-curator skill. This endpoint reads those files directly from the
user's workspace.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.paths import get_workspace_dir

router = APIRouter()

# Mtime-keyed cache of filter_metadata blobs. posts.filtered.json is rewritten
# only when the curator runs; until then we shouldn't re-parse multi-MB JSON
# every time the user clicks the AI Curation tab.
_filter_meta_cache: dict[str, tuple[float, dict]] = {}


def _read_filter_metadata(filtered: Path) -> dict | None:
    """Return the filter_metadata blob from posts.filtered.json (cached by
    mtime). None if the file is missing or unparseable."""
    try:
        st = filtered.stat()
    except OSError:
        return None
    key = str(filtered)
    cached = _filter_meta_cache.get(key)
    if cached and cached[0] == st.st_mtime:
        return cached[1]
    try:
        data = json.loads(filtered.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    meta = data.get("filter_metadata", {}) or {}
    out = {
        "kept": meta.get("kept_posts", len(data.get("posts", []))),
        "dropped_count": meta.get("dropped_count", 0),
        "source_posts": meta.get("source_posts"),
        "median_score": meta.get("median_score"),
        "avg_score": meta.get("avg_score"),
        "category_counts": meta.get("category_counts"),
        "filtered_at": meta.get("filtered_at"),
    }
    _filter_meta_cache[key] = (st.st_mtime, out)
    return out


def _safe_pack_dir(pack_name: str) -> Path:
    """Resolve <workspace>/exports/<pack_name> and ensure it's inside exports/."""
    ws = get_workspace_dir()
    if ws is None:
        raise HTTPException(412, "Workspace not set up yet.")
    exports = (ws / "exports").resolve()
    pack_dir = (exports / pack_name).resolve()
    if not str(pack_dir).startswith(str(exports) + "/") and pack_dir != exports:
        raise HTTPException(400, "Invalid pack name.")
    if not pack_dir.is_dir():
        raise HTTPException(404, f"Pack not found: {pack_name}")
    return pack_dir


@router.get("/packs")
async def list_curated_packs():
    """List pack folders in workspace that contain posts.filtered.json."""
    ws = get_workspace_dir()
    if ws is None:
        return {"is_setup": False, "packs": []}

    exports = ws / "exports"
    if not exports.exists():
        return {"is_setup": True, "packs": []}

    packs = []
    for pack_dir in sorted(exports.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not pack_dir.is_dir():
            continue
        filtered = pack_dir / "posts.filtered.json"
        if not filtered.exists():
            continue
        meta = _read_filter_metadata(filtered)
        if meta is None:
            continue
        packs.append({
            "name": pack_dir.name,
            **meta,
            "modified": pack_dir.stat().st_mtime,
        })

    return {"is_setup": True, "packs": packs}


@router.get("/packs/{pack_name}")
async def get_curated_pack(pack_name: str):
    """Return posts.filtered.json content for a specific pack."""
    pack_dir = _safe_pack_dir(pack_name)
    filtered = pack_dir / "posts.filtered.json"
    if not filtered.exists():
        raise HTTPException(404, "posts.filtered.json not found in this pack.")
    try:
        return json.loads(filtered.read_text())
    except json.JSONDecodeError as e:
        raise HTTPException(500, f"Could not parse posts.filtered.json: {e}")


@router.get("/packs/{pack_name}/media/{file_path:path}")
async def serve_pack_media(pack_name: str, file_path: str):
    """Serve a media file from <pack>/media/<file_path>."""
    pack_dir = _safe_pack_dir(pack_name)
    media_root = (pack_dir / "media").resolve()
    target = (media_root / file_path).resolve()
    if not str(target).startswith(str(media_root) + "/") and target != media_root:
        raise HTTPException(400, "Invalid media path.")
    if not target.is_file():
        raise HTTPException(404, f"Media not found: {file_path}")
    return FileResponse(target)
