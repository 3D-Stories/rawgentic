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

import json
import random
import sys
from pathlib import Path
from typing import Optional, Sequence

# Sibling hook import (hooks/*.py import each other via PYTHONPATH=hooks / sys.path.insert).
import adversarial_review_lib as _adv

_REPO = Path(__file__).resolve().parent.parent
RUBRIC_DIR = Path(__file__).resolve().parent / "data" / "bakeoff_rubrics"
DEFAULT_SINK_PATH = _REPO / "docs" / "measurements" / "bakeoff_results.jsonl"

# Competitor sets (models resolved to lanes from the routing table at call time).
DESIGN_MODELS: tuple = ("gpt-5.6-sol", "claude-opus-4-8")                       # sol vs opus, every round
BUILD_MODELS: tuple = ("claude-sonnet-5", "claude-opus-4-8", "gpt-5.6-terra")   # gate-flagged bake-off
INCUMBENT_MODEL = "claude-opus-4-8"   # the headless judge-degrade fallback (owner-picked, §3.3)


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


def make_glm_judge(rubric_text: str, *, seed, complete_fn=None, build_evidence=None, retries: int = 1):
    """Return a ``judge(results, rubric=None)`` callable for ``run_competitive``.

    Anonymizes + shuffles the ok candidates, asks glm-5.2 to score + pick a winner on the (1-based)
    draft labels, maps the winning label back to the ORIGINAL candidate index
    (``winner_index = order[winner_draft - 1]`` — 1-based label to 0-based order, Step-4 finding M1),
    and returns ``{winner_index, scores, degraded: False}``. ``confidence`` is folded into ``scores``
    so ``run_competitive``'s record retains it (finding L1). Up to ``retries`` extra glm attempts on a
    None/unparseable verdict (§3.3 "after one retry"); a degenerate candidate set is NOT retried."""
    complete = complete_fn or _adv.glm_complete

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
def hybrid_failure_strategy(*, headless: bool, incumbent_index: int, sink=None):
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
                sink({"winner_index": None, "n_candidates": len(results), "judge_degraded": True,
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


def _candidates_for(snapshot, models, prompt, *, seat):
    pe = _pe()
    candidates = []
    for model in models:
        lane = _lane_for_model(snapshot, model)
        if lane is None:
            raise ValueError(f"model {model!r} has no lane in the routing table (cannot build candidate)")
        candidates.append(pe.Candidate(
            seat=seat, model=model, prompt=prompt, provider=lane["provider"], pool=lane["pool"],
            transport=lane.get("transport", "native"), auth_mode=lane.get("auth_mode", "subscription_oauth"),
            credential_ref=lane.get("credential_ref")))
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
                     sink_path=None, models=DESIGN_MODELS, complete_fn=None, dispatch=None):
    """Competitive design round (sol vs opus every round), glm-5.2 judge on the design rubric.
    Winner's exact bytes become the phase artifact. Returns ``run_competitive``'s
    ``(winner, losers, judge_obs, record)``."""
    pe = _pe()
    candidates = _candidates_for(snapshot, models, prompt, seat="design")
    incumbent_index = models.index(INCUMBENT_MODEL) if INCUMBENT_MODEL in models else 0
    judge = make_glm_judge(load_rubric("design"), seed=seed, complete_fn=complete_fn)
    sink = bakeoff_sink(sink_path)
    strategy = hybrid_failure_strategy(headless=headless, incumbent_index=incumbent_index, sink=sink)
    kwargs = dict(judge=judge, failure_strategy=strategy, sink=sink, snapshot=snapshot, quota=quota,
                  capture_root=capture_root, require_parallel=True)
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
