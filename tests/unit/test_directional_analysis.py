"""Tests for the P1 directional analyzer (R1 Task 3).

Inputs are the stored dist10 fixture signals under ``tests/fixtures/mpeg_psy_model2_dist10`` (read unchanged; synthetic
tones and silence, unit-test signals, not evaluation data) and small labelled arrays for summary edge cases.

Two different claims are tested separately:

- **Capture replay** feeds probe B's captured ``thm``/``en`` (dist10's own values) into the production cell selection
  and margin kernel, and compares with an independent per-cell ``10 * (log10 T - log10 E)`` written here; it checks
  aggregation and arithmetic, not the Python front end.
- **Full chain** checks that the analyzer's T/E are exactly ``dist10_clip_to_bands`` of the verified counts (the
  reviewed front end, unchanged). The front end's residual against dist10 is reported by
  ``capture/compare_band_margins.py``; no tolerance is asserted here, and the pilot's 0.005552 dB is not used.
"""

import dataclasses
import hashlib
import json
import math

import numpy as np
import pytest
import soundfile as sf
from masking_support import (
    FIXTURE,
    FULL,
    RATE,
    TA,
    TB,
    TC,
    N,
    World,
    fixture_counts,
    state_of,
)

import autonomous_mixing.adapters.dsp_analysis.directional_analysis as analysis
from autonomous_mixing.adapters.dsp_analysis.directional_analysis import (
    ANALYZER,
    QUALIFIED_METRICS,
    RAW_GEOMETRIC_SUPPORT_CELLS,
    RAW_MASKER_SILENT_CELLS,
    RAW_MEAN_POSITIVE_MARGIN_DB,
    RAW_METRICS,
    RAW_NONFINITE_MARGIN_CELLS,
    RAW_POSITIVE_MARGIN_CELLS,
    RAW_POSITIVE_MARGIN_FRACTION,
    RAW_QUIET_FLOOR_THRESHOLD_CELLS,
    RAW_SIGNED_MARGIN_MEDIAN_DB,
    RAW_TARGET_SILENT_CELLS,
    UNCALIBRATED_REASON,
    directional_cells,
    quiet_floor_bands,
    summarize,
)
from autonomous_mixing.adapters.dsp_analysis.front_end import ClipBandAnalysis, dist10_clip_to_bands
from autonomous_mixing.adapters.dsp_analysis.masking_preparation import manifest_path
from autonomous_mixing.core.ports.analysis import AudioAnalyzer
from autonomous_mixing.core.records.identifiers import VersionRef
from autonomous_mixing.core.records.integrity import ArtifactRootBinding
from autonomous_mixing.core.records.masking_measurement import BandRegion, MaskingPair
from autonomous_mixing.core.records.observation import (
    AnalysisScope,
    AnalysisSpec,
    FrequencyRange,
    MeasuredScalar,
    TimeRange,
    Unavailable,
)
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, Success
from autonomous_mixing.core.records.verification import MetricSelector

MASKER, TARGET, SILENCE = "masker_1k_a8192", "target_2k_a1024", "silence_12granules"


def probe_b(name: str) -> list[dict]:
    path = FIXTURE / "expected_long_mapping" / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture
def world(tmp_path) -> World:
    return World(tmp_path, {TA: fixture_counts(MASKER), TB: fixture_counts(TARGET)})


def values(result) -> list:
    assert isinstance(result, Success), result
    return [o.result.value if isinstance(o.result, MeasuredScalar) else o.result for o in result.value.observations]


def independent_summaries(masker_counts, target_counts, calls, bands) -> dict:
    """Per-cell reference written without the production selection: plain loops over math.log10."""
    a = dist10_clip_to_bands(np.asarray(masker_counts), sample_rate_hz=RATE)
    b = dist10_clip_to_bands(np.asarray(target_counts), sample_rate_hz=RATE)
    q = quiet_floor_bands()
    margins, silent_b, silent_a, quiet = [], 0, 0, 0
    for k in calls:
        for band in bands:
            margins.append(10.0 * (math.log10(a.thm[k, band]) - math.log10(b.en[k, band])))
            silent_b += int(b.nonzero_samples[k] == 0)
            silent_a += int(a.nonzero_samples[k] == 0)
            quiet += int(a.thm[k, band] == q[band])
    ordered = sorted(margins)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    positive = sum(1 for m in margins if m > 0)
    return {"cells": len(margins), "median": median, "positive": positive, "fraction": positive / len(margins),
            "mean_positive": sum(max(m, 0.0) for m in margins) / len(margins), "target_silent": silent_b,
            "masker_silent": silent_a, "quiet": quiet}  # fmt: skip


