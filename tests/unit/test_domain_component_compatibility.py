"""Compatibility of the domain component refactor: legacy and new imports name the same objects.

The root ``autonomous_mixing.domain`` exports and the legacy ``values``/``execution`` modules are
migration interfaces. This is the only test module that should import them for compatibility checks.
"""

import importlib

import autonomous_mixing.domain as root
import autonomous_mixing.domain.shared as shared
import autonomous_mixing.domain.values as legacy_values

IDENTIFIERS = (
    "ProjectId",
    "TrackId",
    "ArtifactId",
    "ObservationId",
    "ContextId",
    "ActionId",
    "DecisionId",
    "CriteriaId",
    "ConditionId",
    "ValidationResultId",
    "RunId",
    "AttemptId",
    "ExecutionId",
    "CheckpointId",
    "Revision",
)
INVARIANT_HELPERS = (
    "require_text",
    "require_finite",
    "require_finite_result",
    "require_count",
    "require_utc",
    "require_instance",
    "require_tuple_of",
)
SHARED_PUBLIC = (*IDENTIFIERS, "VersionRef", *INVARIANT_HELPERS)


def test_shared_legacy_exports_are_identical():
    assert set(shared.__all__) == set(SHARED_PUBLIC)
    for name in SHARED_PUBLIC:
        assert getattr(legacy_values, name) is getattr(shared, name), name
        if name in root.__all__:
            assert getattr(root, name) is getattr(shared, name), name


COMPONENT_PUBLIC = {
    "project": {
        "tracks": ("Track",),
        "snapshot": ("ProjectSnapshot",),
        "artifacts": ("AudioArtifact", "ArtifactKind"),
        "checkpoint": ("ArtifactCheckpoint",),
        "scope": ("AnalysisScope", "TimeRange", "FrequencyRange"),
        # Added by the processing-state migration (ADR-019): new public records, not moved objects.
        "processing": ("TrackGainState", "ProjectProcessingState"),
        "state": ("ProjectState",),
        # Added by the execution-contract stage (ADR-020): full-mix recipe records.
        "mix": ("SummingPolicy", "MixRecipe"),
        # Added by the processing-spec stage (ADR-021).
        "processing_spec": (
            "ProcessingSpec",
            "NumericalPrecision",
            "GainRule",
            "SummationOrder",
            "LevelPolicy",
            "OutputContainer",
            "SampleEncoding",
        ),
    },
    "context": {
        "assertions": ("AssertionSource", "TrackRole", "ContextPreference"),
        "musical_context": ("MusicalContext",),
    },
    "observation": {
        "observation": ("Observation", "MeasuredScalar", "Unavailable"),
        "specifications": ("AnalysisSpec", "RenderSpec"),
        "provenance": ("validate_observation_sources",),
    },
}


# ADR-027 migration step M1: records moved to ``autonomous_mixing.core.records``. Their exact owner
# module is asserted by tests/architecture/test_core_records.py (guarantee ledger G9); here the legacy
# paths must still name the same object.
RECORDS = "autonomous_mixing.core.records"


def _check_component(component: str, owners: dict[str, tuple[str, ...]]) -> None:
    package = importlib.import_module(f"autonomous_mixing.domain.{component}")
    assert hasattr(package, "__path__"), f"{component} must be a package"
    expected = {name for names in owners.values() for name in names}
    assert set(package.__all__) == expected, component
    for module_name, names in owners.items():
        module = importlib.import_module(f"autonomous_mixing.domain.{component}.{module_name}")
        for name in names:
            obj = getattr(package, name)
            if obj.__module__.startswith(RECORDS + "."):
                assert getattr(importlib.import_module(obj.__module__), name) is obj
                # The legacy module path must still export the moved object (until migration step M4).
                assert hasattr(module, name), f"{module.__name__} lost {name}"
                assert getattr(module, name) is obj
            else:
                assert obj.__module__ == module.__name__, f"{component}.{name} defined in {obj.__module__}"
                assert getattr(module, name) is obj
            assert getattr(root, name) is obj, f"root {name}"


