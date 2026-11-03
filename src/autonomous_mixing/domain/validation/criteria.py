"""Compatibility shim (ADR-027 migration step M1): definitions moved to ``autonomous_mixing.core.records``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.core.records.verification import (
    AbsoluteGuard,
    BaselineReference,
    ComparisonOperator,
    DeltaGuard,
    ImprovementCondition,
    ImprovementDirection,
    MetricSelector,
    TargetCondition,
    ValidationCriteria,
)

# Private helper re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.core.records.verification import _require_condition_fields as _require_condition_fields

__all__ = [
    "MetricSelector",
    "ComparisonOperator",
    "ImprovementDirection",
    "BaselineReference",
    "TargetCondition",
    "ImprovementCondition",
    "AbsoluteGuard",
    "DeltaGuard",
    "ValidationCriteria",
]
