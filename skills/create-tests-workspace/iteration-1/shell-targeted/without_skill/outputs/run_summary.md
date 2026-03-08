# Test Suite for gpu-fan-controller install.sh

## Overview

Created a comprehensive test suite for the `install.sh` bash installer script in the gpu-fan-controller project. The suite tests all six stages of the installer: root check, dependency verification, IPMI kernel module loading, file installation, IPMI connectivity testing, and GPU access testing.

## Files Generated

| File | Purpose |
|------|---------|
| `test_install.sh` | Main test suite with 37 test cases organized into 9 test groups |
| `test_framework.sh` | Minimal pure-bash test framework providing assertions, test isolation, and reporting |
| `test_helpers.sh` | Sandbox environment builder, mock binary management, and domain-specific assertions |
| `run_summary.md` | This summary |

## Approach

### Sandboxing Strategy

The core challenge is that `install.sh` is designed to run as root and writes to system paths (`/opt/`, `/etc/`, `/etc/systemd/system/`). The test suite solves this with a **path-rewriting sandbox**:

1. **Path substitution**: `sed` rewrites all system paths in `install.sh` to point at temp directories (e.g., `/opt/gpu-fan-controller` becomes `$tmpdir/install`).
2. **Mock binary isolation**: Essential coreutils (`cp`, `mkdir`, `chmod`, `sed`, etc.) are symlinked into a mock `bin/` directory. Mock versions of `python3`, `ipmitool`, `nvidia-smi`, `modprobe`, and `systemctl` are created as shell scripts that log their invocations. The sandbox PATH is set to **only** the mock bin directory, ensuring that removing a mock truly makes the command unfindable.
3. **Root check bypass**: The `EUID` check is rewritten to `if false` so tests can run unprivileged, with a separate test that re-enables the check to verify the rejection logic.
4. **Temp directory isolation**: Each test gets a fresh `mktemp` directory that is cleaned up after the test completes.

### No External Dependencies

The test framework is pure bash -- no bats, shunit2, shellcheck, or other tools required. It provides:
- Assertion functions: `assert_equals`, `assert_contains`, `assert_file_exists`, `assert_file_mode`, `assert_exit_code`, etc.
- Domain-specific assertions: `assert_mock_called`, `assert_mock_called_with`, `assert_mock_not_called`
- Isolated test runner with per-test temp dirs
- Color-coded pass/fail output with summary

## Test Coverage

### 37 tests across 9 groups:

| Group | Count | What is tested |
|-------|-------|----------------|
| Root Check | 2 | Non-root rejection, sudo hint in error message |
| Dependency Checks | 7 | Missing python3/ipmitool/nvidia-smi detection, install hints for ipmitool only, fail-fast behavior via `set -e` |
| IPMI Kernel Modules | 4 | modprobe calls for ipmi_devintf/ipmi_si, persistent config creation, graceful failure handling, missing modules-load.d |
| File Installation | 9 | Python script copy + permissions + content integrity, config install vs. skip-if-exists, .conf.new creation, systemd service install, daemon-reload, directory creation |
| IPMI Connectivity Test | 2 | Fan sensor read attempt, warning on failure (non-fatal) |
| GPU Access Test | 2 | Temperature read attempt, warning on failure (non-fatal) |
| Output and Messaging | 5 | Banner, step numbers [1/6]-[6/6], completion message, next-steps instructions, method alternatives |
| Script Properties | 3 | Shebang, `set -e`, executable permission |
| Integration | 2 | Full happy-path end-to-end, idempotent reinstall preserving existing config |

## Results

```
Total:   37
Passed:  37
Failed:  0
```

All tests pass when run as a non-root user.

## How to Run

```bash
cd $PLUGIN_ROOT/skills/create-tests-workspace/iteration-1/shell-targeted/without_skill/outputs
bash test_install.sh
```
