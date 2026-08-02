#!/usr/bin/env python3
"""Multi-issue driver — dependency-DAG + schema-readability helpers (#163).

Backs the documented multi-issue driver PATTERN (``docs/multi-issue-driver.md``).
The driver itself is a *documented orchestration pattern* (design #134), not a
skill; this module supplies the small pieces of that pattern whose behavior the
issue's acceptance criteria make worth unit-testing rather than describing in
prose:

- ``parse_depends_on(body)`` — extract issue-number dependencies from an issue
  body: only ``#<digits>`` in the immediate list under a recognized dependency
  phrase (not negated, word-boundary matched) or a task-list checkbox is taken;
  free-text numbers are not. It is NOT markdown-aware (a phrase quoted in a
  blockquote/code fence is still taken), so it is a best-effort filter, not a
  hard security boundary.
- ``topo_sort_issues(issues)`` — Kahn topological sort of the campaign queue by
  ``depends_on``; deterministic tie-break (lowest issue number first);
  **fail-closed** on a cycle (raises ``DependencyCycleError`` with the cycle in
  the message) so a cyclic queue halts loudly instead of silently mis-ordering.
- ``next_ready_issue(state, deps_satisfied_by)`` — the advance rule: the first
  ``queued`` issue whose in-queue dependencies are satisfied (``merged`` by
  default; ``pr_open`` also counts when the knob is ``"pr_open"``). A
  deferred/abandoned dependency parks its dependents; the loop keeps going with
  independent issues.
- ``validate_driver_state(state)`` — minimal structural readability check for
  schema v1 AND v2 (a v1 file with no ``depends_on`` still validates — #163 AC7).

**Scope boundary (deliberate).** This is the dependency-DAG subset only. The
fuller state-transition validator (``record_outcome`` / ``defer_issue`` / queue
mutation) that design #134 follow-up #2 deferred is intentionally NOT here — it
stays evidence-gated. Extend this module with that layer only when campaign
experience shows hand-maintained state transitions are error-prone.

Pure, stdlib-only, no I/O and no side effects — safe to import from the driver
pattern, the test suite, or a ``python3 -c`` one-liner in the docs.
"""
import heapq
import re

# Canonical driver-state statuses (design #134 status machine).
VALID_STATUSES = frozenset(
    {"queued", "in_progress", "pr_open", "merged", "deferred", "abandoned"}
)
SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2})
# What "dependency satisfied" means, per the deps_satisfied_by policy knob.
_SATISFIED_BY = {
    "merged": frozenset({"merged"}),
    "pr_open": frozenset({"merged", "pr_open"}),
}

# A dependency phrase ("depends on" / "depends-on" / "blocked by" / "blocked-by"),
# anchored at word boundaries so it is NOT matched inside another word
# ("unblocked by" must not count). Matched case-insensitively on the raw line
# (not a lowercased copy), so offsets can't drift on case-length-changing Unicode.
_DEP_PHRASE_RE = re.compile(
    r"(?<![a-z])(?:depends?[ -]on|blocked[ -]by)(?![a-z])", re.IGNORECASE
)
# Immediate negation right before a phrase ("not blocked by", "no longer depends
# on") — the phrase is then a statement of NON-dependency and is skipped. This
# keeps ordinary issue-body prose from injecting a false dependency. An optional
# "be"/"get" bridge is allowed so grammatical modal negations still match
# ("cannot be blocked by", "won't be blocked by", "never be blocked by").
_NEG_BEFORE_RE = re.compile(
    r"\b(?:not|never|cannot|can't|won't|doesn't|isn't|no longer)\s+"
    r"(?:(?:be|get)\s+)?$",
    re.IGNORECASE,
)
# The dependency LIST immediately following a phrase: "#10", "#10, #20 and #30",
# "#10 & #20", optionally led by a colon and by a noun ("issue"/"PR"/"epic",
# optionally "the"). Anchored at the segment start and stopping at the first token
# that is not a `#N`, a list separator, or such a noun — so it does NOT swallow a
# following sentence ("Depends on #10. See #20" → only #10). The leading noun sits
# OUTSIDE group 1 and inner nouns sit INSIDE it, but `_HASH_NUM_RE.findall` pulls
# only the `#N` tokens either way, so every listed number is still captured.
_DEP_LIST_RE = re.compile(
    r"\s*:?\s*(?:(?:the\s+)?(?:issues?|prs?|epics?)\s+)?"
    r"(#\d+(?:\s*(?:,|and|&|or)\s*(?:(?:the\s+)?(?:issues?|prs?|epics?)\s+)?#\d+)*)",
    re.IGNORECASE,
)
# Task-list checkbox referencing an issue, e.g. "- [ ] #101" / "* [x] #102".
_TASK_LIST_RE = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s*#(\d+)\b")
_HASH_NUM_RE = re.compile(r"#(\d+)\b")

# --------------------------------------------------------------------------- #
# #840: citation extraction from an issue body
# --------------------------------------------------------------------------- #
# An issue body is UNTRUSTED text, so every quantifier below is BOUNDED. There is no `+`
# or `*` outside a character class anywhere in these patterns — that is asserted
# structurally by `tests/hooks/test_revalidation_extraction.py`, because the classic ReDoS
# shape is a variable-length group under an unbounded quantifier and a body is exactly the
# input an attacker (or an unlucky paste) controls. Bounds: at most 8 path segments of at
# most 64 characters each, so a match attempt does O(1) work per starting position.
#
# Extensions are an allowlist rather than `\w+`: "a/b.c" in prose is not a citation, and
# widening this is how prose starts reading as code.
_CITATION_EXTENSIONS = "py|md|json|sh|yml|yaml|toml|js|ts|tsx|html|css|cfg|ini|txt"
_PATH_SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}"

# URLs are stripped BEFORE candidate scanning: `https://example.com/a/b/c.py` names a path
# on someone else's host, not in this repository, and treating it as a citation would tie a
# child's freshness to a file it never referenced.
_URL_RE = re.compile(r"https?://[^\s)\]>`\"']{1,2048}")

# The leading `(?:\.{1,2}/){0,4}` deliberately CAPTURES `./` and `../` rather than excluding
# them in a lookbehind, so traversal can be rejected explicitly on the matched text below.
# An explicit rejection is auditable; a lookbehind that happens to exclude it is not.
# The `/` in the lookbehind is what rejects an absolute path: in `/etc/x.py` the candidate
# would have to start after a `/`.
_CITATION_RE = re.compile(
    r"(?<![\w/-])"
    r"((?:\.{1,2}/){0,4}"
    rf"(?:{_PATH_SEGMENT}/){{1,8}}"
    rf"{_PATH_SEGMENT}\.(?:{_CITATION_EXTENSIONS}))"
    # Optional line/range suffix, stripped: `path:84`, `path:84-85`, `path#L84`, `path@84`.
    r"(?::\d{1,6}(?:-\d{1,6})?|\#L\d{1,6}(?:-L?\d{1,6})?|@\d{1,6})?"
)

# Exposed so a drift-guard test can assert the no-unbounded-quantifier property directly.
CITATION_PATTERNS = (_URL_RE, _CITATION_RE)


