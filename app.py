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

from pipeline import store, run as pipeline_run

COOLDOWN_MIN = int(os.environ.get("RE7_COOLDOWN_MIN", "15"))
DAILY_CAP = int(os.environ.get("RE7_DAILY_CAP", "6"))
GATE = os.path.join(store.OUTPUT, "gate.json")

st.set_page_config(page_title="Re7 Content Engine", page_icon=None, layout="wide")

# Streamlit Cloud injects secrets here; the pipeline reads the environment.
for k in ("ANTHROPIC_API_KEY", "APIFY_TOKEN", "RE7_TELEGRAM_HANDLE", "APIFY_X_ACTOR", "APIFY_LINKEDIN_ACTOR"):
    try:
        if k in st.secrets and st.secrets[k]:
            os.environ[k] = str(st.secrets[k])
    except Exception:  # noqa: BLE001 - no secrets file locally
        pass


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
    out["queue"] = pd.read_csv(p) if os.path.exists(p) else pd.DataFrame()
    out["posts"] = store.load()
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

# ----------------------------------------------------------------------------- collection status
with st.expander("Where this run's data came from", expanded=False):
    rows = []
    for name, r in (lr.get("collect") or {}).items():
        rows.append({"source": name, "status": r.get("status"), "fetched": r.get("fetched", ""), "new": r.get("new", ""),
                     "note": r.get("error", "")})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption("'seed' = manual captures from 1 Nov 2026, always merged first so the dashboard never depends on a live scraper. "
               "LinkedIn in production uses the Page admin analytics export (csv_import); a third-party collector is used here for the exercise.")

tab_memo, tab_stats, tab_queue, tab_data = st.tabs(["Insights memo", "What performs, by channel", "Next week's content", "Single source (all posts)"])

with tab_memo:
    st.markdown(memo or "_Memo is written on the first live run._")

with tab_stats:
    if ins:
        for ch, c in ins["channels"].items():
            st.subheader({"x": "X", "linkedin": "LinkedIn", "telegram": "Telegram"}.get(ch, ch))
            st.caption(f"{c['posts']} labelled posts · engagement index is relative to this channel's median post (1.0 = typical)")
            a, b = st.columns(2)
            with a:
                st.markdown("**By theme**")
                st.dataframe(pd.DataFrame(c["by_theme"]), hide_index=True, use_container_width=True)
            with b:
                st.markdown("**By format**")
                st.dataframe(pd.DataFrame(c["by_format"]), hide_index=True, use_container_width=True)
            attr = c.get("by_attribute", {})
            if attr:
                st.markdown("**Does naming a person or a partner matter?**")
                st.dataframe(pd.DataFrame([{"attribute": k, "index with": v["with"], "index without": v["without"], "n with": v["n_with"]}
                                           for k, v in attr.items()]), hide_index=True, use_container_width=True)
        if ins.get("experiments"):
            st.subheader("How this system's own posts performed")
            st.dataframe(pd.DataFrame(ins["experiments"]), hide_index=True, use_container_width=True)
        else:
            st.caption("No system-generated posts have been published and collected yet. Once the queue is used, each post is scored here against its hypothesis.")

with tab_queue:
    if len(queue):
        st.caption("Drafts for human review. Posts with a compliance flag need sign-off before scheduling. "
                   "Each carries the hypothesis it tests; when published and collected next week it is scored against it.")
        for _, r in queue.iterrows():
            with st.container(border=True):
                st.markdown(f"**{r.get('day', '')} {r.get('time_uk', '')} · {r.get('account', '')} · {str(r.get('channel', '')).upper()} · {r.get('format', '')}**")
                st.write(r.get("text", ""))
                meta = f"Source: [{r.get('source_title', '')}]({r.get('source_url', '')}) · Hypothesis: {r.get('hypothesis', '')} · Tag: `{r.get('experiment_tag', '')}`"
                st.caption(meta)
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
