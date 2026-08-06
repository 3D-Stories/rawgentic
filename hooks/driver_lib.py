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

Pure, stdlib-only, no I/O and no side effects when imported — safe to import
from the driver pattern, the test suite, or a ``python3 -c`` one-liner in the
docs.

Not a CLI: direct invocation (``python3 hooks/driver_lib.py …``) refuses loudly
with exit 2 (#905) — the runnable commands live in ``hooks/launcher_lib.py``. A
silent rc-0 here was once read as a passing gate while the real gate refused.
"""
import copy
import hashlib
import heapq
import re

# Canonical driver-state statuses (design #134 status machine).
# DELIBERATELY does NOT include the terminal-for-now waits. Those are campaign-level and
# live in the additive top-level `campaign_wait` object instead (#943): this vocabulary
# is closed and enforced twice (`record_child_outcome`, `validate_driver_state`) with
# three further closed sets keyed off it (`_DISPOSED_STATUSES`, `TERMINAL_STATUSES`, the
# `pr_open` transition map), so adding a word here would silently change all of their
# meanings. `additionalProperties: true` permits new FIELDS, never new `status` VALUES.
VALID_STATUSES = frozenset(
    {"queued", "in_progress", "pr_open", "merged", "deferred", "abandoned"}
)

# Campaign-level terminal-for-now states (#943, epic #871 AC 5). "Terminal for now"
# because a Stop-hook goal loop must be able to read an HONEST wait: a paused campaign
# is neither complete nor failing, and treating it as unmet made the hook nag an
# owner-ordered pause three times in one measured run.
CAMPAIGN_WAIT_STATUSES = frozenset({"waiting_for_owner", "waiting_for_reset"})

# `clears_when` is required with the rest: a pause whose exit condition nobody can state
# is a stall wearing a pause's clothes.
_CAMPAIGN_WAIT_FIELDS = ("status", "reason", "blocker_id", "entered_at", "clears_when")

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
_CITATION_EXTENSIONS = frozenset({
    "py", "md", "json", "jsonl", "sh", "yml", "yaml", "toml", "js", "ts", "tsx",
    "html", "css", "cfg", "ini", "txt", "patch", "lock",
})
_MAX_PATH_COMPONENTS = 12
_MAX_COMPONENT_LEN = 96
_PATH_SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}"

# Tokenised, not regex-substring. The Step-8a reviews (2026-08-02) confirmed by execution
# that the previous regex matched EXTENSION PREFIXES — `src/view.tsx` extracted as
# `src/view.ts`, `hooks/a.pyc` as `hooks/a.py`, `data/run.jsonl` as `data/run.json` — because
# the alternation had no right boundary and listed `ts` before `tsx`. A false *resolving*
# prefix then yields `paths` with a WRONG set, whose empty intersection produces `quick`.
# That is the single dangerous misclassification for this design, so the grammar is no longer
# expressed as a substring match at all: tokens are split out first and then validated by
# COMPONENT, with the extension compared by exact set membership.
# NOTE: `_` and `~` are deliberately NOT delimiters. They are markdown emphasis markers,
# but they are also ordinary filename characters — splitting on `_` would shatter every
# `test_driver_lib.py` in the corpus, which is a far worse failure than missing an emphasised
# path. Caught by self-testing this rewrite before committing it.
_TOKEN_SPLIT_RE = re.compile(r"[\s`\"'<>()\[\]{},;|]+")
_URL_SCHEME_RE = re.compile(r"\A[A-Za-z][A-Za-z0-9+.-]{0,31}://")
# `path:84`, `path:84-85`, `path#L84`, `path#L84-L90`, `path@84`.
_CITATION_SUFFIX_RE = re.compile(r"(?::\d{1,6}(?:-\d{1,6})?|\#L\d{1,6}(?:-L?\d{1,6})?|@\d{1,6})\Z")
# A single path component. A LEADING DOT is allowed (`.github`, `.gitignore`) — the previous
# grammar rejected those and also refused every root-level file such as `README.md`, so a body
# citing one looked citation-free. `.` and `..` are rejected explicitly below, by component,
# rather than by a `".." in text` substring test that also rejected legitimate names.
_COMPONENT_RE = re.compile(r"\A\.?[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")

CITATION_PATTERNS = (_TOKEN_SPLIT_RE, _URL_SCHEME_RE, _CITATION_SUFFIX_RE, _COMPONENT_RE)


def _candidate_path(token: str):
    """One token -> a repo-relative path, or None. Pure, bounded, no backtracking hazard."""
    if not token or _URL_SCHEME_RE.match(token):
        # The WHOLE token is dropped when it is a URL. The previous code deleted a bounded
        # 2048-character prefix, which both left a foreign tail to be rescanned as a citation
        # and — because `,` was not a delimiter — swallowed a real citation that followed a
        # URL. Tokenising first fixes both directions at once.
        return None
    # rstrip only: `.strip(".")` ate the LEADING dot of `.github/...`, turning a valid
    # dot-directory citation into an unresolvable `github/...`. Found by self-test.
    token = token.strip().rstrip(".")         # trailing sentence punctuation
    token = _CITATION_SUFFIX_RE.sub("", token)
    if token.startswith("./"):
        token = token[2:]
    if not token or token.startswith("/") or "\\" in token:
        return None                            # absolute, or a Windows separator we do not read
    components = token.split("/")
    if not 1 <= len(components) <= _MAX_PATH_COMPONENTS:
        return None
    for component in components:
        if component in ("", ".", ".."):
            return None                        # traversal or an empty component
        if len(component) > _MAX_COMPONENT_LEN or not _COMPONENT_RE.match(component):
            return None
    name = components[-1]
    if "." not in name:
        return None
    stem, _, extension = name.rpartition(".")
    # A bare extension like `.json` is not a filename. Require a non-empty stem.
    if not stem:
        return None
    # EXACT extension membership, never a prefix match.
    if extension.lower() not in _CITATION_EXTENSIONS:
        return None
    return token


def cited_paths(body: str, resolves) -> tuple[list[str], str]:
    """Repository paths an issue body CITES, plus how confident that reading is.

    Returns ``(paths, extraction)`` where ``extraction`` is one of:

    - ``"paths"``     — EVERY path-shaped candidate resolves in an endpoint tree.
    - ``"none"``      — the body names nothing path-shaped at all. Confidently citation-free.
    - ``"ambiguous"`` — at least one candidate could NOT be resolved.

    **Any unresolved candidate makes the whole body ambiguous** (Step-11 review, 2026-08-02).
    The previous rule returned ``"paths"`` as soon as ONE candidate resolved and silently
    discarded the rest, so a body pairing an untouched resolving decoy with a stale citation
    got ``quick`` — untrusted text manufacturing the classification that REDUCES scrutiny.
    Ambiguity now propagates, because a body we could only partly read is one we cannot vouch
    for.

    ``resolves`` is the set of paths known to exist in one of the two endpoint trees. It is
    INJECTED rather than probed here because this module is pure — no I/O, no subprocess —
    a promise enforced by a source grep in `tests/hooks/test_driver_state_write_back.py`.
    """
    if not isinstance(body, str) or not body:
        return ([], "none")
    known = set(resolves or ())
    candidates: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN_SPLIT_RE.split(body):
        path = _candidate_path(token)
        if path is None or path in seen:
            continue
        # A SINGLE-component token (`supervisor.py`) counts only when it really is a
        # root-level file. Supporting root-level citations at all was a review finding —
        # `README.md` was previously invisible — but measurement against five real issue
        # bodies then showed the naive version turning every bare filename mentioned in
        # prose into an UNRESOLVED citation, which dragged four of five fixtures to
        # `ambiguous`. Prose naming a module is not a path claim; a resolving root-level
        # file is. Multi-component tokens keep failing loudly when they do not resolve.
        if "/" not in path and path not in known:
            continue
        seen.add(path)
        candidates.append(path)
    if not candidates:
        return ([], "none")
    resolved = [c for c in candidates if c in known]
    if len(resolved) != len(candidates):
        return (resolved, "ambiguous")
    return (resolved, "paths")


class DriverStateError(ValueError):
    """Raised on a malformed driver-state or an invalid driver operation."""


class DependencyCycleError(DriverStateError):
    """Raised (fail-closed) when the dependency graph contains a cycle."""


class QueueRevalidationRequired(DriverStateError):
    """Raised when the remaining queue has not been revalidated against the current head.

    **LIVE since PR 2** (`_refuse_unrevalidated_queue`, reached from `next_ready_issue`,
    `fresh_session_handoff` and `validate_queue_revalidation`). PR 1 defined it and raised it
    nowhere, deliberately — the design gate refused an earlier plan that shipped the refusal
    before the mechanism that clears it, which would have jammed a live campaign between two
    PRs. That is history now; do not read this class as inert.

    It subclasses `DriverStateError` so every existing `except DriverStateError` caller stays
    correct, and it is a distinct type so callers can tell "the queue is stale" from "nothing
    is ready" — `launcher_lib next-child` maps it to rc 6 ("revalidate, then retry") rather
    than rc 2 ("this state is unusable").

    That distinction is the whole reason selection RAISES rather than returning `None`.
    `resume_prompt_for_state` USED to collapse every non-ready outcome into `None` and report it
    as "complete or blocked", which would have announced a stale queue to the operator as *the
    epic finished*; it now returns a result object carrying the outcome, so the collapse is
    closed (`tests/hooks/test_revalidation_gate.py::TestRefusalPropagation`). The raise is still
    what makes that possible, and reintroducing a `None` return would reopen it.
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
_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_CLAIM_KINDS = frozenset({"citation", "cause", "ac"})
_CLAIM_VERDICTS = frozenset({"holds", "broken"})
_CLAIM_REQUIRED = ("kind", "quoted_from_body", "checked_against", "evidence", "verdict")
_EXTRACTIONS = frozenset({"paths", "none", "ambiguous"})
_DEPTHS = frozenset({"deep", "quick"})
# `issue_obsolete` is deliberately ABSENT: it is not an outcome a stamped child may carry,
# because a stamped child is selectable. It lives only in `pending_disposition`.
_OUTCOMES = frozenset({"still_valid", "body_corrected"})
_PENDING_DISPOSITIONS = frozenset({"issue_obsolete"})
# Statuses that SETTLE a pending owner decision, so a leftover `validated_against` beside a
# `pending_disposition` is stale bookkeeping rather than a live contradiction (round-5 High 2).
# `deferred`/`abandoned` are the two dispositions the refusal itself names; `merged` is a
# stronger outcome than either, and exempting it is what keeps a merged child with a stale
# marker from jamming the campaign with no way out. Deliberately NOT here: `pr_open` and
# `in_progress`, which leave the decision outstanding — and `pr_open` can satisfy a dependent's
# dependency, so exempting it let an obsolete child hand out somebody else's work.
_DISPOSED_STATUSES = frozenset({"deferred", "abandoned", "merged"})


def _enum(value, allowed, what):
    """Set membership that cannot leak a raw TypeError.

    A list or dict reaching `value in frozenset(...)` raises `TypeError: unhashable type`,
    which escapes the documented `DriverStateError` contract and would surface to a caller as
    an unexpected crash rather than a validation refusal (Step-11 review, 2026-08-02).
    """
    if not isinstance(value, str) or value not in allowed:
        raise DriverStateError(f"{what} must be one of {sorted(allowed)}, got {value!r}")
    return value


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
        _enum(claim["kind"], _CLAIM_KINDS, f"claims[{index}].kind")
        _enum(claim["verdict"], _CLAIM_VERDICTS, f"claims[{index}].verdict")
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
    _enum(record["extraction"], _EXTRACTIONS, "extraction")
    _enum(record["depth"], _DEPTHS, "depth")
    pending = record.get("pending_disposition")
    if pending is not None:
        _enum(pending, _PENDING_DISPOSITIONS, "pending_disposition")
    outcome = record["outcome"]
    # `pending_disposition` and `outcome` are MUTUALLY EXCLUSIVE (adversarial-diff review,
    # 2026-08-02, confirmed by execution). A stamped child is selectable, so an obsolete child
    # must stay unstamped — carrying both let a receipt assert an obsolete child was fine.
    if pending is not None:
        if outcome is not None:
            raise DriverStateError(
                f"a child with pending_disposition {pending!r} must carry outcome null — it is "
                "NOT stamped, and a stamped child is selectable")
    elif not isinstance(outcome, str) or outcome not in _OUTCOMES:
        raise DriverStateError(
            f"outcome must be one of {sorted(_OUTCOMES)}, got {outcome!r} — note that "
            "'issue_obsolete' is NOT an outcome: a stamped child is selectable, so an obsolete "
            "child must stay unstamped and carry pending_disposition instead")
    validate_validated_against(record["from_sha"])
    validate_validated_against(record["to_sha"])
    # `body_hash` was presence-checked only, so `body_hash: null` passed and the receipt's
    # claimed binding to the issue body meant nothing (same review).
    body_hash = record["body_hash"]
    if isinstance(body_hash, bool) or not isinstance(body_hash, str) \
            or not _SHA256_RE.match(body_hash):
        raise DriverStateError(
            f"body_hash must be 64 lowercase hex characters, got {body_hash!r} — an unbound "
            "receipt cannot attest which body was read")
    n_claims = validate_claims(record["claims"])
    if not _is_int(record["validated_at"]) or record["validated_at"] < 0:
        raise DriverStateError(
            f"validated_at must be a non-negative int epoch, got {record['validated_at']!r}")
    # SEMANTIC coherence. The fields were validated independently, so a receipt could assert
    # `still_valid` while its own evidence said `broken` (same review, confirmed by execution).
    verdicts = [c["verdict"] for c in record["claims"]]
    correction = record.get("correction_comment")
    if outcome == "still_valid":
        if "broken" in verdicts:
            raise DriverStateError(
                "outcome 'still_valid' contradicts its own evidence: "
                f"{verdicts.count('broken')} of {n_claims} claims are 'broken'")
        if correction is not None:
            raise DriverStateError(
                "outcome 'still_valid' must not carry a correction_comment — nothing was "
                "corrected")
    elif outcome == "body_corrected":
        if "broken" not in verdicts:
            raise DriverStateError(
                "outcome 'body_corrected' requires at least one 'broken' claim naming what "
                "was wrong")
        if not isinstance(correction, str) or not correction.strip():
            raise DriverStateError(
                "outcome 'body_corrected' requires a correction_comment — a correction that "
                "was never posted is not a correction")
    return True


_RECEIPT_REMEDY = (" Run the revalidate-children skill to rebuild the receipt from evidence "
                   "(it drops any record that no longer validates), then retry")

# Canonical decimal, ASCII only (round-9 High 2). `str.isdigit()` was the old check and it is
# true for `"01"`, `"001"` and the Unicode digit `"١"` — all of which `int()` maps to 1, so the
# validator accepted them while every consumer looks up `children.get(str(number))` and found
# nothing. A `pending_disposition` filed under `"01"` therefore validated cleanly AND was
# invisible to the owner gate, which released the dependent. Identity has to have one spelling.
_CANONICAL_CHILD_KEY_RE = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")


def validate_queue_revalidation(state: dict) -> bool:
    """Validate the campaign receipt, and make EVERY refusal name the remedy that clears it.

    **The remedy is attached HERE, at the function boundary, not at each `raise` (round 8).** The
    round-8 sweep drove all 3840 reachable gate states through their own printed instructions and
    found 280 that named nothing an operator could run — every one of them a bare `DriverStateError`
    from this validator. An earlier fix in the same round had patched exactly one of those raise
    sites, which is the mistake this whole PR keeps repeating: fix the instance, ship the twin.
    A wrapper covers the raise sites that exist today AND the ones somebody adds later, which is
    the only version of this fix that stays fixed.

    `QueueRevalidationRequired` passes through untouched: it is the designed, recoverable refusal
    and already carries its own remedy, which is an OWNER write-back rather than this skill.
    """
    try:
        return _validate_queue_revalidation(state)
    except QueueRevalidationRequired:
        raise
    except DriverStateError as exc:
        # **Attached UNCONDITIONALLY, and the remedy is carried STRUCTURALLY (round-9 High 3).**
        # This used to skip the append when the message already contained "revalidate-children",
        # which is corruption-controlled text: a receipt whose `version` is the string
        # `"revalidate-children"` echoes into the message, suppressed the remedy, and left a
        # refusal naming nothing to run. The jam harness shared the identical substring blind
        # spot and scored that instructionless refusal as recoverable — the guard and its test
        # were wrong in exactly the same way, which is why `remedy` is now an ATTRIBUTE rather
        # than something anybody has to find in prose.
        error = DriverStateError(str(exc).rstrip(".") + "." + _RECEIPT_REMEDY)
        error.remedy = "revalidate"
        raise error from exc


def _validate_queue_revalidation(state: dict) -> bool:
    """Validate the campaign-level receipt AND its linkage to `issues[].validated_against`.

    Added after the adversarial-diff review (2026-08-02) found that nothing validated the
    receipt or connected it to the per-child stamps, so a fabricated receipt passed the
    documented entry point and a later gate could see a current stamp with no evidence behind
    it. A state with no `queue_revalidation` key passes silently — every pre-#840 campaign.
    """
    reval = state.get("queue_revalidation")
    if reval is None:
        return True
    if not isinstance(reval, dict):
        raise DriverStateError("queue_revalidation must be a JSON object")
    for field in ("version", "extractor_version", "validated_head", "children"):
        if field not in reval:
            raise DriverStateError(f"queue_revalidation.{field} is required")
    if reval["version"] != 1 or reval["extractor_version"] != 1:
        raise DriverStateError(
            f"unsupported queue_revalidation version {reval['version']!r}/"
            f"{reval['extractor_version']!r} — refusing to read a receipt written by a "
            "version this code does not understand")
    head = validate_validated_against(reval["validated_head"])
    children = reval["children"]
    if not isinstance(children, dict):
        raise DriverStateError("queue_revalidation.children must be an object keyed by number")
    parsed: dict[int, dict] = {}
    for key, record in children.items():
        if not (isinstance(key, str) and _CANONICAL_CHILD_KEY_RE.match(key)):
            raise DriverStateError(
                f"queue_revalidation.children key {key!r} is not a canonical issue number — "
                "'01', '001' and non-ASCII digits all parse to the same int but no consumer "
                "looks them up, so a record filed under one is invisible to the owner gate")
        validate_revalidation_child(record)
        if record["to_sha"] != head:
            raise DriverStateError(
                f"child #{key} receipt was computed against {record['to_sha']!r} but the "
                f"receipt claims validated_head {head!r} — a receipt cannot attest a head it "
                "was not computed against")
        parsed[int(key)] = record
    # The linkage. A stamp with no receipt entry is exactly the fabricated-provenance case.
    for issue in state.get("issues", []):
        stamped = issue.get("validated_against")
        if stamped is None:
            continue
        validate_validated_against(stamped)
        if stamped == head and issue["number"] not in parsed:
            raise DriverStateError(
                f"issue #{issue['number']} is stamped at the validated head but the receipt "
                "carries no evidence for it")
        # #840 Step-11 round 3, finding 5. `validate_revalidation_child` already refuses
        # `pending_disposition` alongside an `outcome`, for the reason that decides this too: a
        # STAMPED child is selectable, so an obsolete one must stay unstamped. The record-level
        # check could not see the issue-level stamp, so both together passed the whole validator —
        # the receipt asserted an obsolete child had been cleared. Selection refuses it anyway on
        # the pending marker, so this closes an invariant hole rather than a bypass; but a receipt
        # is exactly the artifact whose invariants have to hold on their own.
        #
        # **Scoped to UNDISPOSED statuses (round-4 High 4, corrected by round-5 High 2).**
        # Round 4 exempted every non-`queued` status on the reasoning that a non-queued child is
        # not selectable. True, and beside the point: a `pr_open` child SATISFIES A DEPENDENCY
        # under `deps_satisfied_by: "pr_open"`, so a stamped child the receipt calls obsolete
        # could unblock a DIFFERENT child, and that one was handed out. The question is never
        # only "is this child selectable" but "what does this child let somebody else do".
        #
        # `_DISPOSED_STATUSES` is what genuinely settles the pending owner decision: the two
        # documented dispositions, plus `merged`, which is a stronger outcome than either and
        # must be exempt or a merged child with a stale marker would jam the campaign for good.
        #
        # **Scoped at all (round-4 High 4).** Unscoped, this refusal outlived its own
        # remedy: `record_child_outcome` moves the STATUS to `deferred`/`abandoned` and leaves the
        # stamp untouched, so the invariant kept firing after the owner had done exactly what the
        # message asked, and re-running the revalidation skill could not help either — it skips a
        # child that is no longer eligible. The campaign was then unrecoverable, which is the very
        # class of defect rounds 2 and 3 were spent removing. The invariant is about
        # SELECTABILITY, so it applies only where selection can happen; once a child is disposed
        # of it is not selectable and a leftover stamp asserts nothing.
        # The stamped-plus-pending invariant went with the owner gate (#848). It existed only
        # because a stamped child is SELECTABLE and an obsolete one must not be — a statement
        # about a gate that no longer runs. Keeping it would refuse a receipt shape nothing acts
        # on, which is the "guard keyed to something that cannot matter" class this PR has now
        # shipped twice. `validate_revalidation_child` still refuses a record carrying BOTH a
        # pending_disposition and an outcome: that is record coherence, not gating, and it stands.
    return True


# Statuses `git diff --name-status` emits. R (rename) and C (copy) carry TWO paths; the rest
# carry one. That asymmetry is the whole reason this function exists — measured by probe on
# 2026-08-02, not read from documentation: `git mv` + commit yields
# `R100<TAB>old_name.py<TAB>new_name.py`, three tab-separated fields, where an `M` row has two.
# A parser assuming two fields would take `old_name.py` as the STATUS and lose the old path.
_DIFF_ONE_PATH_STATUSES = frozenset({"A", "D", "M", "T", "U", "X", "B"})
_DIFF_TWO_PATH_STATUSES = frozenset({"R", "C"})
# Whole-token match. `R100`, `R087` and a bare `R` are all real git output; `MALFORMED`,
# `R1000`, `RX` and `M1` are not, and each of those was ACCEPTED before this pattern existed.
_DIFF_STATUS_RE = re.compile(r"\A(?:[ADMTUXB]|[RC][0-9]{0,3})\Z")


def parse_changed_paths(diff_text: str) -> set[str]:
    """Changed repository paths from ``git diff --name-status -M`` output. PURE.

    A rename contributes BOTH its old and its new path. Both matter: a child citing the old
    path and a child citing the new one are each affected by the rename, and dropping either
    would under-report the changed set — which biases a child toward `quick` when it needs
    `deep`. Failing toward LESS scrutiny is the one direction this design must never take, so
    every malformed row raises instead of being skipped.

    An EMPTY diff is not an error: a merge that changed nothing is legitimate. A diff that
    cannot be READ is an error. Keeping those distinguishable is the point — "no data" must
    never quietly become "no changes".

    `-M` is retained at the call site only so behaviour does not depend on a repo-local
    `diff.renames=false`; git enables rename detection by default, so the `R` row appears
    either way (probed 2026-08-02, correcting an earlier claim in the design).
    """
    if not isinstance(diff_text, str):
        raise DriverStateError(
            f"diff text must be a string, got {type(diff_text).__name__}")
    changed: set[str] = set()
    for lineno, raw in enumerate(diff_text.splitlines(), start=1):
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        fields = line.split("\t")
        status = fields[0].strip()
        # EXACT status syntax, not a first-character check. The adversarial-diff review
        # (2026-08-02) confirmed by execution that `MALFORMED<TAB>a.py` parsed as an `M` row:
        # the old code took `status[:1]`, so any word beginning with a valid letter was
        # accepted. Corrupt input then under-reports the changed set, which downgrades a child
        # to `quick` — failing toward LESS scrutiny, the one direction forbidden here.
        if not _DIFF_STATUS_RE.match(status):
            raise DriverStateError(
                f"diff line {lineno}: malformed status {status!r} — expected one of "
                f"{sorted(_DIFF_ONE_PATH_STATUSES)} or R/C with an optional 0-3 digit "
                "similarity score; refusing to guess how many paths it carries")
        letter = status[0].upper()
        # EXACT field counts. The same review confirmed `M<TAB>a.py<TAB>b.py` silently
        # returned only `a.py`, losing the second path: the old code sliced `fields[1:2]`
        # and ignored the rest instead of refusing a row it did not understand.
        expected = 3 if letter in _DIFF_TWO_PATH_STATUSES else 2
        if len(fields) != expected:
            what = ("a rename/copy, which must carry BOTH an old and a new path"
                    if expected == 3 else "a single-path status")
            raise DriverStateError(
                f"diff line {lineno}: status {status!r} is {what}, so the row must have "
                f"exactly {expected} tab-separated fields; got {len(fields)} in {line!r}")
        paths = fields[1:expected]
        for path in paths:
            path = path.strip()
            if not path:
                raise DriverStateError(f"diff line {lineno}: empty path field in {line!r}")
            changed.add(path)
    return changed


def carries_successor_evidence(record) -> bool:
    """Does this record hold something the SUCCESSOR still needs? PURE.

    #840 round 12, found independently by two lenses. `rebuild_receipt` drops any record that does
    not attest the head being validated — and for the active `in_progress` child that deleted its
    `body_corrected` record, which is the ONLY place `corrections_clause` can read the correction
    from. The worklist is `queued`-only, so nothing re-supplied it; the rung then reported PASSED
    and the successor resumed from the stale body the correction existed to fix.

    "Successor-facing" is exactly what `corrections_clause` renders: a broken claim, a correction
    comment, or a pending disposition. Deliberately NOT "any record" — dropping ordinary
    `still_valid` evidence is how a corrupt entry becomes recoverable (round 8), and this must not
    become "never drop anything".
    """
    if not isinstance(record, dict):
        return False
    if record.get("correction_comment") or record.get("pending_disposition"):
        return True
    return any(isinstance(claim, dict) and claim.get("verdict") == "broken"
               for claim in (record.get("claims") or []))


def _receipt_covers_child(state: dict, number: int, observed_head: str) -> bool:
    """Does a CURRENT campaign receipt carry evidence for this child? PURE.

    #840 Step-11 round 6, High 3. A `validated_against` stamp is a claim; the receipt is the
    evidence behind it. Treating the stamp alone as "already done" let the head clause refuse a
    campaign while `revalidation_worklist` returned nothing to do — the refusal named a skill that
    had no work and so could never advance the receipt the gate demands.

    **Coverage requires a STRUCTURALLY VALID record, not a present key (round 8, High 3).** The
    first fix asked only `str(number) in children`, which reopened the very jam it closed, one
    layer down: a corrupt current record — `body_hash: "bad"` reproduces it — made selection die
    inside `validate_queue_revalidation` with a hard data error while this function reported the
    child covered, so the worklist came back empty and nothing the operator could run rebuilt the
    entry. Key presence is not attestation. `to_sha` is checked here too: `validate_queue_revalidation`
    enforces it against the receipt head, but the worklist path never runs that validator, so this
    function cannot borrow its guarantee.

    Fails toward MORE work: anything it cannot positively read as valid evidence becomes a
    re-audit candidate, which costs a look and never hands out an unattested child.
    """
    reval = state.get("queue_revalidation")
    if not isinstance(reval, dict) or reval.get("validated_head") != observed_head:
        return False
    children = reval.get("children")
    if not isinstance(children, dict):
        return False
    record = children.get(str(number))
    try:
        validate_revalidation_child(record)
    except DriverStateError:
        return False
    return record.get("to_sha") == observed_head


def _baseline_usable(sha, unresolvable: frozenset) -> bool:
    """Can ``sha`` actually serve as the left endpoint of a diff? PURE.

    #840 Step-11 round 3, High 1. Two different ways a baseline is unusable, and they must be
    handled together because the consequence is identical:
      * **malformed** — decidable from the value alone. `queue.schema.json` constrains
        `base_default_branch_sha` to a string and nothing more, so `""` and `"abc"` are
        schema-valid and were reaching the strict validator, which raised.
      * **unresolvable** — a well-formed SHA whose object is gone (force-pushed, pruned, or from
        a different repository). That is I/O and this module does none, so the caller PROBES and
        passes the answer in; here it is simply believed.
    """
    if sha is None or isinstance(sha, bool) or not isinstance(sha, str):
        return False
    if not _SHA_RE.match(sha):
        return False
    return sha not in unresolvable


def revalidation_worklist(state: dict, observed_head: str, extractions: dict,
                          changed_by_child: dict, issue_state_probe=None,
                          unresolvable_shas=None) -> list[dict]:
    """Which remaining children need a look against ``observed_head``, and how hard a one. PURE.

    **Owner ruling 2026-08-02: the cited-paths intersection decides HOW HARD to look, never
    WHETHER.** Every eligible child not stamped at the current head appears here. Nothing is
    auto-cleared.

    The refuted earlier design cleared a child whose cited files a merge had not touched. Both
    pass-2 reviewers refuted it independently as an incomplete dependency model, and #835 is
    the standing proof: its body was wrong about the *cause*, not about a filename, so a path
    filter would have cleared it — and #835 is one of the three incidents that caused #840 to
    be filed at all.

    ``depth`` is ``"deep"`` when the body could not be confidently read (`extraction` is not
    ``"paths"``) or when its cited paths intersect what changed; ``"quick"`` otherwise. Both
    still get a look; only the required work differs.

    Eligibility is EFFECTIVE status, not durable status: a `queued` entry the probe confirms
    already merged must not block the queue on a revalidation nobody can meaningfully perform.
    A probe outage conservatively keeps the child eligible, matching
    `effective_issue_statuses`' own never-veto-on-outage rule.

    ``extractions``/``changed_by_child`` are INJECTED — this module does no I/O. A MISSING
    entry raises rather than defaulting: an absent extraction would silently produce `quick`,
    which fails toward LESS scrutiny, and that is the one direction this design must never
    take. "No data" must never read as "no changes".

    **Baselines, and the division of labour (round-3 High 1).** Each item reports its
    ``baseline`` provenance — ``"stamp"``, ``"base"``, or ``"unavailable"``. A child carrying a
    stamp is dated from ITS stamp; one without, from the campaign base. When that commit is
    unusable — malformed, or named in ``unresolvable_shas`` — the range collapses to
    ``from_sha == to_sha == observed_head`` with ``depth`` forced to ``"deep"`` and provenance
    recorded as ``"unavailable"``. It never raises: raising is what made the universal gate
    unrecoverable twice.

    ``unresolvable_shas`` is the SKILL's half of that split. Whether a well-formed SHA still
    exists is I/O (force-push, prune, wrong repository), so the skill probes — e.g.
    ``git cat-file -e <sha>^{commit}`` per distinct baseline — and passes the failures in. Omit
    it and only malformed values are caught, which leaves the skill to jam one step later on a
    ``git diff`` whose left endpoint does not exist.
    """
    validate_validated_against(observed_head)
    issues = state.get("issues", [])
    _numbers(issues)
    base = state.get("base_default_branch_sha")
    unresolvable = frozenset(unresolvable_shas or ())
    effective, _overlaid = effective_issue_statuses(issues, issue_state_probe)
    work: list[dict] = []
    reval_children = ((state.get("queue_revalidation") or {}).get("children") or {}) \
        if isinstance(state.get("queue_revalidation"), dict) else {}
    for issue in issues:
        number = issue["number"]
        if effective[number] != "queued":
            # **A non-queued child still needs auditing when it holds successor-facing evidence
            # (round 12).** The worklist being `queued`-only is what made the active child's
            # correction unreplaceable: `rebuild_receipt` dropped the record and nothing could
            # produce a new one, so refusing the drop alone would have been another
            # unrecoverable jam. A DISPOSED child is exempt — nobody will be handed it, so its
            # correction has no consumer left.
            record = reval_children.get(str(number)) if isinstance(reval_children, dict) else None
            if not (carries_successor_evidence(record)
                    and issue.get("status") not in _DISPOSED_STATUSES):
                continue
        stamped = issue.get("validated_against")
        # **A stamp is evidence only when a CURRENT receipt vouches for it (round-6 High 3).**
        # This used to skip on the stamp alone, which made the head clause's own refusal
        # unclearable: a child stamped at the observed head under a STALE or ABSENT receipt was
        # skipped, the worklist came back empty, and the skill the refusal names had nothing to
        # audit and no way to advance the receipt. That is the fifth unrecoverable jam this issue
        # has produced, and the first found by asking what the REMEDY does rather than what the
        # refusal does.
        covered = _receipt_covers_child(state, number, observed_head)
        if stamped == observed_head and covered:
            continue                           # validated at this head, and attested
        # Audited again, and its stamp is NOT a baseline: there is no range to diff and nothing
        # attesting it, so it falls through to unavailable/deep rather than producing an empty
        # range that would buy `quick`.
        #
        # **A per-child LOCAL, not a mutation of `base` (round-7 Low 1).** The first version set
        # `base = None` here, and `base` is loop-invariant — so one unattested stamp silently
        # downgraded every LATER unstamped child to `head..head`/`deep` even where the campaign
        # base was perfectly good. It fails toward more scrutiny, so it was never unsafe, but it
        # is provenance the receipt then records wrongly, and it is the sort of quiet
        # cross-contamination that is very hard to see later.
        # **A stamp is a baseline only when something attests THAT stamp (round-11 High 2).**
        # This used to check attestation only for a stamp equal to the observed head, so a child
        # stamped at an OLDER head under a CURRENT receipt carrying no entry for it kept
        # `baseline="stamp"` and bought `quick` — and `quick` takes citation claims as-is, so the
        # missing evidence suppressed exactly the checks `deep` would have run. Failing toward
        # LESS scrutiny is the one direction forbidden here.
        #
        # Scoped to campaigns that HAVE a receipt: a pre-#840 campaign has none, so nothing could
        # attest any stamp, and forcing every child deep there would make the first arm — the
        # migration path rounds 2 and 3 were spent making possible — expensive for every legacy
        # campaign at once. `test_a_usable_base_and_stamp_are_still_used` is the twin that caught
        # this fix being too broad on the first attempt.
        if stamped is None:
            force_unavailable = False
        elif state.get("queue_revalidation") is not None:
            force_unavailable = not _receipt_covers_child(state, number, stamped)
        else:
            force_unavailable = stamped == observed_head
        # A malformed stamp used to RAISE here (round-3 High 1). `observed_head` is validated
        # above, so the equality check needs no validation of its own, and an unusable stamp is
        # now a baseline problem — handled with the campaign base below — not a hard error.
        if number not in extractions:
            raise DriverStateError(
                f"no extraction supplied for child #{number} — refusing to default it, because "
                "an absent extraction would silently produce depth 'quick' and fail toward less "
                "scrutiny")
        if number not in changed_by_child:
            raise DriverStateError(
                f"no changed-file set supplied for child #{number} — 'no data' must never read "
                "as 'no changes'")
        # Validate the VALUES, not merely that the keys exist. The adversarial-diff review
        # (2026-08-02) confirmed by execution that `changed_by_child[n] = None` became an empty
        # set and `(None, "paths")` became a successful extraction with no intersection —
        # both silently producing `quick`, which directly contradicted this function's own
        # docstring promise that unreadable data must never become "no changes".
        entry = extractions[number]
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            raise DriverStateError(
                f"extraction for child #{number} must be a (paths, extraction) pair, "
                f"got {entry!r}")
        cited, extraction = entry
        _enum(extraction, _EXTRACTIONS, f"extraction verdict for child #{number}")
        if not isinstance(cited, (list, tuple, set, frozenset)):
            raise DriverStateError(
                f"cited paths for child #{number} must be a collection, got {cited!r} — a null "
                "cannot be read as 'nothing was cited'")
        if any(not isinstance(p, str) or not p.strip() for p in cited):
            raise DriverStateError(
                f"cited paths for child #{number} must all be non-empty strings, got {cited!r}")
        if extraction == "paths" and not cited:
            raise DriverStateError(
                f"child #{number} claims extraction 'paths' with no paths — incoherent; an "
                "empty result is 'none' or 'ambiguous', never a successful extraction")
        changed = changed_by_child[number]
        if not isinstance(changed, (list, tuple, set, frozenset)):
            raise DriverStateError(
                f"changed-file set for child #{number} must be a collection, got {changed!r} — "
                "a null cannot be read as 'nothing changed'")
        intersects = bool(set(cited) & set(changed))
        depth = "deep" if (extraction != "paths" or intersects) else "quick"
        # Which commit dates this child's range, and can it actually serve?
        #
        # A child that carries a stamp uses ITS stamp — never the campaign base. Falling through
        # from an unusable stamp to the base would date the range from a commit this child was
        # never validated at, and a wider-but-wrong range can buy `quick` on a real change.
        # A child with no stamp uses the campaign base; that is the first arm.
        if force_unavailable:
            candidate, provenance = None, "stamp"
        else:
            candidate, provenance = (stamped, "stamp") if stamped is not None else (base, "base")
        if _baseline_usable(candidate, unresolvable):
            from_sha = candidate
        else:
            # **This used to RAISE, and Step-11 rounds 2 and 3 both proved that made the universal
            # gate unrecoverable.** Round 2: `base_default_branch_sha` is optional and nullable in
            # queue.schema.json, so a schema-valid pre-#840 campaign was refused by the gate while
            # the clearing skill could not build its first worklist — re-running changed nothing,
            # so there was no path from "refused" to "armed". Round 3: the same jam survived for
            # every OTHER unusable value, because the schema constrains the field to a string and
            # nothing more — `""`, `"abc"` and a force-pushed SHA all still raised, as did a
            # pruned per-child stamp. A migration with no way through is worse than no migration.
            #
            # The fallback collapses the range to `observed_head` at BOTH ends and forces `deep`.
            # That is the honest reading: with no usable baseline there is no range to diff, so
            # nothing can be shown to be untouched and every claim has to be checked against the
            # current tree. It fails toward MORE scrutiny, which is the only direction allowed
            # here, and `from_sha == to_sha` is a valid receipt shape (both are full SHAs).
            from_sha, provenance, depth = observed_head, "unavailable", "deep"
        work.append({"number": number, "depth": depth, "extraction": extraction,
                     "from_sha": from_sha, "to_sha": observed_head, "baseline": provenance})
    return work


def normalize_issue_body(body: str) -> str:
    """The canonical form `body_hash` is taken over. PURE.

    Defined HERE rather than in prose (round-9 High 5). The design doc said "sha256 of the
    normalized body" and no code anywhere defined "normalized", so two sessions hashing the same
    body could disagree and the receipt's binding to the body meant nothing. The rules are the
    minimum that survive a round-trip through the GitHub API and a local editor:

    * CRLF and CR both become LF — the API and a Windows editor disagree, and that is not a body
      change;
    * trailing whitespace is stripped per line, for the same reason;
    * leading and trailing blank lines are dropped.

    Nothing else is touched: internal blank lines, markdown and unicode are content.
    """
    if not isinstance(body, str):
        raise DriverStateError(f"issue body must be a string, got {type(body).__name__}")
    lines = [line.rstrip() for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def build_revalidation_record(*, body: str, from_sha: str, to_sha: str, extraction: str,
                              depth: str, claims: list, validated_at: int,
                              outcome: str | None = "still_valid",
                              pending_disposition: str | None = None,
                              correction_comment: str | None = None) -> dict:
    """One `queue_revalidation.children[<n>]` record, built and validated. PURE.

    Round-9 High 5: the skill told an agent to pass `audited` records "carrying
    `to_sha == observed_head`" and said nothing else, while `validate_revalidation_child` requires
    EIGHT fields plus a `body_hash` whose derivation was documented nowhere. The printed call
    could not run, and an agent had to reverse-engineer the validator or the tests first. A
    constructor is the executable version of that contract — and it validates its own output, so
    a bad record fails here rather than at the receipt write.

    `validated_at` is INJECTED: this module does no I/O and takes no clock, which is also what
    keeps its tests deterministic.
    """
    record = {"body_hash": hashlib.sha256(
                  normalize_issue_body(body).encode("utf-8")).hexdigest(),
              "from_sha": from_sha, "to_sha": to_sha, "extraction": extraction, "depth": depth,
              "outcome": outcome, "claims": claims, "validated_at": validated_at}
    if pending_disposition is not None:
        # The two are mutually exclusive by the receipt's own coherence rule: an obsolete child
        # is awaiting a decision, so it has no outcome yet.
        record["pending_disposition"] = pending_disposition
        record["outcome"] = None
    if correction_comment is not None:
        record["correction_comment"] = correction_comment
    validate_revalidation_child(record)
    return record


def rebuild_receipt(state: dict, observed_head: str, audited: dict) -> dict:
    """Write the campaign receipt FROM EVIDENCE. PURE — returns a new state, mutates nothing.

    This is `skills/revalidate-children/SKILL.md` step 7 made executable. It used to be prose, and
    prose is where three of this issue's eight review rounds found defects — a step described in
    English is a step every future session re-derives, slightly differently. The rules below are
    the ones that were impossible to state safely in a paragraph:

    * **A record that does not validate is not evidence, so it is DROPPED.** Carrying it forward
      is what made a corrupt entry unrecoverable (round-8 sweep, 39 states): the worklist only
      ever audits ELIGIBLE children, so a malformed record belonging to a `merged` or
      `in_progress` child was never rewritten and `validate_queue_revalidation` refused for ever.
      Rebuilding from evidence has no such blind spot — anything unreadable simply does not
      survive into the new receipt.
    * **A record attesting a different head is dropped too.** It says nothing about this one.
    * **A stamp with no surviving record is CLEARED.** The linkage invariant refuses a child
      stamped at the validated head with no evidence behind it, and after a drop that is exactly
      what would be left. The stamp is the claim; the record is the evidence; losing the evidence
      must lose the claim, never the other way round.
    * A `pending_disposition` on a record is carried forward as EVIDENCE of what the audit
      found, but it gates nothing while #848 is open, so it no longer withholds the stamp.

    ``audited`` maps issue number to the record just produced for it — normally one entry per
    `revalidation_worklist` item. An EMPTY ``audited`` is legitimate and still advances the head:
    a campaign whose children are all merged or in flight has nothing to audit but must still be
    armable, or the gate would be permanently shut on the mid-child handoff it exists to serve.

    Fails CLOSED on STRUCTURE: the result is validated before it is returned, so it can never be
    the source of a structurally invalid receipt. It does **not** promise the gate will then open
    — round-9 Medium 1 corrected an earlier docstring that claimed exactly that. An incomplete
    audit leaves eligible children stamped at an older head and the gate refuses that on purpose.
    Structural validity and a satisfied gate are different properties; only the first is
    guaranteed here.
    """
    validate_validated_against(observed_head)
    if not isinstance(audited, dict):
        raise DriverStateError(f"audited must be a dict of number -> record, got "
                               f"{type(audited).__name__}")
    new = copy.deepcopy(state)
    prior = state.get("queue_revalidation")
    prior_children = prior.get("children") if isinstance(prior, dict) else None
    # **A live owner obligation blocks the rebuild (round-9 High 1, found independently by two
    # reviewers).** A `pending_disposition` is OWNER state that happens to be stored inside a
    # HEAD-SCOPED audit record, so every rule that drops or replaces records against a head can
    # destroy it — and three separate paths did: a malformed record was dropped, a record
    # attesting an older head was dropped, and a clean audited record simply overwrote the
    # marker. In each case the campaign armed, the gate opened, and a dependent was handed out
    # with nobody having decided anything. Refusing is the only safe direction: a machine may not
    # close a child, and it may not launder the requirement to either.
    #
    # The recoverability sweep scored all three as PASSES, because its whole question is "does
    # the gate open" — which is precisely what laundering achieves. That gap is now covered by
    # `TestTheGateNeverOpensOverALiveOwnerDecision`.
    children: dict[str, dict] = {}
    if isinstance(prior_children, dict):
        for key, record in prior_children.items():
            if not (isinstance(key, str) and _CANONICAL_CHILD_KEY_RE.match(key)):
                continue                       # not an addressable issue number
            try:
                validate_revalidation_child(record)
            except DriverStateError:
                continue                       # unreadable, so not evidence
            if record.get("to_sha") == observed_head:
                children[key] = copy.deepcopy(record)
    # **Refuse to DROP evidence the successor still needs (round 12).** Everything above kept a
    # prior record only when it attests `observed_head`; for the active child that silently
    # deleted the correction `corrections_clause` reads. Scoped to UNDISPOSED children, and to
    # records that actually carry successor-facing evidence, so ordinary stale or corrupt records
    # are still dropped freely — that dropping is what makes a corrupt entry recoverable.
    prior_map = prior_children if isinstance(prior_children, dict) else {}
    for issue in state.get("issues", []):
        number = issue["number"]
        if issue.get("status") in _DISPOSED_STATUSES or int(number) in {
                int(k) for k in audited}:
            continue
        record = prior_map.get(str(number))
        if carries_successor_evidence(record) and str(number) not in children:
            error = DriverStateError(
                f"refusing to rebuild the receipt: child #{number} is still active and its record "
                "carries evidence the successor needs (a correction, a broken claim or a pending "
                "disposition), and this rebuild would drop it. Re-audit that child against the "
                "observed head and supply its replacement record — the revalidate-children skill "
                "lists it")
            error.remedy = "revalidate"
            raise error
    for number, record in audited.items():
        validate_revalidation_child(record)
        # **An OLDER audit may not replace a NEWER record at the SAME head (round 12).** The
        # state lock serialises WRITES; it does not order EVIDENCE. A session holding a record
        # prepared before another session's correction landed would otherwise overwrite it, and
        # both are legitimately "at the observed head".
        existing = children.get(str(int(number)))
        if isinstance(existing, dict):
            was, now = existing.get("validated_at"), record.get("validated_at")
            if _is_int(was) and _is_int(now):
                if now < was:
                    raise DriverStateError(
                        f"refusing the audit record for child #{number}: it was validated at "
                        f"{now} but the receipt already holds evidence validated at {was}, which "
                        "is newer. Re-audit against the observed head rather than replaying an "
                        "older pass")
                # **Round 13, found by all six reviewers.** `validated_at` is an integer epoch
                # SECOND, so two audits prepared inside the same second TIE — and the round-12
                # guard above, testing only `<`, let the incoming one win on a tie. That is the
                # same evidence loss round 12 existed to close. Equality orders nothing, so it
                # is only safe when there is nothing to order: an IDENTICAL record is a retry of
                # a write that may have been interrupted, and must still succeed or a rebuild
                # could never be re-run. Anything else at the same instant is refused.
                if now == was and record != existing:
                    raise DriverStateError(
                        f"refusing the audit record for child #{number}: it and the receipt's "
                        f"existing evidence are both stamped {now}, so they are unordered — an "
                        "equal timestamp cannot decide which is later, and the existing record "
                        "differs. Re-audit against the observed head so the replacement carries "
                        "a strictly later validated_at")
        if record.get("to_sha") != observed_head:
            # **This is the NORMAL case of `main` moving mid-audit, not a caller bug — so it
            # names a remedy (round-10, all three lenses).** It used to state only the mismatch,
            # which left the operator holding an rc 6 and no next step for the most ordinary
            # thing that can happen during a long audit: somebody merged while you were reading
            # issue bodies. The evidence is not wrong, it is simply dated.
            error = DriverStateError(
                f"the audit record for child #{number} attests {record.get('to_sha')!r}, not the "
                f"head being validated ({observed_head!r}) — origin/main moved while the audit "
                "was running. Re-run the revalidate-children skill against the newly observed "
                "head; the evidence you gathered is dated, not wrong")
            error.remedy = "revalidate"
            raise error
        children[str(int(number))] = copy.deepcopy(record)
    new["queue_revalidation"] = {"version": 1, "extractor_version": 1,
                                 "validated_head": observed_head, "children": children}
    for issue in new.get("issues", []):
        key = str(issue["number"])
        record = children.get(key)
        if record is None:
            stamped = issue.get("validated_against")
            # An UNUSABLE stamp is cleared too, not only one matching the head. It names no
            # commit, so it attests nothing — and leaving it made this very function refuse its
            # own output at the fail-closed validation below, which turned the documented remedy
            # into a crash for every campaign carrying one.
            if stamped is not None and (stamped == observed_head
                                        or not _baseline_usable(stamped, frozenset())):
                del issue["validated_against"]     # the claim outlived its evidence
            continue
        # **An audited child is STAMPED even when its record carries a marker (#848).** The
        # "an obsolete child stays unstamped" rule existed solely to protect the owner gate — a
        # stamped child is selectable, so one awaiting a decision must not be. With that gate cut
        # the rule has nothing left to protect and became an unrecoverable jam instead: the child
        # was never stamped, so the per-child provenance clause refused for ever and re-running
        # the skill changed nothing. Caught by the jam sweep, not by review. #848 restores this
        # together with the clause it serves.
        issue["validated_against"] = observed_head
    validate_queue_revalidation(new)
    return new


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


def _refuse_unrevalidated_queue(state: dict, observed_head: str, effective: dict) -> None:
    """The #840 head-and-provenance gate. Raises `QueueRevalidationRequired`, or returns.

    Refuse when EITHER clause fails (design §4, both required — pass-2 findings #1 and #2):

        observed_head != queue_revalidation.validated_head
        any eligible child's validated_against != observed_head
        any DURABLY-UNDISPOSED child carries a pending_disposition

    Note the scope difference on the third clause, and it is deliberate (round-6 High 2, restated
    here at round-8 Medium 2 because this docstring still carried the old eligible-only wording
    beside the corrected code). Stamp freshness is an ELIGIBLE-child question. The pending marker
    is not: a `pr_open` child cannot be selected but can still SATISFY a dependency, so an
    obsolete one would otherwise hand out somebody else's work.

    The head clause alone was r2's design and both reviewers refuted it: a brand-new campaign
    sitting at its base head, or a newly-added unstamped child at an unmoved head, would hand out
    work with no provenance at all. The per-child clause alone is equally insufficient — stamps
    can be advanced without the receipt advancing atomically, and then every child *looks*
    current while nothing attests the queue as a whole.

    `pending_disposition` is the owner gate. Revalidation may conclude a child is obsolete, but
    closing a child is an owner decision, so the machine's job is to REFUSE rather than choose
    between `deferred` and `abandoned`. It is read from the receipt because that is where §3 puts
    it, and it holds regardless of the child's stamps — an obsolete child that is otherwise fully
    stamped and current must still not be handed out.

    Eligibility is EFFECTIVE status, not durable status: a `queued` entry the probe confirms
    already merged must not jam the queue on a revalidation nobody can meaningfully perform.

    **The per-child clauses are skipped when no child is eligible; the HEAD clause is not.**
    An earlier revision of this docstring said "nothing is refused when no child is eligible",
    and that stopped being true when Step-11 finding 3 made the head comparison unconditional:
    the rung asserts "this campaign's queue is current", which a mid-child handoff needs to hold
    even while its only child is `in_progress` and nothing is selectable. So a campaign with no
    eligible child and a stale — or absent — receipt IS refused. What the eligibility check buys
    is only that an empty queue is not asked for per-child provenance it cannot have.
    """
    validate_validated_against(observed_head)
    reval = state.get("queue_revalidation")
    validated_head = None
    children: dict = {}
    if reval is not None:
        # Validated BEFORE any eligibility shortcut (Step-11 review finding 3, reproduced): the
        # old code returned early when nothing was eligible, so a receipt of
        # `{"validated_head": "not-a-sha"}` was never validated and `produce_queue_revalidated`
        # reported the ladder rung PASSED on it. The producer's contract says a malformed receipt
        # fails closed, so the validation cannot sit behind a shortcut that exists for selection.
        #
        # This also checks the LINKAGE to the per-child stamps, so a fabricated stamp with no
        # evidence behind it fails here rather than passing the gate.
        validate_queue_revalidation(state)
        validated_head = reval.get("validated_head")
        children = reval.get("children") or {}
    eligible = [i for i in state.get("issues", []) if effective[i["number"]] == "queued"]
    reasons: list[str] = []
    # The head clause is UNCONDITIONAL, not gated on eligibility (finding 3). It is a statement
    # about the QUEUE as a whole, and an empty eligible set does not make a stale receipt fresh —
    # the ladder rung asserts "this campaign's queue is current", which a mid-child handoff needs
    # to be true even while its only child is `in_progress`.
    #
    # A campaign with NO receipt fails it too (finding 1, owner decision 2026-08-02). The
    # compatibility argument for waving those through was refuted by the reviewer and the
    # refutation is decisive: a refusal is recoverable by running `revalidate-children`, whereas
    # silent selection is the one failure direction this design forbids. An un-armed campaign is
    # therefore refused with an actionable reason rather than quietly advanced.
    if validated_head != observed_head:
        reasons.append(
            f"the campaign receipt attests {validated_head!r}, not the observed head "
            f"{observed_head!r}" if validated_head is not None else
            "this campaign has never been revalidated (no queue_revalidation receipt) — run "
            "/rawgentic:revalidate-children once to arm it")
    # The outstanding set is structured, not just prose, because `fresh_session_handoff` has to
    # hand the successor a worklist and this is the only place that knows which children are in
    # it. `depth` is deliberately NOT computed here: it needs the changed-file sets, which are
    # I/O, and this module is pure. The revalidate-children skill annotates depth via
    # `revalidation_worklist`.
    outstanding: list[dict] = []
    # **The `pending_disposition` OWNER GATE IS NOT ENFORCED HERE — cut deliberately (#848).**
    # It was added in round 6 of this PR's review and broke in rounds 7, 8, 9 and 10; four of
    # round 10's six findings lived entirely inside it, while the two clauses above had been
    # stable since round 5. Owner decision 2026-08-02: ship the stable half and rebuild the owner
    # gate in #848 behind ONE function that owns "what is wrong and what clears it" — the
    # structural answer to the defect that kept recurring, which was several sites each computing
    # their own remedy from information some of them did not have.
    #
    # The FIELD still exists and is still validated on every record; nothing gates on it, exactly
    # as v3.117.0 shipped the rest of this machinery inert. `TestTheOwnerGateIsInert` pins that,
    # and #848 INVERTS that guard rather than deleting it.
    #
    # What stays open until #848 lands, stated rather than hidden: an obsolete child can still
    # satisfy a dependent's dependency under `deps_satisfied_by: "pr_open"`. That is a PRE-EXISTING
    # hole — nothing enforced it before #840 either — so cutting this returns to the status quo
    # rather than regressing past it.
    for issue in eligible:
        number = issue["number"]
        stamped = issue.get("validated_against")
        if stamped is None:
            outstanding.append({"number": number, "validated_against": None,
                                "reason": "never revalidated"})
            continue
        # **An unusable stamp is STALE PROVENANCE, not a hard error (round-8 sweep).** This used
        # to `validate_validated_against(stamped)` and raise, which killed selection with a bare
        # data error naming no remedy — and `revalidation_worklist` had already stopped raising on
        # exactly this value at round-3 High 1, so the gate and the skill disagreed about whether
        # `"abc"` was recoverable. It is: the stamp claims a head it cannot name, which is a claim
        # with no evidence, and rebuilding the receipt clears it.
        if not _baseline_usable(stamped, frozenset()):
            outstanding.append({"number": number, "validated_against": stamped,
                                "reason": f"carries an unusable stamp {stamped!r}, so it names no "
                                          "head it can be compared against"})
            continue
        if stamped != observed_head:
            outstanding.append({"number": number, "validated_against": stamped,
                                "reason": "revalidated against a stale head"})
            continue
    reasons.extend(f"#{item['number']}: {item['reason']}" for item in outstanding)
    if reasons:
        # **The closing instruction is CONDITIONAL (round-7 Medium 1).** It used to append "Run
        # the revalidate-children skill … then retry" to every refusal, including a pending-only
        # one whose own text had just explained that revalidation cannot clear it — so the
        # message contradicted itself and its last line was a no-op. The suffix now names only
        # the remedies that apply: revalidation for stale provenance, the owner's write-back for
        # a pending marker, and both only when both are genuinely outstanding.
        #
        # **A STALE HEAD counts as needing revalidation, even with no stale-provenance child
        # (round 8, Medium 1).** This was derived from `outstanding` alone, which holds per-child
        # items only — so a stale receipt head plus a pending-only outstanding set printed
        # "re-running it will change nothing" while the stale head was precisely what re-running
        # fixes. Executing the printed remedy left the gate still refusing, and only then asked
        # for revalidation: a two-step remedy delivered one step at a time, which is the same
        # defect as a remedy that does nothing, spread over two attempts.
        # Only ONE remedy exists while the owner gate is out (#848): revalidation. The two-part
        # and owner-only branches went with it — a suffix naming a remedy no clause can produce is
        # exactly the prose-contradicting-code defect this PR kept shipping.
        suffix = ". Run the revalidate-children skill, post any corrections, then retry."
        error = QueueRevalidationRequired(
            "refusing to hand out the next child: the remaining queue has not been revalidated "
            f"against {observed_head}. " + "; ".join(reasons) + suffix)
        # Carried on the exception so `fresh_session_handoff` can surface a worklist without
        # re-deriving it — and so the refusal is never reduced to an opaque string the caller has
        # to parse.
        error.observed_head = observed_head
        error.validated_head = validated_head
        error.outstanding = outstanding
        # STRUCTURAL, not something a reader has to infer from the prose (round-9 High 3 and
        # Medium 1). Consumers — including the jam sweep — dispatch on this rather than pattern
        # matching the message, so corruption-controlled text cannot forge or suppress a remedy,
        # and a refusal can be checked for having DISCLOSED every action it will take.
        error.remedy = "revalidate"
        raise error


def campaign_deps_satisfied_by(state: dict) -> str:
    """The campaign's persisted `policy.deps_satisfied_by`, or the strict default. PURE.

    #840 Step-11 round 4, High 1. `fresh_session_handoff` took `next_ready_issue`'s `"merged"`
    default and silently discarded the persisted policy, so the documented unattended stacked-PR
    flow (`deps_satisfied_by: "pr_open"`, dependents advance once their prerequisite has an open
    PR) reported `blocked` — which the driver reads as "nothing left" and stops on. #840's own
    gate work is what routed selection through that path, so it shipped the regression.

    **An unusable value falls back to `"merged"`, the STRICTER rule, rather than raising.**
    `pr_open` is the looser of the two, so a value nobody can read must not buy it; and raising
    would strand a whole campaign over a typo in a knob, which is the unrecoverable class this
    issue has now hit three times. Fail toward strictness, never toward a jam.
    """
    policy = state.get("policy")
    if not isinstance(policy, dict):
        return "merged"
    value = policy.get("deps_satisfied_by")
    if isinstance(value, str) and not isinstance(value, bool) and value in _SATISFIED_BY:
        return value
    return "merged"


def next_ready_issue(state: dict, deps_satisfied_by: str = "merged",
                     issue_state_probe=None, *,
                     observed_head: str | None = None) -> int | None:
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
    # #840 — the gate runs BEFORE selection, because a stale queue must refuse whether or not
    # something happens to be ready. The discriminator is the STATE, never the argument: an
    # optional enforcement input is a bypass, and this repo has already shipped that exact defect
    # once (`tests/hooks/test_driver_state_write_back.py:304-306` documents an optional probe that
    # shipped dead). So a campaign that opted into revalidation and is then queried with no
    # observation is REFUSED rather than silently waved through.
    if observed_head is not None:
        _refuse_unrevalidated_queue(state, observed_head, effective)
    elif state.get("queue_revalidation") is not None:
        raise DriverStateError(
            "this campaign carries a queue_revalidation receipt, so selection requires a freshly "
            "observed head (launcher_lib.observe_head). Selecting without one would skip the "
            "freshness gate entirely — pass observed_head, or explicitly None only for a campaign "
            "that predates #840 and has no receipt")
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
    # #840 note, deliberately NOT a write here. `handoff_claim` compares the persisted queue
    # payload for exact equality, and this writer is the normal way a child's status changes, so an
    # unrelated terminal reconciliation between `open_handoff` and the claim DOES invalidate the
    # pending payload (Step-11 finding 4). An earlier fix cancelled the record from here and was
    # reverted: `tests/hooks/test_mid_child_handoff.py` asserts by AST that `open_handoff` is the
    # ONLY writer of `handoff_pending` in this module, and that single-writer rule is worth more
    # than the convenience — a second mutation path for the handoff record is exactly the parallel
    # mechanism #665 was rewritten to avoid.
    #
    # The recovery already exists and is the honest one: a refused claim leaves the predecessor
    # alive and guarded, and the NEXT handoff attempt calls `open_handoff`, which bumps the
    # generation and writes a payload derived from current state. See `handoff_claim` and
    # `handoff_queue_is_current` for how the refusal is reported so it is not mistaken for a
    # foreign claim.
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


# --------------------------------------------------------------------------- #
# #927: transport replaces `session_mode`
# --------------------------------------------------------------------------- #
#: A campaign records a PREFERENCE (durable) and resolves an EFFECT per transition. The old
#: `session_mode` conflated the two into one permanent hand-authored answer, so a campaign
#: could not express "I want a pane chain, but herdr is missing right now".
PANE_CHAIN_TRANSPORT = "pane_chain"
INLINE_TRANSPORT = "inline"
TRANSPORTS = frozenset({PANE_CHAIN_TRANSPORT, INLINE_TRANSPORT})

#: The one-way legacy mapping. Kept as a module constant rather than inlined twice so the
#: resolver and the compatibility projection cannot drift apart — a drift here would let a
#: rolled-back build execute the OPPOSITE transport, silently.
_LEGACY_TO_TRANSPORT = {
    FRESH_SESSION_MODE: PANE_CHAIN_TRANSPORT,   # "fresh-session"
    "single-session": INLINE_TRANSPORT,
}
_TRANSPORT_TO_LEGACY = {v: k for k, v in _LEGACY_TO_TRANSPORT.items()}


def campaign_transport(state) -> tuple[str, str]:
    """The campaign's PREFERRED transport and where that answer came from. PURE.

    Returns ``(transport, provenance)`` with provenance in
    ``recorded | migrated | unrecognized | legacy_default``.

    Migration happens on READ and writes nothing — materialising it is the next locked write's
    job, so no read path mutates state. The canonical field always wins over a disagreeing
    legacy one, because the legacy field is a write-only projection (see `legacy_session_mode`)
    and a hand edit of it must never override the real answer.

    ``legacy_default`` marks the ONE case that is genuinely defaulted: a pre-existing campaign
    carrying neither field. A NEW campaign never reaches it — creation probes and records a
    preference explicitly. Conflating those two is exactly the regression that would leave a
    healthy new campaign on ``inline`` and preserve the default #927 exists to invert.
    """
    if not isinstance(state, dict):
        return (INLINE_TRANSPORT, "legacy_default")

    recorded = state.get("preferred_transport")
    if recorded is not None:
        if recorded in TRANSPORTS:
            return (recorded, "recorded")
        return (INLINE_TRANSPORT, "unrecognized")

    legacy = state.get("session_mode")
    if legacy is not None:
        migrated = _LEGACY_TO_TRANSPORT.get(legacy)
        if migrated is not None:
            return (migrated, "migrated")
        # Never guess. An unrecognised value degrades visibly rather than being mapped by
        # resemblance — but it does NOT hard-fail, because that would strand a live campaign.
        return (INLINE_TRANSPORT, "unrecognized")

    return (INLINE_TRANSPORT, "legacy_default")


#: Where the two-event log lives on driver-state. Additive; `additionalProperties: true`
#: already permits it, and `validate_driver_state` has no unknown-key branch.
TRANSITIONS_KEY = "transitions"

#: The CLOSED set of terminal outcomes. `launch_indeterminate` is deliberately ABSENT: an
#: earlier draft made it terminal while other paths still required the same transition to be
#: reclaimable, so an implementation treating terminals as closed would strand it forever. An
#: indeterminate launch is the ABSENCE of a terminal event — the crash signature, not an event.
TERMINAL_OUTCOMES = frozenset({
    "successor_acked",        # a successor is running and acknowledged
    "inline_continued",       # resolved inline; the predecessor carries on
    "launch_failed",          # provably nothing started; retryable
    "start_failed",           # a pane exists but no agent runs in it
    "reconciled_no_action",   # reclaim proved there was nothing to do
    "created",                # campaign creation recorded a preference
    "parked_unreconcilable",  # a human must look; never auto-reclaimed
})


def transition_events(state) -> list:
    """Every transition event, oldest first. PURE, and never raises on a malformed state."""
    if not isinstance(state, dict):
        return []
    events = state.get(TRANSITIONS_KEY)
    return list(events) if isinstance(events, list) else []


def append_resolution(state: dict, *, transition_id: str, generation, trigger: str, kind: str,
                      preferred: str, effective: str, probe_reason: str, probe_ms,
                      pane_ref, panes_before, now_ts: int, attempt: int = 1,
                      advisory_emitted: "bool | None" = None) -> str:
    """Append the RESOLUTION event and return its ``resolution_id``.

    This lands BEFORE any action. `successor_pane` is null and `split_attempted` is False at
    this point, and that pair is what later makes recovery unambiguous: a crash here means
    nothing was ever launched.
    """
    resolution_id = f"{transition_id}#{attempt}"
    # A duplicate id would make `resolution()` and `append_terminal_outcome` target the FIRST
    # record, so one terminal event would make every duplicate look closed and hide an
    # unreconciled launch (Step-11 finding 5). Reusing an attempt number is a caller bug.
    if any(e.get("resolution_id") == resolution_id and "outcome" not in e
           for e in transition_events(state)):
        raise DriverStateError(
            f"resolution {resolution_id!r} already exists — reusing an attempt number would "
            "hide an unreconciled launch behind the first record's terminal event")
    state.setdefault(TRANSITIONS_KEY, []).append({
        "resolution_id": resolution_id,
        "transition_id": transition_id,
        "generation": generation,
        "trigger": trigger,
        "kind": kind,
        "preferred_snapshot": preferred,
        "effective": effective,
        "probe_reason": probe_reason,
        "probe_ms": probe_ms,
        "pane_ref": pane_ref,
        "panes_before": list(panes_before) if panes_before is not None else None,
        "split_attempted": False,
        "successor_pane": None,
        "advisory_emitted": advisory_emitted,
        "observed_at": now_ts,
    })
    return resolution_id


def resolution(state, resolution_id: str) -> "dict | None":
    """The resolution event with this id, or None. PURE."""
    for event in transition_events(state):
        if event.get("resolution_id") == resolution_id and "outcome" not in event:
            return event
    return None


def _require_resolution(state, resolution_id: str) -> dict:
    found = resolution(state, resolution_id)
    if found is None:
        raise DriverStateError(f"no resolution {resolution_id!r} to amend")
    return found


def mark_split_attempted(state: dict, *, resolution_id: str) -> None:
    """Record that a split is ABOUT to be called — before calling it.

    The ordering is the whole safety property. With the marker landing first,
    ``split_attempted=False`` proves nothing was created, and only that state authorises a
    relaunch. If it landed after the split, a crash in between would be indistinguishable from
    "never started" and could launch a second successor beside a live one.
    """
    _require_resolution(state, resolution_id)["split_attempted"] = True


def record_successor_pane(state: dict, *, resolution_id: str, pane: str) -> None:
    """Amend the resolution with the ownership-verified new pane id.

    The one permitted in-place write, confined to a field that is null until the split returns.
    """
    _require_resolution(state, resolution_id)["successor_pane"] = pane


def append_terminal_outcome(state: dict, *, resolution_id: str, outcome: str,
                            now_ts: int) -> None:
    """Close a resolution with an immutable terminal event."""
    if outcome not in TERMINAL_OUTCOMES:
        raise DriverStateError(
            f"unknown terminal outcome {outcome!r}; expected one of "
            f"{sorted(TERMINAL_OUTCOMES)}")
    found = resolution(state, resolution_id)
    if found is None:
        raise DriverStateError(f"no resolution {resolution_id!r} to close")
    state.setdefault(TRANSITIONS_KEY, []).append({
        "resolution_id": resolution_id,
        "transition_id": found.get("transition_id"),
        "claim_attempt": found.get("resolution_id", "").rsplit("#", 1)[-1],
        "outcome": outcome,
        "observed_at": now_ts,
    })


def unterminated_resolutions(state) -> list:
    """Resolution ids with no terminal event — the crash signature. PURE."""
    closed = {e.get("resolution_id") for e in transition_events(state) if e.get("outcome")}
    return [e["resolution_id"] for e in transition_events(state)
            if "outcome" not in e and e.get("resolution_id") not in closed]


def transport_set_blocked(state, *, now_ts: int, lease_s: int = 1800) -> tuple[bool, str]:
    """May the sanctioned `transport set` command change the preference now? ``(blocked, reason)``.

    #927 AC 2. Two refusals, and the second is the one that is easy to forget: a child in flight
    is the `mid-child-handoff` case rather than this one, AND a live handoff claim means a
    boundary is mid-launch — changing the recorded answer under it would let the launch and the
    record disagree about what was chosen.
    """
    # Fail CLOSED (Step-11 finding 9). This is a guard: without a readable `issues` list there
    # is no evidence that nothing is in flight, and reporting `ready` on no evidence would let
    # the preference change during an active child or a launch.
    if not isinstance(state, dict):
        return (True, "state_unreadable")
    issues = state.get("issues")
    if not isinstance(issues, list):
        return (True, "issues_unreadable")
    for issue in issues:
        if isinstance(issue, dict) and issue.get("status") == "in_progress":
            return (True, "child_in_flight")
    generation = state.get("generation")
    if generation is not None and handoff_claim_is_live(
            state, now_ts=now_ts, lease_s=lease_s):
        return (True, "handoff_claim_active")
    return (False, "ready")


def _terminal_for(state, resolution_id: str) -> "dict | None":
    """The LATEST terminal event for a resolution, or None. PURE.

    Latest, not first (Step-11 finding 7): the log is append-ordered, so returning the first
    match would keep reporting `parked_unreconcilable` after an unpark had already resolved it,
    and `unpark_blocked` would go on permitting repeated, conflicting adopt/discard decisions
    for the same resolution.
    """
    latest = None
    for event in transition_events(state):
        if event.get("resolution_id") == resolution_id and event.get("outcome"):
            latest = event
    return latest


def unpark_blocked(state, *, resolution_id: str) -> tuple[bool, str]:
    """May `transport unpark` clear this resolution? ``(blocked, reason)``. PURE.

    Only a `parked_unreconcilable` resolution may be unparked. Without this the design's
    "only an operator clears it" would mean hand-editing driver-state, which everything else
    here forbids.
    """
    terminal = _terminal_for(state, resolution_id)
    if terminal is None:
        return (True, "unknown_resolution")
    if terminal.get("outcome") != "parked_unreconcilable":
        return (True, "not_parked")
    return (False, "ready")


def append_unpark(state: dict, *, resolution_id: str, outcome: str, operator: str,
                  reason: str, now_ts: int) -> None:
    """Record an operator's unpark decision as a NEW event.

    Appends rather than rewriting: the `parked_unreconcilable` event stays as the audit record
    of what the run could not decide for itself.
    """
    if outcome not in TERMINAL_OUTCOMES:
        raise DriverStateError(
            f"unknown unpark outcome {outcome!r}; expected one of {sorted(TERMINAL_OUTCOMES)}")
    state.setdefault(TRANSITIONS_KEY, []).append({
        "resolution_id": resolution_id,
        "outcome": outcome,
        "operator": operator,
        "reason": reason,
        "observed_at": now_ts,
    })


def boundary_advisory_line(*, preferred: str, effective: str, reason: str) -> "str | None":
    """The one-line operator advisory for a degraded boundary, or None. PURE.

    #927 AC 4: an operator must SEE the choice being made rather than infer it from silence.
    Carries only a fixed reason token — never probe stdout, which would let odd daemon output
    (terminal escapes included) reach an operator's console.
    """
    if effective == preferred:
        return None
    return (f"### epic-run: transport={effective} preferred={preferred} "
            f"reason={reason} — re-probing next transition")


def advisory_due(transition_id: str, already_emitted) -> bool:
    """Has this transition already advised? PURE.

    Keyed on ``transition_id``, NOT ``generation``: `creation` and `boundary_resume` do not bump
    a generation, so a generation key would make them collide and silently suppress one.
    """
    return transition_id not in (already_emitted or set())


#: What a reclaimer may do with an unterminated boundary resolution.
RECONCILE_VERDICTS = frozenset({
    "relaunch_permitted",   # PROVEN nothing survives
    "adopt_successor",      # a live, running successor exists — never displace it
    "start_failed",         # a pane exists but no agent runs in it
    "park",                 # cannot prove either way; a human decides
})


def reconcile_boundary(record, *, fresh_panes, panes_with_agents,
                       anchor_pane) -> tuple[str, str]:
    """May a reclaimer relaunch this boundary transition? ``(verdict, reason)``. PURE.

    This is where #927's Critical is actually enforced. The rule it exists to make impossible:
    reading ``successor_pane: null`` as proof that nothing was created. With the amendment
    ordering from `mark_split_attempted`, ``null`` has TWO meanings and only one of them is safe:

    ``split_attempted`` False  -> the split was never called; relaunch is proven safe.
    ``split_attempted`` True   -> INDETERMINATE. A pane may exist under a null. The question is
                                  answered by DIFFING a fresh inventory against the recorded
                                  ``panes_before``, never by trusting the null.

    Everything unprovable parks. That is a deliberate liveness-for-safety trade: a stalled run a
    human can restart beats two successors nobody notices.
    """
    if not isinstance(record, dict):
        return ("park", "no_baseline_to_diff")
    # An unreadable inventory can never authorise anything — checked before every other branch,
    # because each of them depends on the inventory being trustworthy.
    if fresh_panes is None:
        return ("park", "inventory_unreadable")

    successor = record.get("successor_pane")
    if successor is not None:
        if successor not in fresh_panes:
            return ("relaunch_permitted", "successor_gone")
        # UNKNOWN agent state is not "no agent" (Step-11 finding 4). Coercing an unreadable
        # agent inventory to an empty set would classify a possibly-live successor as
        # `start_failed` and authorise retiring it.
        if panes_with_agents is None:
            return ("park", "agent_state_unknown")
        if successor not in panes_with_agents:
            # A pane is not a running agent. Acking an empty pane as a live successor would
            # stall the campaign forever with nothing to notice it.
            return ("start_failed", "pane_without_agent")
        return ("adopt_successor", "successor_alive")

    # ONLY an explicit False proves the split was never called (Step-11 finding 3). A missing,
    # null or malformed marker means a corrupt or partially-written resolution, and absence of
    # evidence is not evidence of absence — fall through to the inventory diff instead of
    # authorising a relaunch on a record we cannot read.
    if record.get("split_attempted") is False:
        return ("relaunch_permitted", "never_started")

    # Indeterminate: prove by diff or park. Never by the null.
    panes_before = record.get("panes_before")
    if panes_before is None:
        return ("park", "no_baseline_to_diff")
    appeared = set(fresh_panes) - set(panes_before) - {anchor_pane}
    if appeared:
        return ("park", "indeterminate_pane_appeared")
    return ("relaunch_permitted", "diff_proves_nothing_created")


def child_boundary_precondition(state, next_issue) -> tuple[bool, str]:
    """May a CHILD-BOUNDARY handoff open right now? Returns ``(ok, reason)``. PURE.

    #845, folded into #927. The boundary and the mid-child paths reuse the same generation /
    claim / lease / ack machinery and differ ONLY here, in what must be true before a claim is
    taken:

    - mid-child requires exactly one child ``in_progress`` matching the position record;
    - the boundary requires the opposite — the next child ``queued`` and NOTHING in flight.

    Keeping the difference in a precondition, rather than in the fence, is what lets the fence be
    reused verbatim and leaves the mid-child path genuinely untouched.

    Note what is deliberately NOT here (D232): no ``kind`` discriminator. ``_refuse_foreign_kind``
    documents that this entry point serves only the boundary handoff, "which carries no kind at
    all", and refuses any kind — including an unrecognised one — with rc 3. Introducing one would
    make the boundary reject its own record.
    """
    if not isinstance(state, dict):
        return (False, "next_child_not_queued")
    issues = state.get("issues")
    issues = issues if isinstance(issues, list) else []
    # A child in flight is the mid-child case, and it is checked FIRST: a run with something
    # in_progress must never fall through to a boundary handoff just because the named next
    # child happens to look queued.
    if any(isinstance(i, dict) and i.get("status") == "in_progress" for i in issues):
        return (False, "child_in_flight")
    for issue in issues:
        if isinstance(issue, dict) and issue.get("number") == next_issue:
            if issue.get("status") == "queued":
                return (True, "ready")
            return (False, "next_child_not_queued")
    return (False, "next_child_not_queued")


def legacy_session_mode(transport: str) -> "str | None":
    """The write-only `session_mode` projection for a transport, or None if unknown. PURE.

    This exists ONLY so a build rolled back to a pre-#927 version keeps behaving correctly: it
    reads `session_mode` and knows nothing of `preferred_transport`. It is an OUTPUT, never a
    source — new code reads the canonical field. Removed in a later cleanup issue.
    """
    return _TRANSPORT_TO_LEGACY.get(transport)


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


def corrections_clause(state: dict, issue: int) -> str:
    """The correction a successor MUST see before it builds child ``issue``. ``""`` when none.

    #840's mandatory correction consumer. Posting a correction comment does not repair the issue
    body, and before this nothing surfaced one to the implementing agent — measured:
    ``grep -c correction hooks/driver_lib.py`` was **0**. So an agent could pass the freshness gate
    and then build from the exact stale claim the revalidation had already caught. The 2026-08-02
    owner ruling put the consumer IN SCOPE for that reason, and it lives in the prompt BUILDERS
    because the prompt is the only artifact the successor is guaranteed to receive.

    It renders the evidence, not just a link: the claim verbatim from the body, what it was checked
    against, and what the check found. A bare URL is an instruction to go and read something, which
    an unattended successor may not do; the quoted evidence is the correction itself.
    """
    reval = state.get("queue_revalidation") or {}
    record = (reval.get("children") or {}).get(str(issue))
    if not isinstance(record, dict):
        return ""
    broken = [c for c in (record.get("claims") or [])
              if isinstance(c, dict) and c.get("verdict") == "broken"]
    pending = record.get("pending_disposition")
    url = record.get("correction_comment")
    if not broken and not pending:
        return ""
    parts = [f" CORRECTION for #{issue} — its body carries claims that were checked against the "
             "current main and FOUND STALE. Do NOT build from them, and do not treat the body as "
             "authoritative where it conflicts with this:"]
    for index, claim in enumerate(broken, start=1):
        parts.append(
            f" ({index}) the body claims {claim.get('quoted_from_body')!r}; checked against "
            f"{claim.get('checked_against')}; found: {claim.get('evidence')}.")
    if url:
        parts.append(f" The correction comment is posted at {url} — the body itself is "
                     "deliberately NOT edited, so the comment is the authority.")
    if pending:
        # **INFORMATIONAL, not a block (round-11, found by two lenses independently).** The
        # `pending_disposition` owner gate was cut to #848, so a child carrying a marker is now
        # SELECTED — and this sentence still told the successor an owner decision was required
        # "before any work starts". An unattended successor reads the prompt, not the code, so it
        # stalled on a gate that no longer exists. The marker is still surfaced, because the owner
        # does need to see it; it just no longer claims to stop anything.
        parts.append(f" NOTE, not a blocker: an earlier revalidation marked this child {pending!r}."
                     " That marker is INFORMATIONAL until #848 lands — it does not gate this work."
                     " Proceed, and flag it to the owner in your summary so they can decide"
                     " whether the child is still worth doing.")
    return "".join(parts)


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
        "read the driver-state + the decision store (`python3 hooks/decision_log.py read "
        "--project <name> --run epic-<N>`), and run the next ready child (currently "
        f"#{next_issue}) via /rawgentic:implement-feature to full WF2 completion. Derive position "
        "from durable state, never in-context memory; never re-do a merged/closed child; restate "
        "the run's auth grant. On a blocker, post the ERROR comment and end so the next fresh "
        "session continues."
        # #840 — appended, never interleaved: the bind must stay first (#682) and the correction
        # must survive whether or not the bind travels inside the prompt (#694).
        + corrections_clause(state, next_issue)
    )
    return _lead_with_bind(body, project, include_bind)


def fresh_session_handoff(state: dict, *, mode: str, project=None,
                          include_bind: bool = True, issue_state_probe=None,
                          observed_head: str | None = None) -> dict:
    """Decide the process-boundary handoff after a child reaches a terminal outcome (#569).

    Returns an explicit disposition (NEVER a bare None — design §4 [2]):
    - ``{"outcome": "single_session"}`` when ``mode`` is not the fresh-session mode: no boundary,
      the driver loops in-session exactly as today (byte-identical default).
    - ``{"outcome": "complete"}`` ONLY when EVERY child is ``merged`` — the sole epic-close trigger.
    - ``{"outcome": "ready", "next_issue", "generation", "campaign", "resume_prompt"}`` when a
      queued dependency-satisfied child exists (``generation`` is the monotonic claim token).
    - ``{"outcome": "blocked"}`` when unmerged children remain but none is ready (all
      deferred/abandoned/dependency-blocked) — the epic stays OPEN; NEVER conflated with complete.
    - ``{"outcome": "revalidation_required", "worklist", "observed_head", "reason"}`` (#840) when
      the remaining queue has not been revalidated against ``observed_head``.

    #840, and this is the whole reason selection RAISES instead of returning ``None``: every
    non-``ready`` outcome used to collapse into ``None`` at `resume_prompt_for_state`, which
    reports it as "complete or blocked". A stale queue announced to the operator as *the epic
    finished* is the worst failure available here, so `revalidation_required` is its own explicit
    disposition and is never conflated with `blocked`.
    """
    issues = state.get("issues", [])
    _numbers(issues)  # fail-closed on missing/non-int/duplicate number
    # #840 Step-11 finding 2 (Critical, reproduced): the mode check used to be the FIRST thing
    # here, so `single-session` returned before selection and an armed campaign with a STALE
    # receipt advanced anyway — even when a freshly observed head was supplied. The single-session
    # loop is the epic driver's DEFAULT and the documented fallback, so that was not a corner: it
    # was the main path.
    #
    # The gate therefore runs BEFORE the mode branch. It is still conditional on `observed_head`
    # being supplied, because only a caller that made a real observation can be gated — which is
    # why `launcher_lib next-child` exists and why the in-session loop must select through it
    # rather than reading state itself.
    if observed_head is not None:
        effective_pre, _ = effective_issue_statuses(issues, issue_state_probe)
        try:
            _refuse_unrevalidated_queue(state, observed_head, effective_pre)
        except DriverStateError as exc:
            # **A RECOVERABLE receipt error becomes the same disposition (round-10 Medium 1).**
            # Only `QueueRevalidationRequired` was caught here, so a structural receipt error —
            # an unsupported version, a non-canonical key — escaped as a bare `DriverStateError`.
            # `launcher_lib.main` catches only `LauncherError`, so the real handoff CLI would
            # have exited with an UNCAUGHT TRACEBACK on a state its own message says to fix by
            # running one skill. The `remedy` attribute is what separates recoverable from
            # genuinely corrupt; anything without it still propagates to rc 2.
            if not isinstance(exc, QueueRevalidationRequired) \
                    and getattr(exc, "remedy", None) != "revalidate":
                raise
            return {"outcome": "revalidation_required",
                    "worklist": getattr(exc, "outstanding", []),
                    "observed_head": getattr(exc, "observed_head", observed_head),
                    "validated_head": getattr(exc, "validated_head", None),
                    "reason": str(exc)}
    elif state.get("queue_revalidation") is not None:
        raise DriverStateError(
            "this campaign carries a queue_revalidation receipt, so a handoff disposition "
            "requires a freshly observed head (launcher_lib.observe_head); refusing to decide "
            "without one")
    if mode != FRESH_SESSION_MODE:
        return {"outcome": "single_session"}
    # #695 AC2: the overlay reaches the COMPLETE verdict too, not just selection. A campaign
    # whose last child shipped outside the driver reads `queued` on disk, and without this it
    # would never report complete — the epic would stay open forever with nothing runnable,
    # which is the same stale-file defect wearing a different outcome.
    effective, _overlaid = effective_issue_statuses(issues, issue_state_probe)
    if issues and all(effective[i["number"]] == "merged" for i in issues):
        return {"outcome": "complete"}
    # This is the ONE production selection site, so the probe has to arrive here or the
    # corroboration is dead code. `_cmd_handoff` supplies the real `gh api graphql` probe, and
    # (#840) the freshly observed head from `launcher_lib.observe_head`.
    try:
        # The campaign's own policy, not this function's default (round-4 High 1): dropping it
        # here turned every `pr_open` stacked-PR campaign into a permanent `blocked`.
        nxt = next_ready_issue(state, campaign_deps_satisfied_by(state),
                               issue_state_probe=issue_state_probe,
                               observed_head=observed_head)
    except DriverStateError as exc:
        # Same widening as the pre-gate above (round-10 Medium 1): a recoverable receipt error
        # reaching selection must become the disposition, not a traceback.
        if not isinstance(exc, QueueRevalidationRequired) \
                and getattr(exc, "remedy", None) != "revalidate":
            raise
        return {"outcome": "revalidation_required",
                "worklist": getattr(exc, "outstanding", []),
                "observed_head": getattr(exc, "observed_head", observed_head),
                "validated_head": getattr(exc, "validated_head", None),
                "reason": str(exc)}
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
                # #840 AC4a — one of the two producers. Built here, next to the disposition it
                # rides, so it is the same snapshot the gate just approved rather than a second
                # read that could disagree with it.
                "queue": revalidated_queue_payload(state),
                "resume_prompt": _build_resume_prompt(state, nxt, chosen,
                                                      include_bind=include_bind)}
    return {"outcome": "blocked"}


