# Durable decision capture — design

**Status:** design, awaiting owner review · **Author:** session `1d27b840` · **Date:** 2026-08-02

## The problem, measured

`hooks/notes-size-handler.py` (added 2026-04-09, #56/#59) is invoked by `hooks/session-start`
on `startup` and `compact` over every `*.md` in `$CLAUDE_DOCS/session_notes/` (`session-start:488-504`).
Over `THRESHOLD = 800` lines it keeps `KEEP_LINES = 200` and **deletes the rest**.

It has fired **10 times across 152 files**. Eight of those ten are shown below (`arc.md` at 805 and
`saystory.md` at 802 are the other two); **six of the ten are epic/run decision logs**:

| trimmed from | file | date |
|---|---|---|
| 1034 | `3dstories-fleet.md` | 2026-07-20 |
| 831 | `EPIC475-run-handoff.md` | 2026-07-23 |
| 851 | `epic-667-autorun-log.md` | 2026-07-28 |
| 893 | `epic-63-autorun-log.md` | 2026-07-30 |
| 815 | `epic-722-autorun-log.md` | 2026-07-30 |
| 802 | `rawgentic.md` | 2026-08-01 |
| 848 | `epic-756-autorun-log.md` | 2026-08-02 |
| 960 | `epic-46-autorun-log.md` | 2026-08-02 |

**At least six epic runs have silently lost their decision history.** Epic #46 lost D1–D30 and a
25-entry error catalogue today; the others were never checked, and their content is equally gone.

This is not a latent hazard. It selectively destroys the longest-running, highest-value logs,
because those are the only files that reach 800 lines.

### Three distinct defects

1. **One file, two jobs.** Operational churn (step markers, recovery breadcrumbs — high volume,
   only the tail matters) shares a file with decisions (low volume, the OLD entries are the
   valuable ones). Volume from the first pushes the second over the threshold. The trimmer cannot
   tell them apart, so it keeps the routine chatter and deletes the reasoning.
2. **It destroys, and it fails OPEN.** The only preservation path is
   `try_ingest()` → `POST http://localhost:{port}/ingest`, default port **9077**. Nothing listens
   there (verified: connection refused). The real memory server is `10.0.17.205:8420`. The
   function catches every exception, returns `False` — **and the caller ignores the result and
   trims anyway** (`notes-size-handler.py:104-118`). The safety net has been dead for ~4 months
   and its failure is unobservable.
3. **Lines are a bad proxy** for the context cost the hook exists to control. 200 long lines can
   cost more than 800 short ones.

## The invariant (owner decision, 2026-08-02)

> **A decision log is never truncated. Ever. By anything.**

Not "trimmed carefully", not "archived first", not "trimmed only when very large" — never. A
decision's value does not decay with age; the OLDEST entries are the ones a later session most
needs, because they are the ones nobody remembers. Every loss recorded above came from treating
decisions as if they were logs of routine activity.

**Enforced structurally, not by policy.** The decisions store lives at `claude_docs/decisions/`,
a SIBLING of `session_notes/`, so the trimmer's glob (`$CLAUDE_DOCS/session_notes/*.md`) cannot
reach it — and it is `.jsonl`, not `.md`, so even a future glob widened to `**/*.md` misses it.
A rule that depends on remembering a rule is the thing that failed here; the file has to be
somewhere the destroying code does not look.

Two tests hold the line: one asserts the trimmer's own file-discovery never yields a path under
`decisions/`, and one writes a decision, forces a trim cycle, and asserts it is still readable
byte-for-byte.

## Design

### Split the two jobs

| file | job | trimmed? | injected? |
|---|---|---|---|
| `claude_docs/decisions/<project>.jsonl` | decisions, append-only | **never** | last N only |
| `claude_docs/session_notes/<project>.md` | operational tail | yes | tail only |

One JSON object per line:

```json
{"id":"D31","ts":"2026-08-02T18:01:44Z","session":"1d27b840","project":"claude-skills",
 "run":"epic-46","title":"design-system shows spacing, does not state it",
 "body":"...","overturnable":"delete tokens.py, restore the literals"}
```

JSONL because it is append-only by construction (a crash mid-write costs one line, not the file),
greppable, and readable without parsing the whole file. `overturnable` is mandatory — this
workspace's decision convention already requires every decision to record its undo.

Trimming `session_notes/*.md` then becomes **safe**, because nothing irreplaceable lives there.

### Components

1. **`hooks/decision_log.py`** — `append` and `read --last N --run <id>`. One helper, one home;
   skills call it instead of `>>` into a markdown file. Atomic write via the existing
   `atomic_write_text` helper (#285 already routed nine sites through it).
2. **`notes-size-handler.py` becomes non-destructive and fails CLOSED.** Before any cut, the
   removed content is written to `<file>.<ts>.archive.md`. **If that write fails, the trim does
   not happen.** Fail-open is the defect that caused every loss above.
3. **Kill or fix the dead ingest.** `localhost:9077` has never served. Either point it at the real
   server (`MEMORY_SERVER_URL`, `10.0.17.205:8420`) and check the result, or delete it — a
   best-effort call whose failure is invisible is worse than no call, because it reads like a
   safety net in code review.
4. **Threshold measured in characters**, not lines (owner's suggestion, 2026-08-02) — it is the
   honest proxy for context cost.
5. **Injection shrinks:** session-start injects the **last 15 decisions** for the bound project
   plus the notes tail, never full history. 15 rather than "N" because an unspecified number is
   how this drifts back to injecting everything; it is a one-constant change if it proves wrong.
   The full store is always available on demand via `decision_log.py read`.

### Failure handling

| failure | behaviour |
|---|---|
| archive write fails | **no trim** — original untouched, error surfaced |
| decisions file unwritable | skill fails loudly; a decision that was not recorded must not look recorded |
| JSONL line corrupt | reader skips it and reports the line number; one bad line never blocks the rest |
| ingest unreachable | irrelevant to safety — never gates the trim either way |

### Testing

1. Trim a file over threshold; assert **every original line** is recoverable from the archive.
2. Make the archive write fail; assert the original file is **byte-identical** afterwards.
3. Append a decision, force a trim cycle, assert the decision is still readable.
4. Assert a file at exactly the threshold is untouched (off-by-one).
5. Assert the character-based measure trims a few very long lines and spares many short ones.

Each gets a mutation: flip fail-closed to fail-open and test 2 must fail; drop the archive write
and test 1 must fail.

### Migration

- The rescue copy (`claude_docs_rescue_20260802T182555Z/`, 206 files) is the stopgap; it holds the
  current state of everything, including the six already-damaged logs — **their lost content is
  not recoverable from it**, only their surviving tails.
- Existing `epic-*-autorun-log.md` files move to the new decisions store going forward. No attempt
  is made to reconstruct what is gone: invented decision history is indistinguishable from real
  history to a future session, which is the failure this whole design exists to prevent.

## Out of scope

- Reconstructing the six destroyed logs.
- Changing what skills choose to write down (only where it lands).
- The mempalace ingestion pipeline beyond fixing or removing this one dead call.
- `claude_docs/session_notes.md` (11,138 lines) — it sits ABOVE the scanned directory and has
  never been at risk. Noted because a first reading of this bug suggested otherwise.

## Risk

**Medium.** Touches a hook that runs on every session start, so a bug here is felt everywhere.
Mitigated by fail-closed (the worst case becomes "a file did not get trimmed", not "a file was
destroyed") and by the tests above. **Complexity: M.**
