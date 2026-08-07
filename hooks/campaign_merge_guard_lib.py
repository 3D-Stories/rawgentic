"""Pure decision layer for the campaign merge guard (#976).

`hooks/campaign-merge-guard.py` is the thin PreToolUse wrapper; everything decidable
lives here so it can be unit-tested without a subprocess. Same split as
`security-guard.py` / `security_guard_lib.py`.

**What this guard is, and is not (D187).** It hard-blocks a raw `gh pr merge` on the Bash
tool path when the target PR belongs to an active campaign, and points the caller at
`broker-merge`. It stops an *accidental* raw merge — a session that drifted from the
prose. It does **not** stop a deliberate bypass: PreToolUse fires per Claude Code tool
call, not per OS process, so a `python3 -c "subprocess.run(['gh','pr','merge',…])"` is
invisible to it. The threat model is caller confusion and prose drift — the same one the
broker states for its own target binding (`launcher_lib.py:5300-5303`).

Standard library only, and no subprocess or network call: this runs on EVERY Bash tool
call, so it must be cheap and it must never block on I/O it does not control (the
constraint `supervision_lib.py` documents for the same reason).
"""
import json
import os
import re
import time

#: Mirrored from `driver_lib._DISPOSED_STATUSES`. Copied rather than imported because
#: `driver_lib` is ~3900 lines and this module is loaded on every Bash tool call.
#: `tests/hooks/test_campaign_merge_guard.py::test_disposed_statuses_mirror_driver_lib`
#: is the drift guard that keeps the copy honest (repo CLAUDE.md §4 mistake 21).
DISPOSED_STATUSES = frozenset({"merged", "deferred", "abandoned"})

#: Bounded reads (review finding F5). The host's behavior when a PreToolUse hook exceeds
#: its registered timeout is UNPROVEN, so the design removes the dependency instead of
#: resting on it: the work is capped so the hook always answers first.
MAX_STATE_BYTES = 1024 * 1024
MAX_STATE_FILES = 64
#: Well under the 5 s registered in hooks/hooks.json.
INTERNAL_DEADLINE_S = 2.0

DRIVER_STATE_RELPATH = os.path.join("claude_docs", ".driver-state")

#: Cheap pre-filter for the hot path — every Bash call in every session hits this and
#: nothing else. `gh`, then `pr`, then `merge`, as whole words in order.
_PREFILTER_RE = re.compile(r"\bgh\b[^\n;|&]*?\bpr\b[^\n;|&]*?\bmerge\b")

#: The classifier. `gh pr merge` as three consecutive tokens, in COMMAND POSITION: the
#: start of the string or just after a separator (`;` `|` `&` newline, which covers `&&`
#: and `||`), allowing leading whitespace and `VAR=value` prefixes. So
#: `cd x && gh pr merge 887` matches, while `echo 'run gh pr merge 887 by hand'` and
#: `grep -rn 'gh pr merge' docs/` do not — a mention is not an invocation.
#:
#: Deliberately NOT matched: `sh -c "gh pr merge …"` and other wrappers. That is the
#: right trade for this guard's threat model (D187): a false positive blocks unrelated
#: work in every project on the host, while a false negative only misses a form nobody
#: reaches by accident — and the guard never claimed to stop a deliberate bypass.
_MERGE_RE = re.compile(
    r"(?:^|[;|&\n])\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*gh\s+pr\s+merge\b")

_PR_RE = re.compile(r"\b(\d{1,7})\b")
_REPO_RE = re.compile(r"--repo(?:=|\s+)([^\s;|&]+)")


