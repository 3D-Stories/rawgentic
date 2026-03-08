# Test Generation Summary: gpu-fan-controller

## Project Analyzed

- **Location:** `/home/rocky00717/claude-personal/projects/gpu-fan-controller/`
- **Language:** Python 3 (single-file script: `gpu_fan_controller.py`)
- **Purpose:** GPU-aware fan controller for Intel S2600WTTR + Tesla P40. Monitors NVIDIA GPU temperature via `nvidia-smi` and controls chassis fan speed through Intel BMC via `ipmitool`.

## What Was Generated

### Test Files (88 tests total, all passing)

| File | Tests | What It Covers |
|------|-------|----------------|
| `conftest.py` | — | Shared fixtures: `default_config`, `lan_config`, `config_file`, `fan_ctrl`, `fan_ctrl_lan`. Also adds the project root to `sys.path`. |
| `test_calculate_duty.py` | 24 | Fan curve calculation logic (`calculate_duty()`): GPU temp thresholds, linear ramp behavior, monotonicity, emergency temps, CPU override, edge cases (equal ramp endpoints, equal min/max duty, full 0-100 range). |
| `test_config.py` | 9 | Config loading (`load_config()`): valid files, missing files, empty files, section/key presence, numeric parsing, boolean parsing, LAN-mode config, partial configs. |
| `test_temperature_readers.py` | 20 | Temperature reading functions: `get_gpu_temp()` (normal, whitespace, errors, timeout, missing binary, GPU index) and `get_cpu_temps()` (IPMI thermal margins, direct CPU temps, mixed sensors, sysfs fallback, various error conditions, sensor name variants). |
| `test_fan_control.py` | 24 | `IntelBMCFanControl` class: construction (local/LAN mode), `_run_ipmi()` helper, `set_duty()` with intel_legacy/manual_override/auto methods, duty clamping, `restore_auto()` with fallback, `get_current_duty()`, `get_fan_rpms()` parsing. |
| `test_signal_and_main.py` | 7 | Signal handling (`signal_handler`), `main()` startup with missing config, main loop integration (start/stop cycle, failsafe on GPU failure, IPMI deduplication, exception recovery with auto-restore). |
| `pytest.ini` | — | Pytest configuration. |

### Supporting Files

| File | Purpose |
|------|---------|
| `pytest.ini` | Pytest runner configuration with verbose output and short tracebacks. |

## Key Design Decisions

1. **All subprocess calls are mocked.** The project shells out to `nvidia-smi` and `ipmitool`, which are hardware-dependent. Every test mocks `subprocess.run` to test the parsing and logic without requiring actual hardware.

2. **Filesystem access is mocked for sysfs.** The `get_cpu_temps()` fallback reads `/sys/class/thermal/thermal_zone*/` -- mocked via `unittest.mock.patch` on `Path`.

3. **`conftest.py` uses an absolute path** (`/home/rocky00717/claude-personal/projects/gpu-fan-controller`) for `sys.path` insertion since the test files live in a different directory tree than the source.

4. **Integration tests for `main()`** use `time.sleep` mocking to control loop iterations and verify the full startup-loop-shutdown lifecycle without blocking.

## Bugs Discovered

1. **`get_fan_rpms()` entity locator parsing:** The RPM parser iterates all pipe-delimited fields and takes the first `float(field.split()[0]) > 0`. In standard IPMI output like `"System Fan 1 | 30h | ok | 7.1 | 5400 RPM"`, it grabs `7.1` (the entity locator) instead of `5400` (the actual RPM). This is documented in `test_parse_fan_rpms_with_entity_locator`. Since RPMs are only used for status logging, this is low-severity.

2. **`calculate_duty()` ZeroDivisionError:** When `ramp_start_temp == ramp_end_temp` and the GPU temperature exceeds that value, the linear ramp calculation divides by zero (`(temp - start) / (end - start)` where `end == start`). This is an edge case in misconfigured setups, documented in `test_ramp_start_equals_ramp_end_above_threshold`.

## How to Run

```bash
cd /home/rocky00717/claude-personal/projects/rawgentic/skills/create-tests-workspace/iteration-1/python-greenfield/without_skill/outputs

# If pytest's default tmpdir has ownership issues, use --basetemp:
python3 -m pytest -v --basetemp=./.pytest_tmp
```

**Result:** 88 passed in ~2.2s.
