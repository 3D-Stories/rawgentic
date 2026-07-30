# Owner comments — session-mining-2026-07-30 (rev 2)

Exported: 2026-07-30T17:35:24.422Z
Comments: 8

## 1. [verdict-h1] §verdicts

> H1 · OPUS-5 INSTRUCTION-FOLLOWING Partially confirmed A real, smaller set of genuine failures — inside two system problems that manufacture the same feeling: goal-machinery defects and a consent-quality gap. 23 H1-highs confirmed · 12 refuted

**Owner comment:** i suspect the goal-machinery defects revolve around pane-handoff jobs (/clear-prep) injecting things into goals and the goals getting worse the more handoffs there are.

## 2. [verdict-h4] §verdicts

> H4 · CLAUDE.MD RATIONALIZATION Not supported Every rule the confirmed failures violated still exists in the current tiers. Failures are pressure-shaped, not knowledge-shaped. 1 medium finding in 155 mined

**Owner comment:** i think we should do a review with fable 5 on xhigh the before and after claude.md's at each leevel.

## 3. [ex-c0a03151] §h1 — Three layers the first draft conflated

> 22 fires vs an owner-only DONE clausec0a03151 · 07-28 · goal-design, not model 00:36:37ASSTThe loop is now costing you tokens for no progress, so I'll be direct: the only way to stop it is to clear or narrow the /goal. I can't do that myself, and I won't fake completion to satisfy it.

**Owner comment:** this one is partially true.  the goal only loops 9 times before it stops churning if it cannot continue

## 4. [callout-25] §h2 — Over-hardening: real at the margins, ratified at the core

> Correction — the first draft's centerpiece was wrong twi #25: the hardening didn't cause the failure. It caught one. Zero chapters cached — but the root cause was a stale container constant (htmlbits.py:34) after the site moved to an Elementor theme. Drift, not review-imposed brittleness. And the "hypothetical" substring false-positive the review targeted materialized on the live page (.entry-content .post-likes-widget{} inside a style block) — the hardened parser correctly ignored it: "A naive  …

**Owner comment:** In general, my assertion that I could build a scraper in a couple of hours to do this because we only ever have to scrape once stands. I don't understand why there's 29,000 lines of code to do this. It's insane. 15 PRs and 23 commits. Are you freaking kidding me?

## 5. [flow-h5] §h5 — The sysop handoff, reconstructed

> 96e1d746 · w1:pA914:56 bound: sysopwork: rawgenticRan the registry grep, saw sysop, wrote "Session is bound to sysop but all work this session was … the rawgentic repo" — then passed --project rawgentic. Asked the owner about pane teardown; never about the project fork. → 7abd6487 · successor15:03 bound: rawgenticCame up bound to rawgentic with the #722 goal armed. Owner interrupted, re-bound it to sysop at 15:18 — the rawgentic goal kept firing (Stop hook, 15:18:56). Interrupted again 15:20. →  …

**Owner comment:** This failure was actually really impactful because we lost the whole Sysop on the server addition/migration work.

## 6. [driver-1] §synthesis — Where the leverage actually is

> Consent quality (new, highest leverage)Model-drafted goals, ACs, and scope revisions become "owner intent" through thin ratification moments — then the system enforces them expensively, and both owner and models misremember who decided what within hours. Overrides of standing instructions must surface as first-class yes/no questions, never as clauses inside 4,000 characters.

**Owner comment:** I also find there's really not a lot of need for a complex 40,000-character goal.  Often times a simple " Finish all stories in Epic, merge all PRs, create a UAT test plan, UAT as much as you can, hand off user UAT to users."  Should be sufficient.  I think the more context you have in the goal, the easier it is to be misinterpreted.  How can we put some guardrails around this?  Also, I think we need a mechanism and guardrails that the initial goal is set by the user and then pain handoffs have to respect that goal and the only thing that can change in the goal  Mid-session have to be approved by the user. use /ask-owner skill if needed

## 7. [action-1] §actions — Recommended actions — proposed, nothing filed without approval

> Highest leverageRatification diffs + a deferral registryAny goal/AC text that overrides a prior owner instruction becomes an explicit yes/no AskUserQuestion naming that instruction — never a clause inside a >500-char paste. Owner deferrals become hard blocks the epic driver and WF2 Step 1 check.

**Owner comment:** yes! and if owner is not around because they have indicated so, use the /ask-owner skill.

## 8. [action-4] §actions — Recommended actions — proposed, nothing filed without approval

> Fixes H3Wire the executor + de-monocultureEpic #756/#735 covers the instruction layer. Add executorRouting to active projects; implementation seats off the main model (Sonnet per the standing rule); keep gpt-5.6-sol for critique, with the class field.

**Owner comment:** yes but make sure we review the default models for the executor during implementation. i want to make some changes. I dont agree with "keep gpt-5.6-sol for critique, with the class field." critique should also be an executor with a configurable model.
