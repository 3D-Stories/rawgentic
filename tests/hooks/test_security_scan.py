"""Tests for hooks/security_scan.py — the shared tool-based security scanner.

WF2 (implement-feature) Step 11.5 and WF9 (security-audit) both call this one
tested lib so the actual *tool-based* scanning (secrets / dependency-CVE / SAST /
IaC) lives in a single fail-closed place instead of being re-derived in prose.

The design splits cleanly into PURE functions (severity normalization, per-tool
output parsers, scanner selection, the gate decision) — exhaustively unit-tested
here — and one orchestrator (`run_scan`) that takes injectable `which`/`runner`
so the subprocess wiring is deterministic without any scanner installed. The gate
is the security-critical heart, so it gets the most adversarial coverage:
fail-CLOSED on a real finding or an unparseable scanner, but degrade with a
VISIBLE skip (never silent) when a tool simply isn't installed.
"""
import json
import os
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

SCAN_CLI = HOOKS_DIR / "security_scan.py"


# --- helpers ---------------------------------------------------------------

def _proc(returncode=0, stdout="", stderr=""):
    """A fake subprocess.CompletedProcess-alike for the injected runner."""
    class _P:
        pass
    p = _P()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


def _fake_runner_from(mapping):
    """Build a runner(cmd) that dispatches on the tool name (cmd[0])."""
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        tool = cmd[0]
        return mapping.get(tool, _proc(returncode=0, stdout=""))
    runner.calls = calls
    return runner


def _which_from(present):
    present = set(present)
    return lambda tool: ("/usr/bin/" + tool) if tool in present else None


# --- normalize_severity ----------------------------------------------------

class TestNormalizeSeverity:
    """Each tool speaks a different severity vocabulary; the lib folds them all
    into one ordered scale {critical, high, medium, low, unknown} so the gate can
    reason uniformly."""

    @pytest.mark.parametrize("tool,raw,expected", [
        # gitleaks findings carry no severity — a leaked secret is always critical
        ("gitleaks", None, "critical"),
        ("gitleaks", "whatever", "critical"),
        # npm audit: moderate maps to medium, info to low
        ("npm", "critical", "critical"),
        ("npm", "high", "high"),
        ("npm", "moderate", "medium"),
        ("npm", "low", "low"),
        ("npm", "info", "low"),
        # osv / generic database severities (upper-case)
        ("osv-scanner", "CRITICAL", "critical"),
        ("osv-scanner", "HIGH", "high"),
        ("osv-scanner", "MODERATE", "medium"),
        ("osv-scanner", "MEDIUM", "medium"),
        ("osv-scanner", "LOW", "low"),
        # semgrep
        ("semgrep", "ERROR", "high"),
        ("semgrep", "WARNING", "medium"),
        ("semgrep", "INFO", "low"),
        # trivy keeps its own buckets; UNKNOWN stays unknown
        ("trivy", "CRITICAL", "critical"),
        ("trivy", "HIGH", "high"),
        ("trivy", "UNKNOWN", "unknown"),
        # anything unrecognized degrades to unknown rather than guessing
        ("npm", None, "unknown"),
        ("osv-scanner", "", "unknown"),
        ("semgrep", "bogus", "unknown"),
    ])
    def test_maps(self, tool, raw, expected):
        from security_scan import normalize_severity
        assert normalize_severity(tool, raw) == expected


# --- parsers ---------------------------------------------------------------

class TestParseGitleaks:
    def test_empty_array_is_no_findings(self):
        from security_scan import parse_gitleaks
        assert parse_gitleaks("[]") == []

    def test_blank_output_is_no_findings(self):
        from security_scan import parse_gitleaks
        # gitleaks with --report-path - prints nothing when there are no leaks
        assert parse_gitleaks("") == []
        assert parse_gitleaks("null") == []

    def test_parses_leak_as_critical_secret(self):
        from security_scan import parse_gitleaks
        out = json.dumps([
            {"RuleID": "aws-access-key", "File": "src/app.py",
             "StartLine": 10, "Description": "AWS Access Key"},
        ])
        findings = parse_gitleaks(out)
        assert len(findings) == 1
        f = findings[0]
        assert f["kind"] == "secrets"
        assert f["severity"] == "critical"
        assert f["scanner"] == "gitleaks"
        assert "src/app.py" in f["location"]
        assert f["identifier"] == "aws-access-key"

    def test_malformed_json_raises(self):
        from security_scan import parse_gitleaks, SecurityScanError
        with pytest.raises(SecurityScanError):
            parse_gitleaks("{not json")


