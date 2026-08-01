"""#427 — executor_routing_lib: seat resolution, config parse, path derivation, dispatch (stubbed —
no live provider call), CLI contract, guarded import. Asserts the ACTUAL executing model on BOTH
paths (executor -> routed model; inherit -> prior behavior untouched)."""
import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent.parent / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))
import executor_routing_lib as er  # noqa: E402
import model_routing_lib as mr  # noqa: E402

er._ensure_pe_importable()  # put phase_executor/src on sys.path for this test module
# phase_executor resolves at runtime via _ensure_pe_importable; pylint (astroid) can't see it from
# tests/hooks/ (unlike tests/phase_executor/), so the static no-name-in-module here is a false
# positive — the 39 tests below exercise these imports. Scoped disable, not a blanket one.
# pylint: disable=no-name-in-module
from phase_executor import contract, enforce, ledger, routing  # noqa: E402
from phase_executor.adapters import codex_cli  # noqa: E402
from phase_executor.engine import run_seat, PROVIDER_ENGINE as _PROVIDER_ENGINE  # noqa: E402
from phase_executor.quota import QuotaCoordinator, QuotaTimeout  # noqa: E402
from phase_executor.terminal_backend import Liveness  # noqa: E402
# pylint: enable=no-name-in-module
import jsonschema  # noqa: E402
import complexity_gate as cg  # noqa: E402

CLI = str(HOOKS / "executor_routing_lib.py")


# --- fixtures ----------------------------------------------------------------------------------
def _lane(pool, provider="anthropic", transport="native", cred=None):
    return {"provider": provider, "transport": transport, "auth_mode": "subscription_oauth",
            "credential_ref": cred, "pool": pool}


def _snapshot():
    """Synthetic table mirroring the real ship/intake/plan seats + a review seat (to exercise the
    pre-check-denial branch) — self-contained so a shipped-table edit can't wobble these tests."""
    table = {
        "schema_version": "1",
        "pools": {"claude": {"concurrency": 2}, "codex": {"concurrency": 4}, "zhipu": {"concurrency": 2}},
        "seats": {
            "ship": {"primary": {"model": "claude-sonnet-5", "lane": _lane("claude")},
                     "chain": [{"model": "claude-opus-4-8", "lane": _lane("claude")},
                               {"model": "claude-fable-5", "lane": _lane("claude")}]},
            "intake": {"primary": {"model": "claude-opus-4-8", "lane": _lane("claude")},
                       "chain": [{"model": "claude-fable-5", "lane": _lane("claude")},
                                 {"model": "claude-sonnet-5", "lane": _lane("claude")}]},
            "plan": {"primary": {"model": "claude-opus-4-8", "lane": _lane("claude")},
                     "chain": [{"model": "claude-fable-5", "lane": _lane("claude")},
                               {"model": "gpt-5.6-terra", "lane": _lane("codex", provider="openai")}]},
            "review": {"role": "review",
                       "primary": {"model": "claude-fable-5", "lane": _lane("claude")}, "chain": []},
            "build": {"role": "build",  # #464 §E: a build-role seat for the attested-gate dispatch path
                      "primary": {"model": "claude-sonnet-5", "lane": _lane("claude")},
                      "chain": [{"model": "claude-opus-4-8", "lane": _lane("claude")}]},
        },
        "policy": {"enforced_roles": ["review", "build"]},  # #464 §D: table-declared enforced roles
        "forbidden_combinations": [
            {"model_pattern": "haiku", "reason": "never Haiku"},
            {"rule": "cross_model_author", "reason": "reviewer != author"},
        ],
    }
    return routing.RoutingSnapshot.from_table(table)


def _obs(req, status=contract.OK, actual_override="__req__"):
    """A schema-valid Observation. Availability failure -> actual_model None (real-adapter contract);
    OK -> actual == requested unless overridden (to force an identity breach)."""
    if actual_override != "__req__":
        actual = actual_override
    elif status == contract.OK:
        actual = req.requested_model
    else:
        actual = None
    usage = {"input": 5, "output": 7, "cached": 0} if status == contract.OK else None
    return contract.Observation(
        run_id="r", attempt_id="0-x", correlation_id=req.correlation_id, seat=req.seat, engine="claude",
        transport=req.transport, requested_model=req.requested_model, actual_model=actual,
        prompt_hash="sha256:x", context_hashes=[], usage=usage, timing_ms=1, queued_ms=0,
        process={"exit_code": 0 if status != contract.NONZERO_EXIT else 1, "timed_out": status == contract.TIMEOUT},
        parse_status=status, parsed_payload=req.prompt, raw_capture_path=None, fallback_reason=None,
        routing_config_digest="sha256:d",
    )


def _stub(status_by_model=None, actual_by_model=None, *, record=None):
    status_by_model = status_by_model or {}
    actual_by_model = actual_by_model or {}
    def dispatch(engine, req, *, run_id, attempt_id, capture_root, digest, queued_ms, fallback_reason):
        if record is not None:
            record.append((attempt_id, req.requested_model))
        st = status_by_model.get(req.requested_model, contract.OK)
        ao = actual_by_model.get(req.requested_model, "__req__")
        return _obs(req, status=st, actual_override=ao)
    return dispatch


def _dispatch(seat, tmp_path, **kw):
    qc = QuotaCoordinator(tmp_path / "permits", {"claude": 2, "codex": 4, "zhipu": 2})
    audit = kw.pop("audit", None) or enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    return er.dispatch_seat(
        seat=seat, prompt="hi", run_id="run1", correlation_id=kw.pop("cid", "wf2:step5"),
        author_provider=kw.pop("author_provider", None), effort=None, timeout=5.0, context=(),
        snapshot=_snapshot(), quota=qc, audit=audit, capture_root=str(tmp_path / "runs"),
        routing=routing, enforce=enforce, run_seat=run_seat,
        dispatch_real=kw.pop("dispatch_real", _stub()),
    ), audit


def _ws(tmp_path, executor_routing="__none__", project="rawgentic", path=".",
        default_architecture="__none__"):
    entry = {"name": project, "path": path, "modelRouting": {"analysis": "sonnet"}}
    if executor_routing != "__none__":
        entry["executorRouting"] = executor_routing
    top = {"projects": [entry]}
    if default_architecture != "__none__":
        top["defaultArchitecture"] = default_architecture
    p = tmp_path / "ws.json"
    p.write_text(json.dumps(top), encoding="utf-8")
    return str(p)


def _cfg(repo, pointer=None):
    """Write a COMPLETE valid .rawgentic.json into a fake project (#445 P2-G1: derive
    hard-requires repo+project sections, so a pointer-only fixture would itself exit 2)."""
    cfg = {"version": 1,
           "project": {"type": "application"},
           "repo": {"fullName": "owner/fake", "defaultBranch": "main"}}
    if pointer is not None:
        cfg["phaseExecutorTable"] = {"version": 1, "file": pointer}
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".rawgentic.json").write_text(json.dumps(cfg), encoding="utf-8")
    return repo / ".rawgentic.json"


def _gate(*, bakeoff=False):
    """A #429 GateDecision + a matching plan-context (a subset of its input_snapshot). Benign inputs
    -> single outcome; risk_level high -> bake-off outcome (#464 §E)."""
    task = {"risk_level": "high" if bakeoff else "standard"}
    gd = cg.needs_bakeoff(task, {"complexity": "standard"}, {"files": [], "lines": 1, "file_count": 1})
    # Step-11 diff review (REOPENS step6-H1): the context must be the COMPLETE canonical key set —
    # a partial subset silently disables the omitted-field stale checks.
    return gd, {k: gd.input_snapshot[k] for k in sorted(cg.REQUIRED_PLAN_CONTEXT_KEYS)}


def _dispatch_build(tmp_path, gate_decision, plan_context, **kw):
    """Dispatch the build-role seat through the REAL dispatch_seat with a stub provider, threading the
    gate evidence the build path requires (#464 §E)."""
    qc = QuotaCoordinator(tmp_path / "permits", {"claude": 2, "codex": 4, "zhipu": 2})
    audit = enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    res = er.dispatch_seat(
        seat="build", prompt="hi", run_id="run1", correlation_id=kw.pop("cid", "wf2:build"),
        author_provider=None, effort=None, timeout=5.0, context=(),
        snapshot=_snapshot(), quota=qc, audit=audit, capture_root=str(tmp_path / "runs"),
        routing=routing, enforce=enforce, run_seat=run_seat,
        dispatch_real=kw.pop("dispatch_real", _stub()),
        gate_decision=gate_decision, plan_context=plan_context,
    )
    return res, audit


# --- config parse (V3: absent vs invalid) ------------------------------------------------------
def test_parse_absent_is_empty():
    assert er.parse_executor_routing(mr._ABSENT) == {}


@pytest.mark.parametrize("raw", ["oops", ["ship"], 3])
def test_parse_present_non_object_malformed(raw):
    with pytest.raises(er.MalformedConfig):
        er.parse_executor_routing(raw)


def test_parse_bad_version_malformed():
    with pytest.raises(er.MalformedConfig):
        er.parse_executor_routing({"version": 2, "seats": {}})


def test_parse_unknown_seat_malformed():
    with pytest.raises(er.MalformedConfig):
        er.parse_executor_routing({"version": 1, "seats": {"bogus": "executor"}})


def test_parse_invalid_mode_malformed():
    with pytest.raises(er.MalformedConfig):
        er.parse_executor_routing({"version": 1, "seats": {"ship": "on"}})


def test_parse_valid():
    assert er.parse_executor_routing({"version": 1, "seats": {"ship": "executor", "intake": "inherit"}}) \
        == {"ship": "executor", "intake": "inherit"}


# --- resolve-seat action -----------------------------------------------------------------------
def test_resolve_absent_block_is_executor_default(tmp_path):
    # #474 guard (a): no defaultArchitecture, no executorRouting → EXECUTOR is the default
    assert er.resolve_seat_action("ship", _ws(tmp_path), "rawgentic")[0] == "executor"


def test_resolve_seat_not_in_config_is_executor_default(tmp_path):
    # #474: a seat absent from an agreeing executorRouting block still defaults executor
    ws = _ws(tmp_path, {"version": 1, "seats": {"ship": "executor"}})
    assert er.resolve_seat_action("plan", ws, "rawgentic")[0] == "executor"


def test_resolve_executor_mode(tmp_path):
    ws = _ws(tmp_path, {"version": 1, "seats": {"ship": "executor"}})
    assert er.resolve_seat_action("ship", ws, "rawgentic")[0] == "executor"


@pytest.mark.parametrize("seat", ["merge", "ci_triage", "deploy_verify", "step16"])
def test_resolve_driver_only(tmp_path, seat):
    assert er.resolve_seat_action(seat, _ws(tmp_path), "rawgentic")[0] == "driver_only"


def test_resolve_unknown_seat_raises(tmp_path):
    with pytest.raises(er.MalformedConfig):
        er.resolve_seat_action("frobnicate", _ws(tmp_path), "rawgentic")


def test_resolve_present_malformed_raises(tmp_path):
    ws = _ws(tmp_path, "not-an-object")
    with pytest.raises(er.MalformedConfig):
        er.resolve_seat_action("ship", ws, "rawgentic")


# --- path derivation ---------------------------------------------------------------------------
def test_derive_paths_run_id_less_and_repo_local(tmp_path):
    repo = tmp_path / "projects" / "rawgentic"
    p = er.derive_paths(repo, "rawgentic", "run1", {"claude": 2})
    assert p["capture_root"] == str(repo / ".rawgentic" / "runs")     # run_id-LESS (V2)
    assert p["capture_root"].endswith("/runs") and "run1" not in p["capture_root"]
    assert p["permits_dir"] == str(repo / ".rawgentic" / "runtime" / "permits" / p["pool_sig"])


def test_pool_signature_stable_and_discriminating():
    a = er.pool_signature({"claude": 2, "codex": 4})
    assert a == er.pool_signature({"codex": 4, "claude": 2})  # order-independent
    assert a != er.pool_signature({"claude": 3, "codex": 4})  # different pools -> different ns


@pytest.mark.parametrize("bad", ["../evil", "a/b", "", "..", "a\x00b"])
def test_derive_paths_rejects_unsafe(tmp_path, bad):
    with pytest.raises(er.MalformedConfig):
        er.derive_paths(tmp_path, "rawgentic", bad, {"claude": 2})


def test_resolve_repo_root_from_project_path(tmp_path):
    (tmp_path / "projects" / "rawgentic").mkdir(parents=True)
    ws = _ws(tmp_path, path="./projects/rawgentic")
    assert er.resolve_repo_root(ws, "rawgentic") == (tmp_path / "projects" / "rawgentic").resolve()


# --- dispatch (stubbed): ACTUAL executing model, both paths, per-attempt check_pre -------------
@pytest.mark.parametrize("seat,expect", [("ship", "claude-sonnet-5"), ("intake", "claude-opus-4-8"),
                                         ("plan", "claude-opus-4-8")])
def test_dispatch_actual_model_is_routed_primary(tmp_path, seat, expect):
    res, audit = _dispatch(seat, tmp_path)
    assert res["ok"] is True and res["exit"] == 0
    assert res["actual_model"] == expect and res["verified"] is True
    kinds = [r["kind"] for r in audit.records()]
    assert "receipt" in kinds and "observation" in kinds


def test_dispatch_chain_fallback_selects_second_target(tmp_path):
    # intake: primary opus-4-8 availability-fails -> eligible_targets[1] == chain[0] == fable-5
    rec = []
    res, audit = _dispatch("intake", tmp_path,
                           dispatch_real=_stub({"claude-opus-4-8": contract.NONZERO_EXIT}, record=rec))
    assert res["ok"] is True and res["actual_model"] == "claude-fable-5"
    # per-attempt check_pre: a receipt for BOTH attempts, each target_identity == the declared chain entry
    receipts = [r for r in audit.records() if r["kind"] == "receipt"]
    assert len(receipts) == 2
    got = {tuple(r["target_identity"]) for r in receipts}
    want = {enforce.target_identity(t)
            for t in routing.eligible_targets("intake", _snapshot())[:2]}
    assert got == want
    # two real attempts, primary (opus) then fallback (fable), in eligible_targets order
    assert len(rec) == 2
    assert rec[0][1] == "claude-opus-4-8" and rec[1][1] == "claude-fable-5"
    assert rec[0][0].startswith("0-") and rec[1][0].startswith("1-")


def test_dispatch_identity_breach_exit4(tmp_path):
    # OK status but the provider reports a DIFFERENT model than requested -> non-retryable breach
    res, audit = _dispatch("ship", tmp_path,
                           dispatch_real=_stub(actual_by_model={"claude-sonnet-5": "claude-opus-4-8"}))
    assert res["ok"] is False and res["exit"] == er.EXIT_ENFORCEMENT
    assert res["error"]["retryable"] is False
    kinds = [r["kind"] for r in audit.records()]
    assert "receipt" in kinds and "observation" in kinds  # both appended before the breach verdict


def test_dispatch_pre_check_denial_exit4_receipt_only(tmp_path):
    # A review-role seat with no author_provider -> check_pre author_provider_missing -> denial BEFORE
    # any provider call: a denial receipt, NO observation (A6). (ship/intake/plan never hit this.)
    res, audit = _dispatch("review", tmp_path, author_provider=None)
    assert res["ok"] is False and res["exit"] == er.EXIT_ENFORCEMENT
    recs = audit.records()
    assert any(r["kind"] == "receipt" and r["verdict"] == "fail" for r in recs)
    assert not any(r["kind"] == "observation" for r in recs)  # no provider call happened


def test_dispatch_chain_exhausted_availability_exit3(tmp_path):
    # every claude target availability-fails -> chain exhausted -> retryable exit 3
    allfail = {m: contract.NONZERO_EXIT for m in
               ("claude-opus-4-8", "claude-fable-5", "claude-sonnet-5")}
    res, _ = _dispatch("intake", tmp_path, dispatch_real=_stub(allfail))
    assert res["ok"] is False and res["exit"] == er.EXIT_AVAILABILITY
    assert res["error"]["retryable"] is True


def test_dispatch_audit_write_failure_exit5(tmp_path):
    class BustedAudit:
        path = tmp_path / "runs" / "run1" / "routing-audit.jsonl"
        def append_receipt(self, receipt):
            pass
        def append_observation(self, obs, *, receipt):
            raise OSError("disk full")
    res, _ = _dispatch("ship", tmp_path, audit=BustedAudit())
    assert res["ok"] is False and res["exit"] == er.EXIT_INTERNAL
    assert res["error"]["retryable"] is False and res["error"].get("correlation_id") == "wf2:step5"


# --- INHERIT path (#474: now the LEGACY architecture): executor never fires; model_routing unchanged
def test_inherit_path_does_not_dispatch_and_model_routing_unchanged(tmp_path):
    ws = _ws(tmp_path, default_architecture="legacy")  # #474: inherit = declared legacy rollback
    assert er.resolve_seat_action("ship", ws, "rawgentic")[0] == "inherit"
    # no-touch guard: #427 does not edit model_routing role resolution
    assert mr.resolve(ws, "rawgentic", "analysis") == ("sonnet", None)
    # and dispatch is simply never invoked on the inherit path (a spy proves 0 calls)
    calls = []
    er.resolve_seat_action("ship", ws, "rawgentic")  # decision only
    assert calls == []


# --- CLI via subprocess ------------------------------------------------------------------------
def _run_cli(*args):
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True)


def test_cli_resolve_inherit(tmp_path):
    # #474: the inherit action is produced by the declared legacy rollback, not by absence
    r = _run_cli("resolve-seat", "--seat", "ship", "--workspace",
                 _ws(tmp_path, default_architecture="legacy"), "--project", "rawgentic")
    assert r.returncode == 0
    assert json.loads(r.stdout)["action"] == "inherit"


def test_cli_resolve_driver_only(tmp_path):
    r = _run_cli("resolve-seat", "--seat", "merge", "--workspace", _ws(tmp_path), "--project", "rawgentic")
    assert r.returncode == 0 and json.loads(r.stdout)["action"] == "driver_only"


def test_cli_resolve_unknown_seat_exit2(tmp_path):
    r = _run_cli("resolve-seat", "--seat", "nope", "--workspace", _ws(tmp_path), "--project", "rawgentic")
    assert r.returncode == er.EXIT_MALFORMED
    assert json.loads(r.stdout)["ok"] is False


def test_cli_resolve_present_malformed_exit2(tmp_path):
    ws = _ws(tmp_path, "not-an-object")
    r = _run_cli("resolve-seat", "--seat", "ship", "--workspace", ws, "--project", "rawgentic")
    assert r.returncode == er.EXIT_MALFORMED


def test_derived_dirs_ignored_by_tracked_gitignore(tmp_path):
    # V1: the derived capture/permit dirs must be ignored by the PROJECT repo's TRACKED .gitignore
    # (repo-distributed), not merely the checkout-local .git/info/exclude — else a fresh clone / CI
    # could commit captured prompts. Test in a FRESH `git init` repo carrying ONLY the tracked
    # .gitignore (no local info/exclude), which is exactly the clone/CI environment.
    tracked = (Path(__file__).resolve().parent.parent.parent / ".gitignore").read_text(encoding="utf-8")
    fresh = tmp_path / "clone"
    fresh.mkdir()
    subprocess.run(["git", "init", "-q", str(fresh)], check=True)
    (fresh / ".gitignore").write_text(tracked, encoding="utf-8")
    for path in (".rawgentic/runs/run1/routing-audit.jsonl", ".rawgentic/runtime/permits/abc/x"):
        r = subprocess.run(["git", "-C", str(fresh), "check-ignore", path], capture_output=True, text=True)
        assert r.returncode == 0, f"{path} NOT ignored by the tracked .gitignore alone (fresh clone would commit it)"


def test_dispatch_quota_timeout_exit3(tmp_path):
    # Step-8a R1 (High): a saturated pool past the timeout must map to the retryable exit 3, not a
    # bare traceback. QuotaCoordinator with the claude pool limited to 0 + a tiny timeout.
    qc = QuotaCoordinator(tmp_path / "permits", {"claude": 0, "codex": 4, "zhipu": 2})
    audit = enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    res = er.dispatch_seat(
        seat="ship", prompt="hi", run_id="run1", correlation_id="wf2:step12",
        author_provider=None, effort=None, timeout=0.05, context=(),
        snapshot=_snapshot(), quota=qc, audit=audit, capture_root=str(tmp_path / "runs"),
        routing=routing, enforce=enforce, run_seat=run_seat, dispatch_real=_stub(),
        quota_timeout=QuotaTimeout,
    )
    assert res["ok"] is False and res["exit"] == er.EXIT_AVAILABILITY
    assert res["error"]["code"] == "quota_timeout" and res["error"]["retryable"] is True


def test_dispatch_audit_validation_error_exit5(tmp_path):
    # Step-8a R2 F2: a jsonschema.ValidationError from audit append (NOT OSError/ValueError) must
    # still emit a structured exit 5, not a bare traceback.
    class SchemaBustedAudit:
        path = tmp_path / "runs" / "run1" / "routing-audit.jsonl"
        def append_receipt(self, receipt):
            pass
        def append_observation(self, obs, *, receipt):
            raise jsonschema.exceptions.ValidationError("schema-invalid obs")
    res, _ = _dispatch("ship", tmp_path, audit=SchemaBustedAudit())
    assert res["ok"] is False and res["exit"] == er.EXIT_INTERNAL
    assert res["error"].get("correlation_id") == "wf2:step5"


def test_do_dispatch_missing_routing_table_structured(tmp_path, monkeypatch):
    # Step-8a R1 F2, #445-migrated: a config-less project now legitimately resolves the PACKAGE
    # default (AC2) — the surviving hazard is the shipped default itself being missing/unreadable,
    # which must still emit a structured exit, not crash. (A missing DECLARED override is the
    # separate exit-2 cell test_dispatch_path_declared_missing_exit2.)
    repo = tmp_path / "projects" / "empty"
    repo.mkdir(parents=True)
    ws = _ws(tmp_path, {"version": 1, "seats": {"ship": "executor"}}, path="./projects/empty")
    (tmp_path / "p.txt").write_text("hi")
    monkeypatch.setattr(routing, "default_table_path", lambda: tmp_path / "gone-table.json")

    class A:
        seat = "ship"; prompt_file = str(tmp_path / "p.txt"); run_id = "run1"; context_file = None
        correlation_id = None; author_provider = None; effort = None; timeout = 5.0
        workspace = ws; project = "rawgentic"; gate_file = None; plan_file = None
    rc = er._do_dispatch(A())
    # EXACT exit 5 (Step-11 R1): a missing PACKAGE-DEFAULT table is the internal-fault class —
    # exit 2 is reserved for declared-override config errors (the 8a-A1 documented asymmetry).
    assert rc == er.EXIT_INTERNAL


def test_do_resolve_executor_missing_path_exit2(tmp_path):
    # Step-8a R1 F3: resolve_repo_root's MalformedConfig (no project.path) must be a structured
    # exit 2 from _do_resolve's executor branch, not an uncaught escape.
    p = tmp_path / "ws.json"
    p.write_text(json.dumps({"projects": [
        {"name": "rawgentic", "executorRouting": {"version": 1, "seats": {"ship": "executor"}}}
    ]}), encoding="utf-8")  # note: NO "path" field

    class A:
        seat = "ship"; workspace = str(p); project = "rawgentic"
    assert er._do_resolve(A()) == er.EXIT_MALFORMED


def test_resolve_corrupt_workspace_fails_closed(tmp_path):
    # Step-11 D3/A3: a PRESENT-but-corrupt/unreadable workspace must fail CLOSED (MalformedConfig →
    # exit 2) for the enforcement glue, NOT silently collapse to inherit like a clean absence.
    corrupt = tmp_path / "ws.json"
    corrupt.write_text('{"projects": [ TRUNCATED not json', encoding="utf-8")
    with pytest.raises(er.MalformedConfig):
        er.resolve_seat_action("ship", str(corrupt), "rawgentic")


def test_resolve_absent_workspace_is_executor_not_error(tmp_path):
    # #474 AC2: a genuinely-absent workspace is "not configured" → EXECUTOR default (non-error;
    # corrupt stays fail-closed — absent != corrupt).
    missing = str(tmp_path / "does-not-exist.json")
    assert er.resolve_seat_action("ship", missing, "rawgentic")[0] == "executor"


def test_model_routing_stays_fail_open_on_corrupt_workspace(tmp_path):
    # The shared loader must stay fail-OPEN for modelRouting (strict_read default False) — a corrupt
    # workspace resolves to inherit, never raises (executor glue's strict read must not leak into it).
    corrupt = tmp_path / "ws.json"
    corrupt.write_text('{ not json', encoding="utf-8")
    assert mr.resolve(str(corrupt), "rawgentic", "analysis") == ("inherit", None)


@pytest.mark.parametrize("bad_path", ["/etc", "../../../../etc", "/tmp/evil"])
def test_resolve_repo_root_rejects_escaping_path(tmp_path, bad_path):
    # Step-11 D4: an absolute or ../-traversing project.path escapes the workspace dir → refused.
    ws = _ws(tmp_path, path=bad_path)
    with pytest.raises(er.MalformedConfig):
        er.resolve_repo_root(ws, "rawgentic")


def test_dispatch_unknown_seat_in_table_exit2_not_traceback(tmp_path):
    # Step-11 A2-F1: a routing table lacking the wired seat → RoutingError must map to structured
    # exit 2, not escape as a bare traceback.
    table = {
        "schema_version": "1",
        "pools": {"claude": {"concurrency": 2}, "codex": {"concurrency": 4}, "zhipu": {"concurrency": 2}},
        "seats": {"ship": {"primary": {"model": "claude-sonnet-5", "lane": _lane("claude")}, "chain": []}},
        "forbidden_combinations": [],
    }
    snap = routing.RoutingSnapshot.from_table(table)  # note: NO "plan" seat
    qc = QuotaCoordinator(tmp_path / "permits", {"claude": 2, "codex": 4, "zhipu": 2})
    audit = enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    res = er.dispatch_seat(
        seat="plan", prompt="hi", run_id="run1", correlation_id="wf2:step5", author_provider=None,
        effort=None, timeout=5.0, context=(), snapshot=snap, quota=qc, audit=audit,
        capture_root=str(tmp_path / "runs"), routing=routing, enforce=enforce, run_seat=run_seat,
        dispatch_real=_stub(), quota_timeout=QuotaTimeout,
    )
    assert res["ok"] is False and res["exit"] == er.EXIT_MALFORMED
    assert res["error"]["code"] == "routing_table_invalid"


def test_guarded_import_failure_exit5(tmp_path, monkeypatch):
    # A stale tree / missing dep => ImportError inside the subcommand => structured exit 5.
    def boom():
        raise ImportError("no phase_executor")
    monkeypatch.setattr(er, "_import_phase_executor", boom)

    class A:
        seat = "ship"; prompt_file = str(tmp_path / "p.txt"); run_id = "run1"; context_file = None
        correlation_id = None; author_provider = None; effort = None; timeout = 5.0
        workspace = _ws(tmp_path, {"version": 1, "seats": {"ship": "executor"}}); project = "rawgentic"
    (tmp_path / "p.txt").write_text("hi")
    assert er._do_dispatch(A()) == er.EXIT_INTERNAL


# --- #464 §B / #568: WIRED_SEATS vocabulary; design is single-dispatch-refused -------------------
def test_wired_seats_vocabulary():
    # #568 Phase-2 added the read-only `offload` seat (Hermes HTTP gateway).
    assert er.WIRED_SEATS == frozenset(
        {"intake", "analysis", "design", "plan", "build", "review", "ship", "offload"})
    assert er.COMPETITIVE_ONLY == frozenset({"design"})


def test_offload_is_wired_and_single_dispatchable():
    # #568: offload is a normal wired seat (not competitive) — it CAN be single-dispatched.
    assert er.classify_seat("offload") == "wired"
    assert "offload" not in er.COMPETITIVE_ONLY
    assert er.parse_executor_routing({"version": 1, "seats": {"offload": "executor"}}) \
        == {"offload": "executor"}


def test_offload_seat_resolves_in_default_table():
    # The default routing table carries the offload seat on the hermes/nousresearch lane.
    import pathlib
    from phase_executor import routing as _routing  # pylint: disable=no-name-in-module
    rt = er.resolve_table(pathlib.Path("."), _routing)
    seat = rt.snapshot.seat("offload")
    assert seat["primary"]["model"] == "hermes-agent"
    assert seat["primary"]["lane"]["provider"] == "nousresearch"
    assert seat["manifest"]["tool_grants"] == ["read"]
    assert "nousresearch" in seat["manifest"]["confinement"]  # F1 confinement coverage


def test_parse_design_opt_in_rejected():
    # design is in the vocabulary but competitive-only — opting it into single-dispatch is refused.
    with pytest.raises(er.MalformedConfig):
        er.parse_executor_routing({"version": 1, "seats": {"design": "executor"}})


def test_parse_analysis_opt_in_accepted():
    # analysis is a newly-wired non-competitive seat — it CAN be single-dispatched.
    assert er.parse_executor_routing({"version": 1, "seats": {"analysis": "executor"}}) \
        == {"analysis": "executor"}


def test_classify_design_is_wired_vocabulary():
    # classify keeps returning "wired" (vocabulary); the refusal lives on the resolve/dispatch path.
    assert er.classify_seat("design") == "wired"


def test_resolve_design_refused_exit2(tmp_path):
    with pytest.raises(er.MalformedConfig):
        er.resolve_seat_action("design", _ws(tmp_path), "rawgentic")


def test_cli_resolve_design_refused_exit2(tmp_path):
    r = _run_cli("resolve-seat", "--seat", "design", "--workspace", _ws(tmp_path), "--project", "rawgentic")
    assert r.returncode == er.EXIT_MALFORMED
    assert json.loads(r.stdout)["ok"] is False


def test_dispatch_design_refused_exit2(tmp_path):
    qc = QuotaCoordinator(tmp_path / "permits", {"claude": 2, "codex": 4, "zhipu": 2})
    audit = enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    res = er.dispatch_seat(
        seat="design", prompt="hi", run_id="run1", correlation_id="wf2:design",
        author_provider=None, effort=None, timeout=5.0, context=(),
        snapshot=_snapshot(), quota=qc, audit=audit, capture_root=str(tmp_path / "runs"),
        routing=routing, enforce=enforce, run_seat=run_seat, dispatch_real=_stub())
    assert res["ok"] is False and res["exit"] == er.EXIT_MALFORMED
    assert res["error"]["code"] == "competitive_only_seat"


