# Spike #654 — Test Plan

**Written** 2026-07-27. **Version pins:** Claude Code **2.1.220**, herdr **0.7.5**.
Re-verify every binary-derived finding on any Claude Code upgrade.

This is the test plan the owner asked for ("need to actually test it and need a test plan").
It is a *runnable* plan: each test names the question it settles, the exact commands, the
pass criterion, the cost, and — deliberately — what it still cannot tell you.

Spike #654 is investigation-only. **Nothing in this plan implements anything.** Every test
either reads state or uses a temporary, env-gated rig that is removed afterwards.

---

## 0. Status ledger — what is already settled

Do not re-run these. They are recorded so the plan does not re-derive them.

| # | Question | Verdict | Evidence |
|---|---|---|---|
| Q2 | Can a goal be set with no human? | **YES** | `claude -p "/goal X"` writes a `goal_status` transcript attachment; `claude --resume` restores an unmet goal (status line `◎ /goal active`). Issue comment `5093356529`. |
| Q3 | Keystroke-free restart primitives | **No missing primitive** | Interactive `claude "/goal …"` under a pty auto-submits and arms the guard. Same comment. |
| — | herdr spawn composition, composed | **PROVEN end to end** | `pane split --cwd <trusted>` → `pane run <new> exec <wrapper>` → `Goal set` → `✔ Goal achieved`. |
| — | Folder-trust hypothesis | **CONFIRMED, and pre-seedable** | `~/.claude.json` → `projects["<abs path>"]["hasTrustDialogAccepted"] = true`. Comment `5094541802`. |
| **O1** | **Does a PreCompact block reason reach the model?** | **YES — settled 2026-07-27** | See §0.1. |
| **O2** | **Is vetoing compaction safe (manual trigger)?** | **YES — settled 2026-07-27** | See §0.2. |

### 0.1 O1 — the block reason DOES reach the model (CONFIRMED)

Run live in throwaway herdr pane `w1:pAH` with `RG_PRECOMPACT_TEST=1`, using the env-gated rig
`~/.claude/hooks/precompact-block-probe.py` (exit 2 + reason on stderr).

The reason is delivered to the model as **`<local-command-stderr>` attached to the `/compact`
command message**. Proof that it reached *the model* and not merely the UI: the pane's agent,
told to answer from context with no tool call, quoted the unguessable marker back —

```
● Yes. It arrived as <local-command-stderr> attached to the /compact command message:
  ▎ Compaction blocked by PreCompact hook: [python3 …/precompact-block-probe.py]:
  ▎ RGPROBE-MARKER-9471 PreCompact veto fired. trigger=manual. …
  Marker code: RGPROBE-MARKER-9471, trigger=manual.
```

**Design consequence:** D3 is no longer constrained. PreCompact can carry the whole instruction
itself; it does not have to degrade to "set a flag and let the Stop hook do the talking".

**Bonus confirmation for D3's safety requirement:** the payload's **`trigger` field is present
and correctly valued** (`trigger=manual` for an owner-typed `/compact`). The "never hijack a
manual compact" rule in D3 is therefore implementable exactly as written.

### 0.2 O2 — repeated manual vetoes do NOT wall the turn (CONFIRMED, bounded)

Same pane. 5 `/compact` submissions; ≥3 distinct block events directly observed, each followed
by a genuine assistant reply (`● VETOALIVE-2`, `● VETOALIVE-4`, `● VETOALIVE-5`), pane ending
`idle` and responsive. No `stopHookBlockingCount`-style cap was hit.

**Scope of this result — read it narrowly.** It covers `trigger=manual` on a session at ~6%
context. It says **nothing** about the case the design actually depends on: an *auto* compaction
vetoed at real context pressure, where nothing else is relieving the window. That is **T4**, and
it remains the single most likely thing in this spike to be wrong.

---

## 1. What this plan still has to settle

| Test | AC | Question |
|---|---|---|
| T1 | AC1 (Q1) | Statusline bridge reading, matched against `/context` for the same session |
| T2 | AC1 (Q1) | Transcript fallback reading, from a session that renders **no** statusline |
| T3 | AC4 | The real auto-compact trigger percentage, on a 1M window **and** a 200k window |
| T4 | AC4 / O2-auto | Is vetoing an **auto** compaction under real context pressure safe? |
| R1 | AC5 | End-to-end restart rehearsal composing D2+D3+D4 |

AC5's recommendation and issue decomposition are written from T1–T4 + R1; they are not
themselves tests.

---

## 2. Standing rules for every test here

