# Pareto Board

A local PR triage board. Open pull requests show up as cards grouped by what
stage of the pipeline they touch. You drag each one into **Review now** or
**Hold**. A quiet meter keeps you honest about the 80/20 rule: only ~20% of
the backlog belongs in Review now.

## Run it

```bash
python3 server.py
```

That's it — it opens http://localhost:8417. No dependencies beyond Python 3.

## How it works

**Triage sessions are ephemeral.** Each session is one sorting pass over the
currently open PRs. Hit *Finish session* and it's archived; the next session
starts with empty buckets. The History tab lists every past session and shows
exactly how PRs were bucketed, from a snapshot taken at sort time — so history
stays readable even after those PRs close.

**Labels are permanent.** Classifying a card (*Classify* / *Edit labels*)
applies real `stage:` / `kind:` / flag labels to the PR or issue on GitHub,
using your credentials. New PRs and issues arrive under **Uncategorized**
until you classify them. The Issues tab is for labeling only — sessions and
buckets are a PR thing.

**Descriptions** come from `meta.json` when curated; anything else shows
"Description not yet generated."

## Auth

Writes (labels) and reads use, in order:

1. the `gh` CLI's stored credentials (`gh auth token`) — if you use gh, this just works
2. `GITHUB_TOKEN` env var (classic PAT with `repo` scope, or fine-grained with issues read/write)
3. anonymous (read-only, public repos, rate-limited)

## Config

```bash
PB_REPO=owner/repo python3 server.py   # track a different repo
PB_PORT=9000 python3 server.py         # different port
```

## Data layout

```
sessions/20260611-143027.json   # one file per triage session (gitignored)
meta.json                       # optional curated per-PR descriptions
```

A session file:

```json
{
  "id": "20260611-143027",
  "repo": "owner/repo",
  "started_at": 1781234567.0,
  "finished_at": null,
  "buckets": { "514": "now", "493": "hold" },
  "snapshot": { "514": { "title": "…", "stage": "judgment", "url": "…" } }
}
```
