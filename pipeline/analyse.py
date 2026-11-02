"""Analyse: (1) label every unlabelled post against the fixed taxonomy with Claude,
(2) compute per-channel statistics on a normalised engagement index,
(3) write a short insights memo from the statistics (not from the posts)."""
from __future__ import annotations

import json
import os
import re
from datetime import date

import pandas as pd

from . import llm, insights as ins2
from .taxonomy import THEMES, FORMATS, ATTRIBUTES
from .store import OUTPUT

LABEL_SYSTEM = """You label social media posts for an institutional DeFi investment firm (Re7 Capital; its vault-curation arm is Re7 Labs).
For each post pick exactly one theme from the list, one format from the list, and two booleans.
Themes: {themes}
Formats: {formats}
names_person: {np}
names_partner: {npart}
entities: list of the specific people and organisations named or tagged in the post (e.g. ["Evgeny Gokhberg", "Bloomberg", "Sharplink"]); use the canonical name, not the handle; [] if none.
Be conservative: if a post is a short reply or disclaimer, theme = other. Output a JSON list of objects with keys post_id, theme, format, names_person, names_partner, entities."""


def label_posts(df: pd.DataFrame, batch: int = 25, force: bool = False, errors: list | None = None) -> tuple[pd.DataFrame, int]:
    """Fill theme / names_person / names_partner (and correct format) where missing."""
    errors = errors if errors is not None else []
    need = df[(df["theme"] == "") | (df["label_source"] == "heuristic") | force].copy()
    need = need[need["text"].str.strip() != ""]  # cannot label what we cannot read
    if need.empty:
        return df, 0
    system = LABEL_SYSTEM.format(
        themes="; ".join(f"{k}: {v}" for k, v in THEMES.items()),
        formats=", ".join(FORMATS),
        np=ATTRIBUTES["names_person"], npart=ATTRIBUTES["names_partner"])
    labelled = 0
    df = df.set_index("post_id")
    for i in range(0, len(need), batch):
        chunk = need.iloc[i:i + batch]
        payload = [{"post_id": r.post_id, "channel": r.channel, "format_hint": r.format, "text": r.text[:600]}
                   for r in chunk.itertuples()]
        try:
            out = llm.complete_json(system, json.dumps(payload, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001 - keep going; report the batch
            errors.append(f"label batch {i // batch + 1}: {str(e)[:160]}")
            continue
        for item in out:
            pid = item.get("post_id")
            if pid not in df.index:
                continue
            theme = item.get("theme", "other")
            df.at[pid, "theme"] = theme if theme in THEMES else "other"
            fmt = item.get("format", "")
            if fmt in FORMATS:
                df.at[pid, "format"] = fmt
            df.at[pid, "names_person"] = str(bool(item.get("names_person")))
            df.at[pid, "names_partner"] = str(bool(item.get("names_partner")))
            ents = item.get("entities") or []
            df.at[pid, "entities"] = "; ".join(str(e) for e in ents if e)[:300]
            df.at[pid, "label_source"] = "claude"
            labelled += 1
    return df.reset_index(), labelled


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def compute_index(df: pd.DataFrame) -> pd.DataFrame:
    """engagement_index = engagement_score / median score of main posts on that channel.
    1.0 = a typical post for that channel; 5.0 = five times a typical post."""
    df = df.copy()
    score = _num(df["likes"]).fillna(0) + 2 * _num(df["reposts"]).fillna(0) + 3 * _num(df["comments"]).fillna(0)
    # Telegram exposes views only, so views are its engagement measure.
    tg = df["channel"] == "telegram"
    score[tg] = _num(df.loc[tg, "views"]).fillna(0)
    df["engagement_score"] = score
    main = df[df["is_reply"].astype(str) != "True"]
    # Median per channel AND account: Re7 Labs and Re7 Capital have different baselines on X.
    med = main.groupby(["channel", "account"])["engagement_score"].median().replace(0, 1)
    df["engagement_index"] = df.apply(
        lambda r: round(r["engagement_score"] / med.get((r["channel"], r["account"]), 1), 2) if r["is_reply"] != "True" else "", axis=1)
    return df


def stats(df: pd.DataFrame) -> dict:
    main = df[(df["is_reply"].astype(str) != "True") & (df["theme"] != "")].copy()
    main["engagement_index"] = _num(main["engagement_index"])
    main["views"] = _num(main["views"])
    out: dict = {"as_of": date.today().isoformat(), "channels": {}}
    for ch, g in main.groupby("channel"):
        c: dict = {
            "posts": int(len(g)),
            "median_engagement_score": float(df[(df.channel == ch) & (df.is_reply.astype(str) != "True")]["engagement_score"].astype(float).median()),
            "by_theme": [], "by_format": [], "by_attribute": {},
            "top_posts": [], "bottom_themes": [],
        }
        for key, name in (("theme", "by_theme"), ("format", "by_format")):
            agg = g.groupby(key).agg(n=("post_id", "count"), mean_index=("engagement_index", "mean"),
                                     median_views=("views", "median")).reset_index()
            agg = agg.rename(columns={key: "label"})
            agg["directional"] = agg["n"] < ins2.MIN_N
            agg = agg.sort_values(["directional", "mean_index"], ascending=[True, False])
            c[name] = [{"label": r["label"], "n": int(r["n"]), "mean_index": round(float(r["mean_index"]), 2),
                        "median_views": (None if pd.isna(r["median_views"]) else int(r["median_views"])),
                        "directional": bool(r["directional"])} for _, r in agg.iterrows()]
        for attr in ("names_person", "names_partner"):
            t = g[g[attr].astype(str) == "True"]["engagement_index"].mean()
            f = g[g[attr].astype(str) != "True"]["engagement_index"].mean()
            c["by_attribute"][attr] = {"with": round(float(t), 2) if pd.notna(t) else None,
                                       "without": round(float(f), 2) if pd.notna(f) else None,
                                       "n_with": int((g[attr].astype(str) == "True").sum())}
        acc = g.groupby("account").agg(n=("post_id", "count"), mean_index=("engagement_index", "mean"),
                                       median_score=("engagement_score", "median"), median_views=("views", "median")).reset_index()
        c["by_account"] = [{"account": r["account"], "n": int(r["n"]), "mean_index": round(float(r["mean_index"]), 2),
                            "median_score": float(r["median_score"]),
                            "median_views": (None if pd.isna(r["median_views"]) else int(r["median_views"]))} for _, r in acc.iterrows()]
        top = g.sort_values("engagement_index", ascending=False).head(3)
        c["top_posts"] = [{"date": r.date, "theme": r.theme, "format": r.format, "index": float(r.engagement_index),
                           "text": (r.text or "")[:140]} for r in top.itertuples()]
        c["bottom_themes"] = [t["label"] for t in c["by_theme"][-2:]] if len(c["by_theme"]) >= 2 else []
        out["channels"][ch] = c
    out["second_order"] = ins2.build(main)
    # experiment feedback: how did posts this system generated perform?
    exp = main[main["experiment_tag"].astype(str) != ""]
    out["experiments"] = [{"tag": r.experiment_tag, "channel": r.channel, "index": float(r.engagement_index), "date": r.date}
                          for r in exp.itertuples()]
    return out


MEMO_SYSTEM = """This is an internal analytics task about social-media engagement statistics. It involves no investment advice, no performance claims and no promotion; it only describes which kinds of posts attracted engagement.

You write the weekly content memo for the Head of Investor Relations at Re7 Capital, an FCA-regulated institutional DeFi investment firm with two accounts: Re7 Capital (the fund) and Re7 Labs (vault curation). You are given statistics only, never the posts themselves.

How to read the numbers: engagement_index is relative to each channel-and-account's median post (1.0 = typical, 3.0 = three times typical), so it compares across channels; raw likes do not. Telegram has no reactions, so its score is views. 'return_on_effort' = share of engagement divided by share of posts: 2.0 means a theme earns twice its share of attention; 0.5 means half. Groups with n below the stated minimum are directional and must be labelled so.

Write for someone who has four minutes and will act on Monday. Structure, with these exact headings:
## The one thing
One paragraph: the single most consequential finding, with its numbers and what it implies.
## Do more of
Two or three themes/formats/tactics, each with the effort-versus-return numbers and the channel.
## Do less of (or do differently)
What is absorbing posting effort for below-average return, with numbers, and the specific change (e.g. a different format, a person's profile instead of the company page, tagging the partner).
## Where the same content travels best
From the cross-channel data: which channel the Weekly and research reports perform best on, and what that implies for how each is adapted per channel.
## Who moves the needle
From the entity leaderboard and extremes: which named people or partners are associated with the best posts, and the tactic that follows (guest selection, asking partners to repost).
## Three changes for next week
Numbered, specific, each tied to a number above and to a channel.
## Something I did not expect
One finding that cuts against the obvious reading.
## Caveats
Sample sizes, directional groups, what the data cannot show (e.g. LinkedIn impressions), and whether any system-generated experiments have been scored yet.

Plain English, UK spelling, no hype, no bullet soup: short paragraphs with numbers in them. Under 450 words."""


PCT = re.compile(r"\d+(?:\.\d+)?\s?%|\bAPY\b|\bAPR\b", re.I)


def _clean(text: str, n: int = 90) -> str:
    """Post snippets for the writing prompts: no yield figures (they are irrelevant to
    engagement analysis and can trip content filters), short, single line."""
    return PCT.sub("[figure]", (text or "").replace("\n", " "))[:n]


def memo_digest(st: dict) -> dict:
    """What the memo needs, without the bulk: no post texts beyond 90 chars, no long lists, no yield figures."""
    so = st.get("second_order", {})
    d = {"as_of": st.get("as_of"), "min_n_for_confidence": so.get("min_n_for_confidence", 3), "channels": {}}
    for ch, c in st.get("channels", {}).items():
        d["channels"][ch] = {
            "posts": c["posts"], "by_account": c.get("by_account", []),
            "by_theme": [{k: v for k, v in t.items() if k != "median_views"} for t in c["by_theme"]],
            "by_format": [{k: v for k, v in t.items() if k != "median_views"} for t in c["by_format"][:6]],
            "by_attribute": c.get("by_attribute", {}),
        }
    d["effort_vs_return"] = so.get("effort_vs_return", {})
    d["theme_by_channel"] = so.get("cross_channel", {}).get("theme_by_channel", {})
    d["same_piece_across_channels"] = so.get("cross_channel", {}).get("editions", [])[:5]
    d["who_drives_it"] = so.get("who_drives_it", [])[:8]
    d["reach_vs_resonance_x"] = so.get("reach_vs_resonance_x", {})
    d["weekly_trend"] = {ch: rows[-6:] for ch, rows in so.get("weekly_trend", {}).items()}
    d["day_of_week"] = so.get("day_of_week", {})
    d["extremes"] = {ch: {"top": [{**p, "text": _clean(p["text"])} for p in ex["top"][:2]],
                          "bottom": [{**p, "text": _clean(p["text"])} for p in ex["bottom"][:2]]}
                     for ch, ex in so.get("extremes", {}).items()}
    d["same_piece_across_channels"] = [{**e, "content": e["content"][:60]} for e in d["same_piece_across_channels"]]
    d["experiments"] = st.get("experiments", [])[:10]
    return d


def write_memo(st: dict) -> str:
    return llm.complete(MEMO_SYSTEM, json.dumps(memo_digest(st), ensure_ascii=False), max_tokens=1800)


def run(df: pd.DataFrame, live: bool = True) -> tuple[pd.DataFrame, dict, str, int, list]:
    labelled = 0
    errors: list = []
    df, _ = prelabel(df)
    if live:
        df, labelled = label_posts(df, errors=errors)
    df = compute_index(df)
    df = ins2.assign_content_keys(df)
    st = stats(df)
    if live:
        try:
            memo = write_memo(st)
        except Exception as e:  # noqa: BLE001
            errors.append(f"memo: {str(e)[:160]}")
            memo = open(os.path.join(OUTPUT, "memo.md")).read() if os.path.exists(os.path.join(OUTPUT, "memo.md")) else ""
    else:
        memo = "(memo not regenerated: offline run)"
    os.makedirs(OUTPUT, exist_ok=True)
    with open(os.path.join(OUTPUT, "insights.json"), "w") as f:
        json.dump(st, f, indent=2)
    with open(os.path.join(OUTPUT, "memo.md"), "w") as f:
        f.write(memo)
    return df, st, memo, labelled, errors


# ---------------------------------------------------------------------------
# Rule-based pre-labelling: used so the dashboard is never empty before the
# first live run. Anything labelled this way is re-labelled by Claude when live.
RULES = [
    ("media_appearance", r"bloomberg|crypto minutes|podcast|1000x|real vision"),
    ("research_report", r"report|framework|clear ?street|zodia"),
    ("market_commentary", r"the weekly|re7's weekly|weekly:"),
    ("speaking_event", r"speaking at|summit|conference|panel"),
    ("labs_product", r"vault|morpho|euler|frax|re7 labs"),
    ("award_recognition", r"award|shortlist|winner"),
    ("hiring_culture", r"hiring|we're hiring|job"),
    ("chart_data", r"chart of the week"),
]


def prelabel(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    n = 0
    for i, r in df[df["theme"] == ""].iterrows():
        text = (r["text"] or "").lower()
        if str(r["is_reply"]) == "True":
            theme = "other"
        elif not text:
            continue
        else:
            theme = next((t for t, rx in RULES if re.search(rx, text)), "other")
        df.at[i, "theme"] = theme
        df.at[i, "label_source"] = "heuristic"
        df.at[i, "names_person"] = str(bool(re.search(r"evgeny|gokhberg|@\w+|\b[A-Z][a-z]+ [A-Z][a-z]+\b", r["text"] or "")))
        df.at[i, "names_partner"] = str(bool(re.search(r"bloomberg|clear street|zodia|morpho|frax|sharplink|bcb|canton|cointelegraph|monad|starknet|optimism", text)))
        n += 1
    return df, n
