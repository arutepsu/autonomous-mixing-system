"""Contract tests for verification evidence matching (plan docs/superpowers/plans/2026-11-06-verification-evidence-plan.md).

All records are labelled metadata fixtures: artifact lengths, rates, metric names, units and values are
structural examples, not evaluation data. A match states that declared metadata agree; it does not show
physical alignment, file integrity or that a measurement method is valid. No verdict is computed here.
"""

import dataclasses
import typing
from datetime import datetime, timezone

import pytest

import autonomous_mixing.core.verification.evidence as evidence_module
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.identifiers import (
    ArtifactId,
    CheckpointId,
    ObservationId,
    ProjectId,
    Revision,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.observation import (
    AnalysisScope,
    FrequencyRange,
    MeasuredScalar,
    Observation,
    TimeRange,
    Unavailable,
)
from autonomous_mixing.core.records.project import (
    ArtifactCheckpoint,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    Track,
    TrackGainState,
)
from autonomous_mixing.core.records.verification import MetricSelector
from autonomous_mixing.core.records.verification_evidence import (
    EvidenceMatch,
    EvidenceUnavailable,
    EvidenceUnavailableCode,
    MatchedEvidence,
    MeasurementDefinition,
    SourceRequirement,
    StateEvidence,
)
from autonomous_mixing.core.verification.evidence import match_evidence

UTC_NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
METRIC = VersionRef("metric-a", "1")
SPEC = VersionRef("analysis-spec", "1")
ANALYZER = VersionRef("analyzer", "0.1")
ALIGNMENT = VersionRef("declared-alignment", "1")  # a caller declaration; physical alignment is not checked
RENDER = VersionRef("render", "1")
RATE, SECONDS = 48000, 60
T1, T2 = TrackId("t1"), TrackId("t2")
PAIR_SCOPE = AnalysisScope((T1, T2), TimeRange(0.0, 60.0))

Code = EvidenceUnavailableCode


# --- state fixtures ----------------------------------------------------------------------------------


def _artifact(aid, tracks, revision, *, kind=ArtifactKind.STEM, **metadata) -> AudioArtifact:
    values = dict(
        timeline_start_seconds=0.0, sample_rate_hz=RATE, channel_count=2, frame_count=RATE * SECONDS, render_spec=RENDER
    )
    values.update(metadata)
    return AudioArtifact(
        ArtifactId(aid),
        ProjectId("p1"),
        Revision(revision),
        tuple(TrackId(t) for t in tracks),
        kind,
        f"outputs/{aid}.wav",
        values["timeline_start_seconds"],
        values["sample_rate_hz"],
        values["channel_count"],
        values["frame_count"],
        "d" * 64,
        values["render_spec"],
    )


def _state(revision, members) -> ProjectState:
    tracks = (Track(T1, "track 1"), Track(T2, "track 2"))
    snapshot = ProjectSnapshot(ProjectId("p1"), Revision(revision), UTC_NOW, tracks)
    gains = (TrackGainState(T1, _artifact("in-t1", ("t1",), "r0"), 0.0),)
    return ProjectState(
        snapshot,
        ProjectProcessingState(ProjectId("p1"), Revision(revision), gains),
        ArtifactCheckpoint(CheckpointId(f"cp-{revision}"), ProjectId("p1"), Revision(revision), members),
    )


# State r2 retains stem s1 produced at r1 and has a fresh stem s2 and mix; s2-old (r1) is superseded.
S1 = _artifact("s1", ("t1",), "r1")
S2 = _artifact("s2", ("t2",), "r2")
MIX = _artifact("mix", ("t1", "t2"), "r2", kind=ArtifactKind.MIX)
S2_OLD = _artifact("s2-old", ("t2",), "r1")
STATE = _state("r2", (S1, S2, MIX))
# A later state r3 with fresh IDs for the same subjects and metadata.
STATE_R3 = _state("r3", (_artifact("s1-r3", ("t1",), "r3"), _artifact("s2-r3", ("t2",), "r3")))


def _observation(oid="o1", sources=("s1", "s2"), *, result=None, scope=PAIR_SCOPE, metric=METRIC, spec=SPEC,
                 analyzer=ANALYZER) -> Observation:  # fmt: skip
    return Observation(
        ObservationId(oid),
        metric,
        tuple(ArtifactId(a) for a in sources),
        scope,
        MeasuredScalar(-14.0, "unit-a") if result is None else result,
        spec,
        analyzer,
        UTC_NOW,
    )


def _evidence(*observations, state=STATE, alignment=ALIGNMENT) -> StateEvidence:
    return StateEvidence(state, observations, alignment)


# --- definition fixtures -----------------------------------------------------------------------------


def _source(tracks, kind=ArtifactKind.STEM, **overrides) -> SourceRequirement:
    values = dict(
        kind=kind,
        source_track_ids=tuple(TrackId(t) for t in tracks),
        render_spec=RENDER,
        sample_rate_hz=RATE,
        channel_count=2,
        timeline_start_seconds=0.0,
        frame_count=RATE * SECONDS,
    )
    values.update(overrides)
    return SourceRequirement(**values)


STEM_PAIR = (_source(("t1",)), _source(("t2",)))
SUMMED_MIX = (_source(("t1", "t2"), ArtifactKind.MIX),)


def _definition(sources=STEM_PAIR, *, scope=PAIR_SCOPE, unit="unit-a", **overrides) -> MeasurementDefinition:
    values = dict(
        selector=MetricSelector(METRIC, scope),
        unit=unit,
        analysis_spec=SPEC,
        analyzer=ANALYZER,
        alignment=ALIGNMENT,
        sources=sources,
    )
    values.update(overrides)
    return MeasurementDefinition(**values)


def _code(result) -> EvidenceUnavailableCode:
    assert isinstance(result, EvidenceUnavailable), result
    return result.code


# --- records -----------------------------------------------------------------------------------------


def test_unavailable_codes_are_exact():
    assert [(c.name, c.value) for c in EvidenceUnavailableCode] == [
        ("MISSING", "missing"),
        ("AMBIGUOUS", "ambiguous"),
        ("SETTINGS_MISMATCH", "settings_mismatch"),
        ("INVALID_SOURCE", "invalid_source"),
        ("SUBJECT_MISMATCH", "subject_mismatch"),
        ("SOURCE_METADATA_MISMATCH", "source_metadata_mismatch"),
        ("UNAVAILABLE_RESULT", "unavailable_result"),
        ("UNIT_MISMATCH", "unit_mismatch"),
    ]


def test_evidence_match_union_is_exact():
    assert typing.get_args(EvidenceMatch) == (MatchedEvidence, EvidenceUnavailable)


@pytest.mark.parametrize(
    "record, fields",
    [
        (
            SourceRequirement,
            ["kind", "source_track_ids", "render_spec", "sample_rate_hz", "channel_count", "timeline_start_seconds",
             "frame_count"],
        ),
        (MeasurementDefinition, ["selector", "unit", "analysis_spec", "analyzer", "alignment", "sources"]),
        (StateEvidence, ["state", "observations", "alignment"]),
        (MatchedEvidence, ["observation"]),
        (EvidenceUnavailable, ["code", "explanation"]),
    ],
    ids=lambda v: getattr(v, "__name__", ""),
)  # fmt: skip
def test_records_have_exact_required_frozen_fields(record, fields):
    assert [f.name for f in dataclasses.fields(record)] == fields
    for field in dataclasses.fields(record):
        assert field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING
    assert record.__dataclass_params__.frozen


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "stem"},
        {"source_track_ids": ()},
        {"source_track_ids": (T1, T1)},
        {"source_track_ids": ("t1",)},
        {"source_track_ids": [T1]},
        {"render_spec": "render@1"},
        {"sample_rate_hz": 0},
        {"sample_rate_hz": True},
        {"channel_count": 0},
        {"channel_count": 3},
        {"timeline_start_seconds": -1.0},
        {"timeline_start_seconds": float("inf")},
        {"frame_count": -1},
    ],
)
def test_source_requirement_applies_the_artifact_metadata_constraints(overrides):
    with pytest.raises(ValueError):
        _source(("t1",), **overrides)


