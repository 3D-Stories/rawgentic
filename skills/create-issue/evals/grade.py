#!/usr/bin/env python3
"""Programmatic grader for create-issue evals.

    grade.py --scenario <name> --fixture <dir> --transcript <file> [--out grading.json]

Every check reads the gh-mock's recorded state (<fixture>/.gh-mock/) and/or the
run transcript. Because the mock only writes created-issue.json when a real
`gh issue create` ran, the liveness checks FAIL on a simulated walkthrough --
which is exactly the failure the prior eval set could not produce.

grading.json uses the field names the eval viewer expects: text/passed/evidence.
"""
import argparse
import json
import os
import re
import sys

CONFIG_REPO = "octo-eval/sentinel-app"
DECOY = "sentinel-legacy"
SEED_ISSUE_NUM = "42"
SEED_ISSUE_HINT = "websocket"


def load(fixture):
    st = os.path.join(fixture, ".gh-mock")
    created = None
    cp = os.path.join(st, "created-issue.json")
    if os.path.exists(cp):
        try:
            created = json.load(open(cp))
        except Exception:
            created = None
    body = ""
    bp = os.path.join(st, "created-issue-body.md")
    if os.path.exists(bp):
        body = open(bp).read()
    calls = ""
    clp = os.path.join(st, "calls.log")
    if os.path.exists(clp):
        calls = open(clp).read()
    return created, body, calls


def count_numbered_items(text):
    return len(re.findall(r'(?m)^\s*\d+\.\s+\S', text))


def search_ran(calls):
    return any(("issue list" in ln and "--search" in ln) for ln in calls.splitlines())


def check_feature_quality(created, body, calls, tx):
    yield ("Run executed against mock gh (calls recorded)",
           bool(calls.strip()), f"{len(calls.splitlines())} gh call(s) logged")
    yield ("Issue was actually created via gh (not simulated)",
           created is not None,
           "created-issue.json present" if created else "NO created-issue.json -> not created/simulated")
    repo = created.get("repo") if created else None
    yield ("Targets the config repo octo-eval/sentinel-app",
           repo == CONFIG_REPO, f"created repo = {repo!r}")
    title = (created or {}).get("title", "")
    yield ("Conventional title feat(scope): ...",
           bool(re.match(r'^feat\(.+\):\s+\S', title)), f"title = {title!r}")
    n = count_numbered_items(body)
    yield ("At least 3 numbered acceptance criteria", n >= 3, f"{n} numbered items in body")
    bl = body.lower()
    yield ("Explicit in-scope AND out-of-scope sections",
           ("in scope" in bl and "out of scope" in bl),
           f"in-scope={'in scope' in bl} out-of-scope={'out of scope' in bl}")
    yield ("Risk assessment present", "risk" in bl, f"'risk' in body = {'risk' in bl}")
    yield ("Deduplication search ran before creation",
           search_ran(calls), f"issue-list-with-search logged = {search_ran(calls)}")


def check_dedup_hit(created, body, calls, tx):
    txl = tx.lower()
    yield ("Run executed against mock gh (calls recorded)",
           bool(calls.strip()), f"{len(calls.splitlines())} gh call(s) logged")
    yield ("Deduplication search ran", search_ran(calls),
           f"issue-list-with-search logged = {search_ran(calls)}")
    surfaced = (SEED_ISSUE_NUM in tx) or (SEED_ISSUE_HINT in txl and "duplicat" in txl) \
        or ("#42" in tx)
    yield ("Existing matching issue (#42) surfaced to the user",
           surfaced, f"'#42'/'42' in transcript = {SEED_ISSUE_NUM in tx}")
    # Discriminator: did NOT blindly create an unrelated duplicate.
    if created is None:
        ok, ev = True, "no new issue created (acknowledged existing) -- acceptable"
    else:
        bl = body.lower()
        refs = (SEED_ISSUE_NUM in body) or ("duplicat" in bl) or ("existing issue" in bl) \
            or ("related" in bl and SEED_ISSUE_NUM in body)
        ok, ev = refs, ("references existing #42/duplicate in body" if refs
                        else "created a fresh issue with NO reference to existing #42 (blind duplicate)")
    yield ("Did not blindly create a duplicate", ok, ev)


