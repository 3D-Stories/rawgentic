# #793 — adversarial diff-review economics: design (rev 3, post Step-4 pass 2)

**Issue:** #793 (epic #756) · **Complexity:** standard_feature · **Lane:** elected · **Base:**
`9b37a11d` v3.115.1 · **Baseline:** 6520 passed / 21 skipped / exit 0 (carried — tree matches base)
· **Loop-backs:** design **2/2 (cap reached)**, global 2/3

Owner comment on the issue: **"do all that!"** — all five research-backed fixes are approved
scope. Spike S5 sequences them; it does not gate them.

**Rewritten wholesale at rev 3, not patched.** Pass 2's F3 found that my incremental rev-2 edits
had left three sections contradicting each other about where file paths come from. Editing in
place is what produced that, so this revision restates the whole design consistently.

## Revision history

- **rev 1** — five fixes; docs-only reused `count_impl_files == 0`; `docs/**/*.html` stripped by
  glob; the existing finding dedupe treated as the aggregation pass; first-fit packing.
- **rev 2** — after pass 1 (4 High): affirmative docs predicate; HTML stripped by same-stem `.md`
  pairing; fail-closed reducer; next-fit packing.
- **rev 3 (this)** — after pass 2 (3 High, all `clear`): `docs/` is **not** synonymous with
  documentation; **pairing is not provenance**; one canonical record stream replaces two
  contradictory path sources; the reducer covers every `CodexResult` field; **all accounting is
  bytes, not characters**.

## Spike S5 — measured, in BYTES

Command (flags pinned so it reproduces):
`git show --no-ext-diff --no-color --unified=3 --format= 0c1a5b14`

**Correction I own:** rev 1 and rev 2 reported **characters** labelled as bytes, and rev 2 then
explained the reviewer's differing figure as "their diff configuration". That explanation was
wrong and I asserted it without checking. Measured: **387,774 characters vs 390,078 bytes**,
delta 2,304 — exactly the reviewer's number. The cap is applied to **bytes** (`read_artifact`
reads `f.read(cap + 1)` on a binary handle, `adversarial_review_lib.py:513`,`:517`), so bytes are
the only correct unit here and everything below uses `len(text.encode("utf-8"))`.

| Measure | Value |
|---|---|
| Full patch | **390,078 bytes** · 3,914 lines · 38 files |
| Cap | `_MAX_BYTES_DEFAULT = 200_000` (`adversarial_review_lib.py:41`) |
| Ratio | **1.95× the cap** |
| Stripped, provenance-footer rule | **109,676 B — 28.1%** |
| Residual | **280,402 B — 71.9%, still 1.40× the cap** |

Stripped: `docs/planning/campaign-log.html` 109,218 B · `docs/assets/*.png` 458 B.
**Deliberately NOT stripped:** `docs/workflow-diagram.html`, 38,028 B — see Fix 2.

**Three conclusions:**

1. **Noise-stripping alone cannot fix the truncation.** That was the spike's question; the answer
   is no. Residual is still 1.40× the cap. **Chunking is required, not optional.**
2. **The issue's allowlist misses the dominant contributor** — `campaign-log.html` is 28% of the
   patch on its own and the issue never names rendered HTML.
3. **Truncation is OURS, not the provider's.** `read_artifact` (`:505-520`) cuts the artifact
   before the model sees it. The retry must key on our own sidecar flag; a provider
   `finish_reason` would never fire.

**One observation the issue does not raise.** `_MAX_BYTES_MAX = 5_000_000` (`:42`), so the 200 KB
default is tunable by two orders of magnitude and `docs/config-reference.md:822` records no
rationale. Raising it would end the truncation without any of this work — but it would not touch
the **economics**, which is the issue's actual subject. Recorded so the trade-off is visible.

## The canonical record stream (fixes pass-2 F3)

Every helper below consumes **one ordered stream of records**, not raw patch text plus a
separately-derived path list:

```
Record = (old_path, new_path, status, mode, chunk_bytes)
```

Produced alongside the patch itself: Step 11 builds the diff from **two** commands (high-risk
paths, then low-risk — `references/steps.md:1302-1303`), so the metadata is produced with
`git diff --raw -z -M` **for the same two partitions in the same order**, then zipped with the
`^diff --git` blocks. A length mismatch is a **loud failure**, never a silent re-pairing.