def test_source_requirement_keeps_values_including_zero_frames():
    tracks = (T2, T1)
    requirement = _source(("t1",), ArtifactKind.GROUP, source_track_ids=tracks, frame_count=0)
    assert requirement.source_track_ids is tracks and requirement.frame_count == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"unit": " "},
        {"sources": ()},
        {"sources": list(STEM_PAIR)},
        {"sources": (STEM_PAIR[0], "t2")},
        {"sources": (_source(("t1",)),)},  # does not cover the selector's t2
        {"selector": None},
        {"analysis_spec": "spec"},
        {"analyzer": None},
        {"alignment": "aligned"},
    ],
)
def test_measurement_definition_rejects_invalid_fields_and_uncovered_selector_tracks(overrides):
    with pytest.raises(ValueError):
        _definition(**overrides)


def test_measurement_definition_keeps_source_order():
    reordered = (STEM_PAIR[1], STEM_PAIR[0])
    assert _definition(reordered).sources is reordered


def test_state_evidence_allows_empty_and_rejects_repeated_ids_even_for_identical_records():
    assert _evidence().observations == ()
    observation = _observation()
    with pytest.raises(ValueError):
        _evidence(observation, observation)
    with pytest.raises(ValueError):
        StateEvidence(STATE, [observation], ALIGNMENT)
    with pytest.raises(ValueError):
        StateEvidence(None, (observation,), ALIGNMENT)
    with pytest.raises(ValueError):
        StateEvidence(STATE, (observation,), "aligned")
    # Distinct IDs with the same metric and scope stay representable, so the matcher can report ambiguity.
    assert len(_evidence(_observation("o1"), _observation("o2")).observations) == 2