1. **Throwaway panes only.** Never the owner's own pane. Create with
   `herdr pane split --current --direction right --no-focus`, read `result.pane.pane_id` from
   the JSON, `herdr pane close <id>` when done.
2. **Every rig is env-gated and removed.** The pattern that worked: the hook exits 0 immediately
   unless its own `RG_*` env var is `1`, so arming it in `~/.claude/settings.json` cannot affect
   any live session. Back up `settings.json` first; `diff` against the backup after removing the
   entry and require *identical*.
3. **Strip `HERDR_*` from spawned children** unless you want the child reporting itself as the
   agent for the *parent's* pane (recorded trap, comment `5093356529`).
4. **No repo changes.** These tests touch `~/.claude/` and `/tmp` only.

### 2.1 Two herdr gotchas that will produce wrong results

Both cost time on 2026-07-27; neither is obvious.

- **`herdr wait …` does not exist in 0.7.5.** The skill documentation describes
  `herdr wait agent-status`, but the installed binary has no top-level `wait` command — only
  `herdr pane wait-output`. This is a live instance of the drift #659 tracks. Poll
  `herdr pane get <id>` for `agent_status` instead.
- **`pane wait-output --match` matches the prompt echo, not the reply.** The pane transcript
  contains your own submitted prompt, so waiting for a canary string you just *sent* returns
  success immediately and reads as a pass. This nearly produced a false "ALL ROUNDS SURVIVED"
  in the O2 run. **Always confirm against the assistant-output marker** — grep the transcript
  for `^● <canary>`, not for the bare canary.
- Related: `pane read --source recent-unwrapped` returned empty in this environment while
  `--source recent` and `--source visible` worked. Prefer `recent`. The `recent` buffer is a
  rolling render (~47 lines observed), **not** a full transcript — counts taken from it are
  lower bounds, so read incrementally rather than tallying once at the end.
- `pane run <id> "<text>"` put text in the composer without submitting; an explicit
  `pane send-keys <id> Enter` was required. Budget for it.

---

## 3. T1 — statusline bridge reading (AC1, authoritative path)

**Question.** Does `context_window.used_percentage`, as delivered to the statusLine command,
match what the session itself reports via `/context`?

**Why it matters.** This is the primary meter. If it drifts from `/context`, every threshold
built on it is wrong.

**Method.**

1. Back up the statusline script:
   `cp ~/.claude/rawgentic-statusline.sh ~/.claude/rawgentic-statusline.sh.bak-t1`
2. The script already does `input=$(cat)`. Immediately after that line, add a tee that is
   inert unless armed:
   ```bash
   [ "${RG_CTX_PROBE:-}" = "1" ] && printf '%s\n' "$input" >> /tmp/rg-ctx-probe.jsonl
   ```
3. Spawn a throwaway pane with `--env RG_CTX_PROBE=1`, launch `claude`, and give it enough
   turns to move off 0% (any few prompts).
4. In that pane run `/context` and read the reported percentage from the pane.
5. Read the last line of `/tmp/rg-ctx-probe.jsonl` and extract
   `.context_window.used_percentage`, `.context_window.context_window_size`, and `.session_id`.
6. Restore the statusline script from the backup; `diff` and require identical.

**Pass criterion.** `|used_percentage − /context percentage| ≤ 1` percentage point, **and** the
payload's `session_id` equals the probe session's id (not another pane's — the statusline runs
for every session, so the JSONL will interleave).

**Fail meaning.** If they disagree by more than 1pp, the statusline figure is measuring
something other than what the user sees, and the meter must key on the transcript path instead.

**Cost.** ~10 minutes, one throwaway pane, no tokens beyond a few short turns.

**What it cannot tell you.** Nothing about headless — the statusline never renders there. That
is T2, and it is why T2 is required rather than a nice-to-have.

---

## 4. T2 — transcript fallback reading (AC1, headless path)

**Question.** Does summing the last `message.usage` row reproduce the true in-context total for
a session with no statusline?

**Method.**

1. **Validate the formula against a known-good reading first.** On the *same* session used in
   T1, locate the transcript by `session_id` and compute
   `input_tokens + cache_read_input_tokens + cache_creation_input_tokens` from the **last**
   `message.usage` row. Compare to T1's `used_percentage × context_window_size`.
2. Only once the formula validates, apply it headless: run
   `claude -p "<short prompt>"` in a trusted directory, capture the emitted session id, and
   compute the same sum from that transcript.

**Pass criterion.** Step 1 agrees with T1 within **2 percentage points**. Step 2 yields a
non-zero reading resolved **by `session_id` / `transcript_path`**.

