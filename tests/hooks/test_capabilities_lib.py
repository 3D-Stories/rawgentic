"""Tests for hooks/capabilities_lib.py — the config->capabilities derivation.

PR 4c extracts the "Build the capabilities object" derivation (previously an
identical prose block duplicated across all 11 workflow SKILL.md files + the
docs table) into one tested, fail-closed CLI. The orchestrator resolves the
active project (semantic, stays in prose); the CLI loads + validates the config
and derives the capabilities object so the mapping can no longer drift between
11 copies, and a malformed config can't masquerade as a feature-less project.
"""
import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

CAPS_CLI = HOOKS_DIR / "capabilities_lib.py"


def _run_cli(*args, timeout=10):
    import subprocess
    result = subprocess.run(
        ["python3", str(CAPS_CLI), *args],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout, result.stderr, result.returncode


# A minimal VALID config: the three required sections only (no optional ones).
def _base_config(**overrides):
    cfg = {
        "version": 1,
        "project": {"type": "application"},
        "repo": {"fullName": "owner/name", "defaultBranch": "main"},
    }
    cfg.update(overrides)
    return cfg


class TestDeriveEssentialFields:
    def test_repo_and_branch_and_type(self):
        from capabilities_lib import derive_capabilities
        caps = derive_capabilities(_base_config())
        assert caps["repo"] == "owner/name"
        assert caps["default_branch"] == "main"
        assert caps["project_type"] == "application"

    @pytest.mark.parametrize("mutate", [
        lambda c: c.pop("repo"),
        lambda c: c["repo"].pop("fullName"),
        lambda c: c["repo"].update({"fullName": ""}),
        lambda c: c["repo"].update({"fullName": 123}),
        lambda c: c["repo"].pop("defaultBranch"),
        lambda c: c["repo"].update({"defaultBranch": None}),
        lambda c: c.pop("project"),
        lambda c: c["project"].pop("type"),
        lambda c: c["project"].update({"type": "  "}),
    ])
    def test_essential_missing_or_wrong_type_raises(self, mutate):
        """repo/default_branch/project_type feed `gh`/`git` commands in every
        skill — a null/empty/wrong-typed value would break a command after work
        is done, so derive must fail closed BEFORE any side effects."""
        from capabilities_lib import derive_capabilities, CapabilitiesError
        cfg = _base_config()
        mutate(cfg)
        with pytest.raises(CapabilitiesError):
            derive_capabilities(cfg)

    def test_non_object_config_raises(self):
        from capabilities_lib import derive_capabilities, CapabilitiesError
        for bad in ([], "x", 5, None):
            with pytest.raises(CapabilitiesError):
                derive_capabilities(bad)


class TestDeriveOptionalAbsentDefaults:
    """An ABSENT optional section yields the documented degraded default — that
    is the whole point of the has_* flags (false = "feature not available")."""

    def test_all_optionals_absent(self):
        from capabilities_lib import derive_capabilities
        caps = derive_capabilities(_base_config())
        assert caps["has_tests"] is False
        assert caps["test_commands"] == []
        assert caps["has_ci"] is False
        assert caps["has_deploy"] is False
        assert caps["deploy_method"] is None
        assert caps["has_database"] is False
        assert caps["migration_dir"] is None
        assert caps["has_docker"] is False


class TestDeriveTesting:
    def test_frameworks_yield_has_tests_and_commands(self):
        from capabilities_lib import derive_capabilities
        cfg = _base_config(testing={"frameworks": [
            {"name": "pytest", "command": "pytest tests/ -v"},
            {"name": "vitest", "command": "npm test"},
        ]})
        caps = derive_capabilities(cfg)
        assert caps["has_tests"] is True
        assert caps["test_commands"] == ["pytest tests/ -v", "npm test"]

    def test_empty_frameworks_list_is_no_tests(self):
        from capabilities_lib import derive_capabilities
        caps = derive_capabilities(_base_config(testing={"frameworks": []}))
        assert caps["has_tests"] is False
        assert caps["test_commands"] == []

    def test_framework_missing_command_raises(self):
        """has_tests=true with an empty/partial test_commands looks usable but
        breaks the TDD/verify step — keep them consistent or fail closed."""
        from capabilities_lib import derive_capabilities, CapabilitiesError
        cfg = _base_config(testing={"frameworks": [{"name": "pytest"}]})
        with pytest.raises(CapabilitiesError):
            derive_capabilities(cfg)

    @pytest.mark.parametrize("bad_testing", [
        "pytest",                       # not an object
        None,                           # null section (setup omits, never nulls)
        {"frameworks": "pytest"},       # frameworks not a list
        {"frameworks": None},           # frameworks present-but-null (orig .length would crash)
        {"frameworks": ["pytest"]},     # element not an object
    ])
    def test_malformed_testing_raises_not_silently_false(self, bad_testing):
        """A present-but-garbled testing block (incl. an explicit null) must ERROR,
        not silently downgrade to has_tests=false (which would skip TDD on a
        project that has tests). Absent is different — that yields the default."""
        from capabilities_lib import derive_capabilities, CapabilitiesError
        with pytest.raises(CapabilitiesError):
            derive_capabilities(_base_config(testing=bad_testing))


class TestDeriveDeploy:
    def test_compose_method_has_deploy_true(self):
        from capabilities_lib import derive_capabilities
        caps = derive_capabilities(_base_config(deploy={"method": "compose"}))
        assert caps["has_deploy"] is True
        assert caps["deploy_method"] == "compose"

    def test_manual_method_carveout_has_deploy_false(self):
        """method=='manual' yields has_deploy=FALSE even though deploy exists."""
        from capabilities_lib import derive_capabilities
        caps = derive_capabilities(_base_config(deploy={"method": "manual"}))
        assert caps["has_deploy"] is False
        assert caps["deploy_method"] == "manual"

    @pytest.mark.parametrize("method", ["compose", "ssh", "script"])
    def test_each_real_method_has_deploy_true(self, method):
        from capabilities_lib import derive_capabilities
        caps = derive_capabilities(_base_config(deploy={"method": method}))
        assert caps["has_deploy"] is True
        assert caps["deploy_method"] == method

    def test_deploy_present_without_method(self):
        """method key ABSENT -> no deploy (the documented default), not an error."""
        from capabilities_lib import derive_capabilities
        caps = derive_capabilities(_base_config(deploy={"command": "x"}))
        assert caps["has_deploy"] is False
        assert caps["deploy_method"] is None

    @pytest.mark.parametrize("bad", [5, None, "", "typo", "Compose"])
    def test_deploy_method_invalid_raises(self, bad):
        """Present-but-invalid method (wrong type, null, empty, or outside the
        compose/ssh/script/manual enum) must fail closed — an unknown method
        would otherwise yield has_deploy=true for a deploy path that can't run."""
        from capabilities_lib import derive_capabilities, CapabilitiesError
        with pytest.raises(CapabilitiesError):
            derive_capabilities(_base_config(deploy={"method": bad}))


class TestDeriveCiDatabaseDocker:
    def test_ci_provider_present(self):
        from capabilities_lib import derive_capabilities
        assert derive_capabilities(_base_config(ci={"provider": "github-actions"}))["has_ci"] is True

    def test_ci_present_without_provider(self):
        from capabilities_lib import derive_capabilities
        assert derive_capabilities(_base_config(ci={"workflowDir": ".github"}))["has_ci"] is False

    def test_database_type_and_migration_dir(self):
        from capabilities_lib import derive_capabilities
        caps = derive_capabilities(_base_config(database={"type": "postgres", "migrationsDir": "sql"}))
        assert caps["has_database"] is True
        assert caps["migration_dir"] == "sql"

    def test_database_without_migration_dir(self):
        from capabilities_lib import derive_capabilities
        caps = derive_capabilities(_base_config(database={"type": "postgres"}))
        assert caps["has_database"] is True
        assert caps["migration_dir"] is None

    def test_docker_compose_files_present(self):
        from capabilities_lib import derive_capabilities
        cfg = _base_config(infrastructure={"docker": {"composeFiles": [{"name": "app", "path": "docker-compose.yml"}]}})
        assert derive_capabilities(cfg)["has_docker"] is True

    def test_infrastructure_without_docker_is_false_not_crash(self):
        """The real transcribe case: infrastructure.hosts present, no docker
        sub-object. Must null-guard infrastructure.docker, not throw."""
        from capabilities_lib import derive_capabilities
        cfg = _base_config(infrastructure={"hosts": [{"name": "vm1"}]})
        assert derive_capabilities(cfg)["has_docker"] is False

    def test_docker_empty_compose_files_is_false(self):
        from capabilities_lib import derive_capabilities
        cfg = _base_config(infrastructure={"docker": {"composeFiles": []}})
        assert derive_capabilities(cfg)["has_docker"] is False

    @pytest.mark.parametrize("cfg_kwargs", [
        {"ci": "github"},                                       # ci not an object
        {"ci": None},                                           # ci null section
        {"ci": {"provider": 1}},                                # provider wrong type
        {"ci": {"provider": None}},                             # provider present-but-null
        {"database": "postgres"},                               # database not an object
        {"database": {"type": 1}},                              # type wrong type
        {"database": {"type": None}},                           # type present-but-null
        {"database": {"type": "pg", "migrationsDir": 5}},       # migrationsDir wrong type
        {"infrastructure": "hosts"},                            # infra not an object
        {"infrastructure": {"docker": "compose.yml"}},          # docker not an object
        {"infrastructure": {"docker": None}},                   # docker present-but-null
        {"infrastructure": {"docker": {"composeFiles": "x"}}},  # composeFiles not a list
        {"infrastructure": {"docker": {"composeFiles": None}}}, # composeFiles present-but-null
    ])
    def test_malformed_optional_sections_raise(self, cfg_kwargs):
        """Present-but-invalid (null or wrong-type) -> error; never a silent
        default. Only an ABSENT key yields the documented default."""
        from capabilities_lib import derive_capabilities, CapabilitiesError
        with pytest.raises(CapabilitiesError):
            derive_capabilities(_base_config(**cfg_kwargs))


class TestCanonicalFieldSet:
    def test_capability_fields_constant_matches_output(self):
        """CAPABILITY_FIELDS is the canonical list the docs table + drift guard
        check against; it must equal exactly the keys derive produces."""
        from capabilities_lib import derive_capabilities, CAPABILITY_FIELDS
        caps = derive_capabilities(_base_config())
        assert set(caps.keys()) == set(CAPABILITY_FIELDS)
        # the two gap fields fixed by this PR are present
        assert "deploy_method" in CAPABILITY_FIELDS
        assert "migration_dir" in CAPABILITY_FIELDS


class TestDeriveCLI:
    def test_valid_config_emits_config_and_capabilities(self, tmp_path):
        p = tmp_path / ".rawgentic.json"
        p.write_text(json.dumps(_base_config(testing={"frameworks": [{"name": "pytest", "command": "pytest"}]})))
        out, err, rc = _run_cli("derive", "--config", str(p))
        assert rc == 0, err
        payload = json.loads(out)
        assert payload["config"]["repo"]["fullName"] == "owner/name"
        assert payload["capabilities"]["has_tests"] is True
        assert payload["capabilities"]["repo"] == "owner/name"

    def test_missing_file_fails_closed(self, tmp_path):
        _, err, rc = _run_cli("derive", "--config", str(tmp_path / "nope.json"))
        assert rc == 1
        assert "setup" in err.lower()

    def test_malformed_json_fails_closed(self, tmp_path):
        p = tmp_path / ".rawgentic.json"
        p.write_text("{not valid")
        _, err, rc = _run_cli("derive", "--config", str(p))
        assert rc == 1

    def test_non_object_json_fails_closed(self, tmp_path):
        p = tmp_path / ".rawgentic.json"
        p.write_text("[1,2,3]")
        _, _, rc = _run_cli("derive", "--config", str(p))
        assert rc == 1

    def test_essential_missing_fails_closed(self, tmp_path):
        p = tmp_path / ".rawgentic.json"
        cfg = _base_config()
        cfg["repo"].pop("fullName")
        p.write_text(json.dumps(cfg))
        _, _, rc = _run_cli("derive", "--config", str(p))
        assert rc == 1

    def test_version_mismatch_warns_but_succeeds(self, tmp_path):
        """Faithful to the existing docs: a version mismatch WARNS on stderr but
        does NOT stop (migration-path safety), unlike a corrupt config."""
        p = tmp_path / ".rawgentic.json"
        p.write_text(json.dumps(_base_config(version=2)))
        out, err, rc = _run_cli("derive", "--config", str(p))
        assert rc == 0, err
        assert "version" in err.lower()
        assert json.loads(out)["capabilities"]["repo"] == "owner/name"

    def test_missing_version_warns_but_succeeds(self, tmp_path):
        p = tmp_path / ".rawgentic.json"
        cfg = _base_config()
        cfg.pop("version")
        p.write_text(json.dumps(cfg))
        out, err, rc = _run_cli("derive", "--config", str(p))
        assert rc == 0, err

    def test_no_subcommand_fails(self):
        _, _, rc = _run_cli()
        assert rc != 0


# The 11 workflow skills whose config-loading block carried the identical
# (now-extracted) capabilities derivation.
WIRED_SKILLS = [
    "adversarial-review", "create-issue", "create-tests", "fix-bug",
    "implement-feature", "incident", "optimize-perf", "refactor",
    "security-audit", "update-deps", "update-docs",
]
SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"
DOCS = Path(__file__).resolve().parent.parent.parent / "docs" / "config-reference.md"


class TestCapabilitiesSkillWiring:
    """Drift guard: every workflow skill must drive the derivation through the CLI,
    not a re-introduced prose copy of the (formerly duplicated) mapping."""

    @pytest.mark.parametrize("skill", WIRED_SKILLS)
    def test_skill_invokes_derive_cli(self, skill):
        content = (SKILLS_DIR / skill / "SKILL.md").read_text()
        assert "capabilities_lib.py derive" in content, (
            f"{skill}/SKILL.md must invoke `capabilities_lib.py derive`; if you "
            f"renamed the subcommand, update the skills and this guard."
        )

    @pytest.mark.parametrize("skill", WIRED_SKILLS)
    def test_skill_has_no_prose_derivation(self, skill):
        """The old hand-derivation lines must not coexist with the CLI — two
        sources of the mapping is exactly the drift this PR removes."""
        content = (SKILLS_DIR / skill / "SKILL.md").read_text()
        assert "has_tests: config.testing exists AND" not in content, (
            f"{skill}/SKILL.md re-introduced the prose capabilities derivation; "
            f"the CLI is the single source of truth."
        )
        assert "Build the `capabilities` object from config:" not in content


class TestDocsTableDriftGuard:
    """The docs/config-reference.md capabilities table must list exactly the
    fields the lib produces — so the doc, the lib, and the skills stay in sync."""

    def _doc_table_fields(self):
        import re
        text = DOCS.read_text()
        start = text.index("**Capabilities object.**")
        end = text.index("Skills adapt their behavior", start)
        region = text[start:end]
        # rows look like:  | `field` | ... |
        return set(re.findall(r"^\|\s*`([a-z_]+)`\s*\|", region, re.MULTILINE))

    def test_doc_table_matches_capability_fields(self):
        from capabilities_lib import CAPABILITY_FIELDS
        assert self._doc_table_fields() == set(CAPABILITY_FIELDS), (
            "docs/config-reference.md capabilities table is out of sync with "
            "capabilities_lib.CAPABILITY_FIELDS — update the table (or the lib)."
        )