def test_matched_evidence_requires_a_scalar_result_and_keeps_the_observation():
    observation = _observation()
    assert MatchedEvidence(observation).observation is observation
    with pytest.raises(ValueError):
        MatchedEvidence(_observation(result=Unavailable("silent region")))
    with pytest.raises(ValueError):
        MatchedEvidence(None)


@pytest.mark.parametrize("code, explanation", [("missing", "x"), (Code.MISSING, " "), (Code.MISSING, None)])
def test_evidence_unavailable_requires_a_code_and_nonblank_text(code, explanation):
    with pytest.raises(ValueError):
        EvidenceUnavailable(code, explanation)


# --- matching: success -------------------------------------------------------------------------------


def test_matching_returns_the_original_observation():
    observation = _observation()
    result = match_evidence(_definition(), _evidence(observation))
    assert isinstance(result, MatchedEvidence) and result.observation is observation


def test_a_measured_zero_matches():
    observation = _observation(result=MeasuredScalar(0.0, "unit-a"))
    assert match_evidence(_definition(), _evidence(observation)).observation is observation


def test_a_retained_older_production_member_matches():
    assert S1.revision != STATE.revision  # s1 was produced at r1 and is retained by r2
    assert isinstance(match_evidence(_definition(), _evidence(_observation())), MatchedEvidence)


def test_fresh_candidate_ids_for_the_same_subjects_match_without_comparing_artifact_ids():
    observation = _observation("o-r3", ("s1-r3", "s2-r3"))
    result = match_evidence(_definition(), _evidence(observation, state=STATE_R3))
    assert isinstance(result, MatchedEvidence) and result.observation is observation


def test_a_summed_mix_definition_matches_a_mix_observation():
    observation = _observation("o-mix", ("mix",))
    assert match_evidence(_definition(SUMMED_MIX), _evidence(observation)).observation is observation


def test_argument_types_are_checked():
    with pytest.raises(ValueError):
        match_evidence(None, _evidence(_observation()))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        match_evidence(_definition(), (_observation(),))  # type: ignore[arg-type]


# --- matching: metric/scope and ambiguity ------------------------------------------------------------


def test_no_observation_is_missing():
    assert _code(match_evidence(_definition(), _evidence())) is Code.MISSING


@pytest.mark.parametrize(
    "observation",
    [
        _observation(metric=VersionRef("metric-a", "2")),
        _observation(scope=AnalysisScope((T1, T2), TimeRange(0.0, 30.0))),
        _observation(scope=AnalysisScope((T2, T1), TimeRange(0.0, 60.0))),  # scope equality is exact
    ],
    ids=["metric-version", "scope-time", "scope-track-order"],
)
def test_metric_and_scope_must_match_exactly(observation):
    assert _code(match_evidence(_definition(), _evidence(observation))) is Code.MISSING


def test_two_exact_matches_are_ambiguous_before_settings_or_availability():
    usable = _observation("o1")
    other = _observation("o2", analyzer=VersionRef("analyzer", "0.2"), result=Unavailable("not measured"))
    result = match_evidence(_definition(), _evidence(usable, other))
    assert _code(result) is Code.AMBIGUOUS


def test_non_matching_observations_do_not_make_a_match_ambiguous():
    target = _observation("o1")
    unrelated = _observation("o2", metric=VersionRef("metric-b", "1"))
    assert match_evidence(_definition(), _evidence(target, unrelated)).observation is target


# --- matching: settings ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "observation, alignment",
    [
        (_observation(spec=VersionRef("analysis-spec", "2")), ALIGNMENT),
        (_observation(analyzer=VersionRef("analyzer", "0.2")), ALIGNMENT),
        (_observation(), VersionRef("declared-alignment", "2")),
    ],
    ids=["analysis-spec", "analyzer", "alignment"],
)
def test_settings_and_declared_alignment_must_match_exactly(observation, alignment):
    assert _code(match_evidence(_definition(), _evidence(observation, alignment=alignment))) is Code.SETTINGS_MISMATCH


def test_settings_are_checked_before_sources():
    observation = _observation(sources=("s1", "s2-old"), analyzer=VersionRef("analyzer", "0.2"))
    assert _code(match_evidence(_definition(), _evidence(observation))) is Code.SETTINGS_MISMATCH


