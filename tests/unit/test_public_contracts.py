"""Public contract shapes that survive the ADR-027 migration.

Restored in step M4 after Codex's review: these assertions lived in the retired compatibility test files
(``test_application_component_compatibility.py``, ``test_domain_component_compatibility.py``) but check
contracts that still exist, independent of any import path: constructor, function and protocol
signatures (baseline 218c3dc, with the changes recorded there), generic result shapes, union members,
Protocol inheritance and exact enum values. Bodies are unchanged except for canonical imports.
"""

import inspect
import typing
from inspect import Parameter

from autonomous_mixing.core.ports.analysis import AudioAnalyzer
from autonomous_mixing.core.ports.decision import DecisionMaker
from autonomous_mixing.core.ports.rendering import Renderer
from autonomous_mixing.core.records.admission import (
    AdmissionResult,
    AdmittedAction,
    MetadataReady,
    PreflightRejected,
    PreflightResult,
    RejectedAction,
)
from autonomous_mixing.core.records.rendering import RenderedCandidate, RenderFailure, RenderOutcome
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, PortResult, Success


def test_shared_result_types_keep_their_generic_shape_and_codes():
    (value,) = inspect.signature(Success).parameters.values()
    assert value.name == "value" and isinstance(value.annotation, typing.TypeVar)
    assert typing.get_args(PortResult)[1] is PortFailure
    assert typing.get_origin(typing.get_args(PortResult)[0]) is Success
    assert [(c.name, c.value) for c in PortFailureCode] == [
        ("INVALID_INPUT", "invalid_input"),
        ("UNSUPPORTED", "unsupported"),
        ("UNAVAILABLE", "unavailable"),
        ("INVALID_RESPONSE", "invalid_response"),
        ("INTERNAL_ERROR", "internal_error"),
    ]


def test_port_protocols_and_outcome_unions_keep_their_shape():
    for port in (AudioAnalyzer, DecisionMaker, Renderer):
        assert typing.Protocol in port.__mro__, port.__name__
    assert typing.get_args(AdmissionResult) == (AdmittedAction, RejectedAction)
    assert typing.get_args(RenderOutcome) == (RenderedCandidate, RenderFailure)
    assert typing.get_args(PreflightResult) == (MetadataReady, PreflightRejected)
    # Deliberate extension, increment 2E (2026-11-05): NoChange joins the decision outcome union.
    from autonomous_mixing.core.records.decision import Abstain, Decision, NoChange, ProposeAction

    assert typing.get_args(typing.get_type_hints(Decision)["outcome"]) == (ProposeAction, NoChange, Abstain)


def _params(obj) -> list[tuple]:
    signature = inspect.signature(obj, eval_str=True)
    return [(p.name, p.kind, p.default, p.annotation) for p in signature.parameters.values()] + [
        ("return", signature.return_annotation)
    ]


def _fields(*pairs, returns=inspect.Signature.empty):
    return [(name, Parameter.POSITIONAL_OR_KEYWORD, Parameter.empty, annotation) for name, annotation in pairs] + [
        ("return", returns)
    ]


def _method(*pairs, returns):
    return [("self", Parameter.POSITIONAL_OR_KEYWORD, Parameter.empty, Parameter.empty)] + _fields(
        *pairs, returns=returns
    )


