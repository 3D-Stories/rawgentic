**Written** 2026-08-01 by the epic #756 auto-run (session `811192ca`). **Purpose:** five children
of epic #756 are blocked on owner decisions. Each decision rests on a spike or a design
investigation. This page puts the evidence in one place so the rulings can be made from data
rather than from a summary of a summary.

**Read this first — the honesty rules for this page.** Every load-bearing claim below is marked
**CONFIRMED** (evidence named: file:line, command output, or a measurement table) or **INFERRED**
(with what would confirm it). Where a spike produced a wrong answer before it produced the right
one, that sequence is recorded — in two cases it is the most important finding on the page.
Nothing here has been smoothed for readability at the cost of a caveat.


## 0. The five decisions, at a glance

| # | Issue | The decision | Recommendation | If you say nothing |
|---|---|---|---|---|
| 1 | **#794** | Keep the executor architecture, or retire it for orchestrator-with-subagents? | **Keep it; re-run the spike properly, then pursue per-run session reuse** | #795 and #799 stay blocked indefinitely |
| 2 | **#792** | Three scope amendments on the quota guard (receipt wording, per-pool scope, ship-half-now) | **Amend AC1 + AC2; ship the Claude half now** | A designed, thrice-reviewed guard stays unbuilt |
| 3 | **#797** | Context thresholds 55/75 as instructed — even though the maths says they fire later? | **Fix the meter's overshoot first, then retune** | The meter keeps firing late at its current 50 |
| 4 | **#777** | Per-phase token telemetry cannot be built on today's data. Park, grow, or narrow? | **Park it; half is already shipped** | An unbuildable issue sits in the queue looking live |
| 5 | **#796** | Eight mechanical verbs: three design passes failed. Split it? | **Split — ship candidate 3 alone, re-file candidate 1** | A converged, shippable sub-fix stays unshipped |

