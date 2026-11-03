"""Deterministic metadata preflight for one bounded gain/full-mix execution.

``preflight_execution`` is pure: it reads only the request records, invokes no processor, touches
no file and allocates nothing. Its success result is deliberately named ``MetadataReady``: it does
not prove file integrity or hashes, sample alignment, authorization against external state that may
have changed since, workspace safety or collision freedom, or exclusive access. A future execution
service must establish those immediately before processing and recheck state before publication.
"""

from autonomous_mixing.application.admission import ActionAdmissionRequest, RejectedAction, admit_action
from autonomous_mixing.application.execution.processing import ProcessingPlan
from autonomous_mixing.application.execution.requests import ExecutionRequest

# Moved definitions stay reachable at this legacy path until migration step M4 (explicit aliases).
from autonomous_mixing.core.records.admission import MetadataReady as MetadataReady
from autonomous_mixing.core.records.admission import PreflightCode as PreflightCode
from autonomous_mixing.core.records.admission import PreflightRejected as PreflightRejected
from autonomous_mixing.core.records.admission import PreflightResult as PreflightResult
from autonomous_mixing.domain.project import ArtifactKind, AudioArtifact
from autonomous_mixing.domain.shared import require_instance


def _dimensions(artifact: AudioArtifact) -> tuple[int, int, int, float]:
    return (artifact.sample_rate_hz, artifact.channel_count, artifact.frame_count, artifact.timeline_start_seconds)


def preflight_execution(request: ExecutionRequest) -> PreflightResult:
    """Check an execution request's metadata in a fixed order; the first failure wins.

    1. rerun admission now (an earlier admission is not a capability token); 2. workspace identity;
    3. new candidate revision and IDs disjoint from current audio and fixed baselines; 4. recipe equals
    the snapshot inventory and bindings cover it exactly with matching STEM members; 5. selected mix is a
    MIX member with exactly the recipe contributors; 6. explicit target gain state; 7. identical
    dimensions for mapped stems, fixed target input and selected mix, non-zero length and the render
    rate and channels; 8. no undeclared current artifact depends on the target.
    """
    require_instance(request, ExecutionRequest, "request")
    execution_id = request.execution_id

    def reject(code: PreflightCode, explanation: str) -> PreflightRejected:
        return PreflightRejected(execution_id, code, explanation, None)

    # 1. admission, rerun against the current decision request
    admission = admit_action(ActionAdmissionRequest(request.decision_request, request.decision, request.policy))
    if isinstance(admission, RejectedAction):
        return PreflightRejected(
            execution_id,
            PreflightCode.ADMISSION_REJECTED,
            f"admission rejected the decision: {admission.code.name}",
            admission,
        )
    action = admission.action
    target = action.operation.track_id
    state = request.decision_request.state
    audio = state.audio

    # 2. logical workspace assignment
    if request.workspace.execution_id != execution_id:
        return reject(
            PreflightCode.INVALID_WORKSPACE, f"workspace is assigned to execution {request.workspace.execution_id}"
        )

    # 3. candidate identity
    candidate = request.candidate
    # Local collision check: current state, audio members' and fixed baselines' production revisions.
    if candidate.revision in state.known_revisions:
        return reject(
            PreflightCode.INVALID_CANDIDATE_IDENTITY,
            f"candidate revision {candidate.revision} reuses a locally known revision",
        )
    baseline_ids = {gain.input_artifact.artifact_id for gain in state.processing.track_gains}
    reused = sorted(
        str(a) for a in {candidate.target_artifact_id, candidate.mix_artifact_id} & (audio.artifact_ids | baseline_ids)
    )
    if reused:
        return reject(
            PreflightCode.INVALID_CANDIDATE_IDENTITY,
            f"candidate artifact IDs reuse current audio or fixed baseline IDs: {', '.join(reused)}",
        )

    # 4. recipe and stem bindings
    recipe = request.mix_recipe
    snapshot_tracks = {track.track_id for track in state.snapshot.tracks}
    if set(recipe.track_ids) != snapshot_tracks:
        return reject(
            PreflightCode.INVALID_RECIPE_BINDINGS, "the full-mix recipe must contain exactly the snapshot's tracks"
        )
    bound = {binding.track_id: binding.artifact_id for binding in request.stem_bindings}
    if set(bound) != set(recipe.track_ids):
        return reject(PreflightCode.INVALID_RECIPE_BINDINGS, "stem bindings must cover exactly the recipe contributors")
    current_stems = []
    for track in recipe.track_ids:
        member = audio.artifact_for(bound[track])
        if member is None:
            return reject(
                PreflightCode.INVALID_RECIPE_BINDINGS, f"bound artifact {bound[track]} is not a current audio member"
            )
        if member.kind is not ArtifactKind.STEM or member.source_track_ids != (track,):
            return reject(
                PreflightCode.INVALID_RECIPE_BINDINGS,
                f"bound artifact {member.artifact_id} is not a STEM of exactly track {track}",
            )
        current_stems.append(member)

    # 5. selected current full mix
    mix = audio.artifact_for(request.current_mix_artifact_id)
    if mix is None or mix.kind is not ArtifactKind.MIX:
        return reject(PreflightCode.INVALID_MIX, f"{request.current_mix_artifact_id} is not a current MIX member")
    if set(mix.source_track_ids) != set(recipe.track_ids):
        return reject(
            PreflightCode.INVALID_MIX, f"mix {mix.artifact_id} does not contain exactly the recipe contributors"
        )

    # 6. explicit gain state for the target; never a default
    target_gain = next((gain for gain in state.processing.track_gains if gain.track_id == target), None)
    if target_gain is None:
        return reject(PreflightCode.MISSING_GAIN_STATE, f"track {target} has no explicit gain state")
    fixed_input = target_gain.input_artifact

    # 7. identical metadata dimensions (not proof of sample alignment)
    involved = (*current_stems, fixed_input, mix)
    if len({_dimensions(artifact) for artifact in involved}) != 1:
        return reject(
            PreflightCode.INCOMPATIBLE_AUDIO,
            "stems, fixed target input and mix must share sample rate, channels, frame count and timeline start",
        )
    rate, channels, frames, _start = _dimensions(mix)
    if frames == 0:
        return reject(PreflightCode.INCOMPATIBLE_AUDIO, "input audio has zero frames")
    if (rate, channels) != (request.render_spec.sample_rate_hz, request.render_spec.channel_count):
        return reject(
            PreflightCode.INCOMPATIBLE_AUDIO, "input sample rate or channel count differs from the render spec"
        )

    # 8. every current artifact depending on the target must be one of the declared replacements
    target_stem = current_stems[recipe.track_ids.index(target)]
    declared = {target_stem.artifact_id, mix.artifact_id}
    undeclared = sorted(
        str(member.artifact_id)
        for member in audio.artifacts
        if target in member.source_track_ids and member.artifact_id not in declared
    )
    if undeclared:
        return reject(
            PreflightCode.UNSUPPORTED_DEPENDENCY,
            f"undeclared artifacts depend on track {target}: {', '.join(undeclared)}",
        )

    return MetadataReady(
        ProcessingPlan(
            execution_id=execution_id,
            project_id=state.project_id,
            before_revision=state.revision,
            candidate=candidate,
            target_track_id=target,
            fixed_target_input=fixed_input,
            requested_gain_db=action.operation.value_db,
            current_stems=tuple(current_stems),
            target_current_artifact_id=target_stem.artifact_id,
            current_mix_artifact_id=mix.artifact_id,
            recipe=recipe,
            render_spec=request.render_spec,
            processing_spec=request.processing_spec,
            workspace=request.workspace,
        )
    )
