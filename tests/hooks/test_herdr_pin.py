"""Drift guards for the herdr toolchain pin (#609, epic #667).

`hooks/herdr-pin.json` is the single machine-readable source of truth for which herdr
release this workspace is pinned to. It exists so #390's future workspace-doctor check 8
("herdr binary present AND version == the pinned release") has one place to read, and so
the pin can never silently disagree with the version floor the build seat already
enforces.

The load-bearing test here is `test_pin_version_matches_herdr_version_floor`:
`HERDR_VERSION_FLOOR` in the executor's herdr backend and the pin file were two
records of ONE fact; the executor died in the M0 retreat (#866), so the pin file is
now the single self-authoritative record.

The same file also carries the `integrations` block (#610): what herdr's per-agent hook
installs do to a host. Those guards come in two kinds, and the distinction is deliberate
because #610's review correctly called an earlier framing self-referential:

- **Record-integrity guards** compare the authored record against constants. They cannot
  prove anything about a real install; what they buy is that the record cannot silently
  widen, drift from the runbook that quotes it, or grow a concrete home path in a public
  repo. Their docstrings say so.
- **Fixture-based guards** compare REAL output of `herdr integration install claude`, run
  against a sandboxed `$HOME` seeded with `tests/fixtures/herdr/settings.before.json`. These
  carry the actual AC1/AC2 properties: the install adds exactly one `SessionStart` entry, and
  every other hook owner (mempalace's `Stop`/`PreCompact`, the `PreToolUse` chain) comes out
  value-identical. The committed `install.diff` is asserted to be the real diff of that pair,
  and the committed v7 script fixture is asserted byte-equal to the pinned digest — which is
  possible only because that script is generic (it takes its identity from the environment
  and embeds no host path, so its bytes match on every machine).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS = REPO_ROOT / "hooks"
PIN_PATH = HOOKS / "herdr-pin.json"

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


def test_pin_is_self_authoritative(pin: dict) -> None:
    """M0d (#866): the executor herdr-backend version-floor constant was deleted
    with the executor — this pin file is now the ONE record of the qualified
    herdr version (#609 provenance intact). The pin must carry a parseable
    version so future bumps stay deliberate."""
    pinned = tuple(int(part) for part in pin["pin"]["version"].split("."))
    assert len(pinned) == 3 and all(p >= 0 for p in pinned)


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
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "herdr"
SETTINGS_BEFORE = FIXTURES / "settings.before.json"
SETTINGS_AFTER = FIXTURES / "settings.after.json"
INSTALL_DIFF = FIXTURES / "install.diff"
SCRIPT_FIXTURE = FIXTURES / "herdr-agent-state.v7.sh"

# The exact hook command the installer writes, with the install-time home normalized to
# `<HOME>` in the committed fixture. Asserted by EQUALITY, not suffix: `endswith` would
# accept `evil-command; bash '<HOME>/…/herdr-agent-state.sh' session`, which is precisely
# what a guard named "invokes only the managed script" must reject (#610 review, Medium 4).
EXPECTED_HOOK_COMMAND = "bash '<HOME>/.claude/hooks/herdr-agent-state.sh' session"


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


def test_recorded_footprint_names_only_sessionstart(claude_integration: dict) -> None:
    """RECORD-SHAPE guard only — deliberately labelled as such.

    This compares the authored record against a constant, so it cannot prove anything about
    a real install; the #610 review was right to call the earlier "executable invariant"
    framing self-referential, and the follow-up set-disjointness assert was redundant once
    this equality holds. The actual unperturbed-hooks property is proven against installer
    output in `test_install_adds_only_the_sessionstart_entry` /
    `test_install_leaves_every_other_hook_owner_value_identical` below. What this still buys
    is cheap: a future edit widening the RECORD to claim herdr owns another event fails here.
    """
    events = claude_integration["settings_footprint"]["hook_events"]
    assert events == ["SessionStart"], f"expected exactly ['SessionStart'], got {events!r}"


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
    """The entry runs the herdr-managed script with the `session` action and NOTHING else.

    Asserted by equality against `EXPECTED_HOOK_COMMAND`. A suffix check (the original form)
    accepted an arbitrary prefix — `evil; bash '<HOME>/…' session` would have passed a guard
    whose whole purpose is to reject exactly that (#610 review, Medium 4).
    """
    entry = claude_integration["settings_footprint"]["entry"]
    assert entry["type"] == "command"
    assert entry["command"] == EXPECTED_HOOK_COMMAND, (
        f"expected exactly {EXPECTED_HOOK_COMMAND!r}, got {entry['command']!r}"
    )
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


# --------------------------------------------------------------------------------------
# Fixture-based guards (#610 review) — these compare REAL installer output, not the record
# against itself. The fixtures were produced by running the actual `herdr integration
# install claude` against a sandboxed $HOME seeded with `settings.before.json`, then
# normalizing the sandbox path to the literal `<HOME>`. That normalization is the ONLY
# edit; everything else is byte-exact installer output.
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def before_after() -> tuple[dict, dict]:
    return (
        json.loads(SETTINGS_BEFORE.read_text(encoding="utf-8")),
        json.loads(SETTINGS_AFTER.read_text(encoding="utf-8")),
    )


def test_install_adds_only_the_sessionstart_entry(before_after: tuple[dict, dict]) -> None:
    """#610 AC1/AC2, proven against installer output.

    The ONLY semantic change the installer makes is one added `SessionStart` entry. Every
    other top-level key and every other hook event must be untouched.
    """
    before, after = before_after
    assert set(after) == set(before), "installer must not add or drop top-level keys"
    for key in set(before) - {"hooks"}:
        assert after[key] == before[key], f"top-level {key!r} changed"

    assert "SessionStart" not in before["hooks"], "fixture precondition: no herdr entry yet"
    assert set(after["hooks"]) == set(before["hooks"]) | {"SessionStart"}, (
        "the only new hook event may be SessionStart"
    )
    added = after["hooks"]["SessionStart"]
    assert added == [
        {
            "hooks": [
                {"command": EXPECTED_HOOK_COMMAND, "timeout": 10, "type": "command"},
            ],
            "matcher": "*",
        }
    ], f"unexpected SessionStart entry: {added!r}"


def test_install_leaves_every_other_hook_owner_value_identical(
    before_after: tuple[dict, dict],
) -> None:
    """The real AC2: mempalace's and the PreToolUse chain's registrations survive intact.

    Compared by VALUE, because the installer re-serializes the whole file with sorted keys —
    a byte comparison would fail on cosmetics and tell us nothing about semantics. This is
    what the runbook's unperturbed claim rests on.
    """
    before, after = before_after
    for event, entries in before["hooks"].items():
        assert after["hooks"][event] == entries, (
            f"hook event {event!r} was perturbed by the install: "
            f"{before['hooks'][event]!r} -> {after['hooks'][event]!r}"
        )
    # And specifically the owners #610 AC2 names, so the intent survives a fixture edit.
    commands = [
        h["command"]
        for entries in after["hooks"].values()
        for entry in entries
        for h in entry["hooks"]
    ]
    assert "mempalace-hook-wrapper.sh precompact" in commands
    assert "mempalace-hook-wrapper.sh stop" in commands
    assert "rtk hook claude" in commands


def test_committed_install_diff_matches_the_fixtures() -> None:
    """The committed unified diff IS the diff of the committed fixtures.

    Without this, `install.diff` is decorative prose free to drift from the pair it claims to
    describe. Regenerated with the SAME tool and flags the runbook documents:
      diff -u tests/fixtures/herdr/settings.before.json tests/fixtures/herdr/settings.after.json
    (then the two `---`/`+++` header lines are normalized to bare filenames, since the real
    ones carry local paths and mtimes).

    Deliberately shells out rather than using `difflib`: GNU diff and difflib pick different —
    both valid — hunk alignments for the same change, so comparing them tests diff-algorithm
    agreement instead of artifact freshness. Subprocess is also this repo's house style.
    """
    proc = subprocess.run(
        ["diff", "-u", str(SETTINGS_BEFORE), str(SETTINGS_AFTER)],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 1, (
        f"expected diff to report differences (rc 1), got rc {proc.returncode}"
    )
    fresh = proc.stdout.splitlines()[2:]                     # drop path/mtime headers
    committed = INSTALL_DIFF.read_text(encoding="utf-8").splitlines()[2:]
    assert committed == fresh, (
        "install.diff has drifted from the fixtures it documents — regenerate it"
    )


def test_committed_script_fixture_matches_the_pinned_digest(
    claude_integration: dict,
) -> None:
    """Byte-exact, and verifiable in CI — unlike the host digest on its own.

    The v7 script is generic: it reads its pane/socket identity from the environment and
    embeds no host path, so the bytes are identical on every machine. Measured: the fixture's
    sha256 equals the live host's installed script. Committing it turns the recorded digest
    from an unverifiable observation into an assertion CI can actually make.
    """
    digest = hashlib.sha256(SCRIPT_FIXTURE.read_bytes()).hexdigest()
    assert digest == claude_integration["hook_script"]["sha256"], (
        "committed script fixture does not match the digest recorded in herdr-pin.json"
    )
    assert SCRIPT_FIXTURE.stat().st_size == claude_integration["hook_script"]["size"]


def test_committed_script_fixture_carries_no_host_path(claude_integration: dict) -> None:
    """A committed copy of a host file is exactly where a home path leaks into a public repo."""
    text = SCRIPT_FIXTURE.read_text(encoding="utf-8")
    assert "/home/" not in text and "/Users/" not in text, "host path in the script fixture"
    assert "HERDR_INTEGRATION_VERSION=7" in text, "fixture must be the v7 script"


def test_claude_integration_verification_provenance(claude_integration: dict) -> None:
    """An unattributed record is a claim; a dated method pointing at a real file is
    evidence. Also pins the honesty caveat from Step-4 finding #4 — the digest is a
    recorded observation, and the record has to say so."""
    v = claude_integration["verified"]
    assert v["issue"] == 610
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", v["at"])
    assert v["method"].strip(), "record HOW the footprint was measured"
    assert (REPO_ROOT / v["runbook"]).is_file(), f"runbook path {v['runbook']!r} does not exist"
    # The caveat's job is to stop the digest being over-claimed. Since #610's review the digest
    # IS asserted in CI against a committed byte-exact fixture, so the honest residual limit is
    # narrower than before: CI still cannot check it against a LIVE host. Pin that residual —
    # the phrasing may evolve, the disclosure of what is NOT verified may not.
    caveat = claude_integration["hook_script"]["_comment"].lower()
    assert "live host" in caveat, (
        "the digest caveat must still name what CI cannot verify — the digest against a live "
        "host — so a reader never mistakes the fixture assertion for host verification"
    )
    assert "herdr integration status" in caveat, (
        "the caveat must name the operator-side check that DOES cover a live host"
    )
