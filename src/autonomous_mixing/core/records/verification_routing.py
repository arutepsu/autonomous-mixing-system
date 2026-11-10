"""Verification routes: the coordinator's next obligation after a verification result (attempt contract §27).

A route names a future obligation, not a completed transition. The coordinator must still establish
each route's acceptance, recovery and budget prerequisites.
"""

from enum import Enum


class VerificationRoute(Enum):
    FINISH_INITIAL_SUCCESS = "finish_initial_success"  # subject to a known accepted reference and finalization
    CHECK_BASELINE_READINESS = "check_baseline_readiness"  # later readiness checks precede any correction
    FINISH_INITIAL_INCONCLUSIVE = "finish_initial_inconclusive"  # future INITIAL_ASSESSMENT_INCONCLUSIVE
    COMMIT_THEN_FINISH = "commit_then_finish"  # success only after confirmed acceptance
    COMMIT_THEN_ITERATE = "commit_then_iterate"  # iteration only after confirmed acceptance
    RESTORE_THEN_CONSIDER_ITERATION = "restore_then_consider_iteration"  # verified restoration, then budget
    RECONCILE_THEN_FINISH_INCONCLUSIVE = "reconcile_then_finish_inconclusive"  # future VERIFICATION_INCONCLUSIVE
