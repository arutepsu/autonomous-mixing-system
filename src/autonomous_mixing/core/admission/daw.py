"""DAW gain coordinates and the calibrated capability gate; pure, no dispatch (plan 2026-11-18-daw-control-contract).

``resolve_daw_gain`` maps coordinates only. ``daw_gain_bounds`` derives the advertised range from the confirmed
state; ``gate_daw_apply`` decides, before dispatch, whether one admitted action may be written inside the tested
profile. Neither grants audio acceptance or touches the DAW.
"""

import math
import struct

from autonomous_mixing.core.records.actions import Action
from autonomous_mixing.core.records.admission import AdmittedAction, TrackGainPermission
from autonomous_mixing.core.records.capabilities import GainBounds
from autonomous_mixing.core.records.daw import DawGainResolution, DawTrackState
from autonomous_mixing.core.records.daw_binding import DawFaderReading, DawTrackBinding
from autonomous_mixing.core.records.daw_control import (
    DawApplyPermitted,
    DawApplyRejected,
    DawApplyRequest,
    DawControlProfile,
    DawGateCode,
    DawGateResult,
    DawInspection,
    DawInspectRequest,
    DawNoWrite,
    daw_change_rejection,
    daw_cut_rejection,
    daw_state_rejection,
)
from autonomous_mixing.core.records.daw_recovery import (
    DawControlState,
    DawReconciliation,
    DawRecoveryAuthorization,
    DawRecoveryDecision,
    DawRecoveryEntry,
    DawRecoveryPlan,
    DawRestoreGateCode,
    DawRestoreGateResult,
    DawRestoreNoWrite,
    DawRestorePermitted,
    DawRestoreRejected,
    DawRestoreRequest,
    daw_recovery_plan_for,
)
from autonomous_mixing.core.records.identifiers import ExecutionId, VersionRef
from autonomous_mixing.core.records.invariants import require_finite, require_finite_result, require_instance


def resolve_daw_gain(
    action: Action,
    *,
    state: DawTrackState,
    observed_fader: DawFaderReading,
    normalized_tolerance: float,
    db_tolerance: float,
) -> DawGainResolution:
    """Map an absolute action from the binding's baseline after checking references and drift.

    Tolerances are caller-supplied, inclusive and non-negative. The returned delta may be zero
    or positive; admission and the adapter's runtime capabilities still decide whether to execute.
    No calibration, permission decision or audio-processing claim is implied.
    """
    require_instance(action, Action, "action")
    require_instance(state, DawTrackState, "state")
    require_instance(observed_fader, DawFaderReading, "observed_fader")
    require_finite(normalized_tolerance, "normalized_tolerance")
    require_finite(db_tolerance, "db_tolerance")
    if normalized_tolerance < 0 or db_tolerance < 0:
        raise ValueError("DAW reading tolerances must be non-negative")
    binding = state.binding
    if action.project_id != binding.project_id:
        raise ValueError("action project differs from DAW binding")
    if action.operation.track_id != binding.track_id:
        raise ValueError("action track differs from DAW binding")
    if action.expected_revision != state.revision:
        raise ValueError("action revision differs from DAW state")
    # Preserve exact integer arithmetic while containing its float-representability check.
    try:
        normalized_difference = require_finite_result(
            lambda: observed_fader.normalized - state.confirmed_fader.normalized, "observed normalized difference"
        )
        db_difference = require_finite_result(
            lambda: observed_fader.db - state.confirmed_fader.db, "observed dB difference"
        )
        if abs(normalized_difference) > normalized_tolerance or abs(db_difference) > db_tolerance:
            raise ValueError("observed fader differs from confirmed DAW state beyond tolerance")
        target = require_finite_result(lambda: _mapped_target(binding, action.operation.value_db), "DAW mapped target")
        delta = require_finite_result(lambda: target - observed_fader.db, "DAW mapped delta")
        return DawGainResolution(action, state, observed_fader, target, delta)
    except OverflowError:
        raise ValueError("DAW derived readings and coordinates must be finite and representable as floats") from None


_SIGN = 1 << 63


def _mapped_target(binding, value_db: float) -> float:
    """The baseline mapping, written once: ``DawGainResolution`` re-derives exactly this expression."""
    return binding.baseline_fader.db + (value_db - binding.baseline_gain_db)


