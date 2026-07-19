# Run-Records & Completion Summary (`hooks/work_summary.py`)

The shared lib used by **WF2 Step 16** and **WF3 (fix-bug) Step 14** (Workflow
Completion Summary) to do two jobs from one validated record:

1. **Render the completion summary** — the standardized "WF2 COMPLETE" block the
   skill used to hand-type. Rendering it from a structured record means its shape
   no longer drifts run to run.
2. **Emit a run-record** — one structured JSON line appended to a JSONL store
   capturing what the run actually did: issue/type, change volume, tests, each
   quality gate's *findings caught vs resolved*, the Step 11.5 security-scan
   status, loop-backs, and the PR/CI/deploy outcome.

Accumulated across runs, the store is the **Tier-2 measurement telemetry
substrate**. The [Tier-1 impact report](measurements/2026-06-15-wf2-extraction-impact.md)
proves reliability/consistency/maintainability from tests + git without running
the workflow; Tier-2 (speed and output-quality) needs end-to-end A/B runs over a
fixed corpus, and *those* runs are what each run-record measures. A gate's
effectiveness ("how often does the design critique actually catch something")
becomes a query over the store instead of a sentence the user reads once.

## The run-record schema (v1)

`schema_version` and `generated_at` are stamped by the tool; everything else is
supplied by the workflow. Counts are non-negative integers; `resolved` may not
exceed `findings`, `passing` may not exceed `total`, `used` may not exceed
`budget`.

