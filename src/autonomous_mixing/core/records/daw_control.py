"""DAW inspect/apply contract records and the calibrated capability profile (plan 2026-11-18-daw-control-contract).

These records describe one fader change on one owner-confirmed DAW insert. They are not offline-rendering
records: no outcome carries a candidate revision, artifacts or an ``ExecutionStatus``, and a confirmed write
confirms only the reported parameter reading, never routing, plugins, project state or audio. The calibration
curve is not here: the profile declares the tested ranges in both coordinates, and the adapter that owns the
conversion derives them at full precision and checks the normalized target before dispatch.
"""

import re
from dataclasses import dataclass
from enum import Enum

from autonomous_mixing.core.records.admission import AdmittedAction
from autonomous_mixing.core.records.daw import DawGainResolution, DawTrackState
from autonomous_mixing.core.records.daw_binding import DawFaderReading, DawTrackBinding
from autonomous_mixing.core.records.identifiers import ExecutionId, VersionRef
from autonomous_mixing.core.records.invariants import require_finite, require_instance, require_text

_COMMAND_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")


def _require_index(value: object, field: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field} must be a built-in int of at least 1")
    return value


def _require_tolerance(value: object, field: str) -> float:
    if require_finite(value, field) < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


def _require_command_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not _COMMAND_ID.fullmatch(value):
        raise ValueError(f"{field} must be 8-64 characters of A-Z, a-z, 0-9, '_' or '-'")
    return value


def _require_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a bool, got {value!r}")
    return value


def _optional_reading(value: object, field: str) -> None:
    if value is not None:
        require_instance(value, DawFaderReading, field)


# --- calibrated profile ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DawBuild:
    """Exact DAW product version text and scripting API number, as reported or as tested."""

    product_version: str
    scripting_api: int

    def __post_init__(self) -> None:
        require_text(self.product_version, "DawBuild.product_version")
        _require_index(self.scripting_api, "DawBuild.scripting_api")


@dataclass(frozen=True)
class DawRange:
    """Inclusive range with distinct finite endpoints; values are kept exactly as supplied (no rounding)."""

    minimum: float
    maximum: float

    def __post_init__(self) -> None:
        low = require_finite(self.minimum, "DawRange.minimum")
        high = require_finite(self.maximum, "DawRange.maximum")
        if not low < high:
            raise ValueError(f"DawRange.minimum {low} must be below maximum {high}")

    def contains(self, value: float) -> bool:
        return self.minimum <= value <= self.maximum


@dataclass(frozen=True)
class DawControlProfile:
    """Declared tested scope of one DAW write path: build, project title, insert, conversion, ranges, limits.

    Configuration supplied by the adapter; core holds no defaults. ``fader_db_range`` is the image of
    ``normalized_range`` under ``conversion`` as derived by the adapter at full precision (core cannot check it).
    The profile is a declaration, not proof that the open project, routing or audio match the calibration.
    """

    reference: VersionRef
    build: DawBuild
    project_title: str
    insert_index: int
    insert_name: str
    conversion: VersionRef
    normalized_range: DawRange
    fader_db_range: DawRange
    minimum_cut_db: float
    maximum_cut_db: float
    start_normalized_tolerance: float
    start_db_tolerance: float
    success_db_tolerance: float
    readback_normalized_tolerance: float

    def __post_init__(self) -> None:
        require_instance(self.reference, VersionRef, "DawControlProfile.reference")
        require_instance(self.build, DawBuild, "DawControlProfile.build")
        require_text(self.project_title, "DawControlProfile.project_title")
        _require_index(self.insert_index, "DawControlProfile.insert_index")
        require_text(self.insert_name, "DawControlProfile.insert_name")
        require_instance(self.conversion, VersionRef, "DawControlProfile.conversion")
        require_instance(self.normalized_range, DawRange, "DawControlProfile.normalized_range")
        require_instance(self.fader_db_range, DawRange, "DawControlProfile.fader_db_range")
        if self.normalized_range.minimum < 0 or self.normalized_range.maximum > 1:
            raise ValueError("DawControlProfile.normalized_range must lie within [0, 1]")
        low = require_finite(self.minimum_cut_db, "DawControlProfile.minimum_cut_db")
        high = require_finite(self.maximum_cut_db, "DawControlProfile.maximum_cut_db")
        if not 0 < low <= high:
            raise ValueError("DawControlProfile cut limits must satisfy 0 < minimum_cut_db <= maximum_cut_db")
        for name in ("start_normalized_tolerance", "start_db_tolerance", "success_db_tolerance",
                     "readback_normalized_tolerance"):  # fmt: skip
            _require_tolerance(getattr(self, name), f"DawControlProfile.{name}")

    def covers(self, binding: DawTrackBinding) -> bool:
        """True if the binding's declared project title and insert are the tested ones."""
        return (binding.project_title, binding.insert_index, binding.insert_name) == (
            self.project_title,
            self.insert_index,
            self.insert_name,
        )


