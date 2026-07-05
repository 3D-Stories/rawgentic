#!/usr/bin/env python3
"""Shared tool-based security scanner for rawgentic workflows.

WF2 (implement-feature) Step 11.5 and /rawgentic:scan both shell out to this
one tested lib so the *tool-based* part of a security review — running actual
scanners and turning their output into a gate decision — lives in a single
fail-closed place instead of being re-derived in fragile prose. The LLM-reasoning
part of security review (authorization/business-logic, STRIDE) is unchanged and
complementary; scanners cannot find those, and this lib never pretends to.

Coverage (each gated on project_type/has_docker AND on the tool being installed):
- secrets : gitleaks, scanned over the branch diff (`<base>..HEAD`) so a
            pre-existing fixture secret elsewhere in history doesn't block the PR.
- sca     : dependency-CVE scan. Prefers osv-scanner (one binary, every
            ecosystem); falls back to `npm audit` (node) or `pip-audit` (python).
- sast    : semgrep with the low-false-positive `p/ci` ruleset, diff-scoped via
            --baseline-commit; blocks only on ERROR-severity findings.
- iac     : trivy config (Dockerfile/compose/Actions/k8s/Terraform misconfig),
            only when the project has Docker (capabilities.has_docker).

Gate philosophy (the security-critical heart, exhaustively unit-tested):
- FAIL CLOSED on a real finding: a leaked secret always blocks; a Critical/High
  dependency CVE or SAST/IaC finding blocks; a known CVE with *no* severity
  rating blocks (it is still a known CVE). The blocking severity set is
  env-configurable (RAWGENTIC_SECURITY_BLOCK_SEVERITIES, default critical,high).
- FAIL CLOSED on a broken scanner: a tool that ran but produced unparseable
  output is recorded as an error and blocks — "I couldn't tell" is never "clean".
- DEGRADE VISIBLY on a missing tool: a scanner whose tool isn't installed is
  reported as a skip (with a reason) and does NOT block. setup is what ensures
  the tools exist; this gate must still run usefully on whatever is present.

Design: pure functions (normalize_severity, the per-tool parsers,
select_scanners, decide_gate) carry all the logic and are fully unit-tested; the
single impure orchestrator `run_scan` takes injectable `which`/`runner` so the
subprocess wiring is deterministic in tests with no scanner installed.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys


class SecurityScanError(ValueError):
    """A scanner produced output that could not be parsed. Surfaced as a gate
    error (fail-closed) rather than mistaken for a clean result."""


# Default blocking threshold. Findings at this severity OR ABOVE block the PR;
# below is advisory. Override via RAWGENTIC_SECURITY_BLOCK_SEVERITIES (comma-
# separated) — treated as a THRESHOLD (the lowest level listed blocks that level
# and everything above), so a lower setting only makes the gate STRICTER and can
# never accidentally make high/critical advisory. Env-configurable from v1.
BLOCK_SEVERITIES_DEFAULT = ("critical", "high")

_SEV_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "unknown": 0}

# A scanner that exits with an unexpected code AND produced no findings may have
# failed — fail closed — UNLESS its stderr says there was simply nothing to scan
# (e.g. osv-scanner on a project with no lockfiles), which is a benign skip.
_NOTHING_TO_SCAN_RE = re.compile(
    r"no package sources found|no packages? found|nothing to scan|"
    r"no supported (?:lockfiles?|manifests?)", re.IGNORECASE)

# Per-tool severity vocabularies folded into the canonical scale.
_NPM_SEV = {"critical": "critical", "high": "high", "moderate": "medium",
            "low": "low", "info": "low"}
_GENERIC_SEV = {"critical": "critical", "high": "high", "moderate": "medium",
                "medium": "medium", "low": "low"}
_SEMGREP_SEV = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}
_TRIVY_SEV = {"critical": "critical", "high": "high", "medium": "medium",
              "low": "low", "unknown": "unknown"}


def normalize_severity(tool: str, raw) -> str:
    """Fold a tool-specific severity into {critical,high,medium,low,unknown}.

    Unrecognized/absent values degrade to "unknown" rather than guessing — the
    gate then decides what "unknown" means per finding kind."""
    if tool == "gitleaks":
        return "critical"  # any secret is critical, regardless of metadata
    if raw is None:
        return "unknown"
    if tool == "semgrep":
        return _SEMGREP_SEV.get(str(raw).upper(), "unknown")
    if tool == "npm":
        return _NPM_SEV.get(str(raw).lower(), "unknown")
    if tool == "trivy":
        return _TRIVY_SEV.get(str(raw).lower(), "unknown")
    # osv-scanner / generic database severities
    return _GENERIC_SEV.get(str(raw).lower(), "unknown")


def _finding(scanner, kind, severity, identifier, location, title):
    return {"scanner": scanner, "kind": kind, "severity": severity,
            "identifier": identifier, "location": location, "title": title}


def _loads(stdout):
    """Parse JSON; blank output means the tool emitted nothing (no findings)."""
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SecurityScanError(f"unparseable scanner output: {exc}")


# --- parsers ---------------------------------------------------------------

def parse_gitleaks(stdout) -> list:
    """gitleaks --report-format json emits a JSON array of leak objects (or
    nothing/null when clean)."""
    data = _loads(stdout)
    if not data:
        return []
    if not isinstance(data, list):
        raise SecurityScanError("gitleaks output was not a JSON array")
    out = []
    for leak in data:
        rule = leak.get("RuleID") or leak.get("Rule") or "secret"
        loc = leak.get("File", "")
        line = leak.get("StartLine")
        if line:
            loc = f"{loc}:{line}"
        out.append(_finding(
            "gitleaks", "secrets", "critical", rule, loc,
            leak.get("Description", "potential secret")))
    return out


def parse_osv_scanner(stdout) -> list:
    """osv-scanner --format json: results[].packages[].vulnerabilities[].
    Severity prefers database_specific.severity, else falls through to unknown."""
    data = _loads(stdout)
    if not data:
        return []
    out = []
    for res in data.get("results", []) or []:
        src = (res.get("source") or {}).get("path", "")
        for pkg in res.get("packages", []) or []:
            name = (pkg.get("package") or {}).get("name", "?")
            for vuln in pkg.get("vulnerabilities", []) or []:
                raw_sev = (vuln.get("database_specific") or {}).get("severity")
                out.append(_finding(
                    "osv-scanner", "sca",
                    normalize_severity("osv-scanner", raw_sev),
                    vuln.get("id", "?"), src,
                    f"{name}: {vuln.get('id', 'vulnerability')}"))
    return out


def parse_npm_audit(stdout) -> list:
    """npm audit --json (v7+): vulnerabilities is a map keyed by package name.

    On failure (e.g. no lockfile) npm emits {"error": {...}} with no
    "vulnerabilities" key AND exits 1 — the same exit code as "vulns found" — so
    the error envelope, not the exit code, is what distinguishes a failed audit
    from a clean one. Treat it as fail-closed, never as "no vulnerabilities"."""
    data = _loads(stdout)
    if not data:
        return []
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        summary = err.get("summary") if isinstance(err, dict) else err
        raise SecurityScanError(f"npm audit failed: {summary}")
    out = []
    for name, info in (data.get("vulnerabilities") or {}).items():
        out.append(_finding(
            "npm", "sca", normalize_severity("npm", info.get("severity")),
            name, "package-lock.json", f"{name}: vulnerable dependency"))
    return out


def parse_pip_audit(stdout) -> list:
    """pip-audit --format json: dependencies[].vulns[] (severity usually absent
    -> unknown, which the gate treats conservatively for a known CVE)."""
    data = _loads(stdout)
    if not data:
        return []
    out = []
    deps = data.get("dependencies", data) if isinstance(data, dict) else data
    for dep in deps or []:
        name = dep.get("name", "?")
        for vuln in dep.get("vulns", []) or []:
            raw_sev = vuln.get("severity")  # frequently None
            out.append(_finding(
                "pip-audit", "sca",
                normalize_severity("osv-scanner", raw_sev),
                vuln.get("id", "?"), "requirements",
                f"{name}: {vuln.get('id', 'vulnerability')}"))
    return out


def parse_semgrep(stdout) -> list:
    """semgrep --json: results[] with extra.severity (ERROR/WARNING/INFO)."""
    data = _loads(stdout)
    if not data:
        return []
    out = []
    for r in data.get("results", []) or []:
        extra = r.get("extra") or {}
        loc = r.get("path", "")
        line = (r.get("start") or {}).get("line")
        if line:
            loc = f"{loc}:{line}"
        out.append(_finding(
            "semgrep", "sast",
            normalize_severity("semgrep", extra.get("severity")),
            r.get("check_id", "?"), loc,
            extra.get("message", "static-analysis finding")))
    return out


def parse_trivy_config(stdout) -> list:
    """trivy config --format json: Results[].Misconfigurations[]."""
    data = _loads(stdout)
    if not data:
        return []
    out = []
    for res in data.get("Results", []) or []:
        target = res.get("Target", "")
        for mis in res.get("Misconfigurations", []) or []:
            out.append(_finding(
                "trivy", "iac",
                normalize_severity("trivy", mis.get("Severity")),
                mis.get("ID", "?"), target,
                mis.get("Title", "misconfiguration")))
    return out


# --- scanner registry + selection -----------------------------------------

# project_type strings that imply a Node / Python toolchain for SCA fallback.
_NODE_TYPES = {"node", "nodejs", "javascript", "typescript", "ts", "js",
               "sveltekit", "svelte", "next", "react", "vue", "express"}
_PYTHON_TYPES = {"python", "py", "django", "flask", "fastapi"}


def _is_node(project_type: str) -> bool:
    return (project_type or "").lower() in _NODE_TYPES


def _is_python(project_type: str) -> bool:
    return (project_type or "").lower() in _PYTHON_TYPES


def _build_gitleaks(project_root, base_ref, full):
    # gitleaks 8.x: `detect` is deprecated. `-r -` streams the JSON report to
    # stdout (logs go to stderr); --redact so secret values never land in our
    # output. Diff mode (WF2 pre-PR gate): `git <path> --log-opts <base>..HEAD`
    # scopes to the branch's new commits so a pre-existing fixture secret
    # elsewhere doesn't block the PR. Full mode (WF9 audit): `dir <path>` scans
    # the whole working tree.
    common = ["--no-banner", "--redact", "--report-format", "json",
              "--report-path", "-"]
    if full:
        return ["gitleaks", "dir", project_root, *common]
    return ["gitleaks", "git", project_root, *common,
            "--log-opts", f"{base_ref}..HEAD"]


def _build_osv(project_root, base_ref, full):
    return ["osv-scanner", "--format", "json", "--recursive", project_root]


def _build_npm(project_root, base_ref, full):
    return ["npm", "audit", "--json", "--prefix", project_root]


def _build_pip_audit(project_root, base_ref, full):
    # `pip-audit` with no target audits the AMBIENT Python environment, not the
    # project — so it must be pointed at the project's requirements files with
    # -r. If there are none (e.g. a poetry/pyproject project, which osv-scanner
    # handles as the primary tool), return None so the orchestrator records a
    # visible skip rather than silently auditing the wrong thing.
    reqs = sorted(glob.glob(os.path.join(project_root, "requirements*.txt")))
    if not reqs:
        return None
    cmd = ["pip-audit", "--format", "json"]
    for r in reqs:
        cmd += ["-r", r]
    return cmd


def _build_semgrep(project_root, base_ref, full):
    # p/ci is the low-false-positive ruleset. Diff mode: --baseline-commit reports
    # only findings introduced since base. Full mode (WF9 audit): scan everything.
    cmd = ["semgrep", "--config", "p/ci", "--json", "--quiet"]
    if not full:
        cmd += ["--baseline-commit", base_ref]
    cmd.append(project_root)
    return cmd


def _build_trivy_config(project_root, base_ref, full):
    # trivy reads .trivyignore ONLY from its process cwd, not the scan target,
    # and the gate (_default_runner) runs trivy from an uncontrolled cwd — so a
    # committed, reviewed project-local .trivyignore would be SILENTLY IGNORED
    # without an explicit --ignorefile. Pass it when present (anchored to the
    # DECLARED project_root, never an arbitrary path) so the gate honors the
    # project's documented misconfig suppressions deterministically, regardless
    # of cwd. Absent -> the command is byte-for-byte unchanged (today's behavior).
    cmd = ["trivy", "config", "--quiet", "--format", "json"]
    ignorefile = os.path.join(project_root, ".trivyignore")
    if os.path.isfile(ignorefile):
        cmd += ["--ignorefile", ignorefile]
    cmd.append(project_root)
    return cmd


# Each scanner: a kind + ordered tool candidates. select_scanners picks the first
# candidate whose tool is installed AND applies to the project; if a candidate
# applies but its tool is missing, the scanner is skipped *with a reason*.
SCANNERS = [
    # clean_rc = exit codes meaning "ran fine, NO findings"; found_rc = "ran fine,
    # findings PRESENT". An rc in found_rc with zero parsed findings is an
    # inconsistency (the tool flagged findings but we extracted none — schema
    # drift / error envelope) and fails closed. An rc in neither is abnormal.
    {"kind": "secrets", "applies": lambda pt, docker: True,
     "candidates": [
         {"tool": "gitleaks", "applies": lambda pt: True,
          "build": _build_gitleaks, "parse": parse_gitleaks,
          "clean_rc": frozenset({0}), "found_rc": frozenset({1})}],  # 0=clean,1=leaks,>1=error
     "missing_reason": "gitleaks not installed (install via /rawgentic:setup)"},
    {"kind": "sca", "applies": lambda pt, docker: True,
     "candidates": [
         {"tool": "osv-scanner", "applies": lambda pt: True,
          "build": _build_osv, "parse": parse_osv_scanner,
          "clean_rc": frozenset({0}), "found_rc": frozenset({1})},  # "no sources"=128->stderr skip
         {"tool": "npm", "applies": _is_node,
          "build": _build_npm, "parse": parse_npm_audit,
          "clean_rc": frozenset({0}), "found_rc": frozenset({1})},  # error envelope caught in parser
         {"tool": "pip-audit", "applies": _is_python,
          "build": _build_pip_audit, "parse": parse_pip_audit,
          "clean_rc": frozenset({0}), "found_rc": frozenset({1})}],
     "missing_reason": "no dependency scanner installed "
                       "(osv-scanner/npm/pip-audit) — install via /rawgentic:setup"},
    {"kind": "sast", "applies": lambda pt, docker: True,
     "candidates": [
         {"tool": "semgrep", "applies": lambda pt: True,
          "build": _build_semgrep, "parse": parse_semgrep,
          # no --error: semgrep exits 0 on success WITH or WITHOUT findings, so
          # there is no findings-indicated code; nonzero is always an error.
          "clean_rc": frozenset({0}), "found_rc": frozenset()}],
     "missing_reason": "semgrep not installed (install via /rawgentic:setup)"},
    {"kind": "iac", "applies": lambda pt, docker: bool(docker),
     "candidates": [
         {"tool": "trivy", "applies": lambda pt: True,
          "build": _build_trivy_config, "parse": parse_trivy_config,
          # no --exit-code: trivy exits 0 on success (with or without findings).
          "clean_rc": frozenset({0}), "found_rc": frozenset()}],
     "missing_reason": "trivy not installed (install via /rawgentic:setup)"},
]


def select_scanners(project_type, has_docker, available):
    """Resolve which scanners run given the project + installed tools.

    Returns (to_run, skipped). to_run items are
    {kind, tool, build, parse}; skipped items are {kind, reason}. A scanner not
    applicable to the project is skipped with a distinct reason from one whose
    tool is merely missing — both are surfaced, never silently dropped.
    """
    available = set(available)
    to_run, skipped = [], []
    for scanner in SCANNERS:
        if not scanner["applies"](project_type, has_docker):
            skipped.append({"kind": scanner["kind"],
                            "reason": "not applicable to this project"})
            continue
        # candidates that apply to this project, in preference order
        applicable = [c for c in scanner["candidates"] if c["applies"](project_type)]
        chosen = next((c for c in applicable if c["tool"] in available), None)
        if chosen is None:
            skipped.append({"kind": scanner["kind"],
                            "reason": scanner["missing_reason"]})
            continue
        to_run.append({"kind": scanner["kind"], "tool": chosen["tool"],
                       "build": chosen["build"], "parse": chosen["parse"],
                       "clean_rc": chosen.get("clean_rc", frozenset({0})),
                       "found_rc": chosen.get("found_rc", frozenset({1}))})
    return to_run, skipped


# --- gate ------------------------------------------------------------------

def _resolve_block_severities(env):
    raw = (env or {}).get("RAWGENTIC_SECURITY_BLOCK_SEVERITIES")
    if not raw:
        return tuple(BLOCK_SEVERITIES_DEFAULT)
    return tuple(s.strip().lower() for s in raw.split(",") if s.strip())


def _block_threshold(block_severities) -> int:
    """The minimum severity RANK that blocks. block_severities is treated as a
    THRESHOLD, not exact membership: the lowest configured severity blocks that
    level and everything above it. So `medium` blocks medium/high/critical and
    can never accidentally make high/critical advisory.

    Fail-closed on a bad config: if it is empty OR contains ANY unrecognized
    token (a typo like `critical,hgih` must not silently drop to blocking only
    `critical`), the WHOLE override is rejected and we fall back to the default
    (block high+critical). This guarantees a typo can never make the gate laxer
    than the default — though it may not honor an intended *stricter* config
    (e.g. `medium,hgih` falls back to `high`), so run_scan also warns when the
    configured value is invalid."""
    tokens = [s.strip().lower() for s in block_severities if s and s.strip()]
    if not tokens or any(t not in _SEV_ORDER for t in tokens):
        tokens = list(BLOCK_SEVERITIES_DEFAULT)
    return min(_SEV_ORDER[t] for t in tokens)


def _is_blocking(finding, min_rank) -> bool:
    if finding["kind"] == "secrets":
        return True  # a leaked secret is never advisory
    if finding["kind"] == "sca" and finding["severity"] == "unknown":
        return True  # a known CVE with no rating is still a known CVE
    return _SEV_ORDER.get(finding["severity"], 0) >= min_rank


def decide_gate(findings, errors=(), block_severities=None) -> dict:
    """Partition findings into blocking vs advisory and decide the gate.

    Fail-closed: any blocking finding OR any scanner error -> blocked. Errors are
    installed-but-broken scanners (unparseable output) — distinct from a missing
    tool, which never reaches here (it is a skip)."""
    if block_severities is None:
        block_severities = BLOCK_SEVERITIES_DEFAULT
    min_rank = _block_threshold(block_severities)
    blocking, advisory = [], []
    for f in findings:
        (blocking if _is_blocking(f, min_rank) else advisory).append(f)
    errors = list(errors)
    return {
        "blocked": bool(blocking) or bool(errors),
        "blocking": blocking,
        "advisory": advisory,
        "errors": errors,
    }


# --- orchestrator ----------------------------------------------------------

def _default_runner(cmd, **kwargs):
    # **kwargs forwards run_scan's cwd=project_root (and anything else) straight to
    # subprocess.run, so every scanner runs from the project root regardless of the
    # gate's own working directory (#101).
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def run_scan(project_root, *, project_type="unknown", has_docker=False,
             base_ref="origin/main", full=False, which=None, runner=None,
             env=None) -> dict:
    """Run the applicable, installed scanners over `project_root` and return
    {findings, skipped, gate}. `full` switches secrets/SAST from diff-scoped (WF2
    pre-PR gate) to whole-tree (WF9 audit). `which`/`runner`/`env` are injectable
    so the whole orchestration is deterministic in tests with no scanner
    installed."""
    which = which or shutil.which
    runner = runner or _default_runner
    env = os.environ if env is None else env
    # Normalize to an absolute path ONCE so the gate is cwd-independent for every
    # scanner (#101): this same value is both the scan TARGET handed to each
    # builder below AND the subprocess `cwd` threaded into the runner, so the two
    # can never disagree (a relative target would otherwise be re-rooted against a
    # changed cwd). semgrep in particular resolves `--baseline-commit` against its
    # process cwd, not the target, so without this it exits rc=2 — fail-closing the
    # whole gate with zero findings — whenever the gate is invoked from any dir
    # other than the repo root. Same class of latent cwd-dependence v2.36.0 fixed
    # for trivy's .trivyignore.
    project_root = os.path.abspath(project_root)

    # Resolve availability against the union of every candidate tool.
    candidate_tools = {c["tool"] for s in SCANNERS for c in s["candidates"]}
    available = {t for t in candidate_tools if which(t)}
    to_run, skipped = select_scanners(project_type, has_docker, available)

    findings, errors = [], []
    for scanner in to_run:
        cmd = scanner["build"](project_root, base_ref, full)
        if cmd is None:
            # The scanner has nothing to point at in this project (e.g. pip-audit
            # with no requirements file). Visible skip, not a silent clean.
            skipped.append({
                "kind": scanner["kind"],
                "reason": f"{scanner['tool']}: no scannable target in project"})
            continue
        try:
            # Run each scanner from the (absolute) project root so its git /
            # baseline resolution happens against the target repo, not the caller's
            # cwd (#101). _default_runner forwards cwd to subprocess.run via
            # **kwargs; injected test runners accept (and may assert on) it too.
            proc = runner(cmd, cwd=project_root)
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append({"scanner": scanner["tool"],
                           "message": f"failed to run: {exc}"})
            continue
        before = len(findings)
        try:
            # A nonzero exit is EXPECTED on findings (gitleaks=1, npm audit=1,
            # osv=1), so findings come from stdout, not the exit code.
            findings.extend(scanner["parse"](proc.stdout))
        except Exception as exc:
            # Fail closed on ANY parse failure — the SecurityScanError we raise
            # for non-JSON / an npm error envelope, or an unexpected-but-valid
            # JSON shape that trips a KeyError/TypeError/AttributeError. Either
            # way it becomes a blocking error, never a crash and never "no
            # findings." Then move on — the exit-code check below would
            # double-count this scanner.
            errors.append({
                "scanner": scanner["tool"],
                "message": f"{type(exc).__name__}: {exc} "
                           f"(rc={proc.returncode}; "
                           f"{(proc.stderr or '').strip()[:200]})"})
            continue
        produced = len(findings) > before
        rc = proc.returncode
        stderr = proc.stderr or ""
        if rc in scanner["found_rc"] and not produced:
            # The exit code says "findings present" but the parser extracted
            # none — schema drift or an error envelope masking real findings.
            # Fail closed: "the tool found something we couldn't read" is never
            # "clean."
            errors.append({
                "scanner": scanner["tool"],
                "message": f"exited rc={rc} indicating findings, but none were "
                           f"parsed — possible scanner/output drift; "
                           f"{stderr.strip()[:200]}"})
        elif rc not in scanner["clean_rc"] and rc not in scanner["found_rc"]:
            # An unexpected exit code: the scan may have run incompletely — fail
            # closed EVEN IF some findings were parsed. The only benign exception
            # is a *dependency* (SCA) scanner reporting it had no package sources
            # AND producing nothing — genuinely "nothing to scan." SCA-only, so a
            # secrets/SAST/IaC tool whose stderr happens to say "no packages"
            # still fails closed.
            if (not produced and scanner["kind"] == "sca"
                    and _NOTHING_TO_SCAN_RE.search(stderr)):
                skipped.append({
                    "kind": scanner["kind"],
                    "reason": f"{scanner['tool']}: nothing to scan (rc={rc})"})
            else:
                errors.append({
                    "scanner": scanner["tool"],
                    "message": f"exited rc={rc}; scan may be incomplete; "
                               f"{stderr.strip()[:200]}"})

    block_severities = _resolve_block_severities(env)
    # Surface an invalid override instead of silently falling back to default —
    # otherwise an intended-stricter config (e.g. "medium,hgih") is dropped with
    # no signal.
    invalid = [s for s in block_severities
               if s.strip() and s.strip().lower() not in _SEV_ORDER]
    if invalid:
        print(f"warning: RAWGENTIC_SECURITY_BLOCK_SEVERITIES has unrecognized "
              f"value(s) {invalid}; falling back to the fail-closed default "
              f"{list(BLOCK_SEVERITIES_DEFAULT)}", file=sys.stderr)
    gate = decide_gate(findings, errors, block_severities)
    return {"findings": findings, "skipped": skipped, "gate": gate}


# --- human rendering + CLI -------------------------------------------------

def render_text(result: dict) -> str:
    gate = result["gate"]
    lines = []
    status = "BLOCKED" if gate["blocked"] else "PASS"
    lines.append(f"Security scan: {status}")
    if gate["blocking"]:
        lines.append(f"\nBlocking ({len(gate['blocking'])}) — fix before PR:")
        for f in gate["blocking"]:
            lines.append(f"  [{f['severity']}] {f['kind']}: {f['identifier']} "
                         f"@ {f['location']} — {f['title']}")
    if gate["errors"]:
        lines.append(f"\nScanner errors ({len(gate['errors'])}) — fail-closed:")
        for e in gate["errors"]:
            lines.append(f"  {e['scanner']}: {e['message']}")
    if gate["advisory"]:
        lines.append(f"\nAdvisory ({len(gate['advisory'])}) — note, fix if easy:")
        for f in gate["advisory"]:
            lines.append(f"  [{f['severity']}] {f['kind']}: {f['identifier']} "
                         f"@ {f['location']}")
    if result["skipped"]:
        lines.append(f"\nSkipped ({len(result['skipped'])}) — tool absent / "
                     f"not applicable (NOT a pass):")
        for s in result["skipped"]:
            lines.append(f"  {s['kind']}: {s['reason']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    """CLI entry point.

    Subcommand:
      scan  run the applicable installed scanners over --project-root and print a
            report ({findings, skipped, gate} JSON with --json, else human text).

    Exit codes:
      0  scan ran; gate PASS (no blocking findings; skips are allowed)
      1  gate BLOCKED — blocking finding(s) or an installed-but-broken scanner
      2  argparse usage error
    """
    parser = argparse.ArgumentParser(prog="security_scan")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan", help="run tool-based security scanners")
    p.add_argument("--project-root", required=True)
    p.add_argument("--project-type", default="unknown",
                   help="capabilities.project_type (drives SCA tool fallback)")
    p.add_argument("--has-docker", action="store_true",
                   help="capabilities.has_docker (enables the IaC scan)")
    p.add_argument("--base-ref", default="origin/main",
                   help="diff base for secret/SAST scoping (e.g. origin/main)")
    p.add_argument("--full", action="store_true",
                   help="scan the whole tree (WF9 audit) instead of the branch "
                        "diff (WF2 pre-PR gate)")
    p.add_argument("--json", action="store_true",
                   help="emit JSON instead of human-readable text")
    args = parser.parse_args(argv)

    if args.cmd == "scan":
        result = run_scan(
            args.project_root, project_type=args.project_type,
            has_docker=args.has_docker, base_ref=args.base_ref, full=args.full)
        if args.json:
            print(json.dumps(result, separators=(",", ":")))
        else:
            print(render_text(result))
        return 1 if result["gate"]["blocked"] else 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
