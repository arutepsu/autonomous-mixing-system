"""Configured real readiness source and R1 integration; temporary unit-test audio only."""

from dataclasses import replace

import pytest
from masking_diagnosis_support import policy, request
from masking_support import TA, TB, Tokens, World, fixture_counts

import autonomous_mixing.drivers.diagnosis_runtime as runtime
from autonomous_mixing.core.ports.coordinator_runtime import DiagnosisSource
from autonomous_mixing.core.records.diagnosis import DiagnosisCoverageStatus, ProblemFamily
from autonomous_mixing.core.records.identifiers import Revision
from autonomous_mixing.core.records.results import Success


def source(pol=None, ids=None):
    return runtime.ConfiguredDiagnosisSource(policy=pol or policy(), identities=ids or Tokens("coverage"))


def test_source_satisfies_the_existing_port_and_reports_truthful_coverage():
    configured: DiagnosisSource = source()
    result = configured.diagnose(request())
    assert isinstance(result, Success) and not result.value.candidates
    assert result.value.coverage[0].status is DiagnosisCoverageStatus.INCOMPLETE


def test_source_uses_the_actual_request_and_configuration():
    configured = source()
    full = configured.diagnose(request()).value
    absent = configured.diagnose(request(observations=())).value
    assert full.coverage[0].explanation != absent.coverage[0].explanation


def test_wrong_policy_or_identity_source_is_configuration_error():
    with pytest.raises(ValueError):
        runtime.ConfiguredDiagnosisSource(policy=None, identities=Tokens())
    with pytest.raises(ValueError):
        runtime.ConfiguredDiagnosisSource(policy=policy(), identities=object())


def test_programming_error_propagates_the_same_exception(monkeypatch):
    error = RuntimeError("bug")

    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(runtime, "diagnose_masking", broken)
    with pytest.raises(RuntimeError) as caught:
        source().diagnose(request())
    assert caught.value is error


def test_source_validates_the_pure_result_before_returning(monkeypatch):
    report = source().diagnose(request()).value
    monkeypatch.setattr(
        runtime, "diagnose_masking", lambda *args, **kwargs: replace(report, revision=Revision("wrong"))
    )
    with pytest.raises(ValueError):
        source().diagnose(request())


def test_source_refuses_coverage_ids_reused_across_calls():
    class Repeating:
        def __init__(self):
            self.i = 0

        def next_token(self):
            self.i += 1
            return f"reused-{self.i % 3}"

    configured = source(ids=Repeating())
    assert isinstance(configured.diagnose(request()), Success)
    with pytest.raises(ValueError, match="reused"):
        configured.diagnose(request())


@pytest.mark.parametrize("signal", ["silence", "nonzero"])
def test_real_preparer_analyzer_router_to_readiness_source(tmp_path, signal):
    import numpy as np
    from test_analysis_router import level_job, router

    from autonomous_mixing.adapters.dsp_analysis.directional_analysis import RAW_GEOMETRIC_SUPPORT_CELLS
    from autonomous_mixing.adapters.dsp_analysis.file_analyzer import SAMPLE_PEAK_DBFS
    from autonomous_mixing.core.records.coordinator_runtime import DiagnosisRequest
    from autonomous_mixing.core.records.observation import Unavailable

    a = fixture_counts("masker_1k_a8192") if signal == "nonzero" else np.zeros(6912, dtype=np.int16)
    b = fixture_counts("target_2k_a1024") if signal == "nonzero" else np.zeros(6912, dtype=np.int16)
    world = World(tmp_path, {TA: a, TB: b})
    pol = policy(world.plan)
    definitions = tuple(d for j in pol.jobs for d in j.measurements) + (
        world.job(RAW_GEOMETRIC_SUPPORT_CELLS),
        world.job(RAW_GEOMETRIC_SUPPORT_CELLS, TB, TA),
        level_job(world, SAMPLE_PEAK_DBFS, TA),
    )
    analysis_request = world.request(definitions)
    measured = router(world).analyze(analysis_request)
    assert isinstance(measured, Success), measured
    qualified = [
        o
        for o in measured.value.observations
        if o.metric.name in {m.selector.metric.name for j in pol.jobs for m in j.measurements}
    ]
    assert len(qualified) == 6 and all(isinstance(o.result, Unavailable) for o in qualified)
    req = DiagnosisRequest(world.stems.state, analysis_request.scope, measured.value.observations)
    result = source(pol).diagnose(req)
    assert isinstance(result, Success) and not result.value.candidates
    p1 = next(r for r in result.value.coverage if r.family is ProblemFamily.MASKING)
    assert p1.status is DiagnosisCoverageStatus.INCOMPLETE and p1.explanation.count("unavailable") == 6
    assert "ta -> tb" in p1.explanation and "tb -> ta" in p1.explanation


# --- experimental fixed-support diagnosis (handoff 2026-11-23, Task 3) --------------------------------------------


def test_source_dispatches_an_explicit_experimental_policy_to_the_experimental_rule():
    from masking_diagnosis_support import calls, experimental_policy, experimental_request

    pol = experimental_policy()
    result = source(pol).diagnose(experimental_request(pol, ((100, 25, calls(4)), (100, 0, 0.0))))
    assert isinstance(result, Success) and len(result.value.candidates) == 1
    masking = [r for r in result.value.coverage if r.family is ProblemFamily.MASKING]
    assert [r.status for r in masking] == [DiagnosisCoverageStatus.ASSESSED] * 2


def test_legacy_source_stays_readiness_only():
    result = source(policy()).diagnose(request())
    assert not result.value.candidates
    assert [r.status for r in result.value.coverage if r.family is ProblemFamily.MASKING] == [
        DiagnosisCoverageStatus.INCOMPLETE
    ]


