"""Actions, execution and rollback receipts, and candidate checkpoint derivation."""

from dataclasses import dataclass
from enum import Enum

from autonomous_mixing.core.records.artifacts import AudioArtifact
from autonomous_mixing.core.records.identifiers import (
    ActionId,
    ArtifactId,
    CheckpointId,
    ExecutionId,
    ProjectId,
    Revision,
    TrackId,
)
from autonomous_mixing.core.records.invariants import require_finite, require_instance, require_text, require_tuple_of
from autonomous_mixing.core.records.project import ArtifactCheckpoint, ProjectState, _require_unique_artifacts


@dataclass(frozen=True)
class SetTrackGain:
    """Set a track's gain to an absolute value in dB (not a relative change). No bounds are implied."""

    track_id: TrackId
    value_db: float

    def __post_init__(self) -> None:
        require_instance(self.track_id, TrackId, "SetTrackGain.track_id")
        require_finite(self.value_db, "SetTrackGain.value_db")


@dataclass(frozen=True)
class Action:
    action_id: ActionId
    project_id: ProjectId
    expected_revision: Revision
    operation: SetTrackGain

    def __post_init__(self) -> None:
        require_instance(self.action_id, ActionId, "Action.action_id")
        require_instance(self.project_id, ProjectId, "Action.project_id")
        require_instance(self.expected_revision, Revision, "Action.expected_revision")
        require_instance(self.operation, SetTrackGain, "Action.operation")


class ExecutionStatus(Enum):
    NOT_STARTED = "not_started"
    FAILED = "failed"  # may leave temporary files; no cleanup is claimed
    CANDIDATE_READY = "candidate_ready"
    UNCERTAIN = "uncertain"  # the outcome cannot be established


@dataclass(frozen=True)
class ExecutionIssue:
    code: str
    message: str

    def __post_init__(self) -> None:
        require_text(self.code, "ExecutionIssue.code")
        require_text(self.message, "ExecutionIssue.message")


@dataclass(frozen=True)
class ExecutionReceipt:
    """Outcome of executing one action. Candidate artifacts are newly produced outputs only.

    ``superseded_artifact_ids`` explicitly names the before-state artifacts the candidates
    replace; replacement is never inferred. For the supported track-gain operation a
    CANDIDATE_READY receipt must supersede at least one artifact. Execution planning must list
    every affected derived artifact (e.g. groups and mixes containing an edited track); this
    record does not implement dependency invalidation. No status authorizes acceptance or
    another edit.
    """

    execution_id: ExecutionId
    action_id: ActionId
    project_id: ProjectId
    before_revision: Revision
    status: ExecutionStatus
    candidate_revision: Revision | None
    candidate_artifacts: tuple[AudioArtifact, ...]
    superseded_artifact_ids: tuple[ArtifactId, ...]
    issue: ExecutionIssue | None

    def __post_init__(self) -> None:
        require_instance(self.execution_id, ExecutionId, "ExecutionReceipt.execution_id")
        require_instance(self.action_id, ActionId, "ExecutionReceipt.action_id")
        require_instance(self.project_id, ProjectId, "ExecutionReceipt.project_id")
        require_instance(self.before_revision, Revision, "ExecutionReceipt.before_revision")
        require_instance(self.status, ExecutionStatus, "ExecutionReceipt.status")
        if self.candidate_revision is not None:
            require_instance(self.candidate_revision, Revision, "ExecutionReceipt.candidate_revision")
        _require_unique_artifacts(self.candidate_artifacts, "ExecutionReceipt.candidate_artifacts", nonempty=False)
        require_tuple_of(
            self.superseded_artifact_ids,
            ArtifactId,
            "ExecutionReceipt.superseded_artifact_ids",
            nonempty=False,
            distinct=True,
        )
        if self.issue is not None:
            require_instance(self.issue, ExecutionIssue, "ExecutionReceipt.issue")
        for artifact in self.candidate_artifacts:
            if artifact.project_id != self.project_id or artifact.revision != self.candidate_revision:
                raise ValueError(
                    f"candidate artifact {artifact.artifact_id} does not match project {self.project_id} "
                    f"and candidate revision {self.candidate_revision}"
                )

        status = self.status.name
        if self.status is ExecutionStatus.CANDIDATE_READY:
            if self.candidate_revision is None or self.candidate_revision == self.before_revision:
                raise ValueError("CANDIDATE_READY requires a candidate revision different from the before revision")
            if not self.candidate_artifacts:
                raise ValueError("CANDIDATE_READY requires candidate artifacts")
            if not self.superseded_artifact_ids:
                raise ValueError("CANDIDATE_READY requires explicit superseded artifact IDs")
            overlap = sorted(str(a) for a in set(self.superseded_artifact_ids) & self.candidate_artifact_ids)
            if overlap:
                raise ValueError(f"a candidate artifact cannot supersede itself: {', '.join(overlap)}")
            if self.issue is not None:
                raise ValueError("CANDIDATE_READY must not carry an issue")
            return
        if self.candidate_revision is not None or self.candidate_artifacts or self.superseded_artifact_ids:
            raise ValueError(f"{status} execution must not claim a candidate revision, artifacts or superseded IDs")
        if self.status in (ExecutionStatus.FAILED, ExecutionStatus.UNCERTAIN) and self.issue is None:
            raise ValueError(f"{status} execution requires an issue")

    @property
    def candidate_artifact_ids(self) -> frozenset[ArtifactId]:
        return frozenset(artifact.artifact_id for artifact in self.candidate_artifacts)


