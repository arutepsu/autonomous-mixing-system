"""Exact-target recovery and explicit reconciliation records (plan 2026-11-18-daw-recovery-reconciliation).

A restoration returns one fader to an exact journaled reading under a recovery authorization that is separate
from cuts-only ``AdmittedAction``s and carries a ``RECOVERY_OPERATION`` budget grant. A reconciliation records
what a fresh reading establishes about one unresolved command; only a separate, authorized resolution clears it.
Every record here concerns this fader's reported parameter readings only: none proves routing, plugins, the
complete project, saved state or audio. Conversion blocks have no clearing record in this version.
"""

import re
from dataclasses import dataclass
from enum import Enum

from autonomous_mixing.core.records.budgets import BudgetGrant, BudgetOperation
from autonomous_mixing.core.records.daw import DawFaderReading, DawTrackBinding
from autonomous_mixing.core.records.daw_control import (
    DawControlProfile,
    DawInspection,
    DawInspectRequest,
    DawRefusalCode,
    DawUncertainReason,
    DawWriteConfirmed,
    DawWriteOffTarget,
    DawWriteRefused,
    DawWriteUncertain,
)
from autonomous_mixing.core.records.identifiers import ExecutionId, VersionRef
from autonomous_mixing.core.records.invariants import require_instance, require_text

_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")


def _require_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{field} must be 8-64 characters of A-Z, a-z, 0-9, '_' or '-'")
    return value


def _require_seq(value: object, field: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field} must be a built-in int of at least 1")
    return value


def _require_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a bool")
    return value


def _optional_reading(value: object, field: str) -> None:
    if value is not None:
        require_instance(value, DawFaderReading, field)


def _in_profile_ranges(profile: DawControlProfile, reading: DawFaderReading) -> bool:
    return profile.normalized_range.contains(reading.normalized) and profile.fader_db_range.contains(reading.db)


# --- targets, authorization and the restore request ---------------------------------------------------------------


class DawCommandKind(Enum):
    APPLY = "apply"
    RESTORE = "restore"


class DawTargetBasis(Enum):
    """Which journaled reading a restoration returns to (outcome ``before`` when recorded, else the request's)."""

    OUTCOME_BEFORE = "outcome_before"
    REQUEST_INSPECTION = "request_inspection"


@dataclass(frozen=True)
class DawRestoreTarget:
    """The exact reading journaled for the root ``source_command_id``; supplied by the adapter from its journal."""

    source_command_id: str
    basis: DawTargetBasis
    reading: DawFaderReading
    conversion: VersionRef

    def __post_init__(self) -> None:
        _require_id(self.source_command_id, "DawRestoreTarget.source_command_id")
        require_instance(self.basis, DawTargetBasis, "DawRestoreTarget.basis")
        require_instance(self.reading, DawFaderReading, "DawRestoreTarget.reading")
        require_instance(self.conversion, VersionRef, "DawRestoreTarget.conversion")


@dataclass(frozen=True)
class DawRecoveryAuthorization:
    """Permission for one restoration, separate from cuts-only admission.

    ``budget_grant`` is the proposed ``RECOVERY_OPERATION`` charge the adapter persists before dispatch; its
    presence does not prove that any configured limits were enforced.
    """

    authorization_id: str
    execution_id: ExecutionId
    authorized_by: VersionRef
    target: DawRestoreTarget
    budget_grant: BudgetGrant

    def __post_init__(self) -> None:
        _require_id(self.authorization_id, "DawRecoveryAuthorization.authorization_id")
        require_instance(self.execution_id, ExecutionId, "DawRecoveryAuthorization.execution_id")
        require_instance(self.authorized_by, VersionRef, "DawRecoveryAuthorization.authorized_by")
        require_instance(self.target, DawRestoreTarget, "DawRecoveryAuthorization.target")
        require_instance(self.budget_grant, BudgetGrant, "DawRecoveryAuthorization.budget_grant")
        if self.budget_grant.operation is not BudgetOperation.RECOVERY_OPERATION:
            raise ValueError("DawRecoveryAuthorization requires a RECOVERY_OPERATION budget grant")


