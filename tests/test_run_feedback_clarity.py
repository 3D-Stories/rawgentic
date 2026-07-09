"""Drift guards for WF14 (run-feedback) — the post-run workflow self-assessment
skill (#337).

Pins the load-bearing contract sentences of skills/run-feedback/SKILL.md and its
references/rubric.md so they cannot silently re-diverge: the scoped report-only
invariant, the always-explicit-repo filing rule, the 3-issue cap with not-filed-cap
preservation, the degraded/unscored evidence modes, the single 6-way telemetry
verdict vocabulary (used identically in both files), the named standing telemetry
weak spots, the dispatches[] consume-when-present rule, the mempalace tri-state
surface line, the artifact-publish failure line, the marker-acceptance boundaries,
and the rubric version stamp every report must quote.

House pattern (repo mistake #6 / CLAUDE.md §5): section- or file-sliced, ONE
canonical sentence per guard, whitespace-normalized — never whole-corpus regex,
never substring counts. Companion to tests/test_wf3_clarity.py.
"""
from pathlib import Path

from tests.corpus import skill_corpus

REPO_ROOT = Path(__file__).resolve().parent.parent


def _corpus() -> str:
    # SKILL.md + references/rubric.md — content pins survive prose moving
    # between the two files (tests/corpus.py convention).
    return skill_corpus("run-feedback")


def _skill() -> str:
    return (REPO_ROOT / "skills" / "run-feedback" / "SKILL.md").read_text()


def _rubric() -> str:
    return (
        REPO_ROOT / "skills" / "run-feedback" / "references" / "rubric.md"
    ).read_text()


def _norm(s: str) -> str:
    """Whitespace-collapse so wrapped prose compares equal."""
    return " ".join(s.split())


VERDICT_VOCAB = (
    "match | mismatch | missing-in-record | missing-in-session | "
    "unverifiable | known-limitation"
)


# --- Report-only invariant (scoped: report pair + session-note DONE marker) ---

def test_report_only_invariant_scoped():
    s = _norm(_skill())
    assert ("never edits rawgentic skills, hooks, or source docs "
            "mid-assessment") in s, (
        "SKILL.md must carry the scoped report-only invariant (#337 design Rev 4)")
    assert ("the ONLY file writes are the WF14 report `.md`/`.html` pair under "
            "`docs/reviews/` plus the session-note DONE marker append") in s, (
        "The invariant must enumerate exactly the two permitted write kinds")


# --- Routing: always-explicit-repo + cap 3 + not-filed-cap preservation ---

def test_filing_always_targets_rawgentic_repo():
    s = _norm(_skill())
    assert ("always filed against `3D-Stories/rawgentic` regardless of which "
            "project the session is bound to") in s, (
        "Defect filing must be explicit-repo (AC3) — never the bound repo")


def test_filing_cap_and_overflow_preserved():
    s = _norm(_skill())
    assert "at most 3 issues per run" in s, "The 3-issue cap must be stated (AC3)"
    assert ("findings beyond the cap carry `routing: not-filed-cap` and are "
            "preserved in the report") in s, (
        "Cap overflow must be preserved, never silently dropped (peer-consult risk)")


# --- Evidence modes: degraded loud, unscored when no evidence at all ---

def test_degraded_mode_is_loud():
    s = _norm(_corpus())
    assert ("degraded mode: assess from session notes alone and state "
            "`record: absent` in the report header") in s, (
        "Missing run-record must produce a LOUD degraded mode (AC1)")


def test_unscored_when_no_evidence_source():
    s = _norm(_skill())
    assert ("neither a resolvable record nor a session-notes path gets an "
            "`unscored` assessment") in s, (
        "No record AND no notes must yield unscored — never a guessed assessment")


# --- Telemetry verdict vocabulary: ONE 6-way set, both files, verbatim ---

