"""Data API endpoints — list runs, serve posts (date/job/platform hierarchy)."""

import asyncio
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.paths import get_collected_data_dir as _DATA_DIR
from app.workspace import reveal_in_finder

router = APIRouter()

# Patterns for detecting directory types
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
JOB_PATTERN = re.compile(r"^job_\d{6}$")
LEGACY_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{6})_(\w+)$")

# Mtime-keyed metadata cache. posts.json files are append-then-immutable per
# run, so once we've parsed them we never need to parse again unless the file
# changes. /runs and /runs/latest each used to re-read every posts.json on
# every page visit (often multi-MB each), which dominated page-load time.
_meta_cache: dict[str, tuple[float, dict]] = {}


def _read_posts_metadata(posts_file: Path) -> dict:
    """Return {post_count, duration_seconds, timestamp} for a posts.json,
    cached by mtime so repeat calls don't re-parse the full file."""
    try:
        st = posts_file.stat()
    except OSError:
        return {}
    key = str(posts_file)
    cached = _meta_cache.get(key)
    if cached and cached[0] == st.st_mtime:
        return cached[1]
    try:
        data = json.loads(posts_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    meta = data.get("metadata", {}) or {}
    out = {
        "post_count": meta.get("post_count", len(data.get("posts", data.get("tweets", [])))),
        "duration_seconds": meta.get("collection_duration_seconds"),
        "timestamp": meta.get("run_timestamp", ""),
    }
    _meta_cache[key] = (st.st_mtime, out)
    return out


def _parse_platform_dir(platform_dir: Path, date: str, job_id: str) -> dict | None:
    """Parse a platform directory within a job."""
    posts_file = platform_dir / "posts.json"
    if not posts_file.exists():
        posts_file = platform_dir / "tweets.json"

    platform = platform_dir.name
    run_id = f"{date}/job_{job_id}/{platform}"

    info = {
        "run_id": run_id,
        "platform": platform,
        "date": date,
        "job_id": job_id,
        "has_posts": posts_file.exists(),
    }

    if posts_file.exists():
        info.update(_read_posts_metadata(posts_file))

    run_log = platform_dir / "run_log.json"
    if run_log.exists():
        try:
            info["run_log"] = json.loads(run_log.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    return info


def _walk_hierarchy() -> list[dict]:
    """Walk the date/job/platform hierarchy and return all runs."""
    if not _DATA_DIR().exists():
        return []

    runs = []

    for date_dir in sorted(_DATA_DIR().iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue

        # New hierarchy: date/job_HHMMSS/platform/
        if DATE_PATTERN.match(date_dir.name):
            date = date_dir.name
            for job_dir in sorted(date_dir.iterdir(), reverse=True):
                if not job_dir.is_dir() or not JOB_PATTERN.match(job_dir.name):
                    continue
                job_id = job_dir.name.replace("job_", "")
                for platform_dir in sorted(job_dir.iterdir()):
                    if not platform_dir.is_dir() or platform_dir.name == "job.json":
                        continue
                    info = _parse_platform_dir(platform_dir, date, job_id)
                    if info:
                        runs.append(info)

        # Legacy flat: YYYY-MM-DD_HHMMSS_platform/
        elif LEGACY_PATTERN.match(date_dir.name):
            m = LEGACY_PATTERN.match(date_dir.name)
            date, time_id, platform = m.group(1), m.group(2), m.group(3)
            posts_file = date_dir / "posts.json"
            if not posts_file.exists():
                posts_file = date_dir / "tweets.json"
            info = {
                "run_id": date_dir.name,
                "platform": platform,
                "date": date,
                "job_id": time_id,
                "has_posts": posts_file.exists(),
                "legacy": True,
            }
            if posts_file.exists():
                info.update(_read_posts_metadata(posts_file))
            runs.append(info)

    return runs


def _group_runs_by_date_and_job(runs: list[dict]) -> list[dict]:
    """Group flat run list into date > job > platforms hierarchy."""
    dates_map: dict[str, dict[str, list]] = {}

    for run in runs:
        date = run.get("date", "unknown")
        job_id = run.get("job_id", "unknown")
        if date not in dates_map:
            dates_map[date] = {}
        if job_id not in dates_map[date]:
            dates_map[date][job_id] = []
        dates_map[date][job_id].append(run)

    result = []
    for date in sorted(dates_map.keys(), reverse=True):
        jobs = []
        for job_id in sorted(dates_map[date].keys(), reverse=True):
            platforms = dates_map[date][job_id]
            # Try to load job.json for metadata
            job_dir = _DATA_DIR() / date / f"job_{job_id}"
            job_meta = {}
            job_json = job_dir / "job.json"
            if job_json.exists():
                try:
                    job_meta = json.loads(job_json.read_text())
                except (json.JSONDecodeError, OSError):
                    pass

            jobs.append({
                "job_id": job_id,
                "started_at": job_meta.get("started_at", ""),
                "platforms": platforms,
            })
        result.append({"date": date, "jobs": jobs})

    return result


def _resolve_run_dir(run_id: str) -> Path:
    """Resolve a run_id to its directory path, handling both new and legacy formats."""
    # New format: 2026-03-22/job_002223/twitter
    run_dir = _DATA_DIR() / run_id
    if run_dir.is_dir():
        return run_dir

    # Legacy format: 2026-03-22_002223_twitter
    run_dir = _DATA_DIR() / run_id
    if run_dir.is_dir():
        return run_dir

    raise HTTPException(404, f"Run not found: {run_id}")


@router.get("/runs")
async def list_runs():
    runs = _walk_hierarchy()
    grouped = _group_runs_by_date_and_job(runs)
    # Also return flat list for backward compat
    return {"dates": grouped, "runs": runs}


def _read_full_run(run_id: str) -> tuple[str, dict] | None:
    """Read posts.json fully for a run_id (used by /runs/latest, run on a
    thread to keep the event loop free during multi-MB JSON parsing)."""
    try:
        run_dir = _resolve_run_dir(run_id)
    except HTTPException:
        return None
    posts_file = run_dir / "posts.json"
    if not posts_file.exists():
        posts_file = run_dir / "tweets.json"
    if not posts_file.exists():
        return None
    try:
        data = json.loads(posts_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return run_id, {
        "metadata": data.get("metadata", {}),
        "posts": data.get("posts", data.get("tweets", [])),
        "run_id": run_id,
    }


@router.get("/runs/latest")
async def get_latest_runs():
    runs = _walk_hierarchy()
    if not runs:
        return {"runs": {}}

    # Pick the newest has_posts run per platform.
    latest: dict[str, dict] = {}
    for run in runs:
        if run.get("has_posts"):
            platform = run.get("platform", "unknown")
            if platform not in latest:
                latest[platform] = run

    # Read each platform's posts.json in parallel — these are the heavy reads
    # (often 1–5 MB each) and were sequential before, dominating page load.
    pairs = await asyncio.gather(
        *(asyncio.to_thread(_read_full_run, info["run_id"]) for info in latest.values())
    )

    result: dict[str, dict] = {}
    for platform, pair in zip(latest.keys(), pairs):
        if pair is not None:
            result[platform] = pair[1]

    return {"runs": result}


@router.get("/runs/{run_id:path}")
async def get_run(run_id: str):
    run_dir = _resolve_run_dir(run_id)

    posts_file = run_dir / "posts.json"
    if not posts_file.exists():
        posts_file = run_dir / "tweets.json"
    if not posts_file.exists():
        raise HTTPException(404, f"No posts file in run: {run_id}")

    return json.loads(posts_file.read_text())


# ----------------------------------------------------------- mutations & paths

def _resolve_safely(rel_path: str) -> Path:
    """Resolve a date / job / run path under the active data dir, refusing
    traversal. Used by delete + reveal endpoints."""
    if not rel_path or rel_path.startswith("/") or ".." in rel_path.split("/"):
        raise HTTPException(400, "Invalid path")
    root = _DATA_DIR().resolve()
    target = (root / rel_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(400, "Path escapes the data directory")
    if not target.exists():
        raise HTTPException(404, f"Not found: {rel_path}")
    return target


@router.delete("/path/{rel_path:path}")
async def delete_path(rel_path: str):
    """Delete a date / job / run directory from the active data dir.

    The frontend Data tab wires this to per-row delete buttons. Removes
    everything under the given path (date dir = whole day; job dir = one
    collection job; platform dir = one platform's run).
    """
    import shutil
    target = _resolve_safely(rel_path)
    # Forget cached metadata for files we're deleting so /runs is fresh
    # next time without restarting the app.
    for cached_key in list(_meta_cache.keys()):
        if cached_key.startswith(str(target)):
            _meta_cache.pop(cached_key, None)
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"ok": True, "deleted": rel_path}


@router.post("/reveal/{rel_path:path}")
async def reveal_path(rel_path: str):
    """Open a date / job / run directory in the OS file manager."""
    target = _resolve_safely(rel_path)
    if not reveal_in_finder(target):
        raise HTTPException(500, "Failed to open folder")
    return {"ok": True, "revealed": str(target)}


@router.post("/reveal-root")
async def reveal_data_root():
    """Open the root data directory itself (workspace/data or fallback)."""
    root = _DATA_DIR()
    root.mkdir(parents=True, exist_ok=True)
    if not reveal_in_finder(root):
        raise HTTPException(500, "Failed to open folder")
    return {"ok": True, "revealed": str(root)}