class TestParseOsvScanner:
    def test_empty_results(self):
        from security_scan import parse_osv_scanner
        assert parse_osv_scanner('{"results": []}') == []
        assert parse_osv_scanner("") == []

    def test_parses_db_specific_severity(self):
        from security_scan import parse_osv_scanner
        out = json.dumps({"results": [{
            "source": {"path": "package-lock.json"},
            "packages": [{
                "package": {"name": "lodash", "version": "4.17.0"},
                "vulnerabilities": [{
                    "id": "GHSA-xxxx",
                    "database_specific": {"severity": "HIGH"},
                }],
            }],
        }]})
        findings = parse_osv_scanner(out)
        assert len(findings) == 1
        f = findings[0]
        assert f["kind"] == "sca"
        assert f["severity"] == "high"
        assert f["identifier"] == "GHSA-xxxx"
        assert "lodash" in f["title"]

    def test_known_vuln_without_severity_is_unknown(self):
        from security_scan import parse_osv_scanner
        out = json.dumps({"results": [{
            "source": {"path": "requirements.txt"},
            "packages": [{
                "package": {"name": "requests", "version": "2.0.0"},
                "vulnerabilities": [{"id": "PYSEC-1234"}],
            }],
        }]})
        findings = parse_osv_scanner(out)
        assert len(findings) == 1
        assert findings[0]["severity"] == "unknown"

    def test_malformed_raises(self):
        from security_scan import parse_osv_scanner, SecurityScanError
        with pytest.raises(SecurityScanError):
            parse_osv_scanner("{oops")


class TestParseNpmAudit:
    def test_no_vulns(self):
        from security_scan import parse_npm_audit
        assert parse_npm_audit('{"vulnerabilities": {}}') == []

    def test_parses_vuln_map(self):
        from security_scan import parse_npm_audit
        out = json.dumps({"vulnerabilities": {
            "minimist": {"severity": "moderate", "via": ["prototype pollution"]},
            "axios": {"severity": "high", "via": ["SSRF"]},
        }})
        findings = parse_npm_audit(out)
        sev = {f["identifier"]: f["severity"] for f in findings}
        assert sev["minimist"] == "medium"
        assert sev["axios"] == "high"
        assert all(f["kind"] == "sca" for f in findings)


class TestParsePipAudit:
    def test_no_vulns(self):
        from security_scan import parse_pip_audit
        assert parse_pip_audit('{"dependencies": []}') == []

    def test_parses_vulns_without_severity_as_unknown(self):
        from security_scan import parse_pip_audit
        out = json.dumps({"dependencies": [
            {"name": "flask", "version": "0.1", "vulns": [
                {"id": "PYSEC-2020-1", "fix_versions": ["1.0"]}]},
            {"name": "safe", "version": "1.0", "vulns": []},
        ]})
        findings = parse_pip_audit(out)
        assert len(findings) == 1
        assert findings[0]["identifier"] == "PYSEC-2020-1"
        assert findings[0]["severity"] == "unknown"
        assert findings[0]["kind"] == "sca"


class TestParseSemgrep:
    def test_no_results(self):
        from security_scan import parse_semgrep
        assert parse_semgrep('{"results": [], "errors": []}') == []

    def test_parses_results_by_severity(self):
        from security_scan import parse_semgrep
        out = json.dumps({"results": [
            {"check_id": "python.lang.security.audit.dangerous-exec",
             "path": "app.py", "start": {"line": 5},
             "extra": {"severity": "ERROR", "message": "exec is dangerous"}},
        ], "errors": []})
        findings = parse_semgrep(out)
        assert len(findings) == 1
        f = findings[0]
        assert f["kind"] == "sast"
        assert f["severity"] == "high"
        assert "app.py" in f["location"]
        assert f["identifier"].endswith("dangerous-exec")


class TestParseTrivyConfig:
    def test_no_misconfig(self):
        from security_scan import parse_trivy_config
        assert parse_trivy_config('{"Results": []}') == []

    def test_parses_misconfigurations(self):
        from security_scan import parse_trivy_config
        out = json.dumps({"Results": [{
            "Target": "Dockerfile",
            "Misconfigurations": [
                {"ID": "DS002", "Severity": "HIGH", "Title": "root user"}],
        }]})
        findings = parse_trivy_config(out)
        assert len(findings) == 1
        f = findings[0]
        assert f["kind"] == "iac"
        assert f["severity"] == "high"
        assert f["identifier"] == "DS002"
        assert "Dockerfile" in f["location"]


# --- _build_trivy_config (honor a project-local .trivyignore) --------------

