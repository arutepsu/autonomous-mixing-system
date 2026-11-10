"""Diagnosis coverage, candidate and evidence records and their reference check (diagnosis-records plan, 2A).

All records are metadata fixtures. Family labels, method names and free-text strings are structural
examples only: no detector, threshold, calibrated probability or musical role is implied, and track
names deliberately carry no role.
"""

import dataclasses
from datetime import datetime, timezone

import pytest

from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.diagnosis import (
    DiagnosisCoverage,
    DiagnosisCoverageStatus,
    DiagnosisReport,
    DiagnosticEvidence,
    ProblemCandidate,
    ProblemFamily,
    UncertaintyRepresentation,
    validate_diagnosis_references,
)
from autonomous_mixing.core.records.identifiers import (
    ArtifactId,
    CheckpointId,
    DiagnosisCoverageId,
    ObservationId,
    ProblemCandidateId,
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

UTC_NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
METHOD_A = VersionRef("method-a", "1")
METHOD_B = VersionRef("method-b", "1")
METRIC = VersionRef("metric-a", "1")

Family = ProblemFamily
Status = DiagnosisCoverageStatus
Repr = UncertaintyRepresentation


# --- fixtures ----------------------------------------------------------------------------------------


def _scope(tracks=("t1", "t2"), start=0.0, end=60.0, freq=None) -> AnalysisScope:
    return AnalysisScope(tuple(TrackId(t) for t in tracks), TimeRange(start, end), freq)


def _evidence(*, insufficient=False, missing=(), limitations=("uncalibrated description",)) -> DiagnosticEvidence:
    return DiagnosticEvidence(
        strength="qualitative: moderate",
        uncertainty="not calibrated",
        representation=Repr.QUALITATIVE,
        insufficient_evidence=insufficient,
        limitations=limitations,
        missing_information=missing,
    )


INSUFFICIENT = dict(insufficient=True, missing=("measurement for t2 unavailable",))


def _coverage(cid="cov-1", family=Family.MASKING, scope=None, status=Status.ASSESSED, method=METHOD_A):
    return DiagnosisCoverage(
        coverage_id=DiagnosisCoverageId(cid),
        family=family,
        scope=_scope() if scope is None else scope,
        status=status,
        method=method,
        explanation="method ran over the declared scope",
    )


def _candidate(
    cid="cand-1",
    coverage="cov-1",
    *,
    project="p1",
    revision="r2",
    family=Family.MASKING,
    scope=None,
    evidence_ids=("o1",),
    method=METHOD_A,
    evidence=None,
) -> ProblemCandidate:
    return ProblemCandidate(
        candidate_id=ProblemCandidateId(cid),
        coverage_id=DiagnosisCoverageId(coverage),
        project_id=ProjectId(project),
        revision=Revision(revision),
        family=family,
        scope=_scope() if scope is None else scope,
        evidence_ids=tuple(ObservationId(o) for o in evidence_ids),
        method=method,
        evidence=_evidence() if evidence is None else evidence,
    )


def _report(coverage=None, candidates=None, *, project="p1", revision="r2") -> DiagnosisReport:
    return DiagnosisReport(
        project_id=ProjectId(project),
        revision=Revision(revision),
        coverage=(_coverage(),) if coverage is None else coverage,
        candidates=(_candidate(),) if candidates is None else candidates,
    )


# --- identifiers and enums ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", [ProblemCandidateId, DiagnosisCoverageId])
def test_new_identifiers_follow_the_identifier_pattern(cls):
    assert str(cls("x-1")) == "x-1"
    assert cls("x-1") == cls("x-1")
    assert cls("x-1") != ObservationId("x-1")
    for bad in ("", "   ", 1, None):
        with pytest.raises(ValueError):
            cls(bad)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cls("x").value = "y"  # type: ignore[misc]


def test_enum_names_and_values_are_exact():
    assert [(m.name, m.value) for m in ProblemFamily] == [
        ("MASKING", "masking"),
        ("FREQUENCY_BUILDUP", "frequency_buildup"),
        ("PHASE_INTERACTION", "phase_interaction"),
    ]
    assert [(m.name, m.value) for m in UncertaintyRepresentation] == [
        ("QUALITATIVE", "qualitative"),
        ("UNCALIBRATED", "uncalibrated"),
    ]
    assert [(m.name, m.value) for m in DiagnosisCoverageStatus] == [
        ("ASSESSED", "assessed"),
        ("UNAVAILABLE", "unavailable"),
        ("INCOMPLETE", "incomplete"),
    ]


@pytest.mark.parametrize(
    ("record", "fields"),
    [
        (
            DiagnosticEvidence,
            [
                ("strength", str),
                ("uncertainty", str),
                ("representation", UncertaintyRepresentation),
                ("insufficient_evidence", bool),
                ("limitations", tuple[str, ...]),
                ("missing_information", tuple[str, ...]),
            ],
        ),
        (
            DiagnosisCoverage,
            [
                ("coverage_id", DiagnosisCoverageId),
                ("family", ProblemFamily),
                ("scope", AnalysisScope),
                ("status", DiagnosisCoverageStatus),
                ("method", VersionRef | None),
                ("explanation", str),
            ],
        ),
        (
            ProblemCandidate,
            [
                ("candidate_id", ProblemCandidateId),
                ("coverage_id", DiagnosisCoverageId),
                ("project_id", ProjectId),
                ("revision", Revision),
                ("family", ProblemFamily),
                ("scope", AnalysisScope),
                ("evidence_ids", tuple[ObservationId, ...]),
                ("method", VersionRef),
                ("evidence", DiagnosticEvidence),
            ],
        ),
        (
            DiagnosisReport,
            [
                ("project_id", ProjectId),
                ("revision", Revision),
                ("coverage", tuple[DiagnosisCoverage, ...]),
                ("candidates", tuple[ProblemCandidate, ...]),
            ],
        ),
    ],
)
def test_record_fields_are_exact_and_required(record, fields):
    assert [(f.name, f.type) for f in dataclasses.fields(record)] == fields
    assert all(
        f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
        for f in dataclasses.fields(record)
    )


def test_no_numeric_probability_or_calibrated_member_exists():
    assert "CALIBRATED" not in UncertaintyRepresentation.__members__
    for record in (DiagnosticEvidence, ProblemCandidate, DiagnosisCoverage, DiagnosisReport):
        assert not {f.type for f in dataclasses.fields(record)} & {float, int}


@pytest.mark.parametrize(
    ("instance", "field", "value"),
    [
        (_evidence(), "insufficient_evidence", True),
        (_coverage(), "status", Status.UNAVAILABLE),
        (_candidate(), "method", METHOD_B),
        (_report(), "candidates", ()),
    ],
)
def test_records_are_frozen(instance, field, value):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field, value)


