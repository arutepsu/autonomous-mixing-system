"""Experimental fixed-support diagnosis: bindings (Task 1) and the deterministic rule (Task 2).

Every observation here is a labelled structural scalar fixture: no detector, audio or calibration ran. A candidate is
an uncalibrated experimental dist10 exceedance hypothesis, never audible masking, a gain instruction or better mixing.
Thresholds are the owner-approved development values (``CURRENT_SCREEN``) unless a test labels another configuration.
"""

import dataclasses
import itertools

import pytest
from masking_diagnosis_support import (
    CURRENT_SCREEN,
    calls,
    experimental_definitions,
    experimental_plan,
    experimental_policy,
    experimental_request,
    hundred_cells,
    support,
)
from masking_support import FULL, TA, TB, N, Tokens, metadata_artifact, source_of, state_of

from autonomous_mixing.core.diagnosis.masking import diagnose_experimental_masking, diagnose_masking
from autonomous_mixing.core.ports.coordinator_runtime import validate_diagnosis_response
from autonomous_mixing.core.records.coordinator_runtime import DiagnosisRequest
from autonomous_mixing.core.records.diagnosis import (
    DiagnosisCoverageStatus,
    ProblemFamily,
    UncertaintyRepresentation,
)
from autonomous_mixing.core.records.identifiers import ArtifactId, ObservationId, Revision, VersionRef
from autonomous_mixing.core.records.masking_diagnosis import (
    EXPERIMENTAL_DIAGNOSIS_METRICS,
    ExperimentalMaskingDiagnosisJob,
    ExperimentalMaskingDiagnosisPolicy,
    experimental_diagnosis_method,
)
from autonomous_mixing.core.records.masking_measurement import bound_specification
from autonomous_mixing.core.records.observation import AnalysisScope, MeasuredScalar, TimeRange, Unavailable

SUPPORT, ROBUST, RUN = (m for m, _ in EXPERIMENTAL_DIAGNOSIS_METRICS)
HIT = (100, 25, calls(4))  # exactly at both inclusive boundaries
MISS = (100, 0, 0.0)


def diagnose(pol, values, *, tokens=None, **kwargs):
    request = experimental_request(pol, values, **kwargs)
    report = diagnose_experimental_masking(request, policy=pol, identities=tokens or Tokens("id"))
    validate_diagnosis_response(request, report)
    return request, report


def masking_rows(report):
    return [r for r in report.coverage if r.family is ProblemFamily.MASKING]


# --- Task 1: bindings -----------------------------------------------------------------------------------------------


def test_valid_binding_pins_method_to_the_full_prepared_plan():
    pol = experimental_policy()
    plan = pol.measurement_plan
    assert pol.method == bound_specification("p1.experimental.diagnosis", "1", plan.prepared_digest)
    assert pol.method == experimental_diagnosis_method(plan)
    assert pol.screening is plan.prepared.experimental_policy is not None
    assert [j.support for j in pol.jobs] == list(plan.prepared.experimental_supports)
    assert [m for m, _ in EXPERIMENTAL_DIAGNOSIS_METRICS] == [
        VersionRef("p1.experimental.support_cells", "1"),
        VersionRef("p1.experimental.robust_exceedance_cells", "1"),
        VersionRef("p1.experimental.longest_robust_run_seconds", "1"),
    ]
    assert not any(f.name in {"minimum_cells", "minimum_robust_fraction", "threshold", "qualified"}
                   for f in dataclasses.fields(ExperimentalMaskingDiagnosisPolicy))  # fmt: skip


def test_plan_without_experimental_extension_is_refused():
    plan = experimental_plan()
    legacy = dataclasses.replace(plan, prepared=dataclasses.replace(plan.prepared, experimental_policy=None,
                                                                     experimental_supports=()))  # fmt: skip
    fixed = plan.prepared.experimental_supports[0]
    with pytest.raises(ValueError, match="experimental"):
        ExperimentalMaskingDiagnosisPolicy(experimental_diagnosis_method(legacy), legacy,
                                           (ExperimentalMaskingDiagnosisJob(fixed, experimental_definitions(fixed, legacy)),))  # fmt: skip


@pytest.mark.parametrize(
    "method",
    [
        VersionRef("p1.experimental.diagnosis", "1"),  # unbound
        VersionRef("p1.calibrated.diagnosis", "1"),  # user-labelled qualification
        bound_specification("p1.experimental.diagnosis", "1", "f" * 64),  # another plan
        bound_specification("p1.experimental.diagnosis", "2", "e" * 64),
    ],
)
def test_method_must_be_the_bound_experimental_identity(method):
    pol = experimental_policy()
    with pytest.raises(ValueError, match="method"):
        dataclasses.replace(pol, method=method)


