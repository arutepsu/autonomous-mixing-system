"""Contract tests for the enriched decision input ``Situation`` (increment 2D).

Spec: docs/superpowers/specs/2026-11-05-situation-integration-design.md. All records are metadata fixtures:
the diagnosis reports are labelled test doubles (no detector ran), metric names, units, scopes and budgets
are structural examples, and nothing here is evaluation data or a musical demonstration. A valid
``Situation`` shows structural consistency of one decision input only; it does not show that a problem
exists or is absent, that an action is permitted, or that a run exists.
"""

import dataclasses
from datetime import datetime, timezone

import pytest

from autonomous_mixing.core.records.actions import Action, ExecutionStatus, SetTrackGain
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.budgets import BudgetLimits, BudgetUsage
from autonomous_mixing.core.records.capabilities import ActionCapabilityView, AvailableTrackGain, GainBounds
from autonomous_mixing.core.records.context import AssertionSource, MusicalContext, TrackRole
from autonomous_mixing.core.records.decision import ControllerIdentity, Situation
from autonomous_mixing.core.records.diagnosis import (
    DiagnosisCoverage,
    DiagnosisCoverageStatus,
    DiagnosisReport,
    DiagnosticEvidence,
    ProblemCandidate,
    ProblemFamily,
    UncertaintyRepresentation,
)
from autonomous_mixing.core.records.feedback import (
    ActionFeedback,
    ActionFeedbackDisposition,
    DecisionFeedback,
    DecisionHistory,
    FeedbackStage,
    ObservationFeedback,
    PortFailureFeedback,
)
from autonomous_mixing.core.records.identifiers import (
    ActionId,
    ArtifactId,
    AttemptId,
    CheckpointId,
    ConditionId,
    ContextId,
    CriteriaId,
    DecisionCallId,
    DecisionId,
    DiagnosisCoverageId,
    ExecutionId,
    ObservationId,
    ProblemCandidateId,
    ProjectId,
    Revision,
    RunId,
    TrackId,
    VerificationResultId,
    VersionRef,
)
from autonomous_mixing.core.records.observation import (
    AnalysisScope,
    FrequencyRange,
    MeasuredScalar,
    Observation,
    TimeRange,
)
from autonomous_mixing.core.records.project import (
    ArtifactCheckpoint,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    Track,
    TrackGainState,
)
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode
from autonomous_mixing.core.records.verification import (
    AbsoluteGuard,
    AcceptanceCriteria,
    AssessmentMode,
    BaselineReference,
    CheckResult,
    CheckStatus,
    ComparisonOperator,
    DeltaGuard,
    ImprovementCondition,
    ImprovementDirection,
    MetricSelector,
    TargetCondition,
    TargetStatus,
    Verdict,
    VerificationResult,
)

UTC_NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
METRIC = VersionRef("metric-a", "1")
METHOD = VersionRef("test-double-diagnosis", "0")  # labelled test double; no detection method exists
CONTROLLER = ControllerIdentity("deterministic-baseline", "0.1")
LIMITS = BudgetLimits(4, 2, 6, 1, 300.0)
RUN = RunId("run-1")

NEW_FIELDS = ("run_id", "scope", "criteria", "diagnosis", "capabilities", "history")


# --- state, context, observations -------------------------------------------------------------------


def _artifact(aid, tracks, revision, *, kind=ArtifactKind.STEM) -> AudioArtifact:
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
        "d" * 64,
        VersionRef("render", "1"),
    )


def _state(revision="r2") -> ProjectState:
    """State r2 keeps stem a-t1 produced at r1; t2's input a-t2 (r1) is superseded in the audio by c-t2."""
    tracks = tuple(Track(TrackId(f"t{i}"), f"track {i}") for i in (1, 2, 3))
    snapshot = ProjectSnapshot(ProjectId("p1"), Revision(revision), UTC_NOW, tracks)
    members = (
        _artifact("a-t1", ("t1",), "r1"),
        _artifact("c-t2", ("t2",), revision),
        _artifact("c-mix", ("t1", "t2", "t3"), revision, kind=ArtifactKind.MIX),
    )
    inputs = {"t1": _artifact("a-t1", ("t1",), "r1"), "t2": _artifact("a-t2", ("t2",), "r1")}
    gains = tuple(TrackGainState(TrackId(t), artifact, 0.0) for t, artifact in inputs.items())
    return ProjectState(
        snapshot,
        ProjectProcessingState(ProjectId("p1"), Revision(revision), gains),
        ArtifactCheckpoint(CheckpointId("cp"), ProjectId("p1"), Revision(revision), members),
    )


