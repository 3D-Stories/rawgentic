"""#928 — the behavioural selection gate for skill evals.

Everything here is unit-testable BECAUSE the live spawn is an injected seam. The one
thing that genuinely needs an installed build and a real session — actually starting
`claude -p` — is the `submit` callable, and these tests pass a fake. What is NOT faked
is the transcript SHAPE: `skills_selected` is tested against the exact block shape
observed in a real 1.5 MB session transcript on 2026-08-05,

    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Skill", "input": {"skill": "rawgentic:switch", ...}}]}}

so the parser is not written against a guessed format.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks"))

import skill_evals  # noqa: E402


# --- discovery -----------------------------------------------------------------------

def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")


def test_discover_finds_both_sanctioned_locations(tmp_path):
    """A skill's own evals/ dir AND its -workspace sibling both count.

    These are the two locations `tests/hooks/test_adversarial_review_registration.py`
    computes the README fraction from, so discovery must agree with that guard.
    """
    root = tmp_path / "skills"
    _write(root / "alpha" / "evals" / "evals.json", {"skill_name": "a", "evals": []})
    _write(root / "beta-workspace" / "evals" / "evals.json", {"skill_name": "b", "evals": []})
    found = {p.relative_to(root).as_posix() for p in skill_evals.discover_eval_files(root)}
    assert found == {"alpha/evals/evals.json", "beta-workspace/evals/evals.json"}


def test_discover_also_finds_a_skill_root_evals_file(tmp_path):
    """`skills/peer-consult/evals.json` really exists, at the skill ROOT.

    Discovery must SEE it (a runner that cannot read a file is not the same thing as a
    count guard that deliberately excludes it — see the zero-case test below).
    """
    root = tmp_path / "skills"
    _write(root / "peer-consult" / "evals.json", {"skill": "peer-consult", "cases": []})
    found = {p.relative_to(root).as_posix() for p in skill_evals.discover_eval_files(root)}
    assert found == {"peer-consult/evals.json"}


# --- loading ------------------------------------------------------------------------

def test_load_cases_reads_the_real_schema(tmp_path):
    p = tmp_path / "evals.json"
    _write(p, {"skill_name": "rawgentic:pane-handoff",
               "evals": [{"id": 1, "prompt": "hand it over",
                          "expected_output": "Invokes the rawgentic:pane-handoff skill."}]})
    name, cases = skill_evals.load_cases(p)
    assert name == "rawgentic:pane-handoff"
    assert [c["id"] for c in cases] == [1]
    assert cases[0]["prompt"] == "hand it over"


def test_load_cases_tolerates_the_peer_consult_stub_schema(tmp_path):
    """The stub is `{"skill": ..., "cases": []}` — a DIFFERENT spelling of both keys.

    A loader that assumes one shape crashes or silently reports zero cases for a real
    file. Zero cases here is correct, but it must be reached by reading the file, not by
    failing to parse it.
    """
    p = tmp_path / "evals.json"
    _write(p, {"skill": "peer-consult", "cases": []})
    name, cases = skill_evals.load_cases(p)
    assert name == "peer-consult"
    assert cases == []


def test_load_cases_refuses_a_malformed_file(tmp_path):
    p = tmp_path / "evals.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(skill_evals.EvalError):
        skill_evals.load_cases(p)


# --- intent classification ----------------------------------------------------------

@pytest.mark.parametrize("expected, intent", [
    # The real pane-handoff case 6 — a NEGATIVE case. This is the one a naive
    # "prompt must trigger skill" harness gets backwards, which the issue calls out.
    ("Does NOT run the ad-hoc handoff. That command LAUNCHES a successor;", "refuse"),
    ("Does not invoke the skill; points at the runbook instead.", "refuse"),
    # Plain natural-language triggering.
    ("Invokes the rawgentic:pane-handoff skill. 'pass off' is the dominant phrasing.",
     "trigger"),
])
def test_classify_intent(expected, intent):
    assert skill_evals.classify_intent(expected) == intent


# --- slash intent comes from the PROMPT, which the corpus settles -------------------
#
# Measured: NOT ONE prompt in the corpus starts with "/". All 12 slash-invocation cases
# phrase it as "Run /rawgentic:<skill> <args>. The working directory is …". And deriving
# slash from `expected_output` was actively wrong — the first version matched any
# path-shaped token, so "./projects/my-app", "docs/reviews" and "wrong-org/wrong-repo"
# all read as slash commands, mislabelling natural-language cases across five skills.

def test_slash_intent_is_read_from_an_embedded_command_not_a_leading_one():
    """The real phrasing: the command sits mid-sentence after 'Run'."""
    assert skill_evals.classify_intent(
        "Prints the notice, invokes the engine, writes a report.",
        prompt="Run /rawgentic:adversarial-review docs/plan.md plan. The working directory is /tmp",
        skill_name="rawgentic:adversarial-review") == "slash"


def test_a_path_in_the_expected_output_is_not_a_slash_command():
    """The regression that mislabelled five skills' natural-language cases."""
    assert skill_evals.classify_intent(
        "Creates ./projects/my-app directory, runs git init, writes docs/reviews/x.md.",
        prompt="Set up a new project called my-app for me.",
        skill_name="rawgentic:new-project") == "trigger"


