# herdr supply-chain vet + pinned install + config baseline (#609)

**Issue:** [#609](https://github.com/3D-Stories/rawgentic/issues/609) · epic
[#667](https://github.com/3D-Stories/rawgentic/issues/667) (HD1.1, plan §4.4)
**Date:** 2026-07-27 · **Verdict: PASS — the installed binary is the authentic pinned release,
verified bit-for-bit against the upstream published digest.**

herdr became load-bearing for two projects' build seats on 2026-07-27
(`projects/rawgentic` at `e673192`, `projects/thewanderinginn` at `f64b467`) while sitting on an
install of unrecorded provenance. This document is the vet artifact that closes that gap: it
records what is installed, proves it is the official release, pins it machine-readably, and
specifies the hardened config baseline.

## 1. What is installed (confirmed)

| Property | Value | How confirmed |
|---|---|---|
| Path | `~/.local/bin/herdr` | `command -v herdr` |
| Version | `herdr 0.7.5` | `herdr --version` (exact stdout) |
| Size | 21,315,048 bytes | `ls -la` |
| SHA-256 | `3dc83288073e4c2d3c679a30e7be97bcca9141c6fd17dbbb9219142e95c59253` | `sha256sum ~/.local/bin/herdr` |
| Upstream repo | `ogulcancelik/herdr` | owner-supplied (epic-run D-6) |
| Upstream tag | `v0.7.5`, published 2026-07-21T18:11:20Z, `isPrerelease: false`, marked Latest | `gh release view v0.7.5` |
| Upstream asset | `herdr-linux-x86_64`, size **21,315,048** | `gh release view v0.7.5 --json assets` |
| Upstream digest | `sha256:3dc83288073e4c2d3c679a30e7be97bcca9141c6fd17dbbb9219142e95c59253` | `gh api repos/ogulcancelik/herdr/releases/tags/v0.7.5` → asset `.digest` |

**The local SHA-256 and the upstream published digest are identical, and the sizes are identical.
The installed binary is the official `v0.7.5` `linux-x86_64` release, bit-for-bit.**

Provenance was established by **verification, not re-installation** (epic-run D-5): the live herdr
server was backing 8 panes with 4 sessions actively working — including another project's in-flight
WF2 run — and no supply-chain goal required disturbing them. Verification yields the same
assurance as a clean re-install and costs nothing.

Two method notes, so this is reproducible and not over-claimed:
- The release publishes **no separate `checksums.txt` asset**. The GitHub API's per-asset `digest`
  field *is* the published checksum, which is why the procedure below reads it rather than
  downloading a checksum file.
- The binary was never re-downloaded during this vet. Comparing the API digest against the local
  hash is sufficient; downloading 21 MB to re-hash it would prove nothing further.

### AGPL posture (owner sign-off carried, 2026-07-23)

herdr is **dual-licensed: AGPL-3.0-or-later + a commercial license** (plan §1.6, repo-confirmed).
rawgentic invokes herdr as a **separate process over CLI/socket** — no linking, no vendoring, no
distribution — so no copyleft obligation attaches. **RE-VERIFY if herdr is ever bundled into a
shipped image**; the commercial license is the standing exit if that changes.

### Trust boundary (owner sign-off carried, 2026-07-23)

Same-user boundary accepted. Confirmed live: `~/.config/herdr/herdr.sock` is mode `srw-------`
(0600) — identical posture to the existing tmux private socket. Revisit only if mixed-trust
processes ever share this account.

## 2. The pin (AC3, AC6)

The pin lives in **`hooks/herdr-pin.json`** — one machine-readable source of truth.

Placement follows the established convention rather than inventing one: the repo already keeps a
machine-readable data file beside the hooks that consume it (`hooks/security-patterns.json`, same
kebab-case JSON-in-`hooks/` shape). Rejected alternatives: `.rawgentic.json` (per-project config,
but a toolchain pin is host scope) and `phase_executor/` (which deliberately does not import
`hooks/`, while #390's doctor will live at `hooks/workspace_doctor.py`).

**How AC6 is satisfied.** AC6 reads *"Pin verified by workspace-doctor (post-#390 re-scope)"*, and
[#390](https://github.com/3D-Stories/rawgentic/issues/390) is still OPEN — no doctor exists. That
is not a blocker, because #390's own owner-approved re-scope gates its herdr checks on *this*
issue: *"Only applies when the workspace has herdr adopted (HD1.1 config baseline present)."*
**#390 depends on #609, not the reverse.** #609's obligation is therefore to record the pin in the
shape #390's check 8 ("herdr binary present AND version == the pinned release") will assert — which
`hooks/herdr-pin.json` does. #609 does not implement the doctor.

**Drift guard.** `HERDR_VERSION_FLOOR = (0, 7, 5)` already ships at
`phase_executor/src/phase_executor/herdr_backend.py:84` (from #633, "qualified live, 40/40
PID-identity reps"). Two independent pins for one fact is exactly the mirrored-constant hazard the
repo operating manual names, so a test asserts `herdr-pin.json`'s version equals
`HERDR_VERSION_FLOOR`. Neither pin can move without the other.

## 3. Reproducible checksummed install (AC1, AC5)

**`curl | sh` is never used in this workspace** (plan §1.6 states this explicitly). The sanctioned
path is the **pinned release binary verified against its published digest** — the decision plan
§1.6 left to this epic.

```bash
#!/usr/bin/env bash
set -euo pipefail   # load-bearing: without it a failed fetch falls through to the compare

PLATFORM=linux-x86_64                  # the key in the pin's `assets` map
ASSET=herdr-$PLATFORM                  # the release asset's filename — NOT the same string
PIN=hooks/herdr-pin.json
WORK=$(mktemp -d)                      # never a fixed /tmp path: world-writable dir + predictable
trap 'rm -rf "$WORK"' EXIT             # name = symlink/pre-seed surface

# 1. Read the pin (never hardcode the version or hash)
read -r VER TAG REPO WANT < <(python3 - "$PIN" "$PLATFORM" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))["pin"]
print(p["version"], p["tag"], p["repo"], p["assets"][sys.argv[2]]["sha256"])
PY
)

# 2. Fetch the asset AND the upstream published digest for the same tag.
#    NOTE: `gh api --jq` takes exactly ONE argument, so --arg must go to a real jq.
gh release download "$TAG" --repo "$REPO" --pattern "$ASSET" --dir "$WORK"
UPSTREAM=$(gh api "repos/$REPO/releases/tags/$TAG" \
  | jq -r --arg a "$ASSET" '.assets[] | select(.name==$a) | .digest' | sed 's/^sha256://')
GOT=$(sha256sum "$WORK/$ASSET" | cut -d' ' -f1)

# 3. Reject EMPTY values before comparing. Skipping this is the trap: if the pin were
#    unreadable and the API returned nothing, `[ "" = "" ]` passes and the guard reports
#    success having verified nothing.
for v in VER TAG REPO WANT UPSTREAM GOT; do
  [ -n "${!v}" ] || { echo "ABORT: $v is empty — verified nothing"; exit 1; }
done

# 4. Three-way agreement REQUIRED: pin == upstream == downloaded bytes.
[ "$WANT" = "$UPSTREAM" ] || { echo "ABORT: pin disagrees with upstream ($WANT vs $UPSTREAM)"; exit 1; }
[ "$WANT" = "$GOT" ]      || { echo "ABORT: downloaded bytes do not match the pin ($WANT vs $GOT)"; exit 1; }

# 5. Only now install
install -m 0755 "$WORK/$ASSET" ~/.local/bin/herdr
[ "$(herdr --version)" = "herdr $VER" ] || { echo "ABORT: installed binary reports the wrong version"; exit 1; }
echo "OK: herdr $VER installed, sha256 $GOT verified against pin and upstream"
```

The three-way check is the point: comparing the download against the pin alone would still install
a tampered artifact if the pin itself were edited, and comparing against upstream alone would drift
silently when upstream re-tags. Requiring pin == upstream == bytes catches both.

**Why the empty-value loop in step 3 exists** (a real defect found reviewing this procedure, not a
hypothetical): string equality in shell is happy to compare two empty strings. A cascade where the
pin is unreadable *and* the API call yields nothing would leave `WANT` and `UPSTREAM` both empty,
`[ "" = "" ]` would pass, and the script would print no ABORT — reporting a successful verification
that checked nothing. `set -euo pipefail` plus the explicit non-empty assertions close it. The
version assertion in step 5 is the same instinct applied at the other end: an install that silently
put the wrong binary in place should fail loudly, not print "OK".

**To verify an already-installed binary** (what this vet did — no download, no mutation):

```bash
sha256sum ~/.local/bin/herdr | cut -d' ' -f1
gh api repos/ogulcancelik/herdr/releases/tags/v0.7.5 \
  --jq '.assets[] | select(.name=="herdr-linux-x86_64") | .digest'
# the two must agree modulo the "sha256:" prefix
```

## 4. Config baseline (AC2, AC3)

Every key below was read from **`herdr --default-config`** — the authoritative source — not
assumed. Defaults are noted so each change is legible as a deliberate delta.

```toml
[update]
channel = "stable"        # default "stable" on Linux/macOS; pinned EXPLICITLY so a
                          # future default change cannot move us onto preview builds
version_check = false     # default true — no background version phone-home on a pinned install
manifest_check = false    # default true — AC2. See the rationale below; this is the
                          # supply-chain hole that a version pin alone does NOT close.

[ui]
mouse_capture = true      # default true — kept explicit; herdr owns mouse UI
mouse_scroll_lines = 3    # default 3 — kept explicit so scroll feel is pinned, not inherited

[ui.sound]
enabled = true            # default true — audible agent state changes in background workspaces
```

**Why `manifest_check = false` is the load-bearing line.** Its default is `true`, and per
`herdr --default-config` it makes herdr *"Check herdr.dev for remote agent-detection manifest
updates in the background."* That means **agent-detection behavior can change under a pinned
binary with no version bump** — a pin on the binary does not pin the manifest. Turning it off, and
pinning any manifest locally in the config dir instead, is what AC2 is actually protecting. The
same reasoning retires `version_check`: a pinned install has no use for a background phone-home.

### Applying the baseline — no restart, no lost panes

`herdr server reload-config` reloads `config.toml` in the **running** server (`herdr --help`:
"Reload config.toml in the running server"). Applying this baseline therefore does **not** require
bouncing the server and does **not** endanger live panes. The earlier assumption that a restart was
needed was wrong, and this is the correction.

```bash
cp ~/.config/herdr/config.toml ~/.config/herdr/config.toml.bak-$(date -u +%Y%m%dT%H%M%SZ)  # undo path
# merge the blocks above into ~/.config/herdr/config.toml (it already has
# [theme]/[ui.toast]/[ui]/[keys]; ADD [update] and [ui.sound], and add the two
# mouse_* keys to the EXISTING [ui] table rather than creating a second one)
herdr config check          # validate before loading
herdr server reload-config  # apply to the running server
```

**Not yet applied to this host.** Under epic-run D-5 the live apply is the owner's to trigger at a
moment of their choosing; this document is the reviewed target and the procedure. The undo is the
timestamped `.bak` copy plus a second `reload-config`.

Current live state, for the diff: `~/.config/herdr/config.toml` is 239 bytes holding `onboarding`,
`[theme]`, `[ui.toast]`, `[ui]`, `[keys]` — **no `[update]` section and no `[ui.sound]` section at
all**, so `manifest_check` and `version_check` are both at their insecure-for-us defaults today.

## 5. Residual risk

| Risk | Severity | Mitigation |
|---|---|---|
| Upstream re-tags `v0.7.5` with different bytes | Medium | The pin stores the digest, so the three-way check fails loudly instead of installing silently |
| No hard semver/back-compat guarantee upstream (plan §1.6) | Medium | `HERDR_VERSION_FLOOR` + this pin + #390's future `api schema` digest check (protocol 17, schema_version 1, recorded in the pin file) |
| Remote agent-detection manifest changes behavior under a pinned binary | Medium | `manifest_check = false` (§4) — the reason AC2 exists |
| Config baseline not yet applied to the live host | Low | Documented procedure + `reload-config`; owner-triggered per D-5 |
| Weekly-cadence upstream drifts past the pin | Low | Pin bump is a deliberate PR that re-runs §3's verification |

## 6. The claim most worth re-checking

**`herdr server reload-config` actually reloads `[update]` and `[ui.sound]` without a restart.**
It is documented in `herdr --help` and I have **not executed it** — doing so would mutate the live
shared server, which D-5 put out of bounds. Everything else in this document was proven by running
the exact command shown. If reload-config turns out to be partial (e.g. reloads UI keys but not
`[update]`), the apply step in §4 needs a restart during a quiet window instead, and #613's runbook
is where that gets settled.

platform_apis:
- api: gh api repos/{owner}/{repo}/releases/tags/{tag} → asset .digest
  feasibility: verified via spike — ran `gh api repos/ogulcancelik/herdr/releases/tags/v0.7.5`; asset `herdr-linux-x86_64` returned `digest: sha256:3dc83288073e4c2d3c679a30e7be97bcca9141c6fd17dbbb9219142e95c59253`, size 21315048 (this exact invocation, not a proxy)
  failure: fail-loud
- api: sha256sum <path>
  feasibility: verified via spike — ran `sha256sum ~/.local/bin/herdr` → `3dc83288073e4c2d3c679a30e7be97bcca9141c6fd17dbbb9219142e95c59253`, matching the upstream digest exactly
  failure: fail-loud
- api: herdr --version
  feasibility: verified via spike — ran `herdr --version` → exact stdout `herdr 0.7.5`; this is the parse target #390 check 8 will assert, so its exact shape is load-bearing
  failure: fail-loud
- api: herdr --default-config
  feasibility: verified via spike — ran `herdr --default-config`; every key name and default in §4 (`[update] channel/version_check/manifest_check`, `[ui] mouse_capture/mouse_scroll_lines`, `[ui.sound] enabled`) was read from its output rather than assumed
  failure: fail-loud
- api: herdr api schema --json
  feasibility: verified via spike — ran `herdr api schema --json` → `{"protocol": 17, "schema_version": 1, ...}`; recorded in the pin file for #390's future check 10, which is #390's to assert, not #609's
  failure: fail-loud

`herdr server reload-config` is deliberately absent from the declaration above: #609 ships no code
that invokes it. It is a documented manual step for the owner's apply moment, and §6 names it as
this document's one unproven claim rather than dressing it as verified.