# --- #470 §2b: internal plan-context mint + enforced plan-digest freshness ------------------------
_PLAN_STD = ("### Task 1: build the thing (#470)\n"
             "- riskLevel: standard\n"
             "- files: hooks/foo.py, hooks/bar.py\n")


def test_mint_gate_e2e_from_wf3_format_fix_plan(tmp_path):
    """#762: a WF3-format plan supplies the gate's two CLI facts end-to-end."""
    plan = tmp_path / "fix-plan.md"
    gate = tmp_path / "gate.json"
    plan.write_text(
        "- wf3-complexity: moderate_bug\n"
        "- estimated-lines: 9\n\n"
        "### Task 1: reproduce the bug\n"
        "- riskLevel: standard\n"
        "- files: tests/test_widget.py\n\n"
        "### Task 2: make the minimal fix\n"
        "- riskLevel: standard\n"
        "- files: src/widget.py\n\n"
        "### Task 3: cover the regression\n"
        "- riskLevel: standard\n"
        "- files: tests/test_widget.py\n",
        encoding="utf-8")
    fields = dict(re.findall(r"^- ([a-z0-9-]+): (.+)$", plan.read_text(encoding="utf-8"), re.M))
    mapped_complexity = {"simple_bug": "standard", "moderate_bug": "standard",
                         "complex_bug": "complex"}[fields["wf3-complexity"]]
    result = _run_cli("mint-gate", "--plan-file", str(plan),
                      "--issue-complexity", mapped_complexity,
                      "--plan-est-lines", fields["estimated-lines"], "--out", str(gate))
    assert result.returncode == 0, result.stderr
    assert json.loads(gate.read_text(encoding="utf-8"))["decision"] is False


def _gate470(plan_content=_PLAN_STD, *, risk="standard", complexity="standard", lines=7, file_count=2):
    """A #470 build gate that RECORDS the plan-file digest it was minted against. The snapshot facts
    are set to MATCH what the mint derives from ``plan_content`` (aggregate risk, distinct-file count)
    so verified_decision's cross-check passes on a fresh plan — mirroring the sibling gate-minting
    step whose plan_est agrees with the parsed plan."""
    return cg.needs_bakeoff({"risk_level": risk}, {"complexity": complexity},
                            {"files": [], "lines": lines, "file_count": file_count},
                            plan_content=plan_content)


def test_mint_plan_context_happy_derives_four_keys():
    gd = _gate470()
    ctx, fresh = er.mint_plan_context(gd, _PLAN_STD, run_id="r1", correlation_id="c1")
    # risk_level + file_count from the LIVE plan; complexity + lines from the gate's own record.
    assert ctx == {"risk_level": "standard", "complexity": "standard", "lines": 7, "file_count": 2}
    # exactly the canonical key set, and it authenticates against the gate (dispatch re-checks it).
    assert frozenset(ctx) == cg.REQUIRED_PLAN_CONTEXT_KEYS
    assert cg.verified_decision(gd, expected_context=ctx) is False
    # audit tuple: gate policy_digest + live plan digest + run/correlation ids.
    assert fresh == {"gate_policy_digest": gd.policy_digest,
                     "plan_digest": cg.plan_content_digest(_PLAN_STD),
                     "run_id": "r1", "correlation_id": "c1"}


def test_mint_plan_context_high_risk_aggregate():
    plan = ("### Task 1: a\n- riskLevel: standard\n- files: a.py\n\n"
            "### Task 2: b\n- riskLevel: high (security surface)\n- files: b.py\n")
    gd = _gate470(plan, risk="high", file_count=2)
    ctx, _ = er.mint_plan_context(gd, plan)
    # ANY high task ⇒ aggregate high; file_count = count of DISTINCT declared files.
    assert ctx["risk_level"] == "high" and ctx["file_count"] == 2


def test_mint_plan_context_byte_identical_plan_passes():
    # design §2b: a byte-identical plan is the "nothing changed" case — the old gate IS current.
    gd = _gate470()
    ctx, fresh = er.mint_plan_context(gd, _PLAN_STD)
    assert fresh["plan_digest"] == gd.input_snapshot["plan_digest"]
    assert ctx["risk_level"] == "standard"


def test_mint_plan_context_stale_plan_raises_gate_stale():
    gd = _gate470()
    revised = _PLAN_STD + "- files: hooks/extra.py\n"  # plan edited after the gate was minted
    with pytest.raises(er.PlanStale) as ei:
        er.mint_plan_context(gd, revised)
    assert ei.value.code == "gate_stale_for_plan"


def test_mint_plan_context_missing_recorded_digest_raises():
    # pre-#470 gate (no plan_content at mint) ⇒ no recorded plan_digest ⇒ fail-closed, distinct code.
    gd = cg.needs_bakeoff({"risk_level": "standard"}, {"complexity": "standard"},
                          {"files": [], "lines": 7, "file_count": 2})
    assert "plan_digest" not in gd.input_snapshot
    with pytest.raises(er.PlanStale) as ei:
        er.mint_plan_context(gd, _PLAN_STD)
    assert ei.value.code == "gate_missing_plan_digest"


def test_mint_plan_context_malformed_plan_bubbles_format_error():
    import plan_lib  # noqa: PLC0415
    # a partial plan (a task heading with no riskLevel line) is a fail-closed parse — bubbled so the
    # CLI maps it to the malformed-input class (exit 2). Fresh digest so the parse is actually reached.
    bad = "### Task 1: a\n- riskLevel: standard\n\n### Task 2: b\n- files: x.py\n"
    gd = _gate470(bad, file_count=1)
    with pytest.raises(plan_lib.PlanFormatError):
        er.mint_plan_context(gd, bad)


# --- #464 §E: build-dispatch gate (attested, launch-bound, context-cross-checked) -----------------
def test_build_missing_gate_file_exit2_no_receipt(tmp_path):
    res, audit = _dispatch_build(tmp_path, None, {"risk_level": "standard"})
    assert res["ok"] is False and res["exit"] == er.EXIT_MALFORMED
    assert res["error"]["code"] == "gate_file_required"
    assert audit.records() == []  # denial pre-check_pre: no receipt minted


def test_build_missing_plan_context_exit2_no_receipt(tmp_path):
    gd, _ = _gate()
    res, audit = _dispatch_build(tmp_path, gd, None)
    assert res["ok"] is False and res["exit"] == er.EXIT_MALFORMED
    assert res["error"]["code"] == "plan_context_required"
    assert audit.records() == []


def test_build_empty_plan_context_exit2_no_receipt(tmp_path):
    # empty {} counts as MISSING — a defense that can be silently emptied is no defense (#464 §E).
    gd, _ = _gate()
    res, audit = _dispatch_build(tmp_path, gd, {})
    assert res["ok"] is False and res["exit"] == er.EXIT_MALFORMED
    assert res["error"]["code"] == "plan_context_required"
    assert audit.records() == []


def test_build_tampered_gate_exit4_no_receipt(tmp_path):
    # input_snapshot edited but policy_digest stale -> verified_decision raises GateTamperError.
    gd, ctx = _gate()
    tampered = cg.GateDecision(decision=gd.decision, reason_codes=gd.reason_codes,
                               input_snapshot={**gd.input_snapshot, "lines": 999},
                               policy_digest=gd.policy_digest)
    res, audit = _dispatch_build(tmp_path, tampered, ctx)
    assert res["ok"] is False and res["exit"] == er.EXIT_ENFORCEMENT
    assert res["error"]["code"] == "gate_tampered"
    assert audit.records() == []  # denial happens pre-check_pre; no attestation to bind


def test_build_stale_gate_context_mismatch_exit4(tmp_path):
    # Integration on the REAL dispatch path (design §E): a valid-digest gate whose independently
    # sourced plan context disagrees with the snapshot is a stale/reused decision -> refused.
    gd, ctx = _gate()  # snapshot risk_level == "standard"
    res, audit = _dispatch_build(tmp_path, gd, dict(ctx, risk_level="high"))  # mismatched plan fact
    assert res["ok"] is False and res["exit"] == er.EXIT_ENFORCEMENT
    assert res["error"]["code"] == "gate_tampered"
    assert audit.records() == []


def test_build_happy_path_single_outcome(tmp_path):
    gd, ctx = _gate()  # single outcome (no bake-off)
    res, audit = _dispatch_build(tmp_path, gd, ctx)
    assert res["ok"] is True and res["exit"] == 0
    assert res["actual_model"] == "claude-sonnet-5" and res["verified"] is True
    recs = audit.records()
    receipts = [r for r in recs if r["kind"] == "receipt"]
    assert receipts and all(r["verdict"] == "pass" for r in receipts)
    r0 = receipts[0]
    assert r0["role"] == "build" and r0["gate_outcome"] == "single"
    assert r0["gate_input_digest"] and r0["gate_digest"] == gd.policy_digest
    assert any(r["kind"] == "observation" for r in recs)


def test_build_gate_bakeoff_denied_receipt_only_exit4(tmp_path):
    # a valid attestation whose outcome is "bakeoff" cannot be re-presented to the single-dispatch
    # path (pass-2 P1) -> check_pre gate_requires_bakeoff -> receipt-only exit 4.
    gd, ctx = _gate(bakeoff=True)
    res, audit = _dispatch_build(tmp_path, gd, ctx)
    assert res["ok"] is False and res["exit"] == er.EXIT_ENFORCEMENT
    recs = audit.records()
    assert any(r["kind"] == "receipt" and r["verdict"] == "fail"
               and "gate_requires_bakeoff" in r["violations"] for r in recs)
    assert not any(r["kind"] == "observation" for r in recs)


def _cli_build_env(tmp_path, gate_obj, *, plan_text=_PLAN_STD, write_plan=True):
    """A real CLI dispatch environment for the build seat (#470 §2b): a project declaring a
    phaseExecutorTable override → a copied shipped table, a workspace binding build=executor, the
    gate file, and (optionally) the live plan file. Returns an args-namespace instance for
    er._do_dispatch. #445 migration (S1/P2-G1): the override resolution is exercised end-to-end on
    the way to the gate/freshness check."""
    repo = tmp_path / "projects" / "rawgentic"
    table_dst = repo / "claude_docs" / "routing" / "phase-executor-table.json"
    table_dst.parent.mkdir(parents=True)
    table_dst.write_bytes(routing.default_table_path().read_bytes())
    _cfg(repo, pointer="claude_docs/routing/phase-executor-table.json")
    ws = _ws(tmp_path, {"version": 1, "seats": {"build": "executor"}}, path="./projects/rawgentic")
    gf = tmp_path / "gate.json"
    gf.write_text(json.dumps({"decision": gate_obj.decision, "reason_codes": list(gate_obj.reason_codes),
                              "input_snapshot": gate_obj.input_snapshot,
                              "policy_digest": gate_obj.policy_digest}), encoding="utf-8")
    (tmp_path / "p.txt").write_text("hi", encoding="utf-8")
    plan_path = tmp_path / "impl-plan.md"
    if write_plan:
        plan_path.write_text(plan_text, encoding="utf-8")

    class A:
        seat = "build"; prompt_file = str(tmp_path / "p.txt"); run_id = "run1"; context_file = None
        correlation_id = "wf2:build"; author_provider = None; effort = None; timeout = 5.0
        workspace = ws; project = "rawgentic"; gate_file = str(gf)
        plan_file = str(plan_path) if write_plan else None
    return A()


def test_cli_build_stale_plan_gate_stale_exit4(tmp_path, capsys):
    # Integration through the CLI --gate-file / --plan-file wiring (#470 §2b): the gate was minted
    # against _PLAN_STD; the live plan on disk was revised, so its digest no longer matches the
    # gate's recorded digest -> gate_stale_for_plan (enforcement, exit 4), refused pre-launch.
    gd = _gate470()
    a = _cli_build_env(tmp_path, gd, plan_text=_PLAN_STD + "- files: hooks/extra.py\n")
    assert er._do_dispatch(a) == er.EXIT_ENFORCEMENT
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "gate_stale_for_plan"


def test_cli_build_missing_plan_digest_exit4(tmp_path, capsys):
    # A pre-#470 gate (minted with no plan_content) carries no recorded plan digest -> fail-closed
    # with the DISTINCT back-compat code (a security control never silently passes on absent evidence).
    gd = cg.needs_bakeoff({"risk_level": "standard"}, {"complexity": "standard"},
                          {"files": [], "lines": 7, "file_count": 2})
    a = _cli_build_env(tmp_path, gd)
    assert er._do_dispatch(a) == er.EXIT_ENFORCEMENT
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "gate_missing_plan_digest"


def test_cli_build_missing_plan_file_exit2(tmp_path, capsys):
    # a build seat now REQUIRES the live plan file (--plan-file replaces --plan-context).
    gd = _gate470()
    a = _cli_build_env(tmp_path, gd, write_plan=False)
    assert er._do_dispatch(a) == er.EXIT_MALFORMED
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "plan_file_required"


def test_cli_build_unreadable_plan_file_exit2(tmp_path, capsys):
    gd = _gate470()
    a = _cli_build_env(tmp_path, gd)
    a.plan_file = str(tmp_path / "does-not-exist.md")
    assert er._do_dispatch(a) == er.EXIT_MALFORMED
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "plan_file_unreadable"


def test_cli_dispatch_parser_rejects_removed_plan_context_arg(tmp_path):
    # #470 §2b: the caller-assembled --plan-context arg is FULLY REMOVED from the CLI surface.
    with pytest.raises(SystemExit):
        er.main(["dispatch", "--seat", "build", "--prompt-file", "x", "--run-id", "r",
                 "--workspace", "w", "--project", "p", "--plan-context", "ctx.json"])


def test_gate_file_nondict_snapshot_structured_exit2_464(tmp_path):
    """#464 Step-8a (R1+R2 converged): a gate file whose input_snapshot is a NON-DICT (with a
    self-consistent digest) must map to the structured exit-2 bad-input path, never a bare
    TypeError traceback — the guard lives at the trust boundary (_load_gate_decision)."""
    with pytest.raises(ValueError, match="input_snapshot"):
        gate = {"decision": False, "reason_codes": [],
                "input_snapshot": None, "policy_digest": "sha256:whatever"}
        p = tmp_path / "gate.json"
        p.write_text(json.dumps(gate), encoding="utf-8")
        er._load_gate_decision(str(p))


@pytest.mark.parametrize("missing", ["risk_level", "complexity", "lines", "file_count"])
def test_build_partial_context_refused_per_field_464(tmp_path, missing):
    """Step-11 diff review (REOPENS 464-step6-H1): a PARTIAL plan context — any canonical key
    omitted — must be refused BEFORE verification (comparing only supplied keys silently disables
    the omitted-field stale-decision checks). Exact key-set equality is the contract."""
    gd, ctx = _gate()
    partial = {k: v for k, v in ctx.items() if k != missing}
    res, audit = _dispatch_build(tmp_path, gd, partial)
    assert res["ok"] is False and res["exit"] == er.EXIT_MALFORMED
    assert res["error"]["code"] == "plan_context_incomplete"
    assert missing in res["error"]["message"]  # names the missing KEY, never values
    assert audit.records() == []


def test_build_extra_context_key_refused_464(tmp_path):
    gd, ctx = _gate()
    ctx = dict(ctx, thresholds={"BAKEOFF_DIFF_LINES": 1})  # gate-internal key smuggled in
    res, audit = _dispatch_build(tmp_path, gd, ctx)
    assert res["ok"] is False and res["exit"] == er.EXIT_MALFORMED
    assert res["error"]["code"] == "plan_context_incomplete"
    assert audit.records() == []


def test_build_fallback_attempt_attestation_bound_464(tmp_path):
    """Step-11 A2: a build CHAIN FALLBACK attempt (index > 0, different target) must mint its own
    launch-bound attestation — receipt for the fallback passes with a gate_input_digest DISTINCT
    from the primary attempt's (per-target binding, no mint-against-primary regression)."""
    gd, ctx = _gate()
    res, audit = _dispatch_build(tmp_path, gd, ctx,
                                 dispatch_real=_stub({"claude-sonnet-5": contract.NONZERO_EXIT}))
    assert res["ok"] is True and res["actual_model"] == "claude-opus-4-8"
    receipts = [r for r in audit.records() if r["kind"] == "receipt"]
    assert len(receipts) == 2 and all(r["verdict"] == "pass" for r in receipts)
    assert all(r["gate_outcome"] == "single" for r in receipts)
    digests = {r["gate_input_digest"] for r in receipts}
    assert len(digests) == 2  # per-target binding: primary vs fallback differ


# --- #445: per-project seat table — resolve_table / seed_table / CLI observability ---------------

import os as _os


def _proj_ws(tmp_path, pointer=None, seats=None):
    """Fake project (complete valid config, optional pointer) + workspace binding it."""
    repo = tmp_path / "projects" / "fake"
    _cfg(repo, pointer=pointer)
    ws = _ws(tmp_path, {"version": 1, "seats": seats or {"ship": "executor"}}, path="./projects/fake")
    return repo, ws


class TestResolveTable:
    def test_absent_config_file_resolves_package_default(self, tmp_path):
        repo = tmp_path / "noconfig"
        repo.mkdir()
        rt = er.resolve_table(repo, routing)
        assert rt.source == "package_default"
        assert rt.path == routing.default_table_path()
        assert rt.snapshot.config_digest == routing.snapshot_from_file(routing.default_table_path()).config_digest

    def test_absent_section_resolves_package_default(self, tmp_path):
        repo = tmp_path / "p"
        _cfg(repo)  # complete config, no phaseExecutorTable
        rt = er.resolve_table(repo, routing)
        assert rt.source == "package_default"

    def test_sentinel_resolves_identical_to_absent_section(self, tmp_path):
        # #531: the answered-defaults sentinel {"version": 1, "file": null} resolves
        # EXACTLY like an absent section — same source, same path, same snapshot
        # digest (the byte-identical-behavior claim, asserted on content not label).
        repo_absent = tmp_path / "absent"
        _cfg(repo_absent)
        repo_sentinel = tmp_path / "sentinel"
        cfg_path = _cfg(repo_sentinel)
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["phaseExecutorTable"] = {"version": 1, "file": None}
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        rt_a = er.resolve_table(repo_absent, routing)
        rt_s = er.resolve_table(repo_sentinel, routing)
        assert rt_s.source == rt_a.source == "package_default"
        assert rt_s.path == rt_a.path
        assert rt_s.snapshot.config_digest == rt_a.snapshot.config_digest

    def test_real_repo_resolves_digest_identical_to_shipped(self):
        # rawgentic's own .rawgentic.json declares no phaseExecutorTable -> byte/digest-identical
        # to the shipped package table (AC1 backward-compat).
        repo = Path(er.__file__).resolve().parent.parent
        rt = er.resolve_table(repo, routing)
        assert rt.source == "package_default"
        assert rt.snapshot.config_digest == routing.snapshot_from_file(
            repo / "phase_executor/src/phase_executor/routing/rawgentic.routing-table.json").config_digest

    def test_override_read_with_package_digest(self, tmp_path):
        repo = tmp_path / "p"
        repo.mkdir()
        dst = repo / "claude_docs" / "t.json"
        dst.parent.mkdir(parents=True)
        dst.write_bytes(routing.default_table_path().read_bytes())
        _cfg(repo, pointer="claude_docs/t.json")
        rt = er.resolve_table(repo, routing)
        assert rt.source == "project_file"
        assert rt.path == dst.resolve()
        assert rt.snapshot.config_digest == routing.snapshot_from_file(routing.default_table_path()).config_digest

    # --- fail-closed matrix (helper level; CLI-level below) ---
    def test_declared_but_missing_never_falls_back(self, tmp_path):
        repo = tmp_path / "p"
        _cfg(repo, pointer="nope/missing.json")
        with pytest.raises(er.MalformedConfig, match="missing.json"):
            er.resolve_table(repo, routing)


def _supervised_no_sandboxed_lane(*, rt, monkeypatch):
    """Drive only the chain filter; no provider/worktree side effect is reachable."""
    pe = er._import_phase_executor()  # noqa: SLF001 - test the CLI's resolved-package seam
    monkeypatch.setattr(pe, "PROVIDER_ENGINE", {"anthropic": "claude", "openai": "claude"})
    args = types.SimpleNamespace(seat="build", correlation_id="762-r3h", author_provider=None)
    return er._run_supervised(  # noqa: SLF001 - refusal is owned by this helper
        args, pe, rt.snapshot, {}, None, types.SimpleNamespace(path="audit.jsonl"), {},
        rt.path.parent, "", None, None, resolved_table=rt)


def test_no_sandboxed_mutating_lane_names_package_default_provenance(tmp_path, monkeypatch):
    repo = tmp_path / "default-project"
    repo.mkdir()
    rt = er.resolve_table(repo, routing)
    result = _supervised_no_sandboxed_lane(rt=rt, monkeypatch=monkeypatch)
    message = result["error"]["message"]
    assert rt.source in message and str(rt.path) in message


def test_no_sandboxed_mutating_lane_names_project_override_provenance(tmp_path, monkeypatch):
    repo = tmp_path / "override-project"
    _cfg(repo, pointer="claude_docs/routing/table.json")
    table = repo / "claude_docs" / "routing" / "table.json"
    table.parent.mkdir(parents=True)
    table.write_bytes(routing.default_table_path().read_bytes())
    rt = er.resolve_table(repo, routing)
    result = _supervised_no_sandboxed_lane(rt=rt, monkeypatch=monkeypatch)
    message = result["error"]["message"]
    assert rt.source == "project_file"
    assert rt.source in message and str(rt.path) in message


def test_retuned_package_table_resolves_and_review_keeps_cross_model_chain_eligible(tmp_path):
    repo = tmp_path / "retuned-package-default"
    repo.mkdir()
    rt = er.resolve_table(repo, routing)  # package schema + semantic validation entry point
    expected = {
        "intake": ("claude-sonnet-5", ["claude-fable-5", "claude-sonnet-5"], "xhigh"),
        "analysis": ("claude-opus-5", ["claude-fable-5", "claude-sonnet-5"], "high"),
        "design": ("gpt-5.6-sol", ["claude-fable-5"], "high"),
        "plan": ("claude-opus-5", ["claude-fable-5", "claude-sonnet-5"], "high"),
        "build": ("claude-sonnet-5", ["claude-opus-5", "gpt-5.6-terra"], "high"),
        "review": ("gpt-5.6-sol", ["claude-fable-5", "claude-sonnet-5"], "high"),
        "ship": ("claude-sonnet-5", ["claude-opus-5", "claude-fable-5"], "high"),
        "offload": ("hermes-agent", ["claude-sonnet-5"], "medium"),
    }
    for seat, (primary, chain, effort) in expected.items():
        spec = rt.snapshot.seat(seat)
        assert spec["primary"]["model"] == primary
        assert [entry["model"] for entry in spec["chain"]] == chain
        assert spec["manifest"]["effort"] == effort
        assert [target["model"] for target in routing.eligible_targets(seat, rt.snapshot)] == [
            primary, *chain]
    assert rt.snapshot.seat("analysis")["manifest"]["bounds"]["max_budget_usd"] == 10.0
    review = routing.eligible_targets("review", rt.snapshot, author_provider="anthropic")
    assert review[0]["model"] == "gpt-5.6-sol"
    assert [target["model"] for target in review] == ["gpt-5.6-sol"]


class TestResolveTerminalBackend:
    """#638: resolve_terminal_backend mirrors resolve_table's config-read pattern (absent
    config/section -> "tmux"; present-but-malformed fails closed, never a silent tmux)."""

    def test_absent_config_file_resolves_tmux(self, tmp_path):
        repo = tmp_path / "noconfig"
        repo.mkdir()
        assert er.resolve_terminal_backend(repo) == "tmux"

    def test_absent_section_resolves_tmux(self, tmp_path):
        repo = tmp_path / "p"
        _cfg(repo)  # complete config, no executorTerminalBackend
        assert er.resolve_terminal_backend(repo) == "tmux"

    def test_valid_herdr_descriptor_resolves_herdr(self, tmp_path):
        repo = tmp_path / "p"
        cfg_path = _cfg(repo)
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["executorTerminalBackend"] = {"version": 1, "build": "herdr"}
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        assert er.resolve_terminal_backend(repo) == "herdr"

    def test_valid_tmux_descriptor_resolves_tmux(self, tmp_path):
        repo = tmp_path / "p"
        cfg_path = _cfg(repo)
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["executorTerminalBackend"] = {"version": 1, "build": "tmux"}
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        assert er.resolve_terminal_backend(repo) == "tmux"

    def test_malformed_section_never_falls_back_silently(self, tmp_path):
        repo = tmp_path / "p"
        cfg_path = _cfg(repo)
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["executorTerminalBackend"] = {"version": 1, "build": "not-a-backend"}
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        with pytest.raises(er.MalformedConfig):
            er.resolve_terminal_backend(repo)

    def test_real_repo_resolves_herdr(self):
        # Smoke test on the REAL config file, not the logic (that is TestSelectLaunch...
        # below): rawgentic's own .rawgentic.json must parse and resolve to what it
        # declares. It declared nothing -> "tmux" until 2026-07-27, when the herdr build
        # seat was switched on for this project after UAT-3 passed live (epic #635).
        # If the gate is ever flipped back, update this line with the config.
        repo = Path(er.__file__).resolve().parent.parent
        assert er.resolve_terminal_backend(repo) == "herdr"


class TestSelectLaunchTerminalBackend:
    """#638 AC4: the build-seat-only gate decision, tested directly (not just its two
    ingredients) so a bug in the COMBINING logic — a swapped condition, wrong role string —
    can't slip through untested."""

    def test_build_seat_herdr_configured_selects_herdr(self):
        assert er.select_launch_terminal_backend("build", "herdr") == "herdr"

    def test_build_seat_tmux_configured_selects_tmux(self):
        assert er.select_launch_terminal_backend("build", "tmux") == "tmux"

    def test_non_build_seat_never_selects_herdr_even_if_configured(self):
        assert er.select_launch_terminal_backend("review", "herdr") == "tmux"
        assert er.select_launch_terminal_backend("analysis", "herdr") == "tmux"

    def test_none_role_never_selects_herdr(self):
        # no snapshot / unresolvable seat role -> never herdr, regardless of config
        assert er.select_launch_terminal_backend(None, "herdr") == "tmux"

    def test_pointer_names_a_directory(self, tmp_path):
        repo = tmp_path / "p"
        _cfg(repo, pointer="somedir")
        (repo / "somedir").mkdir()
        with pytest.raises(er.MalformedConfig, match="not a regular file"):
            er.resolve_table(repo, routing)

    @pytest.mark.skipif(_os.geteuid() == 0, reason="root ignores file permissions — cell cannot bite")
    def test_pointer_unreadable_file(self, tmp_path):
        repo = tmp_path / "p"
        _cfg(repo, pointer="t.json")
        t = repo / "t.json"
        t.write_bytes(routing.default_table_path().read_bytes())
        t.chmod(0)
        try:
            with pytest.raises(er.MalformedConfig, match="t.json"):
                er.resolve_table(repo, routing)
        finally:
            t.chmod(0o644)

    def test_symlink_escape_table_pointer_refused(self, tmp_path):
        outside = tmp_path / "outside.json"
        outside.write_bytes(routing.default_table_path().read_bytes())
        repo = tmp_path / "p"
        _cfg(repo, pointer="link.json")
        (repo / "link.json").symlink_to(outside)
        with pytest.raises(er.MalformedConfig, match="outside the project root"):
            er.resolve_table(repo, routing)

    def test_dangling_symlink_config_is_malformed_not_absent(self, tmp_path):
        repo = tmp_path / "p"
        repo.mkdir()
        (repo / ".rawgentic.json").symlink_to(tmp_path / "gone.json")
        with pytest.raises(er.MalformedConfig, match="fail-closed"):
            er.resolve_table(repo, routing)

    def test_directory_as_config_is_malformed(self, tmp_path):
        repo = tmp_path / "p"
        (repo / ".rawgentic.json").mkdir(parents=True)
        with pytest.raises(er.MalformedConfig):
            er.resolve_table(repo, routing)

    @pytest.mark.skipif(_os.geteuid() == 0, reason="root ignores file permissions — cell cannot bite")
    def test_unreadable_config_is_malformed(self, tmp_path):
        repo = tmp_path / "p"
        cfgp = _cfg(repo)
        cfgp.chmod(0)
        try:
            with pytest.raises(er.MalformedConfig):
                er.resolve_table(repo, routing)
        finally:
            cfgp.chmod(0o644)

    @pytest.mark.parametrize("bad_pointer", ["/abs/t.json", "../escape.json"])
    def test_absolute_and_traversal_pointers_malformed(self, tmp_path, bad_pointer):
        repo = tmp_path / "p"
        _cfg(repo, pointer=bad_pointer)
        with pytest.raises(er.MalformedConfig):
            er.resolve_table(repo, routing)

    def test_schema_invalid_override_content_exit2_class(self, tmp_path):
        repo = tmp_path / "p"
        _cfg(repo, pointer="t.json")
        (repo / "t.json").write_text('{"schema_version": 1}', encoding="utf-8")
        with pytest.raises(er.MalformedConfig, match="failed to load"):
            er.resolve_table(repo, routing)

    def test_statically_dead_seat_override_fails_at_resolution(self, tmp_path):
        table = json.loads(routing.default_table_path().read_text(encoding="utf-8"))
        # Kill an entire seat's chain with a context-free rule (never-Haiku pattern style).
        seat = table["seats"]["ship"]
        for t in (seat["primary"], *seat.get("chain", [])):
            t["model"] = "wombat-9"
        table["forbidden_combinations"].append(
            {"model_pattern": "wombat", "reason": "test: wombat models forbidden"})
        repo = tmp_path / "p"
        _cfg(repo, pointer="t.json")
        (repo / "t.json").write_text(json.dumps(table), encoding="utf-8")
        with pytest.raises(er.MalformedConfig, match="statically dead") as ei:
            er.resolve_table(repo, routing)
        assert "ship" in str(ei.value) and "wombat" in str(ei.value)


