"""Observed completion of a non-durable run, separately from its pre-write terminal snapshot."""

from dataclasses import dataclass

from autonomous_mixing.core.records.acceptance import AcceptedReference
from autonomous_mixing.core.records.coordinator_runtime import ClockReading, RunRequest
from autonomous_mixing.core.records.invariants import require_instance, require_tuple_of
from autonomous_mixing.core.records.run import RunOutcome, RunRecord, RunState
from autonomous_mixing.core.records.run_trace import (
    RunJournalSnapshot,
    RunTraceEvent,
    TerminalSnapshot,
    TraceAppended,
    TraceAppendResult,
    TraceAppendUnknown,
    TraceNotAppended,
)
from autonomous_mixing.core.records.termination import TerminationReason

_HIGH_PRIORITY_RECORDING_REASONS = (
    TerminationReason.ACCEPTANCE_COMMIT_UNCERTAIN,
    TerminationReason.ACCEPTED_STATE_INTEGRITY_FAILED,
    TerminationReason.ACCEPTED_STATE_INTEGRITY_UNCERTAIN,
    TerminationReason.RESTORATION_FAILED,
    TerminationReason.RESTORATION_UNCERTAIN,
    TerminationReason.REQUIRED_RECOVERY_NOT_PERFORMED,
)


def _require_run_binding(run: RunRecord, request: RunRequest) -> None:
    if (run.run_id, run.project_id, run.configuration, run.initial_revision) != (
        request.run_id,
        request.initial_state.project_id,
        request.configuration,
        request.initial_state.revision,
    ):
        raise ValueError("run record does not match the request's run, project, configuration and initial revision")