def test_a_slash_command_for_a_DIFFERENT_skill_is_not_this_skill_s_slash_case():
    """`implement-feature` case 2's prompt is natural language; its OUTPUT mentions
    /rawgentic:setup — the skill telling the user to run something else. That must not
    make it a slash-invocation case for implement-feature."""
    assert skill_evals.classify_intent(
        "Agent detects missing .rawgentic.json, STOPs, and tells user to run /rawgentic:setup",
        prompt="Implement issue #10 for me. The workspace file is at /tmp/x.json",
        skill_name="rawgentic:implement-feature") == "trigger"


def test_corpus_slash_membership_is_exactly_the_four_command_driven_skills():
    """Corpus-level guard on the whole 38-case set.

    adversarial-review, new-project, setup and switch phrase every case as an explicit
    command; nothing else in the corpus does. This pins the issue's own claim that all
    three adversarial-review cases are slash invocations.
    """
    repo = Path(__file__).resolve().parents[2]
    by_skill = {}
    for f in skill_evals.discover_eval_files(repo / "skills"):
        name, cases = skill_evals.load_cases(f)
        if not cases:
            continue
        for c in cases:
            by_skill.setdefault(name, []).append(c["intent"])

    assert by_skill["rawgentic:adversarial-review"] == ["slash", "slash", "slash"]
    for name in ("rawgentic:new-project", "rawgentic:setup", "rawgentic:switch"):
        assert set(by_skill[name]) == {"slash"}, f"{name}: {by_skill[name]}"
    # And the natural-language skills carry NO slash cases at all.
    for name in ("rawgentic:pane-handoff", "rawgentic:fix-bug",
                 "rawgentic:sync-security-patterns", "rawgentic:incident"):
        assert "slash" not in by_skill[name], f"{name}: {by_skill[name]}"


def test_refusal_wins_over_a_skill_mention():
    """Case 6 names the skill AND says it must not run. Refusal must not be masked."""
    text = ("Does NOT run the ad-hoc handoff. Points at docs/runbooks/herdr.md 7.1.2 "
            "instead of invoking rawgentic:pane-handoff.")
    assert skill_evals.classify_intent(text) == "refuse"


# --- the real corpus, which broke the first classifier -------------------------------
#
# Verbatim `expected_output` strings from skills/pane-handoff-workspace/evals/evals.json.
# The first classifier matched "does not" ANYWHERE and so called cases 1, 4 and 5
# refusals — asserting that pane-handoff must NOT fire for three of its dominant real
# phrasings, which is the exact inversion #928 warns a naive harness produces. The
# distinction these pin: a POSITIVE case may still say what the skill does not do
# INTERNALLY; only a negated INVOCATION in the leading clause is a refusal.