def test_support_must_be_exactly_a_frozen_support_of_the_plan():
    pol = experimental_policy()
    fixed = pol.jobs[0].support
    other_cells = dataclasses.replace(fixed, cells=fixed.cells[1:] + ((fixed.cells[-1][0], 11),))
    assert len(other_cells.cells) == len(fixed.cells)  # equal count, different cells
    for wrong in (other_cells, dataclasses.replace(fixed, policy_digest="0" * 64)):
        job = ExperimentalMaskingDiagnosisJob(wrong, experimental_definitions(wrong, pol.measurement_plan))
        with pytest.raises(ValueError, match="support"):
            dataclasses.replace(pol, jobs=(job,))


def test_screening_thresholds_come_only_from_the_plan():
    other = dataclasses.replace(CURRENT_SCREEN, minimum_cells=99)  # labelled boundary configuration, not a tuning
    plan = experimental_plan(screen=other)
    assert experimental_policy(plan).screening.minimum_cells == 99
    assert experimental_policy().screening == CURRENT_SCREEN


@pytest.mark.parametrize(
    "change",
    [
        lambda d: dataclasses.replace(d, unit="dB"),
        lambda d: dataclasses.replace(d, analyzer=VersionRef("other", "1")),
        lambda d: dataclasses.replace(d, analysis_spec=VersionRef("other", "1")),
        lambda d: dataclasses.replace(d, alignment=VersionRef("other", "1")),
        lambda d: dataclasses.replace(d, sources=tuple(reversed(d.sources))),
        lambda d: dataclasses.replace(
            d,
            sources=(
                dataclasses.replace(d.sources[0], frame_count=N - 1),
                dataclasses.replace(d.sources[1], frame_count=N - 1),
            ),
        ),  # fmt: skip
        lambda d: dataclasses.replace(
            d, selector=dataclasses.replace(d.selector, scope=AnalysisScope((TB, TA), FULL))
        ),  # fmt: skip
        lambda d: dataclasses.replace(
            d, selector=dataclasses.replace(d.selector, scope=AnalysisScope((TA, TB), TimeRange(0.0, 0.1)))
        ),  # fmt: skip
    ],
)
def test_every_wrong_binding_is_refused(change):
    pol = experimental_policy()
    job = pol.jobs[0]
    definitions = tuple(change(d) for d in job.measurements)
    with pytest.raises(ValueError):
        dataclasses.replace(pol, jobs=(ExperimentalMaskingDiagnosisJob(job.support, definitions), pol.jobs[1]))


def test_wrong_duplicate_or_missing_metrics_are_refused():
    pol = experimental_policy()
    fixed, plan = pol.jobs[0].support, pol.measurement_plan
    for metrics in (
        EXPERIMENTAL_DIAGNOSIS_METRICS[:2],
        (*EXPERIMENTAL_DIAGNOSIS_METRICS[:2], EXPERIMENTAL_DIAGNOSIS_METRICS[0]),
        (
            (VersionRef("p1.experimental.positive_margin_fraction", "1"), "fraction"),
            *EXPERIMENTAL_DIAGNOSIS_METRICS[1:],
        ),
        ((VersionRef("p1.support_cells", "1"), "cells"), *EXPERIMENTAL_DIAGNOSIS_METRICS[1:]),
    ):
        with pytest.raises(ValueError):
            ExperimentalMaskingDiagnosisJob(fixed, experimental_definitions(fixed, plan, metrics))


def test_duplicate_jobs_are_refused():
    pol = experimental_policy()
    with pytest.raises(ValueError, match="duplicate"):
        dataclasses.replace(pol, jobs=(pol.jobs[0], pol.jobs[0]))


def test_legacy_readiness_records_still_construct_unchanged():
    from masking_diagnosis_support import policy, request

    report = diagnose_masking(request(), policy=policy(), identities=Tokens("legacy"))
    assert not report.candidates and report.coverage[0].status is DiagnosisCoverageStatus.INCOMPLETE


# --- Task 2: the rule -----------------------------------------------------------------------------------------------


