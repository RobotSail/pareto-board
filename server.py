#!/usr/bin/env python3
"""Pareto Board — tiny local server.

Serves the board UI, pulls open PRs live from GitHub, and persists your
review-now/hold sorting to state.json next to this file.

Usage:
    python3 server.py            # serves http://localhost:8417 and opens it
Env:
    PB_REPO   repo to track (default: akashgit/remote-factory)
    PB_PORT   port (default: 8417)
    GITHUB_TOKEN  optional; if unset, tries `gh auth token`, else anonymous
"""

import json
import os
import subprocess
import time
import urllib.request
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("PB_REPO", "akashgit/remote-factory")
PORT = int(os.environ.get("PB_PORT", "8417"))
STATE_PATH = os.path.join(HERE, "state.json")
META_PATH = os.path.join(HERE, "meta.json")

_cache = {"at": 0.0, "data": None}
CACHE_TTL = 60


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


def _github_get(path):
    """Fetch a GitHub API path. Prefers the gh CLI (handles auth and TLS);
    falls back to urllib with GITHUB_TOKEN for environments without gh."""
    try:
        out = subprocess.run(["gh", "api", path], capture_output=True,
                             text=True, timeout=20)
        if out.returncode == 0:
            return json.loads(out.stdout)
    except Exception:
        pass
    req = urllib.request.Request(f"https://api.github.com/{path}")
    req.add_header("Accept", "application/vnd.github+json")
    tok = _token()
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _fetch_prs():
    now = time.time()
    if _cache["data"] is not None and now - _cache["at"] < CACHE_TTL:
        return _cache["data"]

    raw = _github_get(f"repos/{REPO}/pulls?state=open&per_page=100")

    meta = {}
    if os.path.exists(META_PATH):
        with open(META_PATH) as f:
            meta = json.load(f)

    prs = []
    for p in raw:
        labels = [l["name"] for l in p.get("labels", [])]
        stage = next((l.split(":", 1)[1] for l in labels if l.startswith("stage:")), "unclassified")
        kind = next((l.split(":", 1)[1] for l in labels if l.startswith("kind:")), "")
        flags = [f for f in ("competing", "one-way-door") if f in labels]
        m = meta.get(str(p["number"]), {})
        desc = m.get("desc") or (p.get("body") or "").strip().split("\n")[0][:160]
        prs.append({
            "num": p["number"],
            "title": m.get("title") or p["title"],
            "desc": desc,
            "stage": stage,
            "kind": kind,
            "flags": flags,
            "author": p["user"]["login"],
            "url": p["html_url"],
        })

    data = {"repo": REPO, "fetched_at": now, "prs": prs}
    _cache.update(at=now, data=data)
    return data


class Handler(SimpleHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/prs"):
            try:
                self._json(_fetch_prs())
            except Exception as e:
                self._json({"error": str(e)}, 502)
        elif self.path.startswith("/api/state"):
            if os.path.exists(STATE_PATH):
                with open(STATE_PATH) as f:
                    self._json(json.load(f))
            else:
                self._json({})
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/state"):
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            with open(STATE_PATH, "w") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            self._json({"ok": True})
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet


if __name__ == "__main__":
    os.chdir(HERE)
    print(f"Pareto Board → http://localhost:{PORT}  (repo: {REPO})")
    try:
        webbrowser.open(f"http://localhost:{PORT}")
    except Exception:
        pass
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
