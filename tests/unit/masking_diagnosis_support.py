"""Labelled structural diagnosis fixtures; no audio, calibration or detector ran."""

from datetime import timedelta

from masking_support import (
    ALIGNMENT,
    FULL,
    P1_PROFILE,
    REGION,
    TA,
    TB,
    UTC,
    N,
    metadata_artifact,
    source_of,
    state_of,
)

from autonomous_mixing.core.records.coordinator_runtime import DiagnosisRequest
from autonomous_mixing.core.records.identifiers import ObservationId, VersionRef
from autonomous_mixing.core.records.masking_diagnosis import (
    READINESS_METRICS,
    MaskingDiagnosisJob,
    MaskingDiagnosisPolicy,
)
from autonomous_mixing.core.records.masking_measurement import (
    MaskingMeasurementPlan,
    MaskingPair,
    MaskingPreparedPlan,
    PreparationTiming,
    PreparedSupport,
    QualificationStatus,
    SupportKind,
    bound_specification,
    complete_window_calls,
    interval_samples,
    support_digest,
)
from autonomous_mixing.core.records.observation import AnalysisScope, MeasuredScalar, Observation, Unavailable
from autonomous_mixing.core.records.verification import MetricSelector
from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition

ANALYZER = VersionRef("dist10_directional_masking_analyzer", "1")
METHOD = VersionRef("p1.diagnosis_readiness", "1")


def support(pair=(TA, TB), region=REGION, time=FULL):
    a, b = (metadata_artifact(t, N) for t in pair)
    start, stop = interval_samples(time, timeline_start_seconds=0.0, sample_rate_hz=44100, frame_count=N)
    first, count = complete_window_calls(frame_count=N, start_sample=start, stop_sample=stop)
    digest = support_digest(
        profile=P1_PROFILE,
        pair=MaskingPair(*pair),
        region=region,
        sample_rate_hz=44100,
        frame_count=N,
        timeline_start_seconds=0.0,
        start_sample=start,
        stop_sample=stop,
        first_call=first,
        call_count=count,
    )
    return PreparedSupport(
        MaskingPair(*pair),
        region,
        time,
        ALIGNMENT,
        a.artifact_id,
        b.artifact_id,
        a.content_hash,
        b.content_hash,
        44100,
        N,
        0.0,
        start,
        stop,
        first,
        count,
        digest,
    )


def plan(supports=None):
    prepared = MaskingPreparedPlan(
        P1_PROFILE,
        tuple(supports or (support(),)),
        SupportKind.GEOMETRIC,
        QualificationStatus.UNCALIBRATED,
        1000000,
        1000000,
        PreparationTiming(UTC, UTC + timedelta(seconds=1), 1.0),
    )
    digest = "d" * 64  # structural fixture identity only; no manifest is claimed authenticated here
    return MaskingMeasurementPlan(bound_specification("test.p1", "1", digest), ANALYZER, digest, prepared)


def definitions(s, measurement_plan):
    pair = (s.pair.masker, s.pair.target)
    sources = tuple(source_of(metadata_artifact(t, s.frame_count, start=s.timeline_start_seconds)) for t in pair)
    return tuple(
        MeasurementDefinition(
            MetricSelector(metric, AnalysisScope(pair, s.time_range)),
            unit,
            measurement_plan.specification,
            measurement_plan.analyzer,
            s.alignment.method,
            sources,
        )
        for metric, unit in READINESS_METRICS
    )


def policy(p=None):
    p = p or plan()
    return MaskingDiagnosisPolicy(
        METHOD, p, tuple(MaskingDiagnosisJob(s, definitions(s, p)) for s in p.prepared.supports)
    )


def request(pol=None, *, observations=None, state=None):
    pol = pol or policy()
    state = state or state_of((metadata_artifact(TA, N), metadata_artifact(TB, N)))
    scope = AnalysisScope((TA, TB), FULL)
    return DiagnosisRequest(state, scope, tuple(observations) if observations is not None else obs(pol, state=state))


def obs(pol, *, state=None, finite=False):
    state = state or state_of((metadata_artifact(TA, N), metadata_artifact(TB, N)))
    by_track = {a.source_track_ids[0]: a for a in state.audio.artifacts}
    rows = []
    for job in pol.jobs:
        for d in job.measurements:
            ids = tuple(by_track[s.source_track_ids[0]].artifact_id for s in d.sources)
            value = MeasuredScalar(2.0, d.unit) if finite else Unavailable("uncalibrated fixture")
            rows.append(
                Observation(
                    ObservationId(f"o-{len(rows)}"),
                    d.selector.metric,
                    ids,
                    d.selector.scope,
                    value,
                    d.analysis_spec,
                    d.analyzer,
                    UTC,
                )
            )
    return tuple(rows)
