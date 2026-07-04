# Adversarial Review — .rawgentic-diff-review-158-b256f81f.patch

- Date: 2026-07-04
- Artifact type: diff
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 5 (Critical 0, High 2, Medium 3, Low 0)
- **[WARNING]** Artifact truncated to 200000 bytes before review.

## Summary

The diff restructures WF2 into a spine plus reference files and adds shared blocks/verbatim verification. The main risks are that key always-run/resume contracts now depend on on-demand reference loading, and the provided diff does not include enough of the moved step corpus to verify the preservation claims.

## Findings

### 1. [High] completeness · high confidence — skills/implement-feature/SKILL.md ## Steps

> +One line per step; read `references/steps.md` §N before executing Step N. The
> +ordered spine is in `<happy-path>`; MANDATORY vs conditional is in
> +`<mandatory-steps>`.

Step execution now relies on humans/agents honoring an on-demand read instruction, but mandatory safety gates such as review and security scan are no longer present in the loaded spine. If the reference read is skipped, abbreviated, or fails, the spine still contains executable one-line summaries and can proceed with vacuous versions of gates whose detailed failure handling was moved out of view.

**Recommendation:** In `SKILL.md`, make the reading contract a hard gate: before each step, require logging `### WF2 Step N reference loaded: references/steps.md §N` and state that executing from the overview alone is forbidden; add this to `<mandatory-steps>` or `<completion-gate>`.

### 2. [High] correctness · high confidence — skills/implement-feature/SKILL.md <references>

> +references/state-and-resume.md` — the `<state-files>` and
> +  `<resumption-protocol>` contracts. Read before ANY resume, or before reading
> +  or writing a session-scoped state file or the committed review-state pointer.

The old `<state-files>` and `<resumption-protocol>` blocks were in the loaded skill body; after this change they are only read when the runner already knows it is resuming or touching state. A fresh session has no visible resume-detection procedure in the spine, so it can start at Step 1 without first loading the resume protocol, bypassing the stated resumption guard and potentially duplicating or overwriting in-flight work.

**Recommendation:** In `SKILL.md`, add an always-loaded startup instruction before `<happy-path>` or in `<happy-path>`: `Before Step 1, determine whether this is a resume; read references/state-and-resume.md and run <resumption-protocol> whenever session notes, branch, PR, or review-state evidence exists.`

### 3. [Medium] completeness · high confidence — skills/implement-feature/SKILL.md <references>

> +<references>
> +Progressive disclosure. This spine carries the always-run protocols and a
> +one-line-per-step overview; the full detail lives in per-skill reference files,
> +read on demand by this contract:

The diff references `references/run-record.md`, `references/headless.md`, and `references/whole-issue-delegation.md` as required files, but the provided artifact shows no additions or modifications for those files. Their contents and compatibility with the new spine are unverifiable from the provided diff, so claims that Step 16, headless handling, and whole-issue delegation remain preserved cannot be validated from this artifact.

**Recommendation:** Include the diffs for `skills/implement-feature/references/run-record.md`, `references/headless.md`, and `references/whole-issue-delegation.md`, or change the artifact summary to state those files are pre-existing and unchanged with their exact baseline commit.
**Ambiguity:** The files may exist outside the shown diff, but their presence and content are not verifiable from the provided artifact.

### 4. [Medium] completeness · high confidence — README.md v2.61.0 note

> +Since v2.61.0 (#158), the WF2 skill itself loads as a ~295-line spine with on-demand references (`references/steps.md` for per-step detail, `state-and-resume.md`, `headless.md`, `run-record.md`) instead of a 1,600-line monolith — progressive disclosure that shrinks the context paid on every invocation. All gates are preserved verbatim and drift-guarded via the `tests/corpus.py` helper.

The README claims all gates are preserved verbatim and drift-guarded, but the diff only adds a throwaway verifier and does not show any `tests/corpus.py` change or test hook that enforces the split after this commit. This makes the preservation claim unverifiable from the artifact and risks future drift once `scripts/verify_158_split.py` is removed.

**Recommendation:** Either add the actual `tests/corpus.py` drift-guard changes to this diff or change the README sentence to say the one-shot `scripts/verify_158_split.py` was used for the migration rather than claiming ongoing test drift guards.

### 5. [Medium] correctness · high confidence — scripts/verify_158_split.py module docstring

> +Throwaway by design — delete after #158 merges (it pins a one-time migration).

The only verifier shown for the verbatim split is explicitly temporary, while the public documentation claims ongoing drift guarding. After deletion, there is no shown enforcement that future edits preserve moved gates or keep the spine/reference contract aligned.

**Recommendation:** In `scripts/verify_158_split.py` or tests, convert the verifier into a permanent regression test for required gate anchors, or remove the post-merge deletion instruction and wire the script into the test suite for the affected corpus.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._