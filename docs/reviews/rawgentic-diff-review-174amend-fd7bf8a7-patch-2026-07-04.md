# Adversarial Review — .rawgentic-diff-review-174amend-fd7bf8a7.patch

- Date: 2026-07-04
- Artifact type: diff
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 2 (Critical 0, High 0, Medium 2, Low 0)

## Summary

The diff adds `designArtifact.sharedDoc` configuration and prose wiring across WF1/WF2/WF3, plus a reader helper and corpus tests. The main risk is that the helper accepts paths outside the documented planning-doc shape, so the new artifact mode can mutate arbitrary in-repo files despite being presented as a design-doc lifecycle feature.

## Findings

### 1. [Medium] correctness · high confidence — hooks/adversarial_review_lib.py, design_artifact_shared_doc

> +            # project-relative only: reject absolute paths and traversal
> +            if os.path.isabs(sd) or ".." in sd.split("/"):
> +                return None
> +            return sd

The new reader only rejects absolute paths and `..` segments, but the documented shape for `sharedDoc` is `docs/planning/<name>.md`. As written, values such as `README.md`, `.github/workflows/build.yml`, or `hooks/render_artifact.py` are accepted and returned, causing shared-doc mode to update arbitrary in-repo files instead of a planning markdown artifact.

**Recommendation:** In `design_artifact_shared_doc`, validate that `sd` is a normalized relative path under `docs/planning/` and has a `.md` suffix before returning it; otherwise return `None`. Add tests for accepted `docs/planning/program.md` and rejected paths outside `docs/planning` or without `.md`.

### 2. [Medium] internal-consistency · high confidence — docs/config-reference.md, designArtifact row

> +| `designArtifact` | `object` \| `bool` | Opt-in HTML design-artifact lifecycle (#174): WF1 renders + publishes the issue spec artifact; WF2/WF3 create-or-update the `docs/planning/<issue>.{md,html}` artifact (with run telemetry embedded) inside the feature PR before `gh pr create`. Shape: `{ "enabled": bool, "workflows": ["create-issue", "implement-feature", "fix-bug"], "sharedDoc"?: "docs/planning/<name>.md" }` — mirrors `adversarialReview` plus an optional `sharedDoc`. Default disabled (byte-identical behavior for opted-out projects). Fail-closed: missing/malformed → disabled. **`sharedDoc` (optional, project-relative path):** when set, WF1/WF2/WF3 update that ONE rolling design doc across every issue — the multi-issue / campaign model, one program doc refreshed per slot (like this repo's modernization dashboard) — instead of a per-issue `<issue>-<slug>.{md,html}` file; unset = per-issue (default). Absolute paths or `..` traversal in `sharedDoc` fail safe to per-issue. Rendering uses `hooks/render_artifact.py` (self-contained, CSP-safe, escape-first, mountain-time datetime stamp). |

The config reference says malformed `designArtifact` is fail-closed to disabled, but the new `sharedDoc` behavior says invalid `sharedDoc` fails safe to per-issue. Those are different outcomes: a malformed `sharedDoc` inside an enabled block will still create artifacts, not disable `designArtifact`. Implementers cannot tell whether bad `sharedDoc` should disable artifact generation or merely ignore shared-doc mode.

**Recommendation:** In `docs/config-reference.md`, split the behaviors explicitly: keep `designArtifact` missing/malformed as disabled, and state that a malformed `sharedDoc` within an otherwise valid enabled block is ignored and falls back to per-issue. Mirror that wording in WF1/WF2/WF3 skill prose.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._