COMPONENT_PUBLIC.update(
    {
        "action": {
            "operations": ("SetTrackGain",),
            "proposal": ("Action",),
            "execution": (
                "ExecutionStatus",
                "ExecutionIssue",
                "ExecutionReceipt",
                "validate_receipt_against_checkpoint",
                "derive_candidate_checkpoint",
                "derive_candidate_project_state",  # added by the processing-state migration (ADR-019)
                "validate_gain_execution",  # added by the audit F1 correction
            ),
            "rollback": ("RollbackStatus", "RollbackReceipt", "validate_rollback_references"),
        },
        "decision": {
            "decision": ("ControllerIdentity", "AbstentionReason", "Abstain", "ProposeAction", "Decision"),
            "references": ("validate_decision_references",),
        },
        "validation": {
            "criteria": (
                "MetricSelector",
                "ComparisonOperator",
                "ImprovementDirection",
                "BaselineReference",
                "TargetCondition",
                "ImprovementCondition",
                "AbsoluteGuard",
                "DeltaGuard",
                "ValidationCriteria",
            ),
            "result": (
                "AssessmentMode",
                "CheckStatus",
                "ValidationVerdict",
                "TargetStatus",
                "CheckResult",
                "ValidationResult",
            ),
            "consistency": ("validate_validation_references",),
        },
        "run": {
            "configuration": ("RunConfiguration",),
            "budgets": ("BudgetLimits", "BudgetUsage"),
            "run": ("RunState", "RunOutcome", "FinalProjectState", "Run"),
            "attempt": ("AttemptDisposition", "CorrectionAttempt"),
            "references": ("validate_attempt_references",),
        },
    }
)
LEGACY_EXECUTION_PUBLIC = (
    "ArtifactCheckpoint",
    "ExecutionStatus",
    "ExecutionIssue",
    "ExecutionReceipt",
    "validate_receipt_against_checkpoint",
    "RollbackStatus",
    "RollbackReceipt",
    "validate_rollback_references",
    "derive_candidate_checkpoint",
)


def test_action_decision_validation_and_run_interfaces_own_their_objects():
    for component in ("action", "decision", "validation", "run"):
        _check_component(component, COMPONENT_PUBLIC[component])


def test_every_previous_root_export_is_the_same_object_as_its_single_owner():
    owners = {name: shared for name in (*IDENTIFIERS, "VersionRef")}
    for component, modules in COMPONENT_PUBLIC.items():
        package = importlib.import_module(f"autonomous_mixing.domain.{component}")
        for names in modules.values():
            for name in names:
                assert name not in owners, f"{name} owned twice"
                owners[name] = package
    assert set(root.__all__) == set(owners)
    # 76 exports from the component refactor, plus three processing-state records and
    # derive_candidate_project_state (ADR-019), plus MixRecipe and SummingPolicy (ADR-020), plus
    # validate_gain_execution (audit F1 correction), plus ProcessingSpec and its six enums (ADR-021).
    assert len(root.__all__) == 90
    for name, owner in owners.items():
        assert getattr(root, name) is getattr(owner, name), name


def test_legacy_execution_module_reexports_the_same_objects():
    legacy_execution = importlib.import_module("autonomous_mixing.domain.execution")
    project = importlib.import_module("autonomous_mixing.domain.project")
    action = importlib.import_module("autonomous_mixing.domain.action")
    assert set(legacy_execution.__all__) == set(LEGACY_EXECUTION_PUBLIC)
    for name in LEGACY_EXECUTION_PUBLIC:
        owner = project if name == "ArtifactCheckpoint" else action
        assert getattr(legacy_execution, name) is getattr(owner, name), name


def _signature(function) -> list[tuple]:
    import inspect

    return [
        (p.name, p.kind, p.default, p.annotation)
        for p in inspect.signature(function, eval_str=True).parameters.values()
    ] + [("return", inspect.signature(function, eval_str=True).return_annotation)]


