"""Tests for the mixed-job analysis router (R1 Task 4).

Uses the real ``FileAudioAnalyzer`` and ``DirectionalMaskingAnalyzer`` on the stored dist10 fixture signals (unit-test
signals, not evaluation data) plus labelled stub analyzers for misbehaviour that the real analyzers never show.
"""

import dataclasses

import numpy as np
import pytest
from masking_support import FULL, LIMIT, TA, TB, Clock, Tokens, World, fixture_counts, source_of, state_of

import autonomous_mixing.adapters.dsp_analysis.analysis_router as router_module
from autonomous_mixing.adapters.dsp_analysis.analysis_router import AnalysisRouter
from autonomous_mixing.adapters.dsp_analysis.directional_analysis import (
    ANALYZER as P1_ANALYZER,
)
from autonomous_mixing.adapters.dsp_analysis.directional_analysis import (
    RAW_POSITIVE_MARGIN_CELLS,
    RAW_SIGNED_MARGIN_MEDIAN_DB,
)
from autonomous_mixing.adapters.dsp_analysis.file_analyzer import (
    ANALYZER as LEVEL_ANALYZER,
)
from autonomous_mixing.adapters.dsp_analysis.file_analyzer import (
    SAMPLE_PEAK_DBFS,
    SAMPLE_RMS_DBFS,
    FileAudioAnalyzer,
)
from autonomous_mixing.adapters.dsp_analysis.masking_preparation import manifest_path
from autonomous_mixing.core.ports.analysis import AudioAnalyzer
from autonomous_mixing.core.records.analysis import AnalysisResponse
from autonomous_mixing.core.records.identifiers import VersionRef
from autonomous_mixing.core.records.integrity import ArtifactRootBinding
from autonomous_mixing.core.records.observation import AnalysisScope, MeasuredScalar
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, Success
from autonomous_mixing.core.records.verification import MetricSelector
from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition

ALIGN = VersionRef("alignment", "1")
MASKER, TARGET = "masker_1k_a8192", "target_2k_a1024"


def level_job(world: World, metric, track, *, artifacts=None) -> MeasurementDefinition:
    artifact = (artifacts or world.stems.artifacts)[track]
    return MeasurementDefinition(MetricSelector(metric, AnalysisScope((track,), FULL)), "dBFS", world.spec, LEVEL_ANALYZER,
                                 ALIGN, (source_of(artifact),))  # fmt: skip


class Spy:
    def __init__(self, inner):
        self.inner, self.requests = inner, []

    def analyze(self, request):
        self.requests.append(request)
        return self.inner.analyze(request)


@pytest.fixture
def world(tmp_path) -> World:
    return World(tmp_path, {TA: fixture_counts(MASKER), TB: fixture_counts(TARGET)})


def level_analyzer(world: World, prefix="lvl") -> FileAudioAnalyzer:
    return FileAudioAnalyzer(world.stems.roots, max_file_bytes=LIMIT, max_decoded_bytes=LIMIT, clock=Clock(),
                             identities=Tokens(prefix))  # fmt: skip


def router(world: World, level=None, p1=None) -> AnalysisRouter:
    return AnalysisRouter({LEVEL_ANALYZER: level or level_analyzer(world), P1_ANALYZER: p1 or world.make()})


def results(response: AnalysisResponse) -> list:
    return [(o.metric, o.scope, o.analyzer, o.artifact_ids, o.result) for o in response.observations]


def mixed(world: World) -> list:
    return [level_job(world, SAMPLE_PEAK_DBFS, TA), world.job(RAW_SIGNED_MARGIN_MEDIAN_DB),
            level_job(world, SAMPLE_RMS_DBFS, TB), world.job(RAW_SIGNED_MARGIN_MEDIAN_DB, TB, TA),
            world.job(RAW_POSITIVE_MARGIN_CELLS)]  # fmt: skip


def test_the_router_satisfies_the_port(world):
    port: AudioAnalyzer = router(world)
    assert callable(port.analyze)


