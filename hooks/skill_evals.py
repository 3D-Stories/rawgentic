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

# A positive invocation, used only to decide whether a refusal in the same clause comes
# FIRST (review finding 6). Present participles and third-person forms both occur.
_POSITIVE_LEAD = re.compile(
    r"\b(?:invokes?|invoking|runs?|running|fires?|triggers?|uses?|calls?|"
    r"offers\s+or\s+performs|performs)\b", re.I)

# The leading clause: up to the first sentence break. `—` counts as a break because this
# corpus routinely uses an em-dash to start the qualifying half of a sentence, which is
# where the internal negations live.
_LEAD_SPLIT = re.compile(r"(?<=[.!?])\s|\s+—\s+|\n")

# A refusal often names where the request SHOULD go instead. Rather than parse English,
# look for a NAMESPACE-QUALIFIED skill name — `rawgentic:create-issue`, optionally
# slash-prefixed — because that spelling only appears in this corpus when a specific skill
# is being named. The target skill itself is excluded by the caller. Deliberately narrow:
# an unqualified bare word is NOT treated as a redirect, since inferring an assertion from
# ordinary prose is how an oracle starts failing for reasons nobody can read.
_QUALIFIED_SKILL = re.compile(r"/?\b([a-z0-9_-]+):([a-z0-9-]{3,})\b", re.I)


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

    # Cross-model review finding 3: absence of BOTH keys used to collapse to zero cases,
    # so a truncated file or a misspelled key produced a silent empty corpus for that
    # skill — indistinguishable from a deliberate stub. A DECLARED empty list is still
    # fine (the `peer-consult` stub declares `cases: []`); an absent key is not.
    if "evals" in raw:
        entries = raw["evals"]
    elif "cases" in raw:
        entries = raw["cases"]
    else:
        raise EvalError(
            f"{p}: neither `evals` nor `cases` is present. A file declaring no cases must "
            f"say so explicitly (`\"cases\": []`); an absent key is indistinguishable from "
            f"truncation or a misspelling, and this is a gate.")
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
        intent = classify_intent(expected, prompt, name)
        case = {"id": e.get("id", i + 1), "prompt": prompt,
                "expected_output": expected, "intent": intent}
        # A refusal that names the CORRECT route must require that route (review
        # finding 2): epic-run case 5's absence of epic-run is not success if nothing
        # fired at all. An explicit `expect_skill` in the data wins over inference.
        redirect = e.get("expect_skill") or (
            redirect_skill(expected, name) if intent == "refuse" else None)
        if redirect:
            case["expect_skill"] = redirect
        cases.append(case)
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
    # Not a string is not a refusal. `expected_output` is hand-authored per skill, so a
    # dict or a null there must classify as a plain trigger rather than raise out of a
    # classifier — the caller is a gate, and crashing it is worse than reading no signal.
    text = expected_output.strip() if isinstance(expected_output, str) else ""
    if _slash_command_for(prompt, skill_name):
        return "slash"
    if not text:
        return "trigger"
    lead = _LEAD_SPLIT.split(text)[0]
    refusal = _REFUSE_LEAD.search(lead) or _REFUSE_BARE.search(lead)
    if not refusal:
        return "trigger"
    # Cross-model review finding 6: the negation was never bound to the target skill, so
    # a single sentence that BOTH invokes it and declines a different one — "Invokes
    # pane-handoff but does not invoke clear-prep." — inverted the oracle. A real refusal
    # leads with the negation; a positive invocation appearing FIRST means the skill is
    # expected to fire and the negation is about something else.
    positive = _POSITIVE_LEAD.search(lead)
    if positive and positive.start() < refusal.start():
        return "trigger"
    return "refuse"


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
    """Do these name the same skill? NAMESPACE-AWARE (review finding 5).

    `rawgentic:pane-handoff` and the bare `pane-handoff` are the same skill — eval files
    spell the prefixed form while `SKILL.md` frontmatter carries the bare name (#552), so
    a comparison keyed on either spelling alone misjudges half the corpus.

    But comparing ONLY the bare tail made `other-plugin:pane-handoff` satisfy a gate on
    `rawgentic:pane-handoff`, so a same-named skill from another plugin could hide a real
    rawgentic selection failure. Namespaces are therefore compared whenever BOTH sides
    carry one; the tail-only fallback applies only when one side is unqualified.
    """
    sel, want = (selected or "").strip(), (wanted or "").strip()
    if not sel or not want:
        return False
    sel_ns, _, sel_bare = sel.rpartition(":")
    want_ns, _, want_bare = want.rpartition(":")
    if sel_bare != want_bare:
        return False
    if sel_ns and want_ns:
        return sel_ns.lower() == want_ns.lower()
    return True