# --- matching: sources -------------------------------------------------------------------------------


def test_a_superseded_nonmember_source_is_invalid():
    result = match_evidence(_definition(), _evidence(_observation(sources=("s1", "s2-old"))))
    assert _code(result) is Code.INVALID_SOURCE
    assert "s2-old" in result.explanation


def test_a_scope_beyond_the_source_timeline_is_invalid():
    scope = AnalysisScope((T1, T2), TimeRange(0.0, 90.0))
    result = match_evidence(_definition(scope=scope), _evidence(_observation(scope=scope)))
    assert _code(result) is Code.INVALID_SOURCE


def test_a_scope_band_above_the_source_nyquist_is_invalid():
    scope = AnalysisScope((T1, T2), TimeRange(0.0, 60.0), FrequencyRange(100.0, 30000.0))
    result = match_evidence(_definition(scope=scope), _evidence(_observation(scope=scope)))
    assert _code(result) is Code.INVALID_SOURCE


def test_only_value_errors_from_source_validation_become_invalid_source(monkeypatch):
    def rejecting(*args):
        raise ValueError("injected source rejection")

    monkeypatch.setattr(evidence_module, "validate_observation_sources", rejecting)
    result = match_evidence(_definition(), _evidence(_observation()))
    assert _code(result) is Code.INVALID_SOURCE and "injected source rejection" in result.explanation

    error = RuntimeError("validator bug")

    def broken(*args):
        raise error

    monkeypatch.setattr(evidence_module, "validate_observation_sources", broken)
    with pytest.raises(RuntimeError) as caught:
        match_evidence(_definition(), _evidence(_observation()))
    assert caught.value is error


@pytest.mark.parametrize(
    "definition, sources",
    [
        (_definition(STEM_PAIR), ("s2", "s1")),  # reordered stems
        (_definition(STEM_PAIR), ("mix",)),  # summed mix for a stem pair (same track coverage)
        (_definition(SUMMED_MIX), ("s1", "s2")),  # stem pair for a summed mix (same track coverage)
        (_definition((_source(("t1", "t2"), ArtifactKind.GROUP),)), ("mix",)),  # kind differs
        (_definition((_source(("t2", "t1"), ArtifactKind.MIX),)), ("mix",)),  # contributor order differs
    ],
    ids=["reordered-stems", "mix-for-pair", "pair-for-mix", "kind", "contributor-order"],
)
def test_source_subjects_must_match_in_count_kind_and_ordered_contributors(definition, sources):
    assert _code(match_evidence(definition, _evidence(_observation(sources=sources)))) is Code.SUBJECT_MISMATCH


@pytest.mark.parametrize(
    "overrides",
    [
        {"render_spec": VersionRef("render", "2")},
        {"sample_rate_hz": 44100},
        {"channel_count": 1},
        {"timeline_start_seconds": 0.5},
        {"frame_count": RATE * SECONDS - 1},
    ],
)
def test_source_metadata_must_match_exactly(overrides):
    definition = _definition((_source(("t1",)), _source(("t2",), **overrides)))
    assert _code(match_evidence(definition, _evidence(_observation()))) is Code.SOURCE_METADATA_MISMATCH


def test_subject_mismatch_takes_precedence_over_metadata_mismatch():
    definition = _definition((_source(("t1",), sample_rate_hz=44100), _source(("t2",))))
    observation = _observation(sources=("s2", "s1"))
    assert _code(match_evidence(definition, _evidence(observation))) is Code.SUBJECT_MISMATCH


# --- matching: result --------------------------------------------------------------------------------


def test_an_unavailable_result_reports_its_reason():
    result = match_evidence(_definition(), _evidence(_observation(result=Unavailable("silent region"))))
    assert _code(result) is Code.UNAVAILABLE_RESULT and "silent region" in result.explanation


def test_metadata_mismatch_takes_precedence_over_an_unavailable_result():
    definition = _definition((_source(("t1",)), _source(("t2",), channel_count=1)))
    observation = _observation(result=Unavailable("silent region"))
    assert _code(match_evidence(definition, _evidence(observation))) is Code.SOURCE_METADATA_MISMATCH


def test_scalar_units_must_match_exactly():
    observation = _observation(result=MeasuredScalar(-14.0, "unit-b"))
    assert _code(match_evidence(_definition(), _evidence(observation))) is Code.UNIT_MISMATCH


def test_an_unavailable_result_is_reported_before_any_unit_check():
    observation = _observation(result=Unavailable("not measured"))
    assert _code(match_evidence(_definition(unit="unit-b"), _evidence(observation))) is Code.UNAVAILABLE_RESULT
