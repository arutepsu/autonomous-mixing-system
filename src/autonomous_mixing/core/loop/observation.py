"""Observation responsibilities of the controlled serial coordinator: state-integrity inspections, analysis
and diagnosis calls through their gates, the root bindings for a state, recorded state evidence and the
comparison-baseline readiness check. Port responses are validated before they reach progress.

Analysis uses the run's explicit ``analysis_definitions`` and the roots the caller supplies for exactly the
state's current members (``analysis_bindings``); no job is derived from criteria and no root is guessed.
"""

from collections.abc import Callable
from dataclasses import replace

from autonomous_mixing.core.loop.runtime import _invalid, _observations_at, _root_key, gate, observed
from autonomous_mixing.core.ports.analysis import AudioAnalyzer, validate_analysis_response
from autonomous_mixing.core.ports.coordinator_runtime import (
    DiagnosisSource,
    IdentitySource,
    RunClock,
    validate_diagnosis_response,
)
from autonomous_mixing.core.ports.integrity import StateIntegrityInspector, validate_state_integrity_response
from autonomous_mixing.core.ports.run_journal import RunJournal
from autonomous_mixing.core.records.analysis import AnalysisRequest, AnalysisResponse
from autonomous_mixing.core.records.budgets import BudgetOperation
from autonomous_mixing.core.records.coordinator_progress import RuntimeProgress, Stop
from autonomous_mixing.core.records.coordinator_runtime import DiagnosisRequest
from autonomous_mixing.core.records.diagnosis import DiagnosisReport
from autonomous_mixing.core.records.identifiers import ArtifactId, ConditionId
from autonomous_mixing.core.records.integrity import (
    ArtifactRootBinding,
    IntegrityStatus,
    StateIntegrityReport,
    StateIntegrityRequest,
)
from autonomous_mixing.core.records.invariants import require_instance, require_tuple_of
from autonomous_mixing.core.records.observation import Observation
from autonomous_mixing.core.records.project import ProjectState
from autonomous_mixing.core.records.results import PortFailure, Success
from autonomous_mixing.core.records.run_trace import OperationKind
from autonomous_mixing.core.records.termination import TerminationReason
from autonomous_mixing.core.records.verification import AcceptanceCriteria, DeltaGuard
from autonomous_mixing.core.records.verification_evidence import MatchedEvidence, MeasurementDefinition, StateEvidence
from autonomous_mixing.core.verification.evidence import match_evidence


def bindings_for(progress: RuntimeProgress, state: ProjectState) -> tuple[ArtifactRootBinding, ...]:
    ids = dict.fromkeys(
        (a.artifact_id for a in (*state.audio.artifacts, *(g.input_artifact for g in state.processing.track_gains)))
    )
    return tuple((ArtifactRootBinding(i, _root_key(progress, i, progress.request.output_root_key)) for i in ids))


def analysis_bindings(
    state: ProjectState,
    known: tuple[ArtifactRootBinding, ...],
    produced: tuple[ArtifactRootBinding, ...] = (),
) -> tuple[ArtifactRootBinding, ...]:
    """The supplied bindings of exactly ``state``'s current members, in checkpoint order.

    ``known`` and ``produced`` are joined; an artifact ID supplied more than once (equal or conflicting root)
    is rejected, and a current member without a supplied binding raises ``ValueError``. Bindings of other
    artifacts (retained or fixed inputs) are permitted and omitted. Empty ``produced`` suits a state without
    new outputs; it is not a default root. No root is inferred from a key, path or file name.
    """
    require_instance(state, ProjectState, "state")
    supplied: dict[ArtifactId, ArtifactRootBinding] = {}
    for field, bindings in (("known", known), ("produced", produced)):
        for binding in require_tuple_of(bindings, ArtifactRootBinding, f"{field} root bindings", nonempty=False,
                                        distinct=False):  # fmt: skip
            if binding.artifact_id in supplied:
                raise ValueError(f"root binding for {binding.artifact_id} is supplied more than once")
            supplied[binding.artifact_id] = binding
    missing = [str(a.artifact_id) for a in state.audio.artifacts if a.artifact_id not in supplied]
    if missing:
        raise ValueError(f"no root binding for current artifacts: {', '.join(missing)}")
    return tuple(supplied[a.artifact_id] for a in state.audio.artifacts)


