"""Workspace bootstrap — the user's Focus Lab folder.

One user-picked folder holds everything: collected feeds (`data/`), the
curator skill, default `goals.md`, and the Claude Code / agent
auto-discovery glue (`.claude/skills/` symlink, CLAUDE.md, AGENTS.md).
Bootstrapping is ONLY done on explicit user setup — not at app start. This
way a first-time user isn't surprised by a folder appearing in their home dir.

The workspace is the single source of truth — collections write directly
into `<workspace>/data/` (no separate app-data dir) and curation reads them
in place. Packing for sharing is an opt-in step.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from app.paths import CONFIG_PATH, get_workspace_dir, skill_source_dir

SKILL_NAME = "focus-lab-curator"


CLAUDE_MD = """# Focus Lab Feed workspace

You are working in a Focus Lab Feed workspace. Collected feeds live in
`./data/` (organized as `data/YYYY-MM-DD/job_HHMMSS/<platform>/posts.json`),
the curator skill lives at `./skills/focus-lab-curator/`, and the user's
content preferences live in `./goals.md`.

## Curator skill

When the user asks to curate a feed, follow the instructions in
`skills/focus-lab-curator/SKILL.md` (also available via
`.claude/skills/focus-lab-curator/SKILL.md`).

It handles: interactive content-preferences bootstrap (writes `goals.md`),
scoring posts 0–100 against those preferences, and producing a filtered
JSON output the desktop app's **AI Curation** tab can read.

## Typical workflow

1. The user collects a feed in the Focus Lab Feed app (lands in `./data/`).
2. The user says "curate this feed" (or similar).
3. You use the curator skill to score the latest job's posts against
   `./goals.md` and emit a filtered JSON file alongside the data.
4. The Focus Lab Feed app's **AI Curation** tab picks it up automatically.

## Goals resolution

The curator skill reads `./goals.md` from the workspace root. If it's
missing or empty, it runs a short interview to populate it.
"""


AGENTS_MD = """# Focus Lab Feed workspace

Collected feeds live in `./data/YYYY-MM-DD/job_HHMMSS/<platform>/`. Each
platform folder contains `posts.json`, `media/`, and `run_log.json`.

To curate the latest collection for the Focus Lab Feed viewer, read
`skills/focus-lab-curator/SKILL.md` and follow its instructions. The skill
reads `./goals.md` (interviewing the user to bootstrap it if missing) and
emits a filtered JSON file.

Default goals: `./goals.md` in this directory.
"""


README_MD = """# Focus Lab Feed — Workspace

Everything Focus Lab Feed needs lives here.

- `data/` — collected feeds land here automatically (date / job / platform).
- `skills/focus-lab-curator/` — the curator tool kit (the skill instructions,
  `curate.py` for batch scoring, and `viewer.html` for previewing a curated
  job in a phone-shaped browser).
- `.claude/skills/focus-lab-curator/` — Claude Code auto-discovery (symlink).
- `goals.md` — your default content preferences (the skill interviews you
  to fill this in on first use).
- `CLAUDE.md` / `AGENTS.md` — instructions that point agents at the skill.

## Flow

1. **Collect** — open the Focus Lab Feed app, run a collection. Posts and
   media land directly in `./data/YYYY-MM-DD/job_HHMMSS/<platform>/`.
2. **Curate** — open Claude Code (or another agent) in this folder and say
   "curate the latest feed":

       cd ~/Focus Lab Feed
       claude                         # then: "curate the latest feed"

   On the first run the skill interviews you to populate `goals.md`. After
   that, it scores posts against your goals and writes a filtered JSON file
   alongside the data.
3. **View** — open the Focus Lab Feed app's **AI Curation** tab. Curated
   results show up automatically.

If you ever want to share a curated job with someone, an "Output as pack"
action will bundle just `posts.json` + `media/` into a self-contained
folder. Day-to-day curation doesn't need it.

## Goals