def cited_paths(body: str, resolves) -> tuple[list[str], str]:
    """Repository paths an issue body CITES, plus how confident that reading is.

    Returns ``(paths, extraction)`` where ``extraction`` is one of:

    - ``"paths"``    — at least one candidate resolves in an endpoint tree.
    - ``"none"``     — the body names nothing path-shaped at all. Confidently citation-free.
    - ``"ambiguous"``— it names path-shaped tokens, none of which resolve. NOT the same as
      ``"none"``: we could not read it, so it must fail toward MORE scrutiny (``depth: deep``),
      never less. Collapsing these two was a design-gate finding.

    ``resolves`` is the set of paths known to exist in one of the two endpoint trees. It is
    INJECTED rather than probed here because this module is pure — no I/O, no subprocess —
    a promise enforced by a source grep in `tests/hooks/test_driver_state_write_back.py`.
    The caller obtains it with `git cat-file -e <ref>:<path>`.

    Absolute paths and `../` traversal are rejected: a citation is a repository-relative
    path, and anything else is either prose or an attempt to point outside the tree.
    """
    if not isinstance(body, str) or not body:
        return ([], "none")
    scrubbed = _URL_RE.sub(" ", body)
    candidates: list[str] = []
    seen: set[str] = set()
    for match in _CITATION_RE.finditer(scrubbed):
        raw = match.group(1)
        if ".." in raw or raw.startswith("/"):
            continue                      # traversal / absolute — never a citation
        normalized = raw[2:] if raw.startswith("./") else raw
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)
    if not candidates:
        return ([], "none")
    known = set(resolves or ())
    resolved = [c for c in candidates if c in known]
    if not resolved:
        # Path-shaped but unreadable against either tree — the uncertain case.
        return ([], "ambiguous")
    return (resolved, "paths")


class DriverStateError(ValueError):
    """Raised on a malformed driver-state or an invalid driver operation."""


class DependencyCycleError(DriverStateError):
    """Raised (fail-closed) when the dependency graph contains a cycle."""


class QueueRevalidationRequired(DriverStateError):
    """Raised when the remaining queue has not been revalidated against the current head.

    **Defined here in PR 1; nothing raises it until PR 2.** That split is deliberate: the
    design gate refused an earlier plan that shipped the refusal before the mechanism that
    clears it, which would have jammed a live campaign between two PRs.

    It subclasses `DriverStateError` so every existing `except DriverStateError` caller stays
    correct, and it is a distinct type so callers can tell "the queue is stale" from "nothing
    is ready". That distinction is the whole reason selection RAISES rather than returning
    `None`: `resume_prompt_for_state` collapses every non-ready outcome into `None` and reports
    it as "complete or blocked", so a stale queue would otherwise be announced to the operator
    as *the epic finished*.
    """


# --------------------------------------------------------------------------- #
# #840: fail-closed validators for revalidation provenance
# --------------------------------------------------------------------------- #
# These RAISE rather than accumulating errors, unlike `validate_driver_state`. The reason is
# specific, not stylistic: `validate_driver_state` is permissive (it inspects only
# number/status/depends_on and has no unknown-key branch) and `queue.schema.json` sets
# `additionalProperties: true` at both levels, so a malformed stamp would otherwise pass every
# existing check in silence. Provenance that can be garbage is not provenance.
_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")
_CLAIM_KINDS = frozenset({"citation", "cause", "ac"})
_CLAIM_VERDICTS = frozenset({"holds", "broken"})
_CLAIM_REQUIRED = ("kind", "quoted_from_body", "checked_against", "evidence", "verdict")
_EXTRACTIONS = frozenset({"paths", "none", "ambiguous"})
_DEPTHS = frozenset({"deep", "quick"})
# `issue_obsolete` is deliberately ABSENT: it is not an outcome a stamped child may carry,
# because a stamped child is selectable. It lives only in `pending_disposition`.
_OUTCOMES = frozenset({"still_valid", "body_corrected"})
_PENDING_DISPOSITIONS = frozenset({"issue_obsolete"})


def validate_validated_against(value):
    """Return ``value`` if it is a full 40-char lowercase sha, else raise.

    `bool` is rejected explicitly. `isinstance(True, int)` is True in Python and this module
    already carries `_is_int` for that exact trap; a `True` stamp reading as provenance would
    mark a child validated against nothing.
    """
    if isinstance(value, bool) or not isinstance(value, str) or not _SHA_RE.match(value):
        raise DriverStateError(
            f"validated_against must be a 40-character lowercase sha, got {value!r} — an "
            "abbreviated or malformed stamp cannot be compared against an observed head")
    return value


def validate_claims(claims) -> int:
    """Validate a child's evidence records; return how many there are.

    **An empty or absent list is REFUSED**, which is the mechanical half of the owner's
    2026-08-02 ruling on what a "look" is. Without it, an agent could stamp every remaining
    child `still_valid` having checked nothing, and the gate would report a fully validated
    queue — the exact vacuous pass this whole issue exists to eliminate.
    """
    if not isinstance(claims, list) or not claims:
        raise DriverStateError(
            "claims must be a non-empty list — a stamp with no evidence asserts a check that "
            "did not happen")
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise DriverStateError(f"claims[{index}] must be an object, got {type(claim).__name__}")
        for field in _CLAIM_REQUIRED:
            if field not in claim:
                raise DriverStateError(f"claims[{index}].{field} is required")
        if claim["kind"] not in _CLAIM_KINDS:
            raise DriverStateError(
                f"claims[{index}].kind must be one of {sorted(_CLAIM_KINDS)}, got {claim['kind']!r}")
        if claim["verdict"] not in _CLAIM_VERDICTS:
            raise DriverStateError(
                f"claims[{index}].verdict must be one of {sorted(_CLAIM_VERDICTS)}, "
                f"got {claim['verdict']!r}")
        # A present-but-blank evidence field is the cheapest possible fake.
        for field in ("quoted_from_body", "checked_against", "evidence"):
            value = claim[field]
            if not isinstance(value, str) or not value.strip():
                raise DriverStateError(
                    f"claims[{index}].{field} must be a non-empty string — a blank field is an "
                    "assertion that something was checked, with nothing to show for it")
    return len(claims)


def validate_revalidation_child(record) -> bool:
    """Fail-closed structural check of one `queue_revalidation.children[<n>]` record."""
    if not isinstance(record, dict):
        raise DriverStateError("a revalidation child record must be a JSON object")
    for field in ("body_hash", "from_sha", "to_sha", "extraction", "depth", "outcome",
                  "claims", "validated_at"):
        if field not in record:
            raise DriverStateError(f"revalidation child record is missing {field!r}")
    if record["extraction"] not in _EXTRACTIONS:
        raise DriverStateError(
            f"extraction must be one of {sorted(_EXTRACTIONS)}, got {record['extraction']!r}")
    if record["depth"] not in _DEPTHS:
        raise DriverStateError(
            f"depth must be one of {sorted(_DEPTHS)}, got {record['depth']!r}")
    if record["outcome"] not in _OUTCOMES:
        raise DriverStateError(
            f"outcome must be one of {sorted(_OUTCOMES)}, got {record['outcome']!r} — note that "
            "'issue_obsolete' is NOT an outcome: a stamped child is selectable, so an obsolete "
            "child must stay unstamped and carry pending_disposition instead")
    pending = record.get("pending_disposition")
    if pending is not None and pending not in _PENDING_DISPOSITIONS:
        raise DriverStateError(
            f"pending_disposition must be null or one of {sorted(_PENDING_DISPOSITIONS)}, "
            f"got {pending!r}")
    validate_validated_against(record["from_sha"])
    validate_validated_against(record["to_sha"])
    validate_claims(record["claims"])
    if not _is_int(record["validated_at"]):
        raise DriverStateError("validated_at must be an int epoch")
    return True


