"""Reply capture — opens tweet detail pages in parallel tabs to collect replies."""

import asyncio
import re
from dataclasses import asdict, dataclass


@dataclass
class Reply:
    id: str
    text: str
    author_handle: str
    author_name: str
    created_at: str
    likes: int = 0
    retweets: int = 0
    replies: int = 0


TWEET_DETAIL_PATTERN = re.compile(r"/i/api/graphql/.*/TweetDetail")


def _extract_author(core: dict) -> tuple[str, str]:
    try:
        user_result = core.get("user_results", {}).get("result", {})
        user_core = user_result.get("core", {})
        screen_name = user_core.get("screen_name", "")
        name = user_core.get("name", "")
        if not screen_name:
            user_legacy = user_result.get("legacy", {})
            screen_name = user_legacy.get("screen_name", "")
            name = name or user_legacy.get("name", "")
        return (screen_name, name)
    except (AttributeError, TypeError):
        return ("", "")


def _parse_replies_from_detail(body: dict, parent_tweet_id: str) -> list[Reply]:
    replies = []
    try:
        instructions = (
            body.get("data", {})
            .get("threaded_conversation_with_injections_v2", {})
            .get("instructions", [])
        )
        for instruction in instructions:
            for entry in instruction.get("entries", []):
                entry_id = entry.get("entryId", "")

                if entry_id.startswith("cursor-") or entry_id.startswith("tweet-"):
                    continue

                content = entry.get("content", {})
                items = content.get("items", [])
                for item in items:
                    item_content = item.get("item", {}).get("itemContent", {})
                    if item_content.get("itemType") != "TimelineTweet":
                        continue

                    result = item_content.get("tweet_results", {}).get("result", {})
                    if not result:
                        continue

                    if result.get("__typename") == "TweetWithVisibilityResults":
                        result = result.get("tweet", {})
                        if not result:
                            continue

                    tweet_id = result.get("rest_id", "")
                    if not tweet_id or tweet_id == parent_tweet_id:
                        continue

                    legacy = result.get("legacy", {})
                    core = result.get("core", {})
                    handle, name = _extract_author(core)

                    replies.append(Reply(
                        id=tweet_id,
                        text=legacy.get("full_text", ""),
                        author_handle=handle,
                        author_name=name,
                        created_at=legacy.get("created_at", ""),
                        likes=legacy.get("favorite_count", 0),
                        retweets=legacy.get("retweet_count", 0),
                        replies=legacy.get("reply_count", 0),
                    ))
    except Exception as e:
        print(f"[replies] Error parsing TweetDetail: {e}")

    return replies


async def _fetch_replies_for_tweet(
    context, tweet_id: str, tweet_url: str, max_replies: int = 5
) -> list[Reply]:
    captured_body = None

    async def intercept_detail(response):
        nonlocal captured_body
        if TWEET_DETAIL_PATTERN.search(response.url):
            try:
                captured_body = await response.json()
            except Exception:
                pass

    page = await context.new_page()
    page.on("response", intercept_detail)

    try:
        await page.goto(tweet_url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(4000)

        if captured_body:
            replies = _parse_replies_from_detail(captured_body, tweet_id)
            return replies[:max_replies]
        return []
    except Exception as e:
        print(f"[replies] Error fetching {tweet_url}: {e}")
        return []
    finally:
        await page.close()


async def fetch_replies(
    context,
    tweets: list[dict],
    max_replies_per_tweet: int = 5,
    batch_size: int = 4,
) -> dict[str, list[Reply]]:
    all_replies: dict[str, list[Reply]] = {}

    tweet_tasks = []
    for t in tweets:
        handle = t.get("author_handle", "")
        tid = t.get("id", "")
        if handle and tid:
            url = f"https://x.com/{handle}/status/{tid}"
            tweet_tasks.append((tid, url))

    total = len(tweet_tasks)
    print(f"[replies] Fetching replies for {total} tweets (batch_size={batch_size})")

    for i in range(0, total, batch_size):
        batch = tweet_tasks[i:i + batch_size]
        batch_num = i // batch_size + 1
        print(f"[replies] Batch {batch_num}: opening {len(batch)} tabs...")

        tasks = [
            _fetch_replies_for_tweet(context, tid, url, max_replies_per_tweet)
            for tid, url in batch
        ]
        results = await asyncio.gather(*tasks)

        for (tid, url), replies in zip(batch, results):
            all_replies[tid] = replies
            if replies:
                print(f"[replies]   @.../{tid}: {len(replies)} replies captured")

    total_replies = sum(len(r) for r in all_replies.values())
    tweets_with_replies = sum(1 for r in all_replies.values() if r)
    print(f"[replies] Done: {total_replies} replies from {tweets_with_replies}/{total} tweets")

    return all_replies