class TestSeedTable:
    def test_seed_bytes_identical_and_round_trip(self, tmp_path):
        repo = tmp_path / "p"
        _cfg(repo, pointer="claude_docs/routing/phase-executor-table.json")
        dest = repo / "claude_docs" / "routing" / "phase-executor-table.json"
        out = er.seed_table(dest)
        assert out == dest
        assert dest.read_bytes() == routing.default_table_path().read_bytes()
        rt = er.resolve_table(repo, routing)
        assert rt.source == "project_file"
        assert rt.snapshot.config_digest == routing.snapshot_from_file(routing.default_table_path()).config_digest

    def test_seed_refuses_overwrite(self, tmp_path):
        dest = tmp_path / "t.json"
        dest.write_text("{}", encoding="utf-8")
        with pytest.raises(er.MalformedConfig, match="refusing to overwrite"):
            er.seed_table(dest)


class TestResolveSeatCliObservability:
    def test_cli_default_reports_package_source_and_digest(self, tmp_path):
        repo, ws = _proj_ws(tmp_path)
        r = _run_cli("resolve-seat", "--seat", "ship", "--workspace", ws, "--project", "rawgentic")
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["table_source"] == "package_default"
        assert out["config_digest"].startswith("sha256:")

    def test_cli_override_reports_project_source(self, tmp_path):
        repo, ws = _proj_ws(tmp_path, pointer="claude_docs/t.json")
        dst = repo / "claude_docs" / "t.json"
        dst.parent.mkdir(parents=True)
        dst.write_bytes(routing.default_table_path().read_bytes())
        r = _run_cli("resolve-seat", "--seat", "ship", "--workspace", ws, "--project", "rawgentic")
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["table_source"] == "project_file"

    def test_cli_declared_missing_exit2_names_path(self, tmp_path):
        repo, ws = _proj_ws(tmp_path, pointer="gone/t.json")
        r = _run_cli("resolve-seat", "--seat", "ship", "--workspace", ws, "--project", "rawgentic")
        assert r.returncode == er.EXIT_MALFORMED
        err = json.loads(r.stdout)["error"]
        assert err["code"] == "malformed_config" and "gone/t.json" in err["message"]

    def test_cli_dead_seat_exit2(self, tmp_path):
        repo, ws = _proj_ws(tmp_path, pointer="t.json")
        table = json.loads(routing.default_table_path().read_text(encoding="utf-8"))
        seat = table["seats"]["ship"]
        for t in (seat["primary"], *seat.get("chain", [])):
            t["model"] = "wombat-9"
        table["forbidden_combinations"].append({"model_pattern": "wombat", "reason": "test"})
        (repo / "t.json").write_text(json.dumps(table), encoding="utf-8")
        r = _run_cli("resolve-seat", "--seat", "ship", "--workspace", ws, "--project", "rawgentic")
        assert r.returncode == er.EXIT_MALFORMED
        assert "statically dead" in json.loads(r.stdout)["error"]["message"]

    def test_dispatch_path_declared_missing_exit2(self, tmp_path):
        # Representative dispatch-path matrix cell (PL-3): same fail-closed class through
        # _do_dispatch's resolution (stub-free — fails before any provider machinery).
        repo, ws = _proj_ws(tmp_path, pointer="gone/t.json", seats={"ship": "executor"})
        (tmp_path / "p.txt").write_text("hi", encoding="utf-8")

        class A:
            seat = "ship"; prompt_file = str(tmp_path / "p.txt"); run_id = "r1"; context_file = None
            correlation_id = "t"; author_provider = None; effort = None; timeout = 5.0
            workspace = ws; project = "rawgentic"; gate_file = None; plan_file = None
        assert er._do_dispatch(A()) == er.EXIT_MALFORMED

    def test_seed_refuses_dangling_symlink_dest(self, tmp_path):
        # 8a-B2: the is_symlink() half of the overwrite guard — a DANGLING symlink dest
        # (exists() False, is_symlink() True) must refuse, not silently replace the link.
        dest = tmp_path / "t.json"
        dest.symlink_to(tmp_path / "gone.json")
        with pytest.raises(er.MalformedConfig, match="refusing to overwrite"):
            er.seed_table(dest)

    def test_seed_leaves_no_tmp_on_success(self, tmp_path):
        er.seed_table(tmp_path / "t.json")
        assert [p.name for p in tmp_path.iterdir()] == ["t.json"]

    def test_seed_parent_is_a_file_legible(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        with pytest.raises(er.MalformedConfig, match="cannot create parent directory"):
            er.seed_table(blocker / "t.json")


# --- #446: show-table (projection) ----------------------------------------------------------------

class TestShowTable:
    def test_human_summary_default_project(self, tmp_path):
        repo, ws = _proj_ws(tmp_path)
        r = _run_cli("show-table", "--workspace", ws, "--project", "rawgentic")
        assert r.returncode == 0
        assert "table_source: package_default" in r.stdout
        assert "config_digest: sha256:" in r.stdout
        assert "ship" in r.stdout and "review" in r.stdout

    def test_json_projection_default(self, tmp_path):
        repo, ws = _proj_ws(tmp_path)
        r = _run_cli("show-table", "--workspace", ws, "--project", "rawgentic", "--json")
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["projection_version"] == 1
        assert out["table_source"] == "package_default"
        assert out["config_digest"].startswith("sha256:")
        assert out["file"] is None
        seats = {s["seat"]: s for s in out["seats"]}
        assert set(seats) == {"intake", "analysis", "design", "plan", "build", "review", "ship",
                              "offload"}  # #568 Phase-2
        assert seats["build"]["role"] == "build"
        assert isinstance(seats["ship"]["primary"], str) and seats["ship"]["chain"]
        # build_bake_off reports the ACTUAL candidate constant, labeled informational.
        import bakeoff_policy
        assert out["build_bake_off"] == list(bakeoff_policy.BUILD_MODELS)
        assert "not table-editable" in out["build_bake_off_note"]

    def test_json_projection_override_carries_file(self, tmp_path):
        repo, ws = _proj_ws(tmp_path, pointer="claude_docs/t.json")
        dst = repo / "claude_docs" / "t.json"
        dst.parent.mkdir(parents=True)
        dst.write_bytes(routing.default_table_path().read_bytes())
        r = _run_cli("show-table", "--workspace", ws, "--project", "rawgentic", "--json")
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["table_source"] == "project_file"
        assert out["file"] == "claude_docs/t.json"

    def test_broken_override_exit2(self, tmp_path):
        repo, ws = _proj_ws(tmp_path, pointer="gone.json")
        r = _run_cli("show-table", "--workspace", ws, "--project", "rawgentic")
        assert r.returncode == er.EXIT_MALFORMED
        assert "gone.json" in json.loads(r.stdout)["error"]["message"]


# --- #446: apply-table (sparse patch -> validated materialization) --------------------------------

def _patch_file(tmp_path, patch):
    p = tmp_path / "patch.json"
    p.write_text(json.dumps(patch), encoding="utf-8")
    return str(p)


def _apply(ws, tmp_path, patch, dest="claude_docs/routing/phase-executor-table.json",
           expected=None, candidate=None, extra=()):
    args = ["apply-table", "--workspace", ws, "--project", "rawgentic",
            "--patch-json", _patch_file(tmp_path, patch), "--dest", dest]
    if expected is not None:
        args += ["--expected-digest", expected]
    if candidate is not None:
        args += ["--expected-candidate-digest", candidate]
    args += list(extra)
    return _run_cli(*args)


def _pkg_digest():
    return routing.snapshot_from_file(routing.default_table_path()).config_digest


class TestApplyTable:
    def test_validate_only_prints_pointer_and_writes_nothing(self, tmp_path):
        repo, ws = _proj_ws(tmp_path)
        before = sorted(p.name for p in repo.iterdir())
        r = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-5"}},
                   expected=_pkg_digest(), extra=["--validate-only"])
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["config_digest"].startswith("sha256:")
        assert out["pointer"] == {"version": 1, "file": "claude_docs/routing/phase-executor-table.json"}
        assert sorted(p.name for p in repo.iterdir()) == before

    def test_validate_only_combined_with_reset_uses_package_base(self, tmp_path):
        repo, ws = _proj_ws(tmp_path)
        r = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-5"}},
                   expected=_pkg_digest(), extra=["--validate-only", "--reset-to-default"])
        assert r.returncode == 0

    def test_candidate_digest_forbidden_in_validate_only(self, tmp_path):
        repo, ws = _proj_ws(tmp_path)
        r = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-5"}},
                   expected=_pkg_digest(), candidate="sha256:deadbeef", extra=["--validate-only"])
        assert r.returncode == er.EXIT_MALFORMED
        assert "forbidden with --validate-only" in json.loads(r.stdout)["error"]["message"]

    def test_materialize_requires_matching_candidate_digest(self, tmp_path):
        repo, ws = _proj_ws(tmp_path)
        missing = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-5"}}, expected=_pkg_digest())
        assert missing.returncode == er.EXIT_MALFORMED
        assert "requires --expected-candidate-digest" in json.loads(missing.stdout)["error"]["message"]
        stale = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-5"}},
                       expected=_pkg_digest(), candidate="sha256:deadbeef")
        assert stale.returncode == er.EXIT_MALFORMED
        assert "candidate changed since validated" in json.loads(stale.stdout)["error"]["message"]
        assert not (repo / "claude_docs" / "routing" / "phase-executor-table.json").exists()

    def test_fresh_create_end_to_end(self, tmp_path):
        repo, ws = _proj_ws(tmp_path)
        v = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-5"}},
                   expected=_pkg_digest(), extra=["--validate-only"])
        cand = json.loads(v.stdout)["config_digest"]
        r = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-5"}},
                   expected=_pkg_digest(), candidate=cand)
        assert r.returncode == 0
        dest = repo / "claude_docs" / "routing" / "phase-executor-table.json"
        assert dest.is_file()
        assert routing.snapshot_from_file(dest).config_digest == cand
        table = json.loads(dest.read_text(encoding="utf-8"))
        assert table["seats"]["ship"]["primary"]["model"] == "claude-opus-5"
        # lane came from the base table's existing opus rows
        assert table["seats"]["ship"]["primary"]["lane"]["provider"] == "anthropic"

    def test_untouched_seats_keep_existing_override_customizations(self, tmp_path):
        repo, ws = _proj_ws(tmp_path, pointer="claude_docs/t.json")
        base = json.loads(routing.default_table_path().read_text(encoding="utf-8"))
        base["seats"]["intake"]["primary"]["model"] = "claude-fable-5"  # pre-existing customization
        dst = repo / "claude_docs" / "t.json"
        dst.parent.mkdir(parents=True)
        dst.write_text(json.dumps(base), encoding="utf-8")
        base_digest = routing.snapshot_from_file(dst).config_digest
        v = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-5"}},
                   dest="claude_docs/t.json", expected=base_digest, extra=["--validate-only"])
        assert v.returncode == 0
        cand = json.loads(v.stdout)["config_digest"]
        r = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-5"}},
                   dest="claude_docs/t.json", expected=base_digest, candidate=cand)
        assert r.returncode == 0
        after = json.loads(dst.read_text(encoding="utf-8"))
        assert after["seats"]["intake"]["primary"]["model"] == "claude-fable-5"  # kept (A3)
        assert after["seats"]["ship"]["primary"]["model"] == "claude-opus-5"

    def test_reseed_divergent_dest_refused(self, tmp_path):
        # 8a-B2: reach the P3-G4 guard for REAL — validated candidate digest first, then
        # assert the guard's own message (returncode-only was mutation-blind: the earlier
        # candidate-digest guard also exits 2).
        repo, ws = _proj_ws(tmp_path, pointer="claude_docs/t.json")
        dst = repo / "claude_docs" / "t.json"
        dst.parent.mkdir(parents=True)
        dst.write_bytes(routing.default_table_path().read_bytes())
        other = repo / "claude_docs" / "other.json"
        other.write_bytes(routing.default_table_path().read_bytes())
        d = routing.snapshot_from_file(dst).config_digest
        v = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-5"}},
                   dest="claude_docs/other.json", expected=d, extra=["--validate-only"])
        cand = json.loads(v.stdout)["config_digest"]
        r = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-5"}},
                   dest="claude_docs/other.json", expected=d, candidate=cand)
        assert r.returncode == er.EXIT_MALFORMED
        assert "is not the current phaseExecutorTable file" in json.loads(r.stdout)["error"]["message"]

    def test_reset_to_default_reseed_over_existing_override(self, tmp_path):
        # 8a-A out-of-scope note promoted: resetting an EXISTING override back to (patched)
        # package base must be materializable — rt_current supplies the pointer path even
        # when the patch base is the package table.
        repo, ws = _proj_ws(tmp_path, pointer="claude_docs/t.json")
        base = json.loads(routing.default_table_path().read_text(encoding="utf-8"))
        base["seats"]["intake"]["primary"]["model"] = "claude-fable-5"
        dst = repo / "claude_docs" / "t.json"
        dst.parent.mkdir(parents=True)
        dst.write_text(json.dumps(base), encoding="utf-8")
        cur = routing.snapshot_from_file(dst).config_digest  # diff-DF1: guard = CURRENT resolution
        v = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-5"}},
                   dest="claude_docs/t.json", expected=cur,
                   extra=["--validate-only", "--reset-to-default"])
        assert v.returncode == 0
        cand = json.loads(v.stdout)["config_digest"]
        r = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-5"}},
                   dest="claude_docs/t.json", expected=cur, candidate=cand,
                   extra=["--reset-to-default"])
        assert r.returncode == 0
        after = json.loads(dst.read_text(encoding="utf-8"))
        assert after["seats"]["intake"]["primary"]["model"] != "claude-fable-5"  # reset took
        assert after["seats"]["ship"]["primary"]["model"] == "claude-opus-5"

    def test_symlinked_parent_dest_escape_refused(self, tmp_path):
        # 8a-B1: an in-repo symlink dir pointing OUTSIDE the root must not let a
        # fresh-create write escape (canonical containment, not lexical normpath).
        outside = tmp_path / "OUTSIDE"
        outside.mkdir()
        repo, ws = _proj_ws(tmp_path)
        (repo / "claude_docs").mkdir(exist_ok=True)
        (repo / "claude_docs" / "routing").symlink_to(outside)
        v = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-5"}},
                   expected=_pkg_digest(), extra=["--validate-only"])
        assert v.returncode == er.EXIT_MALFORMED
        assert "outside the project root" in json.loads(v.stdout)["error"]["message"]
        assert not (outside / "phase-executor-table.json").exists()

    def test_stale_base_digest_exit2(self, tmp_path):
        repo, ws = _proj_ws(tmp_path)
        r = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-5"}},
                   expected="sha256:stale", extra=["--validate-only"])
        assert r.returncode == er.EXIT_MALFORMED
        assert "base table changed since shown" in json.loads(r.stdout)["error"]["message"]

    def test_empty_patch_is_noop_boundary(self, tmp_path):
        repo, ws = _proj_ws(tmp_path)
        r = _apply(ws, tmp_path, {}, expected=_pkg_digest(), extra=["--validate-only"])
        assert r.returncode == er.EXIT_MALFORMED
        assert "keep defaults" in json.loads(r.stdout)["error"]["message"]

    def test_semantically_empty_seat_patch_refused(self, tmp_path):
        # diff-DF3: {"ship": {}} must not materialize an unchanged table.
        repo, ws = _proj_ws(tmp_path)
        r = _apply(ws, tmp_path, {"ship": {}}, expected=_pkg_digest(), extra=["--validate-only"])
        assert r.returncode == er.EXIT_MALFORMED
        assert "keep defaults" in json.loads(r.stdout)["error"]["message"]
        assert not (repo / "claude_docs" / "routing").exists()

    @pytest.mark.parametrize("patch,frag", [
        ({"wombat": {"primary": "claude-opus-5"}}, "unknown seat"),
        ({"ship": {"floor": "opus"}}, "unknown field"),
        ({"ship": {"primary": "claude-haiku-4-5"}}, "no known lane"),
    ])
    def test_bad_patch_shapes_exit2(self, tmp_path, patch, frag):
        repo, ws = _proj_ws(tmp_path)
        r = _apply(ws, tmp_path, patch, expected=_pkg_digest(), extra=["--validate-only"])
        assert r.returncode == er.EXIT_MALFORMED
        assert frag in json.loads(r.stdout)["error"]["message"]

    def test_escaping_dest_refused_in_validate_only(self, tmp_path):
        repo, ws = _proj_ws(tmp_path)
        r = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-5"}},
                   dest="../outside.json", expected=_pkg_digest(), extra=["--validate-only"])
        assert r.returncode == er.EXIT_MALFORMED
        assert "outside the project root" in json.loads(r.stdout)["error"]["message"]


# --- #470 §2a supervised branch: EXIT_REFUSED, probe plan, canary ordering -------------------
# pylint: disable=no-name-in-module
from phase_executor import canary as _canary  # noqa: E402
from phase_executor import canary_evidence as _cev  # noqa: E402
from phase_executor import contract as _contract  # noqa: E402
# pylint: enable=no-name-in-module

REPO_ROOT = HOOKS.parent  # the plugin registration root — its hooks.json digest is the pinned one


def test_exit_refused_is_additive_six():
    # ADDITIVE, no renumber of the shipped codes (#427/#464).
    assert er.EXIT_REFUSED == 6
    assert (er.EXIT_OK, er.EXIT_MALFORMED, er.EXIT_AVAILABILITY,
            er.EXIT_ENFORCEMENT, er.EXIT_INTERNAL) == (0, 2, 3, 4, 5)


def test_build_probe_plan_derives_classes_from_staged_hooks_json():
    hooks_obj = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    n = [0]
    plan = er.build_probe_plan(hooks_obj, canary=_canary,
                               mk_correlation_id=lambda cls: f"cid-{n.__setitem__(0, n[0] + 1) or n[0]}")
    # every class is a real mutating matcher from the staged hooks.json (never invented)
    assert set(plan) == set(_canary.mutating_guard_classes(hooks_obj))
    for cls, spec in plan.items():
        assert spec["issued_tool"] == cls.split("|")[0]
        assert spec["issued_correlation_id"]


def test_build_probe_plan_empty_when_no_mutating_classes():
    assert er.build_probe_plan({"hooks": {"PreToolUse": []}}, canary=_canary,
                               mk_correlation_id=lambda c: "x") == {}


# -- supervised_dispatch: in-process harness (real canary/collector; injected provider seams) --
def _happy_probe_stream():
    """init + a hook-origin deny per mutating class (Bash: BLOCKED:, Edit: SECURITY BLOCK:).
    tool_use ids deliberately DO NOT match the plan's issued_correlation_id, so the collector's
    live NAME-correlation is what binds them (Task-3 delta)."""
    return [
        {"type": "system", "subtype": "init", "plugins": [{"name": "rawgentic"}]},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "live-1", "name": "Bash", "input": {}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "live-1", "is_error": True,
             "content": [{"type": "text", "text": "BLOCKED: ssh disabled"}]}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "live-2", "name": "Edit", "input": {}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "live-2", "is_error": True,
             "content": [{"type": "text", "text": "SECURITY BLOCK: write denied"}]}]}},
    ]


def _valid_obs(requested="claude-sonnet-5", actual=None, correlation_id=None):
    """#472 D3: append_observation is fail-loud (schema-validated), so every stub supervisor
    must return a SCHEMA-VALID observation. correlation_id default None — the dispatch-site
    ce-stamp is what the audit tests exercise."""
    from phase_executor.capture import hash_text  # noqa: PLC0415  # pylint: disable=no-name-in-module
    return _contract.Observation(
        run_id="run1", attempt_id="0-stub0000", correlation_id=correlation_id, seat="build",
        engine="claude", transport="native", requested_model=requested,
        actual_model=requested if actual is None else actual, prompt_hash=hash_text("hi"),
        context_hashes=[], usage={"input": 1, "output": 1}, timing_ms=1, queued_ms=0,
        process={"exit_code": 0, "timed_out": False}, parse_status=_contract.OK,
        parsed_payload=None, raw_capture_path=None, fallback_reason=None,
        routing_config_digest="sha256:0").to_dict()


class _StubSupervisor:
    def __init__(self, state="completed", obs="__default__", fresh=None):
        self.launched = []
        self._state = state
        self._obs = obs  # "__default__" -> _valid_obs(); else returned verbatim (#733 tests)
        self._fresh = fresh  # #733 Step-11 R1-H1: the post-_finish registry record (or None)

    def launch(self, seat, prompt, **kw):  # noqa: D401 — records the call
        self.launched.append((seat, kw))
        return {"seat": seat, "kw": kw}

    def await_job(self, record, *, timeout_s=3600.0):
        return self._state, (_valid_obs() if self._obs == "__default__" else self._obs)

    def job_record(self, record):  # #733 Step-11 R1-H1: fresh registry read seam
        return self._fresh


def _supervised(tmp_path, *, probe_stream=None, final_argv=None, state="completed",
                probe_raises=False, provision_calls=None, monkeypatch=None,
                terminal_backend="tmux", obs="__default__", fresh=None):
    # The rich claude_mutating machinery (probes, init event) stays unit-tested even though
    # production refuses mutating-claude (STEP 0, MUTATING_FS_SANDBOXED): tests widen the module
    # constant — a monkeypatch of module state, NOT a caller input; production has no such knob.
    # test_supervised_refuses_unsandboxed_mutating_engine pins the production value.
    if monkeypatch is not None:
        monkeypatch.setattr(er, "MUTATING_FS_SANDBOXED", frozenset({"codex", "claude"}))
    qc = QuotaCoordinator(tmp_path / "permits", {"claude": 2, "codex": 4, "zhipu": 2})
    audit = enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    sup = _StubSupervisor(state=state, obs=obs, fresh=fresh)
    gd, ctx = _gate()
    profile = _contract.LaunchProfile(session_policy="fresh", mutating=True)
    calls = provision_calls if provision_calls is not None else []

    def probe_session(*, composition, probe_plan, snapshot_dir):
        if probe_raises:
            raise RuntimeError("probe boom")
        return _happy_probe_stream() if probe_stream is None else probe_stream

    def provision():
        calls.append(True)
        return None, {"handle": True}  # stub supervisor ignores identity/handle content

    snap = _snapshot()
    tgt = routing.eligible_targets("build", snap)[0]
    res = er.supervised_dispatch(
        seat="build", prompt="hi", run_id="run1", correlation_id="wf2:build",
        effort=None, timeout=5.0, engine="claude", profile=profile,
        final_argv=final_argv or ["claude", "--print", "--model", "claude-sonnet-5",
                                  "--output-format", "json"],
        snapshot_dir=str(REPO_ROOT), capture_root=str(tmp_path / "runs"), audit=audit,
        canary=_canary, canary_evidence=_cev, supervisor=sup, probe_session=probe_session,
        provision=provision, gate_decision=gd, plan_context=ctx,
        target=tgt, snapshot=snap, enforce=enforce,
        mk_nonce=lambda: "NONCE-1", mk_probe_cid=lambda cls: f"probe-{cls[:3]}",
        terminal_backend=terminal_backend)
    return res, sup, qc, calls


def test_supervised_happy_path_launches_after_canary(tmp_path, monkeypatch):
    res, sup, _qc, calls = _supervised(tmp_path, monkeypatch=monkeypatch)
    assert res["ok"] is True, res
    assert res["exit"] == er.EXIT_OK
    assert res["action"] == "executor_supervised"
    # a launch happened AND it was after the canary passed (canary summary present) + provisioned
    assert res["canary"]["verdict"] == "pass", res["canary"]
    assert len(sup.launched) == 1 and calls == [True]
    # the staged snapshot digest reached launch (TOCTOU binding)
    assert sup.launched[0][1]["snapshot_digest"] == _canary.compute_registration_digest(str(REPO_ROOT))


def test_supervised_default_terminal_backend_is_tmux(tmp_path, monkeypatch):
    _res, sup, _qc, _calls = _supervised(tmp_path, monkeypatch=monkeypatch)
    assert sup.launched[0][1]["terminal_backend"] == "tmux"


def test_supervised_threads_herdr_terminal_backend_into_launch(tmp_path, monkeypatch):
    # #638: supervised_dispatch's own terminal_backend param reaches supervisor.launch() —
    # the caller (_run_supervised, the seat+config-gate check) decides this before calling.
    _res, sup, _qc, _calls = _supervised(
        tmp_path, monkeypatch=monkeypatch, terminal_backend="herdr")
    assert sup.launched[0][1]["terminal_backend"] == "herdr"


def test_supervised_phase2_refusal_exits_six_and_creates_nothing(tmp_path, monkeypatch):
    # a stream with NO Edit-class deny -> require_canary refuses positive_deny -> exit 6.
    stream = [e for e in _happy_probe_stream()
              if not (e.get("message", {}).get("content", [{}])[0].get("name") == "Edit"
                      or e.get("message", {}).get("content", [{}])[0].get("tool_use_id") == "live-2")]
    res, sup, qc, calls = _supervised(tmp_path, probe_stream=stream, monkeypatch=monkeypatch)
    assert res["exit"] == er.EXIT_REFUSED
    assert res["error"]["code"] == "canary_refused"
    assert any("positive_deny" in v for v in [res["error"]["message"]])
    # NOTHING created: no launch, no worktree provisioned, no task permit held
    assert sup.launched == [] and calls == []
    assert qc.live_permits("claude") == 0


def test_supervised_phase1_refusal_skips_probe_and_launch(tmp_path, monkeypatch):
    # a --bare final_argv fails the LOCAL bare_absent check at phase 1 -> refuse BEFORE the probe.
    calls = []
    res, sup, qc, _ = _supervised(
        tmp_path, final_argv=["claude", "--print", "--bare"], provision_calls=calls,
        monkeypatch=monkeypatch)
    assert res["exit"] == er.EXIT_REFUSED
    assert "bare_detected" in res["error"]["message"]
    assert sup.launched == [] and calls == []


def test_supervised_probe_failure_is_refusal_not_skip(tmp_path, monkeypatch):
    res, sup, _qc, calls = _supervised(tmp_path, probe_raises=True, monkeypatch=monkeypatch)
    assert res["exit"] == er.EXIT_REFUSED
    assert res["error"]["code"] == "canary_refused"
    assert "probe_session_failed" in res["error"]["message"]
    assert sup.launched == [] and calls == []


def test_supervised_missing_gate_refuses_malformed(tmp_path):
    qc = QuotaCoordinator(tmp_path / "permits", {"claude": 2})
    audit = enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    res = er.supervised_dispatch(
        seat="build", prompt="hi", run_id="run1", correlation_id="c",
        effort=None, timeout=5.0, engine="codex",
        profile=_contract.LaunchProfile(mutating=True), final_argv=["codex", "exec"],
        snapshot_dir=str(REPO_ROOT), capture_root=str(tmp_path / "runs"), audit=audit,
        canary=_canary, canary_evidence=_cev, supervisor=_StubSupervisor(),
        probe_session=lambda **k: [], provision=lambda: (None, None),
        gate_decision=None, plan_context=None,
        target=routing.eligible_targets("build", _snapshot())[0], snapshot=_snapshot(),
        enforce=enforce,
        mk_nonce=lambda: "N", mk_probe_cid=lambda c: "p")
    assert res["exit"] == er.EXIT_MALFORMED
    assert res["error"]["code"] == "gate_file_required"


def test_supervised_non_completed_state_maps_to_availability(tmp_path, monkeypatch):
    res, sup, _qc, _ = _supervised(tmp_path, state="timed_out", monkeypatch=monkeypatch)
    assert res["exit"] == er.EXIT_AVAILABILITY
    assert res["error"]["code"] == "supervised_timed_out"
    assert len(sup.launched) == 1  # launch DID happen; the FAILURE was downstream


def test_supervised_refuses_unsandboxed_mutating_engine(tmp_path):
    """Production pin (contract.py SECURITY-LAYER ASYMMETRY, owner 2026-07-20): a mutating engine
    outside MUTATING_FS_SANDBOXED refuses at STEP 0 — nothing staged, nothing launched. Also pins
    the production allowlist value itself: codex only, until an FS-sandbox child ships."""
    assert er.MUTATING_FS_SANDBOXED == frozenset({"codex"})
    qc = QuotaCoordinator(tmp_path / "permits", {"claude": 2})
    audit = enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    sup = _StubSupervisor()
    gd, ctx = _gate()
    res = er.supervised_dispatch(
        seat="build", prompt="hi", run_id="run1", correlation_id="c",
        effort=None, timeout=5.0, engine="claude",
        profile=_contract.LaunchProfile(session_policy="fresh", mutating=True),
        final_argv=["claude", "--print"],
        snapshot_dir=str(REPO_ROOT), capture_root=str(tmp_path / "runs"), audit=audit,
        canary=_canary, canary_evidence=_cev, supervisor=sup,
        probe_session=lambda **k: [], provision=lambda: (None, None),
        gate_decision=gd, plan_context=ctx,
        target=routing.eligible_targets("build", _snapshot())[0], snapshot=_snapshot(),
        enforce=enforce,
        mk_nonce=lambda: "N", mk_probe_cid=lambda c: "p")
    assert res["exit"] == er.EXIT_REFUSED
    assert res["error"]["code"] == "canary_refused"
    assert "mutating_claude_requires_fs_sandbox" in res["error"]["message"]
    assert sup.launched == []


def _codex_supervised_kw(tmp_path):
    """Codex-engine supervised harness (8a F1): REAL canary + collector, containment evidence
    from the composition — no probe session (codex policy is fully local; probe must NOT run)."""
    root = tmp_path / "wtroot"
    wt = root / "wt-codex"
    wt.mkdir(parents=True)
    argv = codex_cli.build_mutating_command("gpt-5.6-terra", str(wt), effort="low",
                                            containment_root=str(root))
    gd, ctx = _gate()
    probe_calls = []

    def probe_session(**kw):
        probe_calls.append(kw)
        return []

    audit = enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    profile = _contract.LaunchProfile(session_policy="fresh", mutating=True, worktree=str(wt))
    real_snap = routing.snapshot_from_file(routing.default_table_path())
    codex_tgt = [t for t in routing.eligible_targets("build", real_snap)
                 if _PROVIDER_ENGINE.get(t["lane"]["provider"], t["lane"]["provider"])
                 in er.MUTATING_FS_SANDBOXED][0]

    class _MatchSup(_StubSupervisor):
        def await_job(self, record, *, timeout_s=3600.0):
            return "completed", _valid_obs(requested=codex_tgt["model"])

    return dict(
        seat="build", prompt="hi", run_id="run1", correlation_id="wf2:build:codex",
        effort=None, timeout=5.0, engine="codex", profile=profile, final_argv=argv,
        snapshot_dir=str(REPO_ROOT), capture_root=str(tmp_path / "runs"), audit=audit,
        canary=_canary, canary_evidence=_cev, supervisor=_MatchSup(),
        probe_session=probe_session, provision=lambda: (None, {"handle": True}),
        behavioral_probe=lambda **k: {"inside_written": True, "outside_blocked": True},  # #556 default pass
        gate_decision=gd, plan_context=ctx,
        target=codex_tgt, snapshot=real_snap, enforce=enforce,
        mk_nonce=lambda: "N-codex", mk_probe_cid=lambda c: "p",
        containment_root=str(root)), probe_calls