_CASE_1 = ("Invokes the rawgentic:pane-handoff skill. 'pass off' is the dominant phrasing in "
           "real requests, not 'handoff', so this must trigger. The skill assembles the anchor "
           "pane from $HERDR_PANE_ID and the project from the session-registry row, and drives "
           "launcher_lib.py ad-hoc-handoff. It does NOT issue herdr terminal primitives itself.")
_CASE_4 = ("Runs clear-prep first to produce the handoff payload, then invokes "
           "rawgentic:pane-handoff CONSUMING its resume-prompt file and goal text. pane-handoff "
           "does not itself invoke clear-prep — clear-prep lives outside this repository.")
_CASE_5 = ("Offers or performs the pass-off unprompted, because the directive tier is the "
           "trigger the user expects to act on without asking. Does not claim to read a meter "
           "directly — it reacts to the hook's reminder, whose threshold and window belong to "
           "contextMeter (#687/#701).")
_CASE_6 = ("Does NOT run the ad-hoc handoff. That command LAUNCHES a successor; an "
           "already-running pane is not a herdr-registered agent, so its gates do not apply. "
           "Points at docs/runbooks/herdr.md section 7.1.2 for the by-hand case instead.")


@pytest.mark.parametrize("text, intent, why", [
    (_CASE_1, "trigger", "negation is about herdr primitives, not about invoking"),
    (_CASE_4, "trigger", "negation is about not invoking clear-prep, a DIFFERENT skill"),
    (_CASE_5, "trigger", "negation is about not reading a meter directly"),
    (_CASE_6, "refuse", "the leading clause negates the invocation itself"),
])
def test_real_pane_handoff_corpus_classifies_correctly(text, intent, why):
    assert skill_evals.classify_intent(text) == intent, why


def test_only_one_real_pane_handoff_case_is_a_refusal():
    """Corpus-level guard: 5 triggers + 1 refusal. A drift here inverts the gate."""
    repo = Path(__file__).resolve().parents[2]
    _, cases = skill_evals.load_cases(
        repo / "skills" / "pane-handoff-workspace" / "evals" / "evals.json")
    intents = [c["intent"] for c in cases]
    assert intents.count("refuse") == 1, f"expected exactly 1 refusal, got {intents}"
    assert intents.count("trigger") == 5, f"expected 5 triggers, got {intents}"
    # And it must be case 6 specifically, not merely one-of-six.
    assert next(c["id"] for c in cases if c["intent"] == "refuse") == 6


# --- transcript parsing (shape verified against a real transcript) -------------------

def _transcript(*skills) -> str:
    lines = [json.dumps({"type": "user", "message": {"content": "hi"}})]
    for s in skills:
        lines.append(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "sure"},
            {"type": "tool_use", "name": "Skill", "input": {"skill": s, "args": ""}}]}}))
    return "\n".join(lines) + "\n"


def test_skills_selected_reads_the_observed_block_shape():
    text = _transcript("rawgentic:switch", "rawgentic:implement-feature")
    assert skill_evals.skills_selected(text) == [
        "rawgentic:switch", "rawgentic:implement-feature"]


def test_skills_selected_ignores_other_tools_and_bad_lines():
    lines = [
        "not json at all",
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Skill", "input": {"skill": "dataviz"}}]}}),
    ]
    assert skill_evals.skills_selected("\n".join(lines)) == ["dataviz"]


def test_skills_selected_empty_when_nothing_fired():
    """The silent-regression signature: no error, no skill. Must read as empty, not crash."""
    assert skill_evals.skills_selected(_transcript()) == []


# --- judging ------------------------------------------------------------------------

def test_trigger_case_passes_when_the_skill_fired():
    v = skill_evals.judge({"id": 1, "intent": "trigger"}, ["rawgentic:pane-handoff"],
                          "rawgentic:pane-handoff")
    assert v["passed"] is True


def test_trigger_case_fails_when_nothing_fired():
    v = skill_evals.judge({"id": 1, "intent": "trigger"}, [], "rawgentic:pane-handoff")
    assert v["passed"] is False
    assert "no skill" in v["why"].lower()


