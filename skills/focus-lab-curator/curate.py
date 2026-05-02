#!/usr/bin/env python3
"""Focus Lab Curator — batch-scores a pack using an agent CLI you already have.

Supported CLIs (auto-detected in this order):
    claude       → Anthropic Claude Code       (`claude --print`)
    codex        → OpenAI Codex CLI            (`codex -q`)
    cursor-agent → Cursor Agent CLI            (`cursor-agent --print --output-format text`)

From inside a pack directory (one containing posts.json and goals.md):

    python3 curate.py                                # auto-pick a CLI; Claude Code defaults to Sonnet 4.6
    python3 curate.py PACK_DIR                       # score a specific pack
    python3 curate.py --cli codex                    # force a specific CLI
    python3 curate.py --batch 10                     # smaller batches (default 20)
    python3 curate.py --model claude-opus-4-7        # override the model the CLI uses

Reads `posts.json` + `goals.md`, chunks posts, invokes the chosen CLI per
batch with the scoring rubric inlined, and writes `posts.filtered.json`
with drop rules applied (category=drain OR score<=19 → audit log, not kept).

Progress is written to stderr so you see `batch 3/12...` while it runs.

Requirements: Python 3.9+ and at least one of {claude, codex, cursor-agent}
in PATH.
"""

from __future__ import annotations

import argparse
import json
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
    # codex expects the prompt as a positional argument rather than on stdin.
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
    """Return (cli_name, runner) — preferred if available, else first installed."""
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
        "or score this pack in-context with your agent instead."
    )


def extract_json_array(text: str) -> list:
    """Pull a JSON array out of the model's response, tolerating code fences."""
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        return json.loads(text)
    # Try fenced blocks
    if "```" in text:
        for block in text.split("```"):
            block = block.strip()
            if block.startswith("json"):
                block = block[4:].strip()
            if block.startswith("[") and block.endswith("]"):
                return json.loads(block)
    # Last resort — find first [ and last ]
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


