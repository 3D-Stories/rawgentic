"""Frontmatter `description` guard for every skill (#909, epic #875).

Sibling of `tests/test_wf2_prose_budget.py` (#856/#874), same shape — a pure
violation collector, unit-tested against synthetic frontmatter, then run over
the real tree — but a different surface: the skill *availability listing*, which
loads every skill's frontmatter `description` into EVERY session.

Two Anthropic-published limits motivate it (skill-authoring best practices,
platform.claude.com, fetched 2026-08-04): a `description` maximum of 1,024
characters, and the guidance to keep a SKILL.md body under 500 lines (the body
budget is #856's guard, not this one).

Measured before state — 2026-08-05, tree 1a03b9cb:

    21 skills, 10,784 chars of description AS LOADED.
    pane-handoff  1,174  <-- the only skill over the 1,024 cap
    revalidate-children 991 · adversarial-review 842 · create-issue 811
    peer-consult 690 · interview 607 · epic-post-mortem 590 · ...

WHY LENGTH IS NOT THE ONLY CHECK. In an unquoted YAML plain scalar, " #" opens
a comment, so the loaded description silently loses its tail:

    epic-run      true 534 chars -> loaded 131   (403 lost, 75%)
    pane-handoff  true 1,203     -> loaded 1,174 (29 lost, incl.
                                    "Requires HERDR_ENV=1.")

Both were confirmed twice: by `yaml.safe_load` over the tree, and by a live
Claude Code session's own available-skills listing, which ended epic-run's entry
mid-phrase at exactly that byte. A length-only guard is blind to this by
construction — it sees 131 chars and calls it comfortably compliant while three
quarters of the triggers are dead text. `test_comment_truncation_is_invisible_to_a_length_check`
below is that exact case, pinned as a fixture.

Deliberately NO per-skill budget dict and NO corpus total (unlike #856): the
population is discovered by glob and every member is covered by one constant, so
a new skill is guarded automatically and there is no stale-entry class to
maintain. A total-char budget was the option the owner's scope decision rejected
(D211) — see the design doc for why.
"""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"

# Anthropic's documented `description` maximum. Claude Code does not enforce the
# API-side validation today, which is the only reason an over-cap description
# has been working — so this guard is the enforcement.
DESCRIPTION_MAX_CHARS = 1024

# A scalar opening with any of these cannot have its tail eaten by " #":
# quotes make "#" literal, and block scalars (>, |) have no comment syntax.
SAFE_SCALAR_STARTS = ("'", '"', ">", "|")

# The next top-level frontmatter key ends the description's scalar span.
_NEXT_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*:", re.M)


def frontmatter_text(skill_md: Path) -> str:
    """The raw YAML frontmatter block of a SKILL.md, without the --- fences."""
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return ""
    parts = text.split("---", 2)
    return parts[1] if len(parts) >= 3 else ""


def scalar_source(frontmatter: str) -> str:
    """The verbatim source of the `description:` scalar, value only.

    Spans from just after `description:` to the next top-level key (or the end),
    so a folded/multi-line scalar is captured whole. Returns "" when the key is
    absent.
    """
    match = re.search(r"^description:[ \t]?", frontmatter, re.M)
    if not match:
        return ""
    rest = frontmatter[match.end():]
    nxt = _NEXT_KEY.search(rest)
    return rest[: nxt.start()] if nxt else rest


def describe(name: str, frontmatter: str) -> dict:
    """One record per skill: what loaded, and what the source looked like."""
    try:
        loaded = (yaml.safe_load(frontmatter) or {}).get("description")
    except yaml.YAMLError as exc:  # a broken scalar must fail loudly, not vanish
        loaded = None
        return {"name": name, "description": None, "scalar_source": "",
                "parse_error": str(exc)}
    return {
        "name": name,
        "description": loaded,
        "scalar_source": scalar_source(frontmatter),
        "parse_error": None,
    }


def measured_descriptions() -> list:
    """A record per `skills/*/SKILL.md`, sorted by skill name."""
    return [
        describe(p.parent.name, frontmatter_text(p))
        for p in sorted(SKILLS_DIR.glob("*/SKILL.md"))
    ]


