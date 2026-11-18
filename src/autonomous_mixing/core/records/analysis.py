"""The analysis port's request and response records, and the record-local job and scope helpers.

An ``AnalysisRequest`` names its jobs explicitly: one ``MeasurementDefinition`` per requested metric and
scope (verification and diagnostic-only jobs alike) and one logical root per current audio member.
Construction checks metadata only: it selects no files, resolves no source requirement to an artifact,
inspects no path and implies no scientific validity of a measurement method.
"""

from dataclasses import dataclass

from autonomous_mixing.core.records.identifiers import VersionRef
from autonomous_mixing.core.records.integrity import ArtifactRootBinding
from autonomous_mixing.core.records.invariants import require_instance, require_tuple_of
from autonomous_mixing.core.records.observation import AnalysisScope, AnalysisSpec, Observation
from autonomous_mixing.core.records.project import ProjectState
from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition


def require_analysis_scope_within(inner: AnalysisScope, outer: AnalysisScope, label: str) -> None:
    """Require ``inner`` to lie within ``outer``: tracks, time range and, if ``outer`` has one, frequency band."""
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


def _job_label(metric: VersionRef, scope: AnalysisScope) -> str:
    tracks = ", ".join(str(t) for t in scope.track_ids)
    band = scope.frequency_range
    span = f"{scope.time_range.start_seconds}-{scope.time_range.end_seconds} s"
    return f"{metric.name}@{metric.version} over tracks {tracks}, {span}" + (
        f", {band.low_hz}-{band.high_hz} Hz" if band is not None else ""
    )


def deduplicate_analysis_definitions(
    definitions: tuple[MeasurementDefinition, ...],
) -> tuple[MeasurementDefinition, ...]:
    """Keep the first definition object per metric and scope, in first-occurrence order.

    Equal repeats coalesce; an unequal definition for the same metric and scope (any differing field:
    unit, settings, analyzer, alignment or sources) is a conflict and raises ``ValueError``.
    """
    require_tuple_of(definitions, MeasurementDefinition, "analysis definitions", nonempty=True, distinct=False)
    kept: dict[tuple[VersionRef, AnalysisScope], MeasurementDefinition] = {}
    for definition in definitions:
        key = (definition.selector.metric, definition.selector.scope)
        first = kept.setdefault(key, definition)
        if first != definition:
            raise ValueError(f"conflicting analysis definitions for {_job_label(*key)}")
    return tuple(kept.values())


def _require_unique_observations(observations: tuple[Observation, ...], field: str) -> None:
    require_tuple_of(observations, Observation, field, nonempty=False, distinct=False)
    ids = [observation.observation_id for observation in observations]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{field} must have unique observation IDs")


@dataclass(frozen=True)
class AnalysisRequest:
    """Request to analyze one project state with explicit jobs and current-member roots.

    ``definitions`` has exactly one job per metric and scope, each under ``specification`` for one of its
    metrics and within ``scope``; every requested metric has at least one job (callers deduplicate with
    ``deduplicate_analysis_definitions``; fields are never canonicalized here). ``root_bindings`` binds
    exactly the current audio members, in any order; superseded fixed inputs are not analysis roots. Audio
    members need not each cover the whole scope; source coverage is checked per resulting observation.
    """

    state: ProjectState
    scope: AnalysisScope
    specification: AnalysisSpec
    definitions: tuple[MeasurementDefinition, ...]
    root_bindings: tuple[ArtifactRootBinding, ...]

    def __post_init__(self) -> None:
        require_instance(self.state, ProjectState, "AnalysisRequest.state")
        require_instance(self.scope, AnalysisScope, "AnalysisRequest.scope")
        require_instance(self.specification, AnalysisSpec, "AnalysisRequest.specification")
        self.scope.validate_against(self.state.snapshot)
        self._validate_definitions()
        self._validate_root_bindings()

    def _validate_definitions(self) -> None:
        require_tuple_of(
            self.definitions, MeasurementDefinition, "AnalysisRequest.definitions", nonempty=True, distinct=False
        )
        spec = self.specification
        keys: set[tuple[VersionRef, AnalysisScope]] = set()
        for definition in self.definitions:
            metric, scope = definition.selector.metric, definition.selector.scope
            label = f"analysis definition {_job_label(metric, scope)}"
            if definition.analysis_spec != spec.ref:
                raise ValueError(f"{label} uses analysis spec {definition.analysis_spec}, not the requested one")
            if metric not in spec.metric_ids:
                raise ValueError(f"{label} names an unrequested metric")
            require_analysis_scope_within(scope, self.scope, label)
            if (metric, scope) in keys:
                raise ValueError(f"{label} is defined more than once")
            keys.add((metric, scope))
        defined = {metric for metric, _ in keys}
        undefined = [f"{m.name}@{m.version}" for m in spec.metric_ids if m not in defined]
        if undefined:
            raise ValueError(f"no analysis definition for requested metrics: {', '.join(undefined)}")

    def _validate_root_bindings(self) -> None:
        require_tuple_of(
            self.root_bindings, ArtifactRootBinding, "AnalysisRequest.root_bindings", nonempty=False, distinct=False
        )
        bound = [binding.artifact_id for binding in self.root_bindings]
        duplicates = sorted({str(a) for a in bound if bound.count(a) > 1})
        if duplicates:
            raise ValueError(f"duplicate analysis root bindings for: {', '.join(duplicates)}")
        current = {artifact.artifact_id for artifact in self.state.audio.artifacts}
        missing = sorted(str(a) for a in current - set(bound))
        extra = sorted(str(a) for a in set(bound) - current)
        if missing or extra:
            raise ValueError(
                f"analysis root bindings must name exactly the current audio members (missing: {missing}, extra: {extra})"
            )


@dataclass(frozen=True)
class AnalysisResponse:
    """Observations produced for a request. Empty is structurally valid; completeness is request-relative."""

    observations: tuple[Observation, ...]

    def __post_init__(self) -> None:
        _require_unique_observations(self.observations, "AnalysisResponse.observations")
