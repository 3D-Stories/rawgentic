"""#466 W3 — engine-managed git worktree lifecycle outside /tmp + orchestrator-side promotion.

Extraction-clean (kukakuka consumes phase_executor): NO ``hooks/`` import. Reuses the same
``capture.sanitize_component`` / atomic-write discipline and the ``contract`` mutating-worktree
boundary the #465 adapters enforce, so any worktree this module creates is dispatchable.

Security through-line (design: docs/planning/2026-07-18-466-worktree-lifecycle.md, iteration 3):
the child is a provider CLI subprocess whose ``.git`` control *file* lives inside the
child-writable worktree, so we NEVER run ``git -C <worktree>`` (it trusts a child-rewritable
gitdir pointer). At create we discover the canonical linked-worktree admin dir FROM the canonical
repo (``git -C <repo> worktree list --porcelain``) and record it in trusted metadata; every later
inspect/diff/promote runs with explicit ``--git-dir=<trusted> --work-tree=<worktree>``. The
structural guarantees hold ONLY for an OS-confined (codex) seat — mutating-claude has no OS sandbox
here (#465 contract.py blocker) and must not be wired (W7) until it does.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from . import capture, contract

# ---------------------------------------------------------------------------
# B.1 — types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorktreeIdentity:
    """Run/seat/attempt identity. ``attempt`` is the SAME ``attempt_id`` string the engine builds
    (``engine.py`` ``f"{i}-{uuid4().hex[:8]}"``), so worktree <-> capture-dir <-> W4 registry share
    one identity. Frozen -> hashable -> usable in ``live_identities`` sets."""

    run_id: str
    seat: str
    attempt: str


@dataclass(frozen=True)
class RetentionPolicy:
    max_retained_count: int = 20
    max_age_s: int = 604800  # 7 days
    pinned: frozenset = frozenset()


@dataclass(frozen=True)
class WorktreeConfig:
    root: str
    retention: RetentionPolicy = RetentionPolicy()
    populate_allowlist: tuple = ()


@dataclass(frozen=True)
class WorktreeHandle:
    path: str
    identity: WorktreeIdentity
    base_sha: str
    root: str
    gitdir: str  # trusted linked-worktree admin dir (canon/.git/worktrees/<name>)
    repo: str  # canonical repo working copy (trusted; all remove/prune/list use it)


@dataclass(frozen=True)
class WorktreeInspection:
    dirty: bool
    changed: tuple
    untracked: tuple
    tree_differs: bool  # candidate-tree != base_sha (catches gitignored child work porcelain misses)


@dataclass(frozen=True)
class RetentionRecord:
    path: str
    identity: WorktreeIdentity
    reason: str
    dirty: bool
    created_at: float
    retained_at: float
    base_sha: str
    redactions: tuple = ()
    redaction_failures: tuple = ()
    redaction_incomplete: bool = False


@dataclass(frozen=True)
class PromotionResult:
    promoted: bool
    new_target_sha: Optional[str] = None
    base_sha: Optional[str] = None
    head_sha: Optional[str] = None
    changed_paths: tuple = ()
    reason: str = ""


# ---------------------------------------------------------------------------
# B.1 — pure planning functions (no I/O)
# ---------------------------------------------------------------------------

_TMPDIR_ENV = "TMPDIR"


def resolve_root(root: str, *, forbid_tmp: bool = True) -> str:
    """Canonicalize and validate the worktree root. Absolute required; reject the filesystem
    root; and (``forbid_tmp``) reject equality-with or containment-under ``realpath('/tmp')`` or
    ``realpath($TMPDIR)`` — spike #452 proved codex default ``workspace-write`` roots include all
    of /tmp+$TMPDIR, so a /tmp-resident root is NOT sibling-isolated. ``forbid_tmp=False`` disables
    ONLY that environment policy (a hermetic in-/tmp integration test); canonical containment at
    create time still applies. ponytail: forbid_tmp is the one seam, never set False in prod."""
    if not isinstance(root, str) or not root:
        raise ValueError("worktree root must be a non-empty path string")
    if not os.path.isabs(root):
        raise ValueError(f"worktree root must be absolute (got {root!r})")
    canon = os.path.realpath(root)
    if canon == os.sep:
        raise ValueError("worktree root cannot be the filesystem root '/'")
    if forbid_tmp:
        forbidden = [os.path.realpath("/tmp")]
        tmpdir = os.environ.get(_TMPDIR_ENV)
        if tmpdir:
            forbidden.append(os.path.realpath(tmpdir))
        for f in forbidden:
            if canon == f or canon.startswith(f + os.sep):
                raise ValueError(
                    f"worktree root {root!r} resolves under {f!r} — spike #452: codex default "
                    f"writable roots include all of /tmp+$TMPDIR, so a /tmp-resident root is not "
                    f"sibling-isolated. Choose a root outside /tmp/$TMPDIR "
                    f"(e.g. ~/.local/state/rawgentic/worktrees).")
    return canon


def component_for(raw: str) -> str:
    """A safe path component: ``sanitize_component(raw) + '-' + sha256(raw)[:8]`` — the short hash
    defeats a sanitize-normalization collision (``a/b`` and ``a_b`` sanitize identically)."""
    safe = capture.sanitize_component(raw)
    digest = hashlib.sha256(str(raw).encode("utf-8")).hexdigest()[:8]
    return f"{safe}-{digest}"


def planned_path(root: str, identity: WorktreeIdentity) -> str:
    """``<root>/<run>/<seat>/<attempt>`` from ``component_for`` (the create-time boundary check is
    ``contract.canonical_contained_worktree`` — the SAME boundary the #465 adapters enforce)."""
    return os.path.join(
        root,
        component_for(identity.run_id),
        component_for(identity.seat),
        component_for(identity.attempt),
    )


def decide_disposition(inspection: WorktreeInspection, observation_status: str) -> str:
    """``"clean"`` (safe to force-remove) ONLY when the observation succeeded (``status == ok``)
    AND the tree is not dirty AND the candidate tree equals base. Otherwise ``"retain"`` — a
    failed/timed-out/cancelled obs retains even a clean tree, and ``tree_differs`` retains child
    work that porcelain-dirty misses (CF-2: gitignored files)."""
    if observation_status != contract.OK:
        return "retain"
    if inspection.dirty or inspection.tree_differs:
        return "retain"
    return "clean"


def _age_key(record: RetentionRecord) -> float:
    return record.retained_at if record.retained_at else record.created_at


def select_evictions(
    records: list, policy: RetentionPolicy, now: float, live_identities
) -> tuple:
    """Return ``(evict_list, pressure)``. Over ``max_retained_count`` -> evict oldest **clean,
    non-pinned, non-live** first, then oldest **dirty past ``max_age_s``**; NEVER a live/pinned
    one. If the over-limit slots cannot be filled without touching a protected (live/pinned) or
    not-yet-aged-dirty record, return ``pressure=True`` and evict nothing beyond what is safe.
    The retained count is therefore AGE-bounded, not hard count-bounded (A-H2 tradeoff)."""
    live = set(live_identities or ())
    pinned = policy.pinned

    def protected(rec: RetentionRecord) -> bool:
        return rec.identity in live or rec.identity in pinned

    over = len(records) - policy.max_retained_count
    if over <= 0:
        return [], False

    order = sorted(records, key=_age_key)
    evict: list = []
    # pass 1: clean, unprotected, oldest first
    for rec in order:
        if len(evict) >= over:
            break
        if not rec.dirty and not protected(rec):
            evict.append(rec)
    # pass 2: dirty past max_age, unprotected, oldest first
    if len(evict) < over:
        chosen = set(id(r) for r in evict)
        for rec in order:
            if len(evict) >= over:
                break
            if id(rec) in chosen:
                continue
            if rec.dirty and not protected(rec) and (now - _age_key(rec)) > policy.max_age_s:
                evict.append(rec)
    pressure = len(evict) < over
    return evict, pressure


def _has_dotdot(rel: str) -> bool:
    return ".." in Path(rel).parts


def validate_allowlist(entries: Iterable, worktree_root: str, source_root: str) -> list:
    """Resolve an explicit ``(src_rel -> dst_rel)`` allowlist. No globs; empty = copy nothing
    (fail-closed). Each side must be relative, contain no ``..``, and resolve to a path contained
    under its root (src under ``source_root``, dst under ``worktree_root``). Returns the resolved
    ``[(src_abs, dst_abs)]`` pairs."""
    src_root = os.path.realpath(source_root)
    wt_root = os.path.realpath(worktree_root)
    out: list = []
    for pair in entries:
        src_rel, dst_rel = pair
        for rel in (src_rel, dst_rel):
            if not isinstance(rel, str) or not rel:
                raise ValueError(f"allowlist entry must be non-empty relative strings: {pair!r}")
            if os.path.isabs(rel):
                raise ValueError(f"allowlist entry must be relative (got absolute {rel!r})")
            if _has_dotdot(rel):
                raise ValueError(f"allowlist entry must not contain '..': {rel!r}")
        src_abs = os.path.realpath(os.path.join(src_root, src_rel))
        dst_abs = os.path.realpath(os.path.join(wt_root, dst_rel))
        if not (src_abs == src_root or src_abs.startswith(src_root + os.sep)):
            raise ValueError(f"allowlist src {src_rel!r} escapes source_root")
        if not (dst_abs == wt_root or dst_abs.startswith(wt_root + os.sep)):
            raise ValueError(f"allowlist dst {dst_rel!r} escapes worktree_root")
        out.append((src_abs, dst_abs))
    return out
