# Pareto Board

A local PR triage board. Open pull requests show up as cards grouped by what
stage of the pipeline they touch. You drag each one into **Review now** or
**Hold**. A quiet meter keeps you honest about the 80/20 rule: only ~20% of
the backlog belongs in Review now.

Classification comes live from the repo's `stage:` / `kind:` labels, so the
board stays current as PRs open, close, and get relabeled.

## Run it

```bash
python3 server.py
```

That's it — it opens http://localhost:8417. No dependencies beyond Python 3.

## Auth

For private repos or to avoid anonymous rate limits, the server looks for a
token in this order:

1. `GITHUB_TOKEN` env var
2. `gh auth token` (if you use the GitHub CLI, this just works)
3. anonymous (fine for public repos, 60 requests/hour)

## Config

```bash
PB_REPO=owner/repo python3 server.py   # track a different repo
PB_PORT=9000 python3 server.py         # different port
```

## State

Your sorting lives in `state.json` next to the server (gitignored):

```json
{ "514": "now", "493": "hold" }
```

Delete it (or hit Reset in the UI) to start over. If you open `index.html`
directly without the server, the board falls back to localStorage.

## Curated descriptions

`meta.json` holds optional per-PR descriptions (plain-language "what it does /
what it enables"). PRs without an entry fall back to the first line of their
GitHub description.
