"""Controlled runtime-adapter requests and MIDI byte transport; never opens devices."""

from autonomous_mixing.adapters.fl_studio.profile import calibrated_profile
from autonomous_mixing.core.admission.daw import gate_daw_apply
from autonomous_mixing.core.records.actions import Action, SetTrackGain
from autonomous_mixing.core.records.admission import AdmittedAction
from autonomous_mixing.core.records.daw import DawTrackState
from autonomous_mixing.core.records.daw_binding import DawFaderReading, DawTrackBinding
from autonomous_mixing.core.records.daw_control import DawInspection, DawInspectRequest
from autonomous_mixing.core.records.identifiers import ActionId, ExecutionId, ProjectId, Revision, TrackId, VersionRef


def make_request(command_id="runtime_command_0001", change=-1.0):
    profile = calibrated_profile(reference=VersionRef("fl-control-profile", "1"))
    binding = DawTrackBinding(
        VersionRef("binding", "1"),
        ProjectId("p1"),
        TrackId("t1"),
        profile.project_title,
        2,
        "Bass",
        2.0,
        DawFaderReading(0.76, -1.1527416706085205),
    )
    state = DawTrackState(binding, Revision("r1"), 2.0, binding.baseline_fader)
    inspection = DawInspection(
        DawInspectRequest(binding, profile), profile.build, binding.project_title, 2, "Bass", binding.baseline_fader
    )
    admitted = AdmittedAction(
        Action(ActionId("a1"), binding.project_id, state.revision, SetTrackGain(binding.track_id, 2.0 + change)),
        VersionRef("policy", "1"),
    )
    return gate_daw_apply(
        admitted,
        state=state,
        inspection=inspection,
        profile=profile,
        execution_id=ExecutionId("e1"),
        command_id=command_id,
        blocked_conversions=frozenset(),
        unresolved_command_id=None,
    ).request