`goals.md` is your default content preferences — you can edit it by hand,
or leave it blank and let the curator skill interview you the first time
you run it.
"""


def _relative_path(target: Path, start: Path) -> Path:
    """Best-effort relative path for symlinks. Falls back to absolute."""
    try:
        return Path(target.resolve().relative_to(start.resolve(), walk_up=True))
    except (ValueError, TypeError):
        return target.resolve()


def bootstrap_workspace(workspace: Path, update_app_files: bool = False) -> dict:
    """Populate `workspace` with the curation structure. Idempotent.

    Files split into two categories:

    * **App-managed** — curator skill, CLAUDE.md, AGENTS.md, README.md, and
      the `.claude/skills` symlink. These evolve with app versions. Created
      when missing; refreshed to the current version when `update_app_files`
      is True.
    * **User-managed** — `goals.md` and `exports/`. Created when missing,
      but NEVER overwritten regardless of flags — this is your data.
    """
    ws = Path(workspace).expanduser().resolve()
    ws.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    updated: list[str] = []

    # ---- user-managed: data/ — where collections land. Pre-create so the
    # static file route serves it cleanly even before the first collection.
    data_dir = ws / "data"
    if not data_dir.exists():
        data_dir.mkdir()
        created.append("data/")

    # ---- app-managed: curator skill ----
    src = skill_source_dir()
    dst = ws / "skills" / SKILL_NAME
    same_dir = src.exists() and src.resolve() == dst.resolve()
    if src.exists() and not same_dir:
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst)
            created.append(f"skills/{SKILL_NAME}/")
        elif update_app_files:
            shutil.rmtree(dst)
            shutil.copytree(src, dst)
            updated.append(f"skills/{SKILL_NAME}/")

    # ---- app-managed: .claude/skills symlink ----
    claude_skill_link = ws / ".claude" / "skills" / SKILL_NAME
    skill_for_link = dst if dst.exists() else src
    link_exists = claude_skill_link.exists() or claude_skill_link.is_symlink()
    if skill_for_link.exists():
        if not link_exists:
            claude_skill_link.parent.mkdir(parents=True, exist_ok=True)
            try:
                rel = _relative_path(skill_for_link, claude_skill_link.parent)
                claude_skill_link.symlink_to(rel)
                created.append(".claude/skills/focus-lab-curator (symlink)")
            except OSError:
                shutil.copytree(skill_for_link, claude_skill_link)
                created.append(".claude/skills/focus-lab-curator (copy)")
        elif update_app_files and not claude_skill_link.is_symlink():
            # Stale copy — replace with fresh symlink.
            shutil.rmtree(claude_skill_link)
            try:
                rel = _relative_path(skill_for_link, claude_skill_link.parent)
                claude_skill_link.symlink_to(rel)
                updated.append(".claude/skills/focus-lab-curator (symlink)")
            except OSError:
                shutil.copytree(skill_for_link, claude_skill_link)
                updated.append(".claude/skills/focus-lab-curator (copy)")

    # ---- user-managed: goals.md — create if missing, NEVER overwrite ----
    goals_dst = ws / "goals.md"
    if not goals_dst.exists():
        template = skill_for_link / "templates" / "goals.md"
        if template.exists():
            shutil.copy2(template, goals_dst)
            created.append("goals.md")

    # ---- app-managed: docs ----
    for rel, content in (("CLAUDE.md", CLAUDE_MD), ("AGENTS.md", AGENTS_MD), ("README.md", README_MD)):
        target = ws / rel
        if not target.exists():
            target.write_text(content)
            created.append(rel)
        elif update_app_files and target.read_text() != content:
            target.write_text(content)
            updated.append(rel)

    return {"workspace": str(ws), "created": created, "updated": updated}


def _read_skill_manifest(skill_dir: Path) -> dict | None:
    """Return the parsed skill.json for a given skill directory, or None."""
    manifest = skill_dir / "skill.json"
    if not manifest.exists():
        return None
    try:
        return json.loads(manifest.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def skill_status() -> dict:
    """Compare the workspace's curator skill version against the shipped version.

    Returns:
        shipped_version:   what this app build ships
        workspace_version: what's installed in the user's workspace (None if none)
        outdated:          True if workspace exists AND versions differ
        is_shared_source:  True in dev when the workspace == repo root and both
                           paths resolve to the same dir (no update needed).
    """
    shipped = skill_source_dir()
    shipped_manifest = _read_skill_manifest(shipped) or {}
    shipped_version = shipped_manifest.get("version")

    ws = get_workspace_dir()
    if ws is None:
        return {
            "shipped_version": shipped_version,
            "workspace_version": None,
            "outdated": False,
            "is_shared_source": False,
            "workspace_has_skill": False,
        }

    ws_skill = ws / "skills" / SKILL_NAME
    is_shared = shipped.exists() and ws_skill.exists() and shipped.resolve() == ws_skill.resolve()
    ws_manifest = _read_skill_manifest(ws_skill) or {}
    workspace_version = ws_manifest.get("version")

    return {
        "shipped_version": shipped_version,
        "workspace_version": workspace_version,
        "outdated": bool(
            shipped_version
            and workspace_version
            and shipped_version != workspace_version
            and not is_shared
        ),
        "is_shared_source": is_shared,
        "workspace_has_skill": workspace_version is not None or ws_skill.exists(),
    }


def save_workspace_dir(workspace: Path) -> None:
    """Persist the chosen workspace path into config.json."""
    cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            cfg = {}
    cfg["workspace_dir"] = str(Path(workspace).expanduser().resolve())
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def reveal_in_finder(path: Path) -> bool:
    """Open a path in the OS file manager."""
    target = Path(path)
    if not target.exists():
        return False
    if sys.platform == "darwin":
        subprocess.run(["open", str(target)], check=False)
    elif sys.platform == "win32":
        subprocess.run(["explorer", str(target)], check=False)
    else:
        subprocess.run(["xdg-open", str(target)], check=False)
    return True
