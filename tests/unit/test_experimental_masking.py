"""Experimental fixed-cell measurements; temporary numerical WAVs only, never musical/evaluation data."""

import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest
from masking_support import TA, TB, World, state_of

from autonomous_mixing.adapters.dsp_analysis.directional_analysis import (
    EXPERIMENTAL_LONGEST_ROBUST_RUN_SECONDS,
    EXPERIMENTAL_METRICS,
    EXPERIMENTAL_ROBUST_EXCEEDANCE_CELLS,
    EXPERIMENTAL_SIGNED_MARGIN_MEDIAN_DB,
    EXPERIMENTAL_SUPPORT_CELLS,
)
from autonomous_mixing.adapters.dsp_analysis.masking_preparation import (
    decode_prepared_plan,
    encode_prepared_plan,
    load_prepared_plan,
    rounding_noise_band_energy,
)
from autonomous_mixing.core.records.analysis import AnalysisRequest
from autonomous_mixing.core.records.identifiers import VersionRef
from autonomous_mixing.core.records.masking_measurement import (
    P1_EXPERIMENTAL_METHOD,
    ExperimentalMaskingPolicy,
    ExperimentalSupport,
)
from autonomous_mixing.core.records.observation import (
    AnalysisScope,
    AnalysisSpec,
    MeasuredScalar,
    TimeRange,
    Unavailable,
)
from autonomous_mixing.core.records.results import PortFailure, Success


def policy(**overrides):
    fields = dict(
        method=P1_EXPERIMENTAL_METHOD,
        energy_above_noise_db=40.0,
        quiet_elevation_db=0.02,
        margin_deadband_db=0.02,
        minimum_cells=100,
        minimum_robust_fraction=0.25,
        minimum_consecutive_calls=4,
    )
    fields.update(overrides)
    return ExperimentalMaskingPolicy(**fields)


@pytest.mark.parametrize(
    "field,value",
    [
        ("energy_above_noise_db", -1),
        ("quiet_elevation_db", float("nan")),
        ("margin_deadband_db", float("inf")),
        ("minimum_cells", True),
        ("minimum_consecutive_calls", 0),
        ("minimum_robust_fraction", 1.1),
        ("method", VersionRef("unsupported", "1")),
    ],
)
def test_policy_rejects_invalid_configuration(field, value):
    with pytest.raises(ValueError):
        policy(**{field: value})


def test_policy_digest_covers_every_threshold():
    p = policy()
    for field, value in [
        ("energy_above_noise_db", 41),
        ("quiet_elevation_db", 0.03),
        ("margin_deadband_db", 0.03),
        ("minimum_cells", 101),
        ("minimum_robust_fraction", 0.3),
        ("minimum_consecutive_calls", 5),
    ]:
        assert policy(**{field: value}).digest != p.digest
    assert json.loads(p.canonical_payload)["minimum_cells"] == 100


def small_world(tmp_path):
    k = np.arange(6912)
    return World(
        tmp_path,
        {
            TA: np.rint(8192 * np.sin(2 * np.pi * 1000 * k / 44100)).astype(np.int16),
            TB: np.rint(2048 * np.sin(2 * np.pi * 1000 * k / 44100)).astype(np.int16),
        },
    )


def test_fixed_support_is_bound_to_cells_policy_and_baseline(tmp_path):
    w = small_world(tmp_path)
    g = w.plan.prepared.supports[0]
    p = policy()
    cells = ((2, 4), (3, 4))
    x = ExperimentalSupport(g, p.digest, "1" * 64, cells)
    assert x.cell_count == 2
    assert dataclasses.replace(x, cells=((2, 4), (4, 4))).digest != x.digest
    assert dataclasses.replace(x, policy_digest=policy(minimum_cells=101).digest).digest != x.digest
    changed = dataclasses.replace(g, masker_sha256="2" * 64)
    assert dataclasses.replace(x, geometry=changed).digest != x.digest
    assert dataclasses.replace(x, noise_reference_sha256="3" * 64).digest != x.digest


@pytest.mark.parametrize("cells", [((2, 4), (2, 4)), ((3, 4), (2, 4)), ((1, 4),), ((2, 0),), ((2, True),), ((999, 4),)])
def test_cells_must_be_ordered_unique_and_inside_geometry(tmp_path, cells):
    g = small_world(tmp_path).plan.prepared.supports[0]
    with pytest.raises(ValueError):
        ExperimentalSupport(g, policy().digest, "1" * 64, cells)


