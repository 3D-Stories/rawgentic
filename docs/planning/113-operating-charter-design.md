# Design — #113: opt-in operating-instructions charter via `@import`

**Issue:** [#113](https://github.com/3D-Stories/rawgentic/issues/113) · **WF2 run** (epic #247) · **Complexity:** standard_feature · **Version bump:** minor (feat) 3.18.0 → 3.19.0

## Problem

Offer an **opt-in** way to install a personal "operating instructions" charter that
attaches to a `CLAUDE.md` via a one-line `@import` + an external versioned file. It must
**never** be a default setup step and **never** silently mutate the user's global
`~/.claude/CLAUDE.md`.

## Owner-fork decision (D1, logged in epic-247-autorun-log.md)

- Ship **both** the mechanism **and** a bundled charter (not mechanism-only).
- The bundled charter is **rawgentic-authored and autonomy-safe** — quality / verification /
  honesty discipline **only**. It is NOT a verbatim copy of the maintainer's personal
  charter, which contains autonomy-gating language ("confirm first…") that the issue's
  **Critical safety invariant** forbids.
- **Default scope = project** `CLAUDE.md` (Layer 3). Global (`~/.claude`, Layer 1) is
  offered but never the default — honors "never default-global".

## Approach

A new **opt-in command** `/rawgentic:install-operating-charter` (auto-registered from
`skills/install-operating-charter/SKILL.md`) that:
1. Resolves the target scope — arg or prompt `{project | global | skip}`; **never runs by
   default**, **never silently** writes global.
2. Validates the bundled charter is autonomy-safe (`assert_charter_safe`) **before** writing.
3. Copies the charter next to the chosen `CLAUDE.md` (idempotent, no-clobber of user edits).
4. Injects a single `@operating-instructions.md` import line under a
   `## Operating Instructions` heading (idempotent — never duplicates, never clobbers).

The mutation + safety logic lives in a **tested helper** `hooks/charter_lib.py`, so the
behavior is unit-tested and the drift guard is reusable (matches rawgentic's
one-tested-source ethos). The SKILL.md is a thin orchestrator over the helper.

### `@import` mechanism — confirmed

The bare relative form `@operating-instructions.md` on its own line inside a `CLAUDE.md`
is Claude Code's import syntax — **confirmed from live ground truth**: this session's
`~/.claude/CLAUDE.md` uses exactly `@operating-instructions.md` and the imported content
loads. Placing the charter file next to the target `CLAUDE.md` makes the same relative
line resolve for both global (`~/.claude/…`) and project (`<path>/…`) scopes.

## File plan

| File | Change | Kind |
|---|---|---|
| `hooks/charter_lib.py` | NEW — `CHARTER_FILENAME`, `import_line`, `charter_block`, `inject_import`, `GATING_PATTERNS`, `find_gating_language`, `assert_charter_safe`, `bundled_charter_path` | impl |
| `skills/install-operating-charter/SKILL.md` | NEW — opt-in command orchestrator | skill (.md) |
| `skills/install-operating-charter/assets/operating-instructions.md` | NEW — autonomy-safe charter content | asset (.md) |
| `.claude-plugin/marketplace.json` | add `"./skills/install-operating-charter"` to `skills[]` | config |
| `plugins/rawgentic/skills/install-operating-charter` | NEW symlink → `../../../skills/install-operating-charter` (codex mirror; guarded by `test_codex_manifest_exposes_same_runtime_skills_as_claude_marketplace`) | symlink |
| `.claude-plugin/plugin.json`, `plugins/rawgentic/.codex-plugin/plugin.json` | version 3.18.0 → 3.19.0 | config |
| `tests/hooks/test_charter_lib.py` | NEW — drift guard + idempotency + no-clobber + non-vacuity | test |
| `tests/hooks/test_adversarial_review_registration.py` (`test_plugin_version_bumped`) | 3.18.0 → 3.19.0 | test |
| `README.md` | changelog + command doc | docs (.md) |

No workflow-diagram REV: this adds a standalone command; it does not change the WF2 spine.

## Safety / failure modes

- **Critical invariant:** the shipped charter must contain **no** autonomy-gating language.
  Enforced by `assert_charter_safe` (grep for `stop for a yes`, `commit and push only when
  asked`, `wait for explicit confirmation`, `a hold persists`, case-insensitive) called
  BOTH by the command (pre-write, fail-closed) and by the drift-guard test on the shipped
  charter. Non-vacuity: the test also asserts each pattern is detected in a synthetic
  gating charter.
- **No silent global mutation:** default scope is project; global requires explicit choice.
- **Idempotent:** re-running never duplicates the import line; `inject_import` returns
  `changed=False` when `@<filename>` is already present.
- **No-clobber:** if the target charter file already exists, the command does not overwrite
  user edits silently — it reports and offers an upgrade only on explicit confirmation.

## Test plan (TDD)

`tests/hooks/test_charter_lib.py`:
1. `import_line()` == `@operating-instructions.md`.
2. `inject_import` adds the section when absent; second call is a no-op (`changed=False`) — idempotency.
3. `inject_import` preserves pre-existing CLAUDE.md content (no-clobber).
4. `assert_charter_safe` passes on the **shipped** charter (AC3 drift guard).
5. Non-vacuity: `find_gating_language` detects EACH forbidden phrase in a synthetic charter;
   `assert_charter_safe` raises on it.
6. `bundled_charter_path()` points at an existing file.

## Step-4 review amendments (applied — independent Opus reviewer, D2)

An independent `rawgentic-reviewer` (Opus) adversarial pass found 3 High + 4 Medium. All
folded in (spec-tightening; 3 High < volume threshold 5, no Step-3 return):

- **A1 (H2 — filename collision, confirmed on this machine):** `~/.claude/operating-instructions.md`
  already exists and `~/.claude/CLAUDE.md:3` is `@operating-instructions.md`. Using that name
  would make global-scope install a silent no-op (import present → no-op; file exists →
  no-clobber) while the *gating* personal charter keeps loading. **Fix:** bundled charter is
  `rawgentic-operating-charter.md`; import line `@rawgentic-operating-charter.md`; idempotency
  keys off that rawgentic-owned name. Coexists with a user's own `operating-instructions.md`.
- **A2 (H1 — guard oversold + circular test):** the REAL control is *rawgentic authors the
  charter + PR review*; the guard is a **regression tripwire**, not "enforcement" (reframed in
  the charter + this doc). Strengthen `GATING_PATTERNS` to cover real gating-language *families*
  (confirm first / get the call before acting / stop and ask / wait for confirmation|approval /
  a hold persists / competing hold / commit … only when asked / don't … without asking / ask
  before …). Non-vacuity test is built from **paraphrased real gating families** (not the guard's
  own literals), so it proves the guard catches the dangerous class, not just that regex compiles.
- **A3 (H3 — skill-count guards):** adding a 14th skill also updates `tests/test_v3_removals.py`
  (`== 13`→`14`), `test_adversarial_review_registration.py::test_readme_count_strings_updated`
  (`13`→`14`, `9/13`→`9/14`), `README.md` L14 + L652, and the marketplace/plugin.json description
  enumeration (sum → 14; `6 SDLC workflow skills` substring preserved so
  `test_descriptions_consistent_count` stays green).
- **A4 (M1):** `inject_import` edge tests — no-trailing-newline target, pre-existing
  `## Operating Instructions` heading without the import line, and **line-anchored** match
  (`^@rawgentic-operating-charter.md\s*$`, not substring).
- **A5 (M2):** provenance sentinel `<!-- rawgentic-operating-charter v1 -->` as the charter's
  first line; no-clobber/upgrade gate on its presence (distinguishes rawgentic's own file from a
  user's same-named file).
- **A6 (M3/M4):** the safety-critical mutation runs as **tested code**, not LLM prose — a CLI
  entrypoint `python3 hooks/charter_lib.py install --scope {project|global} --project-root <root>
  [--home <h>] [--confirm-global] [--force-upgrade]`. `--scope` is required; **`global` refuses
  without `--confirm-global`** (converts "never silent global" into a tested contract). SKILL.md
  invokes it verbatim.
- **A7 (L1):** project-scope `@import` stays *inferred*; co-location makes the relative path
  correct by construction (Step 9 proxy).

## Platform feasibility (#226)

The likeliest-wrong claim is that a relative `@import` resolves identically from a
**project** CLAUDE.md as from `~/.claude` — confirmed for global from live context,
inferred same for project (docs: imports work in any CLAUDE.md). Step 9 proxy: the command
always co-locates the charter with the target CLAUDE.md, so the relative path is correct by
construction regardless of scope. Machine-readable declaration follows (kept as the final
section so no heading interrupts a block):

platform_apis:
- api: Claude Code @import in CLAUDE.md (bare relative @file.md line)
  feasibility: verified via existing-call-site — this session's ~/.claude/CLAUDE.md uses a bare @operating-instructions.md line and the imported content loads
  failure: fail-silent
  surface: the command writes the charter next to the target CLAUDE.md and reports+stat-checks both paths after writing, so a missing/misplaced import is visible
- api: skill auto-registration (skills/<name>/SKILL.md listed in marketplace.json skills[])
  feasibility: verified via existing-call-site — 13 existing skills register this exact way (marketplace.json entry + codex mirror symlink)
  failure: fail-silent
  surface: tests/test_codex_plugin_packaging.py drift guards fail the build if the symlink or whitelist entry is missing
- api: Python stdlib file I/O (pathlib, os.path.expanduser)
  feasibility: verified via existing-call-site — hooks/*.py read and write files with pathlib throughout
  failure: fail-loud
