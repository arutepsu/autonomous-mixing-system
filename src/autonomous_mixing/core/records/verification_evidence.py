"""Measurement definitions and evidence-match results for verification (attempt contract, sections 19-20).

A ``MeasurementDefinition`` declares which measurement a criterion needs: an exact metric and scope,
unit, analysis settings, a caller-declared alignment reference and the ordered source subjects with their
metadata. ``StateEvidence`` bundles one project state's observations. Matching (``core.verification.
evidence``) states only that declared metadata agree; physical alignment, file integrity, cross-state
binding and the validity of a measurement method are established elsewhere. No verdict is computed here.
"""

from dataclasses import dataclass
from enum import Enum

from autonomous_mixing.core.records.artifacts import ArtifactKind
from autonomous_mixing.core.records.identifiers import TrackId, VersionRef
from autonomous_mixing.core.records.invariants import (
    require_count,
    require_finite,
    require_instance,
    require_text,
    require_tuple_of,
)
from autonomous_mixing.core.records.observation import MeasuredScalar, Observation
from autonomous_mixing.core.records.project import ProjectState
from autonomous_mixing.core.records.verification import MetricSelector


@dataclass(frozen=True)
class SourceRequirement:
    """One required source subject (kind and ordered contributors) and its exact declared metadata."""

    kind: ArtifactKind
    source_track_ids: tuple[TrackId, ...]
    render_spec: VersionRef
    sample_rate_hz: int
    channel_count: int
    timeline_start_seconds: float
    frame_count: int

    def __post_init__(self) -> None:
        require_instance(self.kind, ArtifactKind, "SourceRequirement.kind")
        require_tuple_of(
            self.source_track_ids, TrackId, "SourceRequirement.source_track_ids", nonempty=True, distinct=True
        )
        require_instance(self.render_spec, VersionRef, "SourceRequirement.render_spec")
        require_count(self.sample_rate_hz, "SourceRequirement.sample_rate_hz", minimum=1)
        if require_count(self.channel_count, "SourceRequirement.channel_count", minimum=1) > 2:
            raise ValueError(f"SourceRequirement.channel_count must be 1 or 2, got {self.channel_count}")
        if require_finite(self.timeline_start_seconds, "SourceRequirement.timeline_start_seconds") < 0:
            raise ValueError("SourceRequirement.timeline_start_seconds must be >= 0")
        require_count(self.frame_count, "SourceRequirement.frame_count", minimum=0)


@dataclass(frozen=True)
class MeasurementDefinition:
    """The exact measurement a criterion needs; sources are ordered and must cover the selector's tracks.

    The same track coverage can describe a stem pair or a summed mix: the number, kinds and ordered
    contributors of ``sources`` distinguish them.
    """

    selector: MetricSelector
    unit: str
    analysis_spec: VersionRef
    analyzer: VersionRef
    alignment: VersionRef
    sources: tuple[SourceRequirement, ...]

    def __post_init__(self) -> None:
        require_instance(self.selector, MetricSelector, "MeasurementDefinition.selector")
        require_text(self.unit, "MeasurementDefinition.unit")
        require_instance(self.analysis_spec, VersionRef, "MeasurementDefinition.analysis_spec")
        require_instance(self.analyzer, VersionRef, "MeasurementDefinition.analyzer")
        require_instance(self.alignment, VersionRef, "MeasurementDefinition.alignment")
        require_tuple_of(
            self.sources, SourceRequirement, "MeasurementDefinition.sources", nonempty=True, distinct=False
        )
        contributors = {track for source in self.sources for track in source.source_track_ids}
        uncovered = [str(t) for t in self.selector.scope.track_ids if t not in contributors]
        if uncovered:
            raise ValueError(f"MeasurementDefinition.sources do not cover selector tracks: {', '.join(uncovered)}")


@dataclass(frozen=True)
class StateEvidence:
    """One project state, its observations (possibly none) and the caller's declared alignment reference.

    Observation IDs are unique within the bundle, even for identical records. Distinct IDs with the same
    metric and scope are allowed, so the matcher can report ambiguity. Cross-state ID consistency belongs
    to the multi-state verifier.
    """

    state: ProjectState
    observations: tuple[Observation, ...]
    alignment: VersionRef

    def __post_init__(self) -> None:
        require_instance(self.state, ProjectState, "StateEvidence.state")
        require_tuple_of(self.observations, Observation, "StateEvidence.observations", nonempty=False, distinct=False)
        ids = [observation.observation_id for observation in self.observations]
        if len(set(ids)) != len(ids):
            raise ValueError("StateEvidence.observations must have unique observation IDs")
        require_instance(self.alignment, VersionRef, "StateEvidence.alignment")


class EvidenceUnavailableCode(Enum):
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    SETTINGS_MISMATCH = "settings_mismatch"
    INVALID_SOURCE = "invalid_source"
    SUBJECT_MISMATCH = "subject_mismatch"
    SOURCE_METADATA_MISMATCH = "source_metadata_mismatch"
    UNAVAILABLE_RESULT = "unavailable_result"
    UNIT_MISMATCH = "unit_mismatch"


@dataclass(frozen=True)
class MatchedEvidence:
    """The original observation that matched a definition; its result is a measured scalar (zero included)."""

    observation: Observation

    def __post_init__(self) -> None:
        require_instance(self.observation, Observation, "MatchedEvidence.observation")
        require_instance(self.observation.result, MeasuredScalar, "MatchedEvidence.observation.result")


@dataclass(frozen=True)
class EvidenceUnavailable:
    """Why no usable measurement was found for a definition in one state."""

    code: EvidenceUnavailableCode
    explanation: str

    def __post_init__(self) -> None:
        require_instance(self.code, EvidenceUnavailableCode, "EvidenceUnavailable.code")
        require_text(self.explanation, "EvidenceUnavailable.explanation")


EvidenceMatch = MatchedEvidence | EvidenceUnavailable
