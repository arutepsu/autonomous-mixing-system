"""The decision gate: route one decision after PLAN/DECIDE and before ACT.

Standalone: it reads only the supplied records and is not yet called by a loop. It does not apply
scope, capability or gain-permission rules, call admission, infer missing musical context, render,
charge budgets or persist anything.
"""

from typing import assert_never

from autonomous_mixing.core.ports.decision import validate_decision_response
from autonomous_mixing.core.records.decision import (
    Abstain,
    AbstentionReason,
    DecisionResponse,
    NoChange,
    ProposeAction,
    Situation,
)
from autonomous_mixing.core.records.decision_gate import (
    DecisionGateResult,
    DeclineAction,
    EvidenceBlocked,
    InvalidResponse,
    KeepState,
    ReadyForAdmission,
    RequestContext,
)
from autonomous_mixing.core.records.diagnosis import ProblemCandidate
from autonomous_mixing.core.records.identifiers import ObservationId
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.observation import Observation, Unavailable


def _lacks_available_evidence(candidate: ProblemCandidate, observations: dict[ObservationId, Observation]) -> bool:
    """True if the candidate is marked insufficient or none of its own observations is available."""
    if candidate.evidence.insufficient_evidence:
        return True
    return all(isinstance(observations[o].result, Unavailable) for o in candidate.evidence_ids)


def assess_decision(situation: Situation, response: DecisionResponse) -> DecisionGateResult:
    """Validate a response structurally, then route it.

    A structural failure (``ValueError`` from ``validate_decision_response``) becomes ``InvalidResponse``
    with the original message; other exceptions propagate. NoChange routes to ``KeepState``, a
    MISSING_CONTEXT abstention to ``RequestContext`` and any other abstention to ``DeclineAction``. For a
    proposal, each selected candidate is checked against its own evidence only: it is blocked if its
    evidence is marked insufficient or none of its own observations has a result other than
    ``Unavailable`` (a measured zero is available; partial availability passes). Blocked candidates are
    reported in proposal order as ``EvidenceBlocked``; otherwise the result is ``ReadyForAdmission``,
    which is not permission to act.
    """
    require_instance(situation, Situation, "situation")
    require_instance(response, DecisionResponse, "response")
    try:
        validate_decision_response(situation, response)
    except ValueError as error:
        return InvalidResponse(str(error))

    decision = response.decision
    outcome = decision.outcome
    match outcome:
        case NoChange():
            return KeepState(decision)
        case Abstain():
            if outcome.reason is AbstentionReason.MISSING_CONTEXT:
                return RequestContext(decision)
            return DeclineAction(decision)
        case ProposeAction():
            candidates = {candidate.candidate_id: candidate for candidate in situation.diagnosis.candidates}
            observations = {o.observation_id: o for o in situation.observations}
            blocked = tuple(
                candidate_id
                for candidate_id in outcome.candidate_ids
                if _lacks_available_evidence(candidates[candidate_id], observations)
            )
            return EvidenceBlocked(decision, blocked) if blocked else ReadyForAdmission(decision)
        case _:
            assert_never(outcome)
