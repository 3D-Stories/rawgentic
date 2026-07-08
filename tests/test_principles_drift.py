"""#270 (review 5a): principles single-sourcing drift guards.

Canonical one-line P-statements live in the README P-table. docs/principles.md
is a quarantined historical planning artifact whose top-of-file STATUS table
must mirror the README rows (ID + Name), so the two surfaces cannot diverge
silently. C19: the unwired formatter tool names must stay out of the README
P2 row (the repo's real style gate is pylint E-only).
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
PRINCIPLES = REPO_ROOT / "docs" / "principles.md"

# One canonical banner sentence, anchored per drift-guard convention
BANNER_SENTENCE = (
    "This is a quarantined historical planning artifact; the canonical "
    "one-line principle statements live in the README P-table, and the "
    "STATUS table below is the authoritative record of what is actually "
    "enforced today."
)


def _table_rows(text, id_prefix="P"):
    """Parse markdown-table rows whose first cell is P<number>."""
    rows = {}
    for line in text.splitlines():
        m = re.match(r"\|\s*(P\d+)\s*\|\s*([^|]+?)\s*\|", line)
        if m:
            rows[m.group(1)] = m.group(2).strip()
    return rows


def _readme_p_section():
    text = README.read_text(encoding="utf-8")
    start = text.index("### 15 Principles (P1-P15)")
    end = text.index("### ", start + 10)
    return text[start:end]


def test_readme_and_status_table_rows_match():
    readme_rows = _table_rows(_readme_p_section())
    principles_text = PRINCIPLES.read_text(encoding="utf-8")
    status_rows = _table_rows(principles_text[:6000])  # status table is at top
    assert len(readme_rows) == 15, f"README P-table has {len(readme_rows)} rows"
    assert readme_rows == status_rows, (
        "README P-table and principles.md STATUS table diverged:\n"
        f"README only: { {k: v for k, v in readme_rows.items() if status_rows.get(k) != v} }\n"
        f"status only: { {k: v for k, v in status_rows.items() if readme_rows.get(k) != v} }"
    )


def test_principles_carries_quarantine_banner():
    text = PRINCIPLES.read_text(encoding="utf-8")
    # blockquote markers and wrapping are rendering detail — normalize both away
    normalized = " ".join(
        w for w in text.split() if w != ">"
    )
    assert BANNER_SENTENCE in normalized, (
        "docs/principles.md must carry the canonical quarantine banner sentence"
    )


def test_readme_p2_has_no_unwired_tool_names():
    """C19: neither Prettier nor Black is wired anywhere; the real style gate
    is pylint E-only. The README P2 row must not name them as if enforced."""
    section = _readme_p_section()
    assert "Black for Python" not in section
    assert "Prettier" not in section


def test_status_table_marks_only_p15_code_enforced():
    """The review's core finding: only P15's mechanism is enforced in code
    (plan_lib). The STATUS table must not claim code enforcement elsewhere."""
    text = PRINCIPLES.read_text(encoding="utf-8")[:6000]
    rows = [line for line in text.splitlines()
            if re.match(r"\|\s*P\d+\s*\|", line)]
    assert len(rows) == 15, f"STATUS table has {len(rows)} rows"
    for line in rows:
        pid = re.match(r"\|\s*(P\d+)\s*\|", line).group(1)
        if "enforced (code" in line:
            assert pid == "P15", (
                f"{pid} claims code enforcement; only P15 is code-enforced"
            )
    assert any("enforced (code" in line for line in rows), (
        "P15 must be marked enforced (code)"
    )
