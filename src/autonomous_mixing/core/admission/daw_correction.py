"""DAW preflight (coordinator-integration plan, Increment 2; ADR-036): the DAW counterpart of ``preflight_execution``.

Pure. It reruns admission, checks the logical workspace and the new candidate identities, and builds the
``DawCorrectionPlan`` whose export outputs replace exactly the affected set of the target: the target's current stem,
one current stem per declared dependent, and the single current master mix containing the target. A group containing
the target, or a second mix containing it, is refused (ADR-035, G5). Each output is declared with the replaced
artifact's native channel count, and every replaced artifact must itself be an export with the session's render spec,
rate, timeline start and frame count (``INCOMPATIBLE_AUDIO`` otherwise; Increment 3). An export with other dimensions
is refused at import (``DIMENSIONS``). Nothing is folded down, resampled or requantized. File names are derived
from output order only (``01-stem.wav`` ... ``NN-master.wav``), so arbitrary track identifiers never reach a file
name. Passing permits no write: the capability gate still decides against a fresh inspection.
"""

from autonomous_mixing.core.admission.admission import admit_action
from autonomous_mixing.core.records.admission import (
    ActionAdmissionPolicy,
    ActionAdmissionRequest,
    PreflightCode,
    PreflightRejected,
    RejectedAction,
)
from autonomous_mixing.core.records.artifacts import ArtifactKind
from autonomous_mixing.core.records.daw_correction import DawCorrectionPlan, DawPlanReady, DawSessionConfig
from autonomous_mixing.core.records.daw_export import DawExportOutput
from autonomous_mixing.core.records.decision import Decision, Situation
from autonomous_mixing.core.records.identifiers import ArtifactId, ExecutionId, Revision
from autonomous_mixing.core.records.invariants import require_instance, require_tuple_of
from autonomous_mixing.core.records.rendering import ExecutionWorkspace


def preflight_daw_correction(
    *,
    execution_id: ExecutionId,
    command_id: str,
    situation: Situation,
    decision: Decision,
    policy: ActionAdmissionPolicy,
    session: DawSessionConfig,
    workspace: ExecutionWorkspace,
    candidate_revision: Revision,
    output_ids: tuple[ArtifactId, ...],
) -> DawPlanReady | PreflightRejected:
    """Check one DAW correction's metadata in a fixed order; the first failure wins (wiring errors raise).

    1. rerun admission; 2. workspace identity; 3. a DAW parameter state for the bound target; 4. new candidate
    revision and output IDs; 5. exactly one current stem per affected track and a master mix containing the target;
    6. the plan validates against the before state (``daw_export_coverage_problem``: no group of the target, no second
    mix containing it).
    """
    require_instance(session, DawSessionConfig, "session")
    require_tuple_of(output_ids, ArtifactId, "output_ids", nonempty=True, distinct=False)

    def reject(code: PreflightCode, explanation: str) -> PreflightRejected:
        return PreflightRejected(execution_id, code, explanation, None)

    admission = admit_action(ActionAdmissionRequest(situation, decision, policy))
    if isinstance(admission, RejectedAction):
        return PreflightRejected(execution_id, PreflightCode.ADMISSION_REJECTED,
                                 f"admission rejected the decision: {admission.code.name}", admission)  # fmt: skip
    action, state = admission.action, situation.state
    target = action.operation.track_id
    if workspace.execution_id != execution_id or workspace.root_key != session.export_root_key:
        return reject(PreflightCode.INVALID_WORKSPACE, "the export workspace is not this execution's DAW workspace")
    parameter = None if state.daw is None else state.daw.track(target)
    if target != session.binding.track_id or parameter is None or parameter.binding != session.binding:
        return reject(PreflightCode.MISSING_GAIN_STATE, f"track {target} has no DAW parameter state for this session")
    if candidate_revision in state.known_revisions or len(set(output_ids)) != len(output_ids):
        return reject(PreflightCode.INVALID_CANDIDATE_IDENTITY, "the candidate revision or output IDs are not new")
    if set(output_ids) & state.audio.artifact_ids:
        return reject(PreflightCode.INVALID_CANDIDATE_IDENTITY, "an output ID reuses a current artifact ID")
    tracks = (target, *session.routing.dependent_track_ids)
    if len(output_ids) != len(tracks) + 1:
        raise ValueError("preflight_daw_correction needs one output ID per affected stem plus one for the master")
    stems = []
    for track in tracks:
        found = [a for a in state.audio.artifacts if a.kind is ArtifactKind.STEM and a.source_track_ids == (track,)]
        if len(found) != 1:
            return reject(PreflightCode.UNSUPPORTED_DEPENDENCY, f"track {track} has no single current stem to replace")
        stems.append(found[0])
    mixes = [a for a in state.audio.artifacts if a.kind is ArtifactKind.MIX and target in a.source_track_ids]
    if not mixes:  # defensive: a valid RunRequest already requires a current mix of every track
        return reject(PreflightCode.UNSUPPORTED_DEPENDENCY, "no current master mix contains the target")
    # A group of the target, or a second mix containing it, is refused by the plan's coverage rule below.
    replaced = (*stems, mixes[0])
    # Increment 3: native export channels are kept and nothing is converted, so every replaced artifact must itself
    # be a native export with the session's settings; before, initial and candidate stay like-for-like.
    timeline = session.export_timeline
    for old in replaced:
        if (old.render_spec, old.sample_rate_hz, old.timeline_start_seconds, old.frame_count) != (
            session.render_spec,
            timeline.sample_rate_hz,
            timeline.start_seconds,
            timeline.frame_count,
        ):
            return reject(PreflightCode.INCOMPATIBLE_AUDIO,
                          f"{old.artifact_id} is not a native export with the session's export settings")  # fmt: skip
    outputs = tuple(
        DawExportOutput(new_id, old.artifact_id, old.kind, old.source_track_ids, old.channel_count,
                        f"{index:02d}-{'master' if old.kind is ArtifactKind.MIX else 'stem'}.wav")
        for index, (new_id, old) in enumerate(zip(output_ids, replaced, strict=True), 1)
    )  # fmt: skip
    try:
        plan = DawCorrectionPlan(execution_id, command_id, state.project_id, state.revision, candidate_revision, target,
                                 action.operation.value_db, session.binding, session.profile, session.routing, outputs,
                                 session.export_timeline, session.export_format, session.render_spec, workspace)  # fmt: skip
        plan.validate_against(state)
    except ValueError as error:
        return reject(PreflightCode.UNSUPPORTED_DEPENDENCY, f"the DAW plan is not valid for this state: {error}")
    return DawPlanReady(plan)
