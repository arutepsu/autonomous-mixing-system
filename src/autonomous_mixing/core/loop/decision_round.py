"""Decision round responsibilities of the controlled serial coordinator: the exact Situation and complete
prior-call history, the decision call and its retry precedence, the attempt charge for a structurally
valid proposal, the decision gate, admission, the capability-view check and preflight. It yields either a
stop or a PreparedCorrection for rendering.
"""

from collections.abc import Callable
from dataclasses import replace
from typing import assert_never

from autonomous_mixing.core.admission.admission import admit_action
from autonomous_mixing.core.admission.capabilities import build_action_capability_view
from autonomous_mixing.core.admission.decision_gate import assess_decision
from autonomous_mixing.core.admission.preflight import preflight_execution
from autonomous_mixing.core.loop.budgets import check_budget
from autonomous_mixing.core.loop.feedback import _rejection, add_feedback
from autonomous_mixing.core.loop.observation import diagnose
from autonomous_mixing.core.loop.runtime import (
    _invalid,
    _observations_at,
    _stem_id,
    fresh,
    gate,
    identity_failure,
    observed,
    record,
)
from autonomous_mixing.core.ports.coordinator_runtime import DiagnosisSource, IdentitySource, RunClock
from autonomous_mixing.core.ports.decision import DecisionMaker, validate_decision_response
from autonomous_mixing.core.ports.run_journal import RunJournal
from autonomous_mixing.core.records.admission import (
    ActionAdmissionRequest,
    AdmissionRejectionCode,
    ExecutionRequest,
    PreflightRejected,
    RejectedAction,
    StemBinding,
)
from autonomous_mixing.core.records.budgets import BudgetBlock, BudgetOperation
from autonomous_mixing.core.records.capabilities import ActionCapabilityView
from autonomous_mixing.core.records.coordinator_progress import PreparedCorrection, RuntimeProgress, Stop
from autonomous_mixing.core.records.decision import Decision, DecisionResponse, ProposeAction, Situation
from autonomous_mixing.core.records.decision_gate import (
    DeclineAction,
    EvidenceBlocked,
    InvalidResponse,
    KeepState,
    ReadyForAdmission,
    RequestContext,
)
from autonomous_mixing.core.records.feedback import (
    AbstentionFeedback,
    ContextRequestFeedback,
    DecisionHistory,
    FeedbackStage,
    InvalidResponseFeedback,
    NoChangeFeedback,
    NotDispatchedFeedback,
    PortFailureFeedback,
)
from autonomous_mixing.core.records.identifiers import ArtifactId, AttemptId, DecisionCallId, ExecutionId, Revision
from autonomous_mixing.core.records.rendering import CandidateIdentity, ExecutionWorkspace
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, Success
from autonomous_mixing.core.records.run_trace import OperationKind
from autonomous_mixing.core.records.termination import TerminationReason

_ABSTENTION_REASONS = {
    "insufficient_evidence": TerminationReason.INSUFFICIENT_EVIDENCE,
    "conflicting_constraints": TerminationReason.CONFLICTING_CONSTRAINTS,
    "no_justified_action": TerminationReason.NO_JUSTIFIED_ACTION,
}