def parse_depends_on(body: str) -> list[int]:
    """Return the sorted, de-duplicated issue numbers this body depends on.

    Recognition is narrow so ordinary prose is unlikely to inject a spurious
    dependency — but it is NOT markdown-aware, so a dependency phrase quoted
    inside a blockquote or code fence IS still taken (do not treat this as a
    hard security boundary). Two forms are recognized:
      * a dependency phrase ("depends on #N", "blocked by #N, #M", …) — the
        phrase is matched at word boundaries (so "unblocked by" does NOT count)
        and is skipped when immediately negated ("not blocked by", "no longer
        depends on", and modal forms with a "be"/"get" bridge like "cannot be
        blocked by"); only the immediate ``#N`` list right after the phrase
        (comma/"and"/"&"-separated, each ``#N`` optionally led by an
        "issue"/"PR"/"epic" noun, e.g. "depends on issue #10") is taken, stopping
        at a sentence boundary, so a following sentence ("Depends on #10. See #20
        for context") cannot inject #20. Two phrases on one line each contribute
        their own list; and
      * a task-list checkbox line ("- [ ] #N") that references an issue — counted
        even when the same line also carries a dependency phrase.
    A bare ``#N`` in ordinary prose is NOT a dependency.
    """
    if not body:
        return []
    deps: set[int] = set()
    for line in body.splitlines():
        m = _TASK_LIST_RE.match(line)
        if m:
            deps.add(int(m.group(1)))
        for ph in _DEP_PHRASE_RE.finditer(line):
            if _NEG_BEFORE_RE.search(line[: ph.start()]):
                continue  # negated: a statement of non-dependency
            lst = _DEP_LIST_RE.match(line[ph.end():])
            if lst:
                deps.update(int(n) for n in _HASH_NUM_RE.findall(lst.group(1)))
    return sorted(deps)


def _in_queue_deps(issue: dict, numset: set[int]) -> list[int]:
    """Dependencies of ``issue`` that are present in the campaign queue.

    Dependencies outside the queue are external — this pure helper cannot verify
    their state offline, so callers treat them as already satisfied for ordering
    and readiness (documented in ``docs/multi-issue-driver.md``).

    Fail-closed on a malformed ``depends_on``: if it is present but not a list of
    ints (``bool`` rejected), raise ``DriverStateError`` naming the issue. A
    non-int entry (e.g. the string ``"148"``) would otherwise match nothing in
    ``numset`` and silently impose no edge, dropping a real dependency. In-queue
    ints impose an edge; ints not in the queue stay external/satisfied. Both
    ``topo_sort_issues`` and ``next_ready_issue`` route through here, so both are
    fail-closed on this.
    """
    deps = issue.get("depends_on")
    if deps is None:
        return []
    if not isinstance(deps, list) or not all(_is_int(d) for d in deps):
        raise DriverStateError(
            f"issue #{issue.get('number')} depends_on must be a list of ints"
        )
    return [d for d in deps if d in numset]


def _numbers(issues: list[dict]) -> list[int]:
    """Issue numbers, fail-closed: a missing/non-int number or a duplicate raises
    the typed ``DriverStateError`` (not a bare ``KeyError``) so the module's
    fail-loudly contract holds even on un-validated input."""
    nums: list[int] = []
    for idx, issue in enumerate(issues):
        n = issue.get("number") if isinstance(issue, dict) else None
        if not _is_int(n):
            raise DriverStateError(f"issues[{idx}] missing an integer 'number'")
        nums.append(n)
    if len(set(nums)) != len(nums):
        raise DriverStateError("duplicate issue numbers in queue")
    return nums


def topo_sort_issues(issues: list[dict]) -> list[int]:
    """Return a valid execution order (dependencies before dependents).

    Uses Kahn's algorithm with a min-heap so the order is deterministic (among
    ready nodes the lowest issue number goes first). Raises
    ``DependencyCycleError`` — a ``DriverStateError`` — if a cycle remains, with
    the offending cycle rendered in the message. External dependencies (not in
    the queue) impose no ordering edge.
    """
    nums = _numbers(issues)
    numset = set(nums)
    deps_map = {i["number"]: _in_queue_deps(i, numset) for i in issues}

    indeg = {n: 0 for n in nums}
    adj: dict[int, list[int]] = {n: [] for n in nums}
    for n in nums:
        for d in deps_map[n]:
            adj[d].append(n)
            indeg[n] += 1

    ready = [n for n in nums if indeg[n] == 0]
    heapq.heapify(ready)
    order: list[int] = []
    while ready:
        n = heapq.heappop(ready)
        order.append(n)
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                heapq.heappush(ready, m)

    if len(order) != len(nums):
        remaining = {n for n in nums if indeg[n] > 0}
        cycle = _find_cycle(deps_map, remaining)
        rendered = " -> ".join(f"#{c}" for c in cycle)
        raise DependencyCycleError(f"dependency cycle detected: {rendered}")
    return order


def _find_cycle(deps_map: dict[int, list[int]], nodes: set[int]) -> list[int]:
    """Extract one concrete cycle from ``nodes`` for the error message.

    Follows dependency edges (issue -> its dep) until a node repeats. Restricted
    to ``nodes`` (the still-unresolved set), which is guaranteed to contain a
    cycle when Kahn's algorithm did not consume every node.
    """
    for start in sorted(nodes):
        path: list[int] = []
        seen: set[int] = set()
        cur = start
        while cur in nodes and cur not in seen:
            seen.add(cur)
            path.append(cur)
            nxt = [d for d in deps_map.get(cur, []) if d in nodes]
            if not nxt:
                break
            cur = min(nxt)
        if cur in path:  # closed the loop
            return path[path.index(cur):] + [cur]
    return sorted(nodes)  # fallback: name the unresolved set


# #695 — how a CONFIRMED external issue verdict overlays the file's own status.
#
# The probe is a caller-injected callable returning one of these keys (or None). Only the two
# CONFIRMED-closed verdicts overlay anything: `confirmed_open` means the file's status stands,
# and `unknown` (or None, or a raising probe) deliberately does NOT veto — see
# `effective_issue_statuses`.
PROBE_OVERLAY: dict[str, str] = {
    "confirmed_merged": "merged",
    "confirmed_abandoned": "abandoned",
}


