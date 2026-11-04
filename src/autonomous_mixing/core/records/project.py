"""Project tracks, snapshots, artifact checkpoints, gain processing state and composite project state."""

from dataclasses import dataclass
from datetime import datetime

from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.identifiers import ArtifactId, CheckpointId, ProjectId, Revision, TrackId
from autonomous_mixing.core.records.invariants import (
    require_finite,
    require_instance,
    require_text,
    require_tuple_of,
    require_utc,
)


@dataclass(frozen=True)
class Track:
    """A track with stable identity. Musical roles are declared in MusicalContext, never derived from the name."""

    track_id: TrackId
    name: str

    def __post_init__(self) -> None:
        require_instance(self.track_id, TrackId, "Track.track_id")
        require_text(self.name, "Track.name")


@dataclass(frozen=True)
class ProjectSnapshot:
    project_id: ProjectId
    revision: Revision
    captured_at: datetime
    tracks: tuple[Track, ...]

    def __post_init__(self) -> None:
        require_instance(self.project_id, ProjectId, "ProjectSnapshot.project_id")
        require_instance(self.revision, Revision, "ProjectSnapshot.revision")
        require_utc(self.captured_at, "ProjectSnapshot.captured_at")
        require_tuple_of(self.tracks, Track, "ProjectSnapshot.tracks", nonempty=True, distinct=False)
        ids = [track.track_id for track in self.tracks]
        if len(set(ids)) != len(ids):
            raise ValueError("ProjectSnapshot.tracks must have unique track IDs")

    @property
    def track_ids(self) -> frozenset[TrackId]:
        return frozenset(track.track_id for track in self.tracks)

    def require_known_tracks(self, track_ids: tuple[TrackId, ...] | frozenset[TrackId], field: str) -> None:
        unknown = sorted(str(t) for t in set(track_ids) - self.track_ids)
        if unknown:
            raise ValueError(f"{field} references tracks not in project {self.project_id}: {', '.join(unknown)}")


def _require_unique_artifacts(artifacts: tuple[AudioArtifact, ...], field: str, *, nonempty: bool) -> None:
    require_tuple_of(artifacts, AudioArtifact, field, nonempty=nonempty, distinct=False)
    ids = [artifact.artifact_id for artifact in artifacts]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{field} must have unique artifact IDs")


@dataclass(frozen=True)
class ArtifactCheckpoint:
    """Artifact membership of one project state (stems, groups and/or mixes).

    ``revision`` identifies the represented project state. Member artifacts keep their own
    production revisions, which may be earlier than the state revision. A checkpoint may
    describe a candidate or an accepted state; its existence alone does not establish
    acceptance. Complete project coverage is not required, and one artifact per track is not
    assumed.
    """

    checkpoint_id: CheckpointId
    project_id: ProjectId
    revision: Revision
    artifacts: tuple[AudioArtifact, ...]

    def __post_init__(self) -> None:
        require_instance(self.checkpoint_id, CheckpointId, "ArtifactCheckpoint.checkpoint_id")
        require_instance(self.project_id, ProjectId, "ArtifactCheckpoint.project_id")
        require_instance(self.revision, Revision, "ArtifactCheckpoint.revision")
        _require_unique_artifacts(self.artifacts, "ArtifactCheckpoint.artifacts", nonempty=True)
        for artifact in self.artifacts:
            if artifact.project_id != self.project_id:
                raise ValueError(
                    f"checkpoint artifact {artifact.artifact_id} belongs to project {artifact.project_id}, "
                    f"not {self.project_id}"
                )

    @property
    def artifact_ids(self) -> frozenset[ArtifactId]:
        return frozenset(artifact.artifact_id for artifact in self.artifacts)

    @property
    def known_revisions(self) -> frozenset[Revision]:
        """Revisions visible in this membership: the state revision and every member's production revision.

        Local collision detection only; it knows nothing about fixed baselines or unseen history.
        """
        return frozenset({self.revision, *(artifact.revision for artifact in self.artifacts)})

    def artifact_for(self, artifact_id: ArtifactId) -> AudioArtifact | None:
        """Return the recorded member with this ID, or ``None`` if it is not a member."""
        for artifact in self.artifacts:
            if artifact.artifact_id == artifact_id:
                return artifact
        return None

    def validate_against(self, snapshot: ProjectSnapshot) -> None:
        """Require the represented state to be the snapshot's project state and known tracks only."""
        if self.project_id != snapshot.project_id:
            raise ValueError(f"checkpoint project {self.project_id} does not match snapshot {snapshot.project_id}")
        if self.revision != snapshot.revision:
            raise ValueError(f"checkpoint state revision {self.revision} does not match snapshot {snapshot.revision}")
        for artifact in self.artifacts:
            snapshot.require_known_tracks(artifact.source_track_ids, f"checkpoint artifact {artifact.artifact_id}")


