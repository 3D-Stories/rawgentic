WF2's mandatory-step list is prose, and a session under context pressure skips it. This proposes
the one change that makes the list enforceable: **an append-only completion ledger, gated at
`PreToolUse` on the artifact each step alone can produce.** Everything else the gate needs —
detection, the deny mechanism, session binding — already exists in this repo.

```verdict
adopt | The mechanism AND its fail-mode split already shipped in #976; only the ledger is missing.
correct | The obvious framing — "gate on the WAL" — is wrong: the WAL logs tool calls, not steps.
risk | A false positive blocks every session in the workspace, so shadow mode is not optional.
```

**Feeds:** #932 (`decide(gates)`) — supplies its AC (i) candidate list and a trial for AC (ii).
**Builds on:** #976, **merged as PR #978 on 2026-08-06** — `hooks/campaign-merge-guard.py` and
`hooks/campaign_merge_guard_lib.py`, registered `PreToolUse` on matcher `Bash`.
**Status:** design proposal, report-only. No code written, no issues filed (D179).

## The problem, as an incident rather than a preference

`projects/rawgentic/CLAUDE.md` mistake #8 records the failure by name:

> **Skipping WF2 mandatory steps under context pressure.** Step 11 (code review) once caught two
> Critical vulnerabilities on a run judged "too simple to review".

Steps 1–5, 7–9, 11, 11.5, 12 and 16 are declared mandatory in a Markdown list. The workspace manual
repeats the rule. The repo manual repeats it again. It has still happened.

This is not a discipline problem awaiting a firmer sentence. Metaswarm's authors reached the same
conclusion after shipping regressions past a checklist (`docs/coverage-enforcement.md`):

> Telling agents "check coverage before pushing" in a checklist is not enforcement — it's a
> suggestion. Agents skip steps, misread thresholds, or run the wrong command.

## The design fork

```options
Deterministic PreToolUse gate on artifact evidence | Objective, cheap, reuses #976's proven deny path | Needs a completion ledger that does not exist yet | chosen
LLM-judged prompt/agent hook (#932's new capability) | Can evaluate subjective gates no artifact can prove | Per-fire cost, non-deterministic, unmeasured here |
Repurpose step_state.py as the gate | No new store; the file already exists | Inverts a documented FAIL-OPEN contract two consumers depend on | rejected
Keep it prose, dial the wording harder | Zero build cost | Already measured to fail, repeatedly | rejected
```

The first two are complements, not rivals. Objective conditions belong in the deterministic gate;
subjective ones are exactly what #932's `type:'prompt'` and `type:'agent'` hooks are for, and are
where D175 correctly left prose.

## What already exists, and why none of it can be the gate today

This section exists because the obvious proposal is wrong, and the reason is only visible in the
code. Each claim below was verified first-hand on 2026-08-06.

```nodes compare
Already built — reuse
  step_state_post.py | derives step transitions from artifacts | PostToolUse:Bash
  wal-guard | the PreToolUse deny wire format | shipped
  campaign-merge-guard.py | PreToolUse hard enforcement + split fail-mode | shipped #978
  session registry | binds session to project | $CLAUDE_CODE_SESSION_ID
Missing — must be built
  completion ledger | append-only record of which steps passed | new ~
  artifact evaluator | re-checks evidence on disk | new ~
```

### The WAL logs tool calls, not workflow steps

`claude_docs/wal/<project>.jsonl` carries four phases. Counted live on this workspace's own file:
`INTENT` 2,486 · `DONE` 1,881 · `FAIL` 73 · `STOP` 79. The record shape is
`{ts, phase, session, tool, tool_use_id, summary, cwd}`, plus `project` on `STOP`.

There is no workflow field, no step field, and no verdict for anything larger than one tool call.
**The WAL cannot answer "did Step 11 run and pass?"** It answers only "was a command executed."

### `step_state.py` is a now-pointer, and forbids this use in capitals

`claude_docs/wal/<project>.state.json` holds exactly one record, overwritten at each step entry:

```json
{"schema_version":1,"project":"rawgentic","workflow":"wf2","step":"14",
 "step_title":"Merge","issue":964,"session_id":"...","entered_at":"2026-08-07T01:16:04Z"}
```