class TestBuildTrivyConfig:
    """trivy reads .trivyignore ONLY from its process CWD (not the scan target),
    and the gate runs trivy from an uncontrolled CWD — so the command MUST pass
    an explicit --ignorefile for a committed, reviewed project-local .trivyignore
    to be honored at all. The ignorefile is anchored to the DECLARED project_root
    (never an arbitrary path); when absent, the command is byte-for-byte
    unchanged so today's behavior is preserved."""

    def test_absent_trivyignore_passes_no_ignorefile(self, tmp_path):
        from security_scan import _build_trivy_config
        cmd = _build_trivy_config(str(tmp_path), "origin/main", False)
        assert "--ignorefile" not in cmd
        assert cmd == ["trivy", "config", "--quiet", "--format", "json",
                       str(tmp_path)]

    def test_present_trivyignore_adds_ignorefile_anchored_to_root(self, tmp_path):
        from security_scan import _build_trivy_config
        ti = tmp_path / ".trivyignore"
        ti.write_text("DS-0002\n")
        cmd = _build_trivy_config(str(tmp_path), "origin/main", False)
        assert "--ignorefile" in cmd
        i = cmd.index("--ignorefile")
        # anchored to the DECLARED project_root, never an arbitrary location
        assert cmd[i + 1] == str(ti)
        # the positional scan target remains the project_root, and stays last
        assert cmd[-1] == str(tmp_path)

    def test_directory_named_trivyignore_is_not_treated_as_a_file(self, tmp_path):
        from security_scan import _build_trivy_config
        (tmp_path / ".trivyignore").mkdir()
        cmd = _build_trivy_config(str(tmp_path), "origin/main", False)
        assert "--ignorefile" not in cmd

    def test_full_mode_also_honors_ignorefile(self, tmp_path):
        # WF9 (--full / whole-tree) uses the same builder; a suppression must
        # apply there too, not only in WF2's diff-scoped Step 11.5.
        from security_scan import _build_trivy_config
        (tmp_path / ".trivyignore").write_text("DS-0002\n")
        cmd = _build_trivy_config(str(tmp_path), "origin/main", True)
        assert "--ignorefile" in cmd


# --- select_scanners -------------------------------------------------------

class TestSelectScanners:
    """Which scanners run is a function of project_type + has_docker + which
    tools are actually installed. A scanner whose tool is missing is SKIPPED with
    a reason (never silently dropped); a scanner not applicable to the project is
    also skipped with a distinct reason."""

    def _kinds(self, to_run):
        return {(s["kind"], s["tool"]) for s in to_run}

    def test_node_with_all_tools(self):
        from security_scan import select_scanners
        to_run, skipped = select_scanners(
            "node", False, {"gitleaks", "osv-scanner", "semgrep"})
        assert ("secrets", "gitleaks") in self._kinds(to_run)
        assert ("sca", "osv-scanner") in self._kinds(to_run)
        assert ("sast", "semgrep") in self._kinds(to_run)
        # no docker -> iac skipped, not run
        assert all(s["kind"] != "iac" for s in to_run)
        assert any(s["kind"] == "iac" for s in skipped)

    def test_sca_falls_back_to_npm_for_node(self):
        from security_scan import select_scanners
        to_run, _ = select_scanners("node", False, {"gitleaks", "npm", "semgrep"})
        assert ("sca", "npm") in self._kinds(to_run)

    def test_sca_falls_back_to_pip_audit_for_python(self):
        from security_scan import select_scanners
        to_run, _ = select_scanners(
            "python", False, {"gitleaks", "pip-audit", "semgrep"})
        assert ("sca", "pip-audit") in self._kinds(to_run)

    def test_osv_preferred_over_per_language(self):
        from security_scan import select_scanners
        to_run, _ = select_scanners(
            "node", False, {"osv-scanner", "npm"})
        assert ("sca", "osv-scanner") in self._kinds(to_run)
        assert ("sca", "npm") not in self._kinds(to_run)

    def test_iac_runs_when_docker_and_trivy_present(self):
        from security_scan import select_scanners
        to_run, _ = select_scanners("node", True, {"trivy"})
        assert ("iac", "trivy") in self._kinds(to_run)

    def test_iac_skipped_when_docker_but_trivy_missing(self):
        from security_scan import select_scanners
        to_run, skipped = select_scanners("node", True, {"gitleaks"})
        assert all(s["kind"] != "iac" for s in to_run)
        iac_skip = [s for s in skipped if s["kind"] == "iac"]
        assert iac_skip and "trivy" in iac_skip[0]["reason"].lower()

    def test_missing_secret_tool_is_skipped_with_reason(self):
        from security_scan import select_scanners
        to_run, skipped = select_scanners("node", False, set())
        assert all(s["kind"] != "secrets" for s in to_run)
        sec = [s for s in skipped if s["kind"] == "secrets"]
        assert sec and "gitleaks" in sec[0]["reason"].lower()

    def test_no_sca_tool_at_all_is_skipped(self):
        from security_scan import select_scanners
        to_run, skipped = select_scanners("node", False, {"gitleaks", "semgrep"})
        assert all(s["kind"] != "sca" for s in to_run)
        assert any(s["kind"] == "sca" for s in skipped)


# --- decide_gate (the fail-closed heart) -----------------------------------

