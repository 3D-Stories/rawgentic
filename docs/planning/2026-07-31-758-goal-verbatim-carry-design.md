# #758 — Owner-authored goals: verbatim carry across pane-handoffs (design note, small-standard lane)

**Rev 4** — pass-3 finding resolved per owner decision (D18: apply final fixes, proceed, no
fourth pass): the strict snapshot is enforced for EVERY validated teardown — including the
"no live goal" state (`expected = None`; a goal appearing where none was → clear refused,
pane left open) — with the cleared→B regression test added, and the residual read→clear race
documented as the platform ceiling (`/goal clear` has no compare-and-clear form; mitigation:
a goal cannot arm mid-turn in a busy pane — arming requires a Stop evaluation).

**Rev 3** — pass-2 verifier findings applied (both source-confirmed): liveness is now computed
sentinel-only inside `live_owner_goal` (never delegated to `goal_currently_unmet`, which scans
all recursively-discovered rows without a sentinel check — launcher_lib.py:803-823), and the
rev-2 "accepted risk" on mid-handoff re-arm is replaced by an enforced binding (rev 2's
rationale was factually wrong: `perform_handoff` keys the clear on the NEWEST goal row and
treats `--predecessor-goal-condition` as assertion-only — launcher_lib.py:1554,1583-1588).
Rev 2 applied the pass-1 set (D15, owner-approved; 8 findings, all source-verified).

Epic #756 child 4/17. Issue body (2026-07-30T18:14:55Z) is the contract. Source: forensics
§10.1 — owner-authored goals run 1,200–2,000 chars; model-drafted successor goals cluster at
4,000–5,400, and each accretion widens the misinterpretation surface.

## Approach (one obvious; lane note — no multi-approach brainstorm)

The mechanical guard mirrors what `mid-child-handoff` already does (launcher_lib.py:2506:
provenance transcript + equality assertion): make `ad-hoc-handoff`'s **retirement path**
validate the successor's goal against the predecessor's own LIVE goal, read from the
predecessor's transcript. The skill prose stops inviting drafting ("in its own words") and
mandates verbatim carry.

## File changes

