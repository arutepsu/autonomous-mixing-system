"""Consistency checks between verification results and their criteria."""

from autonomous_mixing.core.records.verification import (
    AbsoluteGuard,
    AssessmentMode,
    CheckStatus,
    DeltaGuard,
    TargetStatus,
    ValidationCriteria,
    ValidationResult,
    ValidationVerdict,
)


def validate_validation_references(result: ValidationResult, criteria: ValidationCriteria) -> None:
    """Check that a result's checks are consistent with a criteria definition.

    Record consistency only: no improvement calculation, threshold comparison, audio inspection,
    observation lookup or provenance check.
    """
    if result.criteria_id != criteria.criteria_id or result.criteria_version != criteria.version:
        raise ValueError(
            f"result references criteria {result.criteria_id}@{result.criteria_version}, "
            f"supplied criteria is {criteria.criteria_id}@{criteria.version}"
        )

    by_id = {check.condition_id: check for check in result.checks}
    configured = {c.condition_id for c in (*criteria.targets, *criteria.improvements, *criteria.guards)}
    missing = sorted(str(c) for c in configured - by_id.keys())
    extra = sorted(str(c) for c in by_id.keys() - configured)
    if missing or extra:
        raise ValueError(f"checks do not match criteria (missing: {missing}, extra: {extra})")

    na = CheckStatus.NOT_APPLICABLE
    deferred = [c.condition_id for c in criteria.improvements] + [
        g.condition_id for g in criteria.guards if isinstance(g, DeltaGuard)
    ]
    immediate = [c.condition_id for c in criteria.targets] + [
        g.condition_id for g in criteria.guards if isinstance(g, AbsoluteGuard)
    ]
    if result.mode is AssessmentMode.INITIAL:
        for condition_id in deferred:
            if by_id[condition_id].status is not na:
                raise ValueError(f"INITIAL assessment requires {condition_id} to be NOT_APPLICABLE")
        for condition_id in immediate:
            if by_id[condition_id].status is na:
                raise ValueError(f"INITIAL assessment cannot mark {condition_id} NOT_APPLICABLE")
    else:
        for check in result.checks:
            if check.status is na:
                raise ValueError(f"ATTEMPT assessment cannot mark {check.condition_id} NOT_APPLICABLE")

    if result.verdict is ValidationVerdict.ACCEPTED:
        # Acceptance: every guard passes, and either targets are SATISFIED or, for an UNMET target
        # (intermediate improvement), every improvement condition passes.
        for guard in criteria.guards:
            if by_id[guard.condition_id].status is CheckStatus.FAIL:
                raise ValueError(f"ACCEPTED result has failing guard {guard.condition_id}")
        if result.target_status is TargetStatus.UNMET:
            for improvement in criteria.improvements:
                if by_id[improvement.condition_id].status is not CheckStatus.PASS:
                    raise ValueError(
                        f"ACCEPTED result with UNMET targets requires improvement {improvement.condition_id} to PASS"
                    )

    target_statuses = [by_id[t.condition_id].status for t in criteria.targets]
    has_fail = CheckStatus.FAIL in target_statuses
    has_unavailable = CheckStatus.UNAVAILABLE in target_statuses
    if result.target_status is TargetStatus.SATISFIED and any(s is not CheckStatus.PASS for s in target_statuses):
        raise ValueError("SATISFIED target status requires every target check to PASS")
    if result.target_status is TargetStatus.UNMET and not has_fail:
        raise ValueError("UNMET target status requires at least one failing target check")
    if result.target_status is TargetStatus.UNKNOWN and (has_fail or not has_unavailable):
        raise ValueError("UNKNOWN target status requires an UNAVAILABLE target check and no failing target check")
