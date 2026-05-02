"""Collection API endpoints — start, stop, status, history."""

import asyncio
import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.paths import CONFIG_PATH, FEED_DATA_DIR, get_collected_data_dir
from app.tasks.manager import task_manager
from src.storage import create_job_id

router = APIRouter()

PLATFORMS = ["twitter", "threads", "instagram", "youtube", "linkedin"]


def _load_config() -> dict:
    """Load config and force `output_dir` to the active data dir.

    The on-disk config.json may carry a stale `output_dir` from before the
    workspace existed. Always override so collections land where the rest of
    the app reads them (workspace/data when set up, FEED_DATA_DIR otherwise).
    """
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            cfg = {}
    cfg.setdefault("platforms", {})
    cfg["output_dir"] = str(get_collected_data_dir())
    return cfg


class CollectionRequest(BaseModel):
    platforms: list[str]
    max_posts: int | None = None


async def _run_collection(task, platform: str, config: dict):
    """Run a platform collector as a background task."""
    try:
        task.status = "running"

        if task.progress.get("max_posts_override"):
            config = json.loads(json.dumps(config))
            if platform in config.get("platforms", {}):
                config["platforms"][platform]["max_posts"] = task.progress["max_posts_override"]

        if platform == "twitter":
            from src.platforms.twitter.collector import run
        elif platform == "threads":
            from src.platforms.threads.collector import run
        elif platform == "instagram":
            from src.platforms.instagram.collector import run
        elif platform == "youtube":
            from src.platforms.youtube.collector import run
        elif platform == "linkedin":
            from src.platforms.linkedin.collector import run
        else:
            task.status = "failed"
            task.error = f"Unknown platform: {platform}"
            return

        summary = await run(config)

        if summary.get("error"):
            task.status = "failed"
            task.error = summary["error"]
        else:
            task.status = "completed"
            task.summary = summary

    except asyncio.CancelledError:
        task.status = "cancelled"
    except Exception as e:
        task.status = "failed"
        task.error = str(e)
        print(f"[collection:{platform}] Failed: {e}")


# Per-job dedupe: the UI fires /collection/start/{platform} once per platform,
# and each call schedules its own auto-export coordinator. Without a guard,
# 4 platforms = 4 export folders. First coordinator per job_id wins; the rest
# return immediately.
_auto_exported_jobs: set[str] = set()
_auto_export_lock = asyncio.Lock()


async def _maybe_auto_export(job_id: str, tasks: list[asyncio.Task]):
    """Coordinator: after every platform in this job finishes, run a single
    curation export if `config.auto_export` is enabled. Fire-and-forget from
    /start. Idempotent per job_id — multiple callers collapse to one export."""
    async with _auto_export_lock:
        if job_id in _auto_exported_jobs:
            return
        _auto_exported_jobs.add(job_id)

    # Brief debounce so any sibling /start/{platform} calls under this job_id
    # have time to register their TrackedTasks before we snapshot the list.
    await asyncio.sleep(0.5)

    sibling_async_tasks = [
        t._asyncio_task for t in task_manager.get_collection_tasks_by_job(job_id)
        if t._asyncio_task is not None
    ]
    # Wait on the union of explicitly-passed tasks and any sibling tasks
    # discovered via task_manager — ensures we export ONCE, after the last
    # platform under this job_id finishes.
    await asyncio.gather(*sibling_async_tasks, *tasks, return_exceptions=True)

    cfg = _load_config()
    if not cfg.get("auto_export"):
        return

    # Resolve run_ids for every platform in this job that produced posts.json.
    today = datetime.now().strftime("%Y-%m-%d")
    data_root = get_collected_data_dir()
    job_dir = data_root / today / f"job_{job_id}"
    if not job_dir.exists():
        return
    run_ids = [
        f"{today}/job_{job_id}/{p.name}"
        for p in job_dir.iterdir()
        if p.is_dir() and (p / "posts.json").exists()
    ]
    if not run_ids:
        return

    # Call the curation export endpoint in-process so we reuse its logic
    # (media copy, goals.md bundling, viewer.html, curate.py, README).
    try:
        from app.api.export import CurationExportRequest, export_curation
        result = await export_curation(CurationExportRequest(run_ids=run_ids))
        print(f"[auto-export] job {job_id}: wrote {result.get('path')} "
              f"({result.get('post_count')} posts, {result.get('media_count')} media)")
    except Exception as e:
        print(f"[auto-export] job {job_id} failed: {e}")


