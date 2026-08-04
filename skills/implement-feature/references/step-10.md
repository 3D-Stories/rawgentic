## Step 10: Conditional Memorization (Background)

### Instructions

Run the memorization pass inline, or as a background harness subagent per the
`<model-routing-resolve>` contract when the gate-findings sweep is broad (D182).

**Runs in PARALLEL with Step 11.**

1. Review quality gate findings from Steps 4, 6, and 9.
2. Identify reusable insights — patterns applicable beyond this specific issue.
3. If memorizable insights exist, curate each into memory: if a mempalace MCP
   server is available (`mcp__mempalace__*` tools loaded), store it via
   `mempalace_kg_add` (a fact/decision) or `mempalace_add_drawer` (a note),
   scoped to this project; otherwise — or if the mempalace store call fails —
   check for duplication against CLAUDE.md and MEMORY.md and append if novel.
4. If no reusable patterns: skip entirely.

### Output
Insight stored to mempalace and/or an updated CLAUDE.md (if insights memorized), or no output.

---

