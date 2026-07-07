# Rawgentic — Unified Harness + Code Review

**Date:** 2026-07-07 · **Reviewer:** Fable 5, multi-agent (13 harness agents + 29 code-review agents) · **Mode:** read-only, findings-only.

**Method.** Two multi-agent passes. (1) Harness survey — 5 layer readers inferred the system's goal and flagged what fights it; 8 load-bearing claims re-measured by independent refuters. (2) Code review — 7 dimension reviewers over `projects/rawgentic`, deduped, then one independent second-reviewer per finding that re-read the cited code and could reject. **28 findings total: 6 harness + 22 code (all 22 survived confirmation; several severity-adjusted down; 0 rejected).** No Critical, no High in the code layer; the High items are all in the harness/config layer.

**The goal this all serves** (confirmed across 5 independent layer reads): a personal, multi-project, high-autonomy software factory — issues in, merged verified PRs out, attention pulled to the ends (specify, authorize), the middle autonomous but measurably rigorous; self-productizing (the process is the shippable plugin). The findings below are where the system fights that goal.

---

## Severity summary

| | Critical | High | Medium | Low |
|---|---|---|---|---|
| Harness (config/instruction layer) | 0 | 4 | 2 | 0 |
| Code (`projects/rawgentic`) | 0 | 0 | 7 | 15 |

---

## Part 1 — Harness findings (H-A … H-F)

Full evidence in the prior artifact (all independently re-measured). Recap:

- **H-A · High — Permission allowlist pre-authorizes the classes the safety model relies on.** `~/.claude/settings.json` allows unprompted `python3 -c ":*"` (arbitrary code), `gh api:*` (arbitrary GitHub mutation), and `GH_TOKEN=… git:*` (incl. `push origin main --force`) — while the instructions defer safety to the permission gate and CLAUDE.md says "never push to main." *Effort S.*
- **H-B · High — Four always-on, contradictory output/autonomy contracts.** operating-instructions (rich status) vs caveman (no narration) vs ponytail (3 lines) vs learning-output-style (educational prose + "stop and have the human write code"). Resolved arbitrarily each turn; the loser is trained as ignorable. *Effort M · decision.*
- **H-C · High — Competing process ownership.** superpowers ("MUST invoke a skill before ANY response" + brainstorming HARD-GATE) and reflexion (threat-based judge "you will be killed… value = what you REJECT") claim process rawgentic owns and train the over-reporting the global instructions fight. *Effort M · decision.*
- **H-D · High — Memory-stack layering; authority inverted.** Five systems; the declared-authoritative store (mempalace) is quietest at bootstrap while "legacy" MEMORY.md is loudest and still written despite a standing "do not write" hook; the WAL recovery report re-announces 183 incomplete ops (oldest March) every session with no expiry. ~93 KB injected before the first prompt. *Effort M.*
- **H-E · Medium — principles.md staleness.** (Merged with code C18/C19 below — same root.)
- **H-F · Medium — No janitor.** 684 dead permission rules (54 on a path that no longer exists), 2.6 MB stray root files incl. a byte-identical duplicate operating-instructions.md, 5.7 MB WAL incl. orphans, dead-weight plugins. *Effort M.* (Merged with the code dead-code cluster below.)

---

## Part 2 — Code review findings (C1 … C22), confirmed

### Correctness

- **C1 · Medium/S — `render_summary()` crashes on a non-dict record**, breaking its documented "never raises" contract. `hooks/work_summary.py:578` reads the raw param (`record.get(...)`) instead of the coerced `r = _as_dict(record)` used everywhere else; a top-level JSON array/string/number record makes the best-effort Step-16 summary throw, defeating the whole reason render_summary is separate from validation. *Fix: `r.get("verification_deferred")`; add a test for `render_summary([])`/`"x"`/`42`.*

### Robustness / error-path