# --- inspection -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DawInspectRequest:
    """Read the bound insert's identity, build and fader; the binding must lie inside the profile's scope."""

    binding: DawTrackBinding
    profile: DawControlProfile

    def __post_init__(self) -> None:
        require_instance(self.binding, DawTrackBinding, "DawInspectRequest.binding")
        require_instance(self.profile, DawControlProfile, "DawInspectRequest.profile")
        if not self.profile.covers(self.binding):
            raise ValueError("DawInspectRequest binding lies outside the profile's project title and insert")


@dataclass(frozen=True)
class DawInspection:
    """Observed values as the DAW reported them; a mismatching identity is represented, never accepted.

    ``insert_name`` is ``None`` when the index names no insert. ``fader`` is present exactly when the observed
    project title and insert equal the request's binding (no gain is reported for another target).
    """

    request: DawInspectRequest
    build: DawBuild
    project_title: str
    insert_index: int
    insert_name: str | None
    fader: DawFaderReading | None

    def __post_init__(self) -> None:
        require_instance(self.request, DawInspectRequest, "DawInspection.request")
        require_instance(self.build, DawBuild, "DawInspection.build")
        if not isinstance(self.project_title, str):  # an unsaved project may report an empty title
            raise ValueError("DawInspection.project_title must be a str")
        _require_index(self.insert_index, "DawInspection.insert_index")
        if self.insert_name is not None and not isinstance(self.insert_name, str):
            raise ValueError("DawInspection.insert_name must be a str or None")
        _optional_reading(self.fader, "DawInspection.fader")
        if (self.fader is not None) != self.matches_binding:
            raise ValueError("DawInspection.fader must be present exactly when the identity matches the binding")

    @property
    def matches_binding(self) -> bool:
        binding = self.request.binding
        return (self.project_title, self.insert_index, self.insert_name) == (
            binding.project_title,
            binding.insert_index,
            binding.insert_name,
        )


# --- gate results -----------------------------------------------------------------------------------------------------


class DawGateCode(Enum):
    UNRESOLVED_OPERATION = "unresolved_operation"
    CONVERSION_BLOCKED = "conversion_blocked"
    PROFILE_SCOPE = "profile_scope"
    IDENTITY_MISMATCH = "identity_mismatch"
    UNSUPPORTED_BUILD = "unsupported_build"
    STALE_REVISION = "stale_revision"
    DRIFT = "drift"
    GAIN_INCREASE_UNSUPPORTED = "gain_increase_unsupported"
    CUT_OUT_OF_BOUNDS = "cut_out_of_bounds"
    START_OUT_OF_RANGE = "start_out_of_range"
    TARGET_OUT_OF_RANGE = "target_out_of_range"


@dataclass(frozen=True)
class DawApplyRejected:
    """The gate refused before dispatch; nothing was sent."""

    code: DawGateCode
    explanation: str

    def __post_init__(self) -> None:
        require_instance(self.code, DawGateCode, "DawApplyRejected.code")
        require_text(self.explanation, "DawApplyRejected.explanation")


def daw_state_rejection(
    admitted: AdmittedAction, state: DawTrackState, profile: DawControlProfile, inspection: DawInspection
) -> DawApplyRejected | None:
    """The first state-dependent refusal before resolution (identity, build, references, drift), or None.

    Shared by ``DawApplyRequest`` and the gate so both decide identically. Callers check wiring (types and
    that ``inspection`` answers ``DawInspectRequest(state.binding, profile)``) and the profile scope first.
    """
    if not inspection.matches_binding:
        return DawApplyRejected(DawGateCode.IDENTITY_MISMATCH, "the inspected project title or insert differs")
    if inspection.build != profile.build:
        return DawApplyRejected(DawGateCode.UNSUPPORTED_BUILD, f"build {inspection.build} is not the tested build")
    action, binding = admitted.action, state.binding
    if (action.project_id, action.operation.track_id, action.expected_revision) != (
        binding.project_id,
        binding.track_id,
        state.revision,
    ):
        return DawApplyRejected(DawGateCode.STALE_REVISION, "the action's project, track or revision is not current")
    observed, confirmed = inspection.fader, state.confirmed_fader
    if (
        abs(observed.normalized - confirmed.normalized) > profile.start_normalized_tolerance
        or abs(observed.db - confirmed.db) > profile.start_db_tolerance
    ):
        return DawApplyRejected(DawGateCode.DRIFT, "the observed fader differs from the confirmed state")
    return None