@dataclass(frozen=True)
class DawRestoreRequest:
    """One exact restoration of the bound insert; validated independently of the gate.

    The lineage check (every unresolved command belongs to the authorization's root) depends on journal state and
    stays with the gate and the adapter's journal, which repeats it authoritatively.
    """

    recovery_command_id: str
    authorization: DawRecoveryAuthorization
    binding: DawTrackBinding
    profile: DawControlProfile
    inspection: DawInspection

    def __post_init__(self) -> None:
        _require_id(self.recovery_command_id, "DawRestoreRequest.recovery_command_id")
        require_instance(self.authorization, DawRecoveryAuthorization, "DawRestoreRequest.authorization")
        require_instance(self.binding, DawTrackBinding, "DawRestoreRequest.binding")
        require_instance(self.profile, DawControlProfile, "DawRestoreRequest.profile")
        require_instance(self.inspection, DawInspection, "DawRestoreRequest.inspection")
        target = self.authorization.target
        if self.recovery_command_id in (self.authorization.authorization_id, target.source_command_id):
            raise ValueError("DawRestoreRequest.recovery_command_id must differ from the authorization and root IDs")
        if not self.profile.covers(self.binding):
            raise ValueError("DawRestoreRequest binding lies outside the profile's scope")
        if self.inspection.request != DawInspectRequest(self.binding, self.profile):
            raise ValueError("DawRestoreRequest inspection answers another binding or profile")
        if not self.inspection.matches_binding or self.inspection.build != self.profile.build:
            raise ValueError("DawRestoreRequest inspection does not confirm the bound insert on the tested build")
        if self.profile.conversion != target.conversion:
            raise ValueError("DawRestoreRequest conversion differs from the target's")
        if not _in_profile_ranges(self.profile, target.reading):
            raise ValueError("DawRestoreRequest target lies outside the calibrated range")

    @property
    def target(self) -> DawFaderReading:
        return self.authorization.target.reading


class DawRestoreGateCode(Enum):
    PROFILE_SCOPE = "profile_scope"
    CONVERSION_MISMATCH = "conversion_mismatch"
    IDENTITY_MISMATCH = "identity_mismatch"
    UNSUPPORTED_BUILD = "unsupported_build"
    UNRESOLVED_OTHER = "unresolved_other"
    TARGET_OUT_OF_RANGE = "target_out_of_range"


@dataclass(frozen=True)
class DawRestorePermitted:
    request: DawRestoreRequest

    def __post_init__(self) -> None:
        require_instance(self.request, DawRestoreRequest, "DawRestorePermitted.request")


@dataclass(frozen=True)
class DawRestoreNoWrite:
    """The current reading already equals the target exactly: nothing is sent and nothing is charged."""

    request: DawRestoreRequest

    def __post_init__(self) -> None:
        require_instance(self.request, DawRestoreRequest, "DawRestoreNoWrite.request")
        if self.request.inspection.fader != self.request.target:
            raise ValueError("DawRestoreNoWrite requires a current reading exactly equal to the target")


@dataclass(frozen=True)
class DawRestoreRejected:
    code: DawRestoreGateCode
    explanation: str

    def __post_init__(self) -> None:
        require_instance(self.code, DawRestoreGateCode, "DawRestoreRejected.code")
        require_text(self.explanation, "DawRestoreRejected.explanation")


DawRestoreGateResult = DawRestorePermitted | DawRestoreNoWrite | DawRestoreRejected


# --- restore outcomes ---------------------------------------------------------------------------------------------


def _check_restore_written(record: "DawRestoreConfirmed | DawRestoreMismatch", name: str) -> bool:
    _require_id(record.recovery_command_id, f"{name}.recovery_command_id")
    require_instance(record.before, DawFaderReading, f"{name}.before")
    require_instance(record.after, DawFaderReading, f"{name}.after")
    require_instance(record.target, DawFaderReading, f"{name}.target")
    _require_bool(record.replayed, f"{name}.replayed")
    return record.after.normalized == record.target.normalized and record.after.db == record.target.db


@dataclass(frozen=True)
class DawRestoreConfirmed:
    """Both read-back readings equal the target exactly. Confirms this fader's readings only."""

    recovery_command_id: str
    before: DawFaderReading
    after: DawFaderReading
    target: DawFaderReading
    replayed: bool

    def __post_init__(self) -> None:
        if not _check_restore_written(self, "DawRestoreConfirmed"):
            raise ValueError("DawRestoreConfirmed requires both readings exactly equal to the target")


@dataclass(frozen=True)
class DawRestoreMismatch:
    """A known write that did not land exactly on the target; actions stay blocked."""

    recovery_command_id: str
    before: DawFaderReading
    after: DawFaderReading
    target: DawFaderReading
    replayed: bool

    def __post_init__(self) -> None:
        if _check_restore_written(self, "DawRestoreMismatch"):
            raise ValueError("an exact landing is DawRestoreConfirmed, not DawRestoreMismatch")


