"""Explicit analysis jobs and current-artifact roots (2026-11-10 analysis-jobs contract plan).

Metadata fixtures only: metric names, scopes, units and values are labelled structural examples on generic
tracks, not recommended settings, measurement methods or evaluation data. No file is read or written.
"""

import builtins
import dataclasses
import os

import pytest
from test_run_recording_contracts import (
    ALIGNMENT,
    ANALYZER,
    IN_T1,
    IN_T2,
    MEASUREMENTS,
    METRIC,
    MIX,
    RATE,
    RENDER_REF,
    S1,
    S2,
    SCOPE,
    SECONDS,
    SPEC,
    STATE,
    T1,
    T2,
    UNIT,
    UTC_NOW,
    _artifact,
    _configuration,
    _roots,
    _run_request,
    _state,
)

from autonomous_mixing.core.loop.observation import analysis_bindings
from autonomous_mixing.core.ports.analysis import validate_analysis_response
from autonomous_mixing.core.records.analysis import (
    AnalysisRequest,
    AnalysisResponse,
    deduplicate_analysis_definitions,
    require_analysis_scope_within,
)
from autonomous_mixing.core.records.artifacts import ArtifactKind
from autonomous_mixing.core.records.identifiers import ArtifactId, ObservationId, VersionRef
from autonomous_mixing.core.records.integrity import ArtifactRootBinding
from autonomous_mixing.core.records.observation import (
    AnalysisScope,
    AnalysisSpec,
    FrequencyRange,
    MeasuredScalar,
    Observation,
    TimeRange,
    Unavailable,
)
from autonomous_mixing.core.records.verification import MetricSelector
from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition, SourceRequirement

METRIC_D = VersionRef("metric-d", "1")  # a labelled diagnostic-only metric: no criterion names it
FIRST_HALF = AnalysisScope((T1, T2), TimeRange(0.0, 30.0))
T1_ONLY = AnalysisScope((T1,), TimeRange(0.0, 60.0))
MIX_SOURCE = SourceRequirement(ArtifactKind.MIX, (T1, T2), RENDER_REF, RATE, 2, 0.0, RATE * SECONDS)
CURRENT = (S1, S2, MIX)


def _definition(metric=METRIC, scope=SCOPE, **overrides) -> MeasurementDefinition:
    fields = dict(
        selector=MetricSelector(metric, scope), unit=UNIT, analysis_spec=SPEC, analyzer=ANALYZER,
        alignment=ALIGNMENT, sources=(MIX_SOURCE,),
    )  # fmt: skip
    fields.update(overrides)
    return MeasurementDefinition(**fields)


CRITERION = MEASUREMENTS[0].definition


def _request(definitions=(CRITERION,), bindings=None, *, state=STATE, metrics=(METRIC,), scope=SCOPE):
    return AnalysisRequest(
        state, scope, AnalysisSpec(SPEC, metrics), definitions, _roots(CURRENT) if bindings is None else bindings
    )


def _observation(oid, metric=METRIC, scope=SCOPE, result=None, sources=("mix",)) -> Observation:
    return Observation(
        ObservationId(oid), metric, tuple(ArtifactId(a) for a in sources), scope,
        result if result is not None else MeasuredScalar(0.5, UNIT), SPEC, ANALYZER, UTC_NOW,
    )  # fmt: skip


# --- request fields ----------------------------------------------------------------------------------


def test_analysis_request_has_exactly_five_required_fields_and_keeps_its_objects():
    fields = dataclasses.fields(AnalysisRequest)
    assert [f.name for f in fields] == ["state", "scope", "specification", "definitions", "root_bindings"]
    assert all(f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING for f in fields)
    definitions, bindings = (CRITERION,), _roots(CURRENT)
    request = _request(definitions, bindings)
    assert request.definitions is definitions and request.root_bindings is bindings
    with pytest.raises(TypeError):
        AnalysisRequest(STATE, SCOPE, AnalysisSpec(SPEC, (METRIC,)))


# --- deduplication -----------------------------------------------------------------------------------


def test_deduplication_keeps_the_first_object_per_key_in_first_occurrence_order():
    diagnostic, narrower = _definition(METRIC_D, FIRST_HALF), _definition(METRIC, FIRST_HALF)
    equal_copy = _definition()
    assert equal_copy == CRITERION and equal_copy is not CRITERION
    result = deduplicate_analysis_definitions((CRITERION, diagnostic, equal_copy, narrower, diagnostic))
    assert result == (CRITERION, diagnostic, narrower)
    assert result[0] is CRITERION and result[1] is diagnostic and result[2] is narrower
    assert deduplicate_analysis_definitions(tuple(m.definition for m in MEASUREMENTS)) == (CRITERION,)


