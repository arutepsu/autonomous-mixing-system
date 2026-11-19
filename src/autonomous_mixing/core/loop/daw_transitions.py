"""DAW execution receipt and candidate state derivation (coordinator-integration plan, Increment 1; ADR-035).

Pure functions beside the offline ``transitions``; no loop stage calls them yet. They turn one ``DawImportVerified``
(ADR-033) into the ``CANDIDATE_READY`` receipt and the candidate ``ProjectState`` for the existing verification and
acceptance flow. Only a verified import can do so: a confirmed fader write alone never produces a receipt.

The candidate keeps ``processing`` (fixed-input ``TrackGainState``, ADR-019) unchanged apart from its revision, and
records the change in its DAW parameter state (ADR-034 Option A): the selected application gain and the anchored
confirmed fader reading. Supersession is the import's explicit replacement mapping, validated against the affected
set of the target (``daw_export_coverage_problem``), never inferred.

Every function takes the journaled ``DawApplyRequest`` rather than a free action (review fix 2026-11-19): the action,
its execution, command ID and resolved target come from the request that was actually dispatched, so another action
on the same track cannot be substituted (``daw_apply_export_problem``). The apply must also have been resolved from
the selected state's DAW parameter state.
"""

from datetime import datetime

from autonomous_mixing.core.records.actions import (
    ExecutionReceipt,
    ExecutionStatus,
    derive_candidate_checkpoint,
    validate_receipt_against_checkpoint,
)
from autonomous_mixing.core.records.daw_control import DawApplyRequest
from autonomous_mixing.core.records.daw_correction import daw_apply_export_problem
from autonomous_mixing.core.records.daw_export import DawImportVerified, daw_export_coverage_problem
from autonomous_mixing.core.records.identifiers import CheckpointId
from autonomous_mixing.core.records.invariants import require_instance, require_utc
from autonomous_mixing.core.records.project import (
    DawParameterState,
    DawTrackParameter,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
)


def _check_context(previous: ProjectState, verified: DawImportVerified, apply: DawApplyRequest) -> None:
    require_instance(previous, ProjectState, "previous")
    require_instance(verified, DawImportVerified, "verified")
    require_instance(apply, DawApplyRequest, "apply")
    request, action = verified.request, apply.admitted.action
    problem = daw_apply_export_problem(apply, request)
    if problem is not None:
        raise ValueError(problem)
    if (action.project_id, action.expected_revision) != (previous.project_id, previous.revision):
        raise ValueError("the action does not act on the previous state")
    if action.operation.track_id != request.binding.track_id:
        raise ValueError("the action targets another track than the export's binding")
    if previous.audio != request.before_checkpoint:
        raise ValueError("the export's before checkpoint is not the previous state's audio")
    if previous.daw is None:
        raise ValueError("the previous state has no DAW parameter state")
    parameter = previous.daw.track(action.operation.track_id)
    if parameter is None or parameter.binding != request.binding:
        raise ValueError("the previous state's DAW parameter state does not hold the export's binding")
    state = apply.resolution.state
    if (state.revision, state.selected_gain_db, state.confirmed_fader) != (
        previous.revision,
        parameter.selected_gain_db,
        parameter.confirmed_fader,
    ):
        raise ValueError("the apply was not resolved from the selected state's DAW parameter state")


def daw_execution_receipt(
    previous: ProjectState, verified: DawImportVerified, apply: DawApplyRequest
) -> ExecutionReceipt:
    """The ``CANDIDATE_READY`` receipt of a verified DAW export of ``apply``; validated before it is returned."""
    _check_context(previous, verified, apply)
    request, action = verified.request, apply.admitted.action
    receipt = ExecutionReceipt(
        request.execution_id,
        action.action_id,
        previous.project_id,
        previous.revision,
        ExecutionStatus.CANDIDATE_READY,
        request.candidate_revision,
        verified.candidate_artifacts,
        tuple(replaced for _, replaced in verified.replacements),
        None,
    )
    validate_daw_execution(previous, receipt, apply, verified)
    return receipt


def validate_daw_execution(
    previous: ProjectState, receipt: ExecutionReceipt, apply: DawApplyRequest, verified: DawImportVerified
) -> None:
    """A DAW receipt must state exactly the verified import: its candidates, its replacements and its identities.

    Replaces ``validate_gain_execution`` for the DAW path, whose fixed-input rule would call a declared dependent's
    stem unrelated. Raises ``ValueError``.
    """
    _check_context(previous, verified, apply)
    require_instance(receipt, ExecutionReceipt, "receipt")
    request, action = verified.request, apply.admitted.action
    if receipt.status is not ExecutionStatus.CANDIDATE_READY:
        raise ValueError("a DAW execution receipt is CANDIDATE_READY only after a verified import")
    if (receipt.execution_id, receipt.action_id, receipt.candidate_revision) != (
        request.execution_id,
        action.action_id,
        request.candidate_revision,
    ):
        raise ValueError("the receipt's execution, action or candidate revision differs from the verified import")
    validate_receipt_against_checkpoint(receipt, previous.audio)
    if receipt.candidate_artifacts != verified.candidate_artifacts:
        raise ValueError("the receipt's candidates are not exactly the verified import's artifacts")
    replaced = tuple(old for _, old in verified.replacements)
    if receipt.superseded_artifact_ids != replaced:
        raise ValueError("the receipt's superseded IDs are not exactly the import's explicit replacements")
    problem = daw_export_coverage_problem(previous.audio, request.binding.track_id, request.routing, request.outputs)
    if problem is not None:
        raise ValueError(f"the verified import does not cover the target's affected set: {problem}")


def derive_daw_candidate_state(
    previous: ProjectState,
    verified: DawImportVerified,
    apply: DawApplyRequest,
    checkpoint_id: CheckpointId,
    captured_at: datetime,
) -> ProjectState:
    """The candidate state of a verified DAW export: new audio membership and DAW parameter state, same gains."""
    require_instance(checkpoint_id, CheckpointId, "checkpoint_id")
    require_utc(captured_at, "captured_at")
    receipt = daw_execution_receipt(previous, verified, apply)
    action = apply.admitted.action
    if receipt.candidate_revision in previous.known_revisions:
        raise ValueError(f"candidate revision {receipt.candidate_revision} reuses a locally known revision")
    audio = derive_candidate_checkpoint(previous.audio, receipt, checkpoint_id)
    revision, target = audio.revision, action.operation.track_id
    snapshot = ProjectSnapshot(previous.project_id, revision, captured_at, previous.snapshot.tracks)
    processing = ProjectProcessingState(previous.project_id, revision, previous.processing.track_gains)
    tracks = tuple(
        DawTrackParameter(track.binding, action.operation.value_db, verified.request.anchor.reading)
        if track.track_id == target
        else track
        for track in previous.daw.tracks
    )
    return ProjectState(snapshot, processing, audio, DawParameterState(previous.project_id, revision, tracks))