def test_positive_case_at_both_inclusive_boundaries():
    pol = experimental_policy()
    request, report = diagnose(pol, (HIT, MISS))
    forward, reverse = masking_rows(report)
    assert forward.scope.track_ids == (TA, TB) and reverse.scope.track_ids == (TB, TA)
    assert forward.status is reverse.status is DiagnosisCoverageStatus.ASSESSED
    assert forward.method == reverse.method == pol.method
    (candidate,) = report.candidates
    assert candidate.coverage_id == forward.coverage_id and candidate.scope == forward.scope
    assert candidate.family is ProblemFamily.MASKING and candidate.method == pol.method
    assert candidate.evidence_ids == tuple(o.observation_id for o in request.observations[:3])
    assert (candidate.project_id, candidate.revision) == (request.state.project_id, request.state.revision)
    evidence = candidate.evidence
    assert evidence.representation is UncertaintyRepresentation.UNCALIBRATED
    assert evidence.insufficient_evidence is False and evidence.missing_information == ()
    assert "experimental dist10 exceedance hypothesis" in evidence.strength
    for fact in ("25/100", "0.25", "4 calls", pol.measurement_plan.prepared_digest, pol.jobs[0].support.digest,
                 CURRENT_SCREEN.digest, pol.method.version):  # fmt: skip
        assert fact in evidence.strength + " ".join(evidence.limitations), fact
    text = " ".join(evidence.limitations).lower()
    for limitation in ("uncalibrated", "perceptual", "declared alignment", "noise reference", "overlapping",
                       "no automatic correction"):  # fmt: skip
        assert limitation in text, limitation
    assert "probability" not in evidence.uncertainty.lower() or "no" in evidence.uncertainty.lower()


@pytest.mark.parametrize(
    "values,label",
    [
        ((100, 24, calls(4)), "robust fraction one cell below"),
        ((100, 25, calls(3)), "persistence one call below"),
        ((101, 25, calls(4)), "fraction 25/101 below 0.25"),
    ],
)
def test_each_boundary_below_is_assessed_without_candidate(values, label):
    cells = hundred_cells(support()) + (((support().first_call, 11),) if values[0] == 101 else ())
    pol = experimental_policy(experimental_plan(cells=tuple(sorted(cells)))) if values[0] == 101 else None
    pol = pol or experimental_policy()
    _, report = diagnose(pol, (values, MISS))
    assert masking_rows(report)[0].status is DiagnosisCoverageStatus.ASSESSED, label
    assert report.candidates == (), label


def test_small_support_is_incomplete_even_with_fabricated_large_exceedance():
    cells = hundred_cells(support())[:99]
    pol = experimental_policy(experimental_plan(cells=cells))
    _, report = diagnose(pol, ((99, 99, calls(9)), (99, 99, calls(9))))
    assert all(r.status is DiagnosisCoverageStatus.INCOMPLETE for r in masking_rows(report))
    assert report.candidates == () and "minimum" in masking_rows(report)[0].explanation


def test_zero_support_is_incomplete():
    pol = experimental_policy(experimental_plan(cells=()))
    unavailable = Unavailable("experimental fixed support has 0 cells; minimum is 100")
    _, report = diagnose(pol, ((0, unavailable, unavailable), (0, unavailable, unavailable)))
    assert all(r.status is DiagnosisCoverageStatus.INCOMPLETE for r in masking_rows(report)) and not report.candidates


def test_ordinary_positive_fraction_is_never_the_robust_fraction():
    pol = experimental_policy()
    request = experimental_request(pol, ((100, 10, calls(4)), MISS))
    positive = dataclasses.replace(request.observations[0], observation_id=ObservationId("positive"),
                                   metric=VersionRef("p1.experimental.positive_margin_fraction", "1"),
                                   result=MeasuredScalar(0.99, "fraction"))  # fmt: skip
    request = dataclasses.replace(request, observations=(*request.observations, positive))
    report = diagnose_experimental_masking(request, policy=pol, identities=Tokens())
    assert masking_rows(report)[0].status is DiagnosisCoverageStatus.ASSESSED and report.candidates == ()


@pytest.mark.parametrize(
    "values,reason",
    [
        ((100.5, 25, calls(4)), "support"),
        ((99, 25, calls(4)), "support"),  # count differs from the 100 frozen cells
        ((100, 25.5, calls(4)), "robust"),
        ((100, -1, 0.0), "robust"),
        ((100, 101, calls(4)), "robust"),
        ((100, 25, -calls(1)), "run"),
        ((100, 25, calls(4) + 1e-9), "run"),  # not a whole number of calls on the grid
        ((100, 3, calls(4)), "run"),  # run longer than the robust count
        ((100, 25, calls(11)), "run"),  # longer than the frozen cells allow (10 calls per band)
    ],
)
def test_inconsistent_measurements_are_incomplete_with_no_candidate(values, reason):
    _, report = diagnose(experimental_policy(), (values, MISS))
    row = masking_rows(report)[0]
    assert row.status is DiagnosisCoverageStatus.INCOMPLETE and reason in row.explanation
    assert report.candidates == ()