**`--raw`, not `--name-status` — I probed both.** `--name-status -z` yields only a status letter
and a path; it carries **no file mode**, so it cannot support the symlink/gitlink rejection the
docs predicate needs. `--raw -z` yields `:<old-mode> <new-mode> <old-sha> <new-sha> <status>` plus
the path(s). Probed in a throwaway repo: a symlink appears as `:000000 120000 … A  docs/link.md`,
a regular file as `:000000 100644 … A  docs/real.md`. `-M` makes rename records carry both paths.

**Why not parse paths out of the header.** I probed git rather than assuming:
```
diff --git a/docs/has space.md b/docs/has space.md          <- space: NOT quoted
diff --git a/docs/qu'ote.md b/docs/qu'ote.md                <- apostrophe: NOT quoted
diff --git "a/docs/uni-caf\303\251.md" "b/docs/uni-caf\303\251.md"   <- non-ASCII: QUOTED + octal-escaped
```
A `^diff --git a/(\S+) b/(\S+)` regex captures `a/docs/has` for the first and keeps the quotes on
the third. `-z` output is unambiguous; header text is not.

**Why positional pairing against `changed_paths` is NOT enough** (pass-2 F3): `changed_paths` is
in git's own order while the patch is high-risk-first, so an equal-length reorder would pass a
count check while attributing blocks to the wrong paths — and could strip a product block using
another block's generated classification. Partition-aligned `--raw -z -M` removes that.

*(The spike numbers above are unaffected: all 38 files in the #762 diff have plain ASCII paths
with no spaces. Checked, not assumed.)*

## What ships — all five fixes

All inside `hooks/adversarial_review_lib.py`. No new module: this is that module's own concern.

### Fix 2 — strip generated content (AC1)

`strip_generated(records) -> (text, dropped)`, pure. Drops a record only when **both** endpoints
classify generated (so a product↔generated rename cannot hide content), replacing it with a
one-line marker naming the file — a silent drop would let a real change hide. Marker bytes count
toward the packing budget.

| Rule | Basis |
|---|---|
| `docs/**/*.html` **carrying the renderer provenance footer** | positively identifies `render_artifact.py` output |
| `docs/assets/**` | binary snapshots |
| `**/stubbed-baseline.json`, `uv.lock`, `package-lock.json`, `*.lock` | generated / lockfiles |

**rev 3: provenance, not pairing.** Rev 2 stripped a docs HTML file when a same-stem `.md`
existed. Pass-2 F2 falsified that with an in-repo counterexample and it is decisive:
`docs/workflow-diagram.html` **is** paired with `docs/workflow-diagram.md`, yet it is
**hand-maintained** — the REV recipe has maintainers edit the HTML's `revs`/`versions` data
directly (repo `CLAUDE.md` §5, "A diagram REV") and it carries executable JavaScript. Rev 2 would
have stripped a hand-authored executable file from a security review.

The replacement is the renderer's own footer sentinel, emitted at `hooks/render_artifact.py:600`:
`generated by hooks/render_artifact.py — self-contained, no external resources.`
Measured across the repo: **119 docs HTML, 91 carry it, 28 do not.** The three decisive cases:

| File | Footer | Outcome |
|---|---|---|
| `docs/planning/campaign-log.html` | yes | stripped — real renderer output, the 28% win |
| `docs/workflow-diagram.html` | **no** | **kept** — hand-maintained, executable |
| `docs/planning/2026-07-24-635-epic-uat-console/index.html` | **no** | **kept** — hand-authored app |

`skills/**/*.md` is never stripped: markdown skills are the product here
(`laneImplExtensions: [".md"]`).

### Fix 3 — docs-only diffs skip the layer (AC2)

Two wrong attempts preceded this one, and both were security holes, so the rule is stated
narrowly:

- rev 1 used `count_impl_files == 0`. That returns **0** for a TEST-ONLY diff (verified:
  `tests/hooks/test_security_scan.py` → 0), so a security-test-only change would have been skipped.
- rev 2 accepted **any** path under `docs/`. Pass-2 F1 falsified that: `docs/` contains executable
  code — e.g. `docs/planning/2026-07-24-635-epic-uat-console/pane-env-allowlist-check.py`, a real
  `.py` file whose directory also holds `three.module.js`.

