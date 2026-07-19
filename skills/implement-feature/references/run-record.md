# WF2 run-record schema (Step 16)

Assembled at WF2 Step 16 and validated + persisted by `hooks/work_summary.py`. The tool is
the source of truth for the shape: on `rc == 1` it prints to stderr exactly which fields
are wrong (fix `/tmp/wf2-run-record-<issue>-<session-id>.json` and re-run).

Every key below must be **present**; "nullable" means `null` is an allowed value, NOT that
the key may be omitted (a dropped field is a telemetry gap, not a null). Counts are
non-negative integers and `resolved` may not exceed `findings`:

```json
{
  "workflow": "implement-feature",
  "workflow_version": "<.claude-plugin/plugin.json version>",
  "issue": {"number": <issue # | null>, "type": "feature|bug|chore|other",
            "complexity": "trivial|standard|complex|null"},
  "changes": {"files_changed": N, "insertions": N|null, "deletions": N|null,
              "commits": N},
  "tests": {"added": N, "passing": N|null, "total": N|null},
  "gates": [
    {"step": "4",  "name": "Design Critique",       "findings": N, "resolved": N, "status": "pass|fail|skipped|fast_path",
     "reviewer_kind": "inline|reflexion|builtin_code_review|codex|hand_rolled_multi"},
    {"step": "6",  "name": "Plan Drift",            "findings": N, "resolved": N, "status": "..."},
    {"step": "8a", "name": "Per-task Review",       "findings": N, "resolved": N, "status": "..."},
    {"step": "9",  "name": "Implementation Drift",  "findings": N, "resolved": N, "status": "..."},
    {"step": "11", "name": "Code Review",           "findings": N, "resolved": N, "status": "..."},
    {"step": "15", "name": "Post-Deploy",           "findings": N, "resolved": N, "status": "..."}
  ],
  "security_scan": {"ran": true|false, "blocking_resolved": N, "advisory": N,
                    "skipped": ["<kind>", ...]},
  "loop_backs": {"used": N, "budget": 3},   // used = counters-file total, never memory (#512)
  "outcome": {"pr_number": N|null, "pr_url": "<url>"|null, "merged": true|false|null,
              "ci": "passed|failed|not_configured|skipped",
              "deploy": "success|manual|failed|not_applicable"},
  "follow_ups": ["<any item requiring future attention>", ...],
  "lane": "small-standard|full",
  "verification_deferred": [{"task_id": "<id>", "reason": "<why the dev env can't exercise it>",
                            "local_proxy": "<what WAS run locally>",
                            "target_check": "<exact manual check on the target>"}, ...],
  "usage": {"input_tokens": N|null, "output_tokens": N|null, "cost_estimate_usd": N|null,
            "wall_clock_s": N|null,
            "model_mix": {"<model>": {"input_tokens": N|null, "output_tokens": N|null}, ...}|null,
            "capture_status": "captured|unrecoverable|unavailable"},
  "dispatches": [{"role": "review|implementation|analysis|other", "subagent_type": "<type>",
                  "model": "<model>"|null, "effort": "<effort>"|null,
                  "outcome": "ok|error|retried|dead", "resolution": "primary|fallback|generic"}, ...],
  "goal_guard": "set|skipped|fired"
}
```

The `gates` array carries whichever gates actually ran (Step 11.5 is captured in
`security_scan`, not as a gate row). Use `status: "fast_path"` for a gate the fast path
replaced, `"skipped"` for one that didn't apply.

