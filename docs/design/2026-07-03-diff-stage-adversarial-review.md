# Design: Diff-Stage Cross-Model Adversarial Review at WF2 Step 11 (#131) — rev 3

Date: 2026-07-03 · Issue: #131 · Complexity: standard_feature · rev 3 (rev 2 after pass-1 critique: Codex 2H/3M + Judge1 2M/5L + Judge2 1H/3M/3L + Judge3 1H/4M, loop-back 1; rev 3 after pass-2 Codex 1H/3M, loop-back 2 — design budget now exhausted 2/2)

## Problem

WF2's cross-model gates (Step 4 design, Step 6 plan) never see the code diff; Step 11 is same-model only. Field evidence: three fail-open security bugs caught only by ad-hoc cross-model diff review. WF5's engine scopes to text artifacts and explicitly excludes diffs.

## Approaches considered

**A (chosen) — additive `diff` artifact type + temp-file reuse.** Register `diff` in the engine's type registry with a refutation lens; WF2 Step 11 writes the diff to a temp file under the project root and invokes the existing `review` CLI. Engine core (schema, nonce fencing, validation, fail-closed exits, report rendering, secret scan, truncation) reused.
**B (rejected)** — `review-diff` subcommand running `git diff` inside the lib: couples engine to repo state, more plumbing, no added safety.
**C (rejected)** — same-model registered code-reviewer subagent: defeats the cross-model purpose.

## File changes

### 1. `hooks/adversarial_review_lib.py` (additive)

