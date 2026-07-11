# WF14 Run-Feedback — WF2 #167 (saystory) — 2026-07-10

Rubric: v1 (2026-07-09, #337). Mode: **degraded (record: absent — run in-flight, Step 16
not reached; NOT store-lag)**. Scores are **provisional** — the run is live at Step 4
pass 2; only reached obligations (Steps 1–4) are assessed. Plugin version loaded by the
run: **3.32.2** (cache path quoted in transcript) = current main (3.32.2, commit
`99ccb91`) — defect verification is current, not stale-cache noise.

Assessment trigger: owner-reported display failure, RCA'd this session. Deep-reasoning
pass: cross-model consult, Codex **gpt-5.6-sol** (owner directive), report
`docs/reviews/peer-wf14-167-consult-problem-unknown-date.md` — its corrections
(fidelity 3→2, F1/F4 reclassified, telemetry unscored) are adopted below.

## At a glance

**A high-value design gate did its job (11 findings, 5 High, REV2 forced) — and then its
circuit breaker asked the user to resolve findings the user could never see, because the
presentation lived only in invisible extended thinking under output-compression plugins.**

- Fidelity **2/5 (provisional)** — markers complete for Steps 1–4, but the breaker's
  mandatory "Present ALL problematic findings to the user" was materially skipped
  (user answered blind).
- Gates **4/5** — real catches: 11 design findings pre-implementation, design loop-back
  consumed, REV2 produced; pass-2 disposition still open (in-flight).
- Clarity **3/5** — executable, but SKILL.md:262 "Present ALL problematic findings to
  the user" is under-operationalized: no channel, no ordering constraint, no precedence
  over response-style layers.
- Dispatch **3/5** — opus quality-bar reviewer non-vacuous first try; Codex adversarial
  launch died on an invalid `--out` flag (orchestrator misuse), visibly relaunched.
- Telemetry **unscored** — no record obligation has matured (Step 16 unreached);
  scoring would manufacture a failure for an inapplicable dimension.
- Cost **3/5 (cap: usage session-level only)** — heaviest-least-value: the
  dead-on-arrival Codex launch; 103.7k-token opus design review proportionate for a
  5-High design but unproven at the margin.

**Best catch:** Step 4 pass 1 — 11 design findings (5 High) against the #167 design note
before any implementation; loop-back consumed, REV2 written.
**Worst friction:** `<ambiguity-circuit-breaker>` (SKILL.md v3.32.2:262) — "Present ALL
problematic findings to the user" satisfied, in practice, by invisible thinking + terse
AskUserQuestion option labels.
**Routed:** 2 defect/hardening issues filed (see Routing); no plugin-friction finding
survived consult (F4 reclassified orchestrator-error); 1 environment-hazard memory saved.

## Evidence ledger

| # | Claim | Tag | Evidence |
|---|-------|-----|----------|
| E1 | Run = WF2 #167 saystory, plugin 3.32.2 | confirmed | transcript `e43f6341` skill base path `cache/rawgentic/rawgentic/3.32.2/skills/implement-feature`; session_notes.md L883 `## WF2 #167 run (2026-07-10, m1-m5 overnight driver, M2 child)` |
| E2 | Step markers 1, 1b(deferred), 2, 3, 4×2 present | confirmed | session_notes.md L884 `### WF2 Step 1: Receive Issue — DONE (#167: …)`, L885 (1b deferred: run-level /goal active), L895 (Step 2), L901 (Step 3), L911 + L920 (Step 4 adversarial, pass 1 + pass 2) |
| E3 | Circuit breaker fired; 11 findings (5 High); user asked to apply resolutions | confirmed | transcript L463 AskUserQuestion input: "Circuit breaker: 11 design findings on #167 (5 High). Apply all proposed resolutions above…" |
| E4 | Findings NEVER emitted as visible text | confirmed | transcript L460→463: tool_result → thinking ×2 → AskUserQuestion, zero text blocks; turn output 3702 tokens (~3.3k thinking); all 33 session text blocks are one-liners (enumerated) |
| E5 | Session ran under caveman + ponytail compression mandates | confirmed | SessionStart hook outputs in transcript ("CAVEMAN MODE ACTIVE", "PONYTAIL MODE ACTIVE") |
| E6 | Run-record for #167 absent | confirmed + expected | saystory store latest = #166; run in-flight at Step 4 pass 2 (resumed-session tail: "Resuming WF2 #167 at Step 4 pass-2") |
| E7 | Hook-timeout non-enforcement (cross-session) | confirmed | session `6d866952` entry 2683-2684: mempalace `user-prompt-submit` durationMs 11848 vs timeoutMs 5000; rawgentic `wal-context` durationMs 11849 vs timeoutMs 3000; both `timedOut: false`, cancelled only by user interrupt; Claude Code 2.1.206 |
| E8 | Hook stall transient | confirmed (non-repro) | live re-test ×3 each: mempalace 34-35ms, wal-context 40-42ms, healthz 30ms |
| E9 | Stall cause = host load spike | inferred | would confirm: host metrics at 16:38; both hooks identical duration suggests shared cause; /reload-plugins ran 3 min prior |
| E10 | Codex review dead launch (`--out` on `review`) | confirmed | transcript L454 text + L456 tool_result usage error; relaunched successfully L459-460 |

