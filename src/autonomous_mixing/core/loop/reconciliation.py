"""Reconcile one acceptance event from journal authority and integrity evidence (attempt contract §§22, 28-29).

Pure: no journal, filesystem or clock calls, no selection or publication, retry, rollback receipt or
recording. A future coordinator performs the bounded port calls, records the reconciliation before
ordinary work resumes and, for the previous-state branch, confirms the selection before creating a
VERIFIED rollback receipt; candidate-confirmation branches never produce rollback receipts. If recording
the reconciliation fails, the coordinator terminates for recording failure.
"""

from typing import assert_never

from autonomous_mixing.core.loop.acceptance import validate_acceptance_event
from autonomous_mixing.core.ports.acceptance import validate_acceptance_query_response
from autonomous_mixing.core.ports.integrity import validate_state_integrity_response
from autonomous_mixing.core.records.acceptance import (
    AcceptanceAbsent,
    AcceptanceAuthorityUnknown,
    AcceptanceEvent,
    AcceptanceFound,
    AcceptanceQuery,
    AcceptanceQueryResult,
    AcceptedReference,
)
from autonomous_mixing.core.records.integrity import IntegrityStatus, StateIntegrityReport, StateIntegrityRequest
from autonomous_mixing.core.records.reconciliation import ReconciliationOutcome, ReconciliationResult

Outcome = ReconciliationOutcome


def reconcile_acceptance(
    event: AcceptanceEvent,
    authority: AcceptanceQueryResult,
    *,
    integrity_request: StateIntegrityRequest | None,
    integrity_report: StateIntegrityReport | None,
) -> ReconciliationResult:
    """Return what the journal answer and the integrity evidence establish for ``event``.

    Unknown authority is AUTHORITY_UNCERTAIN and takes no integrity evidence: an intact previous state
    never substitutes for unknown authority. A found event must equal ``event`` exactly; its committed
    reference is authoritative. An absent event leaves ``event.expected`` authoritative; this relies on
    the serial-run discipline (no other acceptance is dispatched while this event is unresolved) and is
    not a latest-head query. For known authority, the supplied request must name exactly the
    authoritative state and the report must answer exactly that request; its aggregate status then
    selects the outcome. Malformed inputs raise ``ValueError``; unexpected errors propagate.
    """
    validate_acceptance_event(event)
    validate_acceptance_query_response(AcceptanceQuery(event.run_id, event.acceptance_id), authority)

    if isinstance(authority, AcceptanceAuthorityUnknown):
        if integrity_request is not None or integrity_report is not None:
            raise ValueError("unknown acceptance authority takes no integrity evidence")
        return ReconciliationResult(Outcome.AUTHORITY_UNCERTAIN, None, None)
    if isinstance(authority, AcceptanceFound):
        if authority.event != event:
            raise ValueError(f"journal holds different event contents under {event.acceptance_id}")
        found, reference, role = True, authority.accepted, "committed candidate"
    elif isinstance(authority, AcceptanceAbsent):
        found, reference, role = False, event.expected, "expected accepted"
    else:
        assert_never(authority)

    status = _integrity_status(reference, role, integrity_request, integrity_report)
    match status:
        case IntegrityStatus.VERIFIED:
            outcome = Outcome.CONFIRMED_ACCEPTANCE if found else Outcome.VERIFIED_PREVIOUS_STATE
            return ReconciliationResult(outcome, reference, reference)
        case IntegrityStatus.FAILED:
            outcome = Outcome.ACCEPTED_STATE_FAILED if found else Outcome.PREVIOUS_STATE_FAILED
        case IntegrityStatus.UNCERTAIN:
            outcome = Outcome.ACCEPTED_STATE_UNCERTAIN if found else Outcome.PREVIOUS_STATE_UNCERTAIN
        case _:
            assert_never(status)
    return ReconciliationResult(outcome, reference, None)


def _integrity_status(
    reference: AcceptedReference,
    role: str,
    request: StateIntegrityRequest | None,
    report: StateIntegrityReport | None,
) -> IntegrityStatus:
    if request is None or report is None:
        raise ValueError(f"known acceptance authority requires an integrity request and report for the {role} state")
    if not isinstance(request, StateIntegrityRequest):
        raise ValueError(f"integrity_request must be a StateIntegrityRequest, got {type(request).__name__}")
    if request.state != reference.state:
        raise ValueError(f"integrity request does not name exactly the {role} state")
    validate_state_integrity_response(request, report)
    return report.status
