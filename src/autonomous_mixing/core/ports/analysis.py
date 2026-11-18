"""The analysis port and the checks on its responses."""

from typing import Protocol

from autonomous_mixing.core.records.analysis import (
    AnalysisRequest,
    AnalysisResponse,
    require_analysis_scope_within,
)
from autonomous_mixing.core.records.identifiers import VersionRef
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.observation import AnalysisScope, validate_observation_sources
from autonomous_mixing.core.records.results import PortResult


class AudioAnalyzer(Protocol):
    """Analyzes artifacts under a versioned specification into typed observations.

    Implementations return expected failures as ``PortFailure``; callers validate successful
    responses with ``validate_analysis_response``.
    """

    def analyze(self, request: AnalysisRequest) -> PortResult[AnalysisResponse]: ...


def _job(key: tuple[VersionRef, AnalysisScope]) -> str:
    metric, scope = key
    return f"{metric.name}@{metric.version} over {scope}"


def validate_analysis_response(request: AnalysisRequest, response: AnalysisResponse) -> None:
    """Check provenance, specification, scope containment and exact completeness of a response.

    Completeness is structural: exactly one observation per requested metric-and-scope job, no others.
    An ``Unavailable`` result satisfies it, but is not a usable number; whether a measurement matches a
    criterion (unit, analyzer, sources) and any verdict are decided by verification, not here.
    """
    require_instance(request, AnalysisRequest, "request")
    require_instance(response, AnalysisResponse, "response")
    requested = request.specification.metric_ids
    jobs = [(d.selector.metric, d.selector.scope) for d in request.definitions]
    seen: set[tuple[VersionRef, AnalysisScope]] = set()
    for observation in response.observations:
        label = f"observation {observation.observation_id}"
        audio, snapshot = request.state.audio, request.state.snapshot
        validate_observation_sources(observation, audio.artifacts, audio, snapshot)
        if observation.analysis_spec != request.specification.ref:
            raise ValueError(f"{label} uses analysis spec {observation.analysis_spec}, not the requested one")
        if observation.metric not in requested:
            raise ValueError(
                f"{label} reports unrequested metric {observation.metric.name}@{observation.metric.version}"
            )
        require_analysis_scope_within(observation.scope, request.scope, label)
        key = (observation.metric, observation.scope)
        if key not in jobs:
            raise ValueError(f"{label} reports {_job(key)}, which was not requested")
        if key in seen:
            raise ValueError(f"{label} duplicates another observation of the same metric and scope")
        seen.add(key)
    missing = [_job(key) for key in jobs if key not in seen]
    if missing:
        raise ValueError(f"no observation for requested metric and scope: {'; '.join(missing)}")
