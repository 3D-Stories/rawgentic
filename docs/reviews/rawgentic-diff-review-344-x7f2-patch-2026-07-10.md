# Adversarial Review — .rawgentic-diff-review-344-x7f2.patch

- Date: 2026-07-10
- Artifact type: diff
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 4 (Critical 0, High 0, Medium 4, Low 0)

## Summary

The change expands artifact rendering to seven visual templates and changes paragraph handling. The main risks are broken backward-compatibility claims, incomplete malformed-config handling, and inconsistent newline/CSP behavior.

## Findings

### 1. [Medium] correctness · high confidence — hooks/adversarial_review_lib.py, `design_artifact_style`

> +    for proj in data.get("projects", []) or []:
> +        if isinstance(proj, dict) and proj.get("name") == project_name:

The function does not validate that `projects` is iterable. A syntactically valid but malformed config such as `{"projects": 1}` raises `TypeError`, contradicting the documented malformed-config fallback and `Never raises` contract; artifact style resolution stops instead of warning and returning `design`.

**Recommendation:** In `design_artifact_style`, assign `projects = data.get("projects", [])`, require it to be a list, and return `design` with a warning when it has any other type before entering the loop.

### 2. [Medium] correctness · high confidence — hooks/render_artifact.py, `_render_roadmap`

> +    lines = markdown.split("\n")
> +    h2 = re.compile(r"##(?!#)\s+(.*)")  # h2 only — ### and deeper stay in-card

CR normalization was added only inside `_render_body_plain`, after `_render_roadmap` has already split and classified the source. With CR-only input, an entire roadmap/dashboard document remains one line and section boundaries collapse into a single heading; CRLF headings can also retain a trailing carriage return. This contradicts the change's general claim that CR line endings are normalized.

**Recommendation:** Normalize `markdown` at the start of `_render_roadmap` using the same `replace("\r\n", "\n").replace("\r", "\n")` sequence before splitting, or normalize once at the public renderer entry point.

### 3. [Medium] correctness · high confidence — docs/design-language.md, Security invariants

> +  **CSP-safe / self-contained.** Inline CSS only; no external host (no CDN link/script/
> +  font/img), so the artifact survives a strict Content-Security-Policy and renders
> +  anywhere offline.

Inline CSS is not inherently compatible with a strict CSP: a policy that disallows inline styles will block the generated `<style>` element, leaving the artifact unstyled. Self-contained/offline operation does not establish the stated CSP compatibility.

**Recommendation:** In `docs/design-language.md` and other CSP-safe claims, document the exact required `style-src` policy and qualify compatibility accordingly, or emit a CSP-authorized stylesheet using an appropriate hash or nonce integration.
**Ambiguity:** The artifact does not define what policy it means by “strict Content-Security-Policy,” but common strict policies reject unauthenticated inline styles.

### 4. [Medium] internal-consistency · high confidence — hooks/render_artifact.py, `_render_body` documentation

> +    blocks). ``plain`` (default) is byte-for-byte the pre-#199 renderer. A decorator,

The byte-compatibility claim is false: this diff changes consecutive plain lines from separate paragraphs into one soft-wrapped paragraph. Any snapshot or downstream consumer relying on byte-identical plain HTML will receive different output.

**Recommendation:** Update `_render_body` and registry comments to remove the byte-identical claim, or preserve the old paragraph behavior for `plain` and enable soft-wrap joining only in the new templates.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._