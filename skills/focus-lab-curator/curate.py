#!/usr/bin/env python3
"""Focus Lab Curator — score one collection job against the user's goals.

A job is `data/YYYY-MM-DD/job_HHMMSS/` and contains one platform subfolder
per source (x, threads, instagram, youtube, linkedin). Each holds a
`posts.json`. This script gathers every post in the job, scores them all
together against `goals.md`, and writes one combined
`posts.filtered.json` at the job root — so the AI Curation tab can rank
across platforms in a single feed.

Supported CLIs (auto-detected in order):
    claude       → Anthropic Claude Code       (`claude --print`)
    codex        → OpenAI Codex CLI            (`codex -q`)
    cursor-agent → Cursor Agent CLI            (`cursor-agent --print --output-format text`)

Typical use, from the workspace root:

    python3 skills/focus-lab-curator/curate.py            # latest job, all platforms together
    python3 skills/focus-lab-curator/curate.py --job 2026-05-02/job_020746
    python3 skills/focus-lab-curator/curate.py --batch 10 --cli codex
    python3 skills/focus-lab-curator/curate.py --workspace /path/to/workspace

Outputs:
    <workspace>/data/<date>/<job_id>/posts.filtered.json

Requirements: Python 3.9+ and one of {claude, codex, cursor-agent} in PATH.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

BATCH_SIZE_DEFAULT = 20
# Each batch is an independent CLI invocation, so we can fan out. 4 keeps
# wall time down without poking the agent's rate limits too hard.
CONCURRENCY_DEFAULT = 4

# Default Claude Code model for batch scoring. Sonnet hits the right cost/
# quality trade-off here — scoring is short-context per batch and doesn't
# need Opus-level reasoning. Override per-run with `--model`.
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"

JOB_DIR_RE = re.compile(r"^job_\d{6}$")
DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


PROMPT = """You are scoring a batch of social-media posts against a user's content preferences.

The user wants a feed that:
1. Helps them toward their goals (most important)
2. Keeps the joy (humor, art, hobbies — score high even if unrelated to goals)
3. Drops the drain (negativity, outrage loops, engagement bait)

USER PREFERENCES (goals.md):
<goals>
{goals}
</goals>

SCORING RUBRIC (integer 0–100):
- 80–100: strongly goal-aligned OR peak joy match
- 60–79:  solid goal or solid joy (the backbone of the feed)
- 40–59:  adjacent / tangentially interesting
- 20–39:  weak signal
- 1–19:   drain-shaped, outrage bait, on avoid list
- 0:      explicit muted author or explicit skip match

CATEGORY (pick the best one):
- "goal"     directly helps with the stated goal
- "joy"      joy-list match (hobby, humor, art, pets, etc.)
- "adjacent" tangentially interesting
- "drain"    on avoid list or drain-shaped
- "neutral"  unclear fit

HARD RULES (override the rubric):
- is_ad=true            → score ≤ 10 unless on the "want more" list
- is_repost=true        → score the reposted content, not the reposter's choice
- quoted_post present   → consider the wrapper + quoted content together
- mixed platforms       → score fairly across them; a tweet and a YouTube short on the same topic should get comparable scores

POSTS TO SCORE:
<batch>
{batch}
</batch>

OUTPUT:
Return ONLY a JSON array — one object per input post, in the same order.
Each object has EXACTLY these keys:
  id             (string, copy from input)
  score          (integer 0–100)
  category       ("goal" | "joy" | "adjacent" | "drain" | "neutral")
  filter_reason  (string, 1–2 sentences)

