"""The controlled serial coordinator: one run of OBSERVE -> DIAGNOSE -> DECIDE -> ACT -> VERIFY.

``run`` composes the existing pure stages (budget check, decision gate, admission, capability view,
preflight, candidate derivation, evidence assessment, verification routing, acceptance validation and
reconciliation) over the injected ports. It is NON_DURABLE and cooperative about deadlines: time and
cancellation are checked at dispatch boundaries, not enforced by interrupting a port.

Recording rules: every dispatch is preceded by an acknowledged ``DispatchIntent``. A confirmed unwritten
intent dispatches nothing and charges nothing; an unknown acknowledgement keeps the conservative charge,
dispatches nothing and ends the run. Time and cancellation are rechecked after acknowledgement; a blocked
dispatch is recorded as ``DispatchSkipped`` and keeps its charge. Nothing is retried except retryable
UNAVAILABLE decision failures within the request's retry policy and the global budgets.

State selection: the live accepted reference advances only after a validated commit acknowledgement or a
confirmed reconciliation. A rendered but unaccepted candidate requires one recovery workflow (integrity
inspection of the saved accepted state; for an unknown commit acknowledgement, one authority query first).
Restoration is logical: the saved reference stays selected; no file is deleted or rewritten. A committed
candidate is reported as the known selected reference on the strength of the journal acknowledgement and
the renderer's evidence claims, not of a separate file inspection.

Implementation note: mutable run state is local to ``run`` (nested functions), following the core rule
that data types live in ``core.records`` and mutation stays in a function's local scope. Steps return a
``(TerminationReason, explanation)`` stop instead of raising. Unexpected exceptions propagate after one
best-effort terminal recording.
"""

from dataclasses import replace
from typing import assert_never

from autonomous_mixing.core.admission.admission import admit_action
from autonomous_mixing.core.admission.capabilities import build_action_capability_view
from autonomous_mixing.core.admission.decision_gate import assess_decision
from autonomous_mixing.core.admission.preflight import preflight_execution
from autonomous_mixing.core.loop.acceptance import validate_acceptance_event
from autonomous_mixing.core.loop.budgets import check_budget
from autonomous_mixing.core.loop.reconciliation import reconcile_acceptance
from autonomous_mixing.core.loop.transitions import derive_candidate_project_state
from autonomous_mixing.core.loop.verification_routing import route_verification
from autonomous_mixing.core.ports.acceptance import (
    validate_acceptance_commit_response,
    validate_acceptance_query_response,
)
from autonomous_mixing.core.ports.analysis import AudioAnalyzer, validate_analysis_response
from autonomous_mixing.core.ports.coordinator_runtime import (
    DiagnosisSource,
    IdentitySource,
    RunClock,
    RunRenderer,
    validate_diagnosis_response,
    validate_run_render_response,
)
from autonomous_mixing.core.ports.decision import DecisionMaker, validate_decision_response
from autonomous_mixing.core.ports.integrity import StateIntegrityInspector, validate_state_integrity_response
from autonomous_mixing.core.ports.run_journal import (
    RunJournal,
    validate_run_journal_snapshot,
    validate_trace_append_response,
)
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
from autonomous_mixing.core.records.actions import (
    Action,
    ExecutionReceipt,
    ExecutionStatus,
    RollbackReceipt,
    RollbackStatus,
)
from autonomous_mixing.core.records.admission import (
    ActionAdmissionRequest,
    AdmissionRejectionCode,
    ExecutionRequest,
    MetadataReady,
    PreflightRejected,
    RejectedAction,
    StemBinding,
)
from autonomous_mixing.core.records.analysis import AnalysisRequest, AnalysisResponse
from autonomous_mixing.core.records.budgets import BudgetBlock, BudgetOperation, BudgetUsage
from autonomous_mixing.core.records.coordinator_runtime import (
    ClockReading,
    DiagnosisRequest,
    RenderPreparationFailure,
    RunRenderRequest,
    RunRequest,
)
from autonomous_mixing.core.records.decision import DecisionResponse, ProposeAction, Situation
from autonomous_mixing.core.records.decision_gate import (
    DeclineAction,
    EvidenceBlocked,
    InvalidResponse,
    KeepState,
    ReadyForAdmission,
    RequestContext,
)
from autonomous_mixing.core.records.diagnosis import DiagnosisReport
from autonomous_mixing.core.records.feedback import (
    AbstentionFeedback,
    ActionFeedback,
    ActionFeedbackDisposition,
    ContextRequestFeedback,
    DecisionFeedback,
    DecisionHistory,
    FeedbackStage,
    InvalidResponseFeedback,
    NoChangeFeedback,
    NotDispatchedFeedback,
    ObservationFeedback,
    PortFailureFeedback,
)
from autonomous_mixing.core.records.identifiers import (
    AcceptanceId,
    ArtifactId,
    AttemptId,
    CheckpointId,
    DecisionCallId,
    ExecutionId,
    Revision,
    RunEventId,
    VerificationResultId,
)
from autonomous_mixing.core.records.integrity import (
    ArtifactRootBinding,
    IntegrityStatus,
    StateIntegrityRequest,
)
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.observation import Observation
from autonomous_mixing.core.records.project import ProjectState
from autonomous_mixing.core.records.reconciliation import ReconciliationOutcome
from autonomous_mixing.core.records.rendering import (
    CandidateIdentity,
    ExecutionWorkspace,
    RenderedCandidate,
    RenderFailure,
    RenderPlan,
)
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, Success
from autonomous_mixing.core.records.run import (
    _REASON_OUTCOMES,
    AttemptDisposition,
    CorrectionAttempt,
    FinalProjectState,
    RunOutcome,
    RunRecord,
    RunState,
)
from autonomous_mixing.core.records.run_report import RunReport
from autonomous_mixing.core.records.run_trace import (
    DispatchIntent,
    DispatchSkipped,
    InvalidPortResponse,
    OperationKind,
    OperationObserved,
    RunJournalSnapshot,
    RunTraceEvent,
    TerminalSnapshot,
    TraceAppended,
    TraceAppendUnknown,
)
from autonomous_mixing.core.records.termination import TerminationReason
from autonomous_mixing.core.records.verification import AssessmentMode, DeltaGuard
from autonomous_mixing.core.records.verification_assessment import VerificationAssessment
from autonomous_mixing.core.records.verification_evidence import MatchedEvidence, StateEvidence
from autonomous_mixing.core.records.verification_routing import VerificationRoute
from autonomous_mixing.core.verification.assessment import assess_verification
from autonomous_mixing.core.verification.evidence import match_evidence

Stop = tuple[TerminationReason, str]

# Recording failures cannot replace these reasons (attempt contract section 27, rule 1).
_PRESERVED_ON_RECORDING_FAILURE = (
    TerminationReason.ACCEPTANCE_COMMIT_UNCERTAIN,
    TerminationReason.ACCEPTED_STATE_INTEGRITY_FAILED,
    TerminationReason.ACCEPTED_STATE_INTEGRITY_UNCERTAIN,
    TerminationReason.RESTORATION_FAILED,
    TerminationReason.RESTORATION_UNCERTAIN,
    TerminationReason.REQUIRED_RECOVERY_NOT_PERFORMED,
)
_ABSTENTION_REASONS = {
    "insufficient_evidence": TerminationReason.INSUFFICIENT_EVIDENCE,
    "conflicting_constraints": TerminationReason.CONFLICTING_CONSTRAINTS,
    "no_justified_action": TerminationReason.NO_JUSTIFIED_ACTION,
}


