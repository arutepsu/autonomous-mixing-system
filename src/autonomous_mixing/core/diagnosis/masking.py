"""Deterministic P1 readiness, not a qualified detector or an all-clear.

No file, provider, array, threshold or producer preference is consumed. Source matching mirrors R1's seven-field
SourceRequirement predicate; a later revision is not a selection heuristic. Alignment remains a declaration.
"""

from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource
from autonomous_mixing.core.records.coordinator_runtime import DiagnosisRequest
from autonomous_mixing.core.records.diagnosis import (
    DiagnosisCoverage,
    DiagnosisCoverageStatus,
    DiagnosisReport,
    ProblemFamily,
)
from autonomous_mixing.core.records.identifiers import DiagnosisCoverageId
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.masking_diagnosis import MaskingDiagnosisJob, MaskingDiagnosisPolicy
from autonomous_mixing.core.records.observation import Unavailable


def _matches_source(artifact, source) -> bool:
    """Exact current-member subject/geometry matching; IDs/hash/revision are not SourceRequirement fields."""
    return (
        artifact.kind is source.kind
        and artifact.source_track_ids == source.source_track_ids
        and artifact.render_spec == source.render_spec
        and artifact.sample_rate_hz == source.sample_rate_hz
        and artifact.channel_count == source.channel_count
        and artifact.timeline_start_seconds == source.timeline_start_seconds
        and artifact.frame_count == source.frame_count
    )


def _job_readiness(request: DiagnosisRequest, job: MaskingDiagnosisJob) -> str:
    support = job.support
    label = f"{support.pair.masker} -> {support.pair.target}; region={support.region.name}; support={support.digest}"
    if support.cell_count == 0:
        label += "; empty geometric support"
    expected_ids = []
    for position, source in enumerate(job.measurements[0].sources):
        matches = [a for a in request.state.audio.artifacts if _matches_source(a, source)]
        if len(matches) != 1:
            status = "missing_source" if not matches else "ambiguous_source"
            return f"{label}: {status} at ordered source {position}"
        expected_ids.append(matches[0].artifact_id)
    findings = []
    for definition in job.measurements:
        metric, scope = definition.selector.metric, definition.selector.scope
        observations = [o for o in request.observations if o.metric == metric and o.scope == scope]
        if len(observations) > 1:
            status = "ambiguous_observation"
        elif not observations:
            status = "mismatched" if any(o.metric.name == metric.name for o in request.observations) else "missing"
        else:
            observation = observations[0]
            if (observation.analysis_spec, observation.analyzer) != (definition.analysis_spec, definition.analyzer):
                status = "settings_mismatch"
            elif observation.artifact_ids != tuple(expected_ids):
                status = "source_mismatch"
            elif isinstance(observation.result, Unavailable):
                status = f"unavailable ({observation.result.reason})"
            elif observation.result.unit != definition.unit:
                status = "unit_mismatch"
            else:
                status = f"present ({observation.observation_id}; bound scalar only)"
        findings.append(f"{metric.name}@{metric.version}: {status}")
    return f"{label}: " + "; ".join(findings)


def diagnose_masking(
    request: DiagnosisRequest, *, policy: MaskingDiagnosisPolicy, identities: IdentitySource
) -> DiagnosisReport:
    """Validate declared readiness, always INCOMPLETE for uncalibrated P1 and no candidates (R2A).

    Invalid job/request scope and invalid/duplicate identity tokens are wiring errors. Missing, ambiguous or
    mismatched evidence is a readiness finding, not an infrastructure failure. Evidence presence never qualifies U.
    """
    require_instance(request, DiagnosisRequest, "request")
    require_instance(policy, MaskingDiagnosisPolicy, "policy")
    for job in policy.jobs:
        scope = job.measurements[0].selector.scope
        scope.validate_against(request.state.snapshot)
        outer = request.scope
        if (
            not set(scope.track_ids) <= set(outer.track_ids)
            or scope.time_range.start_seconds < outer.time_range.start_seconds
            or scope.time_range.end_seconds > outer.time_range.end_seconds
            or outer.frequency_range is not None
        ):
            raise ValueError("configured whole-band diagnosis job lies outside the request scope")
    explanation = (
        "P1 INCOMPLETE: uncalibrated qualification policy; no qualified support S0; "
        "observations are not qualified. " + " | ".join(_job_readiness(request, j) for j in policy.jobs)
    )
    issued = set()

    def row(family, status, method, message):
        coverage_id = DiagnosisCoverageId(identities.next_token())
        if coverage_id in issued:
            raise ValueError("duplicate diagnosis coverage identity token")
        issued.add(coverage_id)
        return DiagnosisCoverage(coverage_id, family, request.scope, status, method, message)

    coverage = (
        row(ProblemFamily.MASKING, DiagnosisCoverageStatus.INCOMPLETE, policy.method, explanation),
        row(
            ProblemFamily.FREQUENCY_BUILDUP,
            DiagnosisCoverageStatus.UNAVAILABLE,
            None,
            "P2 frequency buildup: no supported diagnosis method configured; not an all-clear.",
        ),
        row(
            ProblemFamily.PHASE_INTERACTION,
            DiagnosisCoverageStatus.UNAVAILABLE,
            None,
            "P3 phase interaction: no supported diagnosis method configured; not an all-clear.",
        ),
    )
    return DiagnosisReport(request.state.project_id, request.state.revision, coverage, ())