@pytest.mark.parametrize(
    "conflict",
    [
        {"unit": "unit-b"},
        {"analysis_spec": VersionRef("analysis-spec", "2")},
        {"analyzer": VersionRef("analyzer", "0.2")},
        {"alignment": VersionRef("alignment", "2")},
        {"sources": (dataclasses.replace(MIX_SOURCE, frame_count=RATE),)},
        {"sources": (MIX_SOURCE, MIX_SOURCE)},
    ],
    ids=["unit", "analysis-spec", "analyzer", "alignment", "source-metadata", "source-count"],
)
def test_deduplication_rejects_unequal_definitions_with_the_same_key(conflict):
    with pytest.raises(ValueError, match="conflicting"):
        deduplicate_analysis_definitions((CRITERION, _definition(**conflict)))


@pytest.mark.parametrize("definitions", [(), [CRITERION], (CRITERION, "metric-a")], ids=["empty", "list", "member"])
def test_deduplication_needs_a_nonempty_tuple_of_definitions(definitions):
    with pytest.raises(ValueError):
        deduplicate_analysis_definitions(definitions)


# --- request jobs ------------------------------------------------------------------------------------


def test_one_metric_may_be_requested_at_two_scopes():
    request = _request((CRITERION, _definition(METRIC, FIRST_HALF)))
    assert [d.selector.scope for d in request.definitions] == [SCOPE, FIRST_HALF]


def test_diagnostic_only_jobs_are_valid_request_definitions():
    request = _request((CRITERION, _definition(METRIC_D, T1_ONLY)), metrics=(METRIC, METRIC_D))
    assert request.definitions[1].selector.metric == METRIC_D


@pytest.mark.parametrize(
    "build, match",
    [
        (lambda: _request(()), "definitions"),
        (lambda: _request([CRITERION]), "definitions"),
        (lambda: _request((CRITERION, "metric-a")), "definitions"),
        (lambda: _request((CRITERION,), metrics=(METRIC, METRIC_D)), "metric-d"),
        (lambda: _request((CRITERION, _definition(METRIC_D))), "unrequested"),
        (lambda: _request((_definition(analysis_spec=VersionRef("analysis-spec", "2")),)), "analysis spec"),
        (lambda: _request((_definition(scope=SCOPE),), scope=FIRST_HALF), "outside"),
        (lambda: _request((_definition(scope=SCOPE),), scope=T1_ONLY), "outside"),
        (
            lambda: _request((CRITERION,), scope=dataclasses.replace(SCOPE, frequency_range=FrequencyRange(20.0, 200.0))),
            "frequency",
        ),
        (lambda: _request((CRITERION, _definition())), "more than once"),
        (lambda: _request((CRITERION, _definition(unit="unit-b"))), "more than once"),
    ],
    ids=[
        "empty", "list", "member", "missing-metric-definition", "unrequested-metric", "wrong-spec",
        "time-outside-scope", "tracks-outside-scope", "band-required", "equal-duplicate", "conflicting-duplicate",
    ],
)  # fmt: skip
def test_request_definitions_must_cover_the_specification_exactly_within_scope(build, match):
    with pytest.raises(ValueError, match=match):
        build()


# --- request roots -----------------------------------------------------------------------------------


def test_root_bindings_name_exactly_the_current_members_in_any_order():
    reordered = (ArtifactRootBinding(MIX.artifact_id, "b"), ArtifactRootBinding(S2.artifact_id, "a"),
                 ArtifactRootBinding(S1.artifact_id, "c"))  # fmt: skip
    assert _request(bindings=reordered).root_bindings is reordered


def test_retained_current_members_from_an_older_production_revision_are_bound():
    older = _artifact("s2-old", "r0", ("t2",))
    state = _state(members=(S1, older, MIX))
    request = _request(bindings=_roots((S1, older, MIX)), state=state)
    assert {str(b.artifact_id) for b in request.root_bindings} == {"s1", "s2-old", "mix"}