def _endpoint_permitted(binding, confirmed: DawFaderReading, value_db: float, profile: DawControlProfile) -> bool:
    """Whether the gate's change rule permits ``value_db`` from the confirmed fader, with the gate's arithmetic."""
    target = _mapped_target(binding, value_db)
    if not math.isfinite(target):
        return False
    delta = target - confirmed.db
    return math.isfinite(delta) and daw_change_rejection(delta, target, confirmed, profile) is None


def _order_key(value: float) -> int:
    """An integer with the same order as the finite float ``value`` (adjacent floats differ by one)."""
    bits = struct.unpack("<Q", struct.pack("<d", value))[0]
    return -(bits ^ _SIGN) if bits & _SIGN else bits


def _from_order_key(key: int) -> float:
    bits = (-key) | _SIGN if key < 0 else key
    return struct.unpack("<d", struct.pack("<Q", bits))[0]


def _boundary(binding, confirmed, permitted: float, refused: float, profile: DawControlProfile) -> float:
    """The permitted float nearest ``refused``, between a permitted and a refused value (bisection on float order).

    The gate's predicate is monotone along the gain axis (each float operation of the mapping is monotone), so
    the permitted values form one contiguous run; at most 64 halvings find its edge exactly, wherever it lies,
    including across zero and through subnormal floats.
    """
    inside, outside = _order_key(permitted), _order_key(refused)
    while abs(outside - inside) > 1:
        middle = (inside + outside) // 2
        if _endpoint_permitted(binding, confirmed, _from_order_key(middle), profile):
            inside = middle
        else:
            outside = middle
    return _from_order_key(inside)


def daw_gain_bounds(
    *, profile: DawControlProfile, state: DawTrackState, permission: TrackGainPermission
) -> GainBounds | None:
    """Advertised absolute application-gain range for the bound track, from the confirmed state.

    Validates references first: a permission for another track, or a profile whose project title and insert
    are not the binding's, raises ``ValueError`` (no capability for the wrong insert). With ``c`` the
    application gain equivalent to the confirmed fader, the result is the exact intersection of the policy
    permission, the permitted cuts ``[c - maximum_cut, c - minimum_cut]`` and the absolute window whose targets
    stay inside ``profile.fader_db_range``; ``None`` if it is empty or the confirmed start lies outside either
    profile range. No clamping or rounding. A view, not permission: ``gate_daw_apply`` still decides.

    Endpoints are then made consistent with the gate: each is checked with the gate's own arithmetic and change
    rule (``daw_change_rejection``). Where float rounding makes the gate refuse it (for example an upper endpoint
    of -0.1 resolving to a 0.09999999999999964 dB cut, or a naive endpoint of 0.0 whose permitted neighbour lies
    across the subnormal range), the nearest permitted float inside the window is found by bisection from a
    permitted anchor: the window's midpoint, else whichever endpoint is permitted. Endpoints only move inward,
    so the cut range is never widened, and each moved endpoint is tight (the next float outward is refused). If
    neither endpoint nor the midpoint is permitted, ``None`` is returned: conservative, and only possible when
    the whole window is within float rounding of being empty. The result is exact for an observed fader equal
    to the confirmed one; drift within tolerance can still change the gate's decision at an endpoint.
    """
    require_instance(profile, DawControlProfile, "profile")
    require_instance(state, DawTrackState, "state")
    require_instance(permission, TrackGainPermission, "permission")
    binding = state.binding
    if permission.track_id != binding.track_id:
        raise ValueError(f"permission for {permission.track_id} does not name the bound track {binding.track_id}")
    if not profile.covers(binding):
        raise ValueError("the profile's project title and insert are not the binding's")
    confirmed = state.confirmed_fader
    if not (profile.normalized_range.contains(confirmed.normalized) and profile.fader_db_range.contains(confirmed.db)):
        return None
    g0, f0 = binding.baseline_gain_db, binding.baseline_fader.db
    try:
        current = require_finite_result(lambda: g0 + (confirmed.db - f0), "DAW current application gain")
        low = max(
            permission.minimum_db,
            require_finite_result(lambda: current - profile.maximum_cut_db, "DAW lowest cut"),
            require_finite_result(lambda: g0 + (profile.fader_db_range.minimum - f0), "DAW range floor"),
        )
        high = min(
            permission.maximum_db,
            require_finite_result(lambda: current - profile.minimum_cut_db, "DAW smallest cut"),
            require_finite_result(lambda: g0 + (profile.fader_db_range.maximum - f0), "DAW range ceiling"),
        )
    except OverflowError:
        raise ValueError("DAW gain bounds must be finite and representable as floats") from None
    if low > high:
        return None
    low_ok = _endpoint_permitted(binding, confirmed, low, profile)
    high_ok = _endpoint_permitted(binding, confirmed, high, profile)
    if low_ok and high_ok:
        return GainBounds(low, high)
    middle = low / 2 + high / 2  # no overflow for extreme finite bounds
    if _endpoint_permitted(binding, confirmed, middle, profile):
        anchor = middle
    elif low_ok or high_ok:
        anchor = low if low_ok else high
    else:
        return None
    return GainBounds(
        low if low_ok else _boundary(binding, confirmed, anchor, low, profile),
        high if high_ok else _boundary(binding, confirmed, anchor, high, profile),
    )


