# #314 — Lean orchestrator reads: deterministic projections + validated index readers

Issue: #314 (epic #309). Date: 2026-07-08. Status: FINAL v2 (synthesis of own draft +
Codex peer consult + deep-reasoner brainstorm, owner-approved shape; amended after the
Step-4 gate — opus self-review 1H/2M/2L + Codex adversarial-on-design 1H/5M, one design
loop-back consumed, all findings addressed in place).

## Provenance (AC6)

- Anthropic "plan big, execute small" cookbook — [managed_agents/CMA_plan_big_execute_small.ipynb](https://github.com/anthropics/claude-cookbooks/blob/main/managed_agents/CMA_plan_big_execute_small.ipynb): context isolation — the coordinator never touches token-heavy raw material; cheap workers read it in isolated contexts and return distilled digests. Link verified live (HTTP 200) 2026-07-08.
- Owner standing orchestration model (mempalace memory `feedback-orchestration-model`, 2026-07-02): "Fable = orchestrator … keep own context lean — delegate reading/execution rather than doing it inline."
- Motivating measurement: #303 run-record `usage.model_mix` — orchestrator model at 55.5M of 64.3M input tokens (~86%). The #315-shipped `worker-share` line surfaces this per run.

## Design inputs (this section is the review trail)

Three independent designs were produced blind and synthesized:
1. **Own draft** (approach "rule + file handoff + prose digest with verbatim quotes").
2. **Codex peer consult** (report: `docs/reviews/peer-rawgentic-peer-problem-314-2026-07-08.md`) — verdict *sound-with-changes*: evidence-indexed schema over prose sections; coverage counts; keep Step 8/9 inline (high-decision-density); count fallback events so fail-open can't mask dead delegation.
3. **Deep-reasoner brainstorm** — three architectures; found the **measurement trap** (`usage_capture.py:139` buckets by *model name*, so a same-model reader moves AC4 zero; the expensive thing is *holding* an artifact across turns — cache-read charged every turn, `usage_capture.py:104`); showed 4 of 6 surfaces reduce deterministically and need **no LLM reader at all**; byte-count trigger beats line count (minified one-liner gaming); coverage as **set-equality with the reader fed the known unit list**.

Convergent verdicts adopted: prose-summary digests are the decision-laundering surface → replaced by a structured index; Step 8/9 get no LLM reader (reading a failure to decide a fix IS the correctness decision — AC3); AC5 must be mechanical.

## The canonical rule (goes into steps.md verbatim, drift-guarded)

> A raw artifact whose measured size exceeds its surface's byte threshold never enters the
> orchestrator's context. A deterministic reduction (a runner summary, a gate field, a
> grep of failure lines) is read as a mechanical projection; a reduction that needs
> judgment is produced by an analysis-role reader subagent as a validated index. The
> reader returns material (an index), never a decision; design, plan, gate verdicts, and
> finding evaluation stay orchestrator-side, acting on raw bytes via targeted reads.

## The six surfaces — mechanism selected by determinism

| Surface | Mechanism | What the orchestrator consumes | Judgment kept inline |
|---|---|---|---|
| Step 8 inline test runs | **projection** — runner's own final summary (tail: counts + failing ids + first assertion lines), never `cat` the full log | bounded tail | the fix; RED/GREEN from exit codes |
| Step 9 suite gate | **projection** — same tail discipline | bounded tail + delta vs baseline | gate verdict |
| Step 11.5 security scan | **projection** — the `--json` output's `gate{blocking,advisory,errors}` + per-finding id/loc lines (already structured); full findings blob stays out of context | gate projection | fix/accept per finding |
| Step 13 CI failure | **projection** — grep of `--log-failed` to failing job/step + assertion/traceback first lines | bounded grep | the fix |
| Step 11 branch diff | **LLM index reader** (analysis role) | validated index (below) + targeted raw reads | finding evaluation (targeted `git diff -- <file>` / line-span reads), loop-back calls |
| Step 2 item 1 map | **LLM index reader** (analysis role) — repo-wide mapping moves into the reader's context; orchestrator gets the map | component map index | complexity classification, lane decision |