@dataclass(frozen=True)
class RunReport:
    request: RunRequest
    trace: RunJournalSnapshot
    terminal_event: RunTraceEvent
    terminal_append: TraceAppendResult
    completion: ClockReading
    final_run: RunRecord
    authoritative: AcceptedReference | None
    verified: AcceptedReference | None
    unacknowledged_events: tuple[RunTraceEvent, ...]

    def __post_init__(self) -> None:
        require_instance(self.request, RunRequest, "RunReport.request")
        require_instance(self.trace, RunJournalSnapshot, "RunReport.trace")
        require_instance(self.terminal_event, RunTraceEvent, "RunReport.terminal_event")
        require_instance(
            self.terminal_append, (TraceAppended, TraceNotAppended, TraceAppendUnknown), "RunReport.terminal_append"
        )
        require_instance(self.completion, ClockReading, "RunReport.completion")
        require_instance(self.final_run, RunRecord, "RunReport.final_run")
        require_tuple_of(
            self.unacknowledged_events, RunTraceEvent, "RunReport.unacknowledged_events", nonempty=False, distinct=False
        )
        _require_run_binding(self.final_run, self.request)
        if self.final_run.state is not RunState.FINISHED:
            raise ValueError("completion requires a FINISHED final run")
        if self.trace.run_id != self.request.run_id or self.trace.initial.state != self.request.initial_state:
            raise ValueError("trace does not match the request's run and initial state")
        terminal = self.terminal_event
        require_instance(terminal.payload, TerminalSnapshot, "RunReport.terminal_event.payload")
        if terminal.run_id != self.request.run_id:
            raise ValueError("terminal event belongs to another run")
        _require_run_binding(terminal.payload.run, self.request)
        if self.final_run.created_at != terminal.payload.run.created_at:
            raise ValueError("finalization changed the run's original creation timestamp")
        TerminalSnapshot(self.final_run, self.authoritative, self.verified)
        if self.authoritative is not None and self.authoritative != self.trace.accepted:
            raise ValueError("reported authority differs from the committed acceptance prefix")
        self._validate_append()
        self._validate_usage()
        self._validate_finalization()
        by_id = {event.event_id: event for event in self.trace.events}
        pending = set()
        for event in self.unacknowledged_events:
            if event.run_id != self.request.run_id or event.event_id in pending:
                raise ValueError("unacknowledged events require this run and unique IDs")
            if event.event_id in by_id and by_id[event.event_id] != event:
                raise ValueError("unacknowledged event conflicts with a recorded event")
            pending.add(event.event_id)
        for event in self.trace.events:
            if isinstance(event.payload, RunRequest) and event.payload != self.request:
                raise ValueError("recorded run request differs from the report request")

    def _validate_append(self) -> None:
        response = self.terminal_append
        if isinstance(response, TraceAppended):
            if response.event != self.terminal_event:
                raise ValueError("terminal acknowledgement differs from the submitted event")
            if self.terminal_event not in self.trace.events:
                raise ValueError("acknowledged terminal event must occur in the trace")
        else:
            if response.event_id != self.terminal_event.event_id:
                raise ValueError("terminal append response identifies another event")
            if isinstance(response, TraceNotAppended) and self.terminal_event in self.trace.events:
                raise ValueError("confirmed unwritten terminal event occurs in the committed trace")
            reason = self.final_run.termination_reason
            if self.final_run.outcome is not RunOutcome.FAILED or reason not in (
                TerminationReason.RECORDING_FAILED,
                *_HIGH_PRIORITY_RECORDING_REASONS,
            ):
                raise ValueError(
                    "unacknowledged terminal recording requires recording failure or higher-priority uncertainty"
                )

    def _validate_usage(self) -> None:
        before, after = self.terminal_event.usage, self.final_run.usage
        for field in ("decision_calls", "attempts", "render_calls", "recovery_operations"):
            if getattr(before, field) != getattr(after, field):
                raise ValueError("operation counts cannot change during terminal recording")
        if after.elapsed_seconds < before.elapsed_seconds:
            raise ValueError("completion elapsed time cannot precede the terminal checkpoint")
        if (
            self.final_run.outcome is RunOutcome.SUCCEEDED
            and after.elapsed_seconds >= self.request.configuration.budget_limits.max_elapsed_seconds
        ):
            raise ValueError("SUCCEEDED cannot be claimed at or beyond the elapsed limit")

    def _validate_finalization(self) -> None:
        terminal = self.terminal_event.payload
        if (self.authoritative, self.verified, self.final_run.final_project_state) != (
            terminal.authoritative,
            terminal.verified,
            terminal.run.final_project_state,
        ):
            raise ValueError("terminal recording cannot change authority, integrity or selected-state disposition")
        proposed = (terminal.run.outcome, terminal.run.termination_reason)
        final = (self.final_run.outcome, self.final_run.termination_reason)
        if final == proposed:
            return
        if terminal.run.termination_reason in _HIGH_PRIORITY_RECORDING_REASONS:
            raise ValueError("finalization must preserve the higher-priority authority or recovery reason")
        if not isinstance(self.terminal_append, TraceAppended) and final == (
            RunOutcome.FAILED,
            TerminationReason.RECORDING_FAILED,
        ):
            return
        if (
            isinstance(self.terminal_append, TraceAppended)
            and terminal.run.outcome is not RunOutcome.FAILED
            and final == (RunOutcome.FAILED, TerminationReason.RUNTIME_CONTRACT_FAILED)
        ):
            # The completion observation violated the runtime contract (e.g. an invalid or backward clock reading,
            # which no ClockReading can carry): an acknowledged, not yet failed result may only become this failure.
            return
        exceeded = self.final_run.usage.elapsed_seconds >= self.request.configuration.budget_limits.max_elapsed_seconds
        if (
            exceeded
            and terminal.run.outcome is not RunOutcome.FAILED
            and final == (RunOutcome.LIMIT_REACHED, TerminationReason.ELAPSED_TIME_LIMIT)
        ):
            return
        raise ValueError("completion outcome must preserve terminal facts or record the permitted finalization failure")

    @property
    def durability(self) -> str:
        return "non_durable"