def _context(context_id="c1", version="1") -> MusicalContext:
    source = AssertionSource("producer", UTC_NOW)
    roles = tuple(TrackRole(TrackId(t), r, source) for t, r in [("t1", "ambience"), ("t2", "lead"), ("t3", "texture")])
    return MusicalContext(ContextId(context_id), version, roles, (), frozenset())


def _scope(tracks=("t1", "t2"), start=0.0, end=60.0, band=None) -> AnalysisScope:
    return AnalysisScope(tuple(TrackId(t) for t in tracks), TimeRange(start, end), band)


def _observation(oid, sources, scope=None) -> Observation:
    return Observation(
        ObservationId(oid),
        METRIC,
        tuple(ArtifactId(a) for a in sources),
        scope if scope is not None else _scope(("t1", "t2", "t3")),  # broader than the run scope: allowed
        MeasuredScalar(-14.0, "unit-a"),
        VersionRef("spec", "1"),
        VersionRef("analyzer", "0.1"),
        UTC_NOW,
    )


O_MIX = _observation("o-mix", ("c-mix",))
O_RETAINED = _observation("o-retained", ("a-t1",), _scope(("t1",)))  # r1-produced, still a current member
O_SUPERSEDED = _observation("o-superseded", ("a-t2",), _scope(("t2",)))  # a-t2 is not a current member


# --- criteria, diagnosis, capabilities, history ------------------------------------------------------


def _criteria(scope, *, target=None, improvement=None, absolute=None, delta=None) -> AcceptanceCriteria:
    """One condition per collection; each selector scope defaults to the run scope."""

    def sel(override):
        return MetricSelector(METRIC, scope if override is None else override)

    threshold = MeasuredScalar(-10.0, "unit-a")
    return AcceptanceCriteria(
        CriteriaId("crit"),
        "1",
        (TargetCondition(ConditionId("target"), sel(target), ComparisonOperator.LE, threshold),),
        (
            ImprovementCondition(
                ConditionId("improve"), sel(improvement), ImprovementDirection.LOWER, MeasuredScalar(1.0, "unit-a")
            ),
        ),
        (
            AbsoluteGuard(ConditionId("absolute"), sel(absolute), ComparisonOperator.LE, threshold),
            DeltaGuard(ConditionId("delta"), sel(delta), BaselineReference.INITIAL, MeasuredScalar(2.0, "unit-a")),
        ),
    )


def _coverage(scope, status=DiagnosisCoverageStatus.ASSESSED, cid="cov-1") -> DiagnosisCoverage:
    method = None if status is DiagnosisCoverageStatus.UNAVAILABLE else METHOD
    return DiagnosisCoverage(
        DiagnosisCoverageId(cid), ProblemFamily.MASKING, scope, status, method, "labelled test-double coverage"
    )


def _candidate(scope, evidence=("o-mix",), revision="r2") -> ProblemCandidate:
    statement = DiagnosticEvidence(
        "test-double strength", "uncalibrated", UncertaintyRepresentation.UNCALIBRATED, False, (), ()
    )
    return ProblemCandidate(
        ProblemCandidateId("cand-1"),
        DiagnosisCoverageId("cov-1"),
        ProjectId("p1"),
        Revision(revision),
        ProblemFamily.MASKING,
        scope,
        tuple(ObservationId(o) for o in evidence),
        METHOD,
        statement,
    )


def _diagnosis(scope, *, candidates=None, revision="r2", coverage=None) -> DiagnosisReport:
    candidates = (_candidate(scope, revision=revision),) if candidates is None else candidates
    rows = (_coverage(scope),) if coverage is None else coverage
    return DiagnosisReport(ProjectId("p1"), Revision(revision), rows, candidates)


def _capabilities(scope, *, project="p1", revision="r2", context=("c1", "1"), gains=None) -> ActionCapabilityView:
    gains = (str(scope.track_ids[-1]),) if gains is None else gains  # advertised tracks lie in the view scope
    return ActionCapabilityView(
        ProjectId(project),
        Revision(revision),
        ContextId(context[0]),
        context[1],
        VersionRef("policy", "1"),
        VersionRef("renderer", "1"),
        scope,
        tuple(AvailableTrackGain(TrackId(t), GainBounds(-6.0, 6.0)) for t in gains),
    )


def _usage(calls, elapsed=1.0) -> BudgetUsage:
    return BudgetUsage(calls, 0, 0, 0, elapsed)


