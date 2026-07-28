# #665 + #673 — the review record (WF2 Step 8a and Step 11)

Epic #667, child 4. Seven waves, eleven dispatches, all on the executor `review` seat
(`gpt-5.6-sol`, openai/codex pool, `transport: native`, `-s read-only`, exit 0 each, no Claude
quota consumed). Kept because the SHAPE of it is the useful artifact, not the count.

| Wave | Lenses | Verdict | Findings |
|---|---|---|---|
| 8a | correctness · destructive-path | FAIL · FAIL | 5 · 6 |
| 11 pass 1 | prose-vs-code · fence-refutation | FAIL · FAIL | 7 · 9 |
| 11 pass 2 | fix-verification · adversarial | FAIL · FAIL | 8 · 6 |
| 11 pass 3 | fix-verification · final-gate | DO NOT MERGE ×2 | 6 · 5 |
| 11 pass 4 | two independent merge votes | DO NOT MERGE ×2 | 3 · 4 |
| 11 pass 5 | two independent merge votes | DO NOT MERGE ×2 | 1 blocking, converged |
| 11 pass 6 | narrow verification | **MERGE** | 0 blocking |

Findings per wave: 11 → 16 → 14 → 11 → 7 → 2 → 0.

## What actually made it converge

**Insisting on a reproduction.** Nine defects were reproduced by probe before being fixed — three
by me (`_own_session_id` returning a stolen id with the environment removed; `teardown_allowed`
passing a one-step ladder; `last_unmet_goal_condition` returning a condition a later `met:true` row
had satisfied) and six by reviewers end to end (a replacement guard's clear confirming ours and
closing a live pane; a matching row landing during a duplicated pre-send probe; a re-armed
predecessor being closed; a real re-arm hidden behind one transcript read error; and the same
re-arm race surviving the first fix, found twice independently). Nothing was fixed on a reviewer's
word alone, and two reported findings were **refuted** by checking: a claimed order-dependent test
failure that no ordering reproduces, and an ack-before-gate "defect" that is the design's own
specified order.

**The recurring failure was mine, and it has a name.** Three times a reviewer refuted a fix I had
just written and described as complete:

1. the identity override — narrowed to "may not contradict the environment", when
   `env -u CLAUDE_CODE_SESSION_ID` restored the whole impersonation;
2. the ladder validation — "known step names" is not the invariant, and a one-step ladder still
   authorised a teardown;
3. the pre-send ordering — claimed "fence → probe → baseline → send" while `_destructive_call` still
   ran a state read and a `pane get` subprocess in between; then the identical mistake again for the
   re-arm check.

The pattern in all four: **a guard placed near the destructive act is not the same as a guard placed
immediately before it**, and describing an ordering is not the same as having it. Both fixes ended
the same way — stop delegating to the wrapper, run the checks explicitly, put the last one adjacent
to the syscall.

## What was NOT fixed, and why that is the honest answer

Two claimed Criticals were closed by withdrawing the CLAIM rather than padding the code:

- **Teardown identity is an interlock against mistakes, not authentication.** A caller controls its
  own child's environment, and anyone able to set `CLAUDE_CODE_SESSION_ID` can equally edit
  `.driver-state` or run `herdr pane close` directly. There is no boundary to defend, so the
  runbook's "cannot be asserted" wording was removed.
- **The check-to-syscall sliver** cannot be closed without a fencing token the destructive call
  presents, which herdr 0.7.5 does not offer. Design §10 item 1 already listed it as out of scope,
  and pass 6 confirmed it is the only surviving window.

## Five of my own tests were dishonest

Fixed rather than left green, and worth listing because it is the same defect class this epic hit in
four consecutive children — only in the tests rather than the prose:

- a tautological `... or True`;
- a fake that confirmed the clear on `send-text`, so no test could tell whether the Enter mattered;
- a receipt test that overwrote the very stale receipt whose rejection it claimed to prove;
- a "supersession" case that never created a supersession;
- an ordering assertion over an always-empty slice, which the buggy implementation also satisfied.

Plus a shared fixture whose `met:true` row lacked `sentinel:true`, so it could never have backed the
clear-confirmation reader the design cited it for.

Reports for the Step-4 design waves are the three sibling files in this directory. The per-dispatch
observations live under `.rawgentic/runs/wf2-665-ceaabfe6/` (not committed).
