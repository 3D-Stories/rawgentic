# #467 — W4: tmux supervisor + async dispatch + durable job registry + reaper

**Issue:** #467 (epic #475 W4; depends on #464, satisfied; consumes W3 #466) · **Date:** 2026-07-18
**Complexity:** standard-complex (lifecycle/security-sensitive) · **Lane:** full spine · **iteration 3** (Step-4 pass-2 volume loop-back — 20 fixes folded; design budget 2/2 SPENT → this converges)

platform_apis:

- api: dedicated tmux 3.4 server on a PRIVATE short socket outside /tmp (tmux -S <sock> new-session -d -s NAME -- <argv>, has-session, display-message -p '#{pane_pid}'/'#{pane_dead_status}', list-sessions -F, kill-session, kill-server, capture-pane -p)
  feasibility: verified via spike — a live tmux 3.4 probe THIS session confirmed the EXACT shipped composition: `tmux -S /run/user/$UID/rg-<run>.sock new-session -d -s <name> -- python3 -m phase_executor.pane_runner <spec>` keeps `has-session` ALIVE for the pane parent's lifetime; `pane_pid` = the python pane_runner; the socket is INVISIBLE to the default tmux server (isolating from the owner's tmux AND a codex child's /tmp writes — #452). Socket path must be <108 bytes → /run/user/$UID (27 chars). CORRECTION vs iter 1: `setsid <argv>` is BROKEN (session vanishes); bare `<argv>` is correct.
  failure: fail-loud