# --- DiagnosticEvidence ------------------------------------------------------------------------------


def test_evidence_preserves_text_exactly():
    evidence = DiagnosticEvidence("  weak ", "unknown\n", Repr.UNCALIBRATED, False, (" a ",), ())
    assert (evidence.strength, evidence.uncertainty, evidence.limitations) == ("  weak ", "unknown\n", (" a ",))


@pytest.mark.parametrize(
    "overrides",
    [
        {"strength": ""},
        {"strength": "  "},
        {"strength": 1},
        {"uncertainty": ""},
        {"uncertainty": None},
        {"representation": "qualitative"},
        {"representation": Status.ASSESSED},
        {"insufficient_evidence": 1},
        {"insufficient_evidence": 0},
        {"insufficient_evidence": "yes"},
        {"insufficient_evidence": None},
        {"limitations": ["a"]},
        {"limitations": ("a", "a")},
        {"limitations": ("a", " ")},
        {"limitations": ("a", 1)},
        {"limitations": "a"},
        {"missing_information": ["x"]},
        {"missing_information": ("x", "x")},
        {"missing_information": ("",)},
    ],
)
def test_evidence_rejects_invalid_fields(overrides):
    values = dataclasses.asdict(_evidence())
    with pytest.raises(ValueError):
        DiagnosticEvidence(**{**values, **overrides})


