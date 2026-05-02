"""LinkedIn feed extraction.

Primary path: parse LinkedIn's Voyager GraphQL/REST responses. Each response
ships a flat `included` array with typed entities (Update, Profile,
SocialActivityCounts, VideoPlayMetadata, ...) keyed by `entityUrn`. We index
that array and walk every Update record, resolving `*foo` URN references
back to the real entity.

DOM extraction is kept as a fallback in case LinkedIn ever swaps the API.
"""

import json
import re
from datetime import datetime
from pathlib import Path

from src.models import Post


ACTIVITY_RE = re.compile(r"urn:li:activity:(\d+)")


# Fallback DOM walker — only used if the Voyager API parser yields nothing.
EXTRACT_JS = r"""
() => {
  const out = [];
  const cards = document.querySelectorAll(
    'div.feed-shared-update-v2[data-urn], div[data-id^="urn:li:activity:"]'
  );
  for (const card of cards) {
    const urn = card.getAttribute('data-urn') || card.getAttribute('data-id') || '';
    if (!urn) continue;
    const text = (
      card.querySelector('.update-components-text') ||
      card.querySelector('.feed-shared-inline-show-more-text')
    )?.innerText?.trim() || '';
    const actor = card.querySelector('.update-components-actor');
    const authorName =
      actor?.querySelector('.update-components-actor__title span[aria-hidden="true"]')?.innerText?.trim() ||
      actor?.querySelector('.update-components-actor__title')?.innerText?.trim() || '';
    const m = urn.match(/urn:li:activity:(\d+)/);
    out.push({
      id: urn,
      activity_id: m ? m[1] : '',
      text,
      author_name: authorName,
      url: m ? `https://www.linkedin.com/feed/update/urn:li:activity:${m[1]}/` : '',
    });
  }
  return out;
}
"""