def _accepted_entry() -> DecisionFeedback:
    """Call 1: an accepted gain action r1 -> r2 whose copied evidence includes the superseded a-t2 source."""
    old = ObservationFeedback(Revision("r1"), O_SUPERSEDED)
    new = ObservationFeedback(Revision("r2"), _observation("h-o2", ("c-mix",)))
    check = CheckResult(
        ConditionId("target"),
        CheckStatus.PASS,
        "recorded check",
        (ObservationId("o-superseded"), ObservationId("h-o2")),
    )
    verification = VerificationResult(
        VerificationResultId("v1"),
        CriteriaId("crit"),
        "1",
        AssessmentMode.ATTEMPT,
        (check,),
        Verdict.ACCEPTED,
        TargetStatus.UNMET,
    )
    feedback = ActionFeedback(
        Action(ActionId("a1"), ProjectId("p1"), Revision("r1"), SetTrackGain(TrackId("t2"), -2.0)),
        AttemptId("att-1"),
        ExecutionId("ex-1"),
        ActionFeedbackDisposition.ACCEPTED,
        FeedbackStage.ACCEPTANCE,
        ExecutionStatus.CANDIDATE_READY,
        verification,
        None,
        "accepted",
        "recorded outcome",
        (old, new),
    )
    return DecisionFeedback(
        DecisionCallId("call-1"),
        1,
        RUN,
        ProjectId("p1"),
        Revision("r1"),
        Revision("r2"),
        Revision("r2"),
        DecisionId("d1"),
        _usage(1),
        feedback,
    )


def _port_failure_entry(sequence, before="r2", selected="r2") -> DecisionFeedback:
    failure = PortFailureFeedback(PortFailure(PortFailureCode.UNAVAILABLE, "provider unavailable", True))
    return DecisionFeedback(
        DecisionCallId(f"call-{sequence}"),
        sequence,
        RUN,
        ProjectId("p1"),
        Revision(before),
        None,
        None if selected is None else Revision(selected),
        None,
        _usage(sequence),
        failure,
    )


def _history(*entries, run=RUN) -> DecisionHistory:
    return DecisionHistory(run, ProjectId("p1"), entries)


def _fields(scope=None, *, history=None, usage=None, **overrides) -> dict:
    """Constructor arguments for a valid first call (empty history, one charged call) unless overridden."""
    scope = _scope() if scope is None else scope
    history = _history() if history is None else history
    fields = dict(
        state=_state(),
        context=_context(),
        controller=CONTROLLER,
        observations=(O_MIX, O_RETAINED),
        usage=_usage(len(history.entries) + 1, elapsed=5.0) if usage is None else usage,
        limits=LIMITS,
        run_id=RUN,
        scope=scope,
        criteria=_criteria(scope),
        diagnosis=_diagnosis(scope),
        capabilities=_capabilities(scope),
        history=history,
    )
    fields.update(overrides)
    return fields


def _later_history() -> DecisionHistory:
    return _history(_accepted_entry(), _port_failure_entry(2))


# --- shape -------------------------------------------------------------------------------------------


def test_fields_are_the_six_existing_then_the_six_new_ones_all_required():
    fields = dataclasses.fields(Situation)
    assert [f.name for f in fields] == [
        "state",
        "context",
        "controller",
        "observations",
        "usage",
        "limits",
        *NEW_FIELDS,
    ]
    for field in fields:
        assert field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING, field.name


@pytest.mark.parametrize("name", NEW_FIELDS)
def test_omitting_a_new_field_is_a_type_error(name):
    fields = _fields()
    del fields[name]
    with pytest.raises(TypeError, match=f"missing .*{name}"):  # dataclass behaviour; no fabricated default
        Situation(**fields)


# --- positive constructions --------------------------------------------------------------------------


def test_first_call_with_empty_history_keeps_every_supplied_object():
    fields = _fields()
    situation = Situation(**fields)
    for name, value in fields.items():
        assert getattr(situation, name) is value, name
    assert situation.history.entries == () and situation.usage.decision_calls == 1


def test_later_call_with_the_complete_prior_prefix_keeps_every_supplied_object():
    history = _later_history()
    fields = _fields(history=history, usage=BudgetUsage(3, 1, 1, 0, 9.0))
    situation = Situation(**fields)
    for name, value in fields.items():
        assert getattr(situation, name) is value, name
    assert len(situation.history.entries) == 2