def test_insufficient_evidence_requires_a_stated_missing_item():
    with pytest.raises(ValueError):
        _evidence(insufficient=True, missing=())
    assert _evidence(**INSUFFICIENT).missing_information == ("measurement for t2 unavailable",)


def test_representation_and_insufficiency_are_independent_facts():
    for representation in UncertaintyRepresentation:
        for insufficient in (False, True):
            missing = ("x",) if insufficient else ()
            evidence = DiagnosticEvidence("s", "u", representation, insufficient, (), missing)
            assert (evidence.representation, evidence.insufficient_evidence) == (representation, insufficient)


# --- DiagnosisCoverage -------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [Status.ASSESSED, Status.INCOMPLETE])
def test_assessed_and_incomplete_coverage_require_a_method(status):
    assert _coverage(status=status).method == METHOD_A
    with pytest.raises(ValueError):
        _coverage(status=status, method=None)


@pytest.mark.parametrize("method", [None, METHOD_A])
def test_unavailable_coverage_permits_no_method_or_an_unusable_one(method):
    assert _coverage(status=Status.UNAVAILABLE, method=method).method == method


@pytest.mark.parametrize(
    "overrides",
    [
        {"coverage_id": "cov-1"},
        {"coverage_id": ProblemCandidateId("cov-1")},
        {"family": "masking"},
        {"scope": None},
        {"status": "assessed"},
        {"method": ("method-a", "1")},
        {"explanation": ""},
        {"explanation": "  "},
        {"explanation": None},
    ],
)
def test_coverage_rejects_invalid_fields(overrides):
    values = {f.name: getattr(_coverage(), f.name) for f in dataclasses.fields(DiagnosisCoverage)}
    with pytest.raises(ValueError):
        DiagnosisCoverage(**{**values, **overrides})


# --- ProblemCandidate --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"candidate_id": "cand-1"},
        {"candidate_id": DiagnosisCoverageId("cand-1")},
        {"coverage_id": ProblemCandidateId("cov-1")},
        {"project_id": "p1"},
        {"revision": "r2"},
        {"family": "masking"},
        {"scope": None},
        {"evidence_ids": ()},
        {"evidence_ids": [ObservationId("o1")]},
        {"evidence_ids": (ObservationId("o1"), ObservationId("o1"))},
        {"evidence_ids": ("o1",)},
        {"method": None},
        {"evidence": "strong"},
    ],
)
def test_candidate_rejects_invalid_fields(overrides):
    values = {f.name: getattr(_candidate(), f.name) for f in dataclasses.fields(ProblemCandidate)}
    with pytest.raises(ValueError):
        ProblemCandidate(**{**values, **overrides})


def test_candidate_affected_tracks_are_its_scope_tracks():
    candidate = _candidate(scope=_scope(("t2", "t1")))
    assert candidate.scope.track_ids == (TrackId("t2"), TrackId("t1"))
    assert not hasattr(candidate, "track_ids")


# --- DiagnosisReport ---------------------------------------------------------------------------------


@pytest.mark.parametrize("status", list(DiagnosisCoverageStatus))
def test_empty_reports_keep_their_coverage_status_distinct(status):
    report = _report((_coverage(status=status),), ())
    assert report.candidates == ()
    assert [row.status for row in report.coverage] == [status]


def test_mixed_family_and_region_coverage_is_representable():
    coverage = (
        _coverage("cov-1", Family.MASKING, _scope(("t1",)), Status.ASSESSED),
        _coverage("cov-2", Family.MASKING, _scope(("t2",)), Status.UNAVAILABLE, None),
        _coverage("cov-3", Family.FREQUENCY_BUILDUP, _scope(), Status.INCOMPLETE),
        _coverage("cov-4", Family.MASKING, _scope(("t1",)), Status.ASSESSED, METHOD_B),
    )
    candidates = (
        _candidate("cand-1", "cov-1", scope=_scope(("t1",))),
        _candidate("cand-2", "cov-3", family=Family.FREQUENCY_BUILDUP, evidence=_evidence(**INSUFFICIENT)),
    )
    report = _report(coverage, candidates)
    assert report.coverage == coverage and report.candidates == candidates