def run(
    request: RunRequest,
    *,
    analyzer: AudioAnalyzer,
    diagnosis_source: DiagnosisSource,
    decision_maker: DecisionMaker,
    renderer: RunRenderer,
    integrity_inspector: StateIntegrityInspector,
    journal: RunJournal,
    clock: RunClock,
    identities: IdentitySource,
) -> RunReport:
    """Execute one controlled run and return its immutable NON_DURABLE report.

    The journal must be bound to ``request.run_id`` and the request's initial state at acceptance
    sequence zero, with an empty trace and acceptance history; this API does not resume runs. A journal
    that cannot confirm that, or a clock whose first reading is invalid, is a caller contract error
    (``ValueError``) raised before any operation. Unexpected collaborator or programming exceptions
    propagate after one best-effort terminal recording.
    """
    require_instance(request, RunRequest, "request")
    initial = AcceptedReference(request.initial_state, 0)
    opened = journal.snapshot()
    if not isinstance(opened, Success) or not isinstance(opened.value, RunJournalSnapshot):
        raise ValueError("the journal could not confirm its initialization for this run")
    validate_run_journal_snapshot(request.run_id, initial, opened.value)  # raises for another run/state
    if opened.value.events or opened.value.acceptances:
        raise ValueError("the journal already holds a trace or acceptances; this API does not resume a run")
    first = clock.read()
    if not isinstance(first, ClockReading):
        raise ValueError("the run clock's first reading is not a ClockReading")

    config = request.configuration
    limits = config.budget_limits
    project_id = request.initial_state.project_id
    run_id = request.run_id

    # --- local run state ---------------------------------------------------------------------------
    start = first.monotonic_seconds
    last = first
    usage = BudgetUsage(0, 0, 0, 0, 0.0)
    known: list[RunTraceEvent] = []  # acknowledged (or snapshot-confirmed) trace prefix
    unacknowledged: list[RunTraceEvent] = []
    commits: list[AcceptanceCommitted] = []
    recording_ok = True
    accepted = initial  # authoritative accepted reference, as far as established
    authority_known = True
    verified: AcceptedReference | None = None
    restored_last = False
    tokens: set[str] = set()
    history: list[DecisionFeedback] = []
    stems = {binding.track_id: binding.artifact_id for binding in request.stem_bindings}
    mix_id = request.mix_artifact_id
    roots = {binding.artifact_id: binding.root_key for binding in request.root_bindings}
    observations: dict[Revision, tuple[Observation, ...]] = {}
    definitions = {m.condition_id: m.definition for m in request.measurements}
    diagnosed: tuple[Revision, DiagnosisReport] | None = None

    # --- clock, identities and recording -------------------------------------------------------------

    def checkpoint() -> Stop | None:
        nonlocal last, usage
        reading = clock.read()
        if not isinstance(reading, ClockReading):
            return TerminationReason.RUNTIME_CONTRACT_FAILED, "the run clock returned an invalid reading"
        if reading.monotonic_seconds < last.monotonic_seconds:
            return TerminationReason.RUNTIME_CONTRACT_FAILED, "the run clock's monotonic reading decreased"
        last = reading
        usage = replace(usage, elapsed_seconds=reading.monotonic_seconds - start)
        return None

    def fresh(kind):
        """A new typed identity, or None when the source repeats or supplies an invalid token."""
        token = identities.next_token()
        if not isinstance(token, str) or not token.strip() or token in tokens:
            return None
        tokens.add(token)
        try:
            return kind(token)
        except ValueError:
            return None

    def identity_failure(what: str) -> Stop:
        return TerminationReason.RUNTIME_CONTRACT_FAILED, f"the identity source supplied an invalid or repeated {what}"

    def new_event(payload, event_usage: BudgetUsage) -> RunTraceEvent | None:
        event_id = fresh(RunEventId)
        if event_id is None:
            return None
        return RunTraceEvent(event_id, len(known) + 1, run_id, last.utc, event_usage, payload)

    def append(event: RunTraceEvent):
        """Append once; returns the validated response. Unknown outcomes are resolved by one snapshot."""
        response = journal.append(event)
        try:
            validate_trace_append_response(event, response)
        except ValueError as error:
            response = TraceAppendUnknown(event.event_id, f"invalid append response: {error}")
        if isinstance(response, TraceAppended):
            known.append(event)
            return response
        unacknowledged.append(event)
        if isinstance(response, TraceAppendUnknown):
            snapshot = journal.snapshot()
            if isinstance(snapshot, Success) and isinstance(snapshot.value, RunJournalSnapshot):
                events = snapshot.value.events
                if events[: len(known)] == tuple(known) and len(events) == len(known) + 1 and events[-1] == event:
                    known.append(event)  # recorded after all; still reported as unacknowledged
        return response

    def record(payload, event_usage: BudgetUsage | None = None) -> Stop | None:
        """Record one ordinary event; any non-acknowledgement ends ordinary work for recording failure."""
        nonlocal recording_ok
        if not recording_ok:
            return TerminationReason.RECORDING_FAILED, "an earlier trace event was not acknowledged"
        event = new_event(payload, usage if event_usage is None else event_usage)
        if event is None:
            return identity_failure("event ID")
        if isinstance(append(event), TraceAppended):
            return None
        recording_ok = False
        return TerminationReason.RECORDING_FAILED, f"trace event {event.event_id} was not acknowledged"

    # --- dispatch gates ----------------------------------------------------------------------------

    def gate(kind: OperationKind, build, budget: BudgetOperation | None, *, call=None, attempt=None, execution=None):
        """Pre-gate, intent and post-acknowledgement gate. Returns (intent_id, stage_request, stop, skipped)."""
        nonlocal usage
        stop = checkpoint()
        if stop:
            return None, None, stop, False
        if last.cancelled:
            return None, None, (TerminationReason.USER_CANCELLED, f"cancelled before the {kind.value} intent"), False
        if budget is None:
            if usage.elapsed_seconds >= limits.max_elapsed_seconds:
                return None, None, (TerminationReason.ELAPSED_TIME_LIMIT, f"no time left to {kind.value}"), False
            charged = usage
        else:
            assessment = check_budget(budget, limits=limits, usage=usage, elapsed_seconds=usage.elapsed_seconds)
            if isinstance(assessment, BudgetBlock):
                reason = TerminationReason[assessment.reason.name]
                stop = record(assessment, assessment.usage)
                blocked = (reason, f"budget blocks the next {budget.value}: {assessment.reason.value}")
                return None, None, stop or blocked, False
            charged = assessment.usage_after_charge
        stage_request = build(charged)
        if not recording_ok:
            return None, stage_request, (TerminationReason.RECORDING_FAILED, "recording failed before dispatch"), False
        intent = DispatchIntent(kind, stage_request, accepted, call, attempt, execution)
        event = new_event(intent, charged)
        if event is None:
            return None, stage_request, identity_failure("intent event ID"), False
        response = append(event)
        if not isinstance(response, TraceAppended):
            _set_recording_failed()
            if isinstance(response, TraceAppendUnknown):
                usage = charged  # the intent may exist: keep the conservative charge
            return (
                None,
                stage_request,
                (TerminationReason.RECORDING_FAILED, f"{kind.value} intent unacknowledged"),
                False,
            )
        usage = charged
        stop = checkpoint()
        reason = None
        if stop:
            reason, explanation = stop
        elif last.cancelled:
            reason, explanation = TerminationReason.USER_CANCELLED, f"cancelled after the {kind.value} intent"
        elif usage.elapsed_seconds >= limits.max_elapsed_seconds:
            reason, explanation = TerminationReason.ELAPSED_TIME_LIMIT, f"time ran out after the {kind.value} intent"
        if reason is not None:
            stop = record(OperationObserved(event.event_id, kind, DispatchSkipped(reason, explanation)))
            # A recording failure outranks the cancellation or limit (section 27, rule 1).
            return event.event_id, stage_request, stop or (reason, explanation), True
        return event.event_id, stage_request, None, False

    def _set_recording_failed() -> None:
        nonlocal recording_ok
        recording_ok = False

    def observed(intent_id: RunEventId, kind: OperationKind, outcome) -> Stop | None:
        clock_stop = checkpoint()
        stop = record(OperationObserved(intent_id, kind, outcome))
        return clock_stop or stop

    # --- stages ------------------------------------------------------------------------------------

    def bindings_for(state: ProjectState) -> tuple[ArtifactRootBinding, ...]:
        ids = dict.fromkeys(
            a.artifact_id for a in (*state.audio.artifacts, *(g.input_artifact for g in state.processing.track_gains))
        )
        return tuple(ArtifactRootBinding(i, roots.get(i, request.output_root_key)) for i in ids)

    def inspect(state: ProjectState, budget: BudgetOperation | None):
        """Returns (IntegrityStatus | None for invalid/unperformed, report | None, request, stop, performed)."""
        integrity_request = StateIntegrityRequest(state, bindings_for(state))
        intent_id, _, stop, _ = gate(OperationKind.INSPECT_STATE, lambda charged: integrity_request, budget)
        if intent_id is None or stop:
            return None, None, integrity_request, stop, False
        response = integrity_inspector.inspect(integrity_request)
        try:
            validate_state_integrity_response(integrity_request, response)
        except ValueError as error:
            stop = observed(intent_id, OperationKind.INSPECT_STATE, _invalid(response, error))
            return None, None, integrity_request, stop, True
        stop = observed(intent_id, OperationKind.INSPECT_STATE, response)
        return response.status, response, integrity_request, stop, True

    def analyze(state: ProjectState, failure: TerminationReason):
        """Returns (observations | None, stop)."""
        analysis_request = AnalysisRequest(state, config.scope, config.analysis_spec)
        intent_id, _, stop, _ = gate(OperationKind.ANALYZE, lambda charged: analysis_request, None)
        if stop:
            return None, stop
        result = analyzer.analyze(analysis_request)
        if isinstance(result, PortFailure):
            stop = observed(intent_id, OperationKind.ANALYZE, result)
            return None, stop or (failure, f"analysis failed: {result.code.value}: {result.message}")
        try:
            require_instance(result, Success, "analysis result")
            require_instance(result.value, AnalysisResponse, "analysis response")
            validate_analysis_response(analysis_request, result.value)
        except ValueError as error:
            stop = observed(intent_id, OperationKind.ANALYZE, _invalid(result, error))
            return None, stop or (TerminationReason.RUNTIME_CONTRACT_FAILED, f"invalid analysis response: {error}")
        stop = observed(intent_id, OperationKind.ANALYZE, result.value)
        observations[state.revision] = result.value.observations
        return result.value.observations, stop

    def diagnose(state: ProjectState):
        nonlocal diagnosed
        if diagnosed is not None and diagnosed[0] == state.revision:
            return diagnosed[1], None
        diagnosis_request = DiagnosisRequest(state, config.scope, observations[state.revision])
        intent_id, _, stop, _ = gate(OperationKind.DIAGNOSE, lambda charged: diagnosis_request, None)
        if stop:
            return None, stop
        result = diagnosis_source.diagnose(diagnosis_request)
        if isinstance(result, PortFailure):
            stop = observed(intent_id, OperationKind.DIAGNOSE, result)
            return None, stop or (TerminationReason.DIAGNOSIS_FAILED, f"diagnosis failed: {result.message}")
        try:
            require_instance(result, Success, "diagnosis result")
            validate_diagnosis_response(diagnosis_request, result.value)
        except ValueError as error:
            stop = observed(intent_id, OperationKind.DIAGNOSE, _invalid(result, error))
            return None, stop or (TerminationReason.RUNTIME_CONTRACT_FAILED, f"invalid diagnosis response: {error}")
        stop = observed(intent_id, OperationKind.DIAGNOSE, result.value)
        diagnosed = (state.revision, result.value)
        return result.value, stop

    def evidence_for(state: ProjectState) -> StateEvidence:
        return StateEvidence(state, observations[state.revision], request.alignment)

    # --- feedback ----------------------------------------------------------------------------------

    def add_feedback(call_id, before: ProjectState, outcome, *, decision_id=None, candidate=None, selected=...):
        selected_revision = before.revision if selected is ... else selected
        entry = DecisionFeedback(
            call_id, len(history) + 1, run_id, project_id, before.revision, candidate, selected_revision,
            decision_id, usage, outcome,
        )  # fmt: skip
        history.append(entry)
        return record(entry)

    def copied(verification, bundles) -> tuple[ObservationFeedback, ...]:
        if verification is None:
            return ()
        by_id = {}
        for bundle in bundles:
            for observation in bundle.observations:
                by_id.setdefault(observation.observation_id, (bundle.state.revision, observation))
        cited = dict.fromkeys(e for check in verification.checks for e in check.evidence_ids)
        return tuple(ObservationFeedback(*by_id[e]) for e in cited)

    # --- recovery ----------------------------------------------------------------------------------

    def recover_saved() -> tuple[RollbackStatus | None, Stop | None]:
        """One recovery workflow: inspect the saved accepted state. None status means not performed."""
        nonlocal verified, restored_last
        status, _, _, stop, performed = inspect(accepted.state, BudgetOperation.RECOVERY_OPERATION)
        if not performed:
            return None, stop
        if status is IntegrityStatus.VERIFIED:
            verified, restored_last = accepted, True
            return RollbackStatus.VERIFIED, stop
        verified = None
        return (RollbackStatus.FAILED if status is IntegrityStatus.FAILED else RollbackStatus.UNCERTAIN), stop

    def recovery_stop(rollback: RollbackStatus | None, inner: Stop | None, original: Stop) -> Stop:
        if rollback is None:
            explanation = "recovery of the saved accepted state could not be performed"
            if inner and inner[0] is not TerminationReason.RECORDING_FAILED:
                explanation += f" ({inner[0].value}: {inner[1]})"
            return TerminationReason.REQUIRED_RECOVERY_NOT_PERFORMED, explanation
        if rollback is RollbackStatus.FAILED:
            return TerminationReason.RESTORATION_FAILED, "integrity of the saved accepted state failed"
        if rollback is RollbackStatus.UNCERTAIN:
            return TerminationReason.RESTORATION_UNCERTAIN, "integrity of the saved accepted state is uncertain"
        # A recording or clock failure observed during a verified recovery is never discarded.
        return inner or original

    def failed_attempt(ctx, status, original: Stop, *, stage, verification=None, bundles=()) -> Stop:
        """Recover the saved state after an unaccepted effect, record feedback, and choose the stop."""
        rollback, inner = recover_saved()
        outcome = _effect_feedback(
            ctx, status, rollback, stage=stage, verification=verification, observations=copied(verification, bundles),
            reason=original,
        )  # fmt: skip
        selected = accepted.state.revision if rollback is RollbackStatus.VERIFIED else None
        candidate = ctx["candidate"] if status is ExecutionStatus.CANDIDATE_READY else None
        add_feedback(ctx["call"], ctx["before"], outcome, decision_id=ctx["decision"].decision_id,
                     candidate=candidate, selected=selected)  # fmt: skip
        return recovery_stop(rollback, inner, original)

    # --- one decision round ------------------------------------------------------------------------

    def decision_round() -> Stop | None:
        """None means another round follows; a stop ends the run."""
        nonlocal usage
        state = accepted.state
        diagnosis, stop = diagnose(state)
        if stop:
            return stop
        capabilities = build_action_capability_view(
            state=state, scope=config.scope, context=config.context, policy=request.policy,
            renderer=request.renderer_capabilities,
        )  # fmt: skip
        retries = 0
        while True:
            call_id = fresh(DecisionCallId)
            if call_id is None:
                return identity_failure("decision call ID")

            def build(charged, call_id=call_id):
                return Situation(
                    state, config.context, config.controller, observations[state.revision], charged, limits, run_id,
                    config.scope, config.criteria, diagnosis, capabilities,
                    DecisionHistory(run_id, project_id, tuple(history)),
                )  # fmt: skip

            intent_id, situation, stop, skipped = gate(OperationKind.DECIDE, build, BudgetOperation.DECISION_CALL,
                                                       call=call_id)  # fmt: skip
            if skipped:
                outcome = NotDispatchedFeedback(stop[0].value, stop[1])
                return add_feedback(call_id, state, outcome) or stop
            if stop:
                return stop
            result = decision_maker.decide(situation)
            if isinstance(result, PortFailure):
                stop = observed(intent_id, OperationKind.DECIDE, result) or add_feedback(
                    call_id, state, PortFailureFeedback(result)
                )
                if stop:
                    return stop
                if result.code is PortFailureCode.UNAVAILABLE and result.retryable:
                    # Section 27 precedence: cancellation at this clean boundary, then the time limit, then the
                    # global decision-call limit (both from check_budget, in that order), then local retries.
                    if last.cancelled:
                        return TerminationReason.USER_CANCELLED, "cancelled after a retryable decision failure"
                    assessment = check_budget(BudgetOperation.DECISION_CALL, limits=limits, usage=usage,
                                              elapsed_seconds=usage.elapsed_seconds)  # fmt: skip
                    if isinstance(assessment, BudgetBlock):
                        reason = TerminationReason[assessment.reason.name]
                        return record(assessment, assessment.usage) or (
                            reason,
                            f"no decision retry: budget blocks the next call ({assessment.reason.value})",
                        )
                    if retries < request.retry_policy.max_retries:
                        retries += 1
                        continue
                    return TerminationReason.DECISION_RETRIES_EXHAUSTED, f"decision retries exhausted: {result.message}"
                return TerminationReason.DECISION_FAILED, f"decision failed: {result.code.value}: {result.message}"
            if not isinstance(result, Success) or not isinstance(result.value, DecisionResponse):
                error = "the decision port returned neither a PortFailure nor Success(DecisionResponse)"
                stop = observed(intent_id, OperationKind.DECIDE, _invalid(result, error)) or add_feedback(
                    call_id, state, InvalidResponseFeedback("invalid_port_response", error)
                )
                return stop or (TerminationReason.INVALID_DECISION_RESPONSE, error)
            response = result.value
            stop = observed(intent_id, OperationKind.DECIDE, response)
            if stop:
                return stop
            return evaluate_decision(call_id, situation, response, capabilities)

    def evaluate_decision(call_id, situation: Situation, response: DecisionResponse, capabilities) -> Stop | None:
        nonlocal usage
        state, decision = situation.state, response.decision
        attempt_id = None
        if isinstance(decision.outcome, ProposeAction) and _structurally_valid(situation, response):
            # A structurally valid proposal is charged one attempt before any action checking.
            assessment = check_budget(BudgetOperation.ATTEMPT, limits=limits, usage=usage,
                                      elapsed_seconds=usage.elapsed_seconds)  # fmt: skip
            if isinstance(assessment, BudgetBlock):
                reason = TerminationReason[assessment.reason.name]
                outcome = _rejection(decision.outcome.action, None, FeedbackStage.EVIDENCE_GATE, reason.value,
                                     "the attempt budget blocks checking this proposal", not_executed=True)  # fmt: skip
                stop = record(assessment, assessment.usage) or add_feedback(
                    call_id, state, outcome, decision_id=decision.decision_id
                )
                return stop or (reason, f"budget blocks the next attempt: {assessment.reason.value}")
            usage = assessment.usage_after_charge
            attempt_id = fresh(AttemptId)
            if attempt_id is None:
                return identity_failure("attempt ID")
        route = assess_decision(situation, response)
        stop = record(route)
        if stop:
            return stop
        did = decision.decision_id
        match route:
            case InvalidResponse():
                outcome = InvalidResponseFeedback("invalid_decision_response", route.explanation)
                return add_feedback(call_id, state, outcome) or (
                    TerminationReason.INVALID_DECISION_RESPONSE,
                    f"invalid decision response: {route.explanation}",
                )
            case KeepState():
                outcome = NoChangeFeedback(decision.outcome.explanation)
                return add_feedback(call_id, state, outcome, decision_id=did) or (
                    TerminationReason.NO_CHANGE_PROPOSED,
                    f"the controller proposed no change: {decision.outcome.explanation}",
                )
            case RequestContext():
                questions = decision.outcome.requested_context
                outcome = ContextRequestFeedback(questions, "the controller requested missing context")
                return add_feedback(call_id, state, outcome, decision_id=did) or (
                    TerminationReason.MISSING_CONTEXT,
                    "missing context; a new run is needed after an update. Questions: " + " | ".join(questions),
                )
            case DeclineAction():
                code = decision.outcome.reason.value
                outcome = AbstentionFeedback(code, f"the controller abstained: {code}")
                return add_feedback(call_id, state, outcome, decision_id=did) or (
                    _ABSTENTION_REASONS[code],
                    f"the controller abstained: {code}",
                )
            case EvidenceBlocked():
                blocked = ", ".join(str(c) for c in route.blocked_candidate_ids)
                outcome = _rejection(decision.outcome.action, attempt_id, FeedbackStage.EVIDENCE_GATE,
                                     "insufficient_evidence", f"candidates lack available evidence: {blocked}")  # fmt: skip
                return add_feedback(call_id, state, outcome, decision_id=did) or (
                    TerminationReason.INSUFFICIENT_EVIDENCE,
                    f"the proposal's candidates lack sufficient available evidence: {blocked}",
                )
            case ReadyForAdmission():
                return act(call_id, situation, decision, attempt_id, capabilities)
            case _:
                assert_never(route)

    def act(call_id, situation: Situation, decision, attempt_id: AttemptId, capabilities) -> Stop | None:
        nonlocal verified, usage
        state, action = situation.state, decision.outcome.action
        did = decision.decision_id

        def reject(rejection, stage, code) -> Stop | None:
            stop = record(rejection)
            outcome = _rejection(action, attempt_id, stage, code, rejection.explanation)
            return stop or add_feedback(call_id, state, outcome, decision_id=did)

        admission = admit_action(ActionAdmissionRequest(situation, decision, request.policy))
        if isinstance(admission, RejectedAction):
            return reject(admission, FeedbackStage.ADMISSION, admission.code.value)
        stop = record(admission)
        if stop:
            return stop
        available = {gain.track_id: gain.bounds for gain in capabilities.gain_actions}
        operation = action.operation
        bounds = available.get(operation.track_id)
        if bounds is None or not bounds.minimum_db <= operation.value_db <= bounds.maximum_db:
            code = AdmissionRejectionCode.NO_GAIN_PERMISSION if bounds is None else AdmissionRejectionCode.OUT_OF_BOUNDS
            explanation = (
                f"track {operation.track_id} is not an advertised available gain action"
                if bounds is None
                else f"gain {operation.value_db} dB lies outside the advertised range "
                f"[{bounds.minimum_db}, {bounds.maximum_db}] dB"
            )
            return reject(RejectedAction(did, code, explanation), FeedbackStage.ADMISSION, code.value)

        execution_id = fresh(ExecutionId)
        token = identities.next_token()
        revision = fresh(Revision)
        target_id, new_mix_id = fresh(ArtifactId), fresh(ArtifactId)
        if None in (execution_id, revision, target_id, new_mix_id) or not isinstance(token, str) or token in tokens:
            return identity_failure("execution identity")
        tokens.add(token)
        try:
            workspace = ExecutionWorkspace(execution_id, request.output_root_key, token)
            candidate_identity = CandidateIdentity(revision, target_id, new_mix_id)
        except ValueError as error:
            return TerminationReason.RUNTIME_CONTRACT_FAILED, f"generated execution identity is invalid: {error}"
        execution_request = ExecutionRequest(
            execution_id, situation, decision, request.policy, request.recipe,
            tuple(StemBinding(track, stems[track]) for track in request.recipe.track_ids), mix_id, config.render_spec,
            request.processing_spec, workspace, candidate_identity,
        )  # fmt: skip
        preflight = preflight_execution(execution_request)
        if isinstance(preflight, PreflightRejected):
            return reject(preflight, FeedbackStage.PREFLIGHT, preflight.code.value)
        stop = record(preflight)
        if stop:
            return stop
        return render(call_id, situation, decision, attempt_id, preflight)

    def render(call_id, situation, decision, attempt_id, preflight: MetadataReady) -> Stop | None:
        nonlocal verified
        plan: RenderPlan = preflight.plan
        state, action = situation.state, decision.outcome.action
        before_request = StateIntegrityRequest(state, bindings_for(state))
        render_request = RunRenderRequest(plan, before_request, before_request.required_artifacts)
        intent_id, _, stop, skipped = gate(
            OperationKind.RENDER, lambda charged: render_request, BudgetOperation.RENDER_CALL, call=call_id,
            attempt=attempt_id, execution=plan.execution_id,
        )  # fmt: skip
        if stop:
            stage = FeedbackStage.RENDER if skipped else FeedbackStage.PREPARATION
            outcome = _rejection(action, attempt_id, stage, stop[0].value, stop[1], not_executed=True)
            return add_feedback(call_id, state, outcome, decision_id=decision.decision_id) or stop
        verified = None  # an effect is dispatched: the saved state is unverified until recovery or acceptance
        ctx = {"call": call_id, "before": state, "decision": decision, "action": action, "attempt": attempt_id,
               "execution": plan.execution_id, "candidate": plan.candidate.revision}  # fmt: skip
        response = renderer.process(render_request)
        try:
            validate_run_render_response(render_request, response)
        except ValueError as error:
            stop = observed(intent_id, OperationKind.RENDER, _invalid(response, error))
            original = stop or (TerminationReason.INVALID_RENDER_RESPONSE, f"invalid render response: {error}")
            return failed_attempt(ctx, ExecutionStatus.UNCERTAIN, original, stage=FeedbackStage.RENDER)
        stop = observed(intent_id, OperationKind.RENDER, response)
        if isinstance(response, RenderPreparationFailure):
            original = stop or (TerminationReason.EXECUTION_PREPARATION_FAILED,
                                f"render preparation failed: {response.issue.message}")  # fmt: skip
            return failed_attempt(ctx, ExecutionStatus.NOT_STARTED, original, stage=FeedbackStage.PREPARATION)
        if isinstance(response, RenderFailure):
            original = stop or (TerminationReason.RENDER_FAILED, f"render {response.status.value}: "
                                                                    f"{response.issue.message}")  # fmt: skip
            return failed_attempt(ctx, response.status, original, stage=FeedbackStage.RENDER)
        if stop:
            return failed_attempt(ctx, ExecutionStatus.CANDIDATE_READY, stop, stage=FeedbackStage.RECORDING)
        return verify_candidate(ctx, plan, response)

    def verify_candidate(ctx, plan: RenderPlan, rendered: RenderedCandidate) -> Stop | None:
        before = ctx["before"]
        receipt = ExecutionReceipt(
            plan.execution_id, ctx["action"].action_id, plan.project_id, plan.before_revision,
            ExecutionStatus.CANDIDATE_READY, plan.candidate.revision, (rendered.target_artifact, rendered.mix_artifact),
            (plan.target_current_artifact_id, plan.current_mix_artifact_id), None,
        )  # fmt: skip
        checkpoint_id = fresh(CheckpointId)
        if checkpoint_id is None:
            return failed_attempt(ctx, ExecutionStatus.CANDIDATE_READY, identity_failure("checkpoint ID"),
                                  stage=FeedbackStage.RECORDING)  # fmt: skip
        try:
            candidate = derive_candidate_project_state(before, receipt, ctx["action"], checkpoint_id, last.utc)
        except ValueError as error:
            original = (TerminationReason.INVALID_RENDER_RESPONSE, f"rendered outputs do not form a candidate: {error}")
            return failed_attempt(ctx, ExecutionStatus.UNCERTAIN, original, stage=FeedbackStage.RENDER)
        stop = record(receipt)
        if stop:
            return failed_attempt(ctx, ExecutionStatus.CANDIDATE_READY, stop, stage=FeedbackStage.RECORDING)
        candidate_observations, stop = analyze(candidate, TerminationReason.CANDIDATE_ANALYSIS_FAILED)
        if candidate_observations is None:
            return failed_attempt(ctx, ExecutionStatus.CANDIDATE_READY, stop, stage=FeedbackStage.VERIFICATION)
        if stop:
            return failed_attempt(ctx, ExecutionStatus.CANDIDATE_READY, stop, stage=FeedbackStage.RECORDING)
        result_id = fresh(VerificationResultId)
        if result_id is None:
            return failed_attempt(ctx, ExecutionStatus.CANDIDATE_READY, identity_failure("verification ID"),
                                  stage=FeedbackStage.VERIFICATION)  # fmt: skip
        bundles = (evidence_for(candidate), evidence_for(before), evidence_for(request.initial_state))
        try:
            assessment = VerificationAssessment(
                result_id, AssessmentMode.ATTEMPT, config.criteria, request.measurements, *bundles
            )
        except ValueError as error:
            original = (TerminationReason.RUNTIME_CONTRACT_FAILED, f"candidate evidence is inconsistent: {error}")
            return failed_attempt(ctx, ExecutionStatus.CANDIDATE_READY, original, stage=FeedbackStage.VERIFICATION)
        verification = assess_verification(assessment)
        stop = record(assessment) or record(verification)
        if stop:
            return failed_attempt(ctx, ExecutionStatus.CANDIDATE_READY, stop, stage=FeedbackStage.RECORDING)
        ctx.update(receipt=receipt, candidate_state=candidate, assessment=assessment, verification=verification,
                   bundles=bundles)  # fmt: skip
        route = route_verification(verification, config.criteria)
        match route:
            case VerificationRoute.COMMIT_THEN_FINISH | VerificationRoute.COMMIT_THEN_ITERATE:
                return accept(ctx, route)
            case VerificationRoute.RESTORE_THEN_CONSIDER_ITERATION:
                return restore_after_rejection(ctx)
            case VerificationRoute.RECONCILE_THEN_FINISH_INCONCLUSIVE:
                original = (TerminationReason.VERIFICATION_INCONCLUSIVE, "candidate verification is inconclusive")
                return failed_attempt(ctx, ExecutionStatus.CANDIDATE_READY, original, stage=FeedbackStage.VERIFICATION,
                                      verification=verification, bundles=bundles)  # fmt: skip
            case _:
                raise ValueError(f"an ATTEMPT verification cannot take route {route.name}")

    def restore_after_rejection(ctx) -> Stop | None:
        rollback, inner = recover_saved()
        if rollback is not RollbackStatus.VERIFIED:
            original = (TerminationReason.RESTORATION_FAILED, "the saved state could not be verified after rejection")
            return failed_attempt_after(ctx, rollback, inner, original)
        before = ctx["before"]
        receipt = RollbackReceipt(
            ctx["execution"], before.audio.checkpoint_id, RollbackStatus.VERIFIED, before,
            "logical retention of the saved accepted state; its integrity inspection was verified",
        )  # fmt: skip
        attempt = CorrectionAttempt(
            ctx["attempt"], run_id, ctx["decision"].decision_id, ctx["action"].action_id, before, ctx["receipt"],
            ctx["candidate_state"], ctx["verification"], receipt, AttemptDisposition.ROLLED_BACK,
        )  # fmt: skip
        outcome = _effect_feedback(
            ctx, ExecutionStatus.CANDIDATE_READY, RollbackStatus.VERIFIED, stage=FeedbackStage.RECOVERY,
            verification=ctx["verification"], observations=copied(ctx["verification"], ctx["bundles"]),
            reason=(TerminationReason.RESTORATION_FAILED, "candidate rejected by verification"),
        )  # fmt: skip
        stop = (
            inner
            or record(attempt)
            or add_feedback(
                ctx["call"], before, outcome, decision_id=ctx["decision"].decision_id, candidate=ctx["candidate"]
            )
        )
        return stop

    def failed_attempt_after(ctx, rollback, inner, original) -> Stop:
        outcome = _effect_feedback(
            ctx, ExecutionStatus.CANDIDATE_READY, rollback, stage=FeedbackStage.RECOVERY, verification=ctx.get(
                "verification"), observations=copied(ctx.get("verification"), ctx.get("bundles", ())), reason=original,
        )  # fmt: skip
        add_feedback(ctx["call"], ctx["before"], outcome, decision_id=ctx["decision"].decision_id,
                     candidate=ctx["candidate"], selected=None)  # fmt: skip
        return recovery_stop(rollback, inner, original)

    def accept(ctx, route: VerificationRoute) -> Stop | None:
        nonlocal accepted, verified, restored_last, authority_known
        stop = checkpoint()
        if stop or last.cancelled or usage.elapsed_seconds >= limits.max_elapsed_seconds:
            original = stop or (
                (TerminationReason.USER_CANCELLED, "cancelled before the acceptance commit")
                if last.cancelled
                else (TerminationReason.ELAPSED_TIME_LIMIT, "time ran out before the acceptance commit")
            )
            return failed_attempt(ctx, ExecutionStatus.CANDIDATE_READY, original, stage=FeedbackStage.ACCEPTANCE,
                                  verification=ctx["verification"], bundles=ctx["bundles"])  # fmt: skip
        acceptance_id = fresh(AcceptanceId)
        if acceptance_id is None:
            return failed_attempt(ctx, ExecutionStatus.CANDIDATE_READY, identity_failure("acceptance ID"),
                                  stage=FeedbackStage.ACCEPTANCE)  # fmt: skip
        event = AcceptanceEvent(
            acceptance_id, run_id, ctx["attempt"], ctx["decision"].decision_id, accepted, ctx["action"],
            ctx["receipt"], ctx["assessment"], ctx["verification"],
        )  # fmt: skip
        validate_acceptance_event(event)  # the coordinator's precommit obligation
        stop = record(event)
        if stop:
            return failed_attempt(ctx, ExecutionStatus.CANDIDATE_READY, stop, stage=FeedbackStage.RECORDING,
                                  verification=ctx["verification"], bundles=ctx["bundles"])  # fmt: skip
        # The commit is a dispatch: recheck cancellation and time after the event's acknowledgement.
        stop = checkpoint()
        if stop or last.cancelled or usage.elapsed_seconds >= limits.max_elapsed_seconds:
            original = stop or (
                (TerminationReason.USER_CANCELLED, "cancelled after recording the acceptance event; not committed")
                if last.cancelled
                else (TerminationReason.ELAPSED_TIME_LIMIT, "time ran out after recording the acceptance event; "
                                                             "not committed")
            )  # fmt: skip
            return failed_attempt(ctx, ExecutionStatus.CANDIDATE_READY, original, stage=FeedbackStage.ACCEPTANCE,
                                  verification=ctx["verification"], bundles=ctx["bundles"])  # fmt: skip
        response = journal.commit(event)
        try:
            validate_acceptance_commit_response(event, response)
        except ValueError as error:
            response = AcceptanceCommitUnknown(event.acceptance_id, f"invalid commit response: {error}")
        if isinstance(response, AcceptanceCommitted):
            commits.append(response)
            promote(response.accepted, ctx)
            stop = record(response)
            return stop or finish_acceptance(ctx, route)
        if isinstance(response, AcceptanceNotCommitted):
            stop = record(response)
            original = stop or (TerminationReason.RECORDING_FAILED, f"acceptance not committed: {response.explanation}")
            return failed_attempt(ctx, ExecutionStatus.CANDIDATE_READY, original, stage=FeedbackStage.ACCEPTANCE,
                                  verification=ctx["verification"], bundles=ctx["bundles"])  # fmt: skip
        record(response)  # unknown acknowledgement; reconciliation follows even if this record fails
        return reconcile_unknown(ctx, event, route)

    def promote(reference: AcceptedReference, ctx) -> None:
        nonlocal accepted, verified, restored_last, authority_known, mix_id
        accepted, verified, restored_last, authority_known = reference, reference, False, True
        receipt = ctx["receipt"]
        target, new_mix = receipt.candidate_artifacts
        stems[target.source_track_ids[0]] = target.artifact_id
        mix_id = new_mix.artifact_id
        for artifact in receipt.candidate_artifacts:
            roots.setdefault(artifact.artifact_id, request.output_root_key)

    def finish_acceptance(ctx, route: VerificationRoute) -> Stop | None:
        attempt = CorrectionAttempt(
            ctx["attempt"], run_id, ctx["decision"].decision_id, ctx["action"].action_id, ctx["before"],
            ctx["receipt"], ctx["candidate_state"], ctx["verification"], None, AttemptDisposition.ACCEPTED,
        )  # fmt: skip
        outcome = ActionFeedback(
            ctx["action"], ctx["attempt"], ctx["execution"], ActionFeedbackDisposition.ACCEPTED,
            FeedbackStage.ACCEPTANCE, ExecutionStatus.CANDIDATE_READY, ctx["verification"], None, "accepted",
            "candidate verified and its acceptance committed", copied(ctx["verification"], ctx["bundles"]),
        )  # fmt: skip
        stop = record(attempt) or add_feedback(
            ctx["call"],
            ctx["before"],
            outcome,
            decision_id=ctx["decision"].decision_id,
            candidate=ctx["candidate"],
            selected=ctx["candidate"],
        )
        if stop:
            return stop
        if route is VerificationRoute.COMMIT_THEN_FINISH:
            return TerminationReason.CANDIDATE_CRITERIA_MET, "the accepted candidate satisfies every target"
        return None  # intermediate progress: another round

    def reconcile_unknown(ctx, event: AcceptanceEvent, route: VerificationRoute) -> Stop:
        nonlocal accepted, verified, restored_last, authority_known
        authority_known = False
        verified = None
        query = AcceptanceQuery(run_id, event.acceptance_id)
        intent_id, _, stop, _ = gate(OperationKind.QUERY_ACCEPTANCE, lambda charged: query,
                                     BudgetOperation.RECOVERY_OPERATION)  # fmt: skip
        if intent_id is None or stop:
            return TerminationReason.REQUIRED_RECOVERY_NOT_PERFORMED, "the acceptance authority could not be queried"
        answer = journal.query(query)
        try:
            validate_acceptance_query_response(query, answer)
        except ValueError as error:
            query_stop = observed(intent_id, OperationKind.QUERY_ACCEPTANCE, _invalid(answer, error))
            answer = AcceptanceAuthorityUnknown(run_id, event.acceptance_id, f"invalid authority answer: {error}")
        else:
            query_stop = observed(intent_id, OperationKind.QUERY_ACCEPTANCE, answer)
        if isinstance(answer, AcceptanceAuthorityUnknown):
            result = reconcile_acceptance(event, answer, integrity_request=None, integrity_report=None)
            record(result)  # ACCEPTANCE_COMMIT_UNCERTAIN outranks any recording or clock failure here
            return TerminationReason.ACCEPTANCE_COMMIT_UNCERTAIN, f"acceptance authority unknown: {answer.explanation}"
        if isinstance(answer, AcceptanceFound):
            accepted, authority_known = answer.accepted, True
            if not any(c.event == answer.event for c in commits):
                commits.append(AcceptanceCommitted(answer.event, answer.accepted))
            target_state = answer.accepted.state
        elif isinstance(answer, AcceptanceAbsent):
            authority_known = True
            target_state = accepted.state
        else:
            assert_never(answer)
        if query_stop:
            # The authority fact is kept, but no further suboperation starts after an unrecorded or untimed answer.
            return (
                TerminationReason.REQUIRED_RECOVERY_NOT_PERFORMED,
                f"integrity was not inspected after the authority answer ({query_stop[0].value}: {query_stop[1]})",
            )
        status, report, integrity_request, inspect_stop, performed = inspect(target_state, None)
        found = isinstance(answer, AcceptanceFound)
        if not performed:
            detail = f" ({inspect_stop[0].value}: {inspect_stop[1]})" if inspect_stop else ""
            return TerminationReason.REQUIRED_RECOVERY_NOT_PERFORMED, "state integrity could not be inspected" + detail
        if report is None:  # invalid inspector response
            reason = (TerminationReason.ACCEPTED_STATE_INTEGRITY_UNCERTAIN if found
                      else TerminationReason.RESTORATION_UNCERTAIN)  # fmt: skip
            return reason, "the integrity inspector returned an invalid report"
        result = reconcile_acceptance(event, answer, integrity_request=integrity_request, integrity_report=report)
        outcome = result.outcome
        if outcome is ReconciliationOutcome.CONFIRMED_ACCEPTANCE:
            promote(result.verified_reference, ctx)
        elif outcome is ReconciliationOutcome.VERIFIED_PREVIOUS_STATE:
            verified, restored_last = accepted, True
        # Facts are applied above; a stop from observing the inspection or recording the result is preserved and
        # ends the run instead of resuming ordinary work.
        record_stop = record(result)  # attempted even after an inspection stop, so the facts reach the trace
        pending = inspect_stop or record_stop
        match outcome:
            case ReconciliationOutcome.CONFIRMED_ACCEPTANCE:
                return pending or finish_acceptance(ctx, route)
            case ReconciliationOutcome.VERIFIED_PREVIOUS_STATE:
                return pending or (
                    TerminationReason.RECORDING_FAILED,
                    "the acceptance was confirmed absent after a lost reply",
                )
            case ReconciliationOutcome.ACCEPTED_STATE_FAILED:
                return TerminationReason.ACCEPTED_STATE_INTEGRITY_FAILED, "the committed candidate failed integrity"
            case ReconciliationOutcome.ACCEPTED_STATE_UNCERTAIN:
                return TerminationReason.ACCEPTED_STATE_INTEGRITY_UNCERTAIN, "committed candidate integrity unknown"
            case ReconciliationOutcome.PREVIOUS_STATE_FAILED:
                return TerminationReason.RESTORATION_FAILED, "integrity of the saved accepted state failed"
            case ReconciliationOutcome.PREVIOUS_STATE_UNCERTAIN:
                return TerminationReason.RESTORATION_UNCERTAIN, "integrity of the saved accepted state is uncertain"
            case ReconciliationOutcome.AUTHORITY_UNCERTAIN:
                return TerminationReason.ACCEPTANCE_COMMIT_UNCERTAIN, "acceptance authority unknown"
            case _:
                assert_never(outcome)

    # --- ordinary work -----------------------------------------------------------------------------

    def ordinary() -> Stop:
        nonlocal verified
        stop = record(request)
        if stop:
            return stop
        status, _, _, stop, performed = inspect(request.initial_state, None)
        if not performed:
            return stop
        if status is IntegrityStatus.VERIFIED:
            verified = initial
        if stop:
            return stop
        if status is not IntegrityStatus.VERIFIED:
            if status is IntegrityStatus.FAILED:
                return TerminationReason.ACCEPTED_STATE_INTEGRITY_FAILED, "integrity of the initial state failed"
            return TerminationReason.ACCEPTED_STATE_INTEGRITY_UNCERTAIN, "integrity of the initial state is uncertain"
        initial_observations, stop = analyze(request.initial_state, TerminationReason.INITIAL_ANALYSIS_FAILED)
        if stop:
            return stop
        result_id = fresh(VerificationResultId)
        if result_id is None:
            return identity_failure("verification ID")
        initial_evidence = evidence_for(request.initial_state)
        assessment = VerificationAssessment(
            result_id, AssessmentMode.INITIAL, config.criteria, request.measurements, initial_evidence, None, None
        )
        verification = assess_verification(assessment)
        stop = record(assessment) or record(verification)
        if stop:
            return stop
        route = route_verification(verification, config.criteria)
        match route:
            case VerificationRoute.FINISH_INITIAL_SUCCESS:
                return TerminationReason.INITIAL_CRITERIA_MET, "the initial state satisfies every target"
            case VerificationRoute.FINISH_INITIAL_INCONCLUSIVE:
                return TerminationReason.INITIAL_ASSESSMENT_INCONCLUSIVE, "the initial assessment is inconclusive"
            case VerificationRoute.CHECK_BASELINE_READINESS:
                missing = _missing_baselines(config.criteria, definitions, initial_evidence)
                if missing:
                    return (
                        TerminationReason.REQUIRED_BASELINE_UNAVAILABLE,
                        f"comparison baselines are unavailable for: {', '.join(missing)}",
                    )
            case _:
                raise ValueError(f"an INITIAL verification cannot take route {route.name}")
        while True:
            stop = decision_round()
            if stop:
                return stop

    # --- finalization ------------------------------------------------------------------------------

    def finalize(reason: TerminationReason, explanation: str) -> RunReport:
        authoritative = accepted if authority_known else None
        selected = verified if verified is not None and verified == authoritative else None
        if selected is None:
            final_state = FinalProjectState.UNKNOWN
        elif restored_last:
            final_state = FinalProjectState.RESTORED_TO_LAST_ACCEPTED
        elif selected.sequence == 0:
            final_state = FinalProjectState.UNCHANGED
        else:
            final_state = FinalProjectState.ACCEPTED_EDITS_RETAINED
        outcome = _REASON_OUTCOMES[reason]
        proposed = RunRecord(
            run_id, project_id, config, request.initial_state.revision, first.utc, RunState.FINISHED, usage, outcome,
            final_state, reason, explanation,
        )  # fmt: skip
        terminal_payload = TerminalSnapshot(proposed, authoritative, selected)
        event = new_event(terminal_payload, usage)
        if event is None:
            raise ValueError("the identity source supplied an invalid or repeated terminal event ID")
        response = append(event)
        acknowledged = isinstance(response, TraceAppended)
        # Read back the trace first, so that completion is observed after every journal operation the report needs.
        trace = journal.snapshot()
        if isinstance(trace, Success) and isinstance(trace.value, RunJournalSnapshot):
            try:
                validate_run_journal_snapshot(run_id, initial, trace.value)
                snapshot = trace.value
            except ValueError:
                snapshot = RunJournalSnapshot(run_id, initial, tuple(known), tuple(commits))
        else:
            snapshot = RunJournalSnapshot(run_id, initial, tuple(known), tuple(commits))
        completion = clock.read()
        clock_valid = isinstance(completion, ClockReading) and completion.monotonic_seconds >= last.monotonic_seconds
        if not clock_valid:
            completion = last  # keep the last valid observation; the violation is surfaced below
        final_usage = replace(usage, elapsed_seconds=completion.monotonic_seconds - start)
        final_outcome, final_reason, final_explanation = outcome, reason, explanation
        if not acknowledged and reason not in _PRESERVED_ON_RECORDING_FAILURE:
            final_outcome, final_reason = RunOutcome.FAILED, TerminationReason.RECORDING_FAILED
            final_explanation = f"the terminal event was not acknowledged; proposed {reason.value}: {explanation}"
        elif acknowledged and not clock_valid and outcome is not RunOutcome.FAILED:
            final_outcome, final_reason = RunOutcome.FAILED, TerminationReason.RUNTIME_CONTRACT_FAILED
            final_explanation = (
                f"the completion clock reading was invalid or decreased; proposed {reason.value}: {explanation}"
            )
        elif (
            acknowledged
            and final_usage.elapsed_seconds >= limits.max_elapsed_seconds
            and outcome in (RunOutcome.SUCCEEDED, RunOutcome.ABSTAINED)
        ):
            final_outcome, final_reason = RunOutcome.LIMIT_REACHED, TerminationReason.ELAPSED_TIME_LIMIT
            final_explanation = f"finalization ended at or beyond the time limit; proposed {reason.value}"
        if not clock_valid and final_reason is not TerminationReason.RUNTIME_CONTRACT_FAILED:
            final_explanation += " (the completion clock reading was invalid or decreased)"
        final_run = replace(
            proposed, usage=final_usage, outcome=final_outcome, termination_reason=final_reason,
            termination_explanation=final_explanation,
        )  # fmt: skip
        return RunReport(
            request, snapshot, event, response, completion, final_run, authoritative, selected,
            tuple(e for e in unacknowledged if e.event_id != event.event_id) + (() if acknowledged else (event,)),
        )  # fmt: skip

    try:
        stop = ordinary()
    except Exception:
        try:
            finalize(TerminationReason.RUNTIME_CONTRACT_FAILED, "an unexpected error interrupted the run")
        except Exception:  # noqa: S110 - the original error is re-raised below
            pass
        raise
    return finalize(*stop)