def test_experimental_manifest_roundtrip_keeps_legacy_bytes(tmp_path):
    w = small_world(tmp_path)
    legacy = w.plan.prepared
    payload = encode_prepared_plan(legacy)
    p = policy()
    fixed = tuple(ExperimentalSupport(g, p.digest, "1" * 64, ((2, 4),)) for g in legacy.supports)
    plan = dataclasses.replace(legacy, experimental_policy=p, experimental_supports=fixed)
    encoded = encode_prepared_plan(plan)
    assert json.loads(encoded)["schema_version"] == 2
    assert decode_prepared_plan(encoded) == plan
    assert encode_prepared_plan(legacy) == payload and json.loads(payload)["schema_version"] == 1
    assert plan.qualification == legacy.qualification
    with pytest.raises(ValueError):
        dataclasses.replace(plan, experimental_policy=None)
    with pytest.raises(ValueError):
        dataclasses.replace(plan, experimental_supports=fixed[:1])
    with pytest.raises(ValueError):
        dataclasses.replace(plan, experimental_policy=policy(minimum_cells=101))
    bad = dataclasses.replace(fixed[0], geometry=dataclasses.replace(fixed[0].geometry, target_sha256="4" * 64))
    with pytest.raises(ValueError):
        dataclasses.replace(plan, experimental_supports=(bad, fixed[1]))


def experimental_world(tmp_path, *, silent=False, p=None, channels=1, profile=None):
    k = np.arange(34560)
    a = np.rint(8192 * np.sin(2 * np.pi * 1000 * k / 44100)).astype(np.int16)
    b = np.rint(2048 * np.sin(2 * np.pi * 1000 * k / 44100)).astype(np.int16)
    if silent:
        a[:] = 0
        b[:] = 0
    kwargs = {} if profile is None else {"profile": profile}
    return World(
        tmp_path,
        {TA: a, TB: b},
        time_range=TimeRange(0, len(k) / 44100),
        channels=channels,
        experimental_policy=p or policy(),
        **kwargs,
    )


def measure(w, metrics=None, *, reverse=False, analyzer=None):
    interval = w.plan.prepared.supports[0].time_range
    a, b = (TB, TA) if reverse else (TA, TB)
    metrics = metrics or tuple(EXPERIMENTAL_METRICS)
    definitions = tuple(w.job(m, a, b, time_range=interval, unit=EXPERIMENTAL_METRICS.get(m, "cells")) for m in metrics)
    request = AnalysisRequest(
        w.stems.state,
        AnalysisScope((a, b), interval),
        AnalysisSpec(w.spec, tuple(metrics)),
        definitions,
        w.stems.bindings,
    )
    return (analyzer or w.analyzer).analyze(request)


def values(result):
    assert isinstance(result, Success), result
    return {o.metric: o.result for o in result.value.observations}


def change_source(w, track, samples):
    old = w.stems.artifacts[track]
    w.stems.artifacts[track] = w.stems.write(
        track, samples, rate=44100, subtype="PCM_16", channels=old.channel_count, start=0.0, revision="candidate"
    )
    w.stems.state = state_of(tuple(w.stems.artifacts.values()), revision="candidate")
    from autonomous_mixing.core.records.integrity import ArtifactRootBinding

    w.stems.bindings = tuple(ArtifactRootBinding(a.artifact_id, "inputs") for a in w.stems.artifacts.values())


def test_preparation_freezes_actual_cells_and_reads_each_source_once(tmp_path, monkeypatch):
    import autonomous_mixing.adapters.dsp_analysis.masking_preparation as prep

    seen = []
    read = prep.read_dist10_member

    def spy(a, **kwargs):
        seen.append(a.artifact_id)
        return read(a, **kwargs)

    monkeypatch.setattr(prep, "read_dist10_member", spy)
    w = experimental_world(tmp_path)
    plan = w.plan.prepared
    assert len(seen) == len(set(seen)) == 2
    assert plan.experimental_policy == policy()
    assert len(plan.experimental_supports) == 2
    fixed = plan.experimental_supports[0]
    assert 100 <= fixed.cell_count < fixed.geometry.cell_count
    assert fixed.policy_digest == policy().digest
    assert load_prepared_plan(w.plans_dir, w.plan.prepared_digest) == plan
    assert fixed.digest != plan.experimental_supports[1].digest