def test_task1_constructor_function_and_protocol_signatures_match_the_baseline():
    from autonomous_mixing.core.ports.analysis import AudioAnalyzer, validate_analysis_response
    from autonomous_mixing.core.ports.decision import DecisionMaker, validate_decision_response
    from autonomous_mixing.core.records.actions import Action
    from autonomous_mixing.core.records.analysis import AnalysisRequest, AnalysisResponse
    from autonomous_mixing.core.records.budgets import BudgetLimits, BudgetUsage
    from autonomous_mixing.core.records.capabilities import ActionCapabilityView
    from autonomous_mixing.core.records.context import MusicalContext
    from autonomous_mixing.core.records.decision import (
        ActionJustification,
        ControllerIdentity,
        Decision,
        DecisionResponse,
        NoChange,
        ProposeAction,
        Situation,
    )
    from autonomous_mixing.core.records.diagnosis import DiagnosisReport
    from autonomous_mixing.core.records.feedback import DecisionHistory
    from autonomous_mixing.core.records.identifiers import ProblemCandidateId, RunId
    from autonomous_mixing.core.records.observation import AnalysisScope, AnalysisSpec, Observation
    from autonomous_mixing.core.records.project import ProjectState
    from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, PortResult
    from autonomous_mixing.core.records.verification import AcceptanceCriteria

    expected = {
        PortFailure: _fields(("code", PortFailureCode), ("message", str), ("retryable", bool), returns=None),
        AnalysisRequest: _fields(
            ("state", ProjectState), ("scope", AnalysisScope), ("specification", AnalysisSpec), returns=None
        ),
        AnalysisResponse: _fields(("observations", tuple[Observation, ...]), returns=None),
        Situation: _fields(
            ("state", ProjectState),
            ("context", MusicalContext),
            ("controller", ControllerIdentity),
            ("observations", tuple[Observation, ...]),
            ("usage", BudgetUsage),
            ("limits", BudgetLimits),
            # Deliberate constructor extension, increment 2D (2026-11-05): six required fields, no defaults.
            ("run_id", RunId),
            ("scope", AnalysisScope),
            ("criteria", AcceptanceCriteria),
            ("diagnosis", DiagnosisReport),
            ("capabilities", ActionCapabilityView),
            ("history", DecisionHistory),
            returns=None,
        ),
        DecisionResponse: _fields(("decision", Decision), returns=None),
        # Deliberate constructor contracts, increment 2E (2026-11-05): ProposeAction gains two required fields.
        ActionJustification: _fields(
            ("reasoning", str),
            ("uncertainty", str),
            ("assumptions", tuple[str, ...]),
            ("limitations", tuple[str, ...]),
            returns=None,
        ),
        ProposeAction: _fields(
            ("action", Action),
            ("expected_effect", str),
            ("candidate_ids", tuple[ProblemCandidateId, ...]),
            ("justification", ActionJustification),
            returns=None,
        ),
        NoChange: _fields(("explanation", str), returns=None),
        validate_analysis_response: _fields(("request", AnalysisRequest), ("response", AnalysisResponse), returns=None),
        validate_decision_response: _fields(("request", Situation), ("response", DecisionResponse), returns=None),
        AudioAnalyzer.analyze: _method(("request", AnalysisRequest), returns=PortResult[AnalysisResponse]),
        DecisionMaker.decide: _method(("request", Situation), returns=PortResult[DecisionResponse]),
    }
    for obj, signature in expected.items():
        assert _params(obj) == signature, obj.__qualname__


def test_admission_signatures_match_the_baseline():
    from autonomous_mixing.core.admission.admission import admit_action
    from autonomous_mixing.core.records.actions import Action
    from autonomous_mixing.core.records.admission import (
        ActionAdmissionPolicy,
        ActionAdmissionRequest,
        AdmissionRejectionCode,
        AdmissionResult,
        AdmittedAction,
        RejectedAction,
        TrackGainPermission,
    )
    from autonomous_mixing.core.records.decision import Decision, Situation
    from autonomous_mixing.core.records.identifiers import DecisionId, ProjectId, TrackId, VersionRef

    expected = {
        TrackGainPermission: _fields(("track_id", TrackId), ("minimum_db", float), ("maximum_db", float), returns=None),
        ActionAdmissionPolicy: _fields(
            ("reference", VersionRef),
            ("project_id", ProjectId),
            ("gain_permissions", tuple[TrackGainPermission, ...]),
            returns=None,
        ),
        ActionAdmissionRequest: _fields(
            ("decision_request", Situation),
            ("decision", Decision),
            ("policy", ActionAdmissionPolicy),
            returns=None,
        ),
        AdmittedAction: _fields(("action", Action), ("policy_reference", VersionRef), returns=None),
        RejectedAction: _fields(
            ("decision_id", DecisionId), ("code", AdmissionRejectionCode), ("explanation", str), returns=None
        ),
        admit_action: _fields(("request", ActionAdmissionRequest), returns=AdmissionResult),
    }
    for obj, signature in expected.items():
        assert _params(obj) == signature, obj.__qualname__
    assert [(c.name, c.value) for c in AdmissionRejectionCode] == [
        ("INVALID_REFERENCES", "invalid_references"),
        ("POLICY_PROJECT_MISMATCH", "policy_project_mismatch"),
        ("NOT_AN_ACTION", "not_an_action"),
        ("PROTECTED_TRACK", "protected_track"),
        ("NO_GAIN_PERMISSION", "no_gain_permission"),
        ("OUT_OF_BOUNDS", "out_of_bounds"),
    ]


