"""Drift guards for the herdr toolchain pin (#609, epic #667).

`hooks/herdr-pin.json` is the single machine-readable source of truth for which herdr
release this workspace is pinned to. It exists so #390's future workspace-doctor check 8
("herdr binary present AND version == the pinned release") has one place to read, and so
the pin can never silently disagree with the version floor the build seat already
enforces.

The load-bearing test here is `test_pin_version_matches_herdr_version_floor`:
`HERDR_VERSION_FLOOR` in `phase_executor/src/phase_executor/herdr_backend.py` and the pin
file are two records of ONE fact. Mirrored constants drift silently, so they are asserted
equal — neither can move without the other.

The same file also carries the `integrations` block (#610): what herdr's per-agent hook
installs do to a host. Those guards are **record-integrity** guards, not behavioral ones —
CI has no `~/.claude/`, so nothing here can verify the recorded digest against a real host.
What they do enforce is that the record cannot silently widen: the claimed hook footprint
must stay disjoint from the events other tools own, the digest must stay in sync with the
runbook that quotes it, and the recorded command must not leak a concrete home path into
this public repo.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS = REPO_ROOT / "hooks"
PIN_PATH = HOOKS / "herdr-pin.json"

if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))
import executor_routing_lib as er  # noqa: E402

er._ensure_pe_importable()  # put phase_executor/src on sys.path for this test module
# phase_executor resolves at runtime via _ensure_pe_importable; pylint (astroid) can't see it from
# tests/hooks/ (unlike tests/phase_executor/), so the static no-name-in-module here is a false
# positive. Scoped disable, not a blanket one.
# pylint: disable=no-name-in-module
from phase_executor.herdr_backend import HERDR_VERSION_FLOOR  # noqa: E402

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@pytest.fixture(scope="module")
def pin() -> dict:
    return json.loads(PIN_PATH.read_text(encoding="utf-8"))


def test_pin_file_exists_and_parses(pin: dict) -> None:
    assert pin["version"] == 1
    assert isinstance(pin["pin"], dict)


def test_pin_carries_the_upstream_identity(pin: dict) -> None:
    """A pin without its upstream coordinates cannot be re-verified by anyone else."""
    p = pin["pin"]
    assert p["repo"] == "ogulcancelik/herdr"
    assert p["version"] == "0.7.5"
    assert p["tag"] == f"v{p['version']}", "tag must be the v-prefixed version"
    assert p["prerelease"] is False, "never pin a prerelease build"
    assert p["repo"] in p["release_url"]
    assert p["tag"] in p["release_url"]


def test_pin_version_matches_herdr_version_floor(pin: dict) -> None:
    """The pin and the build seat's enforced floor are two records of one fact (#609).

    If this fails, one of them was bumped alone — reconcile, do not relax the assert.
    """
    pinned = tuple(int(part) for part in pin["pin"]["version"].split("."))
    assert pinned == HERDR_VERSION_FLOOR, (
        f"herdr-pin.json pins {pinned} but phase_executor.herdr_backend."
        f"HERDR_VERSION_FLOOR is {HERDR_VERSION_FLOOR} — bump both or neither"
    )


def test_every_asset_digest_is_a_bare_lowercase_sha256(pin: dict) -> None:
    """A leaked `sha256:` prefix would break every string comparison downstream.

    The GitHub API returns `digest: "sha256:<hex>"`; this field stores the BARE hex so
    consumers can compare it against `sha256sum` output without re-parsing.
    """
    assets = pin["pin"]["assets"]
    assert assets, "pin must carry at least one platform asset"
    for platform, asset in assets.items():
        digest = asset["sha256"]
        assert not digest.startswith("sha256:"), (
            f"{platform}: store the bare hex digest, not the API's prefixed form"
        )
        # fullmatch, NOT match: Python's `$` also matches immediately before a trailing
        # newline, so `match()` accepted "a"*64 + "\n" (65 chars) with span (0, 64) —
        # exactly the wrong-length value this guard exists to reject (#609 Step 11, Low).
        assert _SHA256_RE.fullmatch(digest), f"{platform}: {digest!r} is not 64 lowercase hex chars"
        assert isinstance(asset["size"], int) and asset["size"] > 0
        assert asset["name"] and asset["url"].endswith(asset["name"])


def test_linux_x86_64_asset_is_pinned(pin: dict) -> None:
    """This workspace's own platform must be pinned, or the pin cannot gate our install."""
    asset = pin["pin"]["assets"]["linux-x86_64"]
    assert asset["name"] == "herdr-linux-x86_64"


