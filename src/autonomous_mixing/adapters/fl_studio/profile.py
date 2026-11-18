"""The tested FL build/insert and its versioned conversion; no broader capability is inferred."""

from autonomous_mixing.adapters.fl_studio import protocol
from autonomous_mixing.core.records.daw_control import DawApplyRequest, DawBuild, DawControlProfile, DawRange
from autonomous_mixing.core.records.identifiers import VersionRef
from autonomous_mixing.core.records.invariants import require_instance


def calibrated_profile(*, reference: VersionRef) -> DawControlProfile:
    """Declare only the scope accepted by the recorded calibration, with full-precision endpoints."""
    return DawControlProfile(
        reference,
        DawBuild("Producer Edition v26.1.7 [build 5654]", 45),
        "Thesis Bass Bridge Test",
        2,
        "Bass",
        VersionRef("fl-mixer-h125", "v1"),
        DawRange(0.62, 0.86),
        DawRange(protocol.h_db(0.62), protocol.h_db(0.86)),
        0.1,
        3.0,
        0.001,
        0.05,
        0.05,
        0.001,
    )


def make_apply_command(request: DawApplyRequest) -> dict:
    """Recompute the actual wire target; caller profile declarations never widen runtime support."""
    require_instance(request, DawApplyRequest, "request")
    if request.profile != calibrated_profile(reference=request.profile.reference):
        raise ValueError("request profile is not the supported calibrated profile")
    expected = request.resolution.observed_fader
    return protocol.set_gain_db_command(
        request.command_id,
        request.profile.project_title,
        request.profile.insert_index,
        request.profile.insert_name,
        expected.normalized,
        expected.db,
        request.requested_change_db,
    )