def test_cross_record_and_derivation_signatures_are_preserved():
    from collections.abc import Iterable
    from datetime import datetime
    from inspect import Parameter

    from autonomous_mixing.domain.action import (
        Action,
        ExecutionReceipt,
        RollbackReceipt,
        derive_candidate_checkpoint,
        derive_candidate_project_state,
        validate_receipt_against_checkpoint,
        validate_rollback_references,
    )
    from autonomous_mixing.domain.context import MusicalContext
    from autonomous_mixing.domain.decision import Decision, validate_decision_references
    from autonomous_mixing.domain.observation import Observation, validate_observation_sources
    from autonomous_mixing.domain.project import ArtifactCheckpoint, AudioArtifact, ProjectSnapshot, ProjectState
    from autonomous_mixing.domain.run import CorrectionAttempt, Run, validate_attempt_references
    from autonomous_mixing.domain.shared import CheckpointId
    from autonomous_mixing.domain.validation import ValidationCriteria, ValidationResult, validate_validation_references

    pos, empty = Parameter.POSITIONAL_OR_KEYWORD, Parameter.empty

    def params(*pairs, returns=None):
        return [(name, pos, empty, annotation) for name, annotation in pairs] + [("return", returns)]

    expected = {
        validate_observation_sources: params(
            ("observation", Observation),
            ("artifacts", Iterable[AudioArtifact]),
            ("checkpoint", ArtifactCheckpoint),
            ("snapshot", ProjectSnapshot),
        ),
        validate_decision_references: params(
            ("decision", Decision),
            ("snapshot", ProjectSnapshot),
            ("context", MusicalContext),
            ("observations", Iterable[Observation]),
        ),
        validate_validation_references: params(("result", ValidationResult), ("criteria", ValidationCriteria)),
        # Intentionally changed by the processing-state migration (ADR-019): complete-state rollback.
        validate_rollback_references: params(("receipt", RollbackReceipt), ("before_state", ProjectState)),
        validate_receipt_against_checkpoint: params(("receipt", ExecutionReceipt), ("before", ArtifactCheckpoint)),
        derive_candidate_checkpoint: params(
            ("previous", ArtifactCheckpoint),
            ("receipt", ExecutionReceipt),
            ("checkpoint_id", CheckpointId),
            returns=ArtifactCheckpoint,
        ),
        # New in the processing-state migration (ADR-019).
        derive_candidate_project_state: params(
            ("previous", ProjectState),
            ("receipt", ExecutionReceipt),
            ("action", Action),
            ("checkpoint_id", CheckpointId),
            ("captured_at", datetime),
            returns=ProjectState,
        ),
        validate_attempt_references: params(("attempt", CorrectionAttempt), ("run", Run), ("decision", Decision)),
    }
    for function, signature in expected.items():
        assert _signature(function) == signature, function.__name__


def test_project_context_and_observation_interfaces_own_their_objects():
    for component in ("project", "context", "observation"):
        _check_component(component, COMPONENT_PUBLIC[component])


def test_checkpoint_moved_to_project_keeps_identity_with_legacy_execution():
    project = importlib.import_module("autonomous_mixing.domain.project")
    legacy_execution = importlib.import_module("autonomous_mixing.domain.execution")
    assert project.ArtifactCheckpoint is legacy_execution.ArtifactCheckpoint
    assert root.ArtifactCheckpoint is project.ArtifactCheckpoint


def test_shared_objects_are_defined_once_in_their_owner_modules():
    owners = {name: f"{RECORDS}.identifiers" for name in (*IDENTIFIERS, "VersionRef")}
    owners.update({name: f"{RECORDS}.invariants" for name in INVARIANT_HELPERS})
    for name, module in owners.items():
        obj = getattr(shared, name)
        assert obj.__module__ == module, name
        assert getattr(importlib.import_module(module), name) is obj


# ADR-027 migration step M1 moved these private helpers; their legacy paths stay complete until step M4.
MOVED_PRIVATE_HELPERS = {
    "autonomous_mixing.domain.shared.identifiers": ("_Identifier", "identifiers"),
    "autonomous_mixing.domain.project.artifacts": ("_SHA256_HEX", "artifacts"),
    "autonomous_mixing.domain.project.checkpoint": ("_require_unique_artifacts", "project"),
    "autonomous_mixing.domain.action.execution": ("_require_unique_artifacts", "project"),
    "autonomous_mixing.domain.run.attempt": ("_check_candidate_state", "run"),
    "autonomous_mixing.domain.validation.criteria": ("_require_condition_fields", "verification"),
    "autonomous_mixing.application.execution.identity": ("_DIRECTORY_TOKEN", "rendering"),
    "autonomous_mixing.application.observation.contracts": ("_require_unique_observations", "analysis"),
    "autonomous_mixing.application.decision.contracts": ("_require_unique_observations", "decision"),
}


def test_moved_private_helpers_stay_reachable_at_their_legacy_paths():
    for legacy, (name, owner) in MOVED_PRIVATE_HELPERS.items():
        module = importlib.import_module(legacy)
        assert hasattr(module, name), f"{legacy} lost {name}"
        assert getattr(module, name) is getattr(importlib.import_module(f"{RECORDS}.{owner}"), name), legacy