Projections are incapable of hallucination or verdict-smuggling — that removes AC5 risk
from 4/6 surfaces at zero agent cost. The two judgment surfaces get the index contract.

**Projection fail-closed rule (adversarial finding 2):** a projection is validated before
use — the producing command's exit status is captured; when the source reports failure the
projection must contain non-empty failure identifiers (failing job/step/test ids); an
empty, malformed, or command-failed projection falls back to the inline raw read, logged
and counted exactly like a rejected index. A silent empty grep never stands in for a
failing log. (Step 11.5's projection is the scan JSON's `gate{blocking,advisory,errors}`
+ per-finding id/loc lines — findings are already compact dicts, `security_scan.py:97-99`;
finding IDs are never dropped, only detail prose stays out of context.)

## Index contract (fail-closed, validated by `plan_lib.validate_index`)

```json
{
  "surface": "step11-diff | step2-map",
  "source_ref": "<origin/<default>..<HEAD sha> for step11-diff | <HEAD sha> for step2-map>",
  "entries": [
    {"locator": "<file[:hunk|:line-span]>", "component": "<step2-map only: component id>", "risk_tag": "<plan_lib risk vocab | none>", "one_line": "<= 120 chars, descriptive only"}
  ],
  "coverage": {"expected": ["<unit list the reader was FED>"], "indexed": ["<fed units the reader examined — for step2-map: NOT its discovered entries, which live in entries[] and may legitimately exceed the fed hint list>"]},
  "evidence": [
    {"file": "<path>", "line": N, "text": "<VERBATIM line — only for lines a decision keys on>"}
  ],
  "truncated": false
}
```

**The two surfaces are NOT symmetric (self-review finding 1 — the honest scoping):**
- **step11-diff** reduces a *known bounded artifact* with mechanical ground truth:
  coverage units are `git diff --name-only` output, set-equality there IS a completeness
  proof, `source_ref` staleness is exact. AC5's mechanical guarantee applies in full.
- **step2-map** is repo-wide *discovery* — there is no mechanical ground truth at Step 2
  (no diff exists yet). Its coverage check is a **drop-guard only** (the reader didn't
  silently omit a fed component — units are component ids; each entry carries both
  `component` and file `locator`; locator existence against the repo tree is checked at
  the CALL SITE by the orchestrator (the pure validator has no repo access), not by
  `validate_index`), never a completeness proof; `source_ref` is the HEAD sha (map staleness
  = HEAD moved). Completeness of the map remains the orchestrator's judgment, exactly as
  today (complexity classification, lane decision). AC5's "mechanical core" claim is
  scoped to step11-diff + the four projections; step2-map's guarantee is
  drop-guard + verbatim-evidence verification only.

