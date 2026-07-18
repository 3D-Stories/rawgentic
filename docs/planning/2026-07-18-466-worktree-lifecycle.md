# #466 — W3: engine-managed worktree lifecycle outside /tmp + promotion

**Issue:** #466 (epic #475 W3; depends on #464, satisfied) · **Date:** 2026-07-18
**Complexity:** standard-complex (security-sensitive) · **Lane:** full spine · **iteration 3** (2nd volume loop-back — design budget exhausted)

platform_apis:

- api: git worktree add/remove/prune (git worktree add --detach, remove --force, prune --expire=now, worktree list --porcelain)
  feasibility: verified via existing-call-site — hooks/capabilities_lib.py:362-377 drives live `git worktree add --detach <path> HEAD` + `remove --force` + `prune` in the parallelism probe; same git, same host
  failure: fail-loud
- api: git promotion verbs (worktree list --porcelain for trusted-gitdir discovery, status --porcelain=v2 -z, read-tree/write-tree/commit-tree via a temp GIT_INDEX_FILE, update-ref CAS <ref> <new> <expected>)
  feasibility: verified via spike — a live local git probe run 2026-07-18 (this host) confirmed: a temp-index read-tree(base)+add-A+write-tree captures BOTH modified-tracked AND untracked files into the candidate tree while respecting .gitignore; commit-tree parents it on base; `update-ref target <new> 0{40}` creates a fresh ref; `update-ref target <x> <wrong-expected>` is REFUSED (true CAS); a peer-advanced target promoted with expected=tip but base=stale SILENTLY REVERTED the peer commit (CF-1 — hence the base==expected precondition below); `diff --name-only base tree` lists changed paths. Probe gotcha folded into B.3: the temp GIT_INDEX_FILE MUST live outside the worktree (in .meta) or `add -A` sweeps the index file itself.
  failure: fail-loud
- api: local filesystem retention (mkdir mode 0700, os.replace, shutil.move of a linked worktree into .retained, O_NOFOLLOW open for redaction, os.walk followlinks=False)
  feasibility: verified via spike — a live local FS probe run 2026-07-18 (this host) confirmed: `os.open(path, O_RDWR|O_NOFOLLOW)` overwrites a regular file but RAISES ELOOP on a symlink (target left untouched); `shutil.move` relocates the whole worktree and `chmod 0700` holds on the dest; `os.walk(followlinks=False)` enumerates the moved retained tree and flags symlinks (so the content scan never follows a child-planted link out of the tree)
  failure: fail-loud

## Problem (confirmed, spike-grounded)

Mutating executor seats (claude AND codex, AC-B2/OQ-3 CONVERGED) need an **engine-managed git
worktree** each. Spike #452 proved the default codex `-s workspace-write` writable roots are
`{cwd} ∪ {all of /tmp} ∪ {$TMPDIR}`, so `/tmp`-resident worktrees are NOT sibling-isolated by
default — relocating worktrees **outside /tmp/$TMPDIR** keeps sibling isolation even if the #465
`exclude_slash_tmp` override drifts (defense-in-depth). A worktree isolates the *checkout*, not
the *agent*: promotion into the canonical branch is **orchestrator-side, outside the child's
boundary**, and a dirty/failed worktree is **retained + reported, never silently swept** (AC-3).
No worktree module exists today (`workers/` holds only `zhipuai_call.py`).

## Design — `phase_executor/src/phase_executor/worktree.py` (extraction-clean; pure planning layer + `WorktreeManager` with an injected git/fs runner)

### Security model (the through-line for every decision below)