def test_source_refuses_candidate_ids_reused_across_calls():
    from masking_diagnosis_support import calls, experimental_policy, experimental_request

    class CandidateRepeat:
        def __init__(self):
            self.i = 0

        def next_token(self):
            self.i += 1
            # coverage tokens stay fresh; the candidate token (second in each report) repeats
            return "cand" if self.i % 5 == 2 else f"cov-{self.i}"

    pol = experimental_policy()
    configured = source(pol, ids=CandidateRepeat())
    req = experimental_request(pol, ((100, 25, calls(4)), (100, 0, 0.0)))
    assert isinstance(configured.diagnose(req), Success)
    with pytest.raises(ValueError, match="reused"):
        configured.diagnose(req)


def test_coverage_and_candidate_ids_may_not_collide_across_calls():
    from masking_diagnosis_support import calls, experimental_policy, experimental_request

    class Collide:
        def __init__(self):
            self.values = ["a", "b", "c", "d", "e", "b", "x", "y", "z", "w"]

        def next_token(self):
            return self.values.pop(0)

    pol = experimental_policy()
    configured = source(pol, ids=Collide())
    req = experimental_request(pol, ((100, 25, calls(4)), (100, 0, 0.0)))
    configured.diagnose(req)  # coverage a, candidate b, coverage c, d, e
    with pytest.raises(ValueError, match="reused"):
        configured.diagnose(req)  # next report reuses "b" (a candidate ID) as a coverage ID


def test_source_rejects_a_policy_of_neither_kind():
    with pytest.raises(ValueError):
        runtime.ConfiguredDiagnosisSource(policy=object(), identities=Tokens())


def _experimental_diagnosis_job_definitions(world, fixed):
    from autonomous_mixing.core.records.masking_diagnosis import EXPERIMENTAL_DIAGNOSIS_METRICS

    g = fixed.geometry
    return tuple(world.job(metric, g.pair.masker, g.pair.target, time_range=g.time_range, unit=unit)
                 for metric, unit in EXPERIMENTAL_DIAGNOSIS_METRICS)  # fmt: skip


def _real_experimental_diagnosis(world):
    from autonomous_mixing.core.records.analysis import AnalysisRequest
    from autonomous_mixing.core.records.coordinator_runtime import DiagnosisRequest
    from autonomous_mixing.core.records.masking_diagnosis import (
        ExperimentalMaskingDiagnosisJob,
        ExperimentalMaskingDiagnosisPolicy,
        experimental_diagnosis_method,
    )
    from autonomous_mixing.core.records.observation import AnalysisScope, AnalysisSpec

    prepared = world.plan.prepared
    jobs = tuple(ExperimentalMaskingDiagnosisJob(fixed, _experimental_diagnosis_job_definitions(world, fixed))
                 for fixed in prepared.experimental_supports)  # fmt: skip
    pol = ExperimentalMaskingDiagnosisPolicy(experimental_diagnosis_method(world.plan), world.plan, jobs)
    definitions = tuple(d for j in jobs for d in j.measurements)
    interval = prepared.supports[0].time_range
    analysis = AnalysisRequest(world.stems.state, AnalysisScope((TA, TB), interval),
                               AnalysisSpec(world.spec, tuple(dict.fromkeys(d.selector.metric for d in definitions))),
                               definitions, world.stems.bindings)  # fmt: skip
    measured = world.analyzer.analyze(analysis)
    assert isinstance(measured, Success), measured
    req = DiagnosisRequest(world.stems.state, analysis.scope, measured.value.observations)
    result = source(pol).diagnose(req)
    assert isinstance(result, Success)
    return pol, measured.value.observations, result.value


def test_engineering_integration_real_preparation_analyzer_and_experimental_source(tmp_path):
    """Engineering integration on disposable numerical WAVs (not validation data); the predicate is recomputed."""
    from test_experimental_masking import experimental_world

    from autonomous_mixing.core.records.observation import MeasuredScalar

    world = experimental_world(tmp_path)  # the owner-approved current screening values
    pol, observations, report = _real_experimental_diagnosis(world)
    screen = pol.screening
    rows = [r for r in report.coverage if r.family is ProblemFamily.MASKING]
    assert len(rows) == 2 and all(r.method == pol.method for r in rows)
    for job, row in zip(pol.jobs, rows, strict=True):
        scope = job.measurements[0].selector.scope
        values = {o.metric.name: o.result for o in observations if o.scope == scope}
        n, r, run = (values[m.selector.metric.name] for m in job.measurements)
        assert all(isinstance(v, MeasuredScalar) for v in (n, r, run)), values
        assert row.status is DiagnosisCoverageStatus.ASSESSED and n.value == job.support.cell_count
        expected = (r.value / n.value >= screen.minimum_robust_fraction
                    and run.value >= screen.minimum_consecutive_calls * 576 / 44100)  # fmt: skip
        found = [c for c in report.candidates if c.coverage_id == row.coverage_id]
        assert len(found) == int(expected)
    assert report.candidates, "the labelled numerical fixture is expected to exercise the positive path"


def test_engineering_integration_small_support_is_incomplete(tmp_path):
    from test_experimental_masking import experimental_world
    from test_experimental_masking import policy as screen_policy

    world = experimental_world(tmp_path, p=screen_policy(minimum_cells=10000))  # labelled test configuration
    _, _, report = _real_experimental_diagnosis(world)
    rows = [r for r in report.coverage if r.family is ProblemFamily.MASKING]
    assert all(r.status is DiagnosisCoverageStatus.INCOMPLETE for r in rows) and not report.candidates
