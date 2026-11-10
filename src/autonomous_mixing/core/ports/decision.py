"""The decision port and the checks on its responses and decision references."""

from collections.abc import Iterable
from typing import Protocol

from autonomous_mixing.core.records.context import MusicalContext
from autonomous_mixing.core.records.decision import Decision, DecisionResponse, ProposeAction, Situation
from autonomous_mixing.core.records.identifiers import ObservationId
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.observation import Observation
from autonomous_mixing.core.records.project import ProjectSnapshot
from autonomous_mixing.core.records.results import PortResult


class DecisionMaker(Protocol):
    """Proposes one action or an abstention for a decision request.

    Implementations return expected failures as ``PortFailure``; callers validate successful
    responses with ``validate_decision_response``. A response is a proposal, not permission to
    execute.
    """

    def decide(self, request: Situation) -> PortResult[DecisionResponse]: ...


def validate_decision_response(request: Situation, response: DecisionResponse) -> None:
    """Check a decision's structural references and controller identity against its request.

    For a proposed action, every selected candidate must exist in ``request.diagnosis`` and all of its
    evidence must be among the decision's evidence IDs (extra evidence is allowed; unselected candidates
    impose nothing). NoChange and Abstain pass this structural check. This is not a semantic evidence gate:
    it does not judge whether the evidence is sufficient, the action justified or permitted.
    """
    require_instance(request, Situation, "request")
    require_instance(response, DecisionResponse, "response")
    decision = response.decision
    validate_decision_references(decision, request.state.snapshot, request.context, request.observations)
    if decision.controller != request.controller:
        raise ValueError(
            f"decision controller {decision.controller.name}@{decision.controller.version} does not match "
            f"the requested controller {request.controller.name}@{request.controller.version}"
        )
    if isinstance(decision.outcome, ProposeAction):
        candidates = {candidate.candidate_id: candidate for candidate in request.diagnosis.candidates}
        unknown = [str(c) for c in decision.outcome.candidate_ids if c not in candidates]
        if unknown:
            raise ValueError(f"proposed action cites candidates not in the diagnosis report: {', '.join(unknown)}")
        cited = set(decision.evidence_ids)
        for candidate_id in decision.outcome.candidate_ids:
            uncited = [str(o) for o in candidates[candidate_id].evidence_ids if o not in cited]
            if uncited:
                raise ValueError(
                    f"proposed action selects candidate {candidate_id} but does not cite its evidence: "
                    f"{', '.join(uncited)}"
                )


def validate_decision_references(
    decision: Decision,
    snapshot: ProjectSnapshot,
    context: MusicalContext,
    observations: Iterable[Observation],
) -> None:
    """Check a decision's structural references against a snapshot, context and observations.

    This does not verify source-audio provenance (use ``validate_observation_sources``), whether
    the evidence justifies the action, or any protection, capability or bound. Supplied
    observations not referenced by the decision are ignored.
    """
    if decision.project_id != snapshot.project_id:
        raise ValueError(f"decision project {decision.project_id} does not match snapshot {snapshot.project_id}")
    if decision.expected_revision != snapshot.revision:
        raise ValueError(f"decision expects revision {decision.expected_revision}, snapshot is {snapshot.revision}")
    if decision.context_id != context.context_id or decision.context_version != context.version:
        raise ValueError(
            f"decision context {decision.context_id}@{decision.context_version} does not match "
            f"supplied context {context.context_id}@{context.version}"
        )
    context.validate_against(snapshot)

    by_id: dict[ObservationId, Observation] = {}
    for observation in observations:
        require_instance(observation, Observation, "observation")
        if observation.observation_id in by_id:
            raise ValueError(f"duplicate observation ID supplied: {observation.observation_id}")
        by_id[observation.observation_id] = observation
    missing = [str(o) for o in decision.evidence_ids if o not in by_id]
    if missing:
        raise ValueError(f"missing referenced observations: {', '.join(missing)}")
    for observation_id in decision.evidence_ids:
        by_id[observation_id].scope.validate_against(snapshot)

    if isinstance(decision.outcome, ProposeAction):
        target = decision.outcome.action.operation.track_id
        snapshot.require_known_tracks((target,), "proposed action target")