def revalidated_queue_payload(state: dict) -> dict:
    """The ORDERED snapshot of the queue a successor is about to inherit, plus the head it was
    attested against. PURE.

    Order is load-bearing (`docs/multi-issue-driver.md`), so this is a LIST in `issues` order and
    never a dict keyed by number: a reordered queue hands children out in the wrong dependency
    order, and a set-membership check cannot see that. r2 proposed validating "head and
    membership", which a reviewer refuted for exactly this reason — membership admits a reordered
    queue and falsified per-child fields.

    EVERY child is included, not only the eligible ones. Two reasons: `handoff_claim` has no issue
    probe, so it can only re-derive what durable state alone determines; and a child whose status
    changed between the handoff being written and claimed means the queue moved under the
    successor, which SHOULD invalidate the claim rather than be tolerated.

    Per-child fields come from the receipt (`extraction`, `depth`, `outcome`,
    `correction_comment`) and from the queue entry (`number`, `status`, `validated_against`), so
    the successor consumes the revalidation result rather than re-deriving it — AC4a.
    """
    reval = state.get("queue_revalidation") or {}
    children = reval.get("children") or {}
    payload = []
    for issue in state.get("issues", []):
        record = children.get(str(issue["number"]))
        record = record if isinstance(record, dict) else {}
        payload.append({
            "number": issue["number"],
            "status": issue.get("status"),
            "validated_against": issue.get("validated_against"),
            "extraction": record.get("extraction"),
            "depth": record.get("depth"),
            "outcome": record.get("outcome"),
            "correction_comment": record.get("correction_comment"),
        })
    return {"validated_head": reval.get("validated_head"), "children": payload}


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
    # #840 — `queue` is MANDATORY for a revalidation campaign and ABSENT otherwise.
    #
    # The discriminator is the state's own `queue_revalidation`, NOT the disposition's `kind`.
    # r4 keyed it on `kind` and the verifier caught it: `fresh_session_handoff`'s ready
    # disposition carries no `kind` at all (only `mid_child_handoff` sets one) yet IS a campaign
    # producer, so keying on `kind` would either break the two tests that pin exactly three
    # persisted keys on that very path, or silently drop the campaign queue and bypass claim-time
    # validation. Both are wrong.
    #
    # Optional propagation was also refused (pass-3): a producer that dropped `queue` would have
    # `open_handoff` quietly write the legacy three-key shape, bypassing ordered-payload
    # validation. So it RAISES.
    if state.get("queue_revalidation") is not None:
        queue = disposition.get("queue")
        if queue is None:
            raise DriverStateError(
                "a revalidation campaign's handoff disposition must carry `queue` — writing the "
                "legacy record for it would leave the successor's claim with nothing to validate "
                "and silently bypass the ordered-payload check")
        pending["queue"] = queue
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


