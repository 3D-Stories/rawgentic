# Documentation Completeness — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create 5 new contributor-facing docs, add inline schema comments, and commit all changes as a single docs branch.

**Architecture:** Each doc is a standalone markdown file in `docs/` answering one question for contributors. All factual claims must be verified against the actual codebase before writing. Schema comments are added inline to `templates/rawgentic-json-schema.json`.

**Tech Stack:** Markdown, JSON, bash (for verification)

---

### Task 1: Create docs branch and commit count corrections

**Files:**
- Modify: `README.md` (already modified)
- Modify: `.claude-plugin/plugin.json` (already modified)
- Modify: `.rawgentic.json` (already modified)
- Modify: `docs/plans/2026-03-08-documentation-completeness-design.md` (already modified)

**Step 1: Create branch from main**

```bash
git checkout -b docs/documentation-completeness origin/main
```

Note: The count corrections and design doc are already on main. Cherry-pick or re-apply on the branch.

**Step 2: Verify all count references are consistent**

```bash
grep -n "9 SDLC" README.md .claude-plugin/plugin.json .rawgentic.json
grep -n "13 skills" README.md
grep -n "3 workspace\|3 skills" README.md
```

Expected: All references say "9 SDLC + 3 workspace + 1 security skill + hooks" or "13 skills."

**Step 3: Commit**

```bash
git add README.md .claude-plugin/plugin.json .rawgentic.json docs/plans/
git commit -m "docs: fix skill counts and broken link in README

- Headline: 9 SDLC + 3 workspace + 1 security skill + hooks (13 total)
- Move sync-security-patterns from Workspace table to Security section
- Fix broken link to claude-md-architecture-design.md"
```

---

### Task 2: Write `docs/testing.md`

**Files:**
- Create: `docs/testing.md`
- Reference: `tests/hooks/test_security_guard.py`, `tests/hooks/test_security_guard_e2e.py`

**Step 1: Verify test infrastructure facts**

```bash
# Check pytest is the framework
head -5 tests/hooks/test_security_guard.py
# List all test classes
grep "^class Test" tests/hooks/test_security_guard.py
# Check E2E file exists and its structure
grep "^class Test\|^def test" tests/hooks/test_security_guard_e2e.py
# Verify tests run
cd /path/to/rawgentic && python -m pytest tests/ --co -q 2>&1 | head -20
```

**Step 2: Write `docs/testing.md`**

Content based on design section 1. Include:
- Running Tests: prerequisites, `pytest tests/` command
- Security Guard Tests: unit test coverage (list all TestXxx classes from the actual file), E2E test coverage
- Tests to be Implemented: WAL hooks, session management, security — each with a brief description of what would be tested
- Link to issue #9

**Step 3: Verify all file paths mentioned in the doc exist**

```bash
ls tests/hooks/test_security_guard.py tests/hooks/test_security_guard_e2e.py
ls hooks/security_guard_lib.py hooks/security-guard.py
```

**Step 4: Commit**

```bash
git add docs/testing.md
git commit -m "docs: add testing.md with test coverage and known gaps"
```

---

### Task 3: Write `docs/wal-guide.md`

**Files:**
- Create: `docs/wal-guide.md`
- Reference: `hooks/wal-pre`, `hooks/wal-post`, `hooks/wal-post-fail`, `hooks/wal-stop`, `hooks/wal-lib.sh`, `hooks/session-start`

**Step 1: Extract WAL entry format from wal-lib.sh**

Read `hooks/wal-lib.sh` to find the exact JSON fields written to WAL entries. Document the actual field names, not guesses.

**Step 2: Extract WAL recovery logic from session-start**

Read `hooks/session-start` sections on WAL rotation (line count threshold, age cutoff) and sanitization. Note exact values (5000 lines, 7 days).

**Step 3: Write `docs/wal-guide.md`**

