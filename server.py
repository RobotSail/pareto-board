#!/usr/bin/env python3
"""Pareto Board — tiny local server.

Serves the board UI, pulls open PRs and issues live from GitHub, applies
labels back to GitHub, and records triage sessions locally.

Concepts:
  - Triage sessions are ephemeral: each session sorts the open PRs into
    review-now / hold. Finish a session and the next one starts fresh.
    Past sessions are archived under sessions/ with a snapshot, so history
    stays readable even after PRs close.
  - Labels are permanent: classifying a PR or issue in the UI applies real
    stage:/kind:/flag labels on GitHub via your gh CLI credentials
    (GITHUB_TOKEN as fallback).

Usage:
    python3 server.py            # serves http://localhost:8417 and opens it
Env:
    PB_REPO   repo to track (default: akashgit/remote-factory)
    PB_PORT   port (default: 8417)
    GITHUB_TOKEN  optional; if unset, tries `gh auth token`, else anonymous
"""

import json
import os
import re
import subprocess
import time
import urllib.request
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("PB_REPO", "akashgit/remote-factory")
PORT = int(os.environ.get("PB_PORT", "8417"))
META_PATH = os.path.join(HERE, "meta.json")
SESS_DIR = os.path.join(HERE, "sessions")
LEGACY_STATE = os.path.join(HERE, "state.json")

STAGE_LABELS = ["stage:intent", "stage:execution", "stage:judgment",
                "stage:learning", "stage:interface"]
KIND_LABELS = ["kind:capability", "kind:fix", "kind:hardening",
               "kind:refactor", "kind:docs"]
FLAG_LABELS = ["competing", "one-way-door"]

_cache = {}  # key -> {"at": ts, "data": ...}
CACHE_TTL = 60


# ---------------- GitHub access ----------------

def _token():
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        return tok
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


def _github(method, path, body=None):
    """GitHub API call. Prefers the gh CLI (handles auth and TLS); falls
    back to urllib with GITHUB_TOKEN for environments without gh."""
    try:
        args = ["gh", "api", "-X", method, path]
        kwargs = {"capture_output": True, "text": True, "timeout": 25}
        if body is not None:
            args += ["--input", "-"]
            kwargs["input"] = json.dumps(body)
        out = subprocess.run(args, **kwargs)
        if out.returncode == 0:
            return json.loads(out.stdout) if out.stdout.strip() else {}
        # fall through to urllib on gh failure
    except Exception:
        pass
    req = urllib.request.Request(f"https://api.github.com/{path}",
                                 method=method)
    req.add_header("Accept", "application/vnd.github+json")
    tok = _token()
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    data = json.dumps(body).encode() if body is not None else None
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=15) as r:
        raw = r.read()
        return json.loads(raw) if raw.strip() else {}


def _meta():
    if os.path.exists(META_PATH):
        with open(META_PATH) as f:
            return json.load(f)
    return {}


def _shape(item, meta):
    """Normalize a GitHub PR/issue into a card."""
    labels = [l["name"] for l in item.get("labels", [])]
    stage = next((l.split(":", 1)[1] for l in labels if l.startswith("stage:")),
                 "uncategorized")
    kind = next((l.split(":", 1)[1] for l in labels if l.startswith("kind:")), "")
    flags = [f for f in FLAG_LABELS if f in labels]
    m = meta.get(str(item["number"]), {})
    return {
        "num": item["number"],
        "title": m.get("title") or item["title"],
        "desc": m.get("desc") or "Description not yet generated.",
        "stage": stage,
        "kind": kind,
        "flags": flags,
        "labels": labels,
        "author": item["user"]["login"],
        "url": item["html_url"],
    }


def _cached(key, fetch):
    now = time.time()
    c = _cache.get(key)
    if c and now - c["at"] < CACHE_TTL:
        return c["data"]
    data = fetch()
    _cache[key] = {"at": now, "data": data}
    return data


def _fetch_prs():
    def go():
        raw = _github("GET", f"repos/{REPO}/pulls?state=open&per_page=100")
        meta = _meta()
        return {"repo": REPO, "fetched_at": time.time(),
                "prs": [_shape(p, meta) for p in raw]}
    return _cached("prs", go)


def _fetch_issues():
    def go():
        raw = _github("GET", f"repos/{REPO}/issues?state=open&per_page=100")
        meta = _meta()
        items = [i for i in raw if "pull_request" not in i]
        return {"repo": REPO, "fetched_at": time.time(),
                "issues": [_shape(i, meta) for i in items]}
    return _cached("issues", go)