def handoff_claim_is_live(state: dict, *, now_ts: int, lease_s: int) -> bool:
    """Is the CURRENT generation's handoff claim live? PURE.

    **The name and this line were corrected at round 6.** Round 4 introduced this as "any
    generation"; round 5 scoped it to the current generation (see below) and left the docstring
    claiming otherwise — a function whose contract line contradicts its body is exactly the defect
    class three rounds of this review have been spent removing.

    **Known limit, tracked as #846 and NOT closed here.** Because completion is inferred from
    `claim.generation != state.generation` rather than recorded, a claim that is genuinely still
    running is invisible once a later generation is opened — and nothing stops `open_handoff`
    from opening one, since it has never consulted `handoff_claim` at all (true before #840 PR 2;
    verified by source). Closing that needs an explicit claim lifecycle, which is a change to
    #665's design rather than to this gate.

    #840 Step-11 round 4, High 3. Round 3's precedence fix asked only about the CALLER's own
    generation, which left the dangerous interleaving open: a successor holding generation N+1
    is invisible to a stale successor still asking about N, so the payload diagnostic spoke and
    told the operator to open yet another generation — beside a claimant that is actively
    working. Generation is the right fence for WHO MAY CLAIM; it is the wrong question for
    "is somebody in there right now".

    Live means started, or claimed and still inside its lease. A never-started claim past its
    lease is reclaimable (#665's crash-recovery rule) and is deliberately NOT live — otherwise
    one crashed successor would mask every genuine queue change from then on.
    """
    claim = state.get("handoff_claim")
    if not isinstance(claim, dict):
        return False
    # **Scoped to the CURRENT generation (round-5 High 1).** Round 4 ignored generation entirely
    # so that a claimant on a NEWER generation could not be missed — but nothing clears
    # `handoff_claim` when a generation completes, so a finished claim sat in state looking
    # permanently live and masked every later queue change. The operator was then told "do NOT
    # open another generation" when opening one is precisely the remedy for a payload mismatch:
    # the fix for one unrecoverable instruction had produced another.
    #
    # The current generation is the right scope for BOTH cases. Round 4's stale-caller-N /
    # live-claimant-N+1 shape still trips it, because there the live claim IS the current
    # generation; a historical claim below it is correctly ignored.
    if claim.get("generation") != state.get("generation"):
        return False
    return bool(claim.get("started")) or not handoff_reclaimable(
        state, now_ts=now_ts, lease_s=lease_s)