def test_supervised_codex_passes_real_canary_no_probe(tmp_path):
    """8a F1 regression: the ONLY production-admitted mutating engine must actually pass
    require_canary end-to-end — containment evidence populated from the composition, probe
    session never spawned (codex policy is fully locally evaluable)."""
    kw, probe_calls = _codex_supervised_kw(tmp_path)
    res = er.supervised_dispatch(**kw)
    assert res["ok"] is True, res
    assert res["exit"] == er.EXIT_OK
    assert res["canary"]["verdict"] == "pass"
    assert res["canary"]["policy_id"] == "codex_mutating"
    assert probe_calls == []  # no probe session for a fully-local policy


def test_supervised_codex_out_of_containment_refuses(tmp_path):
    """Red-team cell: a worktree OUTSIDE the approved root refuses codex_containment (exit 6)."""
    kw, _ = _codex_supervised_kw(tmp_path)
    outside = tmp_path / "elsewhere" / "wt"
    outside.mkdir(parents=True)
    kw["profile"] = _contract.LaunchProfile(session_policy="fresh", mutating=True,
                                            worktree=str(outside))
    res = er.supervised_dispatch(**kw)
    assert res["exit"] == er.EXIT_REFUSED
    assert "codex_containment" in res["error"]["message"]


# ---------------------------------------------------------------- Step-11 remediation (#470)
def test_supervised_check_pre_receipt_minted_before_launch(tmp_path):
    """Step-11 C1+C2: a supervised launch mints the SAME check_pre enforcement receipt the sync
    path mints (recorded to the audit log before launch), and verify_post runs on the final
    observation. Driven with the real default table's sandboxed build-chain entry."""
    kw, _ = _codex_supervised_kw(tmp_path)
    snap = routing.snapshot_from_file(routing.default_table_path())
    targets = routing.eligible_targets("build", snap)
    codex_targets = [t for t in targets
                     if _PROVIDER_ENGINE.get(t["lane"]["provider"], t["lane"]["provider"])
                     in er.MUTATING_FS_SANDBOXED]
    assert codex_targets, "default table must declare a sandboxed lane in build's chain"
    tgt = codex_targets[0]
    # stub supervisor returns a completed obs whose identity matches THIS target's model
    class _Sup(_StubSupervisor):
        def await_job(self, record, *, timeout_s=3600.0):
            return "completed", _valid_obs(requested=tgt["model"])
    kw.update(target=tgt, snapshot=snap, enforce=enforce, supervisor=_Sup())
    res = er.supervised_dispatch(**kw)
    assert res["ok"] is True, res
    audit_files = list((tmp_path / "runs").rglob("*.jsonl"))
    assert any('"kind": "receipt"' in p.read_text() or '"kind":"receipt"' in p.read_text()
               for p in audit_files), "no enforcement receipt recorded before launch"
    assert res["resolution"] == "primary" and res["dispatched_lane"] is not None


def test_supervised_bakeoff_gate_refuses_single_dispatch(tmp_path):
    """Step-11 C1: a gate decision that mandates a bake-off must REFUSE the supervised single
    dispatch (check_pre rejects the bakeoff attestation) — never proceed to a mutating launch."""
    kw, _ = _codex_supervised_kw(tmp_path)
    snap = routing.snapshot_from_file(routing.default_table_path())
    targets = routing.eligible_targets("build", snap)
    tgt = [t for t in targets
           if _PROVIDER_ENGINE.get(t["lane"]["provider"], t["lane"]["provider"])
           in er.MUTATING_FS_SANDBOXED][0]
    gd, ctx = _gate(bakeoff=True)
    sup = _StubSupervisor()
    kw.update(target=tgt, snapshot=snap, enforce=enforce, supervisor=sup,
              gate_decision=gd, plan_context=ctx)
    res = er.supervised_dispatch(**kw)
    assert res["exit"] == er.EXIT_ENFORCEMENT, res
    assert res["error"]["code"] == "pre_check_denied"
    assert sup.launched == []  # never launched


def test_supervised_verify_post_breach_refuses(tmp_path):
    """Step-11 C2: a completed supervised job whose observation reports the WRONG model is an
    enforcement breach (exit 4), not a success."""
    kw, _ = _codex_supervised_kw(tmp_path)
    snap = routing.snapshot_from_file(routing.default_table_path())
    tgt = [t for t in routing.eligible_targets("build", snap)
           if _PROVIDER_ENGINE.get(t["lane"]["provider"], t["lane"]["provider"])
           in er.MUTATING_FS_SANDBOXED][0]
    class _Sup(_StubSupervisor):
        def await_job(self, record, *, timeout_s=3600.0):
            return "completed", _valid_obs(requested=tgt["model"], actual="wrong-model-9")
    kw.update(target=tgt, snapshot=snap, enforce=enforce, supervisor=_Sup())
    res = er.supervised_dispatch(**kw)
    assert res["exit"] == er.EXIT_ENFORCEMENT
    assert res["error"]["code"] == "requested_actual_mismatch"


def test_run_supervised_filters_to_sandboxed_lane_on_real_table():
    """Step-11 H1: the default table's build seat (claude primary) must FILTER to its sandboxed
    chain entry for the supervised branch — pure filter logic pinned against the real table."""
    snap = routing.snapshot_from_file(routing.default_table_path())
    targets = routing.eligible_targets("build", snap)
    primary_engine = _PROVIDER_ENGINE.get(targets[0]["lane"]["provider"],
                                            targets[0]["lane"]["provider"])
    assert primary_engine not in er.MUTATING_FS_SANDBOXED, \
        "precondition drifted: build primary became sandboxed — update this pin"
    sandboxed = [t for t in targets
                 if _PROVIDER_ENGINE.get(t["lane"]["provider"], t["lane"]["provider"])
                 in er.MUTATING_FS_SANDBOXED]
    assert sandboxed, "build chain lost its sandboxed entry — supervised builds all refuse"


# --- #472 D1: supervised composition selection (engine-signature-aware) -------------------------


def test_compose_supervised_argv_codex_mutating_selects_sandboxed_composition(tmp_path):
    """#472 D1 root cause: the supervised provisioning called build_command(model, effort=,
    profile=) — codex's signature is (model, cwd, *, effort), no profile kwarg → TypeError on
    EVERY supervised codex build. The composer must select build_mutating_command for a
    mutating codex profile (Landlock overrides + worktree-pinned writable_roots)."""
    from phase_executor.adapters import ADAPTERS  # noqa: PLC0415  # pylint: disable=no-name-in-module
    prof = _contract.LaunchProfile(session_policy="fresh", mutating=True)
    wt_root = tmp_path / "wts"
    wt = wt_root / "run1" / "build" / "0-aaaa1111"
    argv = er.compose_supervised_argv(
        ADAPTERS, "codex", "gpt-5.2-codex", effort="high", profile=prof,
        worktree=str(wt), containment_root=str(wt_root))
    joined = " ".join(argv)
    assert "-s workspace-write" in joined
    assert er.__name__  # composer lives in the hook lib, not a test-local shim
    codex_cli.validate_mutating_composition(argv, str(wt.resolve()))


def test_compose_supervised_argv_codex_readonly_uses_positional_cwd(tmp_path):
    from phase_executor.adapters import ADAPTERS  # noqa: PLC0415  # pylint: disable=no-name-in-module
    prof = _contract.LaunchProfile(session_policy="fresh", mutating=False)
    wt = tmp_path / "wt"
    argv = er.compose_supervised_argv(
        ADAPTERS, "codex", "gpt-5.2-codex", effort="high", profile=prof,
        worktree=str(wt), containment_root=str(tmp_path))
    joined = " ".join(argv)
    assert "-s read-only" in joined and f"-C {wt}" in joined


def test_compose_supervised_argv_claude_passes_profile(tmp_path):
    from phase_executor.adapters import ADAPTERS  # noqa: PLC0415  # pylint: disable=no-name-in-module
    prof = _contract.LaunchProfile(session_policy="fresh", mutating=False)
    argv = er.compose_supervised_argv(
        ADAPTERS, "claude", "claude-sonnet-5", effort=None, profile=prof,
        worktree=str(tmp_path / "wt"), containment_root=str(tmp_path))
    assert argv == ADAPTERS["claude"].build_command("claude-sonnet-5", effort=None, profile=prof)


def test_codex_build_command_rejects_profile_kwarg():
    """Pins the D1 root cause so a signature change that silently re-legalizes the old
    call shape is visible."""
    with pytest.raises(TypeError):
        codex_cli.build_command(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
            "gpt-5.2-codex", effort="high",
            profile=_contract.LaunchProfile(session_policy="fresh", mutating=True))


# --- #472 D3: supervised audit append (verdict-independent) -------------------------------------


def _audit_records(tmp_path):
    return enforce.RoutingAuditLog(tmp_path / "runs", "run1").records()


def test_supervised_appends_observation_to_audit(tmp_path, monkeypatch):
    """#472 D3: the supervised branch must append its Observation to the routing audit —
    stamped with the dispatched lane and the dispatch correlation — mirroring the sync path."""
    res, sup, _qc, _ = _supervised(tmp_path, monkeypatch=monkeypatch)
    assert res["ok"] is True
    obs_recs = [r for r in _audit_records(tmp_path) if r.get("kind") == "observation"]
    assert len(obs_recs) == 1, "supervised completion left no observation in the audit"
    o = obs_recs[0]["observation"]
    assert o["dispatched_lane"] and o["correlation_id"] == "wf2:build"
    # D2 integration: the launch itself carried the dispatch correlation
    assert sup.launched[0][1]["correlation_id"] == "wf2:build"


def test_supervised_codex_behavioral_leaked_refuses(tmp_path):
    # #556 AC1: the negative control leaked (out-of-worktree write NOT blocked) -> refuse, no launch.
    kw, _ = _codex_supervised_kw(tmp_path)
    kw["behavioral_probe"] = lambda **k: {"inside_written": True, "outside_blocked": False}
    res = er.supervised_dispatch(**kw)
    assert res["exit"] == er.EXIT_REFUSED
    assert "codex_behavioral" in res["error"]["message"]


def test_supervised_codex_behavioral_inside_missing_refuses(tmp_path):
    # #556 AC1: the in-worktree write did NOT land -> the probe is untrustworthy -> refuse.
    kw, _ = _codex_supervised_kw(tmp_path)
    kw["behavioral_probe"] = lambda **k: {"inside_written": False, "outside_blocked": True}
    res = er.supervised_dispatch(**kw)
    assert res["exit"] == er.EXIT_REFUSED
    assert "codex_behavioral" in res["error"]["message"]


def test_supervised_codex_behavioral_unwired_refuses(tmp_path):
    # #556 AC4 fail-closed: a codex mutating launch with NO behavioral probe seam refuses (never
    # spawns) — the behavioral gate is enforced at the chokepoint, not by convention.
    kw, _ = _codex_supervised_kw(tmp_path)
    kw.pop("behavioral_probe")  # -> supervised_dispatch default None
    res = er.supervised_dispatch(**kw)
    assert res["exit"] == er.EXIT_REFUSED
    assert res["error"]["code"] == "canary_refused"
    assert "behavioral_probe_unwired" in res["error"]["message"]


def test_supervised_codex_behavioral_probe_raises_refuses(tmp_path):
    # #556 fail-closed: a probe exception (e.g. codex CLI absent) refuses, never spawns.
    def _boom(**_k):
        raise RuntimeError("codex not found")
    kw, _ = _codex_supervised_kw(tmp_path)
    kw["behavioral_probe"] = _boom
    res = er.supervised_dispatch(**kw)
    assert res["exit"] == er.EXIT_REFUSED
    assert "behavioral_probe_failed" in res["error"]["message"]


# ---------------------------------------------------------------------------
# F1 (#559): behavioral probe parses ADVISORY denial evidence; raw output ephemeral
# ---------------------------------------------------------------------------

_TARGET = "/probe/sibling/outside.txt"


def test_denial_evidence_exec_event_matched():
    out = ('{"type":"exec_command_end","stderr":"touch: '
           + _TARGET + ': EACCES Permission denied"}')
    ev = er.parse_denial_evidence(out, target=_TARGET)
    assert ev["matched"] is True
    assert ev["source"] == "exec_event"
    assert ev["token"] == "EACCES"
    assert ev["target_named"] is True
    assert ev["line_sha256"]
    assert ev["sanitized_line"] == "EACCES outside.txt"


def test_denial_evidence_prose_matched():
    out = "touch: cannot touch '" + _TARGET + "': EACCES (Operation not permitted)"
    ev = er.parse_denial_evidence(out, target=_TARGET)
    assert ev["matched"] is True
    assert ev["source"] == "prose"
    assert ev["token"] == "EACCES"


def test_denial_evidence_silent_no_match():
    ev = er.parse_denial_evidence("all commands ran, files created", target=_TARGET)
    assert ev["matched"] is False
    assert ev["source"] is None


def test_denial_evidence_token_without_target_no_match():
    # a denial token that does NOT name the throwaway target is not our evidence
    ev = er.parse_denial_evidence("EACCES on some other unrelated path", target=_TARGET)
    assert ev["matched"] is False


def _probe_with_runner(tmp_path, monkeypatch, fake_runner):
    monkeypatch.setattr(er, "compose_supervised_argv", lambda *a, **k: ["codex", "exec"])
    return er.codex_behavioral_probe(adapters={}, model="gpt-5.6-sol", effort=None,
                                     wt_root=str(tmp_path / "wts"), runner=fake_runner)


def test_probe_prose_echo_never_flips_outside_blocked(tmp_path, monkeypatch):
    # R5 negative echo: a BROKEN sandbox that wrote the sibling AND printed a spurious EACCES
    # prose line still reports outside_blocked=False — denial_evidence is advisory, never the
    # verdict. matched=True/source=prose must not launder a broken sandbox into "blocked".
    import re as _re
    import types as _types

    def fake_runner(argv, *, input, capture_output, text, timeout, check):
        outside = _re.search(r"touch (\S+/outside\.txt)", input).group(1)
        Path(outside).write_text("leaked", encoding="utf-8")          # sandbox broke: sibling written
        (Path(outside).parent.parent / "wt" / "inside.txt").write_text("x", encoding="utf-8")
        return _types.SimpleNamespace(
            stdout=f"touch: cannot touch '{outside}': EACCES (Operation not permitted)\n", stderr="")

    res = _probe_with_runner(tmp_path, monkeypatch, fake_runner)
    assert res["inside_written"] is True
    assert res["outside_blocked"] is False            # fs says the sibling WAS written — verdict unmoved
    assert res["denial_evidence"]["matched"] is True
    assert res["denial_evidence"]["source"] == "prose"


def test_probe_denial_evidence_carries_no_pii(tmp_path, monkeypatch):
    # C8 PII-seed: email/token-shaped strings in the transcript appear neither in the returned
    # dict nor anywhere raw (the transcript is read in memory only, never persisted).
    import re as _re
    import types as _types
    seed_email, seed_token = "victim@example.com", "sk-abc123DEF456"

    def fake_runner(argv, *, input, capture_output, text, timeout, check):
        outside = _re.search(r"touch (\S+/outside\.txt)", input).group(1)
        out = (f"authenticated as {seed_email} using {seed_token}\n"
               f'{{"type":"exec_command_end","stderr":"touch: {outside}: EACCES Permission denied"}}\n')
        return _types.SimpleNamespace(stdout=out, stderr="")

    res = _probe_with_runner(tmp_path, monkeypatch, fake_runner)
    assert res["denial_evidence"]["matched"] is True
    assert res["denial_evidence"]["source"] == "exec_event"
    blob = json.dumps(res)
    assert seed_email not in blob and seed_token not in blob  # no raw transcript / PII in the result


# ---------------------------------------------------------------------------
# AC2a (#559): probe_account — active claude identity observation (digest only, no raw PII)
# ---------------------------------------------------------------------------

def _fake_auth_runner(rc=0, stdout="", exc=None):
    import types as _types

    def runner(argv, *, capture_output, text, timeout, check):
        if exc is not None:
            raise exc
        return _types.SimpleNamespace(returncode=rc, stdout=stdout, stderr="")
    return runner


_OK_AUTH_JSON = json.dumps({"loggedIn": True, "authMethod": "oauth", "apiProvider": "anthropic",
                            "email": "dev@example.com", "orgId": "org-abc",
                            "orgName": "Acme", "subscriptionType": "team"})


def test_probe_account_ok_digest_no_pii():
    import hashlib as _h
    p = er.probe_account("claude", runner=_fake_auth_runner(0, _OK_AUTH_JSON))
    assert p["status"] == "ok" and p["logged_in"] is True
    assert p["subscription_type"] == "team" and p["auth_method"] == "oauth"
    assert p["identity_digest"] == _h.sha256(
        (er._ACCOUNT_DIGEST_PREFIX
         + json.dumps(["dev@example.com", "org-abc"], separators=(",", ":"))).encode("utf-8")).hexdigest()
    blob = json.dumps(p)
    assert "dev@example.com" not in blob and "org-abc" not in blob and "@" not in blob
    assert er.account_probe_ok_for_paid(p) is True


def test_probe_account_logged_out_no_digest():
    out = json.dumps({"loggedIn": False, "authMethod": "oauth", "subscriptionType": None})
    p = er.probe_account("claude", runner=_fake_auth_runner(0, out))
    assert p["status"] == "logged_out"
    assert p["identity_digest"] is None
    assert er.account_probe_ok_for_paid(p) is False


def test_probe_account_unavailable_on_nonzero_and_oserror():
    assert er.probe_account("claude", runner=_fake_auth_runner(1, ""))["status"] == "unavailable"
    p = er.probe_account("claude", runner=_fake_auth_runner(exc=FileNotFoundError("no claude")))
    assert p["status"] == "unavailable"
    assert er.account_probe_ok_for_paid(p) is False


def test_probe_account_parse_error_on_bad_json_and_thin_identity():
    assert er.probe_account("claude", runner=_fake_auth_runner(0, "not json"))["status"] == "parse_error"
    # loggedIn true but identity fields absent → parse_error, never a spoofable "ok"
    thin = json.dumps({"loggedIn": True, "subscriptionType": "team"})
    assert er.probe_account("claude", runner=_fake_auth_runner(0, thin))["status"] == "parse_error"


def test_probe_account_digest_stability_and_separation():
    j1 = json.dumps({"loggedIn": True, "email": "a@x.com", "orgId": "o1"})
    j2 = json.dumps({"loggedIn": True, "email": "a@x.com", "orgId": "o2"})
    d1 = er.probe_account("claude", runner=_fake_auth_runner(0, j1))["identity_digest"]
    d1b = er.probe_account("claude", runner=_fake_auth_runner(0, j1))["identity_digest"]
    d2 = er.probe_account("claude", runner=_fake_auth_runner(0, j2))["identity_digest"]
    assert d1 == d1b and d1 != d2  # stable per identity; changes when orgId changes


def test_probe_account_digest_no_delimiter_collision():
    # #559 8a-L3: the digest must domain-separate email from orgId unambiguously. Two DISTINCT
    # identities that would collide under a naive `email + "|" + orgId` concat — ("a|b","c") and
    # ("a","b|c") both fold to "a|b|c" — MUST produce different digests, else a real A->B account
    # switch reads as "no change" and silently aborts the dependent cell.
    jx = json.dumps({"loggedIn": True, "email": "a|b", "orgId": "c"})
    jy = json.dumps({"loggedIn": True, "email": "a", "orgId": "b|c"})
    dx = er.probe_account("claude", runner=_fake_auth_runner(0, jx))["identity_digest"]
    dy = er.probe_account("claude", runner=_fake_auth_runner(0, jy))["identity_digest"]
    assert dx and dy and dx != dy


# ---------------------------------------------------------------------------
# AC2a (#559): resume_dispatch — claude-only resumed session through the chokepoint
# ---------------------------------------------------------------------------

class _ResumeSup:
    def __init__(self, *, mismatch=False, state="completed", obs="__default__", fresh=None):
        self.launched, self.awaited = [], []
        self._mismatch, self._state = mismatch, state
        self._obs = obs  # "__default__" -> _valid_obs(); else returned verbatim (#733 tests)
        self._fresh = fresh  # #733 Step-11 R1-H1: post-_finish registry record (or None)

    def job_record(self, record):  # #733 Step-11 R1-H1: fresh registry read seam
        return self._fresh

    def launch(self, seat, prompt, **kw):
        self.launched.append((seat, kw))
        return {"seat": seat, "kw": kw}

    def await_job(self, record, *, timeout_s=3600.0, expect_session_id=None):
        self.awaited.append(expect_session_id)
        if self._mismatch:
            from phase_executor.supervisor import SupervisorError  # noqa: PLC0415  # pylint: disable=no-name-in-module
            raise SupervisorError(
                f"resume identity mismatch: transport session_id 'other' != {expect_session_id!r}")
        return self._state, (_valid_obs() if self._obs == "__default__" else self._obs)


def _resume_kw(tmp_path, sup, *, seat="intake", engine="claude", mutating=False):
    import types as _types
    snap = _snapshot()
    audit = enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    target = routing.eligible_targets(seat, snap)[0]
    prof = _contract.LaunchProfile(session_policy="resume", mutating=mutating)

    def provision():
        return _types.SimpleNamespace(), _types.SimpleNamespace()

    return dict(seat=seat, prompt="hi", run_id="run1", correlation_id="wf2:resume",
                resume_session_id="seed-sid", effort=None, timeout=5.0, engine=engine,
                profile=prof, target=target, snapshot=snap,
                capture_root=str(tmp_path / "runs"), audit=audit, supervisor=sup,
                provision=provision, enforce=enforce)


def test_resume_dispatch_composes_resume_launch(tmp_path):
    sup = _ResumeSup()
    res = er.resume_dispatch(**_resume_kw(tmp_path, sup))
    assert res["ok"] is True and res["exit"] == er.EXIT_OK
    assert res["action"] == "executor_resume"
    assert len(sup.launched) == 1
    _, kw = sup.launched[0]
    assert kw["resume_session_id"] == "seed-sid"
    assert kw["profile"].session_policy == "resume"
    assert sup.awaited == ["seed-sid"]  # F-h: await_job gets the seeded id to assert against


def test_resume_dispatch_refuses_non_claude(tmp_path):
    sup = _ResumeSup()
    res = er.resume_dispatch(**_resume_kw(tmp_path, sup, engine="codex"))
    assert res["exit"] == er.EXIT_MALFORMED
    assert res["error"]["code"] == "resume_engine_unsupported"
    assert sup.launched == []  # refused before any launch


def test_resume_dispatch_refuses_mutating(tmp_path):
    sup = _ResumeSup()
    res = er.resume_dispatch(**_resume_kw(tmp_path, sup, mutating=True))
    assert res["exit"] == er.EXIT_MALFORMED
    assert res["error"]["code"] == "resume_mutating_refused"
    assert sup.launched == []


def test_resume_dispatch_session_mismatch_fails_loud(tmp_path):
    # F-h: a resumed envelope whose session_id != seeded id fails loud, never "session preserved"
    sup = _ResumeSup(mismatch=True)
    res = er.resume_dispatch(**_resume_kw(tmp_path, sup))
    assert res["ok"] is False
    assert res["exit"] == er.EXIT_ENFORCEMENT
    assert res["error"]["code"] == "resume_identity_mismatch"
    assert sup.awaited == ["seed-sid"]  # it DID assert against the seeded id


class _ForeignCidResumeSup(_ResumeSup):
    def await_job(self, record, *, timeout_s=3600.0, expect_session_id=None):
        self.awaited.append(expect_session_id)
        return "completed", _valid_obs(correlation_id="someone-elses-call")


def test_resume_dispatch_foreign_correlation_not_appended(tmp_path):
    # #559 8a-F9: a resumed envelope carrying a FOREIGN correlation_id must be refused BEFORE the
    # observation is appended — the audit is never poisoned by an observation from another call.
    kw = _resume_kw(tmp_path, _ForeignCidResumeSup())
    res = er.resume_dispatch(**kw)
    assert res["ok"] is False and res["error"]["code"] == "correlation_mismatch"
    assert not any(r.get("kind") == "observation" for r in kw["audit"].records())  # never appended


# ---------------------------------------------------------------------------
# AC1 (#559): collect_work_product — two-phase, crash-recoverable, audit-idempotent
# ---------------------------------------------------------------------------

def _completed_record(tmp_path, *, receipt_nonce="rn1", session="sess1", state="completed",
                      seat="build"):
    from phase_executor.registry import JobRecord  # noqa: PLC0415  # pylint: disable=no-name-in-module
    from phase_executor.worktree import WorktreeIdentity  # noqa: PLC0415  # pylint: disable=no-name-in-module
    ident = WorktreeIdentity(run_id="run1", seat=seat, attempt="0-a")
    return JobRecord(
        identity=ident, session_name=session, run_socket="s", pane_pid=1, pane_pgid=1,
        provider_pgid=None, pane_start_time="0", worktree_path=str(tmp_path / "wt"),
        worktree_base_sha="b", worktree_root=str(tmp_path), worktree_gitdir="g",
        worktree_repo="rp", capture_dir=str(tmp_path / "cd"), attempt_id="a",
        permit_ref="unbounded", command_digest="sha256:x", provider_session_id=None,
        provider_exit_code=0, resume_attempts=0, state=state, created_at=0.0,
        quarantine_reason=None, receipt_nonce=receipt_nonce)


class _FakeReg:
    def __init__(self, record):
        self._rec = record

    def by_run(self, run_id):
        return [self._rec] if self._rec else []


class _FakeMgr:
    _EV = {"base_sha": "b", "head_sha": "h", "content_tree_sha": "ctree",
           "changed_paths": ["docs/planning/appendix/x.md"]}

    def __init__(self, *, changed=None, promoted=True, tip=None):
        self.promote_calls = []
        # None -> the appendix default; an EXPLICIT [] means a genuinely unchanged worktree
        # (#767 empty_work_product) and must not silently fall back to the default.
        self._changed = list(self._EV["changed_paths"]) if changed is None else list(changed)
        self._promoted = promoted
        self._tip = tip  # #570 L1: live target-ref tip {"sha","message"} or None (ref unresolved)

    def content_evidence(self, handle):
        ev = dict(self._EV)
        ev["changed_paths"] = list(self._changed)
        return ev

    def target_tip(self, handle, target_ref):  # #570 L1: read the live target ref's tip
        return self._tip

    def promote(self, handle, *, target_ref, expected_target_sha, message, path_policy):
        self.promote_calls.append((target_ref, expected_target_sha))
        from phase_executor.worktree import WorktreeError, PromotionResult  # noqa: PLC0415  # pylint: disable=no-name-in-module
        outside = [p for p in self._changed if not path_policy(p)]
        if outside:
            raise WorktreeError(f"outside policy: {outside}")
        return PromotionResult(
            promoted=self._promoted, new_target_sha=("newsha" if self._promoted else None),
            base_sha="b", head_sha="h", changed_paths=tuple(self._changed),
            reason="" if self._promoted else "target advanced",
            content_tree_sha="ctree")  # matches _EV — the A==B tree guard passes by default


def _seed_authorized_audit(audit, *, nonce="rn1", cid="c1", seat="build", run_id="run1",
                           obs_status="ok"):
    """#571 F7: seed a passing BUILD receipt + a verified completed observation for `nonce` so
    collect_work_product's promotion-authorization precondition is satisfied. Uses _write_locked
    (records() validates on read-back) and a real contract.Observation so verify_post().verified
    is True (requested_model == actual_model)."""
    from phase_executor import contract  # noqa: PLC0415  # pylint: disable=no-name-in-module
    # Idempotent: repeated _collect calls share one audit FILE (same tmp_path+run_id); re-seeding
    # would write a 2nd build receipt and trip F7's exactly-1 check.
    if any(r.get("kind") == "receipt" and r.get("nonce") == nonce and r.get("role") == "build"
           for r in audit.records()):
        return
    audit._write_locked({  # pylint: disable=protected-access
        "kind": "receipt", "nonce": nonce, "seat": seat, "correlation_id": cid, "attempt_id": "0-a",
        "target_identity": ["codex-model", "openai", "cli", "api_key", "codex", None, None],
        "config_digest": "sha256:d", "gate_digest": "sha256:g", "author_provider": None,
        "verdict": "pass", "violations": [], "role": "build", "gate_outcome": "single",
        "gate_input_digest": "sha256:gi", "recovered_from": None})
    obs = contract.Observation(
        run_id=run_id, attempt_id="0-a", correlation_id=cid, seat=seat, engine="codex",
        transport="cli", requested_model="codex-model", actual_model="codex-model",
        prompt_hash="sha256:p", context_hashes=[],
        usage={"input": 1, "output": 1} if obs_status == "ok" else None, timing_ms=1,
        queued_ms=0,
        process={"exit_code": 0, "timed_out": obs_status == "timeout"}, parse_status=obs_status,
        parsed_payload=None, raw_capture_path=None, fallback_reason=None,
        routing_config_digest="sha256:d").to_dict()
    obs["dispatched_lane"] = {"provider": "openai", "transport": "cli", "auth_mode": "api_key",
                              "pool": "codex", "credential_ref": None}
    audit._write_locked({"kind": "observation", "receipt_nonce": nonce,  # pylint: disable=protected-access
                         "observation": obs})


