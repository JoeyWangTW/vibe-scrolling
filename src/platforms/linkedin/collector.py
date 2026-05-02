"""LinkedIn feed collector — orchestrates a collection run."""

import asyncio
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from playwright.async_api import async_playwright

from src.media_downloader import download_media
from src.platforms.linkedin.auth import load_session
from src.platforms.linkedin.interceptor import ResponseInterceptor
from src.storage import deduplicate_within_run, get_run_dir, save_posts, save_run_summary, set_run_dir


def print_summary(summary: dict):
    print("\n" + "=" * 50)
    print("  LinkedIn Collection Summary")
    print("=" * 50)
    print(f"  Total posts captured:   {summary['total_posts']}")
    print(f"  Unique posts:           {summary['unique_posts']}")
    print(f"  Media downloaded:       {summary['media_downloaded']}")
    print(f"  Media failures:         {summary['media_failed']}")
    print(f"  Scrolls performed:      {summary['scroll_count']}")
    print(f"  Voyager responses seen: {summary['api_responses_seen']}")
    print(f"  Total run time:         {summary['run_time_seconds']:.1f}s")
    print(f"  Stop reason:            {summary['stop_reason']}")
    if summary["warnings"]:
        for w in summary["warnings"]:
            print(f"    - {w}")
    print("=" * 50 + "\n")


async def run(config: dict) -> dict:
    """Run the LinkedIn feed collector."""
    output_dir = config.get("output_dir", "feed_data")
    platform_config = config.get("platforms", {}).get("linkedin", config)

    job_id = config.get("_job_id")
    run_dir = get_run_dir(output_dir, platform="linkedin", job_id=job_id)
    set_run_dir(run_dir)
    print(f"[linkedin] Run directory: {run_dir}")

    start_time = time.monotonic()
    warnings: list[str] = []

    interceptor = ResponseInterceptor(run_dir=run_dir)
    session_file = platform_config.get("session_file", None)

    async with async_playwright() as p:
        try:
            browser, context, page = await load_session(p, session_file=session_file)
        except (FileNotFoundError, RuntimeError) as e:
            print(f"[linkedin] {e}")
            return {"error": str(e)}

        page.on("response", interceptor.handle_response)
        print("[linkedin] Voyager API archiver attached.")

        # Make sure we're on /feed/ — load_session navigated there but we
        # reload to start with a clean DOM.
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        print("[linkedin] Feed loaded. Waiting for posts to render...")
        await page.wait_for_timeout(6000)

        # Voyager API responses are the source of truth — they arrive via the
        # response interceptor automatically. DOM extraction is only used if
        # the API path yields nothing (e.g. LinkedIn changed the API shape).
        initial_count = len(interceptor.parse_all_posts())
        print(f"[linkedin] Posts from initial load: {initial_count} (api updates seen={interceptor.api_updates_seen})")

        if initial_count == 0:
            await page.wait_for_timeout(5000)
            initial_count = len(interceptor.parse_all_posts())
            if initial_count == 0:
                print("[linkedin] No API posts captured — falling back to DOM extraction.")
                await interceptor.extract_from_page(page)
                initial_count = len(interceptor.parse_all_posts())
                if initial_count == 0:
                    warnings.append("No posts found in feed after extended wait")

        max_posts = platform_config.get("max_posts", 50)
        max_minutes = platform_config.get("max_minutes", 5)
        delay_min = platform_config.get("scroll_delay_min", 3)
        delay_max = platform_config.get("scroll_delay_max", 6)
        stale_limit = 3
        scroll_count = 0
        stale_scrolls = 0
        stop_reason = "Unknown"
        prev_count = initial_count

        while True:
            if prev_count >= max_posts:
                stop_reason = f"Reached max_posts limit ({max_posts})"
                break

            elapsed = (time.monotonic() - start_time) / 60
            if max_minutes and elapsed >= max_minutes:
                stop_reason = f"Reached max_minutes limit ({max_minutes} min)"
                break

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            delay = random.uniform(delay_min, delay_max)
            await asyncio.sleep(delay)
            scroll_count += 1
            await page.wait_for_timeout(3000)

            current_count = len(interceptor.parse_all_posts())
            new_posts = current_count - prev_count

            print(f"[linkedin] Scroll #{scroll_count}: +{new_posts} new posts | total={current_count} | delay={delay:.1f}s")

            if new_posts == 0:
                stale_scrolls += 1
                if stale_scrolls >= stale_limit:
                    stop_reason = f"No new posts after {stale_limit} consecutive scrolls"
                    break
            else:
                stale_scrolls = 0

            prev_count = current_count

        print(f"[linkedin] Stopping: {stop_reason}")

        posts = interceptor.parse_all_posts()
        duration = time.monotonic() - start_time

        if posts:
            unique_posts, _ = deduplicate_within_run(posts)
            downloaded, dl_failed = await download_media(unique_posts, output_dir)
            if dl_failed > 0:
                warnings.append(f"{dl_failed} media download(s) failed")
            save_posts(unique_posts, run_dir, platform="linkedin", duration_seconds=duration)
        else:
            unique_posts = []
            downloaded = 0
            dl_failed = 0
            warnings.append("No posts parsed from feed")

        duration = time.monotonic() - start_time

        summary = {
            "platform": "linkedin",
            "run_timestamp": datetime.now().isoformat(),
            "run_dir": str(run_dir),
            "total_posts": len(posts),
            "unique_posts": len(unique_posts),
            "media_downloaded": downloaded,
            "media_failed": dl_failed,
            "api_responses_seen": interceptor.api_responses_seen,
            "scroll_count": scroll_count,
            "run_time_seconds": round(duration, 2),
            "stop_reason": stop_reason,
            "warnings": warnings,
        }

        print_summary(summary)
        save_run_summary(summary, run_dir)
        await browser.close()

    return summary


async def main():
    config_path = Path("config.json")
    config = json.loads(config_path.read_text()) if config_path.exists() else {"output_dir": "feed_data"}
    await run(config)


if __name__ == "__main__":
    asyncio.run(main())