Validation (all mechanical, all red-first tested):
1. **Closed schema** — unknown top-level or entry keys reject. There is no verdict/severity/patch/recommendation field; `one_line` is length-capped (≤120) and patch-shaped text (`^[+-]` hunk block) in `one_line`/`evidence.text` rejects. **AC3 honesty (self-review finding 2):** the schema blocks *structured* verdict-smuggling; a terse prose verdict still fits in 120 chars and no mechanical check can reject semantics. The real AC3 runtime guard is the next layer: **the orchestrator never acts on index prose — every decision is made from raw bytes via targeted reads** (drift-guarded contract sentence). The validator enforces what it can (schema, patch-shape, length); the re-read contract neutralizes the residual prose channel.
2. **Set-equality coverage** — the dispatcher FEEDS the reader the unit list (`git diff --name-only` output for step11-diff — a completeness proof there; component ids for step2-map — a drop-guard only, see the asymmetry note above). `set(coverage.indexed) == set(coverage.expected)`; step11-diff `entries[].locator` files ∈ expected, step2-map locators validated against the repo tree. Any miss rejects.
3. **Anti-hallucination** — every `evidence[].text` must `grep -F`-match its named file (or the artifact file); a fabricated quote rejects. Whitespace-drift false-rejects fall toward inline: acceptable by design.
4. **Staleness** — orchestrator re-derives `source_ref` before consuming (HEAD unchanged for a diff; recomputed sha for a file). Mismatch ⇒ regenerate or inline.
5. **Vacuous-return guard** — missing file, non-JSON, empty entries, or `truncated: true` rejects (a partial index is not accepted for judgment surfaces; repo mistake #9's `confirmedCount: 0` class lands here).
6. **Rejection ⇒ inline fallback, loudly** — the read falls back inline and the event is logged in session notes AND counted in the run-record (`follow_ups`/`extra`), so fail-open cannot silently mask dead delegation (Codex risk #3). Gates always run either way — delegation is fail-open for *how* material is read, never *whether* a gate executes.

## Delegation trigger

`wc -c` on the piped artifact (`git diff … | wc -c`) — bytes never enter context; byte
count is deterministic and un-gameable by line structure (a minified one-line artifact
defeats `wc -l`). Per-surface thresholds pick the number; bytes pull the trigger:
`WF2_READ_DELEGATE_BYTES_DIFF` (default 65536), `_LOG` (default 32768) — env-tunable,
clamped [4096, 10485760], frozen at import (the `WF2_HIGH_RISK_RATIO_*` pattern,
`plan_lib.py:104-106`). **For thresholded surfaces only, under threshold ⇒ inline exactly
as today** (adversarial finding 6's wording fix). Step 2 item 1 is exempt from byte
thresholds: it delegates whenever the existing Step-2 fan-out runs, and stays inline on
the existing trivial-change path (`steps.md:318` "for a trivially small change … run items
1–6 inline") — this design does not regress that deliberate optimization (self-review
finding 1). No hysteresis — the decision is per-artifact, both branches are correct, and
the marker logs measured bytes + threshold so near-boundary flips are visible.

## Why Step 11 nets positive (double-read audit)

The dominant cost is HOLDING the full diff across Step 11 items 1–8 — cache-read charged
every subsequent turn (`usage_capture.py:104`). The index removes the diff from every
turn; targeted verification reads at finding-evaluation are read late and briefly, not
held. The 3 review agents already self-fetch their diffs (`steps.md` reviewer contract) —
the reader replaces only the orchestrator's own copy. Net negative only when nearly every
hunk needs wide-context evaluation — rare, self-limiting (that finding volume trips the
Step-3 loop-back), and below-threshold diffs never delegate at all.

## AC4 measurement plan (the trap, named)

`usage.model_mix` buckets by MODEL — a same-model reader moves the metric zero, and
orchestrator-opus conflates with reviewer-opus. Therefore: AC4 is measured under
`analysis` routed to a cheaper model than the session (rawgentic already routes
`analysis: sonnet`), via the #315 `worker_token_share` line, and reported as a **floor**.
Local proxy now: this run's own record vs #303/#315. True before/after: the FIRST
post-plugin-reinstall WF2 run — recorded as `verification_deferred` with that exact
target check. Fallback-event and rejected-index counts ride the run-record so a "green"
delta can't hide savings lost to rereads (Codex point 10). **AC-traceability register
(self-review finding 4): AC4 is satisfied-as-floor + verification_deferred at merge time
— it is NOT demonstrated-at-merge, and the Step 11/16 gates must read it that way.**

## A/B experiment (owner acceptance bar, 2026-07-08)

The PR must carry an empirical A/B result proving, on ≥3 real merged-PR diffs of varied
size: **(1) the B-arm saves tokens, and (2) B-arm quality is within ±5% of A-arm.**

- **A-arm (control):** one subagent per artifact reads the FULL raw diff and answers a
  fixed evaluation questionnaire (files with behavior changes; public API/signature
  changes; test files added; the 3 riskiest hunks + why; security-relevant lines; a
  ≤4-sentence overall summary — six items, q1–q6).
- **B-arm (treatment):** sonnet index reader produces a `validate_index`-passing index;
  a consumer subagent answers the SAME questionnaire from the index + targeted raw reads
  only (never the full artifact).
- **Tokens:** per-ARM-RUN totals from each arm's own transcript/accounting (opus
  verifier advisory: the repo's usage tooling buckets by model, not per-subagent — the
  bar needs only per-arm totals, which each arm's separate run reports directly).
  B total (reader + consumer) must be < A total.
- **Quality scoring rubric (adversarial pass-2 finding — fully specified):**
  - q1 (behavior files) and q3 (test files): set-F1 against git ground truth
    (`--name-only` name sets; q1 truth = non-test, non-docs changed files by path
    heuristic), scaled ×10 → 0–10.
  - q2 (API changes), q4 (riskiest hunks), q5 (security-relevant), q6 (summary):
    scored 0–10 each by an INDEPENDENT blinded opus judge holding the raw diff; the two
    arms' answers are presented unlabeled in random order; anchors: 10 = complete +
    precise, 5 = partially correct or materially incomplete, 0 = wrong/fabricated.
  - Per-artifact score = mean of the six 0–10 items (equal weight). Pooled score =
    mean of per-artifact scores. Quality delta = (B_pooled − A_pooled) / A_pooled.
  - **Pass:** |delta| ≤ 5% pooled AND no single artifact with B more than 10% below A.
- **Adversarial live probes** (beyond unit tests): a tampered index with a fabricated
  quote, a coverage-dropped file, and a vacuous return must each be REJECTED by
  `validate_index` live.
- **Honest-failure clause:** if the B-arm misses either bar, the result is reported
  as-is on the issue and the design decision returns to the owner — a failed experiment
  is never shipped as success.
Results table rides the PR body, this doc (appendix added at Step 9), and an issue
comment.

## What is explicitly NOT delegated (AC1 stay-inline list)

Design (3), plan (5), all gate verdicts (4, 6, 9 Part A, 11 item 6 evaluation, 15),
branch/commit/PR mechanics (7, 12, 14), completion judgment (16); every
`implementation`- and `review`-role dispatch unchanged (`modelRouting` untouched — AC3).
Step 8/9 failure-diagnosis reads stay inline by design: deciding why a test failed is a
correctness decision; the projection only strips the log around it.

## File changes

1. `skills/implement-feature/references/steps.md` — new `### Delegated reads (#314)` contract subsection (rule, mechanism table, index schema pointer, trigger, fallback discipline, temp-file rules) + wiring edits at Steps 2 item 1, 8, 9, 11 item 1, 11.5, 13 with `<!-- model-routing: role=analysis -->` on the two reader surfaces. Step 11 item 1a pinned strings preserved verbatim (drift guards `test_wf2_clarity.py:183-225`).
2. `hooks/plan_lib.py` — `validate_index(index, expected_units, artifact_text=None)` beside `validate_build_receipt` (same pure/fail-closed pattern), `INDEX_SURFACES`, byte-threshold env freeze.
3. `tests/hooks/test_plan_lib_index.py` (new, red-first) — happy path; every reject branch: unknown key, verdict-key smuggle, patch-shaped text, >120-char one_line, coverage miss (dropped file), fabricated quote, truncated, empty/vacuous, malformed JSON; threshold clamp/env parse; adversarial "this should fail the gate" one_line.
4. `tests/test_wf2_clarity.py` — drift guards (self-review finding 3's corrections): canonical-rule sentence (ONE file, section-sliced), carve-out + raw-bytes re-read contract sentences, section-sliced `>=` PRESENCE checks for the new reader/projection prose (never a whole-file `count("role=analysis")` — two annotations already exist at steps.md:320 and :1091, and count guards break on any new occurrence, repo mistake #6). The step2-map reader reuses the EXISTING line-320 Step-2 fan-out annotation (Step 2 is not a net-new delegation surface) — only Step 11 item 1 gains a new `role=analysis` annotation. Positive AC3 check: `>=` presence assertions that the review/implementation `role=` annotations at steps.md:539/872/973/1155 survive. Item-1a pinned strings stay byte-identical.
5. `docs/workflow-diagram.html` — **REV** (per #224 precedent): stations 2, 8, 9, 11, 11.5, 13 sub-bullet deltas; snapshots regenerated.
6. `docs/run-records.md` — fallback/rejected-index count note + AC4 floor caveat.
7. README changelog + version ×3 (next: 3.24.22).
8. This doc + rendered `.html` (`render_artifact.py`), committed in the PR.

Temp artifacts (`.rawgentic-read-<issue>-<token>.*`): project root, 0600, stale-swept with
the existing 1a sweep, finally-cleaned, `.git/info/exclude`d.

## Error handling & failure modes

Reader death/timeout/vacuous ⇒ validate rejects ⇒ inline fallback, logged + counted.
Hallucinated quote ⇒ reject. Stale ⇒ source_ref mismatch ⇒ regenerate/inline. Threshold
misconfig ⇒ clamp + stderr + default. Session-limit mid-gate ⇒ gate re-runs per
resumption protocol; artifact + index on disk, revalidatable.

## Security implications

Temp artifacts may carry diff/scan content — 0600, project-root contained, git-excluded,
stale-swept (the 1a posture). Index/evidence text is data, not instructions (existing
workspace rule restated in the contract section). No new secret surface; scan projection
never drops finding IDs (only detail text stays out of context) so triage can't silently
lose a finding.

## Platform / external dependencies

(Adversarial finding 1: the original `none` was false — the design leans on shell/git
machinery. Every dependency below is proven by an existing in-repo call site.)

platform_apis:
- api: git diff / git diff --name-only against origin/<default>..HEAD
  feasibility: verified via existing-call-site — Step 11 item 1 and item 1a already run exactly these (steps.md:1116-1139); the 1a patch build is the proven pattern this design generalizes.
  failure: fail-loud
- api: wc -c on piped command output (bash)
  feasibility: verified via spike — `git diff origin/main~1..origin/main | wc -c` → 7034, run in this design session 2026-07-08 (adversarial pass-2 finding: generic coreutils usage was not exact-object-kind proof; this is the exact invocation shape).
  failure: fail-loud
- api: grep -F fixed-string matching for evidence verification
  feasibility: verified via spike — run 2026-07-08 this session: known line → rc=0 with match printed; fabricated string against the same file → rc=1. The exact hit/miss mechanic validate_index relies on.
  failure: fail-loud
- api: gh run view <id> --log-failed
  feasibility: verified via existing-call-site — Step 13 already directs exactly this invocation (steps.md:1419).
  failure: fail-loud
- api: env-var freeze with clamp at import (WF2_READ_DELEGATE_BYTES_*)
  feasibility: verified via existing-call-site — WF2_HIGH_RISK_RATIO_* implements the identical pattern (plan_lib.py:104-106).
  failure: fail-loud
- api: temp artifact files mode 0600 under project root + stale sweep + .git/info/exclude
  feasibility: verified via existing-call-site — Step 11 item 1a creates .rawgentic-diff-review-<issue>-<token>.patch with exactly this discipline (steps.md:1129-1153: 0600, stale sweep at step start, finally-clean, .git/info/exclude append). Implementation extends the 1a sweep glob list to .rawgentic-read-* and adds a test proving the new pattern is swept and git-ignored.
  failure: fail-silent
  surface: post-creation mechanical asserts, both fail-loud, run immediately after writing each temp artifact (adversarial pass-2 High — a silent chmod/exclude failure must surface at build #1): `stat -c %a <file>` must print 600, and `git check-ignore -q <file>` must exit 0 (spiked live 2026-07-08: check-ignore correctly reports the current sidecar as ignored). Either assert failing aborts the delegated read → inline fallback + loud log. The Step-11 stale-sweep marker additionally logs every swept file, and the new test asserts sweep coverage of the .rawgentic-read-* glob.

## Diagram decision

REV required — station-internal execution-model change on 6 stations; #224 precedent.