def effective_issue_statuses(issues, issue_state_probe=None) -> tuple[dict, dict]:
    """``({number: effective_status}, {number: overlaid_status})`` for a campaign queue.

    #695: the driver-state file goes stale whenever a child ships outside the driver, so a
    `queued` entry is not evidence the child is unfinished. A caller-supplied probe
    corroborates it, and a CONFIRMED closed verdict overlays the file's value.

    The overlay is applied ONCE, here, so it reaches BOTH dependency evaluation and candidate
    selection. Filtering only the candidate was the first design and the cross-model review
    refuted it: a child whose prerequisite really merged but still reads `queued` would leave
    its dependent blocked forever, so an already-stale campaign reports "no ready child" while
    the prerequisite is sitting merged on GitHub.

    Only `queued` entries are probed. Every other status either was written by a terminal event
    or is a live claim, and probing them would spend a network call per candidate to re-derive
    something the file already knows.

    An `unknown` verdict, a `None`, or a probe that RAISES leaves the file's status alone. That
    direction is deliberate: the probe is corroboration and the file is primary once the
    write-back keeps it correct, so a GitHub outage must not become a total campaign stall.
    A visible duplicate PR is recoverable; a silent stall in an unattended run is not.

    Pure: it calls the injected probe and nothing else, so this module keeps the "no I/O"
    promise its docstring makes.
    """
    effective = {}
    overlaid = {}
    for issue in issues:
        num = issue["number"]
        status = issue.get("status")
        effective[num] = status
        if status != "queued" or issue_state_probe is None:
            continue
        try:
            verdict = issue_state_probe(num)
        except Exception:  # pylint: disable=broad-except
            # Corroboration must never break selection — see the docstring.
            continue
        mapped = PROBE_OVERLAY.get(verdict) if isinstance(verdict, str) else None
        if mapped:
            effective[num] = mapped
            overlaid[num] = mapped
    return effective, overlaid


def next_ready_issue(state: dict, deps_satisfied_by: str = "merged",
                     issue_state_probe=None) -> int | None:
    """Return the first queued issue whose dependencies are satisfied, else None.

    "First" is queue order (the ``issues`` list order). A dependency counts as
    satisfied when its status is in the set implied by ``deps_satisfied_by``
    (``"merged"`` → only ``merged``; ``"pr_open"`` → ``merged`` or ``pr_open``).
    A deferred/abandoned dependency is NOT satisfied, so its dependents stay
    parked while independent issues still advance.

    Precondition: run ``topo_sort_issues`` once at campaign start — that is the
    fail-closed cycle gate (it raises ``DependencyCycleError`` on a cyclic
    queue). This function does NOT re-detect cycles; it returns ``None`` (not an
    error) whenever no queued issue is currently ready, which on an acyclic queue
    means "wait for a dependency to advance." On a never-topo-sorted cyclic queue
    it would return ``None`` forever — run the gate first.

    ``issue_state_probe`` (#695) is the AC2 corroboration: with it supplied, a `queued` entry
    whose real issue is confirmed closed can never be selected, whatever the file says, and a
    confirmed-merged prerequisite satisfies its dependents even though the file still calls it
    `queued`. Omitted → byte-identical to the pre-#695 behaviour, which is what keeps #163's
    pinned contract intact. See `effective_issue_statuses` for the verdict vocabulary and for
    why an unreachable probe does not veto.
    """
    if deps_satisfied_by not in _SATISFIED_BY:
        raise DriverStateError(
            f"deps_satisfied_by must be one of {sorted(_SATISFIED_BY)}, "
            f"got {deps_satisfied_by!r}"
        )
    satisfied = _SATISFIED_BY[deps_satisfied_by]
    issues = state.get("issues", [])
    _numbers(issues)  # fail-closed on missing/non-int/duplicate number
    by_num = {i["number"]: i for i in issues}
    numset = set(by_num)
    effective, _overlaid = effective_issue_statuses(issues, issue_state_probe)
    for issue in issues:
        if effective[issue["number"]] != "queued":
            continue
        deps = _in_queue_deps(issue, numset)
        if all(effective[d] in satisfied for d in deps):
            return issue["number"]
    return None


# #695 — statuses a child can never move AWAY from once recorded.
#
# `deferred` is deliberately NOT here: a parked child can legitimately be re-queued later.
# `merged` and `abandoned` are decisions about a shipped or dropped child, and the resume path
# treats this file as authoritative, so silently regressing one corrupts the very state #695
# exists to keep true.
TERMINAL_STATUSES = frozenset({"merged", "abandoned"})


def record_child_outcome(state: dict, issue: int, status: str) -> dict | None:
    """Record a child's terminal status in a campaign queue. PURE — returns a NEW state.

    #695: nothing wrote this back when a child shipped outside the epic driver, so
    `claude_docs/.driver-state/epic-684-watcher-fires.json` still read
    ``{"number": 687, "status": "queued"}`` after #687 was merged (PR #691) and closed. A
    fresh-session resume, obeying its own correct rule — "derive position from durable state,
    never in-context memory" — then offered #687 as the next ready child.

    Returns ``None`` for "nothing to do", which is exactly `_locked_state_update`'s abort
    signal, so the caller writes no file at all in those cases:

    - the queue does not name ``issue`` (a single-session run outside this campaign), or
    - the child already carries ``status`` (idempotent: the write is invoked at BOTH the merge
      confirmation and the run's Step-16 reconciliation, and the second must be free).

    Raises `DriverStateError` on a CALLER error, because these are bugs at the call site rather
    than states of the world:

    - ``status`` outside `VALID_STATUSES` — the vocabulary IS the contract, since
      `next_ready_issue` compares against it exactly; free text would make the file unreadable.
    - a **regression away from a terminal status** (see `TERMINAL_STATUSES`). Membership in
      `VALID_STATUSES` proves a legal *word*, not a legal *transition* — the cross-model design
      review caught that a merged child could otherwise be walked back to `queued`.
    """
    if not isinstance(status, str) or isinstance(status, bool) \
            or status not in VALID_STATUSES:
        raise DriverStateError(
            f"status must be one of {sorted(VALID_STATUSES)}, got {status!r}")
    issues = state.get("issues", [])
    _numbers(issues)  # fail-closed on missing/non-int/duplicate number
    current = None
    for entry in issues:
        if entry["number"] == issue:
            current = entry.get("status")
            break
    if current is None:
        return None                      # not this campaign's child — write nothing
    if current == status:
        return None                      # idempotent
    if current in TERMINAL_STATUSES:
        raise DriverStateError(
            f"refusing to move issue #{issue} from terminal status {current!r} to "
            f"{status!r} — the resume path treats this file as authoritative, so regressing a "
            "shipped child is how a merged issue gets re-run")
    new = dict(state)
    new["issues"] = [dict(e, status=status) if e["number"] == issue else dict(e)
                     for e in issues]
    return new


def _is_int(x) -> bool:
    # bool is a subclass of int in Python; reject it as a "number".
    return isinstance(x, int) and not isinstance(x, bool)


# --------------------------------------------------------------------------- #
# #569: fresh-session-per-child handoff (process-boundary continuity)
# --------------------------------------------------------------------------- #
FRESH_SESSION_MODE = "fresh-session"