@pytest.mark.parametrize(
    "overrides",
    [
        {"project_id": "p1"},
        {"revision": "r2"},
        {"coverage": ()},
        {"coverage": [_coverage()]},
        {"coverage": (_candidate(),)},
        {"candidates": [_candidate()]},
        {"candidates": (_coverage(),)},
    ],
)
def test_report_rejects_invalid_fields(overrides):
    values = {f.name: getattr(_report(), f.name) for f in dataclasses.fields(DiagnosisReport)}
    with pytest.raises(ValueError):
        DiagnosisReport(**{**values, **overrides})


def test_report_rejects_duplicate_coverage_ids():
    with pytest.raises(ValueError, match="coverage ID"):
        _report((_coverage("cov-1"), _coverage("cov-1", Family.PHASE_INTERACTION)), ())


@pytest.mark.parametrize("second_status", list(DiagnosisCoverageStatus))
def test_report_rejects_duplicate_coverage_keys_with_different_ids_or_statuses(second_status):
    with pytest.raises(ValueError, match="family, scope, method"):
        _report((_coverage("cov-1"), _coverage("cov-2", status=second_status)), ())


def test_distinct_methods_may_cover_the_same_family_and_region():
    report = _report((_coverage("cov-1"), _coverage("cov-2", method=METHOD_B)), ())
    assert len(report.coverage) == 2


def test_report_rejects_duplicate_candidate_ids():
    with pytest.raises(ValueError, match="candidate ID"):
        _report(candidates=(_candidate("cand-1"), _candidate("cand-1", evidence_ids=("o2",))))


@pytest.mark.parametrize(
    "candidate",
    [
        _candidate(project="p2"),
        _candidate(revision="r1"),
        _candidate(coverage="cov-missing"),
        _candidate(family=Family.PHASE_INTERACTION),
        _candidate(method=METHOD_B),
    ],
    ids=["project", "revision", "unknown-coverage", "family", "method"],
)
def test_candidate_must_match_report_and_its_coverage_row(candidate):
    with pytest.raises(ValueError):
        _report(candidates=(candidate,))


def test_a_candidate_cannot_borrow_another_rows_method():
    coverage = (_coverage("cov-1"), _coverage("cov-2", method=METHOD_B))
    assert _report(coverage, (_candidate(coverage="cov-2", method=METHOD_B),))
    with pytest.raises(ValueError):
        _report(coverage, (_candidate(coverage="cov-1", method=METHOD_B),))


@pytest.mark.parametrize("method", [None, METHOD_A])
def test_unavailable_coverage_cannot_carry_candidates(method):
    coverage = (_coverage(status=Status.UNAVAILABLE, method=method),)
    with pytest.raises(ValueError, match="UNAVAILABLE"):
        _report(coverage, (_candidate(evidence=_evidence(**INSUFFICIENT)),))


def test_incomplete_coverage_permits_only_insufficient_candidates():
    coverage = (_coverage(status=Status.INCOMPLETE),)
    assert _report(coverage, (_candidate(evidence=_evidence(**INSUFFICIENT)),))
    with pytest.raises(ValueError, match="INCOMPLETE"):
        _report(coverage, (_candidate(),))


def test_assessed_coverage_can_describe_insufficient_hypotheses():
    assert _report(candidates=(_candidate(evidence=_evidence(**INSUFFICIENT)),))