Content based on design section 2. Include:
- Overview: what WAL does, file locations (`claude_docs/wal/<project>.jsonl`, legacy `claude_docs/wal.jsonl`)
- WAL Entry Format: actual JSON schema from wal-lib.sh, example entries for INTENT/DONE/FAIL
- WAL Lifecycle: wal-pre/post/post-fail/stop mapped to phases
- Shared library: mention `hooks/wal-lib.sh` as the common functions used by WAL hooks
- Inspecting WAL Files: jq queries for incomplete ops, filter by session, filter by tool, count ops
- WAL Recovery: session-start behavior with actual thresholds
- Troubleshooting: empty WAL, corrupt entries, missing file

**Step 4: Verify all jq queries work against sample WAL format**

Construct a sample WAL entry matching the format from wal-lib.sh and test each jq query.

**Step 5: Commit**

```bash
git add docs/wal-guide.md
git commit -m "docs: add WAL guide with format, lifecycle, and inspection queries"
```

---

### Task 4: Write `docs/skill-development.md`

**Files:**
- Create: `docs/skill-development.md`
- Reference: `skills/*/SKILL.md`, `skills/*-workspace/`, `skills/phase2-eval-summary.md`, `.claude-plugin/plugin.json`

**Step 1: Verify skill structure facts**

```bash
# Check frontmatter format from an example skill
head -5 skills/create-issue/SKILL.md
# List all workspace directories
ls -d skills/*-workspace/ 2>/dev/null
# Check workspace internal structure
ls -R skills/create-issue-workspace/iteration-1/ 2>/dev/null | head -20
# Check plugin.json skills registration
grep -A2 '"skills"' .claude-plugin/plugin.json | head -10
```

**Step 2: Write `docs/skill-development.md`**

Content based on design section 3. Include:
- Skill Structure: SKILL.md location, frontmatter fields, body as injected prompt
- Workspace Directories: purpose (evaluation, not runtime), structure (iteration-N/benchmark.md, scenario dirs with with_skill/without_skill transcripts)
- Evaluation Methodology: scenario testing, comparison runs, benchmark grading, phase2-eval-summary.md
- Adding a New Skill: create SKILL.md, register in plugin.json, optional evaluation

**Step 3: Verify all referenced paths exist**

```bash
ls skills/phase2-eval-summary.md
ls skills/create-issue-workspace/iteration-1/benchmark.md
```

**Step 4: Commit**

```bash
git add docs/skill-development.md
git commit -m "docs: add skill development guide with evaluation methodology"
```

---

### Task 5: Write `docs/session-notes.md`

**Files:**
- Create: `docs/session-notes.md`
- Reference: `hooks/session-start`, `hooks/wal-context`, `skills/switch/SKILL.md`

**Step 1: Extract session notes facts from hooks**

Read `hooks/session-start` for:
- How notes files are initialized (format, header)
- Archival behavior

Read `hooks/wal-context` for:
- How notes are read and injected into context
- Session registry lookup logic
- Auto-bind behavior (single active project)

Read `skills/switch/SKILL.md` for:
- How session registry entries are written

**Step 2: Write `docs/session-notes.md`**

Content based on design section 4. Include:
- Overview: per-project markdown, location, auto-creation
- How Notes are Populated: session-start init, wal-context reads, workflow step markers, compaction recovery
- Session Registry: file format, entry schema, written by switch/auto-bind, read by wal-context
- Session Lifecycle: numbered flow from start to archival
- Archival: preservation behavior

**Step 3: Verify file paths and formats**

```bash
# Check session-start creates notes with this format
grep "Session Notes" hooks/session-start
# Check wal-context reads registry
grep "session_registry" hooks/wal-context
# Check step marker format referenced in workflow skills
grep "WF.*Step.*DONE" skills/implement-feature/SKILL.md | head -3
```

**Step 4: Commit**

```bash
git add docs/session-notes.md
git commit -m "docs: add session notes guide with registry and lifecycle"
```

---

### Task 6: Write `docs/config-reference.md` and update schema

