"""Import a LinkedIn Page admin analytics export (or any CSV with the standard columns).

LinkedIn Page admins can export post-level analytics from the page's Analytics > Content tab.
This is the production path for LinkedIn; it needs no scraping and gives impressions, which
no public route does. Drop the file in data/imports/ and the orchestrator picks it up.
"""
from __future__ import annotations

import glob
import os

import pandas as pd

from ..schema import Post
from ..store import DATA

IMPORTS = os.path.join(DATA, "imports")

# Accept either our own column names or LinkedIn's export headings.
ALIASES = {
    "date": ["date", "created date", "post date", "created_at"],
    "url": ["url", "post link", "post url"],
    "text": ["text", "post title", "update title", "commentary"],
    "likes": ["likes", "reactions", "likes (total)"],
    "reposts": ["reposts", "shares", "reposts (total)"],
    "comments": ["comments", "comments (total)"],
    "views": ["views", "impressions", "impressions (total)"],
    "format": ["format", "content type", "post type"],
}


def _col(df: pd.DataFrame, key: str):
    low = {c.lower().strip(): c for c in df.columns}
    for a in ALIASES[key]:
        if a in low:
            return low[a]
    return None


def collect(channel: str = "linkedin", account: str = "Re7 Capital") -> list[Post]:
    posts: list[Post] = []
    for path in glob.glob(os.path.join(IMPORTS, f"{channel}*.csv")) + glob.glob(os.path.join(IMPORTS, f"{channel}*.xlsx")):
        df = pd.read_excel(path) if path.endswith(".xlsx") else pd.read_csv(path)
        cols = {k: _col(df, k) for k in ALIASES}
        for _, r in df.iterrows():
            d = str(r[cols["date"]])[:10] if cols["date"] else ""
            if not d or d == "nan":
                continue
            posts.append(Post(channel=channel, account=account, date=d,
                              url=str(r[cols["url"]]) if cols["url"] else "",
                              text=str(r[cols["text"]]) if cols["text"] and str(r[cols["text"]]) != "nan" else "",
                              format=str(r[cols["format"]]).lower() if cols["format"] else "link",
                              likes=r[cols["likes"]] if cols["likes"] else None,
                              reposts=r[cols["reposts"]] if cols["reposts"] else None,
                              comments=r[cols["comments"]] if cols["comments"] else None,
                              views=r[cols["views"]] if cols["views"] else None,
                              collected_via="csv_import"))
    return posts