@pytest.mark.parametrize(
    ("outer", "inner"),
    [
        (_scope(), _scope()),
        (_scope(), _scope(("t1",))),
        (_scope(), _scope(("t2", "t1"))),
        (_scope(start=5.0, end=20.0), _scope(start=5.0, end=20.0)),
        (_scope(start=5.0, end=20.0), _scope(start=6.0, end=19.5)),
        (_scope(freq=FrequencyRange(100.0, 400.0)), _scope(freq=FrequencyRange(100.0, 400.0))),
        (_scope(freq=FrequencyRange(100.0, 400.0)), _scope(freq=FrequencyRange(150.0, 300.0))),
        (_scope(), _scope(freq=FrequencyRange(150.0, 300.0))),
    ],
    ids=["equal", "fewer-tracks", "reordered", "equal-time", "smaller-time", "equal-band", "smaller-band", "free-band"],
)
def test_candidate_scope_contained_in_its_coverage_passes(outer, inner):
    report = _report((_coverage(scope=outer),), (_candidate(scope=inner),))
    assert report.candidates[0].scope == inner


@pytest.mark.parametrize(
    ("outer", "inner"),
    [
        (_scope(("t1",)), _scope(("t1", "t2"))),
        (_scope(("t1",)), _scope(("t3",))),
        (_scope(start=5.0, end=20.0), _scope(start=4.999, end=20.0)),
        (_scope(start=5.0, end=20.0), _scope(start=5.0, end=20.001)),
        (_scope(freq=FrequencyRange(100.0, 400.0)), _scope(freq=FrequencyRange(99.0, 400.0))),
        (_scope(freq=FrequencyRange(100.0, 400.0)), _scope(freq=FrequencyRange(100.0, 401.0))),
        (_scope(freq=FrequencyRange(100.0, 400.0)), _scope()),
    ],
    ids=["extra-track", "other-track", "early-start", "late-end", "low-band", "high-band", "band-required"],
)
def test_candidate_scope_outside_its_coverage_fails(outer, inner):
    with pytest.raises(ValueError, match="scope"):
        _report((_coverage(scope=outer),), (_candidate(scope=inner),))


# --- validate_diagnosis_references: state fixtures ---------------------------------------------------


def _artifact(aid, tracks, revision, *, kind=ArtifactKind.STEM, content="d") -> AudioArtifact:
    return AudioArtifact(
        ArtifactId(aid),
        ProjectId("p1"),
        Revision(revision),
        tuple(TrackId(t) for t in tracks),
        kind,
        f"outputs/{aid}.wav",
        0.0,
        48000,
        2,
        48000 * 60,
        content * 64,
        VersionRef("render", "1"),
    )


# State r2 retains stem a-t1 produced at r1; a-t2 (r1) was superseded by c-t2 (r2) but stays t2's fixed input.
RETAINED_OLD = _artifact("a-t1", ("t1",), "r1")
SUPERSEDED = _artifact("a-t2", ("t2",), "r1")
CURRENT_T2 = _artifact("c-t2", ("t2",), "r2")
CURRENT_MIX = _artifact("c-mix", ("t1", "t2", "t3"), "r2", kind=ArtifactKind.MIX)


def _state(revision="r2") -> ProjectState:
    tracks = tuple(Track(TrackId(f"t{i}"), name) for i, name in enumerate(("Field recording", "Choir", "Tape"), 1))
    snapshot = ProjectSnapshot(ProjectId("p1"), Revision(revision), UTC_NOW, tracks)
    gains = (
        TrackGainState(TrackId("t1"), RETAINED_OLD, 0.0),
        TrackGainState(TrackId("t2"), SUPERSEDED, -2.0),
    )
    processing = ProjectProcessingState(ProjectId("p1"), Revision(revision), gains)
    audio = ArtifactCheckpoint(
        CheckpointId("cp2"), ProjectId("p1"), Revision(revision), (RETAINED_OLD, CURRENT_T2, CURRENT_MIX)
    )
    return ProjectState(snapshot, processing, audio)


RUN_SCOPE = _scope(("t1", "t2", "t3"))


def _observation(oid="o1", sources=("c-mix",), scope=None, result=None) -> Observation:
    return Observation(
        ObservationId(oid),
        METRIC,
        tuple(ArtifactId(a) for a in sources),
        _scope() if scope is None else scope,
        MeasuredScalar(-14.0, "unit-a") if result is None else result,
        VersionRef("spec", "1"),
        VersionRef("analyzer", "0.1"),
        UTC_NOW,
    )