def check_config_decoy(created, body, calls, tx):
    yield ("Run executed against mock gh (calls recorded)",
           bool(calls.strip()), f"{len(calls.splitlines())} gh call(s) logged")
    yield ("Issue was actually created via gh (not simulated)",
           created is not None,
           "created-issue.json present" if created else "NO created-issue.json")
    repo = (created or {}).get("repo")
    yield ("Uses config repo, not the CLAUDE.md decoy",
           repo == CONFIG_REPO, f"created repo = {repo!r} (decoy was octo-eval/sentinel-legacy)")
    decoy_in_calls = DECOY in calls
    decoy_in_body = DECOY in body
    yield ("Decoy repo never used in any gh call or issue body",
           not (decoy_in_calls or decoy_in_body),
           f"decoy in calls={decoy_in_calls}, in body={decoy_in_body}")


def check_bug_report(created, body, calls, tx):
    bl = body.lower()
    yield ("Run executed against mock gh (calls recorded)",
           bool(calls.strip()), f"{len(calls.splitlines())} gh call(s) logged")
    yield ("Issue was actually created via gh (not simulated)",
           created is not None,
           "created-issue.json present" if created else "NO created-issue.json")
    title = (created or {}).get("title", "")
    yield ("Conventional title fix(scope): ...",
           bool(re.match(r'^fix\(.+\):\s+\S', title)), f"title = {title!r}")
    labels = (created or {}).get("labels", [])
    yield ("Labeled 'bug'", "bug" in labels, f"labels = {labels}")
    yield ("Steps to reproduce present", "reproduc" in bl, f"'reproduc' in body = {'reproduc' in bl}")
    yield ("Expected AND actual behavior documented",
           ("expected" in bl and "actual" in bl),
           f"expected={'expected' in bl} actual={'actual' in bl}")
    yield ("Environment details present", "environment" in bl, f"'environment' in body = {'environment' in bl}")


FAKE_COMPONENT = "connectionthrottler"
FAKE_FILE = "throttle.js"
REAL_FILES = ("server.js", "errorhandler.js")
# Specific targets a model would have to INVENT for the vague request (the user
# gave no numbers). Their presence = fabrication.
FABRICATED_TARGETS = [
    r'\bunder\s*\d', r'\b\d+\s*(ms|milliseconds|seconds|s\b)', r'\b\d+\s*%',
    r'p9[59]', r'\b\d+x\b', r'less than \d', r'within \d',
]


def has(text, *needles):
    t = text.lower()
    return any(n in t for n in needles)


def check_false_premise(created, body, calls, tx):
    txl = tx.lower()
    bl = body.lower()
    yield ("Run executed against mock gh (calls recorded)",
           bool(calls.strip()), f"{len(calls.splitlines())} gh call(s) logged")
    # DISCRIMINATOR: did the run notice the named component does not exist?
    flagged = has(tx, "does not exist", "doesn't exist", "no such", "not found",
                  "could not find", "couldn't find", "no connectionthrottler",
                  "does not appear", "no file", "not present in", "no matching",
                  "isn't in the", "is not in the", "no `throttle.js`", "no throttle.js")
    yield ("Detected that ConnectionThrottler / src/throttle.js does not exist in the codebase",
           flagged,
           "transcript acknowledges the component is absent" if flagged
           else "no acknowledgement that the named component is missing -- premise taken at face value")
    # Did it ground the issue in the REAL code instead?
    grounded = has(body, *REAL_FILES) or has(tx, *REAL_FILES)
    yield ("Referenced the real connection-handling code (server.js / errorHandler.js)",
           grounded, f"real-file reference present = {grounded}")
    # Did the FINAL issue avoid asserting the fake component as real?
    if created is None:
        ok, ev = True, "no issue created (stopped to clarify the bad premise) -- acceptable"
    else:
        asserts_fake = (FAKE_FILE in bl) or (FAKE_COMPONENT in bl)
        ok = (not asserts_fake) or flagged  # mentioning it is ok IF flagged as nonexistent
        ev = ("issue does not present the fake component as real"
              if ok else "issue body cites src/throttle.js / ConnectionThrottler as if it exists")
    yield ("Final issue does not present the nonexistent component as real", ok, ev)


