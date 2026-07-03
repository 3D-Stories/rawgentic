# Task 1 Report: `hooks/model_routing_lib.py` — resolution engine

## Status: DONE

## What was built

Two new files, transcribed verbatim from the task brief (`/tmp/claude-1000/wt-mr-plan/.superpowers/sdd/task-1-brief.md`), with no deviation from the brief's code:

- `hooks/model_routing_lib.py` — stdlib-only module exposing:
  - `VALID_MODELS: Final[frozenset[str]]` = `{"opus","sonnet","haiku","fable","inherit"}`
  - `resolve(workspace_path, project_name, role) -> str` — reads `.rawgentic_workspace.json`, finds the project by name, reads its `modelRouting` dict, looks up `role`, falls back to `"inherit"` on any missing/malformed/invalid input (with a stderr warning via `_warn()`), and emits an additional "opus floor" stderr warning only when `role == "review"` and the resolved value is `sonnet` or `haiku` (not for `inherit`/`fable`, not for other roles).
  - `main(argv) -> int` — argparse CLI with a `resolve` subcommand (`--workspace`, `--project`, `--role`), prints the resolved value, always returns 0 (fail-open, no exceptions escape).
- `tests/hooks/test_model_routing.py` — 15 unit tests covering: absent block, configured role, partial config, explicit inherit, invalid model value + warning, malformed (non-dict) `modelRouting` block, missing project, missing workspace file, malformed workspace JSON, opus-floor warnings for sonnet/haiku on `review`, no-warning for `inherit`/`fable` on `review`, no-warning for non-review roles below opus, and two CLI-level tests (good config, bad config) both asserting exit code 0.

No discrepancy found between the brief's code and its tests — traced every test against the implementation by hand before running, all 15 passed on the first GREEN run with zero edits to the brief's code.

## TDD evidence