def test_verification_provenance_is_recorded(pin: dict) -> None:
    """An unattributed pin is a claim; a dated method is evidence."""
    v = pin["pin"]["verified"]
    assert v["issue"] == 609
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", v["at"])
    assert v["method"].strip(), "record HOW the digest was verified"


def test_api_schema_pin_recorded_for_issue_390(pin: dict) -> None:
    """#390 check 10 asserts the api-schema digest; #609 only records it."""
    s = pin["pin"]["api_schema"]
    assert isinstance(s["protocol"], int) and s["protocol"] > 0
    assert isinstance(s["schema_version"], int) and s["schema_version"] > 0


# --------------------------------------------------------------------------------------
# integrations block (#610) — herdr's per-agent hook installs
# --------------------------------------------------------------------------------------

RUNBOOK_PATH = REPO_ROOT / "docs" / "runbooks" / "herdr.md"

# Hook events owned by tools OTHER than herdr on this workspace's hosts: mempalace
# (Stop, PreCompact), the question-visibility guard / tmux-kill guard / rtk (PreToolUse).
# The remainder are Claude Code events herdr's claude integration simply does not register.
# AC2 of #610 is "wal-guard / mempalace hooks verified unperturbed" — expressed here as an
# invariant on the record rather than a sentence in a doc, so a future edit that claims
# herdr owns one of these fails the suite instead of quietly shipping.
NON_HERDR_HOOK_EVENTS = frozenset({
    "Stop",
    "PreCompact",
    "PreToolUse",
    "PostToolUse",
    "SubagentStop",
    "Notification",
    "UserPromptSubmit",
    "SessionEnd",
})


@pytest.fixture(scope="module")
def claude_integration(pin: dict) -> dict:
    return pin["integrations"]["claude"]


def test_integrations_is_a_top_level_sibling_of_pin(pin: dict) -> None:
    """`pin` is one binary release; an integration is host hook state on its own version
    line. Nesting the latter under the former would imply they move together (#610)."""
    assert isinstance(pin["integrations"], dict)
    assert "claude" in pin["integrations"]
    assert "integrations" not in pin["pin"], (
        "integrations must not be nested under the release pin — they version separately"
    )


def test_claude_integration_records_its_version_and_commands(claude_integration: dict) -> None:
    """Without the exact commands, the record cannot be re-verified by anyone else."""
    ci = claude_integration
    assert isinstance(ci["integration_version"], int) and ci["integration_version"] > 0
    assert ci["status_command"] == "herdr integration status"
    assert ci["install_command"] == "herdr integration install claude"
    assert ci["uninstall_command"] == "herdr integration uninstall claude"


def test_claude_footprint_is_sessionstart_only_and_disjoint_from_other_owners(
    claude_integration: dict,
) -> None:
    """#610 AC2, as an executable invariant.

    herdr's claude integration registers exactly one hook event. If a future edit widens
    this record to claim herdr also owns `Stop` or `PreCompact`, that is either wrong or a
    real collision with mempalace — either way it must not ship silently.
    """
    events = claude_integration["settings_footprint"]["hook_events"]
    assert events == ["SessionStart"], f"expected exactly ['SessionStart'], got {events!r}"
    assert not (set(events) & NON_HERDR_HOOK_EVENTS), (
        f"recorded footprint collides with events other tools own: "
        f"{sorted(set(events) & NON_HERDR_HOOK_EVENTS)}"
    )


