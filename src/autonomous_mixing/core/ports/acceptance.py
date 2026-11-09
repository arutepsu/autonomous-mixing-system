"""The acceptance-journal port and the identity checks on its responses (attempt contract §29)."""

from typing import Protocol

from autonomous_mixing.core.records.acceptance import (
    AcceptanceAbsent,
    AcceptanceAuthorityUnknown,
    AcceptanceCommitResult,
    AcceptanceCommitted,
    AcceptanceCommitUnknown,
    AcceptanceEvent,
    AcceptanceFound,
    AcceptanceNotCommitted,
    AcceptanceQuery,
    AcceptanceQueryResult,
    AcceptedReference,
)
from autonomous_mixing.core.records.invariants import require_instance


class AcceptanceJournal(Protocol):
    """Records candidate acceptances for one run and answers queries about them.

    Logical contract for implementations (none exists yet; the first runnable milestone is NON_DURABLE):
    each journal is configured for one run and an initial ``AcceptedReference`` at sequence zero. A new
    event must name the configured run and the current accepted reference as ``expected``. Committing
    records the event and advances the accepted reference in one logical operation. Resubmitting an
    exact committed event returns its original ``AcceptanceCommitted`` acknowledgement, even after later
    acceptances. A reused acceptance ID with different contents, a wrong run or a stale expected
    reference returns ``AcceptanceNotCommitted`` and changes nothing. ``AcceptanceCommitUnknown`` makes no
    claim whether the event was stored; the caller must resolve it with ``query`` and never submit a new
    acceptance blindly. A query searches only the supplied run and acceptance identity.

    There is no generic retryable failure that could erase commit uncertainty; unexpected errors
    propagate. Query resolution is not physical integrity and not a restoration receipt. The future
    coordinator bounds authority queries and integrity checks under its recovery and time gates.
    """

    def commit(self, event: AcceptanceEvent) -> AcceptanceCommitResult: ...

    def query(self, request: AcceptanceQuery) -> AcceptanceQueryResult: ...


def validate_acceptance_commit_response(event: AcceptanceEvent, response: AcceptanceCommitResult) -> None:
    """Check that a commit response concerns exactly the submitted event (identity only, not storage)."""
    require_instance(event, AcceptanceEvent, "event")
    require_instance(response, (AcceptanceCommitted, AcceptanceNotCommitted, AcceptanceCommitUnknown), "response")
    if isinstance(response, AcceptanceCommitted):
        if response.event != event:
            raise ValueError(f"committed response does not carry the submitted event {event.acceptance_id}")
        expected = AcceptedReference(event.assessment.assessed.state, event.expected.sequence + 1)
        if response.accepted != expected:
            raise ValueError("committed response does not carry the submitted event's accepted reference")
    elif response.acceptance_id != event.acceptance_id:
        raise ValueError(f"commit response names {response.acceptance_id}, not {event.acceptance_id}")


def validate_acceptance_query_response(request: AcceptanceQuery, response: AcceptanceQueryResult) -> None:
    """Check that a query response concerns exactly the queried run and acceptance.

    An invalid response raises; it is never treated as confirmed absence.
    """
    require_instance(request, AcceptanceQuery, "request")
    require_instance(response, (AcceptanceFound, AcceptanceAbsent, AcceptanceAuthorityUnknown), "response")
    if isinstance(response, AcceptanceFound):
        run_id, acceptance_id = response.event.run_id, response.event.acceptance_id
    else:
        run_id, acceptance_id = response.run_id, response.acceptance_id
    if (run_id, acceptance_id) != (request.run_id, request.acceptance_id):
        raise ValueError(f"query response names {run_id}/{acceptance_id}, not {request.run_id}/{request.acceptance_id}")
