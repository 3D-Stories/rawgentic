# V1/D1/R2 — UAT run 2, 2026-07-28

## V1 — the herdr version gates
hooks/driver_lib.py:579:    Generation-bound deliberately: an unqualified marker would also match a PREVIOUS handoff's
  --- live: a fake 0.7.4 earlier on PATH ---
  fake reports: herdr 0.7.4
  real reports: herdr 0.7.5
  parsed 'herdr 0.7.4' -> (0, 7, 4); qualified floor is (0,7,5)
  gate verdict: REJECT (below the qualified floor)

## D1 — every read-only runbook command executes verbatim
herdr integration status
herdr integration uninstall
herdr is load-bearing
  --- executing the read-only ones ---
  herdr pane list      rc=0
  herdr tab list       rc=0
  herdr pane current   rc=0
  herdr wait (documented as NOT a command) rc=2

## R2 — the driver_bench parallel-run race
hooks/driver_bench_lib.py


no tests ran in 0.00s
