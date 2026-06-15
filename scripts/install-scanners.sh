#!/usr/bin/env bash
# Install the security scanners that hooks/security_scan.py (WF2 Step 11.5 +
# WF9) shells out to. Idempotent and best-effort: a tool already on PATH is left
# alone, and a tool that can't be auto-installed is reported (never fatal) so the
# scanner just degrades to a visible skip rather than the install blocking work.
#
# Tools: gitleaks (secrets), semgrep (SAST), osv-scanner (dependency CVEs),
# trivy (IaC/Dockerfile, only needed for Docker projects), pip-audit (SCA
# fallback when osv-scanner is unavailable).
#
# Usage:
#   install-scanners.sh            install every missing scanner (best-effort)
#   install-scanners.sh --check    report presence only; exit 1 if any missing
#
# Install strategy per tool, in order of preference:
#   - Homebrew (macOS/Linux) if `brew` is present — handles os/arch/version
#   - pipx (Python tools: semgrep, pip-audit), else `pip install --user`
#   - GitHub release binary (Go tools: gitleaks, osv-scanner, trivy)
# No remote script is piped to a shell; binaries are downloaded to BIN_DIR.
set -u

BIN_DIR="${RAWGENTIC_SCANNER_BIN:-$HOME/.local/bin}"
TOOLS="gitleaks semgrep osv-scanner trivy pip-audit"

have() { command -v "$1" >/dev/null 2>&1; }

_os() { case "$(uname -s)" in Darwin) echo darwin ;; *) echo linux ;; esac; }
_arch() {
  case "$(uname -m)" in
    x86_64|amd64) echo amd64 ;;
    arm64|aarch64) echo arm64 ;;
    *) echo unknown ;;
  esac
}

# --- check mode ------------------------------------------------------------
if [ "${1:-}" = "--check" ]; then
  missing=0
  for t in $TOOLS; do
    if have "$t"; then echo "present: $t"; else echo "MISSING: $t"; missing=1; fi
  done
  exit "$missing"
fi

# Opt-out: installs are on by default (opt-OUT, never opt-in). A user who does
# not want rawgentic touching their toolchain sets this env var (or the
# workspace `installScanners: false`, which the session-start bootstrap honors).
if [ "${RAWGENTIC_SKIP_SCANNER_INSTALL:-}" = "1" ]; then
  echo "rawgentic: scanner install opted out (RAWGENTIC_SKIP_SCANNER_INSTALL=1) — skipping."
  echo "the WF2/WF9 scan will skip any scanner that isn't already installed."
  exit 0
fi

mkdir -p "$BIN_DIR" 2>/dev/null || true
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "note: $BIN_DIR is not on PATH — add it so the scanners are found" ;;
esac

# Fetch a GitHub release asset whose name matches a grep pattern, to BIN_DIR.
# $1=owner/repo  $2=asset-name grep pattern  $3=output binary name
# Handles a raw binary, a .tar.gz (extracts the binary), best-effort.
_gh_release_binary() {
  repo="$1"; pattern="$2"; out="$3"
  url=$(curl -fsSL "https://api.github.com/repos/$repo/releases/latest" 2>/dev/null \
    | python3 -c "import sys,json,re
try:
    d=json.load(sys.stdin)
except Exception:
    sys.exit(0)
pat=re.compile(r'''$pattern''')
for a in d.get('assets',[]):
    if pat.search(a.get('name','')):
        print(a['browser_download_url']); break" 2>/dev/null)
  [ -n "$url" ] || { echo "  could not resolve a release asset for $repo"; return 1; }
  tmp=$(mktemp -d)
  case "$url" in
    *.tar.gz|*.tgz)
      curl -fsSL "$url" -o "$tmp/a.tgz" 2>/dev/null \
        && tar -xzf "$tmp/a.tgz" -C "$tmp" 2>/dev/null \
        && bin=$(find "$tmp" -type f -name "$out" | head -1) \
        && [ -n "$bin" ] && install -m 0755 "$bin" "$BIN_DIR/$out" ;;
    *)
      curl -fsSL "$url" -o "$BIN_DIR/$out" 2>/dev/null && chmod +x "$BIN_DIR/$out" ;;
  esac
  rc=$?
  rm -rf "$tmp"
  return "$rc"
}

_install_python_tool() {  # $1 = pip/pipx package name
  if have pipx; then pipx install "$1" >/dev/null 2>&1 && return 0; fi
  if have pip3; then pip3 install --user "$1" >/dev/null 2>&1 && return 0; fi
  if have pip; then pip install --user "$1" >/dev/null 2>&1 && return 0; fi
  return 1
}

install_one() {
  tool="$1"
  if have "$tool"; then echo "present: $tool"; return 0; fi
  if have brew; then
    echo "installing $tool via brew..."
    brew install "$tool" >/dev/null 2>&1 && { echo "  installed $tool"; return 0; }
  fi
  os=$(_os); arch=$(_arch)
  case "$tool" in
    semgrep|pip-audit)
      echo "installing $tool via pipx/pip..."
      _install_python_tool "$tool" && { echo "  installed $tool"; return 0; } ;;
    osv-scanner)
      echo "installing osv-scanner (release binary, ${os}_${arch})..."
      # osv-scanner publishes a stable latest/download URL
      curl -fsSL -o "$BIN_DIR/osv-scanner" \
        "https://github.com/google/osv-scanner/releases/latest/download/osv-scanner_${os}_${arch}" 2>/dev/null \
        && chmod +x "$BIN_DIR/osv-scanner" && have osv-scanner \
        && { echo "  installed osv-scanner"; return 0; } ;;
    gitleaks)
      echo "installing gitleaks (release binary, ${os}_${arch})..."
      _gh_release_binary "gitleaks/gitleaks" "${os}_${arch}\\.tar\\.gz$" "gitleaks" \
        && { echo "  installed gitleaks"; return 0; } ;;
    trivy)
      echo "installing trivy (release binary, ${os}_${arch})..."
      tos=$(echo "$os" | sed 's/darwin/macOS/; s/linux/Linux/')
      tarch=$(echo "$arch" | sed 's/amd64/64bit/; s/arm64/ARM64/')
      _gh_release_binary "aquasecurity/trivy" "${tos}-${tarch}\\.tar\\.gz$" "trivy" \
        && { echo "  installed trivy"; return 0; } ;;
  esac
  echo "  could not auto-install $tool — install it manually (see docs/security-scan.md)"
  return 1
}

echo "rawgentic: ensuring security scanners are installed (target: $BIN_DIR)"
failed=""
for t in $TOOLS; do
  install_one "$t" || failed="$failed $t"
done

echo ""
if [ -n "$failed" ]; then
  echo "scanners not installed:$failed (the WF2/WF9 scan will skip these — see docs/security-scan.md)"
else
  echo "all scanners present."
fi
# Best-effort: never fail the caller (setup / bootstrap) on a missing scanner.
exit 0
