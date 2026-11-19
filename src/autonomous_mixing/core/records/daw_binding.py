"""DAW fader readings and track bindings: the dependency-free part of the DAW coordinates.

Moved from ``core.records.daw`` (coordinator-integration plan, Increment 1; ADR-035) so that ``ProjectState`` can carry
explicit DAW parameter state (ADR-034, Option A) without importing actions. Contents are unchanged.
"""

from dataclasses import dataclass

from autonomous_mixing.core.records.identifiers import ProjectId, TrackId, VersionRef
from autonomous_mixing.core.records.invariants import require_finite, require_instance, require_text


@dataclass(frozen=True)
class DawFaderReading:
    """Reported parameter values, not evidence of exported audio or calibrated scope."""

    normalized: float
    db: float

    def __post_init__(self) -> None:
        require_finite(self.normalized, "DawFaderReading.normalized")
        require_finite(self.db, "DawFaderReading.db")
        if not 0 <= self.normalized <= 1:
            raise ValueError("DawFaderReading.normalized must be between 0 and 1 inclusive")


@dataclass(frozen=True)
class DawTrackBinding:
    """Versioned coordinate baseline and declared insert identity; no open-file authentication."""

    reference: VersionRef
    project_id: ProjectId
    track_id: TrackId
    project_title: str
    insert_index: int
    insert_name: str
    baseline_gain_db: float
    baseline_fader: DawFaderReading

    def __post_init__(self) -> None:
        require_instance(self.reference, VersionRef, "DawTrackBinding.reference")
        require_instance(self.project_id, ProjectId, "DawTrackBinding.project_id")
        require_instance(self.track_id, TrackId, "DawTrackBinding.track_id")
        require_text(self.project_title, "DawTrackBinding.project_title")
        require_text(self.insert_name, "DawTrackBinding.insert_name")
        if type(self.insert_index) is not int or self.insert_index < 1:
            raise ValueError("DawTrackBinding.insert_index must be a built-in int of at least 1")
        require_finite(self.baseline_gain_db, "DawTrackBinding.baseline_gain_db")
        require_instance(self.baseline_fader, DawFaderReading, "DawTrackBinding.baseline_fader")