**Canonical gate names (#116).** Use the EXACT `name` for each step from the single source
of truth `work_summary.CANONICAL_GATE_NAMES` (`canonical_gate_name(workflow, step)` — keyed
by workflow because WF2/WF3 reuse step numbers). For `implement-feature`: `4`→"Design
Critique", `6`→"Plan Drift", `8a`→"Per-task Review", `9`→"Implementation Drift", `11`→"Code
Review", `15`→"Post-Deploy" — so the Tier-2 `gates[].name` column stops drifting across
sessions. `security_scan.skipped[]` must be a scanner **KIND** from `work_summary.SCANNER_KINDS`
(`secrets`/`sca`/`sast`/`iac`), never a free-text reason — the summarize CLI validates this
fail-closed at write time (`validate_record(..., strict=True)`); historical free-text records
still load (lenient read).

**Multi-pass gate counting (#340).** For a multi-pass gate, `findings` counts UNIQUE findings
across all passes (identity = same artifact location AND same required change) and `resolved`
counts findings whose FINAL disposition at gate close is terminal — applied, fixed-in-gate,
refuted with cited evidence, or dropped by the confidence band. The identity test is mechanical:
same artifact location (file/section/line-range) AND same required change — reviewer wording and
the `source` tag are irrelevant, so a re-raise or re-litigation of an already-counted finding
never adds. FINAL-disposition-at-close governs `resolved`: a finding refuted in pass 2 then
re-opened in pass 3 and left open reverts to unresolved (unresolved deferral without concurrence
counts in `findings` only). Band-drops count in BOTH `findings` and `resolved` because the banded
filter is itself a documented disposition mechanism — excluding drops would make two identical runs
disagree on `findings` merely by confidence phrasing. Worked example: a gate runs 2 passes — pass 1
self-review finds A, B and adversarial finds B' (same location+change as B → identity-merged) and C;
pass 2 self-review re-raises C (no add) and finds D while adversarial re-litigates A's refutation
(no add) → unique findings {A, B, C, D}, `findings: 4`; at close A refuted-with-evidence, B applied,
C applied, D dropped-by-band → `resolved: 4`.

**Compute at gate close, persist, never re-derive (#340 8a).** The identity dedup needs the
per-finding location+change data that lives only in the reviewer results — session-note markers
persist COUNTS, not findings. So the deduped `findings`/`resolved` pair is computed AT GATE
CLOSE (while the finding text is in context) and persisted in that gate's session-note evidence
(the gate's `— DONE` marker detail or its evidence block); Step 16 / WF3 Step 14 assembly READS
the persisted per-gate figures and never re-derives them. Gate close = the last circuit-breaker
resolution before the workflow advances past the step; intermediate loop-back passes do not close
the gate. If a legacy section carries only per-pass counts, their sum is an OVER-count — record it
with an `extra` note naming the gap rather than presenting it as deduped. Disposition aliases
(the phrases session evidence actually uses): "resolved-in-gate" = fixed-in-gate;
"subsumed" = identity-merged into another finding (not a separate finding at all);
"accepted-as-tightening" / "satisfied-by-verification" = applied-class terminal;
any phrase outside this closed set is UNRESOLVED — the set never reopens; the only
evidence-based terminal disposition is "refuted with cited evidence" itself.

**`lane` (OPTIONAL, #135):** `"small-standard"` when the run took the `<small-standard-lane>`,
`"full"` otherwise. Unlike the required keys above, `lane` may be **omitted** — `validate_record`
in `hooks/work_summary.py` only checks the keys it knows about and does not reject unrecognized
top-level keys, so a record without `lane` is exactly as valid as one with it. Existing
run-records recorded before #135 have no `lane` key and remain valid; this is a forward-compatible
addition, not a schema version bump. If a Step-9 lane cross-check widened the lane (see the
small-standard lane design, `docs/design/2026-07-03-small-standard-lane.md`), add a
`"lane-widened"` note to `follow_ups` rather than mutating `lane` after the fact.

**`verification_deferred` (OPTIONAL, #138):** a **structured list** (not a count) of tasks whose
verification was deferred to the target because the dev env fundamentally cannot exercise the
artifact. Each entry MUST carry non-empty `task_id`, `reason`, `local_proxy` (what WAS run locally
— compile/typecheck/extractable unit tests), and `target_check` (the exact manual check for the
human). Omitted/empty on a run with no deferrals; records predating #138 have no key and remain
valid (same forward-compatible, no-version-bump rule as `lane`). A bare count is deliberately NOT
used — the completion gate reconciles each planned deferred task against this list via
`plan_lib.assert_deferrals_recorded`, which needs the task ids, and the per-task evidence must be
legible, not summed away. `task_id`s must be distinct.

**`usage` (OPTIONAL, #155):** best-effort per-run token/cost/time telemetry. It follows the same
*validated-optional* pattern as `verification_deferred` (NOT the unvalidated-passthrough pattern of
`lane`, which `validate_record` never inspects at all): **absent** is fine — old records stay valid,
no schema version bump — but **present is strict**. All five keys (`input_tokens`, `output_tokens`,
`cost_estimate_usd`, `wall_clock_s`, `model_mix`) must be present, and `null` is an allowed *value*
for any of them, same deliberate-null-vs-dropped-field rule as the rest of the schema; if supplied,
`model_mix` maps each model name to its own `{input_tokens, output_tokens}` pair (also
present-with-nullable-values). Tokens-by-model in `model_mix` is the **primary** metric — most runs
are billed against a Claude subscription rather than metered per token, so `cost_estimate_usd` is a
**derived, secondary** figure (a rate-card estimate, useful for cross-checking, not the number to
trend on). **Caveat for A/B use:** `model_mix.input_tokens` sums fresh + cache-creation + cache-read
into one number, so it is a *volume/context-pressure* metric, NOT a cost proxy — cache-read is billed
~1/10th of fresh input, so a version that caches better can show *more* input tokens at *lower* cost.
For a cost comparison use `cost_estimate_usd`; do not rank versions by `input_tokens`.

**`capture_status` (OPTIONAL sixth key, #189):** how the numbers were obtained — a controlled
vocab `{captured, unrecoverable, unavailable}`, fail-closed like `goal_guard`/`reviewer_kind`
(a typo, case-variant, or null is rejected). Absent → old records unchanged (no schema bump).
It is the **schema-level backstop against the #155 null-forever state**: when it is `"captured"`,
`validate_record` REQUIRES `input_tokens` + `output_tokens` to be non-null and **sum > 0** — a
captured claim over a null/zero measurement can no longer be persisted. `"unrecoverable"` = a
historical row with no session-id correlator; `"unavailable"` = capture was attempted for this run
but failed (session file missing / no usage blocks). The two non-captured markers still allow null
tokens (that IS the honest telemetry for a row we cannot fill).

**Live capture — `hooks/usage_capture.py`.** #155 added the field but nothing populated it, so it
was null in all 24 records. Capture now parses the Claude Code session transcript directly (the same
source `npx ccusage` reads) — stdlib-only, deterministic, no network. At Step 16 assembly, capture
the current session's usage and embed it:
```bash
python3 hooks/usage_capture.py capture --session-id "$CLAUDE_CODE_SESSION_ID"
# -> {"input_tokens":N,"output_tokens":N,"cost_estimate_usd":N,"wall_clock_s":null,
#     "model_mix":{...},"capture_status":"captured"}   (or {"capture_status":"unavailable"})
```
Merge that object into the record's `usage` (set `wall_clock_s` from the orchestrator's own timing).
If capture returns `{"capture_status":"unavailable"}` (session file not found / mid-write / no usage),
record it as-is with null tokens rather than fabricating numbers. `ccusage` remains a manual
cross-check only — it is deliberately NOT in the capture path (network/npx would make it flaky).

Populate `usage` at Step 16 **assembly time**, before invoking `summarize` — the store is
append-only, so if usage numbers surface only after the record has already been persisted,
re-running `summarize` to add them would append a **second, duplicate** line for the same run
rather than amend the first. A post-hoc backfill instead means editing the run's existing JSONL
line in place — use `python3 hooks/usage_capture.py backfill --records docs/measurements/run_records.jsonl`
(recovers a row with a session-id correlator, else marks it `unrecoverable`); after that edit, the
pristine drift-guard test validates the whole committed store in CI, so a malformed hand-edit is
caught the same way a bad writer output would be.

**`dispatches` (OPTIONAL, #330):** a **structured list** of per-subagent-invocation dispatch
telemetry, one entry per canonical `DISPATCH` audit line (`shared/blocks/model-routing-resolve.md`)
emitted this run. It follows the same *validated-optional* pattern as `usage`/`verification_deferred`
(NOT the unvalidated-passthrough pattern of `lane`): **absent** is fine — old records stay valid, no
schema version bump — but **present is strict**: each entry must carry all six fields
(`role`, `subagent_type`, `model`, `effort`, `outcome`, `resolution`), and `role`/`outcome`/`resolution`
are a controlled vocabulary quoted directly from `hooks/work_summary.py`'s `DISPATCH_ROLES`,
`DISPATCH_OUTCOMES`, `DISPATCH_RESOLUTIONS` constants — `role` ∈ `{review, implementation, analysis,
other}`, `outcome` ∈ `{ok, error, retried, dead}`, `resolution` ∈ `{primary, fallback, generic}`.
`model`/`effort` are string-or-null. Assembled at Step 16 from the session-notes DISPATCH lines per
the §16 assembly instruction above (grep `^DISPATCH issue=<n> `, map to entries, skip-and-note
malformed lines, never dedup); zero well-formed lines for this issue means the key is **omitted
entirely**, never an empty array.

**Routing telemetry on `dispatches[]` entries (OPTIONAL, #420):** each dispatch entry MAY carry
additional per-dispatch routing-telemetry fields, populated once #417 wires the executor into the WF
prose (the executor's Observation supplies them): `preferred_model` (str|null — the routed/requested
model), `actual_model` (str|null — the provider-reported id), `fallback_reason` (str|null),
`queued_ms` (int|null — quota queue wait), `concurrency` (int|null — observed concurrent-permit
count, for ≤3-ceiling visibility), and `selector` (object|null — `{risk_level, complexity, ceiling}`,
each str|null — the routing selector inputs). Same **validated-optional** rule as the rest of the
schema: absent is fine (a pre-#420 6-key entry stays valid), but a PRESENT value is type-checked
(`validate_record`, fail-closed). Prompt contents are excluded. Bools are rejected where an int is
expected (`queued_ms`/`concurrency`), consistent with the rest of the schema.

**`reviewer_kind` (OPTIONAL, #155):** a per-gate entry, a **controlled vocabulary** per #116's
canonicalization contract: `inline` / `reflexion` / `builtin_code_review` / `codex` /
`hand_rolled_multi`. When present it must be a member of that set — free text is rejected
(fail-closed), which is exactly the drift #116 documents and this field exists to kill. Omit the
key rather than setting it to `null`: `validate_record` only checks membership when the key is
present, it doesn't require the key or accept a null placeholder.

For WF2 assembly, map the actual review mechanism used at each gate: Step 4 design self-review
(the in-repo quality-bar rubric, all lanes since #190/#205 — the 3-judge panel and the external
reflexion dependency were retired) → `inline`; Step 11 three-agent panel → `hand_rolled_multi`;
builtin `/code-review` → `builtin_code_review`; Codex adversarial review → `codex` applies only to
a gate whose SOLE mechanism is codex — not present in current WF2; a merged self-review+codex gate
records the gate-defining mechanism per the precedence rule below. (The `reflexion` vocab member
remains valid for legacy records but is no longer produced by WF2.)

**Merged-gate precedence (#340).** For a merged gate, record the gate-DEFINING mechanism — the
mechanism whose absence would void the gate; the additive opt-in adversarial layer is skippable by
contract and never changes `reviewer_kind`. The operative rule is the enumeration:
Step 4 all lanes → `inline`, Step 6 → `inline`, Step 11 → `hand_rolled_multi` (the adversarial
layers at Steps 4/6/11 are skippable-on-failure by contract, therefore never gate-defining). A
fully-skipped gate (`status: "skipped"`) OMITS `reviewer_kind` entirely — the field is
omit-not-null. The cross-model layer's per-gate
visibility is the existing session-note markers — Step 11's 4-state diff-review marker and the
Step 4/6 `(invoked|skipped|discarded)` parens markers — NOT a `dispatches[]` entry today;
prescribing a DISPATCH line for adversarial-review invocations is a named follow-up.

**`loop_backs` is read from the counters file, never assembled from memory (#512).**
`loop_backs.used` MUST be read at assembly time from
`claude_docs/.wf2-state/<issue>/loopback_counters.json` (`total`) — the documented
source of truth `plan_lib.consume_loopback` maintains, which survives context
compaction and multi-session runs; an in-context count is structurally wrong on any
resumed run (WF2 #467 recorded 0 against a persisted total of 2). A missing counters
file means zero loop-backs were consumed. `work_summary.py summarize` cross-checks
this via `--loopback-counters` (divergence or a malformed counters file fails rc 1,
not persisted); with the flag omitted it auto-discovers the cwd-relative path and
checks only when the file exists. `reviewer_kind` is likewise re-derived at assembly
time from the gate-defining mechanism per the merged-gate precedence enumeration
(#340) above — the additive adversarial layer never changes it; read the gate's
session-note markers, not memory.

**`goal_guard` (OPTIONAL, #156):** a top-level, **validated-optional** field following the same
pattern as `usage` — absent ⇒ old records stay valid, no schema version bump; present ⇒ strict
membership in `{"set", "skipped", "fired", "deferred"}`, fail-closed on anything else (including
non-strings and case variants like `"SET"`). Semantics: `set` = `/goal` was invoked for this run;
`skipped` = the guard was offered but declined, or the run predates the guard and the gap is being
labeled rather than left silently absent; `deferred` (#191) = Step 1b deferred the per-issue goal to
an already-active epic-level campaign goal (`RAWGENTIC_EPIC_GOAL` set) rather than emitting one that
would clobber it; `fired` = the goal evaluator actually blocked a premature stop.
`fired` is currently **MANUAL-ONLY**: no structured signal reaches the orchestrator when the
Stop-hook's goal evaluator blocks a quit, so nothing sets this value automatically today — a human
must recognize the block and record it by hand. This makes the premature-termination metric
**aspirational** until an automated detection path exists; that is a named limitation of the
current wiring, not a bug in this validator.