@pytest.mark.parametrize("position", [0, 1, 2])
def test_unavailable_is_incomplete_never_zero(position):
    values = list(HIT)
    values[position] = Unavailable("current target leaves the experimental band-energy screen")
    _, report = diagnose(experimental_policy(), (tuple(values), MISS))
    row = masking_rows(report)[0]
    assert row.status is DiagnosisCoverageStatus.INCOMPLETE and "unavailable" in row.explanation
    assert report.candidates == ()


@pytest.mark.parametrize(
    "field,value,status",
    [
        ("analysis_spec", VersionRef("other-spec", "1"), "settings"),
        ("analyzer", VersionRef("other-analyzer", "1"), "settings"),
        ("artifact_ids", (ArtifactId("tb-meta"), ArtifactId("ta-meta")), "source"),
        ("scope", AnalysisScope((TA, TB), TimeRange(0.0, 0.1)), "missing"),
        ("result", MeasuredScalar(25.0, "dB"), "unit"),
    ],
)
def test_right_metric_with_wrong_binding_is_not_evidence(field, value, status):
    pol = experimental_policy()
    overrides = {(0, ROBUST.name): {field: value}}
    _, report = diagnose(pol, (HIT, MISS), overrides=overrides)
    row = masking_rows(report)[0]
    assert row.status is DiagnosisCoverageStatus.INCOMPLETE and status in row.explanation
    assert report.candidates == ()


def test_duplicate_selectors_are_ambiguous_not_resolved():
    pol = experimental_policy()
    request = experimental_request(pol, (HIT, MISS))
    twin = dataclasses.replace(request.observations[1], observation_id=ObservationId("twin"))
    request = dataclasses.replace(request, observations=(*request.observations, twin))
    report = diagnose_experimental_masking(request, policy=pol, identities=Tokens())
    assert masking_rows(report)[0].status is DiagnosisCoverageStatus.INCOMPLETE
    assert "ambiguous" in masking_rows(report)[0].explanation and report.candidates == ()


def test_duplicate_observation_ids_are_refused_by_the_existing_request_validator():
    pol = experimental_policy()
    request = experimental_request(pol, (HIT, MISS))
    with pytest.raises(ValueError, match="duplicate observation"):
        DiagnosisRequest(request.state, request.scope, (*request.observations, request.observations[0]))


def test_opposite_directions_and_mixed_jobs_never_cross_link():
    pol = experimental_policy()
    _, report = diagnose(pol, (MISS, HIT))
    forward, reverse = masking_rows(report)
    (candidate,) = report.candidates
    assert candidate.coverage_id == reverse.coverage_id and candidate.scope.track_ids == (TB, TA)
    assert all(o.value.startswith("x-1-") for o in candidate.evidence_ids)
    _, mixed = diagnose(pol, (HIT, (Unavailable("missing"), 0, 0.0)))
    statuses = [r.status for r in masking_rows(mixed)]
    assert statuses == [DiagnosisCoverageStatus.ASSESSED, DiagnosisCoverageStatus.INCOMPLETE]
    assert len(mixed.candidates) == 1 and mixed.candidates[0].scope.track_ids == (TA, TB)


def test_other_families_stay_unavailable_over_the_request_scope():
    _, report = diagnose(experimental_policy(), (HIT, HIT))
    others = {r.family: r for r in report.coverage if r.family is not ProblemFamily.MASKING}
    assert set(others) == {ProblemFamily.FREQUENCY_BUILDUP, ProblemFamily.PHASE_INTERACTION}
    assert all(r.status is DiagnosisCoverageStatus.UNAVAILABLE and r.method is None for r in others.values())
    assert len(report.candidates) == 2


def test_unchanged_members_from_older_revisions_remain_current_sources():
    a, b = metadata_artifact(TA, N), metadata_artifact(TB, N)
    later = state_of((a, b), revision="r9")  # members produced at r1, current at r9
    _, report = diagnose(experimental_policy(), (HIT, MISS), state=later)
    assert report.revision == Revision("r9") and len(report.candidates) == 1


