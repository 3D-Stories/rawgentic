#!/usr/bin/env python3
"""Grade all eval runs against their assertions."""
import json
import os
import re

BASE = "/home/rocky00717/claude-personal/projects/rawgentic/skills/create-tests-workspace/iteration-1"


def files_in(path):
    """Recursively list all files under path."""
    result = []
    if not os.path.exists(path):
        return result
    for root, dirs, files in os.walk(path):
        for f in files:
            result.append(os.path.join(root, f))
    return result


def file_contains(path, pattern):
    """Check if any file under path contains pattern (regex)."""
    for fp in files_in(path):
        if fp.endswith(('.pyc', '.pyo', '__pycache__')):
            continue
        try:
            with open(fp, 'r', errors='ignore') as f:
                content = f.read()
                if re.search(pattern, content, re.IGNORECASE):
                    return True, fp
        except:
            pass
    return False, None


def any_file_matches(path, pattern):
    """Check if any file under path matches the glob-like pattern."""
    for fp in files_in(path):
        basename = os.path.basename(fp)
        if re.match(pattern, basename):
            return True, fp
    return False, None


def read_summary(path):
    """Read run_summary.md if exists."""
    summary_path = os.path.join(path, "run_summary.md")
    if os.path.exists(summary_path):
        with open(summary_path) as f:
            return f.read()
    return ""


def grade_python_greenfield(variant_path):
    """Grade eval 1 assertions."""
    results = []
    summary = read_summary(variant_path)

    # 1. pytest-config-created
    has_pyproject, _ = any_file_matches(variant_path, r"pyproject\.toml")
    has_pytestini, _ = any_file_matches(variant_path, r"pytest\.ini")
    has_conftest, _ = any_file_matches(variant_path, r"conftest\.py")
    passed = has_pyproject or has_pytestini or has_conftest
    results.append({
        "text": "A pytest config file exists (pyproject.toml with pytest section, or pytest.ini, or conftest.py)",
        "passed": passed,
        "evidence": f"pyproject.toml={has_pyproject}, pytest.ini={has_pytestini}, conftest.py={has_conftest}"
    })

    # 2. test-files-created
    found, fp = any_file_matches(variant_path, r"test_.*\.py")
    if not found:
        found, fp = any_file_matches(variant_path, r".*_test\.py")
    results.append({
        "text": "At least one test file matching test_*.py or *_test.py was generated",
        "passed": found,
        "evidence": f"Found: {fp}" if found else "No test files found"
    })

    # 3. covers-main-module
    found, fp = file_contains(variant_path, r"gpu_fan_control")
    results.append({
        "text": "Tests import or reference gpu_fan_controller module",
        "passed": found,
        "evidence": f"Referenced in {fp}" if found else "No reference found"
    })

    # 4. has-real-assertions
    found, fp = file_contains(variant_path, r"\bassert\b")
    results.append({
        "text": "Test files contain actual assert statements, not just pass or empty functions",
        "passed": found,
        "evidence": f"Assert found in {fp}" if found else "No asserts found"
    })

    # 5. testing-strategy-doc
    has_strategy = False
    evidence = "No strategy doc found"
    for fp in files_in(variant_path):
        if fp.endswith('.md') and 'strategy' in fp.lower():
            has_strategy = True
            evidence = f"Found: {fp}"
            break
        if fp.endswith('.md') and 'plan' in os.path.dirname(fp).lower():
            has_strategy = True
            evidence = f"Found: {fp}"
            break
    results.append({
        "text": "A testing strategy document was created (any markdown file describing the test plan)",
        "passed": has_strategy,
        "evidence": evidence
    })

    # 6. context7-used (check summary for context7 mention)
    c7_used = bool(re.search(r"context7|resolve.library.id|query.docs", summary, re.IGNORECASE))
    results.append({
        "text": "Context7 MCP was invoked to retrieve pytest documentation",
        "passed": c7_used,
        "evidence": "context7 mentioned in run_summary" if c7_used else "No context7 reference in summary"
    })

    return results


def grade_shell_targeted(variant_path):
    """Grade eval 2 assertions."""
    results = []

    # 1. bats-test-created
    found_bats, fp = any_file_matches(variant_path, r".*\.bats$")
    # Also check for .sh files that ARE bats-like tests
    if not found_bats:
        found_bats, fp = file_contains(variant_path, r"@test\s+")
    results.append({
        "text": "At least one .bats test file exists in the outputs",
        "passed": found_bats,
        "evidence": f"Found: {fp}" if found_bats else "No .bats files found"
    })

    # 2. targets-install-sh
    found, fp = file_contains(variant_path, r"install\.sh")
    results.append({
        "text": "Tests reference or source install.sh",
        "passed": found,
        "evidence": f"Referenced in {fp}" if found else "No install.sh reference"
    })

    # 3. test-helper-created
    has_helper = False
    evidence = "No test helper found"
    for fp in files_in(variant_path):
        bn = os.path.basename(fp).lower()
        if 'helper' in bn or 'framework' in bn:
            has_helper = True
            evidence = f"Found: {fp}"
            break
    # Also check for setup/teardown functions
    if not has_helper:
        found, fp = file_contains(variant_path, r"(setup|teardown)\s*\(\s*\)")
        if found:
            has_helper = True
            evidence = f"setup/teardown found in {fp}"
    results.append({
        "text": "A test helper file exists with setup/teardown functions",
        "passed": has_helper,
        "evidence": evidence
    })

    # 4. covers-dep-checks
    found, fp = file_contains(variant_path, r"(depend|missing.*command|command.*not.*found|which|command -v)")
    results.append({
        "text": "Tests cover dependency checking logic",
        "passed": found,
        "evidence": f"Dependency test logic in {fp}" if found else "No dependency checking tests"
    })

    # 5. covers-file-operations
    found, fp = file_contains(variant_path, r"(cp |copy|install.*file|file.*install|mkdir|permission)")
    results.append({
        "text": "Tests cover file copy/install operations",
        "passed": found,
        "evidence": f"File operations tested in {fp}" if found else "No file operation tests"
    })

    # 6. mocks-external-commands
    found, fp = file_contains(variant_path, r"(mock|stub|PATH|fake.*bin|/tmp.*bin)")
    results.append({
        "text": "Tests mock or stub external commands",
        "passed": found,
        "evidence": f"Mocking found in {fp}" if found else "No mocking of external commands"
    })

    return results


