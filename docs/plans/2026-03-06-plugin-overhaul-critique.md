# Work Critique Report: Rawgentic Plugin Overhaul Design

## Executive Summary

The design is architecturally sound with strong separation of concerns (workspace vs config layers), correct optimization for the LLM-prompt execution environment, and thorough coverage of all user requirements. The primary risks are in the data layer: missing schema versioning, no JSON corruption handling, and an undefined merge strategy for the "learning config" pattern. These should be resolved before implementation.

**Overall Quality Score: 8/10** (average of 9 + 8 + 7)

---

## Judge Scores

| Judge | Score | Key Finding |
|-------|-------|-------------|
| Requirements Validator | 9/10 | All 17 requirements covered. Minor documentation gaps (user journeys, CLAUDE.md content spec). |
| Solution Architect | 8/10 | Architecture is correct. Config-loading duplication is the main risk. Schema versioning missing. |
| Code Quality Reviewer | 7/10 | 3 High-severity gaps: no schema version, write atomicity, JSON corruption handling. 12 Medium issues. |

---

## Strengths (All Judges Agreed)

1. **Two-layer architecture is clean and correct.** Workspace layer (which project) vs config layer (how project is configured) maps perfectly to real user operations. (All 3 judges)

2. **Deep schema with front-loaded detection is the right optimization.** Moving filesystem probing to setup-time and storing results in JSON minimizes per-invocation token cost. (J2 specifically validated against alternatives)

3. **CLAUDE.md decoupling eliminates fragility.** Replacing generated content with a static pointer removes a class of bugs. (J1, J2)

4. **Session-start hook simplicity.** 3-state flow is minimal, deterministic, and does not read .rawgentic.json. (J2, J3)

5. **Learning config pattern is pragmatic.** Addresses incomplete information at setup time. Config gets richer with use. (J2, J3)

6. **Requirements coverage is thorough.** All 17 explicit and implicit requirements addressed. (J1: 15/17 full pass, 2/17 satisfied through valid architectural decomposition)

7. **Phased implementation is prudent.** Foundation first, then cascade to workflow skills. (J2)

---

## Issues & Gaps

### Critical Issues (Must address before implementation)

