"""Ordered recording alongside the authoritative acceptance journal."""

from typing import Protocol

from autonomous_mixing.core.ports.acceptance import AcceptanceJournal
from autonomous_mixing.core.records.acceptance import AcceptedReference
from autonomous_mixing.core.records.identifiers import RunId
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.results import PortResult
from autonomous_mixing.core.records.run_trace import (
    RunJournalSnapshot,
    RunTraceEvent,
    TraceAppended,
    TraceAppendResult,
    TraceAppendUnknown,
    TraceNotAppended,
)


class RunJournal(AcceptanceJournal, Protocol):
    """One run's ordered trace and authoritative acceptance history.

    Exact event-ID/content duplicates return their original acknowledgement. Conflicting IDs and
    out-of-order sequences are refused without change. Unknown acknowledgement leaves storage uncertain.
    snapshot returns a consistent view of both prefixes. Acceptance retains its atomic logical contract.
    The adapter owns the configured run and immutable initial reference. Dispatch and retries belong
    to the coordinator.
    """

    def append(self, event: RunTraceEvent) -> TraceAppendResult: ...

    def snapshot(self) -> PortResult[RunJournalSnapshot]: ...


def validate_trace_append_response(event: RunTraceEvent, response: TraceAppendResult) -> None:
    require_instance(event, RunTraceEvent, "event")
    require_instance(response, (TraceAppended, TraceNotAppended, TraceAppendUnknown), "response")
    if isinstance(response, TraceAppended):
        if response.event != event:
            raise ValueError("append acknowledgement differs from the submitted event")
    elif response.event_id != event.event_id:
        raise ValueError("append response identifies another event")


def validate_run_journal_snapshot(run_id: RunId, initial: AcceptedReference, response: RunJournalSnapshot) -> None:
    require_instance(run_id, RunId, "run_id")
    require_instance(initial, AcceptedReference, "initial")
    require_instance(response, RunJournalSnapshot, "response")
    if response.run_id != run_id or response.initial != initial:
        raise ValueError("journal snapshot does not match the configured run and initial reference")