def description_violations(records: list) -> list:
    """All description violations, one message per violation.

    Four classes: UNPARSEABLE (frontmatter YAML is broken), MISSING (absent or
    blank description), OVER CAP (loaded description longer than the documented
    maximum), and TRUNCATED (a plain scalar whose tail YAML discards as a
    comment). Every message names the skill; the two measurable classes carry
    the actual count and the delta. Never quotes a whole description back.
    """
    violations = []
    if not records:
        # An empty measurement is a broken measurement, never a clean tree
        # (#856's F4 lesson: a mis-resolved glob must not read as "all good").
        return [
            "EMPTY CORPUS: no skills/*/SKILL.md files were discovered — the "
            "glob resolved to nothing, which is a broken measurement and never "
            "a clean result. Check the path."
        ]
    for rec in records:
        name = rec["name"]
        if rec.get("parse_error"):
            violations.append(
                f"UNPARSEABLE: {name}'s frontmatter is not valid YAML "
                f"({rec['parse_error'].splitlines()[0]}) — the skill may fail to "
                f"load. A single-quoted scalar must double any internal "
                f"apostrophe."
            )
            continue
        desc = rec["description"]
        if desc is None:
            # Absent key or an explicit `description:` null — both are "there is
            # no description", which is a clearer diagnosis than its parsed type.
            violations.append(
                f"MISSING: {name} has no non-empty frontmatter `description` — "
                f"it is what the availability listing keys on, so a skill "
                f"without one cannot be triggered reliably."
            )
            continue
        if not isinstance(desc, str):
            # A YAML sequence/mapping/number/bool/null all parse fine and would
            # sail past a length check, so the TYPE is asserted separately and
            # the message names what was actually parsed.
            violations.append(
                f"WRONG TYPE: {name}'s `description` parsed as "
                f"{type(desc).__name__}, not a string — e.g. `description: [foo]` "
                f"is a YAML sequence. The availability listing needs a plain "
                f"string scalar; quote the value."
            )
            continue
        if not desc.strip():
            violations.append(
                f"MISSING: {name} has no non-empty frontmatter `description` — "
                f"it is what the availability listing keys on, so a skill "
                f"without one cannot be triggered reliably."
            )
            continue
        if len(desc) > DESCRIPTION_MAX_CHARS:
            violations.append(
                f"OVER CAP: {name}'s description is {len(desc)} chars — "
                f"{len(desc) - DESCRIPTION_MAX_CHARS} over the "
                f"{DESCRIPTION_MAX_CHARS}-char documented maximum. Move workflow "
                f"summary and install notes into the body; keep the triggers."
            )
        source = rec["scalar_source"].strip()
        if source and not source.startswith(SAFE_SCALAR_STARTS) and " #" in source:
            lost = source.split(" #", 1)[1]
            violations.append(
                f"TRUNCATED: {name}'s description is an unquoted YAML scalar "
                f"containing ' #', so YAML discards everything from there as a "
                f"comment — {len(lost) + 2} chars never reach the harness, "
                f"starting at ' #{lost[:40]}'. Single-quote the scalar (doubling "
                f"internal apostrophes) so the text survives."
            )
    return violations


# --- unit tests for the collector (synthetic frontmatter) -----------------


def _fm(desc_line: str, name: str = "demo") -> str:
    return f"\nname: {name}\n{desc_line}\nargument-hint: x\n"


def test_clean_descriptions_yield_no_violations():
    records = [describe("demo", _fm("description: Use when the user asks for X."))]
    assert description_violations(records) == []


def test_empty_measurement_is_a_violation_not_a_pass():
    assert description_violations([])
    assert "EMPTY CORPUS" in description_violations([])[0]


def test_over_cap_names_skill_and_delta():
    long_desc = "x" * (DESCRIPTION_MAX_CHARS + 7)
    records = [describe("big", _fm(f"description: {long_desc}", name="big"))]
    v = description_violations(records)
    assert len(v) == 1, v
    assert v[0].startswith("OVER CAP:")
    assert "big" in v[0] and str(DESCRIPTION_MAX_CHARS + 7) in v[0] and " 7 over" in v[0]


def test_missing_description_is_named():
    records = [describe("bare", "\nname: bare\nargument-hint: x\n")]
    v = description_violations(records)
    assert len(v) == 1 and v[0].startswith("MISSING:"), v


def test_blank_description_is_named():
    records = [describe("blank", _fm("description: '   '", name="blank"))]
    v = description_violations(records)
    assert len(v) == 1 and v[0].startswith("MISSING:"), v


