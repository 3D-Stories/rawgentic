# Adversarial Review — 467-diff.patch

- Date: unknown-date
- Artifact type: diff
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 8 (Critical 1, High 7, Medium 0, Low 0)

## Summary

The change adds a tmux-backed supervisor, durable registry, quota handling, recovery, and process reaping. Several recovery and lifecycle paths fail open, allowing identity mismatches to relaunch, live processes to be classified as dead, and quota permits to be released before confirmed termination.

## Findings

### 1. [Critical] security · high confidence — registry.py — classify_recovery

> +    if record.state == "quota_paused" and not live:
> +        return "relaunch" if record.resume_attempts < MAX_RESUME else "fail"
> +    if live:
> +        return "adopt" if identity_matches else "quarantine"

The quota-paused branch relaunches before consulting `identity_matches` or `sentinel_valid`. A dead record whose digest, worktree, capture identity, or spec is known to mismatch is therefore relaunched instead of quarantined, executing an untrusted or corrupted recovery specification across the stated recovery trust boundary.

**Recommendation:** Change `classify_recovery` ordering to: `if not identity_matches: return "quarantine"`; `if sentinel_valid: return "adopt"`; then evaluate the dead `quota_paused` relaunch/cap branch. Add a test for `quota_paused + dead + identity_matches=False` requiring quarantine.

### 2. [High] correctness · high confidence — supervisor.py — _default_dead_fn

> +        if record.pane_pgid > 1 and record.pane_pid > 1 and _pid_alive(record.pane_pid) \
> +                and _group_pids(record.pane_pgid):
> +            return False

The pane group is inspected only while the original pane PID remains alive. If the group leader exits while another member survives, this condition is skipped and `_default_dead_fn` can report confirmed death, allowing worktree probing, session removal, and permit release while a descendant is still running.

**Recommendation:** Change `_default_dead_fn` so a dead or unverifiable pane leader does not make the pane group vacuously dead. Persist sufficient process identities to verify surviving group members safely; return not-dead/unknown until both groups are positively verified empty, and do not clean or release permits in the unknown state.

### 3. [High] correctness · high confidence — supervisor.py — TmuxSupervisor._finish

> +    def _finish(self, record: JobRecord, state: str, **updates) -> JobRecord:
> +        record = replace(record, state=state, **updates)
> +        self._registry.upsert(record)
> +        self._release_permit(record)
> +        return record

Every finish state releases the quota permit, including `completed_with_residue`, kill-unverified timeouts, and quarantine paths where processes may still be alive. A new provider can consequently acquire the slot before the prior provider's death is confirmed, violating the stated launch-to-confirmed-death concurrency ceiling.

**Recommendation:** Add an explicit `release_permit` argument to `_finish`, defaulting to false. Pass true only after `_kill_job` or `_default_dead_fn` positively confirms death; residue states must retain the permit until the reaper records confirmed death.

### 4. [High] security · high confidence — supervisor.py — _identity_matches

> +        # interpreter-independent digest — must mirror launch()'s argv[1:] computation
> +        if command_digest(["-m", "phase_executor.pane_runner", str(spec_path)]) != record.command_digest:
> +            return False
> +        spec = self._read_spec(record)
> +        if not spec.get("request"):
> +            return False  # unreadable/tampered spec: the digest above covers argv, not content

The purported full identity check hashes only the fixed argv and spec pathname, not the spec contents. Replacing the prompt, engine, capture root, profile grants, session policy, or resume ID while leaving any truthy `request` makes `_identity_matches` return true, so a tampered live job can be adopted.

**Recommendation:** Add a canonical `spec_digest` to `JobRecord`, compute it from the complete serialized fixed spec before launch, and compare it during `_identity_matches`. Also explicitly compare the spec's run, seat, attempt, capture path, worktree/containment, engine, target, and resume identity with the record.

### 5. [High] security · high confidence — supervisor.py — TmuxSupervisor.launch exception handler

> +            self._registry.upsert(record)
> +            self._permits[name] = cm
> +            return record
> +        except BaseException:
> +            cm.__exit__(None, None, None)  # never leak a permit on a failed launch (AC-E5)
> +            raise

A registry write failure occurs after tmux has successfully started the job. The exception handler releases the quota permit but does not terminate or durably record the session, leaving an untracked pane/provider running outside the concurrency ceiling—the exact corrupt-registry orphan path the registry is meant to prevent.

**Recommendation:** Change `launch` to persist a launch-intent record before spawning and update it after obtaining process identities. For every post-spawn exception, retain the permit, kill and verify the spawned process tree, then release the permit; if verification fails, persist an emergency residue record and raise.

### 6. [High] security · high confidence — supervisor.py — TmuxSupervisor.recover quarantine branch

> +            elif verdict == "quarantine":
> +                self._kill_job(record)
> +                reason = ("identity mismatch" if not matches else "no valid sentinel")
> +                done = self._finish(record, "quarantined", quarantine_reason=reason)
> +                self._retain(done)

Recovery ignores `_kill_job`'s verification result and records the job as quarantined regardless. If termination fails, the identity-mismatched writer remains live while recovery claims quarantine and proceeds with retention, violating the stated guarantee that a mismatch never leaves a live writer.

**Recommendation:** Capture the `_kill_job` result in `recover`. Only enter ordinary `quarantined` after verified death; on false, store a distinct `quarantined_with_residue`/`kill_unverified` state, retain the evidence, keep the permit, and schedule mandatory reaper retries.

### 7. [High] security · high confidence — supervisor.py — TmuxSupervisor.mark_quota_paused

> +        record = replace(record, state="quota_paused", provider_session_id=provider_session_id)
> +        self._registry.upsert(record)
> +        # the provider exited (usage-limit exit-1) — free the pool slot NOW, else the
> +        # relaunch under the same session_name strands the old permit context manager
> +        # and deadlocks a concurrency-1 pool on the job's own permit (8a R2 finding)
> +        self._release_permit(record)

`mark_quota_paused` performs no state, liveness, sentinel, or session-ID validation before releasing the permit. It can reclassify a live or already-completed job, admit concurrent work while the provider is still running, or relaunch a completed mutating operation and duplicate its side effects; the included lifecycle test explicitly transitions a completed job this way.

**Recommendation:** Restrict `mark_quota_paused` to a dead `exited_no_sentinel` record with no valid sentinel, a nonempty validated provider session ID, and injected usage-limit evidence tied to that attempt. Reject live, completed, timed-out, failed, or already-paused records without changing state or releasing the permit.

### 8. [High] security · high confidence — supervisor.py — TmuxSupervisor.launch JobRecord construction and await_job resume check

> +                provider_session_id=None, provider_exit_code=None,

`launch` clears `provider_session_id` even for `_relaunch`, which supplies the persisted resume ID. The new durable record therefore loses the expected session identity, while `await_job` checks identity only when a caller separately supplies its optional `expect_session_id`; a fresh or wrong resumed session can be accepted as completed through the normal recovered-record flow.

**Recommendation:** Set the launched record's `provider_session_id` to `resume_session_id`. For every record with `resume_attempts > 0`, make `await_job` automatically require that stored value, reject a missing value before launch, and compare it with the transport session ID without relying on a caller-provided optional argument.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._