def decision_round(
    progress: RuntimeProgress,
    *,
    clock: RunClock,
    decision_maker: DecisionMaker,
    diagnosis_source: DiagnosisSource,
    identities: IdentitySource,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop | PreparedCorrection | None]:
    """None means another round follows; a stop ends the run."""
    state = progress.accepted.state
    progress, (diagnosis, stop) = diagnose(
        progress,
        state,
        clock=clock,
        diagnosis_source=diagnosis_source,
        identities=identities,
        journal=journal,
        on_progress=on_progress,
    )
    if stop:
        return (progress, stop)
    capabilities = build_action_capability_view(
        state=state,
        scope=progress.request.configuration.scope,
        context=progress.request.configuration.context,
        policy=progress.request.policy,
        renderer=progress.request.renderer_capabilities,
    )
    retries = 0
    while True:
        progress, call_id = fresh(progress, DecisionCallId, identities=identities, on_progress=on_progress)
        if call_id is None:
            return (progress, identity_failure("decision call ID"))

        def build(charged, call_id: DecisionCallId = call_id):
            return Situation(
                state,
                progress.request.configuration.context,
                progress.request.configuration.controller,
                _observations_at(progress, state.revision),
                charged,
                progress.request.configuration.budget_limits,
                progress.request.run_id,
                progress.request.configuration.scope,
                progress.request.configuration.criteria,
                diagnosis,
                capabilities,
                DecisionHistory(
                    progress.request.run_id, progress.request.initial_state.project_id, tuple(progress.history)
                ),
            )

        progress, (intent_id, situation, stop, skipped) = gate(
            progress,
            OperationKind.DECIDE,
            build,
            BudgetOperation.DECISION_CALL,
            call=call_id,
            clock=clock,
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
        if skipped:
            outcome = NotDispatchedFeedback(stop[0].value, stop[1])
            progress, feedback_stop = add_feedback(
                progress, call_id, state, outcome, identities=identities, journal=journal, on_progress=on_progress
            )
            if not feedback_stop:
                feedback_stop = stop
            return (progress, feedback_stop)
        if stop:
            return (progress, stop)
        result = decision_maker.decide(situation)
        if isinstance(result, PortFailure):
            progress, stop = observed(
                progress,
                intent_id,
                OperationKind.DECIDE,
                result,
                clock=clock,
                identities=identities,
                journal=journal,
                on_progress=on_progress,
            )
            if not stop:
                progress, stop = add_feedback(
                    progress,
                    call_id,
                    state,
                    PortFailureFeedback(result),
                    identities=identities,
                    journal=journal,
                    on_progress=on_progress,
                )
            if stop:
                return (progress, stop)
            if result.code is PortFailureCode.UNAVAILABLE and result.retryable:
                if progress.last.cancelled:
                    return (
                        progress,
                        (TerminationReason.USER_CANCELLED, "cancelled after a retryable decision failure"),
                    )
                assessment = check_budget(
                    BudgetOperation.DECISION_CALL,
                    limits=progress.request.configuration.budget_limits,
                    usage=progress.usage,
                    elapsed_seconds=progress.usage.elapsed_seconds,
                )
                if isinstance(assessment, BudgetBlock):
                    reason = TerminationReason[assessment.reason.name]
                    progress, recording_stop = record(
                        progress,
                        assessment,
                        assessment.usage,
                        identities=identities,
                        journal=journal,
                        on_progress=on_progress,
                    )
                    if not recording_stop:
                        recording_stop = (
                            reason,
                            f"no decision retry: budget blocks the next call ({assessment.reason.value})",
                        )
                    return (progress, recording_stop)
                if retries < progress.request.retry_policy.max_retries:
                    retries += 1
                    continue
                return (
                    progress,
                    (TerminationReason.DECISION_RETRIES_EXHAUSTED, f"decision retries exhausted: {result.message}"),
                )
            return (
                progress,
                (TerminationReason.DECISION_FAILED, f"decision failed: {result.code.value}: {result.message}"),
            )
        if not isinstance(result, Success) or not isinstance(result.value, DecisionResponse):
            error = "the decision port returned neither a PortFailure nor Success(DecisionResponse)"
            progress, stop = observed(
                progress,
                intent_id,
                OperationKind.DECIDE,
                _invalid(result, error),
                clock=clock,
                identities=identities,
                journal=journal,
                on_progress=on_progress,
            )
            if not stop:
                progress, stop = add_feedback(
                    progress,
                    call_id,
                    state,
                    InvalidResponseFeedback("invalid_port_response", error),
                    identities=identities,
                    journal=journal,
                    on_progress=on_progress,
                )
            return (progress, stop or (TerminationReason.INVALID_DECISION_RESPONSE, error))
        response = result.value
        progress, stop = observed(
            progress,
            intent_id,
            OperationKind.DECIDE,
            response,
            clock=clock,
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
        if stop:
            return (progress, stop)
        progress, evaluate_decision_result = evaluate_decision(
            progress,
            call_id,
            situation,
            response,
            capabilities,
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, evaluate_decision_result)
    return (progress, None)


def evaluate_decision(
    progress: RuntimeProgress,
    call_id: DecisionCallId,
    situation: Situation,
    response: DecisionResponse,
    capabilities: ActionCapabilityView,
    *,
    identities: IdentitySource,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop | PreparedCorrection | None]:
    state, decision = (situation.state, response.decision)
    attempt_id = None
    if isinstance(decision.outcome, ProposeAction) and _structurally_valid(situation, response):
        assessment = check_budget(
            BudgetOperation.ATTEMPT,
            limits=progress.request.configuration.budget_limits,
            usage=progress.usage,
            elapsed_seconds=progress.usage.elapsed_seconds,
        )
        if isinstance(assessment, BudgetBlock):
            reason = TerminationReason[assessment.reason.name]
            outcome = _rejection(
                decision.outcome.action,
                None,
                FeedbackStage.EVIDENCE_GATE,
                reason.value,
                "the attempt budget blocks checking this proposal",
                not_executed=True,
            )
            progress, stop = record(
                progress, assessment, assessment.usage, identities=identities, journal=journal, on_progress=on_progress
            )
            if not stop:
                progress, stop = add_feedback(
                    progress,
                    call_id,
                    state,
                    outcome,
                    decision_id=decision.decision_id,
                    identities=identities,
                    journal=journal,
                    on_progress=on_progress,
                )
            return (progress, stop or (reason, f"budget blocks the next attempt: {assessment.reason.value}"))
        progress = replace(progress, usage=assessment.usage_after_charge)
        on_progress(progress)
        progress, attempt_id = fresh(progress, AttemptId, identities=identities, on_progress=on_progress)
        if attempt_id is None:
            return (progress, identity_failure("attempt ID"))
    route = assess_decision(situation, response)
    progress, stop = record(progress, route, identities=identities, journal=journal, on_progress=on_progress)
    if stop:
        return (progress, stop)
    did = decision.decision_id
    match route:
        case InvalidResponse():
            outcome = InvalidResponseFeedback("invalid_decision_response", route.explanation)
            progress, feedback_stop = add_feedback(
                progress, call_id, state, outcome, identities=identities, journal=journal, on_progress=on_progress
            )
            if not feedback_stop:
                feedback_stop = (
                    TerminationReason.INVALID_DECISION_RESPONSE,
                    f"invalid decision response: {route.explanation}",
                )
            return (progress, feedback_stop)
        case KeepState():
            outcome = NoChangeFeedback(decision.outcome.explanation)
            progress, feedback_stop_2 = add_feedback(
                progress,
                call_id,
                state,
                outcome,
                decision_id=did,
                identities=identities,
                journal=journal,
                on_progress=on_progress,
            )
            if not feedback_stop_2:
                feedback_stop_2 = (
                    TerminationReason.NO_CHANGE_PROPOSED,
                    f"the controller proposed no change: {decision.outcome.explanation}",
                )
            return (progress, feedback_stop_2)
        case RequestContext():
            questions = decision.outcome.requested_context
            outcome = ContextRequestFeedback(questions, "the controller requested missing context")
            progress, feedback_stop_3 = add_feedback(
                progress,
                call_id,
                state,
                outcome,
                decision_id=did,
                identities=identities,
                journal=journal,
                on_progress=on_progress,
            )
            if not feedback_stop_3:
                feedback_stop_3 = (
                    TerminationReason.MISSING_CONTEXT,
                    "missing context; a new run is needed after an update. Questions: " + " | ".join(questions),
                )
            return (progress, feedback_stop_3)
        case DeclineAction():
            code = decision.outcome.reason.value
            outcome = AbstentionFeedback(code, f"the controller abstained: {code}")
            progress, feedback_stop_4 = add_feedback(
                progress,
                call_id,
                state,
                outcome,
                decision_id=did,
                identities=identities,
                journal=journal,
                on_progress=on_progress,
            )
            if not feedback_stop_4:
                feedback_stop_4 = (_ABSTENTION_REASONS[code], f"the controller abstained: {code}")
            return (progress, feedback_stop_4)
        case EvidenceBlocked():
            blocked = ", ".join((str(c) for c in route.blocked_candidate_ids))
            outcome = _rejection(
                decision.outcome.action,
                attempt_id,
                FeedbackStage.EVIDENCE_GATE,
                "insufficient_evidence",
                f"candidates lack available evidence: {blocked}",
            )
            progress, feedback_stop_5 = add_feedback(
                progress,
                call_id,
                state,
                outcome,
                decision_id=did,
                identities=identities,
                journal=journal,
                on_progress=on_progress,
            )
            if not feedback_stop_5:
                feedback_stop_5 = (
                    TerminationReason.INSUFFICIENT_EVIDENCE,
                    f"the proposal's candidates lack sufficient available evidence: {blocked}",
                )
            return (progress, feedback_stop_5)
        case ReadyForAdmission():
            progress, act_result = act(
                progress,
                call_id,
                situation,
                decision,
                attempt_id,
                capabilities,
                identities=identities,
                journal=journal,
                on_progress=on_progress,
            )
            return (progress, act_result)
        case _:
            assert_never(route)
    return (progress, None)


def act(
    progress: RuntimeProgress,
    call_id: DecisionCallId,
    situation: Situation,
    decision: Decision,
    attempt_id: AttemptId,
    capabilities: ActionCapabilityView,
    *,
    identities: IdentitySource,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop | PreparedCorrection | None]:
    state, action = (situation.state, decision.outcome.action)
    did = decision.decision_id

    def reject(
        progress: RuntimeProgress,
        rejection,
        stage: FeedbackStage,
        code,
        *,
        identities: IdentitySource,
        journal: RunJournal,
        on_progress: Callable[[RuntimeProgress], None],
    ) -> tuple[RuntimeProgress, Stop | None]:
        progress, stop = record(progress, rejection, identities=identities, journal=journal, on_progress=on_progress)
        outcome = _rejection(action, attempt_id, stage, code, rejection.explanation)
        pending_stop = stop
        if not pending_stop:
            progress, pending_stop = add_feedback(
                progress,
                call_id,
                state,
                outcome,
                decision_id=did,
                identities=identities,
                journal=journal,
                on_progress=on_progress,
            )
        return (progress, pending_stop)

    admission = admit_action(ActionAdmissionRequest(situation, decision, progress.request.policy))
    if isinstance(admission, RejectedAction):
        progress, reject_result = reject(
            progress,
            admission,
            FeedbackStage.ADMISSION,
            admission.code.value,
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, reject_result)
    progress, stop = record(progress, admission, identities=identities, journal=journal, on_progress=on_progress)
    if stop:
        return (progress, stop)
    available = {gain.track_id: gain.bounds for gain in capabilities.gain_actions}
    operation = action.operation
    bounds = available.get(operation.track_id)
    if bounds is None or not bounds.minimum_db <= operation.value_db <= bounds.maximum_db:
        code = AdmissionRejectionCode.NO_GAIN_PERMISSION if bounds is None else AdmissionRejectionCode.OUT_OF_BOUNDS
        explanation = (
            f"track {operation.track_id} is not an advertised available gain action"
            if bounds is None
            else f"gain {operation.value_db} dB lies outside the advertised range [{bounds.minimum_db}, {bounds.maximum_db}] dB"
        )
        progress, reject_result_2 = reject(
            progress,
            RejectedAction(did, code, explanation),
            FeedbackStage.ADMISSION,
            code.value,
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, reject_result_2)
    progress, execution_id = fresh(progress, ExecutionId, identities=identities, on_progress=on_progress)
    token = identities.next_token()
    progress, revision = fresh(progress, Revision, identities=identities, on_progress=on_progress)
    progress, identity = fresh(progress, ArtifactId, identities=identities, on_progress=on_progress)
    progress, identity_2 = fresh(progress, ArtifactId, identities=identities, on_progress=on_progress)
    target_id, new_mix_id = (identity, identity_2)
    if (
        None in (execution_id, revision, target_id, new_mix_id)
        or not isinstance(token, str)
        or token in progress.tokens
    ):
        return (progress, identity_failure("execution identity"))
    progress = replace(progress, tokens=progress.tokens | {token})
    on_progress(progress)
    try:
        workspace = ExecutionWorkspace(execution_id, progress.request.output_root_key, token)
        candidate_identity = CandidateIdentity(revision, target_id, new_mix_id)
    except ValueError as error:
        return (
            progress,
            (TerminationReason.RUNTIME_CONTRACT_FAILED, f"generated execution identity is invalid: {error}"),
        )
    execution_request = ExecutionRequest(
        execution_id,
        situation,
        decision,
        progress.request.policy,
        progress.request.recipe,
        tuple((StemBinding(track, _stem_id(progress, track)) for track in progress.request.recipe.track_ids)),
        progress.mix_id,
        progress.request.configuration.render_spec,
        progress.request.processing_spec,
        workspace,
        candidate_identity,
    )
    preflight = preflight_execution(execution_request)
    if isinstance(preflight, PreflightRejected):
        progress, reject_result_3 = reject(
            progress,
            preflight,
            FeedbackStage.PREFLIGHT,
            preflight.code.value,
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, reject_result_3)
    progress, stop = record(progress, preflight, identities=identities, journal=journal, on_progress=on_progress)
    if stop:
        return (progress, stop)
    return (progress, PreparedCorrection(call_id, state, decision, attempt_id, preflight.plan))


def _structurally_valid(situation: Situation, response: DecisionResponse) -> bool:
    try:
        validate_decision_response(situation, response)
    except ValueError:
        return False
    return True
