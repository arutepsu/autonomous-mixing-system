"""Acceptance-reconciliation outcomes (attempt contract §§22, 28-29).

Reconciliation combines a journal authority answer for one acceptance event with integrity evidence for
the state that answer makes authoritative. ``authoritative_reference`` is the accepted reference the
journal establishes (the committed candidate, or the event's expected reference when the event is
absent); ``verified_reference`` is set only when integrity of that state was also VERIFIED. Confirming a
candidate acceptance and verifying retention of the previous state are distinct outcomes, and a known
authoritative state stays reported even when its files failed or could not be checked.
"""

from dataclasses import dataclass
from enum import Enum

from autonomous_mixing.core.records.acceptance import AcceptedReference
from autonomous_mixing.core.records.invariants import require_instance


class ReconciliationOutcome(Enum):
    CONFIRMED_ACCEPTANCE = "confirmed_acceptance"
    VERIFIED_PREVIOUS_STATE = "verified_previous_state"
    ACCEPTED_STATE_FAILED = "accepted_state_failed"
    ACCEPTED_STATE_UNCERTAIN = "accepted_state_uncertain"
    PREVIOUS_STATE_FAILED = "previous_state_failed"
    PREVIOUS_STATE_UNCERTAIN = "previous_state_uncertain"
    AUTHORITY_UNCERTAIN = "authority_uncertain"


_VERIFIED = (ReconciliationOutcome.CONFIRMED_ACCEPTANCE, ReconciliationOutcome.VERIFIED_PREVIOUS_STATE)


@dataclass(frozen=True)
class ReconciliationResult:
    """What journal authority and integrity evidence establish; no selection, recording or rollback."""

    outcome: ReconciliationOutcome
    authoritative_reference: AcceptedReference | None
    verified_reference: AcceptedReference | None

    def __post_init__(self) -> None:
        require_instance(self.outcome, ReconciliationOutcome, "ReconciliationResult.outcome")
        if self.authoritative_reference is not None:
            require_instance(
                self.authoritative_reference, AcceptedReference, "ReconciliationResult.authoritative_reference"
            )
        if self.verified_reference is not None:
            require_instance(self.verified_reference, AcceptedReference, "ReconciliationResult.verified_reference")
        if self.outcome is ReconciliationOutcome.AUTHORITY_UNCERTAIN:
            if self.authoritative_reference is not None or self.verified_reference is not None:
                raise ValueError("AUTHORITY_UNCERTAIN carries no authoritative or verified reference")
            return
        if self.authoritative_reference is None:
            raise ValueError(f"{self.outcome.name} requires the authoritative reference")
        if self.outcome in _VERIFIED:
            if self.verified_reference != self.authoritative_reference:
                raise ValueError(f"{self.outcome.name} requires the verified reference to equal the authoritative one")
        elif self.verified_reference is not None:
            raise ValueError(f"{self.outcome.name} carries no verified reference")
