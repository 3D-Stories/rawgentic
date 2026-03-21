# Plugin Routing: BMAD + Rawgentic

> **When to use this:** Copy this section into your `~/.claude/CLAUDE.md` (global) or
> project-level `CLAUDE.md` if you have both BMAD and rawgentic installed.
>
> Both plugins overlap in implementation, code review, testing, and documentation.
> Without explicit routing, Claude may pick the wrong skill. This table prevents that.

## Routing Table

| Task | USE | DO NOT USE |
|------|-----|------------|
| Implement a feature | `bmad-dev-story` | `rawgentic:implement-feature` |
| Fix a bug | `bmad-dev-story` | `rawgentic:fix-bug` |
| Create/improve tests | BMAD TEA module (`bmad-tea-*`) | `rawgentic:create-tests` |
| Code review | `bmad-code-review` | — |
| Update documentation | BMAD tech-writer agent | `rawgentic:update-docs` |
| Refactor code | `rawgentic:refactor` | — (BMAD has no formal refactor) |
| Security audit | `rawgentic:security-audit` | — (BMAD has none) |
| Incident response | `rawgentic:incident` | — (BMAD has none) |
| Update dependencies | `rawgentic:update-deps` | — (BMAD has none) |
| Optimize performance | `rawgentic:optimize-perf` | — (BMAD has none) |

**Override rule**: If the user explicitly types a rawgentic slash command
(e.g., `/rawgentic:implement-feature`), honor it — they know what they want.
This table only governs automatic skill selection.

## Why This Division?

BMAD excels at the full product lifecycle — from idea through planning, architecture,
story creation, and sprint-based implementation. Its story files carry rich context
from PRDs, UX specs, and architecture docs that make implementation more informed.

Rawgentic excels at runtime safety (WAL guards, security guards), and has dedicated
workflows for tasks BMAD doesn't cover: security audits (STRIDE), incident response,
dependency updates, performance optimization, and formal refactoring with behavioral
preservation guarantees.

The overlap zone (implementation, bug fixing, testing, documentation) is where BMAD
wins when you've done upstream planning — its story-driven context is richer than
rawgentic's issue-driven approach. Rawgentic's safety hooks (WAL, guards) remain
active regardless of which implementation workflow you use, since they're PreToolUse
hooks that fire on every Bash/Edit/Write call.

## Complementary Capabilities

**Always active from rawgentic (passive hooks — no routing needed):**
- WAL guard: blocks dangerous production commands before execution
- Security guard: blocks dangerous code patterns before they're written
- WAL logging: audit trail of all mutations per-project
- Session notes archival: auto-archive when >600 lines

**Use rawgentic explicitly for:**
- `/rawgentic:refactor` — formal behavioral preservation with characterization tests
- `/rawgentic:security-audit` — STRIDE threat modeling and remediation
- `/rawgentic:incident` — 2-phase incident response (stabilize, then RCA)
- `/rawgentic:update-deps` — security-first dependency updates
- `/rawgentic:optimize-perf` — measure-first performance optimization

**Use BMAD for everything else** (planning, stories, implementation, review, testing, docs).
