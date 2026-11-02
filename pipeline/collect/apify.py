"""X and LinkedIn via Apify actors. Actor ids and field names differ between actors,
so both are configurable and the field mapping is defensive."""
from __future__ import annotations

import os
import re
from datetime import datetime

import requests

from ..schema import Post

APIFY = "https://api.apify.com/v2"

# Defaults can be overridden in the environment; see README "Collectors".
X_ACTOR = os.environ.get("APIFY_X_ACTOR", "apidojo~tweet-scraper")
LI_ACTOR = os.environ.get("APIFY_LINKEDIN_ACTOR", "harvestapi~linkedin-company-posts")


def _run(actor: str, payload: dict, timeout: int = 300) -> list[dict]:
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise RuntimeError("APIFY_TOKEN is not set")
    r = requests.post(f"{APIFY}/acts/{actor}/run-sync-get-dataset-items",
                      params={"token": token, "timeout": timeout, "format": "json"},
                      json=payload, timeout=timeout + 30)
    r.raise_for_status()
    return r.json()


def _first(d: dict, *keys, default=None):
    for k in keys:
        cur = d
        ok = True
        for part in k.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return default


def _date(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return datetime.utcfromtimestamp(v / 1000 if v > 1e11 else v).date().isoformat()
    s = str(v)
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.match(r"\d{4}-\d{2}-\d{2}", s)
    return m.group(0) if m else ""


def collect_x(handle: str, account: str, max_items: int = 60) -> list[Post]:
    items = _run(X_ACTOR, {"twitterHandles": [handle.lstrip("@")], "maxItems": max_items,
                           "sort": "Latest", "includeSearchTerms": False})
    posts = []
    for it in items:
        text = _first(it, "text", "full_text", "fullText", default="")
        url = _first(it, "url", "twitterUrl", default="")
        is_reply = bool(_first(it, "isReply", "in_reply_to_status_id", default=False))
        is_rt = bool(_first(it, "isRetweet", "retweeted_status", default=False))
        is_quote = bool(_first(it, "isQuote", "quoted_status", default=False))
        media = _first(it, "media", "extendedEntities.media", default=[]) or []
        fmt = "repost" if is_rt else "quote" if is_quote else \
              "video" if any("video" in str(m.get("type", "")) for m in media if isinstance(m, dict)) else \
              "image" if media else "link" if "http" in text else "text"
        posts.append(Post(channel="x", account=account, date=_date(_first(it, "createdAt", "created_at")),
                          url=url, text=text, format=fmt,
                          likes=_first(it, "likeCount", "favorite_count", default=0),
                          reposts=_first(it, "retweetCount", "retweet_count", default=0),
                          comments=_first(it, "replyCount", "reply_count", default=0),
                          views=_first(it, "viewCount", "views_count", default=None),
                          is_reply=is_reply or is_rt, collected_via="apify_x"))
    posts = [p for p in posts if p.date]
    if not posts:
        raise RuntimeError(f"Apify X actor {X_ACTOR} returned no usable posts for @{handle}")
    return posts


def collect_linkedin(company_url: str, account: str, max_items: int = 40) -> list[Post]:
    items = _run(LI_ACTOR, {"companies": [company_url], "maxPosts": max_items})
    posts = []
    for it in items:
        text = _first(it, "text", "content", "commentary", default="")
        url = _first(it, "url", "postUrl", "linkedinUrl", default="")
        d = _date(_first(it, "postedAt.date", "postedAt", "publishedAt", "date", "postedAtISO"))
        media_type = str(_first(it, "mediaType", "type", "postType", default="")).lower()
        fmt = "video" if "video" in media_type else "image" if "image" in media_type else \
              "document" if "document" in media_type else "link" if "http" in text or "article" in media_type else "text"
        posts.append(Post(channel="linkedin", account=account, date=d, url=url, text=text, format=fmt,
                          likes=_first(it, "engagement.likes", "numLikes", "likeCount", "reactions", default=0),
                          reposts=_first(it, "engagement.shares", "numShares", "repostCount", default=0),
                          comments=_first(it, "engagement.comments", "numComments", "commentCount", default=0),
                          views=None, collected_via="apify_linkedin"))
    posts = [p for p in posts if p.date]
    if not posts:
        raise RuntimeError(f"Apify LinkedIn actor {LI_ACTOR} returned no usable posts for {company_url}")
    return posts
