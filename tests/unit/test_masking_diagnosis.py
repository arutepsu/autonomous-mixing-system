"""Pure diagnosis-readiness tests; scalar values are structural fixtures, not detector results."""

from dataclasses import fields, replace

import pytest
from masking_diagnosis_support import obs, plan, policy, request, support
from masking_support import FULL, TA, TB, Tokens, state_of

from autonomous_mixing.core.diagnosis.masking import diagnose_masking
from autonomous_mixing.core.ports.coordinator_runtime import validate_diagnosis_response
from autonomous_mixing.core.records.coordinator_runtime import DiagnosisRequest
from autonomous_mixing.core.records.diagnosis import DiagnosisCoverageStatus, ProblemFamily
from autonomous_mixing.core.records.identifiers import ArtifactId, ObservationId, Revision, VersionRef
from autonomous_mixing.core.records.observation import AnalysisScope, MeasuredScalar, TimeRange


def diagnose(req=None, pol=None, ids=None):
    pol = pol or policy()
    req = req or request(pol)
    report = diagnose_masking(req, policy=pol, identities=ids or Tokens("coverage"))
    validate_diagnosis_response(req, report)
    return report


def p1(report):
    return next(r for r in report.coverage if r.family is ProblemFamily.MASKING)


def test_real_bindings_yield_incomplete_not_all_clear():
    report = diagnose()
    assert not report.candidates
    assert p1(report).status is DiagnosisCoverageStatus.INCOMPLETE
    assert "S0" in p1(report).explanation and "uncalibrated" in p1(report).explanation
    assert p1(report).explanation.count("unavailable") == 3
    assert len(report.coverage) == 3
    for row in report.coverage:
        if row.family is not ProblemFamily.MASKING:
            assert row.status is DiagnosisCoverageStatus.UNAVAILABLE and row.method is None


def test_finite_qualified_named_scalars_never_establish_qualification():
    pol = policy()
    result = diagnose(request(pol, observations=obs(pol, finite=True)), pol)
    assert p1(result).status is DiagnosisCoverageStatus.INCOMPLETE and not result.candidates
    assert p1(result).explanation.count("present") == 3
    assert "not qualified" in p1(result).explanation


@pytest.mark.parametrize(
    "change,label",
    [
        (lambda o: replace(o, analysis_spec=VersionRef("wrong", "1")), "settings_mismatch"),
        (lambda o: replace(o, analyzer=VersionRef("wrong", "1")), "settings_mismatch"),
        (lambda o: replace(o, metric=replace(o.metric, version="wrong")), "mismatched"),
        (lambda o: replace(o, artifact_ids=tuple(reversed(o.artifact_ids))), "source_mismatch"),
        (lambda o: replace(o, scope=replace(o.scope, track_ids=tuple(reversed(o.scope.track_ids)))), "mismatched"),
        (lambda o: replace(o, result=MeasuredScalar(1.0, "wrong")), "unit_mismatch"),
    ],
)
def test_mismatched_observation_does_not_fill_a_job(change, label):
    pol = policy()
    original = obs(pol, finite=True)
    changed = (change(original[0]), *original[1:])
    report = diagnose(request(pol, observations=changed), pol)
    assert label in p1(report).explanation and not report.candidates


def test_missing_and_ambiguous_observations_are_distinct():
    pol = policy()
    rows = obs(pol)
    missing = diagnose(request(pol, observations=rows[1:]), pol)
    assert "missing" in p1(missing).explanation
    duplicate = (*rows, replace(rows[0], observation_id=ObservationId("duplicate")))
    result = diagnose(request(pol, observations=duplicate), pol)
    assert "ambiguous_observation" in p1(result).explanation


def test_ambiguity_is_not_resolved_by_picking_a_better_matching_observation():
    pol = policy()
    rows = obs(pol)
    wrong = replace(rows[0], observation_id=ObservationId("wrong"), analysis_spec=VersionRef("other", "1"))
    assert "ambiguous_observation" in p1(diagnose(request(pol, observations=(*rows, wrong)), pol)).explanation


def test_source_ambiguity_is_not_resolved_by_first_or_latest_revision():
    pol = policy()
    base = request(pol)
    old = base.state.audio.artifacts[0]
    extra = replace(old, artifact_id=ArtifactId("newer"), revision=Revision("r2"))
    state = state_of((*base.state.audio.artifacts, extra), revision="r2")
    report = diagnose(request(pol, observations=base.observations, state=state), pol)
    assert "ambiguous_source" in p1(report).explanation and not report.candidates


def test_current_member_with_changed_identity_hash_and_production_revision_is_valid():
    pol = policy()
    base = request(pol)
    current = tuple(
        replace(a, artifact_id=ArtifactId(f"new-{i}"), revision=Revision("r2"), content_hash="1" * 64)
        for i, a in enumerate(base.state.audio.artifacts)
    )
    state = state_of(current, revision="r3")
    report = diagnose(request(pol, state=state, observations=obs(pol, state=state, finite=True)), pol)
    assert report.revision == Revision("r3") and p1(report).explanation.count("present") == 3
    assert not report.candidates