def _validate(report=None, *, state=None, scope=RUN_SCOPE, observations=None):
    return validate_diagnosis_references(
        _report() if report is None else report,
        state=_state() if state is None else state,
        scope=scope,
        observations=(_observation(),) if observations is None else observations,
    )


# --- validate_diagnosis_references: behavior ---------------------------------------------------------


def test_valid_references_pass_with_a_generator():
    observations = (_observation(o) for o in ("o1", "o2"))
    report = _report(candidates=(_candidate(evidence_ids=("o1", "o2")),))
    assert _validate(report, observations=observations) is None


@pytest.mark.parametrize("status", list(DiagnosisCoverageStatus))
def test_empty_reports_of_every_status_validate_without_observations(status):
    assert _validate(_report((_coverage(status=status),), ()), observations=()) is None


def test_mixed_coverage_report_validates():
    coverage = (
        _coverage("cov-1", Family.MASKING, _scope(("t1",)), Status.ASSESSED),
        _coverage("cov-2", Family.PHASE_INTERACTION, _scope(("t2", "t3")), Status.UNAVAILABLE, None),
        _coverage("cov-3", Family.FREQUENCY_BUILDUP, RUN_SCOPE, Status.INCOMPLETE),
    )
    candidates = (
        _candidate("cand-1", "cov-1", scope=_scope(("t1",)), evidence_ids=("o-old",)),
        _candidate("cand-2", "cov-3", family=Family.FREQUENCY_BUILDUP, evidence=_evidence(**INSUFFICIENT)),
    )
    observations = (_observation("o1"), _observation("o-old", ("a-t1",), _scope(("t1",))))
    assert _validate(_report(coverage, candidates), observations=observations) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"report": _coverage()},
        {"state": _state().snapshot},
        {"scope": _scope().time_range},
    ],
)
def test_wrong_argument_types_raise(kwargs):
    with pytest.raises(ValueError):
        _validate(**kwargs)


@pytest.mark.parametrize("observations", [5, None])
def test_non_iterable_observations_raise_value_error(observations):
    with pytest.raises(ValueError):
        validate_diagnosis_references(_report(), state=_state(), scope=RUN_SCOPE, observations=observations)


@pytest.mark.parametrize("bad", [_candidate(), "o1", _observation().scope])
def test_wrong_observation_type_raises_even_when_unreferenced(bad):
    with pytest.raises(ValueError):
        _validate(observations=(_observation(), bad))


def test_iterator_errors_propagate_unchanged():
    error = RuntimeError("source failed")

    def failing():
        yield _observation()
        raise error

    with pytest.raises(RuntimeError) as raised:
        _validate(observations=failing())
    assert raised.value is error


@pytest.mark.parametrize(
    ("report", "state"),
    [
        (_report(project="p2", candidates=(_candidate(project="p2"),)), _state()),
        (_report(revision="r1", candidates=(_candidate(revision="r1"),)), _state()),
        (_report(revision="r1", candidates=()), _state()),
        (_report(), _state("r3")),
    ],
    ids=["project", "revision", "revision-empty-report", "state-revision"],
)
def test_report_must_describe_the_selected_state(report, state):
    with pytest.raises(ValueError, match="report"):
        _validate(report, state=state)


def test_supplied_scope_must_use_known_tracks():
    with pytest.raises(ValueError, match="t9"):
        _validate(scope=_scope(("t1", "t9")))


BANDED_RUN_SCOPE = _scope(("t1", "t2", "t3"), freq=FrequencyRange(20.0, 20000.0))


@pytest.mark.parametrize(
    ("run_scope", "coverage_scope"),
    [
        (_scope(("t1", "t2")), _scope(("t1", "t3"))),
        (RUN_SCOPE, _scope(start=0.0, end=61.0)),
        (BANDED_RUN_SCOPE, _scope(("t1",), freq=FrequencyRange(10.0, 100.0))),
        (BANDED_RUN_SCOPE, _scope(("t1",))),
    ],
    ids=["outside-tracks", "outside-time", "outside-band", "band-required"],
)
def test_coverage_scope_must_lie_within_the_supplied_scope(run_scope, coverage_scope):
    with pytest.raises(ValueError, match="coverage"):
        _validate(_report((_coverage(scope=coverage_scope),), ()), scope=run_scope, observations=())


