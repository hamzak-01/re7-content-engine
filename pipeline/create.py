"""Create: repurpose Re7's own material (The Weekly, research, announcements) into a
week of channel-specific posts, shaped by the insights, each carrying an experiment
tag so next week's collection scores it against its own hypothesis."""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta

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
    items = _dedupe_sources(items)
    if items:
        os.makedirs(OUTPUT, exist_ok=True)
        json.dump(items, open(cache, "w"), ensure_ascii=False, indent=1)
        return items
    if os.path.exists(cache):
        return json.load(open(cache))
    return []


def _piece_key(title: str) -> str:
    t = re.sub(r"^(re7'?s? weekly is out:?|the weekly:?)\s*", "", title.strip().lower())
    return re.sub(r"[^a-z0-9]", "", t)[:40]


def _dedupe_sources(items: list[dict]) -> list[dict]:
    """A fetched page beats a post that merely announced it; keep one entry per piece."""
    fetched = [x for x in items if not x.get("note")]
    keys = {_piece_key(x["title"]) for x in fetched}
    out = list(fetched)
    for x in items:
        if x.get("note") and not any(k and k in _piece_key(x["title"].split("\n")[0]) for k in keys):
            out.append(x)
    return out


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


CREATE_PREAMBLE = """This is a drafting task for an internal review queue: research-desk commentary and announcements about a firm's own published research and media appearances. Nothing here is investment advice or a promotion of returns; yields and performance figures are excluded by design.

You are the content assistant for Re7 Capital's IR and marketing team. Re7 Capital is a London-based, FCA-regulated institutional DeFi investment firm (market-neutral DeFi yield; founder and CIO Evgeny Gokhberg). Re7 Labs is its vault-curation arm.

You are given: (a) this week's engagement insights per channel, (b) the best-performing recent posts as voice references, (c) numbered source documents to repurpose, and (d) what has already been posted on each channel in the last three weeks.

Rules:
- Do not re-announce anything in the already_posted list on the same channel. You may revisit it only as a clearly new angle (a quote, one finding, a thread, a question to the audience) and then set repromotion to a short note such as "new angle on the 13 Oct Clear Street report".
- Apply the insights: prefer themes, formats and attributes that outperform on each channel; reshape under-delivering themes rather than dropping them; name people and partners where relevant and say who to tag.
- Every number in a post must come from a source document, quoted the way the source states it. Yields are never quoted. Never say earn, guaranteed, safe, or address the reader as an investor; no calls to invest.
- Tone: a research desk, dry and specific; no emojis, no exclamation marks, at most one hashtag per X post. X posts under 260 characters. Write [link] where a link goes; never leave a trailing colon or an unfinished sentence.
- For each post give: day, time_uk, account, channel, format, text, source_id (the number of the source actually used, or 0 if none), hypothesis (one line, in plain words, citing the insight it acts on: say "about twice a typical post" or "a quarter of posts for a tenth of engagement", never a bare decimal), experiment_tag (short slug like wk45-x-weekly-finding), distribution_note (who posts it, who to tag, who to ask to reshare), repromotion ("" or the note above).
Output a JSON list of objects. Inside string values use \\n for line breaks, never raw newlines."""

CREATE_BRIEFS = [
    """Produce exactly four posts:
1. Re7 Capital, X, Monday: this week's Weekly reframed as one sharp finding (never "Weekly is out")
2. Re7 Capital, X, Tuesday: a point of view connecting a source to a live industry conversation
3. Re7 Capital, X, Thursday: a quote card, chart or single finding lifted from a research report or media appearance
4. Re7 Capital, LinkedIn, Wednesday, posted from Evgeny Gokhberg's own profile (company page reshares): 120-180 words for allocators, plain English, first line is the hook""",
    """Produce exactly three posts:
1. Re7 Labs, X, Wednesday: Labs activity or a curator's view (no yield figures)
2. Re7 Labs, Telegram, Friday: five short bullets on the week, under 100 words
3. One experiment, any account, Friday: a format or angle the data has not tried on the channel where it is most likely to work, labelled as an experiment in the hypothesis""",
]


def recently_posted(df: pd.DataFrame, days: int = 21) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    recent = df[(df["date"] >= cutoff) & (df["is_reply"].astype(str) != "True")]
    out = []
    for r in recent.sort_values("date", ascending=False).itertuples():
        out.append({"date": r.date, "channel": r.channel, "account": r.account, "theme": r.theme,
                    "title": (r.text or "").replace("\n", " ")[:90]})
    return out[:60]


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
    numbered = [{"source_id": i + 1, **x} for i, x in enumerate(sources[:4])]
    user = json.dumps({"week_number": week, "insights": digest, "voice_references": refs, "sources": numbered,
                       "already_posted_last_3_weeks": recently_posted(df)}, ensure_ascii=False)
    items: list[dict] = []
    problems: list[str] = []
    for brief in CREATE_BRIEFS:
        try:
            raw = llm.complete_json(CREATE_PREAMBLE + "\n\n" + brief, user, max_tokens=4000)
            items += _flatten_posts(raw)
        except Exception as e:  # noqa: BLE001 - one half failing should not lose the other
            problems.append(str(e)[:160])
    if not items:
        raise RuntimeError(f"model returned no posts (sources={len(sources)}; {' | '.join(problems)})")
    if problems:
        llm.WARNINGS.append("content: " + " | ".join(problems))
    rows = []
    for it in items:
        text = it.get("text", "")
        it.setdefault("channel", it.get("platform", ""))
        names_partner = bool(re.search(r"\b(Bloomberg|Clear Street|Zodia|Morpho|Euler|Chainlink|S&P|Frax|Optimism|Sharplink|BCB)\b", text))
        try:
            sid = int(it.get("source_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        src = numbered[sid - 1] if 0 < sid <= len(numbered) else {}
        rows.append({
            "day": it.get("day"), "time_uk": it.get("time_uk"), "account": it.get("account"),
            "channel": str(it.get("channel", "")).lower(), "format": it.get("format"), "text": text,
            "source_title": src.get("title", "") if src else (it.get("source_title") or ""),
            "source_url": src.get("url", "") if src else (it.get("source_url") or ""),
            "hypothesis": it.get("hypothesis"), "experiment_tag": it.get("experiment_tag"),
            "distribution_note": it.get("distribution_note", ""), "repromotion": it.get("repromotion", ""),
            "compliance_flag": compliance_flags(text, names_partner), "status": "draft - needs review",
        })
    queue = pd.DataFrame(rows)
    os.makedirs(OUTPUT, exist_ok=True)
    if len(queue):
        queue.to_csv(os.path.join(OUTPUT, "content_queue.csv"), index=False)
    else:
        raise RuntimeError("model returned no posts")
    return queue