class ResponseInterceptor:
    """Parses LinkedIn Voyager API responses; falls back to DOM if needed."""

    VOYAGER_PATTERN = re.compile(r"linkedin\.com/voyager/api/.*(feed|updates|graphql)", re.I)

    def __init__(self, run_dir: Path):
        self.posts_by_id: dict[str, Post] = {}
        self.run_dir = run_dir
        self.api_responses_seen = 0
        self.api_updates_seen = 0

    # ------------------------------------------------------------------ API
    async def handle_response(self, response):
        if not self.VOYAGER_PATTERN.search(response.url):
            return
        if response.status != 200:
            return

        try:
            body = await response.json()
        except Exception:
            return
        if not isinstance(body, dict) or "included" not in body:
            return

        self.api_responses_seen += 1
        self._archive_raw(body)

        try:
            new_count = self._ingest_voyager(body)
            if new_count:
                print(f"[interceptor:linkedin] Voyager: +{new_count} posts (total={len(self.posts_by_id)})")
        except Exception as e:
            print(f"[interceptor:linkedin] Voyager parse error: {e}")

    def _archive_raw(self, body: dict):
        raw_dir = self.run_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%H%M%S_%f")
        raw_path = raw_dir / f"voyager_{timestamp}.json"
        try:
            raw_path.write_text(json.dumps(body, indent=2))
        except Exception:
            pass

    def _ingest_voyager(self, body: dict) -> int:
        included = body.get("included") or []
        # Index every entity by entityUrn for cheap dereferencing.
        store: dict[str, dict] = {}
        for ent in included:
            urn = ent.get("entityUrn")
            if urn:
                store[urn] = ent

        new_count = 0
        for ent in included:
            if ent.get("$type") != "com.linkedin.voyager.dash.feed.Update":
                continue
            self.api_updates_seen += 1
            post = self._build_post_from_update(ent, store)
            if post and post.id not in self.posts_by_id:
                self.posts_by_id[post.id] = post
                new_count += 1
        return new_count

    # ---------------------------------------------------------------- build
    def _build_post_from_update(self, update: dict, store: dict) -> Post | None:
        try:
            entity_urn = update.get("entityUrn", "") or ""
            m = ACTIVITY_RE.search(entity_urn)
            if not m:
                return None
            activity_id = m.group(1)
            post_urn = f"urn:li:activity:{activity_id}"

            actor = update.get("actor") or {}
            author_name = self._text(actor.get("name"))
            author_headline = self._text(actor.get("description"))
            time_text = (self._text(actor.get("subDescription")) or "").strip(" •·\n\t")

            handle = ""
            actor_url = ""
            nav = actor.get("navigationContext") or {}
            target = nav.get("actionTarget") or ""
            if "/in/" in target:
                handle = target.split("/in/")[-1].split("?")[0].split("/")[0].strip("/")
                actor_url = f"https://www.linkedin.com/in/{handle}"
            elif "/company/" in target:
                handle = target.split("/company/")[-1].split("?")[0].split("/")[0].strip("/")
                actor_url = f"https://www.linkedin.com/company/{handle}"
            elif "/school/" in target:
                handle = target.split("/school/")[-1].split("?")[0].split("/")[0].strip("/")
                actor_url = f"https://www.linkedin.com/school/{handle}"

            commentary = update.get("commentary") or {}
            text = self._text(commentary)

            # Counts via *socialDetail -> SocialDetail -> *totalSocialActivityCounts -> SocialActivityCounts
            likes = comments = shares = 0
            sd_ref = update.get("*socialDetail")
            sd = store.get(sd_ref) if sd_ref else None
            if sd:
                sac_ref = sd.get("*totalSocialActivityCounts")
                sac = store.get(sac_ref) if sac_ref else None
                if sac:
                    likes = sac.get("numLikes", 0) or 0
                    comments = sac.get("numComments", 0) or 0
                    shares = sac.get("numShares", 0) or 0

            content = update.get("content") or {}
            image_urls, video_urls = self._extract_media(content, store)

            # Repost
            is_repost = False
            quoted_post = None
            reshared_ref = update.get("*resharedUpdate")
            reshared = store.get(reshared_ref) if reshared_ref else None
            if reshared is None:
                reshared = update.get("resharedUpdate")  # sometimes inline
            if reshared:
                is_repost = True
                r_actor = reshared.get("actor") or {}
                r_text = self._text(reshared.get("commentary"))
                r_imgs, r_vids = self._extract_media(reshared.get("content") or {}, store)
                r_entity_urn = reshared.get("entityUrn", "")
                rm = ACTIVITY_RE.search(r_entity_urn or "")
                r_activity = rm.group(1) if rm else ""
                quoted_post = {
                    "id": f"urn:li:activity:{r_activity}" if r_activity else "",
                    "text": r_text,
                    "author_name": self._text(r_actor.get("name")),
                    "author_handle": "",
                    "url": f"https://www.linkedin.com/feed/update/urn:li:activity:{r_activity}/" if r_activity else "",
                    "media_urls": r_imgs,
                    "video_urls": r_vids,
                }

            # Promoted/sponsored detection — header text or actor.supplementaryActorInfo
            is_ad = False
            header = update.get("header") or {}
            header_text = self._text(header) or ""
            if re.search(r"promoted|sponsored", header_text, re.I):
                is_ad = True
            sup = self._text(actor.get("supplementaryActorInfo")) or ""
            if re.search(r"promoted|sponsored", sup, re.I):
                is_ad = True

            return Post(
                id=post_urn,
                platform="linkedin",
                text=text,
                author_handle=handle or author_name,
                author_name=author_name,
                created_at=time_text,
                url=f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}/",
                likes=likes,
                reposts=shares,
                replies=comments,
                quotes=0,
                media_urls=image_urls,
                video_urls=video_urls,
                is_repost=is_repost,
                quoted_post=quoted_post,
                is_ad=is_ad,
                platform_data={
                    "activity_id": activity_id,
                    "author_headline": author_headline,
                    "author_url": actor_url,
                    "time_text": time_text,
                },
            )
        except Exception as e:
            print(f"[parser:linkedin] build_post error ({type(e).__name__}): {e!r}")
            return None

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _text(node) -> str:
        """Extract `.text` from a TextViewModel-like dict."""
        if not node or not isinstance(node, dict):
            return ""
        # Direct text field
        if isinstance(node.get("text"), str):
            return node["text"]
        # Nested TextViewModel: {text: {text: "..."}}
        inner = node.get("text")
        if isinstance(inner, dict) and isinstance(inner.get("text"), str):
            return inner["text"]
        return ""

    def _extract_media(self, content: dict, store: dict) -> tuple[list[str], list[str]]:
        images: list[str] = []
        videos: list[str] = []

        # Single/multi image component
        ic = content.get("imageComponent")
        if ic:
            for img in (ic.get("images") or []):
                url = self._image_url_from_attribute(img)
                if url:
                    images.append(url)

        # Article preview thumbnail
        ac = content.get("articleComponent")
        if ac:
            for key in ("largeImage", "smallImage", "image"):
                obj = ac.get(key)
                if isinstance(obj, dict):
                    url = self._image_url_from_attribute(obj)
                    if url:
                        images.append(url)

        # Native LinkedIn video
        lvc = content.get("linkedInVideoComponent")
        if lvc:
            video_url, thumb = self._video_from_metadata(lvc.get("*videoPlayMetadata"), store)
            if video_url:
                videos.append(video_url)
            if thumb:
                images.append(thumb)

        # External video (YouTube, Vimeo embeds) -> capture thumbnail only
        evc = content.get("externalVideoComponent")
        if evc:
            obj = evc.get("thumbnail") or evc.get("image")
            if isinstance(obj, dict):
                url = self._image_url_from_attribute(obj)
                if url:
                    images.append(url)

        # Document (PDF / slides) — first page thumbnail. coverPages can be
        # either a list of pages or a dict keyed by page number depending on
        # the document type.
        dc = content.get("documentComponent")
        if dc:
            doc = dc.get("document") or {}
            cover = doc.get("coverPages")
            page_objs = []
            if isinstance(cover, list):
                page_objs = cover
            elif isinstance(cover, dict):
                page_objs = list(cover.values())
            if page_objs and isinstance(page_objs[0], dict):
                url = self._image_url_from_attribute(page_objs[0])
                if url:
                    images.append(url)

        return images, videos

    def _image_url_from_attribute(self, image_obj: dict) -> str:
        """Walk the LinkedIn image attribute structure to a CDN URL.

        Image objects nest as: {attributes:[{detailData:{vectorImage:{rootUrl,artifacts:[...]}}}]}
        Sometimes the vectorImage hangs directly off the object. Pick the
        largest artifact and concat rootUrl + fileIdentifyingUrlPathSegment.
        """
        if not isinstance(image_obj, dict):
            return ""

        # Find a vectorImage anywhere in the attribute tree.
        vector = self._find_vector_image(image_obj)
        if not vector:
            return ""

        root = vector.get("rootUrl") or ""
        artifacts = vector.get("artifacts") or []
        if not artifacts:
            return ""

        best = max(artifacts, key=lambda a: (a.get("width", 0) or 0) * (a.get("height", 0) or 0))
        seg = best.get("fileIdentifyingUrlPathSegment") or ""
        if not seg:
            return ""
        return f"{root}{seg}" if root else seg

    def _find_vector_image(self, obj) -> dict | None:
        if not isinstance(obj, dict):
            return None
        if obj.get("$type") == "com.linkedin.common.VectorImage":
            return obj
        if "rootUrl" in obj and "artifacts" in obj:
            return obj
        # Common LinkedIn attribute shape
        for attr in obj.get("attributes") or []:
            dd = attr.get("detailData") or {}
            for k in ("vectorImage", "nonEntityProfilePicture", "nonEntityCompanyLogo"):
                v = dd.get(k)
                if isinstance(v, dict):
                    if v.get("rootUrl") and v.get("artifacts"):
                        return v
                    nested = v.get("vectorImage")
                    if isinstance(nested, dict) and nested.get("rootUrl"):
                        return nested
        # Direct vectorImage child
        v = obj.get("vectorImage")
        if isinstance(v, dict) and v.get("rootUrl"):
            return v
        return None

    def _video_from_metadata(self, ref, store: dict) -> tuple[str, str]:
        """Resolve *videoPlayMetadata URN -> highest-quality progressive stream URL + thumbnail."""
        if not ref:
            return "", ""
        # VideoPlayMetadata is usually keyed by digitalmediaAsset URN
        meta = store.get(ref)
        if not meta:
            # Some payloads key it by entityUrn that includes videoPlayMetadata prefix
            for k, v in store.items():
                if k.endswith(ref.split(":")[-1]) and v.get("$type") == "com.linkedin.videocontent.VideoPlayMetadata":
                    meta = v
                    break
        if not meta:
            return "", ""

        streams = meta.get("progressiveStreams") or []
        best_url = ""
        best_pixels = -1
        for s in streams:
            pixels = (s.get("width", 0) or 0) * (s.get("height", 0) or 0)
            for loc in s.get("streamingLocations") or []:
                url = loc.get("url") or ""
                if url and pixels > best_pixels:
                    best_pixels = pixels
                    best_url = url

        thumb = ""
        thumb_obj = meta.get("thumbnail")
        if isinstance(thumb_obj, dict):
            thumb = self._image_url_from_attribute(thumb_obj) or thumb_obj.get("url", "")
        return best_url, thumb

    # ----------------------------------------------------------- DOM fallback
    async def extract_from_page(self, page):
        """Fallback DOM walker. Only adds posts the API hasn't already given us."""
        try:
            items = await page.evaluate(EXTRACT_JS)
        except Exception as e:
            print(f"[interceptor:linkedin] DOM eval failed: {e}")
            return 0

        added = 0
        for item in items or []:
            urn = item.get("id") or ""
            if not urn or urn in self.posts_by_id:
                continue
            self.posts_by_id[urn] = Post(
                id=urn,
                platform="linkedin",
                text=item.get("text", ""),
                author_handle=item.get("author_name", ""),
                author_name=item.get("author_name", ""),
                created_at="",
                url=item.get("url", ""),
                platform_data={"activity_id": item.get("activity_id", ""), "source": "dom_fallback"},
            )
            added += 1
        if added:
            print(f"[interceptor:linkedin] DOM fallback: +{added} posts")
        return added

    def parse_all_posts(self, skip_ads: bool = True) -> list[Post]:
        posts = [p for p in self.posts_by_id.values() if not (skip_ads and p.is_ad)]
        print(f"[parser:linkedin] {len(posts)} unique posts (api updates seen={self.api_updates_seen})")
        return posts