No prose, no markdown fences, no preamble. Start with [ and end with ].
"""


# Fields worth sending to the model. Smaller payload → faster batch.
SLIM_FIELDS = (
    "id", "platform", "text", "author_handle", "author_name",
    "is_repost", "original_author", "is_ad", "media_urls", "video_urls",
)


def slim(post: dict) -> dict:
    out = {k: post.get(k) for k in SLIM_FIELDS if k in post}
    qp = post.get("quoted_post")
    if isinstance(qp, dict):
        out["quoted_post"] = {"text": qp.get("text"), "author_handle": qp.get("author_handle")}
    return out


# Which CLI to shell out to. Each adapter takes (prompt, model|None) and returns
# the agent's stdout text. Order in SUPPORTED_CLIS is the auto-detect priority.

def _run_claude(prompt: str, model: str | None) -> str:
    cmd = ["claude", "--print", "--model", model or DEFAULT_CLAUDE_MODEL]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"claude --print failed (exit {r.returncode}): {r.stderr.strip()}")
    return r.stdout


def _run_codex(prompt: str, model: str | None) -> str:
    cmd = ["codex", "-q"]
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"codex -q failed (exit {r.returncode}): {r.stderr.strip()}")
    return r.stdout


def _run_cursor_agent(prompt: str, model: str | None) -> str:
    cmd = ["cursor-agent", "--print", "--output-format", "text"]
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"cursor-agent failed (exit {r.returncode}): {r.stderr.strip()}")
    return r.stdout


SUPPORTED_CLIS: dict[str, tuple[str, callable]] = {
    "claude":       ("claude",       _run_claude),
    "codex":        ("codex",        _run_codex),
    "cursor-agent": ("cursor-agent", _run_cursor_agent),
}


def detect_cli(preferred: str | None = None) -> tuple[str, callable]:
    if preferred:
        if preferred not in SUPPORTED_CLIS:
            sys.exit(f"error: unknown --cli {preferred!r}. Supported: {', '.join(SUPPORTED_CLIS)}")
        binary, runner = SUPPORTED_CLIS[preferred]
        if shutil.which(binary) is None:
            sys.exit(f"error: `{binary}` CLI not found in PATH (requested via --cli).")
        return preferred, runner
    for name, (binary, runner) in SUPPORTED_CLIS.items():
        if shutil.which(binary):
            return name, runner
    sys.exit(
        "error: no supported agent CLI found in PATH.\n"
        "Install one of: claude (https://github.com/anthropics/claude-code),\n"
        "                codex (https://github.com/openai/codex),\n"
        "                cursor-agent (https://docs.cursor.com/agent-cli)\n"
        "or score this job in-context with your agent instead."
    )


def extract_json_array(text: str) -> list:
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        return json.loads(text)
    if "```" in text:
        for block in text.split("```"):
            block = block.strip()
            if block.startswith("json"):
                block = block[4:].strip()
            if block.startswith("[") and block.endswith("]"):
                return json.loads(block)
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("no JSON array found in response")


def score_batch(batch: list[dict], goals: str, runner: callable, model: str | None = None) -> list[dict]:
    prompt = PROMPT.format(
        goals=goals.strip(),
        batch=json.dumps([slim(p) for p in batch], indent=2, ensure_ascii=False),
    )
    return extract_json_array(runner(prompt, model))


# ----- Workspace / job discovery --------------------------------------------

def resolve_workspace(arg: str | None) -> Path:
    """Pick the workspace root. Priority: --workspace flag, then CWD if it
    looks like a workspace (has data/ or goals.md), then walk upward."""
    if arg:
        ws = Path(arg).expanduser().resolve()
        if not ws.is_dir():
            sys.exit(f"error: --workspace {ws} is not a directory")
        return ws

    cwd = Path.cwd().resolve()
    # CWD is a workspace if it has data/ or goals.md, OR if it's the data dir itself
    for candidate in (cwd, *cwd.parents):
        if (candidate / "data").is_dir() or (candidate / "goals.md").is_file():
            return candidate
        # If we're standing inside a data/<date>/<job_id>/<platform>/ tree,
        # the workspace is the ancestor that contains data/.
        if candidate.name == "data" and candidate.parent.is_dir():
            return candidate.parent

    sys.exit(
        f"error: couldn't find a Focus Lab workspace from {cwd}.\n"
        "Run from inside one (a folder that contains `data/` or `goals.md`),\n"
        "or pass --workspace /path/to/workspace."
    )


def latest_job(data_dir: Path) -> tuple[str, str]:
    """Return (date, job_id) for the most recent job under data/. Picks the
    highest-numbered job under the highest date."""
    if not data_dir.is_dir():
        sys.exit(f"error: {data_dir} not found — collect something first.")
    dates = sorted(
        [d for d in data_dir.iterdir() if d.is_dir() and DATE_DIR_RE.match(d.name)],
        reverse=True,
    )
    if not dates:
        sys.exit(f"error: no collections found under {data_dir}.")
    for date_dir in dates:
        jobs = sorted(
            [j for j in date_dir.iterdir() if j.is_dir() and JOB_DIR_RE.match(j.name)],
            reverse=True,
        )
        for job_dir in jobs:
            # A job is curatable if any of its platform subfolders has posts.json.
            if any((p / "posts.json").exists() for p in job_dir.iterdir() if p.is_dir()):
                return date_dir.name, job_dir.name.replace("job_", "")
    sys.exit(f"error: found dates but no job has a posts.json under {data_dir}.")


def parse_job_arg(arg: str) -> tuple[str, str]:
    """Accept '2026-05-02/job_020746' or '2026-05-02/020746' or just 'job_020746' (today)."""
    arg = arg.strip().strip("/")
    if "/" in arg:
        date, job = arg.split("/", 1)
        if not DATE_DIR_RE.match(date):
            sys.exit(f"error: --job date must look like YYYY-MM-DD, got {date!r}")
        job_id = job.replace("job_", "")
        if not re.match(r"^\d{6}$", job_id):
            sys.exit(f"error: --job id must be 6 digits (HHMMSS), got {job!r}")
        return date, job_id
    # bare job id — assume today
    job_id = arg.replace("job_", "")
    if not re.match(r"^\d{6}$", job_id):
        sys.exit(f"error: --job must be DATE/job_HHMMSS or job_HHMMSS, got {arg!r}")
    return datetime.now().strftime("%Y-%m-%d"), job_id


def load_job_posts(job_dir: Path) -> tuple[list[dict], list[str]]:
    """Read every platform's posts.json under the job and return one big list.

    Returns (posts, platforms) — each post already has a `platform` field
    set by the collector, so cross-platform ranking is well-defined.
    """
    posts: list[dict] = []
    platforms: list[str] = []
    for sub in sorted(job_dir.iterdir()):
        if not sub.is_dir():
            continue
        posts_file = sub / "posts.json"
        if not posts_file.exists():
            posts_file = sub / "tweets.json"
        if not posts_file.exists():
            continue
        try:
            data = json.loads(posts_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"warning: couldn't parse {posts_file}: {e}", file=sys.stderr)
            continue
        platform_posts = data.get("posts") or data.get("tweets") or []
        # Defensively stamp the platform field if the collector forgot.
        for p in platform_posts:
            if not p.get("platform"):
                p["platform"] = sub.name
        posts.extend(platform_posts)
        platforms.append(sub.name)
    return posts, platforms


# ----- Main -----------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="Curate one Focus Lab collection job.")
    p.add_argument("--workspace", default=None,
                   help="workspace root (default: auto-detect from CWD by walking up)")
    p.add_argument("--job", default=None,
                   help="job to curate as 'YYYY-MM-DD/job_HHMMSS' (default: latest)")
    p.add_argument("--cli", default=None, choices=list(SUPPORTED_CLIS),
                   help="force a specific CLI (default: auto — claude > codex > cursor-agent)")
    p.add_argument("--batch", type=int, default=BATCH_SIZE_DEFAULT,
                   help=f"batch size (default {BATCH_SIZE_DEFAULT})")
    p.add_argument("--concurrency", type=int, default=CONCURRENCY_DEFAULT,
                   help=f"max batches in parallel (default {CONCURRENCY_DEFAULT}; set 1 for sequential)")
    p.add_argument("--model", default=None,
                   help="passed through to the CLI's --model flag")
    args = p.parse_args()

    workspace = resolve_workspace(args.workspace)
    data_dir = workspace / "data"
    goals_path = workspace / "goals.md"

    if not data_dir.is_dir():
        sys.exit(f"error: {data_dir} not found in workspace.")

    if args.job:
        date, job_id = parse_job_arg(args.job)
    else:
        date, job_id = latest_job(data_dir)

    job_dir = data_dir / date / f"job_{job_id}"
    if not job_dir.is_dir():
        sys.exit(f"error: job {date}/job_{job_id} not found in {data_dir}.")

    posts, platforms = load_job_posts(job_dir)
    if not posts:
        sys.exit(f"error: no posts found in {job_dir} (no platform has a posts.json).")

    print(f"Workspace: {workspace}", file=sys.stderr)
    print(f"Job:       {date}/job_{job_id}", file=sys.stderr)
    print(f"Platforms: {', '.join(platforms)}  ({len(posts)} posts total)", file=sys.stderr)

    cli_name, runner = detect_cli(args.cli)
    print(f"CLI:       {cli_name}", file=sys.stderr)

    goals = goals_path.read_text() if goals_path.exists() else "(no goals.md yet — score charitably)"

    total_batches = (len(posts) + args.batch - 1) // args.batch
    concurrency = max(1, min(args.concurrency, total_batches))
    print(f"Curating in {total_batches} batch(es) of {args.batch} ({concurrency} in parallel)...",
          file=sys.stderr)

    batches: list[tuple[int, list[dict]]] = [
        (i // args.batch + 1, posts[i:i + args.batch])
        for i in range(0, len(posts), args.batch)
    ]

    scored_map: dict[str, dict] = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(score_batch, batch, goals, runner, args.model): n
            for n, batch in batches
        }
        for fut in as_completed(futures):
            n = futures[fut]
            try:
                scored = fut.result()
            except Exception as e:
                completed += 1
                print(f"  [{completed}/{total_batches}] !! batch {n} failed: {e}", file=sys.stderr)
                continue
            for s in scored:
                sid = str(s.get("id", ""))
                if sid:
                    scored_map[sid] = s
            completed += 1
            print(f"  [{completed}/{total_batches}] batch {n} ✓ ({len(scored)} posts)", file=sys.stderr)

    kept: list[dict] = []
    dropped: list[dict] = []
    for post in posts:
        s = scored_map.get(str(post.get("id", "")))
        if not s:
            kept.append(dict(post, score=50, category="neutral",
                             filter_reason="Not scored (batch error) — kept by default."))
            continue
        score = int(s.get("score", 50))
        cat = str(s.get("category", "neutral"))
        reason = str(s.get("filter_reason", ""))
        if cat == "drain" or score <= 19:
            dropped.append({"id": str(post.get("id", "")), "score": score,
                            "category": cat, "filter_reason": reason,
                            "platform": post.get("platform", "")})
        else:
            kept.append(dict(post, score=score, category=cat, filter_reason=reason))

    kept.sort(key=lambda p: -int(p.get("score", 0)))

    scores = [int(p.get("score", 0)) for p in kept]
    cats: dict[str, int] = {}
    platform_counts: dict[str, int] = {}
    for p2 in kept:
        c = p2.get("category", "neutral")
        cats[c] = cats.get(c, 0) + 1
        pl = p2.get("platform", "unknown")
        platform_counts[pl] = platform_counts.get(pl, 0) + 1

    output = {
        "filter_metadata": {
            "filtered_at": datetime.now(timezone.utc).isoformat(),
            "workspace": str(workspace),
            "job": f"{date}/job_{job_id}",
            "platforms": platforms,
            "goals_snapshot": goals,
            "source_posts": len(posts),
            "kept_posts": len(kept),
            "dropped_count": len(dropped),
            "drop_rule": "category=drain OR score<=19",
            "median_score": sorted(scores)[len(scores) // 2] if scores else 0,
            "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "category_counts": cats,
            "platform_counts": platform_counts,
            "dropped": dropped,
        },
        "posts": kept,
    }

    out_path = job_dir / "posts.filtered.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\nDone — {len(kept)} kept, {len(dropped)} dropped → {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
