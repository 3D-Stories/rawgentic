# Design — render each WF phase's executor seat + default model in the workflow diagram (#447)

**Issue:** #447 (W-child of epic #475). **Blast radius:** HIGH — `docs/workflow-diagram.html`
is the official REV-managed, pytest-guarded single-file SPA; a DATA-shape bug crashes a tab
(the #337 failure mode, invisible to normal pytest until the guard).

## Goal

Show, per WF2 station that maps to an executor seat: the seat's **default model**, **fallback
chain**, and **wired-status** (executor-wired / competitive / gate-flagged bake-off), sourced
from the #445 config source-of-truth rather than hand-hardcoded.

## Confirmed source-of-truth (verified directly)

- `WIRED_SEATS` = the full 7 `{intake,analysis,design,plan,build,review,ship}`
  (`hooks/executor_routing_lib.py:53-54`, pinned by `tests/hooks/test_executor_routing.py:491`).
  **The #447 body's `{intake,plan,ship}` is stale (pre-#464)** — render off the real 7.
- Seat→model→chain: `phase_executor/.../routing/rawgentic.routing-table.json`, resolved by the
  single function `executor_routing_lib.resolve_table()` (#445 AC2).
- Renderer feed: `python3 hooks/executor_routing_lib.py show-table --workspace <ws> --project rawgentic --json`
  → `{seats:[{seat,role,primary,chain}], build_bake_off, build_bake_off_note, config_digest}`.
- **Wired-status is a static classification** (executor-wired ∈ `WIRED_SEATS`; competitive ∈
  `COMPETITIVE_ONLY={design}`; gate-flagged bake-off = `build`), NOT the per-project `resolve-seat`
  runtime action — rawgentic has no `executorRouting` block, so `resolve-seat` returns `inherit`
  for every seat and *errors* on `design` (competitive-only). Rendering off `resolve-seat` would
  show "inherit" everywhere, contradicting AC1. Render off `show-table` + the frozensets.
- Phase→seat mapping is **prose-only** (no machine-readable manifest):
  `docs/planning/2026-07-17-wf2-wf3-executor-seat-placement.md`. Hand-authored in the diagram,
  cited in the delta.

## Approach (selected — synthesis of my draft + the cross-model peer consult, `docs/reviews/peer-rawgentic-peer-problem-447-2026-07-20.md`)

**A: edit-time generator (`--write`/`--check`) + machine-readable phase→seat manifest + a separate
keyed `DATA.seatRouting` dataset + a defensive renderer panel & overview badge.** (Chosen.)

The peer consult's key correction to my first draft: a generator satisfies AC2's literal "values
render from the config source-of-truth" more strongly than manual-copy-plus-drift-assertion — the
primary models and chains become *generated outputs*, and only the inherently prose-owned phase→seat
placement is curated. Its second correction avoids the HIGH-blast tab-crash risk: keep routing
metadata in a **separate keyed block** joined by station id at render time, NOT woven into each
station object (no schema duplication inside the big steps array, one delimited block for the
generator to emit/verify).

Alternatives weighed and rejected:
- **B: hand-place values + a drift-guard test** (my first draft). Functionally close (the test
  catches drift) but the values are still hand-copied; AC2's repeated "render from … not
  hand-hardcoded" wording and the peer both favor generation. The generator IS the drift guard
  (its `--check` mode), so B buys nothing A doesn't.
- **C: rewrite the 287KB DATA blob wholesale.** Rejected — fragile on a HIGH-blast artifact. The
  separate-block design confines generation to one sentinel-delimited region.
- **D: docs-only note.** Rejected — fails AC1.

### Components

1. **Phase→seat manifest** (the one curated input — no machine-readable placement source exists).
   A Python constant `PHASE_SEAT_MAP` in the new hook `hooks/diagram_seat_data.py`, keyed by WF2
   station id → `{seat, placement, refs}`, with the placement-doc citation. Stable station ids.
2. **Generator** `hooks/diagram_seat_data.py` — pure `build_seat_dataset(table_projection, phase_seat_map)`
   returning normalized records `{stationId, seat, role, primary, chain, classification, placement, note}`
   + dataset-level `{config_digest, generator_version}`; classification via one precedence function
   `classify_seat(seat)`: **build → bake-off**, **design → competitive**, else **executor-wired**
   (∈ `WIRED_SEATS`). Thin CLI `write`/`check`: reads the authoritative projection
   (`executor_routing_lib.resolve_table` / `table_projection`), emits the JSON between HTML sentinels
   `/*SEAT-ROUTING-START*/ … /*SEAT-ROUTING-END*/`; `check` compares committed vs regenerated and exits
   non-zero on drift. Generation validates fail-closed: **stationId keys must be unique and each
   stationId maps to exactly one seat; seat reuse ACROSS stations is valid** (the `review` seat maps to
   stations 4, 8a, 11 by design), recognized enums, non-empty primary, array chains.
3. **Separate DATA block.** Emit the literal `DATA.seatRouting =` on the line **immediately before**
   `/*SEAT-ROUTING-START*/`; place **only** the JSON object (a plain object literal, `JSON`-encoded,
   no executable fragments) **between** the sentinels; place the terminating `;` on the line
   **after** `/*SEAT-ROUTING-END*/`. The drift/parse tests slice and `json.loads` **only the text
   between the sentinels**, so that slice must be pure JSON. Shape:
   `{ provenance:{config_digest, generator_version}, records:{<stationId>:{stationId,seat,role,primary,chain,classification,placement,note}} }`
   (no wall-clock/`generatedAt` field — it would make `--check` non-deterministic).
4. **Defensive renderer** `renderRoutingPanel(record)` (DOM-builder only): normalizes strings/arrays,
   returns a harmless "Routing metadata unavailable" panel on malformed input, never throws or mutates
   station DATA. Panel titled **"Routing mode"** (not "wired-status") with copy distinguishing the
   static workflow classification from a project's runtime `inherit`/`executor` action. Shows: seat,
   classification, placement, default model, ordered fallback chain, optional competitive/bake-off note.
   Rendered in `buildDetailSheet`'s right column, only for the newest rev (seat routing is a
   current-state annotation, not part of a historical spine snapshot).
5. **Overview badge** (peer): a compact `seat · classification` badge on each mapped overview node —
   seat + classification only, NO chains (avoids layout instability). Adds AC1 visibility without a drill-in.

### Station → seat mapping (hand-authored, cited)

| WF2 station | seat | status | placement |
|---|---|---|---|
| 1 Receive Issue | intake | executor-wired | Executor |
| 2 Analyze & Classify | analysis | executor-wired | Agent tool |
| 3 Design Solution | design | competitive | Executor (competitive) |
| 4 Gate · Design Critique | review | executor-wired | Executor (design-critique) |
| 5 Implementation Plan | plan | executor-wired | Executor |
| 8 Implementation | build | bake-off | Agent tool (worktree) |
| 8a Per-Task Review | review | executor-wired | Hybrid |
| 11 Pre-PR Code Review | review | executor-wired | Hybrid |
| 12 Create PR | ship | executor-wired | Session / Agent tool |

The mapping has **9 stations across the 7 distinct seats** (`WIRED_SEATS` in full; the `review` seat
intentionally maps to 3 stations — 4, 8a, and 11). Stations with no executor seat (Goal Guard, Plan Drift, Memorize, CI,
Merge & Deploy, Post-Deploy, Summary) get **no** entry in `DATA.seatRouting.records` — driver/session
work, correctly absent.

### Renderer join (DOM-builder only) — resolves the station↔record join explicitly

The station DATA is **not** annotated with a `seat` field; the routing record is looked up from the
separate `DATA.seatRouting.records` block **by station id** at render time, and rendered via the
defensive `renderRoutingPanel(record)`. In `buildDetailSheet`, after the Gate Facts / Lane panels
(newest rev only — `r.ver === w.revs[0]`):
```js
const routingRecord = (r.ver === w.revs[0]) && DATA.seatRouting && DATA.seatRouting.records
  ? DATA.seatRouting.records[s.id] : null;
if (routingRecord) {
  right.push(h('div', {class: right.length ? 'mt16' : ''}, renderRoutingPanel(routingRecord)));
}
```
`renderRoutingPanel(record)` renders `record.seat`, `record.classification`, `record.placement`,
`record.primary` (the default model — the schema field is **`primary`**, not `model`), and
`record.chain` joined `a → b → c`, plus the optional note. It first normalizes strings/arrays and
returns a harmless "Routing metadata unavailable" panel on missing/malformed input; it never throws
or mutates station DATA. No `innerHTML` (`test_no_innerhtml_rendering`).

**Overview badge (SHIPS in #447).** `nodeBadges(s)` gains a compact `<seat> · <classification>` badge
(seat + classification only — no chains, no layout risk), looked up from
`DATA.seatRouting.records[s.id]` and gated to the newest rev exactly like the panel. AC1 visibility
without a drill-in.

## REV plan (spine change → REV required)

- New WF2 rev key = the version this PR bumps to (feat → minor: **3.74.0 → 3.75.0**), added
  newest-first to `revs` + a matching `versions["3.75.0"]` entry carrying the **full** steps array
  (required real-array for `revs[0]` — `test_every_ordered_workflow_has_nonempty_default_steps`).
- Copy 3.40.0's steps verbatim; the seat data does NOT go on station objects (it lives in the
  separate `DATA.seatRouting` block). Mark each of the 9 mapped stations with
  `rev:{delta:"Executor seat + default model / fallback chain / routing mode", refs:["#447"]}`
  so the revision triangle + delta panel render on exactly the stations that gained the routing panel.
- Set `versions["3.40.0"].superseded = "3.75.0"`.
- Provenance footer → `@ plugin 3.75.0`.
- Regen `docs/assets/workflow-diagram-{light,dark}.png` (full-page, 1440px, device scale 2×) via
  localhost + Playwright at `#/wf2/3.75.0`.

## Platform / external dependencies

platform_apis: none

No material platform/external API in the **shipped runtime**: rendering uses the artifact's existing
DOM builder (`h()`/`replaceChildren` — already-precedented throughout the diagram); the generator +
drift-guard test import the in-repo `executor_routing_lib` (not a platform API).

**Build-time dependencies (snapshot regen only — adversarial F6):** `python3 -m http.server` (localhost
bind) + Playwright (headless Chromium, route nav to `#/wf2/3.75.0`, screenshot writes to `docs/assets/`).
Feasibility is **precedented, not assumed**: this exact localhost+Playwright recipe is documented at
`docs/workflow-diagram.md:65-81` and the currently-committed `docs/assets/workflow-diagram-{light,dark}.png`
are its prior output (existing-call-site evidence). Surface (fail-loud): the Step-9 Playwright smoke
navigates the new rev, asserts the routing panel renders, and writes both screenshots, failing with
explicit diagnostics on any unsuccessful step — so a broken regen surfaces at Step 9, not post-merge.
These are dev/CI tooling, never a shipped runtime dependency, so the runtime declaration stays `none`.

## Error handling / failure modes

- DATA-shape bug crashing a tab (#337): mitigated by copying the known-good 3.40.0 steps array and only
  ADDING a field; `test_every_ordered_workflow_has_nonempty_default_steps` + a manual browser smoke of
  the new rev tab before PR.
- Config drift: the new drift-guard test fails CI if the diagram values diverge from the routing table.
- Stale issue body (WIRED_SEATS): render off the verified 7; flag the correction in the PR.

## Security implications

None new — static content addition + a read-only test. No user input, no new external surface,
no secrets. DOM-builder rendering preserves the no-innerHTML security-hook contract.

## Tests (red-before-green)

1. `tests/hooks/test_diagram_seat_data.py` (NEW) — unit tests for the generator lib:
   `build_seat_dataset` produces one record per manifest station with primary/chain verbatim from a
   fixture projection; `classify_seat` precedence (build→bake-off, design→competitive, else
   executor-wired) each exceptional seat; fail-closed on unknown station id / **duplicate stationId
   key** / bad enum / empty primary / non-array chain. **Seat reuse across stations is explicitly
   asserted VALID** (`review` on 4/8a/11 must generate cleanly). `write`→`check` **idempotence**:
   after a `write`, `check` exits 0 and everything OUTSIDE the sentinels is byte-for-byte unchanged
   (self-review Finding #2). Red before the lib exists.
2. `test_seat_routing_block_matches_source_of_truth` (NEW, in `test_workflow_diagram.py`) — the AC2
   drift guard: runs `diagram_seat_data.py check` (imports and compares) and asserts the committed
   `DATA.seatRouting` block equals the freshly-generated one from `resolve_table()`. Red before the block
   is written; CI's "not hand-hardcoded" enforcement.
3. `test_seat_routing_block_parses_and_has_valid_schema` (NEW, renamed per adversarial F3) — slice the
   text **between** the sentinels, `json.loads` it (proves the between-sentinels slice is pure JSON, not
   the `DATA.seatRouting =` assignment or an executable fragment that could crash the tab), assert the
   record schema (known enums, array chains, non-empty primary, unique stationId keys).
4. `test_render_routing_panel_is_defensive` (NEW, adversarial F3 — the actual defense test) — a
   **static CI guard-pin** (matching the repo's Python-string-pin idiom for all diagram behavior):
   assert `renderRoutingPanel` normalizes inputs and contains the `"Routing metadata unavailable"`
   fallback and no unguarded `.map(`/`.join(` on possibly-absent fields, so the fallback cannot be
   silently deleted. The **runtime** proof (no exception, tab stays rendered, fallback visible on
   missing/null/wrong-type/partial records) is a **Step-9 Playwright smoke** injecting malformed
   records via `browser_evaluate` — recorded as the dynamic verification (the repo has no JS-execution
   test harness in CI; a node test would not run under pytest — named honestly, not a silent gap).
5. `test_seat_panel_absent_on_old_rev` (NEW, self-review Finding #1) — assert the renderer gates the
   panel + badge to `w.revs[0]`, so a historical-rev view shows no current-routing overlay.
5b. `test_manifest_station_ids_match_diagram` (NEW, verifier Medium) — parse the station `id`s from the
   new rev's `steps[]` in the diagram DATA and assert every `PHASE_SEAT_MAP` station id is a member
   (subset). Closes the silent-no-render risk: if a manifest id diverges from a real station id, the
   join `DATA.seatRouting.records[s.id]` returns null and NO panel/badge renders while every other test
   stays green — this pins the join resolvable in CI, not only at the one-time Step-9 Playwright smoke.
6. `test_diagram_newest_rev_matches_plugin_version` — stays green (new rev 3.75.0 == bumped plugin).
7. `test_every_ordered_workflow_has_nonempty_default_steps` — stays green (new rev carries a real steps array).
8. Existing no-innerHTML / self-containment / theme / snapshot guards — stay green.

## Files touched

- `hooks/diagram_seat_data.py` (NEW — manifest + generator + `write`/`check` CLI)
- `docs/workflow-diagram.html` (new rev 3.75.0 DATA with rev markers on 9 stations + `DATA.seatRouting`
  block between sentinels + `renderRoutingPanel` + `nodeBadges` seat badge + footer)
- `tests/test_workflow_diagram.py` (4 new guards: source-of-truth drift, block-parses-schema,
  render-panel-defensive static pin, seat-panel-absent-on-old-rev), `tests/hooks/test_diagram_seat_data.py` (NEW)
- `docs/workflow-diagram.md` (recipe note: the seatRouting block + its generator + source-of-truth)
- `docs/config-reference.md` (optional: cross-ref that the diagram renders the seat table)
- `docs/assets/workflow-diagram-{light,dark}.png` (regenerated snapshots)
- `.claude-plugin/plugin.json`, `plugins/rawgentic/.codex-plugin/plugin.json`,
  `tests/hooks/test_adversarial_review_registration.py` (version ×3 → 3.75.0)
- `README.md` (changelog entry, exact shape + diagram REV decision + Suite delta)

## Adversarial-review dispositions (Step 4 cross-model, gpt/Codex — `.rawgentic-adv-findings-447.json`)

One spec-tightening loop-back consumed (`spec_tighten` 1/2, global 1/3). All findings ADOPTED:
- **F1 (Critical, JSON-vs-assignment):** ADOPTED — `DATA.seatRouting =` before START sentinel, JSON only
  between, `;` after END; parse/drift tests slice only between-sentinels. (Components 3, Renderer join, Tests 3.)
- **F2 (Critical, duplicate-seat validation vs the mapping):** ADOPTED — uniqueness is on **stationId**;
  seat reuse across stations is valid and asserted so. (Components 2, mapping note, Tests 1.)
- **F3 (High, "defensive" test doesn't test defense):** ADOPTED — parse test renamed
  `..._has_valid_schema`; a static defensive guard-pin (`test_render_routing_panel_is_defensive`) in CI +
  a Step-9 Playwright malformed-input smoke as the runtime proof (repo has no CI JS-exec harness — named honestly).
- **F4 (High, renderer gates on `s.seat`/`model`, no join):** ADOPTED — explicit join by station id,
  `renderRoutingPanel(record)`, field is `primary`; the stale `s.seat` section removed. (Renderer join.)
- **F5 (High, badge ship-vs-defer contradiction):** ADOPTED the SHIP decision (peer-recommended, cheap,
  AC1 visibility) — all "deferred" leftover text removed. Both F4/F5 ambiguities were self-inflicted
  by an incomplete first-draft edit (author-introduced contradiction = #223 spec-tightening) and are
  resolvable from the design intent + ACs — not owner decisions, so resolved here, not escalated.
- **F6 (Medium, Playwright feasibility):** ADOPTED — build-time deps listed with existing-call-site
  evidence (prior committed snapshots) + a fail-loud Step-9 smoke surface; runtime `platform_apis: none` stands.
- **Self-review #1/#2:** old-rev-no-panel test + generator write→check idempotence test folded into Tests.

## Provenance

Design synthesized from my own draft + the cross-model peer consult (gpt/Codex backend),
`docs/reviews/peer-rawgentic-peer-problem-447-2026-07-20.md`, then hardened against the Step-4
adversarial review. Adopted from the peer: the generator + `--check` over a manual drift-assertion, the
separate keyed dataset, the defensive renderer + "Routing mode" naming, and the overview badge. Retained
from my draft: the confirmed source-of-truth map, the station→seat mapping, and the REV plan.