def test_experimental_measurements_and_reloaded_analyzer_agree(tmp_path):
    w = experimental_world(tmp_path)
    before = values(measure(w))
    after = values(measure(w, analyzer=w.make()))
    assert before == after
    n = w.plan.prepared.experimental_supports[0].cell_count
    assert before[EXPERIMENTAL_SUPPORT_CELLS].value == n
    assert isinstance(before[EXPERIMENTAL_SIGNED_MARGIN_MEDIAN_DB], MeasuredScalar)
    assert before[EXPERIMENTAL_ROBUST_EXCEEDANCE_CELLS].value > 0
    assert before[EXPERIMENTAL_LONGEST_ROBUST_RUN_SECONDS].value >= 4 * 576 / 44100


def test_empty_or_small_support_is_truthful_unavailable(tmp_path):
    w = experimental_world(tmp_path, silent=True)
    result = values(measure(w))
    assert result[EXPERIMENTAL_SUPPORT_CELLS].value == 0
    assert all(isinstance(v, Unavailable) for m, v in result.items() if m != EXPERIMENTAL_SUPPORT_CELLS)
    other = tmp_path / "other"
    other.mkdir()
    w = experimental_world(other, p=policy(minimum_cells=10000))
    result = values(measure(w))
    assert 0 < result[EXPERIMENTAL_SUPPORT_CELLS].value < 10000
    assert isinstance(result[EXPERIMENTAL_SIGNED_MARGIN_MEDIAN_DB], Unavailable)


@pytest.mark.parametrize(
    "track,amplitude,expected_available", [(TB, 0, False), (TB, 1, False), (TA, 1, False), (TA, 0, True)]
)
def test_current_degenerate_sources_do_not_shrink_fixed_support(tmp_path, track, amplitude, expected_available):
    w = experimental_world(tmp_path)
    fixed = w.plan.prepared.experimental_supports[0]
    n = fixed.cell_count
    k = np.arange(34560)
    samples = np.rint(amplitude * np.sin(2 * np.pi * 1000 * k / 44100)).astype(np.int16)
    change_source(w, track, samples)
    result = measure(w)
    observed = values(result)
    assert observed[EXPERIMENTAL_SUPPORT_CELLS].value == n
    median = observed[EXPERIMENTAL_SIGNED_MARGIN_MEDIAN_DB]
    assert isinstance(median, MeasuredScalar if expected_available else Unavailable)
    if expected_available:
        assert observed[EXPERIMENTAL_ROBUST_EXCEEDANCE_CELLS].value == 0
    digest = w.analyzer.evidence_digest(result.value.observations[0].observation_id)
    evidence = json.loads((w.evidence_dir / f"{digest}.json").read_text())
    assert evidence["schema_version"] == 3 and "masker_energy" in evidence
    x = evidence["experimental"]
    assert x["cells"] == [list(cell) for cell in fixed.cells] and x["support_digest"] == fixed.digest
    assert x["comparison_available"] is expected_available
    assert x["baseline_sources"]["target_sha256"] == fixed.geometry.target_sha256
    assert w.plan.prepared.experimental_supports[0] == fixed


@pytest.mark.parametrize("tamper", ["missing", "changed"])
def test_restart_refuses_missing_or_changed_preparation_without_read(tmp_path, monkeypatch, tamper):
    w = experimental_world(tmp_path)
    path = w.plans_dir / f"{w.plan.prepared_digest}.json"
    if tamper == "missing":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b" ")
    import autonomous_mixing.adapters.dsp_analysis.directional_analysis as analysis

    monkeypatch.setattr(analysis, "read_dist10_member", lambda *a, **k: pytest.fail("read despite bad frozen plan"))
    result = measure(w, analyzer=w.make())
    assert isinstance(result, PortFailure)


def test_experimental_metrics_require_explicit_policy_before_read(tmp_path, monkeypatch):
    w = small_world(tmp_path)
    import autonomous_mixing.adapters.dsp_analysis.directional_analysis as analysis

    monkeypatch.setattr(analysis, "read_dist10_member", lambda *a, **k: pytest.fail("legacy experimental read"))
    assert isinstance(measure(w), PortFailure)


