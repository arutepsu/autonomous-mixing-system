"""The analysis port's request and response records."""

from dataclasses import dataclass

from autonomous_mixing.core.records.invariants import require_instance, require_tuple_of
from autonomous_mixing.core.records.observation import AnalysisScope, AnalysisSpec, Observation
from autonomous_mixing.core.records.project import ProjectState


def _require_unique_observations(observations: tuple[Observation, ...], field: str) -> None:
    require_tuple_of(observations, Observation, field, nonempty=False, distinct=False)
    ids = [observation.observation_id for observation in observations]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{field} must have unique observation IDs")


@dataclass(frozen=True)
class AnalysisRequest:
    """Request to analyze one project state.

    Audio members need not each cover the whole scope, since different artifacts may serve different
    metrics. Source coverage is checked per resulting observation.
    """

    state: ProjectState
    scope: AnalysisScope
    specification: AnalysisSpec

    def __post_init__(self) -> None:
        require_instance(self.state, ProjectState, "AnalysisRequest.state")
        require_instance(self.scope, AnalysisScope, "AnalysisRequest.scope")
        require_instance(self.specification, AnalysisSpec, "AnalysisRequest.specification")
        self.scope.validate_against(self.state.snapshot)


@dataclass(frozen=True)
class AnalysisResponse:
    """Observations produced for a request. Empty is structurally valid; completeness is request-relative."""

    observations: tuple[Observation, ...]

    def __post_init__(self) -> None:
        _require_unique_observations(self.observations, "AnalysisResponse.observations")
