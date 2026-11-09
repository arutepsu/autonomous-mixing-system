"""Non-durable ordered run recording composed with the in-memory acceptance journal."""

import threading

from autonomous_mixing.adapters.journal.memory import NON_DURABLE, InMemoryAcceptanceJournal
from autonomous_mixing.core.records.acceptance import (
    AcceptanceCommitResult,
    AcceptanceCommitted,
    AcceptanceEvent,
    AcceptanceQuery,
    AcceptanceQueryResult,
    AcceptedReference,
)
from autonomous_mixing.core.records.identifiers import RunId
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.results import PortResult, Success
from autonomous_mixing.core.records.run_trace import (
    RunJournalSnapshot,
    RunTraceEvent,
    TraceAppended,
    TraceAppendResult,
    TraceNotAppended,
)


class InMemoryRunJournal:
    """Store consistent trace and acceptance prefixes for one controlled run.

    One outer lock serializes every operation. The acceptance journal retains its own atomic logical
    commit contract. Trace storage publishes a complete immutable acknowledgement tuple once, after
    validation. snapshots remain valid after later appends and commits. Fresh instances start empty.

    The coordinator owns precommit validation, effect dispatch and termination policy. This adapter
    provides in-process recording, with no durability or automatic retry.
    """

    def __init__(self, run_id: RunId, initial: AcceptedReference) -> None:
        self._acceptance = InMemoryAcceptanceJournal(run_id, initial)
        self._appends: tuple[TraceAppended, ...] = ()
        self._lock = threading.Lock()

    @property
    def run_id(self) -> RunId:
        return self._acceptance.run_id

    @property
    def initial(self) -> AcceptedReference:
        return self._acceptance.initial

    @property
    def accepted(self) -> AcceptedReference:
        with self._lock:
            return self._acceptance.accepted

    @property
    def commits(self) -> tuple[AcceptanceCommitted, ...]:
        with self._lock:
            return self._acceptance.commits

    @property
    def durability(self) -> str:
        return NON_DURABLE

    def append(self, event: RunTraceEvent) -> TraceAppendResult:
        """Append a valid next event or return its original acknowledgement for an exact duplicate."""
        require_instance(event, RunTraceEvent, "event")
        with self._lock:
            if event.run_id != self.run_id:
                return TraceNotAppended(event.event_id, "event names another run")
            existing = next((ack for ack in self._appends if ack.event.event_id == event.event_id), None)
            if existing is not None:
                if existing.event == event:
                    return existing
                return TraceNotAppended(event.event_id, "event ID already records different contents")
            events = (*tuple(ack.event for ack in self._appends), event)
            try:
                RunJournalSnapshot(self.run_id, self.initial, events, self._acceptance.commits)
            except ValueError as error:
                return TraceNotAppended(event.event_id, str(error))
            acknowledgement = TraceAppended(event)
            updated = (*self._appends, acknowledgement)
            self._appends = updated
            return acknowledgement

    def commit(self, event: AcceptanceEvent) -> AcceptanceCommitResult:
        require_instance(event, AcceptanceEvent, "event")
        with self._lock:
            return self._acceptance.commit(event)

    def query(self, request: AcceptanceQuery) -> AcceptanceQueryResult:
        require_instance(request, AcceptanceQuery, "request")
        with self._lock:
            return self._acceptance.query(request)

    def snapshot(self) -> PortResult[RunJournalSnapshot]:
        with self._lock:
            captured = RunJournalSnapshot(
                self.run_id,
                self.initial,
                tuple(ack.event for ack in self._appends),
                self._acceptance.commits,
            )
            return Success(captured)