**Files:**
- Create: `docs/config-reference.md`
- Modify: `templates/rawgentic-json-schema.json`
- Reference: `skills/setup/SKILL.md`, `skills/implement-feature/SKILL.md` (config-loading protocol)

**Step 1: Extract config-loading protocol from skills**

Read the `<config-loading>` section from any SDLC skill (e.g., `skills/implement-feature/SKILL.md`) to document the exact fallback chain and capabilities object derivation.

**Step 2: Write `docs/config-reference.md`**

Content based on design section 5, Part A. Include:
- Overview: purpose, generated by setup, link to schema file
- Design Principles: config-driven, single source of truth, capabilities object, learning config
- Core Sections: brief description of each top-level key with which workflows consume it
- Versioning: version = 1, mismatch behavior
- Config-Loading Protocol: exact fallback chain, path resolution, error behavior

**Step 3: Add `$comment` fields to `templates/rawgentic-json-schema.json`**

Add a `$comment` to each top-level section. Example:

```json
"testing": {
  "$comment": "Test frameworks. Consumed by WF2-WF4, WF8-WF10 for test execution and CI verification.",
  "frameworks": [...]
}
```

Sections to annotate: project, repo, techStack, testing, database, services, infrastructure, deploy, security, ci, formatting, documentation, custom.

**Step 4: Verify config-loading protocol matches actual skill code**

```bash
grep -A5 "config-loading" skills/implement-feature/SKILL.md | head -10
grep "capabilities" skills/implement-feature/SKILL.md | head -5
```

**Step 5: Commit**

```bash
git add docs/config-reference.md templates/rawgentic-json-schema.json
git commit -m "docs: add config reference and inline schema comments"
```

---

### Task 7: Update `.rawgentic.json` documentation section

**Files:**
- Modify: `.rawgentic.json`

**Step 1: Update primaryFiles to include new docs**

Add the 5 new files to `config.documentation.primaryFiles[]`:

```json
"primaryFiles": ["README.md", "docs/", "LICENSE"]
```

Since `docs/` is already listed as a directory, the new files are automatically included. Verify this is sufficient — no change needed if `docs/` covers it.

**Step 2: Commit (only if change was needed)**

```bash
git add .rawgentic.json
git commit -m "docs: update config documentation paths"
```

---

### Task 8: Final verification and PR

**Step 1: Verify all new docs reference valid paths**

```bash
# Extract all file paths mentioned in new docs and verify they exist
grep -oP '`[^`]*\.(md|py|sh|json|jsonl)`' docs/testing.md docs/wal-guide.md docs/skill-development.md docs/session-notes.md docs/config-reference.md | sort -u
```

Cross-check each path against the filesystem.

**Step 2: Push and create PR**

```bash
git push -u origin docs/documentation-completeness
gh pr create \
  --repo 3D-Stories/rawgentic \
  --title "docs: add 5 contributor-facing docs for testing, WAL, skills, sessions, and config" \
  --body "$(cat <<'EOF'
## Summary
- Fix skill counts across README, plugin.json, .rawgentic.json (9 SDLC + 3 workspace + 1 security = 13)
- Move sync-security-patterns from Workspace table to Security section
- Fix broken README link to plugin-overhaul-design.md
- Add 5 new contributor docs: testing.md, wal-guide.md, skill-development.md, session-notes.md, config-reference.md
- Add inline $comment fields to templates/rawgentic-json-schema.json

## Verification
- All file paths in docs verified against filesystem
- All jq queries tested against WAL entry format
- Skill counts verified: 13 SKILL.md files = 9 + 3 + 1

## Related
- Closes design: docs/plans/2026-03-08-documentation-completeness-design.md
- References #8 (security pattern staleness check)
- References #9 (comprehensive test suite)

## Test plan
- [ ] All docs render correctly as markdown
- [ ] No broken file path references
- [ ] Skill counts consistent across README, plugin.json, .rawgentic.json

Generated with [Claude Code](https://claude.com/claude-code) using WF7
EOF
)"
```
