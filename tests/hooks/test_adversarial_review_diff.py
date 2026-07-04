"""Tests for adversarial_review_lib "diff" artifact type + confidence map (#131, Task 2)
plus the `review --findings-json` fail-closed sidecar (#131, Task 3)."""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import adversarial_review_lib as arl  # noqa: E402

LIB = HOOKS_DIR / "adversarial_review_lib.py"


def test_diff_in_artifact_types():
    assert "diff" in arl.ARTIFACT_TYPES


def test_type_lens_has_diff_key_with_refutation_contract():
    lens = arl._TYPE_LENS["diff"]
    assert "fail-open" in lens
    assert "bypass" in lens
    assert "vacuous" in lens
    assert "+/-" in lens


def test_build_prompt_diff_includes_lens_and_nonce_fence():
    prompt = arl.build_prompt("SOME DIFF TEXT", "diff")
    assert arl._TYPE_LENS["diff"] in prompt
    assert "=== BEGIN UNTRUSTED ARTIFACT" in prompt
    assert "=== END UNTRUSTED ARTIFACT" in prompt


def test_build_prompt_unknown_type_falls_back_to_generic_lens():
    prompt = arl.build_prompt("SOME TEXT", "not-a-real-type")
    assert arl._TYPE_LENS["generic"] in prompt


def test_adv_confidence_to_float_keys_and_values():
    mapping = arl.ADV_CONFIDENCE_TO_FLOAT
    assert set(mapping.keys()) == {"high", "medium", "low"}
    for value in mapping.values():
        assert isinstance(value, float)
        assert 0 < value <= 1
    assert mapping["high"] > mapping["medium"] > mapping["low"]


# ============================================================================
# Task 3: `review --findings-json <path>` fail-closed sidecar (#131)
# ============================================================================

# PATH-stubbed codex harness (same conventions as test_adversarial_review_cli.py).
# The stub additionally TOUCHES a marker file when `exec` runs, so a test can
# assert codex was NEVER invoked (path-validation must gate before egress).


def _codex_stub(bin_dir: Path, *, login_rc=0, exec_body="", exec_rc=0) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    marker = bin_dir / "codex_exec_ran"
    script = bin_dir / "codex"
    body = exec_body.replace("'", "'\\''")
    script.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "login" ] && [ "$2" = "status" ]; then exit %d; fi\n' % login_rc
        + 'if [ "$1" = "exec" ]; then\n'
        f"  touch '{marker}'\n"
        '  out=""; while [ $# -gt 0 ]; do if [ "$1" = "-o" ]; then out="$2"; fi; shift; done\n'
        f"  if [ -n \"$out\" ]; then printf '%s' '{body}' > \"$out\"; fi\n"
        f"  exit {exec_rc}\n"
        "fi\nexit 0\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return marker


def _run(args, *, extra_path: Path | None = None, strip_codex=False):
    env = dict(os.environ)
    if strip_codex:
        env["PATH"] = os.pathsep.join(
            d for d in env.get("PATH", "").split(os.pathsep)
            if d and not os.path.isfile(os.path.join(d, "codex"))
        )
    if extra_path is not None:
        env["PATH"] = str(extra_path) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        ["python3", str(LIB), *args],
        capture_output=True, text=True, timeout=30, env=env,
    )


def _valid_output():
    return json.dumps({"summary": "s", "findings": [
        {"evidence": "a quoted span", "severity": "High", "category": "security",
         "confidence": "high", "description": "d",
         "recommendation": "r", "location": "S1"}]})


# --- resolve_sidecar_path helper (direct unit) ---

