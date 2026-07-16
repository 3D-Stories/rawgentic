# Peer Consult — .rawgentic-routing-problem.md

- Date: 2026-07-16
- Reviewer: Codex (peer designer)

## Approach

Adopt “B now, A only on evidence”: keep the backward-compatible three-role schema, set the project-scoped roles to review=fable, analysis=sonnet, and implementation=opus, and use the existing implementation selector to down-route cheap, ordinary work to sonnet. Run the interactive session on claude-opus-4-8. Opus’s stable design/plan floors outweigh Sonnet’s lower usage-window burn because those decisions are performed inline and errors propagate into every downstream artifact. Use Fable only as a bounded review subagent at existing quality gates, with an explicit Opus quota fallback. Do not add a six-phase configuration yet; the evidence supports role-level distinctions but not enough durable phase-specific choices to justify migration and workflow churn.

## Key decisions

- Driver: claude-opus-4-8. Its design/plan floors (81/82 and 6/6 gates) address the highest-leverage tail risk. Sonnet’s ship advantage and temporary introductory price do not compensate for its observed 46/28 design/plan collapse cells, especially since driver economics are subscription-window burn rather than API dollars.
- Rawgentic configuration remains the existing shape: review=claude-fable-5, analysis=claude-sonnet-5, implementation=claude-opus-4-8. This is a project-local retune and requires no migration for other workspaces.
- Fable review is justified: the +4 versus Opus is the clearest Claude-family review advantage, and review gates protect all downstream work. The 1.6x API-equivalent cost is acceptable because Fable is limited to WF2 Step 4, high-risk-task reviews at Step 8a, and Step 11, rather than used for implementation or general analysis.
- Review fallback chain is Fable -> Opus, triggered only by a recognized quota/capacity rejection. Once Fable quota exhaustion is detected, open a run-scoped circuit breaker and send remaining reviews directly to Opus. Do not fall back merely because a review failed, timed out, or returned an unfavorable result; those cases require retry/error handling, not model substitution. Never include Haiku in any chain.
- Apply one shared semaphore with a maximum of three active Claude subagents across roles. Queue excess work. Avoid speculative duplicate dispatches; retry a rejected Fable invocation on Opus only after confirming the Fable attempt was not accepted.
- Implementation fork: map issue.complexity=complex to the selector’s complex_feature condition. Choose Opus when riskLevel=high OR issue complexity is complex; choose Sonnet only when riskLevel=standard AND complexity is standard/trivial. Unknown or malformed risk/complexity fails closed to Opus. Use both signals because issue complexity captures whole-change difficulty while task risk captures localized blast radius.
- Keep the implementation ceiling at Opus. Fable has no measured build-quality advantage, costs more, and has a build floor of 38. Routing all builds to Opus is also unsupported because the median advantage over Sonnet is inside noise and ordinary tasks have a large cost gap.
- WF2: resolve the three preferred roles once per run as today; use Sonnet for analysis subagents, the existing selector at every implementation dispatch, and Fable-with-Opus-fallback at Steps 4, 8a, and 11. The Opus driver retains final ownership of the change spec, plan, and issue hierarchy.
- WF3: reuse the same role policy. Ensure bug-fix implementation dispatches supply normalized complexity and task risk to select_impl_model; absent signals fail closed to Opus. Route design/diff review subagents through the same review fallback helper. Do not create a separate WF3 model table.
- Ship: retain the Opus driver for final integration and submission. If an existing ship action is already represented as a genuinely standard implementation task, the selector may route it to Sonnet. Do not label work standard merely to obtain Sonnet or introduce a ship-only subagent solely from this benchmark; the handoff/context cost was not measured.
- Evidence refresh: maintain a small checked-in routing decision record containing benchmark ID/date, pricing date, sample size, phase medians, reliability floors, gate counts, chosen values, fallback chain, and rationale. A report-refresh command may generate a candidate comparison, but it must not rewrite production routing automatically.
- Reconsider phase keys only after new reports show at least two repeatable phase distinctions that cannot be represented by the three roles or invocation overrides. Prefer confirmation across two benchmark runs/brief sets, and require both a meaningful quality/reliability difference and material cost impact. Add optional phase overrides only then, with role resolution as the backward-compatible default.
- Do not infer GPT routing from the table: its costs are estimated across engines and GPT/GLM already serve orthogonal adversarial/peer gates. Also unsupported are Fable for intake/build, Sonnet as the interactive driver based only on price, brief-specific routing from two briefs, or an automated optimizer trained on 216 correlated cells.

## Risks

- Fable quota may be exhausted during repeated Step 8a reviews. The Opus fallback circuit breaker preserves progress and prevents repeated quota-consuming probes, but review quality may drop to the measured Opus level.
- Opus driver usage can consume the subscription window faster. Mitigate by farming bounded analysis to Sonnet, ordinary implementation to Sonnet, and reviews to Fable while keeping high-leverage decisions inline.
- Complexity or risk can be understated, incorrectly selecting Sonnet. Preserve fail-closed validation, record the selector inputs and result, and allow the driver to raise—but not silently lower—the selected tier.
- A shared three-agent limit can increase latency on large plans. Queueing is preferable to session-limit deaths; expose queued/running counts so apparent stalls are diagnosable.
- Model aliases, introductory pricing, quotas, and benchmark results will change. Pin supported model identifiers where required and refresh the decision record on new benchmark reports or pricing changes without automatically changing routing.
- The benchmark is small and its briefs are not independent production traffic. Treat median edges as directional and floors as safety evidence; monitor production retry, gate-failure, and escalation rates before expanding specialization.
- Run-scoped fallback state could accidentally leak across projects or sessions. Scope the Fable circuit breaker to the workflow run and preserve each project’s resolved configuration.

## Sketch

Config:
modelRouting:
  review: claude-fable-5
  analysis: claude-sonnet-5
  implementation: claude-opus-4-8

Core dispatch:
roles = resolve_once(workspace, project)

choose_impl(issue_complexity, task_risk):
  normalized_complexity = complex_feature if issue_complexity == complex else issue_complexity
  if either input is missing/invalid: return roles.implementation
  return select_impl_model(
    ceiling=roles.implementation,
    risk_level=task_risk,
    complexity=normalized_complexity)

review(payload):
  acquire shared_claude_semaphore(max=3)
  preferred = opus if run.fable_quota_exhausted else roles.review
  result = dispatch(preferred, payload)
  if preferred == fable and result is recognized_quota_or_capacity_rejection:
    run.fable_quota_exhausted = true
    result = dispatch(opus, payload)
  return result

Epic children:
1. Retune Rawgentic’s three role values and add configuration/never-Haiku regression tests.
2. Add the shared three-agent scheduler plus run-scoped Fable quota detection, Opus fallback, logging, and tests.
3. Wire WF2 Steps 4, 8a, and 11 through review fallback; verify per-run resolution and per-invocation overrides remain intact.
4. Normalize WF2 complexity values at the implementation selector boundary and test the risk-by-complexity matrix, including fail-closed inputs.
5. Bring WF3 implementation and review dispatches onto the same selector/fallback contract, adding fail-closed signal derivation where necessary.
6. Add routing telemetry: workflow, role, phase, preferred/actual model, selector inputs, fallback reason, queue time, and outcome—excluding prompt contents.
7. Add the benchmark provenance decision record and a manual refresh command/checklist that proposes, but never applies, routing changes.
8. Record a deferred phase-override design with an evidence gate; do not implement schema migration until that gate is met.

---
_Peer proposal (report-only). Synthesize at your discretion._
