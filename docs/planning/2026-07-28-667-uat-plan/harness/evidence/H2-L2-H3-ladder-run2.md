# H2 / L2 / H3 / H4 / H5 / C7 — the handoff ladder, UAT run 2 (2026-07-28)

## H2 — agent_pane_busy reproduces on a busy pane
  w1:pD4 was made busy with 'sleep 400'. Attempting an agent prompt against it:
{"error":{"code":"agent_not_found","message":"agent target w1:pD4 not found"},"id":"cli:agent:prompt"}


## L2 / H3 — the ladder REFUSES, and for a different reason than run 1
  Command actually run (a scratch driver-state, a throwaway anchor pane, no real work):
    launcher_lib.py handoff --driver-state /tmp/uat-l2-state.json --anchor-pane w1:pD4 \
      --name uat-l2-successor --project rawgentic --herdr-mode herdr ... (no arming flags)
  Result, verbatim:
    session_mode 'single'         -> "no handoff: campaign disposition is 'single_session'"
    session_mode 'fresh-session'  -> "no handoff: no durable launcher armed (launch mode 'herdr')"

  This is the SAME guard the predecessor session hit and correctly refused to bypass.
  fresh_session_available requires the CALLER to assert --launcher-armed AND
  --fresh-launch-supported, and launcher_lib.py:2156 is explicit that absence must not read as
  support. I can genuinely spawn a pane, so --fresh-launch-supported would be true; I have NO
  durable launcher armed, so --launcher-armed would be FALSE. Passing it to get past a
  guardrail is the one thing the operating rules forbid outright, so it was not passed.

## What #682 DID fix, visible in the contract rather than inferred
                               PROJECT_ROOT --project PROJECT --cwd CWD
                               --registry REGISTRY --transcript-dir
                               TRANSCRIPT_DIR
--
  --project is now REQUIRED on the handoff subcommand — that is #682's fix, and it is why a
  bare '/rawgentic:switch' (which enters list mode and waits for a human) can no longer ship.

## H2 — the precondition does NOT reproduce in run 2
  w1:pD4 running a real 'claude --permission-mode acceptEdits' agent was driven to
  agent_status=working, then 'herdr agent prompt w1:pD4' was issued against it.
  Run 1 reproduced agent_pane_busy live ('is not an available shell').
  Run 2: the prompt was ACCEPTED. No agent_pane_busy, no error — the response returned the
  agent object with agent_status 'working'.
  So the readiness wait #673 added could not be exercised: the condition it fixes did not arise.
  Recorded INCONCLUSIVE, not PASS — a bug that will not reproduce is not a verified fix.

  BONUS, and it corroborates #679: that same response carries state_change_seq: 2217 on the
  agent object. state_change_seq is exactly the key #679 moved the watcher's dedup onto, and
  #612's first design wanted it but abandoned it because the pane.updated EVENT does not carry
  it. Here it is, present on the agent object that the poll path reads.

## H4 / H5 / C7 — all three sit behind the same gate
  H4 (retire-predecessor clears the guard) and H5 (the predecessor's Stop releases) both need a
  COMPLETED six-step ladder to authorise retirement, and C7 (#687 end to end: handoff written,
  successor bound/prompted/goaled, predecessor retired last) needs the same handoff path.
  The ladder refuses at step 0 for an unarmed caller, so none of the three can be reached
  without asserting --launcher-armed falsely. Same root cause, recorded on each rather than
  collapsed into one entry.
