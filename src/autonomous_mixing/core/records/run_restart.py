"""Restart records: the classification of a stored run and the DAW facts it is decided from (Increment 5, ADR-039).

Only a run whose last event is its suspension checkpoint, with consistent DAW facts, may resume. Every other
non-terminal run is classified and refused for explicit operator recovery: nothing is restored, adopted, resolved,
committed or re-dispatched. The authoritative reference is always the committed acceptance head, never a value
derived from the trace.
"""

from dataclasses import dataclass
from enum import Enum

from autonomous_mixing.core.records.acceptance import AcceptedReference
from autonomous_mixing.core.records.daw_binding import DawTrackBinding
from autonomous_mixing.core.records.daw_export import DawStateAnchor
from autonomous_mixing.core.records.daw_recovery import DawControlState, DawRecoveryLineage
from autonomous_mixing.core.records.identifiers import RunEventId, RunId
from autonomous_mixing.core.records.invariants import require_instance, require_text, require_tuple_of
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
