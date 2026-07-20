# Adversarial Review — 2026-07-20-447-diagram-executor-seats.md

- Date: 2026-07-20
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 6 (Critical 2, High 3, Medium 1, Low 0)

## Summary

The artifact proposes generated routing metadata and UI rendering for WF2 stations, but several internal contradictions make the generator, drift check, and renderer contract incompatible as written. The highest risks are a validator that rejects the documented mapping and a sentinel format that cannot pass its specified JSON parser test.

## Findings

### 1. [Critical] correctness · high confidence — Components 2–3; Tests item 3

> 3. **Separate DATA block** `DATA.seatRouting = { generatedAt-free provenance, records:{<stationId>:{…}} }`
>    inside the sentinels (a plain JSON object literal — no executable fragments, `JSON`-encoded).

`DATA.seatRouting = {...}` is a JavaScript assignment, not a JSON value. The specified test later applies `json.loads` to the sentinel-delimited block; if the assignment is inside those sentinels as written, parsing is guaranteed to fail.

**Recommendation:** Change Components item 3 to: `Emit DATA.seatRouting = immediately before /*SEAT-ROUTING-START*/, place only the JSON object between /*SEAT-ROUTING-START*/ and /*SEAT-ROUTING-END*/, and place the terminating semicolon after the end sentinel. The drift test parses and compares only the text between the sentinels.`

### 2. [Critical] internal-consistency · high confidence — Components 2; Station → seat mapping; Tests item 1

> fail-closed on unknown station id / duplicate seat / bad enum
>    / empty primary / non-array chain.

The proposed manifest intentionally maps the `review` seat to stations 4, 8a, and 11, but the specified test rejects a duplicate seat. Implemented literally, the generator test will reject the artifact's own required mapping, so generation and CI cannot succeed.

**Recommendation:** In Tests item 1 and Components item 2, replace `duplicate seat` with `duplicate station id`, and state: `Seat reuse across stations is valid; stationId keys must be unique and each stationId must map to exactly one seat.`

### 3. [High] completeness · high confidence — Tests item 3; Components item 4

> 3. `test_seat_routing_block_parses_and_is_defensive` (NEW) — extract the sentinel-delimited block,
>    `json.loads` it (proves valid JSON, not an executable fragment that could crash the tab), assert the
>    record schema (known enums, array chains, non-empty primary).

Despite its name, this test only parses JSON and validates schema; it never executes `renderRoutingPanel` with missing or malformed input. The claimed non-throwing fallback—the mitigation for the explicitly cited tab-crash failure mode—can be broken while every specified assertion remains green.

**Recommendation:** Add an executable renderer test that loads the diagram with missing, null, wrong-type, and partial routing records; assert that no exception or unhandled rejection occurs, the WF2 tab remains rendered, and `Routing metadata unavailable` is visible. Keep the JSON/schema test separately named `test_seat_routing_block_parses_and_has_valid_schema`.

### 4. [High] correctness · high confidence — Renderer (DOM-builder only)

> In `buildDetailSheet`, after the Gate Facts / Lane panels, add:
> ```js
> if(s.seat){
>   right.push(h('div',{class:right.length?'mt16':''}, panel('Executor Seat','seatp', [ ...h() nodes... ])));
> }
> ```
> Rendering seat name, status badge, placement, `model`, chain joined `a → b → c`, and the note.

The renderer sketch gates on the hand-authored `s.seat` field and never retrieves the generated record from `DATA.seatRouting.records` or calls the specified `renderRoutingPanel(record)`. It also names `model`, while the generated schema names the field `primary`. A literal implementation can therefore display undefined model data or omit the generated routing data entirely while the source-of-truth drift test still passes.

**Recommendation:** Replace the Renderer sketch with an explicit join: `const routingRecord = DATA.seatRouting?.records?.[s.id]; if (routingRecord) { right.push(h('div', {class: right.length ? 'mt16' : ''}, renderRoutingPanel(routingRecord))); }`. Specify that `renderRoutingPanel` renders `record.primary`, and add a browser-level assertion that a mapped station displays the primary and chain from the generated record.
**Ambiguity:** The artifact never defines how the station object, station ID, and separately keyed generated record are joined.

### 5. [High] internal-consistency · high confidence — Approach; Components item 5; Renderer; Files touched; Provenance

> Overview-node badge: **deferred** — the detail-modal panel satisfies AC1; a node badge is a cosmetic
> add that risks the overview layout on a HIGH-blast artifact. Note as a possible follow-up, not shipped.

This says the overview badge will not ship, while the selected Approach, Components item 5, Files touched, and Provenance all include the badge. Implementers and reviewers cannot determine the intended scope, tests, or snapshot changes without resolving the contradiction, creating a likely rework cycle.

**Recommendation:** Adopt the stated deferral consistently: remove `+ overview badge` from the selected Approach, delete Components item 5, remove `+ overview badge` from the `docs/workflow-diagram.html` file entry, and change Provenance to say `the overview badge was considered but deferred`. Add the exact sentence: `#447 ships only the detail-sheet routing panel; overview-node badges are out of scope.`
**Ambiguity:** Two mutually exclusive scope decisions are presented as final.

### 6. [Medium] feasibility · high confidence — Platform / external dependencies

> platform_apis: none
> 
> No material platform/external API: rendering uses the artifact's existing DOM builder
> (`h()`/`replaceChildren` — already-precedented throughout the diagram); the generator + drift-guard
> test import the in-repo `executor_routing_lib` (not a platform API); Playwright + `http.server` are
> dev/CI tooling for snapshot regen, not a shipped runtime dependency.

Calling Playwright and `http.server` development tooling does not eliminate their platform requirements. The artifact provides no cited project capability/manifest entry, exact existing object-kind call site, or spike proving that the configured browser, localhost binding, route navigation, and screenshot writes work in this project's actual CI or sandbox.

**Recommendation:** In Platform / external dependencies, list Playwright, its configured browser binary, localhost binding, and snapshot output writes as build-time dependencies. Cite an exact existing project call site or capability file for each, or add a pre-implementation spike that starts the server, loads `#/wf2/3.75.0`, asserts the routing panel is visible, writes both screenshots, and fails with explicit diagnostics on any unsuccessful step.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._