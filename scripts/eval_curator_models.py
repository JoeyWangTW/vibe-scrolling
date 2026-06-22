#!/usr/bin/env python3
"""Eval: how well do Sonnet and Haiku do the curator's scoring job?

Scores the same N posts with claude-sonnet-4-6 and claude-haiku-4-5, then
shells out to claude-opus-4-7 as a judge. Opus sees the user's goals.md, the
post, and both candidate scorings — for each it returns:

    sonnet_quality: 0-10
    haiku_quality:  0-10
    sonnet_verdict: "good" | "ok" | "bad"
    haiku_verdict:  "good" | "ok" | "bad"
    notes:          short text on disagreement

Aggregate stats + per-post breakdown print at the end. Pack and post count
configurable via CLI flags.

Run:
    python3 scripts/eval_curator_models.py PACK_DIR --n 30
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Re-use the curator's scoring (prompt + CLI runner + extract_json_array).
SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "focus-lab-curator"
sys.path.insert(0, str(SKILL_DIR))
import curate as curator  # type: ignore  # noqa: E402

SONNET = "claude-sonnet-4-6"
HAIKU = "claude-haiku-4-5"
OPUS = "claude-opus-4-7"

JUDGE_PROMPT = """You are evaluating two AI models that scored social-media posts against a user's content preferences.

For each post, both models produced: a score (0-100), a category ({{goal | joy | adjacent | neutral | drain}}), and a filter_reason.

The drop rule applied downstream is: category="drain" OR score<=19 → dropped.

Your job per post: judge whether each model's decision makes sense given the user's goals. You don't need a single "correct" score — judge whether the score is in the right ballpark, the category is defensible, and the reason is grounded in the post + goals.

USER GOALS (goals.md):
<goals>
{goals}
</goals>

Return ONLY a JSON array (one element per post) with this schema:
[
  {{
    "id": "<post id>",
    "sonnet_quality": 0-10,
    "haiku_quality": 0-10,
    "sonnet_verdict": "good" | "ok" | "bad",
    "haiku_verdict": "good" | "ok" | "bad",
    "notes": "<1 short sentence — only fill if disagreement matters>"
  }}
]

Rubric:
- 9-10: spot-on score AND category AND reason; matches goals tightly
- 6-8: defensible — slightly off score or weakly-justified category, but not wrong
- 3-5: questionable — score is in the wrong band OR category is misapplied
- 0-2: clearly wrong — drops something obviously aligned with goals, or keeps obvious drain

Verdict shortcut:
- "good"  = quality 8-10
- "ok"    = quality 4-7
- "bad"   = quality 0-3

