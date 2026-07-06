"""Opt-in operating-instructions charter installer (#113).

Ships an autonomy-safe "operating charter" and attaches it to a chosen `CLAUDE.md`
via a one-line `@import`. Design + owner-fork rationale:
`docs/planning/113-operating-charter-design.md`.

Two safety properties this module owns as *tested code* (the SKILL is a thin
orchestrator that shells out to the `install` CLI — the tested logic here is what
actually runs at install time, not an LLM reimplementation):

1. **Never silent global.** `install(scope="global", ...)` refuses unless
   `confirm_global=True` — so the blast-radius jump to `~/.claude/CLAUDE.md`
   always requires an explicit choice.
2. **Autonomy-safe charter.** `assert_charter_safe` is a *regression tripwire*
   (NOT the primary control — that is: rawgentic authors the charter and it goes
   through PR review). It flags autonomy-gating language so a charter that could
   make a headless run suspend cannot ship unnoticed.

The charter filename is rawgentic-namespaced (`rawgentic-operating-charter.md`)
so it never collides with a user's own `operating-instructions.md` (a real install
uses exactly that name — colliding would make global install a silent no-op).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

CHARTER_FILENAME = "rawgentic-operating-charter.md"
IMPORT_HEADING = "## Operating Instructions"
PROVENANCE_SENTINEL_PREFIX = "<!-- rawgentic-operating-charter"


class GlobalScopeNotConfirmed(Exception):
    """Raised when a global-scope install is attempted without explicit confirmation."""


# --- import line ------------------------------------------------------------

def import_line(filename: str = CHARTER_FILENAME) -> str:
    """The bare Claude Code `@import` line for the charter."""
    return f"@{filename}"


def _import_line_re(filename: str = CHARTER_FILENAME) -> re.Pattern:
    # Line-anchored: the filename appearing in prose/paths must NOT count as imported.
    return re.compile(rf"^@{re.escape(filename)}\s*$", re.MULTILINE)


def has_import(text: str, filename: str = CHARTER_FILENAME) -> bool:
    return bool(_import_line_re(filename).search(text))


def charter_block(filename: str = CHARTER_FILENAME) -> str:
    return f"{IMPORT_HEADING}\n\n{import_line(filename)}\n"


_HEADING_RE = re.compile(r"^##\s+Operating Instructions\s*$", re.MULTILINE)


def _has_heading(text: str) -> bool:
    return bool(_HEADING_RE.search(text))


def inject_import(text: str, filename: str = CHARTER_FILENAME) -> tuple[str, bool]:
    """Return (new_text, changed). Idempotent, no-clobber, newline-safe.

    - Already imported → unchanged, changed=False.
    - Pre-existing `## Operating Instructions` heading → append only the import line
      (no duplicate heading).
    - Missing trailing newline → separated so the addition never fuses to the last line.
    """
    if has_import(text, filename):
        return (text, False)
    addition = f"{import_line(filename)}\n" if _has_heading(text) else charter_block(filename)
    if text == "":
        return (addition, True)
    if text.endswith("\n\n"):
        sep = ""
    elif text.endswith("\n"):
        sep = "\n"
    else:
        sep = "\n\n"
    return (text + sep + addition, True)


# --- gating-language drift guard (regression tripwire, not the primary control) ---

# Autonomy-gating language — a confirmation gate that could make a headless WF2 run
# wrongly suspend. Each entry is (pattern, human label). This is a **curated tripwire,
# not an exhaustive classifier** — it catches a broad set of common gating phrasings so a
# charter that could gate autonomy cannot ship unnoticed, but paraphrase can always evade
# a regex list. The primary control is that rawgentic authors the charter and it goes
# through PR review; this guard is defense-in-depth. Tuned to not trip on legitimate
# quality/verification prose ("... before you fix it", "get the baseline before ...").
_GATING_SPECS: list[tuple[str, str]] = [
    (r"confirm\s+first", "confirm first"),
    (r"before\s+acting\b", "before acting"),
    (r"\bbefore\s+you\s+act\b", "before you act"),
    (r"stop\s+and\s+ask", "stop and ask"),
    (r"stop\s+for\s+a\s+yes", "stop for a yes"),
    (r"pause\s+and\s+check\s+with", "pause and check with"),
    (r"check\s+with\s+(?:the\s+)?user", "check with the user"),
    (r"wait\s+for\s+(?:explicit\s+)?(?:confirmation|approval|the\s+user|a\s+yes|sign-?off|permission)",
     "wait for confirmation/approval"),
    (r"hold\s+persists", "a hold persists"),
    (r"competing\s+hold", "competing hold"),
    (r"only\s+when\s+(?:the\s+)?user\s+asks?", "only when the user asks"),
    (r"commit\s+and\s+push\s+only\s+when", "commit and push only when"),
    (r"proceed\s+without\s+asking", "proceed without asking"),
    (r"without\s+asking\s+first", "without asking first"),
    (r"\bask\s+(?:the\s+user\s+)?before\b", "ask before"),
    (r"(?:seek|get|ask\s+for|obtain|need)\s+(?:the\s+user'?s?\s+)?(?:permission|approval|sign-?off|go-?ahead)",
     "seek permission/approval/sign-off"),
    (r"(?:requires?|needs?)\s+(?:explicit\s+)?(?:user\s+)?(?:permission|approval|sign-?off)",
     "requires approval"),
    (r"without\s+(?:explicit\s+)?(?:user\s+)?(?:approval|permission|sign-?off)",
     "without approval"),
    (r"until\s+(?:the\s+)?user\s+(?:approves|confirms|says|gives)", "until the user approves"),
    (r"do\s+not\s+act\s+until", "do not act until"),
    (r"go-?ahead\s+before", "go-ahead before"),
]
GATING_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(p, re.IGNORECASE), label) for p, label in _GATING_SPECS
]


def find_gating_language(text: str) -> list[str]:
    """Return the labels of any autonomy-gating language families present."""
    return [label for pat, label in GATING_PATTERNS if pat.search(text)]


def assert_charter_safe(text: str) -> None:
    """Fail-closed: raise if the charter contains autonomy-gating language."""
    hits = find_gating_language(text)
    if hits:
        raise ValueError(
            "charter contains autonomy-gating language (a charter must be "
            f"quality/verification/honesty discipline only): {hits}"
        )


def has_provenance_sentinel(text: str) -> bool:
    """True if the first non-empty line marks this as rawgentic's own charter."""
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        return s.startswith(PROVENANCE_SENTINEL_PREFIX)
    return False


