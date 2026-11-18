"""Correction responsibilities of the controlled serial coordinator: the render dispatch, the execution
receipt and derived candidate state, the single candidate analysis and the ATTEMPT verification, turning a
PreparedCorrection into an AssessedCorrection and routing it to acceptance or recovery.
"""

from collections.abc import Callable
from dataclasses import replace

from autonomous_mixing.core.loop.acceptance_flow import accept
from autonomous_mixing.core.loop.feedback import _rejection, add_feedback
from autonomous_mixing.core.loop.observation import analysis_bindings, analyze, bindings_for, evidence_for
from autonomous_mixing.core.loop.recovery import failed_attempt, restore_after_rejection
from autonomous_mixing.core.loop.runtime import _invalid, fresh, gate, identity_failure, observed, record
from autonomous_mixing.core.loop.transitions import derive_candidate_project_state
from autonomous_mixing.core.loop.verification_routing import route_verification
from autonomous_mixing.core.ports.analysis import AudioAnalyzer
from autonomous_mixing.core.ports.coordinator_runtime import (
    IdentitySource,
    RunClock,
    RunRenderer,
    validate_run_render_response,
)
from autonomous_mixing.core.ports.integrity import StateIntegrityInspector
from autonomous_mixing.core.ports.run_journal import RunJournal
from autonomous_mixing.core.records.actions import ExecutionReceipt, ExecutionStatus
from autonomous_mixing.core.records.budgets import BudgetOperation
from autonomous_mixing.core.records.coordinator_progress import (
    AssessedCorrection,
    PreparedCorrection,
    RuntimeProgress,
    Stop,
)
from autonomous_mixing.core.records.coordinator_runtime import RenderPreparationFailure, RunRenderRequest
from autonomous_mixing.core.records.feedback import FeedbackStage
from autonomous_mixing.core.records.identifiers import CheckpointId, VerificationResultId
from autonomous_mixing.core.records.integrity import ArtifactRootBinding, StateIntegrityRequest
from autonomous_mixing.core.records.rendering import RenderedCandidate, RenderFailure, RenderPlan
from autonomous_mixing.core.records.run_trace import OperationKind
from autonomous_mixing.core.records.termination import TerminationReason
from autonomous_mixing.core.records.verification import AssessmentMode
from autonomous_mixing.core.records.verification_assessment import VerificationAssessment
from autonomous_mixing.core.records.verification_routing import VerificationRoute
from autonomous_mixing.core.verification.assessment import assess_verification


