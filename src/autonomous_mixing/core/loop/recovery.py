"""Recovery responsibilities of the controlled serial coordinator: inspection of the saved accepted state
after an unaccepted effect, logical restoration after a rejection, terminal-reason precedence for failed or
unperformed recovery, and reconciliation of an unknown acceptance acknowledgement. A confirmed acceptance
is returned as its VerificationRoute for acceptance_flow to complete, so the two modules never import each
other.
"""

from collections.abc import Callable
from dataclasses import replace
from typing import assert_never

from autonomous_mixing.core.loop.feedback import _effect_feedback, add_feedback, copied
from autonomous_mixing.core.loop.observation import inspect
from autonomous_mixing.core.loop.reconciliation import reconcile_acceptance
from autonomous_mixing.core.loop.runtime import _invalid, gate, observed, promote, record
from autonomous_mixing.core.ports.acceptance import validate_acceptance_query_response
from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource, RunClock
from autonomous_mixing.core.ports.integrity import StateIntegrityInspector
from autonomous_mixing.core.ports.run_journal import RunJournal
from autonomous_mixing.core.records.acceptance import (
    AcceptanceAbsent,
    AcceptanceAuthorityUnknown,
    AcceptanceCommitted,
    AcceptanceEvent,
    AcceptanceFound,
    AcceptanceQuery,
)
from autonomous_mixing.core.records.actions import ExecutionStatus, RollbackReceipt, RollbackStatus
from autonomous_mixing.core.records.budgets import BudgetOperation
from autonomous_mixing.core.records.coordinator_progress import (
    AssessedCorrection,
    PreparedCorrection,
    RuntimeProgress,
    Stop,
)
from autonomous_mixing.core.records.feedback import FeedbackStage
from autonomous_mixing.core.records.integrity import IntegrityStatus
from autonomous_mixing.core.records.reconciliation import ReconciliationOutcome
from autonomous_mixing.core.records.run import AttemptDisposition, CorrectionAttempt
from autonomous_mixing.core.records.run_trace import OperationKind
from autonomous_mixing.core.records.termination import TerminationReason
from autonomous_mixing.core.records.verification import VerificationResult
from autonomous_mixing.core.records.verification_evidence import StateEvidence
from autonomous_mixing.core.records.verification_routing import VerificationRoute


def recover_saved(
    progress: RuntimeProgress,
    *,
    clock: RunClock,
    identities: IdentitySource,
    integrity_inspector: StateIntegrityInspector,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, tuple[RollbackStatus | None, Stop | None]]:
    """One recovery workflow: inspect the saved accepted state. None status means not performed."""
    progress, (status, _, _, stop, performed) = inspect(
        progress,
        progress.accepted.state,
        BudgetOperation.RECOVERY_OPERATION,
        clock=clock,
        identities=identities,
        integrity_inspector=integrity_inspector,
        journal=journal,
        on_progress=on_progress,
    )
    if not performed:
        return (progress, (None, stop))
    if status is IntegrityStatus.VERIFIED:
        selection = (progress.accepted, True)
        progress = replace(progress, verified=selection[0], restored_last=selection[1])
        on_progress(progress)
        return (progress, (RollbackStatus.VERIFIED, stop))
    progress = replace(progress, verified=None)
    on_progress(progress)
    return (progress, (RollbackStatus.FAILED if status is IntegrityStatus.FAILED else RollbackStatus.UNCERTAIN, stop))


def recovery_stop(rollback: RollbackStatus | None, inner: Stop | None, original: Stop) -> Stop:
    if rollback is None:
        explanation = "recovery of the saved accepted state could not be performed"
        if inner and inner[0] is not TerminationReason.RECORDING_FAILED:
            explanation += f" ({inner[0].value}: {inner[1]})"
        return (TerminationReason.REQUIRED_RECOVERY_NOT_PERFORMED, explanation)
    if rollback is RollbackStatus.FAILED:
        return (TerminationReason.RESTORATION_FAILED, "integrity of the saved accepted state failed")
    if rollback is RollbackStatus.UNCERTAIN:
        return (TerminationReason.RESTORATION_UNCERTAIN, "integrity of the saved accepted state is uncertain")
    return inner or original


def failed_attempt(
    progress: RuntimeProgress,
    ctx: PreparedCorrection,
    status: ExecutionStatus,
    original: Stop,
    *,
    stage: FeedbackStage,
    verification: VerificationResult | None = None,
    bundles: tuple[StateEvidence, ...] = (),
    clock: RunClock,
    identities: IdentitySource,
    integrity_inspector: StateIntegrityInspector,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop]:
    """Recover the saved state after an unaccepted effect, record feedback, and choose the stop."""
    progress, (rollback, inner) = recover_saved(
        progress,
        clock=clock,
        identities=identities,
        integrity_inspector=integrity_inspector,
        journal=journal,
        on_progress=on_progress,
    )
    outcome = _effect_feedback(
        ctx,
        status,
        rollback,
        stage=stage,
        verification=verification,
        observations=copied(verification, bundles),
        reason=original,
    )
    selected = progress.accepted.state.revision if rollback is RollbackStatus.VERIFIED else None
    candidate = ctx.candidate_revision if status is ExecutionStatus.CANDIDATE_READY else None
    progress, feedback_stop = add_feedback(
        progress,
        ctx.call_id,
        ctx.before,
        outcome,
        decision_id=ctx.decision.decision_id,
        candidate=candidate,
        selected=selected,
        identities=identities,
        journal=journal,
        on_progress=on_progress,
    )
    return (progress, recovery_stop(rollback, inner, original))


