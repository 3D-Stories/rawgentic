# #735 — executor instruction layer: design (2026-07-30)

**Issue:** #735 (epic #756) · **Author:** WF2 orchestrator, session 048888d0 · **Status:**
owner-ratified rev 4 (FINAL — gate closed). Fork decision ratified 2026-07-30 (ask-owner token
RG-065627, option 1: shape (a), both manual edits). Step-4 pass-1 findings + amended F7 policy
ratified (token RG-227616, option 1). Budget-exhausted escalation after pass 3 ratified (token
RG-662910, option 1: apply 5 fixes, proceed — no fourth pass). Rev 2 folded the 14 pass-1
findings; rev 3 the 12 pass-2 findings; rev 4 the 5 adopted pass-3 fixes (supervised-path retry
predicate — verified vs `supervisor.py:1067-1102`; Vercel explicit linkage; retry-loop ownership
vocabulary; rollout ordering; reviews-bullet scope). Declined pass-3 findings and reasons live in
the dispositions ledger + session notes.
Key rev-3 corrections: rev 2's "in-repo surfaces already agree" was FALSE (steps.md §8 still
carries pre-executor delegation prose — now in scope); the terminal-failure predicate is
re-grounded in the real Observation vocabulary; the receipt-without-observation defect is
tracked in **#766** (rev 2 mis-pointed it at #726).

## Problem, one paragraph