def render(
    progress: RuntimeProgress,
    ctx: PreparedCorrection,
    *,
    analyzer: AudioAnalyzer,
    clock: RunClock,
    identities: IdentitySource,
    integrity_inspector: StateIntegrityInspector,
    journal: RunJournal,
    renderer: RunRenderer,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop | None]:
    plan = ctx.plan
    state, action = (ctx.before, ctx.action)
    call_id, decision, attempt_id = (ctx.call_id, ctx.decision, ctx.attempt_id)
    before_request = StateIntegrityRequest(state, bindings_for(progress, state))
    render_request = RunRenderRequest(plan, before_request, before_request.required_artifacts, before_request.bindings)
    progress, (intent_id, _, stop, skipped) = gate(
        progress,
        OperationKind.RENDER,
        lambda charged: render_request,
        BudgetOperation.RENDER_CALL,
        call=call_id,
        attempt=attempt_id,
        execution=plan.execution_id,
        clock=clock,
        identities=identities,
        journal=journal,
        on_progress=on_progress,
    )
    if stop:
        stage = FeedbackStage.RENDER if skipped else FeedbackStage.PREPARATION
        outcome = _rejection(action, attempt_id, stage, stop[0].value, stop[1], not_executed=True)
        progress, feedback_stop = add_feedback(
            progress,
            call_id,
            state,
            outcome,
            decision_id=decision.decision_id,
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
        if not feedback_stop:
            feedback_stop = stop
        return (progress, feedback_stop)
    progress = replace(progress, verified=None)
    on_progress(progress)
    response = renderer.process(render_request)
    try:
        validate_run_render_response(render_request, response)
    except ValueError as error:
        progress, stop = observed(
            progress,
            intent_id,
            OperationKind.RENDER,
            _invalid(response, error),
            clock=clock,
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
        original = stop or (TerminationReason.INVALID_RENDER_RESPONSE, f"invalid render response: {error}")
        progress, failed_attempt_result_4 = failed_attempt(
            progress,
            ctx,
            ExecutionStatus.UNCERTAIN,
            original,
            stage=FeedbackStage.RENDER,
            clock=clock,
            identities=identities,
            integrity_inspector=integrity_inspector,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, failed_attempt_result_4)
    progress, stop = observed(
        progress,
        intent_id,
        OperationKind.RENDER,
        response,
        clock=clock,
        identities=identities,
        journal=journal,
        on_progress=on_progress,
    )
    if isinstance(response, RenderPreparationFailure):
        original = stop or (
            TerminationReason.EXECUTION_PREPARATION_FAILED,
            f"render preparation failed: {response.issue.message}",
        )
        progress, failed_attempt_result = failed_attempt(
            progress,
            ctx,
            ExecutionStatus.NOT_STARTED,
            original,
            stage=FeedbackStage.PREPARATION,
            clock=clock,
            identities=identities,
            integrity_inspector=integrity_inspector,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, failed_attempt_result)
    if isinstance(response, RenderFailure):
        original = stop or (
            TerminationReason.RENDER_FAILED,
            f"render {response.status.value}: {response.issue.message}",
        )
        progress, failed_attempt_result_2 = failed_attempt(
            progress,
            ctx,
            response.status,
            original,
            stage=FeedbackStage.RENDER,
            clock=clock,
            identities=identities,
            integrity_inspector=integrity_inspector,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, failed_attempt_result_2)
    if stop:
        progress, failed_attempt_result_3 = failed_attempt(
            progress,
            ctx,
            ExecutionStatus.CANDIDATE_READY,
            stop,
            stage=FeedbackStage.RECORDING,
            clock=clock,
            identities=identities,
            integrity_inspector=integrity_inspector,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, failed_attempt_result_3)
    progress, verify_candidate_result = verify_candidate(
        progress,
        ctx,
        plan,
        response,
        analyzer=analyzer,
        clock=clock,
        identities=identities,
        integrity_inspector=integrity_inspector,
        journal=journal,
        on_progress=on_progress,
    )
    return (progress, verify_candidate_result)


def verify_candidate(
    progress: RuntimeProgress,
    ctx: PreparedCorrection,
    plan: RenderPlan,
    rendered: RenderedCandidate,
    *,
    analyzer: AudioAnalyzer,
    clock: RunClock,
    identities: IdentitySource,
    integrity_inspector: StateIntegrityInspector,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop | None]:
    before = ctx.before
    receipt = ExecutionReceipt(
        plan.execution_id,
        ctx.action.action_id,
        plan.project_id,
        plan.before_revision,
        ExecutionStatus.CANDIDATE_READY,
        plan.candidate.revision,
        (rendered.target_artifact, rendered.mix_artifact),
        (plan.target_current_artifact_id, plan.current_mix_artifact_id),
        None,
    )
    progress, checkpoint_id = fresh(progress, CheckpointId, identities=identities, on_progress=on_progress)
    if checkpoint_id is None:
        progress, failed_attempt_result = failed_attempt(
            progress,
            ctx,
            ExecutionStatus.CANDIDATE_READY,
            identity_failure("checkpoint ID"),
            stage=FeedbackStage.RECORDING,
            clock=clock,
            identities=identities,
            integrity_inspector=integrity_inspector,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, failed_attempt_result)
    try:
        candidate = derive_candidate_project_state(before, receipt, ctx.action, checkpoint_id, progress.last.utc)
    except ValueError as error:
        original = (TerminationReason.INVALID_RENDER_RESPONSE, f"rendered outputs do not form a candidate: {error}")
        progress, failed_attempt_result_7 = failed_attempt(
            progress,
            ctx,
            ExecutionStatus.UNCERTAIN,
            original,
            stage=FeedbackStage.RENDER,
            clock=clock,
            identities=identities,
            integrity_inspector=integrity_inspector,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, failed_attempt_result_7)
    progress, stop = record(progress, receipt, identities=identities, journal=journal, on_progress=on_progress)
    if stop:
        progress, failed_attempt_result_2 = failed_attempt(
            progress,
            ctx,
            ExecutionStatus.CANDIDATE_READY,
            stop,
            stage=FeedbackStage.RECORDING,
            clock=clock,
            identities=identities,
            integrity_inspector=integrity_inspector,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, failed_attempt_result_2)
    progress, (candidate_observations, stop) = analyze(
        progress,
        candidate,
        TerminationReason.CANDIDATE_ANALYSIS_FAILED,
        # Unaccepted outputs are located through the workspace root the plan assigned them; retained members
        # keep their known roots. Nothing is promoted to locate files, and no other ID receives a root.
        bindings=analysis_bindings(
            candidate,
            progress.roots,
            tuple(ArtifactRootBinding(a.artifact_id, plan.workspace.root_key) for a in receipt.candidate_artifacts),
        ),
        analyzer=analyzer,
        clock=clock,
        identities=identities,
        journal=journal,
        on_progress=on_progress,
    )
    if candidate_observations is None:
        progress, failed_attempt_result_3 = failed_attempt(
            progress,
            ctx,
            ExecutionStatus.CANDIDATE_READY,
            stop,
            stage=FeedbackStage.VERIFICATION,
            clock=clock,
            identities=identities,
            integrity_inspector=integrity_inspector,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, failed_attempt_result_3)
    if stop:
        progress, failed_attempt_result_4 = failed_attempt(
            progress,
            ctx,
            ExecutionStatus.CANDIDATE_READY,
            stop,
            stage=FeedbackStage.RECORDING,
            clock=clock,
            identities=identities,
            integrity_inspector=integrity_inspector,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, failed_attempt_result_4)
    progress, result_id = fresh(progress, VerificationResultId, identities=identities, on_progress=on_progress)
    if result_id is None:
        progress, failed_attempt_result_5 = failed_attempt(
            progress,
            ctx,
            ExecutionStatus.CANDIDATE_READY,
            identity_failure("verification ID"),
            stage=FeedbackStage.VERIFICATION,
            clock=clock,
            identities=identities,
            integrity_inspector=integrity_inspector,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, failed_attempt_result_5)
    bundles = (
        evidence_for(progress, candidate),
        evidence_for(progress, before),
        evidence_for(progress, progress.request.initial_state),
    )
    try:
        assessment = VerificationAssessment(
            result_id,
            AssessmentMode.ATTEMPT,
            progress.request.configuration.criteria,
            progress.request.measurements,
            *bundles,
        )
    except ValueError as error:
        original = (TerminationReason.RUNTIME_CONTRACT_FAILED, f"candidate evidence is inconsistent: {error}")
        progress, failed_attempt_result_8 = failed_attempt(
            progress,
            ctx,
            ExecutionStatus.CANDIDATE_READY,
            original,
            stage=FeedbackStage.VERIFICATION,
            clock=clock,
            identities=identities,
            integrity_inspector=integrity_inspector,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, failed_attempt_result_8)
    verification = assess_verification(assessment)
    progress, stop = record(progress, assessment, identities=identities, journal=journal, on_progress=on_progress)
    if not stop:
        progress, stop = record(progress, verification, identities=identities, journal=journal, on_progress=on_progress)
    if stop:
        progress, failed_attempt_result_6 = failed_attempt(
            progress,
            ctx,
            ExecutionStatus.CANDIDATE_READY,
            stop,
            stage=FeedbackStage.RECORDING,
            clock=clock,
            identities=identities,
            integrity_inspector=integrity_inspector,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, failed_attempt_result_6)
    ctx = AssessedCorrection(
        ctx.call_id, ctx.before, ctx.decision, ctx.attempt_id, ctx.plan, receipt, candidate, assessment, verification
    )
    route = route_verification(verification, progress.request.configuration.criteria)
    match route:
        case VerificationRoute.COMMIT_THEN_FINISH | VerificationRoute.COMMIT_THEN_ITERATE:
            progress, accept_result = accept(
                progress,
                ctx,
                route,
                clock=clock,
                identities=identities,
                integrity_inspector=integrity_inspector,
                journal=journal,
                on_progress=on_progress,
            )
            return (progress, accept_result)
        case VerificationRoute.RESTORE_THEN_CONSIDER_ITERATION:
            progress, restore_after_rejection_result = restore_after_rejection(
                progress,
                ctx,
                clock=clock,
                identities=identities,
                integrity_inspector=integrity_inspector,
                journal=journal,
                on_progress=on_progress,
            )
            return (progress, restore_after_rejection_result)
        case VerificationRoute.RECONCILE_THEN_FINISH_INCONCLUSIVE:
            original = (TerminationReason.VERIFICATION_INCONCLUSIVE, "candidate verification is inconclusive")
            progress, failed_attempt_result_9 = failed_attempt(
                progress,
                ctx,
                ExecutionStatus.CANDIDATE_READY,
                original,
                stage=FeedbackStage.VERIFICATION,
                verification=verification,
                bundles=bundles,
                clock=clock,
                identities=identities,
                integrity_inspector=integrity_inspector,
                journal=journal,
                on_progress=on_progress,
            )
            return (progress, failed_attempt_result_9)
        case _:
            raise ValueError(f"an ATTEMPT verification cannot take route {route.name}")
    return (progress, None)
