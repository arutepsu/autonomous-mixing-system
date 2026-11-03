"""The analysis port and the checks on its responses."""

from typing import Protocol

from autonomous_mixing.core.records.analysis import AnalysisRequest, AnalysisResponse
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


def _require_scope_within(inner: AnalysisScope, outer: AnalysisScope, label: str) -> None:
    outside = sorted(str(t) for t in set(inner.track_ids) - set(outer.track_ids))
    if outside:
        raise ValueError(f"{label} uses tracks outside the requested scope: {', '.join(outside)}")
    if (
        inner.time_range.start_seconds < outer.time_range.start_seconds
        or inner.time_range.end_seconds > outer.time_range.end_seconds
    ):
        raise ValueError(f"{label} time range lies outside the requested scope")
    if outer.frequency_range is not None:
        band = inner.frequency_range
        if band is None:
            raise ValueError(f"{label} needs an explicit frequency range within the requested scope")
        if band.low_hz < outer.frequency_range.low_hz or band.high_hz > outer.frequency_range.high_hz:
            raise ValueError(f"{label} frequency range lies outside the requested scope")


def validate_analysis_response(request: AnalysisRequest, response: AnalysisResponse) -> None:
    """Check provenance, specification, scope containment and metric completeness of a response."""
    require_instance(request, AnalysisRequest, "request")
    require_instance(response, AnalysisResponse, "response")
    requested = request.specification.metric_ids
    produced: set[VersionRef] = set()
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
        _require_scope_within(observation.scope, request.scope, label)
        key = (observation.metric, observation.scope)
        if key in seen:
            raise ValueError(f"{label} duplicates another observation of the same metric and scope")
        seen.add(key)
        produced.add(observation.metric)
    missing = [f"{m.name}@{m.version}" for m in requested if m not in produced]
    if missing:
        raise ValueError(f"no observation for requested metrics: {', '.join(missing)}")