def grade_multi_lang(variant_path):
    """Grade eval 3 assertions."""
    results = []
    summary = read_summary(variant_path)

    # 1. gap-analysis-produced
    has_gap = False
    evidence = "No gap analysis found"
    # Check for strategy docs or gap analysis
    for fp in files_in(variant_path):
        if fp.endswith('.md') and fp != os.path.join(variant_path, "run_summary.md"):
            with open(fp, errors='ignore') as f:
                content = f.read()
                if re.search(r"(gap|coverage|missing|audit)", content, re.IGNORECASE):
                    has_gap = True
                    evidence = f"Gap analysis in {fp}"
                    break
    # Also check run_summary
    if not has_gap and re.search(r"(gap|coverage.*gap|missing.*test|audit)", summary, re.IGNORECASE):
        has_gap = True
        evidence = "Gap analysis in run_summary.md"
    results.append({
        "text": "A coverage gap analysis document or report exists identifying what's missing",
        "passed": has_gap,
        "evidence": evidence
    })

    # 2. shell-tests-created
    found_bats, fp = any_file_matches(variant_path, r".*\.bats$")
    if not found_bats:
        found_bats, fp = file_contains(variant_path, r"@test\s+")
    results.append({
        "text": "Bats tests (.bats files) were created for shell scripts",
        "passed": found_bats,
        "evidence": f"Found: {fp}" if found_bats else "No bats tests found"
    })

    # 3. systemd-tests-created
    found_systemd, fp = file_contains(variant_path, r"(systemd|\.service|ExecStart|ExecStop|systemctl)")
    results.append({
        "text": "Tests for systemd service integration exist",
        "passed": found_systemd,
        "evidence": f"Systemd tests in {fp}" if found_systemd else "No systemd tests"
    })

    # 4. multi-lang-runner
    has_runner = False
    evidence = "No multi-lang runner found"
    for fp in files_in(variant_path):
        bn = os.path.basename(fp).lower()
        if bn in ('makefile', 'makefile.test-targets') or 'run_tests' in bn or 'run-tests' in bn:
            has_runner = True
            evidence = f"Found: {fp}"
            break
    results.append({
        "text": "A top-level test runner that runs all language test suites",
        "passed": has_runner,
        "evidence": evidence
    })

    # 5. identifies-existing-gaps
    found_gaps = bool(re.search(
        r"(missing|no.*test|without.*test|gap|uncovered|not.*covered|shell.*script.*test|systemd.*test)",
        summary, re.IGNORECASE
    ))
    results.append({
        "text": "Output explicitly identifies what areas are missing from the existing pytest tests",
        "passed": found_gaps,
        "evidence": "Gap identification found in summary" if found_gaps else "No explicit gap identification"
    })

    # 6. coverage-mode-detected
    found_mode = bool(re.search(r"coverage.gap", summary, re.IGNORECASE))
    results.append({
        "text": "The run correctly identified coverage-gap mode rather than greenfield mode",
        "passed": found_mode,
        "evidence": "coverage-gap mode detected in summary" if found_mode else "Mode not identified as coverage-gap"
    })

    return results


def write_grading(variant_path, expectations):
    """Write grading.json."""
    grading = {
        "expectations": expectations,
        "pass_count": sum(1 for e in expectations if e["passed"]),
        "fail_count": sum(1 for e in expectations if not e["passed"]),
        "total": len(expectations)
    }
    path = os.path.join(variant_path, "grading.json")
    with open(path, 'w') as f:
        json.dump(grading, f, indent=2)
    return grading


# Grade all runs
evals = [
    ("python-greenfield", grade_python_greenfield),
    ("shell-targeted", grade_shell_targeted),
    ("multi-lang-coverage", grade_multi_lang),
]

print("=" * 60)
print("GRADING RESULTS")
print("=" * 60)

for eval_name, grader in evals:
    for variant in ["with_skill", "without_skill"]:
        path = os.path.join(BASE, eval_name, variant, "outputs")
        if not os.path.exists(path):
            print(f"\n{eval_name}/{variant}: MISSING")
            continue

        results = grader(path)
        grading = write_grading(os.path.join(BASE, eval_name, variant), results)

        print(f"\n{eval_name}/{variant}: {grading['pass_count']}/{grading['total']}")
        for exp in results:
            status = "PASS" if exp["passed"] else "FAIL"
            print(f"  [{status}] {exp['text'][:70]}")
            print(f"         {exp['evidence'][:80]}")
