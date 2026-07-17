# Adversarial Review — .rawgentic-424-diff.patch

- Date: 2026-07-17
- Artifact type: diff
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 12 (Critical 0, High 12, Medium 0, Low 0)

## Summary

The change introduces a model-seat execution engine with routing, quotas, provider adapters, captures, and a normative observation schema. Multiple policy, identity, quota, and response-integrity checks fail open or can be bypassed through public defaults.

## Findings

### 1. [High] correctness · high confidence — adapters/base.py: resolve_parse_status

> +    if not parsed.usage or "input" not in parsed.usage or "output" not in parsed.usage:
> +        return contract.USAGE_UNAVAILABLE
> +    return contract.OK

Success resolution never requires an actual response payload. Claude or ZhipuAI envelopes with valid identity and usage but missing/empty content, and Codex streams with usage but no agent message, are marked `ok`; competitive judging can consequently select a candidate that returned no answer.

**Recommendation:** Add an explicit response-presence field to `ParsedResult` and require a provider completion event plus a non-null output payload before returning `OK`. Return `PARSE_ERROR` or a dedicated `EMPTY_RESPONSE` non-success status otherwise.

### 2. [High] correctness · high confidence — engine.py: AVAILABILITY_FAILURES

> +AVAILABILITY_FAILURES = frozenset({contract.LAUNCH_ERROR, contract.NONZERO_EXIT, contract.TIMEOUT})

Empty stdout and a stream containing no parseable provider events are classified as `parse_error`, but `parse_error` is excluded from fallback. Thus a no-response primary stops the chain even though the engine's rationale for not falling back—“the model responded”—is false.

**Recommendation:** Distinguish `NO_RESPONSE`/transport-envelope failures from semantic parse failures and include the no-response status in `AVAILABILITY_FAILURES`; retain non-fallback behavior only when a provider response was positively observed.

### 3. [High] correctness · high confidence — engine.py: run_competitive future collection

> +            results[i] = fut.result()  # _run_candidate returns a (possibly non-ok) Observation; a raise here is a real bug

Any candidate exception escapes immediately from `fut.result()`, so remaining results are never collected and the judge/failure strategy is never invoked. This contradicts the stated behavior that candidate exceptions surface as result errors and judging proceeds among completed candidates.

**Recommendation:** Catch each future exception, convert it into a `harness_error` Observation for that candidate, continue collecting all futures, and pass the complete result list to the judge or failure strategy.

### 4. [High] correctness · high confidence — adapters/zhipuai_sdk.py: _invoke_worker

> +    for locked in (True, False):
> +        try:
> +            r = subprocess.run(_uv_command(locked), input=payload, capture_output=True, text=True, timeout=timeout)
> +        except (OSError, subprocess.TimeoutExpired) as exc:
> +            last = ProcOutcome(returncode=None, stdout="", stderr=str(exc), timed_out=isinstance(exc, subprocess.TimeoutExpired), launch_error=str(exc))
> +            continue
> +        outcome = ProcOutcome(returncode=r.returncode, stdout=r.stdout, stderr=r.stderr, timed_out=False)
> +        if r.returncode == 0 and r.stdout.strip():
> +            return outcome
> +        last = outcome

The unlocked fallback runs after every nonzero or empty-output result, not only when the locked environment is unavailable. If the first worker reached the provider but returned an API/auth/quota error, the same request is issued a second time, potentially duplicating a billable or stateful operation; two timeouts can also consume twice the requested timeout.

**Recommendation:** Fall back to the unlocked environment only for a positively identified local dependency/lock-resolution failure before worker execution. Do not retry provider or worker errors, and enforce one shared deadline across both setup attempts.

### 5. [High] correctness · high confidence — schemas/observation.schema.json: process and allOf

> +        "exit_code": {"type": ["integer", "null"]},
> +        "timed_out": {"type": "boolean"}

The normative schema does not couple process outcome to `parse_status`. It validates an `ok` observation with `exit_code` null or nonzero and `timed_out: true`, allowing another producer to publish an internally impossible success that downstream consumers must nevertheless accept.

**Recommendation:** Extend `observation.schema.json` so `parse_status == "ok"` requires `process.exit_code == 0` and `process.timed_out == false`; add corresponding conditionals for timeout and launch-error states and negative validation tests.

### 6. [High] security · high confidence — engine.py: _run_candidate and run_competitive

> +def _run_candidate(c: Candidate, *, snapshot, quota, capture_root, run_id, dispatch) -> contract.Observation:
> +    req = AdapterRequest(
> +        seat=c.seat, requested_model=c.model, prompt=c.prompt, transport=c.transport,
> +        context=tuple(c.context), credential_ref=c.credential_ref,
> +    )

