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


def collect(handle: str, account: str = "Re7 Capital", limit: int = 60) -> list[Post]:
    handle = handle.strip().lstrip("@").replace("https://t.me/", "").replace("t.me/", "").strip("/")
    url = f"https://t.me/s/{handle}"
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    posts: list[Post] = []
    for m in soup.select(".tgme_widget_message_wrap")[-limit:]:
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
        posts.append(Post(channel="telegram", account=account, date=d, url=link["href"], text=text,
                          format=fmt, views=_views(views.get_text()) if views else None,
                          likes=None, reposts=None, comments=None, collected_via="telegram_public"))
    if not posts:
        raise RuntimeError(f"No messages parsed from {url}; is the channel public?")
    return posts