def test_resolve_sidecar_path_accepts_under_root(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    p = arl.resolve_sidecar_path(str(root / "x.findings.json"), str(root))
    assert p == str(root / "x.findings.json")


def test_resolve_sidecar_path_rejects_escape(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    with pytest.raises(arl.ArtifactError):
        arl.resolve_sidecar_path(str(tmp_path / "x.json"), str(root))


def test_resolve_sidecar_path_rejects_sibling_prefix(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    (tmp_path / "proj-evil").mkdir()
    with pytest.raises(arl.ArtifactError):
        arl.resolve_sidecar_path(str(tmp_path / "proj-evil" / "x.json"), str(root))


def test_resolve_sidecar_path_rejects_nul(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    with pytest.raises(arl.ArtifactError):
        arl.resolve_sidecar_path(str(root / "x\x00.json"), str(root))


# --- CLI: review --findings-json ---

def test_review_findings_json_writes_sidecar_success(tmp_path):
    _codex_stub(tmp_path / "bin", exec_body=_valid_output())
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    sidecar = root / "out.findings.json"
    r = _run(["review", "--artifact", str(art), "--project-root", str(root),
              "--date", "2026-06-14", "--findings-json", str(sidecar)],
             extra_path=tmp_path / "bin")
    assert r.returncode == 0
    assert sidecar.exists()
    data = json.loads(sidecar.read_text())
    # EXACTLY the 5 documented keys.
    assert set(data.keys()) == {"status", "summary", "truncated", "secrets", "findings"}
    assert data["status"] == "success"
    assert isinstance(data["truncated"], bool)
    assert isinstance(data["secrets"], list)
    assert isinstance(data["findings"], list) and len(data["findings"]) == 1
    assert data["findings"][0]["severity"] == "High"
    # Sidecar findings match the report's normalized findings (same count).
    report = (root / "docs" / "reviews" / "d-md-2026-06-14.md").read_text()
    assert report.count("[High]") == len(data["findings"])


def test_review_findings_json_stale_replaced_on_success(tmp_path):
    _codex_stub(tmp_path / "bin", exec_body=_valid_output())
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    sidecar = root / "out.findings.json"
    sidecar.write_text("SENTINEL-STALE")
    r = _run(["review", "--artifact", str(art), "--project-root", str(root),
              "--date", "2026-06-14", "--findings-json", str(sidecar)],
             extra_path=tmp_path / "bin")
    assert r.returncode == 0
    data = json.loads(sidecar.read_text())  # sentinel gone -> valid JSON
    assert data["status"] == "success"


def test_review_findings_json_stale_removed_on_failure(tmp_path):
    # Stub codex exits nonzero -> exit 3; pre-existing sidecar must be REMOVED
    # and NOT replaced (fail-closed: no stale sentinel left behind).
    _codex_stub(tmp_path / "bin", exec_rc=1)
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    sidecar = root / "out.findings.json"
    sidecar.write_text("SENTINEL-STALE")
    r = _run(["review", "--artifact", str(art), "--project-root", str(root),
              "--findings-json", str(sidecar)], extra_path=tmp_path / "bin")
    assert r.returncode == 3
    assert not sidecar.exists()


def test_review_findings_json_prereq_fail_no_sidecar(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    sidecar = root / "out.findings.json"
    r = _run(["review", "--artifact", str(art), "--project-root", str(root),
              "--findings-json", str(sidecar)], strip_codex=True)
    assert r.returncode == 2
    # Distinguish prereq-fail (our message) from argparse's own exit-2.
    assert "Codex CLI is not installed" in r.stderr
    assert not sidecar.exists()


def test_review_findings_json_parse_error_no_sidecar(tmp_path):
    _codex_stub(tmp_path / "bin", exec_body="not json")
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    sidecar = root / "out.findings.json"
    r = _run(["review", "--artifact", str(art), "--project-root", str(root),
              "--findings-json", str(sidecar)], extra_path=tmp_path / "bin")
    assert r.returncode == 4
    assert not sidecar.exists()


@pytest.mark.parametrize("escape_name", ["sibling", "traversal"])
def test_review_findings_json_escape_exit2_no_codex(tmp_path, escape_name):
    marker = _codex_stub(tmp_path / "bin", exec_body=_valid_output())
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    if escape_name == "sibling":
        outside = tmp_path / "outside.findings.json"           # /tmp/.../outside.json
    else:
        outside = root / ".." / "trav.findings.json"           # ../ traversal
    r = _run(["review", "--artifact", str(art), "--project-root", str(root),
              "--findings-json", str(outside)], extra_path=tmp_path / "bin")
    assert r.returncode == 2
    # Our escape error, NOT argparse's exit-2, and codex NEVER invoked.
    assert "escapes project root" in r.stderr
    assert not marker.exists()
    assert not Path(os.path.abspath(outside)).exists()


def test_review_findings_json_report_write_fail_exit3_no_sidecar(tmp_path):
    # F3 technique (reused from test_adversarial_review_cli.py): make 'docs' a
    # FILE so os.makedirs(docs/reviews) raises OSError -> exit 3. Sidecar is
    # written only AFTER the report write succeeds, so it must be ABSENT here.
    _codex_stub(tmp_path / "bin", exec_body=_valid_output())
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    (root / "docs").write_text("not a dir")
    sidecar = root / "out.findings.json"
    r = _run(["review", "--artifact", str(art), "--project-root", str(root),
              "--date", "2026-06-14", "--findings-json", str(sidecar)],
             extra_path=tmp_path / "bin")
    assert r.returncode == 3
    assert not sidecar.exists()


def test_review_no_findings_json_flag_no_sidecar(tmp_path):
    # No flag -> byte-identical behavior; no sidecar written anywhere.
    _codex_stub(tmp_path / "bin", exec_body=_valid_output())
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    r = _run(["review", "--artifact", str(art), "--project-root", str(root),
              "--date", "2026-06-14"], extra_path=tmp_path / "bin")
    assert r.returncode == 0
    assert list(root.rglob("*.findings.json")) == []


def test_review_findings_json_collides_with_artifact_exit2_preserves_artifact(tmp_path):
    # --findings-json pointing at the SAME file as --artifact must fail closed
    # BEFORE the stale-sidecar os.remove() -- otherwise the input artifact is
    # destroyed (data loss) and codex then fails on a missing file (#131 Step 8a).
    marker = _codex_stub(tmp_path / "bin", exec_body=_valid_output())
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("ORIGINAL ARTIFACT CONTENT")
    r = _run(["review", "--artifact", str(art), "--project-root", str(root),
              "--date", "2026-06-14", "--findings-json", str(art)],
             extra_path=tmp_path / "bin")
    assert r.returncode == 2
    assert "collision" in r.stderr.lower()
    assert not marker.exists()
    assert art.exists()
    assert art.read_text() == "ORIGINAL ARTIFACT CONTENT"


def test_review_findings_json_collides_with_report_path_exit2(tmp_path):
    # --findings-json pointing at the exact report path adversarial_review_lib
    # would compute (docs/reviews/<slug>-<date>.md) must fail closed BEFORE
    # codex, else the sidecar write clobbers the human-readable report (#131).
    marker = _codex_stub(tmp_path / "bin", exec_body=_valid_output())
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    date = "2026-06-14"
    report_path = arl.review_report_path(str(root), str(art), date)
    r = _run(["review", "--artifact", str(art), "--project-root", str(root),
              "--date", date, "--findings-json", report_path],
             extra_path=tmp_path / "bin")
    assert r.returncode == 2
    assert "collision" in r.stderr.lower()
    assert not marker.exists()
    assert not Path(report_path).exists()
