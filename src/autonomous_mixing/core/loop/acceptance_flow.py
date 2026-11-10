"""Acceptance flow responsibilities of the controlled serial coordinator: the validated acceptance event, its
post-acknowledgement gate and commit dispatch, the commit response (committed, refused or unknown) and the
accepted-attempt record and feedback that complete the original route.
"""

from collections.abc import Callable
from dataclasses import replace

from autonomous_mixing.core.loop.acceptance import validate_acceptance_event
from autonomous_mixing.core.loop.feedback import add_feedback, copied
from autonomous_mixing.core.loop.recovery import failed_attempt, reconcile_unknown
from autonomous_mixing.core.loop.runtime import checkpoint, fresh, identity_failure, promote, record
from autonomous_mixing.core.ports.acceptance import validate_acceptance_commit_response
from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource, RunClock
from autonomous_mixing.core.ports.integrity import StateIntegrityInspector
from autonomous_mixing.core.ports.run_journal import RunJournal
from autonomous_mixing.core.records.acceptance import (
    AcceptanceCommitted,
    AcceptanceCommitUnknown,
    AcceptanceEvent,
    AcceptanceNotCommitted,
)
from autonomous_mixing.core.records.actions import ExecutionStatus
from autonomous_mixing.core.records.coordinator_progress import AssessedCorrection, RuntimeProgress, Stop
from autonomous_mixing.core.records.feedback import ActionFeedback, ActionFeedbackDisposition, FeedbackStage
from autonomous_mixing.core.records.identifiers import AcceptanceId
from autonomous_mixing.core.records.run import AttemptDisposition, CorrectionAttempt
from autonomous_mixing.core.records.termination import TerminationReason
from autonomous_mixing.core.records.verification_routing import VerificationRoute


