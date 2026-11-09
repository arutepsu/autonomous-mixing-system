"""State-integrity requests and per-artifact results (attempt contract §§22, 28-29).

A ``StateIntegrityRequest`` names one project state and a logical root for every artifact the state needs:
its current audio members, then fixed gain inputs that are not current members (superseded inputs remain
required because later execution reads them). Results record an inspector's claim per artifact; record
construction checks structure, not the truth of the external inspection. A report is integrity evidence
only: it is independent of journal authority and does not prove restoration or publication of a state.
"""

from dataclasses import dataclass
from enum import Enum

from autonomous_mixing.core.records.artifacts import _SHA256_HEX, AudioArtifact
from autonomous_mixing.core.records.identifiers import ArtifactId
from autonomous_mixing.core.records.invariants import require_instance, require_text, require_tuple_of
from autonomous_mixing.core.records.project import ProjectState


@dataclass(frozen=True)
class ArtifactRootBinding:
    """Names the logical root an artifact's location is resolved against; physical paths are adapter configuration."""

    artifact_id: ArtifactId
    root_key: str

    def __post_init__(self) -> None:
        require_instance(self.artifact_id, ArtifactId, "ArtifactRootBinding.artifact_id")
        require_text(self.root_key, "ArtifactRootBinding.root_key")


def _required_artifacts(state: ProjectState) -> tuple[AudioArtifact, ...]:
    required = list(state.audio.artifacts)
    seen = {artifact.artifact_id for artifact in required}
    for gain in state.processing.track_gains:
        if gain.input_artifact.artifact_id not in seen:
            seen.add(gain.input_artifact.artifact_id)
            required.append(gain.input_artifact)
    return tuple(required)


@dataclass(frozen=True)
class StateIntegrityRequest:
    """One state to inspect, with exactly one root binding per required artifact (any binding order)."""

    state: ProjectState
    bindings: tuple[ArtifactRootBinding, ...]

    def __post_init__(self) -> None:
        require_instance(self.state, ProjectState, "StateIntegrityRequest.state")
        require_tuple_of(
            self.bindings, ArtifactRootBinding, "StateIntegrityRequest.bindings", nonempty=False, distinct=False
        )
        bound = [binding.artifact_id for binding in self.bindings]
        duplicates = sorted({str(a) for a in bound if bound.count(a) > 1})
        if duplicates:
            raise ValueError(f"duplicate root bindings for: {', '.join(duplicates)}")
        required = {artifact.artifact_id for artifact in self.required_artifacts}
        missing = sorted(str(a) for a in required - set(bound))
        extra = sorted(str(a) for a in set(bound) - required)
        if missing or extra:
            raise ValueError(f"root bindings do not match the required artifacts (missing: {missing}, extra: {extra})")

    @property
    def required_artifacts(self) -> tuple[AudioArtifact, ...]:
        """Current audio members in checkpoint order, then fixed inputs not already present, in gain order."""
        return _required_artifacts(self.state)


class IntegrityStatus(Enum):
    VERIFIED = "verified"  # the inspector claims hash, format and metadata inspection succeeded
    FAILED = "failed"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class ArtifactIntegrityResult:
    """One artifact's inspection outcome. A matching hash alone does not imply VERIFIED."""

    artifact: AudioArtifact
    status: IntegrityStatus
    observed_sha256: str | None
    explanation: str

    def __post_init__(self) -> None:
        require_instance(self.artifact, AudioArtifact, "ArtifactIntegrityResult.artifact")
        require_instance(self.status, IntegrityStatus, "ArtifactIntegrityResult.status")
        observed = self.observed_sha256
        if observed is not None and (not isinstance(observed, str) or not _SHA256_HEX.fullmatch(observed)):
            raise ValueError("ArtifactIntegrityResult.observed_sha256 must be a lowercase SHA-256 hex digest or None")
        require_text(self.explanation, "ArtifactIntegrityResult.explanation")
        if self.status is IntegrityStatus.VERIFIED and observed != self.artifact.content_hash:
            raise ValueError(
                f"VERIFIED result for {self.artifact.artifact_id} requires the observed hash to equal the recorded hash"
            )


@dataclass(frozen=True)
class StateIntegrityReport:
    """One result per required artifact, in ``required_artifacts`` order, kept even after a failure."""

    request: StateIntegrityRequest
    artifacts: tuple[ArtifactIntegrityResult, ...]

    def __post_init__(self) -> None:
        require_instance(self.request, StateIntegrityRequest, "StateIntegrityReport.request")
        require_tuple_of(
            self.artifacts, ArtifactIntegrityResult, "StateIntegrityReport.artifacts", nonempty=True, distinct=False
        )
        required = self.request.required_artifacts
        reported = tuple(result.artifact for result in self.artifacts)
        if reported != required:
            raise ValueError(
                "report results must cover every required artifact exactly once, in order, with equal metadata"
            )

    @property
    def status(self) -> IntegrityStatus:
        """FAILED if any result failed, otherwise UNCERTAIN if any is uncertain, otherwise VERIFIED."""
        statuses = {result.status for result in self.artifacts}
        if IntegrityStatus.FAILED in statuses:
            return IntegrityStatus.FAILED
        if IntegrityStatus.UNCERTAIN in statuses:
            return IntegrityStatus.UNCERTAIN
        return IntegrityStatus.VERIFIED