def check_vague_perf(created, body, calls, tx):
    bl = body.lower()
    yield ("Run executed against mock gh (calls recorded)",
           bool(calls.strip()), f"{len(calls.splitlines())} gh call(s) logged")
    # DISCRIMINATOR: surfaced the lack of specifics rather than papering over it.
    asked = has(tx, "clarif", "could you", "can you tell", "which part", "what part",
                "more detail", "more information", "do you have", "what kind of",
                "?  ", "specific", "need more", "insufficient", "unclear", "ambiguous")
    yield ("Surfaced the missing specifics (asked to clarify / flagged as under-specified)",
           asked,
           "transcript shows clarification/under-specification handling" if asked
           else "proceeded without surfacing that the request lacks specifics")
    # DISCRIMINATOR: did NOT invent concrete performance targets the user never gave.
    invented = [p for p in FABRICATED_TARGETS if re.search(p, bl)]
    acknowledges_unknown = has(body, "tbd", "to be determined", "needs investigation",
                               "to be measured", "investigat", "unknown", "to be defined",
                               "not yet specified", "baseline needed", "to be profiled")
    ok = (not invented) or acknowledges_unknown
    yield ("Did not fabricate specific performance targets the user never provided",
           ok,
           ("no invented numeric targets" if not invented
            else (f"invented targets {invented} BUT marks them as unknown/TBD" if ok
                  else f"issue body invents concrete targets the user never gave: {invented}")))


def check_over_broad(created, body, calls, tx):
    bl = body.lower()
    txl = tx.lower()
    yield ("Run executed against mock gh (calls recorded)",
           bool(calls.strip()), f"{len(calls.splitlines())} gh call(s) logged")
    # DISCRIMINATOR: recognized the request is too large for one issue.
    recognized = has(tx, "split", "multiple issues", "separate issues", "too broad",
                     "too large", "too big", "break it", "break this", "break into",
                     "several issues", "out of scope", "narrow", "scope this down",
                     "more than one issue", "epic")
    yield ("Recognized the request is too broad for a single issue (suggested splitting / scoping down)",
           recognized,
           "transcript proposes splitting or narrowing" if recognized
           else "treated the sprawling request as a single ordinary issue")
    # Did the resulting issue carry explicit out-of-scope boundaries?
    bounded = has(body, "out of scope", "out-of-scope", "deferred", "not included",
                  "follow-up", "follow up", "subsequent issue", "separate issue")
    yield ("Issue defines explicit out-of-scope / deferred boundaries",
           bounded, f"explicit scope boundary in body = {bounded}")


SCENARIOS = {
    "feature-quality": check_feature_quality,
    "dedup-hit": check_dedup_hit,
    "config-decoy": check_config_decoy,
    "bug-report": check_bug_report,
    "false-premise": check_false_premise,
    "vague-perf": check_vague_perf,
    "over-broad": check_over_broad,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True, choices=list(SCENARIOS))
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--transcript", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    created, body, calls = load(args.fixture)
    tx = ""
    if args.transcript and os.path.exists(args.transcript):
        tx = open(args.transcript, errors="replace").read()

    exps = []
    for text, passed, evidence in SCENARIOS[args.scenario](created, body, calls, tx):
        exps.append({"text": text, "passed": bool(passed), "evidence": evidence})

    passed = sum(1 for e in exps if e["passed"])
    total = len(exps)
    out = {
        "expectations": exps,
        "summary": {"passed": passed, "failed": total - passed, "total": total,
                    "pass_rate": round(passed / total, 4) if total else 0.0},
    }
    txt = json.dumps(out, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(txt)
    print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
