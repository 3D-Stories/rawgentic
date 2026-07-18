#!/usr/bin/env python3
"""Config->capabilities derivation for rawgentic workflow skills.

Every workflow skill (implement-feature, fix-bug, create-issue, ... — 11 in all)
derives a `capabilities` object from the project's `.rawgentic.json` at startup
and branches on it (TDD vs implement-verify, skip-CI-gate, deploy method, etc.).
That derivation used to be an IDENTICAL prose block duplicated across all 11
SKILL.md files plus the docs table — three-plus copies that could silently drift,
and a hand-applied mapping where a malformed config could masquerade as a
feature-less project (skipping TDD on a project that actually has tests).

This module encodes the derivation once, as a tested fail-closed function exposed
as a `derive` CLI. The orchestrator still resolves the active project (Level
1/2/3 fallback — conversation/registry/workspace) in prose, since that is
environmental; the CLI takes the resolved config path and owns load + validate +
derive. The contract is all-or-nothing: it either emits a fully-valid
capabilities object or exits non-zero — never a best-effort partial.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import PurePosixPath


# Canonical capability field set. The docs table (docs/config-reference.md) and
# the SKILL.md drift guard check against this list, and the test asserts it
# equals exactly the keys derive_capabilities() produces — so the three places
# that describe capabilities can never drift apart again.
CAPABILITY_FIELDS = (
    "repo",
    "default_branch",
    "project_type",
    "has_tests",
    "test_commands",
    "has_ci",
    "ci_quarantined",
    "ci_quarantine_reason",
    "ci_quarantined_since",
    "has_deploy",
    "deploy_method",
    "has_database",
    "has_docker",
    "migration_dir",
    "phase_executor_table",
)


class CapabilitiesError(ValueError):
    """A config that cannot be safely derived into capabilities.

    Raised for a missing/corrupt config, a wrong-typed essential field, or a
    present-but-malformed optional section. Distinct from an ABSENT optional
    section, which legitimately yields the documented default (false/[]/null).
    """


# Sentinel for "key absent" so it can be distinguished from "key present with a
# null value." The fail-closed rule is: an ABSENT key yields the documented
# default, but a PRESENT-but-invalid value (null OR wrong type) is an error —
# setup omits empty sections rather than writing null, so a present null signals
# a hand-edited/corrupt config that must not silently degrade (e.g. a null
# `frameworks` silently skipping TDD on a project that has tests).
_MISSING = object()

# config.deploy.method is a closed enum (docs/config-reference.md). An unknown
# method must fail closed, not yield has_deploy=true for a path that can't run.
_DEPLOY_METHODS = ("compose", "ssh", "script", "manual")


def _require_nonempty_str(value, field: str) -> str:
    """A required string value. None (absent or null), wrong type, or empty ->
    error. Used for both essential fields and present optional sub-values."""
    if not isinstance(value, str) or not value.strip():
        raise CapabilitiesError(
            f"{field} must be a non-empty string (got {value!r}). Run /rawgentic:setup."
        )
    return value


def _optional_section(config: dict, key: str):
    """A top-level optional section: ABSENT -> _MISSING (caller defaults);
    present-but-null or present-but-not-an-object -> error."""
    val = config.get(key, _MISSING)
    if val is _MISSING:
        return _MISSING
    if not isinstance(val, dict):
        raise CapabilitiesError(
            f"config.{key} must be an object when present (got {val!r}). "
            f"Run /rawgentic:setup."
        )
    return val


def derive_capabilities(config) -> dict:
    """Derive the capabilities object from a parsed `.rawgentic.json` config.

    Fail-closed: essential fields (repo.fullName, repo.defaultBranch,
    project.type — the two required config sections) must be present and
    well-typed or this raises; a present-but-malformed optional section raises
    rather than silently degrading to false. An ABSENT optional section yields
    its documented default.
    """
    if not isinstance(config, dict):
        raise CapabilitiesError(
            "config must be a JSON object (expected an object at top level). "
            "Run /rawgentic:setup."
        )

    # --- Essential: repo + project (the two required config sections) ---
    repo = config.get("repo")
    if not isinstance(repo, dict):
        raise CapabilitiesError("config.repo must be an object. Run /rawgentic:setup.")
    project = config.get("project")
    if not isinstance(project, dict):
        raise CapabilitiesError("config.project must be an object. Run /rawgentic:setup.")

    caps = {
        "repo": _require_nonempty_str(repo.get("fullName"), "config.repo.fullName"),
        "default_branch": _require_nonempty_str(
            repo.get("defaultBranch"), "config.repo.defaultBranch"),
        "project_type": _require_nonempty_str(
            project.get("type"), "config.project.type"),
    }

    # --- testing -> has_tests, test_commands ---
    testing = _optional_section(config, "testing")
    if testing is _MISSING:
        caps["has_tests"] = False
        caps["test_commands"] = []
    else:
        frameworks = testing.get("frameworks", _MISSING)
        if frameworks is _MISSING:
            caps["has_tests"] = False
            caps["test_commands"] = []
        elif not isinstance(frameworks, list):  # null or wrong type
            raise CapabilitiesError(
                "config.testing.frameworks must be an array when present. "
                "Run /rawgentic:setup.")
        else:
            commands = []
            for i, fw in enumerate(frameworks):
                if not isinstance(fw, dict):
                    raise CapabilitiesError(
                        f"config.testing.frameworks[{i}] must be an object. "
                        f"Run /rawgentic:setup.")
                # has_tests=true must imply a runnable command for every framework;
                # an empty test_commands beside has_tests=true looks usable but
                # breaks the TDD/verify step.
                commands.append(_require_nonempty_str(
                    fw.get("command"), f"config.testing.frameworks[{i}].command"))
            caps["has_tests"] = len(frameworks) > 0
            caps["test_commands"] = commands

    # --- ci -> has_ci (keys on config.ci.provider) ---
    # Quarantine (#137) is a HUMAN declaration read from config, never inferred:
    # a chronically-red suite and a genuinely broken diff are mechanically
    # indistinguishable, so the workflow must not decide this itself.
    caps["ci_quarantined"] = False
    caps["ci_quarantine_reason"] = None
    caps["ci_quarantined_since"] = None
    ci = _optional_section(config, "ci")
    if ci is _MISSING:
        caps["has_ci"] = False
    else:
        provider = ci.get("provider", _MISSING)
        if provider is _MISSING:
            caps["has_ci"] = False  # ci section exists but no provider key -> false
        else:
            _require_nonempty_str(provider, "config.ci.provider")  # null/wrong/empty -> error
            caps["has_ci"] = True
        status = ci.get("status", _MISSING)
        if status is not _MISSING:
            if status not in ("active", "quarantined"):
                raise CapabilitiesError(
                    f"config.ci.status must be 'active' or 'quarantined' "
                    f"(got {status!r}). Run /rawgentic:setup.")
            if status == "quarantined":
                if not caps["has_ci"]:
                    raise CapabilitiesError(
                        "config.ci.status is 'quarantined' but no config.ci.provider "
                        "is set — cannot quarantine CI that isn't declared. "
                        "Run /rawgentic:setup.")
                # A quarantine claim MUST carry its justification.
                caps["ci_quarantine_reason"] = _require_nonempty_str(
                    ci.get("quarantineReason", _MISSING), "config.ci.quarantineReason")
                caps["ci_quarantined"] = True
                since = ci.get("quarantinedSince", _MISSING)
                if since is not _MISSING:
                    since = _require_nonempty_str(since, "config.ci.quarantinedSince")
                    # Must be an ISO YYYY-MM-DD date so the Step-1 staleness nag can
                    # compare it deterministically; a nonempty-but-malformed value
                    # would make the nag misfire or silently skip.
                    try:
                        datetime.strptime(since, "%Y-%m-%d")
                    except ValueError:
                        raise CapabilitiesError(
                            f"config.ci.quarantinedSince must be an ISO date "
                            f"(YYYY-MM-DD), got {since!r}. Run /rawgentic:setup.")
                    caps["ci_quarantined_since"] = since

    # --- deploy -> has_deploy, deploy_method (method=="manual" is the carve-out) ---
    deploy = _optional_section(config, "deploy")
    if deploy is _MISSING:
        caps["has_deploy"] = False
        caps["deploy_method"] = None
    else:
        method = deploy.get("method", _MISSING)
        if method is _MISSING:
            caps["deploy_method"] = None
            caps["has_deploy"] = False
        else:
            _require_nonempty_str(method, "config.deploy.method")  # null/wrong/empty -> error
            if method not in _DEPLOY_METHODS:
                raise CapabilitiesError(
                    f"config.deploy.method must be one of {list(_DEPLOY_METHODS)} "
                    f"(got {method!r}). Run /rawgentic:setup.")
            caps["deploy_method"] = method
            caps["has_deploy"] = method != "manual"

    # --- database -> has_database, migration_dir ---
    database = _optional_section(config, "database")
    if database is _MISSING:
        caps["has_database"] = False
        caps["migration_dir"] = None
    else:
        db_type = database.get("type", _MISSING)
        if db_type is _MISSING:
            caps["has_database"] = False
        else:
            _require_nonempty_str(db_type, "config.database.type")  # null/wrong/empty -> error
            caps["has_database"] = True
        mig = database.get("migrationsDir", _MISSING)
        if mig is _MISSING:
            caps["migration_dir"] = None
        else:
            caps["migration_dir"] = _require_nonempty_str(
                mig, "config.database.migrationsDir")  # null/wrong/empty -> error

    # --- phaseExecutorTable -> phase_executor_table (#445) ---
    # Versioned descriptor naming the project-owned seat-table FILE (a complete replacement
    # for the phase_executor package default, never a merge overlay). None ONLY when the
    # section is ABSENT; a present-but-invalid section raises (fail-closed) — resolution must
    # never silently fall back to the package default on a malformed override (the
    # false-cutover class executor_routing_lib.parse_executor_routing refuses). Shape-only
    # here: existence/content validation is load-time (executor_routing_lib.resolve_table).
    pet = _optional_section(config, "phaseExecutorTable")  # raises if present-non-object
    if pet is _MISSING:
        caps["phase_executor_table"] = None
    else:
        version = pet.get("version", _MISSING)
        # Bool is an int subclass — reject it explicitly (True == 1 would sneak through).
        if version is _MISSING or not isinstance(version, int) or isinstance(version, bool) or version != 1:
            raise CapabilitiesError(
                f"config.phaseExecutorTable.version must be 1 "
                f"(got {None if version is _MISSING else version!r}). Run /rawgentic:setup.")
        file_val = _require_nonempty_str(
            pet.get("file", _MISSING), "config.phaseExecutorTable.file")
        p = PurePosixPath(file_val)
        if p.is_absolute() or ".." in p.parts:
            raise CapabilitiesError(
                f"config.phaseExecutorTable.file must be a project-relative path with no "
                f"'..' traversal (got {file_val!r}). Run /rawgentic:setup.")
        if any(ord(c) < 0x20 for c in file_val) or "\\" in file_val:
            # NUL/control chars make pathlib/os raise ValueError downstream (escaping the
            # uniform exit-2 mapping); backslashes are path separators on no supported
            # platform here and only invite confusion. Same class as _UNSAFE_COMPONENT.
            raise CapabilitiesError(
                f"config.phaseExecutorTable.file contains control or backslash characters "
                f"(got {file_val!r}). Run /rawgentic:setup.")
        caps["phase_executor_table"] = file_val

    # --- infrastructure.docker -> has_docker (must null-guard the docker object:
    #     infrastructure can legitimately exist with only `hosts` and no docker) ---
    infra = _optional_section(config, "infrastructure")
    if infra is _MISSING:
        caps["has_docker"] = False
    else:
        docker = infra.get("docker", _MISSING)
        if docker is _MISSING:
            caps["has_docker"] = False
        elif not isinstance(docker, dict):  # null or wrong type
            raise CapabilitiesError(
                "config.infrastructure.docker must be an object when present. "
                "Run /rawgentic:setup.")
        else:
            compose = docker.get("composeFiles", _MISSING)
            if compose is _MISSING:
                caps["has_docker"] = False
            elif not isinstance(compose, list):  # null or wrong type
                raise CapabilitiesError(
                    "config.infrastructure.docker.composeFiles must be an array "
                    "when present. Run /rawgentic:setup.")
            else:
                caps["has_docker"] = len(compose) > 0

    return caps


def _quarantine_fields(config) -> tuple:
    """Extract the CI-quarantine-relevant fields (status, reason, since) from a
    config, defensively (never raises — used to compare base vs head)."""
    ci = config.get("ci") if isinstance(config, dict) else None
    if not isinstance(ci, dict):
        return (None, None, None)
    return (ci.get("status"), ci.get("quarantineReason"), ci.get("quarantinedSince"))


def ci_quarantine_change(base_config, head_config) -> str | None:
    """Return a reason string iff `head_config` INTRODUCES or ALTERS a CI
    quarantine relative to `base_config`, else None (#137 trust guard).

    A quarantine disables the CI gate, so a PR must not be able to quarantine
    CI in its own diff and thereby bypass its own failing CI. The caller loads
    `base_config` from the trusted default branch and `head_config` from the
    working tree; if the quarantine was introduced or changed on the branch,
    the CI gate must stay ACTIVE for this run (pending explicit approval).
    Removing a quarantine (head not quarantined) only makes CI stricter → safe → None.
    """
    head = _quarantine_fields(head_config)
    if head[0] != "quarantined":
        return None  # head does not claim quarantine — no bypass to guard
    base = _quarantine_fields(base_config)
    if base[0] != "quarantined":
        return "CI quarantine is introduced on this branch (not present in the base config)"
    if base != head:
        return "CI quarantine fields differ from the base config (altered on this branch)"
    return None


def probe_parallelism(repo_root: str) -> str:
    """Probe whether the project supports git-worktree isolation (#136).

    Returns 'worktree' when `repo_root` is a real git work tree AND a throwaway
    worktree can be created (then removed), else 'serial-only'. So an orchestrator
    learns up front instead of attempt-then-fail on an Agent-tool "not in a git
    repository" error. Never raises — any failure degrades to 'serial-only'.

    Steady-state non-mutating: the test worktree is created under the SYSTEM temp
    dir and force-removed + pruned in a finally, so the repo is left unchanged.
    (The parallel EXECUTION layer itself is #85; this only fronts it.)
    """
    def _git(args, cwd=repo_root):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)

    try:
        r = _git(["rev-parse", "--is-inside-work-tree"])
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return "serial-only"
    if r.returncode != 0 or r.stdout.strip() != "true":
        return "serial-only"

    parent = None
    wt = None
    try:
        parent = tempfile.mkdtemp(prefix="rawgentic-wt-probe-")
        wt = os.path.join(parent, "wt")  # git worktree add requires a non-existent path
        add = _git(["worktree", "add", "--detach", wt, "HEAD"])
        return "worktree" if add.returncode == 0 else "serial-only"
    except (OSError, subprocess.SubprocessError):
        return "serial-only"
    finally:
        # Cleanup must NEVER raise (that would override the return and crash the
        # probe despite the "never raises" contract). `git worktree remove` both
        # unregisters and deletes; rmtree clears the temp parent; prune then
        # sweeps a registration whose path is already gone (covers a failed
        # remove where the dir was nonetheless deleted).
        try:
            if wt is not None:
                _git(["worktree", "remove", "--force", wt])
            if parent is not None:
                shutil.rmtree(parent, ignore_errors=True)
            _git(["worktree", "prune"])
        except (OSError, subprocess.SubprocessError):
            pass


def load_config(path: str) -> dict:
    """Read + parse + shape-check a `.rawgentic.json`. Fail-closed: a missing or
    corrupt file (or a valid-JSON-but-not-an-object file) raises rather than
    returning something a caller could mistake for an empty config. The read and
    json.loads are wrapped so a partial/truncated file can't fall through to
    per-field access downstream."""
    try:
        with open(path) as f:
            raw = f.read()
    except FileNotFoundError:
        raise CapabilitiesError(
            f"Active project config not found at {path}. Run /rawgentic:setup.")
    except OSError as exc:
        raise CapabilitiesError(f"Could not read config at {path}: {exc}")
    try:
        config = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        raise CapabilitiesError(
            f"Project config at {path} is corrupted (invalid JSON). "
            f"Run /rawgentic:setup to regenerate.")
    if not isinstance(config, dict):
        raise CapabilitiesError(
            f"Project config at {path} must be a JSON object. Run /rawgentic:setup.")
    return config


def main(argv=None) -> int:
    """CLI entry point.

    Subcommand:
      derive  load + validate the config at --config and print
              {"config": <parsed>, "capabilities": <derived>} as JSON

    Exit codes:
      0  success — both objects printed to stdout (a version mismatch is WARNED
         on stderr but does not stop, matching the documented warn-and-continue
         behavior and keeping in-flight projects on an older schema unblocked)
      1  fail-closed: missing/corrupt config, or a config that derives invalidly
      2  argparse usage error
    """
    parser = argparse.ArgumentParser(prog="capabilities_lib")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser(
        "derive",
        help="load + validate config, emit {config, capabilities} JSON")
    p.add_argument("--config", required=True,
                   help="path to the resolved <activeProject.path>/.rawgentic.json")
    pp = sub.add_parser(
        "probe-parallelism",
        help="probe git-worktree isolation availability (#136); prints "
             "'worktree' or 'serial-only'")
    pp.add_argument("--repo-root", required=True,
                    help="the project's git repo root to probe")

    args = parser.parse_args(argv)

    if args.cmd == "probe-parallelism":
        print(probe_parallelism(args.repo_root))
        return 0

    if args.cmd == "derive":
        try:
            config = load_config(args.config)
        except CapabilitiesError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        # Version handling stays warn-and-continue (faithful to config-reference
        # docs + migration-path safety): a missing/newer version is surfaced but
        # does not block, unlike a corrupt config which fails closed above.
        version = config.get("version")
        if version is None:
            print("warning: config.version is missing; assuming version 1",
                  file=sys.stderr)
        elif version != 1:
            print(f"warning: config.version is {version!r} (expected 1); "
                  f"proceeding, but fields may have moved", file=sys.stderr)
        try:
            caps = derive_capabilities(config)
        except CapabilitiesError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps({"config": config, "capabilities": caps},
                         separators=(",", ":")))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