def gate_daw_apply(
    admitted: AdmittedAction,
    *,
    state: DawTrackState,
    inspection: DawInspection,
    profile: DawControlProfile,
    execution_id: ExecutionId,
    command_id: str,
    blocked_conversions: frozenset[VersionRef],
    unresolved_command_id: str | None,
) -> DawGateResult:
    """Decide before dispatch whether an admitted action may be written inside the calibrated profile.

    ``admit_action`` must already have run; this adds runtime capability and state checks and never replaces
    it. Order (the first failing check wins): an unresolved earlier operation; a blocked conversion; the
    binding outside the profile's scope; then, after wiring checks, identity, build, stale references and
    drift (shared with ``DawApplyRequest``); then ``resolve_daw_gain`` with the profile's start tolerances;
    exactly 0.0 dB gives ``DawNoWrite``; then direction, cut limits and both ranges. Expected refusals are
    ``DawApplyRejected``; wrong types or an inspection answering another request raise ``ValueError``.
    """
    require_instance(admitted, AdmittedAction, "admitted")
    require_instance(state, DawTrackState, "state")
    require_instance(inspection, DawInspection, "inspection")
    require_instance(profile, DawControlProfile, "profile")
    require_instance(execution_id, ExecutionId, "execution_id")
    if not isinstance(blocked_conversions, frozenset) or not all(
        isinstance(item, VersionRef) for item in blocked_conversions
    ):
        raise ValueError("blocked_conversions must be a frozenset of VersionRef")
    if unresolved_command_id is not None and not isinstance(unresolved_command_id, str):
        raise ValueError("unresolved_command_id must be a str or None")

    if unresolved_command_id is not None:
        return DawApplyRejected(
            DawGateCode.UNRESOLVED_OPERATION, f"operation {unresolved_command_id} is unresolved; reconcile it first"
        )
    if profile.conversion in blocked_conversions:
        return DawApplyRejected(DawGateCode.CONVERSION_BLOCKED, f"conversion {profile.conversion} is blocked")
    if not profile.covers(state.binding):
        return DawApplyRejected(DawGateCode.PROFILE_SCOPE, "the binding lies outside the profile's tested scope")
    if inspection.request != DawInspectRequest(state.binding, profile):
        raise ValueError("the inspection answers another binding or profile")
    rejection = daw_state_rejection(admitted, state, profile, inspection)
    if rejection is not None:
        return rejection
    resolution = resolve_daw_gain(
        admitted.action,
        state=state,
        observed_fader=inspection.fader,
        normalized_tolerance=profile.start_normalized_tolerance,
        db_tolerance=profile.start_db_tolerance,
    )
    if resolution.delta_db == 0:
        return DawNoWrite(resolution)
    rejection = daw_cut_rejection(resolution, profile)
    if rejection is not None:
        return rejection
    request = DawApplyRequest(execution_id, command_id, admitted, resolution, profile, inspection, resolution.delta_db)
    return DawApplyPermitted(request)


