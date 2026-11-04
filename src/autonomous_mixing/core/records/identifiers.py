"""Typed identifiers and version references."""

from dataclasses import dataclass

from autonomous_mixing.core.records.invariants import require_text


@dataclass(frozen=True)
class _Identifier:
    value: str

    def __post_init__(self) -> None:
        require_text(self.value, type(self).__name__)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ProjectId(_Identifier):
    pass


@dataclass(frozen=True)
class TrackId(_Identifier):
    """Project-scoped stable track identity; independent of the display name."""


@dataclass(frozen=True)
class ArtifactId(_Identifier):
    pass


@dataclass(frozen=True)
class ObservationId(_Identifier):
    pass


@dataclass(frozen=True)
class ContextId(_Identifier):
    pass


@dataclass(frozen=True)
class ActionId(_Identifier):
    pass


@dataclass(frozen=True)
class DecisionId(_Identifier):
    pass


@dataclass(frozen=True)
class CriteriaId(_Identifier):
    """Identifies a criteria definition together with its version."""


@dataclass(frozen=True)
class ConditionId(_Identifier):
    """Unique within one criteria definition."""


@dataclass(frozen=True)
class VerificationResultId(_Identifier):
    pass


@dataclass(frozen=True)
class RunId(_Identifier):
    pass


@dataclass(frozen=True)
class AttemptId(_Identifier):
    pass


@dataclass(frozen=True)
class ExecutionId(_Identifier):
    pass


@dataclass(frozen=True)
class CheckpointId(_Identifier):
    pass


@dataclass(frozen=True)
class Revision(_Identifier):
    """Opaque project revision token; only equality is meaningful (no ordering or counter contract)."""


@dataclass(frozen=True)
class VersionRef:
    """Exact identifier of a versioned specification or implementation; no implicit compatibility."""

    name: str
    version: str

    def __post_init__(self) -> None:
        require_text(self.name, "VersionRef.name")
        require_text(self.version, "VersionRef.version")
