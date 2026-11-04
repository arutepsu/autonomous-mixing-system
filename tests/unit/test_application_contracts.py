"""Focused tests for application result records, observation/decision contracts and ports (Task 3A).

All records are metadata fixtures; the analyzer and controller below are minimal in-memory test doubles,
not product features. Metric names, units and budgets are structural examples only.
"""

import dataclasses
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autonomous_mixing.core.ports.analysis import AudioAnalyzer, validate_analysis_response
from autonomous_mixing.core.ports.decision import DecisionMaker, validate_decision_response
from autonomous_mixing.core.records.actions import Action, SetTrackGain
from autonomous_mixing.core.records.analysis import AnalysisRequest, AnalysisResponse
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.budgets import BudgetLimits, BudgetUsage
from autonomous_mixing.core.records.context import AssertionSource, MusicalContext, TrackRole
from autonomous_mixing.core.records.decision import (
    Abstain,
    AbstentionReason,
    ControllerIdentity,
    Decision,
    DecisionResponse,
    ProposeAction,
    Situation,
)
from autonomous_mixing.core.records.identifiers import (
    ActionId,
    ArtifactId,
    CheckpointId,
    ContextId,
    DecisionId,
    ObservationId,
    ProjectId,
    Revision,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.observation import (
    AnalysisScope,
    AnalysisSpec,
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
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, PortResult, Success

UTC_NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
METRIC_A, METRIC_B, METRIC_C = VersionRef("metric-a", "1"), VersionRef("metric-b", "1"), VersionRef("metric-c", "1")
SPEC = AnalysisSpec(VersionRef("spec", "1"), (METRIC_A, METRIC_B))
CONTROLLER = ControllerIdentity("deterministic-baseline", "0.1")
SRC_DIR = Path(__file__).resolve().parents[2] / "src" / "autonomous_mixing"
# --- fixtures ----------------------------------------------------------------------


def _snapshot(revision: str = "r2", project: str = "p1") -> ProjectSnapshot:
    names = ("Field recording", "Choir", "Tape loop")
    tracks = tuple(Track(TrackId(f"t{i}"), name) for i, name in enumerate(names, start=1))
    return ProjectSnapshot(ProjectId(project), Revision(revision), UTC_NOW, tracks)


def _artifact(aid, tracks, revision, *, kind=ArtifactKind.STEM, seconds=60, project="p1") -> AudioArtifact:
    return AudioArtifact(
        ArtifactId(aid),
        ProjectId(project),
        Revision(revision),
        tuple(TrackId(t) for t in tracks),
        kind,
        f"outputs/{aid}.wav",
        0.0,
        48000,
        2,
        48000 * seconds,
        "d" * 64,
        VersionRef("render", "1"),
    )


def _checkpoint(revision: str = "r2", artifacts=None, project: str = "p1") -> ArtifactCheckpoint:
    if artifacts is None:
        # State r2 retains a stem produced at r1 next to outputs produced at r2 (no retagging).
        artifacts = (
            _artifact("a-t1", ("t1",), "r1"),
            _artifact("c-t2", ("t2",), "r2"),
            _artifact("c-mix", ("t1", "t2", "t3"), "r2", kind=ArtifactKind.MIX),
        )
    return ArtifactCheckpoint(CheckpointId("cp2"), ProjectId(project), Revision(revision), artifacts)


def _processing(revision: str = "r2", project: str = "p1") -> ProjectProcessingState:
    # t1's input is the retained a-t1 member; t2's baseline a-t2 was superseded in audio but stays the input.
    inputs = {"t1": _artifact("a-t1", ("t1",), "r1"), "t2": _artifact("a-t2", ("t2",), "r1")}
    gains = tuple(TrackGainState(TrackId(t), artifact, -2.0 if t == "t2" else 0.0) for t, artifact in inputs.items())
    return ProjectProcessingState(ProjectId(project), Revision(revision), gains)


def _state(snapshot=None, processing=None, audio=None) -> ProjectState:
    return ProjectState(
        snapshot if snapshot is not None else _snapshot(),
        processing if processing is not None else _processing(),
        audio if audio is not None else _checkpoint(),
    )


def _scope(tracks=("t1", "t2", "t3"), start=0.0, end=60.0, freq=None) -> AnalysisScope:
    return AnalysisScope(tuple(TrackId(t) for t in tracks), TimeRange(start, end), freq)


def _observation(oid="o1", metric=METRIC_A, sources=("c-mix",), scope=None, spec=SPEC.ref, result=None) -> Observation:
    return Observation(
        ObservationId(oid),
        metric,
        tuple(ArtifactId(a) for a in sources),
        scope if scope is not None else _scope(("t1", "t2")),
        result if result is not None else MeasuredScalar(-14.0, "unit-a"),
        spec,
        VersionRef("analyzer", "0.1"),
        UTC_NOW,
    )


def _complete_observations() -> tuple[Observation, ...]:
    return (
        _observation("o-a", METRIC_A, ("c-mix",), _scope(("t1", "t2"))),
        _observation("o-b", METRIC_B, ("a-t1",), _scope(("t1",), 0.0, 30.0), result=Unavailable("silent region")),
    )


def _analysis_request(**overrides) -> AnalysisRequest:
    fields = dict(state=_state(), scope=_scope(), specification=SPEC)
    fields.update(overrides)
    return AnalysisRequest(**fields)


def _context(protected: frozenset = frozenset(), context_id: str = "c1") -> MusicalContext:
    source = AssertionSource("producer", UTC_NOW)
    roles = tuple(TrackRole(TrackId(t), r, source) for t, r in [("t1", "ambience"), ("t2", "lead"), ("t3", "texture")])
    return MusicalContext(ContextId(context_id), "1", roles, (), protected)


def _decision_request(**overrides) -> Situation:
    fields = dict(
        state=_state(),
        context=_context(),
        controller=CONTROLLER,
        observations=_complete_observations(),
        usage=BudgetUsage(1, 0, 2, 0, 3.5),
        limits=BudgetLimits(4, 2, 6, 1, 300.0),
    )
    fields.update(overrides)
    return Situation(**fields)


def _decision(outcome=None, evidence=("o-a",), *, context=("c1", "1"), controller=CONTROLLER, target="t2"):
    if outcome is None:
        action = Action(ActionId("x1"), ProjectId("p1"), Revision("r2"), SetTrackGain(TrackId(target), -2.0))
        outcome = ProposeAction(action, "lower track t2")
    return Decision(
        DecisionId("d1"),
        ProjectId("p1"),
        Revision("r2"),
        ContextId(context[0]),
        context[1],
        tuple(ObservationId(o) for o in evidence),
        "structured rationale",
        controller,
        outcome,
    )


class _InMemoryAnalyzer:
    """Test double: returns a pre-built result and counts calls; performs no analysis."""

    def __init__(self, result: PortResult[AnalysisResponse]) -> None:
        self.result = result
        self.calls = 0

    def analyze(self, request: AnalysisRequest) -> PortResult[AnalysisResponse]:
        self.calls += 1
        return self.result


class _InMemoryController:
    """Test double: returns a pre-built result and counts calls; makes no decisions."""

    def __init__(self, result: PortResult[DecisionResponse]) -> None:
        self.result = result
        self.calls = 0

    def decide(self, request: Situation) -> PortResult[DecisionResponse]:
        self.calls += 1
        return self.result


def _call_analyzer(analyzer: AudioAnalyzer, request: AnalysisRequest) -> PortResult[AnalysisResponse]:
    return analyzer.analyze(request)


def _call_controller(controller: DecisionMaker, request: Situation) -> PortResult[DecisionResponse]:
    return controller.decide(request)


# --- result records ------------------------------------------------------------------


def test_success_wraps_any_value_and_is_frozen():
    success = Success(AnalysisResponse(()))
    assert success.value == AnalysisResponse(())
    assert Success[int](3).value == 3
    with pytest.raises(dataclasses.FrozenInstanceError):
        success.value = None


def test_port_failure_codes_are_exactly_the_specified_set():
    assert {code.name for code in PortFailureCode} == {
        "INVALID_INPUT",
        "UNSUPPORTED",
        "UNAVAILABLE",
        "INVALID_RESPONSE",
        "INTERNAL_ERROR",
    }


@pytest.mark.parametrize(
    "code, message, retryable",
    [
        ("UNAVAILABLE", "service down", True),
        (PortFailureCode.UNAVAILABLE, "", True),
        (PortFailureCode.UNAVAILABLE, "  ", True),
        (PortFailureCode.UNAVAILABLE, RuntimeError("boom"), True),
        (PortFailureCode.UNAVAILABLE, "service down", 1),
        (PortFailureCode.UNAVAILABLE, "service down", "yes"),
        (PortFailureCode.UNAVAILABLE, "service down", None),
    ],
)
def test_port_failure_requires_typed_code_text_message_and_bool_retryable(code, message, retryable):
    with pytest.raises(ValueError):
        PortFailure(code, message, retryable)


def test_port_failure_holds_only_code_message_and_advisory_flag():
    failure = PortFailure(PortFailureCode.INVALID_RESPONSE, "schema mismatch", False)
    assert {f.name for f in dataclasses.fields(PortFailure)} == {"code", "message", "retryable"}
    with pytest.raises(dataclasses.FrozenInstanceError):
        failure.retryable = True


# --- analysis request ----------------------------------------------------------------


def test_analysis_request_accepts_state_with_older_production_artifacts():
    request = _analysis_request()
    assert {str(a.revision) for a in request.state.audio.artifacts} == {"r1", "r2"}
    assert request.state.audio.revision == request.state.snapshot.revision == request.state.revision
    # The t2 baseline (produced at r1) is not a current audio member but remains the fixed input.
    t2_input = request.state.processing.track_gains[1].input_artifact
    assert t2_input.artifact_id not in request.state.audio.artifact_ids and str(t2_input.revision) == "r1"


def test_requests_have_one_state_field_instead_of_snapshot_and_checkpoint():
    for record in (AnalysisRequest, Situation):
        names = {f.name for f in dataclasses.fields(record)}
        assert "state" in names and not names & {"snapshot", "checkpoint"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"state": "p1@r2"},
        {"state": _checkpoint()},
        {"state": _snapshot()},
        {"scope": (TrackId("t1"),)},
        {"specification": SPEC.ref},
    ],
    ids=["state", "audio-only", "snapshot-only", "scope", "specification"],
)
def test_analysis_request_rejects_wrong_types(overrides):
    with pytest.raises(ValueError):
        _analysis_request(**overrides)


