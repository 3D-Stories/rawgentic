# #558 — Genuine quota detection + per-call caps + classifier persistence (H5)

**Issue:** [#558](https://github.com/3D-Stories/rawgentic/issues/558) · epic #560 executor-hardening · WF2 run `wf2-558-0a54ddc6` · 2026-07-21
**Baseline:** 4263 passed / 16 skipped / 0 failed @ `49c59d5` (origin/main).

## Scope (owner decision D-12, 2026-07-21)

**#558 ships SHADOW INFRASTRUCTURE.** Deliverables: the versioned conjunctive classifier
(shadow-gated — it can classify and persist evidence but never auto-transition until a
`(classifier_version, rule_table_digest)` pair is calibration-allowlisted), the
`JobRecord.quota_classification` evidence field, and the per-call cap wiring. Stated
plainly: with the calibration gate, sync-only claude dispatch, and codex-only mutating
supervision, **no current production job changes state because of this PR** — every
claude `NONZERO_EXIT` supervised collect starts producing evidence immediately, and the
guard machinery is fully tested via injected calibrated stubs. **The live
`quota_paused` transition AC moves to #559** (owner-ratified): #559 captures a genuine
usage-limit exit, calibrates the rules against it, adds the ONE allowlist entry, and
proves the transition end-to-end. AC-E1's "genuine usage-limit" clause is satisfied
across #558+#559 jointly, by design.

## Problem

Three coupled gaps surfaced by the #472 proving run (all re-verified on the branch base this run):

- **(a) Quota transition unreachable.** A real usage-limit exit-1 child still writes a
  `NONZERO_EXIT` `observation.json` (`adapters/base.py:110-111`), so `await_job`'s collect
  branch finalizes `completed` (`supervisor.py:696-700`) — and `mark_quota_paused` then
  refuses the terminal state (`supervisor.py:505-508`). `mark_quota_paused` has **zero
  production callers** (Step-2 blast analysis): quota entry exists only as a test seam.
- **(b) AC-B4 per-call caps absent.** The bounds schema
  (`schemas/routing-table.schema.json`, `$defs.manifest.bounds`, `additionalProperties:
  false`) admits only `timeout_s` (required) + `max_budget_usd` (optional); every real
  manifest supplies **only** `timeout_s` (`routing/rawgentic.routing-table.json`). The one
  path that requires `max_budget_usd` (mutating claude, `contract.py:356-360`) is
  production-unreachable (`MUTATING_FS_SANDBOXED={codex}`). No token/turn field exists at
  all; zhipu's `max_tokens` is hardcoded 1024 (`adapters/zhipuai_sdk.py:88`).
- **(c) No classifier evidence field.** `JobRecord` (`registry.py:40-72`) has no
  `quota_classification`; serde (`registry.py:181-210`) can't carry one.

## Approaches considered

**A (chosen) — supervisor-side detection at collection + shared pause-guard helper.**
A new pure classifier module consumed inside `await_job`'s collect branch; pause
finalization goes through `_finish` with the classification attached to the same upsert.
Pros: one writer path (`_finish` already owns upsert + permit release — Step-2 finding:
no permit mirroring needed), evidence atomically persisted, fail-closed guards factored
once and shared with `mark_quota_paused`. Cons: touches the most safety-critical method
in the engine — mitigated by red-first fixture corpus + integration tests.

**B (rejected) — caller-side detection in `executor_routing_lib` after `await_job`
returns.** Rejected: the record is already terminal `completed` by then (the exact
unreachability being fixed); pausing after would need a second upsert (double-write,
non-atomic vs AC3) and leaves the supervisor's own state machine lying about what
happened.

## Design

### AC1 — conjunctive classifier + collection-time detection

**New module `phase_executor/src/phase_executor/quota_detect.py`** (pure, no I/O —
mirrors the "pure core" repo convention; distinct from `quota.py`, which is the permit
coordinator):

```python
@dataclass(frozen=True)
class StderrEvidence:            # built at the I/O boundary (supervisor._read_stderr)
    decoded_text: str            # errors="replace" decode of what was read
    raw_sha256: str              # sha256 over the RAW BYTES read (not the decode)
    byte_count: int
    read_error: Optional[str]    # "missing" | "unreadable: <errno>" | "oversized" | None

@dataclass(frozen=True)
class QuotaClassification:
    verdict: bool                # all four conjuncts true AND read_error is None
    conjuncts: dict              # {"provider_claude": bool, "exit_1": bool,
                                 #  "usage_limit_lang": bool, "reset_retry_lang": bool}
    engine: str                  # echoed inputs — evidence self-describes
    exit_code: Optional[int]
    source: str                  # always "stderr.txt" (the only sanctioned verdict input)
    rule_ids: tuple              # matched pattern identifiers (e.g. "usage.v1/a")
    stderr_sha256: str           # raw-byte hash from StderrEvidence
    stderr_bytes: int
    read_error: Optional[str]    # carried from StderrEvidence — distinct from a negative
    envelope_subtype: Optional[str]  # ALLOWLISTED transport envelope subtype —
                                 # OBSERVABILITY ONLY, never a verdict input (#559
                                 # calibration data for the stdout-envelope hypothesis)
    envelope_subtype_sha256: Optional[str]  # sha of a non-allowlisted subtype (persisted
                                 # as "unknown" + this hash — pass-4 A-F5)
    envelope_error: Optional[str]    # "missing" | "malformed" | "oversized" | None —
                                 # the bounded _envelope_meta reader's typed outcome
    classifier_version: int      # 1 — bump on any rule change

@dataclass(frozen=True)
class EnvelopeMeta:              # built by supervisor._envelope_meta (bounded reader)
    session_id: Optional[str]
    subtype: Optional[str]       # allowlisted, or "unknown"
    subtype_sha256: Optional[str]  # set when subtype == "unknown"
    error: Optional[str]         # "missing" | "malformed" | "oversized" | None

def classify_quota_exit(*, engine: str, exit_code: Optional[int],
                        stderr: StderrEvidence,
                        envelope: Optional[EnvelopeMeta] = None) -> QuotaClassification: ...
```

The classifier copies `envelope.subtype` / `envelope.subtype_sha256` / `envelope.error`
into the evidence fields verbatim (observability only, never verdict inputs).

No raw stderr text is persisted — not even an excerpt (pass-2 self-review F7: provider
stderr can carry tokens/URL secrets; `jobs.json` must not become a secret-bearing
surface). `rule_ids` + hashes identify the match; the capture dir's `stderr.txt` (0700)
remains the on-disk forensic source.

**I/O contract (`_read_stderr` → `StderrEvidence`):** the file is read as RAW BYTES in
bounded-memory chunks up to an 8 MiB ceiling; `raw_sha256` streams over the bytes
actually read; the scan text is the `errors="replace"` decode. Files over the ceiling
are NOT classified: `read_error="oversized"`, prefix hash + true byte count recorded
(pass-2 adversarial F5's explicit either/or — we choose bounded-with-honest-marker over
unbounded full-file streaming). Missing/unreadable files carry their typed `read_error`.
Any non-None `read_error` forces `verdict=False` with the reason visible — never
indistinguishable from an ordinary negative (pass-2 F4).

**v1 matching contract (normalization — pass-2 adversarial F2):** decode → `casefold()`
→ match PER LINE (conjuncts 3 and 4 may match on different lines of the same stderr;
no cross-line joining). Patterns are word-boundary-anchored, case-insensitive via the
casefold, with bounded proximity inside a line (never unbounded `.*` across the whole
text). The exact regex table + rule ids live in `quota_detect.py` AS the versioned v1
source of truth, pinned byte-for-byte by the fixture corpus — the design doc pins the
normalization rules; the module pins the patterns (one home, no prose/code drift). A canonical serialization of the v1 rule table is digest-asserted in tests next to `classifier_version` — ANY rule-table change fails the digest test until the version is bumped (pass-3 A-F6: fixtures alone cannot pin the table byte-for-byte).

Conjunctive — ALL must hold (a miss anywhere → `verdict=False`, current behavior kept):
1. `engine == "claude"` (provider conjunct — codex/zhipu never classify).
2. `exit_code == 1` exactly (SIGKILL 137, exit 2, timeout kills never classify).
3. Usage-limit language in stderr: anchored on `usage`-proximate limit phrasing
   (e.g. `usage limit`, `out of … usage`, `5-hour/weekly … limit`) — deliberately does
   NOT match bare `limit`/`rate limit`, so API throttling (429) stays negative.
4. Reset/retry language in stderr: **explicit temporal recovery phrasing only** —
   `resets at/in`, `try again at/after/in`. `upgrade` is deliberately NOT a match
   (Step-4 adversarial F2: an upgrade-only usage-limit message is not temporally
   recoverable — pausing it would relaunch an unrecoverable session); an upgrade-only
   fixture is a required negative.

Keyed on **capture `stderr.txt` ONLY** (`capture.py:95-96`; reachable as
`Path(record.capture_dir) / "stderr.txt"` — same pattern as `_transport_session_id`,
`supervisor.py:1100-1108`). The model transcript / `transport.stdout.txt` is never an
input, so transcript-only quota phrases with exit 0 are structurally negative (conjunct 2
fails before text is even consulted).

**Corpus honesty (pinned in the module docstring):** the repo holds ZERO real captured
usage-limit stderr (Step-2 corpus sweep: all 16 existing `stderr.txt` captures are empty;
spike #455 and #472 both failed/declined to capture one). Positive fixtures are therefore
**synthetic, built from the external-docs shape** (exit 1, no retry, usage+reset
language); the classifier ships `classifier_version: 1` and **#559 (live proving run)
calibrates it against a genuine capture** — a version bump + fixture swap is the planned
correction path. Negative fixtures mix REAL captured samples (wrong-cwd resume
`No conversation found with session ID: <id>` from spike-455 doc:125; SIGKILL-137-empty)
with clearly-labelled synthetic ones (claude-flavored auth expiry, account-selection,
network failure, 429 throttle, upgrade-only usage-limit). Fixture bytes stay RAW —
provenance lives in filenames + a `fixtures/MANIFEST.md` sidecar, never inside the file
(pass-2 F12: a header would alter the exact bytes classified and hashed; genuine
captures get their sha256 asserted in tests). A THIRD real negative was captured
this run: the budget-trip probe (`--max-budget-usd 0.01` → exit 1, EMPTY stderr, envelope
`error_max_budget_usd` on stdout) — pinned as a fixture proving a budget trip never
classifies as quota.

**Scope boundary (Step-4 adversarial F1, declined with rationale):** shipping the
detector against a synthetic positive corpus IS the owner-ratified #560 decomposition —
#558 builds the capability, #559 (live proving run) captures a genuine usage-limit exit
and calibrates (`acceptance-criteria.md:62` records the deferral chain). The classifier
is conservative: an unmatched real message means `verdict=False` → exactly today's
behavior, never a wrong pause. Keeping the detector "disabled until #559" would leave
#559 nothing to calibrate.

**Integration — `await_job` collect branch (`supervisor.py:682-700`):** after sentinel
validation, gated on the cheap precheck `parse_status == NONZERO_EXIT` before any file
I/O (a normal `ok` collect never touches `stderr.txt`). Ordering is kill-first (r6/r7):
`_kill_job` precedes EVERY evidence read — including the resume-identity assert, which
is relocated to run post-kill on the bounded `_envelope_meta` (its failure action,
finish `"failed"` + raise, is unchanged):

```python
# NONZERO_EXIT precheck already passed; resume-identity assert is relocated to run
# AFTER the kill via the bounded _envelope_meta (its failure action — finish "failed",
# raise — is unchanged, just no longer a pre-kill read).
killed = self._kill_job(record)         # KILL-VERIFY FIRST (r6 evidence ordering):
                                        # already-dead → True (supervisor.py:577-611);
                                        # all evidence reads below see a stable,
                                        # dead-writer snapshot
spec = self._verified_spec(record)      # digest-checked; parsed from verified bytes
if spec is None:
    cls, meta = None, None              # tampered/missing spec: classify nothing
else:
    ev = self._read_stderr(record)      # StderrEvidence (bounded, O_NOFOLLOW, fstat)
    meta = self._envelope_meta(record)  # EnvelopeMeta (bounded 256 KiB prefix)
    cls = classify_quota_exit(engine=spec_engine, exit_code=_exit_code_of(obs),
                              stderr=ev, envelope=meta)
if cls is not None and cls.verdict:
    decision = self._auto_pause_allowed(record, obs, profile, meta.session_id, cls,
                                        killed=killed)   # PauseDecision(allowed, refusal)
    if decision.allowed:                # implies killed is True — kill-verified is a
        self._finish(record, "quota_paused",             # guard conjunct
                     release_permit=True,
                     provider_session_id=meta.session_id,
                     provider_exit_code=_exit_code_of(obs),
                     quota_classification=asdict(cls) | {"paused": True})
        return "quota_paused", obs
    if decision.refusal == "kill_unverified":
        self._finish(record, "completed_with_residue", release_permit=False,
                     provider_exit_code=_exit_code_of(obs),
                     quota_classification=asdict(cls) | {"paused": False,
                                                         "refusal": decision.refusal})
        return "completed_with_residue", obs
    refusal_reason = decision.refusal   # persisted on the completed finalize below
else:
    refusal_reason = None               # negative / read_error: evidence self-describes
# Every OTHER classifier invocation (negative, read_error, refused, spec unverified)
# still persists its evidence on today's finalize (pass-3 S-F10 — the persistence
# trigger is CLASSIFIER INVOCATION on a claude NONZERO_EXIT collect, never only a
# positive verdict):
extra = ({"quota_classification": asdict(cls)
          | ({"paused": False, "refusal": refusal_reason} if refusal_reason
             else {"paused": False})} if cls is not None else
         {"quota_classification": {"paused": False, "refusal": "spec_unverified",
          "classifier_version": 1}} if spec is None and is_claude_nonzero else {})
state = "completed" if killed else "completed_with_residue"
self._finish(record, state, release_permit=killed,
             provider_exit_code=_exit_code_of(obs), **extra)
```

**Evidence-snapshot ordering (pass-4 S-F7):** the branch structure above is normative —
the sentinel is validated and the writer is KILLED-VERIFIED (`_kill_job`) **before**
`stderr.txt` / `transport.stdout.txt` are read for classification, so the hashed
evidence describes a stable, dead-writer state (no growth/replacement between sentinel
validation and evidence read). Both readers open with `O_NOFOLLOW`, require a regular
file, and `fstat` the open fd (size/identity) — a symlink or non-regular file is
`read_error: "unreadable: irregular"`. The one already-shipped pre-kill read —
`_transport_session_id` in the resume-identity assert (`supervisor.py:689`) — is
replaced by the bounded `_envelope_meta` read AFTER the kill, with the assert relocated
accordingly (the assert's failure action, kill+fail, is unchanged — it just no longer
needs to precede the kill).

**Two-layer guard contract (pass-3 A-F2/S-F3 — one shared rule set cannot serve both
writers without breaking one of them):**
- **`_pause_common_allowed(record, obs, session_id)`** — shared by BOTH writers:
  non-empty resumable `provider_session_id` (no envelope →
  `refusal: "no_resumable_session"` — a `quota_paused` record with no session id would
  make `classify_recovery`'s relaunch arm (`registry.py:126-127`) compose an impossible
  `--resume`); effectful-sentinel refusal (an envelope-producing `parse_status` not in
  `AVAILABILITY_FAILURES` (`contract.py:55`) or an attested `actual_model` → refuse;
  `NONZERO_EXIT` IS an availability failure, so a genuine quota sentinel with
  `actual_model: null` passes); non-terminal record state.
- **`_auto_pause_allowed` = common + automatic-mode conjuncts, returning a structured
  `PauseDecision(allowed: bool, refusal: Optional[str])`** — guards evaluate in the
  declared order (verified spec → read-only → resume policy → calibration → common
  predicate) and the FIRST failing guard's reason is the persisted `refusal` (pass-4
  A-F4: deterministic single-reason evidence, not an implementer's choice)** (collection-time
  detection only): **verified spec** (digest match — pass-3 S-F2); **read-only profile**
  (`not profile.mutating` — a mutating job may have applied Edit/Bash effects before the
  quota exit; `actual_model: null` proves only that no final envelope was emitted →
  `refusal: "mutating_requires_manual"`); **resume policy**
  (`profile.session_policy == "resume"` — a fresh launch composed
  `--no-session-persistence` (`claude_cli.py:47-48`), resuming it is spike-#455's LOUD
  failure → `refusal: "session_not_persisted"`); **calibration gate** (pass-3 S-F5
  shadow mode, hardened per pass-4 S-F1 — a `>= MIN` gate is monotonic while calibration
  is not): auto-pause requires `(cls.classifier_version, rule_table_digest)` ∈
  `CALIBRATED_CLASSIFIERS`, an **exact allowlist of calibrated pairs** that ships EMPTY
  (`frozenset()`) — v1 and every future uncalibrated version/digest runs in SHADOW
  (`refusal: "uncalibrated_classifier"`), even for a custom resume-policy routing table.
  #559's genuine capture adds the one calibrated pair; a regression test pins that an
  unknown future version stays shadow-only. **Kill-verified is itself a guard conjunct**
  (r7): `_kill_job` runs FIRST (before any evidence read — the r6 snapshot ordering) and
  its result is passed into `_auto_pause_allowed(..., killed=)`; `killed=False` →
  `PauseDecision(allowed=False, refusal="kill_unverified")` → the residue branch. Pause
  happens only on verified-dead, and every evidence byte was read after that
  verification.
- **Injected mode (`mark_quota_paused`)** keeps its EXISTING preconditions exactly
  (dead job, non-terminal state, non-empty caller-supplied session id — the
  owner-authorization boundary is the caller) + `_pause_common_allowed`'s sentinel
  check, and now routes its write through `_finish` carrying injected-classification
  evidence (`{"injected": true, "paused": true}` — pass-2 F9). Mutating recovery stays
  possible HERE by design: the read-only/resume-policy/calibration conjuncts are
  automatic-mode-only.

**Spec trust boundary (pass-3 S-F2; typed per pass-4 S-F9):** `_verified_spec` reads the
spec file's bytes ONCE, recomputes the digest against `record.spec_digest` (the same
trust rule recovery already applies, `supervisor.py:848`), and returns the object PARSED
FROM THOSE EXACT BYTES (never a second read) — or `None` on missing/malformed/mismatch.
The launch profile used by the guards is reconstructed from that verified object once,
strictly validated; a malformed profile inside a digest-valid spec is
`refusal: "spec_unverified"` too. All guard evaluation flows through the typed
`PauseDecision` with deterministic precedence.