@dataclass(frozen=True)
class DawRestoreRefused:
    """Refused without a write (on the wire after reservation, or locally before it)."""

    recovery_command_id: str
    code: DawRefusalCode
    explanation: str
    observed: DawFaderReading | None

    def __post_init__(self) -> None:
        _require_id(self.recovery_command_id, "DawRestoreRefused.recovery_command_id")
        require_instance(self.code, DawRefusalCode, "DawRestoreRefused.code")
        require_text(self.explanation, "DawRestoreRefused.explanation")
        _optional_reading(self.observed, "DawRestoreRefused.observed")


@dataclass(frozen=True)
class DawRestoreUncertain:
    recovery_command_id: str
    reason: DawUncertainReason
    before: DawFaderReading | None
    after: DawFaderReading | None
    explanation: str

    def __post_init__(self) -> None:
        _require_id(self.recovery_command_id, "DawRestoreUncertain.recovery_command_id")
        require_instance(self.reason, DawUncertainReason, "DawRestoreUncertain.reason")
        _optional_reading(self.before, "DawRestoreUncertain.before")
        _optional_reading(self.after, "DawRestoreUncertain.after")
        require_text(self.explanation, "DawRestoreUncertain.explanation")


DawRestoreOutcome = DawRestoreConfirmed | DawRestoreMismatch | DawRestoreRefused | DawRestoreUncertain
_OUTCOME_TYPES = (DawWriteConfirmed, DawWriteOffTarget, DawWriteRefused, DawWriteUncertain, DawRestoreConfirmed,
                  DawRestoreMismatch, DawRestoreRefused, DawRestoreUncertain)  # fmt: skip


# --- control state ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DawUnresolvedCommand:
    """One unresolved command and its lineage: an apply is its own root; a restoration belongs to one root."""

    command_id: str
    kind: DawCommandKind
    root_command_id: str
    attempt: int | None
    ledger_seq: int
    recorded_start: DawFaderReading
    commanded: DawFaderReading
    outcome: (
        DawWriteConfirmed
        | DawWriteOffTarget
        | DawWriteRefused
        | DawWriteUncertain
        | DawRestoreConfirmed
        | DawRestoreMismatch
        | DawRestoreRefused
        | DawRestoreUncertain
        | None
    )  # None: pending, no outcome

    def __post_init__(self) -> None:
        _require_id(self.command_id, "DawUnresolvedCommand.command_id")
        require_instance(self.kind, DawCommandKind, "DawUnresolvedCommand.kind")
        _require_id(self.root_command_id, "DawUnresolvedCommand.root_command_id")
        _require_seq(self.ledger_seq, "DawUnresolvedCommand.ledger_seq")
        require_instance(self.recorded_start, DawFaderReading, "DawUnresolvedCommand.recorded_start")
        require_instance(self.commanded, DawFaderReading, "DawUnresolvedCommand.commanded")
        if self.outcome is not None and not isinstance(self.outcome, _OUTCOME_TYPES):
            raise ValueError("DawUnresolvedCommand.outcome must be a DAW apply or restore outcome, or None")
        if self.kind is DawCommandKind.APPLY:
            if self.root_command_id != self.command_id or self.attempt is not None:
                raise ValueError("an apply is its own root and has no attempt number")
        else:
            if self.root_command_id == self.command_id:
                raise ValueError("a restoration is never its own root")
            _require_seq(self.attempt, "DawUnresolvedCommand.attempt")


@dataclass(frozen=True)
class DawRecoveryCharge:
    """A recovery budget charge persisted before dispatch, once per stable recovery command ID."""

    authorization_id: str
    recovery_command_id: str
    ledger_seq: int
    budget_grant: BudgetGrant

    def __post_init__(self) -> None:
        _require_id(self.authorization_id, "DawRecoveryCharge.authorization_id")
        _require_id(self.recovery_command_id, "DawRecoveryCharge.recovery_command_id")
        _require_seq(self.ledger_seq, "DawRecoveryCharge.ledger_seq")
        require_instance(self.budget_grant, BudgetGrant, "DawRecoveryCharge.budget_grant")
        if self.budget_grant.operation is not BudgetOperation.RECOVERY_OPERATION:
            raise ValueError("DawRecoveryCharge requires a RECOVERY_OPERATION budget grant")


def _require_ordered(items: tuple, cls: type, field: str) -> None:
    if not isinstance(items, tuple) or not all(isinstance(item, cls) for item in items):
        raise ValueError(f"{field} must be a tuple of {cls.__name__}")
    seqs = [item.ledger_seq for item in items]
    if any(a >= b for a, b in zip(seqs, seqs[1:])):
        raise ValueError(f"{field} must be strictly ordered by ledger_seq")