FOREIGN = _artifact("x", ("t1",), "r2", project="p9")


@pytest.mark.parametrize(
    "build, match",
    [
        (lambda: _analysis_request(state=_state(audio=_checkpoint(revision="r1"))), "revision"),
        (lambda: _analysis_request(state=_state(processing=_processing(revision="r1"))), "revision"),
        (lambda: _analysis_request(state=_state(audio=_checkpoint(artifacts=(FOREIGN,), project="p9"))), "project"),
        (lambda: _analysis_request(state=_state(audio=_checkpoint(artifacts=(_artifact("x", ("t9",), "r2"),)))), "t9"),
        (lambda: _analysis_request(scope=_scope(("t1", "t9"))), "t9"),
    ],
    ids=["audio-revision", "processing-revision", "project", "unknown-artifact-track", "unknown-scope-track"],
)
def test_analysis_request_rejects_state_or_scope_inconsistencies(build, match):
    with pytest.raises(ValueError, match=match):
        build()


# --- analysis response ---------------------------------------------------------------


def test_analysis_response_may_be_empty_but_must_be_typed_and_unique():
    assert AnalysisResponse(()).observations == ()
    with pytest.raises(ValueError):
        AnalysisResponse(list(_complete_observations()))
    with pytest.raises(ValueError):
        AnalysisResponse((_observation("o1"), _observation("o1", METRIC_B)))
    with pytest.raises(ValueError):
        AnalysisResponse(("o1",))


