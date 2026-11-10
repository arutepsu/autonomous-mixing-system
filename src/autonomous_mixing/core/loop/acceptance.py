"""Coordinator-side precommit validation of an acceptance event (attempt contract §29).

The journal port and its adapters cannot import loop or stage internals, so the coordinator runs this
check before committing. It does not establish run, attempt or decision membership, the authority of the
run's criteria, or rendered-file integrity: an ACCEPTED verification result is a mathematical assessment
of the supplied observations, not proof that the files exist unchanged.
"""

from autonomous_mixing.core.loop.transitions import derive_candidate_project_state
from autonomous_mixing.core.records.acceptance import AcceptanceEvent
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.verification.assessment import assess_verification


def validate_acceptance_event(event: AcceptanceEvent) -> None:
    """Require that the candidate follows from the action and that the verdict follows from the evidence.

    Re-derives the candidate from the expected state, execution and action (reusing the gain,
    supersession, retained-artifact and processing-state rules) with the candidate's own checkpoint ID
    and capture time, and recomputes the verification result. Both must equal the recorded ones exactly.
    """
    require_instance(event, AcceptanceEvent, "event")
    candidate = event.assessment.assessed.state
    derived = derive_candidate_project_state(
        event.expected.state,
        event.execution,
        event.action,
        candidate.audio.checkpoint_id,
        candidate.snapshot.captured_at,
    )
    if derived != candidate:
        raise ValueError(f"assessed candidate {candidate.revision} does not follow from the action and execution")
    if assess_verification(event.assessment) != event.verification:
        raise ValueError(
            f"recorded verification {event.verification.validation_result_id} differs from the recomputed result"
        )
