"""Curated-job API — list collection jobs that have been through the curator
skill, return their filtered posts, and (re)serve their media files.

A job is "curated" when its directory under the workspace's `data/` tree
contains `posts.filtered.json` produced by the focus-lab-curator skill:

    <workspace>/data/<date>/<job_id>/posts.filtered.json

We read those files directly from the workspace data dir. Media URLs in the
filtered file already point under the data dir (e.g.
`2026-05-02/job_020746/linkedin/media/foo.jpg`) so the existing
`/feed_data/{path}` route serves them — no special media endpoint needed.
"""

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.paths import get_collected_data_dir

router = APIRouter()

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
JOB_RE = re.compile(r"^job_\d{6}$")

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
        "platform_counts": meta.get("platform_counts"),
        "platforms": meta.get("platforms") or [],
        "filtered_at": meta.get("filtered_at"),
    }
    _filter_meta_cache[key] = (st.st_mtime, out)
    return out


def _safe_job_dir(job_id_path: str) -> Path:
    """Resolve <data>/<date>/<job_HHMMSS> safely. job_id_path is 'YYYY-MM-DD/job_HHMMSS'."""
    parts = job_id_path.strip("/").split("/")
    if len(parts) != 2 or not DATE_RE.match(parts[0]) or not JOB_RE.match(parts[1]):
        raise HTTPException(400, "Invalid job id (expected 'YYYY-MM-DD/job_HHMMSS').")
    root = get_collected_data_dir().resolve()
    target = (root / parts[0] / parts[1]).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(400, "Job path escapes the data directory.")
    if not target.is_dir():
        raise HTTPException(404, f"Job not found: {job_id_path}")
    return target


@router.get("/jobs")
async def list_curated_jobs():
    """Walk the data tree and surface every job that has posts.filtered.json."""
    data_root = get_collected_data_dir()
    if not data_root.exists():
        return {"is_setup": True, "jobs": []}

    jobs = []
    for date_dir in sorted(data_root.iterdir(), reverse=True):
        if not date_dir.is_dir() or not DATE_RE.match(date_dir.name):
            continue
        for job_dir in sorted(date_dir.iterdir(), reverse=True):
            if not job_dir.is_dir() or not JOB_RE.match(job_dir.name):
                continue
            filtered = job_dir / "posts.filtered.json"
            if not filtered.exists():
                continue
            meta = _read_filter_metadata(filtered)
            if meta is None:
                continue
            job_id = f"{date_dir.name}/{job_dir.name}"
            jobs.append({
                "id": job_id,
                "date": date_dir.name,
                "job_id": job_dir.name.replace("job_", ""),
                **meta,
                "modified": filtered.stat().st_mtime,
            })

    # Newest first by curation time, falling back to mtime.
    jobs.sort(key=lambda j: j.get("filtered_at") or j["modified"], reverse=True)
    return {"is_setup": True, "jobs": jobs}


@router.get("/jobs/{date}/{job_dir}")
async def get_curated_job(date: str, job_dir: str):
    """Return posts.filtered.json content for a specific curated job."""
    job_path = _safe_job_dir(f"{date}/{job_dir}")
    filtered = job_path / "posts.filtered.json"
    if not filtered.exists():
        raise HTTPException(404, "posts.filtered.json not found in this job.")
    try:
        return json.loads(filtered.read_text())
    except json.JSONDecodeError as e:
        raise HTTPException(500, f"Could not parse posts.filtered.json: {e}")
