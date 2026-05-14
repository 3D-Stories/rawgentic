# Rawgentic — HIGH Issues

**Source:** First-run retrospective 2026-03-22

---

## 1. Headless mode should block all SSH commands to deploy targets (A2)

### What Happened

Even if the bot doesn't use `.rawgentic.json` deploy commands, nothing prevents a WF2 step from deciding to SSH somewhere for ad-hoc verification. The bot had no awareness that running commands on a remote host could impact a live environment.

### Recommendation

Headless mode should explicitly block all SSH commands to deploy targets during implementation as a defense-in-depth layer. The bot should only interact with:
- GitHub via `gh` CLI
- The local filesystem

SSH to infrastructure hosts should require an explicit `headless.allowSSH: true` flag in `.rawgentic.json` or be gated behind a headless interaction point (which would pause and ask the orchestrator for permission).

This is defense-in-depth on top of removing `.ssh/` from the container (arc issue). Even if SSH keys are available (e.g., a human-run headless session), the skill should refuse to use them unless explicitly allowed.

---

## 2. Post status comments at WF2 step boundaries in headless mode (A3)

### What Happened

For 70 minutes, the only signal was the initial "AI agent claimed this issue" comment and an "Implementation started" comment. The human had no idea what the bot was doing, whether it was stuck, or what it had accomplished. The user asked "what is it doing" and "is it stuck" multiple times during monitoring.

### Recommendation

Post status comments on the GitHub issue at WF2 step boundaries. At minimum:

| Step | Comment |
|------|---------|
| Step 2 | "Analysis complete — [summary of findings, branch created]" |
| Step 8 | "Implementation in progress — [N files changed, M commits so far]" |
| Step 11 | "Code review starting — [reviewing N commits across M files]" |
| Step 12 | "Creating PR — implementation complete" |

Each comment should include what was done and what's next. In headless mode, these comments are the primary communication channel. They should be concise but informative.

### Cross-References

- Top 5 action item #3
- Related: Arc MEDIUM "Completion comment with metrics" (E3) — the final comment is the arc orchestrator's responsibility, but mid-run comments are the skill's responsibility

## 3. Tool stop hook prevents writting to other projects documentation.

Because the creation of this tool impacted multiuple projects (arc, rawgentic, chorestory, sysop), The retrospective was not able to create documentation in in the other projects folders.

### Recommendation

Adjust hok to allow writting into other projects docs folder