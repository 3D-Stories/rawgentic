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

#: Bounded reads (Step 4 finding F5). The host's behavior when a PreToolUse hook exceeds
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

#: The classifier, applied to QUOTE-STRIPPED text (see `parse_merge_command`). `gh pr
#: merge` as three consecutive tokens in COMMAND POSITION: the start of a segment,
#: allowing leading whitespace and `VAR=value` prefixes.
#:
#: Deliberately NOT matched: `sh -c "gh pr merge …"` and other wrappers. That is the right
#: trade for this guard's threat model (D187): a false positive blocks unrelated work in
#: every project on the host, while a false negative only misses a form nobody reaches by
#: accident — and the guard never claimed to stop a deliberate bypass.
_MERGE_RE = re.compile(
    r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*gh\s+pr\s+merge\b")

#: Segment separators. Splitting quote-stripped text means a `;` inside a quoted string
#: can no longer masquerade as one (Step 8a finding F6).
_SEGMENT_RE = re.compile(r"(?:\|\||&&|[;|&\n])")

#: `cd <literal>` as a whole segment.
_CD_RE = re.compile(r"^\s*cd\s+(\S+)\s*$")

#: Shell text this module refuses to reason about inside a `cd` target.
_UNSAFE_CHARS = ("$", "`", "*", "?", "~")

#: `gh pr merge` flags that consume the NEXT token as a value. Without this the first
#: number anywhere after `merge` was taken as the PR, so `--subject 2026 887` resolved to
#: PR 2026 (Step 8a finding F5).
_VALUE_FLAGS = frozenset({
    "--subject", "-t", "--body", "-b", "--body-file", "-F",
    "--match-head-commit", "--author-email", "-A", "--repo", "-R",
})
_REPO_FLAGS = frozenset({"--repo", "-R"})


def _executable_text(command):
    """`command` with quoted strings and heredoc bodies removed.

    Delegates to `step_state_post.executable_text` — the repo already owns this exact
    problem ("a command that MENTIONS a needle is not a command that RUNS it", v3.138.1),
    so this reuses it rather than growing a second copy. On import failure the raw text is
    returned: this runs before classification, where the policy is fail-open.
    """
    try:
        import step_state_post  # pylint: disable=import-outside-toplevel
        return step_state_post.executable_text(command)
    except Exception:  # pylint: disable=broad-except
        return command


def _tokenize(segment):
    return [t for t in segment.split() if t]


def parse_merge_command(command):
    """Classify *command*. Returns a dict or ``None``.

    ``None`` means "not a raw `gh pr merge`" and is the answer for essentially every
    command. Otherwise::

        {"pr": int|None, "repo": str|None, "cd": str|None, "cd_unresolvable": bool}

    ``pr is None`` means the invocation IS a raw merge but its target could not be read —
    `gh pr merge` with no number merges the current branch's PR, which this hook cannot
    resolve, so the caller must treat it as an unknown target rather than as "no PR".
    """
    if not isinstance(command, str) or not command:
        return None
    if not _PREFILTER_RE.search(command):
        return None

    text = _executable_text(command)
    segments = _SEGMENT_RE.split(text)

    cd_target, cd_unresolvable = None, False
    merge_tokens = None
    for segment in segments:
        cd_match = _CD_RE.match(segment)
        if cd_match:
            candidate = cd_match.group(1)
            if any(ch in candidate for ch in _UNSAFE_CHARS):
                cd_unresolvable = True
            else:
                cd_target = candidate.strip("\"'")
            continue
        if _MERGE_RE.match(segment):
            merge_tokens = _tokenize(segment)
            break

    if merge_tokens is None:
        return None

    # Drop everything up to and including the `merge` verb, plus any VAR=value prefixes.
    try:
        start = merge_tokens.index("merge") + 1
    except ValueError:
        start = len(merge_tokens)
    args = merge_tokens[start:]

    pr, repo, index = None, None, 0
    while index < len(args):
        token = args[index]
        if token.startswith("-"):
            name, _, inline = token.partition("=")
            if inline:
                if name in _REPO_FLAGS:
                    repo = inline
                index += 1
                continue
            if name in _VALUE_FLAGS:
                if name in _REPO_FLAGS and index + 1 < len(args):
                    repo = args[index + 1]
                index += 2
                continue
            index += 1
            continue
        if pr is None and token.isdigit():
            pr = int(token)
        index += 1

    return {"pr": pr, "repo": repo, "cd": cd_target,
            "cd_unresolvable": cd_unresolvable}


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


def effective_cwd(target, hook_cwd):
    """Where the merge segment would actually run (Step 8a finding F3).

    `cd /campaign-project && gh pr merge 887` executes in the campaign project, not in the
    hook's cwd, so resolving campaign state from `hook_cwd` alone read the wrong project's
    state — or none at all.
    """
    cd_target = (target or {}).get("cd")
    if not cd_target:
        return hook_cwd
    if os.path.isabs(cd_target):
        return cd_target
    try:
        return os.path.normpath(os.path.join(hook_cwd or os.getcwd(), cd_target))
    except (OSError, ValueError, TypeError):
        return hook_cwd


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


def campaign_activity(state):
    """``"active"`` | ``"settled"`` | ``"invalid"``.

    Three-valued deliberately (Step 8a finding F4). A syntactically valid JSON object whose
    `issues` field is missing or is not a list is **invalid**, not settled: reading it as
    "no active children" let a corrupt campaign file allow a raw merge, which is the exact
    opposite of the fail-closed rule for state that cannot be evaluated. Every real
    campaign file carries an `issues` list (verified across all five in
    claude_docs/.driver-state/), so this cannot refuse legitimate state.
    """
    if not isinstance(state, dict):
        return "invalid"
    issues = state.get("issues")
    if not isinstance(issues, list):
        return "invalid"
    for entry in issues:
        if not isinstance(entry, dict):
            return "invalid"
        if entry.get("status") not in DISPOSED_STATUSES:
            return "active"
    return "settled"


def read_campaigns(project_root, deadline=None):
    """Read durable campaign state.

    Returns ``(active_campaigns, unevaluable)``:

    - ``active_campaigns`` — ``[{"campaign": str, "issues": [...]}, …]``
    - ``unevaluable`` — reasons the state set could not be fully evaluated.

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

    # Filter to state files BEFORE applying the cap, and never drop an overflow silently
    # (Step 8a finding F1): `.lock` siblings live in this directory and used to consume
    # the budget, and files past the cap vanished with no trace, which allowed the merge.
    state_files = [n for n in names if n.endswith(".json")]
    if len(state_files) > MAX_STATE_FILES:
        unevaluable.append(
            "<%d campaign files exceed the %d-file read cap>"
            % (len(state_files), MAX_STATE_FILES))
        state_files = state_files[:MAX_STATE_FILES]

    for name in state_files:
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
        activity = campaign_activity(state)
        if activity == "invalid":
            unevaluable.append(name)
        elif activity == "active":
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

    # A foreign repository is not this campaign's business (Step 4 finding F3: PR numbers
    # are repository-scoped, so binding on the number alone denies unrelated work).
    target_repo = target.get("repo")
    if target_repo and project_repo and target_repo != project_repo:
        return {"action": "allow",
                "reason": "targets %s, not this project's %s" % (target_repo, project_repo)}

    reasons = list(unevaluable)
    if target.get("cd_unresolvable"):
        reasons.append("<the command changes directory to a path this guard cannot "
                       "resolve, so the campaign state to check is unknown>")
    if reasons:
        return {
            "action": "deny", "kind": "unevaluable", "files": reasons,
            "reason": "campaign state could not be evaluated: %s"
                      % ", ".join(str(f) for f in reasons),
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


def _root_arg(project_root):
    """`--project-root` value for the replacement command.

    Step 8a finding F8: a literal `.` is wrong whenever the Bash call ran in a
    subdirectory of the project, which is exactly when the caller most needs the command
    to work as printed.
    """
    if not project_root:
        return "."
    return project_root if not re.search(r"[\s'\"$`]", project_root) \
        else "'%s'" % project_root.replace("'", "'\\''")


def format_deny(decision, project_root=None):
    """The AC5 message: never a bare "blocked"."""
    kind = decision.get("kind")
    broker = "python3 hooks/launcher_lib.py broker-merge"
    root = _root_arg(project_root)

    if kind == "campaign":
        pr, issue = decision.get("pr"), decision.get("issue")
        campaign = decision.get("campaign")
        command = ("  %s --pr %s --issue %s \\\n    --campaign %s --project-root %s"
                   % (broker, pr, issue, campaign, root))
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
            "--project-root %s\n"
            % (decision.get("campaign"), broker, decision.get("campaign"), root))

    return (
        "BLOCKED: campaign state could not be evaluated, so this guard cannot prove that "
        "merging is safe.\n\n"
        "  Unevaluable: %s\n\n"
        "This guard fails closed once a command is identified as a raw `gh pr merge` — "
        "the refusal costs you this one command, while an unnoticed bypass costs the "
        "campaign its authority checks, its execute-once claim and its telemetry.\n\n"
        "Fix or remove the unreadable state under claude_docs/.driver-state/, or merge "
        "through the broker:\n\n  %s --pr <pr> --issue <issue> \\\n    --campaign "
        "<campaign> --project-root %s\n"
        % (", ".join(str(f) for f in decision.get("files") or ["<unknown>"]),
           broker, root))
