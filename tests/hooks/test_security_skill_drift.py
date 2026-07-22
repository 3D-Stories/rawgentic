"""Drift guards for the security-guard skills vs enforced code / upstream (#579).

Two skills drifted from their sources of truth:
- add-exception's `standard` preset text must equal SECURITY_PRESETS["standard"]
  (hooks/security_guard_lib.py), the code that actually enforces the preset.
- sync-security-patterns must read SECURITY_PATTERNS from the upstream `patterns.py`
  (its real home) and capture `regex` + handle `path_check`, not the old
  `security_reminder_hook.py` (which only imports the list).
"""
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "hooks"))

from security_guard_lib import SECURITY_PRESETS  # noqa: E402

ADD_EXCEPTION = REPO_ROOT / "skills" / "add-exception" / "SKILL.md"
SYNC_SKILL = REPO_ROOT / "skills" / "sync-security-patterns" / "SKILL.md"


def _standard_preset_from_skill(text: str) -> set:
    """Extract the rule tokens from add-exception's `standard` SECURITY preset line:
    ``- `standard` -> `rule_a rule_b ...` `` under the security-guard preset block.
    Scoped past the security-guards heading so it never matches the WAL preset that
    appears earlier in the file with the same `standard` label."""
    heading = "Preset expansion for security guards:"
    idx = text.find(heading)
    assert idx != -1, "security-guards preset heading not found"
    m = re.search(r"`standard`\s*->\s*`([^`]+)`", text[idx:])
    assert m, "add-exception `standard` security preset line not found"
    return set(m.group(1).split())


def test_add_exception_standard_preset_matches_enforced_code():
    """AC3: the skill's `standard` preset text equals the enforced SECURITY_PRESETS set."""
    skill_set = _standard_preset_from_skill(ADD_EXCEPTION.read_text(encoding="utf-8"))
    assert skill_set == SECURITY_PRESETS["standard"], (
        f"add-exception `standard` preset drifted: skill={sorted(skill_set)} "
        f"code={sorted(SECURITY_PRESETS['standard'])}"
    )


def test_add_exception_no_stale_walguard_line_cite():
    """AC4: the fragile wal-guard PATTERN_NAMES line-number cite (69-82) is gone."""
    assert "69-82" not in ADD_EXCEPTION.read_text(encoding="utf-8"), (
        "stale wal-guard line-number cite (69-82) still present"
    )


def test_sync_skill_reads_patterns_py():
    """AC1: extraction source is the upstream patterns.py, not security_reminder_hook.py."""
    text = SYNC_SKILL.read_text(encoding="utf-8")
    assert "hooks/patterns.py" in text, "sync skill must point Step 1 at hooks/patterns.py"
    assert "SECURITY_PATTERNS" in text
    assert "security-guidance/hooks/security_reminder_hook.py" not in text, (
        "sync skill still names security_reminder_hook.py as the extraction source"
    )


def test_sync_skill_captures_regex_and_handles_path_check():
    """AC1: extraction captures `regex` and handles `path_check` (not silently dropped)."""
    text = SYNC_SKILL.read_text(encoding="utf-8")
    assert "regex" in text, "sync skill must instruct capturing the regex field"
    assert "path_check" in text, "sync skill must handle path_check-based rules explicitly"
