"""#928 — the behavioural gate that proves a skill still gets SELECTED.

**Why this exists.** Skill `description` text is exactly what selection keys on, and a
selection regression is SILENT: no error, no failing test — the skill simply never fires.
#700 recorded seven such misses for `pane-handoff` in 36 hours. Eleven real `evals.json`
files sat in this repo as data for a harness that did not exist; the tests that mention
them check EXISTENCE, the computed README fraction, or pin trigger phrasings as plain
substrings (`tests/test_skill_description_budget.py:438-499`). None exercised selection.

**The seam, and why it is where it is.** Selection runs from the INSTALLED plugin cache
(`~/.claude/plugins/cache/rawgentic/rawgentic/<version>/`), not from this repo, so a real
verdict needs a live session. Reinstalling the plugin while sessions using its hooks are
live is prohibited (repo `CLAUDE.md` §7, mistake #5) — that is what stopped #909 from
building this. So `submit` is INJECTED: every classification, parse and verdict is pure
and runs in the ordinary pytest lane, and only the live spawn sits behind the seam.

Note what this means and does not mean, because the distinction is the whole design:
observing a verdict needs no reinstall — the ALREADY-installed build answers fine. Only
proving a NOT-YET-INSTALLED build still selects correctly needs one. That is a manual
gate by nature, not a CI lane, which resolves the issue's fourth checkbox.

**The transcript shape is measured, not guessed.** A selection appears as an `assistant`
line carrying a `tool_use` block named `Skill`:

    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Skill", "input": {"skill": "rawgentic:switch"}}]}}

Verified 2026-08-05 against a real 1.5 MB session transcript under
`~/.claude/projects/<slug>/<session-id>.jsonl`.

Fail-mode: this is a GATE, so it fails CLOSED — an unreadable eval file raises, and a
dead submitter scores its case as FAILED, never as a pass (repo mistake #9: a vacuous
result is a failed dispatch).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Both sanctioned eval locations, plus the skill-root spelling that really occurs
# (`skills/peer-consult/evals.json`). The first two are what
# `tests/hooks/test_adversarial_review_registration.py` computes the README fraction
# from; the third is a file that exists and must be READ rather than crashed on. Reading
# it is not the same as counting it — it holds zero cases, which the README documents as
# a deliberate stub, so it contributes nothing to a run either way.
_EVAL_GLOBS = ("*/evals/evals.json", "*/evals.json")

# Refusal is tested FIRST and wins, because a negative case routinely names the skill it
# must not run (`pane-handoff` case 6 does exactly that). Checking "mentions the skill"
# first is how a harness gets negative cases backwards — the failure mode the issue
# explicitly warns about.
#
# But a bare negation anywhere in the prose is NOT the signal, and getting that wrong
# inverts the gate. Measured against the real corpus: `pane-handoff` cases 1, 4 and 5 are
# POSITIVE cases whose prose also says what the skill does not do INTERNALLY — "It does
# NOT issue herdr terminal primitives itself", "pane-handoff does not itself invoke
# clear-prep", "Does not claim to read a meter directly". A whole-text match called all
# three refusals, i.e. asserted the skill must NOT fire for three of its dominant real
# phrasings. So refusal is scoped to the LEADING CLAUSE and must negate an INVOCATION
# verb: every positive case in this corpus opens with what the skill DOES ("Invokes…",
# "Runs…", "Offers or performs…"), and the one true negative opens with "Does NOT run".
_INVOKE_VERB = r"(?:invoke|invokes|run|runs|trigger|triggers|fire|fires|use|uses|call|calls)"
_REFUSE_LEAD = re.compile(
    r"\b(?:does\s+not|doesn'?t|do\s+not|don'?t|must\s+not|should\s+not|never|will\s+not|"
    r"won'?t|refuses?\s+to)\s+" + _INVOKE_VERB + r"\b", re.I)
# A leading clause that is *only* a refusal statement, e.g. "No skill fires here."
_REFUSE_BARE = re.compile(r"^\s*(?:no\s+skill|nothing)\b.{0,40}?\b"
                          r"(?:fires?|runs?|invoked|selected)\b", re.I)

# The leading clause: up to the first sentence break. `—` counts as a break because this
# corpus routinely uses an em-dash to start the qualifying half of a sentence, which is
# where the internal negations live.
_LEAD_SPLIT = re.compile(r"(?<=[.!?])\s|\s+—\s+|\n")


class EvalError(ValueError):
    """Raised on an eval file this module cannot trust. Fails closed, never silently."""


def discover_eval_files(skills_root) -> list[Path]:
    """Every eval file under `skills_root`, de-duplicated, in stable sorted order."""
    root = Path(skills_root)
    seen: dict[Path, None] = {}
    for pattern in _EVAL_GLOBS:
        for p in sorted(root.glob(pattern)):
            seen.setdefault(p.resolve(), None)
    return sorted(seen, key=lambda p: p.as_posix())


def load_cases(path) -> tuple[str, list[dict]]:
    """`(skill_name, cases)` from one eval file. Accepts BOTH key spellings on disk.

    The real files use `skill_name` + `evals`; the `peer-consult` stub uses `skill` +
    `cases`. Assuming one shape either crashes or silently reports zero cases for a real
    file, so both are read and each case is normalised to
    `{"id", "prompt", "expected_output", "intent"}`.
    """
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise EvalError(f"{p}: unreadable or malformed eval file: {e}") from e
    if not isinstance(raw, dict):
        raise EvalError(f"{p}: top level must be an object, got {type(raw).__name__}")

    name = raw.get("skill_name") or raw.get("skill")
    if not isinstance(name, str) or not name.strip():
        raise EvalError(f"{p}: no usable skill name (`skill_name` or `skill`)")

    entries = raw.get("evals")
    if entries is None:
        entries = raw.get("cases")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise EvalError(f"{p}: `evals`/`cases` must be a list")

    cases = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            raise EvalError(f"{p}: case {i} is not an object")
        prompt = e.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise EvalError(f"{p}: case {e.get('id', i)} has no prompt")
        expected = e.get("expected_output") or ""
        cases.append({"id": e.get("id", i + 1), "prompt": prompt,
                      "expected_output": expected,
                      "intent": classify_intent(expected, prompt, name)})
    return name.strip(), cases


def _slash_command_for(prompt: str, skill_name: str) -> bool:
    """Does `prompt` carry an explicit `/…` command invoking THIS skill?

    Measured against the corpus: not one prompt starts with `/`. All twelve
    command-driven cases embed it mid-sentence — "Run /rawgentic:switch backend-api. The
    working directory is …" — so a `startswith("/")` test finds nothing.

    Keyed on the skill's own name, deliberately. `implement-feature` case 2 expects the
    skill to STOP and tell the user to run `/rawgentic:setup`; that is a different
    skill's command appearing in the OUTPUT, and treating any command as this case's
    invocation would relabel a natural-language case as a slash one.
    """
    bare = (skill_name or "").split(":")[-1].strip()
    if not bare:
        return False
    pattern = re.compile(r"/(?:[a-z0-9_-]+:)?" + re.escape(bare) + r"\b", re.I)
    return bool(pattern.search(prompt or ""))


def classify_intent(expected_output: str, prompt: str = "", skill_name: str = "") -> str:
    """One of `refuse`, `slash`, `trigger` — the three intents already in the data.

    The signals come from DIFFERENT places, which is the correction that made this agree
    with the corpus:

    - `slash` is a property of the PROMPT (an explicit command for this skill). Reading
      it from `expected_output` matched any path-shaped token — `./projects/my-app`,
      `docs/reviews`, `wrong-org/wrong-repo` — and mislabelled natural-language cases
      across five skills.
    - `refuse` is a property of the expected OUTPUT, scoped to the leading clause and
      requiring a negated invocation verb (see `_REFUSE_LEAD`).

    Precedence: `slash` first. An explicit command selects the skill by construction, so
    a command-driven case is never a selection refusal — `adversarial-review` case 2
    opens "Refuses: the artifact path resolves outside the project root", but that is the
    skill running and then rejecting its ARGUMENT, which for a selection gate is a
    successful selection.
    """
    text = (expected_output or "").strip()
    if _slash_command_for(prompt, skill_name):
        return "slash"
    if not text:
        return "trigger"
    lead = _LEAD_SPLIT.split(text)[0]
    if _REFUSE_LEAD.search(lead) or _REFUSE_BARE.search(lead):
        return "refuse"
    return "trigger"


def skills_selected(transcript_text: str) -> list[str]:
    """Skills the session actually invoked, in order, from a transcript's JSONL text.

    Tolerant by design: a transcript is an append-only log that can hold partial or
    non-JSON lines, and a parse error on one line must not hide the selections on the
    others. An empty list is a real answer — it is the silent-regression signature.
    """
    out: list[str] = []
    for line in (transcript_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for blk in content:
            if (isinstance(blk, dict) and blk.get("type") == "tool_use"
                    and blk.get("name") == "Skill"):
                chosen = (blk.get("input") or {}).get("skill")
                if isinstance(chosen, str) and chosen.strip():
                    out.append(chosen.strip())
    return out


def _same_skill(selected: str, wanted: str) -> bool:
    """`rawgentic:pane-handoff` and `pane-handoff` name the same skill.

    Eval files spell the prefixed form; `SKILL.md` frontmatter carries the bare name
    (#552), so a comparison keyed on either spelling alone would misjudge half the corpus.
    """
    return selected.split(":")[-1] == wanted.split(":")[-1]


def judge(case: dict, selected, skill_name: str) -> dict:
    """Verdict for one case: did the RIGHT thing happen for this case's intent?"""
    chosen = list(selected or [])
    fired = any(_same_skill(s, skill_name) for s in chosen)
    intent = case.get("intent", "trigger")
    cid = case.get("id")

    if intent == "refuse":
        passed = not fired
        why = ("correctly did not invoke it" if passed
               else f"{skill_name} fired, but this case requires it NOT to")
    else:
        passed = fired
        if fired:
            why = "invoked as required"
        elif not chosen:
            why = "no skill was invoked at all (the silent-regression signature)"
        else:
            why = f"the wrong skill fired: {', '.join(chosen)}"
    return {"id": cid, "intent": intent, "passed": passed, "why": why,
            "selected": chosen, "skill": skill_name}


def run(cases, skill_name: str, submit) -> list[dict]:
    """Judge every case, submitting each prompt through the INJECTED `submit`.

    `submit(prompt) -> transcript_text`. A submitter that raises scores its case as
    FAILED and keeps going: a dead spawn is a failed dispatch, never a clean pass, and
    one dead case must not discard the verdicts already earned.
    """
    results = []
    for case in cases or []:
        try:
            transcript = submit(case["prompt"])
        except Exception as e:  # noqa: BLE001 — any submitter failure is a case failure
            results.append({"id": case.get("id"), "intent": case.get("intent", "trigger"),
                            "passed": False, "why": f"submitter failed: {e}",
                            "selected": [], "skill": skill_name})
            continue
        results.append(judge(case, skills_selected(transcript), skill_name))
    return results


def _main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="skill_evals.py",
        description="Discover skill eval files and report their cases and intents. "
                    "Selection itself is a MANUAL gate — see --live.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_d = sub.add_parser("discover", help="list eval files with case counts and intents")
    p_d.add_argument("--skills-root", default="skills")

    p_r = sub.add_parser("run", help="judge one skill's cases")
    p_r.add_argument("--file", required=True, help="an evals.json path")
    p_r.add_argument("--live", action="store_true",
                     help="actually spawn `claude -p` per prompt against the INSTALLED "
                          "build. Refused here: it needs a live session and, to test an "
                          "unreleased build, a reinstall that is prohibited while "
                          "hook-using sessions run (CLAUDE.md §7).")

    args = ap.parse_args(argv)

    if args.cmd == "discover":
        total = 0
        for f in discover_eval_files(args.skills_root):
            try:
                name, cases = load_cases(f)
            except EvalError as e:
                print(f"INVALID {f}: {e}")
                return 1
            total += len(cases)
            intents = {}
            for c in cases:
                intents[c["intent"]] = intents.get(c["intent"], 0) + 1
            shape = ", ".join(f"{k}={v}" for k, v in sorted(intents.items())) or "none"
            print(f"{f}  skill={name}  cases={len(cases)}  [{shape}]")
        print(f"total cases: {total}")
        return 0

    name, cases = load_cases(args.file)
    if args.live:
        print("REFUSED: --live is a documented manual gate, not an automated lane.\n"
              "  A verdict needs a real session started against the installed build.\n"
              "  See docs/skill-evals.md for the exact procedure and why CI cannot run it.")
        return 2
    print(f"skill={name} cases={len(cases)}")
    for c in cases:
        print(f"  case {c['id']}: intent={c['intent']}  prompt={c['prompt'][:60]!r}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    import sys as _sys
    raise SystemExit(_main(_sys.argv[1:]))