ALL_RAW = (RAW_GEOMETRIC_SUPPORT_CELLS, RAW_SIGNED_MARGIN_MEDIAN_DB, RAW_POSITIVE_MARGIN_CELLS,
           RAW_POSITIVE_MARGIN_FRACTION, RAW_MEAN_POSITIVE_MARGIN_DB, RAW_TARGET_SILENT_CELLS, RAW_MASKER_SILENT_CELLS,
           RAW_QUIET_FLOOR_THRESHOLD_CELLS, RAW_NONFINITE_MARGIN_CELLS)  # fmt: skip


def check_raw(result_values, reference):
    cells, median, positive, fraction, mean_positive, target_silent, masker_silent, quiet, nonfinite = result_values
    assert cells == reference["cells"] and positive == reference["positive"] and nonfinite == 0
    assert (target_silent, masker_silent, quiet) == (reference["target_silent"], reference["masker_silent"],
                                                     reference["quiet"])  # fmt: skip
    # median and fraction are exact reorderings of the same floats; the mean is a sum of up to 200 terms
    assert median == pytest.approx(reference["median"], rel=0, abs=1e-12)
    assert fraction == reference["fraction"]
    assert mean_positive == pytest.approx(reference["mean_positive"], rel=1e-12, abs=1e-12)


# --- port and direction -------------------------------------------------------------------------------------------


def test_the_analyzer_satisfies_the_port(world):
    port: AudioAnalyzer = world.analyzer
    assert callable(port.analyze)


def test_both_directions_in_one_request_are_kept_apart(world):
    jobs = [world.job(m) for m in ALL_RAW] + [world.job(m, TB, TA) for m in ALL_RAW]
    result = world.analyzer.analyze(world.request(jobs))
    got = values(result)
    observations = result.value.observations
    a, b = world.stems.artifacts[TA].artifact_id, world.stems.artifacts[TB].artifact_id
    assert [o.scope.track_ids for o in observations] == [(TA, TB)] * 9 + [(TB, TA)] * 9
    assert [o.artifact_ids for o in observations] == [(a, b)] * 9 + [(b, a)] * 9
    assert all(o.analyzer == ANALYZER and o.analysis_spec == world.spec for o in observations)
    calls, bands = range(2, 12), range(1, 21)
    check_raw(got[:9], independent_summaries(fixture_counts(MASKER), fixture_counts(TARGET), calls, bands))
    check_raw(got[9:], independent_summaries(fixture_counts(TARGET), fixture_counts(MASKER), calls, bands))
    assert got[1] != got[10]  # the two directions differ


def test_reversed_source_order_is_unsupported(world):
    reversed_sources = world.job(RAW_SIGNED_MARGIN_MEDIAN_DB)
    reversed_sources = dataclasses.replace(reversed_sources, sources=tuple(reversed(reversed_sources.sources)))
    result = world.analyzer.analyze(world.request([reversed_sources]))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.UNSUPPORTED


def test_direction_without_prepared_support_is_invalid(tmp_path):
    world = World(tmp_path, {TA: fixture_counts(MASKER), TB: fixture_counts(TARGET)}, directions=((TA, TB),))
    result = world.analyzer.analyze(world.request([world.job(RAW_SIGNED_MARGIN_MEDIAN_DB, TB, TA)]))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.INVALID_INPUT
    assert "no prepared support" in result.message


# --- capture replay (dist10's own T/E) ------------------------------------------------------------------------------


def _captured(name: str) -> ClipBandAnalysis:
    lines = probe_b(name)
    column = lambda key: np.array([line[key] for line in lines])  # noqa: E731
    return ClipBandAnalysis(lines[0]["clip_samples"], RATE, column("call"), column("window_start"),
                            column("window_end"), column("window_end_within_clip").astype(bool) & (column("call") >= 2),
                            column("window_nonzero_samples"), column("en").astype(np.float64),
                            column("thm").astype(np.float64))  # fmt: skip


