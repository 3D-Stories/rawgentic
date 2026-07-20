"""#447 — unit tests for the workflow-diagram executor-seat generator.

`hooks/diagram_seat_data.py` joins the #445 routing-table projection (the
source-of-truth) with a curated phase->seat manifest and emits the diagram's
`DATA.seatRouting` block. These tests pin the pure generator: classification
precedence, verbatim primary/chain passthrough, fail-closed validation, seat
reuse across stations, and write->check idempotence over the committed HTML.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))

import diagram_seat_data as dsd  # noqa: E402


# A fixture projection shaped exactly like `executor_routing_lib show-table --json`.
FIXTURE_PROJECTION = {
    "projection_version": 1,
    "table_source": "package_default",
    "config_digest": "sha256:deadbeef",
    "file": None,
    "seats": [
        {"seat": "intake", "role": None, "primary": "claude-opus-4-8",
         "chain": ["claude-fable-5", "claude-sonnet-5"]},
        {"seat": "analysis", "role": None, "primary": "claude-sonnet-5",
         "chain": ["claude-opus-4-8", "claude-fable-5"]},
        {"seat": "design", "role": None, "primary": "gpt-5.6-sol",
         "chain": ["claude-opus-4-8"]},
        {"seat": "plan", "role": None, "primary": "claude-opus-4-8",
         "chain": ["claude-fable-5", "gpt-5.6-terra"]},
        {"seat": "build", "role": "build", "primary": "claude-sonnet-5",
         "chain": ["claude-opus-4-8", "gpt-5.6-terra"]},
        {"seat": "review", "role": "review", "primary": "claude-fable-5",
         "chain": ["gpt-5.6-sol", "claude-sonnet-5"]},
        {"seat": "ship", "role": None, "primary": "claude-sonnet-5",
         "chain": ["claude-opus-4-8", "claude-fable-5"]},
    ],
    "build_bake_off": ["claude-sonnet-5", "claude-opus-4-8", "gpt-5.6-terra"],
    "build_bake_off_note": "informational",
}


class TestClassifySeat:
    def test_build_is_bakeoff(self):
        assert dsd.classify_seat("build") == "bake-off"

    def test_design_is_competitive(self):
        assert dsd.classify_seat("design") == "competitive"

    def test_wired_seats_are_executor_wired(self):
        for seat in ("intake", "analysis", "plan", "review", "ship"):
            assert dsd.classify_seat(seat) == "executor-wired"

    def test_unknown_seat_fails_closed(self):
        with pytest.raises(ValueError):
            dsd.classify_seat("orchestrator")


class TestBuildSeatDataset:
    def test_records_carry_verbatim_primary_and_chain(self):
        manifest = (("5", "plan", "Executor", ("#447",)),)
        ds = dsd.build_seat_dataset(FIXTURE_PROJECTION, manifest)
        rec = ds["records"]["5"]
        assert rec["seat"] == "plan"
        assert rec["primary"] == "claude-opus-4-8"
        assert rec["chain"] == ["claude-fable-5", "gpt-5.6-terra"]
        assert rec["classification"] == "executor-wired"
        assert rec["placement"] == "Executor"
        assert rec["stationId"] == "5"

    def test_seat_reuse_across_stations_is_valid(self):
        # F2: the review seat maps to 3 stations — must generate cleanly.
        manifest = (
            ("4", "review", "Executor (design-critique)", ("#447",)),
            ("8a", "review", "Hybrid", ("#447",)),
            ("11", "review", "Hybrid", ("#447",)),
        )
        ds = dsd.build_seat_dataset(FIXTURE_PROJECTION, manifest)
        assert set(ds["records"]) == {"4", "8a", "11"}
        assert all(r["seat"] == "review" for r in ds["records"].values())

    def test_design_and_build_get_notes(self):
        manifest = (("3", "design", "Executor (competitive)", ("#447",)),
                    ("8", "build", "Agent tool (worktree)", ("#447",)))
        ds = dsd.build_seat_dataset(FIXTURE_PROJECTION, manifest)
        assert ds["records"]["3"]["note"]  # competitive note present
        assert ds["records"]["8"]["note"]  # bake-off note present

    def test_provenance_carries_digest_and_version(self):
        ds = dsd.build_seat_dataset(FIXTURE_PROJECTION, (("1", "intake", "Executor", ("#447",)),))
        assert ds["provenance"]["config_digest"] == "sha256:deadbeef"
        assert ds["provenance"]["generator_version"] == dsd.GENERATOR_VERSION

    def test_manifest_seat_absent_from_projection_fails_closed(self):
        manifest = (("1", "no-such-seat", "Executor", ("#447",)),)
        with pytest.raises(ValueError):
            dsd.build_seat_dataset(FIXTURE_PROJECTION, manifest)

    def test_duplicate_station_id_fails_closed(self):
        manifest = (("5", "plan", "Executor", ("#447",)),
                    ("5", "intake", "Executor", ("#447",)))
        with pytest.raises(ValueError):
            dsd.build_seat_dataset(FIXTURE_PROJECTION, manifest)

    def test_empty_primary_fails_closed(self):
        proj = json.loads(json.dumps(FIXTURE_PROJECTION))
        proj["seats"][3]["primary"] = ""  # plan
        with pytest.raises(ValueError):
            dsd.build_seat_dataset(proj, (("5", "plan", "Executor", ("#447",)),))

    def test_non_array_chain_fails_closed(self):
        proj = json.loads(json.dumps(FIXTURE_PROJECTION))
        proj["seats"][3]["chain"] = "claude-fable-5"  # plan, not a list
        with pytest.raises(ValueError):
            dsd.build_seat_dataset(proj, (("5", "plan", "Executor", ("#447",)),))


class TestRealManifest:
    def test_real_phase_seat_map_generates_against_live_table(self):
        # The shipped manifest against the real routing projection resolves cleanly.
        proj = dsd.load_projection()
        ds = dsd.build_seat_dataset(proj, dsd.PHASE_SEAT_MAP)
        assert set(ds["records"]) == {sid for sid, *_ in dsd.PHASE_SEAT_MAP}
        # every mapped seat is one of the 7 wired seats
        assert {r["seat"] for r in ds["records"].values()} <= dsd.wired_seats()


class TestCLIWriteCheckIdempotence:
    def test_write_then_check_is_idempotent_and_preserves_outside(self, tmp_path):
        # A minimal HTML carrying the sentinels; write must fill them and
        # leave everything outside byte-for-byte unchanged; check must then pass.
        cli = str(HOOKS / "diagram_seat_data.py")
        head = "<html><body>\nconst DATA = {order:[]};\n"
        block = ("DATA.seatRouting =\n/*SEAT-ROUTING-START*/\n{}\n"
                 "/*SEAT-ROUTING-END*/\n;\n")
        tail = "</body></html>\n"
        html = tmp_path / "d.html"
        html.write_text(head + block + tail)
        w = subprocess.run([sys.executable, cli, "write", "--html", str(html)],
                           capture_output=True, text=True)
        assert w.returncode == 0, w.stderr
        after = html.read_text()
        assert after.startswith(head)
        assert after.endswith(tail)
        # second write is a no-op; check passes
        c = subprocess.run([sys.executable, cli, "check", "--html", str(html)],
                           capture_output=True, text=True)
        assert c.returncode == 0, c.stderr
        # a mutated block is detected by check
        html.write_text(after.replace('"intake"', '"tampered"', 1)
                        if '"intake"' in after else after + "X")
        c2 = subprocess.run([sys.executable, cli, "check", "--html", str(html)],
                            capture_output=True, text=True)
        assert c2.returncode != 0
