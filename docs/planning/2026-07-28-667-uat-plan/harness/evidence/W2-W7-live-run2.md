# W2-W7 — the watcher fires, live (UAT run 2, 2026-07-28)

Watcher: pane_watch_lib.py watch --source poll --poll-interval-s 3 --sender-cmd 'cat >> /tmp/uat-w2.log'
Throwaway pane w1:pD3 (tab w1:t15) running 'claude --permission-mode default --model sonnet' in /tmp.

## W2 — exactly ONE notification per block transition
Pane driven to a REAL permission prompt ('This command requires approval / Do you want to proceed?'):
  agent_status=blocked revision=3
  notification body delivered:
    herdr: w1:pD3 [claude] is blocked — waiting on you (w1 w1:t15)  count: 1 notification(s)

  NOTE the revision: it did NOT advance on the working->blocked transition. That is #679's
  SECOND root cause, and why the dedup key had to become state_change_seq rather than revision.

## W5 — no pane content in the body
  The body is: pane id, agent name, workspace and tab. It contains none of the prompt text,
  no command, no transcript. The words 'chmod', 'curl' and 'approval' do not appear:
    chmod      present? 0
    curl       present? 0
    approval   present? 0
    proceed    present? 0

## W3 — re-observing the same block does NOT re-notify
  After the first notification the watcher kept polling the SAME blocked pane for a further
  20 s (47 polls total). Notification count stayed at 1; heartbeat reported notified: 1.

## W4 — unblock then re-block DOES notify again
  Esc cleared the prompt (agent_status blocked -> done, notifications still 1), then a second
  gated command re-blocked the pane. Heartbeat then reported notified: 2 and a second body was
  appended. (The bodies concatenate without a newline because the capture sender is a bare
  'cat >>', so the heartbeat's notified counter is the authoritative count, not wc -l.)
    final heartbeat of that watcher: {"events": 7, "notified": 2, "polls": 64, "poll_failures": 0}

## W7 — the startup sweep catches an ALREADY-blocked pane
  The first watcher was killed while w1:pD3 was still blocked. A FRESH watcher was started and
  within 14 s (3 polls) it delivered:
    herdr: w1:pD3 [claude] is blocked — waiting on you (w1 w1:t15)  UAT FINDING (minor, not a W7 failure): that watcher's heartbeat reports notified: 0 while the
  body was demonstrably delivered — write_heartbeat only counts report['notified'], and the
  startup-sweep path does not pass one. Delivery is proven by the body; the COUNTER
  under-reports sweep notifications. Filed as a follow-up.

## W6 — heartbeat advances, and the stall warning fires past its window
  Heartbeat advanced monotonically across every observation in this run:
    polls 7 -> 19 -> 47 -> 64 (first watcher), 3 -> 9 (second), poll_failures 0 throughout,
    agents_tracked 8, mode 0600, written atomically.
  stall_warning, driven directly against a real pane shape:
    inside the window  (10s of 20s): None
    PAST the window    (45s of 20s): 'herdr: w1:pD3 has been idle for ~0m with no recognised transition. This is a stale-pane warning, NOT a missed-prompt detector — it cannot tell a missed approval from a pane that is simply idle. Worth a look.'
    a working pane is never stalled: None
    heartbeat_due(last=now-20, interval=20) -> True   (>= not >, per the Step-8a finding)

## P1/P2/P3 — the same live run demonstrates the whole detection tier
  P1: w1:pD3 reached agent_status=blocked on a REAL 'This command requires approval' prompt.
  P3: Esc cleared it and agent_status went blocked -> done within ~12 s.
  P2: the transition IS carried and observable — the watcher notified on it. BUT the
      'revision advances' half of P2's wording is REFUTED by this run's own reading:
      revision stayed at 3 across working->blocked. That is exactly #679's SECOND root cause,
      and it is why the dedup key moved from revision to state_change_seq on snapshot.agents[].
      Run 1 recorded P2 PASS with 'revision advances; state_change_seq ABSENT' — that premise
      is now superseded, and the observability the check exists to test is confirmed.
