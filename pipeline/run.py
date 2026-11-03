"""Orchestrator: collect -> single source -> analyse -> create -> outputs.

    python -m pipeline.run            # full live run (needs ANTHROPIC_API_KEY; APIFY_TOKEN optional)
    python -m pipeline.run --offline  # no network, no API: seed + cached data, stats only

Every stage records what it did in output/last_run.json so the dashboard can show
exactly which sources were live and which fell back.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import store, analyse, create, persist
from .collect import telegram, apify, csv_import, x_public

REUSE_HOURS = float(os.environ.get("RE7_REUSE_HOURS", "12"))  # paid scrapes younger than this are reused
STATE_PATH = os.path.join(store.OUTPUT, "collect_state.json")


def _state() -> dict:
    try:
        return json.load(open(STATE_PATH))
    except Exception:  # noqa: BLE001
        return {}


def _mark(name: str) -> None:
    st = _state()
    st[name] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    os.makedirs(store.OUTPUT, exist_ok=True)
    json.dump(st, open(STATE_PATH, "w"))


def _age_hours(name: str) -> float | None:
    t = _state().get(name)
    if not t:
        return None
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(t)).total_seconds() / 3600
    except ValueError:
        return None


CONFIG = {
    "x_handles": {"Re7Capital": "Re7 Capital", "Re7Labs": "Re7 Labs"},
    "linkedin_pages": {"https://www.linkedin.com/company/re7capital/": "Re7 Capital"},
    "telegram_handle": os.environ.get("RE7_TELEGRAM_HANDLE", "re7labsalpha"),  # public Re7 Labs Alpha channel
}


def _noop(stage: str, detail: str) -> None:
    pass


def collect_all(df, live: bool, progress=_noop) -> tuple[object, dict]:
    """Run every collector; the network ones run in parallel, then merge in a fixed order."""
    report: dict = {}

    def fetch(name, fn):
        t0 = time.time()
        try:
            return name, fn(), None, round(time.time() - t0, 1)
        except Exception as e:  # noqa: BLE001 - a failed collector must not stop the run
            return name, [], str(e)[:200], round(time.time() - t0, 1)

    def absorb(name, posts, err, secs):
        nonlocal df
        if err is not None:
            report[name] = {"status": "failed", "error": err, "seconds": secs}
            progress("collect", f"{name}: failed ({err[:60]})")
            return
        df, new = store.merge(df, posts)
        report[name] = {"status": "ok", "fetched": len(posts), "new": new, "seconds": secs}
        if posts and name.split(":")[0] in ("x", "linkedin"):
            _mark(name)
        progress("collect", f"{name}: {len(posts)} posts, {new} new")

    def fresh(name, channel, account) -> bool:
        """True when a paid scrape for this source is recent enough to reuse the saved posts."""
        age = _age_hours(name)
        if age is None or age > REUSE_HOURS:
            return False
        have = int(((df["channel"] == channel) & (df["account"] == account) & (df["collected_via"] != "seed")).sum()) if len(df) else 0
        if have == 0:
            return False
        report[name] = {"status": "reused", "fetched": have, "new": 0, "seconds": 0.0,
                        "error": f"fetched {age:.1f}h ago; reused to save scraper credit (refreshes after {REUSE_HOURS:g}h)"}
        progress("collect", f"{name}: {have} posts, fetched {age:.1f}h ago, reused")
        return True

    # Seed (manual captures from 1 Nov 2026) is always merged first: it is the floor.
    absorb(*fetch("seed", store.load_seed))
    # Admin exports dropped in data/imports/ (production path for LinkedIn).
    absorb(*fetch("csv_import", lambda: csv_import.collect("linkedin")))
    if not live:
        return df, report

    jobs = []
    if CONFIG["telegram_handle"]:
        jobs.append(("telegram", lambda: telegram.collect(CONFIG["telegram_handle"], account="Re7 Labs")))
    else:
        report["telegram"] = {"status": "skipped", "error": "RE7_TELEGRAM_HANDLE not set"}
    have_apify = bool(os.environ.get("APIFY_TOKEN"))

    def x_job(h, acct):
        """Apify first; public syndication only if that fails. Returns a list of (name, result) rows."""
        rows = []
        if have_apify:
            r = fetch(f"x:{h}", lambda: apify.collect_x(h, acct))
            rows.append(r)
            if r[2] is None:
                return rows
        rows.append(fetch(f"x:{h}:public", lambda: x_public.collect(h, acct)))
        return rows

    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(fetch, n, f) for n, f in jobs]
        xfuts = [ex.submit(x_job, h, a) for h, a in CONFIG["x_handles"].items() if not fresh(f"x:{h}", "x", a)]
        if have_apify:
            futs += [ex.submit(fetch, "linkedin:apify", lambda u=u, a=a: apify.collect_linkedin(u, a))
                     for u, a in CONFIG["linkedin_pages"].items() if not fresh("linkedin:apify", "linkedin", a)]
        else:
            report["apify"] = {"status": "skipped", "error": "APIFY_TOKEN not set; using seed/imports"}
        results = [f.result() for f in futs]
        for xf in xfuts:
            results += xf.result()
    # merge in a stable order so post_ids and supersession behave the same every run
    for name, posts, err, secs in sorted(results, key=lambda r: r[0]):
        absorb(name, posts, err, secs)
    return df, report


def main(live: bool = True, progress=_noop) -> dict:
    """progress(stage, detail) is called from the main thread as each step finishes."""
    started = datetime.now(timezone.utc)
    summary: dict = {"started": started.isoformat(timespec="seconds"), "mode": "live" if live else "offline"}
    progress("start", "loading the last run's data")
    summary["pull"] = persist.pull()
    df = store.load()
    try:
        df, summary["collect"] = collect_all(df, live, progress)
        df, superseded = store.dedupe_seed(df)
        summary["collect"]["seed_rows_superseded_by_live"] = superseded
        store.save(df)
        summary["posts_total"] = int(len(df))
        progress("collect", f"single source now holds {len(df)} posts")

        to_label = int(((df["theme"] == "") | (df["label_source"] == "heuristic")).sum()) if live else 0
        progress("analyse", f"labelling {to_label} new posts with Claude" if to_label else "nothing new to label")
        df, insights, _, labelled, errors = analyse.run(df, live=live, with_memo=False)
        store.save(df)
        summary["analyse"] = {"labelled_now": labelled,
                              "labelled_total": int((df["theme"] != "").sum()),
                              "channels": {k: v["posts"] for k, v in insights["channels"].items()},
                              "errors": errors}
        progress("analyse", "statistics computed; writing the memo and drafting content (in parallel)")

        with ThreadPoolExecutor(max_workers=2) as ex:
            f_memo = ex.submit(analyse.finish_memo, insights, live, errors)
            f_create = ex.submit(create.run, df, insights, live)
            memo = f_memo.result()
            progress("analyse", "memo written" if memo else "memo failed; kept the previous one")
            try:
                queue = f_create.result()
                summary["create"] = {"posts_generated": int(len(queue)),
                                     "flagged_for_compliance": int((queue["compliance_flag"] != "").sum()) if len(queue) else 0}
                from . import llm
                if llm.WARNINGS:
                    summary["create"]["warnings"] = [w[:200] for w in llm.WARNINGS]
                    llm.WARNINGS.clear()
                progress("create", f"{len(queue)} draft posts ready")
            except Exception as e:  # noqa: BLE001
                summary["create"] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
                progress("create", f"content drafting failed: {str(e)[:80]}")
        summary["status"] = "ok"
    except Exception as e:  # noqa: BLE001
        summary["status"] = "failed"
        summary["error"] = f"{type(e).__name__}: {e}"
        summary["trace"] = traceback.format_exc()[-1500:]
    summary["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    summary["seconds"] = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
    os.makedirs(store.OUTPUT, exist_ok=True)
    with open(os.path.join(store.OUTPUT, "last_run.json"), "w") as f:
        json.dump(summary, f, indent=2)
    # append to run history
    hist = os.path.join(store.OUTPUT, "run_history.jsonl")
    with open(hist, "a") as f:
        f.write(json.dumps({k: v for k, v in summary.items() if k != "trace"}) + "\n")
    progress("persist", "saving results")
    summary["persist"] = persist.push(f"run {summary['started'][:16]}")
    with open(os.path.join(store.OUTPUT, "last_run.json"), "w") as f:
        json.dump(summary, f, indent=2)
    if summary["persist"].get("status") == "ok":
        persist.push_one("output/last_run.json", f"run {summary['started'][:16]}: log")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="no network or API calls")
    args = ap.parse_args()
    s = main(live=not args.offline)
    print(json.dumps(s, indent=2))
