"""The single source: data/posts.csv. Load, merge new rows (by post_id), save."""
from __future__ import annotations

import os
import pandas as pd

from .schema import COLUMNS, Post

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SEED = os.path.join(DATA, "seed")
OUTPUT = os.path.join(ROOT, "output")
POSTS_CSV = os.path.join(DATA, "posts.csv")


def load() -> pd.DataFrame:
    if os.path.exists(POSTS_CSV):
        df = pd.read_csv(POSTS_CSV, dtype=str, keep_default_na=False)
    else:
        df = pd.DataFrame(columns=COLUMNS)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df[COLUMNS]


def save(df: pd.DataFrame) -> None:
    os.makedirs(DATA, exist_ok=True)
    df = df[COLUMNS].copy()
    df = df.sort_values(["channel", "date"], ascending=[True, False])
    df.to_csv(POSTS_CSV, index=False)


def merge(df: pd.DataFrame, posts: list[Post]) -> tuple[pd.DataFrame, int]:
    """Add posts not already present (by post_id); refresh engagement on existing ones.
    Returns (df, number_of_new_rows)."""
    if not posts:
        return df, 0
    new = pd.DataFrame([p.row() for p in posts]).astype(str)
    existing = set(df["post_id"])
    fresh = new[~new["post_id"].isin(existing)]
    # update engagement counts on rows we already hold (numbers grow over time)
    upd = new[new["post_id"].isin(existing)].set_index("post_id")
    if len(upd):
        df = df.set_index("post_id")
        for col in ("likes", "reposts", "comments", "views", "engagement_score"):
            df.loc[upd.index, col] = upd[col]
        df = df.reset_index()
    df = pd.concat([df, fresh], ignore_index=True)
    return df[COLUMNS], len(fresh)


def load_seed() -> list[Post]:
    """Hamza's manual captures (1 Nov 2026) - the fallback when live collectors are unavailable."""
    posts: list[Post] = []
    xp = os.path.join(SEED, "x_re7capital_2026-11-01.csv")
    if os.path.exists(xp):
        for _, r in pd.read_csv(xp, dtype=str, keep_default_na=False).iterrows():
            posts.append(Post(channel="x", account="Re7 Capital", date=r["date"], text=r["text"],
                              format=r["format"], likes=r["likes"], reposts=r["reposts"],
                              comments=r["comments"], views=r["views"] or None,
                              is_reply=r["is_reply"] == "True", collected_via="seed"))
    lp = os.path.join(SEED, "linkedin_re7capital_2026-11-01.csv")
    if os.path.exists(lp):
        for _, r in pd.read_csv(lp, dtype=str, keep_default_na=False).iterrows():
            posts.append(Post(channel="linkedin", account="Re7 Capital", date=r["date"],
                              date_precision=r["date_precision"], url=r["url"], text=r["text"],
                              format="video" if "Crypto Minutes" in r["text"] and "episode" in r["text"].lower() else "link",
                              likes=r["likes"], reposts=r["reposts"], comments=r["comments"],
                              collected_via="seed"))
    return posts
