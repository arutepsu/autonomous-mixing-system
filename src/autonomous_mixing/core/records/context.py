"""Musical-context records: track roles, preferences and their provenance."""

from dataclasses import dataclass
from datetime import datetime

from autonomous_mixing.core.records.identifiers import ContextId, TrackId
from autonomous_mixing.core.records.invariants import require_instance, require_text, require_tuple_of, require_utc
from autonomous_mixing.core.records.project import ProjectSnapshot


@dataclass(frozen=True)
class AssertionSource:
    """Who supplied a context assertion, and when."""

    source_id: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        require_text(self.source_id, "AssertionSource.source_id")
        require_utc(self.recorded_at, "AssertionSource.recorded_at")


@dataclass(frozen=True)
class TrackRole:
    track_id: TrackId
    role: str
    source: AssertionSource

    def __post_init__(self) -> None:
        require_instance(self.track_id, TrackId, "TrackRole.track_id")
        require_text(self.role, "TrackRole.role")
        require_instance(self.source, AssertionSource, "TrackRole.source")


@dataclass(frozen=True)
class ContextPreference:
    """A declared preference; an empty ``track_ids`` tuple means it is not tied to specific tracks."""

    description: str
    track_ids: tuple[TrackId, ...]
    source: AssertionSource

    def __post_init__(self) -> None:
        require_text(self.description, "ContextPreference.description")
        require_tuple_of(self.track_ids, TrackId, "ContextPreference.track_ids", nonempty=False, distinct=True)
        require_instance(self.source, AssertionSource, "ContextPreference.source")


@dataclass(frozen=True)
class MusicalContext:
    """Versioned producer context.

    ``protected_track_ids`` is a structural representation for future enforcement; this record
    does not itself enforce protection. Constraint and preference types remain open (D6).
    """

    context_id: ContextId
    version: str
    track_roles: tuple[TrackRole, ...]
    preferences: tuple[ContextPreference, ...]
    protected_track_ids: frozenset[TrackId]

    def __post_init__(self) -> None:
        require_instance(self.context_id, ContextId, "MusicalContext.context_id")
        require_text(self.version, "MusicalContext.version")
        require_tuple_of(self.track_roles, TrackRole, "MusicalContext.track_roles", nonempty=False, distinct=False)
        assigned = [role.track_id for role in self.track_roles]
        if len(set(assigned)) != len(assigned):
            raise ValueError("MusicalContext.track_roles assigns more than one role to a track")
        require_tuple_of(
            self.preferences, ContextPreference, "MusicalContext.preferences", nonempty=False, distinct=False
        )
        if not isinstance(self.protected_track_ids, frozenset):
            raise ValueError("MusicalContext.protected_track_ids must be a frozenset")
        for track_id in self.protected_track_ids:
            require_instance(track_id, TrackId, "MusicalContext.protected_track_ids element")

    def role_for(self, track_id: TrackId) -> str | None:
        """Return the declared role, or ``None`` when no role was supplied (unknown)."""
        for assignment in self.track_roles:
            if assignment.track_id == track_id:
                return assignment.role
        return None

    def validate_against(self, snapshot: ProjectSnapshot) -> None:
        snapshot.require_known_tracks(tuple(r.track_id for r in self.track_roles), "MusicalContext.track_roles")
        for preference in self.preferences:
            snapshot.require_known_tracks(preference.track_ids, "MusicalContext.preferences")
        snapshot.require_known_tracks(self.protected_track_ids, "MusicalContext.protected_track_ids")