def _apply_labels(num, add, remove):
    """Apply/remove labels on a PR or issue (same endpoint for both)."""
    if add:
        _github("POST", f"repos/{REPO}/issues/{num}/labels", {"labels": add})
    for name in remove:
        try:
            _github("DELETE", f"repos/{REPO}/issues/{num}/labels/{name}")
        except Exception:
            pass  # already absent
    _cache.clear()


# ---------------- Sessions ----------------

def _session_path(sid):
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}", sid):
        raise ValueError("bad session id")
    return os.path.join(SESS_DIR, f"{sid}.json")


def _save_session(s):
    os.makedirs(SESS_DIR, exist_ok=True)
    with open(_session_path(s["id"]), "w") as f:
        json.dump(s, f, indent=2, sort_keys=True)


def _list_sessions():
    if not os.path.isdir(SESS_DIR):
        return []
    out = []
    for fn in sorted(os.listdir(SESS_DIR)):
        if fn.endswith(".json"):
            with open(os.path.join(SESS_DIR, fn)) as f:
                out.append(json.load(f))
    return out


def _active_session():
    sessions = _list_sessions()
    for s in reversed(sessions):
        if not s.get("finished_at"):
            return s
    s = {
        "id": time.strftime("%Y%m%d-%H%M%S"),
        "repo": REPO,
        "started_at": time.time(),
        "finished_at": None,
        "buckets": {},
        "snapshot": {},
    }
    _save_session(s)
    return s


def _migrate_legacy():
    """One-time: turn an old state.json into the first (finished) session."""
    if not os.path.exists(LEGACY_STATE) or os.path.isdir(SESS_DIR):
        return
    try:
        with open(LEGACY_STATE) as f:
            buckets = json.load(f)
        if not buckets:
            return
        prs = {str(p["num"]): p for p in _fetch_prs()["prs"]}
        s = {
            "id": time.strftime("%Y%m%d-%H%M%S", time.localtime(
                os.path.getmtime(LEGACY_STATE))),
            "repo": REPO,
            "started_at": os.path.getmtime(LEGACY_STATE),
            "finished_at": os.path.getmtime(LEGACY_STATE),
            "buckets": buckets,
            "snapshot": {k: prs[k] for k in buckets if k in prs},
            "note": "imported from legacy state.json",
        }
        _save_session(s)
        os.rename(LEGACY_STATE, LEGACY_STATE + ".imported")
    except Exception:
        pass


# ---------------- HTTP ----------------

class Handler(SimpleHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        try:
            if self.path.startswith("/api/prs"):
                self._json(_fetch_prs())
            elif self.path.startswith("/api/issues"):
                self._json(_fetch_issues())
            elif self.path.startswith("/api/sessions/"):
                sid = self.path.rsplit("/", 1)[1]
                with open(_session_path(sid)) as f:
                    self._json(json.load(f))
            elif self.path.startswith("/api/sessions"):
                items = [{k: s.get(k) for k in
                          ("id", "repo", "started_at", "finished_at", "note")}
                         | {"now": sum(1 for v in s["buckets"].values() if v == "now"),
                            "hold": sum(1 for v in s["buckets"].values() if v == "hold")}
                         for s in _list_sessions()]
                self._json(items)
            elif self.path.startswith("/api/session"):
                self._json(_active_session())
            else:
                super().do_GET()
        except FileNotFoundError:
            self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 502)

    def do_POST(self):
        try:
            if self.path.startswith("/api/session/bucket"):
                data = self._body()
                num, bucket = str(data["num"]), data.get("bucket")
                s = _active_session()
                if bucket in ("now", "hold"):
                    s["buckets"][num] = bucket
                    pr = next((p for p in _fetch_prs()["prs"]
                               if str(p["num"]) == num), None)
                    if pr:
                        s["snapshot"][num] = pr
                else:
                    s["buckets"].pop(num, None)
                    s["snapshot"].pop(num, None)
                _save_session(s)
                self._json(s)
            elif self.path.startswith("/api/session/finish"):
                s = _active_session()
                s["finished_at"] = time.time()
                _save_session(s)
                self._json({"ok": True, "finished": s["id"]})
            elif self.path.startswith("/api/labels"):
                data = self._body()
                _apply_labels(int(data["num"]),
                              [l for l in data.get("add", []) if l],
                              [l for l in data.get("remove", []) if l])
                self._json({"ok": True})
            else:
                self.send_error(404)
        except Exception as e:
            self._json({"error": str(e)}, 502)

    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet


if __name__ == "__main__":
    os.chdir(HERE)
    _migrate_legacy()
    print(f"Pareto Board → http://localhost:{PORT}  (repo: {REPO})")
    try:
        webbrowser.open(f"http://localhost:{PORT}")
    except Exception:
        pass
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