def daw_cut_rejection(resolution: DawGainResolution, profile: DawControlProfile) -> DawApplyRejected | None:
    """The first refusal of the resolved change (direction, cut limits, start and target ranges), or None.

    A delta of exactly 0.0 is not a cut: the gate returns ``DawNoWrite`` before calling this, and a request
    with it is refused here. Comparisons are exact and inclusive; no rounding policy is applied in core.
    """
    return daw_change_rejection(resolution.delta_db, resolution.target_fader_db, resolution.observed_fader, profile)


def daw_change_rejection(
    delta: float, target_fader_db: float, start: DawFaderReading, profile: DawControlProfile
) -> DawApplyRejected | None:
    """The single rule behind ``daw_cut_rejection`` and the advertised ``daw_gain_bounds`` endpoints."""
    if delta > 0:
        return DawApplyRejected(DawGateCode.GAIN_INCREASE_UNSUPPORTED, "gain increases are not supported")
    if not profile.minimum_cut_db <= -delta <= profile.maximum_cut_db:
        return DawApplyRejected(
            DawGateCode.CUT_OUT_OF_BOUNDS,
            f"a cut of {-delta} dB lies outside [{profile.minimum_cut_db}, {profile.maximum_cut_db}] dB",
        )
    if not (profile.normalized_range.contains(start.normalized) and profile.fader_db_range.contains(start.db)):
        return DawApplyRejected(DawGateCode.START_OUT_OF_RANGE, "the starting fader lies outside the tested range")
    if not profile.fader_db_range.contains(target_fader_db):
        return DawApplyRejected(DawGateCode.TARGET_OUT_OF_RANGE, "the target fader dB lies outside the tested range")
    return None


@dataclass(frozen=True)
class DawApplyRequest:
    """One fader change permitted inside the calibrated scope; validated independently of the gate.

    Every invariant that does not depend on run state is checked here: references, the inspection and profile
    it was permitted against, drift, the permitted cut and both ranges. Whether an earlier operation is still
    unresolved and whether the conversion is blocked depend on controller state and are the gate's and the
    controller's responsibility. ``command_id`` is stable: a retry must reuse it with an identical request.
    """

    execution_id: ExecutionId
    command_id: str
    admitted: AdmittedAction
    resolution: DawGainResolution
    profile: DawControlProfile
    inspection: DawInspection
    requested_change_db: float

    def __post_init__(self) -> None:
        require_instance(self.execution_id, ExecutionId, "DawApplyRequest.execution_id")
        _require_command_id(self.command_id, "DawApplyRequest.command_id")
        require_instance(self.admitted, AdmittedAction, "DawApplyRequest.admitted")
        require_instance(self.resolution, DawGainResolution, "DawApplyRequest.resolution")
        require_instance(self.profile, DawControlProfile, "DawApplyRequest.profile")
        require_instance(self.inspection, DawInspection, "DawApplyRequest.inspection")
        require_finite(self.requested_change_db, "DawApplyRequest.requested_change_db")
        state = self.resolution.state
        if self.admitted.action != self.resolution.action:
            raise ValueError("DawApplyRequest admitted action differs from the resolution's action")
        if not self.profile.covers(state.binding):
            raise ValueError("DawApplyRequest binding lies outside the profile's project title and insert")
        if self.inspection.request != DawInspectRequest(state.binding, self.profile):
            raise ValueError("DawApplyRequest inspection answers another binding or profile")
        rejection = daw_state_rejection(self.admitted, state, self.profile, self.inspection)
        if rejection is None and self.inspection.fader != self.resolution.observed_fader:
            raise ValueError("DawApplyRequest inspection fader differs from the resolution's observation")
        if rejection is None:
            rejection = daw_cut_rejection(self.resolution, self.profile)
        if rejection is not None:
            raise ValueError(f"DawApplyRequest is not permitted: {rejection.code.name}: {rejection.explanation}")
        if self.requested_change_db != self.resolution.delta_db:
            raise ValueError("DawApplyRequest.requested_change_db differs from the resolution's delta")


@dataclass(frozen=True)
class DawApplyPermitted:
    request: DawApplyRequest

    def __post_init__(self) -> None:
        require_instance(self.request, DawApplyRequest, "DawApplyPermitted.request")


@dataclass(frozen=True)
class DawNoWrite:
    """The resolved change is exactly 0.0 dB: nothing is dispatched (not a refusal)."""

    resolution: DawGainResolution

    def __post_init__(self) -> None:
        require_instance(self.resolution, DawGainResolution, "DawNoWrite.resolution")
        if self.resolution.delta_db != 0:
            raise ValueError("DawNoWrite requires a resolved delta of exactly 0 dB")


