# Design — Observation work_product + telemetry I1-I3 + schema v2 (#469, W6 of epic #475)

**Blast radius:** MEDIUM-HIGH — touches the NORMATIVE Observation schema (kukakuka consumes it),
so the version bump + freeze semantics must be exact.

## Governing decision (#434 = option b)
Bump `schema_version` `"1"` → `"2"` per the schema's own rule (an additive field under
`additionalProperties:false` breaks an old vendored copy → new-doc→old-schema is breaking). v1
stays frozen for existing consumers; consumers validate by DECLARED version. #469 ships the bump +
the normative versioning-policy prose. AC doc header + D-5 confirm: work_product + AC-I1 fields ship
as `schema_version:"2"` with W6.

## Approach (selected)

### 1. Schema v2 bump + version-dispatching validator (AC1)
**The repo already holds v1 Observation documents** — `tests/phase_executor/fixtures/kukakuka-observation.json`
(the cross-consumer parity fixture, the whole POINT of #434), `tests/phase_executor/fixtures/routing-audit.jsonl`,
and many test-constructed v1 observations. A naive const `"1"→"2"` bump on the single schema would break
every one on validation. #434(b)'s "**consumers validate by declared version**" is therefore literal — it
requires per-version frozen schemas + a version-dispatching validator, NOT a bare const flip. (This
corrects the initial map's "no dual mechanism needed" read — the map was right that none EXISTS; the v1
fixtures show one is now REQUIRED.)

- **Freeze v1:** copy the current `observation.schema.json` (its exact current field set) to
  `observation-1.schema.json`, `$id …/observation-1.json`, const `"1"` — FROZEN, never edited again.
- **Add v2:** `observation.schema.json` becomes the v2 schema — const `"2"`, `$id …/observation-2.json`,
  plus the new `work_product` + AC-I1 properties. (Keeping the canonical filename as "latest" minimises
  churn for the ~dozen call sites that load `observation.schema.json`.)
- **Version-dispatching validator:** `observation_schema(version)` maps `"1"→observation-1.schema.json`,
  `"2"→observation.schema.json` (lru-cached per version). `validate_observation(obs)` reads
  `obs.get("schema_version")` and validates against the matching frozen schema; an **unknown/missing
  version fails closed** (raises). So a `"1"` doc (kukakuka fixture) validates against frozen v1; a `"2"`
  doc validates against v2 — the exact #434(b) mechanism.
- **Why dual-schema IS required here (peer-consult fork, resolved by code evidence):** the peer proposed
  a single v2 schema with NO dual dispatch, arguing nothing re-validates v1. But
  `tests/phase_executor/test_schemas.py:104` (`test_ac2_kukakuka_shaped_observation_validates`) DOES
  `jsonschema.validate(kukakuka_fixture, OBS_SCHEMA)` and the fixture is `schema_version: 1` — a bare
  const flip breaks it, and the fixture is the cross-consumer parity proof (#434's raison d'être). "Validate
  by declared version" is therefore literal. That test is repointed at the version-dispatching
  `validate_observation` (v1 fixture → frozen v1). (A single superset schema with a `["1","2"]` enum was
  also rejected: it can't enforce that a v1 doc must NOT carry a v2-only field.)
- `contract.py`: `SCHEMA_VERSION = "2"` (new producer default flows through the dataclass).
- **All validation call sites route through `validate_observation(obs)` (adversarial H1).** Direct
  `observation_schema()`/`OBS_SCHEMA` loads that bypass dispatch would reject a v1 doc against the v2
  const, defeating the guarantee. Audit every general Observation validation (`enforce.RoutingAuditLog.append_observation`,
  `enforce.reconcile_run`'s per-obs loop, `stub_pane_adapter`, `test_schemas.py`, `test_contract.py`,
  `test_parsers.py`, `test_canary.py`) → all use `validate_observation` (version-dispatch). A DIRECT
  schema load is reserved for explicitly v2-only checks (e.g. the meta-valid `check_schema` test) — those
  are the only audited exceptions. A test exercises a v1 doc through each consumer path (the kukakuka v1
  fixture + a v1 routing-audit doc) and asserts it still validates.
- **Normative versioning-policy prose** into BOTH the schema descriptions AND `phase_executor/README.md`:
  *"A field whose addition breaks an older vendored copy (any change under `additionalProperties:false`)
  bumps `schema_version`. Each version ships as its own FROZEN `observation-<n>.schema.json` and is never
  edited after release. A document is validated against the schema of its DECLARED `schema_version`
  (unknown version = fail-closed). New producers emit the current version; prior-version documents are
  never retro-mutated."*
- v1 fixtures keep validating (against frozen v1); `test_schemas.py::_obs_ok` gains a v2 variant while a
  v1 case pins that the frozen v1 schema still validates a v1 doc AND rejects the new v2-only fields.

### 2. work_product typed field (AC2 — executor-derived, OQ-4)
Optional-additive object under v2 (emit-when-set, like effort/canary_result):
```
work_product: {kind, worktree_path, base_sha, head_sha, content_tree_sha, changed_paths[],
               documents[], tests[{command_digest, status, exit_code, report_ref}], promotion_status}
```
**`content_tree_sha` is the anti-tamper CONTENT commitment (adversarial H2).** `head_sha` +
`changed_paths` record only committed content + path NAMES — a dirty/untracked file's BYTES could change
while every recorded field stays identical, so names alone can't establish what the seat produced.
`content_tree_sha` is the git tree object id of the FULL worktree state (committed + dirty + untracked),
materialized by `worktree.py`'s existing `_candidate_tree(handle)` (temp `GIT_INDEX_FILE` → `add -A` →
`write-tree`, `.gitignore`-respecting) — so the whole produced state is content-addressed under ONE
executor-controlled snapshot boundary, not per-helper-call drift.
Helper `derive_work_product(handle, *, kind, documents=(), tests=(), promotion=None)` — a DISTINCT
operation, NOT `PromotionResult` reuse (a work_product exists for failed / rejected / never-promoted
seats too, where no promotion ran):
- takes a `worktree.WorktreeHandle`; derives `base_sha` (`handle.base_sha`), `head_sha`
  (`_worktree_head`), `changed_paths` via the trusted-gitdir `inspect()`/`_candidate_changed()`
  primitives — **including porcelain dirty + untracked**, not just `base..HEAD` (peer risk: `head_sha`
  alone is not a content commitment). `changed_paths` normalized to sorted, unique, worktree-relative.
- **The API takes NO agent-reported SHAs or changed paths.** The recorded git evidence is ALWAYS
  executor-derived; a provider's self-reported claim stays only in its existing `parsed_payload` surface,
  never feeds `work_product`. `documents` are restricted to executor-verified changed paths (an
  out-of-worktree report uses `report_ref`, not a git-evidence claim); `tests[]` entries come from
  executor-observed commands/results.
- `promotion_status` is an explicit enum `{not_attempted, promoted, not_promoted, failed}`. When a
  `PromotionResult` is supplied, its SHAs/changed_paths are RECONCILED against the independently-derived
  evidence (mismatch is a loud refuse), NOT copied wholesale.
- OQ-4 test: pass a fabricated `parsed_payload` claiming false SHAs/paths → the built `work_product`'s
  `changed_paths`/SHAs equal the git-derived truth; the false claim never affects the record.
- Reuses `worktree.py`'s trusted-gitdir helpers (promote the required read-only ops to a small public
  evidence surface or cover the reuse with focused tests — the helpers are currently private).

### 3. NEW AC-I1 telemetry fields (typed, optional-additive under v2; POPULATION = #470)
Add to schema + dataclass + emit-when-set (like canary_result — #469 adds the typed fields, #470's
dispatch choke-point populates them, mirroring the #468→#470 split):
- `session_policy` (str: fresh|resume) — the D-8 policy actually used.
- `worktree_id` (str) — `WorktreeIdentity`.
- `tmux_session` (str) — `registry.session_name`.
- `budget` ({reserved_usd: number ≥ 0, spent_usd: number ≥ 0}) — unit/rounding fixed in prose (USD, cents).
- `hook_denials` — a nonnegative INTEGER count on the Observation (peer: detailed denial EVENTS go to the
  run-record, not every Observation — avoids duplicating potentially sensitive payloads per-dispatch).
- `work_product` (§2).
Existing (effort, canary_result, dispatched_lane.pool) unchanged. All new objects are typed with
`additionalProperties:false` at every level; governed vocab (`kind`, `session_policy`, test `status`,
`promotion_status`) uses enums; monetary/count values are nonneg; `exit_code` is int-or-null; ref/digest
strings are non-empty.

### 4. I2/I3 aggregation substrate (AC3) — extend the Tier-2 store, never a new system
- **I2** (per-run) is the orchestrator run-record (`work_summary.py` / `run_records.jsonl`) which
  ALREADY carries gates, timing (#506), usage model_mix, PR/CI. #469 adds the missing I1-derived
  rollup hooks the run-record needs — but the bulk of I2 exists; #469 confirms coverage + adds
  `work_product` refs + a `seat_outcomes` pointer, not a rewrite.
- **I3** (cross-run) — the `seat-outcomes.jsonl` sidecar is DEFERRED WHOLESALE to #473 (peer: creating
  the sidecar before stable join keys + idempotent aggregation are defined risks duplicate/irreconcilable
  rows; #473 "baselines + alerts" owns it and rides this I3 history). #469's I3 contribution is only the
  **documented join/reference semantics** — the `work_product` refs + the join keys (run_id, seat,
  model, issue) that #473's aggregation will key on — written into the AC doc / README, NOT a new file.
  Redaction/retention rules for host-specific fields (worktree_path, tmux_session, denial events) are
  defined where they land (the run-record / #473 sidecar), not on the per-dispatch Observation.

## Platform / external dependencies

platform_apis: none

Pure Python (jsonschema/json/git-via-worktree-primitives) over in-repo inputs. The git derivation
reuses `worktree.py`'s existing trusted-gitdir `subprocess` calls (an already-precedented in-repo
call site), not a new platform surface.

## Error handling / failure modes
- Schema v2: a document declaring `"1"` against the v2 schema fails the const — but nothing
  re-validates stored v1 docs (write-time only), so this is the intended freeze, not a break.
- work_product: git derivation reuses the strict trusted-gitdir helpers (a `strict` unreadable-path
  refuses rather than silently under-reporting). A lying self-report cannot alter the recorded evidence.
- validate_observation stays fail-loud on a malformed v2 document.

## Security implications
The work_product records what an agentic seat produced; the executor-derived git evidence is the
anti-tamper property (an agent can't fabricate its changed-file/commit record). No secrets, no new
external surface. The seat-outcomes sidecar records outcomes, not credentials.

## Tests (red-before-green)
1. `test_schema_version_is_2` — current schema const == "2", SCHEMA_VERSION == "2"; frozen
   `observation-1.schema.json` const == "1".
1b. **Version dispatch (back-compat):** a v1 doc (the kukakuka fixture) validates against frozen v1 via
   `validate_observation`; a v2 doc validates against v2; a v2-only field (work_product) on a doc
   DECLARING `"1"` is REJECTED (frozen v1 has no such property); an unknown `schema_version` fails closed.
2. work_product: omit→validates; set→round-trips + validates; malformed (bad tests[] entry)→rejected.
3. `test_work_product_ignores_lying_self_report` (AC2) — build an Observation whose `parsed_payload`
   fabricates false changed-files/SHAs; assert the `work_product`'s `changed_paths` + `content_tree_sha`
   equal the git-derived truth (derive_work_product takes no self-report, so the claim can't leak in).
3b. `test_work_product_content_tree_sha_binds_dirty_untracked` (adversarial H2) — a dirty/untracked
   content change flips `content_tree_sha` even when `head_sha` + `changed_paths` names are unchanged.
4. Each new I1 field: optional-additive omit/set/malformed (the established template); `hook_denials`
   is an int count; `budget` values nonneg.
4b. `test_v1_document_validates_through_each_consumer` (adversarial H1) — a v1 doc (kukakuka fixture) passes
   `validate_observation` (dispatch → frozen v1); a v2-only field on a `"1"`-declared doc is rejected;
   unknown version fails closed.
5. I3 contract documentation (adversarial H3): assert the documented join/reference fields
   (run_id, seat, model, issue + work_product refs) are present in the AC doc/README — NO seat-outcomes
   schema, file, row validation, or append helper is introduced by #469 (that is #473).
6. Full suite green; `test_schemas.py::_obs_ok` + any "1" fixtures repointed at version-dispatch (NOT bulk-bumped to "2").

## Files touched
- `phase_executor/.../schemas/observation-1.schema.json` (NEW — frozen v1 copy, const "1")
- `phase_executor/.../schemas/observation.schema.json` (→ v2: const "2", $id, work_product + I1 props + prose)
- `phase_executor/.../contract.py` (SCHEMA_VERSION="2", version-dispatch `observation_schema`/`validate_observation`,
  dataclass fields, to_dict emit-when-set, `derive_work_product`)
- `phase_executor/README.md` (normative versioning-policy prose)
- `tests/phase_executor/test_schemas.py` (repoint the kukakuka v1 fixture at version-dispatch + v2 cases),
  `test_contract.py`, `test_worktree_*` / a work_product test (git-derivation + lying-payload OQ-4 test)
- version ×3 → 3.77.0 (minor, feat); README changelog. No diagram REV (no WF2/WF3 spine change).
- NOT #469: the seat-outcomes sidecar + baselines/alerts (→ #473); dispatch-time POPULATION of the new
  I1 fields (→ #470 wiring, mirroring the #468 canary_result field→#470 split).

## Adversarial-review dispositions (Step 4, gpt/Codex) — one design loop-back consumed (design 1/3)
- **H1 (all-call-site dispatch):** ADOPTED — every general validation via `validate_observation`; direct
  schema loads audited to v2-only; v1-through-each-consumer test.
- **H2 (content commitment):** ADOPTED — `content_tree_sha` (full dirty+untracked tree via `_candidate_tree`)
  binds produced CONTENT, not just committed head + path names; single snapshot boundary.
- **H3 (test-5 sidecar contradiction):** ADOPTED — test 5 is now an I3 doc/join assertion; no sidecar in #469.
- Mediums: cent-precision prose (USD, integer cents); lying-payload test clarified (fabricated
  parsed_payload, work_product unaffected); git external-dep noted (reuses worktree.py's precedented
  trusted-gitdir subprocess, not a new surface); worktree_path/tmux_session redaction deferred to the
  consuming surface (#473 sidecar / run-record), noted on the Observation.

## Provenance
Design + cross-model peer consult (gpt/Codex), hardened against the Step-4 adversarial review. ADOPTED from the peer: distinct `derive_work_product`
(not PromotionResult reuse), the `promotion_status` enum + reconcile-not-copy, no agent SHAs in the API,
precise typed v2 shapes (additionalProperties:false / enums / nonneg / exit_code int-or-null), `hook_denials`
as an int count (events→run-record), dirty+untracked derivation, and deferring the I3 sidecar to #473.
REFUTED (with code evidence): the peer's "no dual-schema / single v2" — `test_schemas.py:104` validates
the v1 kukakuka fixture against the current schema, so version-dispatch is required by #434(b)'s
"validate by declared version." Retained from my draft: the confirmed #434(b) decision + the source-of-truth map.
