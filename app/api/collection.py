"""Collection API endpoints — start, stop, status, history."""

import asyncio
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.paths import CONFIG_PATH, get_collected_data_dir
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


@router.post("/start")
async def start_collection(request: CollectionRequest):
    config = _load_config()
    # All platforms in this request share the same job_id
    job_id = create_job_id()
    config["_job_id"] = job_id
    started = []

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
        started.append({"platform": platform, "task_id": task.task_id, "status": "started"})

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