def parse_merge_command(command):
    """Classify *command*. Returns ``{"pr": int|None, "repo": str|None}`` or ``None``.

    ``None`` means "not a raw `gh pr merge`" and is the answer for essentially every
    command. A returned dict with ``pr is None`` means the invocation IS a raw merge but
    its target could not be read — `gh pr merge` with no number merges the current
    branch's PR, which this hook has no way to resolve.
    """
    if not isinstance(command, str) or not command:
        return None
    if not _PREFILTER_RE.search(command):
        return None
    match = _MERGE_RE.search(command)
    if not match:
        return None

    tail = command[match.end():]
    # Stop at a command separator so `gh pr merge && gh pr view 42` cannot borrow 42.
    tail = re.split(r"[;|&\n]", tail, maxsplit=1)[0]

    repo_match = _REPO_RE.search(tail)
    repo = repo_match.group(1) if repo_match else None
    if repo:
        # Never let the repo's own text supply the PR number.
        tail = tail.replace(repo_match.group(0), " ")

    pr_match = _PR_RE.search(tail)
    pr = int(pr_match.group(1)) if pr_match else None
    return {"pr": pr, "repo": repo}


def find_project_root(start_path):
    """Walk up for the directory holding `.rawgentic.json`, or ``None``.

    Same shape as `security-guard.py:49-63` — the established hook pattern here.
    """
    if not start_path:
        return None
    try:
        current = os.path.abspath(start_path)
    except (OSError, ValueError, TypeError):
        return None
    if os.path.isfile(current):
        current = os.path.dirname(current)
    while True:
        if os.path.exists(os.path.join(current, ".rawgentic.json")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def configured_repo(project_root):
    """`repo.fullName` from the project's own config, or ``None``.

    A hook reading its own single config block directly, fail-open — the sanctioned
    exception in the repo manual (§1), not a deviation.
    """
    if not project_root:
        return None
    try:
        with open(os.path.join(project_root, ".rawgentic.json"), encoding="utf-8") as fh:
            config = json.load(fh)
        name = (config.get("repo") or {}).get("fullName")
        return name if isinstance(name, str) and name else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _campaign_is_active(state):
    """Active == at least one child is not yet disposed."""
    issues = state.get("issues")
    if not isinstance(issues, list):
        return False
    for entry in issues:
        if not isinstance(entry, dict):
            continue
        if entry.get("status") not in DISPOSED_STATUSES:
            return True
    return False


def read_campaigns(project_root, deadline=None):
    """Read durable campaign state.

    Returns ``(active_campaigns, unevaluable)``:

    - ``active_campaigns`` — ``[{"campaign": str, "issues": [...]}, …]``
    - ``unevaluable`` — names of state files that EXIST but could not be read.

    A missing directory yields ``([], [])``: absence, not failure. That is deliberately
    the rule `docs/supervision.md` already states — "`ENOENT` under a valid root is the
    only file failure treated as absence" — and it is what keeps every project that has
    never run a campaign completely unaffected (AC4).
    """
    active, unevaluable = [], []
    if not project_root:
        return active, unevaluable
    state_dir = os.path.join(project_root, DRIVER_STATE_RELPATH)
    try:
        names = sorted(os.listdir(state_dir))
    except FileNotFoundError:
        return active, unevaluable          # absence
    except (OSError, ValueError):
        return active, ["<driver-state directory unreadable>"]

    for name in names[:MAX_STATE_FILES]:
        if not name.endswith(".json"):
            continue
        if deadline is not None and time.monotonic() > deadline:
            unevaluable.append("<deadline reached before reading %s>" % name)
            break
        path = os.path.join(state_dir, name)
        try:
            if not os.path.isfile(path):
                continue
            if os.path.getsize(path) > MAX_STATE_BYTES:
                unevaluable.append(name)
                continue
            with open(path, encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, ValueError, UnicodeDecodeError):
            unevaluable.append(name)
            continue
        if not isinstance(state, dict):
            unevaluable.append(name)
            continue
        if _campaign_is_active(state):
            active.append({
                "campaign": state.get("campaign") or name[:-len(".json")],
                "issues": [e for e in state.get("issues") or [] if isinstance(e, dict)],
            })
    return active, unevaluable


def _match_pr(active, pr):
    """The first active campaign naming *pr*, as ``(campaign, issue_number)``."""
    for campaign in active:
        for entry in campaign["issues"]:
            if entry.get("status") in DISPOSED_STATUSES:
                continue
            if entry.get("pr") == pr:
                return campaign["campaign"], entry.get("number")
    return None, None


def decide(target, active, unevaluable, project_repo):
    """The decision table. Returns ``{"action": "allow"|"deny", …}``.

    Fail-CLOSED lives here and ONLY here — it is reachable only once *target* proves the
    command is a raw `gh pr merge`, so its whole blast radius is one refused raw command
    (D186). The sanctioned `broker-merge` path never reaches this function.
    """
    if target is None:
        return {"action": "allow", "reason": "not a raw gh pr merge"}

    # A foreign repository is not this campaign's business (review finding F3: PR numbers
    # are repository-scoped, so binding on the number alone denies unrelated work).
    target_repo = target.get("repo")
    if target_repo and project_repo and target_repo != project_repo:
        return {"action": "allow",
                "reason": "targets %s, not this project's %s" % (target_repo, project_repo)}

    if unevaluable:
        return {
            "action": "deny", "kind": "unevaluable", "files": list(unevaluable),
            "reason": "campaign state exists but could not be read: %s"
                      % ", ".join(str(f) for f in unevaluable),
        }

    if not active:
        return {"action": "allow", "reason": "no active campaign"}

    pr = target.get("pr")
    if pr is None:
        return {
            "action": "deny", "kind": "unknown-target",
            "campaign": active[0]["campaign"],
            "reason": "the target PR could not be read from the command while a campaign "
                      "is active",
        }

    campaign, issue = _match_pr(active, pr)
    if campaign is None:
        return {"action": "allow",
                "reason": "no active campaign names PR #%d" % pr}
    return {"action": "deny", "kind": "campaign", "campaign": campaign,
            "issue": issue, "pr": pr,
            "reason": "PR #%d is a child of active campaign %s" % (pr, campaign)}


def format_deny(decision):
    """The AC5 message: never a bare "blocked"."""
    kind = decision.get("kind")
    broker = "python3 hooks/launcher_lib.py broker-merge"

    if kind == "campaign":
        pr, issue = decision.get("pr"), decision.get("issue")
        campaign = decision.get("campaign")
        command = ("  %s --pr %s --issue %s \\\n    --campaign %s --project-root ."
                   % (broker, pr, issue, campaign))
        return (
            "BLOCKED: this PR belongs to an active campaign, so it merges through the "
            "supervised broker.\n\n"
            "  Campaign: %s   Issue: #%s   PR: #%s\n\n"
            "A raw `gh pr merge` skips authority evaluation, target binding, the "
            "execute-once claim and the decision telemetry. Run this instead:\n\n%s\n"
            % (campaign, issue, pr, command))

    if kind == "unknown-target":
        return (
            "BLOCKED: campaign %s is active and this command's target PR could not be "
            "read.\n\n"
            "`gh pr merge` with no PR number merges the current branch's PR, which this "
            "guard cannot resolve — so it cannot prove the merge is not a campaign "
            "child.\n\n"
            "Name the PR explicitly if it is unrelated to the campaign, or merge through "
            "the broker:\n\n  %s --pr <pr> --issue <issue> \\\n    --campaign %s "
            "--project-root .\n"
            % (decision.get("campaign"), broker, decision.get("campaign")))

    return (
        "BLOCKED: campaign state exists but could not be read, so this guard cannot "
        "prove that merging is safe.\n\n"
        "  Unreadable: %s\n\n"
        "This guard fails closed once a command is identified as a raw `gh pr merge` — "
        "the refusal costs you this one command, while an unnoticed bypass costs the "
        "campaign its authority checks, its execute-once claim and its telemetry.\n\n"
        "Fix or remove the unreadable state under claude_docs/.driver-state/, or merge "
        "through the broker:\n\n  %s --pr <pr> --issue <issue> \\\n    --campaign "
        "<campaign> --project-root .\n"
        % (", ".join(str(f) for f in decision.get("files") or ["<unknown>"]), broker))
