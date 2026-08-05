# Skill-selection evals — the behavioural gate (#928)

A skill's `description` is exactly what selection keys on, and a selection regression is
**silent**: no error, no failing test — the skill simply never fires. #700 recorded seven such
misses for `pane-handoff` in 36 hours.

Eleven `evals.json` files have sat in this repo as data for a harness that did not exist here. The
tests that mention them check *existence*, the computed README fraction and membership
(`tests/hooks/test_adversarial_review_registration.py:345-372`), or pin trigger phrasings as plain
substrings (`tests/test_skill_description_budget.py:438-499`). **None of them exercised selection.**
`hooks/skill_evals.py` is the missing piece.

## The three intents, and where each signal comes from

The corpus already carries three distinct intents. They are read from **different places**, and
getting that wrong inverts the gate:

| Intent | Signal | Read from |
|---|---|---|
| `slash` | the prompt carries an explicit `/…` command for **this** skill | the **prompt** |
| `refuse` | the leading clause negates an invocation verb | the **expected output** |
| `trigger` | neither of the above — natural language must select the skill | default |

Two measured reasons the naive readings fail:

- **`slash` must not come from `expected_output`.** A first cut matched any path-shaped token, so
  `./projects/my-app`, `docs/reviews` and `wrong-org/wrong-repo` all read as slash commands,
  mislabelling natural-language cases across five skills. Note also that **not one prompt in the
  corpus starts with `/`** — all twelve command-driven cases embed it mid-sentence ("Run
  `/rawgentic:switch backend-api`. The working directory is …"), so `startswith("/")` finds nothing.
- **`refuse` must be scoped to the leading clause and bound to an invocation verb.** A whole-text
  "does not" match called `pane-handoff` cases 1, 4 and 5 refusals — i.e. asserted the skill must
  **not** fire for three of its dominant real phrasings. Those cases are positive; their prose
  merely says what the skill does not do *internally* ("It does NOT issue herdr terminal
  primitives itself"). Only case 6 negates the invocation, and it does so in its first sentence.

`slash` takes precedence over `refuse`: an explicit command selects the skill by construction, so a
command-driven case is never a *selection* refusal. `adversarial-review` case 2 opens "Refuses: the
artifact path resolves outside the project root" — that is the skill running and then rejecting its
**argument**, which for a selection gate is a successful selection.

## Where it runs — the decision (#928's fourth checkbox)

**A live verdict is a documented MANUAL gate, not a CI lane.** Not for cost — for a hard
prerequisite:

- Selection runs from the **installed plugin cache**
  (`~/.claude/plugins/cache/rawgentic/rawgentic/<version>/`), never from this repo.
- Proving a **not-yet-installed** build still selects correctly therefore needs that build
  installed, and reinstalling while sessions using its hooks are live is prohibited
  (`CLAUDE.md` §7, mistake #5). That is precisely what stopped #909 from building this.
- CI cannot install a plugin build and start real sessions, so a `pytest` lane cannot produce the
  verdict without faking the very thing under test.

What IS automated, in the ordinary suite: discovery, both on-disk schemas, intent classification
(pinned against the real corpus), transcript parsing, and every verdict rule — because the live
spawn is an **injected seam**. `tests/hooks/test_skill_evals.py` passes a fake submitter.

**One distinction worth keeping straight:** *observing* a verdict needs no reinstall — the
already-installed build answers fine. Only validating a build that is not yet installed does.

## Running it

```bash
# Every eval file, its case count and its intent mix. Exit 1 on an unreadable file.
python3 hooks/skill_evals.py discover --skills-root skills

# One skill's cases and their classified intents.
python3 hooks/skill_evals.py run --file skills/epic-run/evals/evals.json
```

`run --live` deliberately **refuses** and points here, rather than pretending an automated verdict.

### Containment first — the prompts are real requests

**Read this before running anything.** The corpus prompts are not inert strings. They include
"Implement issue #42 for me", "cycle through all issues in epic #906" and
"Run /rawgentic:new-project my-app". Submitted to a real installed plugin inside a real checkout,
a correct selection is followed by the skill *doing its job* — branching, committing, filing
issues, standing up a campaign. Observing selection must not become performing the work.

There is **no turn cap to lean on**: Claude Code as installed here (2.x) has no `--max-turns` flag
(checked against `claude --help`), so "stop immediately after the `Skill` event" cannot be
enforced by the CLI. Containment is therefore two things you must do yourself:

1. **Run from a disposable scratch directory, never a real project checkout** — and one with no
   reachable `.rawgentic_workspace.json`. Every rawgentic workflow skill loads config as its first
   act and STOPS when the workspace file is missing, which is exactly the behaviour you want: the
   skill is selected (the observation lands) and then declines to proceed.
2. **Deny the mutating tools**, so a fired skill cannot change anything even if it tries:

   ```bash
   claude -p "<the case prompt, verbatim>" \
     --disallowed-tools "Bash Edit Write NotebookEdit" \
     --output-format stream-json
   ```

   `--disallowed-tools` takes a comma- or space-separated list of tool names to deny. Selection is
   recorded before any denial matters, so the verdict survives the containment.

Treat any case whose prompt names a real issue or epic number as the highest-risk case, and
consider rewriting it to an obviously fictional number before running it live.

### The manual gate, step by step

1. Exit every session using rawgentic hooks (`CLAUDE.md` §7 step 1).
2. `claude plugin remove rawgentic@rawgentic && claude plugin install rawgentic@rawgentic`.
3. From the contained scratch directory above, start a fresh session per case and submit the case's
   `prompt` verbatim, with no other context — context contaminates selection, which is the
   behaviour under test.
4. Read the verdict from the transcript, not from the reply. A selection appears as an `assistant`
   line carrying a `tool_use` block named `Skill`:

   ```json
   {"type": "assistant", "message": {"content": [
       {"type": "tool_use", "name": "Skill", "input": {"skill": "rawgentic:epic-run"}}]}}
   ```

   `skills_selected(transcript_text)` parses exactly that. The shape was verified on 2026-08-05
   against a real 1.5 MB transcript under `~/.claude/projects/<slug>/<session-id>.jsonl` — it is
   measured, not guessed.
5. Judge with `judge(case, selected, skill_name, responded=transcript_responded(text))`. Three
   rules that are easy to get backwards:
   - For a `refuse` case, the skill **firing** is the failure.
   - A `refuse` case does **not** pass on a dead session. `responded=False` fails it, because "the
     skill was absent" and "nothing came back" are the same observation and only the first is
     success.
   - A `refuse` case that names the correct route instead (`expect_skill`) requires **that** skill
     to fire. `epic-run` case 5 is not satisfied by epic-run merely being absent — the request must
     actually reach `implement-feature`. Two such redirects are derived from the corpus today
     (`epic-run` cases 5 and 6); a case may also state `expect_skill` explicitly, which wins over
     inference.

## Why the `peer-consult` stub is skipped, and why discovery still reads it

`skills/peer-consult/evals.json` is `{"skill": "peer-consult", "cases": []}` — an empty stub, and
the README documents it as such. It contributes no cases. But it uses a **different schema** from
every real file (`skill` + `cases` instead of `skill_name` + `evals`), so a loader that assumes one
shape either crashes or silently reports zero cases for a *real* file. `load_cases` accepts both
spellings; reading a file is not the same as counting it.

## Deferred verification

**The live end-to-end run has NOT been executed for this change, and could not be.** The epic #906
auto-run that shipped it is itself a long-lived session using these hooks, so the reinstall in step
2 was prohibited throughout — the same prohibition that stopped #909.

| | |
|---|---|
| **Deferred** | the live gate: steps 1-5 against a freshly installed build |
| **Why** | `CLAUDE.md` §7 / mistake #5 — no reinstall while hook-using sessions are live |
| **Local proxy that DID run** | all 49 tests in `tests/hooks/test_skill_evals.py`, plus `discover` over the real 44-case corpus, with the transcript parser pinned against a real 1.5 MB transcript |
| **What the proxy cannot show** | that a real session, given a corpus prompt, selects the skill the case expects. Every component *below* the spawn is covered; the spawn itself is covered only by a fake |
| **Target check** | run the gate for `rawgentic:epic-run` first — its 403 restored description characters have never been exercised for selection at all — then record the per-case verdicts in this section |

Whoever runs it should also confirm the containment above behaves as described: that a workflow
skill selected inside a workspace-less scratch directory really does stop at its config gate. That
claim is reasoned from the skills' documented `<config-loading>` contract, **not** measured.