def main() -> int:
    p = argparse.ArgumentParser(description="Curate a Focus Lab Feed pack via an agent CLI.")
    p.add_argument("pack", nargs="?", default=".", help="pack directory (default: cwd)")
    p.add_argument("--cli", default=None, choices=list(SUPPORTED_CLIS),
                   help="force a specific CLI (default: auto — claude > codex > cursor-agent)")
    p.add_argument("--batch", type=int, default=BATCH_SIZE_DEFAULT, help=f"batch size (default {BATCH_SIZE_DEFAULT})")
    p.add_argument("--concurrency", type=int, default=CONCURRENCY_DEFAULT,
                   help=f"max batches running in parallel (default {CONCURRENCY_DEFAULT}; set 1 for sequential)")
    p.add_argument("--model", default=None, help="passed through to the CLI's --model flag")
    p.add_argument("--keep-media", action="store_true",
                   help="skip media cleanup (default: remove media for dropped posts to shrink the pack)")
    p.add_argument("--drop-videos", action="store_true",
                   help="additionally drop ALL videos (mp4/mov/webm) regardless of whether they're kept")
    args = p.parse_args()

    pack = Path(args.pack).resolve()
    posts_path = pack / "posts.json"
    goals_path = pack / "goals.md"
    out_path = pack / "posts.filtered.json"

    if not posts_path.exists():
        sys.exit(f"error: {posts_path} not found")
    cli_name, runner = detect_cli(args.cli)
    print(f"Using {cli_name}", file=sys.stderr)

    goals = goals_path.read_text() if goals_path.exists() else "(no goals.md yet — score charitably)"
    data = json.loads(posts_path.read_text())
    posts = data.get("posts") or data.get("tweets") or []
    if not isinstance(posts, list) or not posts:
        sys.exit("error: posts.json has no posts array")

    total_batches = (len(posts) + args.batch - 1) // args.batch
    concurrency = max(1, min(args.concurrency, total_batches))
    print(f"Curating {len(posts)} posts in {total_batches} batch(es) of {args.batch} "
          f"({concurrency} in parallel)...", file=sys.stderr)

    # Build the batch list up front so we can fan out. Each CLI invocation
    # blocks on subprocess.run, but it releases the GIL during I/O — threads
    # get true wall-clock parallelism here.
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

    # Assemble output
    kept: list[dict] = []
    dropped: list[dict] = []
    for post in posts:
        s = scored_map.get(str(post.get("id", "")))
        if not s:
            # Unscored (batch failed) — keep at neutral mid-score so content isn't lost.
            kept.append(dict(post, score=50, category="neutral",
                             filter_reason="Not scored (batch error) — kept by default."))
            continue
        score = int(s.get("score", 50))
        cat = str(s.get("category", "neutral"))
        reason = str(s.get("filter_reason", ""))
        if cat == "drain" or score <= 19:
            dropped.append({"id": str(post.get("id", "")), "score": score, "category": cat, "filter_reason": reason})
        else:
            kept.append(dict(post, score=score, category=cat, filter_reason=reason))

    kept.sort(key=lambda p: -int(p.get("score", 0)))

    scores = [int(p.get("score", 0)) for p in kept]
    cats: dict[str, int] = {}
    for p2 in kept:
        c = p2.get("category", "neutral")
        cats[c] = cats.get(c, 0) + 1

    output = {
        "filter_metadata": {
            "filtered_at": datetime.now(timezone.utc).isoformat(),
            "goals_snapshot": goals,
            "source_posts": len(posts),
            "kept_posts": len(kept),
            "dropped_count": len(dropped),
            "drop_rule": "category=drain OR score<=19",
            "median_score": sorted(scores)[len(scores) // 2] if scores else 0,
            "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "category_counts": cats,
            "dropped": dropped,
        },
        "posts": kept,
    }

    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\nDone — {len(kept)} kept, {len(dropped)} dropped → {out_path}", file=sys.stderr)

    # ---- Media cleanup ----
    # Raw packs carry full-size media for EVERY collected post. After
    # scoring, many posts are dropped but their media still sits in
    # media/. Delete orphans (and optionally all videos) to shrink the pack.
    if not args.keep_media:
        referenced: set[str] = set()
        for post in kept:
            for p2 in (post.get("local_media_paths") or []):
                referenced.add(p2)
            qp = post.get("quoted_post")
            if isinstance(qp, dict):
                for p2 in (qp.get("local_media_paths") or []):
                    referenced.add(p2)

        media_dir = pack / "media"
        removed_count = 0
        removed_bytes = 0
        video_exts = {".mp4", ".mov", ".m4v", ".webm"}
        if media_dir.exists():
            for f in media_dir.rglob("*"):
                if not f.is_file():
                    continue
                rel = str(f.relative_to(pack))  # "media/<name>"
                is_video = f.suffix.lower() in video_exts
                keep_it = (rel in referenced) and not (args.drop_videos and is_video)
                if not keep_it:
                    try:
                        sz = f.stat().st_size
                        f.unlink()
                        removed_bytes += sz
                        removed_count += 1
                    except OSError:
                        pass
            # Also remove now-empty subdirectories under media/.
            for d in sorted((p for p in media_dir.rglob("*") if p.is_dir()),
                            key=lambda p: len(p.parts), reverse=True):
                try:
                    d.rmdir()
                except OSError:
                    pass

        if removed_count:
            mb = removed_bytes / 1024 / 1024
            note = " (including all videos)" if args.drop_videos else ""
            print(f"Trimmed {removed_count} orphan media file(s){note} — freed {mb:.1f} MB.",
                  file=sys.stderr)

            # Rewrite kept posts' local_media_paths to drop any path we deleted
            # (shouldn't happen for the "referenced" set, but --drop-videos
            # can delete referenced videos).
            if args.drop_videos:
                for post in kept:
                    post["local_media_paths"] = [
                        p2 for p2 in (post.get("local_media_paths") or [])
                        if (pack / p2).exists()
                    ]
                output["posts"] = kept
                out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