class TestDecideGate:
    def _finding(self, kind="sast", severity="high", scanner="semgrep"):
        return {"scanner": scanner, "kind": kind, "severity": severity,
                "identifier": "X", "location": "f", "title": "t"}

    def test_clean_passes(self):
        from security_scan import decide_gate
        g = decide_gate([], [])
        assert g["blocked"] is False
        assert g["blocking"] == [] and g["advisory"] == []

    def test_secret_always_blocks(self):
        from security_scan import decide_gate
        g = decide_gate([self._finding(kind="secrets", severity="critical",
                                        scanner="gitleaks")], [])
        assert g["blocked"] is True
        assert len(g["blocking"]) == 1

    def test_high_blocks_medium_is_advisory(self):
        from security_scan import decide_gate
        g = decide_gate([
            self._finding(kind="sca", severity="high"),
            self._finding(kind="sca", severity="medium"),
        ], [])
        assert g["blocked"] is True
        assert len(g["blocking"]) == 1
        assert len(g["advisory"]) == 1

    def test_low_is_advisory_only(self):
        from security_scan import decide_gate
        g = decide_gate([self._finding(kind="sast", severity="low")], [])
        assert g["blocked"] is False
        assert len(g["advisory"]) == 1

    def test_unknown_severity_sca_blocks_conservatively(self):
        # A known CVE with no severity rating is still a known CVE — fail closed.
        from security_scan import decide_gate
        g = decide_gate([self._finding(kind="sca", severity="unknown")], [])
        assert g["blocked"] is True

    def test_unknown_severity_sast_is_advisory(self):
        # SAST/IaC unknown is not a known-vuln signal, so it stays advisory.
        from security_scan import decide_gate
        g = decide_gate([self._finding(kind="sast", severity="unknown")], [])
        assert g["blocked"] is False

    def test_scanner_error_blocks_even_with_no_findings(self):
        # An installed scanner that produced unparseable output must not be read
        # as "clean" — fail closed.
        from security_scan import decide_gate
        g = decide_gate([], [{"scanner": "semgrep", "message": "boom"}])
        assert g["blocked"] is True
        assert g["errors"]

    def test_block_severities_override_demotes_high(self):
        from security_scan import decide_gate
        g = decide_gate([self._finding(kind="sca", severity="high")], [],
                        block_severities=("critical",))
        assert g["blocked"] is False
        assert len(g["advisory"]) == 1

    def test_block_severities_are_a_threshold_not_exact_membership(self):
        # Finding #3: a lower configured severity must block that level AND ABOVE,
        # so setting "medium" can never accidentally make high/critical advisory.
        from security_scan import decide_gate
        g = decide_gate([
            self._finding(kind="sast", severity="high"),
            self._finding(kind="sast", severity="critical"),
        ], [], block_severities=("medium",))
        assert g["blocked"] is True
        assert len(g["blocking"]) == 2

    def test_garbage_block_severities_fall_back_to_failclosed_default(self):
        # An unrecognized/empty config must NOT disable blocking (fail-closed).
        from security_scan import decide_gate
        g = decide_gate([self._finding(kind="sca", severity="high")], [],
                        block_severities=("nonsense",))
        assert g["blocked"] is True

    def test_mixed_garbage_block_severities_fails_closed(self):
        # A TYPO in one token ("hgih") must not silently relax the gate to the
        # remaining (stricter-looking) token — the whole override is invalid, so
        # fall back to the fail-closed default. Otherwise a fat-finger downgrades
        # High to advisory.
        from security_scan import decide_gate
        g = decide_gate([self._finding(kind="sast", severity="high")], [],
                        block_severities=("critical", "hgih"))
        assert g["blocked"] is True


# --- run_scan orchestration (dependency-injected, no real tools) -----------