**1. No `version` field in `.rawgentic.json`** (J2 weakness #2, J3 Issue F)
- Both architect and quality reviewer flagged this independently
- `.rawgentic_workspace.json` has `"version": 1` but project config does not
- When schema evolves, skills reading old-format configs will fail silently
- **Recommendation:** Add `"version": 1` to schema. Config-loading protocol checks version against expected and suggests `/rawgentic:setup --upgrade` if mismatched.

**2. No error handling for corrupted/malformed JSON** (J3 Issue M)
- The "learning config" pattern has skills writing to JSON files during execution
- A crash mid-write could leave corrupted JSON
- Config-loading protocol says "parse JSON" but no error path
- **Recommendation:** Add to protocol: if malformed JSON, STOP and report with file path. Suggest `/rawgentic:setup` to regenerate.

**3. "Learning config" has no merge strategy** (J2 acknowledged, J3 Issue J)
- Can skills overwrite existing values? Append to arrays? What about conflicts?
- Example: setup says `testing.frameworks: [vitest]`, later implement-feature discovers pytest. Append? Replace? Ask?
- **Recommendation:** Define merge policy:
  - Append-only for arrays (never remove existing entries)
  - Set fields that are null/missing, never overwrite existing non-null values
  - On conflict, log and ask user at end of skill execution

### High Priority (Should address before implementation)

**4. `lastUsed` timestamp update on every skill invocation** (J2 weakness #3, J3 Issue I)
- Every workflow skill writes to workspace file. Purely informational data causing unnecessary writes.
- **Recommendation:** Remove from config-loading protocol. Update only in `switch`, `new-project`, and optionally at skill completion (not start).

**5. No CLAUDE.md migration path for existing users** (J1 Gap #3, J3 Issue L)
- Current CLAUDE.md contains P1-P14 principles, constants, critique matrix
- Design says "Delete template" but no step removes old content from existing CLAUDE.md
- **Recommendation:** Add migration step to `setup`: detect old SDLC section, parse as seed values, remove after .rawgentic.json is written.

**6. `techStack` has no controlled vocabulary** (J3 Issue Q)
- Skills need to pattern-match against `techStack` values but no canonical list defined
- `["Node.js"]` vs `["node"]` vs `["nodejs"]` will produce inconsistent behavior
- **Recommendation:** Either (a) define canonical names in schema template, or (b) make `techStack` purely informational and derive all capability decisions from structured sections only. J3 recommends option (b) — more robust.

**7. `new-project` must explicitly handle workspace file creation** (J1 Gap #4)
- Step 4 says "Add to workspace" but doesn't specify creating the file if missing
- First-time user flow depends on this
- **Recommendation:** Step 4 should read: "Create `.rawgentic_workspace.json` if missing, then add project entry."

### Medium Priority (Address during implementation)

**8. Config-loading protocol duplication across 10+ skills** (J2 weakness #1)
- ~20 identical lines with no mechanical enforcement of consistency
- **J2 mitigation options:** (a) Extract to shared reference file — costs one extra Read per invocation but single source of truth. (b) Validation script that diffs blocks across skills.
- Design explicitly chose inline (user decision in Q5). Mitigation script is additive.

**9. Contradictory required fields** (J3 Issue A)
- Design doc says `project`, `repo`, `techStack` required
- Brainstorming session says `project` and `repo` only
- **Recommendation:** Resolve to one answer. If `techStack` becomes informational per recommendation #6, it could be optional.

**10. `project.root` is always `"."` — redundant** (J3 Issue B)
- Project path is already in workspace file. `root: "."` carries no information.
- **Recommendation:** Either remove, or clarify purpose (monorepo subdirectory support).

**11. Re-running `setup` may wipe learned capabilities** (J3 Issue V)
- If user re-runs setup, auto-detection regenerates config from scratch
- Previously learned entries (from skill discoveries) could be lost
- **Recommendation:** Setup should merge with existing .rawgentic.json, preserving learned entries.

**12. `new-project` has no rollback on clone failure** (J3 Issue N)
- If `git clone` fails after folder creation, empty folder gets registered
- **Recommendation:** Reorder: create folder -> clone -> verify success -> register.

**13. Hook working directory assumption** (J3 Issue W)
- Hook reads `./rawgentic_workspace.json` but cwd may not be Claude root
- **Recommendation:** Use `$CLAUDE_PROJECT_ROOT` or search upward.

### Low Priority (Known limitations, address later)

- **No `unregister`/`remove` skill** (J3 Issue P) — acknowledged, defer to post-Phase 1
- **`services[].type` enum too narrow** (J3 Issue E) — allow arbitrary strings
- **No general `commands`/`scripts` section** (J3 Issue R) — add `custom: {}` escape hatch (J2 recommendation)
- **`deploy` supports only single target** (J3 Issue S) — known limitation
- **`switch` doesn't verify directory exists** (J3 Issue O)
- **`research` project type not handled by existing skills** (J3 Issue C)
- **No garbage collection for stale workspace entries** (J3 Issue H)

---

## Areas of Consensus (All 3 Judges)

- The two-layer architecture is correct
- Deep schema is the right choice for LLM prompt environment
- CLAUDE.md should be pointer-only
- Session-start hook design is clean
- Schema needs a version field
- The learning config pattern needs guardrails (merge strategy + corruption handling)
- Phased implementation ordering is sound

## Areas of Debate

**Debate 1: Should config-loading protocol be extracted to a shared file?**
- J2 recommends extracting to reduce duplication risk (single source of truth, one extra Read per invocation)
- User explicitly chose inline in Q5 during brainstorming
- **Resolution:** Respect user's decision. Add a validation script as a mitigation. Revisit if drift becomes a problem.

**Debate 2: Should `techStack` be required or informational?**
- Design doc says required with at least one entry
- J3 argues it should be purely informational since capabilities are derived from structured sections
- **Resolution:** Reasonable disagreement. Making it optional + informational is the safer choice. All capability logic should derive from structured sections, not string matching on `techStack`.

---

## Action Items (Prioritized)

**Must Do (before implementation):**
- [ ] Add `"version": 1` to `.rawgentic.json` schema
- [ ] Add malformed JSON error handling to config-loading protocol
- [ ] Define merge policy for "learning config" (append arrays, set nulls, ask on conflict)
- [ ] Resolve `techStack` required vs optional + vocabulary question

**Should Do (during Phase 1 implementation):**
- [ ] Move `lastUsed` update out of config-loading protocol (update at skill completion or only in switch/new-project)
- [ ] Add CLAUDE.md migration step to `setup` skill
- [ ] Explicitly handle workspace file creation in `new-project`
- [ ] Add `custom: {}` section to schema for arbitrary project-specific constants
- [ ] Clarify or remove `project.root` field
- [ ] Add "User Journeys" section (cold start, returning, switching) to design doc
- [ ] Specify re-run `setup` merge behavior (preserve learned entries)
- [ ] Fix clone failure rollback ordering in `new-project`
- [ ] Add hook working directory handling

**Could Do (Phase 2+):**
- [ ] Create `rawgentic:unregister` skill
- [ ] Add general `scripts`/`commands` section to schema
- [ ] Support multiple deploy targets
- [ ] Add schema validation to config-loading protocol
- [ ] Create a linting script for config-loading protocol consistency across skills

---

## Conclusion

The design is well-architected and ready for implementation with targeted amendments. The two-layer workspace/config separation is the right abstraction. The three critical issues (schema versioning, JSON corruption handling, merge strategy) affect the reliability of the data layer that every skill depends on — they should be resolved in the design document before Phase 1 implementation begins. The high-priority items (migration path, techStack vocabulary, lastUsed writes) will cause user friction if deferred. All low-priority items are acceptable known limitations.

**Verdict:** Needs targeted improvements before implementation (3 critical + 4 high-priority items)

*Generated using Multi-Agent Debate + LLM-as-a-Judge pattern*
*Review Date: 2026-03-06*
