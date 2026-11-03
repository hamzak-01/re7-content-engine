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
         "output/content_queue.csv", "output/run_history.jsonl", "output/sources_cache.json",
         "output/collect_state.json"]
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
        _write_marker({"status": "ok", "files": report["files"], "reason": "after push"})
    except Exception as e:  # noqa: BLE001
        report = {"status": "failed", "error": str(e)[:200]}
    return report


def _fetch(rel: str) -> bytes | None:
    """Contents API first (no CDN lag, works with the token), raw URL as a fallback."""
    try:
        r = requests.get(f"{API}/repos/{REPO}/contents/{rel}", headers=_headers(), params={"ref": BRANCH}, timeout=20)
        if r.status_code == 200:
            j = r.json()
            if j.get("encoding") == "base64" and j.get("content"):
                return base64.b64decode(j["content"])
            if j.get("download_url"):  # files over 1 MB come without inline content
                r2 = requests.get(j["download_url"], headers=_headers(), timeout=30)
                if r2.status_code == 200:
                    return r2.content
    except Exception:  # noqa: BLE001
        pass
    try:
        r = requests.get(f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{rel}", headers=_headers(), timeout=20)
        if r.status_code == 200:
            return r.content
    except Exception:  # noqa: BLE001
        pass
    return None


def _rows(rel: str = "data/posts.csv") -> int:
    path = os.path.join(ROOT, rel)
    if not os.path.exists(path):
        return 0
    with open(path, "rb") as f:
        return max(sum(1 for _ in f) - 1, 0)


def _write_marker(report: dict) -> None:
    marker = os.path.join(ROOT, "output", ".pulled")
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    report = {**report, "rows": _rows()}
    open(marker, "w").write(json.dumps(report))


def pull() -> dict:
    """Fetch the latest committed data/outputs from the data branch, once per app session.

    The host resets files tracked in `main` on every redeploy but leaves untracked files
    (such as the marker) alone, so the marker also records how many posts the single source
    held; if it has shrunk since, something reset it and we pull again. If nothing could be
    fetched the marker is not written, so the next rerun tries again."""
    marker = os.path.join(ROOT, "output", ".pulled")
    if os.path.exists(marker):
        try:
            seen = json.load(open(marker)).get("rows", 0)
        except Exception:  # noqa: BLE001
            seen = 0
        if _rows() >= seen:
            return {"status": "skipped", "reason": "already pulled this session"}
    report = {"status": "ok", "files": [], "reason": "single source was reset; reloading" if os.path.exists(marker) else ""}
    try:
        for rel in FILES:
            content = _fetch(rel)
            if content is None:
                continue
            path = os.path.join(ROOT, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(content)
            report["files"].append(rel)
        if report["files"]:
            _write_marker(report)
        else:
            report = {"status": "empty", "reason": "no files found on the data branch yet"}
    except Exception as e:  # noqa: BLE001
        report = {"status": "failed", "error": str(e)[:200]}
    return report


def push_one(rel: str, message: str) -> None:
    """Commit a single file to the data branch (used to update the run log after the main push)."""
    if not os.environ.get("GITHUB_TOKEN"):
        return
    try:
        path = os.path.join(ROOT, rel)
        with open(path, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        url = f"{API}/repos/{REPO}/contents/{rel}"
        cur = requests.get(url, headers=_headers(), params={"ref": BRANCH}, timeout=20)
        body = {"message": message, "content": content, "branch": BRANCH}
        if cur.status_code == 200:
            body["sha"] = cur.json()["sha"]
        requests.put(url, headers=_headers(), json=body, timeout=30)
    except Exception:  # noqa: BLE001
        pass