**Bounded envelope metadata (pass-3 S-F4):** `_envelope_meta` replaces the pattern of
`_transport_session_id`'s unbounded `json.load` (`supervisor.py:1100-1108`) for this
path: it reads AT MOST 256 KiB of `transport.stdout.txt`, parses that prefix, and
extracts `session_id` (str-typed) + `subtype` — oversized/malformed → both None with
`envelope_error` evidence; collection then fails toward refusal without releasing a
live permit. The subtype is persisted ONLY through an allowlist
(`{"success", "error_max_budget_usd", "error_during_execution", ...}` — the known CLI
result subtypes) with a 64-char bound; anything else persists as `"unknown"` plus its
sha256 (pass-3 A-F7/S-F7 — no raw provider prose reaches `jobs.json`).

**Production-reachability scope (pass-2 SR-F1, stated plainly):** collection-time
detection lives on the SUPERVISED path (`await_job`) — the epic's subject
(`epic:executor-hardening` = "hardening the phase_executor supervised path"). Today's
non-mutating claude seats dispatch synchronously (`engine.run_seat`) with no JobRecord,
so `quota_paused` is structurally meaningless there: a sync claude quota exit remains a
visible NONZERO_EXIT availability failure that chain-fallback handles, exactly as
before. #559 drives the supervised resume-policy claude path live; this PR ships the
capability it will exercise.

