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
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

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
    tree_differs: bool  # candidate-tree != base_sha; guards a HEAD-advanced/committed work-product
    # that a status-vs-HEAD read would call clean. For the ONLY sanctioned seat (an OS-confined
    # codex child that cannot commit or move HEAD) it never diverges from `dirty`; it becomes
    # load-bearing only on the unconfined/claude path (W7-gated). NOT a gitignore signal — `add -A`
    # respects .gitignore; gitignored child secrets are closed by the retain-path os.walk, not here.


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


def PROMOTE_ANY(path: str) -> bool:  # pylint: disable=invalid-name,unused-argument
    """#472 D7: the EXPLICIT allow-all promotion path policy. ``promote`` requires a
    ``path_policy`` (fail-closed boundary); a caller that genuinely wants every changed
    path promotable names this instead of omitting the policy."""
    return True


def _norm_rel_components(raw: str, *, what: str) -> tuple:
    """POSIX-normalize a relative path to its component tuple, refusing empty/whitespace,
    absolute, and any ``..`` component (fail-closed). Shared by ``promote_appendix_only`` for
    BOTH its prefixes (factory time) and each candidate path (call time)."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{what} must be a non-empty path (got {raw!r})")
    # A backslash is a valid POSIX filename character, NOT a path separator — treating it as one
    # (raw.replace("\\","/")) would fold a repo-root file literally named "docs\planning\appendix\x"
    # into the appendix prefix, bypassing the policy (8a-F3). Reject it outright: git tracks paths
    # with forward slashes, so a legitimate promotable path never contains a backslash.
    if "\\" in raw:
        raise ValueError(f"{what} must not contain a backslash (a POSIX filename char, not a "
                         f"separator) (got {raw!r})")
    posix = raw
    if posix.startswith("/"):
        raise ValueError(f"{what} must be relative, not absolute (got {raw!r})")
    parts = tuple(c for c in posix.split("/") if c not in ("", "."))
    if not parts or any(c == ".." for c in parts):
        raise ValueError(f"{what} must not be empty, '.', or contain '..' (got {raw!r})")
    return parts


def promote_appendix_only(prefixes) -> Callable[[str], bool]:
    """#559 AC1 (design §2.6): a ``path_policy`` factory admitting ONLY changed paths under one of
    the given directory ``prefixes`` — the scoped counterpart to ``PROMOTE_ANY``. Both the prefixes
    (at factory time) and each candidate path (at call time) are POSIX-normalized and rejected if
    empty, absolute, or containing a ``..`` component; comparison is on COMPONENT boundaries so
    ``docs/planning/appendix/`` never admits ``docs/planning/appendix-evil/x``. A malformed prefix
    is a factory-time ``ValueError`` (fail-closed) — a caller cannot construct an over-broad policy;
    a malformed candidate path at call time is simply not promotable (returns False)."""
    prefix_tuples = [_norm_rel_components(p, what="prefix") for p in prefixes]
    if not prefix_tuples:
        raise ValueError("promote_appendix_only: at least one prefix is required")

    def policy(path: str) -> bool:
        try:
            parts = _norm_rel_components(path, what="path")
        except ValueError:
            return False  # a malformed candidate path is never promotable (fail-closed)
        return any(parts[:len(pref)] == pref for pref in prefix_tuples)
    return policy


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
    failed/timed-out/cancelled obs retains even a clean tree, and ``tree_differs`` retains a
    HEAD-advanced/committed work-product a status-vs-HEAD read would call clean (the W7-gated
    unconfined path — NOT gitignored work, which the retain-path os.walk redacts instead).

    Deliberate contract for gitignored-only content on a SUCCESSFUL obs (dirty=False,
    tree_differs=False): the disposition is ``clean`` and the worktree is force-removed. This is
    NOT a leak — removal destroys the gitignored file, it does not persist it — and gitignored
    content is not promotable work product (``add -A`` respects .gitignore). A gitignored secret is
    only ever *retained* on a dirty-or-failed disposition, where the os.walk in ``_retain`` scans
    it (best-effort — see the redaction block; detection is heuristic, not a categorical
    guarantee)."""
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


def _identity_from_dict(data: dict) -> WorktreeIdentity:
    return WorktreeIdentity(run_id=data["run_id"], seat=data["seat"], attempt=data["attempt"])