# Terminal-backend verdicts (`launcher_lib.select_launch_mode`) that CAN cross the process
# boundary. `single_session` is deliberately absent: it is the verdict's way of saying "keep the
# current loop". Anything unrecognised is treated the same way — see `fresh_session_available`.
LAUNCHABLE_MODES = frozenset({"herdr", "pane_less"})


BIND_DIRECTIVE = "/rawgentic:switch"

# A bind directive WITH a project argument. The argument is the whole point: Step-4 review finding,
# and it is the defect that made the first version of this fix useless. `/rawgentic:switch` with no
# argument does not bind anything — the switch skill enters LIST MODE and asks a human "which project
# do you want to bind this session to?" (`skills/switch/SKILL.md`, Steps 1-2). An unattended
# successor obeying a bare command therefore sits at a question, never appends the registry row, and
# has its pane closed when `project_switched` exhausts. So the guard checks the command's SHAPE.
_BIND_WITH_PROJECT = re.compile(re.escape(BIND_DIRECTIVE) + r"\s+(?!off\b)(\S+)")

# A project NAME, not arbitrary text. Step-11 finding: `project` is interpolated into prompt text
# that is sent to a pane with `send-text`, and it was validated only as "a non-empty string" — so
# control characters or instruction-like prose could ride into a prompt. Workspace project names are
# directory-ish tokens; anything else is refused before interpolation.
_PROJECT_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def valid_project_name(project) -> bool:
    """Is this a workspace project NAME safe to interpolate into a pane-bound prompt?"""
    return isinstance(project, str) and bool(_PROJECT_NAME_RE.match(project.strip()))

# The bind must be the prompt's FIRST instruction, and that is now checked as a PREFIX rather than
# guessed at. Two review passes rejected the earlier approaches for the same underlying reason: a
# positional/keyword classifier cannot establish semantic ordering, and any proxy for it admits
# "read this first, then bind" — which is precisely the behaviour that burns the 120 s budget. A
# prefix check needs no classification at all, so the canonical builders put the bind first and the
# guard simply confirms it.
# Exactly ONE optional leading marker token may precede the bind. `prompt_marker` is
# caller-supplied and opaque by contract (the launcher matches it as a plain substring to prove
# `prompt_landed`), so pinning the canonical `[rawgentic-midchild:N:G]` shape here would refuse every
# other caller's marker. A single whitespace-free token cannot express an instruction that consumes
# the 120 s budget, which is what keeps this a prefix check rather than a classifier.
_BIND_PREFIX_SKIP = re.compile(r"\A\s*(?:(?!" + re.escape(BIND_DIRECTIVE) + r")\S+\s+)?")


