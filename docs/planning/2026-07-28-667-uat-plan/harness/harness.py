#!/usr/bin/env python3
"""Epic #667 UAT harness — record a verdict per check, with its evidence.

Deliberately not a test framework. Each check is a shell command plus a
predicate on its output; the harness's whole job is that a verdict can never be
recorded without the evidence that produced it, and that re-reading is possible
without re-running. Results append to results.jsonl; raw output goes to
evidence/<id>.txt.

  harness.py record <id> <verdict> <note> [--evidence FILE]
  harness.py run <id> <note> -- <command...>       # PASS iff exit 0
  harness.py summary
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(ROOT, "results.jsonl")
EVIDENCE = os.path.join(ROOT, "evidence")

VERDICTS = ("PASS", "FAIL", "BLOCKED", "INCONCLUSIVE")

# id -> (tier, one-line claim being tested). Kept here so the summary can name
# every check that has NOT run — a harness that only lists what it tried reads
# as complete when it isn't.
CHECKS = {
    "P1": (0, "A Claude pane reports agent_status=blocked on a permission prompt"),
    "P2": (0, "An event frame carries the transition; revision advances"),
    "P3": (0, "blocked clears when the prompt is answered"),
    "W1": (1, "--sender-cmd exists so the watcher is testable without paging"),
    "W2": (1, "Exactly one notification per block transition"),
    "W3": (1, "Re-observing the same block does not re-notify"),
    "W4": (1, "Unblock then re-block does notify again"),
    "W5": (1, "No pane content in the notification body"),
    "W6": (1, "Heartbeat advances; stall warning fires past its window"),
    "W7": (1, "Startup sweep catches an already-blocked pane"),
    "H1": (2, "Scratch campaign fixture loads"),
    "H2": (2, "agent_pane_busy reproduces; readiness wait fixes it"),
    "H3": (2, "Successor spawns, gets the goal, binds the project"),
    "H4": (2, "retire-predecessor clears the guard and closes the pane"),
    "H5": (2, "The predecessor's Stop releases"),
    "H6": (2, "Ladder refuses teardown while successor checks are unmet"),
    "V1": (3, "A fake herdr 0.7.4 FAILS the version check"),
    "V2": (3, "Installed binary sha256 matches the release digest"),
    "L1": (4, "Read-only launcher subcommands return sane output"),
    "L2": (4, "handoff runs live in fresh-session mode on a throwaway"),
    "D1": (5, "Every runbook command executes verbatim"),
    "D2": (5, "The #654 Q1 context measurement reproduces"),
    "R1": (6, "Whole suite + both lint lanes green"),
    "R2": (6, "The driver_bench parallel-run race reproduces (or is downgraded)"),
    # Converted from human-only to agent-runnable, 2026-07-28. Each was an agent-checkable FACT
    # welded to an owner JUDGEMENT; the judgement halves (W8b, V3b, L3b, C8) were split out and stay
    # the owner's, deliberately NOT registered here — an unregistered id cannot be recorded, so the
    # agent can never quietly answer them for them.
    "W8a": (1, "The notification is DELIVERED, not merely sent (transport success, not an exit code)"),
    "H7": (2, "A real authorised handoff on a live epic child (owner grant D-3)"),
    "V3a": (3, "0.7.6 compatibility evidence: both pinned gates re-checked against it"),
    "L3a": (4, "A per-item recommendation with evidence for #611's deferred ACs and #666's premise"),
    "D3": (5, "A cold reader with no authoring context follows the runbook"),
    "R3": (6, "Plugin reinstall; a fresh session loads the shipped version"),
    # Tier 7 — #687's context-pressure trigger. VOID until #687 merges.
    "C1": (7, "The meter's context reading agrees with an independently computed one"),
    "C2": (7, "A threshold crossing emits once per tier, not once per turn"),
    "C3": (7, "Cadence: at most one check per 5 turns or 5 minutes, whichever comes first"),
    "C4": (7, "A mid-phase break is refused; a step-boundary break fires"),
    "C5": (7, "Thresholds are relative: the same count tiers differently on 200k vs 1M"),
    "C6": (7, "Fail-open: an absent or corrupt transcript emits nothing and blocks nothing"),
    "C7": (7, "End to end: handoff written, successor bound/prompted/goaled, predecessor retired last"),
}


def _append(rec):
    os.makedirs(EVIDENCE, exist_ok=True)
    with open(RESULTS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def record(check_id, verdict, note, evidence=None):
    if verdict not in VERDICTS:
        print(f"verdict must be one of {VERDICTS}", file=sys.stderr)
        return 2
    if check_id not in CHECKS:
        print(f"unknown check id {check_id}", file=sys.stderr)
        return 2
    tier, claim = CHECKS[check_id]
    _append({"id": check_id, "tier": tier, "claim": claim, "verdict": verdict,
             "note": note, "evidence": evidence, "ts": time.time()})
    print(f"{verdict:13} {check_id}  {note}")
    return 0


def run(check_id, note, cmd):
    """PASS iff the command exits 0. Output always lands in evidence/."""
    if check_id not in CHECKS:
        print(f"unknown check id {check_id}", file=sys.stderr)
        return 2
    os.makedirs(EVIDENCE, exist_ok=True)
    ev = os.path.join(EVIDENCE, f"{check_id}.txt")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    with open(ev, "w", encoding="utf-8") as fh:
        fh.write(f"$ {' '.join(cmd)}\n--- rc={proc.returncode} ---\n")
        fh.write(proc.stdout)
        if proc.stderr:
            fh.write("\n--- stderr ---\n" + proc.stderr)
    return record(check_id, "PASS" if proc.returncode == 0 else "FAIL",
                  f"{note} (rc={proc.returncode})", ev)


def summary():
    latest = {}
    if os.path.exists(RESULTS):
        with open(RESULTS, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    latest[rec["id"]] = rec      # last write wins
    by_tier = {}
    for cid, (tier, claim) in CHECKS.items():
        by_tier.setdefault(tier, []).append(cid)
    counts = {v: 0 for v in VERDICTS}
    counts["NOT RUN"] = 0
    print(f"\n  epic #667 UAT — {len(latest)}/{len(CHECKS)} checks recorded\n")
    for tier in sorted(by_tier):
        print(f"  ── tier {tier} ──")
        for cid in sorted(by_tier[tier]):
            rec = latest.get(cid)
            verdict = rec["verdict"] if rec else "NOT RUN"
            counts[verdict] = counts.get(verdict, 0) + 1
            note = f" — {rec['note']}" if rec else ""
            print(f"    {verdict:13} {cid}  {CHECKS[cid][1]}{note}")
        print()
    print("  " + "  ".join(f"{k}={v}" for k, v in counts.items() if v))
    # Exit non-zero while anything is unresolved, so a caller cannot mistake a
    # partial run for a clean one.
    return 1 if (counts.get("FAIL") or counts.get("NOT RUN")) else 0


def main(argv):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    verb = argv[1]
    if verb == "summary":
        return summary()
    if verb == "record":
        ev = None
        args = argv[2:]
        if "--evidence" in args:
            i = args.index("--evidence")
            ev = args[i + 1]
            args = args[:i] + args[i + 2:]
        if len(args) < 3:
            print("record <id> <verdict> <note>", file=sys.stderr)
            return 2
        return record(args[0], args[1], " ".join(args[2:]), ev)
    if verb == "run":
        if "--" not in argv:
            print("run <id> <note> -- <command...>", file=sys.stderr)
            return 2
        sep = argv.index("--")
        head, cmd = argv[2:sep], argv[sep + 1:]
        if len(head) < 2 or not cmd:
            print("run <id> <note> -- <command...>", file=sys.stderr)
            return 2
        return run(head[0], " ".join(head[1:]), cmd)
    print(f"unknown verb {verb}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
