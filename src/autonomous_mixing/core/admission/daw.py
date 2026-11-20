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
    DawReconcileFinding,
    DawReconciliation,
    DawRecoveryAuthorization,
    DawRecoveryDecision,
    DawRecoveryEntry,
    DawRecoveryPlan,
    DawResolutionKind,
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
from autonomous_mixing.core.records.run_restart import (
    DawLatestState,
    DawRestartFacts,
    DawStartRefused,
    OperatorOperation,
    OperatorRecoveryOption,
    RunInterrupted,
    RunInterruptionCode,
)
from autonomous_mixing.core.records.termination import TerminationReason


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


def daw_start_check(
    control_state: DawControlState,
    latest: DawLatestState | None,
    *,
    binding: DawTrackBinding,
    profile: DawControlProfile,
    confirmed: DawFaderReading,
) -> DawStartRefused | None:
    """Whether the FL journal is explained by the run's configuration before any decision (G14, ADR-039).

    Refused, adopting and restoring nothing: an unresolved command (``DAW_COMMAND_UNRESOLVED``); the session's blocked
    conversion (``DAW_CONVERSION_BLOCKED``); a latest state-establishing FL event that cannot be anchored
    (``latest`` None), or whose anchor is not exactly the configured binding and confirmed fader, both coordinates and
    signed zeros (``DAW_CONTINUITY_FAILED``). An empty journal is explained.
    """
    require_instance(control_state, DawControlState, "control_state")
    if control_state.unresolved:
        ids = tuple(item.command_id for item in control_state.unresolved)
        return DawStartRefused(TerminationReason.DAW_COMMAND_UNRESOLVED, ids,
                               "unresolved FL commands precede this run: " + ", ".join(ids))  # fmt: skip
    if profile.conversion in control_state.blocked_conversions:
        return DawStartRefused(TerminationReason.DAW_CONVERSION_BLOCKED, (),
                               "the session's fader conversion is blocked")  # fmt: skip
    if latest is None:
        return DawStartRefused(TerminationReason.DAW_CONTINUITY_FAILED, (),
                               "the latest FL event cannot be anchored: the DAW state is unexplained")  # fmt: skip
    require_instance(latest, DawLatestState, "latest")
    anchor = latest.anchor
    if anchor is None:
        return None
    exact = all(a == b and math.copysign(1.0, a) == math.copysign(1.0, b)
                for a, b in ((anchor.reading.normalized, confirmed.normalized), (anchor.reading.db, confirmed.db)))  # fmt: skip
    if anchor.binding != binding or not exact:
        return DawStartRefused(TerminationReason.DAW_CONTINUITY_FAILED, (anchor.source_id,),
                               f"the latest FL state ({anchor.source_id}) is not exactly the configured confirmed "
                               "fader: the DAW state is unexplained")  # fmt: skip
    return None


_KIND_FOR_FINDING = {
    DawReconcileFinding.AT_COMMANDED: DawResolutionKind.APPLIED_AS_COMMANDED,
    DawReconcileFinding.AT_RECORDED_START: DawResolutionKind.NOT_APPLIED_OR_REVERTED,
}
_NO_RECOVERY = (RunInterruptionCode.FINISHED, RunInterruptionCode.NOT_STARTED)


def operator_recovery_options(
    run: RunInterrupted | None,
    facts: DawRestartFacts,
    reconciliations: tuple[DawReconciliation, ...],
    accepted_executions: frozenset = frozenset(),
) -> tuple[OperatorRecoveryOption, ...]:
    """The operations an operator may authorize for an interrupted run, or for FL commands no run explains (``run``
    None), from journal facts only (ADR-040). Nothing here adopts a state or restores anything by itself.

    For every unresolved command in scope: reconcile; resolve, only where the latest evidence of its lineage supports
    a kind (the run's recorded reconciliation of it, or the root's own confirmed restoration). For every write root in
    scope whose lineage is the latest and has no unresolved restoration, and whose execution no committed acceptance
    holds (``accepted_executions``; an accepted edit is never undone, review fix 2026-11-20): one exact restoration to
    its journaled target. For orphans (``run`` None) the facts hold only roots no run records.
    For a run: the retained-audio integrity inspection and closing. A finished or unstarted run has none.
    """
    require_instance(facts, DawRestartFacts, "facts")
    if run is not None:
        require_instance(run, RunInterrupted, "run")
        if run.code in _NO_RECOVERY:
            return ()
    if facts.control_state is None or not facts.available:
        return ((OperatorRecoveryOption(OperatorOperation.CLOSE, None, None, None, "close the run with what is "
                                        "recorded; the FL journal's facts are unavailable"),) if run else ())  # fmt: skip
    lineages = facts.lineages
    members = {member: lineage for lineage in lineages for member in lineage.members}
    unresolved = [c for c in facts.control_state.unresolved if run is None or c.command_id in members]
    options = []
    for command in unresolved:
        options.append(OperatorRecoveryOption(OperatorOperation.RECONCILE, command.command_id, None, None,
                                              f"read the fader once for unresolved {command.kind.value} "
                                              f"{command.command_id}; clears nothing"))  # fmt: skip
        lineage = members.get(command.command_id)
        findings = [f for f in (lineage.findings if lineage else ()) if f.subject_command_id == command.command_id]
        latest = findings[-1] if findings else None
        evidence = next((r for r in reconciliations if latest is not None
                         and r.reconciliation_id == latest.entry_id), None)  # fmt: skip
        newer_restoration = lineage is not None and any(
            r.reserved_seq > (latest.seq if latest else 0) for r in lineage.restorations
        )
        if evidence is not None and not newer_restoration and evidence.finding in _KIND_FOR_FINDING:
            kind = _KIND_FOR_FINDING[evidence.finding]
            options.append(OperatorRecoveryOption(OperatorOperation.RESOLVE, command.command_id, kind,
                                                  evidence.reconciliation_id, f"resolve {command.command_id} as "
                                                  f"{kind.value}, citing its latest reconciliation"))  # fmt: skip
        if lineage is not None and command.command_id == lineage.root_command_id and lineage.restorations:
            last = lineage.restorations[-1]
            confirmed = type(last.outcome).__name__ == "DawRestoreConfirmed"
            if confirmed and last.reserved_seq > (latest.seq if latest else 0):
                options.append(OperatorRecoveryOption(
                    OperatorOperation.RESOLVE, command.command_id, DawResolutionKind.RESTORED_TO_RECORDED_START,
                    last.command_id, f"resolve {command.command_id}, citing its own confirmed restoration"))  # fmt: skip
    for lineage in lineages:
        open_restoration = any(r.command_id in lineage.unresolved for r in lineage.restorations)
        accepted = lineage.apply.execution_id in accepted_executions
        if lineage.own_latest() and not open_restoration and not accepted:
            options.append(OperatorRecoveryOption(OperatorOperation.RESTORE, lineage.root_command_id, None, None,
                                                  f"restore the fader exactly to the journaled start of "
                                                  f"{lineage.root_command_id}"))  # fmt: skip
    if run is not None:
        options.append(OperatorRecoveryOption(OperatorOperation.INSPECT_INTEGRITY, None, None, None,
                                              "inspect the retained audio of the authoritative state"))  # fmt: skip
        options.append(OperatorRecoveryOption(OperatorOperation.CLOSE, None, None, None,
                                              "close the run, recording facts only; nothing is restored"))  # fmt: skip
    return tuple(dict.fromkeys(options))
