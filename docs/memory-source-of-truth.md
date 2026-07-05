# Memory Source of Truth

Where rawgentic's "memories" live after the reflexion removal (#205) and the
memory-migration investigation (#206), and which store is authoritative for what.

Background architecture (dated draft): `docs/superpowers/specs/2026-04-08-dual-memory-backend-design.md`.

## The three stores

| Store | Location | Role | Authoritative for |
|---|---|---|---|
| **Project contract** | `CLAUDE.md` (workspace root + each `projects/<name>/CLAUDE.md`) | Operating instructions loaded verbatim every session | **Yes** — instructions, conventions, pre-PR checklist |
| **Session notes** | `claude_docs/session_notes/<project>.md` (+ WAL, registry) | Per-project workflow progress across compaction/resume | **Yes** — in-flight run state |
| **Curated auto-memory** | `~/.claude/projects/<workspace>/memory/` (`MEMORY.md` index + per-fact files) | Durable curated facts, loaded into every session via the `MEMORY.md` system-reminder | **Yes** — durable cross-session facts |
| **mempalace** | memory server, `MEMORY_SERVER_URL=http://10.0.17.205:8420` | Semantic-search + knowledge-graph **replica**, queried during workflow runs | **No** — a recall index fed by the stores above |

The first three are **in-repo / in-`~/.claude`, human-readable, git- or file-backed,
and are the source of truth**. mempalace is a **downstream recall replica**: it makes
those facts semantically searchable during a workflow run. It is never edited by hand
as a primary; it is populated by the pipes below.

## How mempalace gets populated (why no bulk migration is needed)

Three pipes keep mempalace current automatically — this is why #206 closed as a
*documentation* task, not a data move:

1. **PreCompact fork.** The mempalace PreCompact hook saves each session (a diary
   drawer + gotcha drawers) into the project's mempalace wing before context compacts.
   Since rawgentic sessions run for weeks with frequent compaction, this is the
   primary ingest trigger (the design spec's key finding: `wal-stop` rarely fires).
2. **WF2 Step 10 (memorize).** Post-#205, Step 10 curates each run's insights into
   mempalace via `mempalace_kg_add` / `mempalace_add_drawer`, scoped to the project;
   it falls back to `CLAUDE.md` / the memory dir only if mempalace is unreachable.
3. **One-time restage.** A 2026-04-15 restage loaded the repos' `docs/` markdown into
   mempalace (the bulk of the `rawgentic` wing).

WF2 Step 2 (Layer-3 recall) reads mempalace back — `mempalace_search` +
`mempalace_kg_query` — so durable facts surface at design time.

## The #206 determination (no bulk migration)

Investigation (2026-07-05) found the migration the issue anticipated had **already
happened organically** once #205's Step-10-to-mempalace change shipped:

- Recent curated facts are already present in the `rawgentic` mempalace wing
  (verified by direct query — e.g. the GitHub-Pages/`.nojekyll` gotcha and the
  full session diary, created via the PreCompact fork; `driver_lib` version facts).
- The curated auto-memory dir is already loaded into **every** session via the
  `MEMORY.md` system-reminder, so it is queryable through the interface actually used.

A **bulk copy** of the auto-memory dir into mempalace was rejected because:

- It would create a **dual source of truth with no back-sync** — mempalace cannot
  write corrections back to the `MEMORY.md` files, so a fact edited in one store
  goes stale in the other (drift).
- #206's scope explicitly excludes other projects' memories, but the auto-memory
  dir is cross-project.
- The recall value is already delivered by the three pipes above.

**Rule going forward:** write durable facts to the authoritative store (a `CLAUDE.md`
instruction, or a `MEMORY.md` per-fact file). Let the pipes replicate them into
mempalace. Treat mempalace as read-mostly for recall; never hand-edit it as a
primary. When a fact changes, update the in-repo / `~/.claude` source; if a stale
mempalace drawer needs correcting, use `mempalace_kg_invalidate` /
`mempalace_delete_by_source` rather than editing in place.