def validate_receipt_against_checkpoint(receipt: ExecutionReceipt, before: ArtifactCheckpoint) -> None:
    """Check that a receipt applies to the before state it claims to start from.

    Requires matching project and before revision, superseded IDs that are members of the
    before state, and candidate IDs disjoint from every before-state artifact ID (no reuse).
    """
    if receipt.project_id != before.project_id:
        raise ValueError(f"execution project {receipt.project_id} does not match checkpoint {before.project_id}")
    if receipt.before_revision != before.revision:
        raise ValueError(
            f"execution before revision {receipt.before_revision} does not match checkpoint revision {before.revision}"
        )
    unknown = sorted(str(a) for a in set(receipt.superseded_artifact_ids) - before.artifact_ids)
    if unknown:
        raise ValueError(f"superseded artifacts are not in the before checkpoint: {', '.join(unknown)}")
    reused = sorted(str(a) for a in receipt.candidate_artifact_ids & before.artifact_ids)
    if reused:
        raise ValueError(f"candidate artifact IDs reuse before-checkpoint IDs: {', '.join(reused)}")


def derive_candidate_checkpoint(
    previous: ArtifactCheckpoint, receipt: ExecutionReceipt, checkpoint_id: CheckpointId
) -> ArtifactCheckpoint:
    """Build the candidate state's membership: retained previous artifacts, then the new candidates.

    Retained artifacts are carried over unchanged (identity, hash, location and production
    revision), in their previous order, followed by the candidate artifacts in receipt order. The
    result represents the candidate revision, which must not reuse a revision visible in the
    previous membership (its state revision or a member's production revision; fixed baselines and
    history are not visible here). This constructs candidate membership only; it does not accept the
    correction and assumes no action target.
    """
    require_instance(checkpoint_id, CheckpointId, "checkpoint_id")
    if receipt.status is not ExecutionStatus.CANDIDATE_READY:
        raise ValueError(f"candidate derivation requires a CANDIDATE_READY execution, got {receipt.status.name}")
    validate_receipt_against_checkpoint(receipt, previous)
    if receipt.candidate_revision is None:  # unreachable: CANDIDATE_READY receipts always carry one
        raise ValueError("candidate derivation requires a candidate revision")
    if receipt.candidate_revision in previous.known_revisions:
        raise ValueError(
            f"candidate revision {receipt.candidate_revision} reuses a revision known in the previous membership"
        )
    superseded = set(receipt.superseded_artifact_ids)
    retained = tuple(artifact for artifact in previous.artifacts if artifact.artifact_id not in superseded)
    return ArtifactCheckpoint(
        checkpoint_id, previous.project_id, receipt.candidate_revision, retained + receipt.candidate_artifacts
    )


class RollbackStatus(Enum):
    VERIFIED = "verified"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class RollbackReceipt:
    """Result of restoring the saved before state after an execution.

    ``restored_state`` is required for VERIFIED and forbidden otherwise. It records restoration of the
    saved state, including its original snapshot timestamp, not a newly captured snapshot.
    """

    execution_id: ExecutionId
    checkpoint_id: CheckpointId
    status: RollbackStatus
    restored_state: ProjectState | None
    explanation: str

    def __post_init__(self) -> None:
        require_instance(self.execution_id, ExecutionId, "RollbackReceipt.execution_id")
        require_instance(self.checkpoint_id, CheckpointId, "RollbackReceipt.checkpoint_id")
        require_instance(self.status, RollbackStatus, "RollbackReceipt.status")
        if self.restored_state is not None:
            require_instance(self.restored_state, ProjectState, "RollbackReceipt.restored_state")
        require_text(self.explanation, "RollbackReceipt.explanation")
        if self.status is RollbackStatus.VERIFIED and self.restored_state is None:
            raise ValueError("VERIFIED rollback requires the restored complete state")
        if self.status is not RollbackStatus.VERIFIED and self.restored_state is not None:
            raise ValueError(f"{self.status.name} rollback must not claim a restored state")


def validate_rollback_references(receipt: RollbackReceipt, before_state: ProjectState) -> None:
    """Check checkpoint identity and, for VERIFIED, exact equality with the saved before state.

    Equality covers settings, audio membership and snapshot together, so audio-only or settings-only
    restoration cannot be verified. Matching metadata is a recorded claim, not external proof: an
    adapter must verify files and the current accepted-reference state before issuing VERIFIED.
    """
    require_instance(receipt, RollbackReceipt, "receipt")
    require_instance(before_state, ProjectState, "before_state")
    if receipt.checkpoint_id != before_state.audio.checkpoint_id:
        raise ValueError(
            f"rollback refers to checkpoint {receipt.checkpoint_id}, not {before_state.audio.checkpoint_id}"
        )
    if receipt.status is RollbackStatus.VERIFIED and receipt.restored_state != before_state:
        restored = receipt.restored_state
        differing = [
            part
            for part in ("snapshot", "processing", "audio")
            if restored is None or getattr(restored, part) != getattr(before_state, part)
        ]
        raise ValueError(
            f"verified rollback does not restore the saved before state exactly (differs in: {', '.join(differing)})"
        )
