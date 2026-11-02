"""Second-order statistics: the views that turn 'what scored well' into 'what to change'.

All functions take the labelled, indexed posts frame and return plain dicts/lists for
insights.json, the memo prompt and the dashboard.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd

MIN_N = 3  # below this a group is reported as directional


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def main_posts(df: pd.DataFrame) -> pd.DataFrame:
    m = df[(df["is_reply"].astype(str) != "True") & (df["theme"] != "")].copy()
    m["engagement_index"] = _num(m["engagement_index"])
    m["engagement_score"] = _num(m["engagement_score"]).fillna(0)
    m["views"] = _num(m["views"])
    m["likes"] = _num(m["likes"])
    return m


# ---------------------------------------------------------------- content keys (same piece across channels)
def content_key(text: str) -> str:
    t = (text or "").replace("\n", " ")
    for rx in (r"weekly is out:\s*(.+?)(?:\s+(?:in this week|we breakdown|we break down|read the full|full article)|\s*🔹|$)",
               r"new report\s*[—:–-]\s*(.+?)(?:\s+as defi|\s+institutional interest|\s*🔹|$)",
               r"new episode of .+?:\s*(.+?)(?:\s+avi|\s*▶|$)"):
        m = re.search(rx, t, re.I)
        if m:
            return re.sub(r"[^a-z0-9 ]", "", m.group(1).lower()).strip()[:80]
    return ""


def assign_content_keys(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["content_key"] = df["text"].apply(content_key)
    return df


# ---------------------------------------------------------------- effort vs return
def effort_vs_return(m: pd.DataFrame) -> dict:
    out = {}
    for ch, g in m.groupby("channel"):
        total_posts, total_score = len(g), g["engagement_score"].sum() or 1
        rows = []
        for theme, t in g.groupby("theme"):
            share_posts = len(t) / total_posts
            share_eng = t["engagement_score"].sum() / total_score
            rows.append({"theme": theme, "n": int(len(t)), "share_of_posts": round(share_posts, 3),
                         "share_of_engagement": round(share_eng, 3),
                         "return_on_effort": round(share_eng / share_posts, 2) if share_posts else None,
                         "directional": len(t) < MIN_N})
        rows.sort(key=lambda r: (-r["n"] >= MIN_N, -(r["return_on_effort"] or 0)))
        out[ch] = sorted(rows, key=lambda r: -(r["return_on_effort"] or 0))
    return out


# ---------------------------------------------------------------- the same piece on different channels
def cross_channel(m: pd.DataFrame) -> dict:
    keyed = m[m["content_key"] != ""]
    editions = []
    for key, g in keyed.groupby("content_key"):
        if g["channel"].nunique() < 2:
            continue
        editions.append({"content": key, "date": g["date"].max(),
                         "by_channel": {ch: round(float(x["engagement_index"].mean()), 2) for ch, x in g.groupby("channel")}})
    editions.sort(key=lambda e: e["date"], reverse=True)
    # where does each theme travel best? mean index per channel for themes posted on 2+ channels
    theme_travel = {}
    for theme, g in m.groupby("theme"):
        if g["channel"].nunique() >= 2:
            theme_travel[theme] = {ch: {"mean_index": round(float(x["engagement_index"].mean()), 2), "n": int(len(x))}
                                   for ch, x in g.groupby("channel")}
    return {"editions": editions[:8], "theme_by_channel": theme_travel}


# ---------------------------------------------------------------- who drives it
def entity_leaderboard(m: pd.DataFrame, top: int = 10) -> list[dict]:
    rows = []
    for r in m.itertuples():
        for e in str(r.entities or "").split(";"):
            e = e.strip()
            if e:
                rows.append({"entity": e, "channel": r.channel, "index": r.engagement_index})
    if not rows:
        return []
    e = pd.DataFrame(rows)
    agg = e.groupby("entity").agg(mentions=("index", "count"), mean_index=("index", "mean")).reset_index()
    agg = agg[agg["mentions"] >= 2].sort_values("mean_index", ascending=False).head(top)
    return [{"entity": r.entity, "mentions": int(r.mentions), "mean_index": round(float(r.mean_index), 2)} for r in agg.itertuples()]


# ---------------------------------------------------------------- reach vs resonance (X has views)
def reach_vs_resonance(m: pd.DataFrame) -> dict:
    x = m[(m["channel"] == "x") & m["views"].notna() & (m["views"] > 0)].copy()
    if x.empty:
        return {}
    x["like_rate"] = x["likes"] / x["views"]
    out = {}
    for theme, g in x.groupby("theme"):
        out[theme] = {"n": int(len(g)), "median_views": int(g["views"].median()),
                      "likes_per_1000_views": round(float(g["like_rate"].mean() * 1000), 1)}
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["median_views"]))


# ---------------------------------------------------------------- trend and timing
def weekly_trend(m: pd.DataFrame, weeks: int = 8) -> dict:
    m = m.copy()
    m["d"] = pd.to_datetime(m["date"], errors="coerce")
    m = m[m["d"].notna()]
    m["week"] = m["d"].dt.to_period("W-SUN").apply(lambda p: p.start_time.date().isoformat())
    cutoff = (date.today() - timedelta(weeks=weeks)).isoformat()
    m = m[m["week"] >= cutoff]
    out = {}
    for ch, g in m.groupby("channel"):
        wk = g.groupby("week").agg(posts=("post_id", "count"), mean_index=("engagement_index", "mean")).reset_index()
        out[ch] = [{"week_starting": r.week, "posts": int(r.posts), "mean_index": round(float(r.mean_index), 2)} for r in wk.itertuples()]
    return out


def day_of_week(m: pd.DataFrame) -> dict:
    m = m.copy()
    m["d"] = pd.to_datetime(m["date"], errors="coerce")
    m = m[m["d"].notna() & (m["date_precision"].astype(str) != "approx")]
    m["dow"] = m["d"].dt.day_name()
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    out = {}
    for ch, g in m.groupby("channel"):
        agg = g.groupby("dow").agg(n=("post_id", "count"), mean_index=("engagement_index", "mean"))
        out[ch] = [{"day": d, "n": int(agg.loc[d, "n"]), "mean_index": round(float(agg.loc[d, "mean_index"]), 2)}
                   for d in order if d in agg.index]
    return out


# ---------------------------------------------------------------- top and bottom, with text
def extremes(m: pd.DataFrame, k: int = 3) -> dict:
    out = {}
    for ch, g in m.groupby("channel"):
        g = g.sort_values("engagement_index", ascending=False)
        pick = lambda rows: [{"date": r.date, "theme": r.theme, "format": r.format, "index": float(r.engagement_index),
                              "views": (None if pd.isna(r.views) else int(r.views)), "text": (r.text or "")[:160]} for r in rows.itertuples()]
        out[ch] = {"top": pick(g.head(k)), "bottom": pick(g.tail(k))}
    return out


def build(m: pd.DataFrame) -> dict:
    m = m.copy()
    for c in ("engagement_index", "engagement_score", "views", "likes"):
        m[c] = _num(m[c])
    m["engagement_score"] = m["engagement_score"].fillna(0)
    return {
        "effort_vs_return": effort_vs_return(m),
        "cross_channel": cross_channel(m),
        "who_drives_it": entity_leaderboard(m),
        "reach_vs_resonance_x": reach_vs_resonance(m),
        "weekly_trend": weekly_trend(m),
        "day_of_week": day_of_week(m),
        "extremes": extremes(m),
        "min_n_for_confidence": MIN_N,
    }