Competitive candidates are dispatched directly without applying `eligible_targets` or `target_forbidden_reason`; `snapshot` is not used for eligibility. A competitive call can therefore execute Haiku or a same-provider reviewer despite the stated never-Haiku and cross-model-author invariants.

**Recommendation:** Change `_run_candidate`/`run_competitive` to require `author_provider`, resolve every candidate through the snapshot's seat targets, and reject it before quota acquisition unless `target_forbidden_reason(...)` returns null.

### 7. [High] security · high confidence — routing.py: _row_matches

> +    if row.get("rule") == "cross_model_author":
> +        return author_provider is not None and lane["provider"] == author_provider

The cross-model guard explicitly evaluates to false when author provenance is absent, while `run_seat` defaults `author_provider` to null. Callers can omit the field and route a reviewer to the author's provider, violating the shipped rule that this must never happen.

**Recommendation:** When a snapshot contains a `cross_model_author` rule, require a non-empty authenticated `author_provider` in `run_seat` and `run_competitive`; raise `RoutingError` if it is absent instead of treating the target as eligible.

### 8. [High] security · high confidence — quota.py: QuotaCoordinator.acquire; engine.py: Candidate

> +        limit = self.limits.get(pool)
> +        if limit is None:
> +            yield None  # unbounded pool: no gating
> +            return

Unknown pool names disable quota enforcement. Because public `Candidate.pool` defaults to an empty string and competitive candidates are not checked against snapshot pools, an omitted or invented pool bypasses the concurrency ceiling completely.

**Recommendation:** Make `Candidate.pool` required and non-empty, validate every competitive candidate pool against `snapshot.table["pools"]`, and make `QuotaCoordinator.acquire` raise for unknown pools unless an explicit `allow_unbounded=True` mode was configured.

### 9. [High] security · high confidence — adapters/claude_cli.py: run; corresponding Codex and ZhipuAI run paths

> +    cmd = build_command(req.requested_model, effort=req.effort)

`credential_ref` is used to select a separate quota account but is not applied to the Claude command or environment; the Codex and ZhipuAI adapters likewise do not bind execution to it. Different references can therefore shard the quota while all calls still use the same ambient credential, and routing can execute under a credential different from the one recorded.

**Recommendation:** In every adapter `run`, resolve `credential_ref` through an allowlisted credential configuration and apply that configuration to the subprocess/SDK environment. Derive the quota account from the resolved credential identity, not directly from caller-controlled text, and reject unresolved references.

### 10. [High] security · high confidence — adapters/codex_cli.py: parse_codex

> +        actual = requested_model  # pinned -m, direct connection, no proxy substitution

The Codex adapter fabricates identity evidence by copying the requested model into `actual_model`. This directly contradicts the normative contract's provider-reported innermost-ID requirement and allows a substituted or aliased upstream model to be recorded as verified `ok`.

**Recommendation:** Remove the assignment from `parse_codex`. Until the CLI supplies provider-reported model evidence, return `actual_model=None` and `identity_failure`, or obtain independently authenticated model identity from a supported provider envelope.

### 11. [High] security · high confidence — routing.py: RoutingSnapshot

> +@dataclass(frozen=True)
> +class RoutingSnapshot:
> +    """An immutable routing epoch: the table plus its digest."""
> +    table: dict

The frozen dataclass still holds a mutable dictionary by reference. A caller can mutate targets, forbidden rules, or pool limits after digest calculation, changing routing behavior under the old audited digest and bypassing an in-flight snapshot's policy.

**Recommendation:** In `RoutingSnapshot.from_table`, deep-copy and recursively freeze the validated table, or store canonical immutable data and return defensive copies; compute the digest from that frozen copy.

### 12. [High] security · high confidence — quota.py: _reap_stale

> +            stale = (not _pid_alive(pid))
> +            if not stale:
> +                try:
> +                    stale = (now - token.stat().st_mtime) > self.stale_after
> +                except OSError:
> +                    stale = True

A permit is reaped solely because its file is old even when its holder PID is confirmed alive. Any invocation lasting longer than `stale_after` loses its live permit, allowing another process to acquire the slot and exceed the promised cross-process ceiling.

**Recommendation:** In `_reap_stale`, never age-reap a token whose holder identity is still valid. Store a process-start identity to defeat PID reuse, or add a heartbeat/lease-renewal mechanism and reap only expired leases whose holder cannot renew.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._