"""Durable storage for the single source and outputs.

Streamlit Community Cloud wipes the app's disk on every redeploy or reboot, so the
collected data would vanish. With a GitHub token in the environment, every run is
committed to the `data` branch of the repository (not `main`, so it never triggers a
redeploy), and the app pulls that branch on start-up. Without a token the app still
works; it just starts from the repository's committed state each time.
"""
from __future__ import annotations

import base64
import json
import os

import requests

from .store import ROOT

REPO = os.environ.get("RE7_GITHUB_REPO", "hamzak-01/re7-content-engine")
BRANCH = os.environ.get("RE7_DATA_BRANCH", "data")
FILES = ["data/posts.csv", "output/last_run.json", "output/insights.json", "output/memo.md",
         "output/content_queue.csv", "output/run_history.jsonl", "output/sources_cache.json"]
API = "https://api.github.com"


def _headers():
    tok = os.environ.get("GITHUB_TOKEN", "")
    h = {"Accept": "application/vnd.github+json", "User-Agent": "re7-content-engine"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _ensure_branch() -> None:
    r = requests.get(f"{API}/repos/{REPO}/git/ref/heads/{BRANCH}", headers=_headers(), timeout=20)
    if r.status_code == 200:
        return
    main = requests.get(f"{API}/repos/{REPO}/git/ref/heads/main", headers=_headers(), timeout=20)
    main.raise_for_status()
    sha = main.json()["object"]["sha"]
    requests.post(f"{API}/repos/{REPO}/git/refs", headers=_headers(),
                  json={"ref": f"refs/heads/{BRANCH}", "sha": sha}, timeout=20).raise_for_status()


def push(message: str = "pipeline run") -> dict:
    """Commit the data and output files to the data branch. Returns a small report."""
    if not os.environ.get("GITHUB_TOKEN"):
        return {"status": "skipped", "reason": "GITHUB_TOKEN not set"}
    report = {"status": "ok", "files": []}
    try:
        _ensure_branch()
        for rel in FILES:
            path = os.path.join(ROOT, rel)
            if not os.path.exists(path):
                continue
            with open(path, "rb") as f:
                content = base64.b64encode(f.read()).decode()
            url = f"{API}/repos/{REPO}/contents/{rel}"
            cur = requests.get(url, headers=_headers(), params={"ref": BRANCH}, timeout=20)
            body = {"message": f"{message}: {rel}", "content": content, "branch": BRANCH}
            if cur.status_code == 200:
                if cur.json().get("content", "").replace("\n", "") == content:
                    continue  # unchanged
                body["sha"] = cur.json()["sha"]
            requests.put(url, headers=_headers(), json=body, timeout=30).raise_for_status()
            report["files"].append(rel)
    except Exception as e:  # noqa: BLE001
        report = {"status": "failed", "error": str(e)[:200]}
    return report


def pull() -> dict:
    """Fetch the latest committed data/outputs from the data branch if newer than local."""
    marker = os.path.join(ROOT, "output", ".pulled")
    if os.path.exists(marker):
        return {"status": "skipped", "reason": "already pulled this session"}
    report = {"status": "ok", "files": []}
    try:
        for rel in FILES:
            raw = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{rel}"
            r = requests.get(raw, headers=_headers(), timeout=20)
            if r.status_code != 200:
                continue
            path = os.path.join(ROOT, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(r.content)
            report["files"].append(rel)
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        open(marker, "w").write(json.dumps(report))
    except Exception as e:  # noqa: BLE001
        report = {"status": "failed", "error": str(e)[:200]}
    return report