- **C20 · Medium/S — wal-guard headless block-audit is dead.** `hooks/wal-guard:40-50` writes a `GUARD_BLOCK` audit line only if `WAL_FILE` is set, but wal-guard never calls `wal_init_file` (the only assigner). In headless — "where guards are the last defense, blocks MUST be auditable" — the deny still fires but is never recorded. *Fix: call `wal_init_file` after `wal_resolve_project`; test the audit line.*
- **C21 · Medium/M (latent — currently unreachable) — Divergent `claudeDocsPath` resolution would silently disable the cross-project guard.** `hooks/wal-lib.sh:76-95` trusts an absolute claudeDocsPath outside `$HOME`; siblings `wal-stop:38-45` and `wal-bind-guard:68-75` reject it and fall back. Result: WAL/registry writes could land in a different dir than the guard reads, skipping the Gate-2 cross-project write check. **Verified 2026-07-07: no project (and not the workspace) sets `claudeDocsPath` at all, so the divergent branch is never taken today — this is a latent inconsistency, not a live hole.** *Fix: apply the under-HOME containment check in all three resolvers (or none) before anyone sets an absolute path.*
- **C22 · Low/S — Project name not path-traversal-validated in the shared paths.** `wal-stop:67-72` validates `*/*|*\\*|..*`; `wal-lib.sh:114` (`wal_init_file`), `wal-context:64,88`, and `security-guard.py:176` build file paths from the same registry-derived name with no guard. *Fix: one validation in `wal_resolve_project` covers all bash callers.*

### Duplication / simplification

- **C6 · Medium/M — Atomic write reimplemented in 6+ hooks; plan_lib uses a weaker variant.** `notes-size-handler.py:116`, `scanner_bootstrap.py:64`, `post_update_reconcile.py:248` & `:276`, `registry_prune.py:63` repeat mkstemp→os.replace; `plan_lib.py:1893` uses a fixed-name temp with no unlink-on-exception, violating the repo's own §5 hook checklist. (Confirmer: `adversarial_review_lib.py:1431` is NOT mkstemp — drop that one site.) *Fix: one `atomic_write` helper; route all sites incl. plan_lib.*
- **C7 · Medium/M — wal-stop / wal-suspend reinvent wal-lib.sh resolution and have diverged.** They don't `source wal-lib.sh`; the inline copies carry the C21 divergence. *Fix: source the lib, call `wal_find_workspace`/`wal_resolve_claude_docs`/`wal_resolve_project`; removes ~30 dup lines each and collapses C21.*
- **C5 · Low/M — quality-bar.md is a hand-synced byte-identical triple** outside the shared-block mechanism (`skills/{fix-bug,implement-feature,setup}/references/`, md5 `23a78567…`). Not in `sync_shared_blocks.py` MANIFEST. (Confirmer: an existing feasibility test catches full divergence, so Low not Medium.) *Fix: single-source it via the shared-block mechanism (needs whole-file support).*

### Performance (hot-path hooks, fire on every tool call)

- **C8 · Medium/S — WAL stdin JSON re-parsed with 4 separate jq spawns** (`wal-lib.sh:29-34`), on every mutation tool call across wal-pre/post/post-fail/guard/context. Measured ~124 ms/Bash call. *Fix: one jq emitting all fields; reuse `$WAL_RAW_INPUT`.*
- **C9 · Low/S — wal-guard spawns one grep per pattern (up to 12)** per Bash call in strict/standard mode, and as the default when no workspace resolves. *Fix: one combined `grep -iE`.*
- **C10 · Low/M — wal-bind-guard fires on every Read** (~52-63 ms), doing full config work only to allow in the common bound-project case. *Fix: cheap string-prefix fast-path; consolidate the 3-jq parse.*
- **C11 · Low/S — Workspace up-tree search forks a subshell per parent level** (`wal-lib.sh:58`, also `wal-bind-guard`). (Confirmer: the security-guard.py citation is wrong — pure-Python, no fork.) *Fix: pure-bash `dir=${dir%/*}`.*
- **C12 · Low/S — session-start spawns python3 once per notes file** on every startup/compact (`session-start:440`). *Fix: one python invocation looping internally, or a `wc -l` gate.*
- **C13 · Low/S — session-start shells to `query-archive.py`, removed in #57** (`session-start:464`) — dead spawn path. *Fix: delete the `_do_archive_context` block.*

### Test health