def handoff_claim_completion_unprovable(state: dict) -> bool:
    """Is there a STARTED claim on another generation whose completion cannot be proven? PURE.

    #840 Step-11 round 7, High 1. Completion is inferred from `claim.generation !=
    state.generation`, never recorded (#846), so "an older started claim" and "a successor that
    finished cleanly" are indistinguishable from durable state. That ambiguity is tolerable for
    deciding who may claim — the generation fence handles it — but NOT for the advice printed on
    a refusal: telling an operator to open yet another generation while a started claimant may
    still be running is how a competitor gets spawned, and that instruction is emitted by code
    this PR added. So the PR owns making it safe even though the lifecycle gap predates it.

    Returns True only for the genuinely ambiguous shape. A claim on the CURRENT generation is
    handled by `handoff_claim_is_live`; a never-started claim was never a takeover.
    """
    claim = state.get("handoff_claim")
    if not isinstance(claim, dict) or not claim.get("started"):
        return False
    return claim.get("generation") != state.get("generation")


def handoff_claim_blocked_by_live_claim(state: dict, generation: int, *, now_ts: int,
                                        lease_s: int) -> bool:
    """Is a LIVE (started, or claimed-and-still-within-lease) claim holding ``generation``? PURE.

    #840 Step-11 round 3, High 3. `handoff_claim` checks this BEFORE it compares the queue payload,
    so when a foreign claimant is live AND the payload is stale, the live claim is the real refusal.
    `retire_predecessor` needs to know which one fired: reporting the payload instead says "no claim
    was ever created — open a new generation", which spawns a COMPETITOR while the real claimant is
    still working. A live claim outranks every payload diagnostic.

    `handoff_claim` calls this itself, so the predicate cannot drift from the refusal it explains.
    """
    claim = state.get("handoff_claim")
    if not isinstance(claim, dict) or claim.get("generation") != generation:
        return False
    return handoff_claim_is_live(state, now_ts=now_ts, lease_s=lease_s)


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
    if handoff_claim_blocked_by_live_claim(state, generation, now_ts=now_ts, lease_s=lease_s):
        return (False, state)  # already taken over, or a live in-progress claim
    # #840 AC4a's consumer — validated HERE, under the generation fence this function already
    # enforces, because this is the moment the successor takes ownership of the queue.
    #
    # The comparison is EXACT and whole-payload: it covers the order, `validated_head`, and every
    # declared per-child field against durable state. r2 proposed "head and membership", which a
    # reviewer refuted — membership admits a reordered queue (order decides which child runs next)
    # and cannot see a falsified `outcome`, `depth` or `correction_comment`. Re-deriving the
    # expected payload from state and comparing wholesale is both stricter and simpler than
    # field-by-field checks, and it cannot drift from the producer because both call the same
    # function.
    if state.get("queue_revalidation") is not None:
        if pend.get("queue") != revalidated_queue_payload(state):
            return (False, state)
    new = dict(state)
    new["handoff_claim"] = {"generation": generation, "claimant": claimant,
                            "claimed_at": now_ts, "started": False}
    return (True, new)


