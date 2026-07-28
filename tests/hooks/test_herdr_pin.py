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
        assert _SHA256_RE.match(digest), f"{platform}: {digest!r} is not 64 lowercase hex chars"
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