@pytest.mark.parametrize(
    "label,line,parsed_type",
    [
        ("sequence", "description: [foo]", "list"),
        ("mapping", "description: {a: b}", "dict"),
        ("numeric", "description: 42", "int"),
        ("boolean", "description: true", "bool"),
    ],
)
def test_non_string_description_is_rejected_with_its_type(label, line, parsed_type):
    """A non-string `description` parses cleanly and passes a length check.

    `description: [foo]` is non-empty, is one element long, is under the cap and
    contains no ' #', so a guard that only measures length accepts frontmatter
    the loader cannot use. The type is therefore asserted on its own, and the
    message names what was actually parsed.
    """
    records = [describe(label, _fm(line, name=label))]
    v = description_violations(records)
    assert len(v) == 1 and v[0].startswith("WRONG TYPE:"), v
    assert parsed_type in v[0], v


def test_explicit_null_description_reads_as_missing_not_a_type_error():
    """`description:` with no value is absent, not a type mistake."""
    records = [describe("nulled", _fm("description:", name="nulled"))]
    v = description_violations(records)
    assert len(v) == 1 and v[0].startswith("MISSING:"), v


def test_comment_truncation_is_invisible_to_a_length_check():
    """The fixture that proves a length-only guard is the wrong surface.

    This description PARSES CLEANLY and lands far under the cap, so a cap check
    alone passes it — while everything after ' #' is silently dead text. This is
    epic-run's live defect in miniature (403 of 534 chars lost).
    """
    records = [describe("eaten", _fm("description: trigger A # trigger B", name="eaten"))]
    loaded = records[0]["description"]
    assert loaded == "trigger A", loaded          # YAML already ate the tail
    assert len(loaded) < DESCRIPTION_MAX_CHARS    # so the cap check is happy
    v = description_violations(records)
    assert len(v) == 1 and v[0].startswith("TRUNCATED:"), v
    assert "eaten" in v[0] and "trigger B" in v[0]


def test_single_quoted_scalar_keeps_its_hash():
    records = [describe("ok", _fm("description: 'trigger A #5 and trigger B'", name="ok"))]
    assert records[0]["description"] == "trigger A #5 and trigger B"
    assert description_violations(records) == []


def test_double_quoted_scalar_keeps_its_hash():
    records = [describe("ok", _fm('description: "trigger A #5 and B"', name="ok"))]
    assert records[0]["description"] == "trigger A #5 and B"
    assert description_violations(records) == []


def test_folded_block_scalar_keeps_its_hash():
    fm = "\nname: ok\ndescription: >-\n  trigger A #5 and\n  trigger B\nargument-hint: x\n"
    records = [describe("ok", fm)]
    assert "#5" in records[0]["description"]
    assert description_violations(records) == []


def test_multiline_plain_scalar_continuation_hash_is_caught():
    """The case BOTH naive implementations get wrong (Step-4 review F3).

    A line-only check inspects `description: trigger A` and sees no ' #', so it
    passes while the continuation line's tail is silently eaten. The scalar span
    must run to the next TOP-LEVEL key, not to the end of the first line.
    """
    fm = "\nname: demo\ndescription: trigger A\n  and trigger B # trigger C\nargument-hint: x\n"
    records = [describe("demo", fm)]
    assert records[0]["description"] == "trigger A and trigger B"   # YAML ate 'trigger C'
    v = description_violations(records)
    assert len(v) == 1 and v[0].startswith("TRUNCATED:"), v
    assert "trigger C" in v[0]


def test_another_fields_comment_is_not_blamed_on_the_description():
    """The opposite naive failure: scanning the whole frontmatter over-rejects.

    A comment on `argument-hint:` is legitimate and belongs to that field, so the
    scalar span must END at the next top-level key.
    """
    fm = "\nname: demo\ndescription: trigger A\nargument-hint: a path # optional\n"
    records = [describe("demo", fm)]
    assert records[0]["description"] == "trigger A"
    assert description_violations(records) == []


def test_hash_without_leading_space_is_not_a_comment():
    """`(#732)` is safe — YAML only opens a comment on ' #'."""
    records = [describe("ok", _fm("description: Use for a handoff (#732) now.", name="ok"))]
    assert records[0]["description"].endswith("(#732) now.")
    assert description_violations(records) == []


