#!/usr/bin/env python3
"""One-shot verbatim verifier for the #158 WF2 spine split (commit 2 gate).

Every non-empty stripped line of the PRE-SPLIT SKILL.md (read from git at the
given base ref) must appear in the post-split corpus: SKILL.md + every
references/*.md + the two extracted shared/blocks files. Inserted stubs and
pointers are additions and never checked; this only proves nothing was dropped
or reworded. Exits 0 clean, 1 with the missing lines listed.

Throwaway by design — delete after #158 merges (it pins a one-time migration).

NOTE: green only at the PURE-SPLIT commit (cbb29a0) against the pre-split base.
The later dead-weight pass + #160 rename intentionally edit ~9 connective lines,
so running this at branch HEAD reports those 9 as "missing" — that is the
sanctioned commit-3 rewrite, not dropped content.
"""
import subprocess
import sys
from pathlib import Path

BASE_REF = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "implement-feature"

original = subprocess.run(
    ["git", "show", f"{BASE_REF}:skills/implement-feature/SKILL.md"],
    capture_output=True, text=True, cwd=ROOT, check=True,
).stdout

corpus_parts = [(SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")]
for ref in sorted((SKILL_DIR / "references").glob("*.md")):
    corpus_parts.append(ref.read_text(encoding="utf-8"))
for blk in ("model-routing-resolve.md", "loop-back-budget.md"):
    corpus_parts.append((ROOT / "shared" / "blocks" / blk).read_text(encoding="utf-8"))
corpus_lines = {ln.strip() for part in corpus_parts for ln in part.splitlines()}

missing = []
for i, ln in enumerate(original.splitlines(), 1):
    s = ln.strip()
    if s and s not in corpus_lines:
        missing.append(f"{i}: {s[:160]}")

if missing:
    print(f"VERBATIM FAIL — {len(missing)} original lines missing from the split corpus:")
    print("\n".join(missing[:40]))
    if len(missing) > 40:
        print(f"... and {len(missing)-40} more")
    sys.exit(1)
print(f"VERBATIM OK — all {sum(1 for l in original.splitlines() if l.strip())} original lines present in the split corpus")
