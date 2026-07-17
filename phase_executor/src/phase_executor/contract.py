"""The Observation contract — one producer of the normative JSON-Schema documents.

`contract.py` is ONE producer implementation; a Rust producer (kukakuka) emits the same
`observation.schema.json` documents. The schema is the normative artifact, not this module.

Design rules (plan 2026-07-16-per-phase-model-routing §3.1 + #424 design §9c):
- `actual_model` is the provider-reported id from the INNERMOST envelope. It is mandatory
  evidence when `parse_status == "ok"` and MAY be null only on a non-success status (so a
  pre-envelope timeout is still recordable). The schema enforces this conditional; we never
  fabricate an id or substitute the requested model.
- `canonicalize_model_id` normalizes ids for the requested==actual comparison (aliases,
  provider prefixes, context-window `[..]` tags, trailing dates) WITHOUT rewriting the raw
  evidence stored in `actual_model`.
"""
from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = "1"

# parse_status vocabulary (mirrors observation.schema.json enum).
OK = "ok"
NONZERO_EXIT = "nonzero_exit"
TIMEOUT = "timeout"
LAUNCH_ERROR = "launch_error"
PARSE_ERROR = "parse_error"
IDENTITY_FAILURE = "identity_failure"
USAGE_UNAVAILABLE = "usage_unavailable"
HARNESS_ERROR = "harness_error"
PARSE_STATUSES = frozenset(
    {OK, NONZERO_EXIT, TIMEOUT, LAUNCH_ERROR, PARSE_ERROR, IDENTITY_FAILURE, USAGE_UNAVAILABLE, HARNESS_ERROR}
)

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"

_PROVIDER_PREFIXES = (
    "us.anthropic.", "eu.anthropic.", "apac.anthropic.", "anthropic.", "anthropic/",
    "openai/", "openai.", "zhipuai/", "zhipu/",
)
_BRACKET_RE = re.compile(r"\[[^\]]*\]")          # context-window / variant tag, e.g. [1m]
_TRAILING_DATE_RE = re.compile(r"-\d{8}$")        # dated revision, e.g. -20251001


@functools.lru_cache(maxsize=None)
def _load_schema(name: str) -> dict:
    return json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))


def observation_schema() -> dict:
    return _load_schema("observation.schema.json")


def routing_table_schema() -> dict:
    return _load_schema("routing-table.schema.json")


def canonicalize_model_id(model_id: Optional[str]) -> str:
    """Normalize a model id for requested==actual comparison.

    Strips a known provider prefix, a bracketed variant tag (``[1m]``), and a trailing
    ``-YYYYMMDD`` date; lowercases. Does NOT collapse distinct families/versions
    (``claude-opus-4-8`` != ``claude-sonnet-5``). Returns "" for a falsy/non-string input
    so an absent id never spuriously matches another absent id at the call site.
    """
    if not model_id or not isinstance(model_id, str):
        return ""
    s = model_id.strip().lower()
    for prefix in _PROVIDER_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    s = _BRACKET_RE.sub("", s)
    s = _TRAILING_DATE_RE.sub("", s)
    return s.strip("-. ")


def models_match(requested: Optional[str], actual: Optional[str]) -> bool:
    """True iff requested and actual canonicalize to the same non-empty id."""
    rc = canonicalize_model_id(requested)
    ac = canonicalize_model_id(actual)
    return bool(rc) and rc == ac


@dataclass(frozen=True)
class Observation:
    """A single model-seat invocation record. Serialize with ``to_dict``; the dict conforms
    to observation.schema.json (validate with ``validate_observation``)."""

    run_id: str
    attempt_id: str
    seat: str
    engine: str
    transport: str
    requested_model: str
    actual_model: Optional[str]
    prompt_hash: str
    usage: Optional[dict]
    timing_ms: int
    queued_ms: int
    process: dict
    parse_status: str
    parsed_payload: Any
    raw_capture_path: Optional[str]
    fallback_reason: Optional[str]
    routing_config_digest: str
    context_hashes: list = field(default_factory=list)
    correlation_id: Optional[str] = None
    judge_degraded: Optional[bool] = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        out = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "correlation_id": self.correlation_id,
            "seat": self.seat,
            "engine": self.engine,
            "transport": self.transport,
            "requested_model": self.requested_model,
            "actual_model": self.actual_model,
            "prompt_hash": self.prompt_hash,
            "context_hashes": list(self.context_hashes),
            "usage": self.usage,
            "timing_ms": self.timing_ms,
            "queued_ms": self.queued_ms,
            "process": dict(self.process),
            "parse_status": self.parse_status,
            "parsed_payload": self.parsed_payload,
            "raw_capture_path": self.raw_capture_path,
            "fallback_reason": self.fallback_reason,
            "routing_config_digest": self.routing_config_digest,
        }
        # judge_degraded is a bool-only optional (no null in schema): emit only when set.
        if self.judge_degraded is not None:
            out["judge_degraded"] = self.judge_degraded
        return out


def validate_observation(obs: dict) -> None:
    """Raise jsonschema.ValidationError if ``obs`` does not conform. Fail-loud."""
    import jsonschema  # noqa: PLC0415 (deferred: keep import cost off the hot path / off consumers that only build)

    jsonschema.validate(obs, observation_schema())


def validate_routing_table(table: dict) -> None:
    """Raise jsonschema.ValidationError if ``table`` does not conform. Fail-loud."""
    import jsonschema  # noqa: PLC0415

    jsonschema.validate(table, routing_table_schema())
