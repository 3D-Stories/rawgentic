# WF2 per-step detail

Reading contract: read the matching section (§N) before executing Step N.
The spine (SKILL.md) carries the one-line-per-step overview and the always-run
protocols; this file carries the full per-step instructions plus the
step-semantic blocks they depend on (`<small-standard-lane>`,
`<trivial-work-check>`, `<learning-config>`).

---

<small-standard-lane>
The small-standard lane is a **middle gear** between the `<trivial-work-check>` exit and the
full 16-step spine. It is a **semantic replacement** of the old Step-4-only fast path,
generalized to the whole spine: a 3–5-file UI feature or a 2-file hardening guard genuinely
needs a **code review** but not a **pre-implementation design panel**. The lane is cheaper on
**design ceremony**, never on **review or security**.

**Canonical predicate: `small_standard_lane_eligible`.** It replaces the old `fast_path_eligible`,
which is kept as a **deprecated alias**:

    fast_path_eligible = small_standard_lane_eligible

so every existing "Step-4 self-review vs. critique" reader keeps working unchanged — for that one
decision the lane still selects the self-review, so old Step-4-only callers see identical behavior. New
code reads the canonical name. The flag now controls the **whole lane** (Steps 3/4/5/6/9
collapse), not just Step 4.

**Eligibility — `small_standard_lane_eligible == true` when ALL hold:**
- complexity ∈ {`simple_change`, `standard_feature`} (never `complex_feature` — that is always the full spine), AND
- **changed implementation source files ≤ 7** (`LANE_MAX_IMPL_FILES`) — the SAME counting rule `plan_lib.count_impl_files`/`lane_decision` apply: count non-test, non-doc **source** files the change creates/modifies; **exclude** test files (`test_*`, `*_test.*`, `tests/`), docs (`*.md`, `docs/`), and generated/lockfiles; a rename counts as **1**. Test+doc files are excluded because a small feature legitimately touches several without being "big." **Markdown-is-product opt-in (#143):** for a prompt/skill repo whose *product* is markdown (rawgentic itself — `skills/*/SKILL.md`), a `.md`-only change would otherwise count as ~0 impl files and always slip under the ceiling. Set `laneImplExtensions` in the project's `.rawgentic.json` (e.g. `[".md"]`) and `count_impl_files` counts those extensions toward the ceiling — a `docs/` dir still stays docs (genuine project docs are never product), and tests stay excluded. Default (unset) = current behavior, so app repos never regress. AND
- no architecture change, no migration, no new cross-service surface, no new dependency (the signals Step 2 already gathers), AND
- not `trivial_work` (that has its own exit at `<trivial-work-check>`, which takes precedence).

This SUPERSEDES the old two-branch rule (simple_change unconditionally + standard_feature only if
WF1-validated): a non-WF1 standard_feature of bounded size is exactly the field case the lane
exists for. WF1 origin still *strengthens* confidence but is no longer required.

**Decision call (mechanical — mirrors how Step 8 invokes `select_impl_model`).** Pass the Step-2
authoritative complexity, the Step-2 ESTIMATED impl-file count (via `count_impl_files` over the
estimated changed-file list from Step 2 item 1), and the arch/migration/dep/trivial booleans
Step 2 gathered:
```bash
python3 -c "import sys,json; sys.path.insert(0,'hooks'); from plan_lib import lane_decision, count_impl_files, lane_impl_extensions; cfg=json.load(open('<activeProject.path>/.rawgentic.json')); exts=lane_impl_extensions(cfg); n=count_impl_files([<estimated changed file list>], impl_extensions=exts); t,r=lane_decision('<complexity>', n, <has_arch_change>, <has_migration>, <has_new_dep>, <is_trivial>); print(t); print(r)"
```
`lane_impl_extensions(cfg)` reads the optional `laneImplExtensions` markdown-is-product config
(#143; empty by default → the historical exclude-`.md` behavior). Pass the SAME `impl_extensions`
to the Step-9 `count_impl_files` reconcile so entry and reconcile count identically.
`lane_decision` returns `(tier, reason)` with tier ∈ {`trivial`, `full`, `lane`}. **`tier == "lane"`
→ `small_standard_lane_eligible = true`**; `trivial` defers to `<trivial-work-check>`; `full` runs
the whole spine. Log the tier + reason in session notes.

**Secondary signal — bounded multi-defect election (#225).** When the Step-2 analysis
identifies the change as **2..`MAX_LANE_DEFECTS` (3) separately-understood, bounded defects**
(e.g. the same fix pattern across native-core + host + frontend) with no architecture
change, pass their per-defect impl-file estimates (same `count_impl_files` exclusions,
including the same `impl_extensions` from `lane_impl_extensions(cfg)` — #143) as
`defect_file_counts=[...]` on the `lane_decision` call. The lane is then electable even when
the TOTAL exceeds 7, provided each defect is ≤ `LANE_MAX_IMPL_FILES` AND the total is ≤
`MAX_LANE_DEFECTS × LANE_MAX_IMPL_FILES` (21) — a multi-defect change is lane-eligible iff
each defect independently would be, with a hard aggregate ceiling. Malformed or over-bound
counts fail closed to the full spine, and the returned reason enumerates the per-defect
counts verbatim so an implausible split is reviewable.

**Operator override (#225).** When the operator judges an over-cutoff change bounded, the
surfacing block below gains choice **(c) Force lane** — re-call `lane_decision(...,
operator_override=True)`. In an **unattended** run there is no interactive choice: set the
per-run env `RAWGENTIC_WF2_FORCE_LANE=1` to elect it (precedent: `RAWGENTIC_EPIC_GOAL`, the
per-run signal pattern; the wiring is prose-enforced at this call site —
`lane_decision` stays pure); otherwise unattended auto-resolve stays conservative (full).
**The architecture-change / migration / new-dependency guards force the full spine
regardless — neither the secondary signal nor the operator override can bypass them (and a
complex_feature classification is likewise unbypassable: re-tag it, don't force it).**

**Sanctioned-count handoff (#225).** An elected-lane run (secondary signal or override) logs
its **sanctioned expected impl-file count** in the lane marker — the Step-2 estimate N it was
elected at — so Step 9's cross-check compares the real diff against the sanctioned figure,
not the ordinary 7. An override or secondary-signal lane election is logged with its reason verbatim — never silent.

**Input-source honesty.** `lane_decision` is a pure, unit-tested function, but at Step 2
(pre-implementation) `file_count` is an **estimate** from the Step-2 component map — there is no
diff yet. So lane eligibility is "mechanically decided **given** the Step-2 estimates," not fully
mechanical end-to-end. Guard: **Step 9 cross-checks the actual changed-file count** (see Step 9's
lane cross-check); on a material overshoot it records a `lane-widened` note — it does NOT
retroactively fail. A deterministic pre-diff detector is the AC5 follow-up.

**Surfacing (suggested-never-silent; mirrors `<trivial-work-check>`).** When
`small_standard_lane_eligible`, **or** the tier came back `full` on file-count alone with all
hard guards passed (the #225 operator-override case), and the lane is not already forced or
declined, STOP and present:
```
Step 2 → SMALL-STANDARD detected (<N files, complexity>). Recommend the small-standard lane:
  keeps TDD + code review + security scan + CI; skips the design panel + drift gates.
  (a) Small-standard lane  [recommended]
  (b) Full WF2 (design panel + all gates)
  (c) Force lane (operator override — offered only in the tier=="full"-on-file-count-alone
      case; re-calls lane_decision with operator_override=True)
```
This is a **suggestion, never a hard gate** — the orchestrator must NOT silently pick the lane;
continuing the full workflow is always valid. In an **unattended** run there is no interactive
user, so AUTO-RESOLVE the lane-vs-full choice: take the lane for eligible changes and the full
spine for `complex_feature`; the tier=="full"-on-count-alone case stays full unless the per-run
`RAWGENTIC_WF2_FORCE_LANE=1` env elects the override (see the Operator override paragraph).
Log the choice in session notes.

### Keep / collapse table (the contract)

| Step | Full WF2 | Small-standard lane | Why |
|---|---|---|---|
| 3 Design | inline 1-2 approaches + doc | **brief design note** (file list + failure modes + security), no multi-approach brainstorm | small work has one obvious approach |
| 4 Design critique | **quality-bar rubric** + peer consult + opt-in adversarial-on-design | **quality-bar rubric only** — NO peer consult, NO adversarial-on-design | #190 retired the same-model multi-judge design panel from WF2; cross-model scrutiny is the opt-in adversarial-on-design (full spine) |
| 5 Plan | full task decomposition + drift-ready fields | **checklist plan**: ordered tasks, each with `riskLevel` + a verification line; parallel_group/files optional | keeps TDD + risk tagging; drops ceremony |
| 6 Plan drift | self-review + optional adversarial-on-plan | **SKIP** (folded — the checklist is small enough to eyeball; Step 9 still verifies AC coverage) | a 3-task checklist has no drift surface |
| 8 / 8a | TDD; 8a for high-risk tasks | TDD kept; **8a still fires for any `riskLevel: high` task** — as the ONE accumulated wave (#492), timing changed, coverage not | security surface never loses review coverage |
| 9 Impl drift | self-review (Part A) + evidence (Part B) | **evidence-only**: run the suite, record the delta, verify each AC has a covering test; skip the alignment self-review | evidence is the real gate |
| 11 Code review | 2-agent (#492) | **≥1 reviewer** — the single lane reviewer takes the security/strong seat (the security lens is never the one dropped, #492) + the opt-in diff adversarial sub-step (#131) still applies | **NON-NEGOTIABLE — this is where the value is** |
| 11.5 Security scan | full | **UNCHANGED** | tool gate never skipped |
| 12/13/14 PR/CI/merge | full | **UNCHANGED** | |
| 16 run-record | full | **UNCHANGED shape**, `complexity` reflects lane; add `lane: "small-standard"` marker | lane runs stay measurable vs full |

**Exact retained vs. removed gates** (no vague "every safety gate"):
- **RETAINED (unchanged):** TDD red-green (Step 8), Step 8a per-task review for any `riskLevel: high` task, Step 11 code review (≥1 reviewer, the single lane reviewer on the security/strong seat) + the #131 opt-in diff adversarial sub-step, Step 11.5 security scan, CI (Step 13), PR + merge (Steps 12/14), run-record (Step 16).
- **COLLAPSED:** Step 3 (brief note, no multi-approach brainstorm), Step 4 (quality-bar rubric only — no peer consult, no adversarial-on-design; WF2's Step 4 uses the same rubric on the full spine too, so the lane differs only by dropping the opt-in cross-model layers), Step 5 (checklist plan, keeps riskLevel + verification), Step 9 (Part B evidence only — Part A alignment self-review removed).
- **REMOVED entirely:** Step 6 (plan drift).

The RETAINED set is non-negotiable: Step 11 caught 2 Criticals on a run judged "too simple to
review." **Step 11 (code review) and Step 11.5 (security scan) are never traded away in the lane.**
</small-standard-lane>

<trivial-work-check>
Some changes are below even `simple_change` — genuinely **trivial**: a typo, a
comment, a one-line guard, a version/string/constant tweak, a doc-only edit. Running
the full 16-step workflow (and especially the multi-agent reviews) on these costs far
more than the change is worth. This check surfaces that BEFORE the workflow invests in
design, planning, and review.

**Trigger (evaluated in Step 2, after complexity classification):** set
`trivial_work = true` only when ALL hold:
- 1 file (occasionally 2), and roughly ≤ 10 changed lines
- no new logic / control flow / public surface, no new dependency, no migration
- mechanical or cosmetic, low reversal cost (a wrong edit is trivially reverted)
- nothing that warrants its own test *design* (a one-line regression test is fine; the
  change does not need TDD ceremony to get right)

This is a **suggestion, never a hard gate** — the orchestrator must NOT bail on its own,
and continuing the full workflow is always a valid choice.

**When `trivial_work == true` (interactive):** STOP and present, concisely:
```
Step 2 → TRIVIAL detected (<N files, ~M lines, <one-line why>).
The full WF2 (16 steps) is likely overkill for this. Proceed how?
  (a) Do it directly now — quick edit + a targeted test + branch + PR  [recommended]
  (b) Continue the full WF2 workflow
```
Wait for the choice.
- **(a) Do it directly:** LEAVE the workflow. Make the change with the project's
  baseline hygiene only — branch off the default branch, add a targeted test if one is
  warranted, run the suite, bump the version + update docs per the project's pre-PR
  checklist, open a PR — but SKIP the design critique (Step 4), plan + drift gates
  (Steps 5–6, 9), per-task + multi-agent reviews (Steps 8a, 11), and the run-record
  ceremony (Step 16). If you do emit a run-record, set `complexity: "trivial"`.
- **(b) Continue:** proceed to Step 3 as normal (valid when the user wants the full
  audit trail regardless of size).

This is distinct from `<small-standard-lane>`: the lane makes a *non-trivial* change
cheaper (collapses design ceremony, keeps review + security) while staying in the workflow;
the trivial-work check asks whether running the workflow is warranted *at all*.
</trivial-work-check>

<learning-config>
If this workflow discovers new project capabilities during execution (e.g., a new test framework, a previously unknown service), update `.rawgentic.json` before completing:
- Append to arrays (e.g., add new test framework to testing.frameworks[])
- Set fields that are currently null or missing
- Do NOT overwrite existing non-null values without asking the user
- Always read full file, modify in memory, write full file back
</learning-config>

---

### Delegated reads (#314)

Concept: Anthropic "plan big, execute small" cookbook
(https://github.com/anthropics/claude-cookbooks/blob/main/managed_agents/CMA_plan_big_execute_small.ipynb)
— context isolation: the coordinator never touches token-heavy raw material. The canonical
rule: **A raw artifact whose measured size exceeds its surface's byte threshold never
enters the orchestrator's context.** A deterministic reduction (a runner summary, a gate
field, a grep of failure lines) is read as a **mechanical projection**; a reduction that
needs judgment would be produced by an analysis-role reader subagent as a **validated index**.
**The reader returns material (an index), never a decision; design, plan, gate verdicts,
and finding evaluation stay orchestrator-side** — and **every decision is made from raw
bytes via targeted reads** (the index only says where the bytes are; index prose is never
evidence).

**Ship scope (#314, owner decision 2026-07-09 — option 3):** only the **mechanical
projections** are wired this release (Steps 8, 9, 11.5, 13 below). The **validated-index
reader path** (the `step11-diff` and `step2-map` LLM readers) is **BUILT but NOT WIRED**:
`plan_lib.validate_index` and its suite ship as dormant infrastructure, and the byte
thresholds / temp-artifact / staleness rules below are its spec for when it wires. The A/B
experiment (`docs/planning/2026-07-08-314-ab-results.md`) settled quality in the index
arm's favor across three rounds, but the LLM reader costs +25–71% more tokens one-shot with
no implementation that removes it; its only remaining benefit is an unmeasured held-context
("carry") saving. Wiring the readers is gated on the AC4 production carry measurement —
until then they stay dormant.

**Live this release — mechanical projections:**
- **Projection validation (fail-closed):** capture the producing command's exit status;
  when the source reports failure the projection must contain non-empty failure
  identifiers; **an empty, malformed, or command-failed projection falls back to the
  inline raw read**, logged and counted like a rejected index. The CI-log projection's
  threshold is `WF2_READ_DELEGATE_BYTES_LOG` (default 32768) — env-tunable, clamped, frozen
  at import in `hooks/plan_lib.py`.

**Deferred — validated-index reader path (built, not wired):**
- **Trigger:** measure with a PIPED byte count (`git diff … | wc -c` — bytes never enter
  context). Thresholds: `WF2_READ_DELEGATE_BYTES_DIFF` (default 65536) for diffs,
  `WF2_READ_DELEGATE_BYTES_LOG` (default 32768) for logs/scan output — env-tunable,
  clamped, frozen at import in `hooks/plan_lib.py`. Under threshold ⇒ inline exactly as today.
- **Index validation:** every reader return is a hypothesis. Validate with
  `plan_lib.validate_index(index, expected_units, artifact_text)` — closed schema,
  set-equality coverage against the unit list the dispatcher FED the reader
  (`git diff --name-only` for step11-diff: a completeness proof; component ids for
  step2-map: a drop-guard only — discovered entries legitimately exceed the fed hints),
  verbatim-evidence verification (fabricated quotes reject), patch-shape and
  truncated/vacuous rejection. Rejection ⇒ **inline fallback, logged in session notes and
  counted in the run-record** — fail-open for HOW material is read, never for WHETHER a
  gate runs.
- **Temp artifacts:** `.rawgentic-read-<issue>-<token>.*` under the project root, mode
  0600, appended to the SAME stale-sweep globs and `.git/info/exclude` discipline as the
  Step 11 item 1a patch files. Immediately after writing each artifact run two fail-loud
  post-creation asserts: `stat -c %a <file>` must print `600`, and
  `git check-ignore -q <file>` must exit 0 — either failing aborts the delegated read
  (inline fallback + loud log).
- **Staleness:** re-derive `source_ref` before consuming an index (HEAD unchanged for a
  diff; HEAD sha for the step2-map). Mismatch ⇒ regenerate or read inline.

---

