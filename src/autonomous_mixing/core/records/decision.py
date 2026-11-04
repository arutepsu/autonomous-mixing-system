"""Controller decisions and the decision port's request and response records."""

from dataclasses import dataclass
from enum import Enum

from autonomous_mixing.core.records.actions import Action
from autonomous_mixing.core.records.budgets import BudgetLimits, BudgetUsage
from autonomous_mixing.core.records.context import MusicalContext
from autonomous_mixing.core.records.identifiers import ContextId, DecisionId, ObservationId, ProjectId, Revision
from autonomous_mixing.core.records.invariants import require_instance, require_text, require_tuple_of
from autonomous_mixing.core.records.observation import Observation, validate_observation_sources
from autonomous_mixing.core.records.project import ProjectState


@dataclass(frozen=True)
class ControllerIdentity:
    name: str
    version: str

    def __post_init__(self) -> None:
        require_text(self.name, "ControllerIdentity.name")
        require_text(self.version, "ControllerIdentity.version")


@dataclass(frozen=True)
class ProposeAction:
    action: Action
    expected_effect: str

    def __post_init__(self) -> None:
        require_instance(self.action, Action, "ProposeAction.action")
        require_text(self.expected_effect, "ProposeAction.expected_effect")


class AbstentionReason(Enum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    MISSING_CONTEXT = "missing_context"
    CONFLICTING_CONSTRAINTS = "conflicting_constraints"
    NO_JUSTIFIED_ACTION = "no_justified_action"


@dataclass(frozen=True)
class Abstain:
    """An explicit decision not to act. ``requested_context`` explains what is missing; it does not pause a run."""

    reason: AbstentionReason
    requested_context: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_instance(self.reason, AbstentionReason, "Abstain.reason")
        require_tuple_of(self.requested_context, str, "Abstain.requested_context", nonempty=False, distinct=True)
        for entry in self.requested_context:
            require_text(entry, "Abstain.requested_context element")


@dataclass(frozen=True)
class Decision:
    decision_id: DecisionId
    project_id: ProjectId
    expected_revision: Revision
    context_id: ContextId
    context_version: str
    evidence_ids: tuple[ObservationId, ...]
    rationale: str
    controller: ControllerIdentity
    outcome: ProposeAction | Abstain

    def __post_init__(self) -> None:
        require_instance(self.decision_id, DecisionId, "Decision.decision_id")
        require_instance(self.project_id, ProjectId, "Decision.project_id")
        require_instance(self.expected_revision, Revision, "Decision.expected_revision")
        require_instance(self.context_id, ContextId, "Decision.context_id")
        require_text(self.context_version, "Decision.context_version")
        require_tuple_of(self.evidence_ids, ObservationId, "Decision.evidence_ids", nonempty=False, distinct=True)
        require_text(self.rationale, "Decision.rationale")
        require_instance(self.controller, ControllerIdentity, "Decision.controller")
        require_instance(self.outcome, (ProposeAction, Abstain), "Decision.outcome")
        if isinstance(self.outcome, ProposeAction):
            if not self.evidence_ids:
                raise ValueError("Decision proposing an action must reference at least one observation")
            action = self.outcome.action
            if action.project_id != self.project_id:
                raise ValueError(f"proposed action targets project {action.project_id}, decision {self.project_id}")
            if action.expected_revision != self.expected_revision:
                raise ValueError(
                    f"proposed action expects revision {action.expected_revision}, decision {self.expected_revision}"
                )


def _require_unique_observations(observations: tuple[Observation, ...], field: str) -> None:
    # Decision's own copy of the observation component's private check (same check and messages), so
    # decision does not depend on observation. Consolidating validation semantics is a separate task.
    require_tuple_of(observations, Observation, field, nonempty=False, distinct=False)
    ids = [observation.observation_id for observation in observations]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{field} must have unique observation IDs")


@dataclass(frozen=True)
class Situation:
    """Runtime-visible inputs for one decision.

    Usage may exceed limits (the record describes, it does not enforce). Artifact locations are
    ordinary runtime source references; this record alone does not sandbox a provider.
    """

    state: ProjectState
    context: MusicalContext
    controller: ControllerIdentity
    observations: tuple[Observation, ...]
    usage: BudgetUsage
    limits: BudgetLimits

    def __post_init__(self) -> None:
        require_instance(self.state, ProjectState, "Situation.state")
        require_instance(self.context, MusicalContext, "Situation.context")
        require_instance(self.controller, ControllerIdentity, "Situation.controller")
        _require_unique_observations(self.observations, "Situation.observations")
        require_instance(self.usage, BudgetUsage, "Situation.usage")
        require_instance(self.limits, BudgetLimits, "Situation.limits")
        snapshot, audio = self.state.snapshot, self.state.audio
        self.context.validate_against(snapshot)
        for observation in self.observations:
            validate_observation_sources(observation, audio.artifacts, audio, snapshot)


@dataclass(frozen=True)
class DecisionResponse:
    decision: Decision

    def __post_init__(self) -> None:
        require_instance(self.decision, Decision, "DecisionResponse.decision")
