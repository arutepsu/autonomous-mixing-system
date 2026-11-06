"""Evidence matching for verification: find a criterion's declared measurement in one state's observations.

Pure and deterministic; it reads only the supplied records. It establishes that declared settings,
alignment references, sources and metadata agree, not physical alignment, file integrity, project or
run binding across states, measurement-method validity or threshold justification. No condition or
verdict is calculated here.
"""

from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.observation import MeasuredScalar, Unavailable, validate_observation_sources
from autonomous_mixing.core.records.verification_evidence import (
    EvidenceMatch,
    EvidenceUnavailable,
    EvidenceUnavailableCode,
    MatchedEvidence,
    MeasurementDefinition,
    StateEvidence,
)

Code = EvidenceUnavailableCode


def match_evidence(definition: MeasurementDefinition, evidence: StateEvidence) -> EvidenceMatch:
    """Return the observation matching ``definition`` in ``evidence``, or why none is usable.

    Order, first failure wins: exact metric and scope (none: MISSING; more than one: AMBIGUOUS, before any
    other property is considered); analysis spec, analyzer and declared alignment (SETTINGS_MISMATCH);
    source membership and coverage against the bundle state's checkpoint via ``validate_observation_sources``
    (its ``ValueError`` only: INVALID_SOURCE; other exceptions propagate); source count, kinds and ordered
    contributors (SUBJECT_MISMATCH); render version, sample rate, channels, timeline start and frame count
    (SOURCE_METADATA_MISMATCH); an ``Unavailable`` result (UNAVAILABLE_RESULT); the scalar unit
    (UNIT_MISMATCH). Artifact IDs, paths, hashes and production revisions are not compared, so retained
    older-production members and fresh candidate IDs can match.
    """
    require_instance(definition, MeasurementDefinition, "definition")
    require_instance(evidence, StateEvidence, "evidence")
    selector = definition.selector

    matches = [o for o in evidence.observations if o.metric == selector.metric and o.scope == selector.scope]
    if not matches:
        return EvidenceUnavailable(Code.MISSING, f"no observation of {selector.metric.name}@{selector.metric.version} "
                                                 "with the exact selector scope")  # fmt: skip
    if len(matches) > 1:
        ids = ", ".join(str(o.observation_id) for o in matches)
        return EvidenceUnavailable(Code.AMBIGUOUS, f"several observations match the metric and scope exactly: {ids}")
    observation = matches[0]

    settings = []
    if observation.analysis_spec != definition.analysis_spec:
        settings.append("analysis spec")
    if observation.analyzer != definition.analyzer:
        settings.append("analyzer")
    if evidence.alignment != definition.alignment:
        settings.append("declared alignment")
    if settings:
        return EvidenceUnavailable(
            Code.SETTINGS_MISMATCH, f"observation {observation.observation_id} differs in: {', '.join(settings)}"
        )

    state = evidence.state
    checkpoint = state.audio
    try:
        validate_observation_sources(observation, checkpoint.artifacts, checkpoint, state.snapshot)
    except ValueError as error:
        return EvidenceUnavailable(Code.INVALID_SOURCE, f"observation {observation.observation_id}: {error}")
    sources = [checkpoint.artifact_for(artifact_id) for artifact_id in observation.artifact_ids]

    required = definition.sources
    if len(sources) != len(required) or any(
        (artifact.kind, artifact.source_track_ids) != (requirement.kind, requirement.source_track_ids)
        for artifact, requirement in zip(sources, required, strict=False)
    ):
        return EvidenceUnavailable(
            Code.SUBJECT_MISMATCH,
            f"observation {observation.observation_id} sources do not match the required ordered subjects",
        )
    for position, (artifact, requirement) in enumerate(zip(sources, required, strict=True), start=1):
        declared = (
            requirement.render_spec,
            requirement.sample_rate_hz,
            requirement.channel_count,
            requirement.timeline_start_seconds,
            requirement.frame_count,
        )
        actual = (
            artifact.render_spec,
            artifact.sample_rate_hz,
            artifact.channel_count,
            artifact.timeline_start_seconds,
            artifact.frame_count,
        )
        if actual != declared:
            return EvidenceUnavailable(
                Code.SOURCE_METADATA_MISMATCH,
                f"observation {observation.observation_id} source {position} metadata differs from the definition",
            )

    result = observation.result
    if isinstance(result, Unavailable):
        return EvidenceUnavailable(
            Code.UNAVAILABLE_RESULT, f"observation {observation.observation_id} is unavailable: {result.reason}"
        )
    require_instance(result, MeasuredScalar, "observation.result")
    if result.unit != definition.unit:
        return EvidenceUnavailable(
            Code.UNIT_MISMATCH,
            f"observation {observation.observation_id} unit {result.unit!r} is not {definition.unit!r}",
        )
    return MatchedEvidence(observation)
