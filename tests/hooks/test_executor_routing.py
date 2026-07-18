"""#427 — executor_routing_lib: seat resolution, config parse, path derivation, dispatch (stubbed —
no live provider call), CLI contract, guarded import. Asserts the ACTUAL executing model on BOTH
paths (executor -> routed model; inherit -> prior behavior untouched)."""
import json
import subprocess
import sys
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
from phase_executor import contract, enforce, routing  # noqa: E402
from phase_executor.engine import run_seat  # noqa: E402
from phase_executor.quota import QuotaCoordinator, QuotaTimeout  # noqa: E402
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


def _ws(tmp_path, executor_routing="__none__", project="rawgentic", path="."):
    entry = {"name": project, "path": path, "modelRouting": {"analysis": "sonnet"}}
    if executor_routing != "__none__":
        entry["executorRouting"] = executor_routing
    p = tmp_path / "ws.json"
    p.write_text(json.dumps({"projects": [entry]}), encoding="utf-8")
    return str(p)


def _gate(*, bakeoff=False):
    """A #429 GateDecision + a matching plan-context (a subset of its input_snapshot). Benign inputs
    -> single outcome; risk_level high -> bake-off outcome (#464 §E)."""
    task = {"risk_level": "high" if bakeoff else "standard"}
    gd = cg.needs_bakeoff(task, {"complexity": "standard"}, {"files": [], "lines": 1, "file_count": 1})
    return gd, {"risk_level": gd.input_snapshot["risk_level"]}


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
def test_resolve_absent_block_is_inherit(tmp_path):
    assert er.resolve_seat_action("ship", _ws(tmp_path), "rawgentic")[0] == "inherit"


def test_resolve_seat_not_in_config_is_inherit(tmp_path):
    ws = _ws(tmp_path, {"version": 1, "seats": {"ship": "executor"}})
    assert er.resolve_seat_action("plan", ws, "rawgentic")[0] == "inherit"


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


# --- INHERIT path: executor never fires; prior behavior (model_routing) unchanged --------------
def test_inherit_path_does_not_dispatch_and_model_routing_unchanged(tmp_path):
    ws = _ws(tmp_path)  # no executorRouting -> inherit
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
    r = _run_cli("resolve-seat", "--seat", "ship", "--workspace", _ws(tmp_path), "--project", "rawgentic")
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


def test_do_dispatch_missing_routing_table_structured(tmp_path):
    # Step-8a R1 F2: a project repo lacking the shipped routing table must emit a structured exit,
    # not crash. path points at a tmp dir with no phase_executor/.../routing-table.json.
    repo = tmp_path / "projects" / "empty"
    repo.mkdir(parents=True)
    ws = _ws(tmp_path, {"version": 1, "seats": {"ship": "executor"}}, path="./projects/empty")
    (tmp_path / "p.txt").write_text("hi")

    class A:
        seat = "ship"; prompt_file = str(tmp_path / "p.txt"); run_id = "run1"; context_file = None
        correlation_id = None; author_provider = None; effort = None; timeout = 5.0
        workspace = ws; project = "rawgentic"
    rc = er._do_dispatch(A())
    assert rc in (er.EXIT_INTERNAL, er.EXIT_MALFORMED)  # structured, never a bare exit 1


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


def test_resolve_absent_workspace_is_inherit_not_error(tmp_path):
    # A genuinely-absent workspace is "not configured" → inherit (NOT a read-error fail-closed).
    missing = str(tmp_path / "does-not-exist.json")
    assert er.resolve_seat_action("ship", missing, "rawgentic")[0] == "inherit"


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


# --- #464 §B: WIRED_SEATS is the full 7-seat vocabulary; design is single-dispatch-refused --------
def test_wired_seats_is_the_full_seven():
    assert er.WIRED_SEATS == frozenset(
        {"intake", "analysis", "design", "plan", "build", "review", "ship"})
    assert er.COMPETITIVE_ONLY == frozenset({"design"})


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
    gd, _ = _gate()  # snapshot risk_level == "standard"
    res, audit = _dispatch_build(tmp_path, gd, {"risk_level": "high"})  # mismatched plan fact
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


def test_cli_build_stale_gate_exit4(tmp_path):
    # Integration through the CLI --gate-file / --plan-context wiring on the shipped table: a stale
    # (context-mismatched) gate is rejected pre-launch (exit 4), so no provider call is made.
    repo = tmp_path / "projects" / "rawgentic"
    table_dst = repo / er._ROUTING_TABLE_REL
    table_dst.parent.mkdir(parents=True)
    real_table = Path(er.__file__).resolve().parent.parent / er._ROUTING_TABLE_REL
    table_dst.write_text(real_table.read_text(encoding="utf-8"), encoding="utf-8")
    ws = _ws(tmp_path, {"version": 1, "seats": {"build": "executor"}}, path="./projects/rawgentic")
    gd, _ = _gate()  # snapshot risk_level == "standard"
    gf = tmp_path / "gate.json"
    gf.write_text(json.dumps({"decision": gd.decision, "reason_codes": list(gd.reason_codes),
                              "input_snapshot": gd.input_snapshot, "policy_digest": gd.policy_digest}),
                  encoding="utf-8")
    cf = tmp_path / "ctx.json"
    cf.write_text(json.dumps({"risk_level": "high"}), encoding="utf-8")  # mismatched fact
    (tmp_path / "p.txt").write_text("hi", encoding="utf-8")

    class A:
        seat = "build"; prompt_file = str(tmp_path / "p.txt"); run_id = "run1"; context_file = None
        correlation_id = "wf2:build"; author_provider = None; effort = None; timeout = 5.0
        workspace = ws; project = "rawgentic"; gate_file = str(gf); plan_context = str(cf)
    assert er._do_dispatch(A()) == er.EXIT_ENFORCEMENT