**#794 is the highest-leverage single ruling on this page**: it alone unblocks #795 and #799, and it
decides whether the executor work already merged (#735, #767, #765, #762) has a future.


## 1. Spike S3 — can byte-identical seat briefs hit the prompt cache? (#794)

**This is the architecture gate.** The epic's own AUTO MODE contract says: "#794 IS A HARD GATE.
Its AC1 is spike S3 (~15 min): can byte-identical briefs hit cache? If NO — STOP, ask the owner,
and do NOT start #799 or #795." Your framing on the issue was blunter: "if we cant get cache
working, we may need to abandon executor path and go back to orchastrator with sub-agents."

### Purpose

Every executor dispatch is a fresh CLI session (`session_policy: "fresh"`). Turn 1 writes the whole
seat prompt to cache — **observed 46k–132k tokens at 1.25–2× price** — and every subsequent loop
turn re-reads the conversation from cache (observed 1.5M–3.1M cumulative reads per dispatch). Reads
are cheap in dollars (~0.1×) but they count against the 5-hour throughput window. If a second
dispatch of the same brief could reuse the first one's cached prefix, that write cost disappears.

**Why it might work at all (CONFIRMED mechanism):** Anthropic's prompt cache is org-scoped and keyed
on exact prompt-prefix **bytes** + model — not on session identity. So cross-session reuse is
possible in principle.

### Method

Five dispatches of one identical brief, seat `analysis`, Claude lane, `claude-opus-5`,
`session_policy: fresh`, run id `wf2-794-cf9ff806`. All five shared an identical `prompt_hash`,
model, lane and `parse_status: ok`. Read `cache_creation_input_tokens` (write) and
`cache_read_input_tokens` (read) per dispatch.

### Results (CONFIRMED — `docs/measurements/2026-08-01-794-spike-s3-cache-reuse.md`)

| dispatch | wall time | `cache_write` | `cache_read` |
|---|---|---|---|
| 1 (cold) | 01:45:11 | 49,106 | 17,300 |
| 2 | 01:45:18 | 46,825 | 21,393 |
| 3 | 01:45:57 | **0** | 66,406 |
| 4 | 01:54:28 | 50,941 | 17,300 |
| 5 | 01:54:38 | 45,013 | 21,416 |

**Four of five paid a full ~45–51k prefix write. One paid zero, and it did not repeat.**

### Verdict: INCONCLUSIVE — and that is a finding, not a dodge

The spike **cannot** answer the gate question. Three defects in the experiment, all caught at
Step-11 review rather than by the author:

1. **The dispatches were not back-to-back.** D1–D3 ran within 46 s; **D4 came 8 m 31 s later**. An
   earlier draft of the measurement doc asserted "five dispatches back-to-back over ~40 s" — that
   was **false**. Read with the real timing, this is two bursts: `write, write, HIT` then
   `write, write, (no third)`. That is consistent with a warmup hypothesis, not a refutation of one.
2. **The provider inputs were NOT identical, even though the brief was.** `prompt_hash` covers only
   `req.prompt`; Anthropic's cached prefix spans tools + system + messages. Total provider input
   varied (~66.4k vs ~68.2k on alternating dispatches). **A byte-identical dispatch was never
   actually achieved at the layer that matters.** The stable 17,300 / ~21,4xx alternation in
   `cache_read` is that variation showing through — it is Claude Code's own system prompt being read
   from the org cache, independent of the brief.
3. **Every write was the 1-hour tier, not the 5-minute tier.** Raw transport shows
   `ephemeral_1h_input_tokens` carrying the whole write on every dispatch, `ephemeral_5m_input_tokens: 0`
   throughout. An earlier draft, the adapter comment, and `phase_executor/README.md` all reasoned
   from a 5-minute TTL. **That reasoning was wrong** — and with a 1-hour TTL, an 8.5-minute gap
   should not have expired anything, so TTL does not explain D4's miss either.

### The process failure, recorded deliberately

**This spike produced four successive wrong answers before review stopped it.** n=1 read as "cache
works". n=2 as "cache fails". n=3 as "works after a two-dispatch warmup" — and that verdict reached
a commit and would have unblocked #795 and #799 on a single anomalous data point. n=5 read as "does
not work", which was also over-claimed.

The root cause of the first three was mechanical and is worth knowing: `adapters/claude_cli.py`
mapped `cached` to `cache_read_input_tokens` and **discarded `cache_creation_input_tokens`**, with
the observation schema `additionalProperties: false` so nothing downstream could add it. **#794's own
AC2 — a measured before/after on `cache_creation_input_tokens` — was unmeasurable against shipped
code.** PR #811 (merged, v3.115.0) landed that telemetry and deliberately did not ship a verdict.

### What a controlled re-run needs

- Fixed inter-dispatch interval, recorded, and varied deliberately (10 s / 2 min / 10 min).
- ≥3 dispatches per burst and ≥3 bursts, so warmup is distinguishable from noise.
- The **full provider input** captured per dispatch, not just `prompt_hash` — the prefix that matters
  includes tools and system, which the current hash does not cover.
- Tier-split writes recorded (`ephemeral_5m` vs `ephemeral_1h`), now known to differ.
- A `session_policy: "resume"` arm — because lever 2 does not depend on winning a byte-exact match.

### Recommendation — do NOT retire the executor

**CONFIRMED:** the mechanism can hit (D3 achieved `cache_write: 0`). **INFERRED, with a named cause:**
the misses come from Claude Code injecting per-session bytes early in the prompt — the scratchpad
path containing the session id, and volatile git status. **The brief is the tail of the prompt, not
the prefix**, so making the brief byte-identical cannot rescue a mismatched prefix. That would be
confirmed by the controlled re-run above capturing full provider input.

This argues for **lever 2 (per-run seat session reuse — resume instead of `fresh` for later
dispatches of the same seat within a run)** over lever 1 (prefix stabilization), because lever 2 does
not require winning a byte-exact prefix match at all.

Retiring the executor on this evidence would be discarding working, gated, audited machinery
(see §2) because of an experiment that was not controlled well enough to indict it.


## 2. Spike #735 — is the executor `build` seat actually wired, and does it spawn panes?

### Purpose

Three open questions with large blast radius: had the `build` seat ever run; what did the executor
epics actually ship; and does the executor spawn a herdr pane per phase, as the owner's mental model
assumed. The concern, verbatim from the issue: "if the shipped design never spawns panes, then
several epics bought something other than what was asked for."

### Method

Dispatch the `build` seat for real — one trivial read-only prompt from a scratch worktree — rather
than reasoning about it. Then make the agent report its own `cwd`. Then re-count build receipts at
the **engine** level (`.rawgentic/runs/*/routing-audit.jsonl`) rather than from skill session notes.

### Results (all CONFIRMED)

**1. `executor:build` works end to end.**

```
state           : completed          resolution   : primary
canary verdict  : pass  (policy_id codex_mutating, profile mutating)
                  required+passed: codex_containment, codex_behavioral, bare_absent
requested/actual: gpt-5.6-terra / gpt-5.6-terra
parse_status    : ok    exit_code: 0    timed_out: false
timing_ms       : 17811
usage           : input 36615 (cached 27136) / output 215
receipt         : seat=build role=build verdict=pass gate_outcome=single
observation     : written
```

It required and correctly enforced the full `#464 §E` gate chain — `--gate-file` (an authenticated
`#429` GateDecision), `--plan-file`, and exact-key-set plan-context equality on
`{complexity, file_count, lines, risk_level}`. Each precheck refused correctly before minting a
receipt. **The machinery is complete, gated, canary-checked, and fast (17.8 s).**

**2. The executor isolates by per-dispatch git worktree + subprocess — it never splits a pane.**

```
.rawgentic/runtime/worktrees/spike-735-build-ca5113e1/build-44575cf5/0-3a1fb27b-8d02e0a2
```

So the owner's mental model ("the main pane driving, a herdr pane per phase") and the shipped design
**genuinely diverge** — but the shipped design is not empty. It bought worktree-level isolation plus a
receipt/observation audit trail, rather than pane-level visibility. Whether pane-per-phase is also
wanted is a design question, not a bug, and deserves an explicit decision rather than being
rediscovered a third time.

**3. The zero was a CALLER problem.** Engine-level census over all 31 recorded runs:

| runs with a `build` receipt | which |
|---|---|
| `559-cell1` (1), `559-cell1b` (2) | #559 development test cells, not real workflow runs |
| `spike-735-build` (2) | this spike |
| **every real `wf2-` / `wf3-` run** | **0** |

For contrast, **22 of 31** runs carry a `review` receipt. So the engine has executed a build seat
exactly three times in its history, and every one was a developer proving the engine — never a
workflow doing work. Root cause: the always-loaded manuals named `rawgentic-implementer` /
`deep-reasoner` / Sonnet, and nothing resident in context ever named the `build` seat. **Entirely an
instruction-layer problem, not an execution-layer one** — a materially easier problem than filed.

**4. The timeout default was strangling every review (F4).** `--timeout` defaulted to a flat
**300 s** while `engine.py:86-93` `_effective_timeout` is `min(caller, bound)` — so the flat default
was the binding constraint. The `review` seat declares `bounds.timeout_s: 1800`; `build` declares
**3600**. A caller who did not hand-tune got **1/6 of review's sanctioned budget, 1/12 of build's**.

| real Step-11 review | wall time | would the 300 s default have killed it? |
|---|---|---|
| #719 | 788 s | yes |
| #720 | 399.7 s | yes |

Two of two. Fixed in `646bd97` (PR #753, v3.109.3) — now defaults to the seat's own bound.

### Recommendation

This spike is the strongest argument against retiring the executor: the engine is proven, gated,
and fast; its problem was that nothing called it, which #762 has since addressed. Feed this into the
#794 ruling.


## 3. #792 — the 5-hour-window dispatch guard: three decisions

### Purpose

Nothing stops the executor dispatching Claude-lane work when the 5-hour window is nearly exhausted.
In #762, budget-exhaust chains wasted whole attempts (**~$12.6 on one Step-2**) and two dispatches
died on org-level 429s. Owner directive (note 7): "build something that looks at the 5-hour window
usage and stop sending calls after a certain limit, say 90%."

### Method and status

Three design passes, each reviewed by two independent cross-model reviewers (`gpt-5.6-sol` via the
executor `review` seat, plus adversarial-on-design). Every load-bearing finding was verified against
source before acceptance. **Design loop-back budget is now exhausted** (`{design: 2, total: 2}`), and
two pass-3 findings are flagged `ambiguous`, which independently trips the ambiguity circuit breaker.

| Pass | Verdict | Findings |
|---|---|---|
| 1 | FAIL | 17 merged (1 Critical, 6 High) |
| 2 | FAIL | 1 Critical + 6 High |
| 3 | NOT SHIPPABLE | 3 High + 4 Medium |

**The gates earned their cost** — real defects caught that would otherwise have shipped:

- Rev 1's installer wrote a cache record its own reader would have rejected every time — the guard
  would have been **permanently inactive while reading as shipped**.
- Rev 2's audit record used a new `kind`, but `enforce.py:_validate_record` raises on any unknown
  kind — it would have **poisoned `records()` and reconciliation for the entire run**.
- Rev 2 promised ordered skip data on the Observation; `observation-2.json` is
  `additionalProperties: false` and its own description says such an addition bumps `schema_version`.
- Rev 2 keyed accounts on a `"default"` sentinel for a null `credential_ref`, but
  `adapters/claude_cli.py:_claude_env` returns `None` there and the subprocess **inherits ambient
  `CLAUDE_CONFIG_DIR`** — distinct accounts would have been collapsed.

### The three decisions

**1. AC1 says the refusal reason must be "in the receipt". No receipt can exist.** `enforce.check_pre`
runs only per-attempt (`executor_routing_lib.py:853`; `:808` confirms no receipt is minted before
it). A total quota wall means no attempt happens, so there is nothing to attach a receipt to. Rev 3
proposes a durable `quota_block` audit record instead, and does not pretend that is a receipt.
→ **Amend AC1 to "durably attributable in the audit trail"?**

**2. AC2 says "Codex-lane dispatches unaffected" — your own comment makes that wrong.** Codex moved
to weekly-only and a free reset was burned. → **Confirm AC2 becomes "each pool guarded
independently, refusal names which pool"?**

**3. `codex_weekly` has no verified data source.** The Anthropic endpoint is confirmed working (your
own S1 spike), so Claude 5-hour and 7-day are implementable now. There is no equivalent verified
codex usage read, and two attempts to reach credentials were denied by the permission classifier.
- **(a)** Ship the Claude half now (`Part of #792`), file the codex weekly pool as its own child with
  its own spike. The pool table is generic, so codex becomes config + one adapter, no redesign.
- **(b)** Hold #792 entirely until a codex source is spiked.

**Recommendation: (a).** Shipping less than was asked for is the owner's call, not the
implementer's — which is why this is a blocker rather than a quiet decision.

### Still unresolved (pass-3 Highs, all verified at source)

- **F1** — the wall is evaluated after `_do_dispatch` appends the expected call to the ledger
  (`:3186`). A walled dispatch leaves an expected call with no receipt; retrying the same correlation
  id is rejected as a duplicate (`ledger.py:237`) and a new id leaves the original permanently
  unreconciled (`enforce.py:843`). **The advertised "retryable, self-resolving" refusal cannot
  actually complete within the same run.** Fix: evaluate before `append_expected`.
- **F2** — the per-dispatch `quota_guard` status has no durable sink. `quota_block` only exists when
  something is blocked, so an all-fail-open run looks identical to a fully-protected one. **This is
  the silent-failure class epic #756 exists to kill, inside the feature meant to prevent it.**
- **F3** — the effective-config-dir identity is resolved relative to process cwd, but a mutating
  Claude subprocess changes cwd to its worktree (`claude_cli.py:158`), and a value handed to a
  subprocess gets no `~` expansion. Poller and gate can disagree on the same account.

No branch was cut, no code written. Design rev 3 is durable on branch `docs/792-design-artifacts`
(`8db07f4`).


## 4. #797 — context-meter thresholds: the instruction and the maths disagree

### Purpose

Owner instruction, verbatim: "if 60-80 is the degredation point lets change to 55 and 75" —
i.e. `hooks/context_meter.py:84-85`, `DEFAULT_CHECK_IN_PCT = 35` → **55**, `DEFAULT_ACT_PCT = 50` → **75**.

### The problem found while implementing (CONFIRMED by projection analysis)

Today `DEFAULT_ACT_PCT = 50`, but the meter **already fires late** — it fired the directive tier at
**69%** when the act line was 50. Moving the act line to 75 makes it fire **later still**, in every
realistic case, even with a projection guard that fires before the crossing rather than after:

| steady per-turn growth | projected ACT fires at | vs today's 50 |
|---|---|---|
| 1 pt | 74% | **24 points later** |
| 5 pt | 70% | **20 points later** |
| 10 pt | 65% | **15 points later** |
| 15 pt | 60% | **10 points later** |
| 20 pt | 55% | **5 points later** |

The projection can never fire below 55 (the check-in line), so **55 is the floor** no matter how
large the jump.

### Why that is likely the opposite of the intent

The instruction aims to keep the session **out** of the degradation band. But the code's own recorded
rationale (`hooks/context_meter.py:76-85`) says the binding constraint is different:

> "At 35% of a 1M window a session has ~650k tokens in hand to finish its phase and hand over
> properly; at 70% it has 300k and is already choosing what to drop."

That argument is about **room left to write a good handoff**, not about where quality degrades. The
two pull in opposite directions. AC1 already anticipated this: "Thresholds are 55/75 (or the
owner-approved alternative if the overshoot fix changes the calculus)." **The overshoot analysis
changed the calculus.** Step-4 review returned SHIPPABLE: NO (4 High + 1 Medium), with this finding
flagged ambiguous.

### Recommendation

**Fix the late-firing first** (#729 and #734 are the meter-reliability children, both already in this
epic), then retune on a meter that fires when it says it does. Setting 55/75 on today's overshooting
meter would likely push real handoffs past 75% — inside the degradation band, with less room to hand
over well.


## 5. #777 — per-phase token telemetry cannot be built on today's data

### Purpose

Per-phase input/output-token **and** time breakdown in WF2/WF3 run-records, feeding owner notes
5/9/12/13 and every future cost comparison.

### Method and status

Two design revisions, each reviewed cross-model at the Step-4 gate. Both failed on volume: pass 1
FAIL (7 High + 1 Medium), pass 2 **SHIPPABLE: NO** (8 High). A loop-back remains in budget and was
deliberately **not** spent — the second review did not find fixable design defects, it established
that **the inputs required for correct attribution are not produced anywhere.**

### The finding that matters most

**Half of AC1 is already shipped, and was nearly rebuilt.** Per-step and per-phase **wall time**
already exists (#506/#589): `step_state.compute_timing` → `work_summary._auto_embed_timing:182`,
with `timing_coverage_warning:231` already flagging gaps. **The genuine gap is token attribution
only** — and nothing in the system records tokens per phase. `usage_capture` is session-wide.

### Recommendation

**Park it,** with the finding written down, and untick it from the epic. A third revision could only
narrow the feature to near-uselessness or grow it into a much larger one, and that is a scope call
for the owner. If per-phase token attribution is genuinely wanted, it should be filed as its own
groundwork project — recording the data first, reporting on it second.


## 6. #796 — eight mechanical verbs: split it

### Purpose

Convert eight verbs the workflows re-derive as prose every run into scripts.

### Status

Three design passes, two independent reviewers per pass. Design loop-back **2/2 exhausted**, global
2/3. The #798 budget-exhausted carve-out **does not apply** — it requires the ambiguity breaker to
return `clear`, and pass 3 carries two ambiguous findings.

### Two author errors caught, both instructive

1. **A fabricated verification claim shipped into the design.** Rev 4 said "Verified against the
   live corpus: all six entries at `README.md:730-746` satisfy both patterns." It was never run.
   When run, the proposed boundary pattern `(?![\w.])` forbids a trailing period and **five of six
   live entries end `no diagram REV.`** — per-entry matches: v3.115.1 **0**, v3.115.0 **0**,
   v3.114.1 **0**, v3.114.0 1, v3.113.4 0. **The guard would have rejected almost every real release.**
2. **An unprobed git invocation.** `git show -- <ref>:<path>` reads nothing, because `--` separates
   revisions from pathspecs. Confirmed live: `git show HEAD~1:m.json` returns the blob;
   `git show -- HEAD~1:m.json` returns **empty**. Correct form: `git show --end-of-options <ref>:<path>`.

### The re-triage (done, and it stands)

| # | Candidate | Verdict | Reason |
|---|---|---|---|
| 1 | Version-bump ×4 + changelog | **DEFER to its own issue** | three failed design passes |
| 2 | Pin-guard allowlist | NOT SHIPPED | a partial allowlist manufactures false confidence exactly where `sweep_hand_pins`' exclusions already hide drift (`phase_executor/` outside `SWEEP_GLOBS`, the whole `docs/` tree excluded, **337 canonical-sentence prose pins** unswept, checker not in CI) |
| 3 | Step-12 PR-body asserts | **SHIP** | converged; see below |
| 4 | Merge-verification bundle | NOT SHIPPED | half exists (`launcher_lib.classify_issue_state:2102` → `confirmed_merged`); rest needs a squash-SHA check (no seam) and the **first issue-body write in the codebase** (`grep 'gh issue edit' hooks/*.py` → zero) |
| 5 | ci-wait verb | NOT SHIPPED | working prior art at workspace tier (`.claude/skills/ci-wait/watch.sh`) |
| 6 | usage-capture-legs | NOT SHIPPED | `session_registry.jsonl`'s 419 entries carry no issue key, so it cannot correlate |
| 7 | D6 sweep table | NOT SHIPPED | **no skill defines an executable grammar** — docs name the obligation, no columns/row shape/output format exist |
| 8 | Marker-writer | NOT SHIPPED | a grammar migration in disguise: 19 WF2 marker forms, ~24 test-side literal pins, one structural parser with a tie rule |

### Recommendation: split

1. **Ship candidate 3 alone** (`plan_lib assert-pr-body`) as this issue's deliverable. Both pure
   functions already exist (`plan_lib.py:301,345`) with **no production caller** — they run only in
   the end-of-run completion gate, which is exactly why the #781 H1 slip fired after merge. Two
   review findings still apply and both have clean fixes: reject a zero-task plan with rc 2 instead
   of passing vacuously (`plan_lib.py:352-353` returns `(True, [])` on an empty list), and bind
   `--plan-file` to the Step-8 gate artifact's recorded plan digest rather than trusting prose.
2. **File candidate 1 as its own issue**, carrying the three-pass evidence.
3. Two cheap correctness fixes can ride with either: `skills/epic-run/SKILL.md:67` says "version
   bump **×3** surfaces" and has been **four** since #470; and `canary.py:36` plus
   `tests/phase_executor/test_canary_digest_pin.py:3` both say the registration digest is "re-pinned
   per release" when `compute_registration_digest:229-263` reads no version at all — **prose
   inviting an action the code forbids.**

Design rev 4 (with both defects preserved as evidence, not as a plan) is on branch
`docs/796-design-artifacts`.


## 7. Earlier spikes — verdicts, for completeness

These are not blocking any decision on this page. Included so the spike record is complete rather
than selective; each links to its full report in `docs/planning/`.

| Spike | Question | Verdict |
|---|---|---|
| **#452** codex containment | Does `codex exec -s workspace-write` confine a mutating child? | **CONDITIONAL.** Enforced by an OS sandbox (Landlock + seccomp) blocking `$HOME` by default — **but default writable roots include all of `/tmp` and `$TMPDIR`**, where engine worktrees live. Naive `-s workspace-write` does **NOT** isolate a child from sibling worktrees. Worktree-only confinement is achievable and was live-verified, but needs three explicit config overrides. |
| **#453** `--tmux` × `-p` | Do they compose for supervisor spawn? | Conclusive verdict reached; Linux-first, tmux 3.4. Report-only, no production changes. |
| **#454** guardrail canary | Do project hooks fire in `claude -p` children, incl. `CLAUDE_CONFIG_DIR` lanes? | **CRITICAL: a fresh/unprovisioned `CLAUDE_CONFIG_DIR` lane child loads ZERO plugin hooks** — the entire guard layer (wal-guard, security-guard, session-start) is **silently absent** (`"plugins":[]`, 0 `hook_started` events vs 9 in a normal child). Multi-account lanes MUST provision the plugin layer per lane, and the canary MUST run per lane, or every guardrail vanishes. |
| **#455** resume mechanics | session_id capture, resume-from-cwd, worktree cell, abnormal-exit residue | Spike complete, report-only. Probes run from throwaway scratch dirs; codex resume explicitly out of scope. |
| **#456** effort vocabularies | Real accepted value sets for codex `model_reasoning_effort` + GLM `reasoning_effort` | Complete; normalized-to-native mapping drafted. Docs-only. |
| **#654** test plan | The owner-requested runnable test plan | Written 2026-07-27, pinned to Claude Code 2.1.220 / herdr 0.7.5. Investigation-only — each test names the question it settles, exact commands, pass criterion, cost, **and what it still cannot tell you.** |


## 8. Where the raw evidence lives

| Artifact | Path |
|---|---|
| S3 cache measurement | `docs/measurements/2026-08-01-794-spike-s3-cache-reuse.md` |
| Owner-notes economics review | `docs/measurements/2026-08-01-owner-notes-executor-economics.md` |
| #762 retrospective | `docs/measurements/2026-08-01-762-retrospective.md` |
| Epic front-loaded path | `docs/planning/2026-08-01-epic-756-front-loaded-path.md` |
| #792 design rev 3 | branch `docs/792-design-artifacts` (`8db07f4`) |
| #796 design rev 4 | branch `docs/796-design-artifacts` (`6c9d7b47`) |
| #793 design artifacts | branch `docs/793-design-artifacts` (`335056cc`) |
| Epic decision log (D1–D67) | `claude_docs/session_notes/epic-756-autorun-log.md` (not in git — `claude_docs/` is gitignored) |
| Earlier spike reports | `docs/planning/2026-07-17-spike-45*.md`, `docs/planning/2026-07-27-spike-654-test-plan.md` |


## 9. One more thing worth your attention — #782

Not a decision on this list, but it is why old herdr panes stopped closing, and it is small.

**CONFIRMED:** #758 merged 2026-07-31 01:13 (`158b6ee2`, PR #773) and made goal-reading trust only
rows stamped `sentinel: true` — deliberately, as a prompt-injection boundary: without it, structured
content pasted into a transcript could forge a `goal_status` row and spoof "already cleared" (that
was the real #758 pass-2 bypass). **Side effect:** Claude Code stamps `sentinel: true` when it arms
a goal but **not** when the Stop hook evaluates one, so after any evaluation the newest row is
unstamped, the fail-closed validator refuses, and pane-handoff teardown exits before closing
anything. Measured: **53 of the 120 most recent transcripts are in the refusing state.**

#802 looked like the fix, but PR #803 (`26f865ef`) changed only the refusal wording. **#782 — open,
zero comments, not in this epic — is the real fix**, and it is the best small follow-up candidate.
