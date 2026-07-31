# #765 — Step-3 competitive bake-off: the recorded decision (WIRE)

Epic #756 child 6/20 · 2026-07-31 · sessions 4ac8c4b0 (Steps 1–4, fork) + b9a74758 (Steps 5–16)
· plugin v3.111.0

## The decision

**WIRE the bake-off — owner decision D26 (2026-07-31).** Two-way /ask-owner (token
RG-761044, option_required): the owner selected option 2, "WIRE the bake-off now in this
child," answering in ~4 minutes (delivery 0f0ec03c3646). The RETIRE recommendation was
presented alongside it and **declined** — recorded here per the run's decision log
(`claude_docs/session_notes/epic-756-autorun-log.md` D26).

This satisfies **AC1** (a recorded decision with reason). **AC3** (the RETIRE branch's
carve-out wording) is n/a under WIRE. AC2 and AC4 are covered by the shipped changes below.

## The fork as it was presented (both branches honestly costed)

The issue's binary fork, evidence-hardened by a three-pass Step-4 gate (10 unique findings,
all adopted; the gate twice caught this session's own evidence errors — an overclaimed
"no wiring exists" and a glm probe that repeated the documented 2026-07-17 false-defer):

- **(a) WIRE now** — the policy layer (`hooks/bakeoff_policy.run_design_round`: sol-vs-opus
  candidates, glm-5.2 judge, winner's exact bytes become the artifact) already existed; what
  was missing was a workflow-callable adapter carrying the WF2 run identity (the engine
  minted a random `run_id`; `Candidate` had no correlation field), a sanitized committable
  evidence artifact (the raw sink is gitignored by owner decision — it embeds full candidate
  payloads and local paths), one live proving round, and the Step-3 prose + drift-guard
  flips. Recurring cost thereafter: one extra full design generation plus a judge call per
  gated design round — concurrent across quota pools, so tokens/quota rather than
  wall-clock. Benefit: always-on candidate diversity in the phase with the highest gate
  iteration this epic (4 of 5 children needed 3 Step-4 passes) — plausible but unmeasured.
- **(b) RETIRE permanently** — kill the drift debt (the carve-out had outlived its closed
  #472 pointer), keep the machinery for the build bake-off and the kukakuka extraction,
  and gate any re-open on fresh evidence (e.g. an A/B of one child's design run both ways).

**The recommendation was RETIRE, narrowly** — adoption-follows-demand: the build seat
earned its wiring through a measured defect trail (#735 → #767 → #762), while the design
bake-off had zero measured demand against a real recurring token cost. The margin was
explicitly narrow (WIRE executable now, the mechanism genuinely non-redundant with
critique-of-one-candidate), which is why it went to the owner as a genuine call rather
than a foregone conclusion. The owner chose WIRE; that call is the decision of record.

## What shipped (PR: feature/765-wire-step3-bakeoff, v3.111.0)

1. **Run-identity plumbing** (`phase_executor/engine.py`): `run_competitive` accepts and
   records the caller's `run_id`/`correlation_id`; `Candidate.correlation_id` reaches the
   `AdapterRequest` and the harness Observation. Omitted identity keeps the prior shape.
2. **Workflow-callable CLI** (`hooks/bakeoff_policy.py design-round`): the exact command the
   WF2 Step-3 prose names — requires `--run-id`/`--correlation-id` (exit 2 without), exits 3
   fail-loud on an interactive judge failure, and writes a **sanitized** `--evidence-out`
   record (whitelist-only; `prompt_sha256`/`rubric_sha256` bind the evidence to its inputs).
3. **AC2 evidence** (`docs/measurements/bakeoff-765-design-round-evidence.json`): one LIVE
   glm-judged sol-vs-opus round dispatched through that CLI under the run's own identity
   (`wf2-765-b9a74758` / `765-s8-live-round`), `judge_degraded: false`, both candidates ok
   — pinned by a fail-closed guard test that rejects a degraded or wrong-model round and
   any payload/absolute-path leak.
4. **Prose + guards (AC4)**: the shared-block Step-3 row states the wired truth (command,
   judge-env prerequisite, failure semantics, lane exemption); `_CARVEOUT_TRUTH` flipped
   with negative pins — no `#472` deferral survives in the block, corpus, or
   `docs/run-records.md`, whose reconciliation section now states the honest per-stream
   join truth (design rounds carry workflow identity; the build stream's wiring is #762's).
5. **Diagram REV 3.111.0** (station-3 delta): the official sheet's Design Solution station
   documents the wired round; classification legitimately stays `competitive` — now true.

## Re-open / reversal path

Reversal is a prose + guard flip of the same surfaces (the RETIRE implementation shape is
preserved in the gate-ratified design note, rev 4) behind a fresh owner-approved issue —
ideally carrying the A/B evidence this decision ships the instrumentation for: every wired
round's sanitized record is now attributable to its run, so bake-off value is measurable
per child from here on.
