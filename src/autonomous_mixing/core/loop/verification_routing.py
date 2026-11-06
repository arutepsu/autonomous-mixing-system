"""Map a verification result to the coordinator's next obligation (attempt contract §§20-22, 27).

Pure: no transition, effect or budget charge. The verdict is the one ``assess_verification`` supplied;
it is not recomputed here, and the consistency check does not authenticate an arbitrary result's
numerical verdict.
"""

from typing import assert_never

from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.verification import (
    AcceptanceCriteria,
    AssessmentMode,
    TargetStatus,
    Verdict,
    VerificationResult,
)
from autonomous_mixing.core.records.verification_routing import VerificationRoute
from autonomous_mixing.core.verification.consistency import validate_verification_references


def route_verification(result: VerificationResult, criteria: AcceptanceCriteria) -> VerificationRoute:
    """Return the route for a structurally consistent ``result``; structural errors propagate as ``ValueError``.

    INITIAL: ACCEPTED -> FINISH_INITIAL_SUCCESS; REJECTED -> CHECK_BASELINE_READINESS; INCONCLUSIVE ->
    FINISH_INITIAL_INCONCLUSIVE. ATTEMPT: ACCEPTED with SATISFIED targets -> COMMIT_THEN_FINISH, with UNMET
    targets -> COMMIT_THEN_ITERATE; REJECTED -> RESTORE_THEN_CONSIDER_ITERATION; INCONCLUSIVE ->
    RECONCILE_THEN_FINISH_INCONCLUSIVE.
    """
    require_instance(result, VerificationResult, "result")
    require_instance(criteria, AcceptanceCriteria, "criteria")
    validate_verification_references(result, criteria)

    match result.mode, result.verdict:
        case AssessmentMode.INITIAL, Verdict.ACCEPTED:
            return VerificationRoute.FINISH_INITIAL_SUCCESS  # VerificationResult requires SATISFIED here
        case AssessmentMode.INITIAL, Verdict.REJECTED:
            return VerificationRoute.CHECK_BASELINE_READINESS
        case AssessmentMode.INITIAL, Verdict.INCONCLUSIVE:
            return VerificationRoute.FINISH_INITIAL_INCONCLUSIVE
        case AssessmentMode.ATTEMPT, Verdict.ACCEPTED:
            match result.target_status:
                case TargetStatus.SATISFIED:
                    return VerificationRoute.COMMIT_THEN_FINISH
                case TargetStatus.UNMET:
                    return VerificationRoute.COMMIT_THEN_ITERATE
                case TargetStatus.UNKNOWN:  # excluded by VerificationResult
                    raise ValueError("ACCEPTED result cannot have UNKNOWN target status")
                case _:
                    assert_never(result.target_status)
        case AssessmentMode.ATTEMPT, Verdict.REJECTED:
            return VerificationRoute.RESTORE_THEN_CONSIDER_ITERATION
        case AssessmentMode.ATTEMPT, Verdict.INCONCLUSIVE:
            return VerificationRoute.RECONCILE_THEN_FINISH_INCONCLUSIVE
    raise ValueError(f"unroutable result: {result.mode!r}, {result.verdict!r}")