- **C14 · Low/S — "9/15 skills have evals.json" is an uncomputed substring pin and already wrong** (`test_adversarial_review_registration.py:61`; `sync-security-patterns/evals/evals.json` exists at 2991 B, uncounted). The guard asserts a literal string, never enumerates disk. *Fix: compute the count from disk, assert README renders the same number.*
- **C15 · Low/S — Headless annotation guard counts the substring `[Headless` over the whole corpus** (`test_headless.py:1485`) — any incidental occurrence breaks it; forces manual bumps. *Fix: anchored regex `\[Headless:`, or a `>=` floor + structural check.*
- **C16 · Low/S — security-guard-check.sh write-failure branch has no test.** Every other branch is covered; the "fail-closed on the nag when the decision file can't be written" path isn't. *Fix: chmod the parent unwritable, assert empty stdout + no file.*
- **C17 · Low/S — `test_no_issue_template_carries_auto_label` passes vacuously** when `.github/ISSUE_TEMPLATE` is absent (current state) — early-returns with zero assertions, guarding a real approval-gate security property that is currently un-exercised. *Fix: `pytest.skip` so the vacuity is visible.*

### Doc↔code drift (merges harness H-E)

- **C18 · Medium/M — README presents `docs/principles.md` as the plugin's "enforcement mechanisms," but it's a March planning artifact for a different project** describing hooks that were never built, stated in the present tense as current fact (P1–P14; only P15 is enforced). *Fix: banner it as a superseded planning artifact, correct the four "existing hook already enforces" sentences, single-source principle statements.*
- **C19 · Low/S — README P2 says "Black for Python," principles.md says "Ruff"; neither (nor Prettier) is wired anywhere.** *Fix: drop the tool names from README P2 (the real style gate is pylint E-only) or reconcile + mark not-implemented.*

### Dead code (merges harness H-F)