1. **`hooks/launcher_lib.py`**
   - New helper `live_owner_goal(transcript_text) -> str | None` — trusts ONLY
     sentinel-bearing goal rows (`row.get("sentinel") is True`), never a bare nested
     `type: goal_status` object, because `_find_goal_status` is deliberately recursive and a
     tool result embedded in the transcript can carry a forged row (pass-1 F2). **Liveness is
     computed inside the helper from the same sentinel-only row stream (pass-2 F1):** walk
     `_iter_goal_status`, keep rows with `sentinel is True` and a non-empty string condition;
     the LAST such row decides — `met is False` → that condition is the live owner goal
     (returned VERBATIM); `met is True` → None (cleared); no sentinel rows at all → None.
     `goal_currently_unmet` is deliberately NOT used: it scans every recursively-discovered
     row without a sentinel check, so a forged sentinel-less `met:true` row carrying the
     genuine condition would spoof "already cleared" through it (pass-2 F1, source-confirmed).
     (Pass-1 F6 context: `last_unmet_goal_condition` is historical, not live — also unused here.)
   - New pure function `validate_goal_carry(successor_goal, predecessor_live_goal, *, approved_answer) -> tuple[bool, str]`:
     - Comparison is on **armed forms**: `armed_condition(x)[0]` of each side, exact `==`
       after ONE documented normalization — strip a single trailing newline from the
       successor text (file reads end with `\n`; the armed row never carries it). No
       `strip()` (pass-1 F4).
     - `approved_answer` (non-empty string) passes with a reason string recording the
       override verbatim; empty/None means no override.
     - `predecessor_live_goal is None` passes (nothing to validate — goal already cleared).
     - On mismatch the reason carries ONLY lengths + the numeric first-divergence offset —
       never goal content (pass-1 F8).
   - `_cmd_ad_hoc_handoff`, **teardown path only** (the successor continues THIS session's
     work):
     - Validate `own` (already required) with `_SESSION_ID_RE` and assert the built path
       `<transcript_dir>/<own>.jsonl` resolves directly beneath `transcript_dir` (pass-1 F7).
     - Transcript absent or unreadable → **LauncherError (fail CLOSED)**: validation runs
       before `perform_handoff`, so refusing strands nothing; `--no-teardown` is the escape
       for genuinely additive work (pass-1 F3 — reverses rev-1's fail-open choice).
     - `live_owner_goal(...)` → None ⇒ proceed (already-cleared case). Otherwise
       `validate_goal_carry` must pass, else LauncherError (exit 2) naming the #758 rule and
       both escape hatches.
   - Flag `--goal-rewrite-approved <owner-answer>` **takes a required value**: the owner's
     verbatim yes/no answer approving the new goal text (D15/F1 resolution). Recorded in the
     output JSON as `goal_rewrite_approved: "<answer>"` so the audit trail carries the claimed
     approval text, not a bare boolean. Trust ceiling stated in the docstring: this is a
     caller assertion — no crypto root of trust exists; the enforceable layer is the skill
     prose gating it on an explicit owner question plus this audit record. AC3 permits
     "rejects or flags"; an unapproved differing goal is REJECTED, an approved one is FLAGGED
     in the audit output.
   - **Enforced binding on the destructive half (pass-2 F2 + pass-3 F1, D18):**
     `perform_handoff` gains two keyword-only params — `strict_goal_binding: bool = False`
     and `expected_predecessor_goal: str | None = None` (defaults preserve every existing
     caller byte-identically). The ad-hoc CLI passes `strict_goal_binding=True` on EVERY
     validated teardown — with `expected_predecessor_goal` set to the validated live goal,
     **or `None` when validation found no live goal** (the cleared state is part of the
     snapshot, pass-3 F1). Under strict binding the teardown-clear section re-reads the
     predecessor transcript immediately before clearing and REFUSES the clear (pane left OPEN
     and guarded; `predecessor_guard` names the refusal + numeric divergence offset, no goal
     content) whenever the newest sentinel-bearing live condition differs from the snapshot —
     including a goal APPEARING where the snapshot said none (then nothing is cleared and the
     pane is not closed). The successor — already verified and armed with the VALIDATED owner
     goal — keeps running. Scope choices, stated: the destructive half aborts; the handoff is
     not unwound (the successor carries the goal that was owner-authored at validation time —
     exactly the #758 contract); and the residual race between the final re-read and the
     clear send is a PLATFORM CEILING — `/goal clear` has no compare-and-clear form — accepted
     with the mitigation that a goal cannot arm mid-turn in a busy pane (arming requires a
     Stop evaluation), documented in the docstring (D18). Tests: A→B re-arm → clear refused,
     pane open, successor armed with A; cleared→B (snapshot None, B appears) → clear refused,
     pane open.
   - `--no-teardown` (additive helper handoff) is exempt: the predecessor keeps its own goal;
     a helper legitimately gets different work.
2. **`skills/pane-handoff/SKILL.md`** — rewrite "The goal condition" (Step 1) to:
   goal text is OWNER-AUTHORED and carried **verbatim** (read it with `read-goal-condition`,
   never retype/summarize); model state (STATE/MODE/progress) goes in the handoff FILE, never
   into the goal; a goal change requires an explicit yes/no AskUserQuestion naming the prior
   instruction (owner away → `/ask-owner`) — never embedded in a >500-char paste — and only
   then `--goal-rewrite-approved '<the owner's answer>'`.
3. **Tests (red before green)**
   - `tests/hooks/test_adhoc_pane_handoff.py` — new `TestVerbatimGoalCarry`:
     differing successor goal on teardown path → refused, `perform_handoff` never called (AC3);
     identical goal → proceeds; `--goal-rewrite-approved 'yes — owner said so'` + differing →
     proceeds AND the answer appears in output JSON; goal cleared (unmet→met history) →
     proceeds (liveness via last sentinel row met:true, F6/p2-F1); **forged nested goal_status
     rows are ignored both ways** — a forged sentinel-less unmet row cannot inject a phantom
     goal AND a forged sentinel-less `met:true` row carrying the genuine condition cannot
     spoof "already cleared" (F2 + p2-F1 regression pair); missing/unreadable transcript →
     REFUSED (F3); refusal message carries no goal content (F8); `--no-teardown` → no
     validation; **A→B re-arm between validation and teardown → clear REFUSED under
     strict_goal_binding, pane open, successor armed with A** (p2-F2); `strict_goal_binding`
     defaults False → existing perform_handoff callers byte-identical. Plus
     `validate_goal_carry` / `live_owner_goal` unit rows (trailing-newline normalization,
     armed-form comparison).
   - `tests/test_pane_handoff_skill.py` — prose drift guards: one canonical verbatim-carry
     sentence; the AskUserQuestion/ask-owner approval rule present; "in its own words" gone.
4. **Version surfaces ×4** (3.109.8 → 3.110.0, feat=minor) + README changelog + docs.

## Error handling and failure modes

- **False refusal** (owner really did hand the successor different work): escape hatches are
  `--goal-rewrite-approved '<answer>'` (after the explicit owner yes/no) or `--no-teardown`.
- **Transcript unreadable** on teardown path: REFUSE (fail-closed, F3) — documented in the
  docstring per the repo's per-hook fail-mode rule.
- **Predecessor already cleared its goal** (clear-prep ordering): `live_owner_goal` returns
  None → nothing to validate → proceed (matches existing `already_clear` semantics).
- **Over-cap goals:** comparison uses `armed_condition` on both sides, so a >4000-char goal
  compares in its armed (truncated) form — the same form the successor row will carry.
- **Backward compat:** callers carrying identical goals are unaffected; callers passing
  model-drafted differing goals are now refused — that is the feature, not a regression.
  `perform_handoff` gains one keyword-only param, `strict_goal_binding=False` — every
  existing caller (campaign `handoff`, `mid-child-handoff`) is byte-identical by default;
  the validation guard itself lives in the CLI adapter, and only the strict clear-binding
  lives inside `perform_handoff` (it must — the clear happens there).

## Security implications

Guard READS the caller's own transcript (directory already a required input) and compares
strings. Sentinel-only row trust closes the forged-nested-row spoof (F2). Session-id validated
against `_SESSION_ID_RE` + containment before path construction (F7). Refusal output carries
lengths + numeric divergence offset only — no goal content, no control sequences (F8). No
shell interpolation, no new subprocess.

## Platform / external dependencies

platform_apis: none

## Scope fidelity

In: pane-handoff goal-carry contract + emitted guidance; ad-hoc-handoff guard path.
Out (per issue): /goal evaluator (harness), stop-hook breaker (P3 sibling #760 holds the
deferred authorization halves), deferral registry (P2 sibling). `mid-child-handoff` already
validates — untouched. Campaign `handoff` derives its goal from the predecessor transcript
itself — already provenance-bound, untouched.