def test_verdict_vocabulary_in_skill():
    assert VERDICT_VOCAB in _norm(_skill()), (
        "SKILL.md must carry the canonical 6-way verdict vocabulary verbatim")


def test_verdict_vocabulary_in_rubric():
    assert VERDICT_VOCAB in _norm(_rubric()), (
        "rubric.md must carry the SAME 6-way verdict vocabulary verbatim — "
        "the design's one-set-three-surfaces rule")


def test_negative_verdicts_designated():
    s = _norm(_rubric())
    assert ("`mismatch`, `missing-in-record`, and `missing-in-session` count as "
            "negative findings; `known-limitation` and `unverifiable` never do") in s, (
        "The rubric must state which verdicts are negative findings")


# --- Standing known-weak spots named as checks (AC8) ---

def test_known_weak_spots_named():
    s = _norm(_rubric())
    assert ("usage attribution is session-level: the recorded tokens are "
            "whole-SESSION, so per-run cost claims are `known-limitation`") in s, (
        "Rubric must name the usage-attribution weak spot as a check (AC8)")
    assert "reviewer_kind fidelity" in s, (
        "Rubric must name the reviewer_kind fidelity check (AC8)")
    assert "gate-count honesty" in s, (
        "Rubric must name the gate-count honesty check (AC8)")
    assert ("the store append lags one PR by design") in s, (
        "Rubric must name the store-lag-known weak spot distinct from a "
        "genuinely missing record")


# --- dispatches[] consume-when-present (#329/#330 not landed) ---

def test_dispatches_consume_when_present():
    s = _norm(_corpus())
    assert ("`dispatches[]` is consumed when present; its absence is "
            "`missing-in-record` ONLY when session evidence shows dispatch "
            "activity") in s, (
        "dispatches[] must be consume-when-present with the evidence-gated "
        "absence verdict (#329/#330 open)")


# --- mempalace tri-state surface line (AC4) ---

def test_mempalace_tristate_surface():
    s = _norm(_skill())
    assert ("exactly one of: the saved memory's id/slug, \"friction memory save "
            "FAILED") in s and "mempalace unavailable — friction memory SKIPPED" in s, (
        "The routing section must print exactly one mempalace tri-state surface "
        "line (AC4 visible skip)")


# --- Artifact publication: best-effort with required failure line ---

def test_artifact_publish_failure_line():
    s = _norm(_skill())
    assert ("artifact publish FAILED/unavailable — committed .html is source "
            "of truth") in s, (
        "Artifact publication must be best-effort with the required failure line")


# --- Marker-acceptance boundaries for prose-grep evidence ---

def test_marker_acceptance_boundaries():
    s = _norm(_skill())
    assert ("only line-start markdown headings count; anything inside code "
            "fences or quoted/example text is ignored") in s, (
        "Marker grep must carry acceptance boundaries — no false evidence from "
        "quoted text")
    assert ("every accepted marker is quoted verbatim (with its source line) in "
            "the report's evidence ledger") in s, (
        "Accepted markers must be quoted with source lines in the evidence ledger")


# --- Secrets redaction covers every egress surface (issues + memory too) ---

def test_secrets_rule_covers_all_egress_surfaces():
    r = _norm(_rubric())
    assert ("no token values, no raw log dumps, in every egress surface: the report, "
            "filed issues, the published artifact, and the mempalace memory") in r, (
        "The secrets-by-name rule must cover the unscanned egress paths "
        "(issue body + memory write), not just the committed report")
    assert ("redacted BEFORE it enters the evidence ledger") in r, (
        "Redaction must happen at ledger ingestion — the only choke point "
        "for the unscanned egress paths")


# --- Rubric version stamp quoted in every report ---

def test_rubric_version_stamp():
    r = _norm(_rubric())
    assert "Rubric version: v1" in r, "rubric.md must carry its version stamp"
    assert ("every report quotes the rubric version it was assessed under") in _norm(
        _corpus()
    ), "Reports must quote the rubric version so assessments stay comparable"