## Findings and classification

**F1 — Circuit-breaker findings presented invisibly.** Class: **ORCHESTRATOR ERROR**
(consult-corrected: "to the user" already excludes invisible reasoning — the channel
ambiguity does not excuse the conduct), **with prose-invited component** → plugin
feedback per rubric: the inviting sentence is SKILL.md:262 "Present ALL problematic
findings to the user" — no required visible artifact, no ordering constraint, no
precedence over active response-style instructions. Filed as hardening (issue 1).
Consult risk notes adopted: a prose-only fix may fail under future compression layers —
the issue asks for a machine-checkable precondition where feasible; a linked report
alone can still leave the user blind unless severity/counts are visibly summarized.

**F2 — Whole-run narration compressed to one-liners.** Class: **ENVIRONMENT**
(third-party caveman/ponytail plugins). Not independently filed; folded into issue 1's
precedence clause. Runbook gap noted → memory (rubric: environment findings still get a
runbook note when the plugin lacks one).

**F3 — Hook timeout non-enforcement + wal-context lacking internal deadline.** Split:
harness non-enforcement (E7) = **ENVIRONMENT** (Claude Code, not fileable here);
wal-context having no self-imposed deadline while the harness's is proven unreliable =
**resilience enhancement** (consult: "defense-in-depth, not a demonstrated plugin
defect" — no rawgentic-side hang reproduced, E8). Filed as issue 2 with consult's
sharpened shape: internal monotonic deadline, subprocess-tree termination, safe degraded
result, timeout telemetry.

**F4 — Dead Codex launch (`--out` misuse).** Class: **ORCHESTRATOR ERROR**
(consult-corrected from friction: one mistaken invocation does not demonstrate the
sibling CLIs are systematically misleading). Watch-flag: reclassify to friction if the
misuse recurs across runs.

## Telemetry audit (degraded)

Record absent and **not yet due** (Step 16 unreached; distinct from store-lag-known and
from a genuinely missing record of a completed run). Per degraded-mode rules, no
telemetry-accuracy claims are made. Claims intentionally NOT made: record correctness,
cost accuracy, dispatch completeness, store-append behavior, gate-count honesty,
reviewer_kind fidelity. All audited fields: `unverifiable` (not-yet-due). No
telemetry-lane issues.

## Routing

1. Issue 1 (hardening, `enhancement,framework,wf-feedback`): make ambiguity-gate
   findings visibly inspectable before resolution — see filed issue for full shape.
2. Issue 2 (resilience, `enhancement,framework,wf-feedback`): bound wal-context
   execution with an internal monotonic deadline independent of harness enforcement.
3. Memory (environment hazard runbook note): output-compression plugins + invisible
   thinking can swallow gate presentations; harness hook timeouts unreliable on
   2.1.206.
4. Not filed: F2 (environment, folded), F4 (orchestrator error, watch-flag). Cap not
   reached (2/3).

Cross-repo note (outside WF14 scope, owner-approved separately): the mempalace
`user-prompt-submit` hook's internal budget (healthz 2s + search max-time 8s = 10s
worst case) exceeds its declared 5s hook timeout — routed to the
rawgentic-memorypalace repo directly.