def test_experimental_evidence_keeps_dual_mono_source_metadata(tmp_path):
    from autonomous_mixing.core.records.masking_measurement import P1_EXACT_DUAL_MONO_PROFILE

    w = experimental_world(tmp_path, channels=2, profile=P1_EXACT_DUAL_MONO_PROFILE)
    result = measure(w)
    values(result)
    h = w.analyzer.evidence_digest(result.value.observations[0].observation_id)
    e = json.loads((w.evidence_dir / f"{h}.json").read_text())
    assert e["profile"] == [P1_EXACT_DUAL_MONO_PROFILE.name, "2"]
    assert e["experimental"]["status"] == "experimental_unvalidated"
    assert all(v["source_channel_count"] == 2 and v["analysis_channel_count"] == 1 for v in e["sources"].values())


def test_noise_reference_promotion_is_byte_exact_with_research_helper():
    import importlib.util

    p = Path(__file__).resolve().parents[2] / "scripts/fl_studio/analyze_trial.py"
    spec = importlib.util.spec_from_file_location("noise_parity", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    np.testing.assert_array_equal(rounding_noise_band_energy(), m.rounding_noise_band_energy())


def test_policy_requires_a_real_method_reference():
    class Pretender:
        def __eq__(self, other):
            return True

    with pytest.raises(ValueError):
        policy(method=Pretender())


def test_minimum_support_boundary_is_inclusive_without_changing_cells(tmp_path):
    original = experimental_world(tmp_path)
    n = original.plan.prepared.experimental_supports[0].cell_count
    for minimum, expected in ((n, MeasuredScalar), (n + 1, Unavailable)):
        directory = tmp_path / str(minimum)
        directory.mkdir()
        w = experimental_world(directory, p=policy(minimum_cells=minimum))
        assert w.plan.prepared.experimental_supports[0].cells == original.plan.prepared.experimental_supports[0].cells
        assert isinstance(values(measure(w))[EXPERIMENTAL_SIGNED_MARGIN_MEDIAN_DB], expected)


def test_strict_deadband_and_gaps_in_longest_run(tmp_path):
    from autonomous_mixing.adapters.dsp_analysis.directional_analysis import _experimental_values
    from autonomous_mixing.adapters.dsp_analysis.front_end import dist10_clip_to_bands
    from autonomous_mixing.adapters.dsp_analysis.masking import directional_masking_margin_db
    from autonomous_mixing.adapters.dsp_analysis.masking_preparation import noise_reference_digest, quiet_floor_bands

    w = experimental_world(tmp_path)
    k = np.arange(34560)
    a = dist10_clip_to_bands(
        np.rint(8192 * np.sin(2 * np.pi * 1000 * k / 44100)).astype(np.int16), sample_rate_hz=44100
    )
    b = dist10_clip_to_bands(
        np.rint(2048 * np.sin(2 * np.pi * 1000 * k / 44100)).astype(np.int16), sample_rate_hz=44100
    )
    cells = ((2, 6), (3, 6), (4, 6), (6, 6), (7, 6))
    t = a.thm.copy()
    for (call, band), m in zip(cells, (0.2, 0.5, 0.5, 0.5, 0.5), strict=True):
        t[call, band] = b.en[call, band] * 10 ** (m / 10)
    a = dataclasses.replace(a, thm=t)
    equal = float(directional_masking_margin_db(a.thm, b.en).margin_db[2, 6])
    p = policy(minimum_cells=1, margin_deadband_db=equal)
    noise = rounding_noise_band_energy()
    fixed = ExperimentalSupport(w.plan.prepared.supports[0], p.digest, noise_reference_digest(noise), cells)
    result, audit = _experimental_values(a, b, fixed, p, noise, quiet_floor_bands())
    assert audit["comparison_available"]
    assert result[EXPERIMENTAL_ROBUST_EXCEEDANCE_CELLS].value == 4
    assert result[EXPERIMENTAL_LONGEST_ROBUST_RUN_SECONDS].value == 2 * 576 / 44100


def test_noise_identity_mismatch_is_refused_before_current_file_read(tmp_path, monkeypatch):
    from autonomous_mixing.adapters.dsp_analysis.masking_preparation import write_content_addressed
    from autonomous_mixing.core.records.masking_measurement import MaskingMeasurementPlan, bound_specification

    w = experimental_world(tmp_path)
    original = w.plan.prepared
    altered = dataclasses.replace(
        original,
        experimental_supports=tuple(
            dataclasses.replace(s, noise_reference_sha256="0" * 64) for s in original.experimental_supports
        ),
    )
    digest = write_content_addressed(w.plans_dir, encode_prepared_plan(altered))
    w.spec = bound_specification("unit-altered", "1", digest)
    w.plan = MaskingMeasurementPlan(w.spec, w.plan.analyzer, digest, altered)
    import autonomous_mixing.adapters.dsp_analysis.directional_analysis as analysis

    monkeypatch.setattr(analysis, "read_dist10_member", lambda *a, **k: pytest.fail("wrong noise identity read"))
    result = measure(w, analyzer=w.make())
    assert isinstance(result, PortFailure) and "noise-reference" in result.message


@pytest.mark.parametrize("failure", ["cancel", "size"])
def test_experimental_preparation_failure_persists_no_manifest(tmp_path, monkeypatch, failure):
    from masking_support import ALIGNMENT, Clock, prep_job, prep_request

    import autonomous_mixing.adapters.dsp_analysis.masking_preparation as prep

    w = experimental_world(tmp_path)
    store = tmp_path / "failed"
    store.mkdir()
    interval = w.plan.prepared.supports[0].time_range
    req = prep_request(
        w.stems.state,
        w.stems.bindings,
        (prep_job(w.stems.artifacts[TA], w.stems.artifacts[TB], interval),),
        experimental_policy=policy(),
    )
    if failure == "size":
        monkeypatch.setattr(prep, "MAX_MANIFEST_BYTES", 1)
    clock = Clock(cancel_after=3) if failure == "cancel" else Clock()
    result = prep.MaskingPreparer(
        w.stems.roots, store=store, clock=clock, known_alignments=frozenset({ALIGNMENT.method})
    ).prepare(req)
    assert isinstance(result, PortFailure) and not list(store.iterdir())


def test_legacy_preparation_still_does_not_run_spectra(tmp_path, monkeypatch):
    import autonomous_mixing.adapters.dsp_analysis.masking_preparation as prep

    monkeypatch.setattr(prep, "dist10_clip_to_bands", lambda *a, **k: pytest.fail("legacy DSP during preparation"))
    w = small_world(tmp_path)
    assert w.plan.prepared.experimental_policy is None
    assert json.loads(encode_prepared_plan(w.plan.prepared))["schema_version"] == 1


def test_schema_two_requires_the_experimental_extension(tmp_path):
    from autonomous_mixing.adapters.dsp_analysis.masking_preparation import PreparationStoreError

    w = small_world(tmp_path)
    doc = json.loads(encode_prepared_plan(w.plan.prepared))
    doc["schema_version"] = 2
    with pytest.raises(PreparationStoreError):
        decode_prepared_plan(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode())


@pytest.mark.parametrize("audible_first", [True, False])
def test_different_current_artifacts_never_share_values_or_evidence(tmp_path, audible_first):
    from autonomous_mixing.core.records.integrity import ArtifactRootBinding

    w = experimental_world(tmp_path)
    state = w.stems.state
    muted = w.stems.write(
        TB, np.zeros(34560, dtype=np.int16), rate=44100, subtype="PCM_16", channels=1, start=0.0, revision="alternate"
    )
    muted = dataclasses.replace(muted, render_spec=VersionRef("alternate", "1"))
    state = dataclasses.replace(
        state, audio=dataclasses.replace(state.audio, artifacts=state.audio.artifacts + (muted,))
    )
    bindings = w.stems.bindings + (ArtifactRootBinding(muted.artifact_id, "inputs"),)
    interval = w.plan.prepared.supports[0].time_range
    audible = w.job(EXPERIMENTAL_SIGNED_MARGIN_MEDIAN_DB, TA, TB, time_range=interval, unit="dB")
    silent = w.job(
        EXPERIMENTAL_ROBUST_EXCEEDANCE_CELLS,
        TA,
        TB,
        time_range=interval,
        unit="cells",
        artifacts={TA: w.stems.artifacts[TA], TB: muted},
    )
    definitions = (audible, silent) if audible_first else (silent, audible)
    req = AnalysisRequest(
        state,
        AnalysisScope((TA, TB), interval),
        AnalysisSpec(w.spec, tuple(d.selector.metric for d in definitions)),
        definitions,
        bindings,
    )
    response = w.analyzer.analyze(req)
    observed = values(response)
    assert isinstance(observed[EXPERIMENTAL_SIGNED_MARGIN_MEDIAN_DB], MeasuredScalar)
    assert isinstance(observed[EXPERIMENTAL_ROBUST_EXCEEDANCE_CELLS], Unavailable)
    hashes = {o.metric: w.analyzer.evidence_digest(o.observation_id) for o in response.value.observations}
    assert len(set(hashes.values())) == 2
    evidence = json.loads((w.evidence_dir / (hashes[EXPERIMENTAL_ROBUST_EXCEEDANCE_CELLS] + ".json")).read_text())
    assert evidence["sources"]["target"]["artifact_id"] == str(muted.artifact_id)
    assert evidence["experimental"]["comparison_available"] is False


@pytest.mark.parametrize("boundary", ["energy", "quiet"])
def test_inclusive_db_boundaries_admit_equality_and_refuse_next_float_below(boundary):
    from types import SimpleNamespace

    from autonomous_mixing.adapters.dsp_analysis.masking_preparation import experimental_cell_flags, quiet_floor_bands

    noise, q = rounding_noise_band_energy(), quiet_floor_bands()
    p = policy()
    energy = noise * 10000.0
    t = q * (10 ** (0.02 / 10))
    a = SimpleNamespace(en=energy[None, :], thm=t[None, :], nonzero_samples=np.array([1]))
    b = SimpleNamespace(en=energy[None, :], nonzero_samples=np.array([1]))
    cells = tuple((0, band) for band in range(1, 21))
    flags = experimental_cell_flags(a, b, cells, p, noise, q)
    names = ("masker_low_energy", "target_low_energy") if boundary == "energy" else ("quiet_elevation_insufficient",)
    assert all(not flags[name].any() for name in names)
    if boundary == "energy":
        a.en = np.nextafter(energy, 0)[None, :]
        b.en = a.en
    else:
        a.thm = np.nextafter(t, 0)[None, :]
    flags = experimental_cell_flags(a, b, cells, p, noise, q)
    assert all(flags[name].all() for name in names)


def test_large_db_reserves_do_not_overflow_or_understate_a_finite_threshold():
    from types import SimpleNamespace

    from autonomous_mixing.adapters.dsp_analysis.masking_preparation import experimental_cell_flags, quiet_floor_bands

    noise, q = rounding_noise_band_energy(), quiet_floor_bands()
    energy = np.full((1, 21), np.finfo(float).max)
    a = SimpleNamespace(en=energy, thm=energy, nonzero_samples=np.array([1]))
    b = SimpleNamespace(en=energy, nonzero_samples=np.array([1]))
    cells = tuple((0, band) for band in range(1, 21))
    with np.errstate(all="raise"):
        flags = experimental_cell_flags(a, b, cells, policy(quiet_elevation_db=3090), noise, q)
        assert not flags["quiet_elevation_insufficient"][2]  # band 3: Q≈0.032, finite threshold ≈3.2e307
        huge = experimental_cell_flags(a, b, cells, policy(energy_above_noise_db=1e300), noise, q)
        assert huge["masker_low_energy"].all() and huge["target_low_energy"].all()


def test_quiet_floor_is_excluded_initially_even_with_zero_elevation_allowance(tmp_path):
    from types import SimpleNamespace

    from autonomous_mixing.adapters.dsp_analysis.masking_preparation import _screen_cells, quiet_floor_bands

    w = small_world(tmp_path)
    noise, q = rounding_noise_band_energy(), quiet_floor_bands()
    n = 12
    energy = np.tile(noise * 10000, (n, 1))
    threshold = np.tile(q, (n, 1))
    a = SimpleNamespace(en=energy, thm=threshold, nonzero_samples=np.ones(n, dtype=int))
    b = SimpleNamespace(en=energy, nonzero_samples=np.ones(n, dtype=int))
    assert _screen_cells(a, b, w.plan.prepared.supports[0], policy(quiet_elevation_db=0), noise, q) == ()


def test_promoted_noise_reference_matches_prechange_golden():
    from autonomous_mixing.adapters.dsp_analysis.masking_preparation import (
        rounding_noise_partition_energy,
        rounding_noise_per_bin,
    )

    golden = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures/p1_rounding_noise_reference_v1.json").read_text()
    )
    assert rounding_noise_per_bin() == golden["per_bin"]
    np.testing.assert_array_equal(rounding_noise_partition_energy(), golden["partitions"])
    np.testing.assert_array_equal(rounding_noise_band_energy(), golden["bands"])