**Hard requirement, already burned once.** Resolve the transcript by `session_id`, **never by
newest mtime** — that error reported 138k against a true 538k (recorded in the issue). A test
that picks the newest file can pass by luck on a quiet machine and fail silently under the
concurrent sessions this workspace always has.

**Cost.** ~10 minutes.

**What it cannot tell you.** Whether the sum stays correct *across* a compaction — the usage
rows after a compact describe the compacted context. If the meter must survive a compact, add
a step reading one row either side of a real compaction.

---

## 5. T3 — the real auto-compact trigger percentage (AC4)

**Question.** At what percentage does auto-compaction actually fire, on a 1M window and on a
200k window? The ~77% figure in the issue was measured on 200k and is **explicitly unverified**
for 1M — treat it as unknown, not as a default.

**Why not just fill a 1M window.** Because that costs a fortune in tokens and hours. Use the
window-size knob instead.

**Method.**

1. Arm a **logging-only** variant of the rig — same env gate, but it writes the payload and
   exits **0** (no veto), so the compaction proceeds and you observe the natural trigger point:
   ```python
   # exits 0 always; only records
   sys.stderr.write("")            # no reason — do not block
   open("/tmp/rg-precompact-log.jsonl","a").write(raw + "\n")
   sys.exit(0)
   ```
2. Spawn a throwaway pane with `--env CLAUDE_CODE_MAX_CONTEXT_TOKENS=20000` (confirmed knob) so
   the window fills in minutes rather than hours, plus `--env RG_CTX_PROBE=1` so T1's statusline
   tee records the percentage at the same moment.
3. Drive filler turns until the log records an entry with `trigger` = `auto`.
4. Cross-reference the statusline tee line nearest that timestamp to get `used_percentage` at
   the moment of firing.
5. Repeat on a 200k-window model and on a 1M-window model, to see whether the reserve is a
   fixed fraction or a fixed token count.

**Pass criterion.** A recorded `trigger=auto` payload for each window size, each paired with a
statusline `used_percentage` — i.e. two numbers, not one inference.

**Fail meaning.** If the shrunken window changes the *fraction* at which it fires, the reserve
is a token count, not a percentage, and no percentage threshold is portable. That result would
strengthen D3 (PreCompact as a self-calibrating detector) rather than weaken it.

**Cost.** Moderate — filler turns against a 20k window. Keep prompts short and cheap; the point
is bulk, not quality.

**Honest caveat.** `CLAUDE_CODE_MAX_CONTEXT_TOKENS` may itself change the reserve arithmetic.
If the two window sizes disagree, that is a finding, not a broken test — record both.

---

## 6. T4 — is vetoing an AUTO compaction safe? (the dangerous one)

**Question.** When the harness decides it *must* compact and a hook vetoes it, and nothing else
relieves the window, does the session continue, degrade, or hard-fail?

**This is the claim most likely to be wrong in the whole spike.** §0.2 proved only the manual,
low-pressure case. D3 depends entirely on this one.

**Method.**

1. Re-arm the **blocking** rig, but gate the veto on the trigger so it only fires on auto:
   ```python
   if payload.get("trigger") != "auto":
       sys.exit(0)          # never hijack a manual /compact — D3's rule
   ```
   (`trigger` is confirmed present and correctly valued — §0.1.)
2. Throwaway pane, `--env CLAUDE_CODE_MAX_CONTEXT_TOKENS=20000 --env RG_PRECOMPACT_TEST=1`.
3. Fill until the first auto-compact attempt is vetoed.
4. Then keep issuing work prompts. After **each** veto, require a genuine assistant reply
   (`^● <canary>` in the transcript — see §2.1; do not match the prompt echo).
5. Continue for at least **3 consecutive auto-vetoes** past the first.

**Pass criterion.** The session answers a fresh canary after each of ≥3 consecutive auto-vetoes,
and ends `idle`.

**Fail criteria — any of these means D3 needs a fallback:** the turn errors; the session stops
accepting input; replies arrive but are visibly truncated or lose earlier context; or the
harness escalates (watch for `consecutiveRapidRefills`, which the harness already tracks).

**Fail consequence, stated in advance so the result is actionable.** If vetoing auto-compaction
walls the session, D3 inverts: PreCompact must **allow** the compaction and merely record that a
restart is due, with D2's Stop hook carrying the instruction. That is a strictly smaller design
and it is already half-written — so a failure here is survivable, not fatal.

**Cost.** Highest of the plan. Budget an hour and a throwaway pane you are willing to lose.

**Safety.** Throwaway pane only, shrunken window only, blocking gated to `trigger=auto` only.
Remove the settings entry and `diff` against the backup afterwards.

