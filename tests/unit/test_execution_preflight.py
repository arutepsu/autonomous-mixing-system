"""Focused tests for execution request records and deterministic metadata preflight.

Metadata only: no file is created, read, hashed or checked, and no processor is invoked. Gain values and
permission bounds are explicit structural fixtures, not recommendations.
"""

import dataclasses
from datetime import datetime, timezone

import pytest

from autonomous_mixing.core.records.actions import Action, SetTrackGain
from autonomous_mixing.core.records.admission import (
    ActionAdmissionPolicy,
    ExecutionRequest,
    StemBinding,
    TrackGainPermission,
)
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.budgets import BudgetLimits, BudgetUsage
from autonomous_mixing.core.records.capabilities import ActionCapabilityView
from autonomous_mixing.core.records.context import AssertionSource, MusicalContext, TrackRole
from autonomous_mixing.core.records.decision import (
    ActionJustification,
    ControllerIdentity,
    Decision,
    ProposeAction,
    Situation,
)
from autonomous_mixing.core.records.diagnosis import (
    DiagnosisCoverage,
    DiagnosisCoverageStatus,
    DiagnosisReport,
    DiagnosticEvidence,
    ProblemCandidate,
    ProblemFamily,
    UncertaintyRepresentation,
)
from autonomous_mixing.core.records.feedback import DecisionHistory
from autonomous_mixing.core.records.identifiers import (
    ActionId,
    ArtifactId,
    CheckpointId,
    ConditionId,
    ContextId,
    CriteriaId,
    DecisionId,
    DiagnosisCoverageId,
    ExecutionId,
    ObservationId,
    ProblemCandidateId,
    ProjectId,
    Revision,
    RunId,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.mix import (
    GainRule,
    LevelPolicy,
    MixRecipe,
    NumericalPrecision,
    OutputContainer,
    ProcessingSpec,
    SampleEncoding,
    SummationOrder,
    SummingPolicy,
)
from autonomous_mixing.core.records.observation import AnalysisScope, MeasuredScalar, Observation, RenderSpec, TimeRange
from autonomous_mixing.core.records.project import (
    ArtifactCheckpoint,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    Track,
    TrackGainState,
)
from autonomous_mixing.core.records.rendering import CandidateIdentity, ExecutionWorkspace
from autonomous_mixing.core.records.verification import (
    AcceptanceCriteria,
    ComparisonOperator,
    ImprovementCondition,
    ImprovementDirection,
    MetricSelector,
    TargetCondition,
)

UTC_NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
CONTROLLER = ControllerIdentity("deterministic-baseline", "0.1")
FRAMES = 48000 * 60
_D = object()


def _artifact(aid, tracks, revision, *, kind=ArtifactKind.STEM, rate=48000, channels=2, frames=FRAMES, start=0.0):
    return AudioArtifact(
        ArtifactId(aid),
        ProjectId("p1"),
        Revision(revision),
        tuple(TrackId(t) for t in tracks),
        kind,
        f"store/{aid}.wav",
        start,
        rate,
        channels,
        frames,
        "a" * 64,
        VersionRef("render", "1"),
    )


# State r2: t1/t3 stems produced at r1, the current t2 stem rendered at r2 from the r0 baseline a-t2,
# which is no longer an audio member (mixed production revisions, fixed-input semantics).
A_T1 = _artifact("a-t1", ("t1",), "r1")
A_T2_BASELINE = _artifact("a-t2", ("t2",), "r0")
C_T2 = _artifact("c-t2", ("t2",), "r2")
A_T3 = _artifact("a-t3", ("t3",), "r1")
C_MIX = _artifact("c-mix", ("t1", "t2", "t3"), "r2", kind=ArtifactKind.MIX)
TRACKS = (("t1", "Field recording"), ("t2", "Choir"), ("t3", "Tape loop"))


def _state(audio=_D, gains=_D) -> ProjectState:
    snapshot = ProjectSnapshot(
        ProjectId("p1"), Revision("r2"), UTC_NOW, tuple(Track(TrackId(t), name) for t, name in TRACKS)
    )
    if gains is _D:
        gains = (
            TrackGainState(TrackId("t1"), A_T1, 0.0),
            TrackGainState(TrackId("t2"), A_T2_BASELINE, -2.0),
            TrackGainState(TrackId("t3"), A_T3, 0.0),
        )
    members = (A_T1, C_T2, A_T3, C_MIX) if audio is _D else audio
    return ProjectState(
        snapshot,
        ProjectProcessingState(ProjectId("p1"), Revision("r2"), gains),
        ArtifactCheckpoint(CheckpointId("cp2"), ProjectId("p1"), Revision("r2"), members),
    )


# Increment 2E: proposals cite the fixture candidate and carry an explicit, labelled justification.
JUSTIFICATION = ActionJustification(
    "labelled test-double reasoning", "uncalibrated fixture text, not a confidence", (), ("contract fixture only",)
)
CANDIDATES = (ProblemCandidateId("cand-1"),)


def _situation_inputs(state, context, evidence=()) -> dict:
    """Explicit decision-input records added by increment 2D (Situation fields after ``limits``).

    Without ``evidence`` the diagnosis is one labelled UNAVAILABLE coverage row (no diagnosis method
    exists). With ``evidence`` (increment 2E) it is one labelled ASSESSED test-double row and a candidate
    ``cand-1`` citing those actual fixture observations, so proposals can reference a real candidate. The
    capability view advertises no action and the history is the empty first-call prefix (usage charges
    one call). None of them states that the mix has a problem or none, that an action is permitted or that
    a call succeeded.
    """
    scope = AnalysisScope(tuple(sorted(state.snapshot.track_ids, key=str)), TimeRange(0.0, 60.0))
    selector = MetricSelector(VersionRef("metric-a", "1"), scope)
    criteria = AcceptanceCriteria(
        CriteriaId("crit"),
        "1",
        (TargetCondition(ConditionId("target"), selector, ComparisonOperator.LE, MeasuredScalar(-10.0, "unit-a")),),
        (
            ImprovementCondition(
                ConditionId("improve"), selector, ImprovementDirection.LOWER, MeasuredScalar(1.0, "unit-a")
            ),
        ),
        (),
    )
    candidates = ()
    if evidence:
        method = VersionRef("test-double-diagnosis", "0")  # labelled test double; no detection method exists
        coverage = DiagnosisCoverage(
            DiagnosisCoverageId("cov-1"),
            ProblemFamily.MASKING,
            scope,
            DiagnosisCoverageStatus.ASSESSED,
            method,
            "labelled test-double coverage; this fixture asserts nothing about problems",
        )
        statement = DiagnosticEvidence(
            "test-double strength", "uncalibrated", UncertaintyRepresentation.UNCALIBRATED, False, (), ()
        )
        candidates = (
            ProblemCandidate(
                ProblemCandidateId("cand-1"),
                DiagnosisCoverageId("cov-1"),
                state.project_id,
                state.revision,
                ProblemFamily.MASKING,
                scope,
                tuple(ObservationId(e) for e in evidence),
                method,
                statement,
            ),
        )
    else:
        coverage = DiagnosisCoverage(
            DiagnosisCoverageId("cov-1"),
            ProblemFamily.MASKING,
            scope,
            DiagnosisCoverageStatus.UNAVAILABLE,
            None,
            "no diagnosis method exists; this fixture asserts nothing about problems",
        )
    return dict(
        run_id=RunId("run-1"),
        scope=scope,
        criteria=criteria,
        diagnosis=DiagnosisReport(state.project_id, state.revision, (coverage,), candidates),
        capabilities=ActionCapabilityView(
            state.project_id,
            state.revision,
            context.context_id,
            context.version,
            VersionRef("policy", "1"),
            VersionRef("renderer", "1"),
            scope,
            (),
        ),
        history=DecisionHistory(RunId("run-1"), state.project_id, ()),
    )


def _decision_request(state=None, protected=frozenset(), observation_source="c-mix") -> Situation:
    source = AssertionSource("producer", UTC_NOW)
    roles = tuple(TrackRole(TrackId(t), r, source) for t, r in [("t1", "ambience"), ("t2", "lead"), ("t3", "texture")])
    context = MusicalContext(ContextId("c1"), "1", roles, (), frozenset(TrackId(t) for t in protected))
    observation = Observation(
        ObservationId("o1"),
        VersionRef("metric-a", "1"),
        (ArtifactId(observation_source),),
        AnalysisScope((TrackId("t1"), TrackId("t2")), TimeRange(0.0, 60.0)),
        MeasuredScalar(-14.0, "unit-a"),
        VersionRef("spec", "1"),
        VersionRef("analyzer", "0.1"),
        UTC_NOW,
    )
    state = state if state is not None else _state()
    return Situation(
        state,
        context,
        CONTROLLER,
        (observation,),
        BudgetUsage(1, 0, 1, 0, 2.0),
        BudgetLimits(4, 2, 6, 1, 300.0),
        **_situation_inputs(state, context, evidence=("o1",)),
    )


def _decision(value_db=-4.0, target="t2", revision="r2", outcome=_D) -> Decision:
    if outcome is _D:
        action = Action(ActionId("x1"), ProjectId("p1"), Revision(revision), SetTrackGain(TrackId(target), value_db))
        outcome = ProposeAction(action, "set absolute track gain", CANDIDATES, JUSTIFICATION)
    evidence = (ObservationId("o1"),) if isinstance(outcome, ProposeAction) else ()
    return Decision(
        DecisionId("d1"),
        ProjectId("p1"),
        Revision(revision),
        ContextId("c1"),
        "1",
        evidence,
        "structured rationale",
        CONTROLLER,
        outcome,
    )


def _policy(*permissions) -> ActionAdmissionPolicy:
    if not permissions:
        permissions = (TrackGainPermission(TrackId("t2"), -12.0, 0.0),)
    return ActionAdmissionPolicy(VersionRef("admission-policy", "1"), ProjectId("p1"), tuple(permissions))


def _recipe(*tracks) -> MixRecipe:
    return MixRecipe(
        VersionRef("full-mix-recipe", "1"),
        tuple(TrackId(t) for t in tracks or ("t1", "t2", "t3")),
        SummingPolicy.SAMPLE_SUM,
    )


def _bindings(*pairs) -> tuple[StemBinding, ...]:
    pairs = pairs or (("t1", "a-t1"), ("t2", "c-t2"), ("t3", "a-t3"))
    return tuple(StemBinding(TrackId(t), ArtifactId(a)) for t, a in pairs)


def _request(**overrides) -> ExecutionRequest:
    fields = dict(
        execution_id=ExecutionId("e1"),
        decision_request=_decision_request(),
        decision=_decision(),
        policy=_policy(),
        mix_recipe=_recipe(),
        stem_bindings=_bindings(),
        current_mix_artifact_id=ArtifactId("c-mix"),
        render_spec=RenderSpec(VersionRef("render", "1"), 48000, 2),
        processing_spec=SPEC,
        workspace=ExecutionWorkspace(ExecutionId("e1"), "offline-workspace", "e1_run-01"),
        candidate=CandidateIdentity(Revision("r3"), ArtifactId("c3-t2"), ArtifactId("c3-mix")),
    )
    fields.update(overrides)
    return ExecutionRequest(**fields)


def _processing_spec(version: str = "1") -> ProcessingSpec:
    """Explicit test configuration; no global default specification exists."""
    return ProcessingSpec(
        VersionRef("gain-mix-processing", version),
        NumericalPrecision.FLOAT64,
        GainRule.DB_AMPLITUDE,
        SummationOrder.RECIPE_SEQUENTIAL,
        LevelPolicy.PRESERVE,
        OutputContainer.WAV,
        SampleEncoding.FLOAT64,
    )


SPEC = _processing_spec()


# --- request records -----------------------------------------------------------------------


def test_valid_request_records():
    request = _request()
    assert request.workspace.directory_token == "e1_run-01"
    assert request.candidate.revision == Revision("r3")
    assert [str(b.track_id) for b in request.stem_bindings] == ["t1", "t2", "t3"]


@pytest.mark.parametrize("token", ["", "../e1", "e1/x", "e1 run", "e1.run", "é1", "e1\\x"])
def test_workspace_directory_token_is_a_restricted_logical_name(token):
    with pytest.raises(ValueError):
        ExecutionWorkspace(ExecutionId("e1"), "offline-workspace", token)


@pytest.mark.parametrize(
    "args",
    [("e1", "root", "tok"), (ExecutionId("e1"), " ", "tok"), (ExecutionId("e1"), None, "tok")],
    ids=["untyped-execution-id", "blank-root-key", "untyped-root-key"],
)
def test_workspace_requires_typed_identity_and_root_key(args):
    with pytest.raises(ValueError):
        ExecutionWorkspace(*args)


def test_candidate_identity_requires_distinct_typed_artifact_ids():
    with pytest.raises(ValueError):
        CandidateIdentity(Revision("r3"), ArtifactId("same"), ArtifactId("same"))
    with pytest.raises(ValueError):
        CandidateIdentity("r3", ArtifactId("t"), ArtifactId("m"))


def test_stem_binding_requires_typed_ids():
    with pytest.raises(ValueError):
        StemBinding("t1", ArtifactId("a-t1"))
    with pytest.raises(ValueError):
        StemBinding(TrackId("t1"), "a-t1")


@pytest.mark.parametrize(
    "overrides",
    [
        {"stem_bindings": list(_bindings())},
        {"stem_bindings": _bindings(("t1", "a-t1"), ("t1", "a-t3"))},
        {"stem_bindings": _bindings(("t1", "a-t1"), ("t2", "a-t1"))},
        {"stem_bindings": ()},
        {"stem_bindings": (("t1", "a-t1"),)},
        {"execution_id": "e1"},
        {"decision_request": None},
        {"decision": _decision().outcome},
        {"policy": (TrackGainPermission(TrackId("t2"), -12.0, 0.0),)},
        {"mix_recipe": (TrackId("t1"),)},
        {"current_mix_artifact_id": "c-mix"},
        {"render_spec": VersionRef("render", "1")},
        {"processing_spec": VersionRef("gain-mix-processing", "1")},
        {"workspace": "e1_run-01"},
        {"candidate": (Revision("r3"), ArtifactId("c3-t2"), ArtifactId("c3-mix"))},
    ],
    ids=[
        "mutable-bindings",
        "duplicate-binding-track",
        "duplicate-binding-artifact",
        "empty-bindings",
        "untyped-binding",
        "execution-id",
        "decision-request",
        "decision",
        "policy",
        "recipe",
        "mix-id",
        "render-spec",
        "processing-spec",
        "workspace",
        "candidate",
    ],
)
def test_request_rejects_wrong_types_mutable_and_duplicate_bindings(overrides):
    with pytest.raises(ValueError):
        _request(**overrides)


def test_request_construction_does_not_check_cross_record_consistency():
    # Mismatches are reported by preflight as typed results, not construction errors.
    request = _request(
        workspace=ExecutionWorkspace(ExecutionId("e9"), "offline-workspace", "e9"),
        candidate=CandidateIdentity(Revision("r2"), ArtifactId("a-t1"), ArtifactId("c-mix")),
        stem_bindings=_bindings(("t1", "a-t1")),
        current_mix_artifact_id=ArtifactId("missing"),
    )
    assert isinstance(request, ExecutionRequest)


# --- metadata preflight -----------------------------------------------------------------------

import copy  # noqa: E402
import inspect  # noqa: E402

from autonomous_mixing.core.admission.admission import admit_action  # noqa: E402
from autonomous_mixing.core.admission.preflight import preflight_execution  # noqa: E402
from autonomous_mixing.core.records.admission import (  # noqa: E402
    ActionAdmissionRequest,
    AdmissionRejectionCode,
    MetadataReady,
    PreflightCode,
    PreflightRejected,
    RejectedAction,
)
from autonomous_mixing.core.records.decision import Abstain, AbstentionReason  # noqa: E402
from autonomous_mixing.core.records.rendering import RenderPlan  # noqa: E402


def _with(artifact: AudioArtifact, **changes) -> AudioArtifact:
    return dataclasses.replace(artifact, **changes)


def _for_state(state: ProjectState, observation_source="c-mix", **overrides) -> ExecutionRequest:
    return _request(decision_request=_decision_request(state, observation_source=observation_source), **overrides)


def _rejected(result, code: PreflightCode) -> PreflightRejected:
    assert isinstance(result, PreflightRejected), result
    assert result.code is code, (result.code, result.explanation)
    assert result.execution_id == ExecutionId("e1")
    return result


def test_ready_plan_separates_fixed_target_input_from_rendered_current_stems():
    result = preflight_execution(_request())
    assert isinstance(result, MetadataReady)
    plan = result.plan
    assert plan.fixed_target_input is A_T2_BASELINE  # r0 baseline, not a current audio member
    assert plan.requested_gain_db == -4.0  # absolute request; the current -2 dB setting is not a base
    assert plan.target_current_artifact_id == ArtifactId("c-t2")
    assert plan.current_stems == (A_T1, C_T2, A_T3)  # already-rendered stems, recipe order
    assert [str(a.revision) for a in plan.current_stems] == ["r1", "r2", "r1"]  # provenance untouched
    assert (plan.project_id, plan.before_revision, plan.target_track_id) == (
        ProjectId("p1"),
        Revision("r2"),
        TrackId("t2"),
    )
    assert plan.current_mix_artifact_id == ArtifactId("c-mix")
    assert plan.candidate == _request().candidate and plan.workspace == _request().workspace
    assert plan.render_spec == _request().render_spec and plan.recipe == _recipe()


def test_bindings_in_any_order_resolve_into_recipe_order():
    request = _request(
        mix_recipe=_recipe("t3", "t1", "t2"),
        stem_bindings=_bindings(("t2", "c-t2"), ("t3", "a-t3"), ("t1", "a-t1")),
    )
    plan = preflight_execution(request).plan
    assert [str(a.artifact_id) for a in plan.current_stems] == ["a-t3", "a-t1", "c-t2"]


# 1. admission (rerun now; earlier admission is not a capability token)


@pytest.mark.parametrize(
    "overrides, nested",
    [
        (
            {"policy": _policy(TrackGainPermission(TrackId("t1"), -12.0, 0.0))},
            AdmissionRejectionCode.NO_GAIN_PERMISSION,
        ),
        ({"decision": _decision(3.0)}, AdmissionRejectionCode.OUT_OF_BOUNDS),
        ({"decision": _decision(revision="r1")}, AdmissionRejectionCode.INVALID_REFERENCES),
        ({"decision_request": _decision_request(protected={"t2"})}, AdmissionRejectionCode.PROTECTED_TRACK),
        (
            {"decision": _decision(outcome=Abstain(AbstentionReason.NO_JUSTIFIED_ACTION))},
            AdmissionRejectionCode.NOT_AN_ACTION,
        ),
    ],
    ids=["no-permission", "out-of-bounds", "stale-revision", "protected", "abstention"],
)
def test_admission_is_rerun_and_its_rejection_preserved(overrides, nested):
    request = _request(**overrides)
    result = _rejected(preflight_execution(request), PreflightCode.ADMISSION_REJECTED)
    expected = admit_action(ActionAdmissionRequest(request.decision_request, request.decision, request.policy))
    assert result.admission_rejection == expected
    assert result.admission_rejection.code is nested


# 2. workspace


def test_workspace_must_belong_to_this_execution():
    workspace = ExecutionWorkspace(ExecutionId("e9"), "offline-workspace", "e9")
    _rejected(preflight_execution(_request(workspace=workspace)), PreflightCode.INVALID_WORKSPACE)


# 3. candidate identity


@pytest.mark.parametrize(
    "candidate",
    [
        CandidateIdentity(Revision("r2"), ArtifactId("c3-t2"), ArtifactId("c3-mix")),
        CandidateIdentity(Revision("r3"), ArtifactId("a-t1"), ArtifactId("c3-mix")),
        CandidateIdentity(Revision("r3"), ArtifactId("c3-t2"), ArtifactId("c-mix")),
        CandidateIdentity(Revision("r3"), ArtifactId("a-t2"), ArtifactId("c3-mix")),
    ],
    ids=["current-revision", "reuses-audio-stem", "reuses-current-mix", "reuses-fixed-baseline"],
)
def test_candidate_identity_must_be_new(candidate):
    _rejected(preflight_execution(_request(candidate=candidate)), PreflightCode.INVALID_CANDIDATE_IDENTITY)


# 4. recipe and bindings


@pytest.mark.parametrize(
    "overrides",
    [
        {"stem_bindings": _bindings(("t1", "a-t1"), ("t2", "c-t2"))},
        {"stem_bindings": _bindings(("t1", "a-t1"), ("t2", "c-t2"), ("t3", "a-t3"), ("t4", "x-t4"))},
        {"stem_bindings": _bindings(("t1", "a-t1"), ("t2", "a-t2"), ("t3", "a-t3"))},
        {"stem_bindings": _bindings(("t1", "a-t1"), ("t2", "c-mix"), ("t3", "a-t3"))},
        {"stem_bindings": _bindings(("t1", "a-t3"), ("t2", "c-t2"), ("t3", "a-t1"))},
        {"stem_bindings": _bindings(("t1", "a-t1"), ("t2", "c-t2"), ("t3", "unknown"))},
        {"mix_recipe": _recipe("t1", "t2"), "stem_bindings": _bindings(("t1", "a-t1"), ("t2", "c-t2"))},
    ],
    ids=[
        "missing-contributor",
        "extra-binding",
        "baseline-not-a-member",
        "wrong-kind",
        "wrong-source-track",
        "unknown-artifact",
        "recipe-not-full-inventory",
    ],
)
def test_bindings_must_cover_the_full_recipe_with_matching_stem_members(overrides):
    _rejected(preflight_execution(_request(**overrides)), PreflightCode.INVALID_RECIPE_BINDINGS)


# 5. selected current mix

PARTIAL_MIX = _artifact("p-mix", ("t1", "t2"), "r2", kind=ArtifactKind.MIX)


@pytest.mark.parametrize(
    "build",
    [
        lambda: _request(current_mix_artifact_id=ArtifactId("missing")),
        lambda: _request(current_mix_artifact_id=ArtifactId("a-t1")),
        lambda: _for_state(
            _state(audio=(A_T1, C_T2, A_T3, PARTIAL_MIX)), "p-mix", current_mix_artifact_id=ArtifactId("p-mix")
        ),
    ],
    ids=["not-a-member", "not-a-mix", "partial-contributors"],
)
def test_selected_mix_must_be_a_full_recipe_mix_member(build):
    _rejected(preflight_execution(build()), PreflightCode.INVALID_MIX)


# 6. explicit target gain state


def test_target_without_gain_state_is_rejected_not_defaulted():
    state = _state(gains=(TrackGainState(TrackId("t1"), A_T1, 0.0), TrackGainState(TrackId("t3"), A_T3, 0.0)))
    _rejected(preflight_execution(_for_state(state)), PreflightCode.MISSING_GAIN_STATE)


# 7. compatible audio metadata

GROUP_T13 = _artifact("g-t13", ("t1", "t3"), "r1", kind=ArtifactKind.GROUP)


def _state_with(**replacements) -> ProjectState:
    members = {"a-t1": A_T1, "c-t2": C_T2, "a-t3": A_T3, "c-mix": C_MIX, "a-t2": A_T2_BASELINE}
    members.update(replacements)
    gains = (
        TrackGainState(TrackId("t1"), members["a-t1"], 0.0),
        TrackGainState(TrackId("t2"), members["a-t2"], -2.0),
        TrackGainState(TrackId("t3"), members["a-t3"], 0.0),
    )
    audio = tuple(members[k] for k in ("a-t1", "c-t2", "a-t3", "c-mix")) + tuple(
        v for k, v in members.items() if k not in {"a-t1", "c-t2", "a-t3", "c-mix", "a-t2"}
    )
    return _state(audio=audio, gains=gains)


@pytest.mark.parametrize(
    "build",
    [
        lambda: _for_state(_state_with(**{"a-t3": _with(A_T3, sample_rate_hz=44100)})),
        lambda: _for_state(_state_with(**{"a-t3": _with(A_T3, channel_count=1)})),
        lambda: _for_state(_state_with(**{"a-t3": _with(A_T3, frame_count=FRAMES - 1)})),
        lambda: _for_state(_state_with(**{"a-t3": _with(A_T3, timeline_start_seconds=0.5)})),
        lambda: _for_state(_state_with(**{"a-t2": _with(A_T2_BASELINE, frame_count=FRAMES + 48000)})),
        lambda: _for_state(_state_with(**{"c-mix": _with(C_MIX, frame_count=FRAMES + 1)})),
        lambda: _for_state(
            _state_with(
                **{
                    key: _with(art, frame_count=0)
                    for key, art in {
                        "a-t1": A_T1,
                        "c-t2": C_T2,
                        "a-t3": A_T3,
                        "c-mix": C_MIX,
                        "a-t2": A_T2_BASELINE,
                    }.items()
                },
                # Full-length evidence source so the decision stays admissible; audio is checked before dependencies.
                **{"g-t12": _artifact("g-t12", ("t1", "t2"), "r1", kind=ArtifactKind.GROUP)},
            ),
            "g-t12",
        ),
        lambda: _request(render_spec=RenderSpec(VersionRef("render", "1"), 44100, 2)),
        lambda: _request(render_spec=RenderSpec(VersionRef("render", "1"), 48000, 1)),
    ],
    ids=[
        "stem-rate",
        "stem-channels",
        "stem-duration",
        "stem-offset",
        "fixed-input-duration",
        "mix-duration",
        "zero-frames",
        "render-rate",
        "render-channels",
    ],
)
def test_audio_metadata_must_be_mutually_compatible(build):
    _rejected(preflight_execution(build()), PreflightCode.INCOMPATIBLE_AUDIO)


# 8. no undeclared target-dependent outputs

ALT_T2 = _artifact("alt-t2", ("t2",), "r2")
ALT_MIX = _artifact("alt-mix", ("t1", "t2", "t3"), "r2", kind=ArtifactKind.MIX)
GROUP_T23 = _artifact("g-t23", ("t2", "t3"), "r1", kind=ArtifactKind.GROUP)


@pytest.mark.parametrize(
    "extra", [GROUP_T23, ALT_MIX, ALT_T2], ids=["affected-group", "alternate-mix", "alternate-target-stem"]
)
def test_undeclared_target_dependent_artifacts_are_unsupported(extra):
    request = _for_state(_state(audio=(A_T1, C_T2, A_T3, C_MIX, extra)))
    _rejected(preflight_execution(request), PreflightCode.UNSUPPORTED_DEPENDENCY)


def test_untouched_non_target_artifacts_may_remain():
    request = _for_state(_state(audio=(A_T1, C_T2, A_T3, C_MIX, GROUP_T13)))
    assert isinstance(preflight_execution(request), MetadataReady)


# precedence: the first failing check wins

WORKSPACE_E9 = ExecutionWorkspace(ExecutionId("e9"), "offline-workspace", "e9")
REUSED_CANDIDATE = CandidateIdentity(Revision("r2"), ArtifactId("c3-t2"), ArtifactId("c3-mix"))


@pytest.mark.parametrize(
    "build, code",
    [
        (lambda: _request(decision=_decision(3.0), workspace=WORKSPACE_E9), PreflightCode.ADMISSION_REJECTED),
        (lambda: _request(workspace=WORKSPACE_E9, candidate=REUSED_CANDIDATE), PreflightCode.INVALID_WORKSPACE),
        (
            lambda: _request(candidate=REUSED_CANDIDATE, stem_bindings=_bindings(("t1", "a-t1"))),
            PreflightCode.INVALID_CANDIDATE_IDENTITY,
        ),
        (
            lambda: _request(stem_bindings=_bindings(("t1", "a-t1")), current_mix_artifact_id=ArtifactId("missing")),
            PreflightCode.INVALID_RECIPE_BINDINGS,
        ),
        (
            lambda: _for_state(
                _state(audio=(A_T1, C_T2, A_T3, C_MIX), gains=(TrackGainState(TrackId("t1"), A_T1, 0.0),)),
                current_mix_artifact_id=ArtifactId("missing"),
            ),
            PreflightCode.INVALID_MIX,
        ),
        (
            lambda: _for_state(
                _state(gains=(TrackGainState(TrackId("t1"), A_T1, 0.0),)),
                render_spec=RenderSpec(VersionRef("render", "1"), 44100, 2),
            ),
            PreflightCode.MISSING_GAIN_STATE,
        ),
        (
            lambda: _for_state(
                _state(audio=(A_T1, C_T2, A_T3, C_MIX, GROUP_T23)),
                render_spec=RenderSpec(VersionRef("render", "1"), 44100, 2),
            ),
            PreflightCode.INCOMPATIBLE_AUDIO,
        ),
    ],
    ids=[
        "admission-before-workspace",
        "workspace-before-candidate",
        "candidate-before-bindings",
        "bindings-before-mix",
        "mix-before-gain-state",
        "gain-state-before-audio",
        "audio-before-dependency",
    ],
)
def test_rejection_precedence(build, code):
    _rejected(preflight_execution(build()), code)


# purity and record shape


def test_preflight_is_pure_and_invokes_no_processor():
    request = _request()
    before = copy.deepcopy(request)
    preflight_execution(request)
    preflight_execution(_request(decision=_decision(3.0)))
    assert request == before
    assert list(inspect.signature(preflight_execution).parameters) == ["request"]
    # Inspect the implementation module itself (moved to core.admission.preflight in ADR-027 step M2).
    import autonomous_mixing.core.admission.preflight as module

    source = inspect.getsource(module)
    assert "ports" not in source and "process(" not in source


def test_preflight_rejection_record_requires_nested_admission_rejection_exactly_for_admission():
    nested = RejectedAction(DecisionId("d1"), AdmissionRejectionCode.OUT_OF_BOUNDS, "outside range")
    PreflightRejected(ExecutionId("e1"), PreflightCode.ADMISSION_REJECTED, "admission rejected", nested)
    PreflightRejected(ExecutionId("e1"), PreflightCode.INVALID_MIX, "no mix", None)
    for args in [
        (ExecutionId("e1"), PreflightCode.ADMISSION_REJECTED, "admission rejected", None),
        (ExecutionId("e1"), PreflightCode.INVALID_MIX, "no mix", nested),
        (ExecutionId("e1"), PreflightCode.INVALID_MIX, " ", None),
        (ExecutionId("e1"), "INVALID_MIX", "no mix", None),
        ("e1", PreflightCode.INVALID_MIX, "no mix", None),
    ]:
        with pytest.raises(ValueError):
            PreflightRejected(*args)


def test_preflight_codes_are_exactly_the_planned_set():
    assert [code.name for code in PreflightCode] == [
        "ADMISSION_REJECTED",
        "INVALID_WORKSPACE",
        "INVALID_CANDIDATE_IDENTITY",
        "INVALID_RECIPE_BINDINGS",
        "INVALID_MIX",
        "MISSING_GAIN_STATE",
        "INCOMPATIBLE_AUDIO",
        "UNSUPPORTED_DEPENDENCY",
    ]


def test_processing_plan_rejects_inconsistent_structure():
    plan = preflight_execution(_request()).plan
    for changes in [
        {"current_stems": (A_T1, A_T3, C_T2)},  # not recipe order
        {"current_stems": [A_T1, C_T2, A_T3]},
        {"target_current_artifact_id": ArtifactId("a-t1")},
        {"fixed_target_input": A_T1},
        {"requested_gain_db": float("nan")},
        {"candidate": REUSED_CANDIDATE},
        {"workspace": WORKSPACE_E9},
    ]:
        with pytest.raises(ValueError):
            dataclasses.replace(plan, **changes)
    assert isinstance(plan, RenderPlan)


# --- audit F5: candidate revisions must not reuse locally known revisions ------------------


@pytest.mark.parametrize(
    "revision",
    ["r2", "r1", "r0"],
    ids=["current-state-revision", "retained-artifact-production-revision", "superseded-baseline-production-revision"],
)
def test_candidate_revision_must_not_reuse_a_locally_known_revision(revision):
    candidate = CandidateIdentity(Revision(revision), ArtifactId("c3-t2"), ArtifactId("c3-mix"))
    _rejected(preflight_execution(_request(candidate=candidate)), PreflightCode.INVALID_CANDIDATE_IDENTITY)


def test_genuinely_new_candidate_revision_passes_preflight():
    candidate = CandidateIdentity(Revision("r3"), ArtifactId("c3-t2"), ArtifactId("c3-mix"))
    assert isinstance(preflight_execution(_request(candidate=candidate)), MetadataReady)


# --- processing specification propagation ------------------------------------------------------


def test_preflight_carries_the_exact_requested_processing_spec():
    request = _request(processing_spec=_processing_spec("7"))
    plan = preflight_execution(request).plan
    assert plan.processing_spec is request.processing_spec
    assert plan.processing_spec == _processing_spec("7")


def test_execution_request_requires_a_processing_spec():
    fields = {f.name: getattr(_request(), f.name) for f in dataclasses.fields(ExecutionRequest)}
    del fields["processing_spec"]
    with pytest.raises(TypeError):
        ExecutionRequest(**fields)


def test_processing_spec_does_not_change_preflight_outcomes():
    # The specification is carried, not checked: existing rejections and their precedence are unchanged.
    request = _request(processing_spec=_processing_spec("2"), decision=_decision(3.0))
    assert preflight_execution(request).code is PreflightCode.ADMISSION_REJECTED
