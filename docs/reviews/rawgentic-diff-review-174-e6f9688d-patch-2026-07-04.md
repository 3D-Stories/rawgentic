# Adversarial Review — .rawgentic-diff-review-174-e6f9688d.patch

- Date: 2026-07-04
- Artifact type: diff
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 3 (Critical 0, High 1, Medium 2, Low 0)

## Summary

The change adds an HTML artifact renderer plus lifecycle instructions/tests. The main risks are in telemetry robustness and an opt-in gate instruction that silently skips on configuration read errors, which can fail open against the stated lifecycle requirement.

## Findings

### 1. [High] correctness · high confidence — skills/create-issue/SKILL.md Step 4b

> +Exit 0 → enabled; non-zero → skip (default; behavior byte-identical for opted-out

The opt-in check collapses every non-zero result into a silent skip. That includes genuine errors such as a missing helper import, malformed `.rawgentic_workspace.json`, wrong project name substitution, or other config-read failures, so an opted-in project can fail to create/publish the required artifact with no visible failure.

**Recommendation:** Change `skills/create-issue/SKILL.md` Step 4b to distinguish disabled from error: require the gate command to return a distinct code/output for disabled, and treat import/config/read errors as blocking failures with an explicit log entry.

### 2. [Medium] correctness · high confidence — hooks/render_artifact.py _telemetry_html

> +    gates = t.get("gates") or []
> +    gate_rows = ""
> +    for g in gates:
> +        gate_rows += (f"<tr><td>{esc(g.get('step','?'))}</td><td>{esc(g.get('name','?'))}</td>"

Telemetry rendering is only robust for a non-dict top-level value, but it assumes every `gates` element is a dict. A malformed or drifted run-record with `gates` as strings, numbers, or nulls crashes with `AttributeError`, contradicting the stated tolerant rendering behavior and preventing artifact generation.

**Recommendation:** In `hooks/render_artifact.py` `_telemetry_html`, validate `gates` as a list of dicts before calling `.get`; render invalid entries as a visible telemetry-unavailable placeholder or skip them with an explicit warning row.

### 3. [Medium] internal-consistency · high confidence — hooks/render_artifact.py render_artifact

> +    tel = _telemetry_html(telemetry) if telemetry else ""

The renderer contains a placeholder path for an unrecognized telemetry dict, but this truthiness check bypasses it for an empty dict. Passing `{}` as the run-record silently emits no telemetry section, even though `_telemetry_html` explicitly says a dict with no recognized fields should be surfaced visibly.

**Recommendation:** Change `render_artifact` to call `_telemetry_html(telemetry)` whenever `telemetry is not None`, not only when it is truthy.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._