def test_recorded_hook_command_leaks_no_concrete_home_path(claude_integration: dict) -> None:
    """This repo is PUBLIC. The recorded command must use the `<HOME>` placeholder so the
    operator's real home path is never published (#610 Step-4 finding #1)."""
    command = claude_integration["settings_footprint"]["entry"]["command"]
    assert "<HOME>" in command, "record the portable placeholder, not a real home path"
    assert "/home/" not in command and "/Users/" not in command, (
        f"concrete home path leaked into the pin: {command!r}"
    )


def test_recorded_hook_command_invokes_only_the_managed_script(
    claude_integration: dict,
) -> None:
    """The entry runs the herdr-managed script with the `session` action and nothing else —
    guards against an edit that widens what herdr is documented to execute."""
    entry = claude_integration["settings_footprint"]["entry"]
    assert entry["type"] == "command"
    assert entry["command"].endswith("herdr-agent-state.sh' session")
    assert isinstance(entry["timeout"], int) and entry["timeout"] > 0
    assert entry["matcher"] == "*"


def test_claude_hook_script_digest_is_a_bare_lowercase_sha256(
    claude_integration: dict,
) -> None:
    """Same contract as the release assets: bare hex, comparable against `sha256sum`.

    `fullmatch`, not `match` — Python's `$` also matches before a trailing newline, the
    exact 65-char defect #609's Step 11 caught in the asset guard.
    """
    script = claude_integration["hook_script"]
    digest = script["sha256"]
    assert not digest.startswith("sha256:"), "store the bare hex digest"
    assert _SHA256_RE.fullmatch(digest), f"{digest!r} is not 64 lowercase hex chars"
    assert isinstance(script["size"], int) and script["size"] > 0
    assert script["herdr_managed"] is True


def test_claude_integration_digest_matches_the_runbook(claude_integration: dict) -> None:
    """The cross-file drift guard (#610 Step-4 finding #5).

    The pin and the runbook are two records of one digest. The other integration guards are
    self-referential by nature; this one is not — it fails if either file is updated alone.
    """
    assert RUNBOOK_PATH.is_file(), f"runbook missing at {RUNBOOK_PATH}"
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    digest = claude_integration["hook_script"]["sha256"]
    assert digest in runbook, (
        f"runbook does not quote the pinned hook-script digest {digest[:12]}… — "
        f"pin and runbook drifted; reconcile both, do not relax this assert"
    )


def test_claude_integration_records_the_measured_install_behaviours(
    claude_integration: dict,
) -> None:
    """The three behaviours that cost real time to discover, so nobody re-derives them."""
    idem = claude_integration["idempotency"]
    assert idem["repeat_install_is_noop"] is True
    assert idem["appends_on_path_mismatch"] is True
    assert idem["dedupes_on"].strip()
    fp = claude_integration["settings_footprint"]
    assert fp["reformats_whole_file"] is True
    assert fp["drops_trailing_newline"] is True


def test_claude_integration_records_the_uninstall_path(claude_integration: dict) -> None:
    """#610 AC3 — uninstall documented, and documented as *measured*."""
    un = claude_integration["uninstall"]
    assert un["removes_hook_script"] is True
    assert un["removes_only_own_entry"] is True
    assert un["removes_event_key_when_sole_entry"] is True


def test_claude_integration_verification_provenance(claude_integration: dict) -> None:
    """An unattributed record is a claim; a dated method pointing at a real file is
    evidence. Also pins the honesty caveat from Step-4 finding #4 — the digest is a
    recorded observation, and the record has to say so."""
    v = claude_integration["verified"]
    assert v["issue"] == 610
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", v["at"])
    assert v["method"].strip(), "record HOW the footprint was measured"
    assert (REPO_ROOT / v["runbook"]).is_file(), f"runbook path {v['runbook']!r} does not exist"
    caveat = claude_integration["hook_script"]["_comment"].lower()
    assert "recorded observation" in caveat, (
        "the digest caveat must state it is a recorded observation, not a CI-enforced "
        "invariant — CI has no host home directory to check it against"
    )