def test_complete_analysis_response_is_valid():
    validate_analysis_response(_analysis_request(), AnalysisResponse(_complete_observations()))


@pytest.mark.parametrize(
    "observations, match",
    [
        ((_observation("o-a", METRIC_A),), "metric-b"),
        ((*_complete_observations(), _observation("o-c", METRIC_C)), "metric-c"),
        ((_observation("o-a", METRIC_A, spec=VersionRef("spec", "2")), _complete_observations()[1]), "spec"),
        ((*_complete_observations(), _observation("o-a2", METRIC_A)), "duplicate"),
    ],
    ids=["missing-metric", "unrequested-metric", "wrong-specification", "duplicate-metric-scope"],
)
def test_analysis_response_rejects_incomplete_or_mismatched_metrics(observations, match):
    with pytest.raises(ValueError, match=match):
        validate_analysis_response(_analysis_request(), AnalysisResponse(observations))


def test_same_metric_over_different_scopes_is_not_a_duplicate():
    extra = _observation("o-a2", METRIC_A, ("c-mix",), _scope(("t3",)))
    validate_analysis_response(_analysis_request(), AnalysisResponse((*_complete_observations(), extra)))


@pytest.mark.parametrize(
    "observation, request_overrides, match",
    [
        (_observation("o-a", sources=("a-gone",)), {}, "missing"),
        (_observation("o-a", sources=("a-t1",), scope=_scope(("t1", "t2"))), {}, "not covered"),
        (
            _observation("o-a", scope=_scope(("t1", "t2"), 0.0, 90.0)),
            {"scope": _scope(end=120.0)},
            "timeline",
        ),
        (_observation("o-a", scope=_scope(("t1", "t2"))), {"scope": _scope(("t1",))}, "outside"),
        (_observation("o-a", scope=_scope(("t1", "t2"), 0.0, 45.0)), {"scope": _scope(start=10.0)}, "outside"),
        (
            _observation("o-a", scope=_scope(("t1", "t2"))),
            {"scope": _scope(freq=FrequencyRange(20.0, 2000.0))},
            "frequency",
        ),
        (
            _observation("o-a", scope=_scope(("t1", "t2"), freq=FrequencyRange(10.0, 500.0))),
            {"scope": _scope(freq=FrequencyRange(20.0, 2000.0))},
            "frequency",
        ),
    ],
    ids=[
        "missing-source",
        "source-lacks-scope-track",
        "source-time-coverage",
        "tracks-outside-request",
        "time-outside-request",
        "band-required",
        "band-outside-request",
    ],
)
def test_analysis_response_rejects_invalid_sources_and_out_of_request_scopes(observation, request_overrides, match):
    observations = (observation, _complete_observations()[1])
    with pytest.raises(ValueError, match=match):
        validate_analysis_response(_analysis_request(**request_overrides), AnalysisResponse(observations))


