"""Conjunctive usage-limit classifier for claude NONZERO_EXIT collections (#558 AC1).

Pure module — no I/O. Distinct from ``quota.py`` (the inter-process permit
coordinator): this module only classifies evidence already read at the I/O
boundary (``supervisor._read_stderr`` builds :class:`StderrEvidence`;
``supervisor._envelope_meta`` builds :class:`EnvelopeMeta`).

Corpus honesty (design 2026-07-21-558 §AC1): the repo holds ZERO real captured
usage-limit stderr — all 16 existing capture ``stderr.txt`` files are empty, and
spike #455 / the #472 proving run both failed or declined to capture one. The
positive fixtures are therefore SYNTHETIC, built from the external-docs shape
(exit 1, usage-limit + temporal reset language). ``classifier_version = 1``
ships against that synthetic corpus; #559 (live proving run) calibrates against
a genuine capture — the planned correction path is a version bump + fixture
swap + CALIBRATED_CLASSIFIERS allowlist entry. The classifier is conservative:
any conjunct miss → verdict False → exactly the pre-#558 behavior.

v1 matching contract: decode (errors="replace") → ``casefold()`` → match PER
LINE (conjuncts 3 and 4 may match on different lines; no cross-line joining).
Patterns are word-boundary-anchored with bounded proximity inside a line —
never unbounded scans across the whole text. The rule table below IS the
versioned v1 source of truth; its canonical serialization is digest-pinned in
tests next to ``classifier_version``, so any table change fails the digest test
until BOTH the version and ``RULE_TABLE_DIGEST`` are bumped together.

No raw stderr text is persisted — not even an excerpt (provider stderr can
carry tokens/URL secrets). ``rule_ids`` + hashes identify the match; the
capture dir's ``stderr.txt`` (0700) remains the on-disk forensic source.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional

#: Bounded-read ceiling for stderr evidence (8 MiB). Files over the ceiling are
#: never classified: read_error="oversized", prefix hash + TRUE byte count.
CEILING_BYTES = 8 * 1024 * 1024

#: Bump on ANY rule-table change, together with RULE_TABLE_DIGEST.
CLASSIFIER_VERSION = 1

# The v1 rule table: (rule_id, conjunct, pattern). Patterns match against
# casefolded single lines. "rate limit" (API 429 throttling) deliberately does
# NOT match the usage conjunct; "upgrade" is deliberately NOT reset/retry
# language (an upgrade-only usage-limit exit is not temporally recoverable).
_RULES = (
    ("usage.v1/a", "usage_limit_lang", r"\busage\s+limit\b"),
    ("usage.v1/b", "usage_limit_lang",
     r"\b(?:5-hour|weekly|monthly|daily)\s+(?:usage\s+)?limit\b"),
    ("usage.v1/c", "usage_limit_lang", r"\bout\s+of\b[^\n]{0,40}\busage\b"),
    ("reset.v1/a", "reset_retry_lang", r"\bresets?\s+(?:at|in|on)\b"),
    ("reset.v1/b", "reset_retry_lang", r"\bresets?\b[^\n]{0,20}\d"),
    ("reset.v1/c", "reset_retry_lang", r"\btry\s+again\s+(?:at|after|in)\b"),
)

_COMPILED = tuple((rid, conjunct, re.compile(pattern))
                  for rid, conjunct, pattern in _RULES)


def canonical_rule_table() -> str:
    """Deterministic serialization of the v1 rule table (digest-asserted)."""
    rows = [f"{rid}\t{conjunct}\t{pattern}" for rid, conjunct, pattern in _RULES]
    rows.append(f"classifier_version={CLASSIFIER_VERSION}")
    return "\n".join(rows)


#: sha256 of canonical_rule_table() — pinned so a rule change fails loudly
#: until CLASSIFIER_VERSION is bumped alongside it.
RULE_TABLE_DIGEST = "4e7f5f32fd343fa5260897715dcad240869a279ba48772cf5529d5cbd9432953"


@dataclass(frozen=True)
class StderrEvidence:
    """What was read at the I/O boundary — the only sanctioned verdict input."""
    decoded_text: str            # errors="replace" decode of the bytes read
    raw_sha256: str              # sha256 over the RAW BYTES read (not the decode)
    byte_count: int              # TRUE size (may exceed CEILING_BYTES when oversized)
    read_error: Optional[str]    # "missing" | "unreadable: <why>" | "oversized" | None


@dataclass(frozen=True)
class EnvelopeMeta:
    """Bounded transport-envelope read — OBSERVABILITY ONLY, never a verdict input."""
    session_id: Optional[str]
    subtype: Optional[str]           # allowlisted, or "unknown"
    subtype_sha256: Optional[str]    # set when subtype == "unknown"
    error: Optional[str]             # "missing" | "malformed" | "oversized" | None


@dataclass(frozen=True)
class QuotaClassification:
    verdict: bool                # all four conjuncts true AND read_error is None
    conjuncts: dict              # provider_claude / exit_1 / usage_limit_lang / reset_retry_lang
    engine: str                  # echoed inputs — evidence self-describes
    exit_code: Optional[int]
    source: str                  # always "stderr.txt"
    rule_ids: tuple              # matched pattern identifiers, e.g. "usage.v1/a"
    stderr_sha256: str
    stderr_bytes: int
    read_error: Optional[str]    # carried from StderrEvidence — distinct from a negative
    envelope_subtype: Optional[str]
    envelope_subtype_sha256: Optional[str]
    envelope_error: Optional[str]
    classifier_version: int


def evidence_from_bytes(raw: bytes) -> StderrEvidence:
    """Build StderrEvidence from in-memory bytes (pure-side constructor).

    Mirrors the bounded-read contract: over-ceiling input records the prefix
    hash, the TRUE byte count, and read_error="oversized" — it is never
    classified from the oversized prefix.
    """
    if len(raw) > CEILING_BYTES:
        prefix = raw[:CEILING_BYTES]
        return StderrEvidence(
            decoded_text=prefix.decode("utf-8", errors="replace"),
            raw_sha256=hashlib.sha256(prefix).hexdigest(),
            byte_count=len(raw),
            read_error="oversized",
        )
    return StderrEvidence(
        decoded_text=raw.decode("utf-8", errors="replace"),
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        byte_count=len(raw),
        read_error=None,
    )


def classify_quota_exit(*, engine: str, exit_code: Optional[int],
                        stderr: StderrEvidence,
                        envelope: Optional[EnvelopeMeta] = None) -> QuotaClassification:
    """Classify a collection as a genuine usage-limit exit. Conjunctive:

    1. engine == "claude" (codex/zhipu never classify)
    2. exit_code == 1 exactly (SIGKILL 137, exit 2, timeout kills never classify)
    3. usage-limit language in stderr (never bare "limit"/"rate limit")
    4. explicit temporal recovery language (resets at/in, try again at/after/in;
       "upgrade" is NOT a match)

    Any non-None read_error forces verdict False with the reason carried —
    never indistinguishable from an ordinary negative.
    """
    text_hits = {"usage_limit_lang": False, "reset_retry_lang": False}
    matched: list = []
    lines = [line.casefold() for line in stderr.decoded_text.splitlines()]
    for rid, conjunct, rx in _COMPILED:
        for line in lines:
            if rx.search(line):
                matched.append(rid)
                text_hits[conjunct] = True
                break
    conjuncts = {
        "provider_claude": engine == "claude",
        "exit_1": exit_code == 1,
        "usage_limit_lang": text_hits["usage_limit_lang"],
        "reset_retry_lang": text_hits["reset_retry_lang"],
    }
    verdict = stderr.read_error is None and all(conjuncts.values())
    return QuotaClassification(
        verdict=verdict,
        conjuncts=conjuncts,
        engine=engine,
        exit_code=exit_code,
        source="stderr.txt",
        rule_ids=tuple(matched),
        stderr_sha256=stderr.raw_sha256,
        stderr_bytes=stderr.byte_count,
        read_error=stderr.read_error,
        envelope_subtype=envelope.subtype if envelope else None,
        envelope_subtype_sha256=envelope.subtype_sha256 if envelope else None,
        envelope_error=envelope.error if envelope else None,
        classifier_version=CLASSIFIER_VERSION,
    )
