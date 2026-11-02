"""Telegram public channel via the t.me/s/<handle> preview page (no API key, no login)."""
from __future__ import annotations

import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ..schema import Post

UA = {"User-Agent": "Mozilla/5.0 (compatible; re7-content-engine/0.1)"}


def _views(s: str) -> float | None:
    s = s.strip().upper().replace(",", "")
    m = re.match(r"([\d.]+)([KM]?)", s)
    if not m:
        return None
    n = float(m.group(1))
    return n * {"": 1, "K": 1_000, "M": 1_000_000}[m.group(2)]


def _parse_page(html: str, account: str) -> tuple[list[Post], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    posts: list[Post] = []
    oldest_id = None
    for m in soup.select(".tgme_widget_message_wrap"):
        link = m.select_one("a.tgme_widget_message_date")
        t = m.select_one("time")
        body = m.select_one(".tgme_widget_message_text")
        views = m.select_one(".tgme_widget_message_views")
        if not (link and t):
            continue
        d = datetime.fromisoformat(t["datetime"].replace("Z", "+00:00")).date().isoformat()
        text = body.get_text(" ", strip=True) if body else ""
        fmt = "video" if m.select_one(".tgme_widget_message_video_player") else \
              "image" if m.select_one(".tgme_widget_message_photo_wrap") else \
              "link" if m.select_one(".tgme_widget_message_link_preview") or "http" in text else "text"
        mid = link["href"].rstrip("/").split("/")[-1]
        if mid.isdigit() and (oldest_id is None or int(mid) < int(oldest_id)):
            oldest_id = mid
        posts.append(Post(channel="telegram", account=account, date=d, url=link["href"], text=text,
                          format=fmt, views=_views(views.get_text()) if views else None,
                          likes=None, reposts=None, comments=None, collected_via="telegram_public"))
    return posts, oldest_id


def collect(handle: str, account: str = "Re7 Capital", limit: int = 80) -> list[Post]:
    handle = handle.strip().lstrip("@").replace("https://t.me/", "").replace("t.me/", "").strip("/")
    base = f"https://t.me/s/{handle}"
    all_posts: list[Post] = []
    before = None
    for _ in range(6):  # pages of ~20
        url = base + (f"?before={before}" if before else "")
        r = requests.get(url, headers=UA, timeout=20)
        r.raise_for_status()
        posts, oldest = _parse_page(r.text, account)
        if not posts:
            break
        all_posts = posts + all_posts
        if len(all_posts) >= limit or not oldest or oldest == before:
            break
        before = oldest
    if not all_posts:
        raise RuntimeError(f"No messages parsed from {base}; is the channel public?")
    seen, out = set(), []
    for p in all_posts:
        if p.post_id not in seen:
            seen.add(p.post_id); out.append(p)
    return out
