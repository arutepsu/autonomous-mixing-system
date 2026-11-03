"""Gain-execution validation and derivation of the candidate project state."""

from collections import Counter
from datetime import datetime

from autonomous_mixing.core.records.actions import (
    Action,
    ExecutionReceipt,
    ExecutionStatus,
    derive_candidate_checkpoint,
    validate_receipt_against_checkpoint,
)
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.identifiers import CheckpointId, TrackId
from autonomous_mixing.core.records.invariants import require_instance, require_utc
from autonomous_mixing.core.records.project import (
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    TrackGainState,
)


def validate_gain_execution(previous: ProjectState, receipt: ExecutionReceipt, action: Action) -> None:
    """Check that a CANDIDATE_READY gain execution replaces exactly the artifacts its target affects.

    Requires matching action, receipt and state identities (action ID, project, expected/before
    revision), an explicit gain state and at least one current STEM of exactly the target. The receipt
    must supersede every current artifact whose sources include the target and nothing else, and its
    candidates must replace them one-for-one by kind and contributing-track set (multiplicity kept;
    contributor order is provenance, not summation order). The receipt still declares the IDs
    explicitly; this validates that declaration and infers nothing. It is metadata consistency only:
    it does not show that a processor supports groups or rendered any content correctly.
    """
    require_instance(previous, ProjectState, "previous")
    require_instance(receipt, ExecutionReceipt, "receipt")
    require_instance(action, Action, "action")
    if receipt.status is not ExecutionStatus.CANDIDATE_READY:
        raise ValueError(f"gain execution check requires a CANDIDATE_READY execution, got {receipt.status.name}")
    if receipt.action_id != action.action_id:
        raise ValueError(f"receipt is for action {receipt.action_id}, not {action.action_id}")
    if action.project_id != previous.project_id:
        raise ValueError(f"action targets project {action.project_id}, previous state is {previous.project_id}")
    if action.expected_revision != previous.revision:
        raise ValueError(f"action expects revision {action.expected_revision}, previous state is {previous.revision}")
    # Receipt project, before revision, superseded membership and no candidate-ID reuse.
    validate_receipt_against_checkpoint(receipt, previous.audio)

    target = action.operation.track_id
    if not any(gain.track_id == target for gain in previous.processing.track_gains):
        raise ValueError(f"track {target} has no explicit gain state; no baseline is assumed")
    affected = [artifact for artifact in previous.audio.artifacts if target in artifact.source_track_ids]
    if not any(a.kind is ArtifactKind.STEM and a.source_track_ids == (target,) for a in affected):
        raise ValueError(f"the before state has no current STEM of exactly track {target}")

    affected_ids = {artifact.artifact_id for artifact in affected}
    superseded = set(receipt.superseded_artifact_ids)
    stale = sorted(str(a) for a in affected_ids - superseded)
    if stale:
        raise ValueError(f"receipt does not supersede artifacts depending on track {target}: {', '.join(stale)}")
    unrelated = sorted(str(a) for a in superseded - affected_ids)
    if unrelated:
        raise ValueError(f"receipt supersedes artifacts unrelated to track {target}: {', '.join(unrelated)}")

    def shape(artifact: AudioArtifact) -> tuple[ArtifactKind, frozenset[TrackId]]:
        return artifact.kind, frozenset(artifact.source_track_ids)

    if Counter(map(shape, affected)) != Counter(map(shape, receipt.candidate_artifacts)):
        raise ValueError(
            "candidate artifacts must replace the superseded artifacts one-for-one by kind and contributing tracks"
        )


def derive_candidate_project_state(
    previous: ProjectState,
    receipt: ExecutionReceipt,
    action: Action,
    checkpoint_id: CheckpointId,
    captured_at: datetime,
) -> ProjectState:
    """Assemble the complete candidate state claimed by an executed gain action and its receipt.

    First runs ``validate_gain_execution`` and rejects a candidate revision that reuses any locally
    known revision (state, audio members, fixed baselines). The snapshot keeps the previous track
    inventory and order at the receipt's candidate revision and the supplied UTC capture time. Audio
    membership comes from ``derive_candidate_checkpoint``. Processing replaces only the target's
    ``gain_db`` with the requested absolute value; fixed input artifacts, non-target settings and their
    order are carried over unchanged. No baseline or zero gain is invented. This does not render,
    accept or compute deltas; an executor must later verify the actual outputs.
    """
    require_instance(checkpoint_id, CheckpointId, "checkpoint_id")
    require_utc(captured_at, "captured_at")
    validate_gain_execution(previous, receipt, action)
    if receipt.candidate_revision in previous.known_revisions:
        raise ValueError(f"candidate revision {receipt.candidate_revision} reuses a locally known revision")

    audio = derive_candidate_checkpoint(previous.audio, receipt, checkpoint_id)
    revision = audio.revision
    target = action.operation.track_id
    snapshot = ProjectSnapshot(previous.project_id, revision, captured_at, previous.snapshot.tracks)
    gains = tuple(
        TrackGainState(gain.track_id, gain.input_artifact, action.operation.value_db)
        if gain.track_id == target
        else gain
        for gain in previous.processing.track_gains
    )
    return ProjectState(snapshot, ProjectProcessingState(previous.project_id, revision, gains), audio)
