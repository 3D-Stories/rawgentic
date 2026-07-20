# Peer Consult — .rawgentic-peer-problem-447.md

- Date: 2026-07-20
- Reviewer: Codex (peer designer)

## Approach

Introduce an edit-time compiler for executor-seat display data, while keeping the SPA self-contained and DOM-builder-only. Maintain a small, machine-readable phase-to-seat mapping because routing config cannot determine placement. A focused script should call executor_routing_lib.resolve_table() (or its CLI-equivalent library path), join the resolved seat records with that mapping, derive static presentation classifications, and emit a deterministic JavaScript data block consumed by docs/workflow-diagram.html. The renderer should treat this block as optional validated metadata and add an “Executor Seat” section to each mapped WF2 station’s detail sheet. CI should regenerate the block in memory and compare it byte-for-byte or structurally with the committed block, so config changes cannot silently leave the diagram stale. This satisfies “from source-of-truth” more strongly than manually copying values plus a drift assertion: primary models and chains are generated outputs, while only the inherently prose-owned phase-to-seat placement is curated. Add compact overview badges for mapped stations, limited to seat and status; keep model and fallback details in the sheet.

## Key decisions

- Use generated embedded data, not runtime reads: the artifact remains a zero-request single-file SPA while model and fallback values originate from resolve_table().
- Keep phase-to-seat mapping in a small explicit manifest with stable station IDs and seat names. It is the only curated input because no authoritative machine-readable placement source exists.
- Represent status as a separate static display classification, not as resolve-seat output. Use an explicit enum such as executor-wired, competitive, and gate-flagged-bake-off, with build precedence over generic executor-wired membership and design precedence for competitive presentation.
- Name the UI field “Routing mode” or “Workflow classification,” and add concise explanatory copy distinguishing it from project runtime action such as inherit. This avoids implying that the diagram reports a resolved project override.
- Generate one normalized record per mapped station: stationId, seat, role, primary, chain, classification, placement, and optional note. Include config_digest and generator/version metadata once at the dataset level for provenance.
- Validate generated records before rendering: require known station IDs, unique station-to-seat assignments, recognized enum values, non-empty primary strings, array-valued chains, and valid optional notes. Fail closed during generation and degrade safely in the browser.
- Render a compact overview badge containing seat plus classification. Do not put fallback chains on overview nodes; that would increase visual noise and create layout instability.
- Treat the generated block as a committed derivative. CI runs the generator in check mode, confirms exact agreement with resolve_table(), verifies all mapped seats and stations, and retains the existing self-contained-SPA and no-innerHTML guards.
- Apply the complete REV protocol atomically: new REV, per-changed-station delta and references, superseded predecessor, plugin provenance, all three version surfaces, regenerated light/dark 1440px snapshots, and tests.

## Risks

- A manually maintained phase-to-seat manifest can drift from the prose placement document. Mitigate with explicit source references, exhaustive expected WF2 station coverage, duplicate/unmapped checks, and a test that makes placement changes intentional.
- Build belongs to WIRED_SEATS but also has bake-off semantics; design may likewise participate in broader wiring. Without precedence rules, status labels could conflict. Encode classification precedence in one generator function and test each exceptional seat.
- Embedding generated values inside the main station objects duplicates schema responsibilities and increases malformed-DATA risk. Keep routing metadata in a separate keyed dataset and join by station ID at render time.
- Top-level script evaluation can crash the tab if generated syntax is invalid. Serialize with a standard JSON encoder, avoid executable fragments, and test parsing of the extracted block independently.
- Renderer assumptions such as calling map or join on absent chain values can crash detail-sheet navigation. Normalize defaults, check types, and render an explicit unavailable state rather than dereferencing unchecked fields.
- Unknown stations or seats could silently disappear if lookup failures are tolerated. Make generation/CI strict, while keeping browser rendering defensive so a bad optional panel cannot take down the SPA.
- Long model identifiers and fallback chains can overflow the detail sheet or overview node. Use wrapping tokens, semantic lists, bounded badge content, and snapshot coverage at representative narrow and 1440px widths.
- A config digest shown without explaining its scope may be mistaken for runtime project resolution. Label it as the embedded routing-table revision and keep runtime inheritance out of the static classification panel.
- Generator introduction adds maintenance surface. Keep it narrowly scoped, deterministic, dependency-free beyond the existing routing library, and support write/check modes from the same implementation.

## Sketch

Inputs: placement manifest [{stationId, seat, placement, refs}] + executor_routing_lib.resolve_table(). Generation: validate routing table → index seats → join placement entries → derive classification with precedence build=bake-off, design=competitive, otherwise membership in WIRED_SEATS=executor-wired → emit normalized JSON and config_digest into the committed HTML data section. Browser: station lookup by ID → optional routing record → overview badge h('span', seat + classification) → detail panel built only with h()/text nodes, showing Seat, Routing mode, Placement, Default model, ordered fallback chain, and optional competitive/bake-off note. Safety: renderRoutingPanel(record) first normalizes strings/arrays and returns a harmless “Routing metadata unavailable” panel on malformed input; it never mutates station DATA or throws. CI: generator --check; schema and coverage tests; exact primary/chain comparison against resolve_table(); enum-precedence tests; malformed/missing metadata browser test; no-innerHTML and no-external-request guards; REV/version/provenance assertions; full-page light/dark snapshot regeneration.

---
_Peer proposal (report-only). Synthesize at your discretion._
