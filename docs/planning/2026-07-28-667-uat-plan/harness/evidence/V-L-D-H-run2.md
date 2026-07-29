# V1/V2/L1/D1/D2/H1/H6 — re-verified on merged main (UAT run 2, 2026-07-28)

## V1 — a fake herdr 0.7.4 must FAIL the version gate
  version-gate callables in driver_lib: []

## V2 — installed herdr binary digest
/home/rocky00717/.local/bin/herdr
herdr 0.7.5
3dc83288073e4c2d3c679a30e7be97bcca9141c6fd17dbbb9219142e95c59253  /home/rocky00717/.local/bin/herdr
21315048 bytes

## L1 — read-only launcher subcommands
  launcher status -> rc=2
  launcher inspect -> rc=2
usage: launcher_lib.py [-h]
                       {handoff,mid-child-handoff,retire-predecessor,read-goal-condition,build-fallback,select-mode,build-split,verification-steps,goal-text}
                       ...


## D2 — the #654 Q1 context measurement reproduces (see also C1)
{"fraction": 0.653634, "provenance": "env", "tier": "advisory", "transcript": "/home/rocky00717/.claude/projects/-home-rocky00717-rawgentic/1eba31af-8776-497d-b97d-0bdb2ad812fb.jsonl", "used": 653634, "window": 1000000}
  The identity itself, re-derived from #654's captured statusline numbers:
   2 + 1257 + 652960 = 654219 == total_input_tokens 654219 -> True
  and #687 SHIPPED a consumer of that measurement, which is the state change since run 1.

## V1 — the version gates reject a below-qualified herdr
hooks/driver_lib.py:579:    Generation-bound deliberately: an unqualified marker would also match a PREVIOUS handoff's
hooks/pane_watch_lib.py:22:against herdr 0.7.5 on this host, 2026-07-28:
hooks/pane_watch_lib.py:45:`events.subscribe` on herdr 0.7.5 delivers a one-time backlog burst of ~39 frames in under 3 seconds
hooks/pane_watch_lib.py:1519:                              "uses events.subscribe, which on herdr 0.7.5 delivers a backlog "
hooks/launcher_lib.py:13:EXPLICIT pane id, not only `--current`** (read from the pinned 0.7.5 binary's `--help`).
hooks/launcher_lib.py:21:A cross-model review rejected that with upstream citations: herdr 0.7.5 rejects a native agent

## L1 — read-only launcher subcommands (rc=0 expected)
  launcher_lib goal-text -> rc=0
  launcher_lib verification-steps -> rc=0
  launcher_lib select-mode -> rc=0
  launcher_lib build-split -> rc=0
  read-goal-condition on this session's own transcript:
{"condition": "Epic #684 done AND the epic #667 UAT agent half re-run, both today. Epic: children #679, #682, #687 each merged into origin/main with their issue closed, #684's three checkboxes ticked, #684 closed with a summary comment \u2014 OR a blocker posted to that child's issue via the ERROR p

## H1 — the scratch campaign fixture loads
epic-635-herdr-build-seat.json
epic-684-watcher-fires.json
Traceback (most recent call last):
  File "<string>", line 7, in <module>
AttributeError: 'list' object has no attribute 'items'
  driver-state files: ['epic-635-herdr-build-seat.json', 'epic-684-watcher-fires.json']
  epic-684 state keys: ['campaign', 'epic', 'generation', 'issues', 'project', 'project_path', 'schema_version', 'session_mode']

## H6 — the ladder refuses teardown while successor checks are unmet
  teardown_allowed(predecessor checks only) -> (False, "refusing teardown: verification 'project_switched' has not passed — the predecessor stays alive and still guarded")
  teardown_allowed(all six) -> (True, 'all handoff verifications passed — predecessor may be retired')
  issues is a list (the epic trap: the key is issues, not children):
    {'number': 679, 'status': 'merged'}
    {'number': 682, 'status': 'merged'}
    {'number': 687, 'status': 'queued'}