def test_contained_bands_are_accepted_for_frequency_restricted_requests():
    band = FrequencyRange(20.0, 2000.0)
    observations = (
        _observation("o-a", scope=_scope(("t1", "t2"), freq=FrequencyRange(40.0, 2000.0))),
        _observation("o-b", METRIC_B, ("a-t1",), _scope(("t1",), freq=band)),
    )
    validate_analysis_response(_analysis_request(scope=_scope(freq=band)), AnalysisResponse(observations))


# --- decision request ----------------------------------------------------------------


def test_decision_request_with_arbitrary_roles_is_valid():
    request = _decision_request()
    assert request.context.role_for(TrackId("t3")) == "texture"
    assert isinstance(request.observations, tuple)


def test_usage_beyond_limits_remains_representable_in_decision_request():
    request = _decision_request(usage=BudgetUsage(9, 5, 20, 3, 400.0))
    assert request.usage.decision_calls > request.limits.max_decision_calls


@pytest.mark.parametrize(
    "overrides",
    [
        {"observations": list(_complete_observations())},
        {"observations": (_observation("o-a"), _observation("o-a", METRIC_B, ("a-t1",), _scope(("t1",))))},
        {"observations": ("o-a",)},
        {"context": ContextId("c1")},
        {"controller": ("deterministic-baseline", "0.1")},
        {"state": _checkpoint()},
        {"usage": BudgetLimits(4, 2, 6, 1, 300.0)},
        {"limits": BudgetUsage(1, 0, 2, 0, 3.5)},
        {"state": None},
    ],
    ids=[
        "mutable-observations",
        "duplicate-observation-ids",
        "untyped-observations",
        "context",
        "controller",
        "audio-only-state",
        "usage",
        "limits",
        "missing-state",
    ],
)
def test_decision_request_rejects_mutable_duplicate_and_wrong_typed_fields(overrides):
    with pytest.raises(ValueError):
        _decision_request(**overrides)


