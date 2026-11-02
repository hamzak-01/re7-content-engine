"""Create: repurpose Re7's own material (The Weekly, research, announcements) into a
week of channel-specific posts, shaped by the insights, each carrying an experiment
tag so next week's collection scores it against its own hypothesis."""
from __future__ import annotations

import json
import os
import re
from datetime import date

import pandas as pd
import requests
from bs4 import BeautifulSoup

from . import llm
from .taxonomy import COMPLIANCE_TRIGGERS
from .store import OUTPUT

BLOG_INDEX = "https://re7.capital/blog"
UA = {"User-Agent": "Mozilla/5.0 (compatible; re7-content-engine/0.1)"}


def fetch_sources(max_items: int = 3) -> list[dict]:
    """Latest Re7 blog posts (The Weekly, research). Falls back to the cached copy if the site is unreachable."""
    cache = os.path.join(OUTPUT, "sources_cache.json")
    try:
        html = requests.get(BLOG_INDEX, headers=UA, timeout=20).text
        soup = BeautifulSoup(html, "html.parser")
        links = []
        for a in soup.select("a[href*='/blog/']"):
            href = a.get("href", "")
            if re.search(r"/blog/[0-9a-f-]{20,}", href) and href not in links:
                links.append(href if href.startswith("http") else "https://re7.capital" + href)
        items = []
        for href in links[:max_items]:
            page = requests.get(href, headers=UA, timeout=20).text
            ps = BeautifulSoup(page, "html.parser")
            title = (ps.find("h1") or ps.find("title")).get_text(" ", strip=True)
            body = " ".join(p.get_text(" ", strip=True) for p in ps.find_all("p"))
            items.append({"title": title, "url": href, "text": body[:3500]})
        if items:
            os.makedirs(OUTPUT, exist_ok=True)
            json.dump(items, open(cache, "w"), ensure_ascii=False, indent=1)
            return items
    except Exception:  # noqa: BLE001
        pass
    if os.path.exists(cache):
        return json.load(open(cache))
    return []


CREATE_SYSTEM = """You are the content assistant for Re7 Capital's IR and marketing team. Re7 Capital is a London-based, FCA-regulated institutional DeFi investment firm (market-neutral DeFi yield; founder and CIO Evgeny Gokhberg). Re7 Labs is its vault-curation arm.
You are given (a) this week's insights per channel, (b) the top-performing recent posts as voice references, and (c) source documents to repurpose. Produce a one-week schedule, Monday to Friday:
- 3 posts for Re7 Capital on X (under 260 characters each)
- 1 post for Re7 Capital on LinkedIn, written for allocators (120-180 words, one idea, first line is the hook)
- 1 post for Re7 Labs on X if any source mentions Labs activity
- 1 Telegram summary (five short bullets, under 100 words)
Apply the insights: prefer the themes, formats and attributes that index highest on each channel; where a post names a person or partner, name them. Every number must come from a source document, quoted the way the source states it. Yields are market observations, never Re7 returns. Never say earn, guaranteed, safe, or address the reader as an investor; no calls to invest. Tone: a research desk, dry and specific; no emojis, no exclamation marks, at most one hashtag per X post.
For each post give: day, time_uk, account, channel, format, text, source_title, source_url, hypothesis (one line: which insight this post tests, e.g. 'media_appearance + names_person on linkedin'), experiment_tag (short slug like 'wk45-li-media-person').
Output a JSON list."""


def compliance_flags(text: str, names_partner: bool) -> str:
    t = text.lower()
    flags = []
    if any(k in t for k in COMPLIANCE_TRIGGERS["yield_or_performance"]):
        flags.append("quotes yields/performance")
    if any(k in t for k in COMPLIANCE_TRIGGERS["promissory_language"]):
        flags.append("promissory wording")
    if names_partner:
        flags.append("names a third party")
    return "; ".join(flags)


def run(df: pd.DataFrame, insights: dict, live: bool = True) -> pd.DataFrame:
    sources = fetch_sources()
    main = df[(df["is_reply"].astype(str) != "True") & (df["theme"] != "")].copy()
    main["engagement_index"] = pd.to_numeric(main["engagement_index"], errors="coerce")
    refs = main.sort_values("engagement_index", ascending=False).head(6)
    refs = [{"channel": r.channel, "index": r.engagement_index, "theme": r.theme, "text": (r.text or "")[:300]} for r in refs.itertuples()]
    week = date.today().isocalendar()[1]
    if not live:
        return pd.DataFrame()
    user = json.dumps({"week_number": week, "insights": insights, "voice_references": refs, "sources": sources}, ensure_ascii=False)
    items = llm.complete_json(CREATE_SYSTEM, user, max_tokens=6000)
    if isinstance(items, dict):  # model wrapped the list in an object
        items = next((v for v in items.values() if isinstance(v, list)), [])
    rows = []
    for it in items:
        text = it.get("text", "")
        names_partner = bool(re.search(r"\b(Bloomberg|Clear Street|Zodia|Morpho|Euler|Chainlink|S&P|Frax|Optimism|Sharplink|BCB)\b", text))
        rows.append({
            "day": it.get("day"), "time_uk": it.get("time_uk"), "account": it.get("account"),
            "channel": str(it.get("channel", "")).lower(), "format": it.get("format"), "text": text,
            "source_title": it.get("source_title"), "source_url": it.get("source_url"),
            "hypothesis": it.get("hypothesis"), "experiment_tag": it.get("experiment_tag"),
            "compliance_flag": compliance_flags(text, names_partner), "status": "draft - needs review",
        })
    queue = pd.DataFrame(rows)
    os.makedirs(OUTPUT, exist_ok=True)
    queue.to_csv(os.path.join(OUTPUT, "content_queue.csv"), index=False)
    return queue