@pytest.mark.parametrize(("masker", "target"), [(MASKER, TARGET), (TARGET, MASKER), (SILENCE, TARGET),
                                                (MASKER, SILENCE), ("masker_1k_a81.92", "target_2k_a10.24")])  # fmt: skip
def test_capture_replay_margins_and_flags_match_independent_arithmetic(world, masker, target):
    a, b = _captured(masker), _captured(target)
    support = world.persisted.plan.supports[0]
    cells = directional_cells(a, b, support, quiet_floor_bands())
    lines_a, lines_b = probe_b(masker), probe_b(target)
    expected = [10.0 * (math.log10(lines_a[k]["thm"][band]) - math.log10(lines_b[k]["en"][band]))
                for k in range(2, 12) for band in range(1, 21)]  # fmt: skip
    assert cells.cells == tuple((k, band) for k in range(2, 12) for band in range(1, 21))
    # arithmetic-only claim: the kernel's log subtraction (log1p for near values) against math.log10 per cell
    assert np.max(np.abs(cells.margin_values - np.array(expected))) < 1e-9
    assert cells.quiet_floor.tolist() == [lines_a[k]["thm"][band] == lines_a[k]["thm_quiet"][band]
                                          for k in range(2, 12) for band in range(1, 21)]  # fmt: skip
    assert cells.target_silent.tolist() == [lines_b[k]["window_nonzero_samples"] == 0
                                            for k in range(2, 12) for band in range(1, 21)]  # fmt: skip


def test_quiet_floor_vector_equals_probe_b_thm_quiet_exactly():
    q = quiet_floor_bands()
    for name in (MASKER, TARGET, SILENCE):
        for line in probe_b(name):
            assert line["thm_quiet"] == q.tolist()
    assert not q.flags.writeable and q.shape == (21,)


# --- full chain ---------------------------------------------------------------------------------------------------


def test_evidence_t_and_e_are_exactly_the_reviewed_front_end_of_the_verified_counts(world):
    result = world.analyzer.analyze(world.request([world.job(RAW_SIGNED_MARGIN_MEDIAN_DB)]))
    evidence = json.loads(
        (
            world.evidence_dir / f"{world.analyzer.evidence_digest(result.value.observations[0].observation_id)}.json"
        ).read_text()
    )  # noqa: E501
    a = dist10_clip_to_bands(fixture_counts(MASKER), sample_rate_hz=RATE)
    b = dist10_clip_to_bands(fixture_counts(TARGET), sample_rate_hz=RATE)
    assert evidence["masker_threshold"] == a.thm.tolist() and evidence["target_energy"] == b.en.tolist()
    assert evidence["calls"]["call"] == list(range(12)) and evidence["calls"]["eligible"] == a.eligible.tolist()
    assert len(evidence["margin_db"]) == 12 and all(len(row) == 21 for row in evidence["margin_db"])


def test_history_from_call_0_is_kept_for_a_later_interval(tmp_path):
    later = TimeRange(1729 / RATE, N / RATE)  # first complete window: k >= ceil((1729 + 768) / 576) = 5
    world = World(tmp_path, {TA: fixture_counts(MASKER), TB: fixture_counts(TARGET)}, time_range=later)
    (support, _) = world.persisted.plan.supports
    calls = range(support.first_call, support.first_call + support.call_count)
    assert support.first_call == 5
    result = world.analyzer.analyze(world.request([world.job(m, time_range=later) for m in ALL_RAW]))
    check_raw(values(result), independent_summaries(fixture_counts(MASKER), fixture_counts(TARGET), calls,
                                                    range(1, 21)))  # fmt: skip
    # Cropping the clip to the interval would change the values (history and framing differ): not done.
    cropped = independent_summaries(fixture_counts(MASKER)[1729:], fixture_counts(TARGET)[1729:],
                                    range(2, 2 + support.call_count - 1), range(1, 21))  # fmt: skip
    assert values(result)[1] != cropped["median"]


