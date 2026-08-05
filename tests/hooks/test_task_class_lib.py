"""#761 — the task-class field: grammar, exclusions, precedence, diagnostics, snapshot.

Pure functions imported directly (`docs/testing.md`): the resolver takes an issue body as a
string and the snapshot writer takes a path, so neither needs the subprocess treatment the
stdin-driven hooks get.

Every constraint number below (C1..C13) is a pass-6 review finding adopted under D204 and
ledgered at claude_docs/.wf2-state/761/dispositions.jsonl.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / "hooks"
sys.path.insert(0, str(REPO_ROOT / "hooks"))

import task_class_lib as tcl  # noqa: E402


# ======================================================================================
# Grammar — candidates are collected by PREFIX then validated
# ======================================================================================

@pytest.mark.parametrize("value", ["disposable", "internal", "production"])
def test_each_class_resolves_from_the_canonical_line(value):
    cls, prov, diag = tcl.resolve_class(f"blah\n**Task class:** {value}\nmore")
    assert (cls, prov, diag) == (value, "issue_body", None)


def test_label_match_is_case_insensitive_in_the_FULL_grammar_too():
    """C9: the prefix scan was case-insensitive while the full-match regex was not, so an
    upper-case label was COLLECTED and then rejected as malformed — the spec contradicted
    itself. Both halves must agree."""
    cls, prov, diag = tcl.resolve_class("**TASK CLASS:** disposable")
    assert (cls, prov) == ("disposable", "issue_body"), diag
    assert diag is None


def test_value_is_normalized_case_insensitively():
    cls, prov, _ = tcl.resolve_class("**Task class:**   DISPOSABLE  ")
    assert (cls, prov) == ("disposable", "issue_body")


def test_trailing_text_is_malformed_not_silently_ignored():
    """The whole-line-regex bug: a line with junk after the value matched NOTHING, so it
    counted as zero candidates and silently took the default with no diagnostic."""
    cls, prov, diag = tcl.resolve_class("**Task class:** disposable  # because reasons")
    assert cls == "production"
    assert prov == "default"
    assert diag and "malformed" in diag.lower()


def test_unrecognised_value_fails_closed_with_a_diagnostic():
    cls, prov, diag = tcl.resolve_class("**Task class:** throwaway")
    assert (cls, prov) == ("production", "default")
    assert diag and "throwaway" in diag


def test_a_malformed_candidate_does_NOT_fall_back_to_the_config_default():
    """Rule 4: on a malformed candidate the config default is BYPASSED entirely."""
    cls, prov, diag = tcl.resolve_class("**Task class:** nonsense", config_default="internal")
    assert (cls, prov) == ("production", "default"), diag


def test_one_valid_plus_one_malformed_candidate_fails_closed():
    body = "**Task class:** disposable\n**Task class:** garbage extra\n"
    cls, prov, diag = tcl.resolve_class(body)
    assert cls == "production"
    assert diag and "1" in diag and "2" in diag, f"diagnostic must name both line numbers: {diag}"


def test_two_agreeing_valid_lines_still_fail_closed():
    """Deliberate: an 'agreeing duplicates are fine' clause is one more branch to misread."""
    body = "**Task class:** internal\n\n**Task class:** internal\n"
    cls, prov, diag = tcl.resolve_class(body)
    assert cls == "production"
    assert diag and "1" in diag and "3" in diag, diag


def test_zero_candidates_is_the_normal_case_with_no_diagnostic():
    cls, prov, diag = tcl.resolve_class("an issue body that simply says nothing about class")
    assert (cls, prov, diag) == ("production", "default", None)


# ======================================================================================
# Extraction boundary — the line-state fence algorithm
# ======================================================================================

@pytest.mark.parametrize("fence", ["```", "````", "~~~", "```md"])
def test_a_line_inside_a_fence_is_excluded(fence):
    closer = "```" if fence.startswith("`") else "~~~"
    if fence == "````":
        closer = "````"
    body = f"{fence}\n**Task class:** disposable\n{closer}\n"
    cls, prov, diag = tcl.resolve_class(body)
    assert cls == "production", f"fenced line must not set the class ({fence})"
    assert prov == "default"


def test_a_longer_fence_is_not_closed_by_a_shorter_run():
    """CommonMark: the closer must be at least as long as the opener."""
    body = "````\n```\n**Task class:** disposable\n````\n"
    cls, _, _ = tcl.resolve_class(body)
    assert cls == "production"


def test_an_unclosed_fence_excludes_everything_after_it():
    body = "```\n**Task class:** disposable\n"
    cls, _, _ = tcl.resolve_class(body)
    assert cls == "production", "an unterminated fence must fail closed"


def test_a_block_quoted_line_is_excluded():
    cls, _, _ = tcl.resolve_class("> **Task class:** disposable\n")
    assert cls == "production"


def test_a_four_space_indented_line_is_excluded():
    cls, _, _ = tcl.resolve_class("    **Task class:** disposable\n")
    assert cls == "production"


def test_up_to_three_spaces_of_indent_still_counts():
    cls, prov, _ = tcl.resolve_class("   **Task class:** internal\n")
    assert (cls, prov) == ("internal", "issue_body")


def test_an_excluded_candidate_looking_line_emits_an_informational_diagnostic():
    """C10: silent exclusion. Zero candidates emits no diagnostic, so a fenced or indented
    line was ignored with NO feedback — the same silence the visible-note finding was about."""
    body = "```\n**Task class:** disposable\n```\n"
    cls, prov, diag = tcl.resolve_class(body)
    assert cls == "production"
    assert diag, "an excluded candidate-looking line must be reported, not silently dropped"
    assert "2" in diag, f"diagnostic should name the excluded line number: {diag}"
    assert "fenc" in diag.lower(), f"diagnostic should name WHY it was excluded: {diag}"


def test_original_line_numbers_survive_exclusions():
    body = "intro\n```\nfenced\n```\n**Task class:** garbage bad\n"
    _, _, diag = tcl.resolve_class(body)
    assert diag and "5" in diag, f"line number must be the ORIGINAL body line: {diag}"


# ======================================================================================
# Precedence and the config default
# ======================================================================================

def test_body_beats_config_default():
    cls, prov, _ = tcl.resolve_class("**Task class:** disposable", config_default="internal")
    assert (cls, prov) == ("disposable", "issue_body")


def test_config_default_used_when_body_is_silent():
    cls, prov, diag = tcl.resolve_class("nothing here", config_default="internal")
    assert (cls, prov, diag) == ("internal", "config", None)


def test_invalid_config_default_fails_closed_with_a_diagnostic():
    cls, prov, diag = tcl.resolve_class("nothing here", config_default="cheap")
    assert (cls, prov) == ("production", "default")
    assert diag and "cheap" in diag


def test_absent_config_default_is_production():
    cls, prov, diag = tcl.resolve_class("nothing here", config_default=None)
    assert (cls, prov, diag) == ("production", "default", None)


# ======================================================================================
# Snapshot — write-once-adopt, durable, schema+identity validated
# ======================================================================================

def _payload(issue=761, cls="internal", prov="issue_body"):
    return {"task_class": cls, "provenance": prov, "issue": issue,
            "resolved_at": "2026-08-05T00:00:00Z"}


def test_clean_path_creates_the_directory_and_the_file(tmp_path):
    target = tmp_path / "wf2-state" / "761" / "task_class.json"
    assert not target.parent.exists()
    outcome = tcl.write_snapshot(str(target), _payload())
    assert outcome == "created"
    assert json.loads(target.read_text())["task_class"] == "internal"


def test_second_writer_adopts_and_the_first_value_wins(tmp_path):
    target = tmp_path / "761" / "task_class.json"
    assert tcl.write_snapshot(str(target), _payload(cls="internal")) == "created"
    assert tcl.write_snapshot(str(target), _payload(cls="production")) == "adopted"
    assert json.loads(target.read_text())["task_class"] == "internal"


def test_no_temp_residue_is_left_behind(tmp_path):
    target = tmp_path / "761" / "task_class.json"
    tcl.write_snapshot(str(target), _payload())
    tcl.write_snapshot(str(target), _payload())
    leftovers = [n for n in os.listdir(target.parent) if n.startswith(".task_class-")]
    assert leftovers == []


def test_the_containing_directory_is_fsynced_not_just_the_file(tmp_path, monkeypatch):
    """C2: os.link gives atomic VISIBILITY, not durability of the new directory entry. A host
    crash could lose a snapshot a completed run had already used, and the next run would then
    re-resolve a possibly-mutated body. Spy on fsync targets so this cannot regress."""
    target = tmp_path / "761" / "task_class.json"
    synced_dirs = []
    real_fsync = os.fsync

    def spy(fd):
        try:
            if os.fstat(fd).st_mode & 0o170000 == 0o040000:  # S_IFDIR
                synced_dirs.append(fd)
        except OSError:
            pass
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)
    tcl.write_snapshot(str(target), _payload())
    assert synced_dirs, "the containing directory must be fsynced after os.link"


def test_a_non_FileExistsError_oserror_is_fail_loud(tmp_path, monkeypatch):
    target = tmp_path / "761" / "task_class.json"

    def boom(src, dst):
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(os, "link", boom)
    with pytest.raises(tcl.TaskClassError) as e:
        tcl.write_snapshot(str(target), _payload())
    assert "task_class.json" in str(e.value)
    assert "Operation not permitted" in str(e.value) or "errno 1" in str(e.value).lower()


# ---- read-back: schema and identity ---------------------------------------------------

def test_read_snapshot_returns_a_valid_record(tmp_path):
    target = tmp_path / "761" / "task_class.json"
    tcl.write_snapshot(str(target), _payload())
    assert tcl.read_snapshot(str(target), issue=761)["task_class"] == "internal"


def test_unparseable_snapshot_is_fail_loud_and_names_the_remedy(tmp_path):
    target = tmp_path / "761" / "task_class.json"
    target.parent.mkdir(parents=True)
    target.write_text("{not json", encoding="utf-8")
    with pytest.raises(tcl.TaskClassError) as e:
        tcl.read_snapshot(str(target), issue=761)
    assert "delete" in str(e.value).lower(), "the message must name the delete-to-re-resolve remedy"


@pytest.mark.parametrize("mutate,label", [
    (lambda d: d.pop("task_class"), "missing task_class"),
    (lambda d: d.update(task_class="cheap"), "off-enum task_class"),
    (lambda d: d.update(provenance="guess"), "off-enum provenance"),
    (lambda d: d.update(issue="761"), "issue wrong type"),
    (lambda d: d.pop("resolved_at"), "missing resolved_at"),
])
def test_each_invalid_snapshot_shape_is_refused(tmp_path, mutate, label):
    target = tmp_path / "761" / "task_class.json"
    target.parent.mkdir(parents=True)
    d = _payload()
    mutate(d)
    target.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(tcl.TaskClassError):
        tcl.read_snapshot(str(target), issue=761)


def test_a_snapshot_for_a_DIFFERENT_issue_is_refused(tmp_path):
    """C13: a parseable snapshot carrying a valid class for the WRONG issue could otherwise be
    adopted and injected."""
    target = tmp_path / "761" / "task_class.json"
    tcl.write_snapshot(str(target), _payload(issue=999))
    with pytest.raises(tcl.TaskClassError) as e:
        tcl.read_snapshot(str(target), issue=761)
    assert "761" in str(e.value) and "999" in str(e.value)


def test_unknown_extra_keys_are_accepted(tmp_path):
    target = tmp_path / "761" / "task_class.json"
    d = _payload()
    d["future_field"] = "ignored"
    tcl.write_snapshot(str(target), d)
    assert tcl.read_snapshot(str(target), issue=761)["task_class"] == "internal"


# ======================================================================================
# Diagnostic surfacing
# ======================================================================================

def test_format_surface_line_states_class_provenance_and_snapshot_outcome():
    line = tcl.format_surface_line("disposable", "issue_body", "created", None)
    assert "disposable" in line and "issue_body" in line and "created" in line
    assert "DIAGNOSTIC" not in line


def test_format_surface_line_appends_the_diagnostic_when_present():
    line = tcl.format_surface_line("production", "default", "adopted", "line 4 is malformed")
    assert "DIAGNOSTIC" in line and "line 4 is malformed" in line


def test_an_adopted_snapshot_resurfaces_its_stored_diagnostic(tmp_path):
    """A later run must not inherit a silent fallback: the diagnostic is re-emitted on ADOPT."""
    target = tmp_path / "761" / "task_class.json"
    d = _payload(cls="production", prov="default")
    d["diagnostic"] = "line 2 unrecognised value 'throwaway'"
    tcl.write_snapshot(str(target), d)
    rec = tcl.read_snapshot(str(target), issue=761)
    line = tcl.format_surface_line(rec["task_class"], rec["provenance"], "adopted",
                                   rec.get("diagnostic"))
    assert "throwaway" in line


# ======================================================================================
# Step 8a fixes (#761): F3 (cross-model, Medium) and M1 (inline self-review, Medium)
# ======================================================================================

@pytest.mark.parametrize("bad", [42, True, ["internal"], {"a": 1}, 3.5])
def test_a_present_but_NON_STRING_config_value_still_diagnoses(tmp_path, bad):
    """F3: a non-string value was silently indistinguishable from an ABSENT key.

    `read_config_default` filtered on `isinstance(value, str)` and returned None
    otherwise, so `defaultTaskClass: 42` took the `production` default with NO
    diagnostic while `defaultTaskClass: "bogus"` produced one. The design promises a
    diagnostic for an invalid config value; the type of the invalidity is not a
    reason to go quiet. Confirmed by execution before the fix.
    """
    (tmp_path / ".rawgentic.json").write_text(
        json.dumps({"version": 1, tcl.CONFIG_KEY: bad}))
    got = tcl.read_config_default(str(tmp_path))
    cls, prov, diag = tcl._config_outcome(got)
    assert cls == tcl.DEFAULT_CLASS
    assert prov == "default"
    assert diag is not None, f"a present-but-invalid {type(bad).__name__} went silent"
    assert tcl.CONFIG_KEY in diag


def test_an_absent_key_stays_silent(tmp_path):
    """The other half of F3: absent is NOT invalid, and must not diagnose."""
    (tmp_path / ".rawgentic.json").write_text(json.dumps({"version": 1}))
    assert tcl.read_config_default(str(tmp_path)) is None
    assert tcl._config_outcome(None) == (tcl.DEFAULT_CLASS, "default", None)


def test_an_explicit_json_null_is_treated_as_absent(tmp_path):
    """Stated deliberately: `null` is a reasonable spelling of "not set"."""
    (tmp_path / ".rawgentic.json").write_text(
        json.dumps({"version": 1, tcl.CONFIG_KEY: None}))
    assert tcl.read_config_default(str(tmp_path)) is None


def _resolve_cli(*args):
    proc = subprocess.run(
        [sys.executable, str(HOOKS / "task_class_lib.py"), "resolve", *args],
        capture_output=True, text=True, timeout=60)
    return proc


def test_a_failed_BODY_READ_reports_the_contract_line_not_a_traceback(tmp_path):
    """M1: the CLI died with a raw traceback on the paths C8 governs.

    `_cmd_resolve` caught only `TaskClassError`, so an OSError from the body read or
    from `mkdir`/`mkstemp`/the directory `fsync` escaped as a traceback. rc was still
    1 — fail-loud was intact and nothing proceeded on an unread class — but the
    design doc and docs/config-reference.md both state the destination is stderr in
    the form `task-class: FAILED — <reason>`. Shipping docs that describe a format
    the code does not emit is the prose-divergence class this campaign keeps finding.
    """
    proc = _resolve_cli("--issue", "1",
                        "--body-file", str(tmp_path / "nope.md"),
                        "--out", str(tmp_path / "s.json"),
                        "--project-root", str(tmp_path))
    assert proc.returncode == 1
    assert "task-class: FAILED" in proc.stderr, proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    assert "nope.md" in proc.stderr


def test_a_failed_SNAPSHOT_WRITE_reports_the_contract_line(tmp_path):
    """The case C8 is literally about: the WRITE fails, before any marker exists."""
    body = tmp_path / "body.md"
    body.write_text("no class line\n")
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory\n")
    proc = _resolve_cli("--issue", "1", "--body-file", str(body),
                        "--out", str(blocker / "sub" / "task_class.json"),
                        "--project-root", str(tmp_path))
    assert proc.returncode == 1
    assert "task-class: FAILED" in proc.stderr, proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
