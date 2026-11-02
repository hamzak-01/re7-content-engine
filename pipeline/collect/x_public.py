"""Last-resort X collector with no API key: the public syndication timeline that powers
embedded profile widgets. It exposes recent tweets with text, date and public counts.
Coverage is the most recent ~20 posts and the endpoint is not guaranteed, so this sits
behind the Apify collectors and in front of the manual seed."""
from __future__ import annotations

import json
import re
from datetime import datetime

import requests

from ..schema import Post

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def _walk(obj, found: list):
    if isinstance(obj, dict):
        if "full_text" in obj and "created_at" in obj:
            found.append(obj)
        for v in obj.values():
            _walk(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, found)


def collect(handle: str, account: str) -> list[Post]:
    h = handle.lstrip("@")
    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{h}"
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
    if not m:
        raise RuntimeError("syndication page had no embedded data")
    tweets: list = []
    _walk(json.loads(m.group(1)), tweets)
    posts = []
    for t in tweets:
        try:
            d = datetime.strptime(t["created_at"], "%a %b %d %H:%M:%S %z %Y").date().isoformat()
        except ValueError:
            continue
        text = t.get("full_text", "")
        is_rt = text.startswith("RT @") or bool(t.get("retweeted_status"))
        media = (t.get("extended_entities") or t.get("entities") or {}).get("media") or []
        fmt = "repost" if is_rt else "quote" if t.get("is_quote_status") else \
              "video" if any(x.get("type") == "video" for x in media) else "image" if media else \
              "link" if "http" in text else "text"
        tid = t.get("id_str") or t.get("id")
        posts.append(Post(channel="x", account=account, date=d, url=f"https://x.com/{h}/status/{tid}" if tid else "",
                          text=text, format=fmt, likes=t.get("favorite_count", 0), reposts=t.get("retweet_count", 0),
                          comments=t.get("reply_count", 0), views=None,
                          is_reply=bool(t.get("in_reply_to_status_id_str")) or is_rt, collected_via="x_public"))
    if not posts:
        raise RuntimeError("syndication timeline returned no tweets")
    return posts
