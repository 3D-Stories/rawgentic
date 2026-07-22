"""Engine adapters. Each module exposes a pure ``parse_*`` (fixture-tested) and a live ``run``."""
from . import claude_cli, codex_cli, hermes_http, zhipuai_sdk
from .base import AdapterRequest, ParsedResult, build_observation, resolve_parse_status, run_subprocess
from .claude_cli import parse_claude
from .codex_cli import parse_codex
from .hermes_http import parse_run_object as parse_hermes
from .zhipuai_sdk import parse_zhipuai

ADAPTERS = {"claude": claude_cli, "codex": codex_cli, "hermes": hermes_http, "zhipuai": zhipuai_sdk}

__all__ = [
    "AdapterRequest", "ParsedResult", "build_observation", "resolve_parse_status", "run_subprocess",
    "parse_claude", "parse_codex", "parse_hermes", "parse_zhipuai", "ADAPTERS",
    "claude_cli", "codex_cli", "hermes_http", "zhipuai_sdk",
]
