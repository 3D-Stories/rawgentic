#!/usr/bin/env python3
"""#447 — generate the workflow diagram's executor-seat routing block.

The official diagram (`docs/workflow-diagram.html`) renders, per WF2 station that
maps to an executor seat, that seat's default model + fallback chain + routing-mode
classification. Those values are NOT hand-hardcoded: they are GENERATED from the
#445 routing-table source-of-truth (`executor_routing_lib.resolve_table` /
`table_projection`) joined with a small curated phase->seat manifest (the one input
no machine-readable placement source exists for). The generated JSON lives in a
sentinel-delimited block inside the committed HTML; `check` mode re-derives it and
fails on drift, so a config change that isn't mirrored into the diagram breaks CI.

Pure core (`classify_seat`, `build_seat_dataset`) + a thin `write`/`check` CLI.
The core takes a projection dict (as emitted by `executor_routing_lib show-table
--json`) so it is hermetically testable with a fixture.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Sibling hook: the #445 single seat-table resolution + its projection.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import executor_routing_lib as er  # noqa: E402

GENERATOR_VERSION = 1
START = "/*SEAT-ROUTING-START*/"
END = "/*SEAT-ROUTING-END*/"
_CLASSIFICATIONS = ("executor-wired", "competitive", "bake-off")

# The one curated input: WF2 diagram station id -> (seat, placement, refs).
# Placement vocabulary is from docs/planning/2026-07-17-wf2-wf3-executor-seat-placement.md
# (prose-only, no machine-readable source). Seat reuse across stations is intentional
# (the review seat maps to 4/8a/11). Station ids are the diagram DATA `id` values.
PHASE_SEAT_MAP = (
    ("1", "intake", "Executor", ("#447",)),
    ("2", "analysis", "Agent tool", ("#447",)),
    ("3", "design", "Executor (competitive)", ("#447",)),
    ("4", "review", "Executor (design-critique)", ("#447",)),
    ("5", "plan", "Executor", ("#447",)),
    ("8", "build", "Agent tool (worktree)", ("#447",)),
    ("8a", "review", "Hybrid", ("#447",)),
    ("11", "review", "Hybrid", ("#447",)),
    ("12", "ship", "Session / Agent tool", ("#447",)),
)


def wired_seats() -> frozenset:
    """The authoritative wired-seat set (source-of-truth, never hand-listed)."""
    return er.WIRED_SEATS


def classify_seat(seat: str) -> str:
    """Static routing-mode classification (precedence-ordered).

    build -> bake-off (gate-flagged candidate bake-off), design -> competitive
    (COMPETITIVE_ONLY), else executor-wired for any WIRED_SEATS member. This is the
    diagram's presentation classification, NOT the per-project resolve-seat runtime
    action. An unknown seat fails closed.
    """
    if seat == "build":
        return "bake-off"
    if seat == "design":
        return "competitive"
    if seat in er.WIRED_SEATS:
        return "executor-wired"
    raise ValueError(f"unknown seat {seat!r}: not build/design and not in WIRED_SEATS")


def _seat_note(seat: str, projection: dict) -> str | None:
    if seat == "design":
        return "competitive: gpt-5.6-sol vs claude-opus-4-8 + glm-5.2 judge"
    if seat == "build":
        cands = ", ".join(projection.get("build_bake_off", []))
        return f"gate-flagged bake-off candidates: {cands}"
    return None


def build_seat_dataset(projection: dict, phase_seat_map=PHASE_SEAT_MAP) -> dict:
    """Join the routing projection with the phase->seat manifest into the block.

    Fail-closed: a manifest seat absent from the projection, a duplicate station id,
    an empty primary, or a non-list chain each raise ValueError. Seat reuse across
    distinct station ids is VALID (the review seat maps to several stations).
    """
    seats_by_name = {s["seat"]: s for s in projection.get("seats", [])}
    records: dict[str, dict] = {}
    for entry in phase_seat_map:
        station_id, seat, placement, refs = entry
        if station_id in records:
            raise ValueError(f"duplicate station id {station_id!r} in the manifest")
        row = seats_by_name.get(seat)
        if row is None:
            raise ValueError(
                f"station {station_id!r} maps to seat {seat!r} absent from the routing projection")
        primary = row.get("primary")
        chain = row.get("chain")
        if not primary or not isinstance(primary, str):
            raise ValueError(f"seat {seat!r} has an empty/invalid primary model")
        if not isinstance(chain, list):
            raise ValueError(f"seat {seat!r} chain is not a list")
        classification = classify_seat(seat)
        if classification not in _CLASSIFICATIONS:
            raise ValueError(f"seat {seat!r} produced an unrecognized classification {classification!r}")
        records[station_id] = {
            "stationId": station_id,
            "seat": seat,
            "role": row.get("role"),
            "primary": primary,
            "chain": list(chain),
            "classification": classification,
            "placement": placement,
            "note": _seat_note(seat, projection),
        }
    return {
        "provenance": {
            "config_digest": projection.get("config_digest"),
            "generator_version": GENERATOR_VERSION,
        },
        "records": records,
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_projection(repo_root=None) -> dict:
    """Resolve the routing-table projection (the source-of-truth).

    Uses `resolve_table` against the repo's committed `.rawgentic.json` — which
    resolves to the package-default table (file:null) — so this works in a standalone
    CI checkout with no workspace file present.
    """
    repo_root = Path(repo_root) if repo_root else _repo_root()
    pe = er._import_phase_executor()  # noqa: SLF001 — the sanctioned resolver entry
    rt = er.resolve_table(repo_root, pe.routing)
    return er.table_projection(rt, repo_root)


def _render_block(dataset: dict) -> str:
    return json.dumps(dataset, indent=2, sort_keys=True)


def _split_sentinels(text: str):
    si = text.find(START)
    ei = text.find(END)
    if si < 0 or ei < 0 or ei < si:
        raise ValueError(
            f"sentinels not found (or out of order): {START} .. {END}")
    return si + len(START), ei


def _default_html() -> Path:
    return _repo_root() / "docs" / "workflow-diagram.html"


def do_write(html_path: Path) -> int:
    text = html_path.read_text()
    inner_start, inner_end = _split_sentinels(text)
    dataset = build_seat_dataset(load_projection(), PHASE_SEAT_MAP)
    block = "\n" + _render_block(dataset) + "\n"
    new = text[:inner_start] + block + text[inner_end:]
    if new != text:
        html_path.write_text(new)
    return 0


def do_check(html_path: Path) -> int:
    text = html_path.read_text()
    inner_start, inner_end = _split_sentinels(text)
    committed_raw = text[inner_start:inner_end].strip()
    try:
        committed = json.loads(committed_raw)
    except json.JSONDecodeError as e:
        print(f"seat-routing block is not valid JSON: {e}", file=sys.stderr)
        return 1
    expected = build_seat_dataset(load_projection(), PHASE_SEAT_MAP)
    if committed != expected:
        print("seat-routing block is stale vs the routing-table source-of-truth; "
              "run: python3 hooks/diagram_seat_data.py write", file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="generate/verify the diagram executor-seat block")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("write", "check"):
        p = sub.add_parser(name)
        p.add_argument("--html", default=None, help="path to the diagram HTML (default docs/workflow-diagram.html)")
    args = ap.parse_args(argv)
    html_path = Path(args.html) if args.html else _default_html()
    try:
        return do_write(html_path) if args.cmd == "write" else do_check(html_path)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
