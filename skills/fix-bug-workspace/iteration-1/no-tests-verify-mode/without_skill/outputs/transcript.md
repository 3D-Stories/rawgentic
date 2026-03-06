# Fix Bug Transcript - Issue #20 (No Skill, No Tests)

## Task
Fix bug: `parse_date function crashes on ISO format dates like 2026-03-06`

## Project
- **Name:** no-tests-app
- **Path:** /tmp/rawgentic-fix-bug-test/no-tests-verify-mode/projects/no-tests-app
- **Type:** scripts (Python 3.12.0)
- **Config:** No testing section in `.rawgentic.json`

## Diagnosis

### Root Cause
The `parse_date` function in `app.py` unconditionally splits the input string on `/` (US date format `MM/DD/YYYY`). When given an ISO format date like `2026-03-06`, the split on `/` produces a single-element list `["2026-03-06"]`, causing an `IndexError` when accessing `parts[1]` and `parts[2]`.

### Original Code
```python
def parse_date(date_str):
    """Parse a date string. Bug: doesn't handle ISO format."""
    parts = date_str.split("/")
    return {"month": parts[0], "day": parts[1], "year": parts[2]}
```

## Fix Applied

Modified `app.py` to detect the date format by checking for `-` in the input string:
- If `-` is present, treat as ISO format (`YYYY-MM-DD`) and map parts accordingly (year, month, day).
- Otherwise, treat as US format (`MM/DD/YYYY`) and map parts as before (month, day, year).

### Fixed Code
```python
def parse_date(date_str):
    """Parse a date string. Supports ISO format (YYYY-MM-DD) and US format (MM/DD/YYYY)."""
    if "-" in date_str:
        # ISO format: YYYY-MM-DD
        parts = date_str.split("-")
        return {"month": parts[1], "day": parts[2], "year": parts[0]}
    else:
        # US format: MM/DD/YYYY
        parts = date_str.split("/")
        return {"month": parts[0], "day": parts[1], "year": parts[2]}
```

## Verification

### Testing Availability
The `.rawgentic.json` config has no `testing` section configured. No test framework or test files exist in the project. There is only a single `app.py` file.

### Manual Reasoning Verification
- **ISO format `2026-03-06`:** `"-"` is in the string, so it splits on `-` producing `["2026", "03", "06"]`. Returns `{"month": "03", "day": "06", "year": "2026"}`. Correct.
- **US format `03/06/2026`:** `"-"` is NOT in the string, so it splits on `/` producing `["03", "06", "2026"]`. Returns `{"month": "03", "day": "06", "year": "2026"}`. Correct. Original behavior preserved.

### Attempted Runtime Verification
Attempted to run a Python verification script but Bash execution was denied. The fix was verified through code analysis and manual reasoning only.

## Files Modified
- `/tmp/rawgentic-fix-bug-test/no-tests-verify-mode/projects/no-tests-app/app.py`

## Summary
The fix adds ISO format date detection to `parse_date` by checking for the `-` separator. This resolves the crash on ISO format dates like `2026-03-06` while preserving backward compatibility with the existing US format (`MM/DD/YYYY`) parsing.
