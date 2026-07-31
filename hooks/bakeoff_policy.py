"""#428 — competitive-rounds / build-bake-off caller policy for ``phase_executor.run_competitive``.

This is the rawgentic POLICY layer (epic #422 E5): the glm-5.2 judge, the hybrid judge-failure
strategy, the ``bakeoff_results.jsonl`` sink, the design-round + build-bake-off orchestration, and
the D9 reviewer-backend selection. The EXECUTION + failure semantics already live in the
extraction-clean ``phase_executor.run_competitive`` engine (which imports no ``hooks/``); this module
supplies the callables that engine takes as *parameters*.

Fail-closed everywhere it matters (Step-4 review): a missing rubric, an unavailable/unparseable
glm judge, a winner index out of range, fewer than two valid candidates, or a tampered #429 gate
digest all raise rather than silently pick a wrong (or failed) winner.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

# Sibling hook import (hooks/*.py import each other via PYTHONPATH=hooks / sys.path.insert).
import adversarial_review_lib as _adv

_REPO = Path(__file__).resolve().parent.parent
RUBRIC_DIR = Path(__file__).resolve().parent / "data" / "bakeoff_rubrics"
DEFAULT_SINK_PATH = _REPO / "docs" / "measurements" / "bakeoff_results.jsonl"

# Competitor sets (models resolved to lanes from the routing table at call time — so every
# model named here MUST appear somewhere in that table, or _lane_for_model refuses; the
# owner retune 2026-07-24 retired claude-opus-4-8 from the table in favour of claude-opus-5,
# and a drift guard pins DESIGN_MODELS to the design seat's own model set).
DESIGN_MODELS: tuple = ("gpt-5.6-sol", "claude-opus-5")                       # sol vs opus, every round
BUILD_MODELS: tuple = ("claude-sonnet-5", "claude-opus-5", "gpt-5.6-terra")   # gate-flagged bake-off
INCUMBENT_MODEL = "claude-opus-5"   # the headless judge-degrade fallback (owner-picked, §3.3)


class RubricUnavailable(RuntimeError):
    """The rubric file for a phase is missing or empty — never judge blind."""


class JudgeError(RuntimeError):
    """Any fail-closed judge condition. Carries the candidate ``results`` so an interactive
    stop-and-ask path can present the completed drafts without re-dispatching (re-spending quota)."""

    def __init__(self, message: str, *, results: Optional[Sequence] = None):
        super().__init__(message)
        self.results = list(results) if results is not None else []


# ---- phase_executor import (guarded, same pattern as executor_routing_lib) --------------------
def _ensure_pe_importable() -> None:
    src = str(_REPO / "phase_executor" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _pe():
    """Return the ``phase_executor`` package (with ``.contract`` importable). Imported lazily so a
    stale tree surfaces at call time, never at hook module load."""
    _ensure_pe_importable()
    import phase_executor as pe  # noqa: PLC0415
    import phase_executor.contract  # noqa: PLC0415,F401 — ensure the submodule attr exists
    return pe


# ---- rubric -----------------------------------------------------------------------------------
def load_rubric(phase: str) -> str:
    """Read the vendored bench-#14 rubric for ``phase`` (design|build). Fail-closed: a missing or
    empty file raises ``RubricUnavailable`` (we never send an empty rubric to the judge)."""
    path = RUBRIC_DIR / f"{phase}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RubricUnavailable(f"rubric for phase {phase!r} not found at {path}: {exc}") from None
    if not text.strip():
        raise RubricUnavailable(f"rubric for phase {phase!r} is empty ({path})")
    return text


# ---- anonymize + shuffle ----------------------------------------------------------------------
def _ok_payload(obs, pe) -> Optional[str]:
    """The candidate's output text IFF it is a genuine success; else None (excluded from judging).

    Uses ONLY ``parsed_payload`` (the model's text). NEVER reads ``raw_capture_path`` — the provider
    envelope there carries the model id, which would defeat anonymization (Step-4 finding H3)."""
    if getattr(obs, "parse_status", None) != pe.contract.OK:
        return None
    payload = getattr(obs, "parsed_payload", None)
    if payload is None:
        return None
    return payload if isinstance(payload, str) else str(payload)


def anonymize_and_shuffle(results, *, seed):
    """Return ``(drafts, order)`` over ONLY ok / non-None-payload candidates.

    - ``drafts[k]`` = ``{"label": k+1, "text": <anonymized output>}`` in a seeded shuffle (1-based
      labels; no model identity — only ``parsed_payload`` text).
    - ``order[k]`` = the ORIGINAL index (into ``results``) of the candidate shown as Draft ``k+1``.

    Fewer than two valid candidates raises ``JudgeError`` — a degenerate set must not be judged
    (Step-4 finding H2: a failed ``harness_error`` candidate must never win)."""
    pe = _pe()
    valid = [(i, txt) for i, obs in enumerate(results)
             if (txt := _ok_payload(obs, pe)) is not None]
    if len(valid) < 2:
        raise JudgeError(f"bake-off needs >=2 valid candidates, got {len(valid)}", results=results)
    rng = random.Random(seed)  # ponytail: seeded so tests are deterministic; prod passes a per-round nonce
    rng.shuffle(valid)
    order = [i for i, _ in valid]
    drafts = [{"label": k + 1, "text": txt} for k, (_, txt) in enumerate(valid)]
    return drafts, order


# ---- judge ------------------------------------------------------------------------------------
def _build_judge_prompt(rubric_text: str, drafts, *, build_evidence=None) -> str:
    parts = [
        "You are an impartial judge. Score each anonymized draft against the rubric below, then "
        "pick a single winner. The drafts are anonymized and randomly ordered — judge only on the "
        "rubric, never on guessed authorship. Respond ONLY with JSON of the form: "
        '{"winner_draft": <int, 1-based>, "scores": {"<draft label>": {"<criterion>": <0-100>}}, '
        '"confidence": <float 0-1>}.\n\n=== RUBRIC ===\n', rubric_text, "\n\n=== DRAFTS ===\n",
    ]
    for d in drafts:
        parts.append(f"\n--- Draft {d['label']} ---\n{d['text']}\n")
        if build_evidence and d["label"] in build_evidence:
            # build bake-offs judge on deterministic test/static evidence, engine-name-scrubbed.
            parts.append(f"[deterministic evidence for Draft {d['label']}]:\n{build_evidence[d['label']]}\n")
    return "".join(parts)


def _parse_verdict(payload: str, n_drafts: int):
    try:
        verdict = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise JudgeError(f"judge returned non-JSON: {exc}") from None
    if not isinstance(verdict, dict) or "winner_draft" not in verdict:
        raise JudgeError("judge verdict missing winner_draft")
    winner_draft = verdict["winner_draft"]
    # Reject bool (an int subclass) and any non-int (a float like 1.9 would silently truncate to a
    # valid-but-wrong draft; a "1" string is a malformed verdict) — fail closed, don't coerce.
    if isinstance(winner_draft, bool) or not isinstance(winner_draft, int):
        raise JudgeError(f"winner_draft must be an int, got {winner_draft!r}")
    if not 1 <= winner_draft <= n_drafts:
        raise JudgeError(f"winner_draft {winner_draft} out of range 1..{n_drafts}")
    scores = verdict.get("scores") if isinstance(verdict.get("scores"), dict) else {}
    return winner_draft, scores, verdict.get("confidence")


DESIGN_JUDGE_MODEL = "glm-5.2"


def _default_judge_complete():
    """Step-11 R1-F3: the design-round judge is PINNED to glm-5.2, independently of the
    adversarial-review env override (``RAWGENTIC_ADV_REVIEW_GLM_MODEL`` changes that
    library's default) — an env-swapped judge must never run silently under the fixed
    glm-5.2 contract the evidence records."""
    return functools.partial(_adv.glm_complete, model=DESIGN_JUDGE_MODEL)


def make_glm_judge(rubric_text: str, *, seed, complete_fn=None, build_evidence=None, retries: int = 1):
    """Return a ``judge(results, rubric=None)`` callable for ``run_competitive``.

    Anonymizes + shuffles the ok candidates, asks glm-5.2 to score + pick a winner on the (1-based)
    draft labels, maps the winning label back to the ORIGINAL candidate index
    (``winner_index = order[winner_draft - 1]`` — 1-based label to 0-based order, Step-4 finding M1),
    and returns ``{winner_index, scores, degraded: False}``. ``confidence`` is folded into ``scores``
    so ``run_competitive``'s record retains it (finding L1). Up to ``retries`` extra glm attempts on a
    None/unparseable verdict (§3.3 "after one retry"); a degenerate candidate set is NOT retried."""
    complete = complete_fn or _default_judge_complete()

    def judge(results, _rubric=None):
        drafts, order = anonymize_and_shuffle(results, seed=seed)  # <2 valid -> JudgeError (deterministic; no retry)
        # build_evidence is keyed by ORIGINAL candidate index (0-based, = results/candidates order);
        # re-key to the post-shuffle 1-based draft labels here (the caller cannot know the shuffle),
        # so evidence lands on the RIGHT draft (Step-11 finding: label-keying was uncomputable).
        evidence_by_label = None
        if build_evidence:
            evidence_by_label = {k + 1: build_evidence[order[k]]
                                 for k in range(len(order)) if order[k] in build_evidence}
        prompt = _build_judge_prompt(rubric_text, drafts, build_evidence=evidence_by_label)
        last: Optional[JudgeError] = None
        for _attempt in range(max(0, retries) + 1):  # clamp: retries<0 must not skip the loop (raise None)
            payload, err = complete(prompt)
            if payload is None:
                last = JudgeError(f"glm judge unavailable: {err}", results=results)
                continue
            try:
                winner_draft, scores, confidence = _parse_verdict(payload, len(drafts))
            except JudgeError as exc:
                last = JudgeError(str(exc), results=results)
                continue
            winner_index = order[winner_draft - 1]
            if confidence is not None:
                scores = dict(scores)
                scores["_confidence"] = confidence
            return {"winner_index": winner_index, "scores": scores, "degraded": False}
        raise last  # exhausted retries
    return judge


# ---- failure strategy -------------------------------------------------------------------------
def hybrid_failure_strategy(*, headless: bool, incumbent_index: int, sink=None,
                            run_id=None, correlation_id=None):
    """Return a ``failure_strategy(results, exc)`` for ``run_competitive``.

    - **headless** -> winner = the incumbent lane (must itself be an ``ok`` candidate, else fail
      closed), ``judge_degraded=True`` (the record flag excludes it from telemetry, surfaced in the
      morning report).
    - **interactive** -> persist a degraded record via ``sink`` (so the ask-and-resume path has a
      trace) then RE-RAISE — ``run_competitive`` calls the strategy inside its ``except`` handler
      (engine.py:274), which does not re-catch, so a raising strategy propagates = stop and ask
      (Step-4 finding M2). The raised ``JudgeError`` carries the completed ``results`` so no
      paid-for work is thrown away."""
    if headless:
        def _headless(results, exc):
            pe = _pe()
            ok = (0 <= incumbent_index < len(results)
                  and getattr(results[incumbent_index], "parse_status", None) == pe.contract.OK)
            if not ok:
                raise JudgeError(
                    f"headless degrade impossible: incumbent index {incumbent_index} is not an ok candidate",
                    results=results) from exc
            return {"winner_index": incumbent_index, "degraded": True, "scores": None}
        return _headless

    def _interactive(results, exc):
        if sink is not None:
            try:
                # #765 (8a R1-M1): the degraded trace joins the identity contract too —
                # a failure record the run cannot attribute is an audit gap.
                sink({"run_id": run_id, "correlation_id": correlation_id,
                      "winner_index": None, "n_candidates": len(results), "judge_degraded": True,
                      "candidates": [r.to_dict() for r in results], "scores": None,
                      "interactive_judge_failure": str(exc)})
            except Exception:  # noqa: BLE001 — a trace-write failure must not mask the stop-and-ask
                pass
        raise JudgeError(f"judge failed; interactive stop-and-ask: {exc}",
                         results=getattr(exc, "results", results)) from exc
    return _interactive


# ---- results sink -----------------------------------------------------------------------------
def bakeoff_sink(path=None):
    """Return a ``sink(record)`` appending one JSON line to ``bakeoff_results.jsonl``.

    # ponytail: single-writer append (the epic driver runs one bake-off at a time). Records embed
    # full candidate payloads (tens of KB), so O_APPEND size-atomicity would NOT hold anyway — add
    # flock (or atomic_write_lib) only if concurrent bake-off writers ever become real."""
    target = Path(path) if path is not None else DEFAULT_SINK_PATH

    def _sink(record):
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    return _sink


# ---- D9 ---------------------------------------------------------------------------------------
def reviewer_backend_for_winner(winner, *, default: str = "gpt") -> str:
    """D9: the winner's engine picks the adversarial-review backend. A gpt(``codex``)-authored
    winner must NOT be reviewed by gpt -> force ``"glm"``; any non-gpt winner -> the configured
    ``default``. Valid backends are ``{gpt, glm, both}`` (``adversarial_review_lib.BACKENDS``); there
    is no ``"claude"`` adversarial backend, so a claude winner reviewed by gpt/glm is already
    cross-engine (Step-4 finding M4)."""
    # PROVIDER_ENGINE maps openai->codex (engine.py), so a gpt-authored winner's Observation.engine
    # is always "codex" — that is the only gpt-family engine value the adapters emit.
    engine = getattr(winner, "engine", None)
    if engine == "codex":
        return "glm"
    return default


# ---- lane sourcing + orchestration ------------------------------------------------------------
def _lane_for_model(snapshot, model) -> Optional[dict]:
    """The lane a model is declared with in the routing table (primary or any chain, any seat), or
    None if the model is not in the table."""
    for seat_spec in snapshot.table.get("seats", {}).values():
        for entry in [seat_spec.get("primary"), *seat_spec.get("chain", [])]:
            if entry and entry.get("model") == model:
                return entry["lane"]
    return None


def _candidates_for(snapshot, models, prompt, *, seat, correlation_id=None):
    pe = _pe()
    candidates = []
    for model in models:
        lane = _lane_for_model(snapshot, model)
        if lane is None:
            raise ValueError(f"model {model!r} has no lane in the routing table (cannot build candidate)")
        candidates.append(pe.Candidate(
            seat=seat, model=model, prompt=prompt, provider=lane["provider"], pool=lane["pool"],
            transport=lane.get("transport", "native"), auth_mode=lane.get("auth_mode", "subscription_oauth"),
            credential_ref=lane.get("credential_ref"), correlation_id=correlation_id))
    return candidates


def _verified_decision(gate_decision) -> bool:
    """M7 anti-tamper, now single-sourced via ``complexity_gate.verified_decision`` (#464). Kept as a
    thin wrapper because callers/tests reference it; it translates the extracted helper's
    ``GateTamperError`` back to ``JudgeError`` so ``run_build_bakeoff``'s raises-on-tamper contract is
    unchanged. The recompute-digest + authoritative-decision semantics (trust only what the digest
    covers, NOT ``gate_decision.decision``) live there. No ``expected_context`` here: bakeoff_policy
    mints its gate in-process one call earlier and holds no separate plan doc (the ctx=None carve-out)."""
    import complexity_gate  # noqa: PLC0415
    try:
        return complexity_gate.verified_decision(gate_decision)
    except complexity_gate.GateTamperError as exc:
        raise JudgeError(str(exc)) from exc


def run_design_round(prompt, *, snapshot, quota, capture_root, headless, seed,
                     sink_path=None, models=DESIGN_MODELS, complete_fn=None, dispatch=None,
                     run_id=None, correlation_id=None, rubric_text=None):
    """Competitive design round (sol vs opus every round), glm-5.2 judge on the design rubric.
    Winner's exact bytes become the phase artifact. Returns ``run_competitive``'s
    ``(winner, losers, judge_obs, record)``.

    #765: a workflow caller passes its ``run_id``/``correlation_id`` — both land in the
    record and every candidate Observation, so the bake-off stream joins the run's audit
    context instead of minting a random identity."""
    # Step-11 R2-F5: ``rubric_text`` is a single-read optimization for the CLI (which
    # hashes the same text it passes), NOT a rubric bypass — callers must pass
    # ``load_rubric()`` output; blank text refuses instead of judging on nothing.
    if rubric_text is not None and not rubric_text.strip():
        raise ValueError("rubric_text is blank — pass load_rubric('design') output or None")
    pe = _pe()
    candidates = _candidates_for(snapshot, models, prompt, seat="design", correlation_id=correlation_id)
    incumbent_index = models.index(INCUMBENT_MODEL) if INCUMBENT_MODEL in models else 0
    # #765 (8a R1-M2): one rubric read — a caller that hashes the rubric passes the SAME
    # text the judge sees, so the recorded hash can never describe a different rubric.
    judge = make_glm_judge(rubric_text if rubric_text is not None else load_rubric("design"),
                           seed=seed, complete_fn=complete_fn)
    sink = bakeoff_sink(sink_path)
    strategy = hybrid_failure_strategy(headless=headless, incumbent_index=incumbent_index, sink=sink,
                                       run_id=run_id, correlation_id=correlation_id)
    kwargs = dict(judge=judge, failure_strategy=strategy, sink=sink, snapshot=snapshot, quota=quota,
                  capture_root=capture_root, require_parallel=True,
                  run_id=run_id, correlation_id=correlation_id)
    if dispatch is not None:
        kwargs["dispatch"] = dispatch
    return pe.run_competitive(candidates, **kwargs)


def run_build_bakeoff(prompt, *, gate_decision, snapshot, quota, capture_root, headless, seed,
                      sink_path=None, models=BUILD_MODELS, build_evidence=None, complete_fn=None,
                      dispatch=None, default_seat_runner=None):
    """Gated build stage. Verifies the #429 anti-tamper digest and re-derives the authoritative
    decision FIRST (on both paths, so a tampered gate is caught even when it declines a bake-off). A
    False decision -> the single default build seat, wrapped as ``(obs, [], None, record)`` so the
    return shape is uniform with the bake-off path (``run_competitive``'s 4-tuple) and a caller can
    unpack it either way. A True decision -> the {sonnet, opus, terra} bake-off judged on the build
    rubric + deterministic ``build_evidence`` (keyed by original candidate index)."""
    pe = _pe()
    if not _verified_decision(gate_decision):  # integrity + authoritative decision (M7); raises on tamper
        runner = default_seat_runner or pe.run_seat
        obs = runner("build", prompt, snapshot=snapshot, quota=quota, capture_root=capture_root)
        record = {"winner_index": 0, "n_candidates": 1, "judge_degraded": False,
                  "candidates": [obs.to_dict() if hasattr(obs, "to_dict") else obs],
                  "scores": None, "bakeoff_skipped": True}
        return obs, [], None, record
    candidates = _candidates_for(snapshot, models, prompt, seat="build")
    incumbent_index = models.index(INCUMBENT_MODEL) if INCUMBENT_MODEL in models else 0
    judge = make_glm_judge(load_rubric("build"), seed=seed, complete_fn=complete_fn, build_evidence=build_evidence)
    sink = bakeoff_sink(sink_path)
    strategy = hybrid_failure_strategy(headless=headless, incumbent_index=incumbent_index, sink=sink)
    kwargs = dict(judge=judge, failure_strategy=strategy, sink=sink, snapshot=snapshot, quota=quota,
                  capture_root=capture_root, require_parallel=True)
    if dispatch is not None:
        kwargs["dispatch"] = dispatch
    return pe.run_competitive(candidates, **kwargs)


# ---- sanitized evidence + the workflow-callable CLI (#765) -------------------------------------
# Per-candidate WHITELIST — sanitization keeps only these (fail-safe: an unknown field never
# survives). parsed_payload / raw_capture_path / dispatched_lane are deliberately absent: the
# committable evidence must never embed candidate payloads or local paths (the raw sink stays
# gitignored by owner decision 2026-07-20).
_EVIDENCE_CANDIDATE_FIELDS = (
    "seat", "requested_model", "actual_model", "engine", "transport", "parse_status",
    "prompt_hash", "timing_ms", "queued_ms", "usage", "correlation_id",
)
_EVIDENCE_TOP_FIELDS = (
    "run_id", "correlation_id", "winner_index", "n_candidates", "judge_degraded", "scores",
)


def _safe_evidence_key(k) -> bool:
    """A key eligible for the committable evidence: short, no path separators/traversal
    (8a R1-H2/R2-F1 — judge- and adapter-originated keys are model-influenced content)."""
    return (isinstance(k, str) and 0 < len(k) <= 80
            and "/" not in k and "\\" not in k and ".." not in k)


def _clean_numeric_map(node, *, depth: int = 1):
    """Reconstruct a judge/adapter-originated dict keeping ONLY numeric leaves under safe
    keys (never copy model-influenced structures wholesale — injected draft text or paths
    must not survive into the committable artifact). ``depth`` bounds nesting."""
    if not isinstance(node, dict):
        return None
    out = {}
    for k, v in node.items():
        if not _safe_evidence_key(k):
            continue
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out[k] = v
        elif isinstance(v, dict) and depth > 0:
            inner = _clean_numeric_map(v, depth=depth - 1)
            if inner:
                out[k] = inner
    return out or None


_SCORES_DRAFT_KEY_RE = re.compile(r"^Draft \d+$")
# Step-11 R2-F3: run/correlation identity grammar — these strings land verbatim in the
# committable evidence and in audit paths, so they carry no separators, spaces, or controls.
_IDENTITY_RE = re.compile(r"[A-Za-z0-9._:-]{1,120}")


def _clean_scores(node, rubric_text: str):
    """Step-11 R1-F2: judge-controlled score KEYS are themselves a text channel into the
    committable artifact — numeric-leaf filtering alone lets a hostile judge spell a
    payload ACROSS keys. Depth 0 admits only ``Draft <n>`` labels and ``_confidence``;
    depth 1 admits only keys that literally appear in the trusted, repo-committed rubric."""
    if not isinstance(node, dict):
        return None
    out = {}
    for k, v in node.items():
        if not _safe_evidence_key(k):
            continue
        if k == "_confidence" and isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = v
        elif _SCORES_DRAFT_KEY_RE.match(k) and isinstance(v, dict):
            inner = {ik: iv for ik, iv in v.items()
                     if _safe_evidence_key(ik) and ik in rubric_text
                     and isinstance(iv, (int, float)) and not isinstance(iv, bool)}
            if inner:
                out[k] = inner
    return out or None


def sanitize_record(record: dict, *, prompt_text: str, rubric_text: str,
                    judge_model: str = DESIGN_JUDGE_MODEL) -> dict:
    """The COMMITTABLE projection of a ``run_competitive`` record (#765 AC2 evidence).

    Whitelist-only (never strip-and-hope) — and RECONSTRUCTED for the model-influenced
    nested dicts: ``scores`` keys are BOUND to Draft labels + literal rubric substrings
    (Step-11 R1-F2), per-candidate ``usage`` keeps only numeric leaves under safe keys
    (8a R1-H2/R2-F1). ``prompt_sha256``/``rubric_sha256`` bind the evidence to the exact
    prompt and rubric that produced it; ``judge_model`` names the pinned judge (R1-F3)."""
    clean = {k: record.get(k) for k in _EVIDENCE_TOP_FIELDS}
    clean["scores"] = _clean_scores(record.get("scores"), rubric_text)
    cands = []
    for c in record.get("candidates", []):
        row = {k: c.get(k) for k in _EVIDENCE_CANDIDATE_FIELDS if k in c}
        if "usage" in row:
            row["usage"] = _clean_numeric_map(c.get("usage"), depth=0)
        cands.append(row)
    clean["candidates"] = cands
    clean["prompt_sha256"] = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    clean["rubric_sha256"] = hashlib.sha256(rubric_text.encode("utf-8")).hexdigest()
    clean["judge_model"] = judge_model
    return clean


def design_round_enabled(workspace_path: str, project: str) -> bool:
    """The #765 on/off gate (owner decision 2026-07-31): the WF2 Step-3 competitive round
    is OPT-IN via the project's workspace entry — ``designBakeoff: {"enabled": true}`` (or
    bare ``true``). Absent, malformed, or unreadable config → DISABLED (the safe state:
    Step 3 keeps its current mechanism). Fail-open-to-off, never an exception."""
    import model_routing_lib as mr  # noqa: PLC0415 — sibling hook

    try:
        entry = mr._load_project_entry(workspace_path, project)  # noqa: SLF001 — shared loader (one home)
    except Exception:  # noqa: BLE001 — a gate read failure means "not opted in"
        return False
    if not isinstance(entry, dict):
        return False
    gate = entry.get("designBakeoff")
    if gate is True:
        return True
    if isinstance(gate, dict):
        return gate.get("enabled") is True
    return False


def main(argv=None, *, complete_fn=None, dispatch=None) -> int:
    """The workflow-callable design-round CLI (#765) — WF2 Step-3 prose names this command:

        python3 hooks/bakeoff_policy.py design-round \\
          --prompt-file <brief> --run-id wf2-<issue>-<session> \\
          --correlation-id <issue>-s3-design \\
          --workspace <workspace-file> --project <name> \\
          --winner-out <design-draft-path> \\
          [--headless] [--seed N] [--sink <path>] [--evidence-out <path>]

    ``--winner-out`` is the winner-delivery channel (Step-11 ADV-1/R1-F1/R2-F1): the
    sanitized evidence and stdout deliberately carry no payloads, so a workflow round
    that omits it spends the candidates and the judge without producing a design draft —
    the Step-3 prose names it in the canonical command.

    Exit taxonomy: 0 ok; 2 malformed input/config (argparse, unreadable/empty prompt file,
    invalid identity grammar, output-path collision, committable sink, workspace
    resolution); 3 round failure (judge/chain/quota/infeasible — interactive judge failure
    is fail-loud here, never a degraded winner); 4 gate disabled (the designBakeoff opt-in
    is OFF for this project — owner decision D27, default OFF). ``complete_fn``/``dispatch``
    are test injection points; production runs use the live glm judge and real adapters."""
    ap = argparse.ArgumentParser(prog="bakeoff_policy")
    sub = ap.add_subparsers(dest="cmd", required=True)
    dr = sub.add_parser("design-round", help="one competitive design round with workflow identity")
    dr.add_argument("--prompt-file", required=True)
    dr.add_argument("--run-id", required=True, help="the workflow run id (e.g. wf2-<issue>-<session>)")
    dr.add_argument("--correlation-id", required=True, help="the workflow dispatch correlation id")
    dr.add_argument("--workspace", required=True)
    dr.add_argument("--project", required=True)
    dr.add_argument("--headless", action="store_true",
                    help="judge failure degrades to the incumbent (recorded); default is fail-loud")
    dr.add_argument("--seed", type=int, default=None, help="shuffle seed (default: per-round nonce)")
    dr.add_argument("--sink", default=None, help="raw-record sink override (default: gitignored bakeoff_results.jsonl); must resolve OUTSIDE the project repo")
    dr.add_argument("--evidence-out", default=None, help="write the SANITIZED committable record here (atomic)")
    dr.add_argument("--winner-out", default=None, help="write the WINNER's exact payload bytes here (the phase artifact; atomic)")
    en = sub.add_parser("design-round-enabled",
                        help="the #765 opt-in gate: exit 0 enabled, 1 disabled (default OFF)")
    en.add_argument("--workspace", required=True)
    en.add_argument("--project", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "design-round-enabled":
        enabled = design_round_enabled(args.workspace, args.project)
        print(json.dumps({"enabled": enabled}))
        return 0 if enabled else 1

    import executor_routing_lib as er  # noqa: PLC0415 — sibling hook, lazy so import errors surface per-call
    from atomic_write_lib import atomic_write_text  # noqa: PLC0415

    # Step-11 ADV-4: the round path ITSELF enforces the designBakeoff opt-in (owner
    # decision D27, default OFF) — the probe verb is advisory for prose, this is the guard,
    # so a stale or direct caller can never run a token-consuming round while disabled.
    if not design_round_enabled(args.workspace, args.project):
        print(json.dumps({"ok": False, "error": "design_round_disabled",
                          "detail": "designBakeoff opt-in is not enabled for this project "
                                    "(default OFF) — set designBakeoff.enabled true on the "
                                    "workspace project entry to run competitive rounds"}))
        return 4
    # 8a R1-L1: empty identity defeats attribution — refuse before any work.
    if not args.run_id or not args.correlation_id:
        print(json.dumps({"ok": False, "error": "identity_required",
                          "detail": "--run-id and --correlation-id must be non-empty"}))
        return 2
    # Step-11 R2-F3: identity strings land verbatim in the COMMITTABLE evidence — grammar-
    # validate at the boundary so a path-shaped or control-bearing value never gets there.
    if not (_IDENTITY_RE.fullmatch(args.run_id) and _IDENTITY_RE.fullmatch(args.correlation_id)):
        print(json.dumps({"ok": False, "error": "identity_invalid",
                          "detail": "--run-id and --correlation-id must match "
                                    "[A-Za-z0-9._:-]{1,120}"}))
        return 2
    try:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:  # ValueError covers UnicodeDecodeError (8a R2-F7)
        print(json.dumps({"ok": False, "error": "prompt_file_unreadable", "detail": str(exc)}))
        return 2
    # Step-11 ADV-5: a vacuous brief must not spend candidate/judge calls and mint evidence.
    if not prompt.strip():
        print(json.dumps({"ok": False, "error": "prompt_file_empty",
                          "detail": "the design brief is empty or whitespace-only"}))
        return 2
    try:
        repo_root = er.resolve_repo_root(args.workspace, args.project)
        pe = _pe()
        snapshot = er.resolve_table(repo_root, pe.routing).snapshot
        paths = er.derive_paths(repo_root, args.project, args.run_id, snapshot.pool_concurrency())
        quota = pe.QuotaCoordinator(paths["permits_dir"], snapshot.pool_concurrency())
    except er.MalformedConfig as exc:
        print(json.dumps({"ok": False, "error": "malformed_config", "detail": str(exc)}))
        return 2
    except Exception as exc:  # noqa: BLE001 — 8a R2-F7: routing/schema/IO faults here are config-class, exit 2
        print(json.dumps({"ok": False, "error": "config_resolution_failed",
                          "detail": f"{type(exc).__name__}: {exc}"[:500]}))
        return 2
    # Step-11 R1-F4: --sink/--winner-out/--evidence-out must be pairwise distinct — an
    # alias silently erases the raw audit (os.replace over the sink) or overwrites the
    # design draft with the evidence record.
    outputs = {name: Path(p).resolve() for name, p in
               (("--sink", args.sink), ("--winner-out", args.winner_out),
                ("--evidence-out", args.evidence_out)) if p}
    seen_paths = {}
    for name, resolved in outputs.items():
        if resolved in seen_paths:
            print(json.dumps({"ok": False, "error": "output_path_collision",
                              "detail": f"{name} and {seen_paths[resolved]} resolve to the "
                                        f"same path — outputs must be distinct"}))
            return 2
        seen_paths[resolved] = name
    # 8a R2-F3: a raw-record sink INSIDE the repo would break the "raw sink stays
    # gitignored" guarantee — the sanctioned in-repo location is the gitignored default only.
    if args.sink is not None:
        sink_resolved = Path(args.sink).resolve()
        root = Path(repo_root).resolve()
        if sink_resolved == root or root in sink_resolved.parents:
            print(json.dumps({"ok": False, "error": "sink_inside_repo",
                              "detail": f"--sink {args.sink!r} resolves inside the project repo "
                                        f"— raw records carry full candidate payloads and must "
                                        f"never land on a committable path"}))
            return 2
        # Step-11 R2-F2: ANY git work tree is a committable surface, not just the active
        # repo — a sibling repository would silently receive full candidate payloads.
        # Inside a work tree the sink must be check-ignored; outside any work tree is fine.
        probe = subprocess.run(["git", "-C", str(sink_resolved.parent), "rev-parse",
                                "--is-inside-work-tree"],
                               capture_output=True, text=True, check=False)
        if probe.returncode == 0 and probe.stdout.strip() == "true":
            ignored = subprocess.run(["git", "-C", str(sink_resolved.parent), "check-ignore",
                                      "-q", str(sink_resolved)],
                                     capture_output=True, check=False)
            if ignored.returncode != 0:
                print(json.dumps({"ok": False, "error": "sink_committable_path",
                                  "detail": f"--sink {args.sink!r} resolves inside a git work "
                                            f"tree and is not gitignored — raw records carry "
                                            f"full candidate payloads and must never land on "
                                            f"a committable path"}))
                return 2
    seed = args.seed if args.seed is not None else int.from_bytes(os.urandom(4), "big")
    try:
        rubric_text = load_rubric("design")
        winner, _losers, _judge_obs, record = run_design_round(
            prompt, snapshot=snapshot, quota=quota, capture_root=paths["capture_root"],
            headless=args.headless, seed=seed, sink_path=args.sink,
            run_id=args.run_id, correlation_id=args.correlation_id, rubric_text=rubric_text,
            complete_fn=complete_fn, dispatch=dispatch)
    except Exception as exc:  # noqa: BLE001 — round failures (judge/chain/quota/infeasible) are one taxonomy class
        print(json.dumps({"ok": False, "error": "round_failed",
                          "detail": f"{type(exc).__name__}: {exc}"[:500]}))
        return 3
    # 8a R2-F5: a swallowed raw-sink failure must not yield a green CLI — the full audit
    # record was lost, so the round is a failure and no evidence is written.
    if record.get("sink_error"):
        print(json.dumps({"ok": False, "error": "raw_sink_write_failed",
                          "detail": "run_competitive could not persist the raw record "
                                    "(record['sink_error']) — refusing to emit evidence for "
                                    "an unauditable round"}))
        return 3
    clean = sanitize_record(record, prompt_text=prompt, rubric_text=rubric_text)
    try:
        if args.winner_out:
            payload = getattr(winner, "parsed_payload", None)
            if not payload:
                print(json.dumps({"ok": False, "error": "winner_payload_missing",
                                  "detail": "the winning Observation carries no parsed_payload"}))
                return 3
            atomic_write_text(args.winner_out, payload if isinstance(payload, str) else str(payload),
                              prefix=".bakeoff-winner-", suffix=".tmp")
        if args.evidence_out:
            atomic_write_text(args.evidence_out,
                              json.dumps(clean, indent=2, sort_keys=True) + "\n",
                              prefix=".bakeoff-evidence-", suffix=".tmp")
    except OSError as exc:  # 8a R2-F6/R1-M3: output failure is structured, never a traceback
        print(json.dumps({"ok": False, "error": "output_write_failed", "detail": str(exc)[:500]}))
        return 3
    print(json.dumps({"ok": True, "record": clean, "evidence_out": args.evidence_out,
                      "winner_out": args.winner_out}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
