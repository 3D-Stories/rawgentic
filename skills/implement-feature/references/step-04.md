## Step 4: Quality Gate — Design Critique

### Instructions

Step 4 applies the in-repo **quality-bar rubric** (`references/quality-bar.md`) to the design for **all** lanes. The same-model
multi-judge design panel (WF2's old full-critique gate) was retired from WF2 (#190): owner
telemetry showed ≈ 0 measured gain from it. High-stakes design scrutiny stays available — cross-model and opt-in —
via the WF5 **adversarial-on-design** sub-step (item 7), which is where genuine design-flaw
catching now lives on the full spine.

**Determine gate shape based on lane eligibility** (`small_standard_lane_eligible`, a.k.a.
the `fast_path_eligible` alias):
- If `fast_path_eligible == true` (lane): apply the quality-bar rubric only — **NO peer consult
  (the Step 3 sub-step) and NO adversarial-on-design (item 7)**. The lane drops all
  design-stage cross-model ceremony.
- If `fast_path_eligible == false` (full spine): apply the quality-bar rubric, **plus** the opt-in
  Step 3 peer consult and the opt-in adversarial-on-design sub-step (item 7) below.

**Design self-review (quality-bar rubric, `references/quality-bar.md`) — all lanes:**

<!-- model-routing: role=review -->
Run the design self-review INLINE per the `<model-routing-resolve>` contract — a single-pass,
same-model check by the orchestrator over the design, on the `security` lens per
`<review-lens-routing>` (SKILL.md). The cross-model layer at this gate is the opt-in
adversarial-on-design sub-step (item 7), which dispatches
`python3 hooks/review_runner.py review-artifact --artifact <design-doc> --type design
--author-model <your model id> --reviewer <default per the contract> --out <result.json>
--project-root .` from a read-only harness subagent. **The design document is a REQUIRED input: the runner's
mandatory `--artifact` parameter carries it, so a route that cannot carry the bytes cannot be
called (#826) — a review of nothing can never return a verdict this gate would read as a pass.**
The quality-bar self-review is a single-pass, same-model check over the design:
- Does the design respect existing patterns and project conventions, with appropriate
  dependencies (prefer existing libraries)?
- Are all acceptance criteria addressed, edge cases and failure modes identified, and the
  implementation verifiable (tests if available, otherwise manual checks or scripts)?
- Input validation at boundaries, credential handling (no hardcoded secrets),
  backward-compatibility or a migration plan, acceptable performance?
- **Platform / external-dependency feasibility (#226):** an absent `platform_apis:`
  declaration, an `assumed` dependency, weak/uncited evidence (`verified via docs`), or a
  `fail-silent` API with no `surface:` is a **blocking** finding (Critical/High). Beyond
  those mechanical checks, judge credibility: does the cited
  evidence actually prove the API works under THIS project's real config (does the named
  capability/manifest file truly grant it; is the call site the exact API on the exact object
  kind; did the spike exercise the real surface)? Probe-before-claim (`<probe-before-design>`,
  SKILL.md): a spike that exercised a proxy composition rather than the exact shipped
  invocation is itself a blocking finding. And — only judgment can see this — does the
  design **use** a platform API it failed to declare (a `platform_apis: none` that is actually
  false)? A used-but-undeclared API is itself the finding.
- For WF1-validated issues: does the design align with the WF1-critiqued spec?

The self-review produces findings in the shape the gate consumes:
   ```
   Finding #N:
   - Severity: Critical | High | Medium | Low
   - Category: architecture | completeness | security | platform_feasibility | testability | scope_fidelity | migration_safety | performance
   - Description: [what the issue is]
   - Recommendation: [specific action]
   - Ambiguity flag: clear | ambiguous
   - Ambiguity reason: [why, if ambiguous]
   - Loopback-class: spec-tightening | design-flaw   (Critical/High only — lower severities never loop back)
   ```

   **Loopback-class guidance (#223):** `spec-tightening` = the design's INTENT is right
   but its text is wrong — a wording fix, a stale file:line anchor, an internal
   contradiction the author's own edits introduced, a missing sentence the reviewer can
   state verbatim in the Recommendation. `design-flaw` = the intent or structure is
   wrong — wrong approach, missing component, security hole, infeasible dependency.
   When unsure → `design-flaw`.

4. **Volume threshold check** (per-tier independent): thresholds per `VOLUME_THRESHOLDS`.

5. **If loop-back triggered:**
   - The volume loop-back (≥ threshold findings = the design-is-a-mess signal) **NEVER folds**
     to the spec-tightening path — it stays unconditionally on the full `design` source (#223).
   - Check `design_loopback_count` and `global_loopback_total`
   - If within budget: increment counters, apply findings as constraints, return to Step 3
   - **If budget exhausted — the design gate CLOSES budget-exhausted; it does NOT escalate (#798).** Self-close only when the attempted source is `design` and `state["design"] >= _LOOPBACK_SOURCE_MAX["design"]` and the GLOBAL cap is not reached and the ambiguity breaker completed `clear`. A refusal caused by the global cap, or involving `spec_tighten`/`tdd`/`review`/`review_design`, or any ambiguous or conflicting finding, retains STOP/escalate. Before closing, WAIT for any enabled adversarial review (never discard it — a terminal close has no next pass to run the breaker on), merge its findings, and run the ambiguity breaker EXACTLY ONCE; an enabled review that failed, timed out, or returned malformed output is STOP/escalate, because a close is only justified by a *foregone* verdict and a verdict that could not be obtained is not foregone. Then APPLY the final pass's findings to the design, and only then record the close by running:
     ```bash
     python3 hooks/plan_lib.py close-design-gate --issue <n> --gate 4 \
       --findings-file <json> --counters claude_docs/.wf2-state/<n>/loopback_counters.json \
       --breaker-result clear --ledger claude_docs/.wf2-state/<n>/dispositions.jsonl \
       --record-out <extra.json> --note-out claude_docs/session_notes.md --date <YYYY-MM-DD> \
       --project-root .
     ```
     Splice the printed `{"label": "design_gate_close", ...}` object into the Step-16 run record's **TOP-LEVEL** `extra` list. All three write targets must resolve inside `--project-root` and must not be symlinks; `--gate` accepts only `4` (the carve-out is Step-4-only); a corrupt counters file, or any finding carrying an ambiguity/conflict marker, refuses the close even if `--breaker-result clear` was passed — the flag is cross-checked, never trusted.
     **Every Critical/High finding in `--findings-file` must additionally declare a terminal disposition (#903): `terminal_disposition: applied | refuted | deferred`, and `refuted`/`deferred` each need a non-empty `disposition_reason` — the close is only for exhaustion over RESOLVED ground** (#874). Medium/Low need none. Each finding keeps its FULL gate shape — `ambiguity_flag` included, or `findings_are_unambiguous` silently sees "clear" — and adds these; `severity` must BE one of the four band strings (an unclassifiable `"Blocker"` or list refuses, never passes) and `disposition_reason` needs visible text (not `null`, `0`, or only invisibles). **Unlike every refusal above, these are self-repairable and are NOT escalation conditions** — fix and re-run.
     The command re-checks eligibility itself via `plan_lib.design_close_eligible` (design source cap reached AND global cap NOT reached AND breaker `clear`), refuses a corrupt counters file via `plan_lib.counters_are_intact`, cross-checks the breaker flag against the findings via `plan_lib.findings_are_unambiguous`, requires disposed Critical/High findings via `plan_lib.severe_findings_are_disposed`, builds the records via `plan_lib.budget_exhausted_close`, and writes the ledger all-or-nothing via `plan_lib.persist_close`; it exits non-zero writing nothing if the close is not permitted — never hand-write these artifacts and never call the helper directly to bypass the eligibility check. It emits one `adopted` ledger entry per applied finding, the TOP-LEVEL run-record `extra` row `{"label": "design_gate_close", ...}` (splice it into the Step-16 record's top-level `extra`, NOT into the gate row — a gate-row key validates silently and renders nothing), and the canonical session marker `### WF2 Step 4 — design gate CLOSED budget-exhausted (#<issue>: passes=N, <k> findings adopted, ledger <path>)`. Its top-level `adopted` means **adopted INTO THE CLOSE RECORD**, not implemented — the per-finding outcome is `finding.terminal_disposition`, and `adopted` (unlike `declined`) never auto-dissolves at the #393 join, so a refuted finding stays re-raisable. This is a legitimate close, NOT an ERROR — on an unattended run it never triggers the ERROR protocol. Continue to Step 5.
   - **If the adversarial review sub-step (item 7) is enabled and still in flight when this loop-back fires:** do NOT wait for it and do NOT run the ambiguity breaker (thresholds did not pass). **Discard the in-flight adversarial result as stale** — it reviewed a design that is now being revised (this is the documented one-wasted-call tradeoff) — and log `### WF2 Step 4 — Adversarial Review (#<issue>, discarded: superseded by volume loop-back)`. Return to Step 3; the next Step 4 pass dispatches a fresh adversarial review against the revised design.

6. **If thresholds pass:** Apply the ambiguity circuit breaker over the self-review findings — **unless** the adversarial review sub-step (item 7) is enabled for this run. When it is enabled, do NOT run the breaker here; **defer** it to the single merged-findings join barrier in item 7, so the breaker runs **exactly once** over the combined self-review + adversarial findings rather than twice. (The volume/loop-back checks in items 4–5 still run on the self-review findings as soon as the self-review returns; only the breaker is deferred.)

7. **Adversarial review sub-step (opt-in, cross-model — runs concurrently with the self-review).** Evaluate the two gate conditions UP FRONT, before running the self-review, so the cross-model adversarial review of the design document can be dispatched **concurrently with the self-review** rather than serially after it. Both review the same design document, so there is no ordering dependency, and overlapping them removes a serial round-trip from the critical path of every gated run. Gate it on BOTH conditions:
   - `fast_path_eligible == false` (skip cheap-path designs — this is additive to the self-review gate, never a replacement), AND
   - the active project opts in:
     ```bash
     python3 hooks/adversarial_review_lib.py is-enabled \
       --workspace .rawgentic_workspace.json --project <name> --skill implement-feature
     ```
     The command exits `0` when the review is enabled for this skill and `1` (or any non-zero) otherwise. If it exits non-zero, or `fast_path_eligible == true`, **skip silently** — behavior is byte-for-byte unchanged.
   When both gates pass, dispatch the adversarial review **in parallel with the self-review** (write the design doc to a temp file under the project first if it only exists in session notes): from a read-only harness subagent, run `python3 hooks/review_runner.py review-artifact --artifact <design-doc> --type design --author-model <your model id> --reviewer <default per the contract> [--backend <resolved backend>] --out <result.json> --project-root .` — dispatched TOKENLESS, so the result carries `diagnostic: true`. That is correct at THIS gate: the reopen authorization here is the fold's own `consume_loopback` machinery below (the same atomic budget the #855 token choke point debits), never the runner token. Under a `both` backend config, dispatch two runner invocations (one `--backend gpt`, one `--backend glm`, separate `--out` files) and merge their findings deterministically (dedupe key: evidence, location, category; on collision keep the higher severity, tie → higher confidence, tie → the gpt record). Apply the vacuous-result gate from `<model-routing-resolve>` to each result before consuming it; findings (including each finding's `loopback_class`, when present) are read from the result JSON's `findings` list. The review is **report-only** in its effects; bring its findings back into THIS gate at the **join barrier** described next. Resolve `<resolved backend>` first via `python3 hooks/adversarial_review_lib.py backend --workspace .rawgentic_workspace.json --project <name> --key adversarialReview` — exit 0 → stdout is the backend (`gpt`|`glm`|`both`; absent config → `gpt`); exit 2 (invalid config value) → abort this sub-step loudly, never default to gpt.
   - **Join barrier (single breaker):** once both the self-review and the review have returned, merge adversarial findings with the self-review findings into ONE list, tagging each with `source: self-review | adversarial`. Apply the ambiguity circuit breaker **exactly once** over the merged list — this IS the breaker deferred from item 6; never run a second, self-review-only breaker.
   - If the merged list contains one or more Critical/High findings, fold their
     `Loopback-class` tags via `plan_lib.classify_loopback_source(<classes>)` (#223) and
     consume **exactly one** loop-back from the source it returns, regardless of how many
     such findings there are. **Every Critical/High finding contributes exactly one
     Loopback-class entry to the fold; a finding without the field contributes 'untagged',
     which folds to the full design path.** Adversarial-review findings MAY carry a
     `loopback_class` field (engine ≥ 3.39.0, #407); each Critical/High adversarial
     finding contributes via `adversarial_review_lib.loopback_class_entries`: a
     `category: security` finding contributes `untagged` UNCONDITIONALLY (never the
     cheap path, regardless of its tag — model metadata alone must not route a security
     finding cheap); otherwise a vocab value contributes itself; absent/null/off-vocab
     contributes `untagged` (old engines, other sources — folds to the full design path,
     fully backward compatible). Self-review findings keep their existing Loopback-class
     contribution unchanged. The composition
     (`classify_loopback_source(loopback_class_entries(adversarial) + self_review_classes)`)
     is invoked by the orchestrator via `python3 -c`, the established gate-helper pattern.
     **Verifier-brief hardening (#407):** when a spec_tighten cheap pass was reached via
     ANY adversarial-sourced tag, the incremental verifier's brief must include the
     originating Critical/High findings — read from the runner result JSON's `findings`
     list (the canonical normalized report), never a re-derivation — and the verifier must
     confirm EACH is resolved by the applied amendment; any unresolved, omitted, or
     recategorized originating finding escalates to the full `design` path exactly like a
     new Critical/High finding.
     - Fold = `design` → consume `plan_lib.consume_loopback(<counters>, "design")` and
       return to Step 3 once with the unified constraint set (the pre-#223 behavior,
       unchanged). Do not consume per-finding and do not double-count against the
       self-review loop-back.
     - Fold = `spec_tighten` → the **spec-tightening cheap path** (#223): consume
       `plan_lib.consume_loopback(<counters>, "spec_tighten")` and do NOT return to
       Step 3. Apply each finding's Recommendation directly to the affected design-doc
       sections, then dispatch ONE incremental verifier as a read-only harness subagent
       (Agent tool, per `<model-routing-resolve>`) reviewing ONLY the changed
       sections (quote before/after). A spec-tightening loop-back dispatches exactly one verifier over only the changed design sections and never returns to Step 3. Verifier verdict:
       - clean (no new Critical/High) → gate PASSES; continue to Step 5.
       - ANY new Critical/High finding (either class), or any `ambiguous`/conflicting
         verifier finding → **escalate**: consume a `design` loop-back and return to
         Step 3. No chained cheap passes within one gate, and never silent-PASS an
         ambiguous verifier result. Escalation is two distinct consumes for two distinct
         passes (the amend+verify pass happened, then a full loop-back happens) — not a
         double-count of one event.
       - `spec_tighten` exhausted (per-source cap or global) → fall back to consuming
         `design` (full path) if it has budget; else the existing budget-exhausted
         STOP/ERROR protocol.
   - **Disposition-ledger fold (#393, pass N ≥ 2).** The runner carries no dispositions channel — since the M0 retreat the enforcement point is the orchestrator-side **join backstop**, not a prompt fence. At the join: (1) if the issue's canonical `claude_docs/.wf2-state/<issue>/dispositions.jsonl` is absent or empty → nothing to fold (pass 1). (2) Else fold it via `plan_lib.fold_dispositions` (last-write-wins by finding_key over `plan_lib.read_dispositions` output) and hold the folded view for the join backstop below — no temp file, no engine flag. (3) Apply the join backstop below over the returned findings vs the folded ledger. A ledger that fails to read (corrupt JSONL) is an owner-visible loud abort of the adversarial layer — marker `failed (ledger integrity)` — NEVER absorbed as a benign backend failure; a read that merely SKIPPED malformed lines is fail-open but stays visible — record `ledger: degraded (<n> lines skipped)` in the gate's marker tail.
   - **Join backstop (#393):** compute each returned finding's identity via `plan_lib.compute_finding_key` AFTER stripping any valid `REOPENS <id>:` prefix with `plan_lib.strip_reopens` (hashing the prefixed text would make the matched-entry validation unreachable). A finding whose key exactly matches a DECLINED or DISSOLVED ledger entry with no valid REOPENS is auto-dissolved as re-litigation (logged with the entry id, never silently dropped); a match against an ADOPTED entry is surfaced as `possible failed remediation` and NEVER auto-dissolved (an adopted-but-regressed fix must resurface). A REOPENS exemption is valid only when the referenced id exists, equals the matched entry's id, and non-empty delta text follows the colon. **Fuzzy candidate layer (#892):** post-join, `plan_lib.fuzzy_disposition_candidates(finding, folded_entries)` — same `location` + same `category` against a DECLINED/DISSOLVED entry, excluding the exact-key match and excluding ADOPTED entries entirely, surfaces `possible re-litigation of <id>` for adjudication. NEVER auto-dissolves the finding — only a byte-identical `finding_key` match keeps that power; recall widens ADVISORY-only.
   - **Gate-close persistence (#393):** at this gate's close, append each Critical/High finding's TERMINAL disposition (adopted | declined | dissolved) to the issue's `dispositions.jsonl` via `plan_lib.append_disposition` (identity fields + one-line reason + `decided_by`). Deferrals are NOT dispositions — an unresolved High stays in `deferrals.json` (the Step-11 re-presentation pipeline) and gets its ledger entry only at the gate close where it terminally resolves.
   - **Runner failure is non-blocking (the review is additive — the self-review gate already ran).** On ANY non-success from the runner (exit 2/3/4, a dead subagent, a vacuous or stale result — after the contract's one retry), do NOT trigger the ERROR protocol and do NOT block the workflow: skip the adversarial layer, log the failure loudly in session notes (and, in an unattended run, post a STATUS comment on the issue noting the review was skipped), and continue with the self-review result. **Because item 6 deferred the breaker when this sub-step is enabled, on any non-success you MUST still run the single ambiguity circuit breaker exactly once over the self-review-only findings before continuing — skipping the adversarial layer must not skip the breaker** (otherwise the breaker would run zero times). Never treat a failed external review as "passed", and never let its absence halt WF2. (Only the standalone `/rawgentic:adversarial-review` skill ERRORs on an unmet Codex prerequisite, because there the review is the entire task.)
   - **Concurrency tradeoff (accepted):** the review overlaps the self-review, so a design sent back to Step 3 may have spent one cross-model review call before the loop-back — a bounded, accepted cost per loop-back. Do NOT serialize to "save" the call; the latency win on the common path is worth the occasional wasted one.
   - **Pipeline while the wave runs:** per `<review-pipelining>` (SKILL.md), draft the Step 5 implementation plan (non-committing) while this wave and the self-review are in flight; the gate verdict still waits for the join barrier, and a loop-back or breaker outcome revises or discards the draft.
   - Log a marker: `### WF2 Step 4 — Adversarial Review (#<issue>, invoked|skipped): <report path or skip reason>`.

**Breaker decision — run the ambiguity circuit breaker EXACTLY ONCE (items 4–7, summarized).**
This table is authoritative for *which* findings the single breaker runs over — exactly one
row, never twice:

| Volume loop-back fired (item 5)? | Adversarial sub-step (item 7) state | Breaker runs over |
|---|---|---|
| **yes**, budget REMAINS | (any) | **SKIP** — return to Step 3 now; discard any in-flight adversarial result as stale (item 5). The breaker runs on the *next* Step 4 pass. |
| **yes**, budget EXHAUSTED (#798) | (any) | **RUNS — merged.** WAIT for an enabled adversarial review (never discard it), merge, run the breaker EXACTLY ONCE, then close-or-escalate. A terminal close has NO next pass, so deferring the breaker here would let an ambiguous finding close unescalated. |
| no | disabled / not opted-in / fast-path | **self-review-only** findings |
| no | enabled AND returned | **merged** self-review + adversarial (the join barrier, item 7) |
| no | enabled BUT non-success (not installed / timeout / error / parse error) | **self-review-only** findings — skipping the adversarial layer must NOT skip the breaker, **else it runs zero times** (item 7) |

The only path on which the breaker does not run is the volume-loop-back row **while budget
remains**, and that is because it returns to Step 3 *before* the breaker point — not because
the breaker was skipped. When that same row's budget is EXHAUSTED there is no next pass to
defer to, so the breaker RUNS before the close (#798).

**#223 fold note:** the Loopback-class fold runs **post-breaker only** — at the item-7
consumption point (and its self-review-only analog), after the single breaker has
completed. The volume row above never folds (item 5), so this table and the
breaker-exactly-once invariant are unchanged by the tiered loop-back.

**Lane note:** on the fast path the adversarial review sub-step (item 7) and the Step 3 peer
consult do NOT run — the self-review alone is the gate. The dimensions above are unchanged;
only the opt-in cross-model layers are dropped.

### Output
Amended design document.

### Failure Modes
- Zero findings from the self-review: verify it actually analyzed the design (not a rubber-stamp)
- Ambiguity circuit breaker triggers on >50% of findings: design may be underspecified

---