def _record_to_dict(record: RetentionRecord) -> dict:
    return {
        "path": record.path, "identity": _identity_dict(record.identity), "reason": record.reason,
        "dirty": record.dirty, "created_at": record.created_at, "retained_at": record.retained_at,
        "base_sha": record.base_sha, "redactions": list(record.redactions),
        "redaction_failures": list(record.redaction_failures),
        "redaction_incomplete": record.redaction_incomplete,
    }


def _records_from_index(index: dict) -> list:
    out = []
    for r in index.get("records", []):
        out.append(RetentionRecord(
            path=r["path"], identity=_identity_from_dict(r["identity"]), reason=r["reason"],
            dirty=r["dirty"], created_at=r["created_at"], retained_at=r["retained_at"],
            base_sha=r["base_sha"], redactions=tuple(r.get("redactions", ())),
            redaction_failures=tuple(r.get("redaction_failures", ())),
            redaction_incomplete=r.get("redaction_incomplete", False),
        ))
    return out


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
        elif tag == "1":
            changed.append(rec.split(" ", 8)[-1])  # ordinary: path at token index 8
        elif tag == "u":
            changed.append(rec.split(" ", 10)[-1])  # unmerged: 11 tokens, path at index 10
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

    def __init__(self, run, *, forbid_tmp: bool = True, clock=None, retention: RetentionPolicy = None):
        self._run = run
        self._forbid_tmp = forbid_tmp
        if clock is None:
            import time  # noqa: PLC0415

            clock = time.time
        self._clock = clock
        self._retention = retention if retention is not None else RetentionPolicy()

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
        # Recompute retention from the on-disk records (don't trust a latched `pressure` flag): aged
        # dirty records and now-evictable clean ones are dropped here, so `pressure` self-heals as
        # records age out rather than blocking creates forever (the flag is only re-derived on a
        # _retain otherwise). Retained worktrees are never live, so live_identities=() is safe.
        if self._recompute_retention(canon_root):
            raise WorktreeError(
                "retention is still under pressure after recompute (retained count over limit, all "
                "pinned or not-yet-aged) — widen the retention limit or wait for records to age out "
                "(the W4 reaper #467 is the durable resolver); manually removing .retained dirs will "
                "NOT clear it (the index is the source of truth)")
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
        tree_differs = self._candidate_tree(handle) != base_tree
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

    def _candidate_tree(self, handle: WorktreeHandle, *, strict: bool = False) -> str:
        """Build the immutable candidate tree capturing the whole filesystem work product
        (dirty-tracked + untracked, respecting .gitignore) via a temp index that MUST live in
        ``.meta`` (probe gotcha: an in-worktree index gets swept by ``add -A``). Returns the tree
        SHA only — ``changed_paths`` is computed separately by ``_candidate_changed``.

        ``strict`` picks the failure mode for an UNREADABLE path (child chmod-000):
        - ``strict=False`` (inspect/retain teardown): ``add -A --ignore-errors`` skips it and
          continues — teardown must not strand, and the retain-path scan surfaces it separately.
        - ``strict=True`` (PROMOTE): NO ``--ignore-errors``. A skipped unreadable file would
          silently REVERT that path to its base content (or drop an untracked one) in the promoted
          tree while ``changed_paths`` stays silent — so promote fails loud instead (never a silent
          partial promotion)."""
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
            add_args = ["--git-dir", handle.gitdir, "--work-tree", handle.path, "add", "-A"]
            if not strict:
                add_args.append("--ignore-errors")
            rc, _o, err = self._git(*add_args, env=env)
            if strict and rc != 0:
                raise WorktreeError(
                    f"promote: cannot build a complete candidate tree (unreadable path?): "
                    f"{err.strip()} — refusing rather than silently promoting a partial/reverted tree")
            rc, out, err = self._git(
                "--git-dir", handle.gitdir, "--work-tree", handle.path, "write-tree", env=env)
            if rc != 0:
                raise WorktreeError(f"write-tree failed: {err.strip()}")
            return out.strip()
        finally:
            try:
                os.unlink(idx)
            except OSError:
                pass

    def _candidate_changed(self, handle: WorktreeHandle, tree: str) -> list:
        """``diff --name-only -z`` between base and the candidate tree — ``-z`` so a path with a
        newline (core.quotePath) can't mis-parse (the list gates the promote allowlist)."""
        rc, out, err = self._git(
            "--git-dir", handle.gitdir, "--work-tree", handle.path,
            "diff", "--name-only", "-z", handle.base_sha, tree)
        if rc != 0:
            raise WorktreeError(f"candidate diff failed: {err.strip()}")
        return [p for p in out.split("\x00") if p]

    def content_evidence(self, handle: WorktreeHandle) -> dict:
        """#469 W6 (OQ-4): the executor-derived, read-only git commitment for a seat's work product,
        built under ONE snapshot boundary. Returns
        ``{base_sha, head_sha, content_tree_sha, changed_paths}`` where ``content_tree_sha`` is the
        FULL-worktree candidate tree (committed + dirty + untracked, .gitignore-respecting) and
        ``changed_paths`` (sorted, unique, worktree-relative) is diffed from that SAME tree — never a
        second independent write-tree, so the SHA and the path list share one boundary. Uses the
        TRUSTED admin gitdir throughout (never ``git -C <worktree>``). ``strict=True``: an unreadable
        path REFUSES rather than silently under-reporting the produced content (the work-product
        equivalent of promote's no-silent-partial rule). ``contract.derive_work_product`` consumes
        this; ``head_sha`` alone is NOT a content commitment — ``content_tree_sha`` is."""
        tree = self._candidate_tree(handle, strict=True)
        changed = self._candidate_changed(handle, tree)
        head = self._worktree_head(handle)
        return {
            "base_sha": handle.base_sha,
            "head_sha": head,
            "content_tree_sha": tree,
            "changed_paths": sorted(set(changed)),
        }

    # -- populate (write-ahead, CF-7) ------------------------------------

    def populate(self, handle: WorktreeHandle, source_root: str, allowlist) -> None:
        import shutil  # noqa: PLC0415

        pairs = validate_allowlist(allowlist, handle.path, source_root)
        for src_abs, dst_abs in pairs:
            self._meta_populate(handle, dst_abs, "pending")  # write-ahead BEFORE copy
            os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
            shutil.copy2(src_abs, dst_abs)
            self._meta_populate(handle, dst_abs, "complete")

    # -- promote (Task 4, AC-3) — orchestrator-only, CAS-guarded --------

    _ZERO_SHA = "0" * 40

    def _worktree_head(self, handle: WorktreeHandle) -> str:
        rc, out, err = self._git(
            "--git-dir", handle.gitdir, "--work-tree", handle.path, "rev-parse", "HEAD")
        if rc != 0:
            raise WorktreeError(f"worktree HEAD resolve failed: {err.strip()}")
        return out.strip()

    def target_tip(self, handle: WorktreeHandle, target_ref: str) -> Optional[dict]:
        """#570 L1: the live target ref's current tip — ``{"sha", "tree", "parents", "message"}`` —
        or None when the ref does not resolve. collect_work_product uses this to detect a promotion
        that LANDED (``promote``'s ``update-ref`` succeeded) but whose finalize crashed before
        recording ``new_sha``. The landed commit is identified STRUCTURALLY, not by message text:
        ``promote`` commits ``tree == candidate_tree_sha`` (== ``content_evidence.content_tree_sha``)
        parented on ``base_sha`` (== ``expected_target_sha`` for an existing ref, per the CF-1 guard),
        so a genuine landing has ``tip.tree == candidate_tree_sha`` and ``expected_target_sha`` among
        its parents — a fingerprint a foreign/crafted commit cannot forge (it would need our exact
        content tree). ``message`` is returned for diagnostics only, never for authentication."""
        rc, out, _err = self._git(
            "--git-dir", handle.gitdir, "rev-parse", "--verify", "--quiet", f"{target_ref}^{{commit}}")
        sha = out.strip()
        if rc != 0 or not sha:
            return None
        rc_t, tree, _et = self._git("--git-dir", handle.gitdir, "rev-parse", f"{sha}^{{tree}}")
        rc_p, plist, _ep = self._git("--git-dir", handle.gitdir, "rev-list", "--parents", "-n", "1", sha)
        # `rev-list --parents -n1 <sha>` prints "<sha> <parent1> <parent2>..." — drop the leading sha.
        parents = tuple(plist.split()[1:]) if rc_p == 0 else ()
        rc_m, msg, _em = self._git("--git-dir", handle.gitdir, "log", "-1", "--format=%B", sha)
        return {"sha": sha, "tree": tree.strip() if rc_t == 0 else "",
                "parents": parents, "message": msg.strip() if rc_m == 0 else ""}

    def promote(self, handle: WorktreeHandle, *, target_ref: str, expected_target_sha: str,
                message: str, path_policy) -> PromotionResult:
        """Promote the worktree's filesystem work product onto ``target_ref`` — orchestrator-only,
        CAS-guarded (B.3). Builds an immutable candidate tree (dirty + untracked, no child commit
        needed), guards against a stale base (CF-1: a candidate rooted at a stale base would
        silently revert peer commits), commits parented on ``base_sha`` (NEVER on
        ``expected_target_sha``), and compare-and-swaps the ref. Returns a ``PromotionResult`` —
        ``promoted=False`` with a reason on a stale base or a moved target, never a silent revert.

        ``path_policy`` is REQUIRED (#472 D7): the promotion boundary is fail-closed — a caller
        that wants no scoping must name ``PROMOTE_ANY`` explicitly; omitting the policy (or
        passing None) refuses rather than silently promoting every changed path."""
        if path_policy is None:
            raise TypeError(
                "promote: path_policy is required — pass PROMOTE_ANY to explicitly allow all "
                "paths, or a predicate scoping the promotable set (#472 D7)")
        tree = self._candidate_tree(handle, strict=True)  # promote never silently drops/reverts
        changed = self._candidate_changed(handle, tree)
        outside = [p for p in changed if not path_policy(p)]
        if outside:
            raise WorktreeError(
                f"promote refused: {len(outside)} changed path(s) outside promotable policy: "
                f"{outside[:3]}")
        head_sha = self._worktree_head(handle)
        ref_exists = bool(expected_target_sha) and expected_target_sha != self._ZERO_SHA
        # CF-1 base-staleness guard: for an EXISTING ref, the worktree must have been cut at the
        # current tip (base == expected). Otherwise a candidate rooted at the stale base silently
        # reverts whatever landed between base and expected — refuse and let the caller re-cut.
        if ref_exists and handle.base_sha != expected_target_sha:
            return PromotionResult(
                promoted=False, base_sha=handle.base_sha, head_sha=head_sha,
                changed_paths=tuple(changed), reason="base stale — rebase")
        commit_args = [
            "--git-dir", handle.gitdir, "--work-tree", handle.path, "commit-tree", tree, "-m", message]
        if handle.base_sha and handle.base_sha != self._ZERO_SHA:
            commit_args += ["-p", handle.base_sha]  # parent on the REAL base, never on expected
        rc, out, err = self._git(*commit_args)
        if rc != 0:
            raise WorktreeError(f"commit-tree failed: {err.strip()}")
        new_commit = out.strip()
        # atomic compare-and-swap: <old>=expected (all-zeros => "must not exist" create semantics)
        rc, _out, _err = self._git(
            "--git-dir", handle.gitdir, "--work-tree", handle.path,
            "update-ref", target_ref, new_commit, expected_target_sha)
        if rc != 0:
            # CAS refused: the ref moved, was deleted, or otherwise did not equal expected. Safe
            # (no write) either way; the reason covers both moved-and-missing.
            return PromotionResult(
                promoted=False, base_sha=handle.base_sha, head_sha=head_sha,
                changed_paths=tuple(changed), reason="target advanced or ref state changed")
        return PromotionResult(
            promoted=True, new_target_sha=new_commit, base_sha=handle.base_sha,
            head_sha=head_sha, changed_paths=tuple(changed), reason="")

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

    # -- finalize / disposition (Task 3) ---------------------------------

    def finalize(self, handle: WorktreeHandle, observation_status: str, *, live_identities=()):
        """THE only public disposition entry (A-H4). Inspect, decide, route to private _clean or
        _retain. Returns ``None`` on a clean disposition, else the ``RetentionRecord``."""
        inspection = self.inspect(handle)
        disposition = decide_disposition(inspection, observation_status)
        if disposition == "clean":
            self._clean(handle)
            return None
        return self._retain(handle, inspection, observation_status, live_identities=live_identities)

    def _clean(self, handle: WorktreeHandle) -> None:
        rc, _out, err = self._git("-C", handle.repo, "worktree", "remove", "--force", handle.path)
        self._git("-C", handle.repo, "worktree", "prune")
        if os.path.exists(handle.path):
            raise WorktreeError(f"clean removal left residue at {handle.path} (rc={rc}): {err.strip()}")

    def _retain(self, handle: WorktreeHandle, inspection: WorktreeInspection,
                observation_status: str, *, live_identities=()) -> RetentionRecord:
        import shutil  # noqa: PLC0415

        created_at = float(self._read_meta(handle).get("created_at") or self._clock())
        # (1) MOVE + unregister FIRST (so the redaction scan runs over the durable tree, and the
        #     original path is de-registered before we touch contents).
        dest = os.path.join(handle.root, ".retained", _meta_name(handle.identity))
        if os.path.exists(dest):
            shutil.rmtree(dest)  # a prior retain of the same identity — replace
        shutil.move(handle.path, dest)
        os.chmod(dest, 0o700)
        self._git("-C", handle.repo, "worktree", "prune", "--expire=now")
        rc, out, _err = self._git("-C", handle.repo, "worktree", "list", "--porcelain")
        if rc == 0 and any(ln == f"worktree {handle.path}" for ln in out.split("\n")):
            raise WorktreeError(f"retain: worktree still registered after prune: {handle.path}")
        # (2) redact the MOVED tree (CF-2 — walk catches gitignored/committed secrets porcelain misses)
        redactions, failures = self._redact_tree(handle, dest)
        record = RetentionRecord(
            path=dest, identity=handle.identity,
            reason=("failed-observation" if observation_status != contract.OK else "dirty-tree"),
            dirty=inspection.dirty, created_at=created_at, retained_at=self._clock(),
            base_sha=handle.base_sha, redactions=tuple(redactions),
            redaction_failures=tuple(failures), redaction_incomplete=bool(failures),
        )
        # (3) enforce the retention limit + persist the index (CF-6)
        self._enforce_and_persist(handle.root, record, live_identities)
        return record

    # -- redaction --------------------------------------------------------
    # BEST-EFFORT, not a guarantee. What is VERIFIED is that a redaction that RAN landed (the
    # re-read assert) and that every FAILURE is surfaced (redaction_failures/redaction_incomplete).
    # Detection itself is heuristic: a secret in an encoding/name outside the patterns below, or
    # past the scan cap, may not be flagged. A file left un-redacted because nothing matched is NOT
    # marked incomplete (best-effort residual). The retained tree is 0700 same-user; treat
    # `redaction_incomplete=False` as "the scans that ran passed", never as "provably secret-free".

    _MARKER = "[redacted: populated allowlist secret]\n"
    _SCAN_CAP = 1 << 20  # first 1 MiB scanned; a larger file flags scan-truncated (-> incomplete)
    # Genuinely-secret FILE names only. Broad substrings (*token*, *secret*, *.ini, config.json,
    # settings.json, secrets*) were dropped (Step-11 regression F2): they wiped benign files
    # (tokenizer.py, the stdlib secrets.py, tox.ini, VS Code settings.json) and gutted retention's
    # debugging value — a secret INSIDE such a file is still caught by the content scan.
    _SECRET_NAME_GLOBS = (
        ".env", ".env.*", "*.pem", "*.key", "*.p8", "*.p12", "*.pfx", "*.keystore", ".npmrc",
        ".netrc", ".git-credentials", "id_rsa", "id_rsa.*", "id_ed25519", "id_ed25519.*",
        "authorized_keys", "known_hosts", "*service-account*.json", "*credentials*",
        "kubeconfig", "*.kubeconfig",
    )

    @classmethod
    def _is_secret_name(cls, name: str) -> bool:
        import fnmatch  # noqa: PLC0415

        return any(fnmatch.fnmatch(name, pat) for pat in cls._SECRET_NAME_GLOBS)

    @staticmethod
    def _secret_content_re():
        import re  # noqa: PLC0415

        # NB: an EXPLICIT keyword alternation, NOT keyword + `[\w.-]*` — the unbounded suffix was an
        # O(n^2) ReDoS (Step-11 regression F1: `secretsecret…` with no separator backtracks ~93 min
        # on a 1 MiB child-planted file, uninterruptible C-level) AND over-matched `token_count = 5`.
        # Listing compound keys (secret_access_key, api_key, …) keeps the real catches, bounded.
        return re.compile(
            rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----|"      # PEM private keys
            rb"A(?:KIA|SIA)[0-9A-Z]{16}|"                    # AWS long-term + temp access keys
            rb"xox[baprs]-[0-9A-Za-z-]{10,}|"                # Slack tokens
            rb"gh[pousr]_[0-9A-Za-z]{20,}|"                  # GitHub tokens
            rb"(password|passwd|secret[_-]?access[_-]?key|secret[_-]?key|secret|"
            rb"api[_-]?key|access[_-]?key|auth[_-]?token|access[_-]?token|token)"
            rb"[\"']?\s*[:=]\s*[\"']?\S",                    # optional quote + : or = + a value char
            re.IGNORECASE,
        )

    def _redact_tree(self, handle: WorktreeHandle, dest: str) -> tuple:
        redactions: list = []
        failures: list = []
        # (a) exact populated destinations first (from trusted .meta; remap OLD worktree path -> dest)
        meta = self._read_meta(handle)
        for entry in meta.get("populated", []):
            old = entry.get("dst")
            if not old:
                continue
            rel = os.path.relpath(old, handle.path)
            self._overwrite_marker(os.path.join(dest, rel), "populated", redactions, failures)
        # (b) walk the moved tree, no-follow; scan each REGULAR file by name + content
        pattern = self._secret_content_re()
        for dirpath, _dirs, files in os.walk(dest, followlinks=False):
            for fn in files:
                path = os.path.join(dirpath, fn)
                if os.path.islink(path):
                    continue  # never follow/scan a symlink (also enforced by O_NOFOLLOW)
                if self._is_secret_name(fn):
                    self._overwrite_marker(path, "scan", redactions, failures)
                    continue
                matched, truncated = self._scan_content(path, pattern, failures)
                if matched:
                    self._overwrite_marker(path, "scan", redactions, failures)
                elif truncated:
                    # couldn't fully scan -> surface (fail-closed) rather than silently pass
                    failures.append({"path": path, "kind": "scan-truncated",
                                     "error": f"file exceeds {self._SCAN_CAP} bytes; not fully scanned"})
        return redactions, failures

    def _scan_content(self, path: str, pattern, failures: list) -> tuple:
        """Return ``(matched, truncated)``. ``truncated`` means the file is larger than the scan
        cap, so a no-match is inconclusive (caller flags it incomplete)."""
        # O_NONBLOCK so a child-planted FIFO/socket named like a secret can't BLOCK the open forever
        # (teardown DoS); the S_ISREG guard then skips every non-regular node.
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as exc:
            failures.append({"path": path, "kind": "scan-open", "error": str(exc)})
            return False, False
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                failures.append({"path": path, "kind": "scan-nonregular", "error": "not a regular file"})
                return False, False
            data = os.read(fd, self._SCAN_CAP)
        except OSError as exc:
            failures.append({"path": path, "kind": "scan-read", "error": str(exc)})
            return False, False
        finally:
            os.close(fd)
        return bool(pattern.search(data)), st.st_size > self._SCAN_CAP

    def _overwrite_marker(self, path: str, kind: str, redactions: list, failures: list) -> None:
        marker = self._MARKER.encode("utf-8")
        # O_NONBLOCK so a child-planted FIFO/socket can't block the open forever (teardown DoS).
        try:
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as exc:
            failures.append({"path": path, "kind": kind, "error": f"open: {exc}"})
            return
        try:
            st = os.fstat(fd)
        except OSError as exc:
            os.close(fd)
            failures.append({"path": path, "kind": kind, "error": f"fstat: {exc}"})
            return
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            failures.append({"path": path, "kind": kind, "error": "non-regular file, not redacted"})
            return
        nlink = st.st_nlink
        if nlink > 1:
            # HARDLINK: O_NOFOLLOW does not catch a hardlink, and ftruncate would corrupt the
            # SHARED inode — including a second link OUTSIDE the retained tree. Break our link
            # instead (unlink our name, write a fresh file); the outside link keeps its content.
            os.close(fd)
            self._redact_hardlink(path, marker, kind, redactions, failures)
            return
        try:
            os.ftruncate(fd, 0)
            os.write(fd, marker)
        except OSError as exc:
            failures.append({"path": path, "kind": kind, "error": f"write: {exc}"})
            return
        finally:
            os.close(fd)
        # re-read (no-follow) + assert the content is exactly the marker
        try:
            rfd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                got = os.read(rfd, len(marker) + 1)
            finally:
                os.close(rfd)
        except OSError as exc:
            failures.append({"path": path, "kind": kind, "error": f"reread: {exc}"})
            return
        if got != marker:
            failures.append({"path": path, "kind": kind, "error": "reread-mismatch"})
            return
        redactions.append({"path": path, "kind": kind})
        return

    def _redact_hardlink(self, path: str, marker: bytes, kind: str, redactions: list, failures: list) -> None:
        try:
            os.unlink(path)  # remove OUR link only; the outside link keeps the original content
            nfd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                wrote = os.write(nfd, marker)
            finally:
                os.close(nfd)
        except OSError as exc:
            failures.append({"path": path, "kind": kind, "error": f"hardlink-rewrite: {exc}"})
            return
        if wrote != len(marker):  # short write (ENOSPC/EINTR): surface, don't claim success
            failures.append({"path": path, "kind": kind, "error": "hardlink-short-write"})
            return
        redactions.append({"path": path, "kind": kind, "hardlink_broken": True})

    # -- persistent retention index (CF-6) -------------------------------

    def _index_file(self, root: str) -> str:
        return os.path.join(root, ".meta", "retention-index.json")

    def _read_index(self, root: str) -> dict:
        import json  # noqa: PLC0415

        try:
            with open(self._index_file(root), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {"records": [], "pressure": False}

    def _write_index(self, root: str, data: dict) -> None:
        import json  # noqa: PLC0415

        capture.atomic_write_text(Path(self._index_file(root)), json.dumps(data, indent=2, sort_keys=True))

    def _enforce_and_persist(self, root: str, record: RetentionRecord, live_identities) -> None:
        """Add ``record``, evict per policy, persist the index. ``live_identities`` is used
        TRANSIENTLY for THIS eviction decision (don't evict a currently-live sibling) but is NOT
        persisted: W3 cannot keep it authoritative (a later ``create`` clears it), so the W4 reaper
        (#467) must source liveness from its own job registry, never from this index (CF-6).
        ponytail: single-writer read-modify-write. W3 finalizes serially per orchestrator, so no
        lock. If finalize/promote ever runs concurrently, guard this + `_recompute_retention` with
        an flock on the index (a lost update would orphan a RetentionRecord from the reaper)."""
        import shutil  # noqa: PLC0415

        index = self._read_index(root)
        records = _records_from_index(index) + [record]
        evict, pressure = select_evictions(records, self._retention, self._clock(), live_identities)
        evict_ids = {id(r) for r in evict}
        for rec in evict:
            shutil.rmtree(rec.path, ignore_errors=True)
        kept = [r for r in records if id(r) not in evict_ids]
        self._write_index(root, {"records": [_record_to_dict(r) for r in kept], "pressure": pressure})

    def _recompute_retention(self, root: str) -> bool:
        """Re-derive eviction + pressure from the on-disk records so a latched `pressure` self-heals
        as records age out (retained worktrees are never live -> live_identities=()). Evicts newly
        eligible records, rewrites the index, returns the recomputed pressure. A fresh root with no
        index short-circuits without creating one."""
        import shutil  # noqa: PLC0415

        index = self._read_index(root)
        records = _records_from_index(index)
        if not records:
            if index.get("pressure"):
                self._write_index(root, {"records": [], "pressure": False})
            return False
        evict, pressure = select_evictions(records, self._retention, self._clock(), live_identities=())
        if evict:
            evict_ids = {id(r) for r in evict}
            for rec in evict:
                shutil.rmtree(rec.path, ignore_errors=True)
            records = [r for r in records if id(r) not in evict_ids]
        self._write_index(root, {"records": [_record_to_dict(r) for r in records], "pressure": pressure})
        return pressure