Entering Step 14 erases any trace of Step 11, so "was Step 11 reached" is unanswerable by
construction. Its own docstring settles the rest:

> FAIL-OPEN, EVERYWHERE, ALWAYS: this is never a gate. [...] Contrast `hooks/wal-guard`, which is
> fail-CLOSED [...] that hook is a security boundary; this one is pure telemetry.

Two consumers — the statusline and `hooks/wal-context` — already depend on that contract.

### `step_state_post.py` already solved the hard half

It derives the now-pointer from artifacts the orchestrator reliably produces, precisely because the
manual write was under-complied with ("observed under-complied twice under batching on
2026-07-19"). Its MARKER detector parses `### WF<n> Step <X> … DONE (#<issue>)` from a session-notes
append; its SIGNATURE detector recognises per-step commands. **Detection is not the gap. Durable
recording is.**

## The missing primitive: a completion ledger

```callout decision
note | Add an append-only step-completion record; do not repurpose the now-pointer
A fifth WAL phase, or a sibling `<project>.steps.jsonl`. It must be append-only, must name an
evidence artifact rather than only a verdict, and must be written by the detector that already
exists — one helper, one home.
```

```json
{"ts":"2026-08-06T22:41:09Z","phase":"STEP","session":"<uuid>","project":"rawgentic",
 "workflow":"wf2","issue":964,"step":"11","step_title":"Code Review",
 "verdict":"pass","evidence":"docs/reviews/2026-08-06-964-review.md"}
```

### Gate on artifacts, not on self-declaration

The strongest form never reads `verdict`. It re-checks the artifact. This is not a new principle
here — the workspace manual already requires it for deploys:

> Gate "the new build is live" on a signal only the new build emits [...] During a zero-downtime
> swap a 200 can be the OLD container.

A marker saying "Step 11 — DONE" is the 200. The review report matching this run's issue and branch
is the signal only the real step emits.

| Step | Artifact only that step can produce |
| --- | --- |
| 11 — code review | a review report under `docs/reviews/` naming this issue |
| 11.5 — adversarial diff review | the WF5 report for this branch |
| 12 — security scan | a `security_scan.py` receipt for this tree |
| 16 — run record | the appended line in `docs/measurements/run_records.jsonl` |

**Honest limit:** this proves an artifact exists, not that thought went into it. A determined model
could forge a file. That is acceptable — the failure being fixed is *skipping under pressure*, not
deception. Raising the cost from "say nothing" to "forge a report" is the whole win. Do not oversell
it as tamper-proof.

## Fail mode: the split that keeps this safe

Repo convention says fail mode is per-hook: security boundary → fail-closed, convenience →
fail-open. This gate is neither, and forcing it into one is the main way this design could hurt.

**The correct axis is not open versus closed. It is "could not evaluate" versus "evaluated, and the
evidence is absent."**

This is not a new proposal. **#976 shipped exactly this split, as decision D186**, and
`hooks/campaign-merge-guard.py` states it in its own docstring:

> Policy: SPLIT at the classification boundary (#976 AC3, decision D186)
>   - before a command is classified  -> fail-OPEN, with a stderr diagnostic
>   - after it is a raw `gh pr merge`  -> fail-CLOSED

So the safety question this design most needed to answer is already settled in the repo, by a
merged hook with a decision ID. This proposal adopts it verbatim rather than re-deciding it.

| Condition | Decision |
| --- | --- |
| Ledger unreadable, Python missing, any exception | **allow**, log to stderr |
| No WF2/WF3 run in progress for this session and project | **allow**, silently — this is most work |
| Run in progress, evidence artifact present | **allow** |
| Run in progress, evidence artifact **absent** | **deny**, naming the exact remedy |

A hook that denies because it crashed is an outage. A hook that allows because it crashed is a gate
reporting green while broken — the manufactured-green failure the operating instructions forbid.
This split refuses both: it never denies on ignorance, and every could-not-evaluate leaves a log
line, so an allow is never silently mistaken for a pass.

### Scope guard — the highest-risk detail

The gate must fire **only** when a WF2/WF3 run is genuinely in progress for this session and this
project. A docs PR, a hotfix, an ad-hoc session, another project in the same workspace — all pass
untouched. Getting this wrong blocks the workspace, which is why it is the first thing the trial
measures. Session binding comes from `$CLAUDE_CODE_SESSION_ID`, never `.current_session_id`
(workspace mistake #3).

### The override is loud, not absent

Metaswarm wrote an escape hatch into its own gate — skippable when the agent judges the work
simple. That is a gate disabled by the thing it gates. A gate with no override is also wrong: it
strands a real emergency. Proposal: an override that works but is **visible** — it allows the
command, writes a `STEP` record with `verdict:"override"` and the reason, and surfaces in the run
record. Bypass stays possible; silent bypass does not.

## Candidate list — #932's AC (i)

#932's test is *"if violating it once is an incident, make it a hook."* Applied:

```chips
Step 11 review before gh pr create | done
Step 12 security scan before PR | done
Step 16 run record before completion | done
Steps 1-5 ordering (needs ledger first) | wip
Completion honesty (LLM-judged) | wip
Marker discipline (LLM-judged) | wip
One-question-at-a-time (stays prose) | blocked
```

Tier 1 is deterministic and objective — the three marked done above are the adopt candidates, and
**Step 11 before `gh pr create` is the trial for AC (ii)**: it is the rule whose violation already
cost two Critical vulnerabilities. Tier 2 (step ordering) is objective but meaningless without the
ledger. Tier 3 — completion honesty, marker discipline — is genuinely subjective; no artifact can
prove it, which is exactly where the LLM-judged hooks earn their place. The register and
one-question rules are explicit non-candidates: violating them is friction, not an incident.

## Rollout: shadow mode first, and it is not optional

A hook that denies `gh pr create` has workspace-wide blast radius.

1. **Shadow.** Ship the ledger writer and evaluator with the deny path disabled. Log every decision
   it *would* have made.
2. **Measure.** Over real runs, classify each would-have-denied event as a true skip or a false
   positive. One unexplained false positive resets the clock.
3. **Enforce** one rule — Step 11 before `gh pr create`.
4. **Widen** one rule at a time, each with its own shadow period.

Undo at any stage is removing one entry from `hooks/hooks.json` and reinstalling the plugin.

## Risks, including the one most likely to be wrong

- **Highest blast radius:** the scope guard. Wrong, it blocks the workspace. Shadow mode measures
  exactly this.
- **The claim I would most expect to be wrong:** that `step_state_post.py`'s detectors fire
  reliably enough to drive a ledger. Its own docstring reports the manual path was under-complied
  with twice, which is why it exists — but its detectors have never carried a *blocking* decision.
- **Concurrency.** Several sessions share this workspace. The ledger must be append-only with
  per-line records, never read-modify-write.
- **Scope creep into #976.** #976 (merged, #978) owns campaign merges; this owns WF2/WF3 step
  completion. Two PreToolUse:Bash hooks now coexist, so a third must not re-classify commands the
  merge guard already owns. If they converge on one evaluator, that is a follow-up, not a reason to
  couple them now.

## What this does not decide

Whether to adopt at all — that is #932's ruling, and it needs the trial first. Where the ledger
lives: a fifth WAL phase (reuses existing rotation and path resolution) or a sibling file (cleaner
separation). Whether Tier 3 uses `type:'prompt'` or `type:'agent'` — that needs #932's cost
measurement.

## Confirmed vs inferred

**Confirmed** (read first-hand in this repo and workspace, 2026-08-06): the WAL's four phases and
record shape, counted from the live file; `step_state.json`'s single-record structure and its
fail-open docstring; `step_state_post.py`'s two detectors; `wal-guard`'s deny wire format; the text
of #976 and #932; that #976 merged as PR #978 and its D186 split-fail-mode docstring, read from
`hooks/campaign-merge-guard.py` on `origin/main` at `16c07b8d`; metaswarm's
`coverage-enforcement.md` quotation.

**Inferred, not confirmed:** that `step_state_post.py`'s detectors are reliable enough to gate on;
that no fourth mechanism already records step completion somewhere unread; that the artifact table
names the right file for every step. Each needs checking against the current WF2 skill text before
implementation.

```provenance
feeds | #932 decide(gates)
builds on | #976 PreToolUse hard enforcement
prior art | dsifry/metaswarm, read 2026-08-06
verified | 2026-08-06
```
