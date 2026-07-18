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


# ---------------------------------------------------------------------------
# B.2 — WorktreeManager (I/O; injected git runner)
# ---------------------------------------------------------------------------


class WorktreeError(RuntimeError):
    """A worktree lifecycle operation that must fail loud (create/inspect/promote)."""


def _identity_dict(identity: WorktreeIdentity) -> dict:
    return {"run_id": identity.run_id, "seat": identity.seat, "attempt": identity.attempt}


def _meta_name(identity: WorktreeIdentity) -> str:
    return "__".join(
        component_for(x) for x in (identity.run_id, identity.seat, identity.attempt)
    )


def _parse_porcelain_v2(out: str) -> tuple:
    """Parse ``status --porcelain=v2 -z`` NUL-separated records into (changed, untracked). A
    rename record (``2 …``) is followed by its origPath as a separate NUL field — consume it."""
    changed: list = []
    untracked: list = []
    records = out.split("\x00")
    i = 0
    while i < len(records):
        rec = records[i]
        if not rec:
            i += 1
            continue
        tag = rec[0]
        if tag == "?":
            untracked.append(rec[2:])
        elif tag in ("1", "u"):
            changed.append(rec.split(" ", 8)[-1])
        elif tag == "2":
            changed.append(rec.split(" ", 9)[-1])
            i += 1  # skip the origPath NUL field that trails a rename
        # tag "!" (ignored) intentionally dropped
        i += 1
    return changed, untracked