The engine for conjunct 1 comes from the persisted pane spec (`self._read_spec(record)`,
already used by both synthetic-observation sites) — never inferred from output shape.

**Docstring update:** `supervisor.py:17`'s "entered ONLY via the injected classification"
becomes "entered via collection-time detection (`quota_detect`, #558) or the injected
classification (`mark_quota_paused`)".

**Downstream readers hold unchanged** (Step-2 audit): `classify_recovery`
(`registry.py:126`), the `status` short-circuit set (`supervisor.py:175`), `recover`'s
filter (`supervisor.py:1051`), and the dispatch retryable set
(`executor_routing_lib.py:1078`) all reason off `record.state` and are writer-agnostic.

### AC2 — per-call caps (manifest → contract → adapter)

Per ratified AC-B4 (`docs/planning/2026-07-17-orchestrator-executor-acceptance-criteria.md:45`):
claude gets the per-invocation budget bound — a VERIFIED preflight admission gate with live runtime budget tracking; mid-call termination shape unverified, see platform_apis — codex/zhipu declare compensating enforceable
bounds. (AC-B4's pre-launch atomic *reservation* clause is run-level budget accounting —
OUT of #558's AC2 scope per the issue body, which names per-call caps only; noted as
epic-#560-adjacent follow-up.)

1. **Schema** (`routing-table.schema.json` `$defs.manifest.bounds`): add optional
   `max_tokens` (integer ≥1) alongside the existing `max_budget_usd`; `timeout_s` stays
   required. `additionalProperties: false` retained.
2. **Routing table** (`routing/rawgentic.routing-table.json`): every claude-primary/chain
   seat manifest gains `max_budget_usd` (values: analysis/ship 2.0 ≈1.35×, review/plan/
   intake 5.0 ≈3.4×, build 10.0 ≈6.8× the one observed per-dispatch cost proxy 1.48 from
   this run's analysis seats — a bound on runaway liability, NOT a guarantee healthy calls
   never trip; per-seat cost distributions are #473's telemetry scope and the values are
   plain config to retune); zhipu-bearing manifests gain `max_tokens: 1024` — exactly the
   current hardcoded bound (`zhipuai_sdk.py:88`), preserved not loosened (Step-4
   adversarial F5); raising it is a separately-justified manifest change. Codex seats keep
   `timeout_s` as THE declared compensating bound — **AC-B4 is AMENDED to say exactly
   this (owner decision D-13, 2026-07-21):** `codex exec` exposes no token/turn option
   (live `--help` probe), so the ratified "timeout + max-token/turn limits" clause
   becomes "the enforced supervisor/run_subprocess timeout; token/turn caps adopt if a
   future codex CLI grows the flag". The amendment edit to
   `docs/planning/2026-07-17-orchestrator-executor-acceptance-criteria.md` rides this
   PR (contract.py:308 already shipped this reading).
3. **Contract** (`LaunchProfile` + `profile_from_manifest`): add
   `max_tokens: Optional[int] = None`, mapped from `bounds.max_tokens` with the same
   strict-type handling as `max_budget_usd`; the mutating-claude positive-budget
   requirement is unchanged.
4. **Adapters:** claude — `build_command` already composes `--max-budget-usd`
   (`claude_cli.py:70-71`); now every claude seat's profile actually carries a value, so the flag goes live on
   every claude dispatch that carries a profile — all non-mutating sync/competitive
   dispatches (post item 6) and every supervised dispatch; mutating-on-sync is rejected
   outright (item 6). zhipu — `zhipuai_sdk.run` passes
   `profile.max_tokens or 1024` into the worker request (`zhipuai_sdk.py:88`,
   `workers/zhipuai_call.py:37` already accepts it). codex — no cap flag exists
   (Step-2 confirmed: no token/turn flag in `codex exec`); its enforced bound remains the
   supervisor/run_subprocess timeout, and the design documents that explicitly rather
   than inventing an unenforceable knob.
5. **Supervised-path spec threading (Step-4 self-review SR1 — confirmed).** The launch
   profile crosses the supervisor→pane-runner boundary as a field-by-field spec dict:
   write site `supervisor.py:403-405`, read site `pane_runner.py:93-99`
   (`_profile_from_spec`), and the relaunch rebuild `supervisor.py:986-990`. A new
   `LaunchProfile.max_tokens` MUST be added at ALL THREE sites or the cap silently
   vanishes on every supervised dispatch; a spec round-trip test pins each field.
6. **Sync + competitive path threading (pass-2 SR-F2 — CONFIRMED, the dominant gap;
   scoped by pass-3 S-F1).** `engine.run_seat` (`engine.py:99-102`) and `_run_candidate`
   (`engine.py:188`) build `AdapterRequest` with NO profile — the dataclass default
   (uncapped) — so manifest caps would never reach ordinary dispatches at all. Fix: both
   sites derive the per-target profile via `profile_from_manifest` **ONLY when the
   manifest is non-mutating** (no `edit`/`bash` in `tool_grants`): a mutating manifest
   derives a profile that REQUIRES a worktree (`contract.py:345,353`) which these paths
   do not have — deriving unconditionally would fail every build candidate pre-dispatch
   (pass-3 S-F1). A mutating manifest on the sync/competitive path is **REJECTED
   fail-loud before any candidate dispatch** (r6, pass-4 A-F1 — silently keeping an
   uncapped no-profile dispatch would leave exactly the gap AC2 closes; #449's build
   path reaches `run_competitive`, so the rejection sits before candidate fan-out);
   mutating dispatch is exclusively the supervised path's job (which already derives
   with a worktree, `executor_routing_lib.py:1437`). Effective
   timeout = `min(caller timeout, bounds.timeout_s)` — the declared bound can tighten,
   never loosen, the caller's operational value. **The SAME min() applies at supervised
   launch** (pass-4 S-F6: today the caller timeout is written raw into the pane spec,
   `supervisor.py:399`, so a tighter manifest bound is ignored there) — one effective
   timeout, computed before dispatch on BOTH paths, used for provider execution and the
   supervisor deadline. Side effect, deliberate: sync claude dispatches now also carry
   `--allowedTools` from the manifest's `tool_grants` — a hardening the grants always
   declared; production-CLI argv tests (`tests/phase_executor/test_engine.py`) pin the
   exact composed command for single-run, bake-off, and fallback-target paths.
   **Bake-off consequence, stated for #449:** with mutating manifests rejected on the
   sync/competitive paths, live BUILD bake-off dispatch stays unavailable until a capped
   mutating composition exists there — #449's live cells therefore cover non-mutating
   seats + the design-round bake-off; the build bake-off live cell is out of #449's
   reach by this design, documented here so neither issue re-litigates it.

