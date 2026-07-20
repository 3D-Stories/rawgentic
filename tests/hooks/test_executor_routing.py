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
from phase_executor.adapters import codex_cli  # noqa: E402
from phase_executor.engine import run_seat, PROVIDER_ENGINE as _PROVIDER_ENGINE  # noqa: E402
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


# --- #470 §2b: internal plan-context mint + enforced plan-digest freshness ------------------------
_PLAN_STD = ("### Task 1: build the thing (#470)\n"
             "- riskLevel: standard\n"
             "- files: hooks/foo.py, hooks/bar.py\n")


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
        assert set(seats) == {"intake", "analysis", "design", "plan", "build", "review", "ship"}
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
        r = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-4-8"}},
                   expected=_pkg_digest(), extra=["--validate-only"])
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert out["config_digest"].startswith("sha256:")
        assert out["pointer"] == {"version": 1, "file": "claude_docs/routing/phase-executor-table.json"}
        assert sorted(p.name for p in repo.iterdir()) == before

    def test_validate_only_combined_with_reset_uses_package_base(self, tmp_path):
        repo, ws = _proj_ws(tmp_path)
        r = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-4-8"}},
                   expected=_pkg_digest(), extra=["--validate-only", "--reset-to-default"])
        assert r.returncode == 0

    def test_candidate_digest_forbidden_in_validate_only(self, tmp_path):
        repo, ws = _proj_ws(tmp_path)
        r = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-4-8"}},
                   expected=_pkg_digest(), candidate="sha256:deadbeef", extra=["--validate-only"])
        assert r.returncode == er.EXIT_MALFORMED
        assert "forbidden with --validate-only" in json.loads(r.stdout)["error"]["message"]

    def test_materialize_requires_matching_candidate_digest(self, tmp_path):
        repo, ws = _proj_ws(tmp_path)
        missing = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-4-8"}}, expected=_pkg_digest())
        assert missing.returncode == er.EXIT_MALFORMED
        assert "requires --expected-candidate-digest" in json.loads(missing.stdout)["error"]["message"]
        stale = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-4-8"}},
                       expected=_pkg_digest(), candidate="sha256:deadbeef")
        assert stale.returncode == er.EXIT_MALFORMED
        assert "candidate changed since validated" in json.loads(stale.stdout)["error"]["message"]
        assert not (repo / "claude_docs" / "routing" / "phase-executor-table.json").exists()

    def test_fresh_create_end_to_end(self, tmp_path):
        repo, ws = _proj_ws(tmp_path)
        v = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-4-8"}},
                   expected=_pkg_digest(), extra=["--validate-only"])
        cand = json.loads(v.stdout)["config_digest"]
        r = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-4-8"}},
                   expected=_pkg_digest(), candidate=cand)
        assert r.returncode == 0
        dest = repo / "claude_docs" / "routing" / "phase-executor-table.json"
        assert dest.is_file()
        assert routing.snapshot_from_file(dest).config_digest == cand
        table = json.loads(dest.read_text(encoding="utf-8"))
        assert table["seats"]["ship"]["primary"]["model"] == "claude-opus-4-8"
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
        v = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-4-8"}},
                   dest="claude_docs/t.json", expected=base_digest, extra=["--validate-only"])
        assert v.returncode == 0
        cand = json.loads(v.stdout)["config_digest"]
        r = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-4-8"}},
                   dest="claude_docs/t.json", expected=base_digest, candidate=cand)
        assert r.returncode == 0
        after = json.loads(dst.read_text(encoding="utf-8"))
        assert after["seats"]["intake"]["primary"]["model"] == "claude-fable-5"  # kept (A3)
        assert after["seats"]["ship"]["primary"]["model"] == "claude-opus-4-8"

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
        v = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-4-8"}},
                   dest="claude_docs/other.json", expected=d, extra=["--validate-only"])
        cand = json.loads(v.stdout)["config_digest"]
        r = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-4-8"}},
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
        v = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-4-8"}},
                   dest="claude_docs/t.json", expected=cur,
                   extra=["--validate-only", "--reset-to-default"])
        assert v.returncode == 0
        cand = json.loads(v.stdout)["config_digest"]
        r = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-4-8"}},
                   dest="claude_docs/t.json", expected=cur, candidate=cand,
                   extra=["--reset-to-default"])
        assert r.returncode == 0
        after = json.loads(dst.read_text(encoding="utf-8"))
        assert after["seats"]["intake"]["primary"]["model"] != "claude-fable-5"  # reset took
        assert after["seats"]["ship"]["primary"]["model"] == "claude-opus-4-8"

    def test_symlinked_parent_dest_escape_refused(self, tmp_path):
        # 8a-B1: an in-repo symlink dir pointing OUTSIDE the root must not let a
        # fresh-create write escape (canonical containment, not lexical normpath).
        outside = tmp_path / "OUTSIDE"
        outside.mkdir()
        repo, ws = _proj_ws(tmp_path)
        (repo / "claude_docs").mkdir(exist_ok=True)
        (repo / "claude_docs" / "routing").symlink_to(outside)
        v = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-4-8"}},
                   expected=_pkg_digest(), extra=["--validate-only"])
        assert v.returncode == er.EXIT_MALFORMED
        assert "outside the project root" in json.loads(v.stdout)["error"]["message"]
        assert not (outside / "phase-executor-table.json").exists()

    def test_stale_base_digest_exit2(self, tmp_path):
        repo, ws = _proj_ws(tmp_path)
        r = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-4-8"}},
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
        ({"wombat": {"primary": "claude-opus-4-8"}}, "unknown seat"),
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
        r = _apply(ws, tmp_path, {"ship": {"primary": "claude-opus-4-8"}},
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


class _StubSupervisor:
    def __init__(self, state="completed"):
        self.launched = []
        self._state = state

    def launch(self, seat, prompt, **kw):  # noqa: D401 — records the call
        self.launched.append((seat, kw))
        return {"seat": seat, "kw": kw}

    def await_job(self, record, *, timeout_s=3600.0):
        return self._state, {"requested_model": "claude-sonnet-5", "actual_model": "claude-sonnet-5"}


def _supervised(tmp_path, *, probe_stream=None, final_argv=None, state="completed",
                probe_raises=False, provision_calls=None, monkeypatch=None):
    # The rich claude_mutating machinery (probes, init event) stays unit-tested even though
    # production refuses mutating-claude (STEP 0, MUTATING_FS_SANDBOXED): tests widen the module
    # constant — a monkeypatch of module state, NOT a caller input; production has no such knob.
    # test_supervised_refuses_unsandboxed_mutating_engine pins the production value.
    if monkeypatch is not None:
        monkeypatch.setattr(er, "MUTATING_FS_SANDBOXED", frozenset({"codex", "claude"}))
    qc = QuotaCoordinator(tmp_path / "permits", {"claude": 2, "codex": 4, "zhipu": 2})
    audit = enforce.RoutingAuditLog(tmp_path / "runs", "run1")
    sup = _StubSupervisor(state=state)
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
        mk_nonce=lambda: "NONCE-1", mk_probe_cid=lambda cls: f"probe-{cls[:3]}")
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
            return "completed", {"parse_status": "ok",
                                 "requested_model": codex_tgt["model"],
                                 "actual_model": codex_tgt["model"]}

    return dict(
        seat="build", prompt="hi", run_id="run1", correlation_id="wf2:build:codex",
        effort=None, timeout=5.0, engine="codex", profile=profile, final_argv=argv,
        snapshot_dir=str(REPO_ROOT), capture_root=str(tmp_path / "runs"), audit=audit,
        canary=_canary, canary_evidence=_cev, supervisor=_MatchSup(),
        probe_session=probe_session, provision=lambda: (None, {"handle": True}),
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
            return "completed", {"parse_status": "ok",
                                 "requested_model": tgt["model"], "actual_model": tgt["model"]}
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
            return "completed", {"parse_status": "ok",
                                 "requested_model": tgt["model"], "actual_model": "wrong-model-9"}
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


# --- #471 W8: `status --run` — the read-only live-run status surface ----------------------------
# pylint: disable=no-name-in-module
from phase_executor.registry import JobRecord, JobRegistry, session_name as _sname  # noqa: E402
from phase_executor.worktree import WorktreeIdentity as _WId  # noqa: E402
# pylint: enable=no-name-in-module


def _status_repo(tmp_path, *, state="running", with_obs=False, with_spec=True,
                 with_activity=False, run_id="run1"):
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
        state=state, created_at=1.0, quarantine_reason=None)
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
        (cap / "transport.txt").write_text("first line\nlast activity line\n", encoding="utf-8")
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
    assert row["last_activity"]["file"] in ("transport.txt", "observation.json")
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
