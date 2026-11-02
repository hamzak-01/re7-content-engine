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
from .analyse import memo_digest, _clean
from .taxonomy import COMPLIANCE_TRIGGERS
from .store import OUTPUT

UA = {"User-Agent": "Mozilla/5.0 (compatible; re7-content-engine/0.1)"}


BLOG_RE = re.compile(r"https?://re7\.capital/blog/[0-9a-f-]{20,}")


def _fetch_page(url: str) -> dict | None:
    try:
        page = requests.get(url, headers=UA, timeout=20).text
        ps = BeautifulSoup(page, "html.parser")
        h = ps.find("h1") or ps.find("title")
        title = h.get_text(" ", strip=True) if h else url
        body = " ".join(p.get_text(" ", strip=True) for p in ps.find_all("p"))
        if len(body) < 200:
            return None
        return {"title": title, "url": url, "text": body[:3500]}
    except Exception:  # noqa: BLE001
        return None


def fetch_sources(df: pd.DataFrame | None = None, max_items: int = 3) -> list[dict]:
    """Source documents to repurpose. Discovered from Re7's own recent posts (which link to
    The Weekly and research), then fetched from re7.capital. If a page cannot be fetched the
    post text itself stands in, so there is always something to write from."""
    cache = os.path.join(OUTPUT, "sources_cache.json")
    items: list[dict] = []
    seen: set[str] = set()
    if df is not None and len(df):
        recent = df[df["is_reply"].astype(str) != "True"].sort_values("date", ascending=False)
        for r in recent.itertuples():
            text = r.text or ""
            urls = BLOG_RE.findall(text)
            key = urls[0] if urls else None
            is_source_post = bool(urls) or re.search(r"weekly is out|new report|new episode|launched|vault", text, re.I)
            title_key = re.sub(r"[^a-z0-9]", "", text[:70].lower())
            if not is_source_post or (key and key in seen) or title_key in seen:
                continue
            seen.add(title_key)
            if key:
                seen.add(key)
            page = _fetch_page(key) if key else None
            items.append(page or {"title": text[:90], "url": key or r.url, "text": text[:2000],
                                  "note": "post text used; page not fetched"})
            if len(items) >= max_items + 2:
                break
    if items:
        os.makedirs(OUTPUT, exist_ok=True)
        json.dump(items, open(cache, "w"), ensure_ascii=False, indent=1)
        return items
    if os.path.exists(cache):
        return json.load(open(cache))
    return []


def _flatten_posts(obj) -> list[dict]:
    """Collect every dict that looks like a post, however the model nested it."""
    out: list[dict] = []
    if isinstance(obj, dict):
        if "text" in obj and ("channel" in obj or "platform" in obj):
            out.append(obj)
        else:
            for v in obj.values():
                out.extend(_flatten_posts(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_flatten_posts(v))
    return out


CREATE_SYSTEM = """This is a drafting task for an internal review queue: research-desk commentary and announcements about a firm's own published research and media appearances. Nothing here is investment advice or a promotion of returns; yields and performance figures are excluded by design.

You are the content assistant for Re7 Capital's IR and marketing team. Re7 Capital is a London-based, FCA-regulated institutional DeFi investment firm (market-neutral DeFi yield; founder and CIO Evgeny Gokhberg). Re7 Labs is its vault-curation arm.
You are given (a) this week's insights per channel, (b) the top-performing recent posts as voice references, and (c) source documents to repurpose. Produce a one-week schedule, Monday to Friday:
- 3 posts for Re7 Capital on X (under 260 characters each)
- 1 post for Re7 Capital on LinkedIn, written for allocators (120-180 words, one idea, first line is the hook)
- 1 post for Re7 Labs on X if any source mentions Labs activity
- 1 Telegram summary (five short bullets, under 100 words)
Apply the insights: prefer the themes, formats and attributes with the highest return_on_effort on each channel; reshape low-return themes (e.g. the Weekly) into the formats and framings that do work on that channel rather than dropping them; where a post names a person or partner, name them and suggest tagging them. Each post's hypothesis must cite the specific number it is acting on. Every number must come from a source document, quoted the way the source states it. Yields are market observations, never Re7 returns. Never say earn, guaranteed, safe, or address the reader as an investor; no calls to invest. Tone: a research desk, dry and specific; no emojis, no exclamation marks, at most one hashtag per X post.
For each post give: day, time_uk, account, channel, format, text, source_title, source_url, hypothesis (one line: which insight this post tests, e.g. 'media_appearance + names_person on linkedin'), experiment_tag (short slug like 'wk45-li-media-person').
Output a JSON list. Inside string values use \\n for line breaks, never raw newlines."""


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
    sources = fetch_sources(df)
    main = df[(df["is_reply"].astype(str) != "True") & (df["theme"] != "")].copy()
    main["engagement_index"] = pd.to_numeric(main["engagement_index"], errors="coerce")
    refs = main[main["account"] == "Re7 Capital"].sort_values("engagement_index", ascending=False).head(5)
    refs = [{"channel": r.channel, "index": r.engagement_index, "theme": r.theme, "text": _clean(r.text, 280)} for r in refs.itertuples()]
    sources = [{**x, "text": _clean(x.get("text", ""), 3000)} for x in sources if "featured yield" not in x.get("text", "").lower()]
    week = date.today().isocalendar()[1]
    if not live:
        return pd.DataFrame()
    digest = memo_digest(insights)
    digest = {k: digest[k] for k in ("effort_vs_return", "theme_by_channel", "who_drives_it", "reach_vs_resonance_x", "channels") if k in digest}
    user = json.dumps({"week_number": week, "insights": digest, "voice_references": refs, "sources": sources[:4]}, ensure_ascii=False)
    raw = llm.complete_json(CREATE_SYSTEM, user, max_tokens=6000)
    items = _flatten_posts(raw)
    if not items:
        raise RuntimeError(f"model returned no posts (sources={len(sources)}; reply shape={type(raw).__name__})")
    rows = []
    for it in items:
        text = it.get("text", "")
        it.setdefault("channel", it.get("platform", ""))
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
    if len(queue):
        queue.to_csv(os.path.join(OUTPUT, "content_queue.csv"), index=False)
    else:
        raise RuntimeError("model returned no posts")
    return queue