def test_truncated_final_frame_keeps_only_complete_windows(tmp_path):
    counts = {TA: fixture_counts("target_2k_a1024_trunc6536"), TB: fixture_counts(MASKER)[:6536]}
    world = World(tmp_path, counts, time_range=TimeRange(0.0, 6536 / RATE))
    (support, _) = world.persisted.plan.supports
    assert (support.first_call, support.call_count) == (2, 9)
    tr = TimeRange(0.0, 6536 / RATE)
    result = world.analyzer.analyze(world.request([world.job(m, time_range=tr) for m in ALL_RAW]))
    check_raw(values(result), independent_summaries(counts[TA], counts[TB], range(2, 11), range(1, 21)))


# --- silence, quiet floor, empty support, qualified metrics ------------------------------------------------------


def test_silent_target_and_silent_masker_are_flagged_not_hidden(tmp_path):
    world = World(tmp_path, {TA: fixture_counts(MASKER), TC: fixture_counts(SILENCE)}, directions=((TA, TC), (TC, TA)))
    jobs = [world.job(m, TA, TC) for m in ALL_RAW] + [world.job(m, TC, TA) for m in ALL_RAW]
    got = values(world.analyzer.analyze(world.request(jobs)))
    # TA -> TC: the target is exactly silent in every support cell; its raw margins stay available and counted
    assert got[0] == 200 and got[5] == 200 and got[6] == 0 and got[8] == 0
    assert got[2] > 0  # positive raw margins over a silent target: not a masking diagnosis
    # TC -> TA: the masker is silent; its threshold is the quiet floor in every support cell
    assert got[9] == 200 and got[15] == 200 and got[16] == 200 and got[14] == 0


def test_empty_support_has_zero_cells_and_unavailable_summaries(tmp_path):
    short = TimeRange(0.0, 1407 / RATE)
    world = World(tmp_path, {TA: fixture_counts(MASKER), TB: fixture_counts(TARGET)}, time_range=short)
    got = values(world.analyzer.analyze(world.request([world.job(m, time_range=short) for m in ALL_RAW])))
    assert got[0] == 0
    assert all(isinstance(v, Unavailable) and "empty geometric support" in v.reason for v in got[1:])


def test_qualified_metrics_are_unavailable_as_uncalibrated(world):
    jobs = [world.job(m) for m in QUALIFIED_METRICS]
    got = values(world.analyzer.analyze(world.request(jobs)))
    assert len(got) == 7 and all(isinstance(v, Unavailable) and v.reason == UNCALIBRATED_REASON for v in got)


def test_raw_and_qualified_metric_ids_are_distinct():
    assert not set(RAW_METRICS) & set(QUALIFIED_METRICS)
    assert all(m.name.startswith("p1.raw.") for m in RAW_METRICS)
    assert all(not m.name.startswith("p1.raw.") for m in QUALIFIED_METRICS)


def _labelled(thm: np.ndarray, en: np.ndarray, nonzero: int = 1) -> ClipBandAnalysis:
    calls = thm.shape[0]
    k = np.arange(calls)
    return ClipBandAnalysis(N, RATE, k, 576 * k - 768, 576 * k + 256, (k >= 2) & (576 * k + 256 <= N),
                            np.full(calls, nonzero), en, thm)  # fmt: skip


def test_equality_is_not_positive_and_undefined_margins_are_unavailable(world):
    support = world.persisted.plan.supports[0]
    ones = np.ones((12, 21))
    equal = directional_cells(_labelled(ones, ones), _labelled(ones, ones), support, np.zeros(21))
    assert summarize(RAW_POSITIVE_MARGIN_CELLS, equal).value == 0.0  # M == 0 exactly: strict comparison
    assert summarize(RAW_MEAN_POSITIVE_MARGIN_DB, equal).value == 0.0
    zero = ones.copy()
    zero[5, 3] = 0.0
    undefined = directional_cells(_labelled(zero, ones), _labelled(ones, ones), support, np.zeros(21))
    assert summarize(RAW_NONFINITE_MARGIN_CELLS, undefined).value == 1.0
    for metric in (RAW_SIGNED_MARGIN_MEDIAN_DB, RAW_POSITIVE_MARGIN_CELLS, RAW_POSITIVE_MARGIN_FRACTION,
                   RAW_MEAN_POSITIVE_MARGIN_DB):  # fmt: skip
        assert isinstance(summarize(metric, undefined), Unavailable)
    # nonpositive cells stay in the mean's denominator
    mixed = ones.copy()
    mixed[2, 1] = 10.0
    tiny = directional_cells(_labelled(mixed, ones), _labelled(ones, ones), support, np.zeros(21))
    assert summarize(RAW_MEAN_POSITIVE_MARGIN_DB, tiny).value == pytest.approx(10.0 / 200, rel=1e-15)
    assert summarize(RAW_POSITIVE_MARGIN_FRACTION, tiny).value == 1 / 200


