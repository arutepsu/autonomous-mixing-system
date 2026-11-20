"""Restart records: the classification of a stored run and the DAW facts it is decided from (Increment 5, ADR-039).

Only a run whose last event is its suspension checkpoint, with consistent DAW facts, may resume. Every other
non-terminal run is classified and refused for explicit operator recovery: nothing is restored, adopted, resolved,
committed or re-dispatched. The authoritative reference is always the committed acceptance head, never a value
derived from the trace.
"""

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from autonomous_mixing.core.records.acceptance import AcceptedReference
from autonomous_mixing.core.records.daw_binding import DawFaderReading, DawTrackBinding
from autonomous_mixing.core.records.daw_export import DawStateAnchor
from autonomous_mixing.core.records.daw_recovery import DawControlState, DawRecoveryLineage, DawResolutionKind
from autonomous_mixing.core.records.identifiers import RunEventId, RunId
from autonomous_mixing.core.records.invariants import require_instance, require_text, require_tuple_of, require_utc
from autonomous_mixing.core.records.termination import TerminationReason


class RunInterruptionCode(Enum):
    """The first-match classification of a stored run (plan amendment A2, rows 0-12)."""

    NOT_STARTED = "not_started"
    FINISHED = "finished"
    INTERRUPTED_AFTER_ACCEPTANCE = "interrupted_after_acceptance"
    INTERRUPTED_OFFLINE = "interrupted_offline"
    INTERRUPTED_RECOVERY = "interrupted_recovery"
    INTERRUPTED_BEFORE_ACCEPTANCE_COMMIT = "interrupted_before_acceptance_commit"
    INTERRUPTED_BEFORE_WRITE = "interrupted_before_write"
    INTERRUPTED_WRITE_UNRESOLVED = "interrupted_write_unresolved"
    INTERRUPTED_AFTER_WRITE = "interrupted_after_write"
    INTERRUPTED_BEFORE_SUSPENSION = "interrupted_before_suspension"
    CONTINUATION_INTERRUPTED = "continuation_interrupted"
    SUSPENDED_DAW_STATE_UNEXPLAINED = "suspended_daw_state_unexplained"
    INTERRUPTED_BETWEEN_ROUNDS = "interrupted_between_rounds"
    INTERRUPTED_BEFORE_FIRST_WRITE = "interrupted_before_first_write"
    UNCLASSIFIED = "unclassified"  # fail-closed fallback; a coverage test pins that it never occurs


@dataclass(frozen=True)
class RunInterrupted:
    """A stored run that may not resume: its code, the authoritative (committed) reference and its evidence."""

    run_id: RunId
    code: RunInterruptionCode
    authoritative: AcceptedReference
    evidence_event_ids: tuple[RunEventId, ...]
    daw_command_ids: tuple[str, ...]
    explanation: str

    def __post_init__(self) -> None:
        require_instance(self.run_id, RunId, "RunInterrupted.run_id")
        require_instance(self.code, RunInterruptionCode, "RunInterrupted.code")
        require_instance(self.authoritative, AcceptedReference, "RunInterrupted.authoritative")
        require_tuple_of(
            self.evidence_event_ids, RunEventId, "RunInterrupted.evidence_event_ids", nonempty=False, distinct=True
        )
        require_tuple_of(self.daw_command_ids, str, "RunInterrupted.daw_command_ids", nonempty=False, distinct=True)
        require_text(self.explanation, "RunInterrupted.explanation")


@dataclass(frozen=True)
class DawRestartFacts:
    """What the FL journal says about a stored run, gathered once (``None``: that fact was unavailable).

    ``lineages`` are the recovery lineages of the run's dispatched applies that the journal knows; ``unknown_roots``
    are dispatched applies the journal has no lineage for (never reserved, or refused before any write).
    """

    control_state: DawControlState | None
    lineages: tuple[DawRecoveryLineage, ...]
    unknown_roots: tuple[str, ...]
    available: bool

    def __post_init__(self) -> None:
        if self.control_state is not None:
            require_instance(self.control_state, DawControlState, "DawRestartFacts.control_state")
        require_tuple_of(self.lineages, DawRecoveryLineage, "DawRestartFacts.lineages", nonempty=False, distinct=False)
        require_tuple_of(self.unknown_roots, str, "DawRestartFacts.unknown_roots", nonempty=False, distinct=True)
        if not isinstance(self.available, bool):
            raise ValueError("DawRestartFacts.available must be a bool")
        if len({lineage.root_command_id for lineage in self.lineages} | set(self.unknown_roots)) != len(
            self.lineages
        ) + len(self.unknown_roots):
            raise ValueError("DawRestartFacts lists each root once")

    def lineage(self, root_command_id: str) -> DawRecoveryLineage | None:
        return next((item for item in self.lineages if item.root_command_id == root_command_id), None)


@dataclass(frozen=True)
class DawLatestAnchorQuery:
    """A journal-only query for the latest state-establishing FL event of the bound fader (run-start check)."""

    binding: DawTrackBinding

    def __post_init__(self) -> None:
        require_instance(self.binding, DawTrackBinding, "DawLatestAnchorQuery.binding")


@dataclass(frozen=True)
class DawLatestState:
    """The latest state-establishing FL event as an anchor, or ``None`` for a journal with none."""

    anchor: DawStateAnchor | None
    ledger_watermark: int

    def __post_init__(self) -> None:
        if self.anchor is not None:
            require_instance(self.anchor, DawStateAnchor, "DawLatestState.anchor")
        if type(self.ledger_watermark) is not int or self.ledger_watermark < 0:
            raise ValueError("DawLatestState.ledger_watermark must be a non-negative built-in int")
        if self.anchor is not None and self.anchor.ledger_watermark != self.ledger_watermark:
            raise ValueError("DawLatestState watermark differs from its anchor's")


