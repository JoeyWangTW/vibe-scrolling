"""Export API endpoints.

Two distinct exports:

* **Curation export** — writes an unzipped folder `<curation_dir>/focus-lab-pack-<timestamp>/`
  containing `posts.json`, `media/`, `goals.md`, `viewer.html`, and `README.md`.
  Ready to `cd` into and run an agent.

* **Raw export** — a single `.json` or `.csv` file in `~/Downloads/`. No media,
  no zip. For analysis, backup, or piping into other tools.
"""

import csv
import io
import json
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.paths import FEED_DATA_DIR, PROJECT_ROOT, IS_BUNDLED, MEIPASS, get_collected_data_dir, get_workspace_dir

router = APIRouter()


# ----- Helpers --------------------------------------------------------------

def _load_posts_from_run(run_id: str) -> list[dict]:
    # run_id can be path-like: 2026-03-22/job_002223/twitter
    run_dir = get_collected_data_dir() / run_id
    if not run_dir.is_dir():
        return []

    for filename in ["posts.json", "tweets.json"]:
        posts_file = run_dir / filename
        if posts_file.exists():
            try:
                data = json.loads(posts_file.read_text())
                return data.get("posts", data.get("tweets", []))
            except (json.JSONDecodeError, OSError):
                continue
    return []


def _collect_media_files(posts: list[dict]) -> list[tuple[Path, str]]:
    """Collect all local media files. Returns list of (absolute_path, archive_name)."""
    files = []
    seen = set()
    for post in posts:
        for rel_path in post.get("local_media_paths") or []:
            if rel_path in seen:
                continue
            seen.add(rel_path)
            abs_path = get_collected_data_dir() / rel_path
            if abs_path.exists():
                archive_name = f"media/{abs_path.name}"
                files.append((abs_path, archive_name))
    return files


def _rewrite_media_paths(posts: list[dict]) -> list[dict]:
    """Rewrite local_media_paths to point at the pack's flat media/ folder."""
    rewritten = []
    for post in posts:
        p = {**post}
        if p.get("local_media_paths"):
            p["local_media_paths"] = [
                f"media/{Path(path).name}" for path in p["local_media_paths"]
            ]
        rewritten.append(p)
    return rewritten


def _posts_json(posts: list[dict], run_ids: list[str]) -> str:
    return json.dumps({
        "export_metadata": {
            "exported_at": datetime.now().isoformat(),
            "source": "focus-lab-feed-collector",
            "run_ids": run_ids,
            "total_posts": len(posts),
        },
        "posts": posts,
    }, indent=2, ensure_ascii=False)