def test_no_matching_source_is_a_readiness_gap_not_a_wiring_error():
    pol = policy()
    base = request(pol)
    members = tuple(replace(a, render_spec=VersionRef("different", "1")) for a in base.state.audio.artifacts)
    state = state_of(members)
    report = diagnose(request(pol, state=state, observations=()), pol)
    assert "missing_source" in p1(report).explanation


def test_reverse_direction_cannot_supply_missing_forward_metrics():
    pol = policy(plan((support(), support((TB, TA)))))
    reverse = obs(pol)[3:]
    report = diagnose(request(pol, observations=reverse), pol)
    explanation = p1(report).explanation
    assert "ta -> tb" in explanation and "tb -> ta" in explanation
    assert explanation.count("mismatched") == 3 and explanation.count("unavailable") == 3
    assert not report.candidates


def test_raw_only_evidence_remains_missing_qualified_evidence():
    pol = policy()
    rows = obs(pol, finite=True)
    raw = replace(rows[0], metric=VersionRef("p1.raw.geometric_support_cells", "1"))
    result = diagnose(request(pol, observations=(raw,)), pol)
    assert p1(result).explanation.count("missing") == 3 and not result.candidates


def test_empty_geometric_support_and_silence_do_not_mean_success():
    s = support(time=TimeRange(0.0, 100 / 44100))
    assert s.cell_count == 0
    pol = policy(plan((s,)))
    result = diagnose(request(pol), pol)
    assert "empty geometric support" in p1(result).explanation and not result.candidates


@pytest.mark.parametrize("scope", [AnalysisScope((TA,), FULL), AnalysisScope((TA, TB), TimeRange(0, 0.01))])
def test_job_outside_requested_scope_is_a_configuration_error(scope):
    pol = policy()
    r = request(pol)
    with pytest.raises(ValueError, match="scope"):
        diagnose_masking(replace(r, scope=scope), policy=pol, identities=Tokens())


@pytest.mark.parametrize("tokens", [("",), (123,), ("same", "same", "same")])
def test_invalid_or_duplicate_coverage_tokens_are_wiring_errors(tokens):
    class Ids:
        def __init__(self):
            self.tokens = iter(tokens)

        def next_token(self):
            return next(self.tokens)

    with pytest.raises(ValueError):
        diagnose_masking(request(), policy=policy(), identities=Ids())


def test_repeat_classification_is_identical_apart_from_fresh_ids_and_does_not_mutate_inputs():
    pol = policy()
    req = request(pol)
    ids = Tokens()
    one = diagnose(req, pol, ids)
    two = diagnose(req, pol, ids)
    assert [r.explanation for r in one.coverage] == [r.explanation for r in two.coverage]
    assert not set(r.coverage_id for r in one.coverage) & set(r.coverage_id for r in two.coverage)
    assert req == request(pol) and pol == policy()
    assert "preferences" not in {f.name for f in fields(DiagnosisRequest)}


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "kind"),
        ("source_track_ids", ("different",)),
        ("render_spec", VersionRef("other", "1")),
        ("sample_rate_hz", 48000),
        ("channel_count", 2),
        ("timeline_start_seconds", 1.0),
        ("frame_count", 5000),
    ],
)
def test_core_source_matching_agrees_with_r1_for_every_declared_field(field, value):
    from masking_support import N, metadata_artifact, source_of

    from autonomous_mixing.adapters.dsp_analysis.masking_preparation import matches_source
    from autonomous_mixing.core.diagnosis.masking import _matches_source
    from autonomous_mixing.core.records.artifacts import ArtifactKind
    from autonomous_mixing.core.records.identifiers import TrackId

    artifact = metadata_artifact(TA, N)
    requirement = source_of(artifact)
    if field == "kind":
        value = ArtifactKind.GROUP
    if field == "source_track_ids":
        value = (TrackId("different"),)
    changed = replace(requirement, **{field: value})
    assert _matches_source(artifact, requirement) == matches_source(artifact, requirement) is True
    assert _matches_source(artifact, changed) == matches_source(artifact, changed) is False


def test_unavailable_results_have_no_unit_to_mismatch():
    report = diagnose()
    assert "unit_mismatch" not in p1(report).explanation


def test_unrelated_valid_observations_do_not_create_ambiguity():
    pol = policy()
    r = request(pol)
    unrelated = replace(
        r.observations[0], observation_id=ObservationId("unrelated"), metric=VersionRef("level.sample_peak", "1")
    )
    one = diagnose(r, pol)
    two = diagnose(replace(r, observations=(*r.observations, unrelated)), pol)
    assert p1(one).explanation == p1(two).explanation
