"""Contract tests for the P1 preparation and measurement-plan records (R1 Task 1; method spec sections 2-4).

Metadata-only artifacts: no file is read. The geometry under test is the dist10 call grid (call ``k`` eligible when
``k >= 2`` and ``576k + 256 <= N``; window ``[576k - 768, 576k + 256)``), not a calibrated choice.
"""

import dataclasses
from datetime import timedelta

import pytest
from masking_support import (
    ALIGNMENT,
    LIMIT,
    REGION,
    TA,
    TB,
    TC,
    UTC,
    metadata_artifact,
    prep_job,
    prep_request,
    source_of,
    state_of,
)

from autonomous_mixing.core.records.identifiers import TrackId, VersionRef
from autonomous_mixing.core.records.integrity import ArtifactRootBinding
from autonomous_mixing.core.records.masking_measurement import (
    P1_PROFILE,
    AlignmentProvenance,
    BandRegion,
    MaskingMeasurementPlan,
    MaskingPair,
    MaskingPreparationJob,
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
from autonomous_mixing.core.records.observation import TimeRange

N = 6912  # twelve granules
A, B = metadata_artifact(TA, N), metadata_artifact(TB, N)
STATE = state_of((A, B))
BINDINGS = tuple(ArtifactRootBinding(x.artifact_id, "inputs") for x in (A, B))
FULL = TimeRange(0.0, N / 44100)


def support(pair=MaskingPair(TA, TB), time_range=FULL, region=REGION, frames=N, start=0.0, **overrides):
    first, stop = interval_samples(time_range, timeline_start_seconds=start, sample_rate_hz=44100, frame_count=frames)
    call0, calls = complete_window_calls(frame_count=frames, start_sample=first, stop_sample=stop)
    fields = dict(pair=pair, region=region, time_range=time_range, alignment=ALIGNMENT, masker_artifact_id=A.artifact_id,
                  target_artifact_id=B.artifact_id, masker_sha256="a" * 64, target_sha256="b" * 64,
                  sample_rate_hz=44100, frame_count=frames, timeline_start_seconds=start, start_sample=first,
                  stop_sample=stop, first_call=call0, call_count=calls,
                  digest=support_digest(profile=P1_PROFILE, pair=pair, region=region, sample_rate_hz=44100,
                                        frame_count=frames, timeline_start_seconds=start, start_sample=first,
                                        stop_sample=stop, first_call=call0, call_count=calls))  # fmt: skip
    fields.update(overrides)
    return PreparedSupport(**fields)


def timing():
    return PreparationTiming(UTC, UTC + timedelta(seconds=1), 1.0)


def plan(supports=None, **overrides):
    fields = dict(profile=P1_PROFILE, supports=supports or (support(),), kind=SupportKind.GEOMETRIC,
                  qualification=QualificationStatus.UNCALIBRATED, max_file_bytes=LIMIT, max_decoded_bytes=LIMIT,
                  timing=timing())  # fmt: skip
    fields.update(overrides)
    return MaskingPreparedPlan(**fields)


# --- direction and source order -------------------------------------------------------------------------------


def test_opposite_directions_are_distinct_pairs_with_ordered_scopes():
    ab, ba = MaskingPair(TA, TB), MaskingPair(TB, TA)
    assert ab != ba and hash(ab) != hash(ba)
    assert ab.track_ids == (TA, TB) and ba.track_ids == (TB, TA)
    with pytest.raises(ValueError, match="two different tracks"):
        MaskingPair(TA, TA)


def test_both_directions_over_one_interval_are_two_jobs_in_one_request():
    request = prep_request(STATE, BINDINGS, (prep_job(A, B, FULL), prep_job(B, A, FULL)))
    (ab, ba) = request.jobs
    assert ab.pair == MaskingPair(TA, TB) and ab.masker_source.source_track_ids == (TA,)
    assert ba.pair == MaskingPair(TB, TA) and ba.masker_source.source_track_ids == (TB,)
    assert ab.key != ba.key


def test_sources_must_follow_the_pair_order():
    with pytest.raises(ValueError, match="masker_source"):
        MaskingPreparationJob(MaskingPair(TA, TB), FULL, REGION, source_of(B), source_of(A), ALIGNMENT)


def test_a_duplicate_direction_and_interval_is_refused():
    job = prep_job(A, B, FULL)
    with pytest.raises(ValueError):
        prep_request(STATE, BINDINGS, (job, job))
    with pytest.raises(ValueError, match="prepared once"):
        prep_request(STATE, BINDINGS, (job, dataclasses.replace(job, region=BandRegion("other", (3,)))))


# --- exact profile -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("overrides", [{"sample_rate_hz": 48000}, {"channel_count": 2}])
def test_sources_outside_the_profile_are_refused_not_converted(overrides):
    with pytest.raises(ValueError, match="native mono 44100"):
        MaskingPreparationJob(MaskingPair(TA, TB), FULL, REGION, source_of(A, **overrides), source_of(B), ALIGNMENT)


@pytest.mark.parametrize("overrides", [{"frame_count": N - 1}, {"timeline_start_seconds": 1.0}])
def test_unequal_length_or_start_is_refused_without_padding(overrides):
    with pytest.raises(ValueError, match="agree exactly"):
        MaskingPreparationJob(MaskingPair(TA, TB), FULL, REGION, source_of(A), source_of(B, **overrides), ALIGNMENT)


def test_only_the_p1_profile_and_multi_track_sources_are_refused():
    with pytest.raises(ValueError, match="profile"):
        prep_request(STATE, BINDINGS, (prep_job(A, B, FULL),), profile=VersionRef("p1.other", "1"))
    with pytest.raises(ValueError, match="single-track"):
        MaskingPreparationJob(MaskingPair(TA, TB), FULL, REGION, source_of(A, source_track_ids=(TA, TC)),
                              source_of(B), ALIGNMENT)  # fmt: skip


def test_bindings_must_cover_exactly_the_current_members():
    with pytest.raises(ValueError, match="exactly the state's current audio members"):
        prep_request(STATE, BINDINGS[:1], (prep_job(A, B, FULL),))


@pytest.mark.parametrize("field", ["max_file_bytes", "max_decoded_bytes"])
def test_resource_limits_are_positive_integers(field):
    with pytest.raises(ValueError):
        prep_request(STATE, BINDINGS, (prep_job(A, B, FULL),), **{field: 0})
    with pytest.raises(ValueError):
        prep_request(STATE, BINDINGS, (prep_job(A, B, FULL),), max_elapsed_seconds=0.0)


# --- alignment provenance ---------------------------------------------------------------------------------------


def test_alignment_provenance_is_required_and_declared():
    with pytest.raises(ValueError):
        MaskingPreparationJob(MaskingPair(TA, TB), FULL, REGION, source_of(A), source_of(B), None)
    with pytest.raises(ValueError):
        AlignmentProvenance(VersionRef("alignment", "1"), " ")


# --- sample geometry and complete windows --------------------------------------------------------------------------


def test_interval_boundaries_must_be_integral_sample_positions():
    assert interval_samples(TimeRange(576 / 44100, 2304 / 44100), timeline_start_seconds=0.0, sample_rate_hz=44100,
                            frame_count=N) == (576, 2304)  # fmt: skip
    with pytest.raises(ValueError, match="not an integral sample position"):
        interval_samples(TimeRange(0.01, 0.1 + 1e-9), timeline_start_seconds=0.0, sample_rate_hz=44100, frame_count=N)


def test_interval_outside_the_clip_is_refused():
    with pytest.raises(ValueError, match="outside the clip|not an integral"):
        interval_samples(TimeRange(0.0, (N + 1) / 44100), timeline_start_seconds=0.0, sample_rate_hz=44100,
                         frame_count=N)  # fmt: skip


def test_interval_is_relative_to_the_clip_timeline_start():
    assert interval_samples(TimeRange(2.0, 2.0 + 576 / 44100), timeline_start_seconds=2.0, sample_rate_hz=44100,
                            frame_count=N) == (0, 576)  # fmt: skip


@pytest.mark.parametrize(
    ("frames", "start", "stop", "expected"),
    [
        (6912, 0, 6912, (2, 10)),  # k = 2..11: 576*11 + 256 = 6592 <= 6912; k = 12 would need 7168
        (1408, 0, 1408, (2, 1)),  # first eligible call alone: 576*2 + 256 = 1408
        (1407, 0, 1407, (0, 0)),  # one sample short: zero calls, not padded
        (6912, 0, 1407, (0, 0)),
        (6912, 1, 6912, (2, 10)),  # call 2's window [384, 1408) lies inside [1, 6912)
        (6912, 385, 6912, (3, 9)),  # call 2's window starts at 384, before 385
        (6912, 384, 1408, (2, 1)),
        (6536, 0, 6536, (2, 9)),  # truncated final frame: 576*10 + 256 = 6016 <= 6536 < 576*11 + 256
    ],
)
def test_complete_window_calls(frames, start, stop, expected):
    assert complete_window_calls(frame_count=frames, start_sample=start, stop_sample=stop) == expected


def test_every_selected_window_is_complete_and_inside_the_interval():
    for start in range(0, 2000, 37):
        for stop in range(start, 6912, 211):
            first, count = complete_window_calls(frame_count=6912, start_sample=start, stop_sample=stop)
            calls = range(first, first + count)
            for k in calls:
                assert k >= 2 and 576 * k - 768 >= start and 576 * k + 256 <= stop
            # maximality: the neighbouring calls are not admissible
            for k in (first - 1, first + count) if count else ():
                assert not (k >= 2 and 576 * k - 768 >= start and 576 * k + 256 <= stop)


def test_zero_call_support_is_valid_and_has_zero_cells():
    short = support(time_range=TimeRange(0.0, 1000 / 44100))
    assert short.call_count == 0 and short.cell_count == 0


# --- band regions -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("bands", [(0, 1), (0,), (21,), (2, 1), (1, 1), (), (1.0,), [1, 2]])
def test_regions_use_only_ascending_distinct_bands_1_to_20(bands):
    with pytest.raises(ValueError):
        BandRegion("r", bands)


def test_band_0_is_refused_as_descriptive_only():
    with pytest.raises(ValueError, match="band 0 is descriptive only"):
        BandRegion("r", (0, 5))


# --- support identity ---------------------------------------------------------------------------------------------


def test_cells_are_calls_times_region_bands():
    assert support().cell_count == 10 * 20
    assert support(region=BandRegion("low", (1, 2, 3))).cell_count == 30


def test_equal_cell_counts_with_different_support_have_different_digests():
    # 10 calls x 2 bands and 5 calls x 4 bands: equal counts, different cells.
    two = support(region=BandRegion("two", (1, 2)))
    four = support(time_range=TimeRange(0.0, (576 * 6 + 256) / 44100), region=BandRegion("four", (1, 2, 3, 4)))
    assert two.cell_count == four.cell_count == 20 and two.digest != four.digest
    shifted = support(region=BandRegion("two", (3, 4)))
    assert shifted.cell_count == two.cell_count and shifted.digest != two.digest


def test_reversed_direction_has_a_different_support_identity():
    assert support(pair=MaskingPair(TA, TB)).digest != support(pair=MaskingPair(TB, TA)).digest


def test_support_fields_must_match_their_recomputation():
    good = support()
    for change in ({"first_call": 3}, {"call_count": 9}, {"start_sample": 1}, {"digest": "c" * 64},
                   {"frame_count": N - 1}):  # fmt: skip
        with pytest.raises(ValueError):
            dataclasses.replace(good, **change)
    with pytest.raises(ValueError, match="SHA-256"):
        dataclasses.replace(good, masker_sha256="A" * 64)


def test_support_digest_depends_on_timeline_and_geometry():
    base = support()
    later = support(start=1.0, time_range=TimeRange(1.0, 1.0 + N / 44100))
    assert (base.first_call, base.call_count) == (later.first_call, later.call_count) and base.digest != later.digest


# --- qualification ----------------------------------------------------------------------------------------------


def test_only_the_uncalibrated_status_and_geometric_kind_exist():
    assert [s.value for s in QualificationStatus] == ["uncalibrated"]
    assert [k.value for k in SupportKind] == ["geometric"]


@pytest.mark.parametrize("value", ["qualified", "calibrated", True, None])
def test_no_qualified_plan_can_be_constructed(value):
    with pytest.raises(ValueError):
        plan(qualification=value)
    with pytest.raises(ValueError):
        plan(kind=value)
    with pytest.raises(ValueError):
        prep_request(STATE, BINDINGS, (prep_job(A, B, FULL),), qualification=value)


def test_a_prepared_plan_finds_supports_by_direction_and_interval():
    ab, ba = support(), support(pair=MaskingPair(TB, TA))
    prepared = plan((ab, ba))
    assert prepared.support(MaskingPair(TA, TB), FULL) is ab
    assert prepared.support(MaskingPair(TB, TA), FULL) is ba
    assert prepared.support(MaskingPair(TA, TB), TimeRange(0.0, 0.1)) is None
    with pytest.raises(ValueError, match="exactly one prepared support"):
        plan((ab, dataclasses.replace(ab, masker_sha256="c" * 64)))


def test_measurement_plan_binds_specification_analyzer_and_manifest_digest():
    prepared = plan()
    spec = bound_specification("p1.spec", "1", "d" * 64)
    assert spec == VersionRef("p1.spec", "1+prepared.sha256." + "d" * 64)
    bound = MaskingMeasurementPlan(spec, VersionRef("analyzer", "1"), "d" * 64, prepared)
    assert bound.prepared is prepared
    with pytest.raises(ValueError, match="SHA-256"):
        MaskingMeasurementPlan(spec, VersionRef("analyzer", "1"), "xyz", prepared)


@pytest.mark.parametrize(
    "specification",
    [
        VersionRef("p1.spec", "1"),  # unbound
        bound_specification("p1.spec", "1", "e" * 64),  # bound to another prepared plan
        VersionRef("p1.spec", "+prepared.sha256." + "d" * 64),  # no own version
    ],
)
def test_specification_identity_must_carry_the_prepared_digest(specification):
    with pytest.raises(ValueError, match="prepared"):
        MaskingMeasurementPlan(specification, VersionRef("analyzer", "1"), "d" * 64, plan())


def test_bound_specification_validates_its_parts():
    with pytest.raises(ValueError):
        bound_specification("p1.spec", "1", "xyz")
    with pytest.raises(ValueError):
        bound_specification("p1.spec", "1+x", "d" * 64)


def test_timing_is_utc_and_ordered():
    with pytest.raises(ValueError):
        PreparationTiming(UTC, UTC - timedelta(seconds=1), 0.0)
    with pytest.raises(ValueError):
        PreparationTiming(UTC, UTC, -1.0)


def test_track_identity_is_generic():
    assert MaskingPair(TrackId("x"), TrackId("y")).track_ids == (TrackId("x"), TrackId("y"))