class WorktreeManager:
    """Owns the git worktree lifecycle. ``run(cmd, env=None) -> (rc, out, err)`` is injected so the
    pure git mechanics are unit-tested against real git in a tmp repo. Every git call that touches a
    worktree uses the TRUSTED admin gitdir (never ``git -C <worktree>``, which trusts a child-
    rewritable ``.git`` pointer). ``forbid_tmp`` is passed to ``resolve_root`` (default True; a
    hermetic in-/tmp integration test sets False). ``clock`` supplies retention timestamps."""

    def __init__(self, run, *, forbid_tmp: bool = True, clock=None):
        self._run = run
        self._forbid_tmp = forbid_tmp
        if clock is None:
            import time  # noqa: PLC0415

            clock = time.time
        self._clock = clock

    def _git(self, *args, env=None):
        return self._run(["git", *args], env=env)

    # -- metadata --------------------------------------------------------

    def _meta_file(self, handle: WorktreeHandle) -> str:
        return os.path.join(handle.root, ".meta", _meta_name(handle.identity) + ".json")

    def _read_meta(self, handle: WorktreeHandle) -> dict:
        import json  # noqa: PLC0415

        with open(self._meta_file(handle), encoding="utf-8") as fh:
            return json.load(fh)

    def _write_meta(self, handle: WorktreeHandle, meta: dict) -> None:
        import json  # noqa: PLC0415

        capture.atomic_write_text(Path(self._meta_file(handle)), json.dumps(meta, indent=2, sort_keys=True))

    # -- create ----------------------------------------------------------

    def create(self, repo: str, identity: WorktreeIdentity, base_sha: str, *, root: str) -> WorktreeHandle:
        repo = os.path.realpath(repo)
        canon_root = resolve_root(root, forbid_tmp=self._forbid_tmp)
        rc, out, err = self._git("-C", repo, "rev-parse", "--verify", f"{base_sha}^{{commit}}")
        if rc != 0:
            raise WorktreeError(f"base_sha {base_sha!r} is not a commit in {repo}: {err.strip()}")
        verified = out.strip()
        planned = planned_path(canon_root, identity)
        canon_wt = contract.canonical_contained_worktree(planned, canon_root)
        for d in (canon_root, os.path.join(canon_root, ".meta"), os.path.join(canon_root, ".retained")):
            os.makedirs(d, exist_ok=True)
            os.chmod(d, 0o700)
        os.makedirs(os.path.dirname(canon_wt), exist_ok=True)
        if os.path.exists(canon_wt):
            raise WorktreeError(f"worktree leaf already exists (git refuses reuse): {canon_wt}")
        rc, out, err = self._git("-C", repo, "worktree", "add", "--detach", canon_wt, verified)
        if rc != 0:
            raise WorktreeError(f"git worktree add failed: {err.strip()}")
        # From here a failure leaves a REGISTERED checkout with no handle -> compensate (CF-4).
        try:
            gitdir = self._discover_gitdir(repo, canon_wt)
            handle = WorktreeHandle(
                path=canon_wt, identity=identity, base_sha=verified,
                root=canon_root, gitdir=gitdir, repo=repo,
            )
            self._write_meta(handle, {
                "identity": _identity_dict(identity), "base_sha": verified,
                "gitdir": gitdir, "repo": repo, "created_at": self._clock(), "populated": [],
            })
        except BaseException:
            self._compensate_create(repo, canon_wt)  # raises hard if it cannot remove the orphan
            raise
        return handle

    def _discover_gitdir(self, repo: str, canon_wt: str) -> str:
        """Trusted admin-dir discovery (CF-9): read ONLY under canonical ``.git`` — never the
        child-writable worktree ``.git`` file. ``rev-parse --git-common-dir`` gives the canonical
        ``.git``; the admin dir is the ``worktrees/<name>`` whose ``gitdir`` file points at this
        worktree's ``.git``."""
        rc, out, err = self._git("-C", repo, "rev-parse", "--git-common-dir")
        if rc != 0:
            raise WorktreeError(f"git rev-parse --git-common-dir failed: {err.strip()}")
        common = out.strip()
        if not os.path.isabs(common):
            common = os.path.realpath(os.path.join(repo, common))
        wt_dotgit = os.path.realpath(os.path.join(canon_wt, ".git"))
        worktrees = os.path.join(common, "worktrees")
        for name in sorted(os.listdir(worktrees)):
            gitdir_file = os.path.join(worktrees, name, "gitdir")
            try:
                with open(gitdir_file, encoding="utf-8") as fh:
                    content = fh.read().strip()
            except OSError:
                continue
            if os.path.realpath(content) == wt_dotgit:
                return os.path.join(worktrees, name)
        raise WorktreeError(f"trusted admin gitdir not found under {worktrees} for {canon_wt}")

    def _compensate_create(self, repo: str, canon_wt: str) -> None:
        rc, _out, err = self._git("-C", repo, "worktree", "remove", "--force", canon_wt)
        self._git("-C", repo, "worktree", "prune")
        if os.path.exists(canon_wt):
            raise WorktreeError(
                f"CREATE COMPENSATION FAILED — orphan worktree remains at {canon_wt} "
                f"(rc={rc}): {err.strip()}")

    # -- inspect ---------------------------------------------------------

    def inspect(self, handle: WorktreeHandle) -> WorktreeInspection:
        rc, out, err = self._git(
            "--git-dir", handle.gitdir, "--work-tree", handle.path,
            "status", "--porcelain=v2", "-z", "--untracked-files=all",
        )
        if rc != 0:
            raise WorktreeError(f"status failed via trusted gitdir: {err.strip()}")
        changed, untracked = _parse_porcelain_v2(out)
        dirty = bool(changed or untracked)
        base_tree = self._base_tree(handle)
        cand_tree, _changed_paths = self._candidate_tree(handle)
        tree_differs = cand_tree != base_tree
        return WorktreeInspection(
            dirty=dirty, changed=tuple(changed), untracked=tuple(untracked), tree_differs=tree_differs,
        )

    def _base_tree(self, handle: WorktreeHandle) -> str:
        rc, out, err = self._git(
            "--git-dir", handle.gitdir, "--work-tree", handle.path,
            "rev-parse", f"{handle.base_sha}^{{tree}}",
        )
        if rc != 0:
            raise WorktreeError(f"base tree resolve failed: {err.strip()}")
        return out.strip()

    def _candidate_tree(self, handle: WorktreeHandle) -> tuple:
        """Build the immutable candidate tree capturing the whole filesystem work product
        (dirty-tracked + untracked, respecting .gitignore) via a temp index that MUST live in
        ``.meta`` (probe gotcha: an in-worktree index gets swept by ``add -A``). Returns
        ``(tree_sha, changed_paths)``."""
        import tempfile  # noqa: PLC0415

        meta_dir = os.path.join(handle.root, ".meta")
        os.makedirs(meta_dir, exist_ok=True)
        fd, idx = tempfile.mkstemp(dir=meta_dir, prefix=".idx-")
        os.close(fd)
        env = {"GIT_INDEX_FILE": idx}
        try:
            rc, _o, err = self._git(
                "--git-dir", handle.gitdir, "--work-tree", handle.path, "read-tree", handle.base_sha, env=env)
            if rc != 0:
                raise WorktreeError(f"read-tree failed: {err.strip()}")
            rc, _o, err = self._git(
                "--git-dir", handle.gitdir, "--work-tree", handle.path, "add", "-A", env=env)
            if rc != 0:
                raise WorktreeError(f"add -A failed: {err.strip()}")
            rc, out, err = self._git(
                "--git-dir", handle.gitdir, "--work-tree", handle.path, "write-tree", env=env)
            if rc != 0:
                raise WorktreeError(f"write-tree failed: {err.strip()}")
            tree = out.strip()
            rc, out, err = self._git(
                "--git-dir", handle.gitdir, "--work-tree", handle.path,
                "diff", "--name-only", handle.base_sha, tree, env=env)
            changed = [ln for ln in out.split("\n") if ln.strip()]
            return tree, changed
        finally:
            try:
                os.unlink(idx)
            except OSError:
                pass

    # -- populate (write-ahead, CF-7) ------------------------------------

    def populate(self, handle: WorktreeHandle, source_root: str, allowlist) -> None:
        import shutil  # noqa: PLC0415

        pairs = validate_allowlist(allowlist, handle.path, source_root)
        for src_abs, dst_abs in pairs:
            self._meta_populate(handle, dst_abs, "pending")  # write-ahead BEFORE copy
            os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
            shutil.copy2(src_abs, dst_abs)
            self._meta_populate(handle, dst_abs, "complete")

    def _meta_populate(self, handle: WorktreeHandle, dst_abs: str, state: str) -> None:
        meta = self._read_meta(handle)
        pop = meta.setdefault("populated", [])
        for entry in pop:
            if entry.get("dst") == dst_abs:
                entry["state"] = state
                break
        else:
            pop.append({"dst": dst_abs, "state": state})
        self._write_meta(handle, meta)