def test_ambiguous_or_missing_current_sources_are_incomplete():
    a, b = metadata_artifact(TA, N), metadata_artifact(TB, N)
    twin = dataclasses.replace(a, artifact_id=ArtifactId("ta-twin"))
    request = experimental_request(experimental_policy(), (HIT, MISS))
    ambiguous = state_of((a, twin, b))
    report = diagnose_experimental_masking(dataclasses.replace(request, state=ambiguous),
                                           policy=experimental_policy(), identities=Tokens())  # fmt: skip
    assert "ambiguous_source" in masking_rows(report)[0].explanation and not report.candidates
    rerendered = dataclasses.replace(b, render_spec=VersionRef("render", "2"))  # no longer the declared subject
    other = state_of((a, rerendered))
    report = diagnose_experimental_masking(DiagnosisRequest(other, request.scope, ()),
                                           policy=experimental_policy(), identities=Tokens())  # fmt: skip
    assert "missing_source" in masking_rows(report)[0].explanation and not report.candidates
    assert source_of(a) == experimental_policy().jobs[0].measurements[0].sources[0]


def test_job_outside_the_request_scope_is_a_configuration_error():
    pol = experimental_policy()
    narrow = AnalysisScope((TA, TB), TimeRange(0.0, 0.1))
    with pytest.raises(ValueError, match="scope"):
        diagnose(pol, (HIT, MISS), scope=narrow)
    with pytest.raises(ValueError, match="scope"):
        diagnose(pol, (HIT, MISS), scope=AnalysisScope((TA,), FULL))


@pytest.mark.parametrize("tokens", [["a", "a"], ["a", "b", "c", "d", "a"], [None]])
def test_broken_or_duplicate_identities_are_wiring_errors(tokens):
    class Fixed:
        def __init__(self):
            self.values = list(tokens)

        def next_token(self):
            return self.values.pop(0) if self.values else "fresh"

    pol = experimental_policy()
    request = experimental_request(pol, (HIT, HIT))
    with pytest.raises(ValueError):
        diagnose_experimental_masking(request, policy=pol, identities=Fixed())


def test_rule_is_deterministic_apart_from_fresh_ids():
    pol = experimental_policy()
    _, first = diagnose(pol, (HIT, MISS), tokens=Tokens("one"))
    _, second = diagnose(pol, (HIT, MISS), tokens=Tokens("two"))
    strip = lambda r: [(c.status, c.explanation, c.scope) for c in r.coverage]  # noqa: E731
    assert strip(first) == strip(second)
    assert [c.evidence for c in first.candidates] == [c.evidence for c in second.candidates]


# --- review regressions (Codex P2, 2026-11-23) ------------------------------------------------------------------------


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_definition_order_never_changes_the_result(order):
    plan = experimental_plan()
    metrics = tuple(EXPERIMENTAL_DIAGNOSIS_METRICS[i] for i in order)
    jobs = tuple(ExperimentalMaskingDiagnosisJob(s, experimental_definitions(s, plan, metrics))
                 for s in plan.prepared.experimental_supports)  # fmt: skip
    pol = experimental_policy(plan, jobs)
    by_metric = ({SUPPORT: 100, ROBUST: 25, RUN: calls(4)}, {SUPPORT: 100, ROBUST: 30, RUN: calls(5)})
    values = tuple(tuple(by_metric[j][d.selector.metric] for d in job.measurements) for j, job in enumerate(pol.jobs))
    _, report = diagnose(pol, values)
    assert [r.status for r in masking_rows(report)] == [DiagnosisCoverageStatus.ASSESSED] * 2, order
    assert len(report.candidates) == 2, order
    strengths = sorted(c.evidence.strength for c in report.candidates)
    assert "25/100" in strengths[0] + strengths[1] and "30/100" in strengths[0] + strengths[1]
    for candidate in report.candidates:  # evidence cites the job's observations in the fixed catalogue order
        assert [i.value.rsplit("-", 1)[-1] for i in candidate.evidence_ids] == [m.name for m in (SUPPORT, ROBUST, RUN)]


@pytest.mark.parametrize("run", [1e308, 1.7976931348623157e308, 1e20, calls(11)])
def test_durations_beyond_the_frozen_support_are_incomplete_without_overflow(run):
    _, report = diagnose(experimental_policy(), ((100, 25, run), MISS))
    row = masking_rows(report)[0]
    assert row.status is DiagnosisCoverageStatus.INCOMPLETE and "longest" in row.explanation
    assert report.candidates == ()
