---
name: session-recall
description: Full-text search over past Claude Code session history (all projects) via the local FTS5 session index. Use when you need to find what a past session did, said, or decided — "what did we do about X", "which session touched Y", "when did we discuss Z", "search my session history" — and mempalace recall returns nothing or you need the raw conversation text rather than curated memories. Read-only over a local, derived, rebuildable index; never egresses session content. Invoke with /rawgentic:session-recall followed by a search query.
argument-hint: <query> [--project <dir-name>] [--since YYYY-MM-DD] [--until YYYY-MM-DD]
---

# Session Recall — full-text search over session history

<role>
You search the local FTS5 index of Claude Code session JSONL history
(`~/.claude/projects/*/*.jsonl`) and present provenance-carrying results. The
index is a derived, rebuildable cache (workspace
`claude_docs/.session-index/sessions.db`); `claude_docs/session_notes/` stays
the authoritative audit trail and mempalace the authoritative long-term
memory — this skill is for raw full-text recall over everything.
</role>

## Constraints (epic #378, non-negotiable)

- **Explicit invocation only.** Indexing runs only when this skill (or the
  user) runs it — never register hooks, never background it, never daemonize.
- **Secrets by NAME.** Session text can contain credential values. When
  quoting results into chat, reports, issues, or commits, reference any
  secret by its name/env-var, never its value. The DB itself never leaves the
  host.

## Steps

The CLI lives in this plugin: resolve it as `<skill-base-dir>/../../hooks/session_index.py`
(the "Base directory for this skill" line in your context gives the base).
All commands run from the workspace root (the directory holding
`.rawgentic_workspace.json`) so the default DB path resolves; otherwise pass
`--db <workspace>/claude_docs/.session-index/sessions.db` explicitly.

### Step 1: Freshness check

```bash
python3 <plugin-hooks>/session_index.py status
```

- Exit 2 "no index" → first use: run Step 2.
- `staleness — new: N changed: M` with N+M > 0 → refresh (Step 2) before
  searching, unless the user asked about old history only.
- Fresh → skip to Step 3.

### Step 2: Index (incremental)

```bash
python3 <plugin-hooks>/session_index.py index
```

Incremental — only new/changed session files are re-read. First-ever run
walks the whole corpus (minutes on a multi-GB history; say so before running).
Exit 3 = another index run is in progress: wait and retry once, then report.
A "format drift" warning on stderr means the Claude Code JSONL shape may have
changed — surface it to the user verbatim and continue.
`--rebuild` (full re-index) needs disk headroom ≈ 2× the final DB size.

### Step 3: Search

```bash
python3 <plugin-hooks>/session_index.py search "<query>" --literal [--project P] [--since D] [--until D] [--limit N]
```

- Default to `--literal` (plain-text phrase). Drop it ONLY when the user
  explicitly asks for FTS5 operators (AND/OR/NOT/NEAR).
- `--project` takes the mangled directory name (e.g.
  `-home-user-rawgentic`); list candidates with `ls ~/.claude/projects/`.
- Dates are inclusive `YYYY-MM-DD`.
- Exit 2 with a syntax hint → re-run with `--literal`.

### Step 4: Present results

Each result carries session id, timestamp, project, role, snippet, source
path, line number. Present as a compact list (timestamp, project, role,
snippet), group by session when several hits share one, and offer to widen
(`--limit`, drop filters) or narrow (add `--project`/dates) as follow-up.
Apply the secrets-by-NAME rule to every quoted snippet.

## Failure modes

- Workspace root not resolvable from cwd → pass `--db` explicitly (path above).
- FTS5 missing from the Python build → the CLI fails loudly; report, don't
  work around.
- Zero results → say so, suggest the un-filtered or non-literal variant, and
  offer `/rawgentic-memorypalace:recall` for curated-memory search instead.