def test_explicit_empty_inputs_are_valid_and_kept_without_implying_anything():
    # An UNAVAILABLE row, an ASSESSED row without candidates, no available gain action and an empty first
    # history: none of them states that the mix has no problem, that No Change is justified or that a
    # previous call succeeded. They are kept exactly as supplied.
    scope = _scope()
    rows = (
        _coverage(scope, DiagnosisCoverageStatus.ASSESSED, "cov-1"),
        _coverage(_scope(("t1",)), DiagnosisCoverageStatus.UNAVAILABLE, "cov-2"),
    )
    diagnosis = _diagnosis(scope, candidates=(), coverage=rows)
    capabilities = _capabilities(scope, gains=())
    situation = Situation(**_fields(scope, diagnosis=diagnosis, capabilities=capabilities))
    assert situation.diagnosis.candidates == () and situation.diagnosis.coverage == rows
    assert situation.capabilities.gain_actions == ()
    assert situation.history.entries == ()


def test_observations_broader_than_the_run_scope_are_allowed():
    # O_MIX measures t1-t3 while the run scope covers t1-t2; a band outside any run band is also allowed.
    banded = _observation("o-band", ("c-mix",), _scope(("t1", "t2", "t3"), band=FrequencyRange(5000.0, 9000.0)))
    assert set(O_MIX.scope.track_ids) > set(_scope().track_ids)
    Situation(**_fields(observations=(O_MIX, O_RETAINED, banded)))


# --- wrong types -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, value",
    [
        ("run_id", "run-1"),
        ("scope", (TrackId("t1"),)),
        ("criteria", None),
        ("diagnosis", (DiagnosisCoverageId("cov-1"),)),
        ("capabilities", {"project_id": "p1"}),
        ("history", ()),
    ],
)
def test_new_fields_must_have_their_record_types(name, value):
    fields = _fields()
    fields[name] = value  # set after building valid fields, so only Situation sees the wrong type
    with pytest.raises(ValueError, match=f"Situation.{name}"):
        Situation(**fields)


# --- bindings ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "capabilities",
    [
        _capabilities(_scope(), project="p9"),
        _capabilities(_scope(), revision="r1"),
        _capabilities(_scope(), context=("c9", "1")),
        _capabilities(_scope(), context=("c1", "2")),
        _capabilities(_scope(("t1",)), gains=("t1",)),  # contained in the run scope but not equal to it
        _capabilities(_scope(end=59.0)),
        _capabilities(_scope(band=FrequencyRange(20.0, 20000.0))),
    ],
    ids=["project", "revision", "context-id", "context-version", "scope-tracks", "scope-time", "scope-band"],
)
def test_capabilities_must_match_project_revision_context_and_exact_scope(capabilities):
    with pytest.raises(ValueError):
        Situation(**_fields(capabilities=capabilities))


def test_run_scope_must_name_known_tracks():
    scope = _scope(("t1", "t9"))
    with pytest.raises(ValueError, match="t9"):
        Situation(**_fields(scope))


def test_history_must_belong_to_the_independent_run_id():
    with pytest.raises(ValueError):
        Situation(**_fields(history=_history(run=RunId("run-2")), run_id=RUN))
    with pytest.raises(ValueError):
        Situation(**_fields(run_id=RunId("run-2")))


@pytest.mark.parametrize(
    "history, calls",
    [
        (_history(), 2),  # the charge of this call is not one more than the prefix
        (_history(), 0),  # an uncharged snapshot is not a decision input
        (_history(_port_failure_entry(1)), 1),  # the prefix is not followed by this call's charge
        (_history(_port_failure_entry(1, before="r1", selected="r1")), 2),  # chain ends at r1, state is r2
    ],
    ids=["missing-prefix", "uncharged", "stale-count", "chain-mismatch"],
)
def test_history_count_and_chain_bind_to_usage_and_state(history, calls):
    with pytest.raises(ValueError):
        Situation(**_fields(history=history, usage=_usage(calls, elapsed=5.0)))


def test_a_complete_one_entry_prefix_at_the_current_revision_is_valid():
    Situation(**_fields(history=_history(_port_failure_entry(1)), usage=_usage(2, elapsed=5.0)))


def test_history_ending_with_an_unknown_selected_revision_is_rejected():
    unresolved = ActionFeedback(
        Action(ActionId("a1"), ProjectId("p1"), Revision("r2"), SetTrackGain(TrackId("t2"), -2.0)),
        AttemptId("att-1"),
        ExecutionId("ex-1"),
        ActionFeedbackDisposition.UNRESOLVED,
        FeedbackStage.RENDER,
        ExecutionStatus.UNCERTAIN,
        None,
        None,
        "uncertain",
        "recorded outcome",
        (),
    )
    entry = DecisionFeedback(
        DecisionCallId("call-1"), 1, RUN, ProjectId("p1"), Revision("r2"), None, None, DecisionId("d1"),
        _usage(1), unresolved,
    )  # fmt: skip
    with pytest.raises(ValueError, match="unknown selected revision"):
        Situation(**_fields(history=_history(entry), usage=_usage(2)))