---

## 7. R1 — end-to-end restart rehearsal (AC5)

**Question.** Do D2 + D3 + D4 actually compose into a keystroke-free generational handoff?

Every individual link is now proven; the composition is not.

**Method — one rehearsal, scripted, in throwaway panes.**

1. **Predecessor.** Throwaway pane in an already-trusted cwd, launched with a goal:
   `claude "/goal <short condition>"`. Confirm `◎ /goal active`.
2. **Detector.** Armed PreCompact rig (auto-gated per T4) sets a restart-pending flag file.
3. **Instruction.** A temporary Stop hook that blocks **once** (per-session latch — required, or
   it ping-pongs against `stopHookBlockingCount`) and emits: run clear-prep, write the handoff,
   spawn the successor.
4. **Handoff.** The predecessor writes a successor-state file naming the handoff path.
5. **Spawn.** `herdr pane split --cwd <trusted> --env …` → read the new pane id from JSON →
   `pane run <new> exec <space-free wrapper script>`. **The wrapper is mandatory:** `pane run`
   re-splits every argument on spaces, so a multi-word `/goal` condition passed inline arrives
   as separate argv entries and `claude` opens the goal picker instead. The wrapper reads its
   prompt from a file. Strip `HERDR_*` inside it.
6. **Brief.** A temporary `SessionStart` hook injects `resume from <handoff path>` as
   `additionalContext`, landing before the successor's first turn.
7. **Verify the successor**, in this order: status line shows `◎ /goal active`; the goal
   condition is byte-identical to the predecessor's (D4 says verbatim); the handoff path is
   present in its context; it takes a first action consistent with the handoff.

**Pass criterion.** All four successor checks pass with **zero keystrokes** after step 1.

**Do not use these signals — both read as false negatives.** A restored goal appends **no new
`goal_status` row** (it is in-memory plus the status line), so counting transcript rows across
a resume is wrong. And a spawned successor fires the whole user Stop-hook chain, including the
mempalace AUTO-SAVE checkpoint — expect it, and do not read it as the successor misbehaving.

**Also check the goal length budget.** Cap is **4000 chars** (`Jdr=4000`); a live epic-style
goal measured 1458. If R1's condition is near the cap, verify the `goal_set/"too_long"`
telemetry path rather than assuming truncation is graceful.

**Cost.** Half a day, several throwaway panes, several temporary hooks. Remove every hook and
`diff` `settings.json` against its backup at the end.

---

## 8. Deliberately not tested — and why

- **Any implementation.** `context_meter.py`, the statusline edit, `hooks.json` registration,
  a new skill: all out of scope per the issue. T1's statusline tee is temporary and reverted.
- **Replacing Claude Code's own auto-compaction.** Out of scope by the issue's own Scope section.
- **Making a hook run `/clear` or start a session.** Named in the issue as a known wall; the best
  prior art (`enigma/claude-streaming-compactor`) still makes the user paste `claude --resume`.
- **The unattended/cron branch of D1.** D1 chose interactive-first; cron launchers already start a
  fresh `claude --print` per firing, so they are mostly fresh already. Revisit only if D1 changes.
- **Multi-generation chains (successor spawns its own successor).** R1 rehearses one hop. Chain
  depth is a separate question and should be its own test once one hop is reliable.

---

## 9. Suggested order, and where to stop

Run **T1 → T2 → T3 → T4 → R1**, and treat **T4 as the decision gate**:

- **T4 passes** → D3 stands as written; R1 rehearses the full composition.
- **T4 fails** → invert D3 (allow the compaction, record the restart) *before* running R1, so
  the rehearsal exercises the design that will actually ship.

T1 and T2 are cheap and independent — run them first regardless, because AC1 is a deliverable in
its own right and neither depends on the compaction questions.

---

## 10. Rig inventory

| Path | State | Purpose |
|---|---|---|
| `~/.claude/hooks/precompact-block-probe.py` | **exists, unwired, env-gated on `RG_PRECOMPACT_TEST=1`** | O1/O2 (done); reusable for T4 after adding the `trigger != "auto"` early-exit |
| `~/.claude/rawgentic-statusline.sh` | untouched | T1 tee point (add the gated line, back up first) |
| logging-only PreCompact variant | **not yet written** | T3 |
| Stop-hook latch + SessionStart brief | **not yet written** | R1 |

The blocking rig was intentionally **kept** after O1/O2 rather than deleted, because T4 needs it.
It is unwired from `settings.json` and inert without its env var; `settings.json` was verified
byte-identical to its pre-arm backup.
