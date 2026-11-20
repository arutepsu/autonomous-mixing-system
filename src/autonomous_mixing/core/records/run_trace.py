"""Typed operation records and ordered trace/acceptance prefixes for a controlled run."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from autonomous_mixing.core.records.acceptance import (
    AcceptanceAbsent,
    AcceptanceAuthorityUnknown,
    AcceptanceCommitted,
    AcceptanceCommitUnknown,
    AcceptanceEvent,
    AcceptanceFound,
    AcceptanceNotCommitted,
    AcceptanceQuery,
    AcceptedReference,
)
from autonomous_mixing.core.records.actions import ExecutionReceipt
from autonomous_mixing.core.records.admission import AdmittedAction, MetadataReady, PreflightRejected, RejectedAction
from autonomous_mixing.core.records.analysis import AnalysisRequest, AnalysisResponse
from autonomous_mixing.core.records.budgets import BudgetBlock, BudgetUsage
from autonomous_mixing.core.records.coordinator_runtime import (
    DiagnosisRequest,
    RenderPreparationFailure,
    RunRenderRequest,
    RunRequest,
)
from autonomous_mixing.core.records.daw_control import (
    DawApplyPermitted,
    DawApplyRejected,
    DawApplyRequest,
    DawInspection,
    DawInspectRequest,
    DawNoWrite,
    DawWriteConfirmed,
    DawWriteOffTarget,
    DawWriteRefused,
    DawWriteUncertain,
)
from autonomous_mixing.core.records.daw_correction import (
    DawContinuationFinished,
    DawContinuationStarted,
    DawCorrectionSuspended,
    DawPlanReady,
    DawStateQuery,
)
from autonomous_mixing.core.records.daw_export import (
    DawContinuitySnapshot,
    DawExportAwaiting,
    DawExportPreparationFailure,
    DawExportRequest,
    DawFilesVerified,
    DawImportRejected,
    DawImportRequest,
    DawImportVerified,
    DawStateAnchor,
)
from autonomous_mixing.core.records.daw_recovery import (
    DawControlState,
    DawLineageQuery,
    DawReconcileRequest,
    DawReconciliation,
    DawRecoveryDecision,
    DawRecoveryLineage,
    DawRecoverySnapshot,
    DawResolutionRecorded,
    DawResolutionRefused,
    DawResolutionRequest,
    DawRestoreConfirmed,
    DawRestoreMismatch,
    DawRestoreNoWrite,
    DawRestorePermitted,
    DawRestoreRefused,
    DawRestoreRejected,
    DawRestoreRequest,
    DawRestoreTarget,
    DawRestoreUncertain,
)
from autonomous_mixing.core.records.daw_rollback import DawRollbackRecord
from autonomous_mixing.core.records.decision import DecisionResponse, Situation
from autonomous_mixing.core.records.decision_gate import (
    DeclineAction,
    EvidenceBlocked,
    InvalidResponse,
    KeepState,
    ReadyForAdmission,
    RequestContext,
)
from autonomous_mixing.core.records.diagnosis import DiagnosisReport
from autonomous_mixing.core.records.feedback import DecisionFeedback
from autonomous_mixing.core.records.identifiers import AttemptId, DecisionCallId, ExecutionId, RunEventId, RunId
from autonomous_mixing.core.records.integrity import StateIntegrityReport, StateIntegrityRequest
from autonomous_mixing.core.records.invariants import (
    require_count,
    require_instance,
    require_text,
    require_tuple_of,
    require_utc,
)
from autonomous_mixing.core.records.reconciliation import ReconciliationResult
from autonomous_mixing.core.records.rendering import RenderedCandidate, RenderFailure
from autonomous_mixing.core.records.results import PortFailure
from autonomous_mixing.core.records.run import CorrectionAttempt, FinalProjectState, RunOutcome, RunRecord, RunState
from autonomous_mixing.core.records.run_restart import (
    DawLatestAnchorQuery,
    DawLatestState,
    DawStartRefused,
    OperatorRecoveryAuthorization,
    RunInterrupted,
)
from autonomous_mixing.core.records.termination import TerminationReason
from autonomous_mixing.core.records.verification import VerificationResult
from autonomous_mixing.core.records.verification_assessment import VerificationAssessment


class OperationKind(Enum):
    ANALYZE = "analyze"
    DIAGNOSE = "diagnose"
    DECIDE = "decide"
    RENDER = "render"
    QUERY_ACCEPTANCE = "query_acceptance"
    INSPECT_STATE = "inspect_state"
    # ADR-035: the DAW execution path
    DAW_INSPECT = "daw_inspect"
    DAW_APPLY = "daw_apply"
    DAW_RECOVERY = "daw_recovery"
    DAW_STATE = "daw_state"
    EXPORT_PREPARE = "export_prepare"
    EXPORT_IMPORT = "export_import"


StageRequest = (
    AnalysisRequest
    | DiagnosisRequest
    | Situation
    | RunRenderRequest
    | AcceptanceQuery
    | StateIntegrityRequest
    | DawInspectRequest
    | DawApplyRequest
    | DawReconcileRequest
    | DawRestoreRequest
    | DawResolutionRequest
    | DawStateQuery
    | DawLineageQuery
    | DawLatestAnchorQuery
    | DawExportRequest
    | DawImportRequest
)

_REQUEST_TYPES = {
    OperationKind.ANALYZE: AnalysisRequest,
    OperationKind.DIAGNOSE: DiagnosisRequest,
    OperationKind.DECIDE: Situation,
    OperationKind.RENDER: RunRenderRequest,
    OperationKind.QUERY_ACCEPTANCE: AcceptanceQuery,
    OperationKind.INSPECT_STATE: StateIntegrityRequest,
    OperationKind.DAW_INSPECT: DawInspectRequest,
    OperationKind.DAW_APPLY: DawApplyRequest,
    OperationKind.DAW_RECOVERY: (DawReconcileRequest, DawRestoreRequest, DawResolutionRequest),
    # lineage queries: Increment 4 (ADR-038); the run-start latest-anchor query: Increment 5 (ADR-039)
    OperationKind.DAW_STATE: (DawStateQuery, DawLineageQuery, DawLatestAnchorQuery),
    OperationKind.EXPORT_PREPARE: DawExportRequest,
    OperationKind.EXPORT_IMPORT: DawImportRequest,
}
_DAW_KINDS = frozenset({OperationKind.DAW_INSPECT, OperationKind.DAW_APPLY, OperationKind.DAW_RECOVERY,
                        OperationKind.DAW_STATE, OperationKind.EXPORT_PREPARE, OperationKind.EXPORT_IMPORT})  # fmt: skip


def _daw_request_project(request):
    """The project a DAW-path request names, or None when it names none (resolutions, state queries)."""
    if isinstance(request, (DawInspectRequest, DawReconcileRequest, DawRestoreRequest)):
        return request.binding.project_id
    if isinstance(request, DawApplyRequest):
        return request.admitted.action.project_id
    if isinstance(request, DawExportRequest):
        return request.project_id
    if isinstance(request, DawImportRequest):
        return request.awaiting.request.project_id
    return None


@dataclass(frozen=True)
class DispatchIntent:
    operation: OperationKind
    request: StageRequest
    accepted: AcceptedReference
    call_id: DecisionCallId | None
    attempt_id: AttemptId | None
    execution_id: ExecutionId | None

    def __post_init__(self) -> None:
        require_instance(self.operation, OperationKind, "DispatchIntent.operation")
        require_instance(self.request, _REQUEST_TYPES[self.operation], "DispatchIntent.request")
        require_instance(self.accepted, AcceptedReference, "DispatchIntent.accepted")
        for value, kind, name in (
            (self.call_id, DecisionCallId, "call_id"),
            (self.attempt_id, AttemptId, "attempt_id"),
            (self.execution_id, ExecutionId, "execution_id"),
        ):
            if value is not None:
                require_instance(value, kind, f"DispatchIntent.{name}")
        if self.operation is OperationKind.DECIDE:
            if self.call_id is None or self.attempt_id is not None or self.execution_id is not None:
                raise ValueError("decision intent requires a call ID and no attempt or execution IDs")
        if self.operation in _DAW_KINDS:
            self._check_daw()
            return
        if self.operation is OperationKind.RENDER:
            if self.call_id is None or self.attempt_id is None or self.execution_id != self.request.plan.execution_id:
                raise ValueError("render intent requires call, attempt and assigned execution IDs")
            state = self.request.before.state
        elif self.operation is OperationKind.QUERY_ACCEPTANCE:
            return
        else:
            state = self.request.state
        if state.project_id != self.accepted.state.project_id:
            raise ValueError("dispatch request and accepted reference name different projects")
        if self.operation in (OperationKind.DECIDE, OperationKind.RENDER) and state != self.accepted.state:
            raise ValueError("decision/render request must use exactly the accepted state")

    def _check_daw(self) -> None:
        """DAW-path intents (ADR-035): same project; a write or export carries its call, attempt and execution."""
        project = _daw_request_project(self.request)
        if project is not None and project != self.accepted.state.project_id:
            raise ValueError("dispatch request and accepted reference name different projects")
        if self.operation is OperationKind.DAW_APPLY:
            action = self.request.admitted.action
            if self.call_id is None or self.attempt_id is None or self.execution_id != self.request.execution_id:
                raise ValueError("DAW apply intent requires call, attempt and the request's execution IDs")
            if action.expected_revision != self.accepted.state.revision:
                raise ValueError("DAW apply intent must act on exactly the accepted state")
        elif self.operation is OperationKind.DAW_RECOVERY:  # Increment 4 (ADR-038): always one attempt's recovery
            if self.call_id is None or self.attempt_id is None or self.execution_id is None:
                raise ValueError("DAW recovery intent requires call, attempt and execution IDs")
            if isinstance(self.request, DawRestoreRequest) and (
                self.request.authorization.execution_id != self.execution_id
            ):
                raise ValueError("a restoration intent names its authorization's execution")
        elif self.operation in (OperationKind.EXPORT_PREPARE, OperationKind.EXPORT_IMPORT):
            export = self.request if isinstance(self.request, DawExportRequest) else self.request.awaiting.request
            if self.call_id is None or self.attempt_id is None or self.execution_id != export.execution_id:
                raise ValueError("export intent requires call, attempt and the export's execution IDs")


@dataclass(frozen=True)
class DispatchSkipped:
    reason: TerminationReason
    explanation: str

    def __post_init__(self) -> None:
        require_instance(self.reason, TerminationReason, "DispatchSkipped.reason")
        require_text(self.explanation, "DispatchSkipped.explanation")
        if self.reason not in (
            TerminationReason.USER_CANCELLED,
            TerminationReason.ELAPSED_TIME_LIMIT,
            TerminationReason.RECORDING_FAILED,
            TerminationReason.RUNTIME_CONTRACT_FAILED,
        ):
            raise ValueError("reason cannot describe a skipped dispatch")


@dataclass(frozen=True)
class InvalidPortResponse:
    type_name: str
    explanation: str

    def __post_init__(self) -> None:
        require_text(self.type_name, "InvalidPortResponse.type_name")
        require_text(self.explanation, "InvalidPortResponse.explanation")


OperationOutcome = (
    AnalysisResponse
    | DiagnosisReport
    | DecisionResponse
    | PortFailure
    | RenderedCandidate
    | RenderFailure
    | RenderPreparationFailure
    | AcceptanceFound
    | AcceptanceAbsent
    | AcceptanceAuthorityUnknown
    | StateIntegrityReport
    | DispatchSkipped
    | InvalidPortResponse
    | DawInspection
    | DawWriteRefused
    | DawWriteConfirmed
    | DawWriteOffTarget
    | DawWriteUncertain
    | DawReconciliation
    | DawRestoreConfirmed
    | DawRestoreMismatch
    | DawRestoreRefused
    | DawRestoreUncertain
    | DawResolutionRecorded
    | DawResolutionRefused
    | DawStateAnchor
    | DawContinuitySnapshot
    | DawRecoveryLineage
    | DawRecoverySnapshot
    | DawLatestState
    | DawExportAwaiting
    | DawExportPreparationFailure
    | DawFilesVerified
    | DawImportRejected
)

_RESPONSE_TYPES = {
    OperationKind.ANALYZE: (AnalysisResponse, PortFailure),
    OperationKind.DIAGNOSE: (DiagnosisReport, PortFailure),
    OperationKind.DECIDE: (DecisionResponse, PortFailure),
    OperationKind.RENDER: (RenderedCandidate, RenderFailure, RenderPreparationFailure),
    OperationKind.QUERY_ACCEPTANCE: (AcceptanceFound, AcceptanceAbsent, AcceptanceAuthorityUnknown),
    OperationKind.INSPECT_STATE: (StateIntegrityReport,),
    OperationKind.DAW_INSPECT: (DawInspection, PortFailure),
    OperationKind.DAW_APPLY: (DawWriteRefused, DawWriteConfirmed, DawWriteOffTarget, DawWriteUncertain),
    OperationKind.DAW_RECOVERY: (
        DawReconciliation,
        PortFailure,
        DawRestoreConfirmed,
        DawRestoreMismatch,
        DawRestoreRefused,
        DawRestoreUncertain,
        DawResolutionRecorded,
        DawResolutionRefused,
    ),
    OperationKind.DAW_STATE: (
        DawStateAnchor,
        DawContinuitySnapshot,
        DawRecoveryLineage,
        DawRecoverySnapshot,
        DawLatestState,
        PortFailure,
    ),  # fmt: skip
    OperationKind.EXPORT_PREPARE: (DawExportAwaiting, DawExportPreparationFailure),
    OperationKind.EXPORT_IMPORT: (DawFilesVerified, DawImportRejected),
}


@dataclass(frozen=True)
class OperationObserved:
    intent_id: RunEventId
    operation: OperationKind
    outcome: OperationOutcome

    def __post_init__(self) -> None:
        require_instance(self.intent_id, RunEventId, "OperationObserved.intent_id")
        require_instance(self.operation, OperationKind, "OperationObserved.operation")
        require_instance(
            self.outcome,
            (*_RESPONSE_TYPES[self.operation], DispatchSkipped, InvalidPortResponse),
            "OperationObserved.outcome",
        )


@dataclass(frozen=True)
class TerminalSnapshot:
    run: RunRecord
    authoritative: AcceptedReference | None
    verified: AcceptedReference | None

    def __post_init__(self) -> None:
        require_instance(self.run, RunRecord, "TerminalSnapshot.run")
        if self.run.state is not RunState.FINISHED:
            raise ValueError("terminal snapshot requires a FINISHED run")
        for reference in (self.authoritative, self.verified):
            if reference is not None:
                require_instance(reference, AcceptedReference, "terminal reference")
                if reference.state.project_id != self.run.project_id:
                    raise ValueError("terminal reference names another project")
        if self.verified is not None and self.verified != self.authoritative:
            raise ValueError("verified reference must equal the authoritative reference")
        if self.run.final_project_state is FinalProjectState.UNKNOWN:
            if self.verified is not None:
                raise ValueError("UNKNOWN final state cannot carry a verified reference")
        elif self.verified is None:
            raise ValueError("known final state requires a verified reference")
        if self.run.outcome is RunOutcome.SUCCEEDED and self.verified is None:
            raise ValueError("SUCCEEDED requires a verified reference")
        if self.verified is not None:
            if self.run.final_project_state is FinalProjectState.UNCHANGED and self.verified.sequence != 0:
                raise ValueError("UNCHANGED cannot conceal committed accepted edits")
            if (
                self.run.final_project_state is FinalProjectState.ACCEPTED_EDITS_RETAINED
                and self.verified.sequence == 0
            ):
                raise ValueError("ACCEPTED_EDITS_RETAINED requires a committed accepted edit")


RunTracePayload = (
    RunRequest
    | DispatchIntent
    | OperationObserved
    | InvalidResponse
    | KeepState
    | RequestContext
    | DeclineAction
    | EvidenceBlocked
    | ReadyForAdmission
    | AdmittedAction
    | RejectedAction
    | MetadataReady
    | PreflightRejected
    | BudgetBlock
    | ExecutionReceipt
    | CorrectionAttempt
    | VerificationAssessment
    | VerificationResult
    | AcceptanceEvent
    | AcceptanceCommitted
    | AcceptanceNotCommitted
    | AcceptanceCommitUnknown
    | ReconciliationResult
    | DecisionFeedback
    | TerminalSnapshot
    | DawCorrectionSuspended
    # Increment 2 (ADR-036): the DAW preflight, the journal control state and the capability-gate verdict
    | DawPlanReady
    | DawControlState
    | DawApplyPermitted
    | DawNoWrite
    | DawApplyRejected
    # Increment 3 (ADR-037): the continuation, its assessment and its completion evidence
    | DawContinuationStarted
    | DawImportVerified
    | DawImportRejected
    | DawContinuationFinished
    # Increment 4 (ADR-038): the recovery decision, the journaled restore target and a receipt-less rollback
    | DawRecoveryDecision
    | DawRestoreTarget
    | DawRestorePermitted
    | DawRestoreNoWrite
    | DawRestoreRejected
    | DawRollbackRecord
    # Increment 5 (ADR-039): a refused DAW start and the classification of an interrupted stored run
    | DawStartRefused
    | RunInterrupted
    # Increment 6 (ADR-040): the operator's explicit authorization of one recovery operation
    | OperatorRecoveryAuthorization
)


@dataclass(frozen=True)
class RunTraceEvent:
    event_id: RunEventId
    sequence: int
    run_id: RunId
    created_at: datetime
    usage: BudgetUsage
    payload: RunTracePayload

    def __post_init__(self) -> None:
        require_instance(self.event_id, RunEventId, "RunTraceEvent.event_id")
        require_count(self.sequence, "RunTraceEvent.sequence", minimum=1)
        require_instance(self.run_id, RunId, "RunTraceEvent.run_id")
        require_utc(self.created_at, "RunTraceEvent.created_at")
        require_instance(self.usage, BudgetUsage, "RunTraceEvent.usage")
        require_instance(self.payload, RunTracePayload, "RunTraceEvent.payload")
        payload = self.payload
        run_id = None
        if isinstance(
            payload,
            (RunRequest, DecisionFeedback, CorrectionAttempt, AcceptanceEvent, DawCorrectionSuspended,
             DawContinuationStarted, DawContinuationFinished),
        ):  # fmt: skip
            run_id = payload.run_id
        elif isinstance(payload, AcceptanceCommitted):
            run_id = payload.event.run_id
        elif isinstance(payload, TerminalSnapshot):
            run_id = payload.run.run_id
            if payload.run.usage != self.usage:
                raise ValueError("terminal event usage differs from the snapshot")
        elif isinstance(payload, (DawRollbackRecord, RunInterrupted, OperatorRecoveryAuthorization)):
            run_id = payload.run_id
        elif isinstance(payload, DispatchIntent):
            if isinstance(payload.request, (Situation, AcceptanceQuery)):
                run_id = payload.request.run_id
            if isinstance(payload.request, DawRestoreRequest) and (
                payload.request.authorization.budget_grant.usage_after_charge != self.usage
            ):
                raise ValueError("a restoration intent's usage is exactly its authorization's recovery charge")
        if run_id is not None and run_id != self.run_id:
            raise ValueError("trace payload belongs to a different run")


def _require_usage_progress(current: BudgetUsage, previous: BudgetUsage) -> None:
    for field in ("decision_calls", "attempts", "render_calls", "recovery_operations", "elapsed_seconds", "daw_writes"):
        if getattr(current, field) < getattr(previous, field):
            raise ValueError(f"trace usage decreases in {field}")


class StoreDurability(Enum):
    """What the store that produced a snapshot can survive (ADR-039). Independent of event acknowledgement."""

    NON_DURABLE = "non_durable"  # the in-memory journals: nothing survives the process
    LOCAL_SQLITE_PROCESS_CRASH = "local_sqlite_process_crash"  # subprocess-crash evidence only; no power-loss claim


@dataclass(frozen=True)
class RunJournalSnapshot:
    run_id: RunId
    initial: AcceptedReference
    events: tuple[RunTraceEvent, ...]
    acceptances: tuple[AcceptanceCommitted, ...]
    durability: StoreDurability = StoreDurability.NON_DURABLE  # Increment 5 (ADR-039): the producing store's

    def __post_init__(self) -> None:
        require_instance(self.run_id, RunId, "RunJournalSnapshot.run_id")
        require_instance(self.durability, StoreDurability, "RunJournalSnapshot.durability")
        require_instance(self.initial, AcceptedReference, "RunJournalSnapshot.initial")
        require_tuple_of(self.events, RunTraceEvent, "RunJournalSnapshot.events", nonempty=False, distinct=False)
        require_tuple_of(
            self.acceptances, AcceptanceCommitted, "RunJournalSnapshot.acceptances", nonempty=False, distinct=False
        )
        if self.initial.sequence != 0:
            raise ValueError("journal initial acceptance sequence must be zero")
        intents = {}
        observed = set()
        seen = set()
        previous = None
        for position, event in enumerate(self.events, 1):
            if event.run_id != self.run_id or event.sequence != position or event.event_id in seen:
                raise ValueError("trace must be this run's exact prefix with unique event IDs")
            seen.add(event.event_id)
            if previous is not None:
                _require_usage_progress(event.usage, previous)
            previous = event.usage
            payload = event.payload
            if isinstance(payload, DispatchIntent):
                intents[event.event_id] = payload
            elif isinstance(payload, OperationObserved):
                intent = intents.get(payload.intent_id)
                if intent is None or intent.operation is not payload.operation or payload.intent_id in observed:
                    raise ValueError("observation must complete exactly one earlier matching intent")
                observed.add(payload.intent_id)
            elif isinstance(payload, TerminalSnapshot) and position != len(self.events):
                raise ValueError("terminal snapshot must be the final trace event")
        reference = self.initial
        acceptance_ids = set()
        for committed in self.acceptances:
            event = committed.event
            if event.run_id != self.run_id or event.acceptance_id in acceptance_ids or event.expected != reference:
                raise ValueError("acceptances must form this run's complete unique chain from initial")
            acceptance_ids.add(event.acceptance_id)
            reference = committed.accepted

    @property
    def accepted(self) -> AcceptedReference:
        return self.acceptances[-1].accepted if self.acceptances else self.initial


@dataclass(frozen=True)
class TraceAppended:
    event: RunTraceEvent

    def __post_init__(self) -> None:
        require_instance(self.event, RunTraceEvent, "TraceAppended.event")


@dataclass(frozen=True)
class TraceNotAppended:
    event_id: RunEventId
    explanation: str

    def __post_init__(self) -> None:
        require_instance(self.event_id, RunEventId, "TraceNotAppended.event_id")
        require_text(self.explanation, "TraceNotAppended.explanation")


@dataclass(frozen=True)
class TraceAppendUnknown:
    event_id: RunEventId
    explanation: str

    def __post_init__(self) -> None:
        require_instance(self.event_id, RunEventId, "TraceAppendUnknown.event_id")
        require_text(self.explanation, "TraceAppendUnknown.explanation")


TraceAppendResult = TraceAppended | TraceNotAppended | TraceAppendUnknown