# --- criteria containment ----------------------------------------------------------------------------

COLLECTIONS = ("target", "improvement", "absolute", "delta")
BAND = FrequencyRange(100.0, 1000.0)


@pytest.mark.parametrize("collection", COLLECTIONS)
@pytest.mark.parametrize(
    "selector",
    [
        _scope(("t9",)),  # unknown track
        _scope(("t1", "t3")),  # t3 is outside the run tracks
        _scope(start=0.0, end=60.5),  # ends after the run scope
    ],
    ids=["unknown-track", "track-outside", "time-after"],
)
def test_every_criteria_collection_must_stay_within_an_unrestricted_run_scope(collection, selector):
    scope = _scope()
    with pytest.raises(ValueError):
        Situation(**_fields(scope, criteria=_criteria(scope, **{collection: selector})))


@pytest.mark.parametrize("collection", COLLECTIONS)
@pytest.mark.parametrize(
    "selector",
    [
        _scope(start=5.0, end=50.0, band=BAND),  # starts before the run scope below (10 s)
        _scope(start=10.0, end=50.0),  # no band although the run scope restricts frequency
        _scope(start=10.0, end=50.0, band=FrequencyRange(99.0, 1000.0)),
        _scope(start=10.0, end=50.0, band=FrequencyRange(100.0, 1000.5)),
    ],
    ids=["time-before", "band-missing", "band-below", "band-above"],
)
def test_every_criteria_collection_must_stay_within_a_restricted_run_scope(collection, selector):
    scope = _scope(start=10.0, end=50.0, band=BAND)
    with pytest.raises(ValueError):
        Situation(**_fields(scope, criteria=_criteria(scope, **{collection: selector})))


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_exact_endpoints_and_sub_bands_are_contained(collection):
    scope = _scope(start=10.0, end=50.0, band=BAND)
    inner = {
        "target": _scope(start=10.0, end=50.0, band=BAND),  # identical endpoints
        "improvement": _scope(("t2",), 10.0, 20.0, FrequencyRange(100.0, 200.0)),
        "absolute": _scope(("t1",), 40.0, 50.0, FrequencyRange(900.0, 1000.0)),
        "delta": _scope(start=10.0, end=50.0, band=FrequencyRange(150.0, 900.0)),
    }
    Situation(**_fields(scope, criteria=_criteria(scope, **{collection: inner[collection]})))


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_a_selector_band_is_allowed_when_the_run_scope_has_no_frequency_restriction(collection):
    scope = _scope()
    banded = _scope(band=FrequencyRange(20.0, 200.0))
    Situation(**_fields(scope, criteria=_criteria(scope, **{collection: banded})))


# --- diagnosis and observation provenance ------------------------------------------------------------


def test_diagnosis_must_describe_the_current_revision():
    with pytest.raises(ValueError):
        Situation(**_fields(diagnosis=_diagnosis(_scope(), revision="r1")))


def test_diagnosis_coverage_must_stay_within_the_run_scope():
    scope = _scope(("t1",))
    outside = _diagnosis(scope, coverage=(_coverage(_scope()),), candidates=())
    with pytest.raises(ValueError, match="not contained"):
        Situation(**_fields(scope, diagnosis=outside, capabilities=_capabilities(scope, gains=())))


def test_cited_diagnosis_evidence_must_be_among_the_current_observations():
    diagnosis = _diagnosis(_scope(), candidates=(_candidate(_scope(), evidence=("o-absent",)),))
    with pytest.raises(ValueError, match="missing"):
        Situation(**_fields(diagnosis=diagnosis))


def test_unchanged_older_production_sources_pass_when_they_are_current_members():
    diagnosis = _diagnosis(_scope(), candidates=(_candidate(_scope(), evidence=("o-retained",)),))
    Situation(**_fields(diagnosis=diagnosis))


def test_a_superseded_source_fails_as_a_current_observation():
    with pytest.raises(ValueError):
        Situation(**_fields(observations=(O_MIX, O_SUPERSEDED)))


def test_historical_superseded_observations_stay_valid_history_but_not_current_input():
    history = _later_history()
    assert O_SUPERSEDED in [item.observation for item in history.entries[0].outcome.observations]
    Situation(**_fields(history=history, usage=_usage(3)))  # history is not rechecked against current audio
    with pytest.raises(ValueError):
        Situation(**_fields(history=history, usage=_usage(3), observations=(O_MIX, O_SUPERSEDED)))