def _posts_csv(posts: list[dict]) -> str:
    output = io.StringIO()
    fields = [
        "id", "platform", "author_handle", "author_name", "text",
        "created_at", "url", "likes", "reposts", "replies", "quotes",
        "is_repost", "is_ad", "media_urls", "video_urls", "local_media_paths",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for post in posts:
        row = {**post}
        row["media_urls"] = "|".join(row.get("media_urls") or [])
        row["video_urls"] = "|".join(row.get("video_urls") or [])
        row["local_media_paths"] = "|".join(row.get("local_media_paths") or [])
        writer.writerow(row)
    return output.getvalue()


def _human_size(path: Path) -> str:
    b = path.stat().st_size
    return f"{b / 1024 / 1024:.1f} MB" if b > 1024 * 1024 else f"{b / 1024:.0f} KB"


def _resolve_curation_dir() -> Path | None:
    """Packs land in `<workspace>/exports/`. Returns None if no workspace set up."""
    ws = get_workspace_dir()
    if ws is None:
        return None
    return ws / "exports"


def _viewer_html_source() -> Path | None:
    """Locate the mobile viewer HTML to bundle into curation packs."""
    # Dev: viewer/mobile.html at project root. Bundled: ship via spec datas.
    if IS_BUNDLED and MEIPASS:
        candidate = MEIPASS / "viewer" / "mobile.html"
        if candidate.exists():
            return candidate
    candidate = PROJECT_ROOT / "viewer" / "mobile.html"
    return candidate if candidate.exists() else None


def _curate_script_source() -> Path | None:
    """Locate the curator batching script to bundle into packs."""
    if IS_BUNDLED and MEIPASS:
        candidate = MEIPASS / "skills" / "focus-lab-curator" / "curate.py"
        if candidate.exists():
            return candidate
    candidate = PROJECT_ROOT / "skills" / "focus-lab-curator" / "curate.py"
    return candidate if candidate.exists() else None


def _pack_readme(pack_name: str, post_count: int, media_count: int) -> str:
    return f"""# {pack_name}

A Focus Lab Feed pack: **{post_count} posts**, **{media_count} media files**.

## Curate this pack

From this directory:

    python3 curate.py

`curate.py` is a stdlib-only Python script that reads `posts.json` + `goals.md`,
batches posts (default 20), and invokes `claude --print` per batch to score
them. It writes `posts.filtered.json` with drop rules applied and an audit log.

Options: `--batch 10` for smaller batches, `--model opus` for more careful scoring.

### Or let your agent drive

You can also run your agent (Claude Code / Cursor / Codex) in this directory
and say "curate this feed" — the Focus Lab Curator skill will run the script
for you, or score in-context for small packs.

## View the result

When `curate.py` finishes, the Focus Lab desktop app's **AI Curation** tab
picks up `posts.filtered.json` automatically — open the app and switch to
that tab to scroll the curated feed.
"""


# ----- Requests -------------------------------------------------------------

class CurationExportRequest(BaseModel):
    run_ids: list[str]


class RawExportRequest(BaseModel):
    run_ids: list[str]
    format: str = "json"  # "json" or "csv"


# ----- Endpoints ------------------------------------------------------------

@router.post("/curation")
async def export_curation(request: CurationExportRequest):
    """Write an unzipped pack folder into the curation directory.

    The folder contains posts.json, media/, goals.md (from workspace), viewer.html,
    and a README. Agents cd into it to curate.
    """
    all_posts: list[dict] = []
    for run_id in request.run_ids:
        all_posts.extend(_load_posts_from_run(run_id))

    if not all_posts:
        raise HTTPException(404, "No posts found in selected runs")

    curation_dir = _resolve_curation_dir()
    if curation_dir is None:
        raise HTTPException(
            412,
            "Workspace not set up yet. Click 'Set up curation folder' on the Export page.",
        )

    media_files = _collect_media_files(all_posts)
    pack_posts = _rewrite_media_paths(all_posts)

    curation_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    pack_name = f"focus-lab-pack-{timestamp}"
    pack_dir = curation_dir / pack_name
    pack_dir.mkdir()
    (pack_dir / "media").mkdir()

    # posts.json
    (pack_dir / "posts.json").write_text(_posts_json(pack_posts, request.run_ids))

    # Copy media
    copied = 0
    for abs_path, archive_name in media_files:
        dest = pack_dir / archive_name
        try:
            shutil.copy2(abs_path, dest)
            copied += 1
        except OSError:
            pass

    # Bundle viewer.html
    viewer = _viewer_html_source()
    if viewer:
        shutil.copy2(viewer, pack_dir / "viewer.html")

    # Bundle curate.py — the batching harness the skill invokes.
    for src_fn, dest_name in (
        (_curate_script_source, "curate.py"),
    ):
        src = src_fn()
        if src:
            dest = pack_dir / dest_name
            shutil.copy2(src, dest)
            try:
                dest.chmod(0o755)
            except OSError:
                pass

    # Copy workspace goals.md if it exists (so the curator has something to start with).
    ws = get_workspace_dir()
    if ws is not None:
        ws_goals = ws / "goals.md"
        if ws_goals.exists():
            shutil.copy2(ws_goals, pack_dir / "goals.md")

    # README
    (pack_dir / "README.md").write_text(_pack_readme(pack_name, len(all_posts), copied))

    # Compute folder size
    total_bytes = sum(p.stat().st_size for p in pack_dir.rglob("*") if p.is_file())
    size_label = f"{total_bytes / 1024 / 1024:.1f} MB" if total_bytes > 1024 * 1024 else f"{total_bytes / 1024:.0f} KB"

    return {
        "success": True,
        "kind": "curation",
        "path": str(pack_dir),
        "name": pack_name,
        "curation_dir": str(curation_dir),
        "post_count": len(all_posts),
        "media_count": copied,
        "size": size_label,
    }


@router.post("/raw")
async def export_raw(request: RawExportRequest):
    """Write a single posts.json or posts.csv file to ~/Downloads/. No media, no zip."""
    all_posts: list[dict] = []
    for run_id in request.run_ids:
        all_posts.extend(_load_posts_from_run(run_id))

    if not all_posts:
        raise HTTPException(404, "No posts found in selected runs")

    downloads = Path.home() / "Downloads"
    downloads.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    if request.format == "csv":
        content = _posts_csv(all_posts)
        dest = downloads / f"focus-lab-raw-{timestamp}.csv"
    else:
        # For raw we keep local_media_paths as-is (pointing into feed_data/).
        content = _posts_json(all_posts, request.run_ids)
        dest = downloads / f"focus-lab-raw-{timestamp}.json"

    dest.write_text(content)

    return {
        "success": True,
        "kind": "raw",
        "path": str(dest),
        "filename": dest.name,
        "format": request.format,
        "post_count": len(all_posts),
        "size": _human_size(dest),
    }


