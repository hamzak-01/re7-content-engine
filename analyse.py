"""Analyse: (1) label every unlabelled post against the fixed taxonomy with Claude,
(2) compute per-channel statistics on a normalised engagement index,
(3) write a short insights memo from the statistics (not from the posts)."""
from __future__ import annotations

import json
import os
import re
from datetime import date

import pandas as pd

from . import llm
from .taxonomy import THEMES, FORMATS, ATTRIBUTES
from .store import OUTPUT

LABEL_SYSTEM = """You label social media posts for an institutional DeFi investment firm (Re7 Capital; its vault-curation arm is Re7 Labs).
For each post pick exactly one theme from the list, one format from the list, and two booleans.
Themes: {themes}
Formats: {formats}
names_person: {np}
names_partner: {npart}
Be conservative: if a post is a short reply or disclaimer, theme = other. Output a JSON list of objects with keys post_id, theme, format, names_person, names_partner."""


def label_posts(df: pd.DataFrame, batch: int = 25, force: bool = False) -> tuple[pd.DataFrame, int]:
    """Fill theme / names_person / names_partner (and correct format) where missing."""
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
        out = llm.complete_json(system, json.dumps(payload, ensure_ascii=False))
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
            df.at[pid, "label_source"] = "claude"
            labelled += 1
    return df.reset_index(), labelled


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def compute_index(df: pd.DataFrame) -> pd.DataFrame:
    """engagement_index = engagement_score / median score of main posts on that channel.
    1.0 = a typical post for that channel; 5.0 = five times a typical post."""
    df = df.copy()
    df["engagement_score"] = _num(df["likes"]).fillna(0) + 2 * _num(df["reposts"]).fillna(0) + 3 * _num(df["comments"]).fillna(0)
    main = df[df["is_reply"].astype(str) != "True"]
    med = main.groupby("channel")["engagement_score"].median().replace(0, 1)
    df["engagement_index"] = df.apply(
        lambda r: round(r["engagement_score"] / med.get(r["channel"], 1), 2) if r["is_reply"] != "True" else "", axis=1)
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
            agg = agg.sort_values("mean_index", ascending=False)
            c[name] = [{"label": r["label"], "n": int(r["n"]), "mean_index": round(float(r["mean_index"]), 2),
                        "median_views": (None if pd.isna(r["median_views"]) else int(r["median_views"]))}
                       for _, r in agg.rename(columns={key: "label"}).iterrows()]
        for attr in ("names_person", "names_partner"):
            t = g[g[attr].astype(str) == "True"]["engagement_index"].mean()
            f = g[g[attr].astype(str) != "True"]["engagement_index"].mean()
            c["by_attribute"][attr] = {"with": round(float(t), 2) if pd.notna(t) else None,
                                       "without": round(float(f), 2) if pd.notna(f) else None,
                                       "n_with": int((g[attr].astype(str) == "True").sum())}
        top = g.sort_values("engagement_index", ascending=False).head(3)
        c["top_posts"] = [{"date": r.date, "theme": r.theme, "format": r.format, "index": float(r.engagement_index),
                           "text": (r.text or "")[:140]} for r in top.itertuples()]
        c["bottom_themes"] = [t["label"] for t in c["by_theme"][-2:]] if len(c["by_theme"]) >= 2 else []
        out["channels"][ch] = c
    # experiment feedback: how did posts this system generated perform?
    exp = main[main["experiment_tag"].astype(str) != ""]
    out["experiments"] = [{"tag": r.experiment_tag, "channel": r.channel, "index": float(r.engagement_index), "date": r.date}
                          for r in exp.itertuples()]
    return out


MEMO_SYSTEM = """You write a one-page weekly insights memo for the Head of Investor Relations at Re7 Capital, an FCA-regulated institutional DeFi investment firm.
You are given per-channel statistics only. engagement_index is relative to each channel's median post (1.0 = typical; 5.0 = five times typical), so it is comparable across channels; raw likes are not.
Rules: lead with the single most important finding; give numbers with their sample sizes; call anything with n < 5 'directional'; name the best and worst theme and format per channel; report the person/partner attribute effect; if experiments exist, say how system-generated posts performed against their hypothesis; end with three concrete recommendations for next week's content, each tied to a number. Plain English, no hype, under 350 words, UK spelling. Markdown with short headings."""


def write_memo(st: dict) -> str:
    return llm.complete(MEMO_SYSTEM, json.dumps(st, ensure_ascii=False), max_tokens=1500, temperature=0.3)


def run(df: pd.DataFrame, live: bool = True) -> tuple[pd.DataFrame, dict, str, int]:
    labelled = 0
    df, _ = prelabel(df)
    if live:
        df, labelled = label_posts(df)
    df = compute_index(df)
    st = stats(df)
    memo = write_memo(st) if live else "(memo not regenerated: offline run)"
    os.makedirs(OUTPUT, exist_ok=True)
    with open(os.path.join(OUTPUT, "insights.json"), "w") as f:
        json.dump(st, f, indent=2)
    with open(os.path.join(OUTPUT, "memo.md"), "w") as f:
        f.write(memo)
    return df, st, memo, labelled


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