def test_unparseable_frontmatter_is_named_not_swallowed():
    records = [describe("broken", _fm("description: 'it's broken'", name="broken"))]
    v = description_violations(records)
    assert len(v) == 1 and v[0].startswith("UNPARSEABLE:"), v


# --- the live tree --------------------------------------------------------


def test_every_skill_description_is_within_budget_and_intact():
    violations = description_violations(measured_descriptions())
    assert not violations, "\n".join(violations)


def test_the_corpus_was_actually_measured():
    """Guards the guard: a glob that finds nothing must never read as clean."""
    records = measured_descriptions()
    assert len(records) >= 20, f"only {len(records)} skills discovered"


# --- trigger-phrase survival (the diet's actual risk) --------------------

# pane-handoff is the one skill this issue shortens, and its evals exist
# BECAUSE it failed to trigger seven times in 36 hours (#700). Its dictated
# variants are an owner decision (#732: "herder" means herdr, "pain" means
# pane). A diet that drops one is exactly the regression nobody would notice —
# there is no error, the skill simply never fires.
#
# NOT derived from evals.json programmatically, deliberately: of its 6 eval
# cases only 2 are verbatim description phrases, and case 6 is a NEGATIVE case
# ("does NOT run the ad-hoc handoff"), so "every eval prompt must appear in the
# description" would be both wrong and unsatisfiable.
PANE_HANDOFF_REQUIRED_PHRASES = (
    "pass off session in new herdr pane",
    "do the herdr session pane pass off",
    "passoff",
    "pass the session/prompt/goal over",
    "pass everything over",
    "send all the information over to a new pane",
    "send this over to a new pain",
    "hand it over",
    "hand off",
    "handoff",
    "start a new herdr pane and fix the bug",
    "create a new pane and resume with the prompt and goal",
    "clear the context into a new session and pass in the prompt and the goal",
    "use the herder rawgentic skill",
    "resume in a new pane",
    "herder",
    "pain",
)


def missing_phrases(description: str, required: tuple) -> list:
    """Required trigger phrases absent from a description, in order."""
    low = (description or "").lower()
    return [p for p in required if p.lower() not in low]


def test_missing_phrases_detects_a_dropped_variant():
    assert missing_phrases("only handoff here", ("handoff", "pain")) == ["pain"]
    assert missing_phrases("handoff and pain", ("handoff", "pain")) == []


def test_pane_handoff_keeps_every_dictated_trigger_variant():
    fm = frontmatter_text(SKILLS_DIR / "pane-handoff" / "SKILL.md")
    desc = (yaml.safe_load(fm) or {}).get("description") or ""
    missing = missing_phrases(desc, PANE_HANDOFF_REQUIRED_PHRASES)
    assert not missing, (
        "pane-handoff lost trigger phrasings its evals depend on (#700/#732): "
        + ", ".join(repr(m) for m in missing)
    )


def test_adversarial_review_keeps_the_key_its_evals_use():
    """Its 3 eval cases all invoke `/rawgentic:adversarial-review <path>`.

    Only the install-note tail is removed from this description, so the one
    trigger-bearing token worth pinning is the invoke key itself.
    """
    fm = frontmatter_text(SKILLS_DIR / "adversarial-review" / "SKILL.md")
    desc = (yaml.safe_load(fm) or {}).get("description") or ""
    assert "/rawgentic:adversarial-review" in desc


def test_pane_handoff_keeps_its_environment_precondition():
    """The 29 chars YAML was eating included this line."""
    fm = frontmatter_text(SKILLS_DIR / "pane-handoff" / "SKILL.md")
    desc = (yaml.safe_load(fm) or {}).get("description") or ""
    assert "HERDR_ENV=1" in desc, (
        "pane-handoff's description must state its own precondition; it was "
        "silently truncated away before #909"
    )


def test_epic_run_description_survives_to_its_final_clause():
    """epic-run lost 403 of 534 chars to a YAML comment before #909."""
    fm = frontmatter_text(SKILLS_DIR / "epic-run" / "SKILL.md")
    desc = (yaml.safe_load(fm) or {}).get("description") or ""
    assert "write me a goal for the epic" in desc, "epic-run's mined triggers are truncated"
    assert desc.rstrip().endswith("(that is /rawgentic:create-issue)."), (
        "epic-run's description does not reach its final clause — the YAML "
        f"comment truncation is back. Loaded {len(desc)} chars."
    )