`docs_only(records)` is **affirmative, extension-gated and mode-gated**. True only when the record
list is non-empty AND every record satisfies all of:
- path is under `docs/`, or is one of `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`,
  `SECURITY.md`, `LICENSE[.md]`;
- extension is in `{.md, .txt, .rst}` — **or** `.html` that passes the Fix-2 provenance test;
- the **new-side git mode** is a regular file — `100644` only; **`100755` (executable), `120000`
  (symlink) and `160000` (gitlink) are all rejected**. This is why the predicate consumes records
  rather than names, and why the metadata must come from `--raw` (which carries modes) rather than
  `--name-status` (which does not);
- path contains no control characters.

Everything else — `.py`/`.js`/`.sh` anywhere including under `docs/`, tests, locks, minified
assets, `skills/**`, `.github/**`, absolute or traversing paths, and any unknown category — stays
review-eligible.

Run against **falsifying inputs first** — 11 must-be-false cases all pass: test-only (incl. a
security test), lockfile-only, minified asset, `skills/**/*.md`, `hooks/*.py`, mixed docs+code,
empty list, `docs/../hooks/x.py`, absolute path, unknown root file, `.github/workflows/ci.yml`.
Plus the rev-3 additions: `docs/**/*.py`, a symlink record, a gitlink record.

The skip is **visible**: the Step-11 marker records `skipped (docs-only)`. Never silent.

### Fixes 1 + 5 — per-file patches, packed, one aggregation pass (AC4)

- `pack_chunks(records, cap_bytes) -> [[record, ...]]`, pure — **contiguous next-fit** in
  **bytes**, counting each chunk plus its separator and any replacement-marker bytes.
  Not first-fit: sizes 120, 90, 80 at cap 200 give first-fit `[120,80],[90]`, reordering the
  flattened sequence. Order is a security invariant — Step 11 builds the patch high-risk-first so
  a truncation cuts only the low-risk tail. Next-fit closes the current pack rather than
  back-filling, so **flattening reproduces the input sequence exactly** — asserted by a test.
- **A single record exceeding the cap** is sent alone and truncated as today, with `truncated`
  set. An honest floor.
- **Multibyte safety:** a fixture under 200,000 characters but over 200,000 bytes must pack as
  over-cap. That test is the guard against the char/byte error this design already made once.

**The aggregation is a fail-closed reducer** — not the existing dedupe. `normalize_findings`
(`:996`) only validates, exact-dedupes and sorts finding **dicts**; it cannot reduce the per-pack
fields on `CodexResult` (`:1031`) and the sidecar (`:2496`), so a failed middle pack could
otherwise surface as an exit-0 `no_findings`. Every field gets a rule (pass-2 F4):

| Field | Reduction |
|---|---|
| `status` | **every pack must succeed**; any failure fails the backend |
| `truncated` | `any(pack.truncated)` |
| `secrets` | stable union of categories |
| `findings` | concatenate, then `normalize_findings` once |
| `summary` | deterministic join in pack order, each line prefixed with its pack index |
| `raw_error` | the **first** failing pack's error, prefixed with its index — never dropped |
| `model` / `effort` / `backend` | must be identical across packs; a mismatch is an error, never a silent default |

Only the complete aggregate is reported, atomically. A partial result is an error, never a pass.

### Fix 4 — truncation is a bounded retry (AC3)

Today a truncated artifact is reviewed anyway and reported `truncated: true`. New: a pack that
truncates is retried **once** with that pack split finer, bounded by the module's existing
`_MAX_RETRIES_DEFAULT = 1` (`:49`). Keyed on our own `truncated` flag.

### AC5 — measured before/after

`docs/measurements/2026-08-01-793-diff-review-economics.md` carries the byte-accurate table above
plus the post-change re-measurement; the PR body reproduces it.

## Cost honesty

Stripping removes 28.1% of the bytes — a real token saving. Chunking does **not** reduce total
tokens; it trades one over-cap call for N under-cap calls and adds one prompt preamble per pack.
On the #762 patch that is 2 packs after stripping (280 KB / 200 KB), so the overhead is one extra
preamble. The win is that the review **completes** instead of spending 8–10 minutes and failing.
Calling chunking a token saving would be false; it is a completion fix.

