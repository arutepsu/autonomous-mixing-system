"""Exact dual-mono profile integration. Temporary numerical fixtures only; no project audio."""

import dataclasses
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from masking_diagnosis_support import METHOD
from masking_support import (
    ALIGNMENT,
    FULL,
    TA,
    TB,
    Clock,
    Stems,
    Tokens,
    World,
    prep_job,
    prep_request,
    sha256,
    state_of,
)

from autonomous_mixing.adapters.dsp_analysis.analysis_router import AnalysisRouter
from autonomous_mixing.adapters.dsp_analysis.directional_analysis import (
    ANALYZER,
    QUALIFIED_METRICS,
    RAW_GEOMETRIC_SUPPORT_CELLS,
    RAW_SIGNED_MARGIN_MEDIAN_DB,
)
from autonomous_mixing.adapters.dsp_analysis.masking_preparation import (
    MaskingPreparer,
    decode_prepared_plan,
    encode_prepared_plan,
)
from autonomous_mixing.core.records.coordinator_runtime import DiagnosisRequest
from autonomous_mixing.core.records.masking_diagnosis import (
    READINESS_METRICS,
    MaskingDiagnosisJob,
    MaskingDiagnosisPolicy,
)
from autonomous_mixing.core.records.masking_measurement import P1_EXACT_DUAL_MONO_PROFILE, P1_PROFILE
from autonomous_mixing.core.records.observation import Unavailable
from autonomous_mixing.core.records.results import PortFailure, Success
from autonomous_mixing.drivers.diagnosis_runtime import ConfiguredDiagnosisSource

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def policy_for(world):
    return MaskingDiagnosisPolicy(
        METHOD,
        world.plan,
        tuple(
            MaskingDiagnosisJob(
                s,
                tuple(
                    world.job(metric, s.pair.masker, s.pair.target, time_range=s.time_range)
                    for metric, _ in READINESS_METRICS
                ),
            )
            for s in world.plan.prepared.supports
        ),
    )


def signal(silent=False):
    if silent:
        return np.zeros(6912, dtype=np.int16)
    return ((np.arange(6912, dtype=np.int64) * 7919) % 65536 - 32768).astype(np.int16)


def evidence(world, result):
    digest = world.analyzer.evidence_digest(result.value.observations[0].observation_id)
    return (world.evidence_dir / (digest + ".json")).read_bytes()


def test_pre_change_native_manifest_is_byte_identical():
    data = (FIXTURES / "p1_native_mono_prepared_v1.json").read_bytes()
    assert hashlib.sha256(data).hexdigest() == "20a7e0b9950c9c3512bf5d89169ab664f7a0ee4e25c6b03d0f934d1d84a0d2ad"
    assert encode_prepared_plan(decode_prepared_plan(data)) == data


def test_pre_change_native_evidence_is_byte_identical(tmp_path):
    world = World(tmp_path, {TA: signal(True), TB: signal(True)})
    result = world.analyzer.analyze(world.request((world.job(RAW_GEOMETRIC_SUPPORT_CELLS),)))
    assert isinstance(result, Success)
    data = evidence(world, result)
    assert data == (FIXTURES / "p1_native_mono_evidence_v1.json").read_bytes()
    assert hashlib.sha256(data).hexdigest() == "14b14d6bcf9b4fe36e4577c6c752383bb8dfc4475843c5ad106d49ef189eb323"


@pytest.mark.parametrize("channels", [{TA: 1, TB: 1}, {TA: 1, TB: 2}, {TA: 2, TB: 1}, {TA: 2, TB: 2}])
@pytest.mark.parametrize("silent", [False, True])
def test_profile_v2_real_pipeline_matches_mono_numerics_and_records_original_channels(tmp_path, channels, silent):
    mono_dir = tmp_path / "mono"
    mono_dir.mkdir()
    dual_dir = tmp_path / "dual"
    dual_dir.mkdir()
    data = {TA: signal(silent), TB: signal(silent)}
    mono = World(mono_dir, data)
    dual = World(dual_dir, data, channels=channels, profile=P1_EXACT_DUAL_MONO_PROFILE)
    defs = [
        dual.job(metric, *pair)
        for pair in ((TA, TB), (TB, TA))
        for metric in (RAW_GEOMETRIC_SUPPORT_CELLS, RAW_SIGNED_MARGIN_MEDIAN_DB, *QUALIFIED_METRICS)
    ]
    req = dual.request(defs)
    result = AnalysisRouter({ANALYZER: dual.analyzer}).analyze(req)
    baseline = mono.analyzer.analyze(
        mono.request([mono.job(d.selector.metric, *d.selector.scope.track_ids) for d in defs])
    )
    assert isinstance(result, Success) and isinstance(baseline, Success)
    assert [o.result for o in result.value.observations] == [o.result for o in baseline.value.observations]
    assert dual.plan.prepared.profile == P1_EXACT_DUAL_MONO_PROFILE
    assert dual.persisted.digest != mono.persisted.digest and dual.spec != mono.spec
    assert [s.digest for s in dual.plan.prepared.supports] == [s.digest for s in mono.plan.prepared.supports]
    actual = json.loads(evidence(dual, result))
    expected = json.loads(evidence(mono, baseline))
    assert actual["schema_version"] == 2 and expected["schema_version"] == 1
    assert actual["profile"] == [P1_EXACT_DUAL_MONO_PROFILE.name, "2"]
    assert actual["analysis_profile"] == [P1_PROFILE.name, "1"]
    for name in (
        "calls",
        "masker_threshold",
        "target_energy",
        "margin_db",
        "cell_flags",
        "support_cells",
        "quiet_floor",
    ):
        assert actual[name] == expected[name]
    for role, track in (("masker", TA), ("target", TB)):
        source = actual["sources"][role]
        assert source["source_channel_count"] == channels[track]
        assert source["analysis_channel_count"] == 1
        assert source["channel_mapping"] == (
            "mono_identity@1" if channels[track] == 1 else "exact_dual_mono_first_channel@1"
        )
        assert source["sha256"] == dual.stems.artifacts[track].content_hash
    assert result.value.observations[0].artifact_ids == (
        dual.stems.artifacts[TA].artifact_id,
        dual.stems.artifacts[TB].artifact_id,
    )
    source = ConfiguredDiagnosisSource(policy=policy_for(dual), identities=Tokens("cov"))
    report = source.diagnose(DiagnosisRequest(req.state, req.scope, result.value.observations))
    assert isinstance(report, Success) and not report.value.candidates
    assert report.value.coverage[0].status.value == "incomplete"
    assert all(isinstance(o.result, Unavailable) for o in result.value.observations if o.metric in QUALIFIED_METRICS)


