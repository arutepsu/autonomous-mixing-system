"""Completion responsibilities of the controlled serial coordinator: the pre-write terminal snapshot, its
single append, the journal read-back, the completion clock reading and the effective final report.
"""

from collections.abc import Callable
from dataclasses import replace

from autonomous_mixing.core.loop.runtime import append, new_event
from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource, RunClock
from autonomous_mixing.core.ports.run_journal import RunJournal, validate_run_journal_snapshot
from autonomous_mixing.core.records.coordinator_progress import RuntimeProgress
from autonomous_mixing.core.records.coordinator_runtime import ClockReading
from autonomous_mixing.core.records.results import Success
from autonomous_mixing.core.records.run import _REASON_OUTCOMES, FinalProjectState, RunOutcome, RunRecord, RunState
from autonomous_mixing.core.records.run_report import RunReport
from autonomous_mixing.core.records.run_trace import RunJournalSnapshot, TerminalSnapshot, TraceAppended
from autonomous_mixing.core.records.termination import TerminationReason

_PRESERVED_ON_RECORDING_FAILURE = (
    TerminationReason.ACCEPTANCE_COMMIT_UNCERTAIN,
    TerminationReason.ACCEPTED_STATE_INTEGRITY_FAILED,
    TerminationReason.ACCEPTED_STATE_INTEGRITY_UNCERTAIN,
    TerminationReason.RESTORATION_FAILED,
    TerminationReason.RESTORATION_UNCERTAIN,
    TerminationReason.REQUIRED_RECOVERY_NOT_PERFORMED,
)


def finalize(
    progress: RuntimeProgress,
    reason: TerminationReason,
    explanation: str,
    *,
    clock: RunClock,
    identities: IdentitySource,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, RunReport]:
    authoritative = progress.accepted if progress.authority_known else None
    selected = progress.verified if progress.verified is not None and progress.verified == authoritative else None
    if selected is None:
        final_state = FinalProjectState.UNKNOWN
    elif progress.restored_last:
        final_state = FinalProjectState.RESTORED_TO_LAST_ACCEPTED
    elif selected.sequence == 0:
        final_state = FinalProjectState.UNCHANGED
    else:
        final_state = FinalProjectState.ACCEPTED_EDITS_RETAINED
    outcome = _REASON_OUTCOMES[reason]
    proposed = RunRecord(
        progress.request.run_id,
        progress.request.initial_state.project_id,
        progress.request.configuration,
        progress.request.initial_state.revision,
        progress.first.utc,
        RunState.FINISHED,
        progress.usage,
        outcome,
        final_state,
        reason,
        explanation,
    )
    terminal_payload = TerminalSnapshot(proposed, authoritative, selected)
    progress, event = new_event(
        progress, terminal_payload, progress.usage, identities=identities, on_progress=on_progress
    )
    if event is None:
        raise ValueError("the identity source supplied an invalid or repeated terminal event ID")
    progress, response = append(progress, event, journal=journal, on_progress=on_progress)
    acknowledged = isinstance(response, TraceAppended)
    trace = journal.snapshot()
    if isinstance(trace, Success) and isinstance(trace.value, RunJournalSnapshot):
        try:
            validate_run_journal_snapshot(progress.request.run_id, progress.initial, trace.value)
            snapshot = trace.value
        except ValueError:
            snapshot = RunJournalSnapshot(
                progress.request.run_id, progress.initial, tuple(progress.known), tuple(progress.commits)
            )
    else:
        snapshot = RunJournalSnapshot(
            progress.request.run_id, progress.initial, tuple(progress.known), tuple(progress.commits)
        )
    completion = clock.read()
    clock_valid = (
        isinstance(completion, ClockReading) and completion.monotonic_seconds >= progress.last.monotonic_seconds
    )
    if not clock_valid:
        completion = progress.last
    final_usage = replace(
        progress.usage, elapsed_seconds=completion.monotonic_seconds - progress.first.monotonic_seconds
    )
    final_outcome, final_reason, final_explanation = (outcome, reason, explanation)
    if not acknowledged and reason not in _PRESERVED_ON_RECORDING_FAILURE:
        final_outcome, final_reason = (RunOutcome.FAILED, TerminationReason.RECORDING_FAILED)
        final_explanation = f"the terminal event was not acknowledged; proposed {reason.value}: {explanation}"
    elif acknowledged and (not clock_valid) and (outcome is not RunOutcome.FAILED):
        final_outcome, final_reason = (RunOutcome.FAILED, TerminationReason.RUNTIME_CONTRACT_FAILED)
        final_explanation = (
            f"the completion clock reading was invalid or decreased; proposed {reason.value}: {explanation}"
        )
    elif (
        acknowledged
        and final_usage.elapsed_seconds >= progress.request.configuration.budget_limits.max_elapsed_seconds
        and (outcome in (RunOutcome.SUCCEEDED, RunOutcome.ABSTAINED))
    ):
        final_outcome, final_reason = (RunOutcome.LIMIT_REACHED, TerminationReason.ELAPSED_TIME_LIMIT)
        final_explanation = f"finalization ended at or beyond the time limit; proposed {reason.value}"
    if not clock_valid and final_reason is not TerminationReason.RUNTIME_CONTRACT_FAILED:
        final_explanation += " (the completion clock reading was invalid or decreased)"
    final_run = replace(
        proposed,
        usage=final_usage,
        outcome=final_outcome,
        termination_reason=final_reason,
        termination_explanation=final_explanation,
    )
    return (
        progress,
        RunReport(
            progress.request,
            snapshot,
            event,
            response,
            completion,
            final_run,
            authoritative,
            selected,
            tuple((e for e in progress.unacknowledged if e.event_id != event.event_id))
            + (() if acknowledged else (event,)),
        ),
    )