@dataclass(frozen=True)
class DawStartRefused:
    """Why a DAW run may not start: FL state its configuration does not explain (G14). Nothing is adopted."""

    reason: TerminationReason
    daw_command_ids: tuple[str, ...]
    explanation: str

    def __post_init__(self) -> None:
        require_instance(self.reason, TerminationReason, "DawStartRefused.reason")
        if self.reason not in (
            TerminationReason.DAW_COMMAND_UNRESOLVED,
            TerminationReason.DAW_CONVERSION_BLOCKED,
            TerminationReason.DAW_CONTINUITY_FAILED,
        ):
            raise ValueError("a refused DAW start reuses an existing DAW reason (ADR-039 E6)")
        require_tuple_of(self.daw_command_ids, str, "DawStartRefused.daw_command_ids", nonempty=False, distinct=True)
        require_text(self.explanation, "DawStartRefused.explanation")


# --- operator-authorized recovery (coordinator Increment 6, ADR-040) ---------------------------------------------------


class OperatorOperation(Enum):
    """One operation an operator may authorize; none adopts a state or restores anything automatically."""

    RECONCILE = "reconcile"  # one fresh read of an unresolved command; clears nothing
    RESTORE = "restore"  # one exact restoration to the journaled target (own lineage only)
    RESOLVE = "resolve"  # a resolution, only of the kind the latest evidence supports (the FL journal decides)
    INSPECT_INTEGRITY = "inspect_integrity"  # the retained-audio integrity inspection of the authoritative state
    CLOSE = "close"  # records a terminal snapshot of facts only; never restores


@dataclass(frozen=True)
class OperatorRecoveryOption:
    """One permitted operation with its exact subject; shown before any authorization."""

    operation: OperatorOperation
    subject_command_id: str | None
    resolution_kind: DawResolutionKind | None
    evidence_id: str | None
    explanation: str

    def __post_init__(self) -> None:
        require_instance(self.operation, OperatorOperation, "OperatorRecoveryOption.operation")
        for name in ("subject_command_id", "evidence_id"):
            value = getattr(self, name)
            if value is not None:
                require_text(value, f"OperatorRecoveryOption.{name}")
        if self.resolution_kind is not None:
            require_instance(self.resolution_kind, DawResolutionKind, "OperatorRecoveryOption.resolution_kind")
        require_text(self.explanation, "OperatorRecoveryOption.explanation")
        needs_subject = self.operation in (OperatorOperation.RECONCILE, OperatorOperation.RESTORE,
                                           OperatorOperation.RESOLVE)  # fmt: skip
        if needs_subject != (self.subject_command_id is not None):
            raise ValueError("reconcile, restore and resolve name a command; the other operations do not")
        is_resolve = self.operation is OperatorOperation.RESOLVE
        if is_resolve != (self.resolution_kind is not None) or is_resolve != (self.evidence_id is not None):
            raise ValueError("exactly a resolution names its kind and its evidence")


@dataclass(frozen=True)
class OperatorRecoveryView:
    """Everything an operator sees before authorizing one recovery operation (phase 1, read-only).

    ``run`` is None for FL commands no run explains (``--orphans``). The proposed target is the journaled restore
    target of the run's write; the saved target is the authoritative state's confirmed fader; ``exact`` says whether
    they are exactly equal (ADR-038 D1). The authorization binds to a fingerprint of this whole record.
    """

    run: RunInterrupted | None
    control_state: DawControlState | None
    lineages: tuple[DawRecoveryLineage, ...]
    proposed_target: DawFaderReading | None
    saved_target: DawFaderReading | None
    options: tuple[OperatorRecoveryOption, ...]

    def __post_init__(self) -> None:
        if self.run is not None:
            require_instance(self.run, RunInterrupted, "OperatorRecoveryView.run")
        if self.control_state is not None:
            require_instance(self.control_state, DawControlState, "OperatorRecoveryView.control_state")
        require_tuple_of(self.lineages, DawRecoveryLineage, "OperatorRecoveryView.lineages", nonempty=False,
                         distinct=False)  # fmt: skip
        for name in ("proposed_target", "saved_target"):
            value = getattr(self, name)
            if value is not None:
                require_instance(value, DawFaderReading, f"OperatorRecoveryView.{name}")
        require_tuple_of(self.options, OperatorRecoveryOption, "OperatorRecoveryView.options", nonempty=False,
                         distinct=True)  # fmt: skip

    @property
    def exact(self) -> bool | None:
        if self.proposed_target is None or self.saved_target is None:
            return None
        a, b = self.proposed_target, self.saved_target
        return all(x == y and math.copysign(1.0, x) == math.copysign(1.0, y)
                   for x, y in ((a.normalized, b.normalized), (a.db, b.db)))  # fmt: skip


@dataclass(frozen=True)
class OperatorRecoveryAuthorization:
    """The operator's explicit authorization of exactly one shown option, bound to the shown state's fingerprint."""

    run_id: RunId
    operator: str
    view_fingerprint: str
    option: OperatorRecoveryOption
    authorized_at: datetime

    def __post_init__(self) -> None:
        require_instance(self.run_id, RunId, "OperatorRecoveryAuthorization.run_id")
        require_text(self.operator, "OperatorRecoveryAuthorization.operator")
        if (
            not isinstance(self.view_fingerprint, str)
            or len(self.view_fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in self.view_fingerprint)
        ):
            raise ValueError("OperatorRecoveryAuthorization.view_fingerprint must be a SHA-256 hex digest")
        require_instance(self.option, OperatorRecoveryOption, "OperatorRecoveryAuthorization.option")
        require_utc(self.authorized_at, "OperatorRecoveryAuthorization.authorized_at")
