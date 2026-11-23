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