@pytest.mark.parametrize(
    "bindings, match",
    [
        (_roots((S1, MIX)), "missing"),
        (_roots((S1, S2, MIX, S1)), "duplicate"),
        (_roots((S1, S2, MIX, IN_T1)), "extra"),  # a superseded fixed input is not a current-analysis root
        (_roots((S1, S2, MIX, IN_T1, IN_T2)), "extra"),
        (list(_roots(CURRENT)), "root_bindings"),
        ((*_roots(CURRENT), "exports"), "root_bindings"),
    ],
    ids=["missing", "duplicate", "fixed-input", "all-required", "list", "member"],
)
def test_root_bindings_reject_missing_duplicate_extra_and_wrong_types(bindings, match):
    with pytest.raises(ValueError, match=match):
        _request(bindings=bindings)


def test_request_construction_touches_no_filesystem(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("filesystem access during request construction")

    for module, name in ((builtins, "open"), (os, "stat"), (os, "lstat"), (os, "listdir"), (os, "scandir")):
        monkeypatch.setattr(module, name, forbidden)
    _request((CRITERION, _definition(METRIC, FIRST_HALF)))
    _run_request()


# --- scope containment owner -------------------------------------------------------------------------


def test_scope_containment_lives_with_the_records_and_keeps_its_semantics():
    from autonomous_mixing.core.ports import analysis as port

    assert not hasattr(port, "_require_scope_within")
    band = dataclasses.replace(SCOPE, frequency_range=FrequencyRange(20.0, 200.0))
    require_analysis_scope_within(FIRST_HALF, SCOPE, "inner")
    require_analysis_scope_within(dataclasses.replace(FIRST_HALF, frequency_range=FrequencyRange(40.0, 100.0)), band,
                                  "inner")  # fmt: skip
    require_analysis_scope_within(band, SCOPE, "inner")  # no outer band: any inner band is contained
    for inner, outer, match in (
        (SCOPE, T1_ONLY, "tracks outside"),
        (SCOPE, FIRST_HALF, "time range"),
        (SCOPE, band, "explicit frequency range"),
        (dataclasses.replace(SCOPE, frequency_range=FrequencyRange(10.0, 100.0)), band, "frequency range lies"),
    ):
        with pytest.raises(ValueError, match=match):
            require_analysis_scope_within(inner, outer, "inner")


# --- run request jobs --------------------------------------------------------------------------------


def test_run_request_requires_analysis_definitions_after_measurements():
    names = [f.name for f in dataclasses.fields(type(_run_request()))]
    assert names.index("analysis_definitions") == names.index("measurements") + 1
    fields = {f.name: getattr(_run_request(), f.name) for f in dataclasses.fields(type(_run_request()))}
    del fields["analysis_definitions"]
    with pytest.raises(TypeError):
        type(_run_request())(**fields)


def test_shared_criterion_definitions_are_deduplicated_by_the_caller():
    supplied = tuple(m.definition for m in MEASUREMENTS)
    with pytest.raises(ValueError, match="more than once"):
        _run_request(analysis_definitions=supplied)
    request = _run_request(analysis_definitions=deduplicate_analysis_definitions(supplied))
    assert request.analysis_definitions == (CRITERION,) and request.analysis_definitions[0] is CRITERION


def test_run_request_accepts_a_diagnostic_only_job():
    configuration = _configuration(analysis_spec=AnalysisSpec(SPEC, (METRIC, METRIC_D)))
    diagnostic = _definition(METRIC_D, T1_ONLY)
    request = _run_request(configuration=configuration, analysis_definitions=(CRITERION, diagnostic))
    assert request.analysis_definitions == (CRITERION, diagnostic)


@pytest.mark.parametrize(
    "definitions, metrics, match",
    [
        ((_definition(METRIC, FIRST_HALF),), (METRIC,), "condition"),  # criterion key absent from the jobs
        ((_definition(analyzer=VersionRef("analyzer", "0.2")),), (METRIC,), "condition"),  # conflicting job
        ((_definition(alignment=VersionRef("alignment", "2")),), (METRIC,), "condition"),
        ((CRITERION,), (METRIC, METRIC_D), "metric-d"),  # a requested metric without a job
        ((CRITERION, _definition(METRIC_D)), (METRIC,), "unrequested"),
        ((), (METRIC,), "analysis_definitions"),
        ([CRITERION], (METRIC,), "analysis_definitions"),
    ],
    ids=["criterion-missing", "criterion-analyzer", "criterion-alignment", "metric-without-job",
         "job-without-metric", "empty", "list"],
)  # fmt: skip
def test_run_request_rejects_incomplete_or_conflicting_jobs(definitions, metrics, match):
    configuration = _configuration(analysis_spec=AnalysisSpec(SPEC, metrics))
    with pytest.raises(ValueError, match=match):
        _run_request(configuration=configuration, analysis_definitions=definitions)


# --- exact response completeness ---------------------------------------------------------------------

TWO_SCOPES = _request((CRITERION, _definition(METRIC, FIRST_HALF)))


def test_a_complete_response_in_any_order_is_valid():
    validate_analysis_response(
        TWO_SCOPES, AnalysisResponse((_observation("o-half", scope=FIRST_HALF), _observation("o-all")))
    )


def test_an_unavailable_observation_satisfies_structural_completeness():
    unavailable = _observation("o-half", scope=FIRST_HALF, result=Unavailable("labelled: not measurable"))
    validate_analysis_response(TWO_SCOPES, AnalysisResponse((_observation("o-all"), unavailable)))


@pytest.mark.parametrize(
    "observations, match",
    [
        ((_observation("o-all"),), "no observation"),  # one metric at two scopes, one scope omitted
        ((), "no observation"),
        (
            (_observation("o-all"), _observation("o-half", scope=FIRST_HALF), _observation("o-t1", scope=T1_ONLY)),
            "not requested",
        ),  # contained but unrequested scope
        ((_observation("o-all"), _observation("o-half", scope=FIRST_HALF), _observation("o-d", METRIC_D)), "unrequested"),
        ((_observation("o-all"), _observation("o-half", scope=FIRST_HALF), _observation("o-all-2")), "duplicate"),
    ],
    ids=["second-scope-omitted", "empty", "contained-unrequested-scope", "extra-metric", "duplicate-key"],
)  # fmt: skip
def test_responses_must_cover_exactly_the_requested_metric_scope_pairs(observations, match):
    with pytest.raises(ValueError, match=match):
        validate_analysis_response(TWO_SCOPES, AnalysisResponse(observations))


# --- current-artifact binding helper -----------------------------------------------------------------


def test_analysis_bindings_select_current_members_in_checkpoint_order():
    known = (*_roots((IN_T2, MIX), "exports"), *_roots((S2, S1, IN_T1), "stems"))
    result = analysis_bindings(STATE, known)
    assert [(str(b.artifact_id), b.root_key) for b in result] == [("s1", "stems"), ("s2", "stems"), ("mix", "exports")]


def test_analysis_bindings_join_explicit_produced_bindings_for_older_and_new_members():
    older, new_stem, new_mix = (_artifact("s2", "r1", ("t2",)), _artifact("s1-r2", "r2", ("t1",)),
                                _artifact("mix-r2", "r2", ("t1", "t2"), ArtifactKind.MIX))  # fmt: skip
    candidate = _state(members=(new_stem, older, new_mix), revision="r2")
    produced = _roots((new_stem, new_mix), "workspace-root")
    result = analysis_bindings(candidate, _roots((S1, S2, MIX, IN_T1, IN_T2)), produced)
    assert [(str(b.artifact_id), b.root_key) for b in result] == [
        ("s1-r2", "workspace-root"), ("s2", "exports"), ("mix-r2", "workspace-root")]  # fmt: skip


@pytest.mark.parametrize(
    "known, produced, match",
    [
        (_roots((S1, S2)), (), "mix"),
        ((), (), "s1"),
        (_roots((S1, S2, MIX, S1)), (), "more than once"),
        (_roots((S1, S2, MIX)), _roots((MIX,), "other"), "more than once"),  # conflicting root across tuples
        (_roots((S1, S2, MIX)), _roots((MIX,)), "more than once"),
        ([*_roots(CURRENT)], (), "known"),
        (_roots(CURRENT), ("renders",), "produced"),
    ],
    ids=["missing-current", "nothing-known", "duplicate-known", "conflict-across", "repeat-across", "list", "member"],
)
def test_analysis_bindings_reject_missing_duplicate_or_conflicting_ids(known, produced, match):
    with pytest.raises(ValueError, match=match):
        analysis_bindings(STATE, known, produced)


def test_analysis_bindings_never_invent_a_default_root():
    # Fixed inputs and other retained artifacts never stand in for a missing current member.
    with pytest.raises(ValueError, match="mix"):
        analysis_bindings(STATE, _roots((S1, S2, IN_T1, IN_T2)))