def test_near_zero_and_low_energy_margins_keep_their_sign(world):
    support = world.persisted.plan.supports[0]
    ones = np.ones((12, 21))
    above, below = ones * (1 + 2**-52), ones * (1 - 2**-53)
    low = np.full((12, 21), 1e-300)
    assert summarize(RAW_POSITIVE_MARGIN_CELLS, directional_cells(_labelled(above, ones), _labelled(ones, ones),
                                                                  support, np.zeros(21))).value == 200  # fmt: skip
    assert summarize(RAW_POSITIVE_MARGIN_CELLS, directional_cells(_labelled(below, ones), _labelled(ones, ones),
                                                                  support, np.zeros(21))).value == 0  # fmt: skip
    value = summarize(RAW_SIGNED_MARGIN_MEDIAN_DB, directional_cells(_labelled(low * 10, ones), _labelled(ones, low),
                                                                     support, np.zeros(21))).value  # fmt: skip
    assert value == pytest.approx(10.0, abs=1e-9)


# --- current-artifact binding and geometry --------------------------------------------------------------------------


def _later_state(world: World, track, counts, **artifact_overrides):
    artifact = world.stems.write(track, counts, rate=RATE, subtype="PCM_16", channels=1, start=0.0, revision="r2")
    artifact = dataclasses.replace(artifact, **artifact_overrides) if artifact_overrides else artifact
    members = tuple(artifact if a.source_track_ids == (track,) else a for a in world.stems.artifacts.values())
    return members, state_of(members), tuple(ArtifactRootBinding(a.artifact_id, "inputs") for a in members)


