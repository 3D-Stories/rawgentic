#!/usr/bin/env python3
"""WF5 adversarial-review engine for rawgentic (#77).

Cross-model adversarial review of TEXT artifacts (design/spec/plan/PRD/ADR/RFC/
README) via the Codex CLI as an independent, different-model reviewer. This
module holds ALL the logic so it is deterministically testable (the Codex
subprocess is PATH-stubbed in tests — no live calls in CI). The SKILL.md
orchestrator and the WF2/WF3 quality-gate hooks are thin callers, invoking
this via `python3 -c` import-style calls or the `main()` CLI.

Design invariants (issue #77):
- Report-only: never edits the reviewed artifact.
- Fail-closed: any Codex/parse error yields a non-success status; callers must
  check status == "success" before consuming findings.
- Config-gated, default-disabled: `adversarialReview` lives in the per-project
  entry of .rawgentic_workspace.json (sibling to critiqueMethod/headlessEnabled).
- Warn-only egress, with teeth: artifact text is scanned for obvious secrets and
  the egress notice names what it found. Non-blocking unless
  RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS=1.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from typing import Final


# ============================================================================
# Env-frozen constants (clamped, loaded once at import — mirrors plan_lib.py)
# ============================================================================

_MAX_BYTES_DEFAULT = 200_000
_MAX_BYTES_MIN, _MAX_BYTES_MAX = 1_000, 5_000_000
_TIMEOUT_DEFAULT = 300
_TIMEOUT_MIN, _TIMEOUT_MAX = 10, 1_800
_MAX_RETRIES_DEFAULT = 1
_MAX_RETRIES_MIN, _MAX_RETRIES_MAX = 0, 5


def _coerce_int_env(name: str, default: int) -> int:
    """Parse an env var as int. Non-int / empty / malformed -> default.

    Strict acceptance: optional leading '-' then ASCII digits only.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    stripped = raw.strip()
    if stripped.startswith("-"):
        body, sign = stripped[1:], -1
    else:
        body, sign = stripped, 1
    if not body or not body.isascii() or not body.isdigit():
        print(
            f"adversarial_review_lib: env {name}={raw!r} rejected (not an integer); "
            f"using default {default}",
            file=sys.stderr,
        )
        return default
    return sign * int(body)


def _coerce_bool_env(name: str) -> bool:
    """Parse an env var as bool. '1'/'true'/'yes'/'on' (case-insensitive) -> True."""
    raw = os.environ.get(name)
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


MAX_BYTES: Final[int] = _clamp(
    _coerce_int_env("RAWGENTIC_ADV_REVIEW_MAX_BYTES", _MAX_BYTES_DEFAULT),
    _MAX_BYTES_MIN, _MAX_BYTES_MAX,
)
TIMEOUT_SECONDS: Final[int] = _clamp(
    _coerce_int_env("RAWGENTIC_ADV_REVIEW_TIMEOUT", _TIMEOUT_DEFAULT),
    _TIMEOUT_MIN, _TIMEOUT_MAX,
)
MAX_RETRIES: Final[int] = _clamp(
    _coerce_int_env("RAWGENTIC_ADV_REVIEW_MAX_RETRIES", _MAX_RETRIES_DEFAULT),
    _MAX_RETRIES_MIN, _MAX_RETRIES_MAX,
)
BLOCK_SECRETS: Final[bool] = _coerce_bool_env("RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS")

SEVERITIES: Final[tuple[str, ...]] = ("Critical", "High", "Medium", "Low")
_SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}
CATEGORIES: Final[tuple[str, ...]] = (
    "correctness", "completeness", "feasibility", "consistency",
    "internal-consistency", "security", "scope", "ambiguity",
)
ARTIFACT_TYPES: Final[tuple[str, ...]] = (
    "design", "spec", "plan", "prd", "adr", "rfc", "readme", "generic",
)

# Per-type emphasis appended to the adversarial prompt (the "lens").
_TYPE_LENS: Final[dict[str, str]] = {
    "design": "Focus on architectural soundness, coupling, hidden dependencies, and failure modes.",
    "spec": "Focus on testability, edge cases, ambiguous requirements, and internal contradictions.",
    "plan": "Focus on task sequencing, missing steps, risk, and unverifiable acceptance criteria.",
    "prd": "Focus on measurability of success criteria, scope creep, and unstated assumptions.",
    "adr": "Focus on whether alternatives were fairly considered and consequences fully stated.",
    "rfc": "Focus on interoperability, migration/backward-compat, and protocol edge cases.",
    "readme": "Focus on accuracy versus the described system, completeness, and stale instructions.",
    "generic": "Apply correctness, completeness, consistency, security, and ambiguity lenses broadly.",
}