def test_mixed_jobs_keep_job_order_and_each_analyzer_identity(world):
    result = router(world).analyze(world.request(mixed(world)))
    assert isinstance(result, Success), result
    observations = result.value.observations
    assert [o.analyzer for o in observations] == [LEVEL_ANALYZER, P1_ANALYZER, LEVEL_ANALYZER, P1_ANALYZER, P1_ANALYZER]
    assert [o.scope.track_ids for o in observations] == [(TA,), (TA, TB), (TB,), (TB, TA), (TA, TB)]
    assert LEVEL_ANALYZER == VersionRef("autonomous_mixing.file_level_analyzer", "1")


def test_routed_values_equal_direct_calls(world):
    jobs = mixed(world)
    routed = router(world).analyze(world.request(jobs)).value
    levels = level_analyzer(world).analyze(world.request([jobs[0], jobs[2]])).value
    p1 = world.make().analyze(world.request([jobs[1], jobs[3], jobs[4]])).value
    by_key = {(o.metric, o.scope): o for o in (*levels.observations, *p1.observations)}
    for observation in routed.observations:
        direct = by_key[(observation.metric, observation.scope)]
        assert (observation.result, observation.analyzer, observation.artifact_ids) == (
            direct.result, direct.analyzer, direct.artifact_ids)  # fmt: skip


@pytest.mark.parametrize("kind", ["level", "p1"])
def test_single_analyzer_requests_route_to_that_analyzer_only(world, kind):
    level, p1 = Spy(level_analyzer(world)), Spy(world.make())
    jobs = [level_job(world, SAMPLE_PEAK_DBFS, TA)] if kind == "level" else [world.job(RAW_SIGNED_MARGIN_MEDIAN_DB)]
    result = router(world, level, p1).analyze(world.request(jobs))
    assert isinstance(result, Success)
    assert (len(level.requests), len(p1.requests)) == ((1, 0) if kind == "level" else (0, 1))


def test_subrequests_keep_spec_ref_bindings_and_only_their_jobs(world):
    level, p1 = Spy(level_analyzer(world)), Spy(world.make())
    request = world.request(mixed(world))
    router(world, level, p1).analyze(request)
    (sub_level,), (sub_p1,) = level.requests, p1.requests
    for sub in (sub_level, sub_p1):
        assert sub.specification.ref == request.specification.ref and sub.root_bindings == request.root_bindings
        assert sub.state is request.state and sub.scope == request.scope
    assert sub_level.definitions == (request.definitions[0], request.definitions[2])
    assert sub_level.specification.metric_ids == (SAMPLE_PEAK_DBFS, SAMPLE_RMS_DBFS)
    assert sub_p1.definitions == (request.definitions[1], request.definitions[3], request.definitions[4])
    assert sub_p1.specification.metric_ids == (RAW_SIGNED_MARGIN_MEDIAN_DB, RAW_POSITIVE_MARGIN_CELLS)


def test_unrouted_analyzer_fails_before_any_analyzer_runs(world):
    level, p1 = Spy(level_analyzer(world)), Spy(world.make())
    other = dataclasses.replace(level_job(world, SAMPLE_PEAK_DBFS, TA), analyzer=VersionRef("other.analyzer", "1"))
    result = router(world, level, p1).analyze(world.request([world.job(RAW_SIGNED_MARGIN_MEDIAN_DB), other]))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.UNSUPPORTED
    assert level.requests == [] and p1.requests == []


def test_duplicate_definitions_are_refused_by_the_request_itself(world):
    job = world.job(RAW_SIGNED_MARGIN_MEDIAN_DB)
    with pytest.raises(ValueError, match="more than once"):
        world.request([job, job])


def test_one_failed_sub_batch_fails_the_whole_request(world):
    manifest_path(world.plans_dir, world.plan.prepared_digest).unlink()
    result = router(world).analyze(world.request(mixed(world)))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.UNAVAILABLE
    assert result.message.startswith("autonomous_mixing.p1_directional_analyzer@1: ")


class Stub:
    """Labelled misbehaving analyzer: returns the inner response after applying ``change``."""

    def __init__(self, inner, change):
        self.inner, self.change = inner, change

    def analyze(self, request):
        result = self.inner.analyze(request)
        return Success(AnalysisResponse(self.change(result.value.observations)))