def gate_daw_restore(
    authorization: DawRecoveryAuthorization,
    *,
    binding: DawTrackBinding,
    inspection: DawInspection,
    profile: DawControlProfile,
    recovery_command_id: str,
    control_state: DawControlState,
    own_lineage_only: bool = False,
) -> DawRestoreGateResult:
    """Decide before dispatch whether an authorized exact restoration may be sent.

    Order (the first failing check wins): the binding inside the profile's scope; wiring (the inspection answers
    this binding and profile); the target's conversion; identity; build; lineage (every unresolved command
    belongs to the authorization's root); the target inside both calibrated ranges; an exact current reading
    equal to the target gives ``DawRestoreNoWrite``. The current start may lie anywhere. The adapter's journal
    repeats the lineage and agreement checks authoritatively. Restoring a fader proves only its readings.
    """
    require_instance(authorization, DawRecoveryAuthorization, "authorization")
    require_instance(binding, DawTrackBinding, "binding")
    require_instance(inspection, DawInspection, "inspection")
    require_instance(profile, DawControlProfile, "profile")
    require_instance(control_state, DawControlState, "control_state")
    target = authorization.target
    if not profile.covers(binding):
        return DawRestoreRejected(DawRestoreGateCode.PROFILE_SCOPE, "the binding lies outside the profile's scope")
    if inspection.request != DawInspectRequest(binding, profile):
        raise ValueError("the inspection answers another binding or profile")
    if recovery_command_id in (authorization.authorization_id, target.source_command_id):
        raise ValueError("the recovery command ID must differ from the authorization and root IDs")
    if profile.conversion != target.conversion:
        return DawRestoreRejected(DawRestoreGateCode.CONVERSION_MISMATCH, "the target belongs to another conversion")
    if not inspection.matches_binding:
        return DawRestoreRejected(DawRestoreGateCode.IDENTITY_MISMATCH, "the inspected project or insert differs")
    if inspection.build != profile.build:
        return DawRestoreRejected(DawRestoreGateCode.UNSUPPORTED_BUILD, "the inspected build is not the tested one")
    foreign = [item.command_id for item in control_state.unresolved if item.root_command_id != target.source_command_id]
    if foreign:
        return DawRestoreRejected(
            DawRestoreGateCode.UNRESOLVED_OTHER, f"commands outside this lineage are unresolved: {', '.join(foreign)}"
        )
    reading = target.reading
    if not (profile.normalized_range.contains(reading.normalized) and profile.fader_db_range.contains(reading.db)):
        return DawRestoreRejected(DawRestoreGateCode.TARGET_OUT_OF_RANGE, "the target lies outside the tested range")
    request = DawRestoreRequest(recovery_command_id, authorization, binding, profile, inspection, own_lineage_only)
    if inspection.fader == reading:
        return DawRestoreNoWrite(request)
    return DawRestorePermitted(request)


def daw_recovery_decision(
    entry: DawRecoveryEntry, root_command_id: str, reconciliation: DawReconciliation | None
) -> DawRecoveryDecision:
    """Which recovery steps the evidence permits (G15, coordinator Increment 4, ADR-038).

    A known write (confirmed or off target) is restored exactly, with no reconciliation. An uncertain write is decided
    by one fresh reconciliation of its own root: at the recorded start it is resolved first (and restored only if the
    gate finds the reading inexact); at the commanded value, at neither or ambiguously it is restored and then resolved
    citing that restoration; an unverified identity permits nothing. A tolerance match never establishes rollback.
    """
    require_instance(entry, DawRecoveryEntry, "entry")
    if entry is not DawRecoveryEntry.UNCERTAIN_WRITE:
        if reconciliation is not None:
            raise ValueError("a known write is restored without a reconciliation")
        return DawRecoveryDecision(entry, root_command_id, DawRecoveryPlan.RESTORE, None, None)
    require_instance(reconciliation, DawReconciliation, "reconciliation")
    if reconciliation.subject_command_id != root_command_id:
        raise ValueError("the reconciliation answers another command than the uncertain root")
    return DawRecoveryDecision(entry, root_command_id, daw_recovery_plan_for(reconciliation.finding),
                               reconciliation.reconciliation_id, reconciliation.finding)  # fmt: skip