def redirect_skill(expected_output: str, skill_name: str) -> str | None:
    """The skill a refusal says should run INSTEAD, or None.

    Only a namespace-qualified name counts (see `_QUALIFIED_SKILL`), and the target skill
    itself never counts as its own redirect.
    """
    if not isinstance(expected_output, str):
        return None
    for m in _QUALIFIED_SKILL.finditer(expected_output):
        candidate = f"{m.group(1)}:{m.group(2)}"
        if not _same_skill(candidate, skill_name):
            return candidate
    return None


def transcript_responded(transcript_text: str) -> bool:
    """Did the session actually produce an assistant turn?

    This is the difference between "it answered, and chose no skill" and "nothing came
    back". Review finding 2: without it, a refusal case PASSED on a dead spawn or an
    unparseable transcript — reporting the very silent-failure mode the gate exists to
    catch as a success.
    """
    for line in (transcript_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("type") == "assistant":
            return True
    return False


def judge(case: dict, selected, skill_name: str, responded: bool = True) -> dict:
    """Verdict for one case: did the RIGHT thing happen for this case's intent?

    `responded` says the session produced an assistant turn at all. It is required for a
    refusal to pass, because "the skill was absent" and "nothing happened" are the same
    observation and only the first is success.
    """
    chosen = list(selected or [])
    fired = any(_same_skill(s, skill_name) for s in chosen)
    intent = case.get("intent", "trigger")
    cid = case.get("id")
    redirect = case.get("expect_skill")

    if intent == "refuse":
        if not responded:
            passed, why = False, ("no usable transcript — the session did not respond, so "
                                  "absence of the skill is not evidence of refusal")
        elif fired:
            passed, why = False, f"{skill_name} fired, but this case requires it NOT to"
        elif redirect and not any(_same_skill(s, redirect) for s in chosen):
            passed, why = False, (f"{skill_name} correctly did not fire, but this case "
                                  f"requires {redirect} instead and it did not fire "
                                  f"(selected: {', '.join(chosen) or 'nothing'})")
        else:
            passed, why = True, ("correctly did not invoke it" if not redirect
                                 else f"correctly routed to {redirect} instead")
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
        def _fail(why: str) -> dict:
            return {"id": case.get("id"), "intent": case.get("intent", "trigger"),
                    "passed": False, "why": why, "selected": [], "skill": skill_name}

        # Read the prompt OUTSIDE the spawn guard. Reading it inside attributed a
        # KeyError in our own data to "submitter failed", which misdirects whoever is
        # debugging a live spawn — and it would have called the submitter's failure path
        # without ever calling the submitter.
        prompt = case.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            results.append(_fail("malformed case: no usable `prompt`"))
            continue
        try:
            transcript = submit(prompt)
        except Exception as e:  # noqa: BLE001 — any submitter failure is a case failure
            results.append(_fail(f"submitter failed: {e}"))
            continue
        results.append(judge(case, skills_selected(transcript), skill_name,
                             responded=transcript_responded(transcript)))
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
        files = discover_eval_files(args.skills_root)
        # Review finding 1: this used to print `total cases: 0` and exit 0, so a mistyped
        # --skills-root read as a successful coverage check over an empty corpus.
        if not files:
            print(f"FAILED: no eval files under {args.skills_root!r}. A gate cannot report "
                  f"success over a corpus it did not find — check the path.")
            return 1
        total = 0
        for f in files:
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