- api: POSIX two-group process kill (pane group + the PROVIDER's own group) with descendant-scan verify-dead
  feasibility: verified via spike — a live probe THIS session confirmed reviewer-found reality: the in-pane adapter (adapters/base.py:79 `start_new_session=True`) puts the provider in its OWN process group, DISTINCT from the pane_runner's (probed: runner_pgid != provider_pgid). So a single pane-pgid kill MISSES the provider. Corrected model (both probed): pane_runner SURFACES the provider pgid (sidecar + JobRecord) and a SIGTERM handler killpg's the provider group (graceful → provider dead); if pane_runner is SIGKILL'd, the supervisor killpg's the SURFACED provider pgid directly (probed: orphan → dead). verify-dead scans BOTH groups via pgrep -g / /proc, ignoring Z-state zombies (tmux reaps the pane). A deliberate re-setsid past that is the reaper backstop.
  failure: fail-loud
- api: atomic durable sentinel + registry (mkstemp->fsync->os.replace, pane_runner-local DIR-fsync, JSON job registry, W3 WorktreeHandle rehydrate)
  feasibility: verified via existing-call-site — capture.py:54-65 atomic_write_text + the adapter's write_observation/finalize (claude_cli.py:142-143) IS the sentinel; pane_runner calls the ADAPTER DIRECTLY with a supervisor-fixed attempt_id/capture_root (single writer at a KNOWN path) and adds a pane_runner-local dir-fsync (no shared capture.py change); the registry reconstructs a frozen worktree.WorktreeHandle from its 6 persisted fields.
  failure: fail-loud

## Problem (spec-grounded: AC-E1..E6, OQ-5/OQ-8; spikes #453/#455)

The executor needs a spawn path that **survives orchestrator compaction** (a private tmux server owns
the process), is **observable/killable/rescuable by name**, and completes by **process exit + an atomic
`observation.json` sentinel** — never TUI scraping, never `send-keys` (F7). A usage-limit exit is NOT
survivable (F11: child exits 1) → record `quota_paused`, persist resume state, relaunch `--resume` from
the seat cwd (spike #455: wrong-cwd is a HARD failure). Orphans adopt ONLY on full identity match, else
quarantine (OQ-8). No supervisor/registry module exists today.

## Design — `phase_executor/src/phase_executor/{supervisor.py, registry.py, pane_runner.py}` (extraction-clean; NO hooks import)

### Security / lifecycle through-line

1. **Command-as-initial-pane-process + a thin pane_runner that owns the provider** (AC-E2/E3/E5, F7,
   CF-17/CF-18/CF-19): the pane's initial process is `python3 -m phase_executor.pane_runner <spec>` (bare
   argv; tmux makes the pane its own group leader). The **supervisor resolves routing + acquires the quota
   permit AT LAUNCH** (AC-E5) and hands pane_runner a FIXED spec `{target (model/lane/engine), prompt,
   capture_dir, attempt_id, effort, permit_ref}`. pane_runner: (a) calls the resolved **adapter directly**
   (`mod.run(req, run_id, attempt_id=<fixed>, capture_root=<fixed>)`) — the ONE authoritative
   `observation.json` at a path the supervisor KNOWS — then dir-fsyncs the capture dir and records the
   provider exit code; (b) the adapter spawns the provider `start_new_session=True` (its OWN group), so
   pane_runner **surfaces the provider pgid** (`<capture_dir>/.provider_pgid` + the JobRecord) and installs
   a **SIGTERM handler that killpg's the provider group** for a graceful teardown. `capture-pane` is
   owner-OBSERVABILITY only, never control flow.
2. **Private tmux server, socket outside /tmp** (CF-2/CF-20): `tmux -S <run_socket>`, `run_socket` =
   `/run/user/$UID/rg-<run>.sock` (short <108 B, 0700, outside /tmp/$TMPDIR — #452); `resolve_socket`
   **probes dir existence/creatability** and falls back to `~/.local/state/rawgentic/run` when
   `/run/user/$UID` is absent (headless/CI/root, no logind); preflight fails closed on an unusable dir.
3. **Kill BOTH groups, verify the SET dead, then the session** (AC-E4, CF-17): timeout/cancel = SIGTERM
   the pane group (pane_runner's handler killpg's the provider group) → grace → SIGKILL the pane group AND,
   if the surfaced provider pgid still lives, `killpg` it directly → **scan BOTH groups dead** (pgrep -g /
   proc, Z-state = dead) → only then `kill-session` + emit the timeout Observation. A grandchild that
   re-`setsid`s past the provider group is caught by the reaper backstop (no hard-guarantee claim).
4. **A recovery identity mismatch KILLS both groups + RETAINS, never leaves a live writer** (OQ-8, CF-7):
   adopt only on full identity+digest+worktree+capture match; ANY mismatch → kill both groups (stop the
   untrusted writes) + W3 `_retain` (owner-visible via retained EVIDENCE).
5. **The reaper acts on CONFIRMED-dead processes only** (AC-E6, CF-8/CF-17): every sweep gates on
   process-death FIRST (`has-session` false AND BOTH groups' descendant-scan dead), the worktree-clean
   probe SECOND. Liveness is DERIVED — `has-session` + the capture dir's freshest mtime (AC-I4), never a
   written heartbeat.

### B.1 registry.py — durable job registry (pure core + atomic I/O)

- `JobIdentity` = `worktree.WorktreeIdentity` reused directly (CF-4).
- `session_name(identity)` = `"rg-" + "-".join(component_for(x) for x in (run,seat,attempt))` (W3
  `component_for` hash-disambiguation, CF-11; `.`/`:`→`_`; bounded).
- `JobRecord(identity, session_name, run_socket, pane_pid, pane_pgid, provider_pgid, pane_start_time,
  worktree_{path,base_sha,root,gitdir,repo}, capture_dir, attempt_id, permit_ref, command_digest,
  provider_session_id, provider_exit_code, resume_attempts, state, created_at, quarantine_reason)` —
  carries both group pgids (CF-17), all 6 WorktreeHandle fields (CF-4), the permit_ref (CF-19), and
  resume_attempts (CF-6). `state ∈ {launched, running, exited_no_sentinel, quota_paused, timed_out,
  completed, completed_with_residue, failed, quarantined}`.
- Pure functions: `command_digest(argv)`; `handle_from_record(rec) -> WorktreeHandle`;
  `classify_recovery(record, live, sentinel_valid, observed_identity, now) ->
  adopt|quarantine|relaunch|fail` (relaunch iff quota_paused + no live + `resume_attempts < MAX_RESUME`,
  else fail); `reap_plan(records, live_sessions, now, policy, dead_fn, clean_fn)` — three tiers, kill gated
  on `dead_fn` (BOTH groups) before `clean_fn` (CF-8).
- `JobRegistry` (I/O; injected clock): `<registry_root>/jobs.json` atomic 0700; single-writer doc note.

### B.2 supervisor.py — TmuxSupervisor (async OQ-5; injected `run`)

- `preflight(run_socket)` (AC-E1, fail-closed BOTH ways, CF-13): resolve tmux, `-V` floor, probe verbs on
  the private socket + the socket-dir creatability (CF-20); missing verb / bad version / unusable dir ⇒
  `supported=False`.
- `resolve_socket(run_id)` (CF-2/CF-20): `/run/user/$UID/rg-<san>.sock` or the `~/.local/state` fallback;
  reject /tmp/$TMPDIR and ≥108-byte paths.
- `launch(spec)` (AC-E2/E3/E5): **resolve routing + `QuotaCoordinator.acquire` the permit HERE** (AC-E5,
  CF-19; the supervisor holds it for the job); `tmux -S <sock> new-session -d -s <name> -- python3 -m
  phase_executor.pane_runner <spec-file>` (bare, cwd=worktree); read pane_pid/pane_pgid/start_time; poll
  the sidecar for the surfaced `provider_pgid`; write a `running` JobRecord. NEVER send-keys.
- `status(handle)` (CF-6): `has-session` + a VALID sentinel + the recorded pids → running / completed /
  **exited_no_sentinel (DEFAULT for any ambiguous nonzero exit — NO auto-resume)** / timed_out.
  `quota_paused` only via an INJECTED classification (W9 owns the usage-limit discriminator).
- `await_job(handle, *, poll_s, timeout_s)` (AC-E4, CF-9/CF-12): poll for a schema+identity-VALID
  `observation.json` INDEPENDENT of `.incomplete`. On valid → collect ⇒ kill (both groups; kill-fail →
  `completed_with_residue` + hand to reaper). On timeout → the CF-17 two-group kill, then re-check for a
  valid sentinel AFTER the kill and prefer the child's result; the supervisor's timeout obs NEVER
  overwrites a validated child obs (the obs-writer IS the pane_runner, in the pane group, killed before the
  re-check — race-free). Malformed obs → `exited_no_sentinel`.
- `cancel(handle)`: CF-17 two-group kill + `finalize` (dirty retained); release the permit.
- `recover(run_id)` (OQ-8, CF-6/CF-7/CF-10): per record, `classify_recovery` → adopt (re-attach) /
  quarantine (**kill both groups + `_retain`**) / relaunch (`--resume` from the worktree cwd,
  `resume_attempts += 1` capped at `MAX_RESUME`; **machine-signal identity assert** — wrong-cwd = exit≠0 +
  no valid resumed JSON envelope (primary), a drift-guard canary pins the string, and on SUCCESS the
  resumed `session_id` MUST equal the persisted `provider_session_id`) / fail (cap → `failed`).
- `reap(run_id=None)` (AC-E6, CF-8/CF-17): snapshot `list-sessions` (exact-segment run scope, CF-11) +
  the registry; `reap_plan` gating kill on both-group confirmed-death then clean; kill finalized sessions;
  a wedged known+live+stale-mtime+no-sentinel job → kill both groups, verify dead, `_retain`;
  quarantined/unknown → kill past `max_age` (never a live+fresh-mtime one); W3 retention on dirty
  worktrees + age-bound capture dirs; release orphaned permits (CF-19); owner-visible quarantine list.
- `run_seat_tmux(...)`: sync OQ-5 wrapper = launch + await_job (CF-13-tested).

### B.3 Scope fences

**Genuine usage-limit exit-1 discriminator** → W9 #472 (status defaults ambiguous exits to
`exited_no_sentinel`; only an INJECTED classification enters `quota_paused`; the AC4 stub injects it to
exercise the resume CYCLE). Native `--tmux` OUT (#453). AC-I telemetry fields → W6; AC-J status surface →
W8/W11; skill rewiring to CALL `run_seat_tmux` → W7 #470. AC-E5 concurrency IS in W4 (launch-side permit).

## Tests (AC-1..5)

- pure (registry): `session_name` (component_for, no collision, exact-segment scope); `command_digest`;
  `handle_from_record` round-trip; `classify_recovery` matrix (full→adopt, mismatch→quarantine,
  quota+under-cap→relaunch, quota+cap→fail); `reap_plan` tiers (finalized→kill; live+fresh-mtime→keep;
  wedged→kill+retain; quarantined+dead+clean+aged→kill; quarantined+DIRTY→retain; **kill gated on both-group
  confirmed-death BEFORE clean**); registry round-trip atomic; `resolve_socket` rejects /tmp + >108B +
  falls back when /run/user absent.
- integration (real tmux 3.4 private socket, stub pane_runner + a stub provider that start_new_sessions):
  `preflight` positive + NEGATIVE (missing verb / unusable socket dir → supported=False); `launch`
  resolves+acquires the permit, runs the argv as the pane process (sentinel, NOT capture-pane), records
  pane_pid/pane_pgid + the surfaced **provider_pgid**, invisible on the default server; **two-group kill:
  a stub whose provider is in its own group → SIGTERM pane_runner's handler kills the provider group
  (graceful), AND a SIGKILL'd pane_runner → supervisor kills the surfaced provider pgid (both probed live)**;
  verify-dead scans both groups (Z-state = dead); collect⇒kill zero residue; a valid obs with `.incomplete`
  present → collected; a timeout where the child wrote a valid obs → child's obs wins; malformed obs →
  `exited_no_sentinel`; nonzero-exit-no-sentinel → `exited_no_sentinel` (NOT auto-resumed); INJECTED
  `quota_paused` → `recover` relaunches with the machine-signal identity assert (wrong-cwd → LOUD),
  capped → `failed`; identity mismatch → kill both + retain; `reap` kills a finalized orphan, retains a
  dirty orphan, keeps a live+fresh-mtime session, releases an orphaned permit; `cancel` kills+finalizes;
  `run_seat_tmux` round-trip; `status` derivation; AC-E5 permit held launch→confirmed-death (no leak on a
  killed pane).
- Suite green vs baseline **3634 pass / 10 skip** (the `/10` is the skip count, not failures).
