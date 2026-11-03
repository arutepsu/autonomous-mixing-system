"""Compatibility shim (ADR-027 migration step M1): definitions moved to ``autonomous_mixing.core.records``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.core.records.identifiers import (
    ActionId,
    ArtifactId,
    AttemptId,
    CheckpointId,
    ConditionId,
    ContextId,
    CriteriaId,
    DecisionId,
    ExecutionId,
    ObservationId,
    ProjectId,
    Revision,
    RunId,
    TrackId,
    ValidationResultId,
)

# Private helper re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.core.records.identifiers import _Identifier as _Identifier

__all__ = [
    "ProjectId",
    "TrackId",
    "ArtifactId",
    "ObservationId",
    "ContextId",
    "ActionId",
    "DecisionId",
    "CriteriaId",
    "ConditionId",
    "ValidationResultId",
    "RunId",
    "AttemptId",
    "ExecutionId",
    "CheckpointId",
    "Revision",
]
