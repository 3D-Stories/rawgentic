"""phase_executor — deterministic model-seat execution engine.

Public API:
- Contract:  ``Observation``, ``canonicalize_model_id``, ``models_match``,
             ``validate_observation``, ``validate_routing_table``
- Routing:   ``RoutingSnapshot``, ``RoutingConfig``, ``load_routing_table``,
             ``snapshot_from_file``, ``eligible_targets``, ``select_target``,
             ``ChainExhausted``
- Quota:     ``QuotaCoordinator``, ``QuotaTimeout``
- Engine:    ``run_seat``, ``run_competitive``, ``Candidate``, ``InfeasibleBakeoff``
- Adapters:  ``AdapterRequest``, ``parse_claude``, ``parse_codex``, ``parse_zhipuai``, ``ADAPTERS``
- Enforcement: ``check_pre``, ``verify_post``, ``RoutingAuditLog``, ``reconcile_run``, ``PreReceipt``,
             ``PostCheck``, ``ExpectedCall``, ``Reconcile``, ``target_identity``, ``audited_digests``,
             ``ENFORCEABLE_ROLES``

The normative artifacts are the committed JSON Schemas (``schemas/observation.schema.json``,
``schemas/routing-table.schema.json``); this package is one producer of those documents.
"""
from .adapters import ADAPTERS, AdapterRequest, parse_claude, parse_codex, parse_zhipuai
from .contract import (
    Observation,
    canonicalize_model_id,
    models_match,
    observation_schema,
    routing_table_schema,
    validate_observation,
    validate_routing_table,
)
from .engine import Candidate, InfeasibleBakeoff, run_competitive, run_seat
from .quota import QuotaCoordinator, QuotaTimeout
from .routing import (
    ChainExhausted,
    RoutingConfig,
    RoutingSnapshot,
    eligible_targets,
    load_routing_table,
    select_target,
    snapshot_from_file,
)
from .enforce import (
    ENFORCEABLE_ROLES,
    ExpectedCall,
    PostCheck,
    PreReceipt,
    Reconcile,
    RoutingAuditLog,
    audited_digests,
    check_pre,
    reconcile_run,
    target_identity,
    verify_post,
)

__version__ = "0.1.0"

__all__ = [
    "Observation", "canonicalize_model_id", "models_match", "observation_schema",
    "routing_table_schema", "validate_observation", "validate_routing_table",
    "RoutingSnapshot", "RoutingConfig", "load_routing_table", "snapshot_from_file",
    "eligible_targets", "select_target", "ChainExhausted",
    "QuotaCoordinator", "QuotaTimeout",
    "run_seat", "run_competitive", "Candidate", "InfeasibleBakeoff",
    "AdapterRequest", "parse_claude", "parse_codex", "parse_zhipuai", "ADAPTERS",
    "check_pre", "verify_post", "RoutingAuditLog", "reconcile_run", "PreReceipt", "PostCheck",
    "ExpectedCall", "Reconcile", "target_identity", "audited_digests", "ENFORCEABLE_ROLES",
    "__version__",
]
