"""DAW parameter coordinates; no fixed-input audio processing or execution authority."""

from dataclasses import dataclass

from autonomous_mixing.core.records.actions import Action
from autonomous_mixing.core.records.identifiers import ProjectId, Revision, TrackId, VersionRef
from autonomous_mixing.core.records.invariants import (
    require_finite,
    require_finite_result,
    require_instance,
    require_text,
)


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


@dataclass(frozen=True)
class DawTrackState:
    """Selected parameter state; confirmed readings may differ from ideal targets."""

    binding: DawTrackBinding
    revision: Revision
    selected_gain_db: float
    confirmed_fader: DawFaderReading

    def __post_init__(self) -> None:
        require_instance(self.binding, DawTrackBinding, "DawTrackState.binding")
        require_instance(self.revision, Revision, "DawTrackState.revision")
        require_finite(self.selected_gain_db, "DawTrackState.selected_gain_db")
        require_instance(self.confirmed_fader, DawFaderReading, "DawTrackState.confirmed_fader")


@dataclass(frozen=True)
class DawGainResolution:
    """Checked absolute parameter mapping; no permission, dispatch or candidate-ready claim."""

    action: Action
    state: DawTrackState
    observed_fader: DawFaderReading
    target_fader_db: float
    delta_db: float

    def __post_init__(self) -> None:
        require_instance(self.action, Action, "DawGainResolution.action")
        require_instance(self.state, DawTrackState, "DawGainResolution.state")
        require_instance(self.observed_fader, DawFaderReading, "DawGainResolution.observed_fader")
        binding = self.state.binding
        if self.action.project_id != binding.project_id:
            raise ValueError("DawGainResolution action project differs from binding")
        if self.action.operation.track_id != binding.track_id:
            raise ValueError("DawGainResolution action track differs from binding")
        if self.action.expected_revision != self.state.revision:
            raise ValueError("DawGainResolution action revision differs from state")
        require_finite(self.target_fader_db, "DawGainResolution.target_fader_db")
        require_finite(self.delta_db, "DawGainResolution.delta_db")
        # Large integer results can overflow while the shared helper checks finiteness.
        try:
            target = require_finite_result(
                lambda: binding.baseline_fader.db + (self.action.operation.value_db - binding.baseline_gain_db),
                "DawGainResolution mapped target",
            )
            delta = require_finite_result(lambda: target - self.observed_fader.db, "DawGainResolution mapped delta")
        except OverflowError:
            raise ValueError("DAW mapped coordinates must be finite and representable as floats") from None
        if self.target_fader_db != target:
            raise ValueError("DawGainResolution.target_fader_db differs from baseline mapping")
        if self.delta_db != delta:
            raise ValueError("DawGainResolution.delta_db differs from observed-to-target mapping")