@dataclass(frozen=True)
class DawControlState:
    """Journal-derived state: unresolved commands, blocked conversions and recovery charges, in ledger order."""

    unresolved: tuple[DawUnresolvedCommand, ...]
    blocked_conversions: frozenset[VersionRef]
    recovery_charges: tuple[DawRecoveryCharge, ...]

    def __post_init__(self) -> None:
        _require_ordered(self.unresolved, DawUnresolvedCommand, "DawControlState.unresolved")
        if not isinstance(self.blocked_conversions, frozenset) or not all(
            isinstance(item, VersionRef) for item in self.blocked_conversions
        ):
            raise ValueError("DawControlState.blocked_conversions must be a frozenset of VersionRef")
        _require_ordered(self.recovery_charges, DawRecoveryCharge, "DawControlState.recovery_charges")


# --- reconciliation -----------------------------------------------------------------------------------------------


class DawReconcileFinding(Enum):
    AT_COMMANDED = "at_commanded"
    AT_RECORDED_START = "at_recorded_start"
    AMBIGUOUS = "ambiguous"
    NEITHER = "neither"
    IDENTITY_UNVERIFIED = "identity_unverified"


def classify_reconciliation(
    profile: DawControlProfile, recorded_start: DawFaderReading, commanded: DawFaderReading, reading: DawFaderReading
) -> tuple[DawReconcileFinding, bool, bool]:
    """Classify a fresh reading against both references; exact, inclusive comparisons.

    The reading matches the start within the profile's start tolerances, and the commanded value within the
    read-back (normalized) and success (dB) tolerances. Both -> AMBIGUOUS; commanded only -> AT_COMMANDED; start
    only -> AT_RECORDED_START; neither -> NEITHER. Ambiguity is decided per reading, never from how close the
    two references are.
    """
    matches_start = (
        abs(reading.normalized - recorded_start.normalized) <= profile.start_normalized_tolerance
        and abs(reading.db - recorded_start.db) <= profile.start_db_tolerance
    )
    matches_commanded = (
        abs(reading.normalized - commanded.normalized) <= profile.readback_normalized_tolerance
        and abs(reading.db - commanded.db) <= profile.success_db_tolerance
    )
    if matches_start and matches_commanded:
        finding = DawReconcileFinding.AMBIGUOUS
    elif matches_commanded:
        finding = DawReconcileFinding.AT_COMMANDED
    elif matches_start:
        finding = DawReconcileFinding.AT_RECORDED_START
    else:
        finding = DawReconcileFinding.NEITHER
    return finding, matches_start, matches_commanded


@dataclass(frozen=True)
class DawReconcileRequest:
    """Reconcile one unresolved command from a fresh read the adapter performs itself."""

    reconciliation_id: str
    subject_command_id: str
    binding: DawTrackBinding
    profile: DawControlProfile

    def __post_init__(self) -> None:
        _require_id(self.reconciliation_id, "DawReconcileRequest.reconciliation_id")
        _require_id(self.subject_command_id, "DawReconcileRequest.subject_command_id")
        if self.reconciliation_id == self.subject_command_id:
            raise ValueError("DawReconcileRequest IDs must differ")
        require_instance(self.binding, DawTrackBinding, "DawReconcileRequest.binding")
        require_instance(self.profile, DawControlProfile, "DawReconcileRequest.profile")
        if not self.profile.covers(self.binding):
            raise ValueError("DawReconcileRequest binding lies outside the profile's scope")


@dataclass(frozen=True)
class DawReconciliation:
    """What one fresh reading establishes about ``subject_command_id``: this fader's current reading only.

    It does not establish whether, when or how often the device executed the command, nor external movement,
    routing, plugins, project or audio. It never clears anything; ``resolve`` does, citing it.
    """

    reconciliation_id: str
    subject_command_id: str
    ledger_seq: int
    finding: DawReconcileFinding
    inspection: DawInspection | None
    recorded_start: DawFaderReading
    commanded: DawFaderReading
    matches_start: bool
    matches_commanded: bool
    replayed: bool

    def __post_init__(self) -> None:
        _require_id(self.reconciliation_id, "DawReconciliation.reconciliation_id")
        _require_id(self.subject_command_id, "DawReconciliation.subject_command_id")
        _require_seq(self.ledger_seq, "DawReconciliation.ledger_seq")
        require_instance(self.finding, DawReconcileFinding, "DawReconciliation.finding")
        if self.inspection is not None:
            require_instance(self.inspection, DawInspection, "DawReconciliation.inspection")
        require_instance(self.recorded_start, DawFaderReading, "DawReconciliation.recorded_start")
        require_instance(self.commanded, DawFaderReading, "DawReconciliation.commanded")
        for name in ("matches_start", "matches_commanded", "replayed"):
            _require_bool(getattr(self, name), f"DawReconciliation.{name}")
        inspection = self.inspection
        verified = (
            inspection is not None
            and inspection.matches_binding
            and inspection.build == inspection.request.profile.build
        )
        if not verified:
            if (
                self.finding is not DawReconcileFinding.IDENTITY_UNVERIFIED
                or self.matches_start
                or self.matches_commanded
            ):
                raise ValueError("an unverified reading is IDENTITY_UNVERIFIED and matches nothing")
            return
        expected = classify_reconciliation(
            inspection.request.profile, self.recorded_start, self.commanded, inspection.fader
        )
        if (self.finding, self.matches_start, self.matches_commanded) != expected:
            raise ValueError("DawReconciliation finding and matches must equal the classification of its reading")


