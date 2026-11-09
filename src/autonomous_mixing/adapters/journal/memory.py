"""A NON_DURABLE in-memory acceptance journal for one run (attempt contract §29).

History lives only in this Python object: a fresh instance starts empty even for the same run ID, and
nothing survives the process. Use it for the first non-durable integration milestone only.
"""

import threading

from autonomous_mixing.core.records.acceptance import (
    AcceptanceAbsent,
    AcceptanceAuthorityUnknown,
    AcceptanceCommitResult,
    AcceptanceCommitted,
    AcceptanceEvent,
    AcceptanceFound,
    AcceptanceNotCommitted,
    AcceptanceQuery,
    AcceptanceQueryResult,
    AcceptedReference,
)
from autonomous_mixing.core.records.identifiers import AcceptanceId, RunId
from autonomous_mixing.core.records.invariants import require_instance

NON_DURABLE = "non_durable"


class InMemoryAcceptanceJournal:
    """Implements ``AcceptanceJournal`` for one run, starting from an initial reference at sequence zero.

    The tuple of acknowledgements is the authoritative committed prefix; the accepted reference is
    derived from it, so the two cannot diverge. One lock serializes commits and queries. The journal
    checks the structural event contract it receives and the expected reference; the coordinator must
    run ``validate_acceptance_event`` before submission and establish run, attempt and decision
    membership and file integrity. Outcomes are always definite here; ``AcceptanceCommitUnknown`` and
    ``AcceptanceAuthorityUnknown`` for this run are never produced by this implementation.
    """

    def __init__(self, run_id: RunId, initial: AcceptedReference) -> None:
        require_instance(run_id, RunId, "run_id")
        require_instance(initial, AcceptedReference, "initial")
        if initial.sequence != 0:
            raise ValueError(f"initial accepted reference must have sequence 0, got {initial.sequence}")
        self._run_id = run_id
        self._initial = initial
        self._commits: tuple[AcceptanceCommitted, ...] = ()
        self._lock = threading.Lock()

    @property
    def run_id(self) -> RunId:
        return self._run_id

    @property
    def initial(self) -> AcceptedReference:
        return self._initial

    @property
    def accepted(self) -> AcceptedReference:
        """The initial reference while empty, otherwise the last acknowledgement's reference."""
        return _head(self._initial, self._commits)

    @property
    def commits(self) -> tuple[AcceptanceCommitted, ...]:
        """All acknowledgements in commit order (an immutable snapshot)."""
        return self._commits

    @property
    def durability(self) -> str:
        return NON_DURABLE

    def commit(self, event: AcceptanceEvent) -> AcceptanceCommitResult:
        """Record ``event`` if it names this run and the current accepted reference.

        Order: wrong run; an existing acceptance ID (an equal event returns its original
        acknowledgement, even after later commits; a different event is refused); stale expected
        reference (complete state and sequence). A refusal changes nothing.
        """
        require_instance(event, AcceptanceEvent, "event")
        with self._lock:
            commits = self._commits
            if event.run_id != self._run_id:
                return AcceptanceNotCommitted(
                    event.acceptance_id, f"event names run {event.run_id}; this journal serves run {self._run_id}"
                )
            existing = _find(commits, event.acceptance_id)
            if existing is not None:
                if existing.event == event:
                    return existing
                return AcceptanceNotCommitted(
                    event.acceptance_id, "acceptance ID already committed with different event contents"
                )
            head = _head(self._initial, commits)
            if event.expected != head:
                return AcceptanceNotCommitted(
                    event.acceptance_id,
                    f"expected reference (revision {event.expected.state.revision}, sequence "
                    f"{event.expected.sequence}) is not the current accepted reference (revision "
                    f"{head.state.revision}, sequence {head.sequence})",
                )
            acknowledgement = AcceptanceCommitted(
                event, AcceptedReference(event.assessment.assessed.state, event.expected.sequence + 1)
            )
            self._commits = (*commits, acknowledgement)  # one publication of the new prefix
            return acknowledgement

    def query(self, request: AcceptanceQuery) -> AcceptanceQueryResult:
        """Report whether one acceptance of this run was committed; another run's authority is unknown."""
        require_instance(request, AcceptanceQuery, "request")
        with self._lock:
            commits = self._commits
        if request.run_id != self._run_id:
            return AcceptanceAuthorityUnknown(
                request.run_id, request.acceptance_id, f"this journal serves run {self._run_id}, not {request.run_id}"
            )
        existing = _find(commits, request.acceptance_id)
        if existing is None:
            return AcceptanceAbsent(request.run_id, request.acceptance_id)
        return AcceptanceFound(existing.event, existing.accepted)


def _head(initial: AcceptedReference, commits: tuple[AcceptanceCommitted, ...]) -> AcceptedReference:
    return commits[-1].accepted if commits else initial


def _find(commits: tuple[AcceptanceCommitted, ...], acceptance_id: AcceptanceId) -> AcceptanceCommitted | None:
    # Linear lookup: the first loop's acceptance count is bounded by its attempt budget.
    return next((c for c in commits if c.event.acceptance_id == acceptance_id), None)