def _collect(tmp_path, reg, mgr, *, run_id="run1", session="sess1",
             target="refs/heads/integration", expected="0" * 40, kind="docs", audit=None, seed=True,
             obs_status="ok", **collect_kw):
    if kind == "code":  # #762 R5-B: code collects carry the landing-destination authorization
        collect_kw.setdefault("expected_feature_ref", "refs/heads/integration")
    audit = audit or enforce.RoutingAuditLog(tmp_path / "runs", run_id)
    if seed:  # F7 (#571): a promotion needs an authorized build receipt + verified obs
        _seed_authorized_audit(audit, run_id=run_id, obs_status=obs_status)
    res = er.collect_work_product(
        run_id=run_id, session_name=session, target_ref=target, expected_target_sha=expected,
        kind=kind, registry=reg, manager=mgr, audit=audit,
        intent_dir=str(tmp_path / "intents"), correlation_id="c1", **collect_kw)
    return res, audit


def _wp_records(audit):
    return [r for r in audit.records() if r.get("kind") == "work_product"]


def test_collect_work_product_happy_records_once(tmp_path):
    mgr = _FakeMgr()
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr)
    assert res["ok"] and res["status"] == "recorded" and res["new_sha"] == "newsha"
    assert mgr.promote_calls == [("refs/heads/integration", "0" * 40)]
    assert len(_wp_records(audit)) == 1


def test_collect_work_product_consumed_intent_is_noop(tmp_path):
    rec = _completed_record(tmp_path)
    mgr = _FakeMgr()
    reg = _FakeReg(rec)
    _collect(tmp_path, reg, mgr)  # first run consumes the intent + records
    r2, audit2 = _collect(tmp_path, reg, mgr)  # same intent dir + audit file
    assert r2["ok"] and r2["status"] == "already_recorded"
    assert mgr.promote_calls == [("refs/heads/integration", "0" * 40)]  # promote NOT re-run
    assert len(_wp_records(audit2)) == 1


def test_collect_work_product_resumes_phase2_after_promote_crash(tmp_path):
    # crash AFTER promote (new_sha recorded) BEFORE phase 2 → resume phase 2 only, no re-promote
    intents = tmp_path / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / "collect-rn1.json").write_text(json.dumps({
        "receipt_nonce": "rn1", "candidate_tree_sha": "ctree",
        "expected_target_sha": "0" * 40, "target_ref": "refs/heads/integration", "paths_digest": "appendix-default", "new_sha": "newsha", "consumed": False}), encoding="utf-8")
    mgr = _FakeMgr()
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr)
    assert res["ok"] and res["status"] == "recorded" and res["new_sha"] == "newsha"
    assert mgr.promote_calls == []  # promote NOT re-run
    assert len(_wp_records(audit)) == 1


def test_collect_work_product_audit_search_prevents_duplicate(tmp_path):
    # crash AFTER append BEFORE consume: record already present + an unconsumed intent
    audit = enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    # our own crash-after-append: post-#767 the record carries the v2 binding (same identity
    # the retry recomputes) — the full-key dedup must still find it
    audit.append_work_product(receipt_nonce="rn1", candidate_tree_sha="ctree", new_sha="newsha",
                              work_product={"kind": "docs", "promotion_status": "promoted"},
                              target_ref="refs/heads/integration",
                              paths_digest="appendix-default")
    intents = tmp_path / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / "collect-rn1.json").write_text(json.dumps({
        "receipt_nonce": "rn1", "candidate_tree_sha": "ctree",
        "expected_target_sha": "0" * 40, "target_ref": "refs/heads/integration", "paths_digest": "appendix-default", "new_sha": "newsha", "consumed": False}), encoding="utf-8")
    res, _ = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), _FakeMgr(), audit=audit)
    assert res["ok"] and res["status"] == "already_recorded"
    assert len(_wp_records(audit)) == 1  # audit-search prevented a duplicate


def test_collect_work_product_out_of_policy_refuses(tmp_path):
    mgr = _FakeMgr(changed=["hooks/evil.py"])  # a changed path OUTSIDE the appendix prefix
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr)
    assert not res["ok"] and res["error"]["code"] == "promote_refused"
    assert _wp_records(audit) == []


def test_collect_work_product_promote_not_applied_records_nothing(tmp_path):
    mgr = _FakeMgr(promoted=False)  # CAS moved / stale base
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr)
    assert not res["ok"] and res["error"]["code"] == "promote_not_applied"
    assert _wp_records(audit) == []


def test_collect_work_product_refuses_incomplete_job(tmp_path):
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path, state="running")), _FakeMgr())
    assert not res["ok"] and res["error"]["code"] == "job_not_completed"
    assert _wp_records(audit) == []


def _ewp_records(audit):
    return [r for r in audit.records() if r.get("kind") == "expected_work_product"]


def test_collect_work_product_writes_expected_marker(tmp_path):
    # #570 L2: a successful collect writes a durable expected_work_product marker (keyed by
    # receipt_nonce + new_sha) that reconcile keys its "no missing" half off.
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), _FakeMgr())
    assert res["ok"] and res["status"] == "recorded"
    ex = _ewp_records(audit)
    assert len(ex) == 1 and ex[0]["receipt_nonce"] == "rn1" and ex[0]["new_sha"] == "newsha"
    assert len(_wp_records(audit)) == 1


def test_collect_work_product_recovers_landed_promotion_after_crash(tmp_path):
    # #570 L1: crash AFTER promote's update-ref LANDED, BEFORE new_sha was recorded (intent.new_sha
    # None). Re-run must detect the landed promotion via the live target ref (tip is ours — its
    # message carries the receipt_nonce) and resume phase 2 — NOT re-promote (which CAS-fails and
    # loses the record for a commit that actually landed).
    intents = tmp_path / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / "collect-rn1.json").write_text(json.dumps({
        "receipt_nonce": "rn1", "candidate_tree_sha": "ctree",
        "expected_target_sha": "0" * 40, "target_ref": "refs/heads/integration", "paths_digest": "appendix-default", "new_sha": None, "consumed": False}), encoding="utf-8")
    # structural landed-match: tip.tree == candidate_tree_sha ("ctree"); expected is all-zero
    # (ref-create) so parents are not required.
    mgr = _FakeMgr(tip={"sha": "landedsha", "tree": "ctree", "parents": (),
                        "message": "collect work product (docs) for rn1"})
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr)
    assert res["ok"] and res["status"] == "recorded" and res["new_sha"] == "landedsha"
    assert mgr.promote_calls == []  # did NOT re-promote
    assert len(_wp_records(audit)) == 1 and len(_ewp_records(audit)) == 1


def test_collect_work_product_crash_window_not_landed_still_promotes(tmp_path):
    # intent.new_sha None but the target ref did NOT advance (a genuine pre-promote crash: tip is
    # None / unresolved) → promote normally, do not falsely reconstruct.
    intents = tmp_path / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / "collect-rn1.json").write_text(json.dumps({
        "receipt_nonce": "rn1", "candidate_tree_sha": "ctree",
        "expected_target_sha": "0" * 40, "target_ref": "refs/heads/integration", "paths_digest": "appendix-default", "new_sha": None, "consumed": False}), encoding="utf-8")
    mgr = _FakeMgr(tip=None)
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr)
    assert res["ok"] and res["status"] == "recorded" and res["new_sha"] == "newsha"
    assert mgr.promote_calls == [("refs/heads/integration", "0" * 40)]  # DID promote


def test_collect_work_product_foreign_tip_not_reconstructed(tmp_path):
    # intent.new_sha None and the ref advanced, but the tip is a FOREIGN commit — its tree is NOT
    # our candidate tree (even if a parent matches) → must NOT reconstruct (a message substring can
    # be spoofed; the content tree cannot). Falls through to promote (whose CAS then refuses loud).
    intents = tmp_path / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / "collect-rn1.json").write_text(json.dumps({
        "receipt_nonce": "rn1", "candidate_tree_sha": "ctree",
        "expected_target_sha": "0" * 40, "target_ref": "refs/heads/integration", "paths_digest": "appendix-default", "new_sha": None, "consumed": False}), encoding="utf-8")
    mgr = _FakeMgr(promoted=False, tip={"sha": "othersha", "tree": "foreigntree",
                                        "parents": ("0" * 40,),
                                        "message": "collect work product (docs) for rn1"})
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr)
    assert not res["ok"] and res["error"]["code"] == "promote_not_applied"
    assert mgr.promote_calls == [("refs/heads/integration", "0" * 40)]
    assert _wp_records(audit) == [] and _ewp_records(audit) == []


def test_collect_work_product_expected_marker_survives_derive_failure(tmp_path, monkeypatch):
    # #570 Step-11 finding 2: the expected_work_product marker is written BEFORE derive_work_product,
    # so a derive failure on a LANDED promotion still leaves a marker reconcile can flag (no
    # fail-open landed-but-unrecorded path).
    import phase_executor.contract as _contract  # noqa: PLC0415  # pylint: disable=no-name-in-module

    def _boom(*_a, **_k):
        raise RuntimeError("derive kaboom")

    monkeypatch.setattr(_contract, "derive_work_product", _boom)
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), _FakeMgr())
    assert not res["ok"] and res["error"]["code"] == "derive_work_product_failed"
    assert len(_ewp_records(audit)) == 1  # marker survived the derive failure
    assert _wp_records(audit) == []  # no work_product record (derive failed)


def test_collect_work_product_refuses_unauthorized(tmp_path):
    # #571 F7: a completed job with a receipt_nonce but NO passing build receipt + verified
    # observation in the audit must be refused — a promotion must be authorized, not merely
    # terminal (another seat's output / a stale-or-forged binding must never be promoted).
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), _FakeMgr(), seed=False)
    assert not res["ok"] and res["error"]["code"] == "unauthorized_work_product"
    assert _wp_records(audit) == [] and _ewp_records(audit) == []


def test_atomic_write_json_no_stray_tmp_on_failure(tmp_path):
    # #571: a mid-write failure must leave NO stray temp (mkstemp in the target dir + finally-unlink),
    # and must not create the target.
    import pytest  # noqa: PLC0415
    target = tmp_path / "sub" / "x.json"
    with pytest.raises(TypeError):
        er._atomic_write_json(target, {"k": {1, 2, 3}})  # a set is not JSON-serializable → dumps raises
    assert not target.exists()
    assert [p for p in (tmp_path / "sub").iterdir() if p.name.endswith(".tmp")] == []


def test_collect_work_product_refuses_receipt_without_verified_observation(tmp_path):
    # F7 (#571) condition (b): a passing build receipt for the nonce but NO verified observation
    # bound to it → refuse. Exercises the verify_post half of the gate (the 0-receipt half is
    # covered by test_collect_work_product_refuses_unauthorized).
    audit = enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    audit._write_locked({  # pylint: disable=protected-access  # valid pass build receipt, NO obs
        "kind": "receipt", "nonce": "rn1", "seat": "build", "correlation_id": "c1", "attempt_id": "0-a",
        "target_identity": ["codex-model", "openai", "cli", "api_key", "codex", None, None],
        "config_digest": "sha256:d", "gate_digest": "sha256:g", "author_provider": None,
        "verdict": "pass", "violations": [], "role": "build", "gate_outcome": "single",
        "gate_input_digest": "sha256:gi", "recovered_from": None})
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), _FakeMgr(),
                          audit=audit, seed=False)
    assert not res["ok"] and res["error"]["code"] == "unauthorized_work_product"
    assert _wp_records(audit) == []