The child is a provider CLI **subprocess**. Its `.git` control *file* lives inside the
child-writable worktree, so **we never run `git -C <worktree>`** (which trusts a child-rewritable
gitdir pointer, A-H5): at create we discover the canonical linked-worktree admin dir WITHOUT
reading the child-writable worktree `.git` file (CF-9) — `git -C <repo> rev-parse
--git-common-dir` gives the canonical `.git`, then the admin dir is the `<common>/worktrees/<name>`
whose `gitdir` file (content = that worktree's `.git` path) matches this worktree; all reads are
under canonical `.git`, child-unreachable (`worktree list --porcelain` alone does NOT emit the
admin gitdir on git 2.43 — probed this host). We record it in **trusted metadata**, and every later
inspect/HEAD/diff/promote runs with explicit `--git-dir=<trusted-admin-dir> --work-tree=<worktree>`.
Trusted metadata (`<root>/.meta/…`), the `.retained` area, and the root itself are created
**mode 0700** and live OUTSIDE the child-writable subtree — a codex child (Landlock
`writable_roots=[worktree]`) structurally cannot reach them.

**Child work-product is FILESYSTEM contents only (CF-3).** An OS-confined child cannot write the
linked-worktree admin index/HEAD (they sit under canonical `.git`, outside `writable_roots`), so it
**cannot `git add` or `git commit`** — `git status` and `git commit` from inside the worktree would
fail to write the admin index. This is intentional: the sole child work-product interface is the
worktree's **filesystem state** (dirty-tracked + untracked files), and the candidate-tree
promotion (B.3) captures exactly that WITHOUT needing any child commit. Nothing in W3 depends on
the child having staged or committed.

**These structural guarantees hold ONLY for an OS-CONFINED (codex) mutating seat** (A-H3/S-M1):
the claude mutating path has no OS sandbox here (the #465 contract.py:260 blocker), so a
mutating-claude child could reach `.meta`/canonical `.git` AND could write the admin index — W3's
guarantees are a PREREQUISITE gated on that same block, and W7 must not wire mutating-claude until
claude gets a real FS sandbox. The live negative-write proof is the W5 canary (#468).

### B.1 Types + the pure planning layer (no I/O — unit-tested)

- `WorktreeIdentity(run_id, seat, attempt)` — `attempt` is the SAME `attempt_id` STRING the engine
  already builds (`engine.py:104`, `f"{i}-{uuid4().hex[:8]}"`), so worktree ↔ capture-dir ↔ the
  future W4 registry share one identity (S-M3). `run_id`/`seat` mirror capture's inputs.
- `WorktreeConfig(root, retention, populate_allowlist)`; `RetentionPolicy(max_retained_count=20,
  max_age_s=604800, pinned: frozenset=())`.
- `WorktreeHandle(path, identity, base_sha, root, gitdir, repo)` — `gitdir` = the trusted admin
  dir; `repo` = the canonical repo working-copy path (CF-5), validated at create and recorded in
  `.meta`, so every later `worktree remove`/`prune`/`list` uses a trusted `repo`, never an
  undefined or child-influenced one.
- `RetentionRecord(path, identity, reason, dirty, created_at, retained_at, base_sha, redactions,
  redaction_failures, redaction_incomplete)`.
- `PromotionResult(promoted, new_target_sha, base_sha, head_sha, changed_paths, reason)`.

Pure functions:
- `resolve_root(root) -> str`: absolute-required; `os.path.realpath`; **reject equality with the
  filesystem root**, and **reject equality-with or containment-under `realpath("/tmp")` or (when
  set) `realpath($TMPDIR)`** (A-C1 — the earlier "containment under fs root" wording rejected every
  absolute path). Returns the canonical root.
- `component_for(raw) -> str`: `capture.sanitize_component(raw) + "-" + sha256(raw)[:8]` (short
  hash defeats a sanitize normalization collision).
- `planned_path(root, identity) -> str`: `<root>/<run>/<seat>/<attempt>` from `component_for`;
  the full boundary check is `contract.canonical_contained_worktree(planned, root)` at create
  time (S-M2 — the SAME boundary the #465 adapters enforce at dispatch, so any W3 worktree is
  guaranteed dispatchable; `realpath` works on the not-yet-existing leaf).
- `decide_disposition(inspection, observation_status) -> "clean"|"retain"` (CF-2): retain when
  the Observation status is failure/timeout/cancel, OR the worktree is porcelain-dirty, OR the
  **candidate tree differs from `base_sha`** (`tree_differs` from a `diff --name-only base
  <candidate>` — catches child work that porcelain misses, e.g. gitignored files that later get
  promoted). `clean` (safe force-remove) fires ONLY when the obs succeeded AND the tree equals
  base AND nothing is dirty. A failed obs retains even on a clean tree.
- `select_evictions(records, policy, now, live_identities) -> (evict: list, pressure: bool)`
  (A-H2): over `max_retained_count` → evict oldest **clean, non-pinned, non-live** first, then
  oldest **dirty past `max_age_s`**; NEVER a live/pinned one; if every over-limit slot is
  live/pinned (or dirty-but-not-yet-aged), return `pressure=True` and evict nothing. The retained
  count is therefore **age-bounded, not hard count-bounded**: a burst of dirty-not-yet-aged
  worktrees can hold the count above `max_retained_count` for up to `max_age_s`, at which point
  `create` fails loud rather than silently over-filling (the diagnostic-retention tradeoff, A-H2).
- `validate_allowlist(entries, worktree_root, source_root)`: explicit `(src_rel → dst_rel)`, no
  globs, empty = copy nothing (fail-closed); each normalized, no `..`, src under source_root, dst
  under the worktree.

**Persistent retention index (CF-6):** `<root>/.meta/retention-index.json` (atomic, 0700) holds
every `RetentionRecord`, the live-state source (identities considered live at decision time), and
the current `pressure` flag. `_retain`, eviction, and create-admission all update it, so W4's
reaper (#467) reconstructs `records` / `live_identities` / unresolved `pressure` from disk rather
than needing W3's in-process state.

### B.2 `WorktreeManager` (I/O; injected `run(cmd, cwd=None, env=None) -> (rc, out, err)`)

- `create(repo, identity, base_sha) -> WorktreeHandle`: `resolve_root`; peel `base_sha^{commit}`
  in the canonical repo (fail-loud if unknown); `planned_path` +
  `contract.canonical_contained_worktree`; ensure the root/`.meta`/`.retained` exist mode 0700;
  the leaf must be absent (git refuses reuse); `git -C repo worktree add --detach <path>
  <verified-sha>`; discover the trusted admin dir via `git -C repo rev-parse --git-common-dir`
  then match `<common>/worktrees/*/gitdir` to this worktree's `.git` path (reads only under
  canonical `.git`, never the child-writable worktree `.git`, CF-9 — `worktree list --porcelain`
  alone does not emit the admin gitdir on git 2.43, probed);
  write `<root>/.meta/<sanitized-id>.json` **atomically** (identity, base_sha, gitdir, repo,
  created_at, populated[] — empty until populate).
  **Transactional compensation (CF-4):** any failure AFTER `worktree add` (admin-dir discovery,
  `.meta` write) force-removes the exact created worktree (`git -C repo worktree remove --force
  <path>` + `prune`) and re-raises; if the compensating removal itself fails, raise a hard error
  naming the orphan path (never leave a registered checkout with no handle).
- `inspect(handle) -> WorktreeInspection`: `git --git-dir=<handle.gitdir> --work-tree=<path>
  status --porcelain=v2 -z --untracked-files=all` → dirty + changed/untracked lists
  (trusted-gitdir, independent of the child, A-H5/OQ-4). `tree_differs` (candidate-tree vs base,
  B.3 step 1) is computed here for `decide_disposition` (CF-2).
- `populate(handle, source_root, allowlist)`: `validate_allowlist`; **write-ahead (CF-7):** for
  each entry, atomically append its destination to `.meta` `populated[]` as `pending` BEFORE
  copying, then mark it `complete` after the copy lands. Retention redacts BOTH `pending` and
  `complete` destinations, so a crash mid-copy cannot leave a populated secret outside the ledger.
- `finalize(handle, observation_status)` (A-H4 — the ONLY public disposition entry): `inspect`,
  `decide_disposition`, route to private `_clean` or `_retain`. Force-remove is private and only
  runs on a `clean` disposition.
  - `_clean(handle)`: `git -C handle.repo worktree remove --force <path>` + `prune`; zero residue.
  - `_retain(handle, inspection, reason)`: **(1) move + unregister FIRST:** `shutil.move` the tree
    to `<root>/.retained/<sanitized-id>/` (mode 0700), then `git -C handle.repo worktree prune
    --expire=now` and **assert** the original path is gone via `worktree list --porcelain` (A-M1).
    The retained tree is now a **plain directory** — safe to read for diagnosis, never a navigable
    git worktree; W4 sweeps it by path (S-L3).
    **(2) redact the MOVED tree (VERIFIED, A-H6/S-L1/S-L2/CF-2):** first overwrite the EXACT
    `populated[]` destinations (from trusted `.meta`, pending+complete) with a
    `[redacted: populated allowlist secret]` marker (keep the filename, S-L2). Then **walk the
    moved retained tree directly** (`os.walk(followlinks=False)` over the plain dir — enumerates
    committed AND gitignored files that `status` never lists, closing the CF-2 leak) and scan each
    **regular no-follow** file (`O_NOFOLLOW`; a symlink raises ELOOP and is skipped, never
    followed) by name
    (`.env*, *.pem, *.key, *.p12, *.pfx, *.keystore, .npmrc, .netrc, .git-credentials,
    id_rsa*, id_ed25519*, authorized_keys, known_hosts, *service-account*.json, *credentials*`)
    and by content (PEM `-----BEGIN … PRIVATE KEY-----`, `AKIA[0-9A-Z]{16}`,
    `(?i)(secret|token|password|api[_-]?key)\s*[:=]\s*\S`), overwriting matched contents with the
    marker; **record every read/scan/write outcome, re-read + assert each redacted file, and
    surface `redaction_failures[]`** — any failure marks the record `redaction_incomplete` (owner-
    visible, never a silent "redacted").
    **(3) enforce the limit:** `select_evictions`; delete evicted retained dirs; update the
    persistent retention index (CF-6); if `pressure`, DO NOT evict a live one — `create` fails
    loud on the next call while pressure is unresolved (A-H2).
- `promote(...)`: see B.3.

### B.3 Promotion (AC-3) — orchestrator-only, independent, CAS-guarded

`promote(handle, *, target_ref, expected_target_sha, message, path_policy) -> PromotionResult`,
called by the ORCHESTRATOR process only. **Independence + dirty capture (A-H1):**

1. Build an **immutable candidate tree** capturing the WHOLE filesystem work product (dirty-tracked
   + untracked; no child commit needed — CF-3), not a two-commit diff: with
   `GIT_INDEX_FILE=<a temp file in .meta, NOT the worktree — probe gotcha>` and
   `--git-dir=<handle.gitdir> --work-tree=<path>`, `read-tree <base_sha>` then `add -A` (respects
   `.gitignore` so child-gitignored files are NOT carried into canonical; subject to `path_policy`:
   refuse if any changed path is outside the promotable allowlist/denylist) then `write-tree` →
   the candidate tree object.
2. `changed_paths` = `diff --name-only <base_sha> <candidate-tree>` (independent of the child's
   self-report, OQ-4); `head_sha` from the worktree HEAD via the trusted gitdir.
3. **Base-staleness guard (CF-1) — refuse a silent revert:** when `target_ref` already exists,
   require `base_sha == expected_target_sha` (the worktree was cut at the current target tip). If
   they differ, a peer advanced the target since the cut, and a candidate rooted at the stale
   `base_sha` would **silently revert** the peer's commits — so return
   `PromotionResult(promoted=False, reason="base stale — rebase")` and let the caller re-cut the
   worktree at the new tip (same caller-rebases model as the CAS-refuse path). No 3-way merge in W3.
4. **CAS the target:** the candidate commit ALWAYS parents on the real `base_sha`
   (`commit-tree <candidate> -p <base_sha> -m <message>`; omit `-p` only in the degenerate empty-repo
   case where `base_sha` is the all-zeros sentinel), then
   `git update-ref <target_ref> <new> <expected_target_sha>` — the atomic compare-and-swap.
   `expected_target_sha` is the CAS expectation ONLY (never a commit parent): a fresh ref uses the
   all-zeros sentinel (create semantics, S-L4), an existing ref uses its tip (== `base_sha` by the
   step-3 guard). If the ref moved after sampling, update-ref REFUSES →
   `PromotionResult(promoted=False, reason="target advanced")`.

**Structural orchestrator-only (A-H3):** for an OS-confined codex seat the child cannot write
canonical `.git` (Landlock) nor import `phase_executor.promote` (separate subprocess) — this is a
prerequisite gated on contract.py:260 (mutating-claude blocked), proven live by the W5 canary
(#468). A test asserts the child-facing dispatch surface never references `promote`.

The rich typed `work_product` object (documents, tests[], promotion status) is **OQ-4 / W6 #469**;
W3 returns the minimal `PromotionResult`.

### B.4 Scope

No tmux, no job registry, no cross-run reaper sweep (W4 #467 — it consumes W3's `finalize`/
`select_evictions`/retention-index); no mutating-dispatch wiring (W7 #470, gated on #468 + the #465
claude blocker). Default root chosen by the hooks caller (`~/.local/state/rawgentic/worktrees` — XDG
state, persistent, user-owned, outside /tmp); `resolve_root` enforces the outside-/tmp rule.

## Tests (AC-1..4)

- pure: resolve_root (accepts outside-/tmp abs; rejects `/tmp/x`, `$TMPDIR/x`, `/`, relative,
  symlink-to-/tmp); component_for collision-resistance; planned_path determinism + the
  canonical_contained_worktree boundary (comma/traversal rejected); decide_disposition matrix
  (clean only when obs-ok AND tree==base AND not dirty; retain on failed-obs / dirty /
  tree_differs — CF-2); select_evictions (evict oldest clean, never live/pinned, dirty age-bound,
  pressure when all protected); validate_allowlist (empty=nothing, `..`/escape rejected).
- integration (real tmp git repo, real git): create detached at base + records trusted gitdir+repo;
  create failure after `worktree add` compensates (no orphan checkout, CF-4); inspect via trusted
  gitdir sees dirty even when the child rewrites the worktree `.git` file (the A-H5 attack cell —
  child-swapped gitdir does NOT fool us); populate write-ahead records `pending` before copy and
  `complete` after (CF-7); finalize→clean leaves zero residue; finalize→retain moves + redacts an
  exact-populated `.env` (content gone, name+marker kept) + a scanned `*.pem` + **a gitignored
  child-created secret surfaced by the retained-tree walk** (CF-2, redacted or a redaction_failure,
  never a clean pass) + asserts redaction_failures empty on the happy path, and a chmod-unreadable
  file surfaces a redaction_failure + redaction_incomplete; a child-planted symlink in the retained
  tree is NOT followed (ELOOP-skipped); retention limit evicts oldest clean, never a live one,
  reports pressure + updates the persistent retention index (CF-6); prune --expire=now + list
  --porcelain assert; promote captures dirty+untracked work (ignores a lying self-report file),
  **refuses with "base stale — rebase" when base!=expected and does NOT drop a peer commit** (CF-1),
  parents the candidate on base_sha, creates a fresh ref with all-zeros expected, advances on
  match, refuses on CAS mismatch; the temp index lives in .meta (not swept into the candidate);
  child-cannot-promote structural assertion.
- Suite green vs baseline 3576/10.
