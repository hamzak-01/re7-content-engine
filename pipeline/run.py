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
from datetime import datetime, timezone

from . import store, analyse, create, persist
from .collect import telegram, apify, csv_import, x_public

CONFIG = {
    "x_handles": {"Re7Capital": "Re7 Capital", "Re7Labs": "Re7 Labs"},
    "linkedin_pages": {"https://www.linkedin.com/company/re7capital/": "Re7 Capital"},
    "telegram_handle": os.environ.get("RE7_TELEGRAM_HANDLE", "re7labsalpha"),  # public Re7 Labs Alpha channel
}


def collect_all(df, live: bool) -> tuple[object, dict]:
    report: dict = {}

    def attempt(name, fn):
        t0 = time.time()
        try:
            posts = fn()
            nonlocal df
            df, new = store.merge(df, posts)
            report[name] = {"status": "ok", "fetched": len(posts), "new": new, "seconds": round(time.time() - t0, 1)}
        except Exception as e:  # noqa: BLE001 - a failed collector must not stop the run
            report[name] = {"status": "failed", "error": str(e)[:200], "seconds": round(time.time() - t0, 1)}

    # Seed (manual captures from 1 Nov 2026) is always merged first: it is the floor.
    attempt("seed", store.load_seed)
    # Admin exports dropped in data/imports/ (production path for LinkedIn).
    attempt("csv_import", lambda: csv_import.collect("linkedin"))
    if live:
        if CONFIG["telegram_handle"]:
            attempt("telegram", lambda: telegram.collect(CONFIG["telegram_handle"], account="Re7 Labs"))
        else:
            report["telegram"] = {"status": "skipped", "error": "RE7_TELEGRAM_HANDLE not set"}
        for h, acct in CONFIG["x_handles"].items():
            if os.environ.get("APIFY_TOKEN"):
                attempt(f"x:{h}", lambda h=h, acct=acct: apify.collect_x(h, acct))
            if report.get(f"x:{h}", {}).get("status") != "ok":
                attempt(f"x:{h}:public", lambda h=h, acct=acct: x_public.collect(h, acct))
        if os.environ.get("APIFY_TOKEN"):
            for u, acct in CONFIG["linkedin_pages"].items():
                attempt("linkedin:apify", lambda u=u, acct=acct: apify.collect_linkedin(u, acct))
        else:
            report["apify"] = {"status": "skipped", "error": "APIFY_TOKEN not set; using seed/imports"}
    return df, report


def main(live: bool = True) -> dict:
    started = datetime.now(timezone.utc)
    summary: dict = {"started": started.isoformat(timespec="seconds"), "mode": "live" if live else "offline"}
    summary["pull"] = persist.pull()
    df = store.load()
    try:
        df, summary["collect"] = collect_all(df, live)
        df, superseded = store.dedupe_seed(df)
        summary["collect"]["seed_rows_superseded_by_live"] = superseded
        store.save(df)
        summary["posts_total"] = int(len(df))

        df, insights, memo, labelled, errors = analyse.run(df, live=live)
        store.save(df)
        summary["analyse"] = {"labelled_now": labelled,
                              "labelled_total": int((df["theme"] != "").sum()),
                              "channels": {k: v["posts"] for k, v in insights["channels"].items()},
                              "errors": errors}

        try:
            queue = create.run(df, insights, live=live)
            summary["create"] = {"posts_generated": int(len(queue)),
                                 "flagged_for_compliance": int((queue["compliance_flag"] != "").sum()) if len(queue) else 0}
        except Exception as e:  # noqa: BLE001
            summary["create"] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
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
