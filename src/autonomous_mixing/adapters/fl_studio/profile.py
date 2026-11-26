"""The tested FL builds/inserts and their versioned conversions; no broader capability is inferred.

Exactly two calibrated profiles exist, each declared literally here and selected by name (plan 2026-11-26). There is
no configurable profile and no registry: a profile is supported only if it equals one of these two declarations.
"""

from dataclasses import replace

from autonomous_mixing.adapters.fl_studio import protocol
from autonomous_mixing.core.records.daw_control import DawApplyRequest, DawBuild, DawControlProfile, DawRange
from autonomous_mixing.core.records.identifiers import VersionRef
from autonomous_mixing.core.records.invariants import require_instance

EXISTING_PROFILE = "bass-bridge-test-v1"
HT01_PROFILE = "ht01-live-calibration-v001"
PROFILE_NAMES = (EXISTING_PROFILE, HT01_PROFILE)


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


def ht01_calibrated_profile(*, reference: VersionRef) -> DawControlProfile:
    """The HT01 scope accepted by run ht01_live_20261126_01: exact bounds; the staging points 0.76/0.94 grant nothing.

    Cut limits and tolerances equal the existing profile's; only the scope, conversion and ranges differ.
    """
    low, high = protocol.HT01_CONVERSION_RANGE
    return DawControlProfile(
        reference,
        DawBuild(protocol.HT01_BUILD, protocol.HT01_API),
        protocol.HT01_TITLE,
        protocol.HT01_INSERT["index"],
        protocol.HT01_INSERT["name"],
        VersionRef("fl-mixer-h125-ht01", "v1"),
        DawRange(low, high),
        DawRange(protocol.h_db(low), protocol.h_db(high)),
        0.1,
        3.0,
        0.001,
        0.05,
        0.05,
        0.001,
    )


def supported_profile(name: str, *, reference: VersionRef) -> DawControlProfile:
    """The calibrated profile with exactly this name; anything else is refused."""
    if name == EXISTING_PROFILE:
        return calibrated_profile(reference=reference)
    if name == HT01_PROFILE:
        return ht01_calibrated_profile(reference=reference)
    raise ValueError(f"unsupported DAW profile {name!r}; supported: {', '.join(PROFILE_NAMES)}")


def is_supported_profile(profile: DawControlProfile) -> bool:
    """True only for an unaltered declaration of one of the two profiles (its reference is a label only)."""
    require_instance(profile, DawControlProfile, "profile")
    return profile in (calibrated_profile(reference=profile.reference), ht01_calibrated_profile(
        reference=profile.reference))  # fmt: skip


def same_calibrated_scope(a: DawControlProfile, b: DawControlProfile) -> bool:
    """Whether two profiles declare the same scope, conversion, ranges, limits and tolerances (labels aside)."""
    require_instance(a, DawControlProfile, "profile")
    require_instance(b, DawControlProfile, "profile")
    return replace(a, reference=b.reference) == b


def make_apply_command(request: DawApplyRequest) -> dict:
    """Recompute the actual wire target; caller profile declarations never widen runtime support."""
    require_instance(request, DawApplyRequest, "request")
    if not is_supported_profile(request.profile):
        raise ValueError("request profile is not a supported calibrated profile")
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