# --- resolution -----------------------------------------------------------------------------------------------------


class DawResolutionKind(Enum):
    APPLIED_AS_COMMANDED = "applied_as_commanded"
    NOT_APPLIED_OR_REVERTED = "not_applied_or_reverted"
    RESTORED_TO_RECORDED_START = "restored_to_recorded_start"


class DawResolutionRefusalCode(Enum):
    DUPLICATE_ID_CONFLICT = "duplicate_id_conflict"
    SUBJECT_NOT_UNRESOLVED = "subject_not_unresolved"
    UNKNOWN_EVIDENCE = "unknown_evidence"
    EVIDENCE_NOT_FOR_SUBJECT = "evidence_not_for_subject"
    KIND_NOT_SUPPORTED_BY_EVIDENCE = "kind_not_supported_by_evidence"
    STALE_EVIDENCE = "stale_evidence"
    RESERVATION_AFTER_EVIDENCE = "reservation_after_evidence"
    AUTHORIZATION_REUSED = "authorization_reused"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class DawResolutionAuthorization:
    authorization_id: str
    authorized_by: VersionRef

    def __post_init__(self) -> None:
        _require_id(self.authorization_id, "DawResolutionAuthorization.authorization_id")
        require_instance(self.authorized_by, VersionRef, "DawResolutionAuthorization.authorized_by")


@dataclass(frozen=True)
class DawResolutionRequest:
    """Resolve one unresolved command, citing subject-specific, latest evidence (plan Amendment R)."""

    resolution_id: str
    subject_command_id: str
    kind: DawResolutionKind
    evidence_id: str
    authorization: DawResolutionAuthorization

    def __post_init__(self) -> None:
        _require_id(self.resolution_id, "DawResolutionRequest.resolution_id")
        _require_id(self.subject_command_id, "DawResolutionRequest.subject_command_id")
        require_instance(self.kind, DawResolutionKind, "DawResolutionRequest.kind")
        _require_id(self.evidence_id, "DawResolutionRequest.evidence_id")
        require_instance(self.authorization, DawResolutionAuthorization, "DawResolutionRequest.authorization")
        ids = (self.resolution_id, self.subject_command_id, self.evidence_id, self.authorization.authorization_id)
        if len(set(ids)) != len(ids):
            raise ValueError("DawResolutionRequest IDs must all differ")


@dataclass(frozen=True)
class DawResolutionRecorded:
    resolution_id: str
    subject_command_id: str
    root_command_id: str
    kind: DawResolutionKind
    resolved_reading: DawFaderReading
    evidence_id: str
    ledger_seq: int
    replayed: bool

    def __post_init__(self) -> None:
        _require_id(self.resolution_id, "DawResolutionRecorded.resolution_id")
        _require_id(self.subject_command_id, "DawResolutionRecorded.subject_command_id")
        _require_id(self.root_command_id, "DawResolutionRecorded.root_command_id")
        require_instance(self.kind, DawResolutionKind, "DawResolutionRecorded.kind")
        require_instance(self.resolved_reading, DawFaderReading, "DawResolutionRecorded.resolved_reading")
        _require_id(self.evidence_id, "DawResolutionRecorded.evidence_id")
        _require_seq(self.ledger_seq, "DawResolutionRecorded.ledger_seq")
        _require_bool(self.replayed, "DawResolutionRecorded.replayed")


@dataclass(frozen=True)
class DawResolutionRefused:
    resolution_id: str
    code: DawResolutionRefusalCode
    explanation: str

    def __post_init__(self) -> None:
        _require_id(self.resolution_id, "DawResolutionRefused.resolution_id")
        require_instance(self.code, DawResolutionRefusalCode, "DawResolutionRefused.code")
        require_text(self.explanation, "DawResolutionRefused.explanation")


DawResolutionOutcome = DawResolutionRecorded | DawResolutionRefused
