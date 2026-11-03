"""Compatibility of the application component migration: legacy and canonical imports name the same objects.

The application root ``autonomous_mixing.application`` and the legacy modules ``contracts``, ``results``,
``execution_contracts``, ``execution_preflight`` and ``ports`` (with ``ports.analysis``, ``ports.decision``,
``ports.processing``) are compatibility facades. This is the only test module that imports them for
identity and signature checks. Pickle payloads and ``__module__`` strings are not part of the contract,
except that each public object must be defined once, in its owning component module.
"""

import importlib
import inspect
import typing
from inspect import Parameter

import pytest

APP = "autonomous_mixing.application"

# Canonical ownership (owner module -> public names) per component interface.
CANONICAL = {
    "shared": {
        "results": ("Success", "PortFailureCode", "PortFailure", "PortResult"),
    },
    "observation": {
        "contracts": ("AnalysisRequest", "AnalysisResponse"),
        "validation": ("validate_analysis_response",),
        "ports": ("AudioAnalyzer",),
    },
    "decision": {
        "contracts": ("DecisionRequest", "DecisionResponse"),
        "validation": ("validate_decision_response",),
        "ports": ("DecisionController",),
    },
}

# Legacy dotted paths and the public definitions (including aliases) each must keep exposing.
LEGACY = {
    "results": ("Success", "PortFailureCode", "PortFailure", "PortResult"),
    "contracts": (
        "AnalysisRequest",
        "AnalysisResponse",
        "validate_analysis_response",
        "DecisionRequest",
        "DecisionResponse",
        "validate_decision_response",
    ),
    "ports.analysis": ("AudioAnalyzer",),
    "ports.decision": ("DecisionController",),
}

ALIASES = frozenset({"PortResult"})


def _module(name: str):
    return importlib.import_module(f"{APP}.{name}")


def _canonical_owner(name: str):
    """Return (component interface, owning module) for a public name."""
    for component, owners in CANONICAL.items():
        for module_name, names in owners.items():
            if name in names:
                return _module(component), _module(f"{component}.{module_name}")
    raise KeyError(name)


# ADR-027 migration step M1: records moved to ``autonomous_mixing.core.records``. Their exact owner
# module is asserted by tests/architecture/test_core_records.py (guarantee ledger G9); here the legacy
# paths must still name the same object.
RECORDS = "autonomous_mixing.core"  # ADR-027 M1 records, M2 behavior


def _check_component(component: str) -> None:
    package = _module(component)
    assert hasattr(package, "__path__"), f"{component} must be a package"
    expected = {name for names in CANONICAL[component].values() for name in names}
    assert set(package.__all__) == expected, component
    for module_name, names in CANONICAL[component].items():
        module = _module(f"{component}.{module_name}")
        for name in names:
            obj = getattr(package, name)
            assert getattr(module, name) is obj, f"{component}.{name}"
            if name in ALIASES:
                continue
            if obj.__module__.startswith(RECORDS + "."):
                assert getattr(importlib.import_module(obj.__module__), name) is obj
            else:
                assert obj.__module__ == module.__name__, f"{component}.{name} defined in {obj.__module__}"


@pytest.mark.parametrize("component", ["shared", "observation", "decision"])
def test_shared_observation_and_decision_interfaces_own_their_objects(component):
    _check_component(component)


