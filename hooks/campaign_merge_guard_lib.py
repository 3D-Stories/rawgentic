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
import shlex
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

#: Shell operators that end a command segment. `shlex(punctuation_chars=True)` emits `&&`
#: and `||` as single tokens, and a separator inside a quoted string never becomes one —
#: which is why classification is done over TOKENS rather than raw text (Step 8a F6).
#:
#: Deliberately NOT matched: `sh -c "gh pr merge …"` and other wrappers. That is the right
#: trade for this guard's threat model (D187): a false positive blocks unrelated work in
#: every project on the host, while a false negative only misses a form nobody reaches by
#: accident — and the guard never claimed to stop a deliberate bypass.
_OPERATORS = frozenset({";", "&&", "||", "|", "&", "\n"})

#: `VAR=value` prefixes, which sit before the command word.
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

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


def _strip_heredocs(command):
    """Everything from the first `<<` onward, removed.

    A heredoc body is data being written, never a command being run — the reasoning
    `step_state_post.executable_text` already states for this repo. Cutting can only
    REMOVE text, so it can never manufacture a false denial.
    """
    cut = command.find("<<")
    return command if cut == -1 else command[:cut]


def _lex(command):
    """Shell tokens with operators kept, or ``None`` when the text will not lex.

    `shlex` rather than a regex over raw text (Step 11 findings F1 and F6): it keeps a
    quoted ARGUMENT VALUE intact — `--repo "3D-Stories/rawgentic"` stays one token — while
    a quoted MENTION collapses into a single token, so `echo 'gh pr merge 887'` can never
    look like three command tokens. An earlier version stripped quotes wholesale, which
    turned `--repo "o/r" --squash` into `--repo --squash` and read `--squash` as the
    repository, allowing the merge it was meant to block.
    """
    lexer = shlex.shlex(_strip_heredocs(command), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return None


def _segments(tokens):
    """Split a token list on shell operators."""
    out, current = [], []
    for token in tokens:
        if token in _OPERATORS:
            out.append(current)
            current = []
        else:
            current.append(token)
    out.append(current)
    return out


def _parse_merge_args(args):
    """The `gh pr merge` argument grammar → ``{"pr": int|None, "repo": str|None}``."""
    pr, repo, index = None, None, 0
    while index < len(args):
        token = args[index]
        if token.startswith("-") and token != "-":
            name, sep, inline = token.partition("=")
            if sep:
                if name in _REPO_FLAGS:
                    repo = inline
                index += 1
                continue
            if name in _VALUE_FLAGS:
                value = args[index + 1] if index + 1 < len(args) else None
                # A value-taking flag whose value is missing or is itself a flag has no
                # usable value; never adopt the next flag as one (Step 11 finding F1).
                if value is not None and not value.startswith("-"):
                    if name in _REPO_FLAGS:
                        repo = value
                    index += 2
                    continue
                index += 1
                continue
            index += 1
            continue
        if pr is None and token.isdigit():
            pr = int(token)
        index += 1
    return {"pr": pr, "repo": repo}


def parse_merge_command(command):
    """Classify *command*. Returns a dict or ``None``.

    ``None`` means "not a raw `gh pr merge`" and is the answer for essentially every
    command. Otherwise::

        {"pr": int|None, "repo": str|None,        # the FIRST target, for convenience
         "targets": [{"pr": …, "repo": …}, …],    # EVERY target, in execution order
         "cd": str|None, "cd_unresolvable": bool, "unlexable": bool}

    Every merge target is returned, not just the first (Step 11 finding F2): one Bash call
    may carry several, and `gh pr merge 999; gh pr merge 887` must not be waved through on
    the strength of its harmless first half.

    ``pr is None`` on a target means the invocation IS a raw merge but its target could not
    be read — `gh pr merge` with no number merges the current branch's PR, which this hook
    cannot resolve, so the caller treats it as an unknown target rather than as "no PR".
    """
    if not isinstance(command, str) or not command:
        return None
    if not _PREFILTER_RE.search(command):
        return None

    tokens = _lex(command)
    if tokens is None:
        # It looks like a merge and will not lex. Refusing to guess is the point.
        return {"pr": None, "repo": None, "targets": [{"pr": None, "repo": None}],
                "cd": None, "cd_unresolvable": False, "unlexable": True}

    cd_target, cd_unresolvable, targets = None, False, []
    for segment in _segments(tokens):
        # Drop `VAR=value` prefixes, which precede the command word.
        index = 0
        while index < len(segment) and _ASSIGNMENT_RE.match(segment[index]):
            index += 1
        words = segment[index:]
        if not words:
            continue
        if words[0] == "cd" and len(words) >= 2:
            candidate = words[1]
            if any(ch in candidate for ch in _UNSAFE_CHARS):
                cd_unresolvable = True
            else:
                cd_target = candidate
            continue
        if words[:3] == ["gh", "pr", "merge"]:
            targets.append(_parse_merge_args(words[3:]))

    if not targets:
        return None
    first = targets[0]
    return {"pr": first["pr"], "repo": first["repo"], "targets": targets,
            "cd": cd_target, "cd_unresolvable": cd_unresolvable, "unlexable": False}


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


def valid_issue_entry(entry):
    """Does this queue entry carry the fields the decision actually compares?

    Step 11 finding F3: checking only `isinstance(entry, dict)` accepted
    ``{"pr": "887"}`` as an ACTIVE child, and `_match_pr` then compared that string to the
    integer 887, found nothing, and allowed the raw merge — a corrupt file producing the
    exact outcome the fail-closed rule exists to prevent. `bool` is excluded from the
    integer checks because `True == 1` in Python.
    """
    if not isinstance(entry, dict):
        return False
    number = entry.get("number")
    if not isinstance(number, int) or isinstance(number, bool):
        return False
    status = entry.get("status")
    if not isinstance(status, str) or not status:
        return False
    pr = entry.get("pr")
    if pr is not None and (not isinstance(pr, int) or isinstance(pr, bool)):
        return False
    return True


def campaign_activity(state):
    """``"active"`` | ``"settled"`` | ``"invalid"``.

    Three-valued deliberately (Step 8a finding F4). A syntactically valid JSON object whose
    `issues` field is missing, is not a list, or holds an entry whose compared fields are
    the wrong type is **invalid**, not settled: reading either as "no active children" let
    a corrupt campaign file allow a raw merge, the exact opposite of the fail-closed rule
    for state that cannot be evaluated. Verified against all five real campaign files in
    claude_docs/.driver-state/ — every entry passes — so this cannot refuse legitimate
    state.
    """
    if not isinstance(state, dict):
        return "invalid"
    issues = state.get("issues")
    if not isinstance(issues, list):
        return "invalid"
    for entry in issues:
        if not valid_issue_entry(entry):
            return "invalid"
    for entry in issues:
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
    subjects = [t for t in target.get("targets") or [target]
                if not (t.get("repo") and project_repo
                        and t.get("repo") != project_repo)]
    if not subjects:
        return {"action": "allow",
                "reason": "every merge target names a repository other than %s"
                          % project_repo}

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

    if target.get("unlexable"):
        return {
            "action": "deny", "kind": "unknown-target",
            "campaign": active[0]["campaign"],
            "reason": "the command contains a `gh pr merge` but will not parse as shell, "
                      "so its target cannot be read while a campaign is active",
        }

    # EVERY target is checked, not just the first (Step 11 finding F2): one Bash call can
    # carry several, and a harmless first merge must not clear the whole call.
    for subject in subjects:
        pr = subject.get("pr")
        if pr is None:
            return {
                "action": "deny", "kind": "unknown-target",
                "campaign": active[0]["campaign"],
                "reason": "a target PR could not be read from the command while a "
                          "campaign is active",
            }
        campaign, issue = _match_pr(active, pr)
        if campaign is not None:
            return {"action": "deny", "kind": "campaign", "campaign": campaign,
                    "issue": issue, "pr": pr,
                    "reason": "PR #%d is a child of active campaign %s" % (pr, campaign)}

    return {"action": "allow",
            "reason": "no active campaign names %s"
                      % ", ".join("PR #%s" % t.get("pr") for t in subjects)}


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