## File changes

| File | Change |
|---|---|
| `hooks/adversarial_review_lib.py` | record stream, `strip_generated`, `docs_only`, `pack_chunks`, the reducer, retry |
| `tests/hooks/test_adversarial_review_codex.py` | unit + subprocess CLI tests per helper and the reducer |
| `skills/implement-feature/references/steps.md` | Step-11: produce partition-aligned `--raw -z -M`; the `skipped (docs-only)` marker state |
| `docs/config-reference.md` | the strip allowlist and the provenance sentinel |
| `docs/measurements/2026-08-01-793-diff-review-economics.md` | NEW — before/after |
| `README.md` | changelog entry |
| `.claude-plugin/plugin.json`, `plugins/rawgentic/.codex-plugin/plugin.json`, `phase_executor/src/phase_executor/canary.py`, `tests/hooks/test_adversarial_review_registration.py` | version → 3.116.0 |

## Failure modes

- **Stripping hides a real change** — every dropped file gets a named marker line; a test asserts
  one marker per drop.
- **Stripping a hand-authored file** — the provenance sentinel is a positive test; regressions pin
  `workflow-diagram.html` and the exec-console app as KEPT.
- **A rename hiding content** across the product/generated boundary — both endpoints must classify
  generated.
- **Blocks attributed to the wrong paths** — partition-aligned `--raw -z -M`, with a
  length-mismatch hard failure.
- **Docs-only skipping a review that was needed** — two earlier versions did exactly that; the
  predicate is now extension- and mode-gated and run against 14 falsifying inputs.
- **A partial multi-pack result reported as a pass** — the reducer fails closed on every field.
- **Packing reordering the high-risk-first sequence** — contiguous next-fit, with a
  flatten-equals-input assertion.
- **Character/byte confusion** — this design made that error once; all accounting is
  `len(text.encode("utf-8"))` and a multibyte fixture guards it.

## Security implications

The helpers are pure transforms over a patch this repo produced from its own git history, plus
`git diff --raw -z -M` metadata (fixed argv, no shell, same runner convention as the existing
diff commands). No new file writes beyond the per-file patch artifacts, which follow the existing
Step-11 convention (`.rawgentic-diff-review-<issue>-<token>.patch`, mode `0600`, git-excluded,
stale-swept). The existing `scan_for_secrets` pass runs on **each pack** unconditionally — a
per-pack split must not let content bypass it, and a test asserts the scan call count equals the
pack count. No network beyond the review call that already exists.

## Platform / external dependencies

platform_apis:
- api: git diff --raw -z -M via subprocess.run with a fixed argv list, on the local git CLI
  feasibility: verified via spike — probed live: modes present as :000000 120000 for a symlink and :000000 100644 for a regular file
  failure: fail-loud

A non-zero `git` exit, or a record count that does not match the patch blocks, is a hard failure
of the diff-review layer — never a silent fallback to unattributed stripping.

Everything else is stdlib (`re`, `posixpath`) plus existing in-repo call sites: `read_artifact:505`,
`normalize_findings:996`, `scan_for_secrets`, `CodexResult:1031`. The Codex CLI invocation is
unchanged — this design alters what is passed to it, never how it is invoked.

## Verification

- **AC1** — strip a fixture containing each allowlist category; assert byte deltas and one marker
  per drop; regressions asserting `workflow-diagram.html`, the exec-console app, and
  `skills/**/*.md` are never stripped; a rename across the boundary is not stripped.
- **AC2** — all 14 falsifying + 4 confirming classifier cases parametrised (the falsifying ones
  are the point), incl. `docs/**/*.py`, symlink and gitlink records; plus a Step-11 prose drift
  guard for the `skipped (docs-only)` marker.
- **AC3** — a truncating pack retries exactly once; a second truncation does not loop.
- **AC4** — flatten-equals-input order over next-fit; byte accounting incl. markers; a multibyte
  under-chars/over-bytes fixture; a single over-cap record sent alone; middle-pack failure fails
  the backend; mixed truncation ORs; first-pack secrets appear in the union; `model`/`effort`/
  `backend` mismatch is an error.
- **AC5** — the measurement doc, regenerated post-change and reproduced in the PR body.
