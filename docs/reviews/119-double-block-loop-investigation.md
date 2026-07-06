# #119 — Verify (or refute) the security-guidance "double-block loop" claim

**Issue:** [#119](https://github.com/3D-Stories/rawgentic/issues/119) · epic #247 · **Verdict: REFUTED**

## The claim under test

`hooks/security-guard-check.sh` recommended disabling `security-guidance@claude-plugins-official`, asserting:

> "Both check the same patterns, and the official one uses a broken blocking mechanism that **auto-retries**, so running both causes a confusing **double-block loop**."

Filed as a follow-up to PR #117: the claim was never empirically verified. Three sub-questions:
1. Does Claude Code auto-retry when a PreToolUse hook denies / exits non-zero?
2. Do BOTH PreToolUse hooks fire in sequence, or does rawgentic's `permissionDecision: deny` prevent the official hook's second attempt?
3. Is the double-block loop observed in practice, or theoretical?

## Method

The issue's acceptance criterion asked for a live dual-plugin hook trace. That turned out to be **unnecessary** — a static inspection of the two plugins' hook registrations is **dispositive**, and it is stronger than a trace (a trace shows one run; the registration shows what is structurally possible). The official plugin (v2.0.6) is present in the local plugin cache, so its real hook config was read directly. (A live re-enable trace was also out of reach this session: the plugin is disabled on this machine and the project forbids reinstalling/enabling plugins mid-session.)

## Evidence (primary source — files read, not inferred)

**1. The two plugins hook DIFFERENT lifecycle events.** CONFIRMED by reading each `hooks.json`:

| Plugin | Registered hook events | Blocks an edit… |
|---|---|---|
| **rawgentic** (`hooks/hooks.json`) | SessionStart, **PreToolUse**, PostToolUse, PostToolUseFailure, UserPromptSubmit, Stop | **Pre-edit** — `security-guard.py` is a PreToolUse hook emitting `permissionDecision: deny` (`hooks/security-guard.py:5`) |
| **security-guidance** v2.0.6 (`…/2.0.6/hooks/hooks.json`) | SessionStart, UserPromptSubmit, **PostToolUse**, Stop | **Post-edit** — reviews AFTER the write; `PreToolUse` is **not registered** (`json.load(...)['hooks'].keys()` → no `PreToolUse`) |

**2. The official plugin has NO PreToolUse hook.** CONFIRMED: `PreToolUse in hooks['hooks']` → `False`. It cannot participate in a *PreToolUse* double-block, because it does not run at PreToolUse at all.

**3. The official plugin's block is a PostToolUse/Stop supplementary review, not a tool-call retry.** CONFIRMED by reading `security_reminder_hook.py`: it uses `exit(2)` / `decision:"block"` on PostToolUse/Stop as an **`asyncRewake` auto-turn**, and appends a `CONTINUATION_SUFFIX` that literally states: *"…this review is supplementary feedback, not a replacement for your previous …"* and tells the model to continue the user's original request. That is by-design guidance to address a finding on the next turn — not a retry loop of a blocked operation.

## Analysis — why there is no double-block loop

- rawgentic denies an edit at **PreToolUse**, *before* it happens. When it denies, the edit never executes, so **no PostToolUse fires** for that edit — the official plugin never even sees it. No interaction.
- When rawgentic *allows* an edit (pattern not matched), the edit executes and the official plugin's **PostToolUse** review may then flag it — at a different point in the lifecycle, as supplementary feedback. Redundant coverage, not a competing block.
- A "both PreToolUse hooks fire and one auto-retries" loop is **structurally impossible**: there is only one PreToolUse hook between them (rawgentic's).

**Sub-question answers:**
1. rawgentic's `permissionDecision: deny` is the documented non-retried PreToolUse block; the official plugin's auto-turn (`asyncRewake`) is a PostToolUse/Stop review mechanism, not a PreToolUse retry. The premise conflates two different mechanisms at two different events.
2. Moot — the two PreToolUse hooks don't both exist; only rawgentic's does.
3. The described double-block loop is **theoretical and, on this evidence, cannot occur** with these two plugins as shipped (v2.0.6).

## Confirmed vs inferred

- **CONFIRMED (files read):** the event registrations, the absence of a PreToolUse hook in security-guidance, rawgentic's PreToolUse-deny mechanism, and the official plugin's PostToolUse/Stop `exit(2)` asyncRewake + `CONTINUATION_SUFFIX`.
- **INFERRED (from mechanism, not a live trace):** that no confusing loop is observed in practice. This is a *structural* inference (a plugin with no PreToolUse hook cannot cause a PreToolUse double-block), not a behavioral guess — but it was not observed via a live dual-plugin run this session.
- **Residual, if ever wanted:** a live re-enable trace would additionally show the PostToolUse asyncRewake UX (extra turns / duplicate warnings), which is the real, benign cost of running both — noise, not a loop.

## Outcome — recommendation relaxed (not dropped)

Per the issue's own guidance ("refutes it → relax / drop"), the `security-guard-check.sh` SessionStart notice is **relaxed**: it no longer claims a "broken blocking mechanism that auto-retries / double-block loop." It now states the accurate mechanism — the two hook different lifecycle events, running both is **redundant not conflicting**, and disabling security-guidance is **optional** (to cut duplicate warnings) rather than required. The record-once machinery and the "keep both" opt-out are unchanged. The notice is kept (not dropped) because redundant duplicate security warnings are still a real, if minor, UX cost worth a one-time heads-up.

A drift guard (`tests/hooks/test_security_guard_check.py`) pins the corrected wording: the notice must NOT reassert "double-block loop" / "auto-retries," and MUST describe the redundant-not-conflicting reality.
