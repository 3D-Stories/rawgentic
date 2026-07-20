#!/usr/bin/env python3
"""WF2 resumption step-detection for the implement-feature workflow skill.

WF2 may span multiple Claude Code sessions (context compaction, fresh headless
sessions, worktrees). On every resume the orchestrator must decide which of the
16 steps to re-enter. That decision is a *priority-ordered cascade*: a merged PR
resumes at post-deploy verification even if a stale design doc also exists; a
branch with code changes resumes at the drift check even if the issue was
validated earlier. Applying that ordering by hand in prose is exactly the kind
of fragile interpretation that lets a resume silently land on the wrong step —
redoing finished work, or skipping a quality gate.

This module encodes the cascade once, as a tested pure function, and exposes it
as a `detect-step` CLI. The orchestrator still *gathers* the facts (git/gh for
PR + branch state, session notes for design/issue/test status — the inherently
environmental and semantic parts); this function just applies the canonical
precedence to those facts so the resume target cannot drift from the order the
workflow intends.
"""
import argparse
import sys


# The three fact dimensions, each a small closed enum. Composite enums (rather
# than a pile of booleans) make within-dimension contradictions inexpressible —
# you cannot say "branch has changes but no branch exists" — and let argparse
# `choices=` reject anything unrecognized at the CLI boundary, fail-closed.

# PR dimension. Resume meaning, highest precedence first:
#   merged          -> PR merged                                  -> Step 15
#   ready-to-merge  -> PR open AND (CI green OR project has no CI) -> Step 14
#   open            -> PR open AND CI not yet green                -> Step 13
#   none            -> no PR for this branch                       -> (fall through)
# The orchestrator collapses the prose rule "CI passed (or no CI)" into
# `ready-to-merge` while gathering facts, so this function needs no CI flag.
PR_STATES = ("none", "open", "ready-to-merge", "merged")

# Branch dimension (consulted only when there is no PR):
#   verified  -> branch has commits AND tests pass/verified -> Step 11
#   changes   -> branch has commits, tests not yet verified -> Step 9
#   empty     -> branch exists with no commits              -> Step 8
#   none      -> no feature branch                          -> (fall through)
BRANCH_STATES = ("none", "empty", "changes", "verified")

# Session-notes dimension (consulted only when there is no PR and no branch):
#   design-doc       -> a design document is recorded in notes -> Step 5
#   issue-validated  -> issue validated, no design yet         -> Step 2
#   none             -> neither                                -> Step 1
NOTES_STATES = ("none", "issue-validated", "design-doc")

# Sentinel returned for resumption rule 0: every step marker is present but the
# completion gate was never printed. The workflow is effectively done — run the
# gate and terminate rather than redo Step 15.
COMPLETION_GATE = "completion-gate"

# Executor JobRegistry dimension (#470). This dimension is ADVISORY: it NEVER
# changes the resume step (the step cascade above is the single source of the
# ordering). It only surfaces what the resuming orchestrator must do about live
# executor jobs before re-dispatching a seat. ABSENT is the pre-#470 default —
# a caller that omits it gets byte-identical behavior (no advisory).
#   absent     -> no executor registry dir for this run (pre-#470)  -> no advisory
#   none-live  -> registry present, no live jobs                    -> "resume normally" note
#   live-jobs  -> registry present, live jobs for this run_id       -> recover-adopt advisory
REGISTRY_STATES = ("absent", "none-live", "live-jobs")


def detect_resume_step(
    pr_state: str,
    branch_state: str,
    notes_state: str,
    *,
    markers_complete: bool = False,
    completion_gate_printed: bool = False,
    headless: bool = False,
) -> int | str:
    """Return the WF2 step to resume at given the gathered facts.

    Returns an int step (1, 2, 5, 8, 9, 11, 13, 14, 15, 16) or the
    ``COMPLETION_GATE`` sentinel. The mapping is total over all valid inputs:
    there is no input that falls through to an undefined result. An *unrecognized*
    state raises ``ValueError`` rather than defaulting to Step 1 — silently
    restarting an in-flight workflow because a fact was mistyped would be worse
    than failing loudly.

    ``headless`` (issue #47): in headless mode WF2 is PR-terminal — it never
    merges or deploys. A ``ready-to-merge`` PR (which non-headless would merge at
    Step 14) and a ``merged`` PR (whose post-deploy at Step 15 is meaningless when
    the bot performed no deploy) both resume at Step 16 (completion). ``open`` is
    deliberately NOT remapped: the bot may still fix CI by pushing to its own PR
    branch (a local op, not remote access), so it stays at Step 13.
    """
    for name, value, valid in (
        ("pr_state", pr_state, PR_STATES),
        ("branch_state", branch_state, BRANCH_STATES),
        ("notes_state", notes_state, NOTES_STATES),
    ):
        if value not in valid:
            raise ValueError(
                f"invalid {name} {value!r} (expected one of {list(valid)})"
            )

    # Rule 0 (precedence above everything except the headless check, which the
    # skill runs first in prose): a fully-marked run that never printed its gate.
    if markers_complete and not completion_gate_printed:
        return COMPLETION_GATE

    # PR cascade (rules 1-3) — a PR outranks branch/notes state.
    # Headless (issue #47) is PR-terminal: ready-to-merge and merged both collapse
    # to Step 16 (no merge, no deploy, no post-deploy). `open` is unchanged so the
    # bot can still push CI fixes (a local op) before completing.
    if pr_state == "merged":
        return 16 if headless else 15
    if pr_state == "ready-to-merge":
        return 16 if headless else 14
    if pr_state == "open":
        return 13

    # Branch cascade (rules 4-6) — only reached when there is no PR.
    if branch_state == "verified":
        return 11
    if branch_state == "changes":
        return 9
    if branch_state == "empty":
        return 8

    # Notes cascade (rules 7-8) — only reached when there is no PR and no branch.
    if notes_state == "design-doc":
        return 5
    if notes_state == "issue-validated":
        return 2

    # Rule 9: nothing detected — start from the top.
    return 1