def resume_prompt_binds_first(prompt, project=None) -> tuple[bool, str]:
    """Does this resume prompt OPEN with a valid bind for `project`? (#682)

    `perform_handoff` gives a successor 120 s to bind and append to the session registry, then
    declares `failed_step: project_switched` and CLOSES ITS PANE — a silent, expensive failure: a
    clean-looking `failed_step`, a closed pane, and the successor's finished work lost.

    Three things are checked, all mechanical:

    1. **The prompt starts with the bind**, after an optional mid-child marker. A PREFIX, not a
       proxy. Two reviews refuted the alternatives: a keyword-position classifier produced both a
       false positive and a false negative in one pass, and any "appears early enough" rule still
       accepts "read the handoff first, then bind".
    2. **The command carries a project argument.** A bare `/rawgentic:switch` does not bind: the
       skill enters LIST MODE and waits for a human, so the registry row never appears.
    3. **That argument equals `project` as a whole token.** Substring matching accepted
       `rawgentic-next` for `rawgentic` and would have bound the successor to the wrong project.

    `project` is REQUIRED. An earlier version made it optional and fell back to "does the command
    have any argument", which accepts the English word after a bare directive — a guard that admits
    the exact defect it exists to stop.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        return False, "the resume prompt is not a non-empty string"
    if not valid_project_name(project):
        return False, (f"no valid project name was supplied to validate against ({project!r}); the "
                       "bind cannot be checked, and an unchecked bind is what closes the pane at "
                       "120 s")
    want = project.strip()
    body = prompt[_BIND_PREFIX_SKIP.match(prompt).end():]
    match = _BIND_WITH_PROJECT.match(body)
    if match is None:
        if body.startswith(BIND_DIRECTIVE):
            return False, (f"the prompt opens with a bare {BIND_DIRECTIVE!r} and no project "
                           "argument — that enters the switch skill's LIST MODE and waits for a "
                           "human, so the registry row never appears (#682)")
        return False, (f"the resume prompt does not OPEN with {BIND_DIRECTIVE} {want} — anything "
                       "before the bind can consume the 120 s `project_switched` budget, and "
                       "'bind eventually' is exactly the ordering #682 is about")
    got = match.group(1).rstrip(".,;:")
    if got != want:
        return False, (f"the prompt binds {got!r}, not the expected project {want!r} — a successor "
                       "bound to the wrong project never appends the row this handoff verifies")
    return True, ""


def _bind_opening(project: str) -> str:
    """The prompt's OPENING: the bind command itself, then why it has to come first.

    The command leads because `resume_prompt_binds_first` checks a PREFIX. Two review passes killed
    the softer alternatives for the same underlying reason — a keyword/position rule either
    misclassifies valid prompts (it refused "Do not run git fetch before binding. FIRST run
    /rawgentic:switch rawgentic") or accepts "read the handoff first, then bind", which IS the
    ordering that burns the 120 s budget. A prefix needs no classification.
    """
    return (f"{BIND_DIRECTIVE} {project.strip()} — run this FIRST, before reading any file and "
            "before checking any fact. An unbound session cannot Read anything under projects/, and "
            "the launcher closes this pane if the bind has not landed in "
            "claude_docs/session_registry.jsonl within 120 seconds. Bind, then verify — never the "
            "other way round.")


def _lead_with_bind(body: str, project, include_bind: bool) -> str:
    """Put the bind in front of `body`, or capitalise `body` when the caller sends the bind itself.

    ONE place decides this for both builders (#694). The two prompts have disagreed before, and a
    flag copied into each of them is how that happens again.

    `include_bind=False` is for the herdr handoff path ONLY, where `perform_handoff` sends
    `/rawgentic:switch <project>` as SEND 1 — its own turn, gated on the session-registry row —
    and would otherwise make the successor run the switch skill twice. Every other consumer keeps
    the bind INSIDE the prompt, which is why True is the default: the interactive hand-back and
    the `claude -p` fallback launch each deliver exactly one prompt and have no second send to put
    a bind in.
    """
    if include_bind:
        return f"{_bind_opening(project)} THEN — {body}"
    return body[:1].upper() + body[1:]


def _build_resume_prompt(state: dict, next_issue: int, project=None,
                         include_bind: bool = True) -> str:
    """The canonical idempotent, state-re-deriving resume prompt for a fresh session (no
    in-context memory). Used for the interactive hand-back + a direct `claude -p` spawn; the
    crontab launcher's own static prompt conforms to this (design §4 SR1).

    #682: the bind LEADS — it is the first thing in the prompt, with a project argument and its
    reason. This used to open with "Re-bind the project (/rawgentic:switch), git fetch origin, read
    the driver-state ...": a comma list that reads as parallel tasks rather than an ordering, and a
    bare directive that would have entered the switch skill's list mode and waited for a human.

    #694 adds `include_bind`. It does NOT retract #682 — the bind still has to come first, and on
    every path that delivers one prompt it still leads the prompt. What changed is that the herdr
    handoff has a second send available, so it makes the bind its own verified turn instead, and
    a prefix check stops having to stand in as a proxy for "first". See `_lead_with_bind`.
    """
    epic = state.get("epic")
    epic_ref = f"epic #{epic}" if _is_int(epic) else state.get("campaign", "the campaign")
    body = (
        f"fresh-session resume for {epic_ref}: git fetch origin, "
        "read the driver-state + epic-<N>-autorun-log, and run the next ready child (currently "
        f"#{next_issue}) via /rawgentic:implement-feature to full WF2 completion. Derive position "
        "from durable state, never in-context memory; never re-do a merged/closed child; restate "
        "the run's auth grant. On a blocker, post the ERROR comment and end so the next fresh "
        "session continues."
    )
    return _lead_with_bind(body, project, include_bind)


def fresh_session_handoff(state: dict, *, mode: str, project=None,
                          include_bind: bool = True, issue_state_probe=None) -> dict:
    """Decide the process-boundary handoff after a child reaches a terminal outcome (#569).

    Returns an explicit disposition (NEVER a bare None — design §4 [2]):
    - ``{"outcome": "single_session"}`` when ``mode`` is not the fresh-session mode: no boundary,
      the driver loops in-session exactly as today (byte-identical default).
    - ``{"outcome": "complete"}`` ONLY when EVERY child is ``merged`` — the sole epic-close trigger.
    - ``{"outcome": "ready", "next_issue", "generation", "campaign", "resume_prompt"}`` when a
      queued dependency-satisfied child exists (``generation`` is the monotonic claim token).
    - ``{"outcome": "blocked"}`` when unmerged children remain but none is ready (all
      deferred/abandoned/dependency-blocked) — the epic stays OPEN; NEVER conflated with complete.
    """
    if mode != FRESH_SESSION_MODE:
        return {"outcome": "single_session"}
    issues = state.get("issues", [])
    _numbers(issues)  # fail-closed on missing/non-int/duplicate number
    # #695 AC2: the overlay reaches the COMPLETE verdict too, not just selection. A campaign
    # whose last child shipped outside the driver reads `queued` on disk, and without this it
    # would never report complete — the epic would stay open forever with nothing runnable,
    # which is the same stale-file defect wearing a different outcome.
    effective, _overlaid = effective_issue_statuses(issues, issue_state_probe)
    if issues and all(effective[i["number"]] == "merged" for i in issues):
        return {"outcome": "complete"}
    # This is the ONE production selection site, so the probe has to arrive here or the
    # corroboration is dead code. `_cmd_handoff` supplies the real `gh api graphql` probe.
    nxt = next_ready_issue(state, issue_state_probe=issue_state_probe)
    if nxt is not None:
        chosen = project or state.get("project")
        if not valid_project_name(chosen):
            # Step-11 finding: the guard used to run inside `perform_handoff` — i.e. AFTER the
            # contract has the predecessor call `open_handoff`, which bumps `generation` and writes
            # `handoff_pending`. A refusal there stranded an unclaimed generation that every retry
            # refused identically. Refusing at DISPOSITION time means `open_handoff` (which acts only
            # on "ready") writes nothing, so there is nothing to roll back.
            return {"outcome": "no_project", "next_issue": nxt,
                    "errors": [f"no valid project name for the bind ({chosen!r}); a resume prompt "
                               "without one cannot bind, and the successor's pane would be closed "
                               "when project_switched exhausts (#682)"]}
        generation = (state.get("generation") if _is_int(state.get("generation")) else 0) + 1
        return {"outcome": "ready", "next_issue": nxt, "generation": generation,
                "campaign": state.get("campaign", ""),
                "resume_prompt": _build_resume_prompt(state, nxt, chosen,
                                                      include_bind=include_bind)}
    return {"outcome": "blocked"}


def open_handoff(state: dict, disposition: dict, *, now_ts: int) -> dict:
    """Persist a `ready` disposition as durable handoff state (Step-11 F2 fix — the generation
    counter MUST advance, else a later handoff reuses it and the claim can't tell a new handoff
    from a replay). Returns new_state with `generation` bumped AND `handoff_pending` written
    atomically; the caller persists it. A non-`ready` disposition returns state unchanged."""
    if disposition.get("outcome") != "ready":
        return state
    gen = disposition["generation"]
    new = dict(state)
    new["generation"] = gen
    pending = {"generation": gen, "next_issue": disposition["next_issue"],
               "written_ts": now_ts}
    # #665: a mid-child disposition carries a `kind` discriminator and a durable `position`.
    # Both are copied through ONLY when present, so a #569 child-boundary handoff writes the
    # exact three-key record it always did (pinned by test_shape_is_byte_identical...).
    kind = disposition.get("kind")
    if kind is not None:
        pending["kind"] = kind
    position = disposition.get("position")
    if position is not None:
        pending["position"] = dict(position) if isinstance(position, dict) else position
    new["handoff_pending"] = pending
    return new


def fresh_session_available(state: dict, *, launcher_armed: bool, handoff_writable: bool,
                            fresh_launch_supported: bool,
                            launch_mode: str | None = None) -> tuple[bool, str]:
    """Pre-launch AC6 check (pure over injected probes): can the process boundary be crossed?
    **Step-11 F1 (Critical) fix:** an armed launcher is NOT enough — it must POSITIVELY advertise
    fresh-launch (no-`--resume`) support, else fresh-session mode would falsely activate on the
    resume-first launcher and SILENTLY defeat AC1 (the successor reloads the prior transcript).
    Fail-open — a False result degrades to the single-session loop with a visible marker, never
    aborts. Until the launcher advertises support, this returns False → single-session (safe).

    ``launch_mode`` (#611) is the terminal-backend verdict from
    ``launcher_lib.select_launch_mode``. It is OPTIONAL so every #569 caller keeps its exact
    contract by omitting it. When supplied it is enforced HERE because this — not the launcher —
    is the boundary the driver actually consults: a herdr-gated project with no reachable pane
    yields ``single_session``, and handing it a pane-less successor would retire a working
    predecessor for one already known to die at its first build-seat dispatch. An unrecognised
    mode fails CLOSED to the single-session loop; fail-open means degrading, never launching on
    a verdict nobody understands.
    """
    if not launcher_armed:
        return (False, "no durable launcher armed")
    if not fresh_launch_supported:
        return (False, "launcher does not advertise fresh-launch (no-resume) support")
    if not handoff_writable:
        return (False, "handoff path not writable")
    if launch_mode is None:
        # #611 Step-11 pass-4 High 2: for a campaign that HAS a process boundary, an omitted
        # verdict must not read as "launchable". Leaving it optional made the guard depend on
        # skill prose remembering to pass it — and by the time the launcher discovers the truth
        # the driver has already written `handoff_pending` and ended, so "keep the current
        # loop" is no longer possible. Callers with no boundary (single-session) are unaffected.
        if state.get("session_mode") == FRESH_SESSION_MODE:
            return (False, "terminal-backend launch verdict not supplied — refusing to cross "
                           "the boundary on an unknown backend; keeping the single-session loop")
        return (True, "ok")
    if launch_mode not in LAUNCHABLE_MODES:
        return (False, f"launch mode {launch_mode!r} cannot cross the process boundary — "
                       "keeping the single-session loop")
    return (True, "ok")


def handoff_reclaimable(state: dict, *, now_ts: int, lease_s: int) -> bool:
    """A claimed-but-never-started handoff whose claim is older than the lease is RECLAIMABLE
    (Step-11 F3 fix — a successor that claimed then crashed before `started` must not strand the
    run forever). A started claim is never reclaimable (takeover succeeded)."""
    claim = state.get("handoff_claim")
    if not isinstance(claim, dict) or claim.get("started"):
        return False
    claimed_at = claim.get("claimed_at")
    return _is_int(claimed_at) and (now_ts - claimed_at) > lease_s


def handoff_claim(state: dict, generation: int, *, claimant: str, now_ts: int,
                  lease_s: int = 1800) -> tuple[bool, dict]:
    """Atomically CLAIM the pending handoff for ``generation`` (exactly-one-successor, design §5/§6).
    Pure. Returns ``(True, new_state)`` only when the claim is legitimate:
    - a `handoff_pending` exists whose generation == ``generation`` == the state's current
      `generation` (**F4:** monotonic — reject a stale/`>`/`<` mismatch, and non-negative ints only);
    - AND it is unclaimed, OR the prior claim is reclaimable (**F3:** crashed pre-`started`, past lease).
    A wrong/stale generation, a still-in-progress claim, or a started claim returns ``(False, state)``
    unchanged. The successor calls ``handoff_ack_started`` AFTER rebuilding state + starting the child."""
    pend = state.get("handoff_pending")
    if not isinstance(pend, dict):
        return (False, state)
    cur = state.get("generation")
    if not (_is_int(generation) and generation >= 0 and _is_int(pend.get("generation"))
            and _is_int(cur) and pend["generation"] == generation == cur):
        return (False, state)  # F4: monotonic, current, non-negative — no stale replay
    claim = state.get("handoff_claim")
    if isinstance(claim, dict) and claim.get("generation") == generation:
        if claim.get("started") or not handoff_reclaimable(state, now_ts=now_ts, lease_s=lease_s):
            return (False, state)  # already taken over, or a live in-progress claim
    new = dict(state)
    new["handoff_claim"] = {"generation": generation, "claimant": claimant,
                            "claimed_at": now_ts, "started": False}
    return (True, new)


def handoff_ack_started(state: dict, generation: int, claimant: str) -> tuple[bool, dict]:
    """The successor marks its claim `started` AFTER rebuilding durable state + starting the child
    (Step-11 F3). Only the matching claimant/generation may ack; else ``(False, state)``."""
    claim = state.get("handoff_claim")
    if not isinstance(claim, dict) or claim.get("generation") != generation \
            or claim.get("claimant") != claimant:
        return (False, state)
    new = dict(state)
    new["handoff_claim"] = {**claim, "started": True}
    return (True, new)


# --------------------------------------------------------------------------- #
# #665: interactive mid-child handoff — the case #569 deliberately does not cover
# --------------------------------------------------------------------------- #
# #569 crosses a CHILD BOUNDARY: a child ends, the session ends, a fresh one starts the next
# child. This is the other case — "I am mid-child, out of context, hand me over and keep going"
# — whose trigger is context exhaustion, never cron (epic #667, owner decision D-16).
#
# It reuses #569's primitives rather than adding a second mechanism: the disposition below is
# shaped so `open_handoff` consumes it unchanged, and the successor claims/acks through
# `handoff_claim`/`handoff_ack_started` exactly as a fresh-session successor does. A parallel
# handoff path is the defect the issue's own rewrite exists to prevent.
MID_CHILD_HANDOFF_KIND = "mid_child"

# Every field is required. A PARTIAL position is worse than none: the successor rebuilds from
# it, and the teardown gate compares live state against it — a hole there reads as agreement.
_MID_CHILD_POSITION_FIELDS: tuple[str, ...] = (
    "issue", "step", "branch", "test_baseline", "predecessor_pane",
    "predecessor_session", "goal_condition", "project", "project_path", "repo_root",
)


def mid_child_marker(issue: int, generation: int) -> str:
    """The token the launcher's `prompt_landed` check matches on.

    Generation-bound deliberately: an unqualified marker would also match a PREVIOUS handoff's
    resume prompt still present in the same transcript, so "the prompt landed" could pass on
    evidence produced by a handoff that already failed.
    """
    return f"[rawgentic-midchild:{issue}:{generation}]"


def validate_mid_child_position(position) -> tuple[bool, list[str]]:
    """Fail-closed structural check of a mid-child position record.

    Deliberately does NOT validate the pane id's grammar: that is
    `launcher_lib.validate_pane_id`'s job and it runs there before any herdr call. Importing
    launcher_lib here would invert the lazy-import direction that keeps launcher_lib optional.
    """
    if not isinstance(position, dict):
        return (False, ["position must be a JSON object"])
    errors: list[str] = []
    for field in _MID_CHILD_POSITION_FIELDS:
        if field not in position:
            errors.append(f"position.{field} is required")
            continue
        value = position[field]
        if field == "issue":
            if not _is_int(value):
                errors.append("position.issue must be an int")
        elif not isinstance(value, str) or not value.strip():
            errors.append(f"position.{field} must be a non-empty string")
    return (not errors, errors)


def _build_mid_child_resume_prompt(state: dict, position: dict, generation: int,
                                   include_bind: bool = True) -> str:
    """The successor's instructions, built HERE next to `_build_resume_prompt` so the two
    cannot drift. It re-derives position from durable state — never a copied task list, which
    is session-scoped and, measured live on 2026-07-27, held 30 subjects spanning three
    unrelated projects.

    The marker stays FIRST either way — it is what `prompt_landed` matches, and it must survive
    whether or not the bind travels inside the prompt (#694). See `_lead_with_bind`.
    """
    epic = state.get("epic")
    epic_ref = f"epic #{epic}" if _is_int(epic) else state.get("campaign", "the campaign")
    body = (
        f"mid-child resume for {epic_ref}, "
        f"child #{position['issue']}, interrupted at WF2 step {position['step']}: "
        f"`git fetch origin && git checkout {position['branch']}`. That branch "
        "already carries this child's committed work, so do not re-implement it. The recorded "
        f"test baseline is {position['test_baseline']} — diff against it; do not re-measure it "
        "as if it were fresh. Re-derive every other fact from claude_docs/.driver-state and the "
        "epic autorun log, never from in-context memory, and never re-do a merged or closed "
        "child. Claim this handoff, and only once you are actually on that branch with position "
        "rebuilt, retire the predecessor LAST via "
        "`python3 hooks/launcher_lib.py retire-predecessor`. On a blocker, post the ERROR "
        "comment on the child issue and end so the next session can continue."
    )
    return (f"{mid_child_marker(position['issue'], generation)} "
            f"{_lead_with_bind(body, position.get('project') or '', include_bind)}")


def mid_child_handoff(state: dict, *, position, include_bind: bool = True) -> dict:
    """Decide a mid-child (context-driven) handoff. Mirrors `fresh_session_handoff`'s
    disposition contract so `open_handoff` consumes the result unchanged.

    Outcomes: ``invalid_position`` (with ``errors``), ``no_active_child``,
    ``position_mismatch`` (with ``errors``), or ``ready``.

    Unlike `fresh_session_handoff` this does NOT gate on ``mode == FRESH_SESSION_MODE``: a
    context-driven handover is cron-free, and gating on that mode would refuse exactly the
    in-session campaigns it exists to serve. That is also why this is a sibling function
    rather than a flag on `fresh_session_handoff`, whose ``single_session`` verdict is
    load-bearing for #569 and must keep its meaning.
    """
    ok, errors = validate_mid_child_position(position)
    if not ok:
        return {"outcome": "invalid_position", "errors": errors}
    issues = state.get("issues", [])
    _numbers(issues)  # fail-closed on missing/non-int/duplicate number
    active = [i["number"] for i in issues
              if isinstance(i, dict) and i.get("status") == "in_progress"]
    if not active:
        return {"outcome": "no_active_child"}
    # More than one in_progress is corrupt state (validate_driver_state flags it too), so the
    # position cannot identify THE active child even if it names one of them.
    if len(active) != 1 or position["issue"] != active[0]:
        return {"outcome": "position_mismatch",
                "errors": [f"position.issue {position['issue']} is not the single in_progress "
                           f"child {active}"]}
    generation = (state.get("generation") if _is_int(state.get("generation")) else 0) + 1
    return {"outcome": "ready", "next_issue": active[0], "generation": generation,
            "campaign": state.get("campaign", ""), "kind": MID_CHILD_HANDOFF_KIND,
            "position": dict(position),
            "resume_prompt": _build_mid_child_resume_prompt(state, position, generation,
                                                            include_bind=include_bind)}


def validate_driver_state(state: dict) -> tuple[bool, list[str]]:
    """Minimal readability check for a driver-state object (schema v1 and v2).

    Deliberately lightweight (no jsonschema dependency) so the driver — and any
    campaign in another repo — can sanity-check a state file with the stdlib
    alone. The committed ``queue.schema.json`` is the fuller contract-of-record,
    validated against the example files in the test suite. ``depends_on`` is
    optional, so a v1 file (no dependency arrays) validates unchanged (#163 AC7).

    Scope: structure only. It does NOT check acyclicity — ``topo_sort_issues`` is
    the cycle gate, so a structurally-valid state can still contain a dependency
    cycle. A caller must run ``topo_sort_issues`` before relying on the DAG order.
    """
    errors: list[str] = []
    if not isinstance(state, dict):
        return False, ["driver-state must be a JSON object"]

    sv = state.get("schema_version")
    if not _is_int(sv):
        errors.append("schema_version must be an int")
    elif sv not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(
            f"unknown schema_version {sv} "
            f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
        )

    camp = state.get("campaign")
    if not isinstance(camp, str) or not camp:
        errors.append("campaign must be a non-empty string")

    issues = state.get("issues")
    if not isinstance(issues, list):
        errors.append("issues must be a list")
        return len(errors) == 0, errors

    seen: set[int] = set()
    for idx, issue in enumerate(issues):
        if not isinstance(issue, dict):
            errors.append(f"issues[{idx}] must be an object")
            continue
        n = issue.get("number")
        if not _is_int(n):
            errors.append(f"issues[{idx}].number must be an int")
        else:
            if n in seen:
                errors.append(f"duplicate issue number {n}")
            seen.add(n)
        st = issue.get("status")
        if st not in VALID_STATUSES:
            errors.append(
                f"issues[{idx}].status {st!r} not in {sorted(VALID_STATUSES)}"
            )
        do = issue.get("depends_on")
        if do is not None and (
            not isinstance(do, list) or not all(_is_int(x) for x in do)
        ):
            errors.append(f"issues[{idx}].depends_on must be a list of ints")

    # Serial-active invariant: the driver builds one issue at a time, so at most
    # one issue may be in_progress. pr_open is NOT counted — PRs may accumulate
    # awaiting human merge (the headless stacked-PR flow, deps_satisfied_by=
    # "pr_open"). More than one in_progress is corrupt state that makes resumption
    # ambiguous (which issue is "the" active build?).
    active = [
        i.get("number") for i in issues
        if isinstance(i, dict) and i.get("status") == "in_progress"
    ]
    if len(active) > 1:
        errors.append(
            f"at most one issue may be in_progress (one build at a time; pr_open "
            f"may accumulate awaiting merge); found {len(active)}: {active}"
        )

    return len(errors) == 0, errors


def campaign_goal_text(state: dict) -> str:
    """Build the ONE epic-level `/goal` text for a campaign kickoff (#192).

    The driver runs this at campaign start (the `validate_campaign_start` seam):
    it enumerates the epic anchor + the topo-ordered child queue into a single
    goal with a tolerant escape clause, so the session's Stop-hook guards the
    WHOLE campaign rather than a per-issue goal that lets the run quit after any
    one slot. The driver then (a) emits this text for the owner to run (a skill
    cannot self-set `/goal`) and (b) exports `RAWGENTIC_EPIC_GOAL=<epic>` so each
    child WF2 run's Step 1b defers to it instead of emitting a clobbering
    per-issue goal.

    Raises ``DriverStateError`` if the state has no integer ``epic`` (a campaign
    goal is meaningless without the epic anchor), and ``DependencyCycleError``
    if the child queue has a dependency cycle (surfaced, never silently mis-ordered).
    """
    epic = state.get("epic")
    if not _is_int(epic):
        raise DriverStateError(
            "campaign_goal_text requires an integer 'epic' anchor")
    ordered = topo_sort_issues(state.get("issues", []))
    # Local import keeps driver_lib importable without plan_lib for its pure DAG
    # helpers; both functions are side-effect-free.
    from plan_lib import build_goal_text
    return build_goal_text(epic, [], variant="campaign", child_issues=ordered)


def validate_campaign_start(state: dict, headless: bool = False) -> tuple[bool, list[str]]:
    """Validate a driver state is fit to *start* a campaign, else return errors.

    Structural readability (``validate_driver_state``) plus the start-only rule
    from #163 AC5: a **headless** campaign MUST be anchored to an ``epic`` issue,
    because in headless mode the epic is the STATUS/QUESTION channel — a headless
    run with no epic has no way to surface a blocker, so it must refuse to start
    rather than silently degrade.
    """
    ok, errors = validate_driver_state(state)
    errors = list(errors)
    if headless and not _is_int(state.get("epic")):
        errors.append(
            "headless campaign requires an epic issue number "
            "(the STATUS/QUESTION channel) — refusing to start"
        )
    return len(errors) == 0, errors
