"""Drift guards for WF5 adversarial-review registration + integration (issue #77).

Covers Task 11 (registration + count strings) and Task 12 (WF2/WF3 config-gated
invocation present in the expected steps; consolidation WF5 text).
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

from tests.corpus import skill_corpus

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = REPO_ROOT / "skills"


# --- registration ---

def test_skill_dir_and_frontmatter_exist():
    skill = SKILLS_DIR / "adversarial-review" / "SKILL.md"
    assert skill.exists()
    # LOCATION pin: frontmatter must be in SKILL.md itself (registration);
    # the prose blocks are content pins over the corpus.
    assert "name: adversarial-review" in skill.read_text()
    corpus = skill_corpus("adversarial-review")
    assert "<config-loading>" in corpus
    assert "<completion-gate>" in corpus


def test_marketplace_registers_skill():
    mp = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    skills = mp["plugins"][0]["skills"]
    assert "./skills/adversarial-review" in skills
    # alphabetical placement: add-exception < adversarial-review
    # (admit-to-org-runners sat between them until #788 extracted it to the
    # claude-skills repo; the whitelist must stay sorted regardless of membership)
    assert skills.index("./skills/adversarial-review") == skills.index("./skills/add-exception") + 1
    assert skills == sorted(skills)


# --- version surfaces + changelog tail tokens (#822) -------------------------
#
# Fail mode: fail-CLOSED (CLAUDE.md §3). These are release-contract guards; a
# surface that cannot be read, or a changelog that cannot be parsed, must FAIL
# rather than pass vacuously — the whole point is finding out here instead of
# after a release ships with a stale surface.
#
# THREE surfaces must agree, not four: #866 M0d retired the fourth (a version
# constant in the deleted phase-package), and CLAUDE.md now reads "The version
# lives in THREE surfaces that must match". Two of them are files; the third is
# the pinned literal below, which is why bumping a version means editing a TEST —
# a scoped local run that skips this file is exactly how the miss reaches CI.
# Deliberately not naming the retired module here: tests/test_retirement_tripwire.py
# scans active surfaces for retired vocabulary and flagged the earlier wording.
EXPECTED_PLUGIN_VERSION = "3.141.10"

VERSION_SURFACE_FILES = (
    ".claude-plugin/plugin.json",
    "plugins/rawgentic/.codex-plugin/plugin.json",
)

# Both tail tokens the README changelog shape mandates. Presence searches, and
# deliberately WITHOUT a trailing-boundary assertion: #796 rev 4 proposed
# `(?![\w.])`, which forbids a trailing period — measured against this corpus it
# misses 198 of 224 token-bearing entries, because `no diagram REV.` ends a
# sentence. That fabricated-verification error is what killed the earlier design,
# so these regexes are validated against the live corpus by a test below.
#
# The separator is BOTH forms: 45 live entries use ASCII `->`, the rest `→`.
#
# `(?!\w)` after REV is NOT the rev-4 trap: rev 4 used `(?![\w.])`, which forbids a
# trailing PERIOD. `(?!\w)` still accepts `no diagram REV.` while rejecting longer
# words like `REVISION`. Operands are numeric with the live `+Nskip` suffix rather
# than a permissive `\S+`, so a placeholder cannot pose as a delta. Both forms
# measured against the live corpus: 0 misses on 225 and 227 token-bearing entries.
SUITE_DELTA_RE = re.compile(
    r"Suite\s+\d+(?:\+\d+skip)?\s*(?:→|->)\s*\d+(?:\+\d+skip)?")
# `(?<!not )` blocks a NEGATED declaration. Without it a tail reading
# "Decision is not no diagram REV. Suite 1→2." satisfied every check while expressly
# WITHHOLDING the required decision — an unanchored substring search cannot tell an
# affirmation from its negation. Costs nothing on the live corpus: 225 token-bearing
# entries, 0 new misses (#822, adversarial diff layer).
DIAGRAM_DECISION_RE = re.compile(
    r"(?<!not )(?:no diagram REV|diagram REVs?\s+\d+\.\d+\.\d+)(?!\w)")

# How much of an entry counts as its TAIL for the newest-entry gate. The tokens are
# a tail convention, and searching a whole entry is fail-open: an entry that merely
# DISCUSSES `no diagram REV` in prose (this one does) would satisfy the guard with
# its real decision deleted. Measured: 207 of 221 fully-conforming live entries carry
# both tokens inside the last 200 characters, which is why the tail rule gates the
# NEWEST entry only while the corpus test below stays presence-based.
TAIL_CHARS = 200

# Legacy entries that carry a token but word it differently. NAMED individually so
# a NEW miss fails loudly instead of being absorbed by loosening the regex.
LEGACY_SUITE_EXCEPTIONS = frozenset({"v3.31.1"})       # "Suite unchanged 2556+1skip."
LEGACY_DIAGRAM_EXCEPTIONS = frozenset({"v3.121.0"})    # "the diagram REV is DEFERRED to M0d"


def _changelog_entries():
    """[(version, entry_text)] newest-first. Fail-CLOSED on an unreadable changelog."""
    readme = (REPO_ROOT / "README.md").read_text()
    # EXACT heading match, not a substring test. `"## Changelog" in readme` is
    # satisfied by `## Changelog Archive` or `## Changelog-old`, so a renamed or
    # duplicated heading passed while the guards silently parsed the wrong section —
    # the opposite of the fail-closed behaviour claimed here (#822, adversarial layer).
    heads = list(re.finditer(r"(?m)^## Changelog$", readme))
    assert len(heads) == 1, (
        f"README.md must have EXACTLY ONE `## Changelog` heading; found {len(heads)}. "
        "The changelog guards cannot decide which section is authoritative, so they "
        "fail rather than pass vacuously."
    )
    body = readme[heads[0].start():]
    # Split on EVERY level-3 heading, not on a `### v` lookahead. Splitting on the
    # narrower lookahead was fail-OPEN and cross-model review caught it: a malformed
    # newest heading (say `### 3.125.2`, missing the v) is not a split point, so that
    # entry got glued into the preamble chunk that `[1:]` discards — the previous
    # release silently became entries[0] and the newest-entry gate then validated a
    # STALE entry and PASSED. Proven before the fix: malforming the newest heading
    # made entries[0] resolve to v3.125.1 (#822).
    entries = re.split(r"\n(?=### )", body)[1:]
    assert entries, (
        "README.md's Changelog section parsed to ZERO entries — refusing to pass "
        "vacuously (a heading-shape change would otherwise silently disable these guards)"
    )
    out = []
    for entry in entries:
        head = entry.splitlines()[0]
        m = re.match(r"### (v\d+\.\d+\.\d+) \(\d{4}-\d{2}-\d{2}\)$", head)
        assert m, (
            f"malformed changelog heading {head!r} — must be `### vX.Y.Z (YYYY-MM-DD)`. "
            "A malformed heading must FAIL here rather than vanish from the entry list "
            "and let a stale entry pass as the newest."
        )
        out.append((m.group(1), entry))
    return out


def test_plugin_version_bumped():
    """All THREE version surfaces must agree, and disagreement names each stale one.

    Previously this asserted only `.claude-plugin/plugin.json`, so the codex-plugin
    copy — the one CLAUDE.md §4 mistake #1 says everyone forgets — was pinned by
    nothing. Verified before this change: desyncing it to 9.9.9 left the whole file
    green at 36 passed.
    """
    found = {}
    for rel in VERSION_SURFACE_FILES:
        path = REPO_ROOT / rel
        assert path.exists(), f"version surface {rel} is missing"
        found[rel] = json.loads(path.read_text())["version"]
    found["tests/hooks/test_adversarial_review_registration.py::EXPECTED_PLUGIN_VERSION"] = (
        EXPECTED_PLUGIN_VERSION)

    stale = {k: v for k, v in found.items() if v != EXPECTED_PLUGIN_VERSION}
    assert not stale, (
        "version surfaces disagree — each stale surface and its value:\n"
        + "\n".join(f"  {k}: {v!r} (expected {EXPECTED_PLUGIN_VERSION!r})"
                    for k, v in sorted(stale.items()))
        + "\nAll THREE must be bumped together (#866 M0d retired the canary fourth)."
    )


def test_no_unregistered_plugin_manifest_version_surface():
    """Discovery: every tracked plugin manifest must be a REGISTERED version surface.

    `VERSION_SURFACE_FILES` is a static tuple, so on its own it stays green if a THIRD
    plugin manifest appears — the guard would then be checking two of three surfaces
    and calling that agreement (#822, adversarial layer). Enumerating the tracked
    manifests turns "three surfaces" from a comment into something checked.

    Scope stated honestly: this discovers plugin MANIFESTS (`*plugin.json`), which is
    the class that has actually drifted here. It cannot discover an arbitrary version
    constant declared in source; that remains a convention, not a mechanical guarantee.
    """
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT,
        capture_output=True, text=True, check=True).stdout.split()
    manifests = sorted(p for p in out if p.endswith("plugin.json"))
    # marketplace.json is a registry of skills, not a versioned manifest surface.
    expected = sorted(VERSION_SURFACE_FILES)
    assert manifests == expected, (
        f"tracked plugin manifests {manifests} != registered version surfaces "
        f"{expected}. A new manifest must be added to VERSION_SURFACE_FILES (and the "
        "THREE-surfaces prose updated) or the guard is checking a subset and calling "
        "it agreement."
    )


def test_newest_changelog_entry_carries_both_tail_tokens():
    """The newest entry must carry the diagram decision AND the `Suite old→new` delta.

    Scoped to the NEWEST entry on purpose: the convention is not retroactive — 112
    live entries predate it and carry no `Suite` token at all, so gating every entry
    would fail on a third of the corpus.

    Checked against the entry's TAIL, not the whole entry. Searching the whole entry
    is fail-open, and this very changelog proves it: the v3.125.2 entry DISCUSSES
    `no diagram REV` in prose, so deleting its real decision would have left the guard
    green. Cross-model review caught that (#822).
    """
    version, entry = _changelog_entries()[0]
    # Tie the checked entry to the RELEASE. Without this the guard validated
    # whichever entry happened to be first, so bumping all three version surfaces
    # while forgetting the changelog entry left every guard green with the current
    # release's tail tokens never checked at all (#822, adversarial layer). This is
    # also the mechanical half of the repo's "one PR = one bump = one changelog
    # entry" rule.
    assert version == f"v{EXPECTED_PLUGIN_VERSION}", (
        f"newest changelog entry is {version} but the plugin version is "
        f"v{EXPECTED_PLUGIN_VERSION} — every bump needs its own changelog entry, "
        "newest-first"
    )
    tail = entry.rstrip()[-TAIL_CHARS:]
    assert DIAGRAM_DECISION_RE.search(tail), (
        f"newest changelog entry {version} carries no diagram decision in its last "
        f"{TAIL_CHARS} characters. State it explicitly either way, at the END: "
        "`no diagram REV` or `diagram REV <X.Y.Z>`."
    )
    assert SUITE_DELTA_RE.search(tail), (
        f"newest changelog entry {version} carries no `Suite <old>→<new>` delta in its "
        f"last {TAIL_CHARS} characters (numeric operands, optional `+Nskip`)."
    )
    # Anchored: the delta is the LAST thing in the entry, so it cannot be satisfied by
    # an example quoted mid-prose.
    assert re.search(r"Suite\s+\d+(?:\+\d+skip)?\s*(?:→|->)\s*\d+(?:\+\d+skip)?\.?\s*$",
                     entry.rstrip()), (
        f"newest changelog entry {version} must END with its `Suite <old>→<new>` delta"
    )


def test_changelog_tail_regexes_match_the_live_corpus():
    """AC3: validate both regexes against the LIVE corpus, not a snapshot.

    This is the check whose absence killed #796 rev 4: it claimed "verified against
    the live corpus" without running anything, and the proposed pattern would have
    rejected almost every real release. Any entry carrying a token whose regex does
    not match is either a regex bug or a new wording — both must surface here.
    """
    for version, entry in _changelog_entries():
        if "diagram REV" in entry and version not in LEGACY_DIAGRAM_EXCEPTIONS:
            assert DIAGRAM_DECISION_RE.search(entry), (
                f"{version} mentions a diagram REV but DIAGRAM_DECISION_RE does not "
                "match it — fix the regex or add the version to "
                "LEGACY_DIAGRAM_EXCEPTIONS with its wording in a comment"
            )
        if "Suite " in entry and version not in LEGACY_SUITE_EXCEPTIONS:
            assert SUITE_DELTA_RE.search(entry), (
                f"{version} mentions a Suite delta but SUITE_DELTA_RE does not match "
                "it — fix the regex or add the version to LEGACY_SUITE_EXCEPTIONS "
                "with its wording in a comment"
            )


def test_legacy_tail_token_exceptions_stay_frozen():
    """The legacy exception sets must not grow — they cover HISTORY, which is fixed.

    Without this, the corpus guard above has an easy escape: a NEW entry whose token
    the regex does not match could be waved through by adding its version to an
    exception set instead of fixing the entry or the regex. That cannot be legitimate.
    Changelog history is append-at-the-top and immutable below, so the set of OLD
    entries wording a token unconventionally is closed. Every new entry must satisfy
    the regexes outright.

    If this fails, the fix is almost never to raise the number: it is to correct the
    new entry's tail token, or to widen the regex and prove the widening against the
    live corpus.
    """
    assert LEGACY_SUITE_EXCEPTIONS == frozenset({"v3.31.1"}), (
        f"LEGACY_SUITE_EXCEPTIONS changed to {sorted(LEGACY_SUITE_EXCEPTIONS)}. These "
        "cover immutable history; a new member means a NEW entry dodged the guard."
    )
    assert LEGACY_DIAGRAM_EXCEPTIONS == frozenset({"v3.121.0"}), (
        f"LEGACY_DIAGRAM_EXCEPTIONS changed to {sorted(LEGACY_DIAGRAM_EXCEPTIONS)}. "
        "These cover immutable history; a new member means a NEW entry dodged the guard."
    )
    # Non-vacuity: the excepted versions must still EXIST in the changelog, so a
    # rename or deletion cannot leave a dead exception quietly widening nothing.
    versions = {v for v, _ in _changelog_entries()}
    for excepted in LEGACY_SUITE_EXCEPTIONS | LEGACY_DIAGRAM_EXCEPTIONS:
        assert excepted in versions, (
            f"{excepted} is excepted but no longer present in the changelog — drop the "
            "dead exception rather than leaving it to mask a future entry"
        )


def test_tail_token_regexes_accept_a_trailing_period():
    """AC3's named trap, pinned explicitly: a terminating period must still match.

    `no diagram REV.` ends a sentence in the overwhelming majority of live entries
    (196 with the period against 14 without), so a pattern that forbids one rejects
    the corpus it is meant to validate.
    """
    for probe in ("no diagram REV.", "diagram REV 3.125.0.", "WF2 diagram REVs 3.93.0."):
        assert DIAGRAM_DECISION_RE.search(probe), f"trailing period broke: {probe!r}"
    for probe in ("Suite 4791→4793.", "Suite 6497->6511.",
                  "Suite 2409+1skip→2411+1skip."):
        assert SUITE_DELTA_RE.search(probe), f"trailing period broke: {probe!r}"


def test_descriptions_consistent_count():
    """plugin.json + marketplace.json descriptions reflect v3.0.0 (#161):
    6 active SDLC workflows, stubs removed."""
    plugin = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())
    mp = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    for desc in (plugin["description"], mp["plugins"][0]["description"]):
        assert "9 SDLC workflow skills" in desc
        assert "deprecated stub" not in desc.lower()   # stubs removed at v3.0.0 (#161)
        assert "12 SDLC workflow skills" not in desc


def test_readme_count_strings_updated():
    readme = (REPO_ROOT / "README.md").read_text()
    assert "9 SDLC workflow skills" in readme
    assert "12 SDLC workflow skills" not in readme
    n_skills = len(list((REPO_ROOT / "skills").glob("*/SKILL.md")))
    assert f"provides {n_skills} skills" in readme
    # #271 reviewer note: computed==computed loses the absolute floor a
    # deleted-everywhere skill would have tripped. The plugin description's
    # human-readable breakdown ("6 SDLC + 6 workspace + 1 planning + 2
    # security") is the remaining hand-written tally — assert it sums to the
    # disk count so a silent shrink still fails somewhere.
    import re as _re2
    desc = json.loads(
        (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text()
    )["description"]
    breakdown = [int(n) for n in _re2.findall(
        r"(\d+) (?:SDLC workflow|workspace management|planning|security)", desc)]
    assert len(breakdown) == 4 and sum(breakdown) == n_skills, (
        f"plugin description breakdown {breakdown} must sum to the "
        f"{n_skills} skills on disk"
    )
    # #910: this literal is no longer the guard — `pin:config-driven` in
    # hooks/skill_registration_check.py computes the count from the tree and
    # sweeps every copy, including this one. It stayed at 8 while the corpus
    # carried 9 precisely because consensus between two stale copies looked
    # like agreement.
    assert "All 9 config-driven skills" in readme
    # #271: computed from disk, never a hand-maintained literal. A skill
    # "has evals" iff evals.json exists in its own evals/ dir or its
    # -workspace evals/ dir.
    skills = sorted(p.parent.name for p in (REPO_ROOT / "skills").glob("*/SKILL.md"))
    have = {
        s for s in skills
        if (REPO_ROOT / "skills" / s / "evals" / "evals.json").exists()
        or (REPO_ROOT / "skills" / f"{s}-workspace" / "evals" / "evals.json").exists()
    }
    assert f"{len(have)}/{len(skills)} skills have evals.json" in readme, (
        f"README must render the computed evals fraction "
        f"{len(have)}/{len(skills)}"
    )
    # Membership cross-check: every skill README names as having NO evals
    # must really lack them (C14: the count was right, the membership wrong)
    for name in sorted(set(skills) - have):
        assert f"`{name}`" in readme, (
            f"README's have-none list must name {name} (computed complement)"
        )
    import re as _re
    m = _re.search(
        r"skills have evals\.json[^)]*?the lightweight (.*?) skills have none",
        readme, _re.S)
    assert m, "README must carry the have-none list in its evals sentence"
    listed_none = set(_re.findall(r"`([a-z0-9-]+)`", m.group(1)))
    assert listed_none == (set(skills) - have - {"peer-consult"}), (
        f"README have-none list {sorted(listed_none)} != computed "
        f"{sorted(set(skills) - have - {'peer-consult'})} (peer-consult is "
        f"called out separately as a stub)"
    )
    assert "12 workspace management" in readme  # #113 — README count must match plugin/marketplace descriptions


def test_readme_changelog_has_no_spliced_headings():
    """Guard against the recurring changelog-insertion garble (#192/#193/#194):
    inserting a new entry above the previous heading spliced a `### vX.Y.Z`
    into the middle of a bullet, e.g. `...goal guard### v3.5.0 (2026-07-05)`.
    A lowercase letter immediately followed by `###` never occurs in clean prose."""
    import re
    readme = (REPO_ROOT / "README.md").read_text()
    offenders = re.findall(r"[A-Za-z0-9]###", readme)
    assert not offenders, f"spliced changelog heading(s) detected: {offenders}"


def test_marketplace_skill_dirs_all_exist():
    """Every registered skill path must resolve to a real SKILL.md (no dangling entry)."""
    mp = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    for rel in mp["plugins"][0]["skills"]:
        assert (REPO_ROOT / rel / "SKILL.md").exists(), f"missing {rel}/SKILL.md"


# --- diff artifact type support in SKILL.md (issue #131, Task 4) ---

def test_description_mentions_diff_review_and_drops_not_for_clause():
    # frontmatter is a LOCATION pin (SKILL.md); the dropped clause must be
    # absent from the whole corpus.
    frontmatter = (SKILLS_DIR / "adversarial-review" / "SKILL.md").read_text().split("---")[1]
    assert "diff" in frontmatter.lower()
    assert "NOT for reviewing code diffs" not in skill_corpus("adversarial-review")


def test_constants_supported_artifact_types_includes_diff():
    text = skill_corpus("adversarial-review")
    constants = _section(text, "<constants>", "</constants>")
    line = next(l for l in constants.splitlines() if l.startswith("SUPPORTED_ARTIFACT_TYPES:"))
    types = [t.strip() for t in line.split(":", 1)[1].split(",")]
    assert "diff" in types


def test_body_documents_runner_result_findings():
    # M0b (#866): the engine's --findings-json sidecar retired — the runner
    # result JSON is the findings carrier; the report renders from it.
    text = skill_corpus("adversarial-review")
    assert "hooks/review_runner.py review-artifact" in text
    assert "render_report_md" in text


def test_step1_autodetect_mentions_patch_and_diff_globs():
    text = skill_corpus("adversarial-review")
    step1 = _section(text, "## Step 1:", "## Step 2:")
    assert "*.patch" in step1
    assert "*.diff" in step1
    assert "diff" in step1.lower()


def test_data_handling_mentions_diff_secret_density_and_egress_classifier():
    text = skill_corpus("adversarial-review")
    dh = _section(text, "<data-handling>", "</data-handling>")
    low = dh.lower()
    assert "raw source code" in low
    assert "egress classifier" in low
    assert "non-blocking" in low


# --- consolidation doc ---

def test_consolidation_lists_wf5_adversarial_review():
    doc = (REPO_ROOT / "docs" / "consolidation.md").read_text()
    assert "Adversarial Review" in doc
    # WF5 should no longer be described purely as a reserved "Code Review" gap
    assert "WF5 is **Adversarial Review**" in doc or "| WF5" in doc


def test_design_doc_exists():
    assert (REPO_ROOT / "docs" / "design" / "workflow-adversarial-review.md").exists()


# --- WF2 / WF3 integration (config-gated invocation present in expected steps) ---

def _section(text: str, header: str, next_header: str | None) -> str:
    start = text.index(header)
    end = text.index(next_header, start) if next_header else len(text)
    return text[start:end]


def test_wf2_invokes_in_step4_and_step6():
    text = skill_corpus("implement-feature")
    step4 = _section(text, "## Step 4:", "## Step 5:")
    step6 = _section(text, "## Step 6:", "## Step 7:")
    for section, name in ((step4, "Step 4"), (step6, "Step 6")):
        assert "adversarial-review" in section.lower(), f"WF2 {name} missing adversarial-review invocation"
        assert "is-enabled" in section, f"WF2 {name} missing config gate (is-enabled)"


def test_wf2_step4_is_fast_path_gated():
    text = skill_corpus("implement-feature")
    step4 = _section(text, "## Step 4:", "## Step 5:")
    assert "fast_path_eligible == false" in step4


def test_wf2_reuses_existing_design_loopback_not_new_source():
    """Decision A: adversarial design flaws consume the existing 'design' counter."""
    text = skill_corpus("implement-feature")
    step4 = _section(text, "## Step 4:", "## Step 5:")
    assert '"design"' in step4 or "design` loop-back" in step4
    # must NOT introduce a new 'adversarial' loopback source
    assert '"adversarial"' not in step4


def test_wf3_invokes_in_step4_default_off():
    text = skill_corpus("fix-bug")
    step4 = _section(text, "## Step 4:", "## Step 5:")
    assert "adversarial-review" in step4.lower()
    assert "is-enabled" in step4
    assert "DEFAULT-OFF" in step4 or "default-off" in step4.lower()
    # lightweight rationale preserved
    assert "Lightweight reflect ONLY" in step4


def test_plan_lib_has_no_adversarial_loopback_source():
    """Decision A: no new plan_lib loopback source was added."""
    plan_lib = (REPO_ROOT / "hooks" / "plan_lib.py").read_text()
    assert '"adversarial"' not in plan_lib


def test_setup_has_step_2d():
    text = skill_corpus("setup")
    assert "Step 2d" in text
    assert "adversarialReview" in text


# --- WF1 / WF4 integration (issue #79) ---

def test_wf1_invokes_in_step4_default_off():
    text = skill_corpus("create-issue")
    step4 = _section(text, "## Step 4:", "## Step 5:")
    assert "adversarial-review" in step4.lower(), "WF1 Step 4 missing adversarial-review invocation"
    assert "is-enabled" in step4, "WF1 Step 4 missing config gate (is-enabled)"
    assert "create-issue" in step4, "WF1 hook must gate on the 'create-issue' skill name"
    assert "default-off" in step4.lower() or "DEFAULT-OFF" in step4


def test_wf1_uses_no_plan_lib_loopback():
    """WF1 has no plan_lib loopback — its hook must NOT *invoke* consume_loopback.

    (The prose may mention `consume_loopback` to say it is NOT used; we assert there
    is no actual call, i.e. no `consume_loopback(` invocation.)
    """
    text = skill_corpus("create-issue")
    step4 = _section(text, "## Step 4:", "## Step 5:")
    assert "consume_loopback(" not in step4


def test_wf4_is_removed():
    """WF4 removed at v3.0.0 (#161): the skill dir is gone entirely — its old
    Step 4 adversarial integration cannot half-survive a resurrection either
    (tests/test_v3_removals.py pins the removal)."""
    assert not (REPO_ROOT / "skills" / "refactor").exists()


def test_setup_offers_surviving_workflows():
    """#160: refactor (WF4) is deprecated — setup's Step 2d offer detail lives in
    references/integrations.md (LOCATION pin: the corpus slice between the spine's
    '## Step 2d:' and '## Step 3:' headings resolves to the spine SUMMARY only, so
    this reads the reference file directly to guard the real offer list)."""
    detail = (SKILLS_DIR / "setup" / "references" / "integrations.md").read_text()
    for name in ("implement-feature", "fix-bug", "create-issue"):
        assert name in detail, f"setup Step 2d detail must offer {name}"
    # the example config must not present refactor as a live workflow
    assert '"workflows": ["implement-feature", "fix-bug"]' in detail
    assert '"refactor"]' not in detail
    # spine summary also names no refactor offer
    spine = (SKILLS_DIR / "setup" / "SKILL.md").read_text()
    step2d = _section(spine, "## Step 2d:", "## Step 3:")
    assert "refactor" not in step2d

def test_adversarial_review_evals_exist_and_valid():
    evals_path = SKILLS_DIR / "adversarial-review-workspace" / "evals" / "evals.json"
    assert evals_path.exists(), "missing skills/adversarial-review-workspace/evals/evals.json"
    data = json.loads(evals_path.read_text())
    assert data["skill_name"] == "rawgentic:adversarial-review"
    assert isinstance(data["evals"], list) and len(data["evals"]) >= 3
    for ev in data["evals"]:
        assert isinstance(ev.get("id"), int)
        assert ev.get("prompt") and isinstance(ev["prompt"], str)
        assert ev.get("expected_output") and isinstance(ev["expected_output"], str)


def test_adversarial_review_workspace_has_no_skill_md():
    """Workspace dirs are eval artifacts — must NOT contain a SKILL.md (validator rejects)."""
    ws = SKILLS_DIR / "adversarial-review-workspace"
    assert not (ws / "SKILL.md").exists()


# --- whole-issue delegation (#133) drift guards ---

def test_whole_issue_delegation_reference_exists():
    ref = SKILLS_DIR / "implement-feature" / "references" / "whole-issue-delegation.md"
    assert ref.exists(), "references/whole-issue-delegation.md must exist"
    body = ref.read_text()
    # the receipt schema keys + the trust-boundary contract must be documented
    for token in ("task_shas", "files_per_task", "exit_code", "promotions",
                  "validate_build_receipt", "never self-certif", "fall"):
        assert token in body, f"reference missing {token!r}"


def test_wf2_step8_documents_whole_issue_delegation_submode():
    skill = skill_corpus("implement-feature")
    # the opt-in block, its gate invocation, the validator, and the reference pointer
    assert "whole-issue-delegation: #133" in skill
    assert "--key wholeIssueDelegation" in skill
    assert "validate_build_receipt" in skill
    assert "references/whole-issue-delegation.md" in skill
    # the reject path must NOT prescribe a blanket clean against the operator tree
    assert "never" in skill.lower() and "git clean -fd" in skill  # named only to forbid it


def test_wf2_step8_delegation_is_opt_in_default_off():
    skill = skill_corpus("implement-feature")
    # default-off: a non-zero is-enabled exit skips silently
    assert "default-off" in skill
    assert "skip silently" in skill


# --- setup collects the backend field (#405) ---

def test_setup_2d_asks_backend():
    """#405 AC1: the Step 2d detail asks which backend and stages it; the
    default-gpt-may-omit contract is stated (LOCATION pin: the offer detail
    lives in references/integrations.md, same as test_setup_offers_surviving_workflows)."""
    detail = (SKILLS_DIR / "setup" / "references" / "integrations.md").read_text()
    assert "Which review backend? (gpt / glm / both) [default: gpt]" in detail
    assert "absent → gpt is the documented contract" in detail
    assert 'pip install "zhipuai>=2.1.5"' in detail
    assert "ZHIPUAI_API_KEY" in detail


def test_setup_2d_prereq_nudge_never_blocks():
    """#405 AC4: glm/both picks with an unready prereq print the engine guidance
    and STILL stage — config is intent, the runtime gate enforces."""
    detail = (SKILLS_DIR / "setup" / "references" / "integrations.md").read_text()
    assert "prereq --backend" in detail
    assert "STILL stage" in detail


def test_setup_2g_mirrors_backend_question():
    """#405 AC2: peerConsult asks the same-vocabulary backend question
    independently of the review answer."""
    detail = (SKILLS_DIR / "setup" / "references" / "integrations.md").read_text()
    twog = detail[detail.index("## Step 2g:"):detail.index("## Step 2h:")]
    assert "backend" in twog
    assert "independent" in twog


def test_setup_2d_reconfig_preserves_backend():
    """#405 AC3: re-running setup offers the current backend as the default,
    never silently resetting it."""
    detail = (SKILLS_DIR / "setup" / "references" / "integrations.md").read_text()
    assert "current backend" in detail


def test_config_reference_scope_out_dropped():
    """#405 AC6: setup now collects backend — the hand-edit scope-out note is gone."""
    doc = (REPO_ROOT / "docs" / "config-reference.md").read_text()
    assert "deliberate #403 scope-out" not in doc
    assert "does not yet collect" not in doc


# --- M0c (#866): setup Step 2i (phase-executor seat table) removed with its config surface ---

def test_setup_has_no_step_2i():
    text = skill_corpus("setup")
    assert "Step 2i" not in text
    assert "phaseExecutorTable" not in text  # tripwire-exempt: negative guard


def test_manifest_project_config_entries_have_setup_anchor():
    """#446 S2 (second half — moved from the reconcile guard): every source: project_config
    manifest entry must anchor to a real setup step that stages it."""
    import importlib.util
    import sys as _s
    hooks_dir = REPO_ROOT / "hooks"
    _s.path.insert(0, str(hooks_dir))
    spec = importlib.util.spec_from_file_location(
        "pur_anchor", str(hooks_dir / "post_update_reconcile.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = skill_corpus("setup")
    for feat in mod.FEATURE_MANIFEST:
        if feat.get("source") == "project_config":
            assert feat["key"] in text, f"{feat['key']}: no setup-step anchor in the setup skill"
