"""Controller decisions and the decision port's request and response records."""

from dataclasses import dataclass
from enum import Enum

from autonomous_mixing.core.records.actions import Action
from autonomous_mixing.core.records.budgets import BudgetLimits, BudgetUsage
from autonomous_mixing.core.records.capabilities import ActionCapabilityView
from autonomous_mixing.core.records.context import MusicalContext
from autonomous_mixing.core.records.diagnosis import DiagnosisReport, validate_diagnosis_references
from autonomous_mixing.core.records.feedback import DecisionHistory, validate_decision_history
from autonomous_mixing.core.records.identifiers import ContextId, DecisionId, ObservationId, ProjectId, Revision, RunId
from autonomous_mixing.core.records.invariants import require_instance, require_text, require_tuple_of
from autonomous_mixing.core.records.observation import AnalysisScope, Observation, validate_observation_sources
from autonomous_mixing.core.records.project import ProjectSnapshot, ProjectState
from autonomous_mixing.core.records.verification import AcceptanceCriteria


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


def _outside_scope(inner: AnalysisScope, outer: AnalysisScope) -> str | None:
    """Why ``inner`` is not contained in ``outer``, or None; exact closed comparisons, no clamping."""
    outside = sorted(str(t) for t in set(inner.track_ids) - set(outer.track_ids))
    if outside:
        return f"uses tracks outside the run scope: {', '.join(outside)}"
    time, run_time = inner.time_range, outer.time_range
    if time.start_seconds < run_time.start_seconds or time.end_seconds > run_time.end_seconds:
        return "time range lies outside the run scope"
    band, run_band = inner.frequency_range, outer.frequency_range
    if run_band is not None:
        if band is None:
            return "needs an explicit frequency range within the run scope"
        if band.low_hz < run_band.low_hz or band.high_hz > run_band.high_hz:
            return "frequency range lies outside the run scope"
    return None


def _require_criteria_within(criteria: AcceptanceCriteria, scope: AnalysisScope, snapshot: ProjectSnapshot) -> None:
    for condition in (*criteria.targets, *criteria.improvements, *criteria.guards):
        selector_scope = condition.selector.scope
        selector_scope.validate_against(snapshot)
        problem = _outside_scope(selector_scope, scope)
        if problem is not None:
            raise ValueError(f"Situation criteria condition {condition.condition_id} {problem}")


def _require_capabilities_bound(
    view: ActionCapabilityView, state: ProjectState, context: MusicalContext, scope: AnalysisScope
) -> None:
    if (view.project_id, view.revision) != (state.project_id, state.revision):
        raise ValueError(
            f"Situation.capabilities describe {view.project_id}@{view.revision}, "
            f"not the state {state.project_id}@{state.revision}"
        )
    if (view.context_id, view.context_version) != (context.context_id, context.version):
        raise ValueError(
            f"Situation.capabilities were declared for context {view.context_id} version {view.context_version}, "
            f"not {context.context_id} version {context.version}"
        )
    view.scope.validate_against(state.snapshot)
    if view.scope != scope:
        raise ValueError("Situation.capabilities scope differs from the run scope")


@dataclass(frozen=True)
class Situation:
    """Runtime-visible inputs for one decision call (attempt contract, sections 11-15).

    Usage may exceed limits (the record describes, it does not enforce) and includes the charge for this
    call: the first call has an empty history and ``usage.decision_calls == 1``; a later call carries the
    complete prior-call prefix. Artifact locations are ordinary runtime source references; this record
    alone does not sandbox a provider. Values are kept exactly as supplied.

    Construction checks structure and bindings only: current observations against the state's audio
    (historical ones in ``history`` keep their own revision and are not rechecked), the run scope and every
    criteria selector against the snapshot and containment in the run scope, the diagnosis report's
    references, capabilities bound to the state, context and exact run scope, and the history prefix
    against ``run_id``, the state and ``usage``. It does not check metric-catalog membership, units or
    render Nyquist limits (no analysis or render specification is carried), agreement with an
    authoritative run configuration, policy or renderer, or that ``run_id`` names a persisted run.
    Advertised capabilities are not authorization; empty candidates, available actions or history imply
    neither the absence of problems, nor success, nor permission.
    """

    state: ProjectState
    context: MusicalContext
    controller: ControllerIdentity
    observations: tuple[Observation, ...]
    usage: BudgetUsage
    limits: BudgetLimits
    run_id: RunId
    scope: AnalysisScope
    criteria: AcceptanceCriteria
    diagnosis: DiagnosisReport
    capabilities: ActionCapabilityView
    history: DecisionHistory

    def __post_init__(self) -> None:
        require_instance(self.state, ProjectState, "Situation.state")
        require_instance(self.context, MusicalContext, "Situation.context")
        require_instance(self.controller, ControllerIdentity, "Situation.controller")
        _require_unique_observations(self.observations, "Situation.observations")
        require_instance(self.usage, BudgetUsage, "Situation.usage")
        require_instance(self.limits, BudgetLimits, "Situation.limits")
        require_instance(self.run_id, RunId, "Situation.run_id")
        require_instance(self.scope, AnalysisScope, "Situation.scope")
        require_instance(self.criteria, AcceptanceCriteria, "Situation.criteria")
        require_instance(self.diagnosis, DiagnosisReport, "Situation.diagnosis")
        require_instance(self.capabilities, ActionCapabilityView, "Situation.capabilities")
        require_instance(self.history, DecisionHistory, "Situation.history")
        snapshot, audio = self.state.snapshot, self.state.audio
        self.context.validate_against(snapshot)
        for observation in self.observations:
            validate_observation_sources(observation, audio.artifacts, audio, snapshot)
        self.scope.validate_against(snapshot)
        _require_criteria_within(self.criteria, self.scope, snapshot)
        validate_diagnosis_references(
            self.diagnosis, state=self.state, scope=self.scope, observations=self.observations
        )
        _require_capabilities_bound(self.capabilities, self.state, self.context, self.scope)
        validate_decision_history(self.history, state=self.state, run_id=self.run_id, usage=self.usage)


@dataclass(frozen=True)
class DecisionResponse:
    decision: Decision

    def __post_init__(self) -> None:
        require_instance(self.decision, Decision, "DecisionResponse.decision")