def handoff_queue_is_current(state: dict) -> bool:
    """Does the persisted `handoff_pending.queue` still match what state re-derives? PURE.

    #840 Step-11 finding 4. `handoff_claim` refuses on a queue mismatch, and that refusal used to be
    indistinguishable from "a foreign or live claim holds this generation" — the reason
    `retire_predecessor` printed. The mismatch is a DIFFERENT situation with a different remedy: the
    queue legitimately moved under an in-flight handoff (any `record_child_outcome` on an included
    child does it), the predecessor is still alive and guarded, and the fix is to run the handoff
    again so `open_handoff` writes a payload derived from current state — NOT to retry the same
    generation, which can never succeed, and NOT to wait for the lease, which does not apply because
    no claim was ever created.

    Exact equality is deliberately kept in `handoff_claim`: it is what detects a reordered or
    field-falsified payload, which is the whole point of AC4a. This function only makes the refusal
    legible. It never writes — the single-writer rule for `handoff_pending` is asserted by AST in
    `tests/hooks/test_mid_child_handoff.py`.

    Returns True when there is genuinely nothing to check — a campaign with no receipt, or no
    pending record at all — so a caller can use it as "is the mismatch the reason?" without
    special-casing pre-#840 states.

    **A pending record that carries NO queue while the campaign HAS a receipt returns False**
    (Step-11 round 2, finding 3, reproduced). That is the migration shape: a pre-#840 handoff is
    in flight, `revalidate-children` then arms the campaign, and `handoff_claim` starts requiring a
    payload the in-flight record never had. Returning True there reported the resulting refusal as
    "a foreign or live claim holds it" — the one diagnosis that sends the operator looking for a
    competing session instead of opening a new generation, which is the only thing that works.
    """
    if state.get("queue_revalidation") is None:
        return True
    pending = state.get("handoff_pending")
    if not isinstance(pending, dict):
        return True
    if pending.get("queue") is None:
        return False
    return pending["queue"] == revalidated_queue_payload(state)


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
        # #840 — the mid-child successor resumes the SAME child, so it needs that child's
        # correction just as much as a fresh-session successor needs the next child's.
        + corrections_clause(state, position["issue"])
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
            # #840 AC4a — the second producer. A mid-child successor inherits the same queue and
            # must consume the same attested snapshot.
            "queue": revalidated_queue_payload(state),
            "resume_prompt": _build_mid_child_resume_prompt(state, position, generation,
                                                            include_bind=include_bind)}