| Field | Type | Notes |
|-------|------|-------|
| `workflow` | string | e.g. `implement-feature` (renders as `WF2`), `fix-bug` (`WF3`). |
| `workflow_version` | string | The `.claude-plugin/plugin.json` version — lets Tier-2 compare runs across skill versions. |
| `issue` | object | `number` (int\|null), `type` (`feature`/`bug`/`chore`/`other`), `complexity` (`trivial`/`standard`/`complex`\|null). |
| `changes` | object | `files_changed`, `commits` (ints); `insertions`, `deletions` (int\|null). |
| `tests` | object | `added` (int); `passing`, `total` (int\|null). |
| `gates` | array | Each `{step, name, findings, resolved, status}`; `status` ∈ `pass`/`fail`/`skipped`/`fast_path`. Optional per-gate `reviewer_kind` (#155) ∈ `inline`/`reflexion`/`builtin_code_review`/`codex`/`hand_rolled_multi` when present — free text is rejected. Step 11.5 is captured in `security_scan`, not here. Multi-pass gates count per the [#340 rules below](#gate-counting-340). |
| `security_scan` | object | `ran` (bool), `blocking_resolved`, `advisory` (ints), `skipped` (list of strings). Mirrors the [Step 11.5 gate](security-scan.md). |
| `loop_backs` | object | `used`, `budget` (ints). |
| `outcome` | object | `pr_number` (int\|null), `pr_url` (string\|null), `merged` (bool\|null), `ci` (`passed`/`failed`/`not_configured`/`skipped`), `deploy` (`success`/`manual`/`failed`/`not_applicable`). |
| `follow_ups` | array | Optional list of strings; defaults to `[]`. |
| `extra` | array | Optional ordered `{label, value}` (both strings) pairs for **workflow-specific** human lines that ride along in the render without bloating the uniform core (e.g. WF3's `Root Cause` / `Fix`). Defaults to `[]`. |
| `usage` | object | Optional (#155). Best-effort telemetry: `input_tokens`, `output_tokens` (int\|null), `cost_estimate_usd`, `wall_clock_s` (number\|null), `model_mix` (object\|null, per-model `{input_tokens, output_tokens}`). Present is strict — all 5 keys required, nullable values; absent omits the object entirely rather than nulling it. Optional 6th key `capture_status` (#189): controlled vocab `{captured, unrecoverable, unavailable}`, fail-closed; when `captured`, `input_tokens`+`output_tokens` MUST be non-null and sum > 0 (the schema-level backstop against #155's null-forever state). Populated live by `hooks/usage_capture.py` (parses the session transcript); historical rows backfilled to `unrecoverable`. |
| `dispatches` | array | Optional (#329). List of per-subagent-dispatch objects. Present is strict, same present-is-strict philosophy as `usage`/`goal_guard`: each entry requires all 6 keys — `role` (one of `analysis`/`implementation`/`other`/`review`), `subagent_type` (non-empty string), `model` (string\|null), `effort` (string\|null), `outcome` (one of `dead`/`error`/`ok`/`retried`), `resolution` (one of `fallback`/`generic`/`primary`) — fail-closed on non-strings, case variants, or null in a vocab field. Absent entirely → pre-#329 records stay valid, no schema version bump. |

The fields above the line are the **uniform core** every workflow emits, so
Tier-2 can aggregate across workflows; `extra` is the escape hatch for
workflow-specific context that doesn't need to be an aggregation column.

**Cross-workflow:** WF2 (`workflow: "implement-feature"` → renders `WF2`) runs a
Step 11.5 security scan, so `security_scan.ran` is `true` and the scan line shows
the blocking/advisory/skipped breakdown. WF3 (`workflow: "fix-bug"` → `WF3`) has
no security scan, so it sets `security_scan.ran: false` and the render shows
`Security Scan: not run` rather than referencing a Step 11.5 that never ran. WF3
carries `Root Cause` / `Fix` in `extra` and uses a loop-back budget of 2.

Every documented key must be **present**; "nullable" means `null` is an allowed
*value*, not that the key may be omitted — a dropped field is a telemetry gap (the
aggregation can't tell it from a deliberate null), so it fails validation.

Validation is hand-rolled (no runtime `jsonschema` dependency) and adversarially
unit-tested — notably it rejects `bool` where an int is expected, since `bool` is
a subclass of `int` in Python and a naive check would let `findings: true`
corrupt the substrate.

### Gate counting + merged-gate reviewer_kind (#340) {#gate-counting-340}

Two semantics rules govern `gates[]` assembly; the canonical, worked-example
version lives in `skills/implement-feature/references/run-record.md` (WF3's
completion step points there too).

- **Counting rule (multi-pass gates).** `findings` counts UNIQUE findings across
  all passes of the gate — finding identity is mechanical: same artifact location
  (file/section/line-range) AND same required change; reviewer wording and
  `source` tag are irrelevant, so a re-raise or re-litigation never adds.
  `resolved` counts findings whose FINAL disposition at gate close is terminal:
  applied, fixed-in-gate, refuted with cited evidence, or dropped by the
  confidence band (band-drops count in BOTH `findings` and `resolved`; an
  unresolved deferral counts in `findings` only). The deduped pair is computed at
  gate close and persisted in that gate's session-note evidence; completion-step
  assembly reads the persisted figures and never re-derives them.
- **reviewer_kind precedence (merged gates).** `reviewer_kind` stays a single
  value: record the gate-DEFINING mechanism — the mechanism whose absence would
  void the gate per the skill's own contract. The additive opt-in adversarial
  layer is skippable-on-failure by contract and therefore never changes
  `reviewer_kind` (WF2: Step 4 and Step 6 → `inline`, Step 11 →
  `hand_rolled_multi`). `codex` applies only to a gate whose SOLE mechanism is
  codex. A fully-skipped gate omits `reviewer_kind` entirely.

Historical records assembled before #340 remain valid — the rules govern
assembly going forward; WF14's telemetry audit flags legacy per-pass sums AND
legacy additive-layer `reviewer_kind` values as `known-limitation`, not defects.

## The store

One JSON line per run, appended to:

```
<project-root>/docs/measurements/run_records.jsonl
```

Override with `--store <path>` or the `RAWGENTIC_RUN_RECORD_STORE` env var (env
is configurable from v1). The default is committed and per-project, so the
telemetry is versioned and reproducible alongside the code it measures.

## Usage telemetry & ccusage backfill

`usage` (#155) is optional per-run telemetry: `input_tokens`, `output_tokens`,
`cost_estimate_usd`, `wall_clock_s`, and a per-model `model_mix` breakdown. Most
runs are billed against a Claude subscription rather than metered per API call,
so **tokens-by-model in `model_mix` is the primary metric** Tier-2 should trend
on; `cost_estimate_usd` is a derived, secondary figure (a rate-card estimate,
useful for cross-checking, not what a subscription user is actually paying).
When present, `usage` is validated strictly (all five keys, nullable values);
when the harness can't surface real numbers, the workflow **omits the whole
object** rather than persist a fabricated one.

Since #189, `usage` is populated live by `hooks/usage_capture.py`, which parses
the current session's Claude Code transcript (the same source `ccusage` reads —
stdlib-only, no network) and stamps `capture_status: "captured"` with real
per-model token totals. The optional `capture_status` key is fail-closed
(`{captured, unrecoverable, unavailable}`), and a `captured` claim REQUIRES
non-null tokens summing > 0 — so the #155 state (a `usage` object null forever)
can no longer be persisted. Rows we cannot fill are marked `unrecoverable`
(historical, no session-id correlator) or `unavailable` (capture attempted,
session file missing / no usage), never left as bare nulls; a drift-guard test
fails on any `usage` object with null tokens and no marker.

Populate `usage` at completion-step assembly time, **before** invoking
`work_summary.py summarize` — the store is append-only, so re-running
`summarize` to attach usage after the fact would append a duplicate line for
the same run rather than amend the original. If usage numbers only become
available after a record has already been persisted, the backfill path is a
**hand-edit of that run's existing JSONL line** in place; the pristine
drift-guard test that validates the committed store in CI catches a malformed
edit the same way it would catch a bad writer.

[`ccusage`](https://github.com/ryoppippi/ccusage) (`npx ccusage@latest`) is the
local tool for reading actual token counts out of the Claude Code session logs
when the harness itself doesn't report them — the intended source for the
numbers that populate `usage` at assembly time or during a hand-edit backfill.

When present, `usage` renders as a `- Usage: <in> in / <out> out tokens[, ~$cost][,
Ns wall][ (model: in/out, ...)][, worker-share NN%]` line in the completion summary,
immediately after the `- Tests:` line.

### Derived: worker-token-share (#315)

Concept from the Anthropic "plan big, execute small" cookbook
([`managed_agents/CMA_plan_big_execute_small.ipynb`](https://github.com/anthropics/claude-cookbooks/blob/main/managed_agents/CMA_plan_big_execute_small.ipynb)),
which treats **worker-token-share** — the fraction of input tokens billed to cheap
worker models instead of the expensive coordinator — as its headline optimization
metric. `work_summary.worker_token_share(mix, worker_models)` derives it at render
time from the already-captured `model_mix`; nothing new is recorded at capture time.

**Classification rule (config-derived, never hardcoded):** the worker-model short
names are the unique values of the bound project's `modelRouting` in
`.rawgentic_workspace.json` (resolved by walking up from `--project-root`, ≤5
levels, fail-open). A `model_mix` entry is a **worker** iff any short name (e.g.
`sonnet`, `opus`) is a case-insensitive substring of the model id; everything else
is orchestrator. `share = worker input_tokens / total input_tokens` (input-side,
matching the cookbook). A single-model orchestrator-only run is `0.0`; an
underivable case (no `modelRouting`, malformed/empty `model_mix`, zero totals)
omits the figure entirely — never raises, never renders `?`. Known limitation: if
the orchestrator's own family equals a routed value, its tokens count as worker.

When derivable, `summarize` also injects `usage.worker_token_share` (rounded to
4 decimals) into the persisted record — a **present-optional derived field** like
`lane`: `validate_record`'s required keys are unchanged and records without it
remain valid (no schema version bump).

## timing (#506)

`timing` is optional per-step wall-clock telemetry computed from the step-state
history — the append-only sibling of the #480 now-pointer that every
`step_state.py write` carrying an int issue also feeds
(`claude_docs/wal/history/<project>-issue-<n>.history.jsonl`, keyed
project+issue so a multi-session run accumulates one history). At assembly time
the completion step runs `python3 hooks/step_state.py timing --project <p>
--issue <n>` and embeds the stdout verbatim: per-step entry-interval durations
(the last event is open-ended, `duration_s: null` — never fabricated),
per-workflow phase buckets (design / plan / implement / review / pr_ci / wrap),
an `idle` bucket holding the excess of any interval above the idle threshold
(default 1800s — a quota pause is never silently attributed to the step it
interrupted), and an honest `status` of `complete` / `partial` / `absent`.
`validate_record` checks the key strictly when present (the `usage` pattern);
absent stays valid. This replaces hand-parsing session transcripts for step
markers — the reconstruction the epic #493 timing profile did by hand, measured
~2× off against real wall-clocks.

## dispatches (#329)

`dispatches` is optional structured telemetry for individual subagent dispatches
during a run — one entry per dispatch, each carrying `role`, `subagent_type`,
`model`, `effort`, `outcome`, `resolution`. It exists to replace transcript
archaeology (re-reading a run's raw transcript to reconstruct what got dispatched
and how it went) with a queryable record.

Two of the six keys are easy to conflate but orthogonal — never collapse one into
the other:

- **`outcome`** is the dispatch's *terminal result*: `ok`, `error`, `retried`, or
  `dead`. `dead` is the sharp one — a dispatch that returned "successfully" (no
  error, no retry) but whose result was vacuous (empty body, `confirmedCount: 0`;
  see mistake #9 in this repo's `CLAUDE.md`). A `dead` dispatch is NOT an `error`:
  it completed without raising, it just produced nothing usable.
- **`resolution`** is the *invocation path* that was actually taken: `primary`
  (the named `subagent_type` ran as requested), `fallback` (the named agent type
  was unavailable, so a bundled substitute ran instead), or `generic` (no named
  agent type at all — an inline-prompt tier).

A dispatch can be `{outcome: ok, resolution: fallback}` (the substitute agent did
fine) just as easily as `{outcome: dead, resolution: primary}` (the requested
agent ran and came back empty) — the two axes vary independently.

Emission (actually populating `dispatches` from a live workflow run) is wired by
#330 — the capture contract below.

### Capture (#330)

The schema above defines the shape; this is the CAPTURE side — how each of the
six fields gets INTO the record from a live run.

**Canonical audit line.** At the point each dispatch decision COMPLETES (or the
orchestrator declares it dead/abandoned), the workflow appends one line to the
session notes, fixed key order, single-space-separated (copied verbatim from
`shared/blocks/model-routing-resolve.md`, the canonical source):

```
DISPATCH issue=<n> role=<review|implementation|analysis|other> type=<subagent_type> model=<model|null> effort=<effort|null> outcome=<ok|error|retried|dead> resolution=<primary|fallback|generic>
```

Canonical regex (assembly's scoped grep + the shape the validator enforces):

```
^DISPATCH issue=(\d+) role=(review|implementation|analysis|other) type=([A-Za-z0-9_.:/-]+) model=(null|[A-Za-z0-9_.:/-]+) effort=(null|[A-Za-z0-9_.:/-]+) outcome=(ok|error|retried|dead) resolution=(primary|fallback|generic)$
```

The resolution decision table (which dispatch path maps to which `resolution`
value) lives alongside the grammar in `shared/blocks/model-routing-resolve.md`
(the WF2 canonical source, synced into `skills/implement-feature/SKILL.md`);
`skills/fix-bug/SKILL.md` carries its own review-role-only variant.

**Correlation rules:**

- One line per SUBAGENT INVOCATION, written at completion or abandonment —
  never per attempt.
- Written flush-left at column 0, as its own physical line — never inside a
  list item, blockquote, or fenced code block. The assembler's `^DISPATCH` grep
  is anchored to line start; an indented or bulleted line is rescued only
  into the MALFORMED count, never into `dispatches[]`.
- Retry of the SAME task/invocation is ONE line: `outcome=retried` (retried
  then succeeded) or `outcome=error` (retried and still failed). A dispatch
  PATH abandoned for a different one (e.g. delegation dropped for inline work)
  gets TWO lines — the abandoned path's terminal line, then the new path's
  line.
- `dispatches[]` preserves the session note's line order; assembly performs no
  reordering, grouping, or correlation beyond that.
- Duplicates are NEVER deduped — two identically-configured dispatches (e.g.
  Step 8a legitimately firing two reviewers) are two distinct entries.
- `issue=<n>` is the scoping KEY assembly greps the whole session-notes file
  on (`^DISPATCH issue=<n> `) — it is NOT itself a field carried into the
  assembled `dispatches[]` entry.
- Same-issue re-run limitation: two separate runs of the SAME issue in one
  notes file union their lines. Accepted because a crash-resumed run is the
  common same-issue case and its union is correct; WF14's
  dispatch-completeness rubric audits anomalies.
- Malformed detection operates on this issue's lines: any line whose STRIPPED
  content starts `DISPATCH issue=<n> ` but fails the canonical regex —
  including an indented or list-bulleted line the flush-left grep would
  otherwise miss — is skipped and COUNTED; the record's `extra` gets one note
  `{"label": "dispatch capture notes", "value": "skipped <n> malformed DISPATCH line(s)"}`
  — a malformed line never fails the record and is never silently lost. (A
  `DISPATCH` line with NO parseable `issue=` field is unattributable and stays
  outside this issue's assembly.)
- Zero well-formed lines for this issue → omit the `dispatches` key entirely
  (no empty-array noise).
- Under-count detection (a completion line never written at all) is owned
  ENTIRELY by WF14's dispatch-completeness rubric — assembly never parses the
  lowercase start-time observability line, so it performs no start-vs-
  completion comparison itself.

**Worked example.** A session-notes excerpt with two well-formed lines, a
legitimate duplicate pair, and one malformed line:

```
DISPATCH issue=42 role=review type=rawgentic:rawgentic-reviewer model=opus effort=null outcome=ok resolution=primary
DISPATCH issue=42 role=analysis type=generic-analysis model=null effort=null outcome=ok resolution=generic
DISPATCH issue=42 role=review type=rawgentic:rawgentic-reviewer model=sonnet effort=high outcome=ok resolution=primary
DISPATCH issue=42 role=review type=rawgentic:rawgentic-reviewer model=sonnet effort=high outcome=ok resolution=primary
DISPATCH issue=42 role=review type=rawgentic:rawgentic-reviewer model=opus effort=high outcome=ok
```

Lines 3 and 4 are IDENTICAL and both real — Step 8a firing two
identically-configured reviewers is exactly this shape; assembly keeps both,
never deduped. Line 5 is malformed (missing `resolution=`) and is skipped.

Assembled `dispatches[]` (4 entries, note order preserved):

```json
[
  {"role": "review", "subagent_type": "rawgentic:rawgentic-reviewer", "model": "opus", "effort": null, "outcome": "ok", "resolution": "primary"},
  {"role": "analysis", "subagent_type": "generic-analysis", "model": null, "effort": null, "outcome": "ok", "resolution": "generic"},
  {"role": "review", "subagent_type": "rawgentic:rawgentic-reviewer", "model": "sonnet", "effort": "high", "outcome": "ok", "resolution": "primary"},
  {"role": "review", "subagent_type": "rawgentic:rawgentic-reviewer", "model": "sonnet", "effort": "high", "outcome": "ok", "resolution": "primary"}
]
```

Plus one `extra` entry for the skipped line:

```json
{"label": "dispatch capture notes", "value": "skipped 1 malformed DISPATCH line(s)"}
```

This example validates cleanly against `hooks/work_summary.py`'s
`validate_record` (checked: a minimal record built with these exact 4
`dispatches` entries plus this `extra` note returns `[]` from
`validate_record(rec, strict=True)`).

## Fail-closed for the store, best-effort for the human

- **Fail closed on the store.** A record that fails validation is **never**
  persisted — the substrate stays pristine and a downstream aggregation can trust
  every line.
- **Best-effort for the summary.** `render_summary` never raises; even a record
  that failed validation (or is partial) still produces the "WF2 COMPLETE" block,
  so a telemetry-schema nit never denies the user their Step 16 output.
- The CLI signals the difference via its exit code, so the skill can react.

## CLI

```bash
python3 hooks/work_summary.py summarize \
  --record-file /tmp/wf2-run-record-<issue>-<session-id>.json \
  --project-root <activeProject.path> \
  [--store <path>] [--json] [--no-persist]
```

stdout is the completion summary (or, with `--json`, the normalized record).
Exit codes:

| Code | Meaning |
|------|---------|
| `0` | Record valid: summary rendered and (unless `--no-persist`) appended to the store. |
| `1` | Record invalid: summary still rendered, errors on stderr, record **not** persisted. The skill records the telemetry gap. |
| `2` | Usage error or unreadable/non-JSON record file. |

## Aggregating the store (Tier-2 rollups)

Once runs accumulate, `aggregate` turns the store into the measurements the
substrate exists for — gate effectiveness becomes a query, not a guess:

```bash
python3 hooks/work_summary.py aggregate \
  --store <path> \
  [--json] [--group-by {workflow,version,type,complexity,source}] [--since <ISO-date>]
```

`--store` falls back to `$RAWGENTIC_RUN_RECORD_STORE`; unlike `summarize`,
`aggregate` has **no implicit default store** (rolling up the wrong project's
store silently would be worse than a clear usage error). Markdown by default;
`--json` emits the metric object plus the `excluded`/`excluded_count`. Reported
metrics:

| Group | Metrics |
|-------|---------|
| Gate effectiveness | per gate: hit rate (% of present runs with findings>0), total findings, total resolved, resolution rate, mean findings/run, `runs_present` |
| Loop-backs | mean used; `pct_hit_cap` (used==budget, over runs with budget>0) |
| Outcomes | CI-pass (over ci∈{passed,failed}), merge (over non-null merged), deploy-success (over deploy≠not_applicable), security-blocked (over ran==true), scanner-skip frequency |
| Effort | means of files_changed / insertions / deletions / commits / tests.added (null insertions/deletions excluded from their mean) |
| Dispatches | counts by role and by model (a null/missing model buckets under `(none)`); dead rate (`outcome==dead` over dispatch entries); fallback rate (`resolution==fallback` over dispatch entries); `runs_with_dispatches`. Omitted entirely when no record in the store (or partition, in `--group-by` mode) carries the `dispatches` key at all — a record with `dispatches: []` still counts as carrying it. |

Every rate reports its denominator; a 0 denominator renders `n/a` (never a
divide-by-zero). `--group-by` partitions every metric (a missing complexity
buckets under `(none)`); **`--group-by version`** is the deterministic half of
the cross-skill A/B.

**Gate identity is keyed on `step`, not `step + name`.** Real stores carry the
same gate under drifting names (e.g. `4: Design Critique` vs `4: design critique
(3-judge + codex)`); keying on `step` (which the writer already enforces unique
*within* a record) keeps one gate's effectiveness from fragmenting across name
variants, and the distinct names ride along as a `names` label list. When a store
mixes workflows that reuse a step number for different gates, use `--group-by
workflow` for a clean read. (This is a deliberate refinement of issue #94's
literal AC2.)

### Fleet view — pool multiple stores (#115)

Run-records are written per project, so a workspace's telemetry is fragmented across
N stores. `aggregate` pools them in one pass — the fastest path to the record count
that makes metrics trustworthy:

```bash
# repeatable --store (explicit)
python3 hooks/work_summary.py aggregate --store a/run_records.jsonl --store b/run_records.jsonl --group-by source

# or resolve every ACTIVE project's default store from a workspace
python3 hooks/work_summary.py aggregate --workspace .rawgentic_workspace.json --group-by source
```

- **Origin-tagging (read-side, no schema change):** the record schema has no `project`
  field, so each record is tagged with its origin store/project as `_source` **at load
  time**. `--group-by source` then partitions the fleet by origin (project name in
  `--workspace` mode; the store path with repeatable `--store`).
- **Cross-store fail-closed:** a missing/unreadable store *among several* (or any store
  in `--workspace` mode) is **skipped with a visible stderr warning + a `missing_stores`
  count in `--json`**; the run still aggregates the rest. A **single** explicitly-named
  `--store` that is missing still exits 2 (single-store parity). This is the store-level
  analog of the per-line fail-closed reader below.
- **Caveat:** effort means are only loosely comparable across project *types* (a library
  PR vs an infra PR) — lean on `--group-by` rather than one blended fleet number.

**Fail-closed reader** (mirrors the fail-closed writer): a line that is unparseable
JSON, not an object, schema-invalid, or missing/non-ISO `generated_at` is
**excluded** with a `line N: <reason>` entry surfaced on stderr and counted in the
output — never silently averaged in or dropped. A missing/unreadable store (or a
NUL in the path) is a usage error (exit 2); an empty store renders a `0 records`
report (exit 0). Exit codes follow the tool convention: `0` success (including
corrupt-lines-excluded), `2` usage error.

## How the workflows wire it in

Each completion step (WF2 Step 16 → `/tmp/wf2-run-record-<issue>-<session-id>.json`; WF3 Step 14 →
`/tmp/wf3-run-record-<issue>-<session-id>.json`) assembles the run-record from the data gathered across
the workflow and shells out to the CLI. The tool's stdout **is** the completion
summary, so the skill presents it as-is rather than re-typing it. A drift-guard
test (`tests/hooks/test_work_summary.py::TestWorkSummarySkillWiring`,
parametrized over `implement-feature` and `fix-bug`) asserts each skill keeps
invoking `work_summary.py summarize`, so the wiring can't silently rot. Adding a
new workflow to the substrate is just: emit the uniform core (+ any `extra`
lines), call the CLI, and add the skill to that parametrized guard.

## Capturing an Action's built-in code review {#builtin-code-review-capture}

The post-PR `/code-review` lane (`.github/workflows/claude-code-review.yml`, #196)
runs the built-in reviewer as a **CI Action**, separate from the local WF2 run.
To feed the #162 AC4 A/B (built-in vs hand-rolled findings-yield-per-token), its
review is recorded as a run-record **gate entry** with `reviewer_kind:
builtin_code_review` **and** a `usage` block:

```json
{
  "step": "11",
  "name": "code_review (builtin /code-review, CI Action)",
  "reviewer_kind": "builtin_code_review",
  "findings": <N>, "resolved": <M>, "status": "pass"
}
```

with the run-level `usage.input_tokens` / `output_tokens` / `cost_estimate_usd`
taken from the **Action run's reported usage** (`gh run view <run-id> --json ...`
or the Action's step summary). The built-in lane is **additive** — it does not
replace the hand-rolled `hand_rolled_multi` Step 11 entry; a run that used both
records both gate entries, which is exactly what makes the two arms comparable.

Because the tokens live in the CI run (not the local orchestrator's context),
this capture is a **documented post-run step** (read the Action run's usage and
add the entry), not auto-wired — and the first real captures are **owner-gated**
on the live Action (the auth secret, #195). Until ≥10 accumulate, the #162
decision stays "computable, pending data" (see
`docs/measurements/2026-07-05-issue-162-data-gate-decision.md`).