def registry_advisory(registry_state: str) -> str | None:
    """Return the executor-JobRegistry advisory for a resume, or None (#470).

    This is ADDITIVE to step detection — it NEVER changes the resume step (the
    step cascade is the single source of the ordering); it only tells the
    resuming orchestrator what to do about live executor jobs before it
    re-dispatches any seat. ``absent`` (the pre-#470 default) returns None so a
    caller that omits the registry state gets byte-identical output.

    ``none-live`` returns a "resume normally" note; ``live-jobs`` returns the
    recover-adopt advisory naming ``supervisor.recover(run_id)`` (tmux session
    identity is the adoption key; identity-matched jobs are ADOPTED with the D-12
    permit re-established under the adopting pid, mismatches QUARANTINED), which
    must run BEFORE re-dispatching a seat. An unrecognized state raises
    ``ValueError`` (fail-closed at the boundary, matching the step-cascade enums)
    rather than silently dropping the advisory.
    """
    if registry_state not in REGISTRY_STATES:
        raise ValueError(
            f"invalid registry_state {registry_state!r} "
            f"(expected one of {list(REGISTRY_STATES)})"
        )
    if registry_state == "absent":
        return None
    if registry_state == "none-live":
        return (
            "registry advisory: executor JobRegistry present, no live executor "
            "jobs for this run_id — resume normally."
        )
    # live-jobs
    return (
        "registry advisory: live executor jobs for this run_id — run "
        "supervisor.recover(run_id) BEFORE re-dispatching any seat. "
        "Identity-matched jobs (tmux session identity is the adoption key) are "
        "ADOPTED (D-12 quota permit re-established under the adopting pid); "
        "mismatches are QUARANTINED (surfaced to the user, never adopted)."
    )


def main(argv=None) -> int:
    """CLI entry point.

    Subcommand:
      detect-step  print the WF2 step to resume at (or `completion-gate`)

    Exit codes:
      0  success — the step (or sentinel) is printed to stdout
      1  invalid fact combination (should be unreachable from the CLI because
         argparse `choices=` validates the enums, but kept so the boundary is
         fail-closed if the function ever rejects an input)
      2  argparse usage error (unknown/invalid flag) — also fail-closed
    """
    parser = argparse.ArgumentParser(prog="resume_lib")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser(
        "detect-step",
        help="print the WF2 step to resume at given the gathered facts",
    )
    p.add_argument("--pr-state", required=True, choices=list(PR_STATES),
                   dest="pr_state")
    p.add_argument("--branch-state", required=True, choices=list(BRANCH_STATES),
                   dest="branch_state")
    p.add_argument("--notes-state", required=True, choices=list(NOTES_STATES),
                   dest="notes_state")
    # Explicit boolean VALUE flags (not store_true): the completion-gate rule is
    # part of the deterministic invocation, so the skill's command always passes
    # them rather than conditionally appending a bare flag in prose. `choices`
    # rejects a typo'd value (fail-closed) instead of treating it as false.
    p.add_argument("--markers-complete", choices=["true", "false"], default="false",
                   dest="markers_complete",
                   help="whether all WF2 step markers are present in session notes")
    p.add_argument("--completion-gate-printed", choices=["true", "false"],
                   default="false", dest="completion_gate_printed",
                   help="whether the completion gate was already printed")
    p.add_argument("--headless", choices=["true", "false"], default="false",
                   dest="headless",
                   help="whether running in headless mode (PR-terminal: "
                        "ready-to-merge/merged resume at Step 16, no merge/deploy)")
    # Executor JobRegistry state (#470). Optional; ABSENT (default) is the
    # pre-#470 behavior — byte-identical output, no advisory. When present, an
    # ADVISORY line is written to STDERR so stdout stays the bare step for
    # `STEP=$(... detect-step ...)` capture; the step itself never changes.
    p.add_argument("--registry-state", choices=list(REGISTRY_STATES),
                   default="absent", dest="registry_state",
                   help="executor JobRegistry state for this run_id; ADVISORY "
                        "only (never changes the step). live-jobs => recover-adopt "
                        "advisory on stderr before re-dispatching a seat")

    args = parser.parse_args(argv)

    if args.cmd == "detect-step":
        try:
            step = detect_resume_step(
                args.pr_state, args.branch_state, args.notes_state,
                markers_complete=(args.markers_complete == "true"),
                completion_gate_printed=(args.completion_gate_printed == "true"),
                headless=(args.headless == "true"),
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(step)
        # Registry advisory (#470): stderr only, so stdout stays the bare step.
        advisory = registry_advisory(args.registry_state)
        if advisory is not None:
            print(advisory, file=sys.stderr)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