@dataclass(frozen=True)
class TrackGainState:
    """Absolute additional gain (dB) for one track relative to its fixed input stem. No default or bounds."""

    track_id: TrackId
    input_artifact: AudioArtifact
    gain_db: float

    def __post_init__(self) -> None:
        require_instance(self.track_id, TrackId, "TrackGainState.track_id")
        require_instance(self.input_artifact, AudioArtifact, "TrackGainState.input_artifact")
        require_finite(self.gain_db, "TrackGainState.gain_db")
        if self.input_artifact.kind is not ArtifactKind.STEM:
            raise ValueError(f"fixed input {self.input_artifact.artifact_id} must be a STEM artifact")
        if self.input_artifact.source_track_ids != (self.track_id,):
            raise ValueError(
                f"fixed input {self.input_artifact.artifact_id} must be a stem of exactly track {self.track_id}"
            )


@dataclass(frozen=True)
class ProjectProcessingState:
    """Gain settings of one project state. Coverage may be partial or empty; absent tracks are unrepresented."""

    project_id: ProjectId
    revision: Revision
    track_gains: tuple[TrackGainState, ...]

    def __post_init__(self) -> None:
        require_instance(self.project_id, ProjectId, "ProjectProcessingState.project_id")
        require_instance(self.revision, Revision, "ProjectProcessingState.revision")
        require_tuple_of(
            self.track_gains, TrackGainState, "ProjectProcessingState.track_gains", nonempty=False, distinct=False
        )
        tracks = [gain.track_id for gain in self.track_gains]
        if len(set(tracks)) != len(tracks):
            raise ValueError("ProjectProcessingState.track_gains must have unique track IDs")
        inputs: dict[ArtifactId, AudioArtifact] = {}
        for gain in self.track_gains:
            artifact = gain.input_artifact
            if artifact.project_id != self.project_id:
                raise ValueError(
                    f"fixed input {artifact.artifact_id} belongs to project {artifact.project_id}, not {self.project_id}"
                )
            recorded = inputs.setdefault(artifact.artifact_id, artifact)
            if recorded != artifact:
                raise ValueError(f"fixed input {artifact.artifact_id} is described with conflicting metadata")

    def validate_against(self, snapshot: ProjectSnapshot) -> None:
        """Require the snapshot's project and state revision and known tracks; no file or capability checks."""
        if self.project_id != snapshot.project_id:
            raise ValueError(f"processing project {self.project_id} does not match snapshot {snapshot.project_id}")
        if self.revision != snapshot.revision:
            raise ValueError(f"processing state revision {self.revision} does not match snapshot {snapshot.revision}")
        snapshot.require_known_tracks(tuple(gain.track_id for gain in self.track_gains), "ProjectProcessingState")


@dataclass(frozen=True)
class ProjectState:
    """One consistent project state; it may describe a candidate or an accepted state.

    Snapshot, processing and audio must share the project and state revision. Fixed input artifacts
    need not be current audio members (a superseded baseline must stay available to later
    execution); where an input ID is also an audio member, its metadata must match exactly.
    Constructing a state does not accept it, and structural consistency is not proof that rendered
    files reflect the parameter state. Availability, retention and integrity of artifacts are later
    adapter responsibilities.
    """

    snapshot: ProjectSnapshot
    processing: ProjectProcessingState
    audio: ArtifactCheckpoint

    def __post_init__(self) -> None:
        require_instance(self.snapshot, ProjectSnapshot, "ProjectState.snapshot")
        require_instance(self.processing, ProjectProcessingState, "ProjectState.processing")
        require_instance(self.audio, ArtifactCheckpoint, "ProjectState.audio")
        self.processing.validate_against(self.snapshot)
        self.audio.validate_against(self.snapshot)
        for gain in self.processing.track_gains:
            member = self.audio.artifact_for(gain.input_artifact.artifact_id)
            if member is not None and member != gain.input_artifact:
                raise ValueError(
                    f"fixed input {gain.input_artifact.artifact_id} differs from the audio member with the same ID"
                )

    @property
    def project_id(self) -> ProjectId:
        return self.snapshot.project_id

    @property
    def revision(self) -> Revision:
        return self.snapshot.revision

    @property
    def known_revisions(self) -> frozenset[Revision]:
        """Locally known revisions: the state revision, audio members' and fixed baselines' production revisions.

        A new candidate state must not reuse any of these. Local collision detection only: global
        uniqueness and revisions of unseen history need identity allocation and a ledger.
        """
        baselines = (gain.input_artifact.revision for gain in self.processing.track_gains)
        return self.audio.known_revisions | frozenset(baselines)
