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


# --- experimental diagnosis fixtures (labelled structural records; no audio, detector or calibration) -------------

from autonomous_mixing.core.records.masking_diagnosis import (  # noqa: E402
    EXPERIMENTAL_DIAGNOSIS_METRICS,
    ExperimentalMaskingDiagnosisJob,
    ExperimentalMaskingDiagnosisPolicy,
    experimental_diagnosis_method,
)
from autonomous_mixing.core.records.masking_measurement import (  # noqa: E402
    P1_EXPERIMENTAL_METHOD,
    ExperimentalMaskingPolicy,
    ExperimentalSupport,
)

# The owner-approved development values (p1-experimental-policy-pilot.md); tests never tune them.
CURRENT_SCREEN = ExperimentalMaskingPolicy(P1_EXPERIMENTAL_METHOD, 40.0, 0.02, 0.02, 100, 0.25, 4)
STEP = 576 / 44100


def calls(k: int) -> float:
    """A run of ``k`` calls in the analyzer's exact float expression (``k * 576 / 44100``)."""
    return k * 576 / 44100


def hundred_cells(geometry, bands=range(1, 11)):
    """Every call of ``geometry`` on ten bands: 10 calls x 10 bands = 100 cells (a full 10-call run per band)."""
    calls = range(geometry.first_call, geometry.first_call + geometry.call_count)
    return tuple((k, b) for k in calls for b in bands if b in geometry.region.bands)


def experimental_plan(geometries=None, *, screen=CURRENT_SCREEN, cells=None, digest="e" * 64):
    geometries = tuple(geometries or (support(), support((TB, TA))))
    fixed = tuple(
        ExperimentalSupport(g, screen.digest, "1" * 64, hundred_cells(g) if cells is None else cells)
        for g in geometries
    )
    prepared = MaskingPreparedPlan(
        P1_PROFILE,
        geometries,
        SupportKind.GEOMETRIC,
        QualificationStatus.UNCALIBRATED,
        1000000,
        1000000,
        PreparationTiming(UTC, UTC + timedelta(seconds=1), 1.0),
        screen,
        fixed,
    )
    return MaskingMeasurementPlan(bound_specification("test.p1", "1", digest), ANALYZER, digest, prepared)


def experimental_definitions(fixed, measurement_plan, metrics=EXPERIMENTAL_DIAGNOSIS_METRICS):
    g = fixed.geometry
    pair = (g.pair.masker, g.pair.target)
    sources = tuple(source_of(metadata_artifact(t, g.frame_count, start=g.timeline_start_seconds)) for t in pair)
    return tuple(
        MeasurementDefinition(
            MetricSelector(metric, AnalysisScope(pair, g.time_range)),
            unit,
            measurement_plan.specification,
            measurement_plan.analyzer,
            g.alignment.method,
            sources,
        )
        for metric, unit in metrics
    )


def experimental_policy(p=None, jobs=None):
    p = p or experimental_plan()
    jobs = jobs or tuple(
        ExperimentalMaskingDiagnosisJob(s, experimental_definitions(s, p)) for s in p.prepared.experimental_supports
    )
    return ExperimentalMaskingDiagnosisPolicy(experimental_diagnosis_method(p), p, tuple(jobs))


def scalars(pol, values, *, state=None, overrides=None):
    """One observation per configured selector; ``values[job_index]`` = (support, robust, run) or a result object."""
    state = state or state_of((metadata_artifact(TA, N), metadata_artifact(TB, N)))
    by_track = {a.source_track_ids[0]: a for a in state.audio.artifacts}
    rows = []
    for j, job in enumerate(pol.jobs):
        for d, value in zip(job.measurements, values[j], strict=True):
            ids = tuple(by_track[s.source_track_ids[0]].artifact_id for s in d.sources)
            result = value if not isinstance(value, (int, float)) else MeasuredScalar(float(value), d.unit)
            fields = dict(observation_id=ObservationId(f"x-{j}-{d.selector.metric.name}"), metric=d.selector.metric,
                          artifact_ids=ids, scope=d.selector.scope, result=result, analysis_spec=d.analysis_spec,
                          analyzer=d.analyzer, created_at=UTC)  # fmt: skip
            fields.update((overrides or {}).get((j, d.selector.metric.name), {}))
            rows.append(Observation(**fields))
    return tuple(rows)


def experimental_request(pol, values, *, state=None, overrides=None, scope=None):
    state = state or state_of((metadata_artifact(TA, N), metadata_artifact(TB, N)))
    return DiagnosisRequest(state, scope or AnalysisScope((TA, TB), FULL),
                            scalars(pol, values, state=state, overrides=overrides))  # fmt: skip