- **C2 · Low/S — `hooks/external_ref_lib.py` (262 lines) has zero production consumers.** README:796 flags the intended consumer (#196 code-review gate) as never landed. *Fix: land the gate wiring (closes the #162 silent-pass gap) OR delete module + 16 tests + docs.* **[decision]**
- **C3 · Low/S — `headless_interaction.py:144 parse_metadata()` is dead** — the metadata block is emitted into every headless comment but nothing reads it back (resume uses the suspend-state file). *Fix: wire a `parse-metadata` subcommand into resume, or drop it + the pattern.*
- **C4 · Low/S — `scripts/verify_158_split.py` — self-described throwaway** ("delete after #158 merges"); #158 merged, not runnable-correct at HEAD. *Fix: delete.*

---

## Part 3 — Proposed work breakdown (epics + children)

Nothing filed — proposals awaiting "go." Children sized to one WF2/WF3 run. `[decision]` = needs an owner call, not just mechanical work. Dependency edges noted where two children touch the same file.

### EPIC 1 — Harness safety & instruction coherence
*Config/instruction layer. The only latent-safety epic; highest leverage.*
- **1a · High/S — Tighten the permission allowlist.** Remove the arbitrary-execution catch-alls (`python3 -c`, `gh api:*`, token-prefixed `git:*`), keep scoped entries. `AC:` no allow rule matches an unprompted `push --force` or arbitrary code; the deny path still prompts. *(H-A)*
- **1b · High/M — Resolve the output-style contract conflict `[decision]`.** Pick one voice owner; scope or remove learning-output-style. `AC:` at most one always-on output contract; no session carries mutually-contradictory narration rules. *(H-B)*
- **1c · High/M — Scope competing process-owner plugins out of rawgentic sessions `[decision]`.** superpowers mandate + reflexion judge. `AC:` a rawgentic-driven session does not receive a competing "must brainstorm/must invoke skill/reject-maximizing judge" instruction. *(H-C)*

### EPIC 2 — Memory-stack consolidation *(H-D)*
- **2a · High/S — Expire the WAL recovery report.** Add an age cutoff / ack so stale INTENTs stop re-announcing. `AC:` a fresh crash INTENT shows; a March one does not. *(H-D; also kills the desensitization behind C13's neighborhood.)*
- **2b · High/M — Collapse memory authority `[decision]`.** End the auto-memory write contradiction; make bootstrap match the declared source of truth. `AC:` one store is authoritative at bootstrap and no standing instruction is routinely violated.
- **2c · Med/S — Tune mempalace recall** (raise similarity threshold, scope to bound project). `AC:` per-prompt recall excludes cross-project hits below threshold.
- **2d · Med/S — Degraded path for the PreCompact fail-closed block.** Today a mempalace outage blocks compaction entirely — a long autonomous run wedges at the context wall. `AC:` on save failure, fall back to a local transcript save then approve; block only when both fail. *(H-D detail: the miscalibrated deliberate trade-off.)*

### EPIC 3 — Hook correctness & error-path integrity
- **3a · Med/S — Fix `render_summary()` non-dict crash.** *(C1, QA re-verified at :578)* `AC:` render_summary returns a str for list/str/int input; red-before-green test.
- **3b · Med/M — Unify claude_docs resolution; fix absolute-outside-HOME divergence.** *(C7 + C21)* `AC:` all three resolvers agree; a test pins identical resolution. **← 3d, 4a depend on this (shared wal-lib.sh).**
- **3c · Med/S — Init `WAL_FILE` in wal-guard so headless block audit records.** *(C20, QA re-verified: WAL_FILE referenced :40/:49, `wal_init_file` never called)* `AC:` a headless wal-guard deny appends a GUARD_BLOCK line; test covers it.
- **3d · Low/S — Add project-name path-traversal validation to the shared resolver.** *(C22)* `depends_on 3b.` `AC:` a registry entry with `../` in the project name is rejected by every path-building hook.
- **3e · Med/M — Extract one `atomic_write` helper; route all sites.** *(C6)* QA re-enumeration: true mkstemp sites are notes-size-handler:116, registry_prune:63, post_update_reconcile:248 + :276, scanner_bootstrap:64; plan_lib:1897 is the weaker fixed-name variant; adversarial_review_lib:1431-1447 has its own non-mkstemp tmp+replace pattern. `AC:` one helper; all seven sites route through it; plan_lib gains unlink-on-exception.

### EPIC 4 — Hot-path hook performance
- **4a · Med/S — Consolidate WAL stdin parse to one jq + reuse read input.** *(C8 + C11)* `depends_on 3b.`
- **4b · Low/S — wal-guard: one combined grep instead of 12.** *(C9)*
- **4c · Low/M — wal-bind-guard Read fast-path for the bound project.** *(C10)*
- **4d · Low/S — session-start: batch the notes-size spawn + delete the dead query-archive.py path — and consolidate the startup python3 spawns.** QA pass verified ~7 more per-event python3 spawns in session-start:540-800 (scanner bootstrap, two reconcile invocations, four inline `python3 -c` workspace parses) beyond the per-notes-file loop. `AC:` startup/resume spawns python3 a bounded number of times independent of project count; the query-archive block is gone. *(C12 + C13 + QA-verified extension.)*

### EPIC 5 — Docs & test-guard integrity
- **5a · Med/M — Quarantine principles.md; reconcile README P-table; single-source principle statements.** *(C18 + C19 + H-E)*
- **5b · Low/M — Replace uncomputed count/annotation guards with computed assertions.** *(C14 + C15)*
- **5c · Low/S — Fix the vacuous/untested guards** (template test skip + security-guard-check write-fail case). *(C16 + C17)* `AC:` the template test skips visibly when the dir is absent; a write-fail case asserts empty stdout + no decision file.
- **5d · Low/S — Fix the review-discovered self-rot in the two operating manuals.** Workspace CLAUDE.md says a skill is "FOUR surfaces" while add-skill says seven (reconcile to "see add-skill"); repo CLAUDE.md §3 still cites "two live examples of doc rot" that PR #259 already fixed; both manuals carry unguarded volatile pins (suite/skill counts). `AC:` no contradiction between the manuals and their skills; §3 examples updated or generalized; volatile pins either version-anchored-and-dated or replaced with "read the test". *(Harness fingerprints section.)*

### EPIC 6 — Dead-code & cleanup *(H-F)*
- **6a · Low/M — Wire-or-delete `external_ref_lib` `[decision]`.** Land the #162 code-review-gate consumer or remove module+tests+docs. *(C2)*
- **6b · Low/S — Delete verify_158_split.py; resolve parse_metadata (wire or drop).** *(C3 + C4)*
- **6c · Low/M — Move quality-bar.md into the shared-block single-source.** *(C5)*
- **6d · Med/M — Janitor pass:** prune settings.local.json dead rules (684, incl. 54 on the dead claude-personal path), workspace-root stray files + the duplicate operating-instructions.md, orphan WALs. `AC:` settings.local.json only carries wildcard-capable rules; no orphan WAL for an unregistered project; workspace root has no project-specific artifacts. *(H-F)*
- **6e · Low/S — Prune dead-weight + duplicated plugins.** Disable/remove zero-use plugins (astronomer-data-agents 20 skills/0 uses, microsoft-docs, exa, code-review — fifth competing review owner) and collapse Context7 to ONE connector (currently loaded 4×, one known quota-dead). `AC:` each remaining enabled plugin has either usage or a stated reason; Context7 instructions appear once per session. *(H-F ecosystem detail; distinct from 1b/1c which handle the CONFLICTING plugins.)*

**Suggested sequencing:** EPIC 1 first (safety). EPIC 3 before EPIC 4 (3b unblocks 4a). The four `[decision]` children (1b, 1c, 2b, 6a) want an owner call before implementation; everything else is mechanical. **25 children total after the QA pass added 2d, 5d, 6e** (gaps: PreCompact degraded path, the manuals' self-rot, and plugin/Context7 pruning had landed in no child).

---

## QA pass (2026-07-07, post-publication)

Cross-map verified complete both directions (every finding → ≥1 child; every child → real findings). Spot re-verified first-hand: C1 (:578 raw `record.get` amid `r.get` siblings), C13 (file absent), C14 (sync-security-patterns/evals/evals.json exists, 2991 B), C20 (`wal_init_file` never called in wal-guard), C6 (site list re-enumerated — see 3e). Closed coverage gaps: **full suite run — 2278 passed, 0 failed** (the correctness lane had not run it); session-start:540-800 swept — ~7 additional per-event python3 spawns found, folded into 4d. Gaps found in the breakdown itself and fixed: 2d, 5d, 6e added.

---

## Part 4 — Discard log

**Code review: 0 findings rejected** by the confirmation pass (22/22 survived; severity adjusted down on C2, C3, C5, C9, C10, C11, C18, C19, C20, C21). Reviewer-investigated-but-not-filed non-defects (safe-direction, judged correct): `plan_lib.check_ratio_band` decompose/halt ordering; `driver_lib._NEG_BEFORE_RE` over-capturing in the safe direction; `plan_lib` unanchored extra_patterns (over-promotes = safe); `post_update_reconcile` version-tuple double-nudge (cosmetic, already commented).

**Harness: 1 of 8 partial** — the dead permission-rule count is **684, not ~643**; everything else confirmed, including one confirmed-with-irony (the repo CLAUDE.md §3 "two live examples of doc rot" were both fixed by PR #259 in the same commit that wrote the section citing them — a live drift example became stale on merge).

---

## Part 5 — Coverage (what was NOT swept)

- **Correctness:** did not execute the live Codex subprocess path (test-stubbed) or the git-subprocess paths in plan_lib/security_scan against a real repo; did not run the full pytest suite.
- **Dead code:** did not verify skill-presence drift (marketplace vs disk — count guards already pin it); did not trace transitive dead code beyond external_ref_lib/parse_metadata.
- **Simplification:** did not deep-read every Python hook's internals for further single-caller abstractions; did not audit skill-prose duplication beyond quality-bar.
- **Performance:** did not read session-start lines ~540-800 (headless/scanner-hash/setup-nudge) for further spawns; did not profile the Stop hook or security-guard-check.sh startup cost.
- **Test health:** did not deep-read the large lib-behavior test files (test_plan_lib ~82k, test_work_summary ~80k, test_security_scan ~59k).
- **Doc drift:** did not diff every SKILL.md body against code; did not check consolidation.md / multi-issue-driver.md exhaustively.
- **Error-path:** did not audit WAL write atomicity under concurrent `>>` appends from multiple hooks (registry_prune documents a known non-atomic-append ceiling; not chased across wal-pre/post).

**The one claim most worth re-checking:** C21 was it — and I checked it. No project sets `claudeDocsPath`, so the divergent branch is dead today (latent, not live). Next most worth re-checking: **C6's site list** — the confirmer already corrected one cited atomic-write site (`adversarial_review_lib.py:1431` is not mkstemp), so re-enumerate the exact sites before the 3e refactor rather than trusting the finding's list.