class TestRunScanOrchestration:
    def test_runs_selected_scanners_and_aggregates(self):
        from security_scan import run_scan
        gitleaks_out = json.dumps([
            {"RuleID": "aws", "File": "a.py", "StartLine": 1, "Description": "x"}])
        osv_out = json.dumps({"results": [{
            "source": {"path": "package-lock.json"},
            "packages": [{"package": {"name": "lodash", "version": "1"},
                          "vulnerabilities": [{"id": "G1",
                              "database_specific": {"severity": "HIGH"}}]}]}]})
        semgrep_out = json.dumps({"results": [], "errors": []})
        runner = _fake_runner_from({
            "gitleaks": _proc(1, gitleaks_out),  # rc=1 means leaks found, not error
            "osv-scanner": _proc(1, osv_out),
            "semgrep": _proc(0, semgrep_out),
        })
        result = run_scan(
            "/proj", project_type="node", has_docker=False,
            base_ref="origin/main",
            which=_which_from({"gitleaks", "osv-scanner", "semgrep"}),
            runner=runner, env={})
        assert result["gate"]["blocked"] is True
        kinds = {f["kind"] for f in result["findings"]}
        assert {"secrets", "sca"} <= kinds
        # semgrep ran but found nothing -> no sast finding, but it's not skipped
        assert all(s["kind"] != "sast" for s in result["skipped"])

    def test_missing_tool_is_visible_skip_not_block(self):
        from security_scan import run_scan
        runner = _fake_runner_from({})
        result = run_scan(
            "/proj", project_type="node", has_docker=False,
            which=_which_from(set()),  # nothing installed
            runner=runner, env={})
        # nothing could run, but that is NOT a block — it is surfaced as skips
        assert result["gate"]["blocked"] is False
        assert result["skipped"]
        assert runner.calls == []  # never invoked a missing tool

    def test_unparseable_scanner_output_fails_closed(self):
        from security_scan import run_scan
        runner = _fake_runner_from({
            "gitleaks": _proc(2, "<<garbage not json>>", stderr="crashed"),
            "osv-scanner": _proc(0, '{"results": []}'),
            "semgrep": _proc(0, '{"results": [], "errors": []}'),
        })
        result = run_scan(
            "/proj", project_type="node", has_docker=False,
            which=_which_from({"gitleaks", "osv-scanner", "semgrep"}),
            runner=runner, env={})
        assert result["gate"]["blocked"] is True
        assert any(e["scanner"] == "gitleaks" for e in result["gate"]["errors"])

    def test_pip_audit_targets_requirements_files(self, tmp_path):
        # Finding #1: pip-audit with no target audits the AMBIENT env, missing the
        # project's pinned deps. It must be pointed at the project's requirements.
        from security_scan import run_scan
        (tmp_path / "requirements.txt").write_text("flask==0.1\n")
        runner = _fake_runner_from({
            "gitleaks": _proc(0, "[]"),
            "pip-audit": _proc(0, '{"dependencies": []}'),
            "semgrep": _proc(0, '{"results": [], "errors": []}')})
        run_scan(str(tmp_path), project_type="python", has_docker=False,
                 which=_which_from({"gitleaks", "pip-audit", "semgrep"}),  # no osv -> fallback
                 runner=runner, env={})
        pip_cmd = next(c for c in runner.calls if c[0] == "pip-audit")
        assert "-r" in pip_cmd
        assert any(str(tmp_path) in str(part) for part in pip_cmd)

    def test_pip_audit_skips_when_no_requirements_file(self, tmp_path):
        # No requirements file -> pip-audit must NOT run against the ambient env;
        # it's a visible skip instead.
        from security_scan import run_scan
        runner = _fake_runner_from({
            "gitleaks": _proc(0, "[]"),
            "semgrep": _proc(0, '{"results": [], "errors": []}')})
        result = run_scan(
            str(tmp_path), project_type="python", has_docker=False,
            which=_which_from({"gitleaks", "pip-audit", "semgrep"}),
            runner=runner, env={})
        assert all(c[0] != "pip-audit" for c in runner.calls)
        assert any(s["kind"] == "sca" for s in result["skipped"])

    def test_abnormal_rc_with_findings_still_fails_closed(self):
        # Finding #2: a scanner that exits abnormally AFTER producing a finding
        # ran incompletely — fail closed even though `produced` is True.
        from security_scan import run_scan
        semgrep_out = json.dumps({"results": [
            {"check_id": "x", "path": "a.py", "start": {"line": 1},
             "extra": {"severity": "INFO", "message": "m"}}], "errors": []})
        runner = _fake_runner_from({
            "gitleaks": _proc(0, "[]"),
            "osv-scanner": _proc(0, '{"results": []}'),
            "semgrep": _proc(2, semgrep_out)})  # rc=2 not in {0} -> incomplete
        result = run_scan(
            "/proj", project_type="node", has_docker=False,
            which=_which_from({"gitleaks", "osv-scanner", "semgrep"}),
            runner=runner, env={})
        assert result["gate"]["blocked"] is True
        assert any(e["scanner"] == "semgrep" for e in result["gate"]["errors"])

    def test_benign_skip_only_applies_to_sca(self):
        # Finding #4: a non-SCA scanner failing with "no packages found" in stderr
        # must NOT be misread as a benign skip — only SCA has that benign case.
        from security_scan import run_scan
        runner = _fake_runner_from({
            "gitleaks": _proc(2, "", stderr="no packages found"),
            "osv-scanner": _proc(0, '{"results": []}'),
            "semgrep": _proc(0, '{"results": [], "errors": []}')})
        result = run_scan(
            "/proj", project_type="node", has_docker=False,
            which=_which_from({"gitleaks", "osv-scanner", "semgrep"}),
            runner=runner, env={})
        assert result["gate"]["blocked"] is True
        assert any(e["scanner"] == "gitleaks" for e in result["gate"]["errors"])
        assert not any(s["kind"] == "secrets" for s in result["skipped"])

    def test_npm_error_envelope_fails_closed(self):
        # npm audit emits {"error": {...}} (no "vulnerabilities") on failure AND
        # exits 1 — the SAME code as "vulns found" — so the exit code can't catch
        # it. The parser must treat the error envelope as a fail-closed error,
        # never as "no vulnerabilities".
        from security_scan import run_scan
        runner = _fake_runner_from({
            "gitleaks": _proc(0, "[]"),
            "npm": _proc(1, '{"error": {"code": "ENOLOCK", "summary": "no lockfile"}}'),
            "semgrep": _proc(0, '{"results": [], "errors": []}'),
        })
        result = run_scan(
            "/proj", project_type="node", has_docker=False,
            which=_which_from({"gitleaks", "npm", "semgrep"}),
            runner=runner, env={})
        assert result["gate"]["blocked"] is True
        assert any(e["scanner"] == "npm" for e in result["gate"]["errors"])

    def test_abnormal_exit_no_findings_fails_closed(self):
        # A scanner that exits with an unexpected code and produced NO findings
        # may have failed to run — do not read it as clean.
        from security_scan import run_scan
        runner = _fake_runner_from({
            "gitleaks": _proc(2, "", stderr="fatal: bad revision"),  # rc 2 = error
            "osv-scanner": _proc(0, '{"results": []}'),
            "semgrep": _proc(0, '{"results": [], "errors": []}'),
        })
        result = run_scan(
            "/proj", project_type="node", has_docker=False,
            which=_which_from({"gitleaks", "osv-scanner", "semgrep"}),
            runner=runner, env={})
        assert result["gate"]["blocked"] is True
        assert any(e["scanner"] == "gitleaks" for e in result["gate"]["errors"])

    def test_benign_nothing_to_scan_is_skip_not_error(self):
        # osv-scanner exits nonzero with "No package sources found" on a project
        # with no lockfiles — that is "nothing to scan", NOT a failure. It must be
        # a skip (visible, non-blocking), not a fail-closed error.
        from security_scan import run_scan
        runner = _fake_runner_from({
            "gitleaks": _proc(0, "[]"),
            "osv-scanner": _proc(128, "", stderr="No package sources found, --recursive may help"),
            "semgrep": _proc(0, '{"results": [], "errors": []}'),
        })
        result = run_scan(
            "/proj", project_type="node", has_docker=False,
            which=_which_from({"gitleaks", "osv-scanner", "semgrep"}),
            runner=runner, env={})
        assert result["gate"]["blocked"] is False
        assert not result["gate"]["errors"]
        assert any(s["kind"] == "sca" for s in result["skipped"])

    def test_findings_indicated_rc_but_none_parsed_fails_closed(self):
        # osv-scanner exits 1 ("vulnerabilities found") but the parser extracted
        # ZERO — schema drift or an error envelope masking real findings. The
        # tool said "I found things"; parsing nothing must fail closed, not pass.
        from security_scan import run_scan
        runner = _fake_runner_from({
            "gitleaks": _proc(0, "[]"),
            "osv-scanner": _proc(1, '{"results": []}'),  # rc=1 vs 0 findings
            "semgrep": _proc(0, '{"results": [], "errors": []}')})
        result = run_scan(
            "/proj", project_type="node", has_docker=False,
            which=_which_from({"gitleaks", "osv-scanner", "semgrep"}),
            runner=runner, env={})
        assert result["gate"]["blocked"] is True
        assert any(e["scanner"] == "osv-scanner" for e in result["gate"]["errors"])

    def test_findings_indicated_rc_with_findings_is_ok(self):
        # The consistency check must NOT misfire when rc=1 DOES come with findings.
        from security_scan import run_scan
        osv_out = json.dumps({"results": [{
            "source": {"path": "package-lock.json"},
            "packages": [{"package": {"name": "p", "version": "1"},
                          "vulnerabilities": [{"id": "G",
                              "database_specific": {"severity": "HIGH"}}]}]}]})
        runner = _fake_runner_from({
            "gitleaks": _proc(0, "[]"),
            "osv-scanner": _proc(1, osv_out),
            "semgrep": _proc(0, '{"results": [], "errors": []}')})
        result = run_scan(
            "/proj", project_type="node", has_docker=False,
            which=_which_from({"gitleaks", "osv-scanner", "semgrep"}),
            runner=runner, env={})
        assert not result["gate"]["errors"]
        assert result["gate"]["blocked"] is True  # the high SCA finding blocks

    def test_valid_json_but_wrong_shape_fails_closed_not_crash(self):
        # A scanner that emits valid JSON of an UNEXPECTED shape (here a gitleaks
        # array whose element is a string, not an object) must be recorded as a
        # fail-closed error, NOT crash run_scan with an AttributeError/TypeError.
        from security_scan import run_scan
        runner = _fake_runner_from({
            "gitleaks": _proc(1, '["not-an-object"]'),
            "osv-scanner": _proc(0, '{"results": []}'),
            "semgrep": _proc(0, '{"results": [], "errors": []}'),
        })
        result = run_scan(
            "/proj", project_type="node", has_docker=False,
            which=_which_from({"gitleaks", "osv-scanner", "semgrep"}),
            runner=runner, env={})
        assert result["gate"]["blocked"] is True
        assert any(e["scanner"] == "gitleaks" for e in result["gate"]["errors"])

    def test_env_block_severity_override(self):
        from security_scan import run_scan
        osv_out = json.dumps({"results": [{
            "source": {"path": "package-lock.json"},
            "packages": [{"package": {"name": "p", "version": "1"},
                          "vulnerabilities": [{"id": "G",
                              "database_specific": {"severity": "HIGH"}}]}]}]})
        runner = _fake_runner_from({
            "gitleaks": _proc(0, "[]"),
            "osv-scanner": _proc(1, osv_out),
            "semgrep": _proc(0, '{"results": [], "errors": []}'),
        })
        result = run_scan(
            "/proj", project_type="node", has_docker=False,
            which=_which_from({"gitleaks", "osv-scanner", "semgrep"}),
            runner=runner,
            env={"RAWGENTIC_SECURITY_BLOCK_SEVERITIES": "critical"})
        # high demoted to advisory by the env override
        assert result["gate"]["blocked"] is False
        assert any(f["severity"] == "high" for f in result["gate"]["advisory"])

    def test_gitleaks_invoked_with_diff_scope(self):
        from security_scan import run_scan
        runner = _fake_runner_from({"gitleaks": _proc(0, "[]")})
        run_scan("/proj", project_type="unknown", has_docker=False,
                 base_ref="origin/develop",
                 which=_which_from({"gitleaks"}), runner=runner, env={})
        gitleaks_cmd = next(c for c in runner.calls if c[0] == "gitleaks")
        joined = " ".join(gitleaks_cmd)
        assert "origin/develop..HEAD" in joined
        assert "git" in gitleaks_cmd  # diff mode uses the `git` subcommand

    def test_full_mode_scans_whole_tree_not_diff(self):
        # WF9 audits the entire codebase, not a branch diff: gitleaks scans the
        # working tree (`dir`) and semgrep drops its baseline so it reports all
        # findings, not only new ones.
        from security_scan import run_scan
        runner = _fake_runner_from({
            "gitleaks": _proc(0, "[]"),
            "semgrep": _proc(0, '{"results": [], "errors": []}'),
        })
        run_scan("/proj", project_type="unknown", has_docker=False,
                 base_ref="origin/main", full=True,
                 which=_which_from({"gitleaks", "semgrep"}),
                 runner=runner, env={})
        gitleaks_cmd = next(c for c in runner.calls if c[0] == "gitleaks")
        assert "dir" in gitleaks_cmd and "--log-opts" not in gitleaks_cmd
        semgrep_cmd = next(c for c in runner.calls if c[0] == "semgrep")
        assert "--baseline-commit" not in semgrep_cmd


