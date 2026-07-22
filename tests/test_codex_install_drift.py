"""Drift guard for the Codex CLI install command (#580).

The documented install URL went NXDOMAIN once already. The canonical install
command lives in ONE place — CODEX_INSTALL_CMD in hooks/adversarial_review_lib.py
— and every documentation site must carry it verbatim, so the sites cannot
silently diverge again. The dead domain must not reappear on any surface where
the install command is documented.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "hooks"))

import adversarial_review_lib as arl  # noqa: E402

# split so this guard never trips on its own source
DEAD_DOMAIN = "codex.openai" + ".com"

DOC_SITES = [
    "README.md",
    "skills/adversarial-review/SKILL.md",
    "skills/peer-consult/SKILL.md",
    "skills/setup/references/integrations.md",
    "docs/design/workflow-adversarial-review.md",
    "docs/config-reference.md",
]


def test_canonical_install_cmd_pinned():
    assert arl.CODEX_INSTALL_CMD == "npm install -g @openai/codex"


def test_install_msg_uses_canonical_cmd():
    assert arl.CODEX_INSTALL_CMD in arl._INSTALL_MSG


def test_every_doc_site_carries_canonical_cmd():
    missing = [
        site for site in DOC_SITES
        if arl.CODEX_INSTALL_CMD not in (REPO_ROOT / site).read_text(encoding="utf-8")
    ]
    assert not missing, f"doc sites missing the canonical Codex install command: {missing}"


def test_dead_install_domain_absent():
    surfaces = [REPO_ROOT / "README.md"]
    surfaces += sorted((REPO_ROOT / "skills").rglob("*.md"))
    surfaces += sorted((REPO_ROOT / "docs").rglob("*.md"))
    surfaces += sorted((REPO_ROOT / "hooks").rglob("*.py"))
    hits = [
        str(p.relative_to(REPO_ROOT)) for p in surfaces
        if DEAD_DOMAIN in p.read_text(encoding="utf-8")
    ]
    assert not hits, f"dead Codex install domain still referenced at: {hits}"
