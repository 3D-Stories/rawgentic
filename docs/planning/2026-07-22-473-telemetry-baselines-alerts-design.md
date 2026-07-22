# #473 — Telemetry baselines + alerts (W11, AC-K2/K3) — design

Issue: [#473](https://github.com/3D-Stories/rawgentic/issues/473) · Epic: #475 (W11) · Part of #475
Author: WF2 session 4f8eaed9, 2026-07-22 · Status: r7 (pass-5 final-verify fixes folded — §16;
all pass-1..5 findings resolved in body; awaiting owner gate-close decision)

## 0. Problem

The executor stack emits rich per-dispatch Observations (`.rawgentic/runs/<run-id>/routing-audit.jsonl`,
schema v2), but that store is **ephemeral and git-excluded** — cross-run learning reads nothing.
AC-I3 defines the durable home: a `seat-outcomes.jsonl` sidecar next to the I2 run-record store.
#473 ships that sidecar plus the first consumers: rolling per-seat × per-model **baselines**
(AC-K2) and **advisory alerts** at run end (AC-K3: never a gate). Thresholds become per-project
config staged through setup (AC-K5 / #446).

Verified constraints (analysis seat obs `0-edd3d337`; gate passes 1–4 verification):
- Guard tests forbid seat-outcomes code in `phase_executor/src/` and pin the README I3
  "DEFERRED" wording → code in `hooks/`; README + one guard flip in-PR (no-writer guards stay).
- Audit-log **observation envelope is `{"kind","receipt_nonce","observation"}`** — `run_id`
  lives INSIDE the inner Observation (`enforce.py:381`). Receipt/observation binding checks
  exist at `enforce.py:531-547`.
- The I2 run-record has no `run_id` and `gates[]` has no severity split (live record read);
  `validate_record(strict=True)` tolerates additive keys (verified live). The canonical
  run-record reference (`references/run-record.md:81`) requires severity counts be computed
  AT GATE CLOSE.
- `work_summary.load_store` raises on an absent store (`work_summary.py:868-874`).
- No quota taxonomy exists in `fallback_reason` (`engine.py:153`); quota classification ships
  shadow → no quota alert rule in v1 (deferred, named).
- Repo one-home atomic write: **`hooks/atomic_write_lib.py::atomic_write_text`** ("do not
  reimplement mkstemp/os.replace inline", its own docstring) — the store rewrite routes
  through it. Locking precedent: `fcntl.flock` (`notes-size-handler.py:93`).
- `canonicalize_model_id` returns `""` for degenerate non-null input (`contract.py:99`).
- Live bench reports can be aborted/partial (`driver_bench_lib.py:505`, `aborted` key);
  stubbed cells carry real model labels, live cells are labeled `"live"`.
- Version base: origin/main moved twice mid-run — now **`6c37338` (v3.90.2, PRs #582/#583)**;
  baseline re-recorded on it: **4579 passed / 17 skipped / 0 failed** (chunked, both rc=0).
  Version target **3.90.2 → 3.91.0** (re-verify all four surfaces at branch time).
- `contract.validate_observation` uses phase_executor's existing `jsonschema` dependency —
  #473 adds **no new** dependency.

## 1. Approach

One new hooks module (`hooks/seat_outcomes_lib.py`), one new durable store, additive I2 join
fields, four CLI verbs, one Step-16 prose block per WF. Rejected alternatives (dispatch-time
writes; extending `work_summary.py` in place) per r1–r3: idempotent run-end harvest beats a
per-dispatch durable writer; the I2 validator stays single-purpose.

## 2. Data flow

```
routing-audit.jsonl (per run, ephemeral)
        │  run-end (Step 16): per-entry validation — inner Observation schema + binding
        │  checks + inner run_id == --run-id → enum/grammar-bounded row derivation →
        │  alerts evaluated against history EXCLUDING this run → locked store rewrite
        │  (idempotent on (run_id, attempt_id))
        ▼
docs/measurements/seat-outcomes.jsonl   ← committed I3 sidecar; pending rows ride the NEXT PR
        │                                 staged BY NAME (extends the standing run_records.jsonl
        │                                 slot-15 convention — telemetry written after a merge
        │                                 rides the next PR, exactly as run-records do today;
        │                                 prose names BOTH files)
        │  baselines: rolling nearest-rank p50/p90 per (seat, canonical engine-reported model)
        ▼
alerts (fired only) ──▶ run-end --json `extra_rows` → folded into the run-record `extra` via a
                        python3 JSON read-modify-write (never shell interpolation) BEFORE
                        summarize; the orchestrator also appends the rendered advisory block
                        to session notes. EVERY telemetry step (run-end, JSON fold,
                        session-note append) is loud-log-and-continue on failure — no
                        telemetry failure may block Step 16 (drift-guarded prose, §5).
```

## 3. `hooks/seat_outcomes_lib.py`

Pure core + thin CLI (repo hook exemplar); imports `phase_executor.contract`
(validate/canonicalize/models_match) via the `_ensure_pe_importable` precedent
(`executor_routing_lib.py:1546`), `work_summary.load_store` for I2 history, and
`atomic_write_lib.atomic_write_text` for the store rewrite (one-home rule).

### 3.1 Row schema (`schema_version: "1"`) — enums + bounded identifiers, never free text

**Retention rule: no free text is ever committed.** Fields whose SOURCE defines a closed
vocabulary are validated as **enums mirrored exactly from `observation.schema.json`**:
`parse_status` (the schema's enum), `fallback.parse_status` (same enum), `canary_verdict`,
`work_product_ref.promotion_status`; token counts mirror the source's **integer** types.
The remaining identity fields pass a per-field **grammar allowlist** (identifier grammar
`^[A-Za-z0-9._:/-]{1,<cap>}$` — excludes spaces, `@`, backticks, brackets, quotes)
**followed by a mandatory path-shape rejection step** (`_reject_path_shape`): a grammar-passing
value is ALSO rejected (→ `null` + `redacted_fields`) when it has a leading `/`, any `..` or
`.` path segment, a Windows drive prefix (`^[A-Za-z]:`), a UNC prefix (`\\` / `//`), or two or
more `/` separators. Legitimate provider/model slashes survive (`us.anthropic.claude-x`,
`provider/model` — one internal `/`, no leading slash, no `..`); path-shaped values
(`/root/secret`, `/opt/x`, `/Users/y`, `../z`, `C:/w`) do not. A field failing EITHER step
becomes `null` + a loud `redacted_fields` counter — never stored raw, never truncated-and-kept.
JSON objects are parsed with a duplicate-key-rejecting `object_pairs_hook` (Python's default
last-wins is not accepted for rows or audit entries).

**Honest bound (not a leak-proof claim):** grammar + path-shape rejection block free text,
Markdown/mention/HTML metacharacters, space-separated secrets, and filesystem-path forms; the
NARROWED residual is an opaque token that is BOTH grammar-shaped AND non-path-shaped (e.g. a
20-char alphanumeric key) pasted by a future caller into `correlation_id` — a designed trace
key retained by decision. This residual and the retention of exact activity timestamps/counts
in git history are documented in `docs/run-records.md`. Redaction tests **assert the field is
`null`** (not merely absent from the serialization) for every path form and free-text form,
and assert `redacted_fields` incremented; a separate test documents the narrowed residual with
a grammar-valid non-path sentinel.

| field | source | contract |
|---|---|---|
| `schema_version` | `"1"` | unknown keys rejected for v1 |
| `run_id`, `attempt_id` | inner Observation | idempotency key; grammar, caps 120 |
| `correlation_id` | Observation | grammar, cap 128 (trace key; never parsed for issue) |
| `issue` | validated `--record-file` ONLY (its `issue.number`, required **> 0** at this surface; record must pass `validate_record` and its `run_id` must equal `--run-id`) | int or null; standalone `harvest` without a record writes null. **Enrichment (merge-rule exception):** an existing row whose content digest matches and whose `issue` is null MAY be upgraded to a validated positive `issue` by a later run-end (atomic rewrite); non-null↔non-null conflict = loud exit 1 |
| `seat`, `engine` | Observation | grammar, cap 64 |
| `parse_status` | Observation | **enum (source-mirrored)** |
| `requested_model`, `actual_model` | Observation | grammar, cap 120 — `actual_model` is the ENGINE-REPORTED identity (named honestly; no cryptographic attestation exists) |
| `model` | `canonicalize_model_id(actual_model)`; null when actual is null OR the canonical result is empty (`contract.py:99`) | canonical join key |
| `models_match` | `models_match(requested, actual)`; null when actual null | |
| `lane` | `dispatched_lane` → `{provider, transport, auth_mode, pool}` | grammar cap 64 each; `credential_ref` NEVER copied |
| `fallback` | structured, never raw text: parse `fallback_reason` against the engine format (`engine.py:153`) → `{"kind":"model_fallback","from_model":<grammar>,"parse_status":<enum>}`; unparseable non-null → `{"kind":"other"}`; null → null | raw reason string never committed |
| `usage` | `{input, output, cached, cost_proxy}` | input/output/cached **integers** (source-mirrored), cost_proxy finite number; all ≥0; null-tolerated |
| `timing_ms`, `queued_ms` | Observation | real ints (bool rejected), ≥0 |
| `exit_code`, `timed_out` | `process` | |
| `canary_verdict` | `canary_result["verdict"]` when dict-with-string-verdict; else null | **enum**, cap 32 |
| `work_product_ref` | `{content_tree_sha, base_sha, head_sha, promotion_status}` when present | SHAs `^[0-9a-f]{40,64}$`; status **enum**; paths/documents/tests never copied |
| `hook_denials` | Observation | int ≥0, null-tolerated |
| `budget` | `{reserved_usd, spent_usd}` | finite ≥0; null-tolerated |
| `experiment_id`, `arm` | null | AC-K4 stubs |
| `recorded_at` | harvest time, strict ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`, parse-validated) — **ingestion time, not run time** | excluded from content digest |

**Content digest:** SHA-256 over `json.dumps(row, sort_keys=True, separators=(",",":"))`
UTF-8, excluding `recorded_at` and `issue`. Key-order-independence tested.

**Denylist (value-sweep test):** `worktree_path`, `tmux_session`, `raw_capture_path`,
`worktree_id`, `credential_ref`, `prompt_hash`, `context_hashes`, `parsed_payload`,
`fallback_reason` (raw), denial events. Tests sweep serialized rows for denylisted keys,
free-text survivals, and path forms (POSIX roots incl. `/root`,`/opt`, macOS `/Users`,
Windows drive + UNC, relative `../`) — structurally unreachable past the enum/grammar
contracts, and the tests prove it.

`validate_seat_outcome(row)` — strict fail-closed (types, source-mirrored enums, integer
token counts, bool-as-int rejected, finite numbers, grammars, strict-UTC `recorded_at`,
unknown keys rejected, idempotency-key presence). Applied to derived rows AND to every
stored v1 row on every read.

### 3.2 Harvest — bound, validated, locked non-destructive rewrite

Per audit line: duplicate-key-rejecting JSON parse (fail → `skipped_malformed`) → keep
`kind=="observation"` → `contract.validate_observation(inner)` (fail →
`skipped_invalid_observation`) → **inner `observation["run_id"] == --run-id`** (the envelope
has no run_id — `enforce.py:381`) → **binding checks** against the same file's receipts
indexed by nonce: receipt exists, **receipt `verdict == "pass"`**, **no duplicate nonce on
either side**, **observation `attempt_id` == the receipt's**, seat match, correlation match,
target-identity + config-digest match (`enforce.py:531-547` parity) — any failure →
`skipped_unbound`, loud. **Honest scope:** binding detects accidental orphans, drift, and
post-write tamper; receipts share the same mutable gitignored file, so this is NOT a
hostile-forgery boundary and no MAC is added — stated, not implied away.

**Bounds:** audit file ≤10MB and ≤500 observation entries per harvest — exceeding either
**aborts the harvest loudly before any write** (an over-bound audit is an anomaly; no partial
take). Line cap 64KB. Config/bench/record files ≤1MB each (over → that input unavailable,
loud). The DURABLE store has **no byte cap** (it must grow). Reads are **streamed** so the
per-group metric working set stays window-bounded, BUT the dedup **key/digest index is
O(number of rows)** — it must hold every existing `(run_id, attempt_id)` to skip duplicates,
so harvest memory grows linearly with the store. This is declared honestly (NOT "bounded"):
#473 ships a tested **soft cardinality expectation** — `run-end` emits a
`telemetry: sidecar large (N rows)` advisory once the store exceeds `SIDECAR_SOFT_MAX_ROWS`
(default 50_000) — and store **compaction before that limit** is the named follow-up in
`docs/run-records.md` (the disk-backed SQLite-index alternative is recorded there as the
upgrade path). At telemetry volumes (tens of rows/run) the linear index is immaterial for
years; the advisory surfaces the ceiling before it bites. A missing capture dir yields rows 0
**plus a visible `telemetry: no capture dir` advisory in the run-end output** — silence never
reads as success.

**FD-hardened opens (TOCTOU-safe, ALL six inputs — store, lockfile, audit file,
`--record-file`, config, bench):** `os.open` with `O_NOFOLLOW` (lockfile creation
`O_CREAT`), then `fstat` the OPENED fd (regular file) and parse from that fd. Because
`O_NOFOLLOW` guards only the final component, the default store/lock/quarantine live in a
**preflight-verified directory fd**: `os.open(project_root/"docs/measurements", O_DIRECTORY|
O_NOFOLLOW)`, `fstat`-checked, and every default-path open/rename is `dir_fd`-relative
(`os.open(..., dir_fd=)`, `os.replace(src, dst, src_dir_fd=, dst_dir_fd=)`) so an ancestor
symlink swap cannot redirect the write. No `lstat`-then-open.

**Store write = locked, redaction-safe read-repair-rewrite** (reconciles pass-3 "never
destroy data" with pass-5 "never re-commit un-redacted content"):
1. `fcntl.flock(LOCK_EX)` on the `dir_fd`-relative `seat-outcomes.jsonl.lock` **first, before
   opening the store** — the store read + evaluation + rewrite ALL happen under the one held
   lock (closes the read-before-lock race).
2. Stream-read the store from the verified fd; per line:
   - valid v1 row → index key + digest, kept in the committed store.
   - valid-JSON **future/unknown `schema_version`** → **passed through byte-verbatim** into
     the committed store (forward-compat; a newer writer's redaction contract is ≥ current),
     counted `passed_through`.
   - **invalid-but-parseable interior line** (fails the current v1 validator) → **moved to the
     gitignored `seat-outcomes.jsonl.quarantine`, NOT re-committed** — durability is preserved
     for diagnosis without re-committing content that never passed redaction; counted loud.
   - provably-torn **terminal** fragment (no trailing newline + JSON parse fails) → quarantine,
     loud.
   The committed store therefore only ever contains valid v1 rows (all redaction-passing by
   construction) + forward-compat future-version rows; nothing is silently deleted (everything
   excluded goes to the gitignored quarantine).
3. Merge: skip existing `(run_id, attempt_id)` keys with equal digests; the ONE exception is
   `issue` enrichment (§3.1: null → validated positive, digest unchanged); same key +
   different content digest → **abort before any write** (exit 1, named keys, original
   untouched).
4. Rewrite via `atomic_write_lib.atomic_write_text` (the one-home tmp+replace helper) +
   parent-dir fsync. Unlock. **`.gitignore` gains `docs/measurements/seat-outcomes.jsonl.lock`
   and `…​.quarantine`** (the committed `seat-outcomes.jsonl` is tracked; its lock/quarantine
   siblings are not) — a guard test asserts both are ignored.
Idempotent re-run appends nothing. Absent default store = first run (empty history, note);
explicit unreadable `--store` = usage error exit 2. (The I2 store's own unlocked append is
pre-existing behavior, out of scope — named follow-up; the review-baseline read tolerates a
torn terminal I2 line.)

### 3.3 Baselines (AC-K2)

`compute_baselines(rows, *, exclude_run_id=None, min_n=5, window=30)` — group by
`(seat, model)` over canonical engine-reported models; rows with `model == null` are reported
as `unknown_model_rows` (count ONLY — no metrics object, no baseline). Window ordering key =
`(recorded_at, run_id, attempt_id)` — deterministic under equal timestamps; semantics are
**ingestion order** (documented: a delayed recovery harvest ingests late by design).
`exclude_run_id` drops the evaluated run's rows.

**Closed output schema — exact, enumerated (drift-tested field by field):** top level =
`{"groups": {...}, "unknown_model_rows": int, "review_findings": {...}|null,
"bench_anchors": {...}, "notes": [str]}`. Every group = exactly four metrics:
`timing_ms` + `cost` (percentile objects `{n, missing, status, p50?, p90?}`) and
`fallback_rate` + `mismatch_rate` (rate objects `{n, missing, status, numerator?, value?}`).
No other metric keys exist in v1. ALL metric objects carry `status ∈
{ok, insufficient_history}` (`n < min_n` → numeric fields ABSENT — never fabricated).
**Denominator semantics per metric:** `timing_ms`/`cost` — n = rows where the value is
present and finite, `missing` = the rest. `fallback_rate` — **`fallback` is ALWAYS
observable** (null means "primary served", a real measurement): n = group size, numerator =
rows with `fallback != null`, `missing` = 0 by construction. `mismatch_rate` — observable
only when `models_match` is non-null: n = non-null rows, numerator = `false` rows. Group
keys `"<seat>|<model>"` in sorted order (deterministic). p50 = `statistics.median`; p90 =
nearest-rank `sorted[ceil(0.9n)-1]`. `cost` = `usage.cost_proxy` falling back per-row to
`budget.spent_usd`.

**Review baseline:** `compute_review_baseline(i2_records, *, workflow, exclude_run_id,
window, min_n)` — the current workflow is an explicit argument (the function filters).
Eligible records: `workflow` matches, **non-null grammar-valid `run_id`** (the dedupe key —
records without one are ineligible, counted), BOTH severity fields present (§3.6),
deduplicated by `run_id` ("latest" = last occurrence in store order), `run_id !=
exclude_run_id`, latest `window` eligible distinct runs. Output: the same closed
percentile-metric object over Σ(critical+high), under `baselines["review_findings"]`.
Timing note: at run-end time the current record is not yet persisted to the I2 store
(`summarize` runs after), so self-dilution cannot occur even before the explicit exclusion.

**Bench anchors — one deterministic policy:** *stubbed*
(`docs/measurements/driver-bench/stubbed-baseline.json`): validate
`{cells:[{fixture,model,rep,scores}]}`; per-model dimension means recomputed from cells;
scores must be finite numbers (typed-object/null scores skipped + counted). *live*:
candidates = `live-<ts>.json` ordered by the **validated filename timestamp** (mtime never
used; unparseable filename → skipped + noted). **The newest candidate is always the reported
anchor**: if it carries `aborted` it is reported with `status: "partial"` (named), and the
newest non-aborted candidate is ALSO exposed under `last_completed` — selection and status
are one rule, no skip-vs-report contradiction. Live cells are labeled `"live"` → reported
campaign-level, never per-model. Anchors are quality references, never merged into
timing/cost percentiles. Missing/malformed file → that anchor `status: "unavailable"` +
note; `stubbed` and `live` carry independent statuses.

### 3.4 Alerts (AC-K3) — advisory, never a gate

`evaluate_alerts(record, run_rows, baselines, thresholds) -> list[<evaluation result>]` —
pure; **closed evaluation-result schema**:

```json
{"rule": "seat_wall_time_p90", "status": "fired|not_evaluated|disabled",
 "reason": "ok|no_baseline|missing_input|disabled",
 "advisory": true, "seat": "...", "model": "...",
 "observed": 912000, "threshold": 90000, "baseline_n": 12, "message": "<fixed template>"}
```

**Cardinality:** statistical row-level rules (`seat_wall_time_p90`, `seat_cost_p90`) emit at
most ONE result per (rule, seat, model) group, citing the worst offender + an exceedance
count; record-level and unconditional rules emit exactly one result per rule. **Per-rule-class
field contract:** row-level rules populate seat/model/observed/threshold/baseline_n;
record-level rules (`dispatch_failures`, `review_findings_p90`, `fallback_fired`,
`model_mismatch`, `parse_failure`) carry null seat/model unless a single row is implicated;
`not_evaluated`/`disabled` results carry rule/status/reason with numeric fields null —
required-vs-null is declared per class and validated in tests. **Global disable
(`enabled: false`) returns one `disabled` result per rule** (deterministic, observable — §4's
wording defers to this). **Only `status=="fired"` renders** as alerts/`extra_rows`;
`not_evaluated`/`disabled` appear in `--json` only. Messages are fixed templates + numeric
evidence + enum/grammar-bounded identifiers (hostile-string tests run row→alert→
`render_summary` end-to-end — `extra.value` renders verbatim, `work_summary.py:740`).

| rule | class | fires when | default |
|---|---|---|---|
| `fallback_fired` | uncond | run rows with `fallback != null` > threshold | 0 |
| `dispatch_failures` | uncond | record `dispatches[]` outcomes {`error`,`dead`} > threshold — documented as **broad dispatch failure** (no canary discriminator exists in that vocabulary) | 0 |
| `model_mismatch` | uncond | any run row `models_match == false` | on |
| `parse_failure` | uncond | any run row `parse_status != "ok"` | on |
| `seat_wall_time_p90` | stat | run row `timing_ms` > group baseline p90 | on |
| `seat_cost_p90` | stat | run row cost > group baseline p90 | on |
| `review_findings_p90` | stat | Σ(findings_critical+findings_high) over this record's gates > review-baseline p90 | on |

**Deferred (no producer signal): quota-pause rule** — lands when genuine quota capture
activates (#559 OPS follow-up); documented in `docs/run-records.md`.

### 3.5 CLI + Step-16 contract

```
harvest         --run-id <id> --project-root <root> [--record-file <f>] [--store <p>] [--capture-root <p>]
baselines       --project-root <root> [--store <p>] [--json]
run-end         --run-id <id> --record-file <f> --project-root <root> [--json]
validate-config --json <telemetryAlerts block>          (setup Step 2j's strict validator, §4)
```

`run-end` **strict-validates `--record-file` first** (`work_summary.validate_record`); when
the record carries `run_id` it must equal `--run-id` (mismatch = usage error); a record
without `run_id` → loud warn + `issue: null`. Order: validate record → read+validate audit →
load history → `compute_baselines(history, exclude_run_id=...)` +
`compute_review_baseline(..., workflow=record.workflow, ...)` → evaluate → locked
non-destructive rewrite (append + any issue enrichment) → print advisory block / `--json
{rows_appended, skipped_*, redacted_fields, passed_through, evaluations, alerts, extra_rows,
advisory_block}`. `harvest` without `--record-file` always writes `issue: null` (recovery
tool; a later run-end enriches per §3.1).

`extra_rows` = ready-made `{"label": "telemetry-alert:<rule>", "value": "<template>"}` for
**fired** results only, capped 20 + a truncation row. Step-16 prose (drift-guarded, §5):
call `run-end` before `summarize`; fold `extra_rows` via a `python3` JSON read-modify-write
into the assembled record; **every telemetry step — the run-end invocation, the JSON fold,
and the session-note append — is individually loud-log-and-continue on failure** (telemetry
can never block Step 16); append the advisory block to session notes; set the record's
`run_id` and per-gate severity fields when assembling.

Exit codes: 0 ok (alert presence never changes exit), 1 internal fault/digest conflict,
2 usage error.

### 3.6 Additive I2 fields (join + severity gaps)

`hooks/work_summary.py` additive, backward-compatible:
- top-level `run_id` (grammar-validated when present) — the I3↔I2 join key.
- per-gate `findings_critical`, `findings_high` — **both-or-neither** (one without the other
  is a validation error); each int ≥0; `findings_critical + findings_high ≤ findings`.
Legacy records without them: tolerated everywhere, ineligible for the review-baseline scan.

**Severity capture is a gate-close duty** (`references/run-record.md:81` already requires
gate-close computation): `run-record.md` + the WF2/WF3 gate-close prose gain the persistence
instruction — deduplicated Critical/High per gate, recorded in the gate's session-note
marker; **a finding whose severity changes across passes counts at its FINAL severity at
that gate's close** (the terminal disposition's severity).

## 4. Config (AC-K5 / #446): `telemetryAlerts`

```json
"telemetryAlerts": {"version": 1, "enabled": true, "windowSize": 30, "minSamples": 5,
                    "thresholds": {"fallback_fired": 0, "seat_wall_time_p90": true}}
```

Value contract, per rule: count rules (`fallback_fired`, `dispatch_failures`) take
`false | non-negative real int` (`false` checked before the real-int guard — disables);
toggle rules take bool. `version` == 1; `windowSize` int 1..1000; `minSamples` int
1..windowSize; NaN/Inf rejected; bool-as-int rejected. `enabled: false` disables evaluation —
run-end still harvests, and `evaluate_alerts` returns one `disabled` result per rule (§3.4).

**Parse order guards operator intent:** `enabled` is parsed FIRST and independently — a
valid `enabled: false` beside a malformed sibling key still disables (a whole-block
fail-open may never override an explicit disable); only the remaining keys degrade to
defaults, with ONE loud stderr advisory.

**Two validation postures, one shared validator:** `validate_telemetry_alerts(block) ->
list[str]` implements the strict contract (unknown keys rejected); the `validate-config`
CLI verb exposes it and **setup Step 2j invokes that verb before staging** (customize path);
runtime `load_thresholds` calls the SAME function and downgrades failures to the fail-open
above. Parity tests: setup-produced default/custom/disabled blocks round-trip through the
runtime loader with identical effective thresholds. Setup choices: accept defaults → stage
sentinel `{"version": 1}`; customize → stage validated explicit block; decline → no key
written (absent ≡ defaults; declining declines customization, not alerting — disabling is
the explicit `enabled: false`).

Canonical surfaces in-PR: `templates/rawgentic-json-schema.json` (single source of truth —
annotated `telemetryAlerts` block) + `docs/config-reference.md` + setup Step 2j prose +
`references/integrations.md`; drift tests assert template/config-reference/
`DEFAULT_THRESHOLDS` rule-id agreement AND documented defaults == code defaults.

## 5. Prose + docs changes

- WF2 `references/steps.md` §16 + WF3 `references/steps.md` Step-16 analog: the §3.5
  contract block (incl. the per-step loud-continue clause). WF2 `references/run-record.md`:
  `run_id` + gate severity fields + gate-close capture duty incl. the final-severity rule.
  Slot-15 telemetry-rider prose extended to name BOTH `run_records.jsonl` AND
  `seat-outcomes.jsonl` (parity with the standing convention — telemetry written post-merge
  rides the next PR staged by name; drift guards are the enforcement mechanism, the repo's
  architecture for workflow behavior).
- Setup: SKILL.md Step 2j + `references/integrations.md`.
- `phase_executor/README.md` I3 section: DEFERRED → shipped (store path, hooks module,
  idempotency key, redaction contract, work_product_ref subset); pe-boundary restated.
- `docs/run-records.md`: I3 sidecar section (schema, verbs, rules incl. deferred quota rule,
  retention rationale for timestamps/counts, commit-flow, follow-ups: store compaction,
  I2 append locking).
- `README.md`: blurb + Changelog (diagram decision + Suite old→new tails).

## 6. Tests — `tests/hooks/test_seat_outcomes.py` (+ guards)

Real-envelope fixtures via `RoutingAuditLog.append_observation()` (not hand-rolled dicts).
Per §3 behaviors plus: binding skips (all seven check classes incl. verdict/dup-nonce/
attempt_id); inner-run_id filter; enum mirroring (parse_status/canary/promotion) +
integer token counts; duplicate-JSON-key rejection; grammar redaction (secret sentinels
incl. a grammar-VALID one asserting the documented residual, all path forms, Markdown/
mention metacharacters) end-to-end through `render_summary`; two-process concurrent harvest
(no dup keys, no torn store); NON-DESTRUCTIVE rewrite (future-version + invalid interior
lines byte-preserved; terminal fragment quarantined); issue-enrichment (null→positive ok,
conflict exit 1); digest key-order independence + conflict abort-before-write; streamed
store read bounded-memory behavior; over-bound audit aborts with no write; closed baselines
schema per field incl. fallback_rate full-denominator math + rate statuses + unknown-group
count-only; recorded_at strict-UTC + window tie-break key; review-baseline
workflow-arg/run_id-eligibility/dedupe/exclusion/window; bench newest-is-reported policy
(aborted→partial + last_completed) + typed-score skip + filename-timestamp ordering;
evaluation-result schema per rule class + cardinality (worst-offender single result) +
global-disable one-per-rule; count-rule `false` disable; enabled-first parse (malformed
sibling never overrides disable); both-or-neither severity + sum bound; run-end record
validation + run_id agreement; first-run absent stores; pristine committed-sidecar guard;
WF2+WF3 prose drift guards (run-end ordering, JSON fold, per-step loud-continue,
session-note append, severity gate-close capture); `test_i3_aggregation_docs.py`
deferred→shipped flip (no-writer guards kept verbatim). Gate: scoped per task; FULL
`/home/rocky00717/.local/bin/pytest tests/ -q` exactly twice (Step 2 re-record done:
4579/17/0 @ 6c37338; Step 9).

## 7. Scope discipline (NOT in this PR)

No learning loop / autonomous routing (AC-K4 stubs); no quota rule (§3.4); no trend
analysis; no WF14/WF17 code changes; no dashboard; no store compaction and no I2-append
locking (named follow-ups); no legacy backfill; no receipt MAC (§3.2 honest scope).

## 8. Security implications

Committed-telemetry surface bounded by construction: source-mirrored enums + grammar
allowlists (no free text), structured fallback, binding checks against orphan/tamper,
FD-hardened O_NOFOLLOW opens on every input, fixed-template + bounded-identifier rendering,
duplicate-key-rejecting parses, bounded volumes with abort-before-write. Honest residuals
stated: grammar-shaped opaque values in trace keys; no hostile-forgery boundary on the
same-file audit log; activity-timestamp retention in git history.

## 9. Error handling / failure modes

Per §3.2/§3.5. A run that dies before Step 16 loses rows unless `harvest` is re-run before
capture cleanup — documented recovery path (writes `issue: null`; a later run-end enriches).

## Platform / external dependencies

platform_apis:
- api: fcntl.flock (LOCK_EX) + os.open O_NOFOLLOW on the POSIX runtime (Linux host + ubuntu CI)
  feasibility: verified via existing-call-site — hooks/notes-size-handler.py:93 uses fcntl.flock in this repo today; O_NOFOLLOW/fstat are exercised by this PR's own tests on the same runtime
  failure: fail-loud
  surface: harvest aborts with a named error when lock/open fails — tests assert the loud path

(No NEW dependency: remaining stdlib `statistics`/`hashlib`/`json`/`argparse`;
`contract.validate_observation` uses phase_executor's EXISTING `jsonschema` dependency
(`contract.py:410`); `work_summary.load_store` + `atomic_write_lib.atomic_write_text`
same-repo.)

## 10. Versions / multi-PR

Single PR (~900-1100 lines with tests; one coherent feature). Version ×4 (all four surfaces:
`.claude-plugin/plugin.json`, `plugins/rawgentic/.codex-plugin/plugin.json`,
`test_adversarial_review_registration.py::test_plugin_version_bumped`,
`canary.py::EXPECTED_PLUGIN_VERSION`): **current origin/main (3.90.2 @ `6c37338`) → 3.91.0**,
re-verified at branch time (main moved twice during this design phase). canary.py edit =
synchronization-only, no phase_executor pkg bump (#449 precedent).

## 11. Peer-consult provenance (unchanged from r2 — git history has the full list)

Adopted: nearest-rank percentiles; exclude-current-run; unconditional/statistical split;
attested-only model identity; lane in row; parse_failure rule; window/minSamples knobs.
Declined: harvest-inside-summarize; capabilities_lib derivation; line-index outcome_id;
windowDays/severity levels; `dispatch_*` renames.

## 12. Pass-1 gate revision ledger (volume loop-back #1) — see git history for the full table

Self-review 8H/8M/3L + adversarial 4H/3M, all folded in r3: I2 run_id + severity fields;
quota rule deferred; locked idempotent write; consume-time validation; redaction contract;
extra_rows fold contract; first-run stores; exclude_run_id; closed config contract; canonical
config surfaces; closed baselines schema; bench two-shape extraction; prose drift guards;
work_product_ref subset; dispatch_failures honest naming; canary.py sync-only bump;
store-read ceiling; session-note append; sidecar commit flow; canary_verdict verbatim.

## 13. Pass-2 gate revision ledger (volume loop-back #2) — see git history for the full table

Self-review 7H + adversarial 4H/4M folded in r4: inner-run_id filter (enforce.py:381);
receipt binding; grammar allowlists; telemetry-rider prose; gate-close severity capture;
locked rewrite; FD/containment; record validation + record-sourced issue; markdown-safe
identifiers; stored-row revalidation; volume bounds; count-rule false|int; strict-setup/
fail-open-runtime split; bench eligibility; empty-canonicalize null; review-baseline
dedupe/exclusion/window; evaluation-result schema; severity-field validation; digest
canonicalization. DECLINED: I2-writer locking (follow-up); beyond-drift-guard prose
enforcement. REFUTED at the time: the 3.90.1 base claim (main later moved — §0).

## 14. Pass-3 gate revision ledger (owner-granted loop-back #3)

Owner grant: iMessage "Override +1 for 473". Resolutions (r5-intended; §15 records that the
r5 edit application partially failed and r6 implements them in body): enums-not-just-grammar
+ honest leak bound (SR3-F1/F11); binding checks verdict/dup-nonce/attempt_id + narrowed
claim (SR3-F2); non-destructive rewrite (SR3-F3/AR3-F2); ingestion-order window key
(AR3-F1/SR3-F12); input bounds (SR3-F4/AR3-F7); O_NOFOLLOW+fstat (SR3-F5); issue enrichment
+ issue>0 (SR3-F6/AR3-F8); enumerated baseline schema (SR3-F7); review-baseline workflow arg
(SR3-F8); per-rule-class result fields + global disable (SR3-F9); deterministic live-anchor
policy (SR3-F10); executable setup validation (SR3-F13); base refresh (SR3-F14); capture-dir
visibility (AR3-F3); run-end I2 timing note (AR3-F4); attempt_id collision via digest abort
(AR3-F5); POSIX platform declaration (AR3-F6).

## 15. Pass-4 gate revision ledger (r5 text repair + new amendments, 2026-07-22)

Pass 4 reviewed MIXED text — r5's §3.2/§3.5/§4 section edits had silently no-opped
(str.replace needle misses), so several findings re-reported §14 resolutions absent from the
body; r6 is a verified full rewrite implementing them. Genuinely NEW pass-4 amendments,
all folded:

| finding | resolution in r6 |
|---|---|
| ADV4-F1 store 10MB cap vs growth | durable store uncapped; streamed reads, bounded per-group memory; compaction follow-up (§3.2) |
| ADV4-F3 / SR4-F5 fold/session-note failure surfacing | per-step loud-log-and-continue clause, drift-guarded (§2/§3.5) |
| ADV4-F4 / SR4-F17 enrichment vs merge rule | explicit merge-rule exception for null→positive issue (§3.1/§3.2) |
| ADV4-F9 fcntl platform declaration | platform_apis block with existing-call-site evidence (§Platform) |
| SR4-F1 fallback_rate denominator math | fallback always observable — n = group size, null = primary served (§3.3) |
| SR4-F3 "attested" overclaim | renamed engine-reported identity (§3.1/§3.3) |
| SR4-F11 review-baseline run_id eligibility | non-null grammar-valid run_id required (§3.3) |
| SR4-F13 alert cardinality | one result per (rule,seat,model), worst offender + count (§3.4) |
| SR4-F14 atomic-write one-home rule | `atomic_write_lib.atomic_write_text` reused — no inline mkstemp (§0/§3.2) |
| SR4-F15 base stale again | 6c37338 / 3.90.2 → 3.91.0, re-verify at branch time; baseline 4579/17/0 re-recorded (§0/§10) |
| SR4-F16 enabled:false vs malformed sibling | enabled parsed first, independently (§4) |
| SR4-F19 duplicate JSON keys | duplicate-key-rejecting object_pairs_hook (§3.1/§3.2) |
| SR4-F20 500-row bound semantics | over-bound audit aborts loudly, no partial take (§3.2) |
| SR4-F21 global-disable contradiction | §4 defers to §3.4: one disabled result per rule (§3.4/§4) |
| SR4-F22 severity change across passes | final severity at gate close (§3.6) |
| SR4-F9 timestamp retention minimization | retention rationale documented in run-records.md (§3.1/§5) |
| SR4-F10 rider vs post-merge timing | named parity with the standing run-records slot-15 flow (§2/§5) — prior disposition upheld |
| SR4-F7 grammar-valid sentinel objection | prior pass-3 disposition upheld (honest-residual documentation + test); no change |

## 16. Pass-5 final-verify revision ledger (owner's granted verify pass, 2026-07-22)

Owner granted ONE final verification pass (rule: clean → proceed; new Crit/High → block).
Pass 5 was NOT clean — 2 adversarial High + 2 self-review High — but all four were concrete
claim-fixes/reconciliations with exact reviewer recommendations, folded here (spec-tightening,
no architecture change). Presented to the owner for the gate-close decision rather than
self-authorizing.

| finding | resolution in r7 |
|---|---|
| ADV5-F2 / SELF5-F1 grammar accepts path-shaped values | mandatory `_reject_path_shape` step after the grammar (leading `/`, `..`/`.` segments, drive/UNC prefix, ≥2 `/`); legitimate single-slash provider/model ids survive; redaction tests assert `null` not mere absence (§3.1) |
| ADV5-F1 / SELF5-F7 "memory stays bounded" false with all-row index | claim corrected to honest O(rows); soft-cardinality advisory `SIDECAR_SOFT_MAX_ROWS` (50_000) + compaction/ SQLite-index follow-up named (§3.2) |
| SELF5-F2 preserving invalid interior lines re-commits un-redacted content | invalid interior rows → gitignored `.quarantine` (durability kept, committed store holds only valid + forward-compat rows); reconciles pass-3 "never destroy" with pass-5 "never re-commit unredacted" (§3.2) |
| SELF5-F3 O_NOFOLLOW guards only final component | preflight-verified directory fd; all default-path opens/renames dir_fd-relative (ancestor-symlink swap blocked) (§3.2) |
| SELF5-F13 / ADV5-M read-before-lock race | flock acquired FIRST; read+evaluate+rewrite all under the one held lock (§3.2) |
| SELF5-F14 lock/quarantine beside committed file | `.gitignore` entries for `…​.lock` + `…​.quarantine` + guard test (§3.2) |
| ADV5-M / SELF5-F6 audit dir sanitize_component parity | harvest derives the capture dir via the SAME `capture.sanitize_component(run_id)` the writer uses (enforce.py:352-360) — implementation note for T2 |
| SELF5-F8 recorded_at second-precision ties | window tie-break already (recorded_at, run_id, attempt_id); documented as ingestion-order, sub-second ties broken by the id pair (§3.3, existing) |
| SELF5 Mediums/Lows (parse_failure granularity, bench bool/dim validation, extra_rows truncation ordering, exit_code/timed_out row contract, PR-size estimate) | T-level implementation detail — folded into the plan's per-task RED lists; not design-blocking |
| ADV5-M fcntl spike evidence | platform_apis block cites existing-call-site (flock) + this PR's own O_NOFOLLOW tests as the spike (§Platform) |