7. **Zhipu adapter boundary validation (pass-2 F11).** `LaunchProfile` is publicly
   constructible; the adapter is the backstop. `zhipuai_sdk.run` refuses (pre-launch,
   `CompositionError`) a `max_tokens` that is not a non-boolean `int ≥ 1`; only a valid
   value (or None → 1024 default) reaches the worker request.
8. **Claude budget composition validation (pass-3 S-F8).** `LaunchProfile` is publicly
   constructible; `claude_cli.build_command` currently stringifies any non-None
   `max_budget_usd`. It gains the same boundary rule as zhipu: a present value must be a
   non-boolean finite number > 0, else `CompositionError` before launch.

### AC3 — `JobRecord.quota_classification`

- Additive field: `quota_classification: Optional[dict] = None` (follows the
  `spec_digest`/`receipt_nonce`/`recovered_from` additive precedent, `registry.py:64-72`).
- Serde: `_record_to_dict` writes it; `_record_from_dict` reads with `.get(...)` default
  `None` (backward-read: every pre-#558 `jobs.json` loads unchanged).
- Written **in the same `replace(...)` + `upsert` that sets `state="quota_paused"`**
  (via `_finish(**updates)` — one atomic registry write, `registry.py`'s existing
  tempfile+`os.replace` path), so classification and state can never diverge. Also
  written on the refused-pause completed path (evidence-only, `paused: false`).
- Survives relaunch **explicitly, not automatically** (Step-4 self-review SR2 —
  confirmed): `_relaunch` calls `launch()` (`supervisor.py:994-1000`), which constructs a
  brand-NEW `JobRecord` under the SAME identity — the upsert replaces the paused record,
  so the classification would be lost. Fix: `launch()` gains an optional
  `quota_classification` passthrough (exactly the `recovered_from` precedent added by
  #554 on the same call, `supervisor.py:1000`), and `_relaunch` copies it from the paused
  record. A test asserts the field is present on the post-relaunch record.
- Corruption handling **uniform across every read API** (pass-2 F10 — `get`/`all`/
  `by_run` call `_record_from_dict` without `read_all`'s wrapper): the non-dict check
  lives INSIDE `_record_from_dict` itself, raising `RegistryCorrupt` directly, so no
  registry read path can leak a `TypeError`. Tests cover `get`, `all`, `by_run`, AND
  `read_all` with a malformed classification, plus a pre-#558 record.

## File changes

| File | Change |
|---|---|
| `phase_executor/src/phase_executor/quota_detect.py` | NEW — pure conjunctive classifier |
| `phase_executor/src/phase_executor/supervisor.py` | collect-branch detection, `_pause_allowed` factoring, `_read_stderr`, docstring |
| `phase_executor/src/phase_executor/registry.py` | `quota_classification` field + serde |
| `phase_executor/src/phase_executor/contract.py` | `LaunchProfile.max_tokens` + manifest mapping |
| `phase_executor/src/phase_executor/adapters/zhipuai_sdk.py` | wire `profile.max_tokens` |
| `phase_executor/src/phase_executor/pane_runner.py` | `_profile_from_spec` reads `max_tokens` (SR1) |
| `phase_executor/src/phase_executor/engine.py` | sync + competitive paths derive manifest profile + effective timeout (SR-F2) |
| `phase_executor/src/phase_executor/schemas/routing-table.schema.json` | `bounds.max_tokens` |
| `phase_executor/src/phase_executor/routing/rawgentic.routing-table.json` | per-seat cap values |
| `tests/phase_executor/fixtures/claude-stderr-*.txt` + `fixtures/MANIFEST.md` | NEW — raw classifier corpus; provenance ONLY in filenames + MANIFEST.md (no in-file headers) |
| `tests/phase_executor/test_quota_detect.py` | NEW — unit corpus tests |
| `tests/phase_executor/test_supervisor_launch.py` | await_job quota-collection integration tests |
| `tests/phase_executor/test_registry.py` | field serde/roundtrip/corruption |
| `tests/phase_executor/test_contract.py` | `max_tokens` bounds tests |
| Version surfaces ×6 + README changelog + this doc | per repo manual |

## Configuration changes

Routing-table manifests as above. No env vars, no new dependencies, no migration
(`jobs.json` is backward-read additive).

## Error handling / failure modes

- Classifier is **conservative by construction**: any conjunct miss → `False` → exactly
  today's behavior. A false NEGATIVE finalizes `completed` — terminal, so
  `mark_quota_paused` cannot convert it afterwards (pass-4 A-F6): recovery from a missed
  quota exit is an out-of-band re-dispatch, exactly as today (status quo, stated plainly); a false POSITIVE
  could wrongly resume a job — guarded by the conjunctive design + the five `_pause_allowed`
  conjuncts (read-only profile, resume policy, session id, effectful-sentinel refusal,
  verified kill).
- `stderr.txt` missing/unreadable/oversized → `StderrEvidence.read_error` set →
  `verdict=False` with the typed reason persisted — distinct from an ordinary negative,
  never raises inside `await_job`.
- Pause refused (mutating / fresh policy / uncalibrated / no session id / effectful sentinel / unverified spec) → visible `paused:false` evidence with its `refusal` reason, state = today's `completed`. Kill unverified → `completed_with_residue`, permit RETAINED — its lifecycle is the EXISTING one: `reap_plan`'s `_FINALIZED` tier sweeps the session once `dead_fn` confirms both groups dead (`registry.py:164-166`), and `QuotaCoordinator`'s stale-reap (dead holder pid) is the permit-leak backstop (`supervisor.py:656-658`). Never a silent drop.
- Malformed persisted classification → `RegistryCorrupt` raised inside
  `_record_from_dict` (uniform across get/all/by_run/read_all).

## Security implications

- NO provider stderr text is persisted in the registry — rule ids + raw-byte hashes only
  (a stderr line can carry tokens/URL secrets; the 0700 capture dir keeps the forensic
  copy). The envelope subtype persisted is a CLI-generated enum-like string, not prose.
- Automatic pause is quintuple-guarded (read-only, resume-policy, session id,
  non-effectful sentinel, verified-dead provider) — wrongful resume of a mutating or
  still-live job is structurally excluded; mutating pause stays owner-authorized manual.
- No new subprocess surfaces, no new network calls, no credential handling. For claude,
  `max_budget_usd` is verified as a preflight admission gate with live runtime budget
  tracking — mid-call termination on cap-crossing is UNVERIFIED (platform_apis), so the
  enforced per-call bound remains the supervisor/run_subprocess timeout; the zhipu token
  bound is preserved (1024), not loosened.

## Platform / external dependencies

platform_apis:
- api: `claude --max-budget-usd <amount>` on headless `--print` subscription-OAuth invocations (all claude seats, not just mutating)
  feasibility: verified via spike — FOUR live probes this run (2026-07-21, claude 2.1.216), claim scoped to exactly what they prove. (1) Acceptance: `--max-budget-usd 0.50` → exit 0, `total_cost_usd: 0.3368` (flag accepted + cost accounting live under subscription auth). (2) PREFLIGHT gate: `--max-budget-usd 0.01` → **exit 1, envelope `"subtype": "error_max_budget_usd"`, `is_error: true`, `duration_api_ms: 0`, zero tokens** — an under-estimate cap refuses BEFORE API work, fail-loud, machine-readable. (3+4) Mid-run crossing attempts: two output-heavy prompts under a barely-above-preflight cap — both times the model declined the token burn (session-config interference), AND probe 4's reply QUOTED its remaining budget ("$0.36 left") → the cap value is surfaced into the runtime (live budget tracking in-call). **What remains unverified: the termination shape when an ADMITTED call crosses its cap mid-run** — the likeliest-wrong claim in this design; named for #559's live cycle + #473 telemetry to observe on real trips. The per-call bound claim is therefore: verified preflight admission gate + verified runtime budget tracking + fail-loud machine-readable trip envelope; the supervisor/run_subprocess timeout remains the independently enforced backstop on every dispatch. Budget-trip capture doubles as a REAL negative fixture: exit 1 with EMPTY stderr (envelope on stdout) → the stderr-keyed classifier structurally never confuses a budget trip with a usage-limit exit.
  failure: fail-loud
  (proven: exit 1 + machine-readable `error_max_budget_usd` envelope subtype → surfaces as a NONZERO_EXIT observation on our side)
- api: `zhipuai` SDK `max_tokens` request parameter
  feasibility: verified via existing-call-site — `workers/zhipuai_call.py:37` already passes `max_tokens` from the request JSON; this design only threads the value from the manifest.
  failure: fail-loud
  (SDK raises; worker exits nonzero → NONZERO_EXIT observation)
- api: `re` stdlib classification of captured stderr text
  feasibility: verified via existing-call-site — stdlib, same pattern class as `plan_lib`'s matchers; no platform risk.
  failure: fail-loud
  (pure function; fixture-pinned)

## Multi-PR assessment

Single PR. ~10 impl files, one contained package + schema/table; no separable phases
(classifier without persistence or caps would strand AC coverage).

## Test strategy (red-first)

1. `test_quota_detect.py`: every positive fixture → `verdict=True`; EVERY negative-corpus
   fixture (wrong-cwd resume, SIGKILL-137, auth expiry, account-selection, network, 429
   throttle, exit-0 transcript-quota, upgrade-only usage-limit, budget-trip empty-stderr)
   → `False`; conjunct-isolation cases (right text wrong exit; right exit wrong provider; usage lang without reset lang); read_error evidence distinct from ordinary negative; bounded-read matrix — ceiling−1 / ceiling / ceiling+1 bytes, invalid UTF-8 with asserted raw-byte hash, file growth during read, adversarial maximum-length lines with bounded runtime, no classification from an oversized prefix.
2. `await_job` integration, TWO primary cases (pass-4 A-F3 — the shipped v1 default is
   SHADOW): (i) v1 quota-shaped capture (all other guards satisfied) → `completed` with
   `paused:false`, `refusal:"uncalibrated_classifier"`; (ii) an explicit
   allowlist-injected calibrated pair (a test-scoped `CALIBRATED_CLASSIFIERS` containing
   the stub's `(version, digest)`) with the same capture →
   `quota_paused`, permit released, classification persisted in the same record,
   `classify_recovery` says `relaunch` — pinning the path #559 activates by bumping one
   constant. Then the refusal matrix — fresh policy →
   `session_not_persisted`; mutating profile → `mutating_requires_manual`; no envelope →
   `no_resumable_session`; effectful sentinel → refused; kill unverified →
   `completed_with_residue` + permit retained; each with `paused:false` evidence.
   `mark_quota_paused` routes through `_finish` with injected evidence; its existing
   tests stay green.
3. Registry: roundtrip with/without field; pre-#558 record loads (backward-read);
   malformed field → `RegistryCorrupt`.
4. Contract/adapters: `max_tokens` mapping, strict types, zhipu wiring, claude argv
   carries `--max-budget-usd` for a bounds-bearing non-mutating profile; supervised spec
   ROUND-TRIP pins every profile field incl. `max_tokens` (write supervisor.py:403-405 →
   read pane_runner.py:93-99 → relaunch rebuild :986-990); relaunch-survival test pins
   `quota_classification` on the post-relaunch record.

## Peer-consult provenance

Backend `gpt` (blind both ways; consult rc=0; report `docs/reviews/peer-rawgentic-peer-problem-558-2026-07-21.md`, run-local/gitignored). Independent proposal converged on the core: pure versioned stderr-only conjunctive classifier, shared pause-guard helper for both writers, `_finish` extension with ONE atomic upsert, additive strictly-validated `JobRecord` field, no invented claude token/turn flags, table-driven corpus with labelled-synthetic positives.

**Adopted from gpt:** bounded stderr read (byte ceiling + replacement decoding); `NONZERO_EXIT` precheck before file I/O; evidence shape enriched with `source`/`rule_ids`/echoed engine+exit; "never silently broaden matches — version every rule change".

**Declined, with rationale:**
- *positive-but-unsafe → `exited_no_sentinel`/`quarantined`*: a validated sentinel WAS collected — registry semantics (`registry.py:31`) define `completed` as exactly that; relabelling would falsify state for downstream reconcile. `completed` is terminal (never relaunched), which already satisfies the safety property gpt wanted; the persisted refusal evidence carries the signal.
- *cap union incl. `max_turns` + caps-required enforcement + adapter capability rejection*: no engine here enforces a turn cap (claude/codex have no flag; zhipu takes tokens) — a dead schema field; caps-required breaks existing third-party tables (gpt's own migration-risk item); manifests are per-SEAT and serve multi-engine chains, so "reject unsupported cap for engine" mis-fits — per-engine consumption at composition (each adapter enforces what it can; timeout is the universal floor) is the correct reading of AC-B4's "where the provider supports one".
- *pre-launch atomic reservation ledgers (run+pool, pricing conversion, leases)*: AC-B4's OTHER clause — run-level accounting, explicitly out of #558 AC2 (issue body names per-call caps). Recorded as epic-#560-adjacent follow-up in the run-record.
