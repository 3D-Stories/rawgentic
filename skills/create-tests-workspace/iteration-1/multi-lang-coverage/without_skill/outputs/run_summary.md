# Test Coverage Review - gpu-fan-controller

## Project Analyzed

`$TARGET_PROJECT_ROOT/`

Components:
- `gpu_fan_controller.py` -- Python daemon: reads GPU/CPU temps, calculates fan duty via a linear ramp, sends IPMI raw commands to the Intel BMC
- `install.sh` -- Bash installer: checks root, verifies deps, copies files, loads IPMI kernel modules, installs systemd service
- `gpu-fan-controller.service` -- systemd unit: Type=simple, Restart=on-failure, ExecStopPost safety fallback, hardening directives
- `gpu_fan_controller.conf` -- INI config: temperature thresholds, fan duty range, IPMI connection settings

## Pre-existing Test Coverage

The project had **no tests**. Per instructions, basic pytest tests were created as the "existing" baseline.

## What Was Generated

### 1. existing_tests/ (baseline pytest -- 14 tests)

| File | Tests | What it covers |
|------|-------|----------------|
| `conftest.py` | -- | Shared fixtures: `sample_config`, `sample_config_lan` |
| `test_fan_curve.py` | 10 | `calculate_duty()`: below idle, idle boundary, ramp start, midway ramp, ramp end, emergency, above emergency, GPU=None failsafe, CPU override, CPU below threshold |
| `test_config.py` | 4 | `load_config()` from file and missing file; `IntelBMCFanControl` init in local and LAN modes |

### 2. tests_shell/ (BATS -- 19 tests across 2 files)

| File | Tests | What it covers |
|------|-------|----------------|
| `test_install.bats` | 10 | Root-privilege check, missing dependency detection, all-deps-present check, bash syntax validation (`bash -n`), `set -e` usage, systemd unit file reference, `daemon-reload` call, config preservation logic, IPMI module loading, module auto-load config |
| `test_install_sandbox.bats` | 9 | Full sandboxed install in a temp directory with patched paths: verifies controller script is copied, config is copied, service file is copied, script is marked executable, existing config is not overwritten, `.conf.new` is created for existing configs, IPMI module-load config content, exit code 0, completion banner output |

Shell tests use stub binaries for `python3`, `ipmitool`, `nvidia-smi`, `systemctl`, and `modprobe` so they run without real hardware.

Requires: [BATS](https://github.com/bats-core/bats-core) (`sudo apt install bats`)

### 3. tests_systemd/ (pytest -- 43 tests across 4 files)

| File | Tests | What it covers |
|------|-------|----------------|
| `conftest.py` | -- | Shared fixtures |
| `test_service_unit.py` | 16 | Parses the `.service` file as INI and validates: Description, After ordering (network + nvidia-persistenced), Wants dependency, Type=simple, ExecStart references python3/controller/config, ExecStopPost restores auto-fan via correct IPMI raw bytes, Restart=on-failure, RestartSec range, ProtectHome/ProtectSystem hardening, ReadWritePaths, WantedBy=multi-user.target, no hardcoded passwords, absolute paths in all Exec directives |
| `test_config_validation.py` | 15 | Validates shipped `.conf`: file exists and is parseable, all 5 sections present, poll_interval positive and >= 1s, failsafe_duty 50-100%, valid log level, gpu_index >= 0, temperature thresholds properly ordered, idle temp 20-60C, emergency below Tjmax, duty ranges 0-100 and min <= max, CPU monitor is bool, CPU max temp 50-100C, IPMI mode/method valid values, no plaintext password in defaults |
| `test_signal_handling.py` | 16 | Signal handler sets RUNNING=False for SIGTERM/SIGINT, handlers are registered, `restore_auto()` sends correct 0x30/0x8C/0x00 bytes, clears manual-mode flag, tries fallback on failure, `set_duty()` clamps 0-100, `get_gpu_temp()` returns int on success / None on failure/timeout/missing binary, `get_cpu_temps()` parses thermal margin and direct readings, returns empty on failure |
| `test_service_lifecycle.py` | 9 | **Integration tests** (marked `@pytest.mark.integration`, skipped by default): start/stop/restart via systemctl, journal log assertions for BMC restore message, SIGKILL auto-restart, SIGTERM graceful shutdown, startup banner in journal, enable/disable symlink management |

## Test Execution Results

**67 of 76 pytest tests executed and passed** (9 integration tests skipped -- they require root + installed service + real hardware).

```
67 passed in 0.12s
```

The 19 BATS shell tests are syntactically valid but were not executed because `bats` is not installed on this machine.

## Coverage Gaps Filled

| Area | Before | After |
|------|--------|-------|
| Python fan-curve logic | None | 10 unit tests |
| Python config/IPMI init | None | 4 unit tests |
| Python signal handling + subprocess mocks | None | 16 unit tests |
| Shell script (install.sh) | None | 19 BATS tests |
| Systemd unit file structure | None | 16 validation tests |
| Shipped config sanity | None | 15 validation tests |
| Systemd service lifecycle (integration) | None | 9 integration tests |
| **Total** | **0** | **76 tests + 13 test classes** |

## Remaining Gaps / Recommendations

1. **BATS runtime**: Install `bats` to run the shell tests (`sudo apt install bats`).
2. **Integration test execution**: Run `pytest -m integration --run-integration` on the actual server with the service installed and real IPMI/NVIDIA hardware.
3. **get_cpu_temps() sysfs path**: The sysfs fallback (Method 2 in `get_cpu_temps()`) is only lightly tested via mocks. A test with a fake `/sys/class/thermal` tree would improve confidence.
4. **LAN-mode IPMI**: No end-to-end test covers the LAN path (`-I lanplus -H ... -U ... -P ...`). A mock-based test could validate the full command construction.
5. **Log file creation**: `main()` creates log directories with `os.makedirs`; this path is untested.
6. **Concurrent failures escalation**: The `consecutive_failures` ramp in the main loop is only exercisable through a full integration run.