@router.post("/start")
async def start_collection(request: CollectionRequest):
    config = _load_config()
    # All platforms in this request share the same job_id
    job_id = create_job_id()
    config["_job_id"] = job_id
    started = []
    spawned_tasks: list[asyncio.Task] = []

    for platform in request.platforms:
        if platform not in PLATFORMS:
            raise HTTPException(400, f"Unknown platform: {platform}")

        if task_manager.get_active_collection_task(platform):
            started.append({"platform": platform, "status": "already_running"})
            continue

        task = task_manager.create_task("collection", platform)
        task.job_id = job_id
        if request.max_posts:
            task.progress["max_posts_override"] = request.max_posts

        task._asyncio_task = asyncio.create_task(_run_collection(task, platform, config))
        spawned_tasks.append(task._asyncio_task)
        started.append({"platform": platform, "task_id": task.task_id, "status": "started"})

    # Fire-and-forget coordinator — auto-export once all platforms finish.
    if spawned_tasks:
        asyncio.create_task(_maybe_auto_export(job_id, spawned_tasks))

    return {"tasks": started, "job_id": job_id}


@router.post("/start/{platform}")
async def start_single_collection(platform: str, max_posts: int | None = None, job_id: str | None = None):
    if platform not in PLATFORMS:
        raise HTTPException(400, f"Unknown platform: {platform}")

    if task_manager.get_active_collection_task(platform):
        raise HTTPException(409, f"Collection already running for {platform}")

    config = _load_config()
    # Use provided job_id or create a new one
    config["_job_id"] = job_id or create_job_id()
    task = task_manager.create_task("collection", platform)
    task.job_id = config["_job_id"]
    if max_posts:
        task.progress["max_posts_override"] = max_posts

    task._asyncio_task = asyncio.create_task(_run_collection(task, platform, config))

    # Fire-and-forget auto-export coordinator (no-op unless config.auto_export).
    # Per-job dedupe inside _maybe_auto_export keeps multi-platform UI starts
    # from producing one pack per platform.
    asyncio.create_task(_maybe_auto_export(config["_job_id"], [task._asyncio_task]))

    return {"task_id": task.task_id, "status": task.status, "platform": platform, "job_id": config["_job_id"]}


@router.get("/status")
async def get_status():
    tasks = task_manager.get_tasks_by_type("collection")

    by_platform = {}
    for t in tasks:
        if t.platform not in by_platform or t.started_at > by_platform[t.platform].started_at:
            by_platform[t.platform] = t

    return {
        platform: task.to_dict()
        for platform, task in by_platform.items()
    }


@router.post("/stop/{task_id}")
async def stop_collection(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(404, f"Task not found: {task_id}")

    if task.status not in ("starting", "running"):
        return {"status": task.status, "message": "Task is not running"}

    if task._asyncio_task and not task._asyncio_task.done():
        task._asyncio_task.cancel()

    task.status = "cancelled"
    return {"status": "cancelled", "task_id": task_id}


@router.get("/history")
async def get_history():
    """Return collection history using the date/job/platform hierarchy."""
    from app.api.data import _walk_hierarchy, _group_runs_by_date_and_job

    runs = _walk_hierarchy()
    # Enrich with run_log summaries
    data_root = get_collected_data_dir()
    for run in runs:
        run_dir = data_root / run["run_id"]
        run_log = run_dir / "run_log.json"
        if run_log.exists():
            try:
                run["summary"] = json.loads(run_log.read_text())
            except (json.JSONDecodeError, OSError):
                pass

    grouped = _group_runs_by_date_and_job(runs)
    return {"dates": grouped, "runs": runs}
