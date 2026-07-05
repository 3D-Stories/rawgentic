#!/usr/bin/env python3
"""Multi-issue driver — dependency-DAG + schema-readability helpers (#163).

Backs the documented multi-issue driver PATTERN (``docs/multi-issue-driver.md``).
The driver itself is a *documented orchestration pattern* (design #134), not a
skill; this module supplies the small pieces of that pattern whose behavior the
issue's acceptance criteria make worth unit-testing rather than describing in
prose:

- ``parse_depends_on(body)`` — extract issue-number dependencies from an issue
  body (strict, prompt-injection-safe: only ``#<digits>`` under a recognized
  dependency phrase or task-list checkbox is taken; free-text numbers are not).
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
# ("unblocked by" must not count). Each phrase contributes the `#<digits>` in its
# own segment (up to the next phrase), so order on the line does not matter.
_DEP_PHRASE_RE = re.compile(r"(?<![a-z])(?:depends?[ -]on|blocked[ -]by)(?![a-z])")
# Immediate negation right before a phrase ("not blocked by", "no longer depends
# on") — the phrase is then a statement of NON-dependency and is skipped. This
# keeps ordinary issue-body prose from injecting a false dependency.
_NEG_BEFORE_RE = re.compile(
    r"\b(?:not|never|cannot|can't|won't|doesn't|isn't|no longer)\s+$"
)
# Task-list checkbox referencing an issue, e.g. "- [ ] #101" / "* [x] #102".
_TASK_LIST_RE = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s*#(\d+)\b")
_HASH_NUM_RE = re.compile(r"#(\d+)\b")


class DriverStateError(ValueError):
    """Raised on a malformed driver-state or an invalid driver operation."""


class DependencyCycleError(DriverStateError):
    """Raised (fail-closed) when the dependency graph contains a cycle."""


def parse_depends_on(body: str) -> list[int]:
    """Return the sorted, de-duplicated issue numbers this body depends on.

    Only two forms are recognized, so untrusted issue-body prose cannot inject a
    spurious dependency:
      * a dependency phrase ("depends on #N", "blocked by #N, #M", …) — the
        phrase is matched at word boundaries (so "unblocked by" does NOT count)
        and is skipped when immediately negated ("not blocked by", "no longer
        depends on"); every ``#<num>`` in that phrase's segment is taken, so the
        order of two phrases on one line does not drop deps; and
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
        low = line.lower()
        phrases = list(_DEP_PHRASE_RE.finditer(low))
        for i, ph in enumerate(phrases):
            if _NEG_BEFORE_RE.search(low[: ph.start()]):
                continue  # negated: a statement of non-dependency
            end = phrases[i + 1].start() if i + 1 < len(phrases) else len(line)
            segment = line[ph.end():end]
            deps.update(int(n) for n in _HASH_NUM_RE.findall(segment))
    return sorted(deps)


def _in_queue_deps(issue: dict, numset: set[int]) -> list[int]:
    """Dependencies of ``issue`` that are present in the campaign queue.

    Dependencies outside the queue are external — this pure helper cannot verify
    their state offline, so callers treat them as already satisfied for ordering
    and readiness (documented in ``docs/multi-issue-driver.md``).
    """
    return [d for d in issue.get("depends_on", []) if d in numset]


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


def next_ready_issue(state: dict, deps_satisfied_by: str = "merged") -> int | None:
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
    for issue in issues:
        if issue.get("status") != "queued":
            continue
        deps = _in_queue_deps(issue, numset)
        if all(by_num[d].get("status") in satisfied for d in deps):
            return issue["number"]
    return None


def _is_int(x) -> bool:
    # bool is a subclass of int in Python; reject it as a "number".
    return isinstance(x, int) and not isinstance(x, bool)


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

    return len(errors) == 0, errors
