"""#427 live AC (marked `live`, skipped unless RUN_LIVE=1 — needs a real `claude` CLI + auth):

The executor-routing glue actually routes the SHIP seat through the executor and the provider
reports the routed model. This is the real end-to-end proof of the executor-ON path that the
stubbed unit tests can only simulate (they inject a fake dispatch). Opting a seat into the executor
in production should be gated on this preflight for the seat's provider.
"""
import pathlib
import shutil
import sys

import pytest

from phase_executor import contract, enforce, routing
from phase_executor.engine import _dispatch_real, run_seat
from phase_executor.quota import QuotaCoordinator

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
HOOKS = REPO_ROOT / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))
import executor_routing_lib as er  # noqa: E402

pytestmark = pytest.mark.live

_HAVE_CLAUDE = shutil.which("claude") is not None
_TABLE = REPO_ROOT / "phase_executor" / "src" / "phase_executor" / "routing" / "rawgentic.routing-table.json"


@pytest.mark.skipif(not _HAVE_CLAUDE, reason="needs the `claude` CLI on PATH")
def test_ship_seat_live_reports_sonnet(tmp_path):
    snap = routing.snapshot_from_file(_TABLE)
    quota = QuotaCoordinator(tmp_path / "permits", snap.pool_concurrency())
    audit = enforce.RoutingAuditLog(tmp_path / "runs", "live-run")
    result = er.dispatch_seat(
        seat="ship", prompt="Reply with exactly: OK", run_id="live-run",
        correlation_id="live:ship", author_provider=None, effort=None, timeout=180.0, context=(),
        snapshot=snap, quota=quota, audit=audit, capture_root=str(tmp_path / "runs"),
        routing=routing, enforce=enforce, run_seat=run_seat, dispatch_real=_dispatch_real,
    )
    assert result["ok"] is True, result
    # provider-reported identity canonicalize-matches the ship seat's primary (sonnet-5)
    assert contract.models_match("claude-sonnet-5", result["actual_model"]), result["actual_model"]
    assert result["verified"] is True
