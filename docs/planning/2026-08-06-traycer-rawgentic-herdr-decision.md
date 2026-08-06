# Traycer × rawgentic × Herdr — decision analysis

**Switch — on a sequenced, spike-gated plan.** Owner decision 2026-08-06: Traycer's user interface is strongly preferred over Herdr's ("1,000× better"), which inverts the console-layer weighting this analysis first rested on. rawgentic survives the switch unchanged. The anchor of the migration is replacing the custom-built pane-handoff machinery with Traycer's native orchestration, and one go/no-go spike gates that: verified multi-KB prompt delivery into a running Claude Code session. The plan is §"The switch plan" below.

**Date:** 2026-08-06 · **Status:** decision document, report-only · **Author:** Claude Fable 5, owner-directed run
**Scope:** should this workspace switch to, combine with, or ignore [Traycer](https://github.com/traycerai/traycer)?
**Method:** three parallel inventory/research agents + first-hand verification of every load-bearing claim; live Claude Code docs for harness claims; cross-model Codex consult (see §Consult). Sources listed in §Sources.

```chips
Owner decision 2026-08-06: full-switch plan | done
Traycer Desktop/CLI/Host: MIT, verified first-hand | done
Codex peer consult: obtained | done
BYOA $0 permanence past 2026-08-31 | blocked
Delivery-semantics spike (go/no-go) | wip
```

---

## Recommendation (read this first)

```verdict
ship | Switch: adopt Traycer as the console and orchestration runtime on the sequenced plan below (owner decision 2026-08-06, UI preference decisive). rawgentic stays the process engine, unchanged.
risk | ONE go/no-go gate before any deep work: the delivery-semantics spike. If Traycer cannot verifiably deliver multi-KB prompts into a running Claude Code session, the switch stalls at console-only.
risk | Herdr is NOT retired until a full epic proves the Traycer transport. The herdr-dashboard inspector has no Traycer equivalent and gets its own disposition decision.
```

**Analysis provenance, stated plainly.** This document's first verdict (pre-owner-review) was "do not switch," resting partly on an *inferred* claim that the owner works terminal-first and would score Traycer's GUI as a loss. The owner falsified that inference on review: the UI preference runs strongly the other way, and the console is the surface touched all day. The facts below (matrix, gaps, effort, licensing) were not changed by that input — only the weighting was. The Codex consult (§Consult) advised the conservative track; the owner's decision supersedes its weighting, not its risk list, and its risks are folded into the plan's gates.

The three decision-critical findings:

1. **The overlap is at the runtime/console layer, not the process layer.** Traycer is an MIT-licensed orchestration app that plans, spawns coding agents (Claude Code included) in worktrees, and verifies results against plans. It has nothing resembling rawgentic's process engine: no cross-model pinned-reviewer gates, no security-scan gate, no TDD enforcement, no loop-back budgets, no run-record telemetry, no append-only decision/goal machinery. rawgentic rides INSIDE any Claude Code session, so it survives a Traycer adoption unchanged — which also means Traycer obsoletes almost none of it. *(Confirmed: capability matrix below, each cell cited.)*
2. **A switch kills the just-shipped handoff transport and the whole herdr-dashboard, and obsoletes only a thin slice of the open roadmap.** The pane-handoff chain (#927 merged 2026-08-06, `origin/main` 224ddace) and its verification ladder are herdr-coupled at the spawn/delivery layer; herdr-dashboard (10,386 source LOC + 17,274 test LOC, 1123-test suite) is a herdr pane by GATE C decision. Of the 7 open milestone epics, Traycer meaningfully overlaps only the herdr-transport slice of M2/M4 and the usage half of herdr-dashboard. M2.5, M3, M5, M6, M7 are untouched. *(Confirmed: §Q7 epic-by-epic walk.)*
3. **Traycer's one genuinely attractive, hard-to-replicate capability is multi-account-per-provider with in-app usage tracking** — several Claude/Codex accounts, choosing which account an agent uses, credit/usage UI. That lands squarely on this workspace's 5-hour-window pain (#586 resume rewrite, long-run-resume). It is the strongest single argument for the parallel trial. *(Confirmed: Traycer Host changelog via docs.traycer.ai/host/changelog.md; #586 scope from epic #871.)*

---

## The switch plan (owner-approved direction, 2026-08-06)

The anchor: **Traycer replaces the custom-built pane-handoff machinery.** What that means precisely — the handoff *contract* (three separately-verified turns, proven by the successor's own on-disk artifacts) is transport-agnostic and stays ours; Traycer replaces the *substrate* (pane split, agent start, send-text, the herdr argv layer in `launcher_lib.py`). Traycer natively provides the parts Herdr never had: task-scoped agent creation in worktrees, agent-to-agent messaging, session resume, lineage, notifications.

```steps
0 | Vet + pin | Supply-chain vet of all three Traycer components (host/desktop/cli), the #609 pattern: pinned versions, checksums, analytics audit (Sentry/PostHog off or built from source), BYOA local-only confirmed. | must
1 | Delivery spike (GO/NO-GO) | Prove Traycer can create a **Terminal Agent** (the real `claude` CLI in a PTY — NOT the Chat interface; rawgentic's hooks, plugins, session JSONL, and goal machinery exist only in the real CLI) and deliver the three handoff turns — bind, multi-KB prompt, /goal — each verified by the existing artifact ladder (registry line, goal_status attachment). Exercise busy-agent, duplicate-send, restart, timeout. Stop the plan here if submission cannot be proven deterministically. | must
2 | Console adoption | Daily-drive Traycer Desktop (BYOA) as the console while ALL existing machinery keeps running on Herdr. Zero rawgentic code changes. Independently valuable; fully reversible. | should
3 | Transport port | Add a `traycer` transport beside `pane_chain`/`inline` in `driver_lib`/`launcher_lib`, shaped by the consult's RuntimeTransport contract (probe/create/send-with-idempotency-key/status/notify/close, immutable IDs, no name-based cleanup). The pane-handoff and epic-run skills keep their invariants; only the backend changes. | must
4 | Proving run | One full epic on the Traycer transport against the Herdr baseline. Fallback asymmetry (consult ⊕): auto-fallback to Herdr only BEFORE successor creation; after partial creation, fail closed. | must
5 | Peripheral ports | Context-meter insert-prompt channel; pane_watch → Traycer notification hooks (URL/command); mid-turn-questions re-route. | should
6 | Retirement + dispositions | Retire Herdr only after step 4 passes plus stable weeks of operation. Undo stays cheap: the herdr pin, runbook, and transport survive in git history. herdr-dashboard: usage half superseded by Traycer's tracking; the inspector gets an explicit owner disposition (port, park, or run inside a Traycer terminal panel — its follow-me dock behavior does not carry over). | must
```

### The epic-run crossover, rationalized (owner input 2026-08-06)

The owner named the deepest overlap precisely: **Traycer's epic pane driving regular panes is the same shape as rawgentic's epic-run driving WF2 children.** The rationalization that keeps one owner per concern:

| Concern | Owner after the switch | Why |
|---|---|---|
| The driving *substrate* — spawn a child agent, deliver its prompt, watch it, continue | **Traycer** (epic pane + agent-to-agent: create child, message, read transcript, lineage tree; `TRAYCER_EPIC_ID`/`TRAYCER_AGENT_ID` env — confirmed, docs.traycer.ai/concepts/agent-to-agent.md, traycer-cli README) | This is exactly the layer #927's pane-chain hand-built on Herdr. Traycer has it natively, with a UI (the lineage tree makes an epic run *visible*, which nothing in the Herdr stack does). |
| The driving *session* — queue derivation, dependency parsing, merge policy, exactly-one-successor fence, revalidate-after-merge, the /goal contract | **rawgentic** — the epic pane simply hosts a Claude Code session running `/rawgentic:epic-run`; children are WF2-fresh sessions in Traycer-spawned panes | The driver's process semantics are plugin logic and survive unchanged (Q2). Only its transport enum gains a `traycer` member (plan step 3). |
| The queue's source of truth | **GitHub epic issues, unchanged** — Traycer's own Epic-mode tickets/specs stay OFF | Same anti-duplication rule as the planning layer: two queue stores would drift, and the whole revalidation discipline (#840, #944) is built on issue bodies. |
| Per-child process (WF2 gates, TDD, reviews, security scan, run-records) | **rawgentic**, inside each child session | Traycer's verification loop is advisory UX; the workspace's is enforced process. Nothing moves. |

Put in one sentence: **the epic pane is the rawgentic driver wearing a Traycer face — Traycer supplies the panes, the messaging, and the visibility; rawgentic keeps deciding what a child is, when it is done, and what the queue says.** Plan step 3 covers the epic-run transport port; step 4's proving run is precisely one epic driven this way.

### What the 2026-08-04 hands-on video adds (owner-supplied evidence)

The owner supplied a 28:20 hands-on ("Fable 5 & Qwen 27B — Traycer Multi-Agent Hands-On Test!", Bijan Bowen, uploaded 2026-08-04; **sponsored by Traycer**, disclosed at t=01:33 — weigh accordingly). A Sonnet 5 agent watched it at full fidelity; I re-read the two load-bearing frames myself. What it changes:

- **Confirms the epic-driving mechanic, and it matches rawgentic's own shape.** The orchestrator (Fable 5, Epic mode) does not inject into an existing pane — it **spawns a new titled child agent-node with a self-contained written brief** per work unit; on child failure it spawns a *fresh* child with a compact handoff brief and never resumes the dead one (t=19:48–19:55, frame-verified: "the old builder died before starting. Spawning a fresh builder... with a self-contained brief"). That is precisely #927's machine-generated-successor-prompt pattern and the no-blind-retry discipline — the integration shape for plan step 3 is "standalone brief per spawn," which rawgentic already produces.
- **Leaves the go/no-go gate OPEN.** Every demo runs through Traycer's own chat-card UI; the raw PTY transport under a Terminal Agent is never shown (the "Terminal Agents VS Terminals" doc page is visible in the nav at t=12:53 but never opened). The video is necessary context, insufficient evidence — step 1 stands unshrunk, and it must target Terminal Agents specifically.
- **Confirms the ticket surface is real.** The README's Collaboration bullet advertises "ticket assignment features directly in the workspace" (t=01:56, on-screen). The "Traycer tickets stay OFF" rule is an active avoidance requirement, not a hypothetical precaution.
- **Confirms Traycer verification is orchestrator self-audit, not independent review.** The demo's scorecard — including its own harness-failure taxonomy: *context exhaustion, silent turn-endings, harness swallowing* (t=22:16–22:53, frame-verified) — is written by the same model that ran the work. It must never substitute for WF2 Step 11 / WF5's cross-model pinned-reviewer gates (owner invariant 2026-08-01); it can complement them.
- **Reinforces keeping rawgentic's dead-agent discipline.** The demo's real failure case (child stalls, dies mid tool-call, failures swallowed without a surfaced retry) is exactly the class rawgentic's verified-kill / no-blind-retry / ERROR-protocol machinery exists for — that discipline ports into the Traycer transport unchanged.

**Effort for the full plan:** the Q6 MVP range (15–35 sessions) plus peripheral ports and retirement/UAT — roughly **20–45 focused sessions ≈ 4–9 calendar weeks**, same uncertainty drivers, dominated by steps 1 and 3. Steps 0–2 alone are ~3–6 sessions and deliver the UI win immediately.

**Roadmap consequence to decide at epic level:** the M2 remainder (#835, and the meter items' insert-prompt channel) and parts of M4 target Herdr-coupled machinery this plan replaces. Re-plan those queues once step 1 returns, not before — a failed spike leaves them all still valid.

---

## The armed /goal for this run (verbatim, per owner instruction)

```
Traycer decision analysis. Met when ALL true: (1) projects/rawgentic/docs/planning/2026-08-06-traycer-rawgentic-herdr-decision.md answers the 7 decision questions (intersection; combining rawgentic+Traycer via pane-handoff; herdr-dashboard uniques; post-switch gaps; MVP shape; effort range; other decision factors incl. licensing/lock-in/maturity/roadmap overlap) with confirmed-vs-inferred evidence; (2) the doc is rendered and deployed via design-doc-publish, live Vercel URL verified cache-busted OR the failure honestly documented after 2 attempts; (3) the md+html pair is committed by name on branch docs/traycer-analysis with a PR open against 3D-Stories/rawgentic main — do NOT merge; (4) the final in-chat summary leads with the recommendation plus the Vercel and PR URLs. If blocked, record it in the doc's "Open questions for the owner" section and continue — never hang the run.
```

---

## The four systems, one line each

| System | What it is | Layer |
|---|---|---|
| **rawgentic** | Claude Code plugin: 24 skills, ~30 hook modules, 5,487-test suite (campaign log, #943 slot). WF1/2/3/5/13 SDLC workflows with mandatory gates, cross-model review runner, security scan, epic driver, telemetry, goal/supervision machinery. | **Process engine** (inside the agent session) |
| **Herdr** | Third-party Rust terminal multiplexer for coding agents (herdr.dev; pinned v0.8.0, `hooks/herdr-pin.json`). Always-on server, real PTY panes, agent detection, socket API. AGPL-3.0 dual-licensed. | **Runtime/console** (around the agent session) |
| **herdr-dashboard** | In-house Python/Textual side-pane app: gated tools-off session inspector over headless `claude -p` + usage strip. 1123 tests, no CI, v0.1.0, never published. | **Companion console** (a herdr pane, by GATE C) |
| **Traycer** | Traycer AI's orchestration product: MIT open-source Desktop app + CLI + Host that plans, spawns BYO coding agents (Claude Code, Codex, OpenCode, Cursor, +12 more) in worktrees, verifies implementations against plans. Plus a separate closed(-inferred) VS Code extension. | **Planner + runtime/console** (around the agent session) |

## Capability matrix

Legend: ● = has it, first-class · ◐ = partial/adjacent · ○ = absent. Every ● claim is cited in the question sections below.

| Capability | rawgentic | Herdr | herdr-dashboard | Traycer |
|---|---|---|---|---|
| SDLC workflows with mandatory gates (TDD, review, security scan) | ● | ○ | ○ | ○ |
| Cross-model review, pinned reviewer identity | ● | ○ | ○ | ◐ agent-to-agent review possible, no enforced gate |
| Plan → execute → verify-against-plan loop | ◐ (Steps 5/9/11 verify AC + drift) | ○ | ○ | ● Verification mode, severity-tagged |
| Multi-agent parallel sessions | ◐ (subagents; epic driver is serial by design) | ● panes | ○ | ● parallel agents, Smart YOLO |
| Fresh-session-per-task boundary | ● epic-run pane_chain (#927) | ◐ (the substrate) | ○ | ◐ worktree-per-agent, fixed at launch |
| Git-worktree isolation per work unit | ● (per-dispatch; this doc was authored in one) | ● `worktree.*` API | ○ | ● Local / new / existing worktree per workspace folder |
| Session handoff chain with verified delivery | ● 3-rung/7-rung artifact ladder | ◐ (primitives only) | ○ | ◐ stores upstream session id for resume; clone-to-host = new agent |
| Agent-to-agent messaging | ◐ (peer-consult via runner; not free-form) | ◐ send-text primitives | ○ | ● first-class, lineage tree, capability matrix |
| Terminal-native (lives in a terminal, headless server) | n/a | ● | ● (Textual in a pane) | ○ GUI app; CLI exists but the panes live in the GUI |
| Remote/phone attach | n/a | ● SSH bridge | via herdr | ◐ cloud sync ($10/mo tier), device switch |
| Usage/limit tracking | ◐ run-records (per-run, not live) | via plugin (usagebar) | ● 5h/7d windows + per-project attribution | ● in-app credit/usage, multi-profile |
| **Multiple accounts per provider** | ○ | ○ | ○ (delegated to usagebar profiles) | ● incl. per-agent account choice |
| Gated, tools-off session inspector (approval gate, sandbox, redaction) | ○ | ○ | ● | ○ |
| Run telemetry as an append-only store | ● run_records.jsonl | ○ | ● (its own) | ○ (nothing documented) |
| Goal/Stop-hook contract machinery | ● | ○ | ○ | ○ |
| Supervision/away-mode (who is watching) | ● #943 Part A shipped | ○ | ○ | ○ |
| Blocked-agent notification | ● pane_watch → notify-owner | ● events | ○ | ● notification center + URL/command hooks |
| Open source | ● (private repo, own code) | ◐ AGPL + commercial | ● (own code, no LICENSE file) | ● MIT (Desktop/CLI/Host); extension inferred closed |

---

## Q1. What is the intersection between rawgentic, Herdr, herdr-dashboard, and Traycer?

**Traycer ≈ (Herdr's runtime role) + (herdr-dashboard's usage role) + (a planning/verification layer rawgentic already has in stronger, process-enforced form).**

- **Traycer ∩ Herdr — large.** Both are the *substrate around* coding agents: spawn agents, manage their sessions/panes, watch liveness, notify on stops. Traycer adds a plan/artifact layer and agent-to-agent messaging; Herdr adds terminal-nativeness, an always-on detachable server, and a same-user socket API this workspace already automates against (confirmed: `hooks/launcher_lib.py` builds herdr argv end-to-end; Traycer feature set from its README and docs.traycer.ai/concepts/*).
- **Traycer ∩ herdr-dashboard — the usage half.** Traycer Desktop 1.1.7 shipped multi-profile usage tracking for multiple Claude & Codex accounts (confirmed: Host changelog). That covers the dashboard's usage strip — and exceeds it on the account dimension. It does NOT cover the dashboard's other half, the gated session inspector (§Q3).
- **Traycer ∩ rawgentic — conceptual, not mechanical.** Traycer's Plan→Execute→Verify loop, Review mode, and Epic mode rhyme with WF2's plan/gates, WF5, and the epic driver. But Traycer's loop is advisory UX (severity-tagged review comments handed back to an agent), while rawgentic's is an enforced process (mandatory steps with named skip-refusals, budgets, fail-closed exits). Neither replaces the other: rawgentic runs *inside* the Claude Code session; Traycer runs *outside* it. (Confirmed: WF2 mandatory-step table `skills/implement-feature/SKILL.md:70-83`; Traycer verification semantics from docs.traycer.ai/extension/tasks/verification.md.)
- **Herdr ∩ herdr-dashboard — host and tenant.** The dashboard is a herdr pane (GATE C, 2026-07-31: "pane approach, permanently... no fork, no upstream ask", README.md:8) reading herdr via CLI + socket.

The four are two stacks, not four rivals: **process engine (rawgentic) × runtime console (Herdr + dashboard)** on one side, and Traycer as a vertically-integrated **planner + runtime console** on the other — with an empty slot where the process engine would be.

## Q2. Could rawgentic's functionality be combined with Traycer, using mechanisms like `/rawgentic:pane-handoff`?

**Yes — rawgentic itself needs no port at all; the handoff transport does.** Two separable facts:

1. **rawgentic runs inside Traycer as-is.** Traycer Terminal Agents run the real `claude` CLI in a PTY, and Claude Code loads plugins, skills, and hooks identically in interactive and `-p` modes ("Without `--bare`, `claude -p` loads the same context an interactive session would" — confirmed, live doc code.claude.com/docs/en/headless.md; hooks fire everywhere Claude Code runs — code.claude.com/docs/en/hooks.md). Custom CLI Agents accept custom argv/flags and Traycer injects `TRAYCER_AGENT_ID` / `TRAYCER_EPIC_ID` env (confirmed: docs.traycer.ai/extension/integrations/custom-cli-agents.md, clients/traycer-cli README via Context7). So every WF gate, WAL guard, the goal Stop-hook, and the supervision state work unchanged in a Traycer-hosted session. **One caveat marked inferred:** PTY-driven behavior differences (keystroke submission races, paste collapse) are NOT documented by Claude Code (confirmed absent from live docs) — herdr integration surfaced exactly this class of bug (`pane run` silently not submitting a 1,400-char prompt, rawgentic#696), and Traycer will have its own equivalents; what would confirm: a live spike sending a rawgentic-sized prompt through a Traycer terminal agent.
2. **The pane-handoff chain is portable in principle because its verification is transport-agnostic.** The chain's hard part — the 3-rung/7-rung ladder proving bind → prompt → goal each arrived — reads artifacts the *successor's own Claude Code* writes to disk (session-registry line, `goal_status` transcript attachment), not herdr state (confirmed: `hooks/launcher_lib.py:212-221`; `docs/runbooks/herdr.md` §7.4 "verification never from scraped pane text"). Only the spawn/delivery layer is herdr-specific (pane split, `agent start`, send-text + separate Enter, `agent_pane_busy` retry). Traycer exposes the analogous surface: `traycer worktree create`, agent create/message, transcript read, NDJSON `--json` output, `CI`/`TRAYCER_NONINTERACTIVE` (confirmed: docs.traycer.ai/cli/commands.md, read first-hand). A `traycer` transport backend beside `pane_chain`/`inline` in `driver_lib`/`launcher_lib` is the natural shape — the #927 design even anticipates transports as a probed, pluggable enum (confirmed: `hooks/driver_lib.py:1611-1622`).

So: combining is feasible and the architecture is already shaped for it. Whether it is *worth* building is Q5/Q6's question.

## Q3. Is there anything in herdr-dashboard that Traycer does not do?

**Yes — the entire session-inspector half, and the security posture around it.** Nothing in Traycer's docs, README, or changelogs matches:

- **The gated, tools-off Q&A inspector**: ask questions about any running agent's live transcript via a headless `claude -p` thread that is *denied all tools* (`--tools ""`, empty MCP config), with answers marked as provenance-limited (confirmed: `qa_engine.py:1645-1661`, inspector provenance note). Traycer's agent-to-agent transcript read is the adjacent capability, but the reader there is another full agent, not a tools-off sandboxed inspector.
- **The capsule approval gate**: digest-bound, one-use, TTL'd modal confirmation showing the *exact bytes* leaving the machine, with redaction counts and cost already spent (confirmed: `qa_engine.py:221-260, 901-929`). No Traycer equivalent documented.
- **bwrap confinement** of the inspector subprocess (tmpfs `~/.claude`, allowlisted env, refuse-if-missing; confirmed: `confinement.py`, #37) and the **preflight canary** proving the sandbox actually blocks tools before user data moves (confirmed: `qa_engine.py:2022-2052`).
- **Per-project token attribution including closed panes**, recovered per-transcript-line from Claude Code's own JSONL cross-referenced with rawgentic's session registry, with an honest `unattributed` row (confirmed: `project_usage.py`). Traycer tracks usage per profile/account — nothing documented at per-project granularity recovered from provider transcripts.
- **Terminal residency**: the dashboard is a follow-me pane inside the terminal multiplexer; Traycer's panels live in its GUI app.

What Traycer *does* cover: the usage strip's 5h/7d windows (and better: multi-account). If the workspace ever adopted Traycer, herdr-dashboard's usage half is superseded; its inspector half has no replacement.

## Q4. What functionality gaps would exist after a direct switch to Traycer, running rawgentic('s workflows) inside it?

Assume: Herdr retired, Traycer Desktop hosts all agent sessions, rawgentic plugin unchanged inside them.

**Survives unchanged (confirmed reasoning per Q2):** all 24 skills, all hooks/gates (WAL, security-guard, context-meter emission, goal machinery, supervision state), mempalace (Claude Code-level MCP config is untouched — Traycer's remote-only MCP limit applies to *Traycer's* platform integrations, not to Claude Code's own `--mcp-config`), telemetry, the epic driver's `inline` transport.

**The gaps:**

| # | Gap | Severity | Mitigation |
|---|---|---|---|
| 1 | **pane-handoff / mid-child-handoff dead** — spawn+delivery is herdr argv (`launcher_lib.py` builders, `:386-545`). Epic-run degrades to the `inline` single-session transport (a designed, visible fallback — `skills/epic-run/SKILL.md:211-215`) — losing the fresh-context-per-child boundary that D176 made load-bearing. | **High** | Build the `traycer` transport (Q5). |
| 2 | **Context-meter's mid-turn insert-prompt channel dead** (`context_meter.py:1017-1050` shells `launcher_lib insert-prompt --pane`). The meter still *emits*; the forced-handoff acting path breaks. | High | Same port; or accept advisory-only meter. |
| 3 | **pane_watch blocked-agent watcher dead** (polls herdr snapshot — `pane_watch_lib.py`). | Medium | Traycer's notification hooks ("call a URL or run a command, filtered by severity" — Host changelog) are a plausible, likely *better* replacement. Inferred; a spike would confirm. |
| 4 | **herdr-dashboard dead entirely** (a herdr pane by GATE C; dock, follow-me, inspector, attribution). Biggest sunk-cost casualty: 10,386 + 17,274 test LOC. | High | Usage half → Traycer's tracking. Inspector half → no replacement (Q3). |
| 5 | **Terminal-native operation lost.** Herdr is a headless server whose panes survive lid-close/SSH; Traycer Desktop is a GUI app (native installers; its CLI manages the Host but the working surface is the GUI). Phone/SSH attach → replaced by $10/mo cloud sync, a different trust model. *Correction (owner input 2026-08-06): the severity here was calibrated on an inferred terminal-first preference the owner rejected — the GUI is preferred. The detach/SSH survival question stays real and is checked in switch-plan step 0.* | ~~Medium–High~~ Low (severity re-scored by owner input) | Cloud sync, or Host-on-server. |
| 6 | **mid-turn-questions workspace skill dead** (spawns a sibling herdr pane). | Low | Re-route via Traycer agent-create, or answer inline. |
| 7 | **Supply-chain posture resets.** herdr is pinned by sha256 with a vet doc (`hooks/herdr-pin.json`, #609); Traycer would need the same vetting from zero, on a 3-component (host/desktop/cli) weekly-RC release train. | Medium | Repeat the #609 process. |
| 8 | **Workspace muscle memory + runbooks** (`docs/runbooks/herdr.md`, 631 lines of measured behaviors) all stale. | Medium | Rewrite against Traycer; the herdr one took weeks of live falsification. |
| 9 | **CLAUDE.md vs AGENTS.md**: Traycer's own planner reads AGENTS.md, not CLAUDE.md (confirmed: docs.traycer.ai/extension/tasks/agents-md.md). Claude Code inside still reads CLAUDE.md, so this only matters if Traycer's planning layer is actually used. | Low | Symlink/duplicate if needed. |

**Not gaps (checked and cleared):** goal arming (Claude Code-level), session resume across processes (documented, cwd-scoped — code.claude.com/docs/en/sessions.md), plugin loading, run-records.

## Q5. What would an MVP look like that brings Traycer, rawgentic, and the Herdr functionality together inside Traycer?

**Shape: keep rawgentic as the process engine, adopt Traycer as the runtime, port one transport.**

**IN (the MVP cut):**
1. **Traycer Desktop on BYOA ($0, local-only)** hosting Claude Code as a Terminal Agent; supply-chain vet + pin first (the #609 pattern).
2. **A `traycer` transport backend** in `launcher_lib`/`driver_lib` beside `pane_chain`/`inline`: spawn successor via `traycer worktree create` + agent create; deliver the three turns (bind / prompt / goal) via the CLI agent-message surface; verify with the *existing* on-disk artifact ladder (registry line below offset, `goal_status` attachment) — unchanged by design.
3. **Transport probe extension**: `transport resolve-creation` learns to answer `traycer` when the Traycer host responds, keeping #927's probed-not-asserted rule.
4. **pane_watch replacement** wired to Traycer notification hooks (URL/command).
5. **One epic proving run** on the new transport (the #559-style gate herdr got) before anything herdr-coupled is retired.

**OUT (deliberately):**
- Traycer's planning layer (Plan/Phases/Epic/YOLO modes) — WF2 owns planning; running both would double-plan every issue.
- Traycer cloud sync, credits, Traycer-provider inference — BYOA only; no egress change.
- Porting the herdr-dashboard inspector — stays parked; decide separately.
- Retiring herdr during the MVP — both runtimes coexist; herdr remains the fallback exactly as tmux did during herdr's own adoption (the workspace has run this playbook before).
- Multi-account adoption (#586 territory) — attractive, but a follow-on, not MVP.

```callout
warn | Riskiest integration assumption
That Traycer's CLI agent-message surface can deliver a multi-KB prompt into a running interactive
Claude Code session and have it **verifiably submit**. This exact seam was herdr's hardest bug class
(#696 unsubmitted paste; `agent_pane_busy` races; Enter-as-separate-send), the Claude Code docs are
silent on PTY-driven input semantics (confirmed NOT DOCUMENTED, live docs), and Traycer's docs
describe agent messaging for *Traycer-managed* automation without byte-size or submission-semantics
guarantees. Spike this first; if it fails, the MVP collapses to "Traycer as console for ad-hoc work only."
```

## Q6. How much effort to reach that MVP?

**Range: roughly 15–35 focused working sessions ≈ 3–7 calendar weeks at this workspace's current cadence — longer if the delivery-semantics spike fails or Traycer's weekly RC train breaks the pin mid-build.** A single number would be false precision; the drivers of uncertainty:

| Driver | Range impact | Why |
|---|---|---|
| Delivery-semantics spike (the Q5 risk) | go/no-go, not hours | If agent-message cannot verifiably submit large prompts, the transport port is dead on arrival. |
| Transport backend port | 8–20 sessions | The verification ladder reuses; spawn/delivery/retry-taxonomy is new. Calibration: herdr's equivalent spanned #611→#927 over ~6 weeks, but most of the generic machinery (ladders, transport enum, fail-closed dispositions) now exists and was the majority of that time. |
| Supply-chain vet + pin | 1–2 sessions | #609 pattern, but ×3 components (host/desktop/cli). |
| pane_watch → notification hooks | 1–3 sessions | Simple if the hook payload carries agent identity; unknown until read. |
| Proving run + UAT | 2–5 sessions | One full epic on the new transport, per the herdr precedent. |
| Traycer churn | +0–30% schedule | Weekly RCs across 3 components; no stated back-compat guarantee found (inferred from release cadence; their protocol docs weren't deep-read). |
| Single-owner review bandwidth | pacing, not size | Every PR here is owner-merged. |

Excluded from the range: porting the dashboard inspector (add ~2–4 weeks if wanted), multi-account adoption, herdr retirement.

## Q7. What else is decision-relevant?

**Licensing & pricing.**
- Traycer Desktop/CLI/Host: **MIT** — confirmed first-hand (raw LICENSE fetched: "MIT License, Copyright (c) 2026 Traycer AI"; GitHub API `license: MIT`). Forkable, vendorable, no copyleft. The VS Code **extension is absent from the OSS repo** — inferred closed-source; what would confirm: an explicit Traycer statement.
- Pricing (confirmed first-hand from docs.traycer.ai/account/pricing): BYOA **$0** local-only; Sync $10/user/mo; Lite/Pro/Ultra $20/$40/$100 with credits. **Driving Claude Code consumes no Traycer credits and adds no markup.** Caveat: the marketing page says BYOA "Free till 31st Aug" — BYOA's permanence past 2026-08-31 is **unverified**.
- Herdr comparison: AGPL-3.0 + commercial dual license (`2026-07-21-herdr-console-plan.md:179-184`) — already analyzed as no-obligation for CLI/socket use. MIT is strictly more permissive.

**Lock-in & data egress.**
- BYOA is local-only by definition (no cloud sync). Privacy Mode: prompts/code not persisted when ON (default for teams, opt-in individual). **Sentry crash reporting and PostHog analytics "may be enabled in release builds"** (confirmed: repo README) — a build-from-source or opt-out check belongs in the vet. Agent traffic goes direct to the provider (no Traycer proxy) on BYOA paths.
- Structural lock-in is low: MIT source + the rawgentic-side port living in *our* transport enum means the exit is "delete one backend," same as tmux→herdr was.

**Maturity & community.**
- Traycer AI, Inc.: founded 2024 (Tanveer Gill, ex-FluxNinja CTO), ~10–20 people (sources conflict on countries), funding **contradictory across sources** (Tracxn "unfunded" vs a LinkedIn convertible-note entry + Flex Capital portfolio listing) — unresolved. Repo public since ~2026-06-25 (Desktop launch), 1,100 stars (API-confirmed), last commit hours old, weekly RC cadence, 132 open issues. 40,860 VS Code Marketplace installs (live count); the 240K Open VSX claim is vendor-stated, unverified.
- Herdr for contrast: 18.9k stars, single dominant author, ~weekly releases, already vetted + pinned here (0.8.0), 333k installs claimed on herdr.dev.
- Both are young single-vendor risks; herdr's is a risk *already paid for* (vet, pin, runbook, falsified behaviors). Traycer's would be bought new.

**Roadmap overlap — what a switch obsoletes vs leaves untouched** (all 7 open epics walked, bodies read):

| Epic | Verdict under a Traycer switch |
|---|---|
| #906 M2 pane-handoff chain (10 open children) | **Partially obsoleted/reshaped**: #835 (herdr paste recovery) dies with the transport; #797/#729/#734 (meter) survive minus the insert-prompt channel; #864/#772/#878 (goal reader), #806, #923, #899 survive untouched. |
| #871 M4 session continuity (5 open children) | **Mostly survives**: #769/#726/#944/#947 are process logic. #586 (resume rewrite) **meaningfully overlaps** Traycer's session-resume + multi-account — the one place Traycer would *reduce* open work. |
| #935 M2.5 telemetry truth | Untouched. |
| #936 M3 runner/review/config | Untouched. |
| #937 M5 identity & concurrency | Untouched (worktree-per-agent mildly helps #594's problem). |
| #938 M6 tail | Untouched. |
| #939 M7 skills & tooling | Untouched. |

**What already-built work survives a switch:** the entire rawgentic plugin (all skills, hooks, 5,487 tests), mempalace, decision logs, goal machinery, run-records, the transport *architecture* (#927's enum + artifact ladder). **What dies:** launcher_lib's herdr argv layer, pane_watch_lib (1,546 lines), herdr-pin + vet, the herdr runbook, mid-turn-questions, the herdr workspace skill, and herdr-dashboard's shipped whole (its inspector concepts could be re-hosted later, at new cost).

**The strategic asymmetry, plainly:** this workspace spent June–August making Herdr boring — vetted, pinned, runbooked, falsified, and now load-bearing (D176). A switch re-buys that layer and re-opens the race-condition classes that were just closed, and the process layer gains nothing from it. *That cost is real and stands — the owner weighed it against the daily-driver UI preference and chose to pay it (Recommendation, 2026-08-06). The switch plan's step-1 gate and Herdr-as-fallback rule exist precisely to keep this cost bounded while it is paid.*

---

## Consult (cross-model, WF13)

**Obtained.** A Codex peer consult (backend `gpt`, reviewer `gpt-5.6-sol`, via `/rawgentic:peer-consult` → `hooks/review_runner.py consult`, exit 0, one attempt) ran on this draft; the full proposal was written to `docs/reviews/peer-2026-08-06-traycer-rawgentic-herdr-decision-2026-08-06.md` (retained locally — `docs/reviews/` is gitignored in this repo, so the report rides no PR; its substance is folded below). The peer **independently converges on the same recommendation**: no switch, Herdr stays the production runtime, Traycer at most an opt-in experimental transport behind a go/no-go delivery spike, multi-account evaluated as a separate capability purchase.

Points from the consult now folded into this doc (marked ⊕ = consult-sourced):

- ⊕ **Transport contract first (Phase 0):** before any spike code, specify a `RuntimeTransport` contract — `probe() / create(worktree, argv) / send(agent_id, payload, idempotency_key) / status() / notify_target() / close()` — with immutable runtime IDs, structured errors, bounded retries, and **no name-based cleanup**. This generalizes the #927 transport enum into an interface a third backend can be held to.
- ⊕ **Fallback asymmetry:** during any proving run, fall back to Herdr automatically **only before successor creation**; after a partial creation, fail closed and require identified recovery — never auto-fallback around a half-created agent/worktree.
- ⊕ **Coexistence risk named:** running two runtimes adds routing/diagnosis complexity and can produce **ambiguous ownership of sessions and worktrees**; the adapter must map Traycer agent/worktree IDs into run-records so ownership stays attributable.
- ⊕ **Multi-account second-order risk:** per-agent account choice could complicate billing attribution, session resume, and the **pinned-reviewer identity guarantee** (an account switch mid-run must not change which model reviewed) — a reason to spike multi-account separately rather than bundling it into the MVP.
- ⊕ **Planning-authority conflict:** if Traycer's own Plan/Verification modes were enabled alongside WF2, the workspace would have two planners with unclear authority; the peer recommends explicitly disabling them, which this doc's OUT cut-line already does — upgraded here from a scope choice to a named risk.

## Open questions for the owner

1. **Is BYOA-permanence worth watching?** The "Free till 31st Aug" line makes Traycer's $0 tier a promo, not a contract. If the trial appeals, the pricing re-check on 2026-09-01 is the first gate.
2. **Does multi-account-per-provider justify a scoped spike on its own** (independent of any switch), given #586? Traycer's Host could conceivably be used *only* for account brokering while herdr stays the console — unexplored, possibly silly, noted for completeness.
3. **Should the herdr-dashboard inspector's approval-gate pattern be written up** as a standalone design note before any runtime decision? It is the one artifact here with no external equivalent.

## Follow-ups (no issues filed — D179 issue throttle)

- Run the delivery-semantics spike (Q5 risk) *only if* the owner opts into the parallel trial.
- Re-check BYOA pricing after 2026-08-31.
- Reconcile the herdr version skew found during inventory: pin says 0.8.0 (`hooks/herdr-pin.json`), while the workspace herdr SKILL.md, `pane_watch_lib.py:22`, and mid-turn-questions target 0.7.5 — a doc-rot fix, unrelated to Traycer.

## Sources

```provenance
artifact | docs/planning/2026-08-06-traycer-rawgentic-herdr-decision.md
peer consult | gpt-5.6-sol, exit 0 — report local-only (docs/reviews/ is gitignored)
traycer repo | github.com/traycerai/traycer @ 2026-08-06 (MIT, 1,100 stars, API-confirmed)
measured | 2026-08-06
branch | docs/traycer-analysis (cut from origin/main 224ddace)
```

**Video evidence:** "Fable 5 & Qwen 27B — Traycer Multi-Agent Hands-On Test!" (youtube.com/watch?v=f0Y3tQp2OqQ, Bijan Bowen, 2026-08-04, 28:20, **Traycer-sponsored** per its own disclosure at t=01:33) — watched at full fidelity by a Sonnet 5 agent (44 frames + native captions); the two load-bearing frames (fresh-child handoff t≈19:55, failure-mode scorecard t≈22:45) re-read first-hand.
**First-hand (this session):** raw LICENSE + GitHub API for traycerai/traycer; docs.traycer.ai/account/pricing; docs.traycer.ai/cli/commands.md; herdr.dev; epic bodies #756/#871/#906/#935/#936/#937/#938/#939 via `gh`; `docs/planning/2026-08-03-756-rationalization-roadmap.md`; `docs/planning/2026-07-21-herdr-console-plan.md`; campaign-log head; spot-checks of `driver_lib.py:1611`, `herdr-pin.json`, `pane-handoff/SKILL.md:18-20`, `epic-run/SKILL.md:57-66`, `launcher_lib.py:5481`.
**Agent-gathered, spot-verified:** Traycer web sweep (48 URLs, incl. docs.traycer.ai extension/concepts/host pages, marketplace listing, third-party comparisons — full list in the research agent's SOURCES READ, retained in session transcript); rawgentic + Herdr integration inventory (file:line cited throughout); herdr-dashboard inventory (file:line cited throughout); live Claude Code docs (headless.md, hooks.md, sessions.md, agent-sdk/overview.md).
**Known-unverified items are marked inferred inline**; the research agent's COULD-NOT-VERIFY list (Discord size, roadmap board contents, extension license, funding, Open VSX count) carries into this doc's claims wherever cited.