- `ARTIFACT_TYPES += ("diff",)`.
- `_TYPE_LENS["diff"]` — refutation framing: the artifact is a unified git diff; attack the CHANGE: hunt fail-open paths (guard bypassable, error path silently passes, check vacuous on empty/corrupt/absent input, "no response"/"not found" treated as success, a security gate weakened or annotated away, tenant/auth/session/token boundary mistakes) and behavior regressions the change's intent does not admit; evidence quotes copy diff lines verbatim including `+`/`-` markers.
- **`review --findings-json <path>` (optional) with a fail-closed sidecar contract** (exit 0 ⟺ fresh, valid sidecar):
  - The path is VALIDATED before any use: realpath of the target's parent directory must be `project_root` or under it (mirror of `resolve_artifact_path` for a not-yet-existing file; NUL/traversal/absolute-escape rejected) → violation is a usage error, non-zero exit. [Judge3 H1]
  - Any pre-existing file at the path is REMOVED before the codex call (stale-read protection, mirroring `run_codex_consult`'s out_path discipline). [Judge3 M4, Judge2 M3b]
  - The sidecar is written ONLY on genuine success, AFTER the markdown report write succeeds; a sidecar-write OSError exits 3 exactly like the existing report-write fail-closed path. [Judge1 L4, Judge2 M3, Codex M4]
  - Payload: `{"status": "success", "summary": str, "truncated": bool, "secrets": [str], "findings": [normalized findings]}` — `truncated`/`secrets` give the structured consumer parity with the human report. [Judge1 M2]
  - Absent flag → CLI behavior byte-identical to today.
  - Naming (`--findings-json`, not `--out`): `consult` is JSON-primary with a REQUIRED `--out`; `review` stays markdown-primary with an OPTIONAL sidecar — different contract, different name, documented in the CLI help. Step 4/6 embedded callers are NOT migrated in this issue (they consume the summary/report today and work; convergence is a possible follow-up). [Judge1 L3 resolution]
- `ADV_CONFIDENCE_TO_FLOAT: Final = {"high": 0.9, "medium": 0.7, "low": 0.4}` — the explicit enum→numeric mapping consumers use to run adversarial findings through `SEVERITY_BANDED_CONFIDENCE` filtering. [Judge2 M2]
- **Diff-type secret posture (rev 4 — hard block REJECTED):** the diff type keeps the existing warn-only posture. Rationale: the identifier-level scanner (`password[:=]`, `api[_-]?key[:=]`, `token[:= ]`, `secret[:=]`) matches legitimate identifiers that densely populate exactly the security-surface diffs this feature targets — a headless hard block would fail-closed the review on its primary target set, and the resulting `failed` state would masquerade as coverage [rev3-verifier F2, refuting rev-2's J3#3 adoption]. Instead: the sidecar's `secrets` categories are SURFACED — appended to the Step 11 marker and included in the headless STATUS comment — so detection is visible without self-defeating the gate. Environments wanting a hard block set the existing `RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS=1` lever (documented for the diff type in config-reference).

### 2. `hooks/plan_lib.py` (additive; `should_promote` UNTOUCHED)

- `any_high_risk_path(paths, extra_patterns=()) -> str | None` — thin plural wrapper looping over the EXISTING private `_path_matches_high_risk` (the `read_review_log` public-alias precedent); returns the **first matching PATH** (not the pattern), which is what reason strings and markers need [rev2-Codex M3]. No refactor of `should_promote`; one matcher, zero drift. [Judge1 M1, Judge3 note]
- `should_run_diff_review(enabled: bool, changed_paths: list[str], has_high_risk_task: bool, extra_patterns=()) -> tuple[bool, str]` — the PURE composite gate, so the dispatch decision is unit-testable red-green instead of living in prose [Judge2 H1]. Truth table:
  - `not enabled` → `(False, "disabled")` (checked first — opt-in gates everything) [Codex M3: explicit `enabled AND (path OR task)` precedence]
  - `enabled and not changed_paths` → `(False, "empty diff")` (no wasted egress on an empty diff even when a high-risk task exists) [Judge2 L6]
  - `enabled and any_high_risk_path(changed_paths)` → `(True, "high-risk path: <first matching path>")` — the reason carries the PATH only; `any_high_risk_path` returns the first matching path (`str | None`), so no pattern plumbing is needed [rev2-Codex M3]
  - `enabled and has_high_risk_task` → `(True, "high-risk task in plan")`
  - otherwise → `(False, "no security surface")`

### 3. `skills/adversarial-review/SKILL.md`

- description: drop the blanket "NOT for reviewing code diffs"; supported types += `diff` (report-only, refutation lens); argument-hint += diff.
- Step 1 auto-detect: `*.patch` / `*.diff` → `diff`.
- `<constants>`: document `--findings-json` and the diff-type headless secret-block.
- `<data-handling>`: a diff is raw source code — highest secret density; headless diff reviews block on detected secrets; NOTE an agent-harness egress classifier may block the invocation entirely — embedded callers treat that as review-failed (non-blocking), standalone runs surface it.

### 4. `skills/implement-feature/SKILL.md` — Step 11 sub-step (mirrors Step 4 item 7 contract)

- **Stale-temp startup cleanup:** at sub-step start, delete any leftover `.rawgentic-diff-review-*.patch` / `.rawgentic-diff-findings-*.json` under the project root (crash recovery from a prior killed run — cleanup-on-exit cannot cover SIGKILL). [rev2-Codex M4]
- **Gate (up front, before dispatching the 3 review agents):** determine enablement with the exact existing probe — `python3 hooks/adversarial_review_lib.py is-enabled --workspace .rawgentic_workspace.json --project <name> --skill implement-feature`; exit 0 ⇒ enabled, any non-zero ⇒ disabled (same command and semantics as Step 4 item 7) [rev2-Codex M2]. Compute `changed_paths` from `git diff --name-only origin/<default_branch>..HEAD` — the SAME base ref used for the patch below, so detection and reviewed content are identical [Judge1 L5]. **If the git command exits non-zero (missing/unfetched base ref), emit the marker `failed (base ref unavailable: <reason>)`, skip dispatch, and continue — the failed marker satisfies the completion-gate requirement** [rev2-Codex H1]. `has_high_risk_task` from the Step 5 plan. Call `plan_lib.should_run_diff_review(enabled, changed_paths, has_high_risk_task)`. False → log the 4-state marker `skipped (<reason>)`, done.
- **Dispatch (concurrent with the 3 review agents):** write the diff **high-risk-files-first** (`git diff origin/<default>..HEAD -- <matching files>` then the remainder) so engine truncation can only cut low-risk tail [Codex H1]; unique run-scoped names `.rawgentic-diff-review-<issue>-<token>.patch` and `.rawgentic-diff-findings-<issue>-<token>.json` under the project root [Judge2 L5]; run `review --artifact <patch> --type diff --project-root <root> --date <date> --findings-json <sidecar>` [Codex H2] in the background, appending `--headless` when the WF2 run is headless (correct prereq/auth messaging; the flag is the engine's ONLY headless signal — no env detection exists) [rev3-verifier F1].
- **Join (before item 3's confidence filter):**
  - non-zero exit → `failed (<exit>: <reason>)`, loud log; **headless: post a STATUS comment noting the skipped diff review (parity with Step 4)** [Judge2 L7]; continue with same-model findings — never treated as passed.
  - exit 0 but sidecar missing/unreadable/schema-invalid → `failed (<reason>)`, never `no_findings`. [Codex M4]
  - sidecar `truncated: true` → `failed (truncated)` — even with high-risk-first ordering, an over-200KB high-risk surface means the reviewer did not see it all; conservative and rare. [Codex H1]
  - success → map each finding's confidence via `ADV_CONFIDENCE_TO_FLOAT`, tag `source: adversarial`, merge into the Step 11 finding list BEFORE item 3 so the severity-banded confidence filter processes them identically [Judge2 M2]; single ambiguity breaker at item 6 over the merged list; design-flaw loop-back stays the single existing `review` source. Marker: `findings_present <N>` or `no_findings`; when the sidecar's `secrets` list is non-empty, append `; secrets detected: <categories>` to the marker (and to the headless STATUS comment) so scanner hits are visible without blocking [rev3-verifier F1/F2 resolution].
- **Cleanup (always-run, finally-style):** delete patch + sidecar on every handled exit path, including failures before join [Judge3 M2]; create both files with restrictive permissions (0600 / umask 077) [Judge3 note, rev2-Codex M4]; the startup stale-temp sweep above covers unhandled termination; `.gitignore` gains `.rawgentic-diff-review-*.patch` and `.rawgentic-diff-findings-*.json` as the staging backstop (repo currently has no gitignore coverage for these) [Judge1 L6, Judge2 L5].
- **Marker (4-state):** `### WF2 Step 11 — Adversarial Diff Review: findings_present <N>|no_findings|failed (<reason>)|skipped (<reason>) — <report path if any>`.
- **Enforcement (rev 4 — no recompute):** `<completion-gate>` gains an item — when the project is opted in (`is-enabled … --skill implement-feature` exits 0), a Step 11 Adversarial Diff Review 4-state marker MUST exist in session notes; absence fails the gate. No gate-time diff recompute: a post-merge recompute sees an empty diff and would waive the requirement exactly in the merge path [rev3-verifier F3]. Opt-in ⇒ marker, unconditionally — `skipped (<reason>)` is a legitimate marker, silent omission is not. Turns "never ran" into a detectable state. [Judge3 M5]

### 5. Tests (TDD, pytest — PATH-stubbed codex; no live calls)

- `tests/hooks/test_plan_lib.py`: `any_high_risk_path` (match, boundary no-match e.g. `src/author.ts`, extra_patterns, empty→None); `should_run_diff_review` full matrix — disabled×{path,task,both,neither}, enabled×{empty-diff+task, path-only, task-only, both, neither} — asserting both the bool and the reason string. [Judge2 H1]
- `tests/hooks/test_adversarial_review_diff.py`: `diff` in ARTIFACT_TYPES; lens contains refutation phrases; `build_prompt(…,"diff")` embeds lens + nonce fences; CLI `review --type diff` happy path writes report AND sidecar with the documented shape incl. `truncated`/`secrets` keys; sidecar ABSENT (and pre-existing one removed) on exit 2/3/4; report-write failure leaves no exit-0 illusion and no fresh sidecar; sidecar-write failure exits 3; sidecar path escaping project root rejected; headless diff secret-block (secret-bearing diff + --headless → status error, no codex call); interactive warn-only unchanged. [Judge2 M4, Judge3 H1/M3/M4]
- Existing suites (`test_adversarial_review_*`, `test_plan_lib`) stay green — all changes additive.

### 6. Docs

- README: WF5 scope line (+diff), WF2 Step 11 gate mention.
- `docs/config-reference.md`: **coupled opt-in called out explicitly** — `adversarialReview.workflows: ["implement-feature"]` now enables the cross-model pass at Steps 4, 6 AND 11 (one more Codex egress/call per security-surface run); per-stage granularity would be a future sub-key. [Judge1 L7]
- `docs/design/workflow-adversarial-review.md` scope note; this design doc committed to `docs/design/` + HTML artifact.

## Error handling / failure modes

- All engine failure modes inherited (exit 2/3/4, fail-closed, never fabricate findings).
- Oversized diff → high-risk-first construction + truncated⇒failed (above).
- Egress blocked by an agent-harness exfil classifier (observed in the field; a diff IS repo content) → non-zero → the failed path, loud, non-blocking.
- Secrets in diff → headless: blocked; interactive: named categories in the egress warning before proceeding.
- Concurrent runs → run-scoped unique temp names; engine temp files already uuid-keyed.

## Security implications

Egress of proprietary source (opt-in config gates the feature; warn-interactive/block-headless posture for the diff type). Prompt injection from diff content: nonce fence + injected-text-is-a-finding rule inherited. Sidecar write path validated under project root, fail-closed. Report-only invariant preserved.

## Peer-consult provenance (cross-model, blind both ways)

Codex peer proposal (docs/reviews/peer-rawgentic-peer-problem-131-2026-07-03.md) independently converged on the architecture (diff-as-guarded-text-artifact, engine lens, opt-in AND security-surface gate, non-blocking join). Adopted from the peer: structured findings sidecar (→ `--findings-json`); explicit outcome vocabulary (→ 4-state marker). Rejected: `generic`-type reuse (AC1 wants first-class `diff`); `.rawgentic/reviews/` temp dir (root-dotfile convention + gitignore backstop instead).

## Critique amendments (rev 1 → rev 2)

| Source | Sev | Amendment |
|---|---|---|
| Codex #1 | High | high-risk-first patch construction; truncated ⇒ failed |
| Codex #2 | High | dispatch command includes `--findings-json <sidecar>` |
| Judge2 #1 | High | pure `should_run_diff_review` gate function; 9-cell tested matrix |
| Judge3 #1 | High | sidecar path validated under project root, fail-closed |
| Codex #3 / J2#2 / J3#4 / J1#2,#4 | Med | explicit gate precedence; enum→float confidence map + merge before item 3; stale-sidecar removal; sidecar carries truncated/secrets; write-failure ⇒ exit 3 |
| Codex #5 / J3#2 / J1#6 / J2#5 | Med/Low | finally-cleanup, run-unique names, gitignore entries |
| J3#3 | Med | headless diff secret-block |
| J3#5 | Med | completion-gate marker enforcement |
| J1#1 | Med | thin wrapper; `should_promote` untouched |
| J2#6 | Low | empty-diff cell in the gate function |
| J2#7 | Low | headless STATUS comment parity |
| J1#3,#5,#7 | Low | naming justification; single base ref; coupled opt-in documented |
| rev2-Codex #1 | High | base-ref failure ⇒ `failed (base ref unavailable)` marker, never a crashed gate |
| rev2-Codex #2,#3,#4 | Med | exact is-enabled command named; reason carries path only (wrapper returns first matching path); stale-temp startup sweep + 0600 perms |
| rev3-verifier F2 | High | headless hard secret-block REVERTED — identifier-level scanner would self-block the feature's own target set; warn-only + visible surfacing + existing env lever instead |
| rev3-verifier F1 | Med | `--headless` propagated on the dispatch command (engine's only headless signal) |
| rev3-verifier F3 | Med | completion-gate enforcement = opt-in ⇒ marker exists, no post-merge diff recompute |

## Out of scope

Step 11 3-agent replacement; WF3 equivalent (follow-up); migrating Step 4/6 callers to the sidecar; base refs other than `origin/<default_branch>`; mandatory pass for non-security diffs.