def restore_after_rejection(
    progress: RuntimeProgress,
    ctx: AssessedCorrection,
    *,
    clock: RunClock,
    identities: IdentitySource,
    integrity_inspector: StateIntegrityInspector,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop | None]:
    progress, (rollback, inner) = recover_saved(
        progress,
        clock=clock,
        identities=identities,
        integrity_inspector=integrity_inspector,
        journal=journal,
        on_progress=on_progress,
    )
    if rollback is not RollbackStatus.VERIFIED:
        original = (TerminationReason.RESTORATION_FAILED, "the saved state could not be verified after rejection")
        progress, failed_attempt_after_result = failed_attempt_after(
            progress, ctx, rollback, inner, original, identities=identities, journal=journal, on_progress=on_progress
        )
        return (progress, failed_attempt_after_result)
    before = ctx.before
    receipt = RollbackReceipt(
        ctx.execution_id,
        before.audio.checkpoint_id,
        RollbackStatus.VERIFIED,
        before,
        "logical retention of the saved accepted state; its integrity inspection was verified",
    )
    attempt = CorrectionAttempt(
        ctx.attempt_id,
        progress.request.run_id,
        ctx.decision.decision_id,
        ctx.action.action_id,
        before,
        ctx.receipt,
        ctx.candidate_state,
        ctx.verification,
        receipt,
        AttemptDisposition.ROLLED_BACK,
    )
    outcome = _effect_feedback(
        ctx,
        ExecutionStatus.CANDIDATE_READY,
        RollbackStatus.VERIFIED,
        stage=FeedbackStage.RECOVERY,
        verification=ctx.verification,
        observations=copied(ctx.verification, ctx.bundles),
        reason=(TerminationReason.RESTORATION_FAILED, "candidate rejected by verification"),
    )
    stop = inner
    if not stop:
        progress, stop = record(progress, attempt, identities=identities, journal=journal, on_progress=on_progress)
    if not stop:
        progress, stop = add_feedback(
            progress,
            ctx.call_id,
            before,
            outcome,
            decision_id=ctx.decision.decision_id,
            candidate=ctx.candidate_revision,
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
    return (progress, stop)


def failed_attempt_after(
    progress: RuntimeProgress,
    ctx: AssessedCorrection,
    rollback: RollbackStatus | None,
    inner: Stop | None,
    original: Stop,
    *,
    identities: IdentitySource,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop]:
    outcome = _effect_feedback(
        ctx,
        ExecutionStatus.CANDIDATE_READY,
        rollback,
        stage=FeedbackStage.RECOVERY,
        verification=ctx.verification,
        observations=copied(ctx.verification, ctx.bundles),
        reason=original,
    )
    progress, feedback_stop = add_feedback(
        progress,
        ctx.call_id,
        ctx.before,
        outcome,
        decision_id=ctx.decision.decision_id,
        candidate=ctx.candidate_revision,
        selected=None,
        identities=identities,
        journal=journal,
        on_progress=on_progress,
    )
    return (progress, recovery_stop(rollback, inner, original))


def reconcile_unknown(
    progress: RuntimeProgress,
    ctx: AssessedCorrection,
    event: AcceptanceEvent,
    route: VerificationRoute,
    *,
    clock: RunClock,
    identities: IdentitySource,
    integrity_inspector: StateIntegrityInspector,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop | VerificationRoute | None]:
    progress = replace(progress, authority_known=False)
    on_progress(progress)
    progress = replace(progress, verified=None)
    on_progress(progress)
    query = AcceptanceQuery(progress.request.run_id, event.acceptance_id)
    progress, (intent_id, _, stop, _) = gate(
        progress,
        OperationKind.QUERY_ACCEPTANCE,
        lambda charged: query,
        BudgetOperation.RECOVERY_OPERATION,
        clock=clock,
        identities=identities,
        journal=journal,
        on_progress=on_progress,
    )
    if intent_id is None or stop:
        return (
            progress,
            (TerminationReason.REQUIRED_RECOVERY_NOT_PERFORMED, "the acceptance authority could not be queried"),
        )
    answer = journal.query(query)
    try:
        validate_acceptance_query_response(query, answer)
    except ValueError as error:
        progress, query_stop = observed(
            progress,
            intent_id,
            OperationKind.QUERY_ACCEPTANCE,
            _invalid(answer, error),
            clock=clock,
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
        answer = AcceptanceAuthorityUnknown(
            progress.request.run_id, event.acceptance_id, f"invalid authority answer: {error}"
        )
    else:
        progress, query_stop = observed(
            progress,
            intent_id,
            OperationKind.QUERY_ACCEPTANCE,
            answer,
            clock=clock,
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
    if isinstance(answer, AcceptanceAuthorityUnknown):
        result = reconcile_acceptance(event, answer, integrity_request=None, integrity_report=None)
        progress, recording_stop = record(
            progress, result, identities=identities, journal=journal, on_progress=on_progress
        )
        return (
            progress,
            (TerminationReason.ACCEPTANCE_COMMIT_UNCERTAIN, f"acceptance authority unknown: {answer.explanation}"),
        )
    if isinstance(answer, AcceptanceFound):
        selection = (answer.accepted, True)
        progress = replace(progress, accepted=selection[0], authority_known=selection[1])
        on_progress(progress)
        if not any((c.event == answer.event for c in progress.commits)):
            progress = replace(
                progress, commits=progress.commits + (AcceptanceCommitted(answer.event, answer.accepted),)
            )
            on_progress(progress)
        target_state = answer.accepted.state
    elif isinstance(answer, AcceptanceAbsent):
        progress = replace(progress, authority_known=True)
        on_progress(progress)
        target_state = progress.accepted.state
    else:
        assert_never(answer)
    if query_stop:
        return (
            progress,
            (
                TerminationReason.REQUIRED_RECOVERY_NOT_PERFORMED,
                f"integrity was not inspected after the authority answer ({query_stop[0].value}: {query_stop[1]})",
            ),
        )
    progress, (status, report, integrity_request, inspect_stop, performed) = inspect(
        progress,
        target_state,
        None,
        clock=clock,
        identities=identities,
        integrity_inspector=integrity_inspector,
        journal=journal,
        on_progress=on_progress,
    )
    found = isinstance(answer, AcceptanceFound)
    if not performed:
        detail = f" ({inspect_stop[0].value}: {inspect_stop[1]})" if inspect_stop else ""
        return (
            progress,
            (TerminationReason.REQUIRED_RECOVERY_NOT_PERFORMED, "state integrity could not be inspected" + detail),
        )
    if report is None:
        reason = (
            TerminationReason.ACCEPTED_STATE_INTEGRITY_UNCERTAIN if found else TerminationReason.RESTORATION_UNCERTAIN
        )
        return (progress, (reason, "the integrity inspector returned an invalid report"))
    result = reconcile_acceptance(event, answer, integrity_request=integrity_request, integrity_report=report)
    outcome = result.outcome
    if outcome is ReconciliationOutcome.CONFIRMED_ACCEPTANCE:
        progress, promote_result = promote(progress, result.verified_reference, ctx, on_progress=on_progress)
    elif outcome is ReconciliationOutcome.VERIFIED_PREVIOUS_STATE:
        selection_2 = (progress.accepted, True)
        progress = replace(progress, verified=selection_2[0], restored_last=selection_2[1])
        on_progress(progress)
    progress, record_stop = record(progress, result, identities=identities, journal=journal, on_progress=on_progress)
    pending = inspect_stop or record_stop
    match outcome:
        case ReconciliationOutcome.CONFIRMED_ACCEPTANCE:
            return (progress, pending or route)
        case ReconciliationOutcome.VERIFIED_PREVIOUS_STATE:
            return (
                progress,
                pending
                or (TerminationReason.RECORDING_FAILED, "the acceptance was confirmed absent after a lost reply"),
            )
        case ReconciliationOutcome.ACCEPTED_STATE_FAILED:
            return (
                progress,
                (TerminationReason.ACCEPTED_STATE_INTEGRITY_FAILED, "the committed candidate failed integrity"),
            )
        case ReconciliationOutcome.ACCEPTED_STATE_UNCERTAIN:
            return (
                progress,
                (TerminationReason.ACCEPTED_STATE_INTEGRITY_UNCERTAIN, "committed candidate integrity unknown"),
            )
        case ReconciliationOutcome.PREVIOUS_STATE_FAILED:
            return (progress, (TerminationReason.RESTORATION_FAILED, "integrity of the saved accepted state failed"))
        case ReconciliationOutcome.PREVIOUS_STATE_UNCERTAIN:
            return (
                progress,
                (TerminationReason.RESTORATION_UNCERTAIN, "integrity of the saved accepted state is uncertain"),
            )
        case ReconciliationOutcome.AUTHORITY_UNCERTAIN:
            return (progress, (TerminationReason.ACCEPTANCE_COMMIT_UNCERTAIN, "acceptance authority unknown"))
        case _:
            assert_never(outcome)
    return (progress, None)