def test_coverage_scope_with_an_unknown_track_is_rejected():
    with pytest.raises(ValueError, match="t9"):
        _validate(_report((_coverage(scope=_scope(("t1", "t9"))),), ()), observations=())


def test_coverage_equal_to_the_supplied_scope_passes():
    assert _validate(_report((_coverage(scope=RUN_SCOPE),), ()), observations=()) is None


def test_unknown_cited_observation_raises():
    with pytest.raises(ValueError, match="o-missing"):
        _validate(_report(candidates=(_candidate(evidence_ids=("o1", "o-missing")),)))


@pytest.mark.parametrize("cited", [True, False])
def test_duplicate_observation_ids_raise_even_for_identical_unreferenced_extras(cited):
    duplicate = _observation("o1") if cited else _observation("o-extra")
    observations = (_observation("o1"), _observation("o-extra"), duplicate)
    with pytest.raises(ValueError, match="duplicate"):
        _validate(observations=observations)


def test_cited_superseded_artifact_is_rejected():
    observation = _observation("o-sup", ("a-t2",), _scope(("t2",)))
    report = _report(candidates=(_candidate(scope=_scope(("t2",)), evidence_ids=("o-sup",)),))
    with pytest.raises(ValueError, match="a-t2"):
        _validate(report, observations=(observation,))


def test_retained_member_with_an_older_production_revision_is_accepted():
    observation = _observation("o-old", ("a-t1",), _scope(("t1",)))
    assert observation.artifact_ids == (RETAINED_OLD.artifact_id,) and RETAINED_OLD.revision == Revision("r1")
    report = _report(candidates=(_candidate(scope=_scope(("t1",)), evidence_ids=("o-old",)),))
    assert _validate(report, observations=(observation,)) is None


def test_cited_observation_from_another_state_is_rejected():
    # c-other is not a member of the selected state's checkpoint (for example a candidate's output).
    with pytest.raises(ValueError, match="c-other"):
        _validate(observations=(_observation("o1", ("c-other",)),))


def test_cited_observation_not_covering_its_scope_is_rejected():
    observation = _observation("o1", ("c-t2",), _scope(("t1", "t2")))
    with pytest.raises(ValueError, match="not covered"):
        _validate(observations=(observation,))


def test_unreferenced_extras_are_ignored_after_type_and_id_checks():
    extras = (_observation("o-sup", ("a-t2",), _scope(("t2",))), _observation("o-unknown", ("c-missing",)))
    assert _validate(observations=(_observation(), *extras)) is None


def test_repeated_citation_across_candidates_is_allowed():
    candidates = (_candidate("cand-1"), _candidate("cand-2", scope=_scope(("t1",))))
    assert _validate(_report(candidates=candidates)) is None


def test_all_cited_measurements_unavailable_requires_explicit_insufficiency():
    observations = (
        _observation("o1", result=Unavailable("silent region")),
        _observation("o2", ("c-t2",), _scope(("t2",)), Unavailable("not computed")),
    )
    sufficient = _candidate(evidence_ids=("o1", "o2"))
    with pytest.raises(ValueError, match="insufficient"):
        _validate(_report(candidates=(sufficient,)), observations=observations)
    insufficient = _candidate(evidence_ids=("o1", "o2"), evidence=_evidence(**INSUFFICIENT))
    assert _validate(_report(candidates=(insufficient,)), observations=observations) is None


@pytest.mark.parametrize("insufficient", [False, True])
def test_partial_availability_is_not_classified_by_the_validator(insufficient):
    observations = (_observation("o1"), _observation("o2", ("c-t2",), _scope(("t2",)), Unavailable("not computed")))
    evidence = _evidence(**INSUFFICIENT) if insufficient else _evidence()
    report = _report(candidates=(_candidate(evidence_ids=("o1", "o2"), evidence=evidence),))
    assert _validate(report, observations=observations) is None