**RED** (module didn't exist yet):
```
$ ~/.local/bin/pytest tests/hooks/test_model_routing.py -q
ERROR collecting tests/hooks/test_model_routing.py
ModuleNotFoundError: No module named 'model_routing_lib'
1 error in 0.20s
```

**GREEN** (after writing `hooks/model_routing_lib.py`):
```
$ ~/.local/bin/pytest tests/hooks/test_model_routing.py -q
...............                                                          [100%]
15 passed in 0.06s
```

**Full suite regression check** (baseline stated as 1384 passed / 5 warnings):
```
$ ~/.local/bin/pytest tests/ -q
1399 passed, 5 warnings in 58.57s
```
Delta: 1384 → 1399 (+15, exactly the new test count). Same 5 warnings (pre-existing `DeprecationWarning: fork()` in `test_plan_lib.py::TestConsumeLoopback::test_concurrent_consume_does_not_overspend`, x5, unrelated to this change). No regressions.

**Real CLI entry point exercised directly** (not just via in-process `main()` in tests), to confirm the actual invocation path the brief documents works, not just the compiled/imported path:
```
$ python3 hooks/model_routing_lib.py resolve --workspace <ws-with-review:sonnet> --project app --role review
[model_routing] review role resolved to 'sonnet', below recommended opus floor — review quality may drop
sonnet
exit=0

$ python3 hooks/model_routing_lib.py resolve --workspace /nonexistent/ws.json --project app --role review
inherit
exit=0

$ python3 hooks/model_routing_lib.py resolve --workspace <ws> --project app --role analysis   # role absent from block
inherit
exit=0
```
Confirms: correct stdout value, opus-floor warning goes to stderr (not stdout, doesn't corrupt the printed value), and exit code 0 in both the happy path and the fail-open path.

## Files changed
- `hooks/model_routing_lib.py` (new, 91 lines)
- `tests/hooks/test_model_routing.py` (new, 115 lines)

## Self-review
- Confirmed `resolve()` never raises: every I/O/parsing step (`open`, `json.load`, dict traversal) is guarded by `try/except` or `isinstance` checks before use; the only paths are "return {} (+ maybe warn)" or a normal return — no bare exceptions can escape.
- Confirmed the opus floor is strictly review-only: the check is `if role == "review" and value in _BELOW_OPUS`, so `analysis`/`implementation` roles resolving to `sonnet`/`haiku` never warn (test `test_analysis_below_opus_does_not_warn_floor` covers this).
- Confirmed `inherit` and `fable` never trigger the floor warning regardless of role, since `_BELOW_OPUS = {"sonnet", "haiku"}` only.
- Confirmed CLI fail-open: `main()` has no code path that returns non-zero or lets an exception propagate; `resolve()` itself is exception-safe as above.
- Ran `python3 -m py_compile` — compiles clean on the repo's Python 3.12.3.
- Style check against a sibling hooks module (`plan_lib.py`): same `Final[...]` constant pattern, module docstring style, stdlib-only — consistent with existing conventions.
- Git hygiene: staged only the two files this task touches (`hooks/model_routing_lib.py`, `tests/hooks/test_model_routing.py`); left the pre-existing untracked `.superpowers/` planning directory alone (not part of this task's scope).

## Concerns
None. This is a self-contained leaf module with no dependencies on other tasks; the brief's code passed every one of its own tests unmodified, and the full suite shows a clean +15 delta with no regressions.

---

# Follow-up: Critical fix — fail-open on invalid-UTF8 workspace file

## Finding addressed

`hooks/model_routing_lib.py::_load_block()` opened the workspace file with `encoding="utf-8"` then called `json.load(f)`, guarded by `except (OSError, json.JSONDecodeError)`. A workspace file containing invalid UTF-8 bytes raises `UnicodeDecodeError`, which is a `ValueError` subclass — **not** a subclass of `OSError` or `json.JSONDecodeError` — so it was unhandled and escaped `resolve()`, violating the documented fail-open contract ("resolve() never raises", "CLI resolve always exits 0").

## Fix

Widened the except clause from `except (OSError, json.JSONDecodeError)` to `except (OSError, ValueError)` in `_load_block()` (`hooks/model_routing_lib.py:36`). `json.JSONDecodeError` is already a `ValueError` subclass, so this one clause now covers both malformed JSON and invalid-UTF8 decode failures, with the same stderr warning + `{}` fallback behavior as before. Added a comment noting why `ValueError` is caught.

## TDD evidence

**Test added** (`tests/hooks/test_model_routing.py`, `test_invalid_utf8_workspace_file_returns_inherit`):
```python
def test_invalid_utf8_workspace_file_returns_inherit(tmp_path, capsys):
    p = tmp_path / ".rawgentic_workspace.json"
    p.write_bytes(b'{"projects":[]}\xff')
    assert mr.resolve(str(p), "app", "review") == "inherit"
    assert capsys.readouterr().err  # warned
```

**RED** (before the fix, run against the original except clause):
```
$ ~/.local/bin/pytest tests/hooks/test_model_routing.py -q -k invalid_utf8
...
>       assert mr.resolve(str(p), "app", "review") == "inherit"
hooks/model_routing_lib.py:60: in resolve
    block = _load_block(workspace_path, project_name)
hooks/model_routing_lib.py:33: in _load_block
    ws = json.load(f)
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 15: invalid start byte
1 failed, 15 deselected in 0.08s
```
Confirms the exact failure mode in the finding: `UnicodeDecodeError` propagated unhandled out of `resolve()`.

**GREEN** (after widening `except (OSError, json.JSONDecodeError)` to `except (OSError, ValueError)`):
```
$ ~/.local/bin/pytest tests/hooks/test_model_routing.py -q
................                                                         [100%]
16 passed in 0.07s
```

**Full suite regression check**:
```
$ ~/.local/bin/pytest tests/ -q
........................................................................ [ 61%]
... (elided) ...
1400 passed, 5 warnings in 56.18s
```
Baseline (pre-fix, per this task's own prior report) was 1399 passed. Delta: 1399 → 1400 (+1, exactly the one new regression test). Same 5 pre-existing `fork()` DeprecationWarnings, unrelated to this change. No regressions.

## Covering test file

`tests/hooks/test_model_routing.py` (16 tests total after this change).

## Commit

See `git log -1` on branch `feat/model-routing-peer-consult` for the SHA of the commit titled `fix(model-routing): fail-open on invalid-UTF8 workspace file`.
