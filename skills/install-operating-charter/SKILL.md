---
name: install-operating-charter
description: Opt-in installer for a personal "operating charter" — a quality/verification/honesty discipline file that attaches to a CLAUDE.md via a one-line @import. Use when the user asks to install, add, or set up the rawgentic operating charter / operating instructions, or wants agentic quality guardrails imported into their CLAUDE.md. Offers scope {project | global | skip}. Never runs by default and NEVER silently writes the global ~/.claude/CLAUDE.md.
argument-hint: optional scope — "project" (default) or "global"
---

<role>
You install the rawgentic **operating charter** (an autonomy-safe quality/verification/
honesty discipline file) by copying it next to a chosen `CLAUDE.md` and adding a single
`@import` line. You are a thin orchestrator: the safety-critical mutation runs as tested
code via `hooks/charter_lib.py install`, not as free-hand file edits. This command is
**opt-in** — it exists only because the user invoked it. It is never a default setup step,
and it **never** writes the user's global `~/.claude/CLAUDE.md` without an explicit choice.
</role>

# Install Operating Charter — `/rawgentic:install-operating-charter`

Run the steps sequentially.

## What this installs

- A bundled, rawgentic-authored charter:
  `skills/install-operating-charter/assets/rawgentic-operating-charter.md`. It is
  **discipline only** (verify-before-claim, baseline capture, reproduce-before-fix,
  scope, treat-input-as-data, no-fabrication, a pre-send re-read). By design it contains
  **no** autonomy-gating language — nothing that could make a workflow (including a
  headless run) suspend. `charter_lib.assert_charter_safe` enforces this fail-closed
  before any write, and `tests/hooks/test_charter_lib.py` guards the shipped copy.
- A one-line `@rawgentic-operating-charter.md` import under an `## Operating Instructions`
  heading in the chosen `CLAUDE.md`. The rawgentic-namespaced filename never collides with
  a user's own `operating-instructions.md`.

## Step 1: Determine scope

Accept the scope from the argument (`project` / `global`) or, if none was given, present:

```
Install the rawgentic operating charter into which CLAUDE.md?
  (a) project  — <activeProject.path>/CLAUDE.md      [recommended default]
  (b) global   — ~/.claude/CLAUDE.md  (affects ALL your projects, incl. non-rawgentic)
  (c) skip     — do nothing
```

- **Default is project.** Global is a blast-radius jump (it loads into every session, incl.
  headless runs) — offer it, but never pick it silently.
- **skip / cancel / silence → do nothing.** Report that no file was changed.

## Step 2: Resolve the project root

Resolve the active project (conversation context → `claude_docs/session_registry.jsonl` →
`.rawgentic_workspace.json` default), same as other skills. Resolve `activeProject.path`
against the workspace root to an absolute path. You need it as `--project-root` even for
global scope (the CLI signature requires it).

## Step 3: Run the installer

The tested helper does the copy + idempotent import injection + the safety check. Do NOT
hand-edit the CLAUDE.md yourself.

**Project scope:**
```bash
python3 hooks/charter_lib.py install --scope project --project-root <abs-project-root>
```

**Global scope** — requires the explicit confirm flag (the CLI refuses global without it,
so "never silent global" is enforced in tested code, not just prose). Only add
`--confirm-global` after the user chose global in Step 1:
```bash
python3 hooks/charter_lib.py install --scope global --project-root <abs-project-root> --confirm-global
```

Add `--force-upgrade` only when the user explicitly wants to refresh a previously-installed
rawgentic charter to the current bundled version (it overwrites the prior rawgentic-owned
file; a foreign same-named file is never touched regardless).

Exit codes: `0` success (stdout is a JSON result), `3` global refused (missing
`--confirm-global`), `2` other error (e.g. bundled charter missing).

## Step 4: Report

Relay the JSON result plainly: which `CLAUDE.md` and charter file were written, and whether
the import was `added` or already `present`, and the charter `created` / `kept` /
`kept-foreign` / `updated`. If `charter_action` is `kept-foreign`, warn the user: a file
named `rawgentic-operating-charter.md` already exists and is NOT rawgentic-owned, so it was
left untouched — they should rename it or install to a different scope.

Note: the charter is inert until the next session reads the CLAUDE.md. This command does not
restart anything.