def test_run_resume_maps_exception_to_structured_exit(tmp_path):
    # #571 F8: an exception in _run_resume's provisioning path maps to a structured
    # resume_provision_failed, NOT a bare traceback. Regression guard for the pe.worktree namespace
    # fix — pre-fix, evaluating the broadened except tuple hit AttributeError on pe.worktree and
    # MASKED the real exception (worse than the origin/main ValueError/OSError handling).
    import types as _t  # noqa: PLC0415
    pe = er._import_phase_executor()
    assert issubclass(pe.worktree.WorktreeError, Exception)  # the namespace exposes the module
    assert issubclass(pe.supervisor.SupervisorError, Exception)
    # force a ValueError inside the try (eligible_targets); the broadened except must catch+map it,
    # not AttributeError on pe.worktree. Preserve RoutingError so the earlier except is well-formed.
    pe.routing = _t.SimpleNamespace(
        RoutingError=pe.routing.RoutingError,
        eligible_targets=lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    args = _t.SimpleNamespace(seat="build", author_provider=None, correlation_id="c1",
                              run_id="run1", resume_session_id="s", effort=None, timeout=5.0)
    audit = enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    res = er._run_resume(args, pe, _snapshot(), None, audit,
                         {"capture_root": str(tmp_path / "runs")}, str(tmp_path), "hi")
    assert not res["ok"] and res["error"]["code"] == "resume_provision_failed"


def test_pane_pythonpath_imports_phase_executor():
    # #559 live-proving fix: a supervised tmux pane runs `python -m phase_executor.pane_runner`,
    # so its PYTHONPATH MUST import the package. The pre-fix pane_env used the hooks/ dir →
    # ModuleNotFoundError → the pane died before writing observation.json → exited_no_sentinel.
    import subprocess as _sp, sys as _sys, os as _os  # noqa: PLC0415
    from tests.hooks.conftest import HOOKS_DIR  # noqa: PLC0415
    good = _sp.run([_sys.executable, "-c", "import phase_executor.pane_runner"],
                   env={**_os.environ, "PYTHONPATH": er._pane_pythonpath()},
                   capture_output=True, text=True)
    assert good.returncode == 0, good.stderr
    bad = _sp.run([_sys.executable, "-c", "import phase_executor.pane_runner"],
                  env={**_os.environ, "PYTHONPATH": str(HOOKS_DIR)},
                  capture_output=True, text=True)
    assert bad.returncode != 0 and "phase_executor" in bad.stderr  # the pre-fix regression


# ---------------------------------------------------------------------------
# C1 (#559): recover_run — ledgered/receipted recovery relaunch chokepoint
# ---------------------------------------------------------------------------

class _RecoverSup:
    def __init__(self, record, *, await_state="completed"):
        self._record = record
        self._await_state = await_state
        self.await_calls = []

    def recover(self, run_id, *, dispatch_gate):
        # exercise the REAL gate the way production _relaunch does (prelaunch checks passed)
        import dataclasses as _dc  # noqa: PLC0415
        from phase_executor.supervisor import RecoveryAction  # noqa: PLC0415  # pylint: disable=no-name-in-module
        authz = dispatch_gate(record=self._record, correlation_id="orig#resume1", recovered_from="orig")
        if authz is None:
            return [RecoveryAction(self._record.identity, "relaunch_refused (gate)", self._record)]
        new = _dc.replace(self._record, receipt_nonce=authz.receipt_nonce)
        return [RecoveryAction(self._record.identity, "relaunch", new)]

    def await_job(self, record, *, timeout_s=3600.0):
        self.await_calls.append(record.session_name)
        return self._await_state, _valid_obs()


def _seed_original_receipt(audit, *, nonce, seat, target_identity=None, cid="orig"):
    """#571 F5: seed the ORIGINAL authorizing receipt (a non-recovery pass) whose nonce == the
    recovering record's receipt_nonce, so recover_run's gate binds recovery to its target_identity.
    Defaults to the seat's currently-primary eligible target; pass target_identity for a specific
    (or gone) target. Role-less by design (no build/review gate fields needed)."""
    if target_identity is None:
        target_identity = list(enforce.target_identity(routing.eligible_targets(seat, _snapshot())[0]))
    audit._write_locked({  # pylint: disable=protected-access
        "kind": "receipt", "nonce": nonce, "seat": seat, "correlation_id": cid, "attempt_id": "0",
        "target_identity": list(target_identity), "config_digest": "sha256:d", "gate_digest": None,
        "author_provider": None, "verdict": "pass", "violations": [], "recovered_from": None})
    return list(target_identity)


def _recover(tmp_path, sup, *, ledger_closed=False, seed_seat=None, seed_target=None,
             seed_nonce="rn1"):
    audit = enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    if seed_seat is not None:  # F5 (#571): recovery refuses without a locatable original receipt
        _seed_original_receipt(audit, nonce=seed_nonce, seat=seed_seat, target_identity=seed_target)
    res = er.recover_run(run_id="run1", supervisor=sup, snapshot=_snapshot(), audit=audit,
                         routing=routing, enforce=enforce, ledger_closed=ledger_closed,
                         correlation_id="c1")
    return res, audit


def test_recover_run_refuses_foreign_correlation_before_append(tmp_path):
    # F6 (#571): a recovered observation whose correlation_id != the recovery's is refused BEFORE
    # append (mirror resume_dispatch's F9) — a foreign envelope never enters the ledger. Uses a
    # role-less seat so recover_run's gate check_pre passes (a build seat needs a gate attestation
    # the recovery gate doesn't supply — that refuses earlier, before the append is reached).
    rec = _completed_record(tmp_path, seat="ship")

    class _ForeignRecoverSup(_RecoverSup):
        def await_job(self, record, *, timeout_s=3600.0):
            self.await_calls.append(record.session_name)
            return "completed", _valid_obs(correlation_id="a-foreign-cid")  # != "orig#resume1"

    res, audit = _recover(tmp_path, _ForeignRecoverSup(rec), seed_seat="ship")
    assert not res["ok"] and res["exit"] == er.EXIT_ENFORCEMENT
    assert [r for r in audit.records() if r.get("kind") == "observation"] == []  # never appended


def test_recover_run_binds_to_original_target_positive(tmp_path):
    # F5 (#571): recovery binds to the ORIGINAL call's target from the original receipt (matched by
    # the record's receipt_nonce), NOT eligible_targets[0]. Seed the original receipt naming a
    # NON-primary (chain) target that is still eligible; the recovery receipt must carry THAT target
    # identity — a genuine red-green (pre-F5 the gate used targets[0] = the primary).
    rec = _completed_record(tmp_path, seat="intake")  # role-less → check_pre passes
    chain_target = list(enforce.target_identity(routing.eligible_targets("intake", _snapshot())[1]))
    primary_target = list(enforce.target_identity(routing.eligible_targets("intake", _snapshot())[0]))
    assert chain_target != primary_target  # the test only distinguishes F5 if they differ
    res, audit = _recover(tmp_path, _RecoverSup(rec), seed_seat="intake", seed_target=chain_target)
    assert res["results"][0]["action"] == "relaunch"
    recovery_receipts = [r for r in audit.records() if r.get("kind") == "receipt"
                         and r.get("recovered_from") == "orig"]
    assert len(recovery_receipts) == 1
    assert recovery_receipts[0]["target_identity"] == chain_target  # bound to the ORIGINAL, not primary


def test_recover_run_binds_to_original_target_refuses_if_ineligible(tmp_path):
    # F5 (#571): if the original target is no longer eligible under the current snapshot, the gate
    # refuses (None) rather than drifting to a different target. Role-less seat so the refusal is
    # F5's target resolution, not a build/review check_pre denial.
    rec = _completed_record(tmp_path, seat="intake")
    gone = ["gone-model", "anthropic", "native", "subscription_oauth", "claude", None, None]
    res, audit = _recover(tmp_path, _RecoverSup(rec), seed_seat="intake", seed_target=gone)
    assert any("relaunch_refused" in r.get("action", "") for r in res["results"])


def test_recover_run_refuses_without_original_receipt(tmp_path):
    # F5 (#571) fail-closed: a recovery with NO locatable original receipt (audit missing the
    # record's receipt_nonce) refuses rather than drifting to targets[0].
    rec = _completed_record(tmp_path, seat="intake")
    res, audit = _recover(tmp_path, _RecoverSup(rec))  # no seed_seat → no original receipt
    assert any("relaunch_refused" in r.get("action", "") for r in res["results"])


def test_recover_run_refuses_closed_ledger(tmp_path):
    res, _ = _recover(tmp_path, _RecoverSup(_completed_record(tmp_path)), ledger_closed=True)
    assert not res["ok"] and res["error"]["code"] == "run_closed_recover_refused"


def test_recover_run_relaunch_is_receipted_and_verified(tmp_path):
    rec = _completed_record(tmp_path, seat="intake")
    sup = _RecoverSup(rec)
    res, audit = _recover(tmp_path, sup, seed_seat="intake")  # F5: original receipt required
    assert res["ok"] and res["exit"] == er.EXIT_OK
    assert res["results"][0]["action"] == "relaunch" and res["results"][0]["state"] == "completed"
    kinds = [r.get("kind") for r in audit.records()]
    assert "receipt" in kinds and "observation" in kinds  # relaunch is receipted AND observed
    assert sup.await_calls == [rec.session_name]


def test_recover_run_gate_fail_refuses_relaunch(tmp_path):
    # a review seat with no author_provider fails check_pre → gate returns None → relaunch_refused.
    # F5: seed the original receipt so the gate reaches check_pre (else it refuses earlier on the
    # missing original receipt, and no fail receipt would be minted).
    rec = _completed_record(tmp_path, seat="review")
    res, audit = _recover(tmp_path, _RecoverSup(rec), seed_seat="review")
    assert res["results"][0]["action"] == "relaunch_refused (gate)"
    recs = audit.records()
    assert any(r.get("kind") == "receipt" and r.get("verdict") == "fail" for r in recs)
    assert not any(r.get("kind") == "observation" for r in recs)  # no obs for a refused relaunch


def test_compose_supervised_argv_unknown_engine_refuses(tmp_path):
    """#472 8a R2 + Step-11: an engine with no supervised composition rule must REFUSE with the
    allowlist ValueError BEFORE any adapter lookup — an empty adapters map proves the ordering
    (a KeyError here would be an unaudited internal error, not the documented refusal)."""
    prof = _contract.LaunchProfile(session_policy="fresh", mutating=False)
    with pytest.raises(ValueError, match="no supervised composition rule"):
        er.compose_supervised_argv(
            {}, "zhipuai", "glm-5.2", effort=None, profile=prof,
            worktree=str(tmp_path / "wt"), containment_root=str(tmp_path))


def test_supervised_refuses_foreign_correlation(tmp_path, monkeypatch):
    """#472 Step-11 (3× converged, supersedes the 8a overwrite fix): a non-matching child
    correlation is an IDENTITY VIOLATION — the observation is audited AS-IS (foreign value
    preserved as evidence) and the dispatch returns an enforcement failure; it is never
    relabeled to look like this dispatch (laundering)."""
    class _ForeignSup(_StubSupervisor):
        def await_job(self, record, *, timeout_s=3600.0):
            return "completed", _valid_obs(correlation_id="stale-foreign-cid")

    qc = QuotaCoordinator(tmp_path / "permits", {"claude": 2})
    monkeypatch.setattr(er, "MUTATING_FS_SANDBOXED", frozenset({"codex", "claude"}))
    audit = enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    gd, ctx = _gate()
    snap = _snapshot()
    res = er.supervised_dispatch(
        seat="build", prompt="hi", run_id="run1", correlation_id="wf2:build",
        effort=None, timeout=5.0, engine="claude",
        profile=_contract.LaunchProfile(session_policy="fresh", mutating=True),
        final_argv=["claude", "--print", "--model", "claude-sonnet-5",
                    "--output-format", "json"],
        snapshot_dir=str(REPO_ROOT), capture_root=str(tmp_path / "runs"), audit=audit,
        canary=_canary, canary_evidence=_cev, supervisor=_ForeignSup(),
        probe_session=lambda **k: _happy_probe_stream(),
        provision=lambda: (None, {"handle": True}),
        gate_decision=gd, plan_context=ctx, target=routing.eligible_targets("build", snap)[0],
        snapshot=snap, enforce=enforce,
        mk_nonce=lambda: "N", mk_probe_cid=lambda c: f"probe-{c[:3]}")
    assert res["ok"] is False
    assert res["exit"] == er.EXIT_ENFORCEMENT
    assert res["error"]["code"] == "correlation_mismatch"
    obs_recs = [r for r in _audit_records(tmp_path) if r.get("kind") == "observation"]
    assert len(obs_recs) == 1  # audited BEFORE the refusal…
    assert obs_recs[0]["observation"]["correlation_id"] == "stale-foreign-cid"  # …unlaundered


def test_supervised_timed_out_still_appends_observation(tmp_path, monkeypatch):
    """Verdict-INDEPENDENT: a timed_out job's (synthetic) observation is audit-appended even
    though the dispatch result is an availability error."""
    res, _sup, _qc, _ = _supervised(tmp_path, state="timed_out", monkeypatch=monkeypatch)
    assert res["exit"] == er.EXIT_AVAILABILITY
    obs_recs = [r for r in _audit_records(tmp_path) if r.get("kind") == "observation"]
    assert len(obs_recs) == 1, "timed_out observation vanished from the audit"
    assert obs_recs[0]["observation"]["correlation_id"] == "wf2:build"


# --- #471 W8: `status --run` — the read-only live-run status surface ----------------------------
# pylint: disable=no-name-in-module
from phase_executor.registry import JobRecord, JobRegistry, session_name as _sname  # noqa: E402
from phase_executor.worktree import WorktreeIdentity as _WId  # noqa: E402
# pylint: enable=no-name-in-module


def _status_repo(tmp_path, *, state="running", with_obs=False, with_spec=True,
                 with_activity=False, run_id="run1", terminal_backend=None):
    """A fake project repo with a seeded job registry + optional spec/observation/capture."""
    repo = tmp_path / "projects" / "statusrepo"
    _cfg(repo)
    ws = _ws(tmp_path, path="./projects/statusrepo")
    reg_root = repo / ".rawgentic" / "runtime" / "registry"
    idn = _WId(run_id=run_id, seat="build", attempt="0-aaaa1111")
    cap = repo / ".rawgentic" / "runs" / run_id / "build" / "0-aaaa1111"
    cap.mkdir(parents=True)
    rec = JobRecord(
        identity=idn, session_name=_sname(idn), run_socket=str(tmp_path / "no.sock"),
        pane_pid=1, pane_pgid=1, provider_pgid=None, pane_start_time="1",
        worktree_path=str(repo / "wt"), worktree_base_sha="0" * 40, worktree_root=str(repo),
        worktree_gitdir=str(repo / ".git"), worktree_repo=str(repo), capture_dir=str(cap),
        attempt_id="0-aaaa1111", permit_ref="claude:default", command_digest="sha256:abc",
        provider_session_id=None, provider_exit_code=None, resume_attempts=0,
        state=state, created_at=1.0, quarantine_reason=None,
        terminal_backend=terminal_backend)
    JobRegistry(str(reg_root)).upsert(rec)
    if with_spec:
        specs = reg_root / "specs"
        specs.mkdir(parents=True, exist_ok=True)
        (specs / f"{_sname(idn)}.json").write_text(json.dumps(
            {"engine": "claude", "request": {"requested_model": "claude-sonnet-5",
                                             "effort": "high"}}), encoding="utf-8")
    if with_obs:
        from phase_executor.supervisor import synthetic_observation  # noqa: PLC0415  # pylint: disable=no-name-in-module
        obs = synthetic_observation(
            run_id=run_id, seat="build", attempt_id="0-aaaa1111", engine="claude",
            requested_model="claude-sonnet-5", prompt="hi", parse_status=contract.TIMEOUT,
            reason="t", routing_config_digest="sha256:" + "0" * 64)
        obs["actual_model"] = "claude-sonnet-5"
        (cap / "observation.json").write_text(json.dumps(obs), encoding="utf-8")
    if with_activity:
        (cap / "transport.stdout.txt").write_text("first line\nlast activity line\n", encoding="utf-8")
    return ws, repo, reg_root


def test_cli_status_missing_registry_empty_seats(tmp_path):
    repo = tmp_path / "projects" / "statusrepo"
    _cfg(repo)
    ws = _ws(tmp_path, path="./projects/statusrepo")
    r = _run_cli("status", "--workspace", ws, "--project", "rawgentic", "--run", "run1")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["run_id"] == "run1" and out["seats"] == []


def test_cli_status_renders_seat_row(tmp_path):
    ws, _, _ = _status_repo(tmp_path, with_obs=True, with_activity=True)
    r = _run_cli("status", "--workspace", ws, "--project", "rawgentic", "--run", "run1")
    assert r.returncode == 0
    (row,) = json.loads(r.stdout)["seats"]
    assert row["seat"] == "build"
    assert row["state"] == "completed"          # valid sentinel wins over dead session
    assert row["recorded_state"] == "running"   # stale registry state visible, not hidden
    assert row["requested_model"] == "claude-sonnet-5" and row["effort"] == "high"
    assert row["actual_model"] == "claude-sonnet-5" and row["engine"] == "claude"
    assert row["eta"] == "no estimate"
    assert row["last_activity"]["file"] in ("transport.stdout.txt", "observation.json")
    assert row["last_activity"]["tail"]


def test_cli_status_dead_session_no_sentinel(tmp_path):
    ws, _, _ = _status_repo(tmp_path, with_obs=False)
    r = _run_cli("status", "--workspace", ws, "--project", "rawgentic", "--run", "run1")
    (row,) = json.loads(r.stdout)["seats"]
    assert row["state"] == "exited_no_sentinel"
    assert row["actual_model"] is None and row["last_activity"] is None


def test_cli_status_filters_to_run(tmp_path):
    ws, _, _ = _status_repo(tmp_path, run_id="run1")
    r = _run_cli("status", "--workspace", ws, "--project", "rawgentic", "--run", "other")
    assert r.returncode == 0 and json.loads(r.stdout)["seats"] == []


def test_cli_status_corrupt_registry_structured_error(tmp_path):
    ws, _, reg_root = _status_repo(tmp_path)
    (reg_root / "jobs.json").write_text("{corrupt", encoding="utf-8")
    r = _run_cli("status", "--workspace", ws, "--project", "rawgentic", "--run", "run1")
    assert r.returncode == er.EXIT_INTERNAL
    out = json.loads(r.stdout)
    assert out["ok"] is False and out["error"]["code"] == "registry_corrupt"


def test_cli_status_is_read_only(tmp_path):
    # AC-J3: a status call never mutates run state — jobs.json bytes are untouched.
    ws, _, reg_root = _status_repo(tmp_path, with_obs=True, with_activity=True)
    before = (reg_root / "jobs.json").read_bytes()
    r = _run_cli("status", "--workspace", ws, "--project", "rawgentic", "--run", "run1")
    assert r.returncode == 0
    assert (reg_root / "jobs.json").read_bytes() == before


def test_cli_status_missing_project_path_exit2(tmp_path):
    ws = _ws(tmp_path, path="./projects/gone")
    r = _run_cli("status", "--workspace", ws, "--project", "rawgentic", "--run", "run1")
    assert r.returncode == er.EXIT_MALFORMED


def test_cli_status_running_window_never_leaks_prompt(tmp_path):
    # 8a R2#1 (High): during the running window the capture dir holds ONLY .incomplete +
    # input.md (the raw prompt — claude_cli.py writes it BEFORE the provider call). The
    # activity probe must never select/echo it.
    ws, repo, _ = _status_repo(tmp_path, with_obs=False, with_spec=True)
    cap = repo / ".rawgentic" / "runs" / "run1" / "build" / "0-aaaa1111"
    (cap / ".incomplete").write_text("engine invocation has not completed\n", encoding="utf-8")
    (cap / "input.md").write_text("SECRET-PROMPT-MARKER do the thing\n", encoding="utf-8")
    r = _run_cli("status", "--workspace", ws, "--project", "rawgentic", "--run", "run1")
    assert r.returncode == 0
    (row,) = json.loads(r.stdout)["seats"]
    assert "SECRET-PROMPT-MARKER" not in r.stdout
    assert row["last_activity"] is None  # only non-allowlisted files present


def test_cli_status_read_only_no_registry_dir_metadata_write(tmp_path):
    # 8a R2#2 (Medium): the read path must not construct JobRegistry — its __init__
    # mkdir/chmods the registry root (a metadata write on a read-only surface).
    ws, _, reg_root = _status_repo(tmp_path)
    import os as _os
    before = _os.stat(reg_root)
    r = _run_cli("status", "--workspace", ws, "--project", "rawgentic", "--run", "run1")
    assert r.returncode == 0
    after = _os.stat(reg_root)
    assert (before.st_mode, before.st_ctime_ns) == (after.st_mode, after.st_ctime_ns)


def test_cli_status_wrong_shape_jobs_json_structured_error(tmp_path):
    # Step-11 L2: valid JSON of the wrong shape (top-level list) must be the structured
    # registry_corrupt exit 5, not an AttributeError traceback.
    ws, _, reg_root = _status_repo(tmp_path)
    (reg_root / "jobs.json").write_text("[1, 2]", encoding="utf-8")
    r = _run_cli("status", "--workspace", ws, "--project", "rawgentic", "--run", "run1")
    assert r.returncode == er.EXIT_INTERNAL
    assert json.loads(r.stdout)["error"]["code"] == "registry_corrupt"


def test_cli_status_corrupt_spec_marked_not_fatal(tmp_path):
    # gpt-diff A5: a corrupt launch spec is a per-row marker, never a whole-run failure.
    ws, _, reg_root = _status_repo(tmp_path)
    spec = reg_root / "specs"
    (spec / next(spec.glob("*.json")).name).write_text("{corrupt", encoding="utf-8")
    r = _run_cli("status", "--workspace", ws, "--project", "rawgentic", "--run", "run1")
    assert r.returncode == 0
    (row,) = json.loads(r.stdout)["seats"]
    assert row["spec_status"] == "corrupt" and row["requested_model"] is None


# ---- #555 ledger-aware chokepoint + close-run + reconcile verb ---------------------------------

def _analysis_project(tmp_path):
    """A project (default table override) + workspace binding analysis=executor — the setup the
    #555 close-run/reconcile/chokepoint verbs resolve. Returns (ws_path, repo_path)."""
    repo = tmp_path / "projects" / "rawgentic"
    table_dst = repo / "claude_docs" / "routing" / "phase-executor-table.json"
    table_dst.parent.mkdir(parents=True)
    table_dst.write_bytes(routing.default_table_path().read_bytes())
    _cfg(repo, pointer="claude_docs/routing/phase-executor-table.json")
    ws = _ws(tmp_path, {"version": 1, "seats": {"analysis": "executor"}}, path="./projects/rawgentic")
    return ws, repo


def _dispatch_args(ws, run_id="run1", cid="wf2:step2", seat="analysis"):
    class A:
        pass
    a = A()
    a.seat = seat; a.prompt_file = None; a.run_id = run_id; a.context_file = None
    a.correlation_id = cid; a.author_provider = None; a.effort = None; a.timeout = 5.0
    a.workspace = ws; a.project = "rawgentic"; a.gate_file = None; a.plan_file = None
    return a


def _run_dir(repo, run_id="run1"):
    return repo / ".rawgentic" / "runs" / run_id


def test_cli_chokepoint_refuses_dispatch_after_run_closed(tmp_path, capsys):
    # #555 AC2: once the ledger is run_closed, a NEW dispatch is refused at the choke-point
    # (before any spawn), exit 4. Uses close-run to close, then dispatches.
    ws, repo = _analysis_project(tmp_path)
    # #474: declare first (close-run no longer seeds an undeclared run)
    assert er.main(["begin-run", "--run-id", "run1", "--workspace", ws, "--project", "rawgentic"]) == er.EXIT_OK
    assert er.main(["close-run", "--run-id", "run1", "--workspace", ws, "--project", "rawgentic"]) == er.EXIT_OK
    capsys.readouterr()
    a = _dispatch_args(ws)
    a.prompt_file = str(tmp_path / "p.txt"); (tmp_path / "p.txt").write_text("hi", encoding="utf-8")
    rc = er._do_dispatch(a)
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ENFORCEMENT
    assert out["error"]["code"] == "run_closed_dispatch_refused"


def test_cli_chokepoint_refuses_keyless_dispatch(tmp_path, capsys):
    # #555 AC2 (8a F2): a dispatch with no correlation_id would be an uninstrumented spawn (no
    # ledger record) — the choke-point refuses it (exit 2) rather than spawning by convention.
    ws, repo = _analysis_project(tmp_path)
    # #474: declare first so the keyless refusal (not run_not_declared) is what fires
    assert er.main(["begin-run", "--run-id", "run1", "--workspace", ws, "--project", "rawgentic"]) == er.EXIT_OK
    capsys.readouterr()
    a = _dispatch_args(ws, cid=None)
    a.prompt_file = str(tmp_path / "p.txt"); (tmp_path / "p.txt").write_text("hi", encoding="utf-8")
    rc = er._do_dispatch(a)
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_MALFORMED and out["error"]["code"] == "correlation_id_required"
    # and nothing was appended to the ledger (no expected record for a refused keyless call)
    lg = ledger.ExpectedCallLedger(_run_dir(repo), "run1")
    assert lg.read().expected == []


def test_cli_close_run_then_double_close_refused(tmp_path, capsys):
    ws, _ = _analysis_project(tmp_path)
    # #474: declare first (close-run no longer seeds)
    assert er.main(["begin-run", "--run-id", "run1", "--workspace", ws, "--project", "rawgentic"]) == er.EXIT_OK
    assert er.main(["close-run", "--run-id", "run1", "--workspace", ws, "--project", "rawgentic"]) == er.EXIT_OK
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["run_closed"] is True
    rc = er.main(["close-run", "--run-id", "run1", "--workspace", ws, "--project", "rawgentic"])
    assert rc == er.EXIT_MALFORMED
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "ledger_refused"


def test_cli_reconcile_final_refuses_open_ledger(tmp_path, capsys):
    # #555 AC3: final requires run_closed last.
    ws, repo = _analysis_project(tmp_path)
    lg = ledger.ExpectedCallLedger(_run_dir(repo), "run1")
    lg.append_initial("sha256:cfg", architecture="executor")
    lg.append_expected("analysis", "c1")
    rc = er.main(["reconcile", "--run-id", "run1", "--mode", "final", "--workspace", ws, "--project", "rawgentic"])
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ANOMALY and out["reconciled"] is False and "run_closed" in out["reason"]


def test_cli_reconcile_provisional_tolerates_in_flight(tmp_path, capsys):
    # #555 AC3: provisional tolerates an open ledger with a not-yet-observed (in-flight) call.
    ws, repo = _analysis_project(tmp_path)
    lg = ledger.ExpectedCallLedger(_run_dir(repo), "run1")
    lg.append_initial("sha256:cfg", architecture="executor")
    lg.append_expected("analysis", "c1")   # no audit obs yet → missing_receipt, tolerated
    rc = er.main(["reconcile", "--run-id", "run1", "--mode", "provisional", "--workspace", ws, "--project", "rawgentic"])
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_OK and out["reconciled"] is True
    # discriminating (8a F8): the verb must READ the ledger's expected set — a regression that
    # dropped the expected-set wiring would report 0 here and this assert would catch it.
    assert out["expected_calls"] == 1


def test_cli_reconcile_final_clean_reconciles_ok(tmp_path, capsys):
    # #555 AC3 (clean run reconciles OK) at the VERB level: a closed ledger whose one expected call
    # binds to a passed receipt + verified observation in the audit → exit 0. (reconcile_run's full
    # binding algebra is covered in test_enforce.py; this pins the verb's ledger↔audit wiring.)
    ws, repo = _analysis_project(tmp_path)
    rd = _run_dir(repo)
    lg = ledger.ExpectedCallLedger(rd, "run1")
    lg.append_initial("sha256:cfg", architecture="executor")
    lg.append_expected("analysis", "c1")
    lg.append_run_closed()
    lane = {"provider": "anthropic", "transport": "native", "auth_mode": "subscription_oauth",
            "pool": "claude", "credential_ref": None}
    tid = list(enforce.target_identity({"model": "claude-sonnet-5", "lane": lane}))
    receipt = {"kind": "receipt", "nonce": "n1", "seat": "analysis", "correlation_id": "c1",
               "attempt_id": "0", "target_identity": tid, "config_digest": "sha256:cfg",
               "verdict": "pass"}
    obs_inner = {"schema_version": "1", "run_id": "run1", "attempt_id": "0", "seat": "analysis",
                 "correlation_id": "c1",
                 "engine": "claude", "transport": "native", "requested_model": "claude-sonnet-5",
                 "actual_model": "claude-sonnet-5", "prompt_hash": "sha256:x", "context_hashes": [],
                 "usage": {"input": 1, "output": 1}, "timing_ms": 1, "queued_ms": 0,
                 "process": {"exit_code": 0, "timed_out": False}, "parse_status": "ok",
                 "parsed_payload": None, "raw_capture_path": None, "fallback_reason": None,
                 "routing_config_digest": "sha256:cfg", "dispatched_lane": lane}
    obs = {"kind": "observation", "receipt_nonce": "n1", "observation": obs_inner}
    (rd / "routing-audit.jsonl").write_text(json.dumps(receipt) + "\n" + json.dumps(obs) + "\n",
                                            encoding="utf-8")
    rc = er.main(["reconcile", "--run-id", "run1", "--mode", "final", "--workspace", ws, "--project", "rawgentic"])
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_OK and out["reconciled"] is True and out["expected_calls"] == 1


def test_cli_reconcile_final_zero_expected_refuses(tmp_path, capsys):
    # #555 AC3/AC4: a closed run with an initial but ZERO expected calls fails final
    # (require_nonempty — a wiring bug that dropped the expected-set cannot ship vacuously).
    ws, repo = _analysis_project(tmp_path)
    lg = ledger.ExpectedCallLedger(_run_dir(repo), "run1")
    lg.append_initial("sha256:cfg", architecture="executor")
    lg.append_run_closed()
    rc = er.main(["reconcile", "--run-id", "run1", "--mode", "final", "--workspace", ws, "--project", "rawgentic"])
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ANOMALY and out["reconciled"] is False


def test_cli_reconcile_provisional_fails_on_hard_anomaly(tmp_path, capsys):
    # #555 AC4: provisional STILL fails on a hard breach — an audit receipt for a call the ledger
    # never expected is an orphan (not an in-flight tolerance case).
    ws, repo = _analysis_project(tmp_path)
    rd = _run_dir(repo)
    lg = ledger.ExpectedCallLedger(rd, "run1")
    lg.append_initial("sha256:cfg", architecture="executor")   # zero expected calls
    # an orphan receipt in the audit: (seat, cid) not in the (empty) expected set
    orphan_receipt = {"kind": "receipt", "nonce": "n1", "seat": "analysis", "correlation_id": "ghost",
                      "attempt_id": "0", "target_identity": ["m", "anthropic", "native",
                      "subscription_oauth", "claude", None, None], "config_digest": "sha256:cfg",
                      "verdict": "pass"}
    (rd / "routing-audit.jsonl").write_text(json.dumps(orphan_receipt) + "\n", encoding="utf-8")
    rc = er.main(["reconcile", "--run-id", "run1", "--mode", "provisional", "--workspace", ws, "--project", "rawgentic"])
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ANOMALY and "orphan" in out["anomalies"]


def test_supervised_await_deadline_clamped_to_effective_timeout(tmp_path, monkeypatch):
    """#558 S-F6 (3-reviewer converged): ONE effective timeout — the manifest/caller
    bound must also be the supervisor await deadline, not an independent 3600s default
    that retains a hung pane + permit + worktree for an hour."""
    seen = {}
    orig = _StubSupervisor.await_job

    def spy(self, record, *, timeout_s=3600.0):
        seen["timeout_s"] = timeout_s
        return orig(self, record, timeout_s=timeout_s)

    monkeypatch.setattr(_StubSupervisor, "await_job", spy)
    res, _sup, _qc, _calls = _supervised(tmp_path, monkeypatch=monkeypatch)
    assert res["exit"] == er.EXIT_OK, res
    assert seen["timeout_s"] == 5.0  # min(await default 3600, effective min(5.0, manifest))


# --- #474: defaultArchitecture — the flip -------------------------------------------------------

def test_resolve_architecture_absent_key_is_executor(tmp_path):
    assert er.resolve_architecture(_ws(tmp_path)) == "executor"


def test_resolve_architecture_explicit_values(tmp_path):
    assert er.resolve_architecture(_ws(tmp_path, default_architecture="executor")) == "executor"
    assert er.resolve_architecture(_ws(tmp_path, default_architecture="legacy")) == "legacy"


def test_resolve_architecture_absent_workspace_is_executor(tmp_path):
    assert er.resolve_architecture(str(tmp_path / "missing.json")) == "executor"


@pytest.mark.parametrize("bad", ["Executor", "LEGACY", "agent-tool", 1, True, None, ["legacy"]])
def test_resolve_architecture_invalid_value_fails_closed(tmp_path, bad):
    with pytest.raises(er.MalformedConfig):
        er.resolve_architecture(_ws(tmp_path, default_architecture=bad))


def test_resolve_architecture_corrupt_workspace_fails_closed(tmp_path):
    corrupt = tmp_path / "ws.json"
    corrupt.write_text('{"defaultArch TRUNCATED', encoding="utf-8")
    with pytest.raises(er.MalformedConfig):
        er.resolve_architecture(str(corrupt))


def test_resolve_legacy_architecture_routes_back(tmp_path):
    # #474 guard (c): the manual joint rollback — legacy → inherit (the Agent-tool path)
    ws = _ws(tmp_path, default_architecture="legacy")
    action, reason = er.resolve_seat_action("ship", ws, "rawgentic")
    assert action == "inherit"
    assert "legacy" in reason and "#474" in reason


def test_resolve_executor_reason_names_the_flip(tmp_path):
    action, reason = er.resolve_seat_action("ship", _ws(tmp_path), "rawgentic")
    assert action == "executor" and "#474" in reason


def test_mixed_config_inherit_seat_under_executor_refused(tmp_path):
    # #474 guard (d) config half: an explicit inherit seat contradicts the executor architecture
    ws = _ws(tmp_path, {"version": 1, "seats": {"ship": "inherit"}})
    with pytest.raises(er.MalformedConfig) as ei:
        er.resolve_seat_action("ship", ws, "rawgentic")
    msg = str(ei.value)
    assert "ship" in msg and "inherit" in msg  # names the offending seat + mode


def test_mixed_config_executor_seat_under_legacy_refused(tmp_path):
    ws = _ws(tmp_path, {"version": 1, "seats": {"ship": "executor"}},
             default_architecture="legacy")
    with pytest.raises(er.MalformedConfig) as ei:
        er.resolve_seat_action("ship", ws, "rawgentic")
    msg = str(ei.value)
    assert "ship" in msg and "executor" in msg


def test_agreeing_executor_seat_under_executor_is_noop_valid(tmp_path):
    # the live rawgentic all-executor block stays valid as a redundant no-op
    ws = _ws(tmp_path, {"version": 1, "seats": {"ship": "executor"}})
    assert er.resolve_seat_action("ship", ws, "rawgentic")[0] == "executor"


def test_agreeing_inherit_seat_under_legacy_is_noop_valid(tmp_path):
    ws = _ws(tmp_path, {"version": 1, "seats": {"ship": "inherit"}},
             default_architecture="legacy")
    assert er.resolve_seat_action("ship", ws, "rawgentic")[0] == "inherit"


def test_driver_only_seats_unaffected_by_architecture(tmp_path):
    for arch in ("executor", "legacy"):
        ws = _ws(tmp_path, default_architecture=arch)
        assert er.resolve_seat_action("merge", ws, "rawgentic")[0] == "driver_only"


def test_malformed_executor_routing_still_refused_under_flip(tmp_path):
    # parse_executor_routing validation unchanged — malformed block refuses even though modes
    # no longer select the tier
    ws = _ws(tmp_path, {"version": 99, "seats": {}})
    with pytest.raises(er.MalformedConfig):
        er.resolve_seat_action("ship", ws, "rawgentic")


def test_single_snapshot_read(tmp_path, monkeypatch):
    # S3-TOCTOU: one json.load per resolution — architecture + entry + block from one snapshot
    calls = []
    real_load = er.json.load
    def counting_load(fh, *a, **k):
        calls.append(getattr(fh, "name", "?"))
        return real_load(fh, *a, **k)
    ws = _ws(tmp_path, {"version": 1, "seats": {"ship": "executor"}})
    monkeypatch.setattr(er.json, "load", counting_load)
    er.resolve_seat_action("ship", ws, "rawgentic")
    ws_reads = [c for c in calls if c.endswith("ws.json")]
    assert len(ws_reads) <= 1, f"workspace read {len(ws_reads)} times: {ws_reads}"


def test_legacy_rollback_target_definitions_exist():
    # #474 guard (c) proxy: the bundled legacy agent definitions stay in-tree as the rollback
    # target (frontmatter-shaped: leading --- block with a name: line)
    repo = Path(__file__).resolve().parents[2]
    for name in ("rawgentic-implementer", "rawgentic-reviewer"):
        p = repo / "agents" / f"{name}.md"
        assert p.is_file(), f"rollback target missing: {p}"
        head = p.read_text(encoding="utf-8").split("---")
        assert len(head) >= 3 and "name:" in head[1], f"frontmatter malformed: {p}"


# --- #474 T3: begin-run + entry-point architecture enforcement ---------------------------------

def _begin(ws, run_id="run1"):
    return er.main(["begin-run", "--run-id", run_id, "--workspace", ws, "--project", "rawgentic"])


def test_cli_begin_run_declares_executor(tmp_path, capsys):
    ws, repo = _analysis_project(tmp_path)
    assert _begin(ws) == er.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["run_id"] == "run1" and out["architecture"] == "executor"
    st = ledger.ExpectedCallLedger(_run_dir(repo), "run1").read()
    assert st.architecture == "executor" and st.initial_digest


def test_cli_begin_run_refuses_under_legacy(tmp_path, capsys):
    ws, repo = _analysis_project(tmp_path)
    raw = json.loads(Path(ws).read_text(encoding="utf-8"))
    raw["defaultArchitecture"] = "legacy"
    raw["projects"][0].pop("executorRouting")  # avoid the mixed-config refusal masking this one
    Path(ws).write_text(json.dumps(raw), encoding="utf-8")
    rc = _begin(ws)
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ENFORCEMENT and out["error"]["code"] == "legacy_architecture_begin_refused"
    assert not (_run_dir(repo) / "expected-calls.jsonl").exists()  # nothing written


def test_cli_begin_run_idempotent_same_digest(tmp_path, capsys):
    ws, _ = _analysis_project(tmp_path)
    assert _begin(ws) == er.EXIT_OK
    capsys.readouterr()
    assert _begin(ws) == er.EXIT_OK  # same (architecture, config_digest) → benign noop
    out = json.loads(capsys.readouterr().out)
    assert out.get("already_declared") is True


def test_cli_begin_run_digest_mismatch_refused(tmp_path, capsys):
    ws, repo = _analysis_project(tmp_path)
    rd = _run_dir(repo)
    lg = ledger.ExpectedCallLedger(rd, "run1")
    lg.append_initial("sha256:DIFFERENT", architecture="executor")
    rc = _begin(ws)
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ENFORCEMENT and out["error"]["code"] == "begin_run_digest_conflict"


def test_cli_dispatch_requires_declaration(tmp_path, capsys):
    # #474: the lazy seed is GONE — dispatch on an undeclared run refuses run_not_declared
    ws, repo = _analysis_project(tmp_path)
    a = _dispatch_args(ws)
    a.prompt_file = str(tmp_path / "p.txt"); (tmp_path / "p.txt").write_text("hi", encoding="utf-8")
    rc = er._do_dispatch(a)
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ENFORCEMENT and out["error"]["code"] == "run_not_declared"
    lg = ledger.ExpectedCallLedger(_run_dir(repo), "run1")
    assert lg.read().initial_digest is None  # consume-never-seed


def test_cli_dispatch_refuses_legacy_pinned_ledger(tmp_path, capsys):
    # guard (d) ledger half: an executor dispatch against a legacy-declared run is mixed — refuse
    ws, repo = _analysis_project(tmp_path)
    lg = ledger.ExpectedCallLedger(_run_dir(repo), "run1")
    lg.append_initial("sha256:cfg", architecture="legacy")
    a = _dispatch_args(ws)
    a.prompt_file = str(tmp_path / "p.txt"); (tmp_path / "p.txt").write_text("hi", encoding="utf-8")
    rc = er._do_dispatch(a)
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ENFORCEMENT and out["error"]["code"] == "mixed_architecture_run_refused"


def test_cli_dispatch_none_architecture_ledger_proceeds(tmp_path, capsys):
    # pre-flip in-flight run (initial exists, no architecture) keeps dispatching (bounded compat)
    ws, repo = _analysis_project(tmp_path)
    rd = _run_dir(repo)
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "expected-calls.jsonl").write_text(
        json.dumps({"kind": "initial", "run_id": "run1", "initial_digest": "sha256:pre"}) + "\n",
        encoding="utf-8")
    a = _dispatch_args(ws)
    a.prompt_file = str(tmp_path / "p.txt"); (tmp_path / "p.txt").write_text("hi", encoding="utf-8")
    rc = er._do_dispatch(a)
    out = json.loads(capsys.readouterr().out)
    # proceeds past the declaration gate: whatever the (stubless) dispatch fails on later, it is
    # NOT the declaration/mixing refusals
    assert out.get("error", {}).get("code") not in ("run_not_declared", "mixed_architecture_run_refused")
    assert rc != er.EXIT_ENFORCEMENT or out["error"]["code"] not in (
        "run_not_declared", "mixed_architecture_run_refused")


def test_cli_dispatch_refuses_after_midrun_rollback(tmp_path, capsys):
    # mid-run flip: declared-executor run + workspace edited to legacy → next dispatch refuses
    # LOUD at the config level (legacy architecture has no executor seats; exit 2)
    ws, repo = _analysis_project(tmp_path)
    assert _begin(ws) == er.EXIT_OK
    capsys.readouterr()
    raw = json.loads(Path(ws).read_text(encoding="utf-8"))
    raw["defaultArchitecture"] = "legacy"
    raw["projects"][0].pop("executorRouting")
    Path(ws).write_text(json.dumps(raw), encoding="utf-8")
    a = _dispatch_args(ws)
    a.prompt_file = str(tmp_path / "p.txt"); (tmp_path / "p.txt").write_text("hi", encoding="utf-8")
    rc = er._do_dispatch(a)
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_MALFORMED and "inherit" in out["error"]["message"]


def test_cli_recover_refuses_under_legacy_workspace(tmp_path, capsys):
    # rollback stops recovery relaunches too ("lever takes effect" on every spawn path)
    ws, repo = _analysis_project(tmp_path)
    lg = ledger.ExpectedCallLedger(_run_dir(repo), "run1")
    lg.append_initial("sha256:cfg", architecture="executor")
    raw = json.loads(Path(ws).read_text(encoding="utf-8"))
    raw["defaultArchitecture"] = "legacy"
    raw["projects"][0].pop("executorRouting")
    Path(ws).write_text(json.dumps(raw), encoding="utf-8")
    rc = er.main(["recover-run", "--run-id", "run1", "--workspace", ws, "--project", "rawgentic"])
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ENFORCEMENT and out["error"]["code"] == "legacy_architecture_recover_refused"


def test_cli_recover_refuses_legacy_pinned_ledger(tmp_path, capsys):
    ws, repo = _analysis_project(tmp_path)
    lg = ledger.ExpectedCallLedger(_run_dir(repo), "run1")
    lg.append_initial("sha256:cfg", architecture="legacy")
    rc = er.main(["recover-run", "--run-id", "run1", "--workspace", ws, "--project", "rawgentic"])
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ENFORCEMENT and out["error"]["code"] == "mixed_architecture_run_refused"


def test_cli_recover_refuses_corrupt_ledger_prelaunch(tmp_path, capsys):
    ws, repo = _analysis_project(tmp_path)
    rd = _run_dir(repo)
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "expected-calls.jsonl").write_text("{corrupt\n", encoding="utf-8")
    rc = er.main(["recover-run", "--run-id", "run1", "--workspace", ws, "--project", "rawgentic"])
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ENFORCEMENT and out["error"]["code"] == "ledger_refused"


def test_cli_close_run_requires_declaration(tmp_path, capsys):
    # #474: the zero-dispatch close seed is GONE — closing a never-declared run refuses
    ws, _ = _analysis_project(tmp_path)
    rc = er.main(["close-run", "--run-id", "run1", "--workspace", ws, "--project", "rawgentic"])
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ENFORCEMENT and out["error"]["code"] == "run_not_declared"


def test_cli_close_run_none_architecture_proceeds(tmp_path, capsys):
    # pre-flip in-flight run closes fine (advisory)
    ws, repo = _analysis_project(tmp_path)
    rd = _run_dir(repo)
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "expected-calls.jsonl").write_text(
        json.dumps({"kind": "initial", "run_id": "run1", "initial_digest": "sha256:pre"}) + "\n",
        encoding="utf-8")
    rc = er.main(["close-run", "--run-id", "run1", "--workspace", ws, "--project", "rawgentic"])
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_OK and out["run_closed"] is True


def test_cli_close_run_refuses_legacy_pinned_ledger(tmp_path, capsys):
    # a legacy-in-ledger state is unreachable via begin-run — defended against hand-crafted files
    ws, repo = _analysis_project(tmp_path)
    lg = ledger.ExpectedCallLedger(_run_dir(repo), "run1")
    lg.append_initial("sha256:cfg", architecture="legacy")
    rc = er.main(["close-run", "--run-id", "run1", "--workspace", ws, "--project", "rawgentic"])
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ENFORCEMENT and out["error"]["code"] == "mixed_architecture_run_refused"


# --- #474 8a remediation: snapshot linearization + symlink + begin-run strictness ---------------

def test_dangling_workspace_symlink_fails_closed(tmp_path):
    # 8a F4: a dangling symlink is present-but-unreadable — MalformedConfig, never executor
    link = tmp_path / "ws.json"
    os.symlink(tmp_path / "gone.json", link)
    with pytest.raises(er.MalformedConfig):
        er.resolve_architecture(str(link))
    with pytest.raises(er.MalformedConfig):
        er.resolve_seat_action("ship", str(link), "rawgentic")


def test_cli_begin_run_refuses_unpinned_prior_ledger(tmp_path, capsys):
    # 8a F3: a pre-3.93 (architecture-less) initial is NOT an executor declaration — begin-run
    # must not certify it as already_declared; consumers keep their None compat
    ws, repo = _analysis_project(tmp_path)
    rd = _run_dir(repo)
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "expected-calls.jsonl").write_text(
        json.dumps({"kind": "initial", "run_id": "run1", "initial_digest": "sha256:pre"}) + "\n",
        encoding="utf-8")
    rc = _begin(ws)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == er.EXIT_ENFORCEMENT and out["error"]["code"] == "begin_run_unpinned_ledger"


def test_cli_dispatch_single_workspace_read(tmp_path, capsys, monkeypatch):
    # 8a F1: the WHOLE dispatch entry point does ONE workspace read — architecture, seat
    # decision, and repo root all come from one snapshot (linearization at the CLI level)
    ws, repo = _analysis_project(tmp_path)
    assert er.main(["begin-run", "--run-id", "run1", "--workspace", ws, "--project", "rawgentic"]) == er.EXIT_OK
    capsys.readouterr()
    opens = []
    real_open = er._load_workspace_snapshot
    def counting(path):
        opens.append(path)
        return real_open(path)
    monkeypatch.setattr(er, "_load_workspace_snapshot", counting)
    a = _dispatch_args(ws)
    a.prompt_file = str(tmp_path / "p.txt"); (tmp_path / "p.txt").write_text("hi", encoding="utf-8")
    er._do_dispatch(a)
    capsys.readouterr()
    assert len(opens) == 1, f"dispatch read the workspace {len(opens)} times"


def test_cli_begin_run_single_workspace_read(tmp_path, capsys, monkeypatch):
    ws, _ = _analysis_project(tmp_path)
    opens = []
    real_open = er._load_workspace_snapshot
    def counting(path):
        opens.append(path)
        return real_open(path)
    monkeypatch.setattr(er, "_load_workspace_snapshot", counting)
    assert _begin(ws) == er.EXIT_OK
    capsys.readouterr()
    assert len(opens) == 1, f"begin-run read the workspace {len(opens)} times"


def test_cli_recover_single_workspace_read(tmp_path, capsys, monkeypatch):
    ws, repo = _analysis_project(tmp_path)
    lg = ledger.ExpectedCallLedger(_run_dir(repo), "run1")
    lg.append_initial("sha256:cfg", architecture="executor")
    opens = []
    real_open = er._load_workspace_snapshot
    def counting(path):
        opens.append(path)
        return real_open(path)
    monkeypatch.setattr(er, "_load_workspace_snapshot", counting)
    er.main(["recover-run", "--run-id", "run1", "--workspace", ws, "--project", "rawgentic"])
    capsys.readouterr()
    assert len(opens) == 1, f"recover-run read the workspace {len(opens)} times"


