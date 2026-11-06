"""Decision-gate results: the route of one structurally checked decision, between PLAN/DECIDE and ACT.

Each result keeps the original ``Decision`` object; outcome details (context questions, abstention
reasons, proposed candidates) are read from it, never copied. A route is not permission to act:
``ReadyForAdmission`` still needs authoritative admission, and ``KeepState`` claims no success.
"""

from dataclasses import dataclass

from autonomous_mixing.core.records.decision import Abstain, AbstentionReason, Decision, NoChange, ProposeAction
from autonomous_mixing.core.records.identifiers import ProblemCandidateId
from autonomous_mixing.core.records.invariants import require_instance, require_text, require_tuple_of


def _require_outcome(decision: Decision, kind: type, field: str) -> None:
    require_instance(decision, Decision, field)
    if not isinstance(decision.outcome, kind):
        raise ValueError(f"{field} must have a {kind.__name__} outcome, got {type(decision.outcome).__name__}")


@dataclass(frozen=True)
class InvalidResponse:
    """The response failed structural validation; ``explanation`` is the validator's original message."""

    explanation: str

    def __post_init__(self) -> None:
        require_text(self.explanation, "InvalidResponse.explanation")


@dataclass(frozen=True)
class KeepState:
    """A valid NoChange: keep the accepted state. Whether that state meets criteria is left to verification."""

    decision: Decision

    def __post_init__(self) -> None:
        _require_outcome(self.decision, NoChange, "KeepState.decision")


@dataclass(frozen=True)
class RequestContext:
    """A valid MISSING_CONTEXT abstention; its questions are ``decision.outcome.requested_context``.

    The future loop ends the run with the last accepted state; updated context starts a new run.
    """

    decision: Decision

    def __post_init__(self) -> None:
        _require_outcome(self.decision, Abstain, "RequestContext.decision")
        if self.decision.outcome.reason is not AbstentionReason.MISSING_CONTEXT:
            raise ValueError("RequestContext.decision must abstain with reason MISSING_CONTEXT")


@dataclass(frozen=True)
class DeclineAction:
    """A valid abstention for any reason other than missing context; the reason is ``decision.outcome.reason``."""

    decision: Decision

    def __post_init__(self) -> None:
        _require_outcome(self.decision, Abstain, "DeclineAction.decision")
        if self.decision.outcome.reason is AbstentionReason.MISSING_CONTEXT:
            raise ValueError("DeclineAction.decision must not abstain for missing context (use RequestContext)")


@dataclass(frozen=True)
class EvidenceBlocked:
    """A valid proposal whose selected candidates listed here lack sufficient available evidence.

    ``blocked_candidate_ids`` is nonempty, unique and a subsequence of the proposal's ``candidate_ids``.
    """

    decision: Decision
    blocked_candidate_ids: tuple[ProblemCandidateId, ...]

    def __post_init__(self) -> None:
        _require_outcome(self.decision, ProposeAction, "EvidenceBlocked.decision")
        require_tuple_of(
            self.blocked_candidate_ids,
            ProblemCandidateId,
            "EvidenceBlocked.blocked_candidate_ids",
            nonempty=True,
            distinct=True,
        )
        remaining = iter(self.decision.outcome.candidate_ids)
        if not all(blocked in remaining for blocked in self.blocked_candidate_ids):
            raise ValueError(
                "EvidenceBlocked.blocked_candidate_ids must be selected by the proposal and in proposal order"
            )


@dataclass(frozen=True)
class ReadyForAdmission:
    """A valid proposal whose selected candidates all have sufficient available evidence; admission decides next."""

    decision: Decision

    def __post_init__(self) -> None:
        _require_outcome(self.decision, ProposeAction, "ReadyForAdmission.decision")


DecisionGateResult = InvalidResponse | KeepState | RequestContext | DeclineAction | EvidenceBlocked | ReadyForAdmission