def accept(
    progress: RuntimeProgress,
    ctx: AssessedCorrection,
    route: VerificationRoute,
    *,
    clock: RunClock,
    identities: IdentitySource,
    integrity_inspector: StateIntegrityInspector,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop | None]:
    progress, stop = checkpoint(progress, clock=clock, on_progress=on_progress)
    if (
        stop
        or progress.last.cancelled
        or progress.usage.elapsed_seconds >= progress.request.configuration.budget_limits.max_elapsed_seconds
    ):
        original = stop or (
            (TerminationReason.USER_CANCELLED, "cancelled before the acceptance commit")
            if progress.last.cancelled
            else (TerminationReason.ELAPSED_TIME_LIMIT, "time ran out before the acceptance commit")
        )
        progress, failed_attempt_result = failed_attempt(
            progress,
            ctx,
            ExecutionStatus.CANDIDATE_READY,
            original,
            stage=FeedbackStage.ACCEPTANCE,
            verification=ctx.verification,
            bundles=ctx.bundles,
            clock=clock,
            identities=identities,
            integrity_inspector=integrity_inspector,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, failed_attempt_result)
    progress, acceptance_id = fresh(progress, AcceptanceId, identities=identities, on_progress=on_progress)
    if acceptance_id is None:
        progress, failed_attempt_result_2 = failed_attempt(
            progress,
            ctx,
            ExecutionStatus.CANDIDATE_READY,
            identity_failure("acceptance ID"),
            stage=FeedbackStage.ACCEPTANCE,
            clock=clock,
            identities=identities,
            integrity_inspector=integrity_inspector,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, failed_attempt_result_2)
    event = AcceptanceEvent(
        acceptance_id,
        progress.request.run_id,
        ctx.attempt_id,
        ctx.decision.decision_id,
        progress.accepted,
        ctx.action,
        ctx.receipt,
        ctx.assessment,
        ctx.verification,
    )
    validate_acceptance_event(event)
    progress, stop = record(progress, event, identities=identities, journal=journal, on_progress=on_progress)
    if stop:
        progress, failed_attempt_result_3 = failed_attempt(
            progress,
            ctx,
            ExecutionStatus.CANDIDATE_READY,
            stop,
            stage=FeedbackStage.RECORDING,
            verification=ctx.verification,
            bundles=ctx.bundles,
            clock=clock,
            identities=identities,
            integrity_inspector=integrity_inspector,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, failed_attempt_result_3)
    progress, stop = checkpoint(progress, clock=clock, on_progress=on_progress)
    if (
        stop
        or progress.last.cancelled
        or progress.usage.elapsed_seconds >= progress.request.configuration.budget_limits.max_elapsed_seconds
    ):
        original = stop or (
            (TerminationReason.USER_CANCELLED, "cancelled after recording the acceptance event; not committed")
            if progress.last.cancelled
            else (
                TerminationReason.ELAPSED_TIME_LIMIT,
                "time ran out after recording the acceptance event; not committed",
            )
        )
        progress, failed_attempt_result_4 = failed_attempt(
            progress,
            ctx,
            ExecutionStatus.CANDIDATE_READY,
            original,
            stage=FeedbackStage.ACCEPTANCE,
            verification=ctx.verification,
            bundles=ctx.bundles,
            clock=clock,
            identities=identities,
            integrity_inspector=integrity_inspector,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, failed_attempt_result_4)
    response = journal.commit(event)
    try:
        validate_acceptance_commit_response(event, response)
    except ValueError as error:
        response = AcceptanceCommitUnknown(event.acceptance_id, f"invalid commit response: {error}")
    if isinstance(response, AcceptanceCommitted):
        progress = replace(progress, commits=progress.commits + (response,))
        on_progress(progress)
        progress, promote_result = promote(progress, response.accepted, ctx, on_progress=on_progress)
        progress, stop = record(progress, response, identities=identities, journal=journal, on_progress=on_progress)
        pending_stop = stop
        if not pending_stop:
            progress, pending_stop = finish_acceptance(
                progress, ctx, route, identities=identities, journal=journal, on_progress=on_progress
            )
        return (progress, pending_stop)
    if isinstance(response, AcceptanceNotCommitted):
        progress, stop = record(progress, response, identities=identities, journal=journal, on_progress=on_progress)
        original = stop or (TerminationReason.RECORDING_FAILED, f"acceptance not committed: {response.explanation}")
        progress, failed_attempt_result_5 = failed_attempt(
            progress,
            ctx,
            ExecutionStatus.CANDIDATE_READY,
            original,
            stage=FeedbackStage.ACCEPTANCE,
            verification=ctx.verification,
            bundles=ctx.bundles,
            clock=clock,
            identities=identities,
            integrity_inspector=integrity_inspector,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, failed_attempt_result_5)
    progress, recording_stop = record(
        progress, response, identities=identities, journal=journal, on_progress=on_progress
    )
    progress, result = reconcile_unknown(
        progress,
        ctx,
        event,
        route,
        clock=clock,
        identities=identities,
        integrity_inspector=integrity_inspector,
        journal=journal,
        on_progress=on_progress,
    )
    if isinstance(result, VerificationRoute):
        progress, finish_acceptance_result = finish_acceptance(
            progress, ctx, result, identities=identities, journal=journal, on_progress=on_progress
        )
        return (progress, finish_acceptance_result)
    return (progress, result)


def finish_acceptance(
    progress: RuntimeProgress,
    ctx: AssessedCorrection,
    route: VerificationRoute,
    *,
    identities: IdentitySource,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop | None]:
    attempt = CorrectionAttempt(
        ctx.attempt_id,
        progress.request.run_id,
        ctx.decision.decision_id,
        ctx.action.action_id,
        ctx.before,
        ctx.receipt,
        ctx.candidate_state,
        ctx.verification,
        None,
        AttemptDisposition.ACCEPTED,
    )
    outcome = ActionFeedback(
        ctx.action,
        ctx.attempt_id,
        ctx.execution_id,
        ActionFeedbackDisposition.ACCEPTED,
        FeedbackStage.ACCEPTANCE,
        ExecutionStatus.CANDIDATE_READY,
        ctx.verification,
        None,
        "accepted",
        "candidate verified and its acceptance committed",
        copied(ctx.verification, ctx.bundles),
    )
    progress, stop = record(progress, attempt, identities=identities, journal=journal, on_progress=on_progress)
    if not stop:
        progress, stop = add_feedback(
            progress,
            ctx.call_id,
            ctx.before,
            outcome,
            decision_id=ctx.decision.decision_id,
            candidate=ctx.candidate_revision,
            selected=ctx.candidate_revision,
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
    if stop:
        return (progress, stop)
    if route is VerificationRoute.COMMIT_THEN_FINISH:
        return (progress, (TerminationReason.CANDIDATE_CRITERIA_MET, "the accepted candidate satisfies every target"))
    return (progress, None)