# --- bundled asset ----------------------------------------------------------

def bundled_charter_path() -> Path:
    return (
        Path(__file__).resolve().parent.parent
        / "skills" / "install-operating-charter" / "assets" / CHARTER_FILENAME
    )


# --- install ----------------------------------------------------------------

def _target_claude_md(scope: str, project_root: str, home: str | None) -> Path:
    if scope == "project":
        return Path(project_root) / "CLAUDE.md"
    h = Path(home) if home else Path(os.path.expanduser("~"))
    return h / ".claude" / "CLAUDE.md"


def install(
    scope: str,
    project_root: str,
    home: str | None = None,
    confirm_global: bool = False,
    force_upgrade: bool = False,
) -> dict:
    """Install the charter into the chosen scope's CLAUDE.md. Returns a result dict.

    Safety: global scope refuses without ``confirm_global`` (never silent global);
    a foreign same-named charter file is never clobbered; the import injection is
    idempotent.
    """
    if scope not in ("project", "global"):
        raise ValueError(f"unknown scope {scope!r} (expected 'project' or 'global')")
    if scope == "global" and not confirm_global:
        raise GlobalScopeNotConfirmed(
            "global scope writes ~/.claude/CLAUDE.md and requires explicit confirmation "
            "(--confirm-global); this is never silent or default"
        )

    claude_md = _target_claude_md(scope, project_root, home)
    charter_dest = claude_md.parent / CHARTER_FILENAME
    charter_text = bundled_charter_path().read_text(encoding="utf-8")
    assert_charter_safe(charter_text)  # fail-closed before any write

    # Write the charter file (no-clobber; upgrade our own file only on --force-upgrade).
    # `loaded_text` is the content that will ACTUALLY load after this call — the freshly
    # written bundle, or the on-disk file we're keeping.
    if charter_dest.exists():
        existing = charter_dest.read_text(encoding="utf-8")
        if has_provenance_sentinel(existing):
            if force_upgrade:
                charter_dest.write_text(charter_text, encoding="utf-8")
                charter_action, loaded_text = "updated", charter_text
            else:
                charter_action, loaded_text = "kept", existing
        else:
            charter_action, loaded_text = "kept-foreign", existing
    else:
        charter_dest.parent.mkdir(parents=True, exist_ok=True)
        charter_dest.write_text(charter_text, encoding="utf-8")
        charter_action, loaded_text = "created", charter_text

    # Only wire the `@import` if the file that will actually load is trustworthy — never
    # point a CLAUDE.md at a charter this command did not write and validate (the whole
    # feature's guarantee is "the charter that loads is autonomy-safe"). A foreign
    # same-named file is never wired; a kept rawgentic file is re-validated by the tripwire.
    if charter_action == "kept-foreign":
        return {
            "scope": scope, "claude_md": str(claude_md), "charter": str(charter_dest),
            "charter_action": charter_action, "import_action": "skipped-foreign-charter",
            "warning": (
                f"a non-rawgentic file named {CHARTER_FILENAME} already exists at "
                f"{charter_dest}; the @import was NOT wired (rename that file, then re-run)"
            ),
        }
    unsafe = find_gating_language(loaded_text)
    if unsafe:
        return {
            "scope": scope, "claude_md": str(claude_md), "charter": str(charter_dest),
            "charter_action": charter_action, "import_action": "skipped-unsafe-charter",
            "warning": (
                f"on-disk charter at {charter_dest} contains autonomy-gating language "
                f"{unsafe}; the @import was NOT wired (pass --force-upgrade to replace it "
                f"with the current safe bundle)"
            ),
        }

    # Inject the import line (idempotent; creates CLAUDE.md if absent).
    body = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    new_body, changed = inject_import(body)
    if changed:
        claude_md.parent.mkdir(parents=True, exist_ok=True)
        claude_md.write_text(new_body, encoding="utf-8")
        import_action = "added"
    else:
        import_action = "present"

    return {
        "scope": scope,
        "claude_md": str(claude_md),
        "charter": str(charter_dest),
        "charter_action": charter_action,
        "import_action": import_action,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="charter_lib")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ip = sub.add_parser("install", help="install the operating charter into a CLAUDE.md")
    ip.add_argument("--scope", required=True, choices=["project", "global"])
    ip.add_argument("--project-root", required=True)
    ip.add_argument("--home", default=None, help="override home dir (global scope; testing)")
    ip.add_argument("--confirm-global", action="store_true",
                    help="required to write ~/.claude/CLAUDE.md (never silent)")
    ip.add_argument("--force-upgrade", action="store_true",
                    help="overwrite a prior rawgentic-owned charter file")
    args = parser.parse_args(argv)

    if args.cmd == "install":
        try:
            res = install(
                scope=args.scope,
                project_root=args.project_root,
                home=args.home,
                confirm_global=args.confirm_global,
                force_upgrade=args.force_upgrade,
            )
        except GlobalScopeNotConfirmed as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            return 3
        except (ValueError, FileNotFoundError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        print(json.dumps(res, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