The executor architecture is the shipped default (#474) and its machinery is proven working
(build-seat spike: end-to-end, canary pass, worktree isolation — issue comment 5128276254), but
real workflow runs almost never dispatch it: the engine census over all 31 recorded runs shows
**zero** `build`-seat dispatches by a real run. Root cause (confirmed, D17–D19): two always-loaded
manuals — the workspace tier (`~/rawgentic/CLAUDE.md` §2) and the user tier (`~/.claude/CLAUDE.md`
standing authorization) — name the legacy Agent-tool subagent types (`rawgentic-implementer`,
`rawgentic-reviewer`, `deep-reasoner`) for exactly the work the executor routes to seats, while the
correct routing instruction lives only in a 1,889-line per-step reference file. The always-resident
instruction wins. Worse, the workspace manual's review-fallback clause names a substitution
(`rawgentic:rawgentic-reviewer`) that the architecture canary REFUSES (verified live:
`{"refused": "architecture_self_check"}`), so a session whose executor review seat dies is routed
into either a refusal or a violation (F7).

## The fork (owner decision required — asked via /ask-owner)

| Shape | What it is | Assessment |
|---|---|---|
| **(a) Rewrite the always-loaded manuals** — the §2 subagent + review bullets name the executor seats; Agent-tool types demoted to the declared legacy-rollback path; the impossible F7 fallback clause replaced with an executable policy | Removes the contradiction at its source. The rewritten bullets themselves carry a one-line routing rule, so the always-loaded tier states the RULE and defers mechanics to the skill | **Recommended.** One instruction, stated once at the widest tier where it is true (the tier-map's own placement rule). Smallest stable surface: one sentence per bullet |
| **(b) Promote the executor routing instruction into an always-loaded surface** (keep existing guidance, add the routing contract alongside) | Leaves the legacy-naming guidance in place; two competing always-loaded instructions instead of one resident vs one buried — the conflict this issue exists to remove, now at equal footing | Rejected as primary: duplication is how the copies drift (the workspace manual's own "Where a rule belongs" warns exactly this). Its useful essence — the rule being resident — is folded into (a) |

**Recommendation: (a).** (b)'s only advantage (residency) is achieved inside (a) because the
rewritten bullets ARE the always-loaded routing rule.

### Second decision inside the fork: the F7 fallback policy (rev 2 — gate-amended, owner-ratified)

"The executor review seat died — now what?" The rev-1 recommendation (substitute an executor
`review`-seat dispatch for a failed Codex review) was REFUTED by the Step-4 gate (self-review F2):
for Claude-authored work the cross-model-author rule leaves ONLY the codex pool eligible in the
review chain, so the substitute would re-dispatch the engine that just failed; an ad-hoc dispatch
also requires a declared run (`begin-run`). GLM is not installed on this host (verified:
`ModuleNotFoundError: zhipuai`). The ratified policy (RG-227616 option 1):

1. **Inside a WFn run — retry gated on PROVABLE death.** The ORCHESTRATOR owns the retry loop:
   one *workflow dispatch* may comprise up to two *executor attempts*; each attempt uses a
   distinct correlation id (`<original>`, `<original>-r1`) and produces its own receipt +
   observation, and the workflow dispatch emits ONE canonical `DISPATCH` line after the loop
   completes (retried-then-succeeded → `outcome=retried`, retried-and-failed → `outcome=error`) —
   the attempt-level executor call never emits a session-note DISPATCH line of its own.
   The retry predicate is path-specific, because death proof differs (rev-4 correction,
   verified at source):
   - **Non-mutating (synchronous) attempts:** retry permitted when an observation is present AND
     `parse_status != "ok"` (closed vocabulary, `contract.py:36-46`) — the sync adapter SIGKILLs
     the whole process group and reaps it BEFORE the observation is written
     (`adapters/base.py:91-99`), so a terminal observation proves death.
   - **Mutating (supervised) attempts — the build seat** (`executor_routing_lib.py:2487-2496`
     routes any mutating profile to the supervised branch): a timeout observation does NOT prove
     death — the supervisor writes one even when its kill was unverified
     (`supervisor.py:1067-1102`, state carries "timeout kill unverified: residue" / a
     `completed_with_residue` terminal). Retry is permitted ONLY on positive death evidence: the
     attempt's terminal state shows a VERIFIED kill (no residue quarantine, no
     `completed_with_residue`). Any residue/unverified-kill state, and any receipt with NO
     observation (the F6 state, tracked as **#766**), goes DIRECTLY to the **ERROR protocol** —
     no blind retry; two live mutating attempts is the worse failure.
   This binds until #733/#766 ship terminal-observation guarantees, at which point
   "indeterminate" shrinks to nothing and the branch stays correct.
2. **The general high-stakes-review bullet** (non-WFn, "Codex fails or stalls"): retry Codex once;
   if it still fails, **report the review as NOT OBTAINED** (blocker/ERROR) — never a same-engine
   substitute, never an unsanctioned harness agent, never silence. No cross-model substitute is
   configured today; if one is wanted later (e.g. the GLM backend: `pip install zhipuai` +
   `ZHIPUAI_API_KEY`), that is its own owner-approved issue.
3. Rejected: adversarial-review path as THE declared fallback (different mechanism — diff-lens
   refutation, not a general reviewer; fine as an additional layer, wrong as the substitute).
4. Rejected: per-run `defaultArchitecture: legacy` rollback as the fallback (a joint owner+operator
   config change by design — too heavy for a mid-run seat death, and exactly the "quiet downgrade"
   the architecture forbids).
5. Rejected (rev 2): executor review-seat substitution — refuted above; kept here so it is not
   re-proposed.

## The build seat's contract (AC1's required statement)

The `build` seat is the WF2 Step-8 / WF3 implementation dispatch surface for MUTATING work:

- **Requires** an authenticated #429 GateDecision (`--gate-file`) plus the live implementation plan
  (`--plan-file`); the CLI derives plan context internally — no caller-assembled context crosses
  the boundary (#464 §E).
- **Engine allowlist:** mutating dispatch is codex-only until an FS-sandboxed child ships
  (`MUTATING_FS_SANDBOXED = {codex}`); a mutating composition on any other engine is refused at
  the canary with exit 6.
- **Isolation:** per-dispatch git worktree + subprocess
  (`.rawgentic/runtime/worktrees/<run>/<seat>-<digest>/<attempt>/`). **The executor never splits a
  pane** — pane-per-phase was the owner's mental model, not the shipped design (settled by spike;
  whether pane visibility is wanted ON TOP is a separate design question, deliberately not this
  issue).
- **Bounds:** seat manifest `timeout_s: 3600` (the dispatch default since the F4 fix);
  `--author-provider` is REQUIRED (its absence is a hard `pre_check_denied`, not a default); the
  cross-model-author invariant applies (an author's engine never builds/reviews its own work where
  the table forbids it).
- **Evidence:** every attempt emits a receipt + observation in
  `.rawgentic/runs/<run-id>/routing-audit.jsonl` and every invocation one canonical `DISPATCH`
  line in session notes. **Caveat binding all guarantees above:** until #733 (killed seat reports
  ok:true) and #766 (killed wait leaves a receipt with no observation) ship, a DEAD dispatch can
  misreport — so every consumer of these guarantees applies the AC3-style acceptance contract
  (receipt+observation matched, `parse_status != ok` handling, attribution) rather than trusting
  a single field. Those two defects are tracked children of epic #756, out of scope here.

## Acceptance criteria → deliverables

| AC | Status | Deliverable |
|---|---|---|
| 1. Fork decision + build-seat contract | this doc + /ask-owner | Decision recorded here + in the epic decision log |
| 2. Surfaces agree | the core edit | Out-of-repo: rewrite `~/rawgentic/CLAUDE.md` §2 two bullets + amend `~/.claude/CLAUDE.md` standing-authorization line. In-repo (rev-3 correction — rev 2's "already agrees" was FALSE): `skills/implement-feature/references/steps.md` §8's modelRouting-delegation block (steps.md:981-1000) still prescribes Agent-tool subagent delegation and "inherit → runs inline", contradicting the executor rule. Fix: legacy-condition that block ("Under the LEGACY architecture (declared) …") and add the executor-primary sentence: "Under the executor architecture (the default), Step-8 plan tasks dispatch via the `build` seat per the `<model-routing-resolve>` seat table; full receipts-asserted adoption across every WF2/WF3 run is #762's acceptance surface." Add a section-sliced drift guard pinning the executor-primary sentence + asserting the delegation block is legacy-conditioned |
| 3. `executor:build` dispatches in a real WF2 run | THIS run | Step 8 dispatches the in-repo edit task through `--seat build`; the `^DISPATCH` line + receipt are the evidence |
| 4. Legacy fence | ALREADY MET | Struck per issue comment 5127226202 (canary refusal verified live); recorded, no work |
| 5. #472 carve-out pointer resolved | stale pointer | #472 CLOSED 2026-07-21 proving read-only seats only; its report never mentions the bake-off. Edit `shared/blocks/model-routing-resolve.md` (Step-3 seat-table row) to the exact replacement text below, then run `scripts/sync_shared_blocks.py`. Pointer target: **#765** (filed 2026-07-30) |

**Exact Step-3 row replacement text (the canonical sentence a drift guard pins):** the row's
notes-cell carve-out clause becomes:

> **AC1 carve-out, current status (#735):** the competitive wiring was never proven — #472 closed
> 2026-07-21 covering read-only seats only. Step 3 design generation stays on its current
> mechanism; whether to wire or retire the bake-off is decided in #765.

## File changes

**In-repo (the PR):**
1. `shared/blocks/model-routing-resolve.md` — the seat-mapping table's Step-3 row: replace the
   stale "proven in #472 … Until then" carve-out text with the current truth + live pointer
   (#765). Run `scripts/sync_shared_blocks.py` (synced copy lands in
   `skills/implement-feature/SKILL.md`).
1b. `skills/implement-feature/references/steps.md` §8 (rev-3 addition, self-review pass-2 F1):
   legacy-condition the modelRouting-delegation block (steps.md:981-1000 — "non-`inherit` model →
   subagent delegation", "inherit → Step 8 runs inline") so it reads as LEGACY-architecture
   prose, and add the executor-primary sentence: "Under the executor architecture (the default),
   Step-8 plan tasks dispatch via the `build` seat per the `<model-routing-resolve>` seat table;
   full receipts-asserted adoption across every WF2/WF3 run is #762's acceptance surface."
2. `tests/` — red-before-green drift guards: (a) pin the corrected carve-out sentence (anchor ONE
   canonical sentence, section-sliced per repo mistake #6); (b) pin the new §8 executor-primary
   sentence and assert the delegation block is legacy-conditioned; update any existing pin that
   holds the old sentences (`tests/test_model_routing_resolve_prose.py`,
   `tests/test_wf2_clarity.py`, `tests/test_bundled_agents.py` — grep at implementation).
3. `docs/planning/2026-07-30-735-executor-instruction-layer-design.md` + `.html` (this doc,
   rendered via `render_artifact.py`, deployed to Vercel per the standing mandate).
4. Version bump ×4 surfaces (3.109.5 → 3.109.6, patch — fix) + README changelog entry (diagram
   decision: no workflow-spine change → no diagram REV; the routing spine is untouched — this is
   instruction-surface text and a carve-out pointer).
5. PR body carries the EXACT final text of the out-of-repo manual edits (reviewability +
   re-applicability — CI cannot see those files).

**Out-of-repo (direct edits, alongside the PR, recorded in the epic decision log):**
6. `~/rawgentic/CLAUDE.md` §2 "Subagents and long-running work": replace the Sonnet/`deep-reasoner`
   implementation-subagent bullet with the executor routing rule (WFn per-phase model work → executor
   seats via the dispatch CLI; build-seat contract in one line; Agent-tool types = legacy rollback
   only; ad-hoc non-WFn dispatch judgment unchanged; never Haiku).
7. `~/rawgentic/CLAUDE.md` §2 "Reviews and second opinions": replace the impossible substitution
   with policy 1+2 above.
8. `~/.claude/CLAUDE.md` "Standing authorization: subagent dispatch": amend the sentence naming
   the legacy types so the standing grant covers "whatever dispatch mechanism the invoked skill
   mandates (executor seats today; the named Agent-tool types only under the declared legacy
   rollback)". The authorization itself stays — only the mechanism naming changes.

## Drift prevention (AC2's "no session can read one and obey the other", kept honest)

- In-repo prose stays CI-pinned (existing + new drift guards).
- The out-of-repo manuals CANNOT be CI-pinned (CI has no workspace). Mitigations, stated honestly:
  the rewritten rule is ONE sentence per surface (small stable target); this doc + the PR body
  carry the exact final text; the epic decision log records the edit date. Residual risk accepted:
  a future hand-edit of the manuals can re-diverge — that is the tier-map's standing discipline
  problem, not newly created here.

## Platform / external dependencies

platform_apis:
- api: `python3 hooks/executor_routing_lib.py dispatch --seat build --prompt-file <brief> --run-id wf2-735-048888d0 --correlation-id <id> --author-provider anthropic --gate-file <gate.json> --plan-file <impl-plan.md> --workspace <ws> --project rawgentic` (mutating, codex engine) on this repo's phase_executor runtime
  feasibility: verified via spike — REACHABILITY + gate-chain evidence, classified honestly: the #735 spike (issue comment 5128276254) dispatched the build seat end-to-end with the full precheck chain (gate-file, plan-file, plan-context equality, author-provider each refusing correctly before the passing run; canary codex_mutating pass on all three required checks; worktree isolation; receipt+observation written) — but its prompt was READ-ONLY, so the exact MUTATING invocation (edit → commit → collection/promotion) is NOT pre-proven. That is this design's likeliest-wrong claim, named per #226: Task 2 itself exercises it under the ratified retry+ERROR failure path, and the AC3 contract below is the check. This session's Step-4 dispatches exercised the same CLI on `--seat review` twice (parse_status ok, 443 s and 351 s — both past the old 300 s default)
  failure: fail-silent
  surface: AC3 acceptance contract — ALL must hold or AC3 is unmet and a logged ERROR is raised — (1) receipt AND observation both present in `.rawgentic/runs/wf2-735-048888d0/routing-audit.jsonl` with matching run/seat/correlation/attempt ids; (2) observation `parse_status: ok`, `timed_out` false, process exit 0, and NO residue/unverified-kill terminal state (an ok:true-while-killed record fails this); (3) attribution is executable: the attempt's worktree (`.rawgentic/runtime/worktrees/wf2-735-048888d0/build-*/<attempt>/`) contains the produced commit, its SHA is recorded in session notes at collection time, and the promoted tree's diff vs the pre-dispatch HEAD equals (4) the expected Task-2 edit exactly — the authoritative Task-2 file set and edit text live in the implementation plan (`claude_docs/.wf2-state/735/impl-plan-735.md`, Task 2). Never trust the dispatch exit code alone
- api: `vercel link --yes --scope <team> --project <name>` then `vercel deploy --yes --prod` (design-doc hosting) from a dedicated deploy dir holding the rendered page as index.html — rev-4 correction: the durable linkage is `.vercel/project.json` (org + project ids), NOT the directory name; an unlinked dir can mint a project under the DEFAULT scope, and the deploy still "succeeds", so wrong-target resolution is fail-silent
  feasibility: verified via existing-call-site — standing convention on this account, verified live 2026-07-24 (team 3d-stories; workspace manual "Vercel deploy specifics"), exercised repeatedly since (per-project hosted docs across this workspace)
  failure: fail-silent
  surface: BEFORE deploying, assert `.vercel/project.json` exists and records the intended team's org id + project; AFTER deploying, save the body and check both facts — `CB=$RANDOM; curl -s -w '%{http_code}' -o /tmp/vercel-check.html "<url>?cb=${CB}"` must print 200 AND `grep -F "<the doc's real <title> text>" /tmp/vercel-check.html` must match (a bare 200 with an age: header can be a stale CDN copy; the cache-buster must expand OUTSIDE single quotes)

Honest-classification note (build dispatch): dispatch ERRORS are loud (exit taxonomy 2/3/4/5/6),
but the known #733/#766 defects mean a KILLED seat can report ok:true or nothing at all — so the
call is treated as fail-silent until those children ship, and the acceptance contract above is
mandatory. The out-of-repo manual edits are applied INLINE by the orchestrator (plain file edits —
the build seat's worktree confinement cannot and must not reach them); the only executor-mediated
mutation is the in-repo Task-2 edit. Write access to both manuals is proven, not assumed: they are
same-user files (`rocky00717`), and this very session already writes sibling paths in both trees
(`.rawgentic_workspace.json` read-modify-write at bind; `claude_docs/` appends) — a failed write
would surface as a plain tool error and gates the merge per the read-back check.

## Security implications

Instruction-layer change; no new code paths. Net tightening: removes a documented path that
routed review work to unsanctioned agent types when the sanctioned one died.

## Error handling / failure modes

- **Rollout ordering (rev 4 — no split-brain window):** the sequence is fixed: (1) complete AC3
  (build dispatch, acceptance contract) and every in-repo commit; (2) open the reviewable PR;
  (3) ONLY THEN edit the two manuals, recording each file's ORIGINAL section slice in the epic
  decision log first (the restore source — the manuals are not in git); (4) read-back gate below;
  (5) merge. A failure at any point before (3) leaves both manuals untouched — the split-brain
  state this issue removes is never recreated mid-run. If either manual write or read-back fails,
  restore BOTH manuals from the recorded slices and raise ERROR.
- **Merge gate (manual read-back):** the PR is NOT merged and #735 is NOT closed until both
  manual files have been read back and matched — whitespace-normalized, section-sliced — against
  the checked-in replacement blocks, and the verification result is recorded in the epic decision
  log. Either comparison failing is an ERROR (blocker), not a follow-up task.
- **Session-transition honesty (AC2 scope):** the manuals load at session start; sessions already
  running when the edit lands keep the OLD text until they end. AC2 is satisfied for sessions
  started after the edit — acceptance evidence must come from such a session; existing sessions
  age out (single-operator workspace, hours not weeks).
- The bake-off follow-up issue exists: **#765** (filed 2026-07-30, before design finalization).
- `sync_shared_blocks.py` forgotten → CI `test_shared_block_drift.py` fails (designed catch).
- The build-seat dispatch (AC3) fails mid-run → the ratified retry policy (terminal-observation
  gated, new correlation id, one retry; indeterminate → ERROR direct). If the retry also fails,
  AC3 stays unmet and the issue gets an honest blocker comment rather than a hand-edit
  masquerading as a seat dispatch.

## Peer consult synthesis (gpt backend, blind both ways)

The cross-model peer proposal (Codex/gpt, produced blind to this draft) converged on the core:
shape (a) hybrid, build-seat contract stated first, retry-once-at-seat-bound-then-ERROR, legacy
only as an explicit clean-boundary rollback, never a mid-dispatch downgrade. Adopted from the
peer (provenance: gpt consult, 2026-07-30):

1. **Verbatim replacement blocks checked into the repo** (below) rather than PR-body-only — the
   in-git copy is the durable re-application source for the out-of-repo manuals.
2. **Confirm termination before retry** — sharpened by the Step-4 gate into the ratified policy:
   retry only on a TERMINAL failure observation; indeterminate liveness (receipt, no observation)
   goes directly to ERROR, and each attempt keeps its own receipt (no obscured first attempt).
3. **Concrete pointer, not an indefinite one** for the carve-out (peer's criticism of "another
   indefinite pointer" accepted): the re-point targets a real filed issue.
4. **Reachability ≠ adoption**, stated honestly: this run's single `executor:build` receipt proves
   the instruction path is executable; sustained adoption is #762's acceptance surface (receipts
   census), not this issue's.

Rejected from the peer, with reasons: wiring the Step-3 bake-off now (real engineering with its
own competitive contract — belongs with #762's seat-wiring scope or the filed follow-up, and AC5
explicitly allows re-point-with-reason); a second "routing charter" copy near the top of SKILL.md
(SKILL.md's `<model-routing-resolve>` already carries the contract — a second copy is the drift
the peer itself lists as its top risk); a new instruction-census test (the existing
`test_model_routing_resolve_prose.py::test_agent_tool_dispatch_instructions_are_legacy_conditioned`
already does this for the corpus — extend only if a gap is found at implementation).

## Manual amendment — verbatim replacement blocks (the out-of-repo edits)

**`~/rawgentic/CLAUDE.md` §2 "Subagents and long-running work", bullet 1 (standing authorization
pointer), REPLACE with:**

> - The user tier's standing dispatch authorization (owner decision 2026-07-27) covers, for the
>   rawgentic WFn workflows, exactly the executor CLI at `python3 hooks/executor_routing_lib.py
>   dispatch`, limited to the `analysis`, `review`, and `build` seats. Any new mechanism or seat
>   requires a new owner authorization before use. Invoking the workflow IS the request — never
>   re-ask permission, and never silently substitute inline work for a review a workflow step
>   mandates (mistake #8; WF2 Step 11 is the canonical example).

**Same section, bullet 2 (Sonnet/`deep-reasoner` guidance), REPLACE with:**

> - **WFn per-phase model work routes through the executor seats the invoked skill names, never
>   Agent-tool subagent types** (#735). The `build` seat serves WF2 implementation dispatches
>   today (WF3 build adoption and broader project wiring: #762) — it requires the authenticated
>   gate + plan files, mutating work is codex-only by allowlist, and it isolates in a per-dispatch
>   git worktree (the executor never splits a pane).
>   `rawgentic:rawgentic-implementer` / `rawgentic:rawgentic-reviewer` are the declared legacy
>   rollback ONLY (`defaultArchitecture: legacy`; the canary refuses them otherwise), and harness
>   agents (`deep-reasoner`, `Explore`) are for ad-hoc non-WFn work. A failed executor seat
>   attempt mid-run: the orchestrator retries ONCE (new correlation id, own receipt; one DISPATCH
>   line for the whole dispatch) ONLY on proven death — for a mutating build attempt that means a
>   VERIFIED kill; residue, an unverified kill, or a receipt with no observation is NOT death and
>   goes directly to the ERROR protocol — no blind retry, never a downgrade. Ad-hoc (non-WFn)
>   subagent judgment unchanged: Sonnet default, Opus for genuinely hard reasoning, never Haiku.

**Same file, §2 "Reviews and second opinions", the fallback sentence — REPLACE
"if Codex fails or stalls, substitute an independent Opus reviewer subagent
(`rawgentic:rawgentic-reviewer`) — an established, accepted substitution." WITH:**

> For an ad-hoc non-WFn high-stakes review: if Codex fails or stalls, retry it once; if it still
> fails, report the review as NOT OBTAINED (a blocker, not a silent skip) — an honestly missing
> review beats a fake substitute. Inside a WFn run, the workflow's terminal-observation-gated
> retry policy controls instead (retry only on proven death; indeterminate → ERROR).
> (`rawgentic:rawgentic-reviewer` is NOT a substitute: the architecture canary refuses it outside
> the declared legacy rollback — #735 F7. An executor `review`-seat dispatch is NOT one either:
> for Claude-authored work the cross-model rule leaves only the just-failed Codex pool eligible.
> No cross-model substitute is configured today; adding one — e.g. the GLM backend — is its own
> owner-approved change.)

**`~/.claude/CLAUDE.md` "Standing authorization: subagent dispatch", first bolded bullet —
APPEND after "Do NOT re-ask for permission, and do NOT silently substitute inline work for a
review agent a workflow step mandates — workflow-specific step rules live in the bound
workspace/project manuals.":**

> For rawgentic WFn workflows the grant covers exactly the executor CLI at
> `python3 hooks/executor_routing_lib.py dispatch`, limited to the `analysis`, `review`, and
> `build` seats; any new mechanism or seat requires a new owner authorization before use. The
> Agent-tool types stay pre-authorized only where a definition genuinely calls for them (the
> declared legacy rollback, or non-WFn skills that name them). Non-WFn authorizations in this
> block are unchanged.

## Multi-PR assessment

No — well under 500 lines, one coherent phase, single PR.