def inspect(
    progress: RuntimeProgress,
    state: ProjectState,
    budget: BudgetOperation | None,
    *,
    clock: RunClock,
    identities: IdentitySource,
    integrity_inspector: StateIntegrityInspector,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[
    RuntimeProgress,
    tuple[IntegrityStatus | None, StateIntegrityReport | None, StateIntegrityRequest, Stop | None, bool],
]:
    """Returns (IntegrityStatus | None for invalid/unperformed, report | None, request, stop, performed)."""
    integrity_request = StateIntegrityRequest(state, bindings_for(progress, state))
    progress, (intent_id, _, stop, _) = gate(
        progress,
        OperationKind.INSPECT_STATE,
        lambda charged: integrity_request,
        budget,
        clock=clock,
        identities=identities,
        journal=journal,
        on_progress=on_progress,
    )
    if intent_id is None or stop:
        return (progress, (None, None, integrity_request, stop, False))
    response = integrity_inspector.inspect(integrity_request)
    try:
        validate_state_integrity_response(integrity_request, response)
    except ValueError as error:
        progress, stop = observed(
            progress,
            intent_id,
            OperationKind.INSPECT_STATE,
            _invalid(response, error),
            clock=clock,
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, (None, None, integrity_request, stop, True))
    progress, stop = observed(
        progress,
        intent_id,
        OperationKind.INSPECT_STATE,
        response,
        clock=clock,
        identities=identities,
        journal=journal,
        on_progress=on_progress,
    )
    return (progress, (response.status, response, integrity_request, stop, True))


def analyze(
    progress: RuntimeProgress,
    state: ProjectState,
    failure: TerminationReason,
    *,
    bindings: tuple[ArtifactRootBinding, ...],
    analyzer: AudioAnalyzer,
    clock: RunClock,
    identities: IdentitySource,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, tuple[tuple[Observation, ...] | None, Stop | None]]:
    """Returns (observations | None, stop). ``bindings`` are the roots of exactly ``state``'s current members."""
    configuration = progress.request.configuration
    analysis_request = AnalysisRequest(
        state, configuration.scope, configuration.analysis_spec, progress.request.analysis_definitions, bindings
    )
    progress, (intent_id, _, stop, _) = gate(
        progress,
        OperationKind.ANALYZE,
        lambda charged: analysis_request,
        None,
        clock=clock,
        identities=identities,
        journal=journal,
        on_progress=on_progress,
    )
    if stop:
        return (progress, (None, stop))
    result = analyzer.analyze(analysis_request)
    if isinstance(result, PortFailure):
        progress, stop = observed(
            progress,
            intent_id,
            OperationKind.ANALYZE,
            result,
            clock=clock,
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, (None, stop or (failure, f"analysis failed: {result.code.value}: {result.message}")))
    try:
        require_instance(result, Success, "analysis result")
        require_instance(result.value, AnalysisResponse, "analysis response")
        validate_analysis_response(analysis_request, result.value)
    except ValueError as error:
        progress, stop = observed(
            progress,
            intent_id,
            OperationKind.ANALYZE,
            _invalid(result, error),
            clock=clock,
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
        return (
            progress,
            (None, stop or (TerminationReason.RUNTIME_CONTRACT_FAILED, f"invalid analysis response: {error}")),
        )
    progress, stop = observed(
        progress,
        intent_id,
        OperationKind.ANALYZE,
        result.value,
        clock=clock,
        identities=identities,
        journal=journal,
        on_progress=on_progress,
    )
    progress = replace(
        progress,
        observations=tuple((b for b in progress.observations if b.state.revision != state.revision))
        + (StateEvidence(state, result.value.observations, progress.request.alignment),),
    )
    on_progress(progress)
    return (progress, (result.value.observations, stop))


def diagnose(
    progress: RuntimeProgress,
    state: ProjectState,
    *,
    clock: RunClock,
    diagnosis_source: DiagnosisSource,
    identities: IdentitySource,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, tuple[DiagnosisReport | None, Stop | None]]:
    if progress.diagnosed is not None and progress.diagnosed[0] == state.revision:
        return (progress, (progress.diagnosed[1], None))
    diagnosis_request = DiagnosisRequest(
        state, progress.request.configuration.scope, _observations_at(progress, state.revision)
    )
    progress, (intent_id, _, stop, _) = gate(
        progress,
        OperationKind.DIAGNOSE,
        lambda charged: diagnosis_request,
        None,
        clock=clock,
        identities=identities,
        journal=journal,
        on_progress=on_progress,
    )
    if stop:
        return (progress, (None, stop))
    result = diagnosis_source.diagnose(diagnosis_request)
    if isinstance(result, PortFailure):
        progress, stop = observed(
            progress,
            intent_id,
            OperationKind.DIAGNOSE,
            result,
            clock=clock,
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, (None, stop or (TerminationReason.DIAGNOSIS_FAILED, f"diagnosis failed: {result.message}")))
    try:
        require_instance(result, Success, "diagnosis result")
        validate_diagnosis_response(diagnosis_request, result.value)
    except ValueError as error:
        progress, stop = observed(
            progress,
            intent_id,
            OperationKind.DIAGNOSE,
            _invalid(result, error),
            clock=clock,
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
        return (
            progress,
            (None, stop or (TerminationReason.RUNTIME_CONTRACT_FAILED, f"invalid diagnosis response: {error}")),
        )
    progress, stop = observed(
        progress,
        intent_id,
        OperationKind.DIAGNOSE,
        result.value,
        clock=clock,
        identities=identities,
        journal=journal,
        on_progress=on_progress,
    )
    progress = replace(progress, diagnosed=(state.revision, result.value))
    on_progress(progress)
    return (progress, (result.value, stop))


def evidence_for(progress: RuntimeProgress, state: ProjectState) -> StateEvidence:
    return StateEvidence(state, _observations_at(progress, state.revision), progress.request.alignment)


def _missing_baselines(
    criteria: AcceptanceCriteria, definitions: dict[ConditionId, MeasurementDefinition], initial_evidence: StateEvidence
) -> list[str]:
    """Conditions whose comparison baseline (the initial state for the first attempt) cannot be matched."""
    comparisons = [*criteria.improvements, *(g for g in criteria.guards if isinstance(g, DeltaGuard))]
    missing = []
    for condition in comparisons:
        match = match_evidence(definitions[condition.condition_id], initial_evidence)
        if not isinstance(match, MatchedEvidence):
            missing.append(str(condition.condition_id))
    return missing
