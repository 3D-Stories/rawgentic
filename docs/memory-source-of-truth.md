# Memory Source of Truth

**Policy:** **mempalace is the authoritative, read/write memory store**
(`MEMORY_SERVER_URL=http://10.0.17.205:8420`) for durable *memory* — facts,
decisions, gotchas, and session insights. Write to it, correct it in place, and read
from it as the primary. Two in-repo stores stay authoritative only for their own
**non-memory** roles, because the tooling reads those files directly (see below).

Background architecture (dated draft): `docs/superpowers/specs/2026-04-08-dual-memory-backend-design.md`.

## The stores and their roles

| Store | Location | Role | Authoritative for |
|---|---|---|---|
| **mempalace** | memory server (`http://10.0.17.205:8420`) | **Authoritative read/write memory** — semantic search + knowledge graph, full CRUD | **Durable memory: facts, decisions, gotchas, session insights** |
| Project contract | `CLAUDE.md` (workspace root + each `projects/<name>/CLAUDE.md`) | Operating instructions loaded **verbatim by Claude Code every session** | Instructions / conventions / pre-PR checklist — **not** episodic memory |
| Session notes / WAL | `claude_docs/session_notes/<project>.md`, WAL, registry | In-flight run-state read **directly by the resume/context hooks** | Current run state (also ingested into mempalace) |
| Auto-memory dir | `~/.claude/projects/<workspace>/memory/` (`MEMORY.md` + files) | **Legacy** Claude Code native auto-memory; secondary bootstrap mirror auto-loaded via the `MEMORY.md` system-reminder | — superseded by mempalace as the memory authority |

## Working with authoritative memory (mempalace)

- **Write** durable memory directly to mempalace:
  - `mempalace_kg_add` — a fact / decision (knowledge-graph triple)
  - `mempalace_add_drawer` — a note / gotcha
  - `mempalace_diary_write` — a session record
- **Correct / delete in place** — mempalace is the authority, so edit it directly; there
  is no replica to reconcile:
  - `mempalace_update_drawer`, `mempalace_kg_invalidate`, `mempalace_delete_by_source`
- **Read** via the mempalace recall protocol: `mempalace_status` on wake-up;
  `mempalace_search` + `mempalace_kg_query` before acting on any prior fact (WF2 Step 2
  Layer-3 recall). Never guess a stored fact — query first.
- **Automatic ingest still feeds it:** the PreCompact fork (session diary + gotcha
  drawers) and WF2 Step 10 memorize keep new insights flowing in without manual steps.

## Why the two in-repo stores can't move

These are constraints of the tooling, not carve-outs from the policy:

- **`CLAUDE.md`** — Claude Code loads its instructions from the file at session start; it
  does not read mempalace for the contract. The contract must live in-repo, git-tracked.
  It is instructions, not memory.
- **Session notes / WAL** — the resume and context-injection hooks read these local files
  directly to reconstruct state after a compaction; they cannot be served from mempalace.
  They are additionally ingested into mempalace via the PreCompact fork.

## The auto-memory dir is now secondary

`~/.claude/projects/<workspace>/memory/` (the `MEMORY.md` index + per-fact files) still
auto-loads into every session via the system-reminder, which makes it a useful bootstrap.
But **mempalace is the memory authority**: write new durable facts to mempalace and correct
them there. The auto-memory files are a human-readable mirror/bootstrap, not the source of
truth for memory.

## History

#206 (2026-07-05, PR #219) first closed this as "mempalace = recall replica, read-mostly,
in-repo stores authoritative." **Owner corrected the same day: mempalace is the
authoritative read/write memory store.** This document supersedes that earlier framing.
