# Testing Strategy: gpu-fan-controller install.sh

**Date:** 2026-03-07
**Mode:** Greenfield
**Scope:** Targeted (install.sh)
**Framework:** bats-core

## Project Context

- **Project:** gpu-fan-controller
- **Description:** GPU-aware fan controller for Intel S2600WTTR + Tesla P40 via IPMI
- **Target file:** `install.sh` - bash installer that checks dependencies, loads kernel modules, copies files, installs systemd service, and tests IPMI/GPU connectivity

## Test Types

**Unit tests** are the primary focus. The installer script has 6 distinct phases, each testable in isolation:

1. Root privilege check (EUID)
2. Dependency verification (python3, ipmitool, nvidia-smi)
3. IPMI kernel module loading and persistent configuration
4. File installation (script + config, with existing config preservation)
5. Systemd service installation
6. Hardware connectivity tests (IPMI fans, GPU temperature)

## Framework: bats-core

**Why bats-core:**
- Standard testing framework for bash scripts
- Supports `@test` blocks with descriptive names
- `run` command captures stdout/stderr and exit codes
- Built-in setup/teardown lifecycle hooks
- bats-assert and bats-support libraries for richer assertions

**No integration/e2e tests needed:** The script interacts with system hardware (IPMI, GPU) and privileged operations (modprobe, systemctl). All external commands will be mocked.

## Mocking Strategy

All external commands are mocked via PATH manipulation:
- Create stub executables in a temp `mocks/` directory
- Prepend `mocks/` to PATH so stubs are found before real commands
- Stubs return configurable exit codes and output

**Commands to mock:**
- `python3`, `ipmitool`, `nvidia-smi` (for dependency checks)
- `modprobe` (kernel module loading)
- `cp`, `mkdir`, `chmod` (file operations - handled via temp directories)
- `systemctl` (systemd operations)
- File system paths are overridden via environment variables or script modification

**Key challenge:** The script uses hardcoded paths (`/opt/gpu-fan-controller`, `/etc/gpu_fan_controller`, `/etc/systemd/system/`). Tests source a modified version of the script with paths redirected to temp directories.

## Test Organization

```
test/
  install.bats          # Main test file for install.sh
  test_helper.bash      # Shared setup, mock creation utilities
```

## Coverage Goals

- All 6 installer phases tested
- Both success and failure paths for each phase
- Config preservation logic (new install vs. existing config)
- Error messages verified for user-facing output
- Exit codes verified for all failure modes

## Key Decisions

1. **Mock approach:** PATH-based stubs over function overrides (more realistic, tests actual command resolution)
2. **Script modification:** Source the script with overridden path variables rather than running it end-to-end (allows testing individual sections)
3. **No coverage tool:** kcov is optional for shell and adds complexity; focus on thorough manual coverage via test case design
4. **Temp directories:** All file operations target `$BATS_TMPDIR` subdirectories to avoid any system side effects