@pytest.mark.parametrize("legacy", ["results", "contracts", "ports.analysis", "ports.decision"])
def test_task1_legacy_paths_reexport_the_canonical_objects(legacy):
    module = _module(legacy)
    for name in LEGACY[legacy]:
        package, _owner = _canonical_owner(name)
        assert getattr(module, name) is getattr(package, name), f"{legacy}.{name}"


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
    from autonomous_mixing.application.decision import (
        DecisionController,
        DecisionRequest,
        DecisionResponse,
        validate_decision_response,
    )
    from autonomous_mixing.application.observation import (
        AnalysisRequest,
        AnalysisResponse,
        AudioAnalyzer,
        validate_analysis_response,
    )
    from autonomous_mixing.application.shared import PortFailure, PortFailureCode, PortResult
    from autonomous_mixing.domain.context import MusicalContext
    from autonomous_mixing.domain.decision import ControllerIdentity, Decision
    from autonomous_mixing.domain.observation import AnalysisSpec, Observation
    from autonomous_mixing.domain.project import AnalysisScope, ProjectState
    from autonomous_mixing.domain.run import BudgetLimits, BudgetUsage

    expected = {
        PortFailure: _fields(("code", PortFailureCode), ("message", str), ("retryable", bool), returns=None),
        AnalysisRequest: _fields(
            ("state", ProjectState), ("scope", AnalysisScope), ("specification", AnalysisSpec), returns=None
        ),
        AnalysisResponse: _fields(("observations", tuple[Observation, ...]), returns=None),
        DecisionRequest: _fields(
            ("state", ProjectState),
            ("context", MusicalContext),
            ("controller", ControllerIdentity),
            ("observations", tuple[Observation, ...]),
            ("usage", BudgetUsage),
            ("limits", BudgetLimits),
            returns=None,
        ),
        DecisionResponse: _fields(("decision", Decision), returns=None),
        validate_analysis_response: _fields(("request", AnalysisRequest), ("response", AnalysisResponse), returns=None),
        validate_decision_response: _fields(("request", DecisionRequest), ("response", DecisionResponse), returns=None),
        AudioAnalyzer.analyze: _method(("request", AnalysisRequest), returns=PortResult[AnalysisResponse]),
        DecisionController.decide: _method(("request", DecisionRequest), returns=PortResult[DecisionResponse]),
    }
    for obj, signature in expected.items():
        assert _params(obj) == signature, obj.__qualname__


def test_shared_result_types_keep_their_generic_shape_and_codes():
    from autonomous_mixing.application.shared import PortFailure, PortFailureCode, PortResult, Success

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
    assert typing.Protocol in _module("observation").AudioAnalyzer.__mro__
    assert typing.Protocol in _module("decision").DecisionController.__mro__


# --- admission (Task 2): ``application.admission`` is now the package interface at the same dotted path.

CANONICAL["admission"] = {
    "policy": ("TrackGainPermission", "ActionAdmissionPolicy"),
    "contracts": (
        "ActionAdmissionRequest",
        "AdmissionRejectionCode",
        "AdmittedAction",
        "RejectedAction",
        "AdmissionResult",
    ),
    "admission": ("admit_action",),
}
ALIASES |= {"AdmissionResult"}


def test_admission_interface_owns_its_objects_in_focused_modules():
    _check_component("admission")
    import autonomous_mixing.application.admission as public
    import autonomous_mixing.application.admission.admission as implementation

    assert public.admit_action is implementation.admit_action
    assert typing.get_args(public.AdmissionResult) == (public.AdmittedAction, public.RejectedAction)


def test_admission_is_a_single_package_without_a_competing_flat_module():
    from pathlib import Path

    package = _module("admission")
    application_dir = Path(_module("shared").__file__).resolve().parents[1]
    assert Path(package.__file__).resolve() == application_dir / "admission" / "__init__.py"
    assert not (application_dir / "admission.py").exists()


