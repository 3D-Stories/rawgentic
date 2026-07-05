# Adversarial Review — .rawgentic-diff-review-148-s10.patch

- Date: 2026-07-04
- Artifact type: diff
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 4 (Critical 0, High 1, Medium 3, Low 0)

## Summary

The diff adds a documented multi-issue driver pattern plus Python helpers for dependency parsing, topological ordering, readiness, and lightweight state validation. The main risks are fail-open or underspecified edges where prose claims stronger guarantees than the code or schema provided in the artifact enforce.

## Findings

### 1. [High] security · high confidence — hooks/driver_lib.py: parse_depends_on

> +            segment = line[ph.end():end]
> +            deps.update(int(n) for n in _HASH_NUM_RE.findall(segment))

After a recognized dependency phrase, the parser treats every `#N` in the rest of that phrase segment as a dependency. A line like `Depends on #10. See #20 for context` will incorrectly add `20`, contradicting the stated prompt-injection-safe behavior and allowing ordinary prose on the same line to inject a false in-queue dependency that can mis-order or block work.

**Recommendation:** Change `parse_depends_on` to stop phrase parsing at a sentence boundary or only accept issue numbers in the immediate dependency list grammar after the phrase; add a regression test such as `Depends on #10. See #20 for context` returning `[10]`.

### 2. [Medium] completeness · high confidence — docs/multi-issue-driver.md: The loop / hooks/driver_lib.py: validate_driver_state

> +Only one issue is `in_progress`/`pr_open` at a time (serial; parallel execution
> +needs worktree isolation and is out of scope — #136/#85).

The serial-active invariant is stated as a driver guarantee, but the provided schema and `validate_driver_state` only validate each issue independently and do not reject a state with multiple `in_progress` or `pr_open` issues. That corrupt state makes resumption ambiguous because the driver later expects to find a single active issue.

**Recommendation:** Add a cross-issue validation rule in `validate_driver_state` that counts statuses in `{in_progress, pr_open}` and errors unless the count is at most one; add an equivalent schema/test guard or document that the JSON Schema is not sufficient for live-state validity.

### 3. [Medium] completeness · high confidence — docs/multi-issue-driver.md: Epic anchor

> +- **Headless runs refuse to start without an epic.** In headless mode the epic is
> +  the STATUS/QUESTION channel (the driver has no terminal), so a headless
> +  campaign with no epic is a hard error at start, not a silent degrade.

This hard start gate is unverifiable from the provided text: the diff adds no start-check function, no headless policy field, and no validator rule tying headless mode to a non-null `epic`. If the prose is treated as implemented, a headless campaign can silently lack its required status/question channel.

**Recommendation:** Add an explicit start-validation surface, for example `validate_campaign_start(state, headless: bool)`, that raises when `headless` is true and `epic` is null/missing, and add a test for that failure path.

### 4. [Medium] internal-consistency · medium confidence — hooks/driver_lib.py: next_ready_issue / docs/multi-issue-driver.md: Dependency ordering

> +    "First" is queue order (the ``issues`` list order). A dependency counts as

`next_ready_issue` advances by the existing `issues` list order, while the dependency-ordering prose promises a topological sort with a lowest-issue-number tie-break. The artifact does not state that the sorted result must be persisted back into `state['issues']`, so independent ready issues can run in arbitrary file/epic order instead of the advertised deterministic dependency order.

**Recommendation:** In `docs/multi-issue-driver.md` Dependency ordering step 2, require rewriting the live `issues` list into `topo_sort_issues` order before the loop, or change `next_ready_issue` to compute/select from topologically sorted ready candidates itself.
**Ambiguity:** A caller could manually reorder the state after `topo_sort_issues`, but that required handoff is not specified in the provided artifact.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._