def test_a_later_state_binds_its_own_current_artifacts_on_the_same_support(world):
    quieter = (fixture_counts(MASKER) // 4).astype(np.int16)
    members, state, bindings = _later_state(world, TA, quieter)
    artifacts = {m.source_track_ids[0]: m for m in members}
    result = world.analyzer.analyze(world.request([world.job(m, artifacts=artifacts) for m in ALL_RAW],
                                                  state=state, bindings=bindings))  # fmt: skip
    got = values(result)
    assert result.value.observations[0].artifact_ids == (artifacts[TA].artifact_id, artifacts[TB].artifact_id)
    check_raw(got, independent_summaries(quieter, fixture_counts(TARGET), range(2, 12), range(1, 21)))


def test_changed_sample_geometry_is_refused_against_the_frozen_support(world):
    members, state, bindings = _later_state(world, TA, fixture_counts(MASKER)[:6000])
    artifacts = {m.source_track_ids[0]: m for m in members}
    result = world.analyzer.analyze(world.request([world.job(RAW_SIGNED_MARGIN_MEDIAN_DB, artifacts=artifacts)],
                                                  state=state, bindings=bindings))  # fmt: skip
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.INVALID_INPUT
    assert "geometry" in result.message


def test_later_file_with_another_subtype_is_unavailable(world):
    members, state, bindings = _later_state(world, TA, fixture_counts(MASKER))
    path = world.stems.root / members[0].location
    sf.write(str(path), fixture_counts(MASKER).astype(np.float64) / 32768.0, RATE, format="WAV", subtype="PCM_24")
    replaced = dataclasses.replace(members[0], content_hash=hashlib.sha256(path.read_bytes()).hexdigest())
    members = (replaced, members[1])
    artifacts = {m.source_track_ids[0]: m for m in members}
    result = world.analyzer.analyze(world.request([world.job(RAW_SIGNED_MARGIN_MEDIAN_DB, artifacts=artifacts)],
                                                  state=state_of(members), bindings=bindings))  # fmt: skip
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.UNAVAILABLE
    assert "unsupported_subtype" in result.message


def test_altered_current_file_is_unavailable(world):
    path = world.stems.root / world.stems.artifacts[TB].location
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(bytes(data))
    result = world.analyzer.analyze(world.request([world.job(RAW_SIGNED_MARGIN_MEDIAN_DB)]))
    assert isinstance(result, PortFailure) and "hash_mismatch" in result.message


def test_alignment_identity_must_match_the_prepared_provenance(world):
    job = world.job(RAW_SIGNED_MARGIN_MEDIAN_DB, alignment=VersionRef("alignment.other", "1"))
    result = world.analyzer.analyze(world.request([job]))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.INVALID_INPUT


# --- plan registry ------------------------------------------------------------------------------------------------


def test_unregistered_specification_is_unsupported(world):
    request = world.request([world.job(RAW_SIGNED_MARGIN_MEDIAN_DB)])
    other = dataclasses.replace(request, specification=AnalysisSpec(VersionRef("other", "1"),
                                                                     request.specification.metric_ids),
                                definitions=tuple(dataclasses.replace(d, analysis_spec=VersionRef("other", "1"))
                                                  for d in request.definitions))  # fmt: skip
    result = world.analyzer.analyze(other)
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.UNSUPPORTED


def test_missing_or_altered_manifest_refuses_analysis(world):
    path = manifest_path(world.plans_dir, world.plan.prepared_digest)
    original = path.read_bytes()
    path.write_bytes(original.replace(b'"call_count":10', b'"call_count":11'))
    result = world.analyzer.analyze(world.request([world.job(RAW_SIGNED_MARGIN_MEDIAN_DB)]))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.UNAVAILABLE
    path.unlink()
    result = world.analyzer.analyze(world.request([world.job(RAW_SIGNED_MARGIN_MEDIAN_DB)]))
    assert isinstance(result, PortFailure) and "missing" in result.message
    assert list(world.plans_dir.iterdir()) == []  # never regenerated


def test_registered_plan_must_equal_the_stored_manifest(world):
    plan = world.plan.prepared
    forged = dataclasses.replace(plan, supports=plan.supports[:1])
    analyzer = world.make(plans=(dataclasses.replace(world.plan, prepared=forged),))
    result = analyzer.analyze(world.request([world.job(RAW_SIGNED_MARGIN_MEDIAN_DB)]))
    assert isinstance(result, PortFailure) and "differs from the registered plan" in result.message


def test_construction_validates_registry_and_limits(world):
    with pytest.raises(ValueError, match="analyzer"):
        world.make(plans=(dataclasses.replace(world.plan, analyzer=VersionRef("other", "1")),))
    with pytest.raises(ValueError, match="two plans"):
        world.make(plans=(world.plan, world.plan))
    with pytest.raises(ValueError):
        world.make(max_evidence_bytes=0)


# --- unsupported and malformed jobs --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"unit": "dBFS"},
        {"analyzer": VersionRef("autonomous_mixing.file_level_analyzer", "1")},
        {"selector": MetricSelector(VersionRef("p1.raw.unknown", "1"), AnalysisScope((TA, TB), FULL))},
        {
            "selector": MetricSelector(
                RAW_SIGNED_MARGIN_MEDIAN_DB, AnalysisScope((TA, TB), FULL, FrequencyRange(100.0, 200.0))
            )
        },
    ],
)
def test_unsupported_jobs_fail_the_whole_batch(world, change):
    good = world.job(RAW_SIGNED_MARGIN_MEDIAN_DB, TB, TA)
    bad = dataclasses.replace(world.job(RAW_SIGNED_MARGIN_MEDIAN_DB), **change)
    result = world.analyzer.analyze(world.request([good, bad]))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.UNSUPPORTED
    assert list(world.evidence_dir.iterdir()) == []