def test_execution_signatures_match_the_baseline():
    from autonomous_mixing.core.admission.preflight import preflight_execution
    from autonomous_mixing.core.ports.rendering import Renderer, validate_rendered_candidate
    from autonomous_mixing.core.records.actions import ExecutionIssue, ExecutionStatus
    from autonomous_mixing.core.records.admission import (
        ActionAdmissionPolicy,
        ExecutionRequest,
        MetadataReady,
        PreflightCode,
        PreflightRejected,
        PreflightResult,
        RejectedAction,
        StemBinding,
    )
    from autonomous_mixing.core.records.artifacts import AudioArtifact
    from autonomous_mixing.core.records.decision import Decision, Situation
    from autonomous_mixing.core.records.identifiers import (
        ArtifactId,
        ExecutionId,
        ProjectId,
        Revision,
        TrackId,
        VersionRef,
    )
    from autonomous_mixing.core.records.mix import MixRecipe, ProcessingSpec
    from autonomous_mixing.core.records.observation import RenderSpec
    from autonomous_mixing.core.records.rendering import (
        CandidateIdentity,
        ExecutionWorkspace,
        RenderedCandidate,
        RenderEvidence,
        RenderFailure,
        RenderOutcome,
        RenderPlan,
    )

    expected = {
        StemBinding: _fields(("track_id", TrackId), ("artifact_id", ArtifactId), returns=None),
        ExecutionWorkspace: _fields(
            ("execution_id", ExecutionId), ("root_key", str), ("directory_token", str), returns=None
        ),
        CandidateIdentity: _fields(
            ("revision", Revision), ("target_artifact_id", ArtifactId), ("mix_artifact_id", ArtifactId), returns=None
        ),
        ExecutionRequest: _fields(
            ("execution_id", ExecutionId),
            ("decision_request", Situation),
            ("decision", Decision),
            ("policy", ActionAdmissionPolicy),
            ("mix_recipe", MixRecipe),
            ("stem_bindings", tuple[StemBinding, ...]),
            ("current_mix_artifact_id", ArtifactId),
            ("render_spec", RenderSpec),
            ("processing_spec", ProcessingSpec),
            ("workspace", ExecutionWorkspace),
            ("candidate", CandidateIdentity),
            returns=None,
        ),
        RenderPlan: _fields(
            ("execution_id", ExecutionId),
            ("project_id", ProjectId),
            ("before_revision", Revision),
            ("candidate", CandidateIdentity),
            ("target_track_id", TrackId),
            ("fixed_target_input", AudioArtifact),
            ("requested_gain_db", float),
            ("current_stems", tuple[AudioArtifact, ...]),
            ("target_current_artifact_id", ArtifactId),
            ("current_mix_artifact_id", ArtifactId),
            ("recipe", MixRecipe),
            ("render_spec", RenderSpec),
            ("processing_spec", ProcessingSpec),
            ("workspace", ExecutionWorkspace),
            returns=None,
        ),
        RenderEvidence: _fields(
            ("artifact_id", ArtifactId),
            ("verified_input_ids", tuple[ArtifactId, ...]),
            ("input_hashes_verified", bool),
            ("finite_samples", bool),
            ("peak_linear", float),
            ("processor", VersionRef),
            ("processing_spec", ProcessingSpec),
            returns=None,
        ),
        RenderedCandidate: _fields(
            ("target_artifact", AudioArtifact),
            ("mix_artifact", AudioArtifact),
            ("evidence", tuple[RenderEvidence, ...]),
            returns=None,
        ),
        RenderFailure: _fields(("status", ExecutionStatus), ("issue", ExecutionIssue), returns=None),
        PreflightRejected: _fields(
            ("execution_id", ExecutionId),
            ("code", PreflightCode),
            ("explanation", str),
            ("admission_rejection", RejectedAction | None),
            returns=None,
        ),
        MetadataReady: _fields(("plan", RenderPlan), returns=None),
        validate_rendered_candidate: _fields(("plan", RenderPlan), ("response", RenderedCandidate), returns=None),
        preflight_execution: _fields(("request", ExecutionRequest), returns=PreflightResult),
        Renderer.process: _method(("plan", RenderPlan), returns=RenderOutcome),
    }
    for obj, signature in expected.items():
        assert _params(obj) == signature, obj.__qualname__
    assert [code.name for code in PreflightCode] == [
        "ADMISSION_REJECTED",
        "INVALID_WORKSPACE",
        "INVALID_CANDIDATE_IDENTITY",
        "INVALID_RECIPE_BINDINGS",
        "INVALID_MIX",
        "MISSING_GAIN_STATE",
        "INCOMPATIBLE_AUDIO",
        "UNSUPPORTED_DEPENDENCY",
    ]


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

    from autonomous_mixing.core.loop.references import validate_attempt_references
    from autonomous_mixing.core.loop.transitions import derive_candidate_project_state
    from autonomous_mixing.core.ports.decision import validate_decision_references
    from autonomous_mixing.core.records.actions import (
        Action,
        ExecutionReceipt,
        RollbackReceipt,
        derive_candidate_checkpoint,
        validate_receipt_against_checkpoint,
        validate_rollback_references,
    )
    from autonomous_mixing.core.records.artifacts import AudioArtifact
    from autonomous_mixing.core.records.context import MusicalContext
    from autonomous_mixing.core.records.decision import Decision
    from autonomous_mixing.core.records.identifiers import CheckpointId
    from autonomous_mixing.core.records.observation import Observation, validate_observation_sources
    from autonomous_mixing.core.records.project import ArtifactCheckpoint, ProjectSnapshot, ProjectState
    from autonomous_mixing.core.records.run import CorrectionAttempt, RunRecord
    from autonomous_mixing.core.records.verification import AcceptanceCriteria, VerificationResult
    from autonomous_mixing.core.verification.consistency import validate_verification_references

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
        validate_verification_references: params(("result", VerificationResult), ("criteria", AcceptanceCriteria)),
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
        validate_attempt_references: params(("attempt", CorrectionAttempt), ("run", RunRecord), ("decision", Decision)),
    }
    for function, signature in expected.items():
        assert _signature(function) == signature, function.__name__