def test_cli_dispatch_refuses_run_digest_conflict(tmp_path, capsys):
    # S11 F1: a routing-table change after begin-run is a declared-state conflict at CONSUME time
    ws, repo = _analysis_project(tmp_path)
    assert _begin(ws) == er.EXIT_OK
    capsys.readouterr()
    table = repo / "claude_docs" / "routing" / "phase-executor-table.json"
    raw = json.loads(table.read_text(encoding="utf-8"))
    raw["pools"]["claude"]["concurrency"] = raw["pools"]["claude"]["concurrency"] + 1
    table.write_text(json.dumps(raw), encoding="utf-8")
    a = _dispatch_args(ws)
    a.prompt_file = str(tmp_path / "p.txt"); (tmp_path / "p.txt").write_text("hi", encoding="utf-8")
    rc = er._do_dispatch(a)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == er.EXIT_ENFORCEMENT and out["error"]["code"] == "run_digest_conflict"


def test_cli_recover_refuses_closed_ledger_at_preflight(tmp_path, capsys):
    # S11 F5: run_closed refuses at the preflight, before supervisor construction
    ws, repo = _analysis_project(tmp_path)
    lg = ledger.ExpectedCallLedger(_run_dir(repo), "run1")
    lg.append_initial("sha256:cfg", architecture="executor")
    lg.append_run_closed()
    rc = er.main(["recover-run", "--run-id", "run1", "--workspace", ws, "--project", "rawgentic"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == er.EXIT_ENFORCEMENT and out["error"]["code"] == "run_closed_recover_refused"


def test_cli_recover_refuses_run_digest_conflict(tmp_path, capsys):
    # S11 R2-2: recovery consumes the declared epoch too
    ws, repo = _analysis_project(tmp_path)
    lg = ledger.ExpectedCallLedger(_run_dir(repo), "run1")
    lg.append_initial("sha256:NOT-THE-TABLE", architecture="executor")
    rc = er.main(["recover-run", "--run-id", "run1", "--workspace", ws, "--project", "rawgentic"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == er.EXIT_ENFORCEMENT and out["error"]["code"] == "run_digest_conflict"


# ---- #647: the read-only status surface resolves the record's backend -------------------
# The mapping lives in a module-level function with the backends INJECTED, precisely so
# these three cases are unit-testable: a closure would have no substitution seam, and
# driving the real thing would need a herdr binary CI does not have (GAP-4).

class _ProbeStub:
    """A TerminalBackend stand-in that returns a fixed Liveness and records its calls."""

    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = []

    def probe_session(self, endpoint, name, timeout=30):  # noqa: ARG002 — signature is the seam
        self.calls.append((endpoint, name))
        return self.verdict


def _status_rec(terminal_backend=None, run_socket="/run/user/1000/rg-x.sock",
                session_name="rg-x"):
    class _R:  # minimal JobRecord stand-in: the verdict helper reads only these three
        pass
    r = _R()
    r.terminal_backend = terminal_backend
    r.run_socket = run_socket
    r.session_name = session_name
    return r


def test_status_live_verdict_herdr_record_probes_the_herdr_backend():
    """AC1/AC4: a herdr-backed record must be probed through the herdr backend. Before this
    fix the status surface ran `tmux -S <run_socket>` unconditionally, and a herdr
    run_socket is a WORKSPACE ID, so tmux failed for an ordinary reason."""
    tmux = _ProbeStub(Liveness.CONFIRMED_GONE)
    herdr = _ProbeStub(Liveness.CONFIRMED_ALIVE)
    rec = _status_rec(terminal_backend="herdr", run_socket="w1")
    live, probe_error = er.status_live_verdict(rec, tmux=tmux, herdr=herdr,
                                              tmux_present=True)
    assert (live, probe_error) == (True, None)
    assert herdr.calls == [("w1", "rg-x")]
    assert tmux.calls == []          # the wrong backend is never consulted


def test_status_live_verdict_indeterminate_surfaces_as_probe_error():
    """AC2: INDETERMINATE is not evidence of death — it must come back with a probe_error
    so the row derives liveness_unknown rather than exited_no_sentinel."""
    herdr = _ProbeStub(Liveness.INDETERMINATE)
    live, probe_error = er.status_live_verdict(
        _status_rec(terminal_backend="herdr"), tmux=_ProbeStub(Liveness.CONFIRMED_GONE),
        herdr=herdr, tmux_present=True)
    assert live is False
    assert probe_error and "indeterminate" in probe_error.lower()


def test_status_live_verdict_tmux_confirmed_gone_is_not_a_degradation():
    """AC3: tmux's routine 'no sessions on this socket' classifies CONFIRMED_GONE, which
    must stay a clean (False, None) — byte-identical to the pre-fix behavior, so an ordinary
    absence never starts reporting as 'unknown'."""
    tmux = _ProbeStub(Liveness.CONFIRMED_GONE)
    assert er.status_live_verdict(_status_rec(), tmux=tmux, herdr=None,
                                  tmux_present=True) == (False, None)
    assert tmux.calls == [("/run/user/1000/rg-x.sock", "rg-x")]


def test_status_live_verdict_absent_tmux_only_degrades_tmux_records():
    """Step-4 Finding #1: the tmux-availability check must be evaluated AFTER backend
    resolution. Pre-fix it was checked first, so on a tmux-less host a HERDR record would
    carry probe_error 'tmux unavailable on this host' — a true flag with a false reason."""
    herdr = _ProbeStub(Liveness.CONFIRMED_ALIVE)
    assert er.status_live_verdict(_status_rec(terminal_backend="herdr"), tmux=None,
                                  herdr=herdr, tmux_present=False) == (True, None)
    live, probe_error = er.status_live_verdict(_status_rec(), tmux=None, herdr=None,
                                              tmux_present=False)
    assert live is False and probe_error == "tmux unavailable on this host"


def test_status_live_verdict_herdr_record_without_backend_refuses_loud():
    """The lifted resolver's fail-loud contract must not be swallowed into a bare False: a
    herdr record with no herdr backend is a configuration fault, not a dead job."""
    live, probe_error = er.status_live_verdict(_status_rec(terminal_backend="herdr"),
                                              tmux=_ProbeStub(None), herdr=None,
                                              tmux_present=True)
    assert live is False
    assert probe_error and "herdr" in probe_error.lower()


def test_do_status_binding_probes_the_records_own_backend(tmp_path, monkeypatch, capsys):
    """Step-8a cross-model finding (Medium): the five `status_live_verdict` tests pin the
    HELPER, not the `live_fn` BINDING. Reverting live_fn to the old unconditional
    `tmux -S <record.run_socket>` while keeping the helper would leave every one of them
    green, so this drives the real `_do_status` wiring end to end.

    Deliberately in-process with an injected backend rather than through the CLI: this host
    HAS a real herdr daemon and CI has none, so a subprocess test would assert
    environment-dependent behavior — exactly the "local machine masks the real answer" trap
    #638 hit. Here a stub reports CONFIRMED_ALIVE, so the row can only be `running` if the
    binding actually consulted the record's OWN backend.
    """
    ws, _, _ = _status_repo(tmp_path, with_obs=False, terminal_backend="herdr")
    seen = []

    class _AliveHerdr:
        def __init__(self, **kwargs):
            pass

        def probe_session(self, endpoint, name, timeout=30):  # noqa: ARG002
            seen.append((endpoint, name))
            return Liveness.CONFIRMED_ALIVE

    real_import = er._import_phase_executor

    def _patched_import():
        pe = real_import()
        pe.HerdrBackend = _AliveHerdr
        return pe

    monkeypatch.setattr(er, "_import_phase_executor", _patched_import)
    rc = er._do_status(types.SimpleNamespace(workspace=ws, project="rawgentic", run="run1"))
    assert rc == 0
    (row,) = json.loads(capsys.readouterr().out)["seats"]
    assert row["state"] == "running"        # pre-fix this was exited_no_sentinel
    assert row["probe_error"] is None
    assert len(seen) == 1                   # the herdr backend WAS the one consulted


def test_status_live_verdict_unrecognised_backend_is_indeterminate():
    """The resolver's new fail-loud guard must surface as an INDETERMINATE row, never as a
    confident state: status catches SupervisorError and reports it as a probe failure."""
    live, probe_error = er.status_live_verdict(
        _status_rec(terminal_backend="screen"), tmux=_ProbeStub(Liveness.CONFIRMED_ALIVE),
        herdr=_ProbeStub(Liveness.CONFIRMED_ALIVE), tmux_present=True)
    assert live is False
    assert probe_error and "unsupported terminal backend" in probe_error


# --- #735 F4: an omitted --timeout must default to the SEAT'S declared bound ---------------

def test_resolve_dispatch_timeout_uses_the_seats_declared_bound():
    """#735 F4 root cause: the CLI defaulted --timeout to a flat 300.0 s while the review
    seat's manifest declares bounds.timeout_s: 1800 and build declares 3600. Because
    engine._effective_timeout is min(caller, bound) — it only ever TIGHTENS — every caller
    that did not hand-tune the flag silently got a sixth of the review seat's sanctioned
    budget. Measured cost: two of two real Step 11 reviews exceeded 300 s (#719 at 788 s,
    #720 at 399.7 s), so both would have been SIGKILLed at the default.
    """
    review = {"role": "review", "manifest": {"bounds": {"timeout_s": 1800}}}
    build = {"role": "build", "manifest": {"bounds": {"timeout_s": 3600}}}
    assert er.resolve_dispatch_timeout(review) == 1800.0
    assert er.resolve_dispatch_timeout(build) == 3600.0


def test_resolve_dispatch_timeout_honours_an_explicit_caller_value():
    """An explicit --timeout still wins here; engine._effective_timeout clamps it to the
    bound afterwards, so this function must not pre-empt that (one clamp, one place)."""
    review = {"role": "review", "manifest": {"bounds": {"timeout_s": 1800}}}
    assert er.resolve_dispatch_timeout(review, 60.0) == 60.0
    # Above the bound is deliberately NOT clamped here — _effective_timeout owns that.
    assert er.resolve_dispatch_timeout(review, 9999.0) == 9999.0


@pytest.mark.parametrize("entry", [
    None,
    {},
    {"manifest": None},
    {"manifest": {}},
    {"manifest": {"bounds": None}},
    {"manifest": {"bounds": {}}},
    {"manifest": {"bounds": {"timeout_s": None}}},
    {"manifest": {"bounds": {"timeout_s": 0}}},
    {"manifest": {"bounds": {"timeout_s": -5}}},
    {"manifest": {"bounds": {"timeout_s": "1800"}}},
    {"manifest": {"bounds": {"timeout_s": True}}},
])
def test_resolve_dispatch_timeout_falls_back_when_no_usable_bound(entry):
    """Fail-SAFE, not fail-closed: a seat that declares no usable bound keeps the historical
    300 s rather than dispatching unbounded. bool is rejected explicitly because True would
    otherwise pass an isinstance(int) check and become a 1-second timeout.
    """
    assert er.resolve_dispatch_timeout(entry) == er.DISPATCH_TIMEOUT_FALLBACK_S == 300.0


# ---------------------------------------------------------------------------
# #733: a killed/failed dispatch must never return ok:true — process-failure
# guard at the sync/supervised/resume/recover result assemblies, with partial
# output preserved on correlation-OWNED failures and never on foreign ones.
# ---------------------------------------------------------------------------

_SHIP_MODELS = ("claude-sonnet-5", "claude-opus-4-8", "claude-fable-5")


def _identity_stub(status, *, process=None, payload="__prompt__"):
    """Every target returns `status` WITH a matching attested identity (the #733 escape
    shape). Optional process/payload overrides via dataclasses.replace."""
    import dataclasses as _dc

    def dispatch(engine, req, *, run_id, attempt_id, capture_root, digest, queued_ms, fallback_reason):
        obs = _obs(req, status=status, actual_override=req.requested_model)
        kw = {}
        if process is not None:
            kw["process"] = dict(process)
        if payload != "__prompt__":
            kw["parsed_payload"] = payload
        return _dc.replace(obs, **kw) if kw else obs
    return dispatch


def test_733_sync_timeout_with_identity_fails_with_partial(tmp_path):
    # T1 (the bug): SIGKILLed-at-timeout final attempt carrying a matching identity must be
    # ok:false / exit 3 with the partial payload preserved and flagged — never a passed gate.
    res, _ = _dispatch("ship", tmp_path, dispatch_real=_identity_stub(contract.TIMEOUT))
    assert res["ok"] is False, res
    assert res["exit"] == er.EXIT_AVAILABILITY
    assert res["error"]["code"] == "dispatch_timeout"
    assert res["error"]["retryable"] is True  # sync kill is engine-issued: positive death evidence
    assert res["partial"] is True
    assert res["partial_payload"] == "hi"
    assert "raw_capture_path" in res
    assert res["observation"]["parse_status"] == "timeout"


def test_733_sync_signalled_exit_fails(tmp_path):
    # T2 (schema-valid signalled shape): nonzero_exit status with exit_code -9
    res, _ = _dispatch("ship", tmp_path, dispatch_real=_identity_stub(
        contract.NONZERO_EXIT, process={"exit_code": -9, "timed_out": False}))
    assert res["ok"] is False and res["exit"] == er.EXIT_AVAILABILITY
    assert res["error"]["code"] == "dispatch_signalled"


def test_733_sync_nonzero_exit_fails(tmp_path):
    # T3: positive non-zero exit with identity
    res, _ = _dispatch("ship", tmp_path, dispatch_real=_identity_stub(contract.NONZERO_EXIT))
    assert res["ok"] is False and res["exit"] == er.EXIT_AVAILABILITY
    assert res["error"]["code"] == "dispatch_nonzero_exit"


def test_733_sync_clean_run_unchanged(tmp_path):
    # T4: the happy path is byte-compatible — no partial key, ok:true, exit 0
    res, _ = _dispatch("ship", tmp_path)
    assert res["ok"] is True and res["exit"] == er.EXIT_OK
    assert "partial" not in res


def test_733_sync_chain_fallback_unchanged(tmp_path):
    # T5: an availability failure on the primary still walks the chain; a later success wins
    res, _ = _dispatch("ship", tmp_path, dispatch_real=_stub(
        status_by_model={"claude-sonnet-5": contract.TIMEOUT}))
    assert res["ok"] is True and res["exit"] == er.EXIT_OK


def test_733_sync_no_identity_timeout_keeps_exit3_and_gains_partial(tmp_path):
    # T7: the pre-existing no-identity availability exhaustion stays exit 3 AND now carries
    # the partial evidence (AC4 — the payload must not be dropped on this branch either).
    res, _ = _dispatch("ship", tmp_path, dispatch_real=_stub(
        status_by_model={m: contract.TIMEOUT for m in _SHIP_MODELS}))
    assert res["ok"] is False and res["exit"] == er.EXIT_AVAILABILITY
    assert res["error"]["code"] == "chain_exhausted_availability"
    assert res["partial"] is True and res["partial_payload"] == "hi"


def test_733_sync_parse_error_with_identity_fails_allowlist(tmp_path):
    # T16 (dispatch leg): the Hermes shape — parse_error, matching identity, exit 0, no payload
    res, _ = _dispatch("ship", tmp_path, dispatch_real=_identity_stub(
        contract.PARSE_ERROR, payload=None))
    assert res["ok"] is False and res["exit"] == er.EXIT_AVAILABILITY
    assert res["error"]["code"] == "dispatch_parse_error"
    assert res["partial"] is False  # nothing parsed -> must not claim a partial exists


def test_733_sync_usage_unavailable_stays_ok(tmp_path):
    # T16 (allowlist's accepted degraded state): attested identity, output, no usage counts
    res, _ = _dispatch("ship", tmp_path, dispatch_real=_identity_stub(contract.USAGE_UNAVAILABLE))
    assert res["ok"] is True and res["exit"] == er.EXIT_OK


def test_733_sync_breach_beats_timeout_and_carries_partial(tmp_path):
    # T14 (sync leg): identity breach + timeout evidence on the SAME observation -> the
    # enforcement verdict (exit 4) retains precedence, and the owned partial evidence rides.
    res, _ = _dispatch("ship", tmp_path, dispatch_real=_stub(
        status_by_model={m: contract.TIMEOUT for m in _SHIP_MODELS},
        actual_by_model={m: "claude-wrong-9" for m in _SHIP_MODELS}))
    assert res["ok"] is False and res["exit"] == er.EXIT_ENFORCEMENT
    assert res["partial"] is True and res["partial_payload"] == "hi"


def _failed_obs_dict(status="timeout", exit_code=0, timed_out=True, payload="partial-review-text",
                     correlation_id=None):
    """A schema-valid FAILED observation dict that still attests the matching identity."""
    o = _valid_obs(correlation_id=correlation_id)
    o["parse_status"] = status
    o["process"] = {"exit_code": exit_code, "timed_out": timed_out}
    o["usage"] = None
    o["parsed_payload"] = payload
    return o


def test_733_supervised_completed_timeout_envelope_fails(tmp_path, monkeypatch):
    # T8: a supervised job that reaches state=completed with an availability-shaped envelope
    # carrying a matching identity must fail, with the owned partial evidence attached.
    res, _sup, _qc, _calls = _supervised(tmp_path, monkeypatch=monkeypatch,
                                         obs=_failed_obs_dict())
    assert res["ok"] is False, res
    assert res["exit"] == er.EXIT_AVAILABILITY
    assert res["error"]["code"] == "supervised_dispatch_timeout"
    assert res["error"]["retryable"] is False  # completed-state failure is NOT proven death
    assert res["partial"] is True and res["partial_payload"] == "partial-review-text"


def test_733_supervised_completed_signalled_envelope_fails(tmp_path, monkeypatch):
    res, _sup, _qc, _calls = _supervised(tmp_path, monkeypatch=monkeypatch,
                                         obs=_failed_obs_dict(status="nonzero_exit",
                                                              exit_code=-9, timed_out=False))
    assert res["ok"] is False and res["exit"] == er.EXIT_AVAILABILITY
    assert res["error"]["code"] == "supervised_dispatch_signalled"


def test_733_supervised_foreign_correlation_carries_no_partial(tmp_path, monkeypatch):
    # T15: a correlation_mismatch refusal must never leak the foreign observation's payload
    res, _sup, _qc, _calls = _supervised(tmp_path, monkeypatch=monkeypatch,
                                         obs=_failed_obs_dict(correlation_id="a-foreign-cid"))
    assert res["exit"] == er.EXIT_ENFORCEMENT
    assert res["error"]["code"] == "correlation_mismatch"
    for k in ("partial", "partial_payload", "raw_capture_path", "observation"):
        assert k not in res, k


def test_733_resume_completed_timeout_envelope_fails(tmp_path):
    # T9: same pair through resume_dispatch
    sup = _ResumeSup(obs=_failed_obs_dict())
    res = er.resume_dispatch(**_resume_kw(tmp_path, sup))
    assert res["ok"] is False and res["exit"] == er.EXIT_AVAILABILITY
    assert res["error"]["code"] == "resume_dispatch_timeout"
    assert res["error"]["retryable"] is False
    assert res["partial"] is True and res["partial_payload"] == "partial-review-text"


def test_733_resume_foreign_correlation_carries_no_partial(tmp_path):
    sup = _ResumeSup(obs=_failed_obs_dict(correlation_id="a-foreign-cid"))
    res = er.resume_dispatch(**_resume_kw(tmp_path, sup))
    assert res["exit"] == er.EXIT_ENFORCEMENT and res["error"]["code"] == "correlation_mismatch"
    for k in ("partial", "partial_payload", "raw_capture_path", "observation"):
        assert k not in res, k


def test_733_recover_completed_timeout_envelope_fails(tmp_path):
    # T13: a recovered completed entry whose envelope fails the predicate is availability, not ok
    rec = _completed_record(tmp_path, seat="ship")

    class _TimeoutRecoverSup(_RecoverSup):
        def await_job(self, record, *, timeout_s=3600.0):
            self.await_calls.append(record.session_name)
            o = _failed_obs_dict()
            o["correlation_id"] = "orig#resume1"
            return "completed", o

    res, _audit = _recover(tmp_path, _TimeoutRecoverSup(rec), seed_seat="ship")
    assert res["ok"] is False and res["exit"] == er.EXIT_AVAILABILITY
    entry = res["results"][0]
    assert entry["verify"] == "process_failure: timeout"
    assert entry["partial"] is True and entry["partial_payload"] == "partial-review-text"


def test_733_collect_timeout_observation_does_not_authorize(tmp_path):
    # T11: a verified-identity timeout observation bound to the build receipt must NOT
    # authorize work-product promotion.
    res, _audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), _FakeMgr(),
                           obs_status="timeout")
    assert res["ok"] is False and res["exit"] == er.EXIT_ENFORCEMENT
    assert res["error"]["code"] == "unauthorized_work_product"


def test_733_supervised_noncompleted_wrong_identity_is_enforcement(tmp_path, monkeypatch):
    # 8a R1-H1/R2-H1 (narrowed): attested-WRONG identity wins over the state verdict
    res, _sup, _qc, _calls = _supervised(tmp_path, monkeypatch=monkeypatch, state="timed_out",
                                         obs=_valid_obs(actual="claude-wrong-9"))
    assert res["exit"] == er.EXIT_ENFORCEMENT
    assert res["error"]["code"] == "requested_actual_mismatch"
    assert res["partial"] is False  # payload None on _valid_obs — flagged honestly


def test_733_supervised_noncompleted_missing_identity_keeps_state_verdict(tmp_path, monkeypatch):
    # identity_missing must NOT become a breach: synthetic no-identity death envelopes keep
    # the retryable state verdict (honest-death recovery unbroken)
    o = _valid_obs()
    o["actual_model"] = None
    o["parse_status"] = "parse_error"
    o["usage"] = None
    res, _sup, _qc, _calls = _supervised(tmp_path, monkeypatch=monkeypatch, state="exited_no_sentinel",
                                         obs=o)
    assert res["exit"] == er.EXIT_AVAILABILITY
    assert res["error"]["code"] == "supervised_exited_no_sentinel"
    assert res["error"]["retryable"] is True


def test_733_resume_noncompleted_wrong_identity_is_enforcement(tmp_path):
    sup = _ResumeSup(state="timed_out", obs=_valid_obs(actual="claude-wrong-9"))
    res = er.resume_dispatch(**_resume_kw(tmp_path, sup))
    assert res["exit"] == er.EXIT_ENFORCEMENT
    assert res["error"]["code"] == "requested_actual_mismatch"


def test_733_sync_nondeath_failure_is_not_retryable(tmp_path):
    # 8a R2-H3: parse_error is a definite, potentially effectful failure — never invite a retry
    res, _ = _dispatch("ship", tmp_path, dispatch_real=_identity_stub(
        contract.PARSE_ERROR, payload=None))
    assert res["error"]["code"] == "dispatch_parse_error"
    assert res["error"]["retryable"] is False


def test_733_recover_foreign_child_cid_refused_even_without_expected(tmp_path):
    # 8a R2-H4: exp_cid=None (legacy recovery) + a non-null foreign child correlation must refuse
    rec = _completed_record(tmp_path, seat="ship")

    class _ForeignSup(_RecoverSup):
        def recover(self, run_id, *, dispatch_gate):
            # LEGACY shape: the gate authorizes with correlation_id=None, so the recovery
            # derives exp_cid=None — the exact bypass R2-H4 names
            import dataclasses as _dc  # noqa: PLC0415
            from phase_executor.supervisor import RecoveryAction  # noqa: PLC0415  # pylint: disable=no-name-in-module
            authz = dispatch_gate(record=self._record, correlation_id=None, recovered_from="orig")
            if authz is None:
                return [RecoveryAction(self._record.identity, "relaunch_refused (gate)", self._record)]
            new = _dc.replace(self._record, receipt_nonce=authz.receipt_nonce)
            return [RecoveryAction(self._record.identity, "relaunch", new)]

        def await_job(self, record, *, timeout_s=3600.0):
            self.await_calls.append(record.session_name)
            return "completed", _valid_obs(correlation_id="a-foreign-cid")

    res, audit = _recover(tmp_path, _ForeignSup(rec), seed_seat="ship")
    assert res["exit"] == er.EXIT_ENFORCEMENT
    entry = next(e for e in res["results"] if "verify" in e)
    assert entry["verify"].startswith("correlation_mismatch")
    for k in ("partial_payload", "raw_capture_path", "observation"):
        assert k not in entry, k


def test_733_attach_partial_falsy_payloads_are_partials():
    # 8a R1-M3: {}, [], "", 0, False ARE payloads — only None is no-payload
    for payload in ({}, [], "", 0, False):
        res = er._attach_partial({"ok": False}, {"parsed_payload": payload})  # pylint: disable=protected-access
        assert res["partial"] is True, payload
    assert er._attach_partial({"ok": False}, {"parsed_payload": None})["partial"] is False  # pylint: disable=protected-access


def test_733_attach_partial_broken_to_dict_never_raises():
    class _Broken:
        def to_dict(self):
            raise TypeError("boom")

    class _NonDict:
        def to_dict(self):
            return ["not-a-dict"]

    for bad in (_Broken(), _NonDict()):
        res = er._attach_partial({"ok": False}, bad)  # pylint: disable=protected-access
        assert res["partial"] is False and res["partial_payload"] is None


# ---------------------------------------------------------------------------
# #733 Step-11 pre-PR review findings (R1-H1, R1-H2, R2-H2) — red-before-green
# ---------------------------------------------------------------------------

def _no_identity_timeout_obs():
    """A completed-state envelope shaped like the supervisor's synthetic timeout: no attested
    identity (verify_post: ok=True, verified=False), process evidence says timeout."""
    o = _valid_obs()
    o["actual_model"] = None
    o["parse_status"] = "timeout"
    o["usage"] = None
    o["process"] = {"exit_code": None, "timed_out": True}
    o["parsed_payload"] = "partial text"
    return o


def test_733_s11_supervised_timeout_unverified_kill_not_retryable(tmp_path, monkeypatch):
    # R1-H1: await_job returns "timed_out" whether or not _kill_job PROVED death; the fresh
    # registry record carries quarantine_reason exactly when it did not. Residue is not proven
    # death — the ratified policy forbids inviting a retry of a possibly-still-running mutation.
    import types as _types
    fresh = _types.SimpleNamespace(quarantine_reason="timeout kill unverified: residue")
    res, _sup, _qc, _calls = _supervised(tmp_path, monkeypatch=monkeypatch,
                                         state="timed_out", fresh=fresh)
    assert res["ok"] is False
    assert res["error"]["code"] == "supervised_timed_out"
    assert res["error"]["retryable"] is False
    assert res["exit"] == er.EXIT_INTERNAL  # residue parity with completed_with_residue


def test_733_s11_supervised_timeout_clean_kill_stays_retryable(tmp_path, monkeypatch):
    # R1-H1 counterpart: a VERIFIED kill (no quarantine_reason) is positive death evidence.
    import types as _types
    fresh = _types.SimpleNamespace(quarantine_reason=None)
    res, _sup, _qc, _calls = _supervised(tmp_path, monkeypatch=monkeypatch,
                                         state="timed_out", fresh=fresh)
    assert res["ok"] is False
    assert res["error"]["code"] == "supervised_timed_out"
    assert res["error"]["retryable"] is True
    assert res["exit"] == er.EXIT_AVAILABILITY


def test_733_s11_resume_timeout_unverified_kill_not_retryable(tmp_path):
    import types as _types
    fresh = _types.SimpleNamespace(quarantine_reason="timeout kill unverified: residue")
    sup = _ResumeSup(state="timed_out", fresh=fresh)
    res = er.resume_dispatch(**_resume_kw(tmp_path, sup))
    assert res["ok"] is False
    assert res["error"]["code"] == "resume_timed_out"
    assert res["error"]["retryable"] is False
    assert res["exit"] == er.EXIT_INTERNAL


def test_733_s11_supervised_completed_no_identity_process_failure_not_retryable(
        tmp_path, monkeypatch):
    # R1-H2: process-failure evidence must be evaluated BEFORE the unverified verdict — a
    # missing identity must never make a failed envelope MORE retryable than one that attested.
    res, _sup, _qc, _calls = _supervised(tmp_path, monkeypatch=monkeypatch,
                                         state="completed", obs=_no_identity_timeout_obs())
    assert res["ok"] is False
    assert res["error"]["code"] == "supervised_dispatch_timeout"  # not supervised_unverified
    assert res["error"]["retryable"] is False
    assert res["exit"] == er.EXIT_AVAILABILITY
    assert res["partial"] is True and res["partial_payload"] == "partial text"


def test_733_s11_resume_completed_no_identity_process_failure_not_retryable(tmp_path):
    sup = _ResumeSup(obs=_no_identity_timeout_obs())
    res = er.resume_dispatch(**_resume_kw(tmp_path, sup))
    assert res["ok"] is False
    assert res["error"]["code"] == "resume_dispatch_timeout"  # not resume_unverified
    assert res["error"]["retryable"] is False
    assert res["exit"] == er.EXIT_AVAILABILITY
    assert res["partial"] is True


def test_733_s11_collect_refuses_foreign_seat_observation(tmp_path):
    # R2-H2: the authorizing observation must BIND to the passing build receipt's identity —
    # same seat/run/correlation — not merely share its nonce with verified identity.
    from phase_executor import contract  # noqa: PLC0415  # pylint: disable=no-name-in-module
    rec = _completed_record(tmp_path)
    reg, mgr = _FakeReg(rec), _FakeMgr()
    audit = enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    audit._write_locked({  # pylint: disable=protected-access
        "kind": "receipt", "nonce": "rn1", "seat": "build", "correlation_id": "c1",
        "attempt_id": "0-a",
        "target_identity": ["codex-model", "openai", "cli", "api_key", "codex", None, None],
        "config_digest": "sha256:d", "gate_digest": "sha256:g", "author_provider": None,
        "verdict": "pass", "violations": [], "role": "build", "gate_outcome": "single",
        "gate_input_digest": "sha256:gi", "recovered_from": None})
    foreign = contract.Observation(
        run_id="run1", attempt_id="0-a", correlation_id="c1", seat="review", engine="codex",
        transport="cli", requested_model="codex-model", actual_model="codex-model",
        prompt_hash="sha256:p", context_hashes=[], usage={"input": 1, "output": 1},
        timing_ms=1, queued_ms=0, process={"exit_code": 0, "timed_out": False},
        parse_status="ok", parsed_payload=None, raw_capture_path=None, fallback_reason=None,
        routing_config_digest="sha256:d").to_dict()
    foreign["dispatched_lane"] = {"provider": "openai", "transport": "cli",
                                  "auth_mode": "api_key", "pool": "codex", "credential_ref": None}
    audit._write_locked({"kind": "observation", "receipt_nonce": "rn1",  # pylint: disable=protected-access
                         "observation": foreign})
    res, _audit = _collect(tmp_path, reg, mgr, seed=False, audit=audit)
    assert res["ok"] is False
    assert res["error"]["code"] == "unauthorized_work_product"


# ---------------------------------------------------------------------------
# #767: per-task exact-path collection — promote_paths param + intent identity v2
# ---------------------------------------------------------------------------

# Step-11 adv F2: exact-path collection is confined to the per-receipt temp-ref namespace
# with create semantics — these tests use the canonical ref for the fixture nonce rn1.
COLLECT_REF = "refs/rawgentic/collect/rn1"


def test_collect_promote_paths_admits_declared_only(tmp_path):
    mgr = _FakeMgr(changed=["src/a.py"])
    res, _ = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr,
                      kind="code", promote_paths=["src/a.py"], target=COLLECT_REF)
    assert res["ok"] and res["status"] == "recorded"


def test_collect_promote_paths_refuses_outside_path(tmp_path):
    mgr = _FakeMgr(changed=["src/a.py", "src/evil.py"])
    res, _ = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr,
                      kind="code", promote_paths=["src/a.py"], target=COLLECT_REF)
    assert res["ok"] is False
    assert res["error"]["code"] == "promote_refused"


