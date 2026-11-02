"""Re7 Content Engine - dashboard with a Run button.

Always shows the last successful run. A live run is gated by a cooldown and a daily
ceiling so the button can be pressed by anyone without running up a bill; when a
live run is not available the page says so and serves the cached result.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta

import pandas as pd
import streamlit as st

from pipeline import store, persist, run as pipeline_run

GATE = os.path.join(store.OUTPUT, "gate.json")

st.set_page_config(page_title="Re7 Content Engine", page_icon=None, layout="wide")

# Streamlit Cloud injects secrets here; the pipeline reads the environment.
for k in ("ANTHROPIC_API_KEY", "APIFY_TOKEN", "GITHUB_TOKEN", "RE7_TELEGRAM_HANDLE", "APIFY_X_ACTOR", "APIFY_LINKEDIN_ACTOR", "RE7_COOLDOWN_MIN", "RE7_DAILY_CAP"):
    try:
        if k in st.secrets and st.secrets[k]:
            os.environ[k] = str(st.secrets[k])
    except Exception:  # noqa: BLE001 - no secrets file locally
        pass


COOLDOWN_MIN = int(os.environ.get("RE7_COOLDOWN_MIN", "3"))  # set RE7_COOLDOWN_MIN=15 in secrets for submission
DAILY_CAP = int(os.environ.get("RE7_DAILY_CAP", "6"))

# Restore the last committed state (data branch) on a fresh container.
try:
    persist.pull()
except Exception:  # noqa: BLE001
    pass


def friendly(df: pd.DataFrame) -> pd.DataFrame:
    """Rename analyst columns to plain English and format indices as multiples of a typical post."""
    d = df.copy()
    for col in ("mean_index", "index", "index with", "index without"):
        if col in d.columns:
            d[col] = d[col].apply(lambda v: "" if v is None or pd.isna(v) else f"{float(v):.1f}×")
    if "share_of_posts" in d.columns and d["share_of_posts"].dtype != object:
        d["share_of_posts"] = (d["share_of_posts"] * 100).round(0).astype(int).astype(str) + "%"
    if "share_of_engagement" in d.columns and d["share_of_engagement"].dtype != object:
        d["share_of_engagement"] = (d["share_of_engagement"] * 100).round(0).astype(int).astype(str) + "%"
    for col in ("theme", "format", "attribute"):
        if col in d.columns:
            d[col] = d[col].astype(str).str.replace("_", " ")
    if "directional" in d.columns:
        d["directional"] = d["directional"].apply(lambda v: "small sample" if v else "")
    if "median_views" in d.columns and d["median_views"].isna().all():
        d = d.drop(columns=["median_views"])
    return d.rename(columns={
        "label": "", "theme": "theme", "n": "posts", "mean_index": "vs a typical post", "index": "vs a typical post",
        "median_views": "typical views", "share_of_posts": "share of posts", "share_of_engagement": "share of engagement",
        "return_on_effort": "ratio", "verdict": "verdict", "directional": "confidence", "mentions": "mentions",
        "entity": "named in post", "median_views": "typical views", "likes_per_1000_views": "likes per 1,000 views",
        "index with": "when present", "index without": "when absent", "n with": "posts where present",
        "week_starting": "week starting", "day": "day", "mean_score": "score", "median_score": "typical score",
    })


def _gate() -> dict:
    if os.path.exists(GATE):
        return json.load(open(GATE))
    return {"runs": []}


def _save_gate(g: dict) -> None:
    os.makedirs(store.OUTPUT, exist_ok=True)
    json.dump(g, open(GATE, "w"))


def gate_status() -> tuple[bool, str, int]:
    """(allowed, reason, runs_left_today)"""
    now = datetime.now(timezone.utc)
    g = _gate()
    today = [datetime.fromisoformat(t) for t in g["runs"] if datetime.fromisoformat(t).date() == now.date()]
    left = max(0, DAILY_CAP - len(today))
    if today:
        last = max(today)
        if now - last < timedelta(minutes=COOLDOWN_MIN):
            nxt = (last + timedelta(minutes=COOLDOWN_MIN)).astimezone().strftime("%H:%M")
            return False, f"Last live run {int((now - last).total_seconds() // 60)} min ago; next available at {nxt}.", left
    if left == 0:
        return False, "Daily live-run limit reached; showing the most recent results.", 0
    return True, "", left


def record_run() -> None:
    g = _gate()
    g["runs"].append(datetime.now(timezone.utc).isoformat())
    g["runs"] = g["runs"][-50:]
    _save_gate(g)


def load_outputs():
    out = {}
    p = os.path.join(store.OUTPUT, "last_run.json")
    out["last_run"] = json.load(open(p)) if os.path.exists(p) else None
    p = os.path.join(store.OUTPUT, "insights.json")
    out["insights"] = json.load(open(p)) if os.path.exists(p) else None
    p = os.path.join(store.OUTPUT, "memo.md")
    out["memo"] = open(p).read() if os.path.exists(p) else ""
    p = os.path.join(store.OUTPUT, "content_queue.csv")
    try:
        out["queue"] = pd.read_csv(p) if os.path.exists(p) and os.path.getsize(p) > 0 else pd.DataFrame()
    except Exception:  # noqa: BLE001 - a malformed or empty queue file must not break the page
        out["queue"] = pd.DataFrame()
    try:
        out["posts"] = store.load()
    except Exception:  # noqa: BLE001
        out["posts"] = pd.DataFrame(columns=store.COLUMNS)
    return out


# ----------------------------------------------------------------------------- header
st.title("Re7 Content Engine")
st.caption("Collect Re7 Capital and Re7 Labs posts from X, LinkedIn and Telegram into one source; "
           "label themes and formats with Claude; find what performs on each channel; "
           "repurpose Re7's own research into next week's content. Built by Hamza Khan.")

allowed, reason, left = gate_status()
c1, c2, c3 = st.columns([1, 2, 2])
with c1:
    pressed = st.button("Run pipeline now", type="primary", disabled=not allowed, use_container_width=True)
with c2:
    if not allowed:
        st.info(reason)
    else:
        st.caption(f"Live runs remaining today: {left}. A run takes 1–3 minutes and costs roughly 20p in API usage.")
with c3:
    lr = load_outputs()["last_run"]
    if lr:
        st.caption(f"Last run: {lr.get('finished', '')[:16].replace('T', ' ')} UTC · {lr.get('mode')} · {lr.get('status')}")

if pressed and allowed:
    record_run()
    with st.status("Running the pipeline", expanded=True) as status:
        st.write("1. Collecting posts (seed, imports, Telegram, Apify for X and LinkedIn)")
        st.write("2. Labelling new posts against the taxonomy with Claude")
        st.write("3. Computing per-channel statistics and writing the insights memo")
        st.write("4. Generating next week's content from Re7's own sources")
        summary = pipeline_run.main(live=True)
        if summary.get("status") == "ok":
            status.update(label="Run complete", state="complete")
        else:
            status.update(label=f"Run finished with an error: {summary.get('error')}", state="error")
        st.json({k: v for k, v in summary.items() if k != "trace"})
    st.rerun()

data = load_outputs()
lr, ins, memo, queue, posts = data["last_run"], data["insights"], data["memo"], data["queue"], data["posts"]

if not lr:
    st.warning("No run yet. Press the button.")
    st.stop()

if lr.get("status") != "ok" or (lr.get("analyse") or {}).get("errors") or "error" in (lr.get("create") or {}):
    probs = []
    if lr.get("error"):
        probs.append(lr["error"])
    probs += (lr.get("analyse") or {}).get("errors", [])
    if "error" in (lr.get("create") or {}):
        probs.append("content generation: " + lr["create"]["error"])
    st.error("This run hit a problem: " + " | ".join(probs))

with st.expander("Run log (last_run.json)", expanded=False):
    st.json({k: v for k, v in lr.items() if k != "trace"})

# ----------------------------------------------------------------------------- collection status
with st.expander("Where this run's data came from", expanded=False):
    rows = []
    for name, r in (lr.get("collect") or {}).items():
        if not isinstance(r, dict):
            continue
        rows.append({"source": name, "status": r.get("status"), "fetched": r.get("fetched", ""), "new": r.get("new", ""),
                     "note": r.get("error", "")})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    pers = lr.get("persist") or {}
    if pers.get("status") == "ok":
        st.caption(f"Saved to GitHub branch 'data' ({len(pers.get('files', []))} files updated), so results survive restarts.")
    elif pers.get("status") == "skipped":
        st.caption("Not saved to GitHub (no GITHUB_TOKEN): results are kept only until the app restarts.")
    elif pers.get("status") == "failed":
        st.caption(f"Saving to GitHub failed: {pers.get('error')}")
    sup = (lr.get("collect") or {}).get("seed_rows_superseded_by_live")
    if sup:
        st.caption(f"{sup} manual seed rows were replaced by live rows describing the same posts.")
    st.caption("'seed' = manual captures from 1 Nov 2026, always merged first so the dashboard never depends on a live scraper. "
               "LinkedIn in production uses the Page admin analytics export (csv_import); a third-party collector is used here for the exercise.")

tab_memo, tab_stats, tab_queue, tab_data = st.tabs(["Insights memo", "What performs, by channel", "Next week's content", "Single source (all posts)"])

with tab_memo:
    st.markdown(memo or "_Memo is written on the first live run._")

with tab_stats:
    if ins:
        so = ins.get("second_order", {})
        names = {"x": "X", "linkedin": "LinkedIn", "telegram": "Telegram"}
        st.info(f"How to read these tables: every post is compared with a typical post on the same channel and account, so "
                f"\"3.0×\" means three times the engagement of a typical post there and \"0.5×\" means half. "
                f"Groups with fewer than {so.get('min_n_for_confidence', 3)} posts are marked as small samples.")

        st.subheader("Where the effort goes, and what it earns")
        st.caption("For each theme: what share of posts it takes up, what share of engagement it brings back, and whether that is a fair return.")
        cols = st.columns(len(so.get("effort_vs_return", {})) or 1)
        for col, (ch, rows) in zip(cols, so.get("effort_vs_return", {}).items()):
            with col:
                st.markdown(f"**{names.get(ch, ch)}**")
                d = pd.DataFrame(rows)
                if len(d):
                    st.dataframe(friendly(d)[["theme", "posts", "share of posts", "share of engagement", "verdict", "confidence"]],
                                 hide_index=True, use_container_width=True)

        cc = so.get("cross_channel", {})
        if cc.get("theme_by_channel"):
            st.subheader("Where the same content travels best")
            rows = []
            for theme, per in cc["theme_by_channel"].items():
                row = {"theme": theme}
                for ch, v in per.items():
                    row[names.get(ch, ch)] = f"{v['mean_index']:.1f}× ({v['n']} posts)"
                rows.append(row)
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            if cc.get("editions"):
                st.caption("Recent pieces posted on more than one channel, index per channel:")
                st.dataframe(pd.DataFrame([{"piece": e["content"], "date": e["date"],
                                            **{names.get(k, k): f"{v:.1f}×" for k, v in e["by_channel"].items()}} for e in cc["editions"]]),
                             hide_index=True, use_container_width=True)

        if so.get("who_drives_it"):
            st.subheader("Who moves the needle")
            st.caption("People and firms named in posts, ranked by how the posts that name them performed (named at least twice).")
            st.dataframe(friendly(pd.DataFrame(so["who_drives_it"])), hide_index=True, use_container_width=True)

        if so.get("reach_vs_resonance_x"):
            st.subheader("Reach versus resonance on X")
            st.caption("Views show reach; likes per 1,000 views show whether the people who saw it cared.")
            st.dataframe(friendly(pd.DataFrame([{"theme": k, "posts": v["n"], "typical views": v["median_views"],
                                                  "likes per 1,000 views": v["likes_per_1000_views"]} for k, v in so["reach_vs_resonance_x"].items()])),
                         hide_index=True, use_container_width=True)

        if so.get("weekly_trend"):
            st.subheader("Trend, last eight weeks")
            st.caption("Average performance of each week's posts against a typical post (1.0×).")
            frames = []
            for ch, rows in so["weekly_trend"].items():
                for r in rows:
                    frames.append({"week": r["week_starting"], "channel": names.get(ch, ch), "mean_index": r["mean_index"]})
            if frames:
                t = pd.DataFrame(frames).pivot(index="week", columns="channel", values="mean_index")
                st.line_chart(t)

        if so.get("day_of_week"):
            st.subheader("Day of week")
            cols = st.columns(len(so["day_of_week"]) or 1)
            for col, (ch, rows) in zip(cols, so["day_of_week"].items()):
                with col:
                    st.markdown(f"**{names.get(ch, ch)}**")
                    st.dataframe(friendly(pd.DataFrame(rows)), hide_index=True, use_container_width=True)

        st.subheader("Best and worst posts")
        for ch, ex in so.get("extremes", {}).items():
            with st.expander(names.get(ch, ch)):
                st.markdown("**Top**")
                st.dataframe(friendly(pd.DataFrame(ex["top"])), hide_index=True, use_container_width=True)
                st.markdown("**Bottom**")
                st.dataframe(friendly(pd.DataFrame(ex["bottom"])), hide_index=True, use_container_width=True)

        st.subheader("Full breakdown by channel")
        for ch, c in ins["channels"].items():
            with st.expander(f"{names.get(ch, ch)} · {c['posts']} labelled posts"):
                a, b = st.columns(2)
                with a:
                    st.markdown("**By theme**")
                    st.dataframe(friendly(pd.DataFrame(c["by_theme"])), hide_index=True, use_container_width=True)
                with b:
                    st.markdown("**By format**")
                    st.dataframe(friendly(pd.DataFrame(c["by_format"])), hide_index=True, use_container_width=True)
                if c.get("by_account"):
                    st.markdown("**By account**")
                    st.dataframe(friendly(pd.DataFrame(c["by_account"])), hide_index=True, use_container_width=True)
                attr = c.get("by_attribute", {})
                if attr:
                    st.markdown("**Does naming a person or a partner matter?**")
                    st.dataframe(friendly(pd.DataFrame([{"attribute": k.replace("names_", "names a "), "index with": v["with"], "index without": v["without"], "n with": v["n_with"]}
                                               for k, v in attr.items()])), hide_index=True, use_container_width=True)
        if ins.get("experiments"):
            st.subheader("How this system's own posts performed")
            st.dataframe(pd.DataFrame(ins["experiments"]), hide_index=True, use_container_width=True)
        else:
            st.caption("No system-generated posts have been published and collected yet. Once the queue is used, each post is scored here against its hypothesis.")

with tab_queue:
    src_p = os.path.join(store.OUTPUT, "sources_cache.json")
    if os.path.exists(src_p):
        try:
            srcs = json.load(open(src_p))
            st.caption("Repurposed from: " + " · ".join(f"[{x.get('title','')[:60]}]({x.get('url','')})" for x in srcs[:5]))
        except Exception:  # noqa: BLE001
            pass
    if len(queue):
        st.caption("Seven drafts for human review, built from this week's sources and the insights above. Posts with a compliance flag need "
                   "sign-off before scheduling. Each carries the finding it acts on; once published and collected next week it is scored against it. "
                   "Nothing already posted in the last three weeks is re-announced unless it is reframed and labelled as a re-promotion.")
        for _, r in queue.iterrows():
            with st.container(border=True):
                st.markdown(f"**{r.get('day', '')} {r.get('time_uk', '')} · {r.get('account', '')} · {str(r.get('channel', '')).upper()} · {r.get('format', '')}**")
                st.write(r.get("text", ""))
                src = f"[{r.get('source_title', '')}]({r.get('source_url', '')})" if isinstance(r.get("source_url"), str) and r.get("source_url") else "none"
                st.caption(f"Why this post: {r.get('hypothesis', '')}")
                st.caption(f"Source: {src} · Tag: `{r.get('experiment_tag', '')}`")
                if isinstance(r.get("distribution_note"), str) and r["distribution_note"]:
                    st.caption(f"Distribution: {r['distribution_note']}")
                if isinstance(r.get("repromotion"), str) and r["repromotion"]:
                    st.caption(f"Re-promotion: {r['repromotion']}")
                if isinstance(r.get("compliance_flag"), str) and r["compliance_flag"]:
                    st.warning(f"Compliance review: {r['compliance_flag']}")
        st.download_button("Download queue as CSV", queue.to_csv(index=False), "content_queue.csv", "text/csv")
    else:
        st.caption("Content is generated on the first live run.")

with tab_data:
    show = posts.copy()
    st.caption(f"{len(show)} posts across {show['channel'].nunique()} channels. This table is data/posts.csv, the single source every step reads.")
    st.dataframe(show[["channel", "account", "date", "format", "theme", "likes", "reposts", "comments", "views",
                       "engagement_index", "names_person", "names_partner", "collected_via", "text", "url"]],
                 hide_index=True, use_container_width=True, height=500)
    st.download_button("Download posts.csv", show.to_csv(index=False), "posts.csv", "text/csv")