# ============================================================================
# Config (.rawgentic_workspace.json per-project adversarialReview field)
# ============================================================================

@dataclass(frozen=True)
class AdversarialReviewConfig:
    enabled: bool
    workflows: tuple[str, ...]


_DISABLED = AdversarialReviewConfig(enabled=False, workflows=())


def _coerce_config(raw: object) -> AdversarialReviewConfig:
    """Coerce a raw adversarialReview value into config. FAIL-CLOSED.

    Accepts: bool shorthand (True/False), or {enabled: bool, workflows: [str]}.
    Anything else -> disabled. `enabled` must be a real bool to count as enabled.
    """
    if isinstance(raw, bool):
        return AdversarialReviewConfig(enabled=raw, workflows=())
    if isinstance(raw, dict):
        enabled = raw.get("enabled")
        if not isinstance(enabled, bool):
            return _DISABLED
        wf_raw = raw.get("workflows")
        if isinstance(wf_raw, list):
            workflows = tuple(w for w in wf_raw if isinstance(w, str))
        else:
            workflows = ()
        return AdversarialReviewConfig(enabled=enabled, workflows=workflows)
    return _DISABLED


def load_adversarial_review_config(
    workspace_path: str, project_name: str
) -> AdversarialReviewConfig:
    """Read the project's adversarialReview config from the workspace file.

    FAIL-CLOSED: missing file, malformed JSON, missing project, missing field,
    or bad value all resolve to disabled. Never raises.
    """
    try:
        with open(workspace_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _DISABLED
    if not isinstance(data, dict):
        return _DISABLED
    projects = data.get("projects")
    if not isinstance(projects, list):
        return _DISABLED
    for proj in projects:
        if isinstance(proj, dict) and proj.get("name") == project_name:
            return _coerce_config(proj.get("adversarialReview"))
    return _DISABLED


def is_enabled_for(workspace_path: str, project_name: str, skill_name: str) -> bool:
    """True iff adversarialReview is enabled AND skill_name is in workflows."""
    cfg = load_adversarial_review_config(workspace_path, project_name)
    return cfg.enabled and skill_name in cfg.workflows


# ============================================================================
# Artifact IO + safety
# ============================================================================

class ArtifactError(ValueError):
    """Raised when the artifact path is unsafe or unreadable."""


def resolve_artifact_path(artifact_path: str, project_root: str) -> str:
    """Resolve artifact_path and assert the final realpath is under project_root.

    Defends against traversal (../), absolute escape, NUL bytes, and the
    sibling-prefix trap (/x/proj vs /x/proj-evil). Returns the resolved path.
    """
    if "\x00" in artifact_path or "\x00" in project_root:
        raise ArtifactError("NUL byte in path")
    root = os.path.realpath(project_root)
    resolved = os.path.realpath(artifact_path)
    # Use commonpath / sep-boundary check, NOT startswith (avoids prefix trap).
    if resolved != root and not resolved.startswith(root + os.sep):
        raise ArtifactError(
            f"artifact path escapes project root: {artifact_path!r} -> {resolved!r}"
        )
    return resolved


def read_artifact(
    artifact_path: str, project_root: str, *, max_bytes: int | None = None
) -> tuple[str, bool]:
    """Read a text artifact under project_root, capped at max_bytes.

    Returns (text, truncated). Raises ArtifactError on unsafe path or read error.
    """
    cap = MAX_BYTES if max_bytes is None else max_bytes
    resolved = resolve_artifact_path(artifact_path, project_root)
    try:
        with open(resolved, "rb") as f:
            raw = f.read(cap + 1)
    except OSError as exc:
        raise ArtifactError(f"cannot read artifact: {exc}") from exc
    truncated = len(raw) > cap
    if truncated:
        raw = raw[:cap]
    return raw.decode("utf-8", errors="replace"), truncated


# Secret patterns: (category-label, compiled regex). Conservative — aims to
# catch obvious credentials, not to be exhaustive. Fixed patterns (no ReDoS).
_SECRET_PATTERNS: Final[list[tuple[str, "re.Pattern[str]"]]] = [
    ("API key", re.compile(r"(?i)\bapi[_-]?key\b\s*[:=]")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("AWS secret", re.compile(r"(?i)\baws_secret_access_key\b\s*[:=]")),
    ("password", re.compile(r"(?i)\bpassw(or)?d\b\s*[:=]")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("token", re.compile(r"(?i)\b(bearer|access[_-]?token|secret[_-]?token)\b\s*[:= ]")),
    ("generic secret", re.compile(r"(?i)\bsecret\b\s*[:=]")),
]


def scan_for_secrets(text: str) -> list[str]:
    """Return de-duplicated category labels for obvious secrets found in text.

    Order-stable (pattern declaration order). Empty list means none detected.
    """
    found: list[str] = []
    for label, pattern in _SECRET_PATTERNS:
        if label in found:
            continue
        if pattern.search(text):
            found.append(label)
    return found


# ============================================================================
# Codex prerequisite detection
# ============================================================================

def codex_installed() -> bool:
    """True iff a `codex` executable is on PATH."""
    return shutil.which("codex") is not None


def codex_authenticated() -> bool:
    """True iff `codex login status` exits 0. Fail-closed on any error."""
    if not codex_installed():
        return False
    try:
        result = subprocess.run(
            ["codex", "login", "status"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


_INSTALL_MSG = (
    "Codex CLI is not installed. Install it (standalone binary) with:\n"
    "  curl -fsSL https://codex.openai.com/install.sh | bash\n"
    "then restart your shell so `codex` is on PATH."
)
_AUTH_MSG = (
    "Codex CLI is installed but not authenticated. Run:\n"
    "  codex login            # interactive ChatGPT OAuth\n"
    "For headless/CI use, authenticate with an API key instead:\n"
    "  printenv OPENAI_API_KEY | codex login --with-api-key"
)
_HEADLESS_AUTH_MSG = (
    "Codex CLI is not authenticated and the session is headless. ChatGPT OAuth "
    "login is interactive-only and cannot run unattended. Provide API-key auth:\n"
    "  printenv OPENAI_API_KEY | codex login --with-api-key"
)


def prereq_status(headless: bool = False) -> tuple[bool, str]:
    """Return (ok, message). ok == True only when installed AND authenticated.

    In headless mode an unauthenticated state yields a headless-specific message
    so the caller can ERROR (not suspend for interactive login).
    """
    if not codex_installed():
        return False, _INSTALL_MSG
    if not codex_authenticated():
        return False, _HEADLESS_AUTH_MSG if headless else _AUTH_MSG
    return True, "Codex CLI installed and authenticated."


# ============================================================================
# Findings schema + validation + normalization
# ============================================================================

FINDINGS_SCHEMA: Final[dict] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": False,
    "required": ["findings"],
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["severity", "category", "description", "recommendation"],
                "properties": {
                    "severity": {"enum": list(SEVERITIES)},
                    "category": {"type": "string"},
                    "description": {"type": "string"},
                    "recommendation": {"type": "string"},
                    "ambiguity_flag": {"type": "boolean"},
                    "ambiguity_reason": {"type": "string"},
                    "location": {"type": "string"},
                },
            },
        },
    },
}


def write_schema(path: str) -> None:
    """Write FINDINGS_SCHEMA to path (for `codex exec --output-schema`)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(FINDINGS_SCHEMA, f)


def validate_finding(d: object) -> tuple[bool, list[str]]:
    """Validate a single finding dict. Returns (ok, errors)."""
    errors: list[str] = []
    if not isinstance(d, dict):
        return False, ["finding is not an object"]
    sev = d.get("severity")
    if sev not in SEVERITIES:
        errors.append(f"invalid/missing severity: {sev!r}")
    for field in ("category", "description", "recommendation"):
        val = d.get(field)
        if not isinstance(val, str) or not val.strip():
            errors.append(f"missing/empty {field}")
    if "ambiguity_flag" in d and not isinstance(d["ambiguity_flag"], bool):
        errors.append("ambiguity_flag must be boolean")
    return (not errors), errors


def validate_findings(findings: object) -> tuple[bool, list[str]]:
    """Validate a list of findings. Returns (ok, errors with indices)."""
    if not isinstance(findings, list):
        return False, ["findings is not a list"]
    all_errors: list[str] = []
    for i, f in enumerate(findings):
        ok, errs = validate_finding(f)
        if not ok:
            all_errors.extend(f"finding[{i}]: {e}" for e in errs)
    return (not all_errors), all_errors


def normalize_findings(raw: object) -> list[dict]:
    """Coerce + de-duplicate + severity-rank a list of findings.

    - Drops entries that fail validation.
    - Default category 'completeness' is NOT applied — invalid findings are
      dropped (fail-closed), keeping the schema honest.
    - Dedupe key: (severity, location, description[:80]).
    - Sort by severity rank (Critical first) then category.
    """
    if not isinstance(raw, list):
        return []
    seen: set[tuple] = set()
    out: list[dict] = []
    for f in raw:
        ok, _ = validate_finding(f)
        if not ok:
            continue
        # Dedupe on the FULL description — truncating the key would silently
        # collapse distinct findings that share an opening clause (#77 Step 8a F2).
        key = (f["severity"], f.get("location", ""), f["description"])
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    out.sort(key=lambda x: (_SEVERITY_RANK[x["severity"]], x["category"]))
    return out


# ============================================================================
# Codex invocation (fail-closed)
# ============================================================================

@dataclass(frozen=True)
class CodexResult:
    status: str  # not_installed|unauthenticated|timeout|error|parse_error|success
    findings: tuple
    raw_error: str = ""
    summary: str = ""
    truncated: bool = False
    secrets: tuple = ()


def build_prompt(artifact_text: str, artifact_type: str) -> str:
    """Construct the adversarial review prompt with a type-aware lens."""
    lens = _TYPE_LENS.get(artifact_type, _TYPE_LENS["generic"])
    cats = ", ".join(CATEGORIES)
    return (
        "You are an independent adversarial reviewer. Critically review the "
        f"following {artifact_type} artifact. {lens}\n\n"
        "Find real problems: contradictions, missing cases, unverifiable claims, "
        "security gaps, and ambiguity. Be specific and skeptical; do not praise.\n"
        f"Classify each finding with severity in [{', '.join(SEVERITIES)}] and "
        f"category in [{cats}]. Provide a concrete recommendation and a location "
        "(section or line) for each. Respond using the provided output schema only.\n\n"
        "=== ARTIFACT START ===\n"
        f"{artifact_text}\n"
        "=== ARTIFACT END ==="
    )


def _parse_codex_output(text: str) -> tuple[list, str] | None:
    """Parse Codex's JSON output into (findings, summary). None on failure."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    findings = data.get("findings")
    if not isinstance(findings, list):
        return None
    summary = data.get("summary", "")
    return findings, summary if isinstance(summary, str) else ""


def run_codex_review(
    artifact_path: str,
    artifact_type: str,
    project_root: str,
    *,
    timeout: int | None = None,
    headless: bool = False,
) -> CodexResult:
    """Run an adversarial review via Codex. FAIL-CLOSED on every error path.

    Reads + size-caps the artifact, scans for secrets (optionally blocking),
    builds a type-aware prompt, and invokes `codex exec --output-schema` with
    shell=False and the prompt on stdin. Validates the structured output.
    """
    # Prereq (gate before any work / egress).
    if not codex_installed():
        return CodexResult(status="not_installed", findings=(), raw_error=_INSTALL_MSG)
    if not codex_authenticated():
        msg = _HEADLESS_AUTH_MSG if headless else _AUTH_MSG
        return CodexResult(status="unauthenticated", findings=(), raw_error=msg)

    try:
        artifact_text, truncated = read_artifact(artifact_path, project_root)
    except ArtifactError as exc:
        return CodexResult(status="error", findings=(), raw_error=str(exc))

    secrets = tuple(scan_for_secrets(artifact_text))
    if secrets and BLOCK_SECRETS:
        return CodexResult(
            status="error", findings=(), secrets=secrets, truncated=truncated,
            raw_error=(
                "Refusing to send artifact to Codex: possible secrets detected "
                f"({', '.join(secrets)}). Unset RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS to override."
            ),
        )

    prompt = build_prompt(artifact_text, artifact_type)
    eff_timeout = TIMEOUT_SECONDS if timeout is None else timeout

    # Per-invocation unique temp names so concurrent reviews in the same
    # project_root cannot collide / read each other's output (#77 Step 8a F4).
    token = uuid.uuid4().hex[:12]
    schema_path = os.path.join(project_root, f".rawgentic-adv-review-schema-{token}.json")
    out_path = os.path.join(project_root, f".rawgentic-adv-review-out-{token}.json")
    last_error = ""
    try:
        write_schema(schema_path)
    except OSError as exc:
        return CodexResult(status="error", findings=(), raw_error=f"schema write failed: {exc}")

    try:
        for attempt in range(MAX_RETRIES + 1):
            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except OSError:
                    pass
            cmd = [
                "codex", "exec",
                "--output-schema", schema_path,
                "-o", out_path,
                "-s", "read-only",
                "-C", project_root,
                "--skip-git-repo-check",
                "-",  # read prompt from stdin
            ]
            try:
                result = subprocess.run(
                    cmd, input=prompt, capture_output=True, text=True,
                    timeout=eff_timeout, shell=False,
                )
            except subprocess.TimeoutExpired:
                last_error = f"codex timed out after {eff_timeout}s"
                continue
            except OSError as exc:
                return CodexResult(status="error", findings=(), raw_error=str(exc),
                                   truncated=truncated, secrets=secrets)
            if result.returncode != 0:
                last_error = (result.stderr or result.stdout or "").strip()[:2000]
                continue
            # Prefer the structured output file; fall back to stdout.
            payload = ""
            if os.path.exists(out_path):
                try:
                    with open(out_path, "r", encoding="utf-8") as f:
                        payload = f.read()
                except OSError:
                    payload = ""
            if not payload.strip():
                payload = result.stdout
            parsed = _parse_codex_output(payload)
            if parsed is None:
                return CodexResult(status="parse_error", findings=(),
                                   raw_error="could not parse Codex output as findings JSON",
                                   truncated=truncated, secrets=secrets)
            raw_findings, summary = parsed
            ok, errs = validate_findings(raw_findings)
            if not ok:
                return CodexResult(status="parse_error", findings=(),
                                   raw_error="; ".join(errs[:10]),
                                   truncated=truncated, secrets=secrets)
            findings = normalize_findings(raw_findings)
            return CodexResult(status="success", findings=tuple(findings),
                               summary=summary, truncated=truncated, secrets=secrets)
        # Retries exhausted.
        status = "timeout" if "timed out" in last_error else "error"
        return CodexResult(status=status, findings=(), raw_error=last_error,
                           truncated=truncated, secrets=secrets)
    finally:
        for p in (schema_path, out_path):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass


# ============================================================================
# Report rendering
# ============================================================================

def slugify(name: str) -> str:
    """Sanitize an artifact name into a safe slug for a report filename."""
    base = os.path.basename(name)
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", base).strip("-").lower()
    slug = re.sub(r"-+", "-", slug)
    return (slug or "artifact")[:50]


def _safe_date(date_str: str) -> str:
    """Sanitize a date string for use in a filename (no path separators / traversal)."""
    cleaned = re.sub(r"[^0-9A-Za-z-]", "-", date_str).strip("-")
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned[:32] or "undated"


def review_report_path(project_root: str, artifact_name: str, date_str: str) -> str:
    """Return <project_root>/docs/reviews/<slug>-<date>.md.

    BOTH the artifact name and the date are sanitized — neither may introduce
    path separators or traversal (#77 Step 8a F1).
    """
    return os.path.join(
        project_root, "docs", "reviews",
        f"{slugify(artifact_name)}-{_safe_date(date_str)}.md",
    )


def egress_warning(secrets: list[str] | tuple[str, ...] | None = None) -> str:
    """Return the warn-only egress notice; names detected secret categories."""
    base = (
        "⚠️  Adversarial review sends the artifact text to OpenAI (Codex) for "
        "an independent model review. The artifact is transmitted off-box."
    )
    if secrets:
        base += (
            "\n⚠️  Possible secrets detected in the artifact "
            f"({', '.join(secrets)}). Review before proceeding; set "
            "RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS=1 to block egress when secrets are found."
        )
    return base


def render_report_md(findings: list[dict], meta: dict) -> str:
    """Render a markdown adversarial-review report."""
    counts = {s: 0 for s in SEVERITIES}
    for f in findings:
        if f.get("severity") in counts:
            counts[f["severity"]] += 1
    lines = [
        f"# Adversarial Review — {meta.get('artifact', 'artifact')}",
        "",
        f"- Date: {meta.get('date', '')}",
        f"- Artifact type: {meta.get('artifact_type', 'generic')}",
        f"- Reviewer: Codex ({meta.get('codex_version', 'unknown')})",
        f"- Findings: {len(findings)} "
        f"(Critical {counts['Critical']}, High {counts['High']}, "
        f"Medium {counts['Medium']}, Low {counts['Low']})",
    ]
    if meta.get("truncated"):
        lines.append(f"- ⚠️ Artifact truncated to {MAX_BYTES} bytes before review.")
    if meta.get("secrets"):
        lines.append(f"- ⚠️ Possible secrets detected: {', '.join(meta['secrets'])}.")
    if meta.get("summary"):
        lines += ["", "## Summary", "", str(meta["summary"])]
    lines += ["", "## Findings", ""]
    if not findings:
        lines.append("_No findings returned._")
    for i, f in enumerate(findings, 1):
        lines += [
            f"### {i}. [{f['severity']}] {f['category']} — {f.get('location', 'n/a')}",
            "",
            f["description"],
            "",
            f"**Recommendation:** {f['recommendation']}",
        ]
        if f.get("ambiguity_flag"):
            lines.append(f"**Ambiguity:** {f.get('ambiguity_reason', '(flagged)')}")
        lines.append("")
    lines += [
        "---",
        "_Report-only: this review does not edit the artifact. "
        "Findings are advisory; incorporate them at your discretion._",
    ]
    return "\n".join(lines)


# ============================================================================
# CLI (for test ergonomics; SKILL.md may also import directly)
# ============================================================================

def main(argv: list[str] | None = None) -> int:
    """CLI: prereq | is-enabled | review. Exit codes: 0 ok, 2 prereq, 3 codex, 4 parse."""
    parser = argparse.ArgumentParser(prog="adversarial_review_lib")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_prereq = sub.add_parser("prereq", help="check Codex prerequisites")
    p_prereq.add_argument("--headless", action="store_true")

    p_enabled = sub.add_parser("is-enabled", help="check per-project enablement")
    p_enabled.add_argument("--workspace", required=True)
    p_enabled.add_argument("--project", required=True)
    p_enabled.add_argument("--skill", required=True)

    p_review = sub.add_parser("review", help="run an adversarial review")
    p_review.add_argument("--artifact", required=True)
    p_review.add_argument("--type", default="generic")
    p_review.add_argument("--project-root", required=True)
    p_review.add_argument("--date", default="")
    p_review.add_argument("--headless", action="store_true")

    args = parser.parse_args(argv)

    if args.cmd == "prereq":
        ok, msg = prereq_status(headless=args.headless)
        print(msg)
        return 0 if ok else 2

    if args.cmd == "is-enabled":
        enabled = is_enabled_for(args.workspace, args.project, args.skill)
        print("enabled" if enabled else "disabled")
        return 0 if enabled else 1

    if args.cmd == "review":
        artifact_type = args.type if args.type in ARTIFACT_TYPES else "generic"
        result = run_codex_review(
            args.artifact, artifact_type, args.project_root, headless=args.headless
        )
        if result.status in ("not_installed", "unauthenticated"):
            print(result.raw_error, file=sys.stderr)
            return 2
        if result.status in ("timeout", "error"):
            print(result.raw_error, file=sys.stderr)
            return 3
        if result.status == "parse_error":
            print(result.raw_error, file=sys.stderr)
            return 4
        # success
        date_str = args.date or "unknown-date"
        report = render_report_md(
            list(result.findings),
            {"artifact": os.path.basename(args.artifact), "date": date_str,
             "artifact_type": artifact_type, "summary": result.summary,
             "truncated": result.truncated, "secrets": list(result.secrets)},
        )
        path = review_report_path(args.project_root, args.artifact, date_str)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(report)
        except OSError as exc:
            # Fail-closed: a write failure must surface as a non-zero exit, not a
            # traceback that a caller could misread as success (#77 Step 8a F3).
            print(f"failed to write report: {exc}", file=sys.stderr)
            return 3
        print(path)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