def test_collect_promote_paths_empty_is_malformed(tmp_path):
    res, _ = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), _FakeMgr(),
                      kind="code", promote_paths=[])
    assert res["ok"] is False
    assert res["exit"] == er.EXIT_MALFORMED


def test_collect_default_policy_stays_appendix(tmp_path):
    # back-compat pin: no promote_paths → the appendix prefix policy, byte-identical behavior
    res, _ = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), _FakeMgr())
    assert res["ok"] and res["status"] == "recorded"
    mgr2 = _FakeMgr(changed=["src/a.py"])  # non-appendix change under the DEFAULT policy
    second = tmp_path / "second"
    second.mkdir()
    res2, _ = _collect(second, _FakeReg(_completed_record(tmp_path)), mgr2)
    assert res2["ok"] is False and res2["error"]["code"] == "promote_refused"


def test_collect_intent_conflict_on_different_target(tmp_path):
    # #767 (pass-2 F3, tightened by Step-11 adv F2): an exact-path re-request against any
    # non-canonical target never even reaches the intent — the ref gate refuses it first.
    rec = _completed_record(tmp_path)
    reg = _FakeReg(rec)
    mgr = _FakeMgr(changed=["src/a.py"])
    r1, _ = _collect(tmp_path, reg, mgr, kind="code", promote_paths=["src/a.py"],
                     target=COLLECT_REF)
    assert r1["ok"] and r1["status"] == "recorded"
    r2, _ = _collect(tmp_path, reg, mgr, kind="code", promote_paths=["src/a.py"],
                     target="refs/heads/other-branch")
    assert r2["ok"] is False
    assert r2["error"]["code"] == "invalid_collect_ref"


def test_collect_intent_conflict_on_different_paths(tmp_path):
    rec = _completed_record(tmp_path)
    reg = _FakeReg(rec)
    mgr = _FakeMgr(changed=["src/a.py"])
    r1, _ = _collect(tmp_path, reg, mgr, kind="code", promote_paths=["src/a.py"],
                     target=COLLECT_REF)
    assert r1["ok"] and r1["status"] == "recorded"
    r2, _ = _collect(tmp_path, reg, mgr, kind="code", promote_paths=["src/a.py", "src/b.py"],
                     target=COLLECT_REF)
    assert r2["ok"] is False
    assert r2["error"]["code"] == "intent_conflict"


def test_collect_identical_rerun_still_already_recorded(tmp_path):
    rec = _completed_record(tmp_path)
    reg = _FakeReg(rec)
    mgr = _FakeMgr(changed=["src/a.py"])
    r1, _ = _collect(tmp_path, reg, mgr, kind="code", promote_paths=["src/a.py"],
                     target=COLLECT_REF)
    assert r1["ok"] and r1["status"] == "recorded"
    r2, _ = _collect(tmp_path, reg, mgr, kind="code", promote_paths=["src/a.py"],
                     target=COLLECT_REF)
    assert r2["ok"] and r2["status"] == "already_recorded"


def test_collect_v2_binding_fields_on_audited_records(tmp_path):
    # rev-4 F3: new records carry binding_version=2 + target_ref + paths_digest
    mgr = _FakeMgr(changed=["src/a.py"])
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr,
                          kind="code", promote_paths=["src/a.py"], target=COLLECT_REF)
    assert res["ok"]
    wps = [r for r in audit.records() if r.get("kind") == "work_product"]
    exps = [r for r in audit.records() if r.get("kind") == "expected_work_product"]
    for r in wps + exps:
        assert r.get("binding_version") == 2
        assert r.get("target_ref") == COLLECT_REF
        assert r.get("paths_digest") and r["paths_digest"] != "appendix-default"


def test_collect_cli_accepts_promote_path_flag(tmp_path):
    # argparse-level pin: the flag exists and repeats (a SystemExit(2) means it does not parse).
    ws = tmp_path / "ws.json"
    ws.write_text(json.dumps({"version": 1, "projects": []}), encoding="utf-8")
    rc = er.main(["collect-work-product", "--run-id", "r1", "--session-name", "s1",
                  "--target-ref", "refs/heads/x", "--expected-target-sha", "0" * 40,
                  "--kind", "code", "--promote-path", "src/a.py", "--promote-path", "src/b.py",
                  "--workspace", str(ws), "--project", "nope"])
    assert isinstance(rc, int)  # parses; downstream config failure is fine here


# ---------------------------------------------------------------------------
# #767 Step-8a fixes: empty work product, intent strictness, digest encoding, dedup key
# ---------------------------------------------------------------------------

def test_collect_empty_worktree_refused(tmp_path):
    # 8a R1/R2-H2: zero changed paths must refuse — an empty commit would advance the branch
    # and satisfy "branch actually advanced" vacuously.
    mgr = _FakeMgr(changed=[])
    res, _ = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr,
                      kind="code", promote_paths=["src/a.py"], target=COLLECT_REF)
    assert res["ok"] is False
    assert res["error"]["code"] == "empty_work_product"


def test_collect_landed_intent_locks_all_fields(tmp_path):
    # 8a R2-H1: with a LANDED intent, a re-request whose candidate/expected differ must refuse —
    # the old matching3 predicate skipped the conflict check entirely on that path. Exercised on
    # the appendix path (Step-11 adv F2's ref gate now precedes the intent on exact-path).
    rec = _completed_record(tmp_path)
    reg = _FakeReg(rec)
    mgr = _FakeMgr()
    r1, _ = _collect(tmp_path, reg, mgr)
    assert r1["ok"] and r1["status"] == "recorded"
    r2, _ = _collect(tmp_path, reg, mgr,
                     expected="1234567890abcdef1234567890abcdef12345678")
    assert r2["ok"] is False
    assert r2["error"]["code"] == "intent_conflict"


def test_collect_corrupt_intent_refuses(tmp_path):
    # 8a R2-H1: an unreadable intent must not silently degrade to "no intent".
    intents = tmp_path / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / "collect-rn1.json").write_text("{not json", encoding="utf-8")
    res, _ = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), _FakeMgr())
    assert res["ok"] is False
    assert res["error"]["code"] == "intent_corrupt"


def test_collect_legacy_landed_intent_verifies_target(tmp_path):
    # 8a R1-H1: a pre-#767 (legacy) landed intent carries no target binding — success requires
    # the REQUESTED target to actually hold the landed sha; the old default made any target pass.
    intents = tmp_path / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / "collect-rn1.json").write_text(json.dumps({
        "receipt_nonce": "rn1", "candidate_tree_sha": "ctree",
        "expected_target_sha": "0" * 40, "new_sha": "newsha", "consumed": True}), encoding="utf-8")
    ok_mgr = _FakeMgr(tip={"sha": "newsha", "tree": "ctree", "parents": (), "message": "m"})
    res, _ = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), ok_mgr)
    assert res["ok"] and res["status"] == "already_recorded"
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "intents").mkdir()
    ((bad / "intents") / "collect-rn1.json").write_text(json.dumps({
        "receipt_nonce": "rn1", "candidate_tree_sha": "ctree",
        "expected_target_sha": "0" * 40, "new_sha": "newsha", "consumed": True}), encoding="utf-8")
    res2, _ = _collect(bad, _FakeReg(_completed_record(tmp_path)), _FakeMgr(tip=None))
    assert res2["ok"] is False
    assert res2["error"]["code"] == "intent_conflict"


def test_collect_paths_digest_encoding_unambiguous(tmp_path):
    # 8a R1-M3: newline-joined serialization collides ["a\nb","c"] with ["a","b\nc"] — the
    # recorded digests must differ.
    d1, d2 = tmp_path / "one", tmp_path / "two"
    d1.mkdir(), d2.mkdir()
    m1 = _FakeMgr(changed=["a\nb", "c"])
    m2 = _FakeMgr(changed=["a", "b\nc"])
    _r1, a1 = _collect(d1, _FakeReg(_completed_record(tmp_path)), m1,
                       kind="code", promote_paths=["a\nb", "c"], target=COLLECT_REF)
    _r2, a2 = _collect(d2, _FakeReg(_completed_record(tmp_path)), m2,
                       kind="code", promote_paths=["a", "b\nc"], target=COLLECT_REF)
    assert _r1["ok"] and _r2["ok"]
    pd1 = [r for r in a1.records() if r.get("kind") == "work_product"][0]["paths_digest"]
    pd2 = [r for r in a2.records() if r.get("kind") == "work_product"][0]["paths_digest"]
    assert pd1 != pd2


def test_collect_audit_dedup_uses_full_binding_key(tmp_path):
    # 8a R1-M4: a same-3-tuple record with a DIFFERENT binding must not suppress writing ours.
    audit = enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    audit.append_work_product(receipt_nonce="rn1", candidate_tree_sha="ctree", new_sha="newsha",
                              work_product={"kind": "docs", "promotion_status": "promoted"})
    mgr = _FakeMgr(changed=["src/a.py"])
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr,
                          kind="code", promote_paths=["src/a.py"], audit=audit,
                          target=COLLECT_REF)
    assert res["ok"]
    wps = _wp_records(audit)
    assert len(wps) == 2  # the v1 foreign record did NOT suppress our v2 binding
    assert any(r.get("binding_version") == 2 and r.get("paths_digest") != "appendix-default"
               for r in wps)


# ---------------------------------------------------------------------------
# #767 Step-11 fixes: unconditional empty guard, canonical collect ref, legacy/semantic
# intent strictness, F-l rebind guard, promoted-tree (TOCTOU) binding
# ---------------------------------------------------------------------------

def test_collect_appendix_empty_worktree_refused(tmp_path):
    # Step-11 adv F1: the empty-work-product guard is UNCONDITIONAL — a default-policy
    # (appendix) collection with zero changed paths must refuse, not land an empty commit
    # that satisfies "branch actually advanced" while every diff-scoped gate passes vacuously.
    mgr = _FakeMgr(changed=[])
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr)
    assert res["ok"] is False
    assert res["error"]["code"] == "empty_work_product"
    assert mgr.promote_calls == []
    assert _wp_records(audit) == []


def test_collect_exact_path_requires_canonical_ref(tmp_path):
    # Step-11 adv F2 + lane R1-F1: exact-path collection must target ONLY the per-receipt
    # temp ref refs/rawgentic/collect/<nonce> — otherwise a caller-controlled target reaches
    # update-ref and can advance a checked-out refs/heads/* branch with a stale checkout.
    mgr = _FakeMgr(changed=["src/a.py"])
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr,
                          kind="code", promote_paths=["src/a.py"])  # default refs/heads target
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_collect_ref"
    assert mgr.promote_calls == []  # refused BEFORE any intent write or promote
    assert _wp_records(audit) == []
    assert not (tmp_path / "intents" / "collect-rn1.json").exists()


def test_collect_exact_path_requires_zero_sha_create(tmp_path):
    # Step-11 adv F2: the canonical temp ref uses create semantics — a real expected SHA
    # would let a caller CAS an EXISTING ref through the exact-path route.
    mgr = _FakeMgr(changed=["src/a.py"])
    res, _ = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr,
                      kind="code", promote_paths=["src/a.py"], target=COLLECT_REF,
                      expected="1234567890abcdef1234567890abcdef12345678")
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_collect_ref"


def test_collect_code_kind_requires_paths(tmp_path):
    # lane R1-F1 (flag half): code collection must not silently downgrade to the appendix
    # policy when --promote-path is omitted — the staging backstop is not an optional flag.
    res, _ = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), _FakeMgr(), kind="code")
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_promote_paths"


def test_collect_unlanded_legacy_intent_always_refuses(tmp_path):
    # Step-11 adv F3: an unlanded legacy intent carries no target identity — in the F-l window
    # its promotion may have landed on an unknown ORIGINAL target, so accepting it for ANY
    # requested target (the old appendix-default carve-out) can spend the receipt twice.
    intents = tmp_path / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / "collect-rn1.json").write_text(json.dumps({
        "receipt_nonce": "rn1", "candidate_tree_sha": "ctree",
        "expected_target_sha": "0" * 40, "new_sha": None, "consumed": False}), encoding="utf-8")
    mgr = _FakeMgr()
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr)
    assert res["ok"] is False
    assert res["error"]["code"] == "intent_conflict"
    assert mgr.promote_calls == []
    assert _wp_records(audit) == []


def test_collect_non_dict_intent_refuses(tmp_path):
    # Step-11 adv F4 + lane R1-F4: a parseable non-dict intent is CORRUPTION, not absence —
    # degrading to the absent-intent path overwrites the receipt's only recovery identity.
    intents = tmp_path / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / "collect-rn1.json").write_text("[]", encoding="utf-8")
    res, _ = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), _FakeMgr())
    assert res["ok"] is False
    assert res["error"]["code"] == "intent_corrupt"


def test_collect_wrong_nonce_intent_refuses(tmp_path):
    # adv F4: the intent file is nonce-NAMED — a dict carrying a different nonce is corruption.
    intents = tmp_path / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / "collect-rn1.json").write_text(json.dumps({
        "receipt_nonce": "OTHER", "candidate_tree_sha": "ctree",
        "expected_target_sha": "0" * 40, "new_sha": None, "consumed": False}), encoding="utf-8")
    res, _ = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), _FakeMgr())
    assert res["ok"] is False
    assert res["error"]["code"] == "intent_corrupt"


def test_collect_mistyped_intent_fields_refuse(tmp_path):
    # adv F4: required fields must be correctly typed — a right-nonce intent with a mangled
    # candidate_tree_sha (or non-bool consumed, or non-str new_sha) is corruption.
    for mangled in ({"candidate_tree_sha": 123}, {"consumed": "yes"}, {"new_sha": 7},
                    {"expected_target_sha": ""}):
        sub = tmp_path / f"case-{sorted(mangled)[0]}-{type(list(mangled.values())[0]).__name__}"
        intents = sub / "intents"
        intents.mkdir(parents=True, exist_ok=True)
        base = {"receipt_nonce": "rn1", "candidate_tree_sha": "ctree",
                "expected_target_sha": "0" * 40, "new_sha": None, "consumed": False}
        base.update(mangled)
        (intents / "collect-rn1.json").write_text(json.dumps(base), encoding="utf-8")
        res, _ = _collect(sub, _FakeReg(_completed_record(tmp_path)), _FakeMgr())
        assert res["ok"] is False, mangled
        assert res["error"]["code"] == "intent_corrupt", mangled


def test_collect_unlanded_rebind_after_landed_promotion_refuses(tmp_path):
    # lane R1-F4 (F-l half): an unlanded same-binding intent whose ORIGINAL promotion actually
    # LANDED (live tip matches ITS candidate tree, parented on ITS expected) must refuse an
    # identity-changed retry — rebinding would erase the only recovery identity and let one
    # receipt authorize a second update.
    intents = tmp_path / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / "collect-rn1.json").write_text(json.dumps({
        "receipt_nonce": "rn1", "candidate_tree_sha": "oldtree",
        "expected_target_sha": "t1sha", "target_ref": "refs/heads/integration",
        "paths_digest": "appendix-default", "new_sha": None, "consumed": False}),
        encoding="utf-8")
    mgr = _FakeMgr(tip={"sha": "t2sha", "tree": "oldtree", "parents": ("t1sha",), "message": "m"})
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr, expected="t2sha")
    assert res["ok"] is False
    assert res["error"]["code"] == "intent_conflict"
    assert mgr.promote_calls == []
    assert _wp_records(audit) == []


def test_collect_unlanded_rebind_without_landing_still_recuts(tmp_path):
    # The legitimate retry-after-CAS-refusal re-cut (rev-4 design): same binding, new candidate,
    # and the live target shows NO landing of the OLD candidate → re-promote proceeds.
    intents = tmp_path / "intents"
    intents.mkdir(parents=True, exist_ok=True)
    (intents / "collect-rn1.json").write_text(json.dumps({
        "receipt_nonce": "rn1", "candidate_tree_sha": "oldtree",
        "expected_target_sha": "t1sha", "target_ref": "refs/heads/integration",
        "paths_digest": "appendix-default", "new_sha": None, "consumed": False}),
        encoding="utf-8")
    mgr = _FakeMgr(tip=None)
    res, _ = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr)
    assert res["ok"] and res["status"] == "recorded"
    assert mgr.promote_calls == [("refs/heads/integration", "0" * 40)]


def test_collect_promoted_tree_mismatch_refuses(tmp_path):
    # lane R1-F3 (TOCTOU): promote's COMMITTED tree must equal the candidate tree captured by
    # the initial evidence (snapshot A == snapshot B) — a worktree that drifted between the
    # empty-check/intent and the commit must refuse, never record.
    class _DriftMgr(_FakeMgr):
        def promote(self, handle, *, target_ref, expected_target_sha, message, path_policy):
            from phase_executor.worktree import PromotionResult  # noqa: PLC0415  # pylint: disable=no-name-in-module
            self.promote_calls.append((target_ref, expected_target_sha))
            return PromotionResult(promoted=True, new_target_sha="newsha", base_sha="b",
                                   head_sha="h", changed_paths=tuple(self._changed), reason="",
                                   content_tree_sha="driftedtree")

    mgr = _DriftMgr()
    res, audit = _collect(tmp_path, _FakeReg(_completed_record(tmp_path)), mgr)
    assert res["ok"] is False
    assert res["error"]["code"] == "promoted_tree_mismatch"
    assert _wp_records(audit) == [] and _ewp_records(audit) == []


def test_collect_appendix_different_target_intent_conflict(tmp_path):
    # pass-2 F3 pinned on the appendix path (target may legitimately vary there): a consumed
    # intent re-requested against a DIFFERENT target refuses loudly, never already_recorded.
    rec = _completed_record(tmp_path)
    reg = _FakeReg(rec)
    mgr = _FakeMgr()
    r1, _ = _collect(tmp_path, reg, mgr)
    assert r1["ok"] and r1["status"] == "recorded"
    r2, _ = _collect(tmp_path, reg, mgr, target="refs/heads/other-branch")
    assert r2["ok"] is False
    assert r2["error"]["code"] == "intent_conflict"


# ---------------------------------------------------------------------------
# #762 Task 2 (R5-C): the reconcile CLI enumerates the landing buckets — one test per
# surfaced bucket, hard in provisional mode; pre_cutover is a REPORT, never an ok-flip.
# ---------------------------------------------------------------------------

_LANE762 = {"provider": "anthropic", "transport": "native", "auth_mode": "subscription_oauth",
            "pool": "claude", "credential_ref": None}


def _r762_receipt(nonce, cid):
    tid = list(enforce.target_identity({"model": "claude-sonnet-5", "lane": _LANE762}))
    return {"kind": "receipt", "nonce": nonce, "seat": "analysis", "correlation_id": cid,
            "attempt_id": "0", "target_identity": tid, "config_digest": "sha256:cfg",
            "verdict": "pass"}


def _r762_obs(nonce, cid):
    inner = {"schema_version": "1", "run_id": "run1", "attempt_id": "0", "seat": "analysis",
             "correlation_id": cid,
             "engine": "claude", "transport": "native", "requested_model": "claude-sonnet-5",
             "actual_model": "claude-sonnet-5", "prompt_hash": "sha256:x", "context_hashes": [],
             "usage": {"input": 1, "output": 1}, "timing_ms": 1, "queued_ms": 0,
             "process": {"exit_code": 0, "timed_out": False}, "parse_status": "ok",
             "parsed_payload": None, "raw_capture_path": None, "fallback_reason": None,
             "routing_config_digest": "sha256:cfg", "dispatched_lane": _LANE762}
    return {"kind": "observation", "receipt_nonce": nonce, "observation": inner}


def _r762_code_wp(nonce, *, new="b" * 40):
    return {"kind": "work_product", "receipt_nonce": nonce, "candidate_tree_sha": "t",
            "new_sha": new,
            "work_product": {"kind": "code", "worktree_path": "/wt", "base_sha": "a" * 40,
                             "head_sha": "h", "content_tree_sha": "t",
                             "changed_paths": ["x.py"], "documents": [], "tests": [],
                             "promotion_status": "promoted"},
            "binding_version": 2, "target_ref": f"refs/rawgentic/collect/{nonce}",
            "paths_digest": "sha256:" + "ab" * 32, "expected_feature_ref": "refs/heads/feat"}


def _r762_landing(nonce, *, new="b" * 40, feature="refs/heads/feat", run_id="run1",
                  temp=None):
    return {"kind": "landed_work_product", "landing_version": 1, "receipt_nonce": nonce,
            "feature_ref": feature, "pre_sha": "a" * 40, "new_sha": new,
            "temp_ref": temp or f"refs/rawgentic/collect/{nonce}",
            "landing_status": "landed", "run_id": run_id, "ts": 1000}


def _r762_run(tmp_path, records, *, expected=("c1",), mode="final", closed=True):
    ws, repo = _analysis_project(tmp_path)
    rd = _run_dir(repo)
    lg = ledger.ExpectedCallLedger(rd, "run1")
    lg.append_initial("sha256:cfg", architecture="executor")
    for cid in expected:
        lg.append_expected("analysis", cid)
    if closed:
        lg.append_run_closed()
    (rd / "routing-audit.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return er.main(["reconcile", "--run-id", "run1", "--mode", mode,
                    "--workspace", ws, "--project", "rawgentic"])


def test_cli_reconcile_flags_unlanded_work_product(tmp_path, capsys):
    recs = [_r762_receipt("n1", "c1"), _r762_obs("n1", "c1"),
            _r762_code_wp("n1"), _r762_landing("n1"),
            _r762_receipt("n2", "c2"), _r762_obs("n2", "c2"),
            _r762_code_wp("n2", new="c" * 40)]
    rc = _r762_run(tmp_path, recs, expected=("c1", "c2"))
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ANOMALY and out["reconciled"] is False
    assert any("n2" in x for x in out["anomalies"]["unlanded_work_product"])


def test_cli_reconcile_flags_orphan_landing(tmp_path, capsys):
    recs = [_r762_receipt("n1", "c1"), _r762_obs("n1", "c1"),
            _r762_code_wp("n1"), _r762_landing("n1"), _r762_landing("nX", new="d" * 40)]
    rc = _r762_run(tmp_path, recs)
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ANOMALY
    assert any("nX" in x for x in out["anomalies"]["orphan_landing"])


def test_cli_reconcile_flags_landing_mismatch(tmp_path, capsys):
    recs = [_r762_receipt("n1", "c1"), _r762_obs("n1", "c1"),
            _r762_code_wp("n1"), _r762_landing("n1", new="d" * 40)]
    rc = _r762_run(tmp_path, recs)
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ANOMALY
    assert any("n1" in x for x in out["anomalies"]["landing_mismatch"])


def test_cli_reconcile_flags_landing_conflict(tmp_path, capsys):
    recs = [_r762_receipt("n1", "c1"), _r762_obs("n1", "c1"),
            _r762_code_wp("n1"), _r762_landing("n1"),
            _r762_landing("n1", feature="refs/heads/OTHER")]
    rc = _r762_run(tmp_path, recs)
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ANOMALY
    assert any("n1" in x for x in out["anomalies"]["landing_conflict"])


def test_cli_reconcile_flags_foreign_run_landing(tmp_path, capsys):
    # the CLI passes its own --run-id into the run-identity arm
    recs = [_r762_receipt("n1", "c1"), _r762_obs("n1", "c1"),
            _r762_code_wp("n1"), _r762_landing("n1", run_id="runFOREIGN")]
    rc = _r762_run(tmp_path, recs)
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ANOMALY
    assert any("n1" in x for x in out["anomalies"]["landing_mismatch"])


def test_cli_reconcile_reports_pre_cutover_without_failing(tmp_path, capsys):
    # R5-D: a pre-cutover code work_product (no landing records in the run) is REPORTED,
    # named, and never flips the verdict — the honest legacy bucket.
    recs = [_r762_receipt("n1", "c1"), _r762_obs("n1", "c1"), _r762_code_wp("n1")]
    rc = _r762_run(tmp_path, recs)
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_OK and out["reconciled"] is True
    assert any("n1" in x for x in out["report"]["pre_cutover_unverifiable"])


def test_cli_reconcile_landing_buckets_hard_in_provisional(tmp_path, capsys):
    # R5-C: the landing buckets are HARD failures in provisional mode (never in-flight tolerance)
    recs = [_r762_receipt("n1", "c1"), _r762_obs("n1", "c1"),
            _r762_code_wp("n1"), _r762_landing("n1"), _r762_landing("nX", new="d" * 40)]
    rc = _r762_run(tmp_path, recs, mode="provisional", closed=False)
    out = json.loads(capsys.readouterr().out)
    assert rc == er.EXIT_ANOMALY and out["reconciled"] is False


def test_landing_identity_fields_mirror_enforce():
    # mirrored constants get drift guards (repo convention): the hooks-side writer dedup and
    # the enforce-side reconcile conflict detection must key on the SAME immutable identity.
    assert er._LANDING_IDENTITY_FIELDS == enforce.LANDING_IDENTITY_FIELDS


# --- #826: a review seat cannot be dispatched with nothing attached -----------------------------
# The defect: a Step-4/8a/11 review brief that says "review the attached design" dispatched with an
# EMPTY context is a quality gate that passes while reviewing nothing. Measured at 3a017dd9 over
# this repo's own run tree: 167 review-seat observations, 117 with empty context_hashes, 10 of
# those carrying an attachment-referencing prompt. Guard mirrors the build seat's
# gate_file_required/plan_context_required precedent — refuse pre-receipt, EXIT_MALFORMED.
_RC_REFERENCING = [
    "Review the attached design. Report findings only.",
    "Review the design document supplied as CONTEXT below (the full markdown).",
    "# Step 8a review\n\nReview the attached diff for silent failures.",
    "Judge the diff below against the plan.",
    "The design is supplied as context below (the full markdown of the design doc).",
]
_RC_SELF_CONTAINED = [
    "hi",
    "# WF2 Step 11 review\n\nHere is the complete diff, inlined:\n\n```\n--- a/x\n+++ b/x\n```\n",
    "Assess whether the plan's tasks each carry a riskLevel. Plan text follows inline.",
]


def _rc_dispatch(tmp_path, *, seat="review", prompt, context=(), **kw):
    """dispatch_seat with a caller-chosen prompt + context (the module's _dispatch pins both)."""
    qc = QuotaCoordinator(tmp_path / "permits", {"claude": 2, "codex": 4, "zhipu": 2})
    audit = enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    res = er.dispatch_seat(
        seat=seat, prompt=prompt, run_id="run1", correlation_id=kw.pop("cid", "wf2:step4"),
        # the review seat's cross-model invariant needs a non-author provider (see :302) —
        # an anthropic reviewer over openai-authored work
        author_provider=kw.pop("author_provider", "openai"), effort=None, timeout=5.0,
        context=tuple(context),
        snapshot=_snapshot(), quota=qc, audit=audit, capture_root=str(tmp_path / "runs"),
        routing=routing, enforce=enforce, run_seat=run_seat,
        dispatch_real=kw.pop("dispatch_real", _stub()),
    )
    return res, audit


@pytest.mark.parametrize("prompt", _RC_REFERENCING)
def test_prompt_references_attachment_true(prompt):
    assert er.prompt_references_attachment(prompt) is True


@pytest.mark.parametrize("prompt", _RC_SELF_CONTAINED)
def test_prompt_references_attachment_false(prompt):
    assert er.prompt_references_attachment(prompt) is False


def test_prompt_references_attachment_handles_non_str():
    # fail-closed on a malformed prompt would refuse every dispatch; the predicate answers the
    # narrow question only, and a non-str simply does not reference an attachment.
    assert er.prompt_references_attachment(None) is False


def test_review_attachment_prompt_without_context_exit2_no_receipt(tmp_path):
    res, audit = _rc_dispatch(tmp_path, prompt="Review the attached design. Report findings only.")
    assert res["ok"] is False and res["exit"] == er.EXIT_MALFORMED
    assert res["error"]["code"] == "review_context_required"
    assert audit.records() == []  # refused pre-check_pre: no receipt minted, no process spawned


def test_review_attachment_prompt_with_context_proceeds(tmp_path):
    res, _ = _rc_dispatch(tmp_path, prompt="Review the attached design.",
                          context=("# Design\n\nthe actual design bytes\n",))
    assert res["ok"] is True, res


def test_review_self_contained_prompt_without_context_proceeds(tmp_path):
    # the 107 legitimate contextless review dispatches must not regress
    res, _ = _rc_dispatch(tmp_path, prompt=_RC_SELF_CONTAINED[1])
    assert res["ok"] is True, res


def test_non_review_seat_attachment_prompt_without_context_unaffected(tmp_path):
    # scoped to the seat that has the defect; a plan-seat brief is untouched
    res, _ = _rc_dispatch(tmp_path, seat="plan", prompt="Review the attached design.")
    assert res["ok"] is True, res