@pytest.mark.parametrize(
    "build, match",
    [
        (lambda: _decision_request(context=_context(protected=frozenset({TrackId("t9")}))), "t9"),
        (lambda: _decision_request(observations=(_observation("o-a", sources=("a-gone",)),)), "missing"),
        (lambda: _decision_request(state=_state(audio=_checkpoint(revision="r1"))), "revision"),
        (lambda: _decision_request(state=_state(processing=_processing(revision="r1"))), "revision"),
    ],
    ids=["context-unknown-track", "observation-source-missing", "mixed-audio-revision", "mixed-processing-revision"],
)
def test_decision_request_rejects_inconsistent_context_sources_and_state(build, match):
    with pytest.raises(ValueError, match=match):
        build()


# --- decision response ---------------------------------------------------------------


def test_valid_proposal_response():
    validate_decision_response(_decision_request(), DecisionResponse(_decision()))


def test_valid_abstention_with_empty_evidence_and_no_observations():
    request = _decision_request(observations=())
    response = DecisionResponse(_decision(Abstain(AbstentionReason.INSUFFICIENT_EVIDENCE), evidence=()))
    validate_decision_response(request, response)


@pytest.mark.parametrize(
    "decision, match",
    [
        (_decision(context=("c9", "1")), "context"),
        (_decision(context=("c1", "2")), "context"),
        (_decision(evidence=("o-a", "o-missing")), "missing"),
        (_decision(target="t9"), "t9"),
        (_decision(controller=ControllerIdentity("other-controller", "1")), "controller"),
    ],
    ids=["context-id", "context-version", "missing-evidence", "unknown-target", "controller"],
)
def test_decision_response_rejects_reference_and_controller_mismatches(decision, match):
    with pytest.raises(ValueError, match=match):
        validate_decision_response(_decision_request(), DecisionResponse(decision))


def test_decision_response_requires_a_decision():
    with pytest.raises(ValueError):
        DecisionResponse(_decision().outcome)


# --- ports ---------------------------------------------------------------------------


def test_analyzer_port_returns_success_or_typed_failure():
    request = _analysis_request()
    ok = _call_analyzer(_InMemoryAnalyzer(Success(AnalysisResponse(_complete_observations()))), request)
    assert isinstance(ok, Success)
    validate_analysis_response(request, ok.value)
    failure = PortFailure(PortFailureCode.UNSUPPORTED, "metric not supported", False)
    assert _call_analyzer(_InMemoryAnalyzer(failure), request) is failure


def test_controller_port_returns_success_or_typed_failure():
    request = _decision_request()
    ok = _call_controller(_InMemoryController(Success(DecisionResponse(_decision()))), request)
    assert isinstance(ok, Success)
    validate_decision_response(request, ok.value)
    failure = PortFailure(PortFailureCode.INVALID_RESPONSE, "unparseable output", False)
    assert _call_controller(_InMemoryController(failure), request) is failure


def test_retryable_failure_is_advisory_and_triggers_no_retry():
    failure = PortFailure(PortFailureCode.UNAVAILABLE, "temporarily unavailable", True)
    analyzer, controller = _InMemoryAnalyzer(failure), _InMemoryController(failure)
    assert _call_analyzer(analyzer, _analysis_request()).retryable is True
    assert _call_controller(controller, _decision_request()).retryable is True
    assert (analyzer.calls, controller.calls) == (1, 1)


# --- dependency boundaries -------------------------------------------------------------
