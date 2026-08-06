**Where work runs (D174, the executor retreat — 2026-08-03).** Analysis and implementation run
INLINE in the orchestrating session. There is no dispatch machinery, no seat table, and no
per-phase model routing. Broad read-only gathers MAY fan out harness subagents (Agent tool,
Explore-style) to keep file dumps out of the main window — inline when narrow; judgment by
breadth (D182). Genuinely parallel implementation tasks MAY use Agent-tool worktree subagents;
the default is inline TDD. What is retired is the executor seat, not subagents.

**Cross-model review runs through ONE entry point** — `hooks/review_runner.py` (D179) —
dispatched from a read-only harness subagent so the inline self-review and the cross-model
review run in parallel. The subagent's ONLY job is to run one runner command and report back
the result path plus the exit code; it must not modify project files — its only permitted
write is the runner's declared `--out` result file. Command shapes:

```bash
# Text artifact (design/plan/spec/…): WF2 Step 4, WF5
python3 hooks/review_runner.py review-artifact --artifact <file> --type <design|plan|diff|…> \
  --author-model <your own model id, verbatim> --reviewer <reviewer, below> \
  [--backend gpt|glm] [--reopen-token <token.json>] --out <result.json> --project-root .

# Code diff vs a base ref: WF2 Steps 8a/11, WF3 Step 9
python3 hooks/review_runner.py review-code --base <base ref> --brief <brief.md> \
  --author-model <your model id> --reviewer <reviewer> [--backend gpt|glm] \
  [--reopen-token <token.json>] --out <result.json> --project-root .

# Independent peer proposal: WF13
python3 hooks/review_runner.py consult --artifact <problem.md> \
  --author-model <your model id> --reviewer <reviewer> [--backend gpt|glm] \
  --out <result.json> --project-root .
```

**The `consult` verb is supervision-gated when unattended (#947 Part B AC6).** If this session
is running away/sleeping (`supervision_lib.py nobody-to-ask` exits 0), run the check FIRST —
zero payload construction on a refusal:

```bash
python3 hooks/supervision_route.py consult-check --workspace-root <workspace root> \
  --project-root . --campaign-id <campaign id> --backend <gpt|glm>
```

Exit 0 → permitted; append `--allowed-backends` from the printed JSON's `allowed_backends` to
the `consult` invocation above (so a mid-flight 429 switch cannot land on an ungranted
provider). Exit 1 → refused; skip the dispatch and report the printed `reason` — never egress
anyway. An ATTENDED session skips this check entirely (a human is present to object).

**Reviewer identity is pinned, never inherited.** The current default reviewer id is
**`gpt-5.6-sol`** (single-sourced HERE — a retired id fails loudly at invocation and is updated
on this one line). The alternate backend is `--backend glm` (model `glm-5.2`). The runner
REFUSES author==reviewer and unresolvable identities; pass your own model id as
`--author-model`, verbatim. Exit codes: `0` success (check `diagnostic` in the result JSON) ·
`2` refused (validation/identity/token — no egress) · `3` terminal backend failure ·
`4` empty/invalid backend output. The runner owns transport policy (#857): one bounded
transport retry, org-wide 429 terminal, one permitted backend switch on a per-account 429 —
callers NEVER add their own retry loop around it.

**Actionable vs diagnostic — the reopen choke point (#855).** A review that may open a fix
round needs a reopen token minted FIRST:

```bash
python3 hooks/plan_lib.py review-reopen --state-file claude_docs/.wf2-state/<issue>/loopback_counters.json \
  --source <design|spec_tighten|tdd|review|review_design> --out <token.json> --project-root .
```

The mint itself debits the atomic loop-back budget; exhaustion refuses (exit 3) and the gate
escalates instead of looping. A tokenless run still reviews, but its result carries
`diagnostic: true`, and the disposition step MUST refuse to open a fix round on a diagnostic
result. Transport retries inside one runner invocation never re-debit. A spent or malformed
token refuses outright.

**The vacuous-result gate — subagent results are hypotheses.** Before consuming ANY subagent
result (review or gather):
1. the artifact it claims exists and is non-empty (for the runner: the `--out` file);
2. the shape parses (for the runner: JSON with a `status` field that matches the exit code);
3. freshness: the result's `head_sha`/`input_sha256` still match the current HEAD/artifact —
   a result whose subject moved before disposition is REJECTED and re-run against the new
   state; and any load-bearing claim is spot-verified against the cited file:line.
A dead subagent, an empty file, or a missing status is a FAILED dispatch — never a pass,
never "still running" (#766). Retry a failed review dispatch once; a second failure follows
the ERROR protocol.

**Disposition.** Findings flow to the gate's normal handling: the severity-banded confidence
filter, High-deferral discipline, the ambiguity circuit breaker, and the loop-back budget.
Fix, defer with rationale, or decline with reason — never silently drop. Concurrency courtesy:
keep ≤ 3 concurrent Claude subagents (token burn; a session-limit hit kills all in-flight
agents with vacuous results). A subagent or runner dispatch is never a gate bypass — every
mandatory review gate runs with identical semantics whether a pass ran inline or through
`hooks/review_runner.py`, and a review that may open a fix round carries a reopen token
minted first.
