"""Audio artifact metadata records."""

import re
from dataclasses import dataclass
from enum import Enum

from autonomous_mixing.core.records.identifiers import ArtifactId, ProjectId, Revision, TrackId, VersionRef
from autonomous_mixing.core.records.invariants import (
    require_count,
    require_finite,
    require_finite_result,
    require_instance,
    require_text,
    require_tuple_of,
)

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


class ArtifactKind(Enum):
    STEM = "stem"
    GROUP = "group"
    MIX = "mix"


@dataclass(frozen=True)
class AudioArtifact:
    """Metadata of rendered or supplied audio. The hash establishes file identity, not perceptual equivalence.

    ``revision`` is the production revision: the project state the artifact was produced from.
    It is provenance and is never retagged; membership of a later state is recorded by an
    ``ArtifactCheckpoint``. Derived timeline end and Nyquist values must be finite floats.
    """

    artifact_id: ArtifactId
    project_id: ProjectId
    revision: Revision
    source_track_ids: tuple[TrackId, ...]
    kind: ArtifactKind
    location: str
    timeline_start_seconds: float
    sample_rate_hz: int
    channel_count: int
    frame_count: int
    content_hash: str
    render_spec: VersionRef

    def __post_init__(self) -> None:
        require_instance(self.artifact_id, ArtifactId, "AudioArtifact.artifact_id")
        require_instance(self.project_id, ProjectId, "AudioArtifact.project_id")
        require_instance(self.revision, Revision, "AudioArtifact.revision")
        require_tuple_of(self.source_track_ids, TrackId, "AudioArtifact.source_track_ids", nonempty=True, distinct=True)
        require_instance(self.kind, ArtifactKind, "AudioArtifact.kind")
        require_text(self.location, "AudioArtifact.location")
        if require_finite(self.timeline_start_seconds, "AudioArtifact.timeline_start_seconds") < 0:
            raise ValueError("AudioArtifact.timeline_start_seconds must be >= 0")
        require_count(self.sample_rate_hz, "AudioArtifact.sample_rate_hz", minimum=1)
        if require_count(self.channel_count, "AudioArtifact.channel_count", minimum=1) > 2:
            raise ValueError(f"AudioArtifact.channel_count must be 1 or 2, got {self.channel_count}")
        require_count(self.frame_count, "AudioArtifact.frame_count", minimum=0)
        if not isinstance(self.content_hash, str) or not _SHA256_HEX.fullmatch(self.content_hash):
            raise ValueError("AudioArtifact.content_hash must be a lowercase SHA-256 hex digest")
        require_instance(self.render_spec, VersionRef, "AudioArtifact.render_spec")
        require_finite_result(lambda: self.timeline_end_seconds, "AudioArtifact timeline end")
        require_finite_result(lambda: self.nyquist_hz, "AudioArtifact Nyquist frequency")

    @property
    def timeline_end_seconds(self) -> float:
        """End of the covered project time, derived from metadata only (no alignment verification)."""
        return self.timeline_start_seconds + self.frame_count / self.sample_rate_hz

    @property
    def nyquist_hz(self) -> float:
        return self.sample_rate_hz / 2