# --- pure helpers ----------------------------------------------------------------------------------


def _invalid(response, error) -> InvalidPortResponse:
    return InvalidPortResponse(type(response).__name__, str(error) or "invalid response")


def _structurally_valid(situation: Situation, response: DecisionResponse) -> bool:
    try:
        validate_decision_response(situation, response)
    except ValueError:
        return False
    return True


def _missing_baselines(criteria, definitions, initial_evidence: StateEvidence) -> list[str]:
    """Conditions whose comparison baseline (the initial state for the first attempt) cannot be matched."""
    comparisons = [*criteria.improvements, *(g for g in criteria.guards if isinstance(g, DeltaGuard))]
    missing = []
    for condition in comparisons:
        match = match_evidence(definitions[condition.condition_id], initial_evidence)
        if not isinstance(match, MatchedEvidence):
            missing.append(str(condition.condition_id))
    return missing


def _rejection(action: Action, attempt_id, stage: FeedbackStage, code: str, explanation: str, *, not_executed=False):
    disposition = ActionFeedbackDisposition.NOT_EXECUTED if not_executed else ActionFeedbackDisposition.REJECTED
    return ActionFeedback(action, attempt_id, None, disposition, stage, None, None, None, code, explanation, ())


def _effect_feedback(ctx, status: ExecutionStatus, rollback, *, stage, verification, observations, reason: Stop):
    """Feedback for a dispatched effect that was not accepted, given the recovery outcome."""
    action, attempt, execution = ctx["action"], ctx["attempt"], ctx["execution"]
    code, explanation = reason[0].value, reason[1]
    keep = verification if status is ExecutionStatus.CANDIDATE_READY else None
    copies = observations if keep is not None else ()
    if rollback is RollbackStatus.VERIFIED and status is ExecutionStatus.CANDIDATE_READY:
        return ActionFeedback(action, attempt, execution, ActionFeedbackDisposition.ROLLED_BACK, FeedbackStage.RECOVERY,
                              status, keep, RollbackStatus.VERIFIED, code, explanation, copies)  # fmt: skip
    if rollback is RollbackStatus.VERIFIED and status in (ExecutionStatus.NOT_STARTED, ExecutionStatus.FAILED):
        failed_stage = stage if stage in (FeedbackStage.PREPARATION, FeedbackStage.RENDER) else FeedbackStage.RENDER
        return ActionFeedback(action, attempt, execution, ActionFeedbackDisposition.FAILED, failed_stage, status, None,
                              None, code, explanation, ())  # fmt: skip
    rollback_status = rollback if rollback in (RollbackStatus.FAILED, RollbackStatus.UNCERTAIN) else None
    if rollback is RollbackStatus.VERIFIED:  # uncertain execution stays unresolved after a verified retention
        rollback_status = RollbackStatus.VERIFIED
    elif rollback_status is None and status is not ExecutionStatus.UNCERTAIN:
        rollback_status = RollbackStatus.UNCERTAIN  # recovery not performed: restoration is not established
    return ActionFeedback(action, attempt, execution, ActionFeedbackDisposition.UNRESOLVED, FeedbackStage.RECOVERY,
                          status, keep, rollback_status, code, explanation, copies)  # fmt: skip
