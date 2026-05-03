"""GraphQL response interception and parsing for X."""

import json
import re
from datetime import datetime
from pathlib import Path

from src.models import Post


class ResponseInterceptor:
    """Intercepts and stores X GraphQL API responses."""

    GRAPHQL_PATTERN = re.compile(r"/i/api/graphql/.*/Home")

    def __init__(self, run_dir: Path):
        self.responses: list[dict] = []
        self.run_dir = run_dir

    async def handle_response(self, response):
        """Callback for page.on('response') — captures matching GraphQL responses."""
        if not self.GRAPHQL_PATTERN.search(response.url):
            return

        try:
            body = await response.json()
            endpoint = response.url.split("/")[-1].split("?")[0]
            status = response.status

            self.responses.append(body)

            raw_dir = self.run_dir / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%H%M%S_%f")
            raw_path = raw_dir / f"{endpoint}_{timestamp}.json"
            raw_path.write_text(json.dumps(body, indent=2))

            tweet_count = self._count_entries(body)
            size = len(json.dumps(body))

            print(
                f"[interceptor] {endpoint} | status={status} | "
                f"size={size:,}B | entries={tweet_count}"
            )

        except Exception as e:
            print(f"[interceptor] Error processing response: {e}")

    def _count_entries(self, body: dict) -> int:
        try:
            instructions = (
                body.get("data", {})
                .get("home", {})
                .get("home_timeline_urt", {})
                .get("instructions", [])
            )
            count = 0
            for instruction in instructions:
                entries = instruction.get("entries", [])
                count += len(entries)
            return count
        except (AttributeError, TypeError):
            return 0

    def parse_all_posts(self, skip_ads: bool = True) -> list[Post]:
        """Parse all captured responses into Post objects."""
        posts_by_id: dict[str, Post] = {}
        ads_skipped = 0
        dupes_within_run = 0

        for response_body in self.responses:
            entries = self._extract_entries(response_body)
            for entry in entries:
                post = self._parse_entry(entry)
                if post is None:
                    continue
                if skip_ads and post.is_ad:
                    ads_skipped += 1
                    continue
                if post.id in posts_by_id:
                    dupes_within_run += 1
                else:
                    posts_by_id[post.id] = post

        posts = list(posts_by_id.values())
        print(
            f"[parser] Parsed {len(posts)} unique tweets "
            f"({dupes_within_run} within-run duplicates, "
            f"{ads_skipped} ads skipped) from {len(self.responses)} response(s)"
        )
        return posts

    # Keep backward-compatible alias
    def parse_all_tweets(self, skip_ads: bool = True) -> list[Post]:
        return self.parse_all_posts(skip_ads=skip_ads)

    def _extract_entries(self, body: dict) -> list[dict]:
        try:
            instructions = (
                body.get("data", {})
                .get("home", {})
                .get("home_timeline_urt", {})
                .get("instructions", [])
            )
            entries = []
            for instruction in instructions:
                entries.extend(instruction.get("entries", []))
            return entries
        except (AttributeError, TypeError):
            return []

    def _parse_entry(self, entry: dict) -> Post | None:
        try:
            content = entry.get("content", {})
            item_content = content.get("itemContent", {})

            item_type = item_content.get("itemType", "")
            if item_type != "TimelineTweet":
                return None

            is_ad = "promotedMetadata" in item_content

            tweet_result = item_content.get("tweet_results", {}).get("result", {})
            if not tweet_result:
                return None

            return self._parse_tweet_result(tweet_result, is_ad=is_ad)

        except Exception as e:
            entry_id = entry.get("entryId", "unknown")
            print(f"[parser] Skipping entry {entry_id}: {e}")
            return None

    def _parse_tweet_result(self, result: dict, is_ad: bool = False) -> Post | None:
        try:
            typename = result.get("__typename", "")
            if typename == "TweetWithVisibilityResults":
                result = result.get("tweet", {})
                if not result:
                    return None

            tweet_id = result.get("rest_id", "")
            if not tweet_id:
                return None

            legacy = result.get("legacy", {})
            core = result.get("core", {})

            author_handle, author_name = self._extract_author(core)

            is_repost = False
            original_author = None
            quoted_post = None

            # Retweet: show original content, note who retweeted
            rt_result = legacy.get("retweeted_status_result", {}).get("result", {})
            if rt_result:
                is_repost = True
                # original_author = the person who retweeted (the feed owner's timeline shows their RT)
                original_author = author_handle
                inner = self._parse_tweet_result(rt_result)
                if inner:
                    return Post(
                        id=tweet_id,
                        platform="x",
                        text=inner.text,
                        author_handle=inner.author_handle,
                        author_name=inner.author_name,
                        created_at=inner.created_at,
                        url=f"https://x.com/{inner.author_handle}/status/{inner.id}",
                        likes=inner.likes,
                        reposts=inner.reposts,
                        replies=inner.replies,
                        quotes=inner.quotes,
                        media_urls=inner.media_urls,
                        video_urls=inner.video_urls,
                        is_repost=True,
                        original_author=original_author,
                        quoted_post=inner.quoted_post,
                        is_ad=is_ad,
                    )

            # Prefer note_tweet for long-form posts (>280 chars)
            note_tweet = result.get("note_tweet", {}).get("note_tweet_results", {}).get("result", {})
            text = note_tweet.get("text") or legacy.get("full_text", "")
            created_at = legacy.get("created_at", "")
            likes = legacy.get("favorite_count", 0)
            retweets_count = legacy.get("retweet_count", 0)
            replies_count = legacy.get("reply_count", 0)
            quotes_count = legacy.get("quote_count", 0)

            media_urls, video_urls = self._extract_media_urls(legacy)

            # Quote tweet: capture the quoted post content
            if legacy.get("is_quote_status"):
                qt_result = result.get("quoted_status_result", {}).get("result", {})
                if qt_result:
                    qt_post = self._parse_tweet_result(qt_result)
                    if qt_post:
                        from dataclasses import asdict
                        quoted_post = {
                            "id": qt_post.id,
                            "text": qt_post.text,
                            "author_handle": qt_post.author_handle,
                            "author_name": qt_post.author_name,
                            "created_at": qt_post.created_at,
                            "url": qt_post.url,
                            "likes": qt_post.likes,
                            "media_urls": qt_post.media_urls,
                            "video_urls": qt_post.video_urls,
                        }

            return Post(
                id=tweet_id,
                platform="x",
                text=text,
                author_handle=author_handle,
                author_name=author_name,
                created_at=created_at,
                url=f"https://x.com/{author_handle}/status/{tweet_id}",
                likes=likes,
                reposts=retweets_count,
                replies=replies_count,
                quotes=quotes_count,
                media_urls=media_urls,
                video_urls=video_urls,
                is_repost=is_repost,
                original_author=original_author,
                quoted_post=quoted_post,
                is_ad=is_ad,
            )

        except Exception as e:
            print(f"[parser] Error parsing tweet result: {e}")
            return None

    def _extract_author(self, core: dict) -> tuple[str, str]:
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

    def _extract_media_urls(self, legacy: dict) -> tuple[list[str], list[str]]:
        image_urls = []
        video_urls = []
        media_source = legacy.get("extended_entities", legacy.get("entities", {}))
        media_items = media_source.get("media", [])

        for item in media_items:
            media_type = item.get("type", "")
            if media_type in ("video", "animated_gif"):
                variants = item.get("video_info", {}).get("variants", [])
                mp4s = [v for v in variants if v.get("content_type") == "video/mp4"]
                if mp4s:
                    best = max(mp4s, key=lambda v: v.get("bitrate", 0))
                    video_urls.append(best["url"])
            else:
                url = item.get("media_url_https", "")
                if url:
                    image_urls.append(url)

        return image_urls, video_urls
