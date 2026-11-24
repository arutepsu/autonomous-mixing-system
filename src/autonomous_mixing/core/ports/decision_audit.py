"""The decision-exchange audit port (R3; diagnosis-and-decision-adapter plan, section 5).

A provider-backed decision maker persists the pending request before dispatch (``begin``), the public response or the
fact that none arrived before interpretation (``record_response``) and the interpretation before returning
(``finish``). ``read`` answers by call key. Results are typed: an unavailable or unconfirmed write is not a recorded
one. The existing ``DecisionMaker.decide`` contract is unchanged; R5 will require a matching completed record before
using a provider result.
"""

from typing import Protocol

from autonomous_mixing.core.records.decision_exchange import (
    AuditRecorded,
    AuditWriteResult,
    CompletedDecisionExchange,
    DecisionCallKey,
    DecisionExchangeRequest,
    ExchangeReadResult,
    ExchangeStatus,
    RecordedDecisionResponse,
)


class DecisionAuditTrail(Protocol):
    """Append-only evidence of decision exchanges for one run, keyed by ``(run_id, decision_call_ordinal)``."""

    def begin(self, request: DecisionExchangeRequest) -> AuditWriteResult: ...

    def record_response(self, response: RecordedDecisionResponse) -> AuditWriteResult: ...

    def finish(self, completion: CompletedDecisionExchange) -> AuditWriteResult: ...

    def read(self, call: DecisionCallKey) -> ExchangeReadResult: ...


def permits_dispatch(result: AuditWriteResult, request: DecisionExchangeRequest) -> bool:
    """Whether ``begin`` just recorded exactly this request as a new pending exchange.

    Only then may a provider request be sent. A replayed, pending, answered or completed exchange, a refusal and an
    unconfirmed write never permit (re)sending.
    """
    return (
        isinstance(result, AuditRecorded)
        and result.newly_recorded
        and result.record.status is ExchangeStatus.PENDING
        and result.record.request == request
    )