@pytest.mark.parametrize(
    "change",
    [
        lambda obs: tuple(dataclasses.replace(o, analyzer=LEVEL_ANALYZER) for o in obs),  # relabeling
        lambda obs: obs[:-1],  # missing observation
        lambda obs: tuple(dataclasses.replace(o, analysis_spec=VersionRef("x", "1")) for o in obs),
    ],
)
def test_misbehaving_sub_analyzers_are_invalid_responses(world, change):
    result = router(world, p1=Stub(world.make(), change)).analyze(world.request(mixed(world)))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.INVALID_RESPONSE


def test_colliding_observation_ids_across_sub_batches_are_invalid(world):
    same = router(world, level=level_analyzer(world, prefix="obs"))  # the P1 analyzer also uses "obs-<n>"
    result = same.analyze(world.request(mixed(world)))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.INVALID_RESPONSE


def test_constructor_validates_routes(world):
    shared = world.make()
    with pytest.raises(ValueError):
        AnalysisRouter({})
    with pytest.raises(ValueError, match="aliasing"):
        AnalysisRouter({LEVEL_ANALYZER: shared, P1_ANALYZER: shared})
    with pytest.raises(ValueError):
        AnalysisRouter({P1_ANALYZER: object()})


def test_native_stereo_levels_are_unchanged_and_stereo_stays_invalid_for_p1(world):
    stereo = world.stems.write(TA, fixture_counts(MASKER), rate=44100, subtype="PCM_16", channels=2, start=0.0,
                               revision="r2")  # fmt: skip
    members = (stereo, world.stems.artifacts[TB])
    state = state_of(members)
    bindings = tuple(ArtifactRootBinding(a.artifact_id, "inputs") for a in members)
    artifacts = {TA: stereo, TB: world.stems.artifacts[TB]}
    levels = [level_job(world, SAMPLE_PEAK_DBFS, TA, artifacts=artifacts),
              level_job(world, SAMPLE_RMS_DBFS, TA, artifacts=artifacts)]  # fmt: skip
    routed = router(world).analyze(world.request(levels, state=state, bindings=bindings))
    direct = level_analyzer(world).analyze(world.request(levels, state=state, bindings=bindings))
    assert [o.result for o in routed.value.observations] == [o.result for o in direct.value.observations]
    peak = routed.value.observations[0].result
    assert isinstance(peak, MeasuredScalar)
    assert peak.value == pytest.approx(20 * np.log10(np.max(np.abs(fixture_counts(MASKER))) / 32768), abs=1e-12)
    p1 = world.job(RAW_SIGNED_MARGIN_MEDIAN_DB, artifacts=artifacts)
    result = router(world).analyze(world.request([*levels, p1], state=state, bindings=bindings))
    assert isinstance(result, PortFailure) and "unsupported_channel_count" in result.message


def test_each_subresponse_is_validated_against_its_subrequest_then_the_merge(world, monkeypatch):
    calls = []
    original = router_module.validate_analysis_response

    def spy(request, response):
        calls.append((request, response))
        return original(request, response)

    monkeypatch.setattr(router_module, "validate_analysis_response", spy)
    level, p1 = Spy(level_analyzer(world)), Spy(world.make())
    request = world.request(mixed(world))
    result = router(world, level, p1).analyze(request)
    assert isinstance(result, Success)
    assert [r for r, _ in calls] == [level.requests[0], p1.requests[0], request]
    assert calls[-1][1] is result.value


def test_an_incomplete_subresponse_is_attributed_to_its_analyzer_before_merging(world):
    level = Stub(level_analyzer(world), lambda obs: obs[:-1])
    p1 = Spy(world.make())
    result = router(world, level=level, p1=p1).analyze(world.request(mixed(world)))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.INVALID_RESPONSE
    assert result.message.startswith("autonomous_mixing.file_level_analyzer@1: invalid subresponse")
    assert p1.requests == []  # refused at the first invalid sub-batch