def _campaign_wait_errors(wait) -> list[str]:
    """Validate the optional top-level `campaign_wait` object (#943).

    Purely additive: absent or null means "not waiting", so every pre-#943 campaign
    file validates byte-unchanged and no `schema_version` bump is needed. The committed
    contract (`docs/driver-state/queue.schema.json`) already sets
    `additionalProperties: true` at the top level.

    This issue ships the field and this validator ONLY. The behavioural consumers —
    scheduling, Stop-hook release, resume, teardown — belong to #927 and #586.
    """
    if wait is None:
        return []
    if not isinstance(wait, dict):
        return ["campaign_wait must be a JSON object or null"]
    errors = []
    for field in _CAMPAIGN_WAIT_FIELDS:
        value = wait.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"campaign_wait.{field} must be a non-empty string")
    status = wait.get("status")
    if isinstance(status, str) and status not in CAMPAIGN_WAIT_STATUSES:
        errors.append(
            f"campaign_wait.status must be one of {sorted(CAMPAIGN_WAIT_STATUSES)}, "
            f"got {status!r}")
    return errors


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

    errors.extend(_campaign_wait_errors(state.get("campaign_wait")))

    # Serial-active invariant: the driver builds one issue at a time, so at most
    # one issue may be in_progress. pr_open is NOT counted — PRs may accumulate
    # awaiting human merge (the unattended stacked-PR flow, deps_satisfied_by=
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


def validate_campaign_start(state: dict) -> tuple[bool, list[str]]:
    """Validate a driver state is fit to *start* a campaign, else return errors.

    Structural readability (``validate_driver_state``). (The old #163 AC5
    headless-campaign epic-anchor rule left with the headless orchestration —
    M0d, #866.)
    """
    ok, errors = validate_driver_state(state)
    return len(list(errors)) == 0, list(errors)


if __name__ == "__main__":
    # #905: a bare `python3 hooks/driver_lib.py <anything>` used to import-and-exit 0
    # silently — success-shaped nothing that was read as a passing gate. Refuse loudly.
    # `sys` is imported HERE so the module's import-time surface stays byte-identical.
    import sys
    sys.stderr.write(
        "driver_lib is a pure library with no CLI — "
        "use `python3 hooks/launcher_lib.py <subcommand>`\n")
    sys.exit(2)