def test_legacy_request_rejects_stereo_candidate_metadata(tmp_path):
    stems = Stems(tmp_path, {TA: signal(), TB: signal()}, channels=2)
    job = prep_job(stems.artifacts[TA], stems.artifacts[TB], FULL)
    with pytest.raises(ValueError, match="mono"):
        prep_request(stems.state, stems.bindings, (job,))


def test_v2_preparation_checks_entire_file_before_writing_support(tmp_path):
    stems = Stems(tmp_path, {TA: signal(True), TB: signal(True)}, channels=2)
    a = stems.artifacts[TA]
    samples = np.zeros((6912, 2), dtype=np.int16)
    samples[-1, 1] = 1
    sf.write(str(stems.root / a.location), samples, 44100, subtype="PCM_16")
    stems.artifacts[TA] = dataclasses.replace(a, content_hash=sha256(stems.root / a.location))
    stems.state = state_of(tuple(stems.artifacts.values()))
    short = dataclasses.replace(FULL, end_seconds=2560 / 44100)
    job = prep_job(stems.artifacts[TA], stems.artifacts[TB], short)
    store = tmp_path / "plans"
    store.mkdir()
    result = MaskingPreparer(
        stems.roots, store=store, clock=Clock(), known_alignments=frozenset({ALIGNMENT.method})
    ).prepare(prep_request(stems.state, stems.bindings, (job,), profile=P1_EXACT_DUAL_MONO_PROFILE))
    assert isinstance(result, PortFailure) and "non_identical_channels" in result.message
    assert not list(store.iterdir())


def test_v2_analysis_rechecks_changed_current_file_beyond_selected_interval(tmp_path):
    short = dataclasses.replace(FULL, end_seconds=2560 / 44100)
    world = World(
        tmp_path, {TA: signal(True), TB: signal(True)}, channels=2, profile=P1_EXACT_DUAL_MONO_PROFILE, time_range=short
    )
    a = world.stems.artifacts[TA]
    samples = np.zeros((6912, 2), dtype=np.int16)
    samples[-1, 1] = 1
    sf.write(str(world.stems.root / a.location), samples, 44100, subtype="PCM_16")
    world.stems.artifacts[TA] = dataclasses.replace(a, content_hash=sha256(world.stems.root / a.location))
    world.stems.state = state_of(tuple(world.stems.artifacts.values()))
    req = world.request((world.job(RAW_GEOMETRIC_SUPPORT_CELLS, time_range=short),))
    result = world.analyzer.analyze(req)
    assert isinstance(result, PortFailure) and "non_identical_channels" in result.message
    assert not list(world.evidence_dir.iterdir())


def test_legacy_analyzer_refuses_stereo_before_reading(tmp_path, monkeypatch):
    world = World(tmp_path, {TA: signal(), TB: signal()})
    a = world.stems.artifacts[TA]
    world.stems.artifacts[TA] = dataclasses.replace(a, channel_count=2)
    state = state_of(tuple(world.stems.artifacts.values()))
    req = world.request((world.job(RAW_GEOMETRIC_SUPPORT_CELLS),), state=state)
    import autonomous_mixing.adapters.dsp_analysis.directional_analysis as analysis

    monkeypatch.setattr(analysis, "read_dist10_member", lambda *a, **k: pytest.fail("legacy stereo read"))
    result = world.analyzer.analyze(req)
    assert isinstance(result, PortFailure)


def test_legacy_readiness_policy_refuses_stereo_definitions(tmp_path):
    world = World(tmp_path, {TA: signal(), TB: signal()}, channels=2, profile=P1_EXACT_DUAL_MONO_PROFILE)
    pol = policy_for(world)
    legacy_plan = dataclasses.replace(world.plan, prepared=dataclasses.replace(world.plan.prepared, profile=P1_PROFILE))
    with pytest.raises(ValueError, match="mono"):
        dataclasses.replace(pol, measurement_plan=legacy_plan)
