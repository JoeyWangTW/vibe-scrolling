"""Scroll automation — timing, depth, stop conditions for X."""

import asyncio
import random
import time
from datetime import datetime

TWITTER_DATE_FORMAT = "%a %b %d %H:%M:%S %z %Y"


def _parse_x_date(date_str: str) -> datetime | None:
    try:
        return datetime.strptime(date_str, TWITTER_DATE_FORMAT)
    except (ValueError, TypeError):
        return None


def _has_post_older_than(posts, oldest_dt: datetime) -> bool:
    for post in posts:
        dt = _parse_x_date(post.created_at)
        if dt and dt.date() < oldest_dt.date():
            return True
    return False


async def scroll_feed(page, delay_min: float = 2.0, delay_max: float = 5.0):
    """Scroll to the bottom of loaded content to trigger infinite scroll loading."""
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    delay = random.uniform(delay_min, delay_max)
    await asyncio.sleep(delay)
    return delay


async def scroll_loop(
    page,
    interceptor,
    *,
    delay_min: float = 2.0,
    delay_max: float = 5.0,
    max_posts: int = 50,
    max_minutes: float | None = None,
    oldest_post_date: str | None = None,
    stale_limit: int = 3,
) -> dict:
    """Scroll the feed in a loop, collecting posts via the interceptor."""
    start = time.monotonic()
    scroll_count = 0
    stale_scrolls = 0

    oldest_dt = None
    if oldest_post_date:
        oldest_dt = datetime.fromisoformat(oldest_post_date)

    prev_count = len(interceptor.parse_all_posts(skip_ads=True))

    conditions = [f"max_posts={max_posts}"]
    if max_minutes is not None:
        conditions.append(f"max_minutes={max_minutes}")
    if oldest_post_date:
        conditions.append(f"oldest_post_date={oldest_post_date}")
    print(f"[scroller] Starting scroll loop ({', '.join(conditions)}, stale_limit={stale_limit})")
    print(f"[scroller] Posts from initial load: {prev_count}")

    while True:
        if max_minutes is not None:
            elapsed_min = (time.monotonic() - start) / 60
            if elapsed_min >= max_minutes:
                reason = f"Reached max_minutes limit ({max_minutes} min)"
                print(f"[scroller] Stopping: {reason}")
                return {"scroll_count": scroll_count, "total_posts": prev_count, "stop_reason": reason}

        if prev_count >= max_posts:
            reason = f"Reached max_posts limit ({max_posts})"
            print(f"[scroller] Stopping: {reason}")
            return {"scroll_count": scroll_count, "total_posts": prev_count, "stop_reason": reason}

        if oldest_dt is not None:
            posts = interceptor.parse_all_posts(skip_ads=True)
            if _has_post_older_than(posts, oldest_dt):
                reason = f"Found post older than {oldest_post_date}"
                print(f"[scroller] Stopping: {reason}")
                return {"scroll_count": scroll_count, "total_posts": len(posts), "stop_reason": reason}

        delay = await scroll_feed(page, delay_min, delay_max)
        scroll_count += 1

        await page.wait_for_timeout(3000)

        current_count = len(interceptor.parse_all_posts(skip_ads=True))
        new_posts = current_count - prev_count

        print(
            f"[scroller] Scroll #{scroll_count}: "
            f"+{new_posts} new posts | "
            f"total={current_count} | "
            f"delay={delay:.1f}s"
        )

        if new_posts == 0:
            stale_scrolls += 1
            print(f"[scroller] No new posts ({stale_scrolls}/{stale_limit} stale scrolls)")
            if stale_scrolls >= stale_limit:
                reason = f"No new posts after {stale_limit} consecutive scrolls"
                print(f"[scroller] Stopping: {reason}")
                return {"scroll_count": scroll_count, "total_posts": current_count, "stop_reason": reason}
        else:
            stale_scrolls = 0

        prev_count = current_count
