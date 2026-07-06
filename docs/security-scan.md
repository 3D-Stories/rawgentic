# Security Scan (`hooks/security_scan.py`)

The shared, tool-based security scanner used by **WF2 Step 11.5** (pre-PR gate)
and, until its removal at v3.0.0 (#161), WF9 security-audit. One tested lib so the actual scanning —
running real tools and turning their output into a gate decision — lives in a
single fail-closed place instead of being re-derived in each workflow's prose.

It complements, and does **not** replace, the LLM security review (WF2 Step 11
Agent 3; WF9 STRIDE). Scanners find concrete, known-pattern problems (a leaked
token, a CVE'd dependency, an injection-shaped code pattern). They cannot find
authorization or business-logic flaws — that is what the review and STRIDE are
for. "Scan passed" means "no known-pattern issue," not "secure."

## What it runs

Each scanner is gated on `capabilities` (project type / Docker) **and** on the
tool being installed:

| Kind | Tool | Notes |
|------|------|-------|
| secrets | `gitleaks` | Always. Diff-scoped to the branch's new commits in WF2; whole working tree in WF9 (`--full`). |
| SCA (dependency CVEs) | `osv-scanner` → `npm audit` → `pip-audit` | Prefers osv-scanner (one binary, every ecosystem); falls back to the per-language tool when osv-scanner is absent. Diff-scoped in WF2 (only advisories in manifests the branch changed); whole tree in WF9 (`--full`). See [Diff-scoped SCA](#diff-scoped-sca-230). |
| SAST | `semgrep` | `p/ci` ruleset (low false-positive). Diff-scoped (`--baseline-commit`) in WF2; whole tree in WF9. |
| IaC | `trivy config` | Only when `capabilities.has_docker` (Dockerfile/compose/Actions/k8s/Terraform misconfig). |

## The gate (fail-closed)

- **Fail closed on a real finding.** A leaked secret always blocks. A
  Critical/High dependency CVE, SAST, or IaC finding blocks. A *known CVE with no
  severity rating* blocks (it is still a known CVE). Medium/Low are advisory.
- **Fail closed on a broken scanner.** A tool that ran but produced unparseable
  output is recorded as an error and blocks — "I couldn't tell" is never "clean."
- **Degrade visibly on a missing tool.** A scanner whose tool isn't installed is
  a *skip* (with a reason) and does **not** block. It is surfaced in the report
  and PR body so the coverage gap stays visible — never silently treated as a
  pass. `/rawgentic:setup` (and the session-start bootstrap) install the tools.

The blocking threshold is tunable from v1 via `RAWGENTIC_SECURITY_BLOCK_SEVERITIES`
(comma-separated, default `critical,high`). It is a **threshold**, not exact
membership: the lowest level listed blocks that level *and everything above it*,
so a lower setting only makes the gate stricter (e.g. `medium` blocks
medium/high/critical) and can never accidentally let a high/critical through. An
empty or unrecognized value falls back to the fail-closed default.

## Diff-scoped SCA (#230)

Like the SAST scan (semgrep `--baseline-commit`), the WF2 pre-PR **SCA** scan is
scoped to what the branch introduced — not the repo's whole pre-existing advisory
set. osv-scanner has no native baseline flag, so the gate approximates it:

1. It computes the dependency manifests the branch changed with a merge-base diff
   (`git diff --name-only <base_ref>...HEAD`), filtered to known lockfiles/manifests
   (`package-lock.json`, `Cargo.lock`, `go.sum`, `requirements*.txt`, …).
2. An SCA finding is kept only when its manifest is in that changed set — matched by
   exact repo-relative path first, then a filename (basename) fallback. Advisories in
   manifests the branch never touched are **pre-existing** and do not block.

Fail-closed by construction:

- If git **cannot** determine the diff (nonzero exit / error), **every** SCA finding
  is kept — an unknown diff is never treated as "nothing introduced."
- A pre-existing advisory in a manifest the branch **did** touch still blocks (the
  gate re-justifies dependency state whenever a PR changes it). The scoping only
  removes the block on *untouched* manifests — the reported failure where a
  zero-dependency PR was blocked by 21 unrelated pre-existing advisories.
- `--full` (WF9 whole-tree audit) is **never** scoped, and the `git diff` only runs
  when an SCA scanner is actually selected (so a no-SCA project makes no git call).

## Cargo-workspace canonical-lock awareness (#230)

In a Cargo **workspace**, cargo resolves the workspace-root `Cargo.lock`; a scan
pointed at a member directory (e.g. `src-tauri`) with `--recursive` would miss that
root lock, so a `cargo update` fix there wouldn't register. The gate now:

- Detects the workspace root (nearest ancestor `Cargo.toml` with a `[workspace]`
  table) and, when the canonical root lock lives **above** the scan root, adds it as
  an explicit `--lockfile` so the canonical lock is always scanned.
- Surfaces a **non-blocking warning** when a committed member `Cargo.lock` diverges
  from the canonical root lock (drift means the member-scoped view differs from what
  cargo actually resolves). Divergence detection is gated on a real `[workspace]`
  relationship, so independent, unrelated crates with their own locks are not
  false-flagged.

## Suppressing IaC misconfigs (`.trivyignore`)

A project can suppress specific trivy IaC misconfigs by committing a `.trivyignore`
file at its **project root**, listing the trivy IDs to ignore — written exactly as
trivy emits them in the JSON `Misconfigurations[].ID` field (on current trivy that
is the hyphenated form, e.g. `DS-0002`; a mismatched token such as `DS0002` matches
nothing and silently suppresses nothing), one per line, with `#` comments for the
rationale. When `<project-root>/.trivyignore` exists (and is a file), the gate
passes it to trivy as `--ignorefile <project-root>/.trivyignore`, so the suppression
is honored **deterministically, regardless of the cwd the gate runs from**. (trivy
only auto-discovers `.trivyignore` from its own working directory, which the gate
does not control — so without the explicit `--ignorefile` a committed suppression
would be silently ignored.)

Only the plain-line `.trivyignore` is honored; trivy's structured `.trivyignore.yaml`
variant is **not** passed by the gate (track it as an enhancement if a project needs
the YAML form's `reason`/`expires` fields).

The file is anchored to the **declared `--project-root`**, never an arbitrary path;
when absent, the trivy command is byte-for-byte unchanged. Because the suppression
lives in the repo, appears in the diff, and goes through code review, it is the
project owner's deliberate, auditable choice — matching trivy's intended model.
Record the rationale beside each ID (and ideally in a `docs/` posture note) so a
future reader sees *why* a finding is suppressed.

## Working directory (cwd-independence)

The gate is **cwd-independent for every scanner**: `run_scan` normalizes
`--project-root` to an absolute path once and runs each scanner with that
directory as its working directory (`cwd`). This matters because some tools
resolve git state against their *process cwd*, not the scan target — most
notably semgrep's diff mode (`--baseline-commit <ref>`), which exits `rc=2` and
(fail-closed) blocks the whole gate with zero findings if it can't resolve the
baseline ref from the current directory. Threading `cwd=<project-root>` means
the gate gives identical results whether you invoke it from the repo root or
from the plugin's `hooks/` directory.

This generalizes the `.trivyignore` `--ignorefile` fix above (which made *trivy*
cwd-robust): now every scanner — gitleaks, the SCA tools, semgrep, and trivy —
runs from the declared `--project-root` regardless of the caller's cwd.

## CLI

```bash
# WF2 pre-PR gate (diff-scoped against the default branch)
python3 hooks/security_scan.py scan \
  --project-root <path> --project-type <type> \
  --base-ref origin/<default-branch> [--has-docker] --json

# WF9 audit (whole tree)
python3 hooks/security_scan.py scan \
  --project-root <path> --project-type <type> --full [--has-docker] --json
```

Exit codes: `0` gate PASS · `1` BLOCKED (blocking finding or broken scanner) ·
`2` usage error. With `--json` the output is `{findings, skipped, gate}` where
`gate` is `{blocked, blocking, advisory, errors}`.

## Installing the scanners

Installs are **opt-out, never opt-in**. `scripts/install-scanners.sh` is
idempotent and best-effort (a present tool is left alone; one that can't be
auto-installed is reported, never fatal):

```bash
bash scripts/install-scanners.sh           # install every missing scanner
bash scripts/install-scanners.sh --check    # report presence only (exit 1 if any missing)
```

It runs automatically at two points, both honoring the opt-outs:

- **Every startup/resume** — the `session-start` hook delegates to
  `hooks/scanner_bootstrap.py`, which re-checks presence (cheap `--check`),
  installs only what's missing in the **background** (it never blocks startup),
  throttles repeat attempts (`RAWGENTIC_SCANNER_RETRY_SECONDS`, default 6h), and
  is **self-healing** — a scanner that goes missing, or one added by a plugin
  update, is reinstalled on the next session with no version bookkeeping. It
  **always writes a status file** at `~/.rawgentic/scanner-status.json`
  (`outcome` ∈ `ok` / `installing` / `throttled` / `skipped-optout-env` /
  `skipped-optout-ws` / `skipped-headless` / `error`, plus `checked_at`,
  `present`, `missing`) so a no-fire or failed install is **visible** instead of
  looking like "all clean"; the install log is `~/.rawgentic/scanner-install.log`.
  (This replaces the old fire-once 0-byte `~/.rawgentic/scanners-bootstrapped`
  marker, which permanently disabled re-checks once written.) Skipped in headless.
- **At `/rawgentic:setup`** — Step 2e runs it explicitly and reports results.

**Opt out** with either:

- `RAWGENTIC_SKIP_SCANNER_INSTALL=1` (environment — good for CI/headless), or
- `"installScanners": false` at the top level of `.rawgentic_workspace.json`
  (persisted; `/rawgentic:setup` writes this when you decline).

Install strategy per tool: Homebrew if present → `pipx`/`pip --user` for the
Python tools (semgrep, pip-audit) → GitHub release binary for the Go tools
(gitleaks, osv-scanner, trivy), downloaded to `~/.local/bin`. No remote script is
piped to a shell. If a tool can't be installed, the scan simply skips it
(visibly) until it is available.
