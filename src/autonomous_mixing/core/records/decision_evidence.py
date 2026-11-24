"""Exact, immutable evidence of a provider result being matched to its completed local audit."""

from dataclasses import dataclass

from autonomous_mixing.core.records.decision import DecisionResponse, Situation
from autonomous_mixing.core.records.decision_exchange import (
    DecisionCallKey,
    DecisionExchangeRecord,
    ExchangeCompletion,
    ExchangeStatus,
    call_key_of,
)
from autonomous_mixing.core.records.invariants import require_instance, require_text
from autonomous_mixing.core.records.results import PortFailure


@dataclass(frozen=True)
class DecisionEvidenceVerified:
    """A driver-checked projection claim, not authentication of a remote provider."""

    situation: Situation
    result: DecisionResponse | PortFailure
    exchange: DecisionExchangeRecord

    def __post_init__(self) -> None:
        require_instance(self.situation, Situation, "evidence situation")
        require_instance(self.result, (DecisionResponse, PortFailure), "evidence result")
        require_instance(self.exchange, DecisionExchangeRecord, "exchange")
        if (
            self.exchange.request.call != call_key_of(self.situation)
            or self.exchange.status is not ExchangeStatus.COMPLETED
        ):
            raise ValueError("evidence requires this call's completed exchange")
        binding = self.exchange.request.packet.binding
        s = self.situation
        if (binding.project_id, binding.revision, binding.context_id, binding.context_version, binding.controller) != (
            s.state.project_id,
            s.state.revision,
            s.context.context_id,
            s.context.version,
            s.controller,
        ):
            raise ValueError("exchange binding differs from the situation")
        done = self.exchange.completion
        if isinstance(self.result, DecisionResponse):
            if done.completion is not ExchangeCompletion.VALID_DECISION or done.decision != self.result.decision:
                raise ValueError("completed decision differs from returned decision")
        elif done.failure != self.result:
            raise ValueError("completed failure differs from returned failure")


@dataclass(frozen=True)
class DecisionEvidenceRejected:
    call: DecisionCallKey
    reason: str

    def __post_init__(self) -> None:
        require_instance(self.call, DecisionCallKey, "evidence call")
        require_text(self.reason, "evidence rejection")