DawGateResult = DawApplyPermitted | DawNoWrite | DawApplyRejected


# --- apply outcomes -----------------------------------------------------------------------------------------------------


class DawRefusalCode(Enum):
    """Pre-write refusals reported by the adapter (mirroring the reviewed bridge); nothing was written."""

    IDENTITY_MISMATCH = "identity_mismatch"
    TRANSPORT_PLAYING = "transport_playing"
    EDIT_UNAVAILABLE = "edit_unavailable"
    START_GAIN = "start_gain"
    OUT_OF_RANGE = "out_of_range"
    UNCALIBRATED_BUILD = "uncalibrated_build"
    UNCALIBRATED_START = "uncalibrated_start"
    CONVERSION = "conversion"
    CONVERSION_MISMATCH = "conversion_mismatch"
    BOUNDS = "bounds"
    DUPLICATE_ID_CONFLICT = "duplicate_id_conflict"
    API_TOO_OLD = "api_too_old"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"  # failed before the pending intent was recorded; afterwards it is uncertain


class DawUncertainReason(Enum):
    TIMEOUT = "timeout"
    INVALID_REPLY = "invalid_reply"
    READBACK_DISAGREES = "readback_disagrees"
    EXCEPTION_AFTER_WRITE = "exception_after_write"
    DISPATCH_INTERRUPTED = "dispatch_interrupted"
    PENDING_UNRESOLVED = "pending_unresolved"


@dataclass(frozen=True)
class DawWriteRefused:
    """Refused before writing; ``observed`` is the reading the adapter reported, if any."""

    command_id: str
    code: DawRefusalCode
    explanation: str
    observed: DawFaderReading | None

    def __post_init__(self) -> None:
        _require_command_id(self.command_id, "DawWriteRefused.command_id")
        require_instance(self.code, DawRefusalCode, "DawWriteRefused.code")
        require_text(self.explanation, "DawWriteRefused.explanation")
        _optional_reading(self.observed, "DawWriteRefused.observed")


def _check_written(record: "DawWriteConfirmed | DawWriteOffTarget", name: str) -> None:
    _require_command_id(record.command_id, f"{name}.command_id")
    require_instance(record.before, DawFaderReading, f"{name}.before")
    require_instance(record.after, DawFaderReading, f"{name}.after")
    commanded = require_finite(record.commanded_normalized, f"{name}.commanded_normalized")
    if not 0 <= commanded <= 1:
        raise ValueError(f"{name}.commanded_normalized must lie within [0, 1]")
    require_finite(record.achieved_change_db, f"{name}.achieved_change_db")
    if record.achieved_change_db != record.after.db - record.before.db:
        raise ValueError(f"{name}.achieved_change_db must equal after.db - before.db")
    _require_bool(record.replayed, f"{name}.replayed")


@dataclass(frozen=True)
class DawWriteConfirmed:
    """The fader reads the commanded position and the dB change is within the success tolerance.

    Confirms only the parameter reading. ``commanded_normalized`` is an adapter claim: core cannot check that
    it is the position producing the requested dB change. ``replayed`` marks a recorded written result returned
    again for an identical retry; refused and uncertain outcomes are replayed unchanged and carry no such flag.
    """

    command_id: str
    before: DawFaderReading
    after: DawFaderReading
    commanded_normalized: float
    achieved_change_db: float
    replayed: bool

    def __post_init__(self) -> None:
        _check_written(self, "DawWriteConfirmed")


@dataclass(frozen=True)
class DawWriteOffTarget:
    """A known write: the fader reads the commanded position but the dB change missed the request."""

    command_id: str
    before: DawFaderReading
    after: DawFaderReading
    commanded_normalized: float
    achieved_change_db: float
    replayed: bool

    def __post_init__(self) -> None:
        _check_written(self, "DawWriteOffTarget")


@dataclass(frozen=True)
class DawWriteUncertain:
    """The effect is not established; it stays unresolved until the controller reconciles it."""

    command_id: str
    reason: DawUncertainReason
    before: DawFaderReading | None
    after: DawFaderReading | None
    explanation: str

    def __post_init__(self) -> None:
        _require_command_id(self.command_id, "DawWriteUncertain.command_id")
        require_instance(self.reason, DawUncertainReason, "DawWriteUncertain.reason")
        _optional_reading(self.before, "DawWriteUncertain.before")
        _optional_reading(self.after, "DawWriteUncertain.after")
        require_text(self.explanation, "DawWriteUncertain.explanation")


DawApplyOutcome = DawWriteRefused | DawWriteConfirmed | DawWriteOffTarget | DawWriteUncertain
