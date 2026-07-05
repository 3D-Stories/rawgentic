#!/usr/bin/env python3
"""Reliable use of external skills/commands (#194).

Two primitives a rawgentic skill needs before it can *depend* on something that
lives outside this repo:

1. ``probe(kind, name)`` — verify a named skill / command / plugin actually
   exists in the Claude Code plugin cache before a gate relies on it. A missing
   one is a **visible skip** (the caller reports it), never a silent pass — this
   is the #162 trap (a gate wired to ``/code-review`` with the plugin uninstalled
   and nothing checking). It also reports whether the source marketplace is
   trusted, so the caller can refuse to *execute* untrusted prompt content.

2. ``vendor_copy(src, name, state_dir, marketplace)`` — keep a durable local copy
   of a vendored external command in a **gitignored** state dir (never committed:
   an external command file is third-party prompt content, so committing it would
   redistribute someone else's content). The copy is refreshed when the source
   hash changes, retained with a ``vanished`` alert if the source disappears, and
   guarded by a trust-gate so we only ever vendor from known marketplaces.

Pure-ish: filesystem reads/writes only, no network. The cache root and state dir
are parameters so the whole module is testable against a tmp tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

# Marketplaces rawgentic legitimately vendors from / relies on. Executing an
# external command file runs its author's prompt content, so this is an
# allow-list, not a probe of whatever happens to be in the cache (which also
# holds transient `temp_git_*` checkouts). Extend per-machine via
# RAWGENTIC_TRUSTED_MARKETPLACES (comma-separated) rather than editing code.
_TRUSTED_MARKETPLACES = frozenset({
    "rawgentic",
    "rawgentic-memorypalace",
    "claude-plugins-official",
    "context-engineering-kit",
    "openai-codex",
    "anthropic-agent-skills",
})

# Where a probe looks by default; overridable (and always overridden in tests).
_DEFAULT_CACHE_ROOT = Path.home() / ".claude" / "plugins" / "cache"

_KIND_SUBDIR = {"skill": "skills", "command": "commands", "agent": "agents"}


class ExternalRefError(Exception):
    """Base error for the external-ref primitives."""


class UntrustedSourceError(ExternalRefError):
    """Refused to vendor a command from a marketplace not on the trust-list."""


def _env_trusted() -> set[str]:
    raw = os.environ.get("RAWGENTIC_TRUSTED_MARKETPLACES", "")
    return {m.strip() for m in raw.split(",") if m.strip()}


def is_trusted(marketplace: str) -> bool:
    """True when ``marketplace`` is a known-trusted source (built-in set or the
    RAWGENTIC_TRUSTED_MARKETPLACES env extension)."""
    return marketplace in _TRUSTED_MARKETPLACES or marketplace in _env_trusted()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def probe(kind: str, name: str, cache_root: str | Path | None = None) -> dict:
    """Look for a skill/command/agent named ``name`` in the plugin cache.

    Returns a dict: ``exists`` (bool), ``path`` (str|None), ``marketplace``,
    ``plugin``, ``version``, ``trusted`` (bool), ``reason`` (a human string — the
    visible-skip message when ``exists`` is False). A command is a ``<name>.md``
    file; a skill/agent is a ``<name>/`` directory. When multiple versions match,
    the lexicographically greatest version dir wins (a stable, deterministic pick
    without importing a semver parser).
    """
    if kind not in _KIND_SUBDIR:
        raise ExternalRefError(
            f"unknown kind {kind!r} (expected one of {sorted(_KIND_SUBDIR)})")
    root = Path(cache_root) if cache_root is not None else _DEFAULT_CACHE_ROOT
    sub = _KIND_SUBDIR[kind]
    target = f"{name}.md" if kind == "command" else name

    if not root.is_dir():
        return {"exists": False, "path": None, "marketplace": None,
                "plugin": None, "version": None, "trusted": False,
                "reason": f"plugin cache root not found: {root}"}

    matches: list[tuple[str, str, str, Path]] = []  # (marketplace, plugin, version, path)
    for mp in sorted(p for p in root.iterdir() if p.is_dir()):
        for plugin in sorted(p for p in mp.iterdir() if p.is_dir()):
            for version in sorted((p for p in plugin.iterdir() if p.is_dir()),
                                  reverse=True):
                cand = version / sub / target
                exists = cand.is_file() if kind == "command" else cand.is_dir()
                if exists:
                    matches.append((mp.name, plugin.name, version.name, cand))

    if not matches:
        return {"exists": False, "path": None, "marketplace": None,
                "plugin": None, "version": None, "trusted": False,
                "reason": (f"no {kind} named {name!r} found in the plugin cache — "
                           f"degrade to a VISIBLE skip, do not treat as a pass")}

    # Prefer a trusted match if any exists; otherwise the first found.
    trusted_matches = [m for m in matches if is_trusted(m[0])]
    mp, plugin, version, path = (trusted_matches or matches)[0]
    return {"exists": True, "path": str(path), "marketplace": mp,
            "plugin": plugin, "version": version, "trusted": is_trusted(mp),
            "reason": "found"}


def _read_manifest(state: Path) -> dict:
    mf = state / "manifest.json"
    if not mf.is_file():
        return {}
    try:
        data = json.loads(mf.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError, OSError):
        return {}


def _write_manifest(state: Path, manifest: dict) -> None:
    mf = state / "manifest.json"
    tmp = mf.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    tmp.replace(mf)


def vendor_copy(src: str | Path, name: str, state_dir: str | Path,
                marketplace: str, copied_at: str = "") -> dict:
    """Vendor ``src`` into ``state_dir`` under ``name`` (a durable, gitignored
    local copy), refreshing on source-hash change.

    Returns a dict with ``status`` in:
      - ``copied``     — first time, or the local copy was missing
      - ``unchanged``  — source hash matches the manifest; nothing done
      - ``refreshed``  — source hash changed; local copy replaced
      - ``vanished``   — source no longer exists; the stale copy is RETAINED and
                          the caller is alerted (a stale command beats none)

    Raises ``UntrustedSourceError`` if ``marketplace`` is not trusted — we never
    copy (let alone execute) prompt content from an unknown source. ``copied_at``
    is an optional caller-supplied timestamp (this module does no clock I/O so it
    stays deterministic/testable); omit it and the manifest simply records "".
    """
    if not is_trusted(marketplace):
        raise UntrustedSourceError(
            f"refusing to vendor {name!r} from untrusted marketplace "
            f"{marketplace!r} — add it to RAWGENTIC_TRUSTED_MARKETPLACES only if "
            f"you trust its command content")

    src_path = Path(src)
    state = Path(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    dest = state / f"{name}.md"
    manifest = _read_manifest(state)
    prev = manifest.get(name, {})

    if not src_path.is_file():
        # Source gone. Keep whatever local copy exists; alert loudly.
        entry = dict(prev)
        entry["vanished"] = True
        manifest[name] = entry
        _write_manifest(state, manifest)
        return {"status": "vanished", "name": name,
                "have_local_copy": dest.is_file(),
                "reason": f"source {src_path} no longer exists; retained the "
                          f"last local copy" if dest.is_file()
                          else f"source {src_path} gone and no local copy exists"}

    sha = _sha256_file(src_path)
    if dest.is_file() and prev.get("sha") == sha and not prev.get("vanished"):
        return {"status": "unchanged", "name": name, "sha": sha}

    status = "refreshed" if (dest.is_file() and prev.get("sha")) else "copied"
    shutil.copyfile(src_path, dest)
    manifest[name] = {"sha": sha, "source_path": str(src_path),
                      "marketplace": marketplace, "copied_at": copied_at}
    _write_manifest(state, manifest)
    return {"status": status, "name": name, "sha": sha, "path": str(dest)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="external_ref_lib")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_probe = sub.add_parser("probe", help="check a skill/command/agent exists")
    p_probe.add_argument("--kind", required=True, choices=sorted(_KIND_SUBDIR))
    p_probe.add_argument("--name", required=True)
    p_probe.add_argument("--cache-root", default=None)

    p_vendor = sub.add_parser("vendor", help="durable-copy-with-refresh")
    p_vendor.add_argument("--src", required=True)
    p_vendor.add_argument("--name", required=True)
    p_vendor.add_argument("--state-dir", required=True)
    p_vendor.add_argument("--marketplace", required=True)
    p_vendor.add_argument("--copied-at", default="")

    p_trust = sub.add_parser("is-trusted", help="exit 0 if the marketplace is trusted")
    p_trust.add_argument("--marketplace", required=True)

    args = parser.parse_args(argv)

    if args.cmd == "probe":
        r = probe(args.kind, args.name, cache_root=args.cache_root)
        print(json.dumps(r))
        # exit 0 whether or not it exists — the JSON carries the answer; a probe
        # is not itself a failure (the CALLER decides whether a miss is fatal).
        return 0
    if args.cmd == "vendor":
        try:
            r = vendor_copy(args.src, args.name, args.state_dir,
                            args.marketplace, copied_at=args.copied_at)
        except UntrustedSourceError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(r))
        # A vanished source is surfaced as non-zero so a caller script notices.
        return 2 if r["status"] == "vanished" else 0
    if args.cmd == "is-trusted":
        return 0 if is_trusted(args.marketplace) else 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