def test_single_source_job_is_unsupported(world):
    job = world.job(RAW_SIGNED_MARGIN_MEDIAN_DB)
    single = dataclasses.replace(job, selector=MetricSelector(job.selector.metric, AnalysisScope((TA,), FULL)),
                                 sources=job.sources[:1])  # fmt: skip
    result = world.analyzer.analyze(world.request([single]))
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.UNSUPPORTED


# --- evidence -----------------------------------------------------------------------------------------------------


def test_evidence_is_strict_bounded_and_backs_each_observation(world):
    jobs = [world.job(RAW_SIGNED_MARGIN_MEDIAN_DB), world.job(RAW_SIGNED_MARGIN_MEDIAN_DB, TB, TA),
            world.job(RAW_POSITIVE_MARGIN_CELLS)]  # fmt: skip
    result = world.analyzer.analyze(world.request(jobs))
    digests = [world.analyzer.evidence_digest(o.observation_id) for o in result.value.observations]
    assert digests[0] == digests[2] != digests[1] and len(list(world.evidence_dir.iterdir())) == 2
    payload = (world.evidence_dir / f"{digests[0]}.json").read_bytes()
    evidence = json.loads(payload, parse_constant=lambda name: pytest.fail(f"non-finite constant {name}"))
    assert evidence["schema"] == "autonomous_mixing.p1_directional_evidence" and evidence["bands"] == 21
    assert evidence["support"]["kind"] == "geometric" and evidence["support"]["qualification"] == "uncalibrated"
    assert evidence["support"]["pair"] == ["ta", "tb"] and evidence["prepared_digest"] == world.plan.prepared_digest
    assert evidence["sources"]["masker"]["sha256"] == world.stems.artifacts[TA].content_hash
    assert evidence["sources"]["masker"]["subtype"] == "PCM_16"
    assert len(evidence["support_cells"]) == 200 == len(evidence["cell_flags"]["target_silent"])
    assert [o["id"] for o in evidence["observations"]] == [str(result.value.observations[i].observation_id)
                                                           for i in (0, 2)]  # fmt: skip
    assert evidence["quiet_floor"]["q"] == quiet_floor_bands().tolist()


def test_undefined_margins_are_null_in_evidence_never_nan():
    grid = analysis._grid(np.array([[1.0, np.nan, -np.inf]]))
    assert grid == [[1.0, None, None]]


def test_evidence_write_failure_or_size_returns_no_success(world, monkeypatch):
    small = world.make(max_evidence_bytes=1000)
    result = small.analyze(world.request([world.job(RAW_SIGNED_MARGIN_MEDIAN_DB)]))
    assert isinstance(result, PortFailure) and "max_evidence_bytes" in result.message

    def broken(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(analysis, "write_content_addressed", broken)
    result = world.analyzer.analyze(world.request([world.job(RAW_SIGNED_MARGIN_MEDIAN_DB)]))
    assert isinstance(result, PortFailure) and "disk full" in result.message
    assert list(world.evidence_dir.iterdir()) == []


def test_every_success_is_validated_against_the_original_request(world, monkeypatch):
    seen = []
    original = analysis.validate_analysis_response
    monkeypatch.setattr(
        analysis, "validate_analysis_response", lambda req, resp: seen.append(req) or original(req, resp)
    )
    request = world.request([world.job(RAW_SIGNED_MARGIN_MEDIAN_DB)])
    assert isinstance(world.analyzer.analyze(request), Success) and seen == [request]


def test_inputs_and_fixtures_are_not_modified(world):
    before = {p: p.read_bytes() for p in world.stems.root.iterdir()}
    world.analyzer.analyze(world.request([world.job(m) for m in ALL_RAW]))
    assert {p: p.read_bytes() for p in world.stems.root.iterdir()} == before


def test_region_selects_its_bands(tmp_path):
    region = BandRegion("mid", (8, 9, 10))
    world = World(tmp_path, {TA: fixture_counts(MASKER), TB: fixture_counts(TARGET)}, region=region)
    got = values(world.analyzer.analyze(world.request([world.job(m) for m in ALL_RAW])))
    check_raw(got, independent_summaries(fixture_counts(MASKER), fixture_counts(TARGET), range(2, 12), (8, 9, 10)))
    assert MaskingPair(TA, TB) == world.persisted.plan.supports[0].pair