def test_admission_signatures_match_the_baseline():
    from autonomous_mixing.application.admission import (
        ActionAdmissionPolicy,
        ActionAdmissionRequest,
        AdmissionRejectionCode,
        AdmissionResult,
        AdmittedAction,
        RejectedAction,
        TrackGainPermission,
        admit_action,
    )
    from autonomous_mixing.application.decision import DecisionRequest
    from autonomous_mixing.domain.action import Action
    from autonomous_mixing.domain.decision import Decision
    from autonomous_mixing.domain.shared import DecisionId, ProjectId, TrackId, VersionRef

    expected = {
        TrackGainPermission: _fields(("track_id", TrackId), ("minimum_db", float), ("maximum_db", float), returns=None),
        ActionAdmissionPolicy: _fields(
            ("reference", VersionRef),
            ("project_id", ProjectId),
            ("gain_permissions", tuple[TrackGainPermission, ...]),
            returns=None,
        ),
        ActionAdmissionRequest: _fields(
            ("decision_request", DecisionRequest),
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


# --- execution (Task 3) ------------------------------------------------------------------

CANONICAL["execution"] = {
    "identity": ("ExecutionWorkspace", "CandidateIdentity"),
    "requests": ("StemBinding", "ExecutionRequest"),
    "processing": (
        "ProcessingPlan",
        "ArtifactProcessingEvidence",
        "ProcessedCandidate",
        "ProcessingFailure",
        "ProcessingOutcome",
    ),
    "validation": ("validate_processed_candidate",),
    "preflight": ("PreflightCode", "PreflightRejected", "MetadataReady", "PreflightResult", "preflight_execution"),
    "ports": ("AudioProcessor",),
}
ALIASES |= {"ProcessingOutcome", "PreflightResult"}
LEGACY.update(
    {
        "execution_contracts": (
            "StemBinding",
            "ExecutionWorkspace",
            "CandidateIdentity",
            "ExecutionRequest",
            "ProcessingPlan",
            "ArtifactProcessingEvidence",
            "ProcessedCandidate",
            "ProcessingFailure",
            "ProcessingOutcome",
            "validate_processed_candidate",
        ),
        "execution_preflight": (
            "PreflightCode",
            "PreflightRejected",
            "MetadataReady",
            "PreflightResult",
            "preflight_execution",
        ),
        "ports.processing": ("AudioProcessor",),
    }
)


def test_execution_interface_owns_its_objects():
    _check_component("execution")
    execution = _module("execution")
    assert typing.get_args(execution.ProcessingOutcome) == (execution.ProcessedCandidate, execution.ProcessingFailure)
    assert typing.get_args(execution.PreflightResult) == (execution.MetadataReady, execution.PreflightRejected)
    assert typing.Protocol in execution.AudioProcessor.__mro__


@pytest.mark.parametrize("legacy", ["execution_contracts", "execution_preflight", "ports.processing"])
def test_execution_legacy_paths_reexport_the_canonical_objects(legacy):
    module = _module(legacy)
    for name in LEGACY[legacy]:
        package, _owner = _canonical_owner(name)
        assert getattr(module, name) is getattr(package, name), f"{legacy}.{name}"


def test_execution_signatures_match_the_baseline():
    from autonomous_mixing.application.admission import ActionAdmissionPolicy, RejectedAction
    from autonomous_mixing.application.decision import DecisionRequest
    from autonomous_mixing.application.execution import (
        ArtifactProcessingEvidence,
        AudioProcessor,
        CandidateIdentity,
        ExecutionRequest,
        ExecutionWorkspace,
        MetadataReady,
        PreflightCode,
        PreflightRejected,
        PreflightResult,
        ProcessedCandidate,
        ProcessingFailure,
        ProcessingOutcome,
        ProcessingPlan,
        StemBinding,
        preflight_execution,
        validate_processed_candidate,
    )
    from autonomous_mixing.domain.action import ExecutionIssue, ExecutionStatus
    from autonomous_mixing.domain.decision import Decision
    from autonomous_mixing.domain.observation import RenderSpec
    from autonomous_mixing.domain.project import AudioArtifact, MixRecipe, ProcessingSpec
    from autonomous_mixing.domain.shared import ArtifactId, ExecutionId, ProjectId, Revision, TrackId, VersionRef

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
            ("decision_request", DecisionRequest),
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
        ProcessingPlan: _fields(
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
        ArtifactProcessingEvidence: _fields(
            ("artifact_id", ArtifactId),
            ("verified_input_ids", tuple[ArtifactId, ...]),
            ("input_hashes_verified", bool),
            ("finite_samples", bool),
            ("peak_linear", float),
            ("processor", VersionRef),
            ("processing_spec", ProcessingSpec),
            returns=None,
        ),
        ProcessedCandidate: _fields(
            ("target_artifact", AudioArtifact),
            ("mix_artifact", AudioArtifact),
            ("evidence", tuple[ArtifactProcessingEvidence, ...]),
            returns=None,
        ),
        ProcessingFailure: _fields(("status", ExecutionStatus), ("issue", ExecutionIssue), returns=None),
        PreflightRejected: _fields(
            ("execution_id", ExecutionId),
            ("code", PreflightCode),
            ("explanation", str),
            ("admission_rejection", RejectedAction | None),
            returns=None,
        ),
        MetadataReady: _fields(("plan", ProcessingPlan), returns=None),
        validate_processed_candidate: _fields(("plan", ProcessingPlan), ("response", ProcessedCandidate), returns=None),
        preflight_execution: _fields(("request", ExecutionRequest), returns=PreflightResult),
        AudioProcessor.process: _method(("plan", ProcessingPlan), returns=ProcessingOutcome),
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


# --- complete inventory (Task 4) ---------------------------------------------------------

# Baseline root ``__all__`` at 218c3dc, in its original order.
BASELINE_ROOT_ALL = [
    "ActionAdmissionPolicy",
    "ActionAdmissionRequest",
    "AdmissionRejectionCode",
    "AdmissionResult",
    "AdmittedAction",
    "AnalysisRequest",
    "AnalysisResponse",
    "DecisionRequest",
    "DecisionResponse",
    "PortFailure",
    "PortFailureCode",
    "PortResult",
    "RejectedAction",
    "Success",
    "TrackGainPermission",
    "admit_action",
    "validate_analysis_response",
    "validate_decision_response",
    "CandidateIdentity",
    "ExecutionRequest",
    "ExecutionWorkspace",
    "StemBinding",
    "ProcessingPlan",
    "MetadataReady",
    "PreflightCode",
    "PreflightRejected",
    "PreflightResult",
    "preflight_execution",
    "ArtifactProcessingEvidence",
    "ProcessedCandidate",
    "ProcessingFailure",
    "ProcessingOutcome",
    "validate_processed_candidate",
]
PROTOCOLS = ("AudioAnalyzer", "DecisionController", "AudioProcessor")
LEGACY["ports"] = PROTOCOLS


def test_root_exports_are_unchanged_and_identical_to_component_objects():
    root = importlib.import_module(APP)
    assert root.__all__ == BASELINE_ROOT_ALL
    for name in BASELINE_ROOT_ALL:
        package, _owner = _canonical_owner(name)
        assert getattr(root, name) is getattr(package, name), name


def test_inventory_covers_every_public_definition_exactly_once():
    owned = [name for owners in CANONICAL.values() for names in owners.values() for name in names]
    assert len(owned) == len(set(owned)) == 36
    assert set(owned) == set(BASELINE_ROOT_ALL) | set(PROTOCOLS)
    assert {component: len(_module(component).__all__) for component in CANONICAL} == {
        "shared": 4,
        "observation": 4,
        "decision": 4,
        "admission": 8,
        "execution": 16,
    }


@pytest.mark.parametrize("legacy", sorted(LEGACY))
def test_every_legacy_path_exports_exactly_its_baseline_definitions_as_canonical_objects(legacy):
    module = _module(legacy)
    assert set(module.__all__) == set(LEGACY[legacy]), legacy
    for name in LEGACY[legacy]:
        package, _owner = _canonical_owner(name)
        assert getattr(module, name) is getattr(package, name), f"{legacy}.{name}"


def test_legacy_ports_package_keeps_its_submodules_and_protocol_identity():
    ports = _module("ports")
    assert ports.analysis.AudioAnalyzer is _module("observation").AudioAnalyzer
    assert ports.decision.DecisionController is _module("decision").DecisionController
    assert ports.processing.AudioProcessor is _module("execution").AudioProcessor
    for name in PROTOCOLS:
        assert typing.Protocol in getattr(ports, name).__mro__, name


def test_infrastructure_audio_facade_and_leaves_are_unchanged():
    audio = importlib.import_module("autonomous_mixing.infrastructure.audio")
    dsp = importlib.import_module("autonomous_mixing.infrastructure.audio.dsp")
    verification = importlib.import_module("autonomous_mixing.infrastructure.audio.verification")
    assert sorted(audio.__all__) == [
        "ArtifactVerificationError",
        "VerificationFailureCode",
        "VerifiedAudio",
        "apply_absolute_gain",
        "sum_stems",
        "verify_resolved_wav_artifact",
        "verify_wav_artifact",
    ]
    for name in ("apply_absolute_gain", "sum_stems"):
        assert getattr(audio, name) is getattr(dsp, name)
    for name in (
        "ArtifactVerificationError",
        "VerificationFailureCode",
        "VerifiedAudio",
        "verify_wav_artifact",
        "verify_resolved_wav_artifact",
    ):
        assert getattr(audio, name) is getattr(verification, name)
    assert verification.WAV_CONTAINERS == ("WAV", "WAVEX", "RF64")