POSTS WITH BOTH SCORINGS:
{rows}
"""


def _format_post_for_judge(post: dict, sonnet: dict, haiku: dict) -> str:
    """Compact text block per post for the judge prompt."""
    text = (post.get("text") or "")[:500]
    author = post.get("author_handle") or post.get("author_name") or "?"
    return (
        f"---\n"
        f"id: {post.get('id')}\n"
        f"author: @{author}\n"
        f"text: {text}\n"
        f"sonnet: score={sonnet.get('score')} cat={sonnet.get('category')} reason={sonnet.get('filter_reason','')[:200]}\n"
        f"haiku:  score={haiku.get('score')} cat={haiku.get('category')} reason={haiku.get('filter_reason','')[:200]}\n"
    )


def _score_with(model: str, batches: list[list[dict]], goals: str, runner) -> list[dict]:
    """Score every batch with `model` in parallel; return flattened scoring list."""
    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(curator.score_batch, b, goals, runner, model) for b in batches]
        for f in futs:
            try:
                out.extend(f.result())
            except Exception as e:
                print(f"  !! batch failed for {model}: {e}", file=sys.stderr)
    return out


def _index_by_id(scored: list[dict]) -> dict[str, dict]:
    return {str(s.get("id", "")): s for s in scored if s.get("id")}


def main() -> int:
    p = argparse.ArgumentParser(description="Eval Sonnet vs Haiku on the curator's scoring job, judged by Opus.")
    p.add_argument("pack", help="pack directory with posts.json + goals.md")
    p.add_argument("--n", type=int, default=30, help="how many posts to sample (default 30)")
    p.add_argument("--batch", type=int, default=10, help="batch size for scoring (default 10)")
    p.add_argument("--out", default=None, help="write the per-post judge output to this JSON file")
    args = p.parse_args()

    pack = Path(args.pack).resolve()
    posts_path = pack / "posts.json"
    goals_path = pack / "goals.md"
    if not posts_path.exists():
        sys.exit(f"error: {posts_path} not found")

    goals = goals_path.read_text() if goals_path.exists() else "(no goals.md — score charitably)"
    posts = (json.loads(posts_path.read_text()).get("posts") or [])[:args.n]
    if not posts:
        sys.exit("error: no posts to evaluate")

    cli_name, runner = curator.detect_cli(None)
    if cli_name != "claude":
        sys.exit(f"error: this eval requires the `claude` CLI (found {cli_name}).")

    batches = [posts[i:i + args.batch] for i in range(0, len(posts), args.batch)]
    print(f"Eval: {len(posts)} posts in {len(batches)} batch(es) of {args.batch}", file=sys.stderr)

    # Score with Sonnet and Haiku in parallel — they're independent.
    print(f"Scoring with {SONNET} and {HAIKU} in parallel...", file=sys.stderr)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=2) as pool:
        sonnet_fut = pool.submit(_score_with, SONNET, batches, goals, runner)
        haiku_fut = pool.submit(_score_with, HAIKU, batches, goals, runner)
        sonnet_scored = sonnet_fut.result()
        haiku_scored = haiku_fut.result()
    print(f"  scoring took {time.time() - t0:.1f}s", file=sys.stderr)

    sonnet_by_id = _index_by_id(sonnet_scored)
    haiku_by_id = _index_by_id(haiku_scored)

    rows = []
    for post in posts:
        pid = str(post.get("id", ""))
        s = sonnet_by_id.get(pid)
        h = haiku_by_id.get(pid)
        if not s or not h:
            continue  # one of the models didn't return this id
        rows.append(_format_post_for_judge(post, s, h))

    if not rows:
        sys.exit("error: no posts had matching scorings from both models")

    # Judge with Opus.
    print(f"Judging with {OPUS}...", file=sys.stderr)
    t0 = time.time()
    judge_input = JUDGE_PROMPT.format(goals=goals, rows="\n".join(rows))
    raw = curator._run_claude(judge_input, OPUS)
    print(f"  judging took {time.time() - t0:.1f}s", file=sys.stderr)

    try:
        verdicts = curator.extract_json_array(raw)
    except Exception as e:
        print(f"\nJudge response could not be parsed as JSON: {e}\n--- raw ---\n{raw}", file=sys.stderr)
        return 1

    # Aggregate.
    sq = [int(v.get("sonnet_quality", 0)) for v in verdicts if isinstance(v.get("sonnet_quality"), (int, float))]
    hq = [int(v.get("haiku_quality", 0)) for v in verdicts if isinstance(v.get("haiku_quality"), (int, float))]
    sv = [v.get("sonnet_verdict", "") for v in verdicts]
    hv = [v.get("haiku_verdict", "") for v in verdicts]

    def stats(xs):
        if not xs:
            return "no data"
        return f"avg={statistics.mean(xs):.2f}  median={statistics.median(xs)}  min={min(xs)}  max={max(xs)}"

    def vcounts(vs):
        c = {"good": 0, "ok": 0, "bad": 0}
        for v in vs:
            if v in c:
                c[v] += 1
        return c

    print()
    print(f"Sonnet quality: {stats(sq)}")
    print(f"  verdicts:    {vcounts(sv)}")
    print(f"Haiku  quality: {stats(hq)}")
    print(f"  verdicts:    {vcounts(hv)}")
    print()

    # Per-post disagreements (where one is "bad" or quality differs by >=3).
    diffs = []
    for v in verdicts:
        sqi = v.get("sonnet_quality", 0)
        hqi = v.get("haiku_quality", 0)
        if not isinstance(sqi, (int, float)) or not isinstance(hqi, (int, float)):
            continue
        if abs(sqi - hqi) >= 3 or v.get("sonnet_verdict") == "bad" or v.get("haiku_verdict") == "bad":
            diffs.append(v)
    if diffs:
        print(f"Notable disagreements ({len(diffs)}):")
        for v in diffs[:20]:
            print(f"  id={v.get('id')}  s={v.get('sonnet_quality')}/{v.get('sonnet_verdict')}  "
                  f"h={v.get('haiku_quality')}/{v.get('haiku_verdict')}  — {v.get('notes', '')}")

    if args.out:
        Path(args.out).write_text(json.dumps({
            "pack": str(pack),
            "n_posts": len(posts),
            "sonnet_quality": sq,
            "haiku_quality": hq,
            "verdicts": verdicts,
        }, indent=2))
        print(f"\nWrote per-post judgments to {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
