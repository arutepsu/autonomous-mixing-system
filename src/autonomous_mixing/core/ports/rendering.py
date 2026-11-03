"""The rendering port and the structural check on its candidates."""

from typing import Protocol

from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.identifiers import ArtifactId
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.rendering import ProcessedCandidate, ProcessingOutcome, ProcessingPlan


class AudioProcessor(Protocol):
    """Renders a plan's fixed target input at the requested gain and the full mix from current stems.

    Returns a ``ProcessedCandidate`` or a typed ``ProcessingFailure``; callers check candidates with
    ``validate_processed_candidate``. It must not choose actions, read hidden evaluation data, accept
    audio or change policy. No implementation exists in the application layer.
    """

    def process(self, plan: ProcessingPlan) -> ProcessingOutcome: ...


def _check_output(artifact: AudioArtifact, plan: ProcessingPlan, expected_id: ArtifactId, kind: ArtifactKind) -> None:
    label = f"output {artifact.artifact_id}"
    if artifact.artifact_id != expected_id:
        raise ValueError(f"{label} must use the assigned candidate ID {expected_id}")
    if artifact.project_id != plan.project_id:
        raise ValueError(f"{label} belongs to project {artifact.project_id}, not {plan.project_id}")
    if artifact.revision != plan.candidate.revision:
        raise ValueError(
            f"{label} has revision {artifact.revision}, not the candidate revision {plan.candidate.revision}"
        )
    if artifact.kind is not kind:
        raise ValueError(f"{label} must have kind {kind.name}")
    reference = plan.fixed_target_input
    expected = (plan.render_spec.sample_rate_hz, plan.render_spec.channel_count, reference.frame_count)
    actual = (artifact.sample_rate_hz, artifact.channel_count, artifact.frame_count)
    if actual != expected or artifact.timeline_start_seconds != reference.timeline_start_seconds:
        raise ValueError(f"{label} dimensions differ from the plan's input and render settings")
    if artifact.render_spec != plan.render_spec.ref:
        raise ValueError(f"{label} render spec {artifact.render_spec} differs from the plan's {plan.render_spec.ref}")


def validate_processed_candidate(plan: ProcessingPlan, response: ProcessedCandidate) -> None:
    """Check a processor's outputs and evidence against the plan (structure only).

    Does not verify files, hashes, sample values, workspace membership or path/alias safety, and
    assembles no receipt or state; evidence flags are adapter claims. Location checks are lexical and
    cover the plan's inputs only (the plan carries the current mix's ID, not its location).
    """
    require_instance(plan, ProcessingPlan, "plan")
    require_instance(response, ProcessedCandidate, "response")
    target, mix = response.target_artifact, response.mix_artifact
    _check_output(target, plan, plan.candidate.target_artifact_id, ArtifactKind.STEM)
    _check_output(mix, plan, plan.candidate.mix_artifact_id, ArtifactKind.MIX)
    if target.source_track_ids != (plan.target_track_id,):
        raise ValueError(f"target output source tracks must be exactly ({plan.target_track_id},)")
    if set(mix.source_track_ids) != set(plan.recipe.track_ids):
        raise ValueError("mix output source tracks must equal the recipe contributors")

    evidence = {record.artifact_id: record for record in response.evidence}
    if set(evidence) != {target.artifact_id, mix.artifact_id}:
        raise ValueError("evidence must contain exactly one record per output artifact")
    for record in evidence.values():
        if not (record.input_hashes_verified and record.finite_samples):
            raise ValueError(f"evidence for {record.artifact_id} does not claim verified inputs and finite samples")
    if evidence[target.artifact_id].processor != evidence[mix.artifact_id].processor:
        raise ValueError("both outputs must cite the same processor")
    for record in evidence.values():
        if record.processing_spec != plan.processing_spec:  # complete immutable value, not only its reference
            raise ValueError(f"evidence for {record.artifact_id} records a processing spec different from the plan's")
    if set(evidence[target.artifact_id].verified_input_ids) != {plan.fixed_target_input.artifact_id}:
        raise ValueError("target evidence must cite exactly the fixed target input")
    non_target = {s.artifact_id for s in plan.current_stems if s.artifact_id != plan.target_current_artifact_id}
    if set(evidence[mix.artifact_id].verified_input_ids) != {target.artifact_id} | non_target:
        raise ValueError("mix evidence must cite exactly the new target stem and the current non-target stems")

    input_locations = {plan.fixed_target_input.location} | {stem.location for stem in plan.current_stems}
    if target.location == mix.location or {target.location, mix.location} & input_locations:
        raise ValueError("output locations must differ from each other and from every input location (lexical check)")