def test_trigger_case_fails_when_the_wrong_skill_fired():
    v = skill_evals.judge({"id": 1, "intent": "trigger"}, ["rawgentic:switch"],
                          "rawgentic:pane-handoff")
    assert v["passed"] is False


def test_refuse_case_passes_when_the_skill_did_not_fire():
    v = skill_evals.judge({"id": 6, "intent": "refuse"}, ["rawgentic:switch"],
                          "rawgentic:pane-handoff")
    assert v["passed"] is True


def test_refuse_case_fails_when_the_skill_fired():
    """Backwards-harness regression guard: firing is a FAILURE for a negative case."""
    v = skill_evals.judge({"id": 6, "intent": "refuse"}, ["rawgentic:pane-handoff"],
                          "rawgentic:pane-handoff")
    assert v["passed"] is False


def test_bare_name_matches_a_prefixed_selection():
    """Eval files spell `rawgentic:pane-handoff`; a file may spell the bare name."""
    v = skill_evals.judge({"id": 1, "intent": "trigger"}, ["rawgentic:pane-handoff"],
                          "pane-handoff")
    assert v["passed"] is True


# --- the run loop, with the live spawn injected --------------------------------------

def test_run_uses_the_injected_submitter_and_never_spawns():
    """The whole point of the seam: this runs in an ordinary pytest lane."""
    calls = []

    def fake_submit(prompt):
        calls.append(prompt)
        return _transcript("rawgentic:pane-handoff")

    cases = [{"id": 1, "prompt": "hand it over", "intent": "trigger"},
             {"id": 6, "prompt": "send to pane w1:pQ7", "intent": "refuse"}]
    results = skill_evals.run(cases, "rawgentic:pane-handoff", fake_submit)
    assert calls == ["hand it over", "send to pane w1:pQ7"]
    assert [r["passed"] for r in results] == [True, False]


def test_run_records_a_submitter_failure_as_a_failed_case_not_a_pass():
    """A dead spawn is a FAILED dispatch, never a clean pass (repo mistake #9)."""
    def broken_submit(prompt):
        raise RuntimeError("claude -p died")

    results = skill_evals.run([{"id": 1, "prompt": "x", "intent": "trigger"}],
                              "s", broken_submit)
    assert results[0]["passed"] is False
    assert "died" in results[0]["why"]


def test_run_on_zero_cases_is_an_empty_result_not_a_vacuous_pass():
    assert skill_evals.run([], "peer-consult", lambda p: "") == []


# --- inline self-review findings (mechanical + bug_logic lens) ------------------------

def test_a_malformed_case_is_not_blamed_on_the_submitter():
    """A case missing `prompt` must not be reported as 'submitter failed'.

    The first version read `case["prompt"]` INSIDE the try that guards the spawn, so a
    KeyError in our own data was attributed to the submitter — misdirecting whoever reads
    the failure at exactly the moment they are debugging a live spawn.
    """
    calls = []
    results = skill_evals.run([{"id": 1, "intent": "trigger"}], "s",
                              lambda p: calls.append(p) or "")
    assert results[0]["passed"] is False
    assert "prompt" in results[0]["why"].lower()
    assert "submitter" not in results[0]["why"].lower()
    assert calls == [], "a malformed case must never reach the submitter"


def test_a_non_string_expected_output_does_not_crash_the_loader(tmp_path):
    """`expected_output` is authored by hand per skill; a dict there must not raise
    AttributeError out of `classify_intent`. It carries no refusal signal, so the case
    is a trigger."""
    p = tmp_path / "evals.json"
    _write(p, {"skill_name": "s", "evals": [
        {"id": 1, "prompt": "go", "expected_output": {"unexpected": "shape"}}]})
    name, cases = skill_evals.load_cases(p)
    assert cases[0]["intent"] == "trigger"


def test_classify_intent_tolerates_a_non_string_argument():
    assert skill_evals.classify_intent(None) == "trigger"
    assert skill_evals.classify_intent({"a": 1}) == "trigger"
