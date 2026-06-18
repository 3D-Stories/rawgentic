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
| `gates` | array | Each `{step, name, findings, resolved, status}`; `status` ∈ `pass`/`fail`/`skipped`/`fast_path`. Step 11.5 is captured in `security_scan`, not here. |
| `security_scan` | object | `ran` (bool), `blocking_resolved`, `advisory` (ints), `skipped` (list of strings). Mirrors the [Step 11.5 gate](security-scan.md). |
| `loop_backs` | object | `used`, `budget` (ints). |
| `outcome` | object | `pr_number` (int\|null), `pr_url` (string\|null), `merged` (bool\|null), `ci` (`passed`/`failed`/`not_configured`/`skipped`), `deploy` (`success`/`manual`/`failed`/`not_applicable`). |
| `follow_ups` | array | Optional list of strings; defaults to `[]`. |
| `extra` | array | Optional ordered `{label, value}` (both strings) pairs for **workflow-specific** human lines that ride along in the render without bloating the uniform core (e.g. WF3's `Root Cause` / `Fix`). Defaults to `[]`. |

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

## The store

One JSON line per run, appended to:

```
<project-root>/docs/measurements/run_records.jsonl
```

Override with `--store <path>` or the `RAWGENTIC_RUN_RECORD_STORE` env var (env
is configurable from v1). The default is committed and per-project, so the
telemetry is versioned and reproducible alongside the code it measures.

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
  --record-file /tmp/wf2-run-record.json \
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
  [--json] [--group-by {workflow,version,type,complexity}] [--since <ISO-date>]
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

**Fail-closed reader** (mirrors the fail-closed writer): a line that is unparseable
JSON, not an object, schema-invalid, or missing/non-ISO `generated_at` is
**excluded** with a `line N: <reason>` entry surfaced on stderr and counted in the
output — never silently averaged in or dropped. A missing/unreadable store (or a
NUL in the path) is a usage error (exit 2); an empty store renders a `0 records`
report (exit 0). Exit codes follow the tool convention: `0` success (including
corrupt-lines-excluded), `2` usage error.

## How the workflows wire it in

Each completion step (WF2 Step 16 → `/tmp/wf2-run-record.json`; WF3 Step 14 →
`/tmp/wf3-run-record.json`) assembles the run-record from the data gathered across
the workflow and shells out to the CLI. The tool's stdout **is** the completion
summary, so the skill presents it as-is rather than re-typing it. A drift-guard
test (`tests/hooks/test_work_summary.py::TestWorkSummarySkillWiring`,
parametrized over `implement-feature` and `fix-bug`) asserts each skill keeps
invoking `work_summary.py summarize`, so the wiring can't silently rot. Adding a
new workflow to the substrate is just: emit the uniform core (+ any `extra`
lines), call the CLI, and add the skill to that parametrized guard.
