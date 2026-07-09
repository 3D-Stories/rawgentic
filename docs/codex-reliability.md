# Codex reliability: consult routing, dead-job protocol, host runbook

Companion doc for issue #334. The 2026-07-09 failure pair (a Codex "thought partner"
dispatch that lost its shell, then hung >21 minutes on a connector call) was NOT a
missing-code problem — the timeout-enforced consult path already existed and was
tested. It was a routing/guidance gap. This doc is the one home for the three rules
that close it. The sentences below are drift-guarded by
`tests/test_codex_reliability_doc.py` — edit them only with the guard.

## 1. Routing rule (which Codex path to use)

The plugin ships THREE sanctioned, timeout-enforced Codex entry points, all backed by
`hooks/adversarial_review_lib.py` (hard `RAWGENTIC_ADV_REVIEW_TIMEOUT` subprocess
timeout, default 600s; fail-closed exit contract 0/2/3/4; tools-OFF prompt so the
Codex sandbox is never exercised):

| Need | Path |
|---|---|
| Critique an artifact (find flaws) | `/rawgentic:adversarial-review` (WF5) → `review` subcommand |
| Independent peer PROPOSAL / thought partner | `/rawgentic:peer-consult` (WF13) → `consult` subcommand |
| Embedded review inside WF2/WF3 gates | the same engine, invoked by the workflow step |

**The rule:** when cross-model consult or thought-partner input is load-bearing,
route it through `/rawgentic:peer-consult` (WF13) or the
`adversarial_review_lib.py consult` CLI — never a bare `codex:codex-rescue` dispatch.
The rescue path (third-party `openai-codex` plugin, `codex-companion.mjs`) is fine for
opportunistic, non-blocking side work, but it has NO wall-clock watchdog: its only
timeout is a 240s status-poll wait (`codex-companion.mjs:69`), the job itself can run
(or hang) forever, and when the Codex local sandbox is unavailable it silently
degrades to connector-only mode where a single stuck connector call stalls the whole
job (observed: >21 min silent `fetch_file`, 2026-07-09).

## 2. Dead-job protocol (when you dispatch companion/rescue anyway)

Give every companion/rescue dispatch an explicit deadline up front (wall-clock, e.g.
the engine's own 600s default is a sane ceiling). Then:
a companion or rescue job silent past its deadline is DEAD: kill it and
substitute — never keep waiting. Substitution order mirrors the review-gate rule
(workspace manual "Reviews and second opinions"): an independent Opus subagent
(`deep-reasoner` for consults, `rawgentic:rawgentic-reviewer` for reviews), stated in
the deliverable's provenance. "Silent" means no new output artifact/transcript bytes —
check the job's output file mtime, not just its status field. This mirrors the
dead-agent rule for Claude subagents (`confirmedCount: 0` + empty body = dead, see
issue #331): a return (or a wait) that produced nothing is a failure, not a pass.

## 3. Host runbook: Codex sandbox dead on Ubuntu 24.04 (userns/AppArmor)

**Signature.** Any bwrap consumer (Codex CLI's Linux sandbox included) fails
immediately with:

```
bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted
```

and the kernel audit log (`dmesg` / `journalctl -k`) shows the mechanism:

```
apparmor="AUDIT" operation="userns_create" ... comm="bwrap" target="unprivileged_userns"
apparmor="DENIED" operation="open" ... profile="unprivileged_userns" name="proc/<pid>/uid_map" denied_mask="wr"
```

**Cause.** Ubuntu 24.04 ships `kernel.apparmor_restrict_unprivileged_userns = 1`:
any unconfined binary creating a user namespace is transitioned into the restrictive
`unprivileged_userns` AppArmor profile, which denies the `uid_map` write and the
netns loopback setup bwrap needs. Not Codex-specific.

**Fix (field-tested 2026-07-09; needs sudo).** Install a local AppArmor profile
granting `userns` to bwrap, then reload:

```bash
printf 'abi <abi/4.0>,\ninclude <tunables/global>\nprofile bwrap /usr/bin/bwrap flags=(unconfined) {\n  userns,\n  include if exists <local/bwrap>\n}\n' \
  | sudo tee /etc/apparmor.d/bwrap && sudo apparmor_parser -r /etc/apparmor.d/bwrap
```

**Verify:** `codex sandbox bash -c 'echo ok'` prints `ok`, and `aa-status` lists a
`bwrap` profile.

**Rejected alternative:** `sysctl kernel.apparmor_restrict_unprivileged_userns=0`
disables the restriction machine-wide for every process — strictly weaker; do not use.

## Provenance

RCA and evidence: issue #334 (kernel-audit capture, reproduction, companion-runtime
line anchors) and `docs/reviews/334-rca-md-2026-07-09.md` (adversarial review of the
RCA). Related: #331 (review-gate fallback + dead-agent detection), #330 (dispatch
telemetry — consult dispatches emit the same audit line), PR #328 (the audit that
first hit the failure).
