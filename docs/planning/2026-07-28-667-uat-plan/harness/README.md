# Epic #667 UAT harness

The tooling that ran the epic #667 UAT (plan: `../../2026-07-28-667-uat-plan.md`, results page:
`../index.html`). Committed because the UAT is meant to be **re-run** after epic #684 fixes the
transport, and rebuilding these from scratch each time guarantees they drift from the plan.

Not pytest. These drive a live herdr server and a live Claude Code session; they can never run in CI.

## `harness.py` — verdict recorder

```bash
python3 harness.py run <id> <note> -- <command...>   # PASS iff the command exits 0
python3 harness.py record <id> <PASS|FAIL|BLOCKED|INCONCLUSIVE> <note> [--evidence FILE]
python3 harness.py summary                            # exits NON-ZERO while any FAIL or NOT RUN
```

Its whole job is that **a verdict cannot be recorded without the evidence that produced it**, and
that `summary` names every check that has NOT run — a harness which only lists what it tried reads
as complete when it isn't. `summary`'s non-zero exit is the DONE gate.

The `CHECKS` dict is the agent-runnable checks from the plan — **37 after the 2026-07-28 re-triage**
(the original 24, plus 6 converted from human-only once the owner authorised them, plus tier 7's 7 for
#687). The four remaining owner judgements (W8b, V3b, L3b, C8) are deliberately NOT registered: an
unregistered id cannot be recorded, so an agent can never quietly answer them on the owner's behalf.
Unknown ids and invalid verdicts are
refused, so a typo fails loudly instead of silently recording nothing.

## `probe.py` — herdr event capture

```bash
UAT_SUBS=pane.updated python3 probe.py out.jsonl 120
```

Records raw frames with wall-clock stamps and **writes the subscribe request into the output file**,
so a frame count means something without having to remember what was asked for. It records; it does
not interpret — interpretation happens against the saved file, so a wrong reading can be re-read
rather than re-run.

**Subscriptions use key `type`, NOT `event`.** An `event` key is rejected
(`invalid_request: missing field 'type'`) AND the server closes the connection, so one malformed
subscription kills the whole feed silently.

## `ask.py` — one-shot socket request

```bash
python3 ask.py '{"id":"q","method":"session.snapshot","params":{}}' 2
```

Each call is its own connection, so a rejected request that closes the socket cannot poison the next
probe. Sending an unknown `method` makes the server **enumerate all 90 valid methods** — that is how
the events surface was mapped.

## `results-run1-2026-07-28.jsonl`

The first run's record: 13 PASS, 8 BLOCKED, 3 INCONCLUSIVE, 0 FAIL. Keep it. Re-runs should be able
to diff against it rather than argue from memory about what changed.