# --- CLI -------------------------------------------------------------------

class TestCli:
    def _run(self, *args, timeout=30):
        import subprocess
        r = subprocess.run(["python3", str(SCAN_CLI), *args],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout, r.stderr, r.returncode

    def test_cli_exit_zero_when_gate_passes(self, monkeypatch):
        import security_scan
        monkeypatch.setattr(security_scan, "run_scan", lambda *a, **k: {
            "findings": [], "skipped": [{"kind": "sast", "reason": "no semgrep"}],
            "gate": {"blocked": False, "blocking": [], "advisory": [], "errors": []},
        })
        rc = security_scan.main(
            ["scan", "--project-root", "/p", "--project-type", "node", "--json"])
        assert rc == 0

    def test_cli_exit_one_when_blocked(self, monkeypatch):
        import security_scan
        monkeypatch.setattr(security_scan, "run_scan", lambda *a, **k: {
            "findings": [{"kind": "secrets", "severity": "critical",
                          "scanner": "gitleaks", "identifier": "x",
                          "location": "f", "title": "t"}],
            "skipped": [],
            "gate": {"blocked": True, "blocking": [{"kind": "secrets"}],
                     "advisory": [], "errors": []},
        })
        rc = security_scan.main(
            ["scan", "--project-root", "/p", "--project-type", "node", "--json"])
        assert rc == 1

    def test_cli_json_shape(self, monkeypatch, capsys):
        import security_scan
        monkeypatch.setattr(security_scan, "run_scan", lambda *a, **k: {
            "findings": [], "skipped": [],
            "gate": {"blocked": False, "blocking": [], "advisory": [], "errors": []},
        })
        security_scan.main(
            ["scan", "--project-root", "/p", "--project-type", "node", "--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert set(data) >= {"findings", "skipped", "gate"}

    def test_cli_requires_subcommand(self):
        _, _, rc = self._run()
        assert rc == 2  # argparse usage error


# --- real-tool smoke (skips when scanners absent, e.g. CI) -----------------

class TestRealToolSmoke:
    """Exercises run_scan against the real repo with whatever scanners happen to
    be installed. Skips entirely in a bare environment (CI installs none), so it
    never flakes — it only adds signal locally where the tools exist."""

    def test_scan_repo_does_not_crash(self):
        import shutil
        from security_scan import run_scan
        if not shutil.which("gitleaks"):
            pytest.skip("no scanners installed")
        repo = str(Path(__file__).resolve().parent.parent.parent)
        result = run_scan(repo, project_type="python", has_docker=False,
                          base_ref="HEAD")  # empty diff -> no new secrets
        assert set(result) >= {"findings", "skipped", "gate"}
        assert isinstance(result["gate"]["blocked"], bool)


# --- wiring drift guards ---------------------------------------------------

REPO_ROOT = HOOKS_DIR.parent
SKILLS_DIR = REPO_ROOT / "skills"
INSTALLER = REPO_ROOT / "scripts" / "install-scanners.sh"


class TestWF2Wiring:
    """WF2 Step 11.5 must invoke the shared lib as a real pre-PR gate."""

    def _text(self):
        return (SKILLS_DIR / "implement-feature" / "SKILL.md").read_text()

    def test_step_11_5_exists_and_invokes_lib(self):
        t = self._text()
        assert "## Step 11.5" in t
        assert "hooks/security_scan.py" in t
        # positioned before Step 12 (the PR step), i.e. a true pre-PR gate
        assert t.index("## Step 11.5") < t.index("## Step 12:")

    def test_step_11_5_reads_gate_and_handles_secrets(self):
        t = self._text()
        assert "gate.blocking" in t
        assert "rotat" in t.lower()  # real secret -> must be rotated, not just deleted

    def test_completion_gate_includes_security_scan(self):
        t = self._text()
        gate = t[t.index("<completion-gate>"):t.index("</completion-gate>")]
        assert "Security scan" in gate and "11.5" in gate


class TestWF9Wiring:
    """WF9 must reuse the SAME lib (full mode) and the stale Serena ref is gone."""

    def _text(self):
        return (SKILLS_DIR / "security-audit" / "SKILL.md").read_text()

    def test_wf9_invokes_shared_lib_full_mode(self):
        t = self._text()
        assert "hooks/security_scan.py" in t
        assert "--full" in t

    def test_stale_serena_reference_removed(self):
        assert "Serena" not in self._text()


class TestSetupWiring:
    """Setup Step 2e installs the scanners by default (opt-out, not opt-in)."""

    def _text(self):
        return (SKILLS_DIR / "setup" / "SKILL.md").read_text()

    def test_step_2e_exists_and_runs_installer(self):
        t = self._text()
        assert "## Step 2e" in t
        assert "install-scanners.sh" in t

    def test_step_2e_is_opt_out(self):
        t = self._text()
        low = t.lower()
        assert "opt-out" in low or "opt out" in low
        assert "installScanners" in t  # the persisted decline flag


class TestInstallerScript:
    """The installer is idempotent, opt-out-aware, and covers every scanner."""

    def test_exists_and_executable(self):
        assert INSTALLER.exists()
        assert os.access(INSTALLER, os.X_OK), "install-scanners.sh must be +x"

    def test_lists_all_scanners(self):
        t = INSTALLER.read_text()
        for tool in ("gitleaks", "semgrep", "osv-scanner", "trivy", "pip-audit"):
            assert tool in t, f"installer must handle {tool}"

    def test_opt_out_env_is_honored(self):
        import subprocess
        r = subprocess.run(
            ["bash", str(INSTALLER)],
            env={**os.environ, "RAWGENTIC_SKIP_SCANNER_INSTALL": "1"},
            capture_output=True, text=True, timeout=30)
        assert r.returncode == 0
        assert "opted out" in r.stdout.lower()

    def test_check_mode_reports_presence(self):
        import subprocess
        r = subprocess.run(["bash", str(INSTALLER), "--check"],
                           capture_output=True, text=True, timeout=30)
        # exit 0 iff all present, 1 if any missing; either way it reports lines
        assert r.returncode in (0, 1)
        assert "gitleaks" in r.stdout


class TestSessionStartBootstrap:
    """The session-start bootstrap is startup-only, opt-out, and run-once."""

    def _text(self):
        return (HOOKS_DIR / "session-start").read_text()

    def test_bootstrap_section_present(self):
        t = self._text()
        assert "_do_scanner_bootstrap" in t
        assert "install-scanners.sh" in t

    def test_bootstrap_is_opt_out_and_guarded(self):
        t = self._text()
        assert "RAWGENTIC_SKIP_SCANNER_INSTALL" in t
        assert "installScanners" in t
        assert "scanners-bootstrapped" in t  # run-once marker
        # must not block the hook: fire-and-forget
        assert "nohup" in t
