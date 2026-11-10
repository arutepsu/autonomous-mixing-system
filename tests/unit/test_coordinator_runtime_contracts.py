"""Contract tests for coordinator runtime records and ports (plan docs/superpowers/plans/2026-11-09-coordinator-runtime-contracts-plan.md).

All records are labelled metadata fixtures on generic project and track identities: no files, clocks or
identity generators are used, and metric names, limits and values are structural examples, not
recommended settings or evaluation data. Ports are exercised with scripted test doubles that establish
response contracts only.
"""

import dataclasses
import typing
from datetime import datetime, timedelta, timezone

import pytest

from autonomous_mixing.core.ports.coordinator_runtime import (
    DiagnosisSource,
    IdentitySource,
    RunClock,
    RunRenderer,
    validate_diagnosis_response,
    validate_run_render_response,
)
from autonomous_mixing.core.records.actions import ExecutionIssue, ExecutionStatus
from autonomous_mixing.core.records.admission import (
    ActionAdmissionPolicy,
    StemBinding,
    TrackGainPermission,
)
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.budgets import BudgetLimits
from autonomous_mixing.core.records.capabilities import GainBounds, RendererCapabilities
from autonomous_mixing.core.records.context import (
    AssertionSource,
    MusicalContext,
    TrackRole,
)
from autonomous_mixing.core.records.coordinator_runtime import (
    ClockReading,
    DecisionRetryPolicy,
    DiagnosisRequest,
    RenderPreparationFailure,
    RunRenderOutcome,
    RunRenderRequest,
    RunRequest,
)
from autonomous_mixing.core.records.decision import ControllerIdentity
from autonomous_mixing.core.records.diagnosis import (
    DiagnosisCoverage,
    DiagnosisCoverageStatus,
    DiagnosisReport,
    ProblemFamily,
)
from autonomous_mixing.core.records.identifiers import (
    ArtifactId,
    CheckpointId,
    ConditionId,
    ContextId,
    CriteriaId,
    DiagnosisCoverageId,
    ExecutionId,
    ObservationId,
    ProjectId,
    Revision,
    RunId,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.integrity import (
    ArtifactRootBinding,
    StateIntegrityRequest,
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
from autonomous_mixing.core.records.observation import (
    AnalysisScope,
    AnalysisSpec,
    MeasuredScalar,
    Observation,
    RenderSpec,
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
from autonomous_mixing.core.records.rendering import (
    CandidateIdentity,
    ExecutionWorkspace,
    RenderedCandidate,
    RenderEvidence,
    RenderFailure,
    RenderPlan,
)
from autonomous_mixing.core.records.results import (
    PortFailure,
    PortFailureCode,
    PortResult,
    Success,
)
from autonomous_mixing.core.records.run import RunConfiguration
from autonomous_mixing.core.records.verification import (
    AcceptanceCriteria,
    BaselineReference,
    ComparisonOperator,
    DeltaGuard,
    ImprovementCondition,
    ImprovementDirection,
    MetricSelector,
    TargetCondition,
)
from autonomous_mixing.core.records.verification_assessment import ConditionMeasurement
from autonomous_mixing.core.records.verification_evidence import (
    MeasurementDefinition,
    SourceRequirement,
)

UTC_NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
RATE, SECONDS = 48000, 60
RENDER_REF = VersionRef("render", "1")
METRIC = VersionRef("metric-a", "1")
SPEC, ANALYZER, ALIGNMENT = (
    VersionRef("analysis-spec", "1"),
    VersionRef("analyzer", "0.1"),
    VersionRef("alignment", "1"),
)
UNIT = "unit-a"
T1, T2 = TrackId("t1"), TrackId("t2")
P1 = ProjectId("p1")


# --- state fixtures ----------------------------------------------------------------------------------


def _artifact(aid, revision, tracks, kind=ArtifactKind.STEM, project="p1") -> AudioArtifact:
    return AudioArtifact(
        ArtifactId(aid), ProjectId(project), Revision(revision), tuple(TrackId(t) for t in tracks), kind,
        f"outputs/{aid}.wav", 0.0, RATE, 2, RATE * SECONDS, "d" * 64, RENDER_REF,
    )  # fmt: skip


S1, S2 = _artifact("s1", "r1", ("t1",)), _artifact("s2", "r1", ("t2",))
MIX = _artifact("mix", "r1", ("t1", "t2"), ArtifactKind.MIX)
IN_T1, IN_T2 = (
    _artifact("in-t1", "r0", ("t1",)),
    _artifact("in-t2", "r0", ("t2",)),
)  # superseded fixed inputs


def _state(members=(S1, S2, MIX), revision="r1", tracks=(T1, T2)) -> ProjectState:
    snapshot = ProjectSnapshot(P1, Revision(revision), UTC_NOW, tuple(Track(t, f"track {t}") for t in tracks))
    gains = tuple(TrackGainState(t, a, 0.0) for t, a in ((T1, IN_T1), (T2, IN_T2)) if t in tracks)
    return ProjectState(
        snapshot, ProjectProcessingState(P1, Revision(revision), gains),
        ArtifactCheckpoint(CheckpointId(f"cp-{revision}"), P1, Revision(revision), members),
    )  # fmt: skip


STATE = _state()
REQUIRED = (S1, S2, MIX, IN_T1, IN_T2)
SCOPE = AnalysisScope((T1, T2), TimeRange(0.0, 60.0))


def _roots(artifacts=REQUIRED, key="exports") -> tuple[ArtifactRootBinding, ...]:
    return tuple(ArtifactRootBinding(a.artifact_id, key) for a in artifacts)


# --- run configuration fixtures ----------------------------------------------------------------------

SELECTOR = MetricSelector(METRIC, SCOPE)
CRITERIA = AcceptanceCriteria(
    CriteriaId("criteria-a"),
    "1",
    (
        TargetCondition(
            ConditionId("tg"),
            SELECTOR,
            ComparisonOperator.LE,
            MeasuredScalar(0.0, UNIT),
        ),
    ),
    (
        ImprovementCondition(
            ConditionId("im"),
            SELECTOR,
            ImprovementDirection.LOWER,
            MeasuredScalar(1.0, UNIT),
        ),
    ),
    (
        DeltaGuard(
            ConditionId("dl"),
            SELECTOR,
            BaselineReference.INITIAL,
            MeasuredScalar(2.0, UNIT),
        ),
    ),  # deferred
)
RENDER_SPEC = RenderSpec(RENDER_REF, RATE, 2)


def _configuration(**overrides) -> RunConfiguration:
    source = AssertionSource("labelled-fixture", UTC_NOW)
    fields = dict(
        scope=SCOPE,
        analysis_spec=AnalysisSpec(SPEC, (METRIC,)),
        render_spec=RENDER_SPEC,
        context=MusicalContext(
            ContextId("c1"),
            "1",
            (TrackRole(T1, "role-a", source), TrackRole(T2, "role-b", source)),
            (),
            frozenset(),
        ),
        criteria=CRITERIA,
        controller=ControllerIdentity("labelled-responses", "0.1"),
        budget_limits=BudgetLimits(
            max_decision_calls=3,
            max_attempts=2,
            max_render_calls=2,
            max_recovery_operations=1,
            max_elapsed_seconds=60.0,
        ),  # fmt: skip
    )
    fields.update(overrides)
    return RunConfiguration(**fields)


def _measurement(condition_id, selector=SELECTOR, unit=UNIT) -> ConditionMeasurement:
    source = SourceRequirement(ArtifactKind.MIX, (T1, T2), RENDER_REF, RATE, 2, 0.0, RATE * SECONDS)
    return ConditionMeasurement(
        ConditionId(condition_id),
        MeasurementDefinition(selector, unit, SPEC, ANALYZER, ALIGNMENT, (source,)),
    )


MEASUREMENTS = (_measurement("tg"), _measurement("im"), _measurement("dl"))
RECIPE = MixRecipe(VersionRef("recipe", "1"), (T1, T2), SummingPolicy.SAMPLE_SUM)
PROCESSING = ProcessingSpec(
    VersionRef("processing", "1"), NumericalPrecision.FLOAT64, GainRule.DB_AMPLITUDE, SummationOrder.RECIPE_SEQUENTIAL,
    LevelPolicy.PRESERVE, OutputContainer.WAV, SampleEncoding.FLOAT64,
)  # fmt: skip
POLICY = ActionAdmissionPolicy(VersionRef("policy", "1"), P1, (TrackGainPermission(T1, -6.0, 0.0),))
CAPABILITIES = RendererCapabilities(VersionRef("renderer", "1"), GainBounds(-12.0, 12.0))
STEM_BINDINGS = (
    StemBinding(T2, ArtifactId("s2")),
    StemBinding(T1, ArtifactId("s1")),
)  # any order
RETRY = DecisionRetryPolicy(VersionRef("retry", "1"), 1)


def _run_request(**overrides) -> RunRequest:
    fields = dict(
        run_id=RunId("run-1"),
        initial_state=STATE,
        configuration=_configuration(),
        policy=POLICY,
        renderer_capabilities=CAPABILITIES,
        processing_spec=PROCESSING,
        recipe=RECIPE,
        stem_bindings=STEM_BINDINGS,
        mix_artifact_id=ArtifactId("mix"),
        measurements=MEASUREMENTS,
        alignment=ALIGNMENT,
        root_bindings=_roots(),
        output_root_key="renders",
        retry_policy=RETRY,
    )
    fields.update(overrides)
    return RunRequest(**fields)


RUN_REQUEST = _run_request()


# --- simple records ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "record, fields",
    [
        (DecisionRetryPolicy, ["reference", "max_retries"]),
        (ClockReading, ["monotonic_seconds", "utc", "cancelled"]),
        (DiagnosisRequest, ["state", "scope", "observations"]),
        (RunRenderRequest, ["plan", "before", "protected_artifacts", "protected_root_bindings"]),
        (RenderPreparationFailure, ["issue", "created_workspace"]),
        (RunRequest, ["run_id", "initial_state", "configuration", "policy", "renderer_capabilities", "processing_spec",
                      "recipe", "stem_bindings", "mix_artifact_id", "measurements", "alignment", "root_bindings",
                      "output_root_key", "retry_policy"]),
    ],
)  # fmt: skip
def test_record_fields_are_exact_required_and_frozen(record, fields):
    assert [f.name for f in dataclasses.fields(record)] == fields
    assert all(f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
               for f in dataclasses.fields(record))  # fmt: skip
    assert record.__dataclass_params__.frozen


def test_render_outcome_union_is_exact():
    assert typing.get_args(RunRenderOutcome) == (
        RenderedCandidate,
        RenderFailure,
        RenderPreparationFailure,
    )


@pytest.mark.parametrize("max_retries", [0, 1, 5])
def test_retry_policy_accepts_counts_from_zero(max_retries):
    assert DecisionRetryPolicy(VersionRef("retry", "1"), max_retries).max_retries == max_retries


@pytest.mark.parametrize(
    "args",
    [
        ("retry", 1),
        (VersionRef("retry", "1"), -1),
        (VersionRef("retry", "1"), True),
        (VersionRef("retry", "1"), 1.0),
    ],
)
def test_retry_policy_validation(args):
    with pytest.raises(ValueError):
        DecisionRetryPolicy(*args)


@pytest.mark.parametrize("monotonic", [0.0, 0, 12.5])
@pytest.mark.parametrize("cancelled", [False, True])
def test_clock_reading_accepts_finite_nonnegative_utc_readings(monotonic, cancelled):
    reading = ClockReading(monotonic, UTC_NOW, cancelled)
    assert (reading.monotonic_seconds, reading.utc, reading.cancelled) == (
        monotonic,
        UTC_NOW,
        cancelled,
    )


@pytest.mark.parametrize(
    "args",
    [
        (-0.5, UTC_NOW, False),
        (float("nan"), UTC_NOW, False),
        (float("inf"), UTC_NOW, False),
        (True, UTC_NOW, False),
        ("1", UTC_NOW, False),
        (1.0, datetime(2026, 11, 9, 12, 0), False),  # naive
        (
            1.0,
            datetime(2026, 11, 9, 13, 0, tzinfo=timezone(timedelta(hours=1))),
            False,
        ),  # not UTC
        (1.0, "2026-11-09", False),
        (1.0, UTC_NOW, 0),
        (1.0, UTC_NOW, "false"),
        (1.0, UTC_NOW, None),
    ],
)
def test_clock_reading_validation(args):
    with pytest.raises(ValueError):
        ClockReading(*args)


def test_preparation_failure_shapes():
    issue = ExecutionIssue("workspace", "allocation refused")
    workspace = ExecutionWorkspace(ExecutionId("e1"), "work", "e1")
    assert RenderPreparationFailure(issue, None).created_workspace is None
    assert RenderPreparationFailure(issue, workspace).created_workspace is workspace
    with pytest.raises(ValueError):
        RenderPreparationFailure("allocation refused", None)
    with pytest.raises(ValueError):
        RenderPreparationFailure(issue, "work/e1")


# --- run request -------------------------------------------------------------------------------------


def test_a_consistent_run_request_keeps_the_supplied_objects():
    request = RUN_REQUEST
    assert request.initial_state is STATE and request.stem_bindings is STEM_BINDINGS
    assert request.measurements is MEASUREMENTS and request.root_bindings[0].artifact_id == ArtifactId("s1")


@pytest.mark.parametrize(
    "field, value",
    [
        ("run_id", "run-1"),
        ("initial_state", STATE.snapshot),
        ("configuration", None),
        ("policy", CAPABILITIES),
        ("renderer_capabilities", POLICY),
        ("processing_spec", RECIPE),
        ("recipe", PROCESSING),
        ("stem_bindings", list(STEM_BINDINGS)),
        ("mix_artifact_id", "mix"),
        ("measurements", list(MEASUREMENTS)),
        ("alignment", "alignment"),
        ("root_bindings", list(_roots())),
        ("retry_policy", 1),
        ("output_root_key", None),
        ("output_root_key", 3),
    ],
)
def test_run_request_field_types(field, value):
    with pytest.raises(ValueError):
        _run_request(**{field: value})


def test_configuration_is_validated_against_the_initial_snapshot():
    outside = AnalysisScope((T1, TrackId("t9")), TimeRange(0.0, 60.0))
    with pytest.raises(ValueError):
        _run_request(configuration=_configuration(scope=outside))


def test_policy_must_name_the_initial_project():
    with pytest.raises(ValueError, match="project"):
        _run_request(policy=dataclasses.replace(POLICY, project_id=ProjectId("p2")))


@pytest.mark.parametrize(
    "bindings, message",
    [
        ((StemBinding(T1, ArtifactId("s1")),), "recipe"),  # missing t2
        (STEM_BINDINGS + (StemBinding(T1, ArtifactId("s1")),), "unique"),  # duplicate track
        ((StemBinding(T1, ArtifactId("s1")), StemBinding(T2, ArtifactId("s1"))), "unique"),  # artifact reused
        ((StemBinding(T1, ArtifactId("s2")), StemBinding(T2, ArtifactId("s1"))), "stem"),  # swapped tracks
        ((StemBinding(T1, ArtifactId("in-t1")), StemBinding(T2, ArtifactId("s2"))), "stem"),  # fixed input, not current
        ((StemBinding(T1, ArtifactId("mix")), StemBinding(T2, ArtifactId("s2"))), "stem"),  # not a STEM
        ((StemBinding(T1, ArtifactId("s1")), StemBinding(T2, ArtifactId("s2")), StemBinding(TrackId("t3"), ArtifactId("s3"))), "recipe"),
    ],
    ids=["missing", "duplicate-track", "reused-artifact", "swapped", "not-current", "not-stem", "extra"],
)  # fmt: skip
def test_stem_bindings_must_bind_each_recipe_track_to_its_current_stem(bindings, message):
    with pytest.raises(ValueError, match=message):
        _run_request(stem_bindings=bindings)


def test_recipe_must_cover_exactly_the_snapshot_tracks():
    partial = MixRecipe(VersionRef("recipe", "1"), (T1,), SummingPolicy.SAMPLE_SUM)
    with pytest.raises(ValueError, match="recipe"):
        _run_request(recipe=partial, stem_bindings=(StemBinding(T1, ArtifactId("s1")),))


def test_recipe_order_is_free_when_bindings_cover_it():
    reversed_recipe = MixRecipe(VersionRef("recipe", "1"), (T2, T1), SummingPolicy.SAMPLE_SUM)
    assert _run_request(recipe=reversed_recipe).recipe is reversed_recipe


@pytest.mark.parametrize(
    "mix_id, members",
    [
        ("missing-mix", (S1, S2, MIX)),
        ("s1", (S1, S2, MIX)),  # a STEM
        (
            "mix-partial",
            (S1, S2, _artifact("mix-partial", "r1", ("t1",), ArtifactKind.MIX)),
        ),  # wrong contributors
    ],
)
def test_selected_mix_must_be_a_current_mix_of_the_recipe(mix_id, members):
    state = _state(members)
    roots = _roots((*members, IN_T1, IN_T2))
    with pytest.raises(ValueError, match="mix"):
        _run_request(initial_state=state, root_bindings=roots, mix_artifact_id=ArtifactId(mix_id))


@pytest.mark.parametrize(
    "roots",
    [
        _roots(REQUIRED[:-1]),
        _roots(REQUIRED[:3]),
        _roots() + _roots((S1,), "other"),
        (),
    ],
    ids=["missing-fixed-input", "missing-all-inputs", "duplicate", "empty"],
)
def test_root_bindings_must_cover_current_audio_and_fixed_inputs(roots):
    with pytest.raises(ValueError):
        _run_request(root_bindings=roots)


@pytest.mark.parametrize(
    "measurements",
    [
        MEASUREMENTS[:2],  # deferred INITIAL delta guard unbound
        MEASUREMENTS + (MEASUREMENTS[0],),
        (_measurement("tg"), _measurement("im"), _measurement("dl", unit="unit-b")),
        (
            _measurement("tg"),
            _measurement("im"),
            _measurement("dl", MetricSelector(VersionRef("metric-a", "2"), SCOPE)),
        ),
        MEASUREMENTS + (_measurement("extra"),),
        (),
    ],
    ids=["missing-deferred", "duplicate", "unit", "selector", "extra", "empty"],
)
def test_measurement_bindings_reuse_the_assessment_rules(measurements):
    with pytest.raises(ValueError):
        _run_request(measurements=measurements)


def test_retry_policy_of_zero_is_accepted():
    assert _run_request(retry_policy=DecisionRetryPolicy(VersionRef("retry", "1"), 0)).retry_policy.max_retries == 0


# --- diagnosis request and response ------------------------------------------------------------------


def _observation(oid="o1", sources=("mix",), state_scope=SCOPE) -> Observation:
    return Observation(
        ObservationId(oid), METRIC, tuple(ArtifactId(a) for a in sources), state_scope, MeasuredScalar(-3.0, UNIT),
        SPEC, ANALYZER, UTC_NOW,
    )  # fmt: skip


RETAINED = _artifact("s1-old", "r0", ("t1",))  # an older-production stem retained in a later state


def test_diagnosis_request_keeps_its_objects_and_allows_older_production_sources():
    state = _state((RETAINED, S2, MIX), revision="r2")
    observations = (
        _observation("o1", ("s1-old",), AnalysisScope((T1,), TimeRange(0.0, 60.0))),
        _observation("o2"),
    )
    request = DiagnosisRequest(state, SCOPE, observations)
    assert request.state is state and request.observations is observations


@pytest.mark.parametrize(
    "args",
    [
        (STATE.snapshot, SCOPE, ()),
        (STATE, (T1, T2), ()),
        (STATE, SCOPE, [_observation()]),
        (STATE, SCOPE, (_observation(), "o2")),
    ],
)
def test_diagnosis_request_types(args):
    with pytest.raises(ValueError):
        DiagnosisRequest(*args)


def test_diagnosis_request_rejects_unknown_scope_tracks():
    with pytest.raises(ValueError):
        DiagnosisRequest(STATE, AnalysisScope((T1, TrackId("t9")), TimeRange(0.0, 60.0)), ())


def test_diagnosis_request_rejects_duplicate_observation_ids():
    with pytest.raises(ValueError, match="o1"):
        DiagnosisRequest(STATE, SCOPE, (_observation(), _observation()))


@pytest.mark.parametrize(
    "sources",
    [("s1-superseded",), ("in-t1",)],
    ids=["unknown", "fixed-input-not-member"],
)
def test_diagnosis_request_requires_sources_in_the_state_audio(sources):
    with pytest.raises(ValueError):
        DiagnosisRequest(
            STATE,
            SCOPE,
            (
                _observation(
                    sources=sources,
                    state_scope=AnalysisScope((T1,), TimeRange(0.0, 60.0)),
                ),
            ),
        )


def test_diagnosis_request_permits_broader_current_observations():
    broad = _observation(state_scope=AnalysisScope((T1, T2), TimeRange(0.0, 60.0)))
    narrow = AnalysisScope((T1,), TimeRange(0.0, 30.0))
    assert DiagnosisRequest(STATE, narrow, (broad,)).scope is narrow


def _report(state=STATE, scope=SCOPE) -> DiagnosisReport:
    # Labelled metadata: an UNAVAILABLE coverage row names no method, so no detection is implied.
    coverage = DiagnosisCoverage(
        DiagnosisCoverageId("cov-1"), ProblemFamily.MASKING, scope, DiagnosisCoverageStatus.UNAVAILABLE, None,
        "labelled fixture: no method configured",
    )  # fmt: skip
    return DiagnosisReport(state.project_id, state.revision, (coverage,), ())


DIAGNOSIS_REQUEST = DiagnosisRequest(STATE, SCOPE, (_observation(),))


class _ScriptedDiagnosis:
    def __init__(self, result):
        self.result = result

    def diagnose(self, request):
        return self.result


def test_diagnosis_source_returns_a_port_result_that_validates():
    source: DiagnosisSource = _ScriptedDiagnosis(Success(_report()))
    result = source.diagnose(DIAGNOSIS_REQUEST)
    assert isinstance(result, Success)
    validate_diagnosis_response(DIAGNOSIS_REQUEST, result.value)
    failure: DiagnosisSource = _ScriptedDiagnosis(PortFailure(PortFailureCode.UNAVAILABLE, "labelled failure", False))
    assert isinstance(failure.diagnose(DIAGNOSIS_REQUEST), PortFailure)


@pytest.mark.parametrize(
    "report",
    [
        _report(state=_state(revision="r9", members=(S1, S2, MIX))),  # another revision
        _report(scope=AnalysisScope((T1, T2), TimeRange(0.0, 90.0))),  # coverage outside the supplied scope
    ],
    ids=["revision", "scope"],
)
def test_diagnosis_response_must_describe_the_supplied_state_and_scope(report):
    with pytest.raises(ValueError):
        validate_diagnosis_response(DIAGNOSIS_REQUEST, report)


@pytest.mark.parametrize(
    "args",
    [(STATE, None), (DIAGNOSIS_REQUEST, Success(None)), (DIAGNOSIS_REQUEST, "report")],
)
def test_diagnosis_response_types(args):
    request, response = args
    if response is None:
        response = _report()
    with pytest.raises(ValueError):
        validate_diagnosis_response(request, response)


# --- outer render request and response ---------------------------------------------------------------

WORKSPACE = ExecutionWorkspace(ExecutionId("e1"), "work", "e1")
PLAN = RenderPlan(
    ExecutionId("e1"), P1, Revision("r1"), CandidateIdentity(Revision("r2"), ArtifactId("s1-r2"), ArtifactId("mix-r2")),
    T1, IN_T1, -3.0, (S1, S2), ArtifactId("s1"), ArtifactId("mix"), RECIPE, RENDER_SPEC, PROCESSING, WORKSPACE,
)  # fmt: skip
BEFORE = StateIntegrityRequest(STATE, _roots())


def _protection(before, protected, retained_key="retained"):
    """Explicit bindings for ``protected``: the before root for required artifacts, ``retained_key`` otherwise."""
    known = before.bindings if isinstance(before, StateIntegrityRequest) else BEFORE.bindings
    keys = {binding.artifact_id: binding.root_key for binding in known}
    return tuple(
        ArtifactRootBinding(a.artifact_id, keys.get(a.artifact_id, retained_key))
        for a in protected
        if isinstance(a, AudioArtifact)
    )


def _render_request(**overrides) -> RunRenderRequest:
    fields = dict(plan=PLAN, before=BEFORE, protected_artifacts=REQUIRED)
    fields.update(overrides)
    if "protected_root_bindings" not in fields:
        fields["protected_root_bindings"] = _protection(fields["before"], fields["protected_artifacts"])
    return RunRenderRequest(**fields)


RENDER_REQUEST = _render_request()


def test_render_request_keeps_its_objects_and_allows_additional_protected_artifacts():
    extra = _artifact("older-output", "r0", ("t2",))
    request = _render_request(protected_artifacts=(*REQUIRED, extra))
    assert request.plan is PLAN and request.before is BEFORE and request.protected_artifacts[-1] is extra


@pytest.mark.parametrize(
    "field, value",
    [
        ("plan", BEFORE),
        ("before", STATE),
        ("protected_artifacts", list(REQUIRED)),
        ("protected_artifacts", (*REQUIRED, "s9")),
    ],
)
def test_render_request_types(field, value):
    with pytest.raises(ValueError):
        _render_request(**{field: value})


def test_render_request_rejects_duplicate_protected_ids():
    with pytest.raises(ValueError, match="s1"):
        _render_request(protected_artifacts=(*REQUIRED, S1))


def test_before_state_must_be_the_plan_project_and_revision():
    later = _state(revision="r9")
    with pytest.raises(ValueError, match="revision"):
        _render_request(before=StateIntegrityRequest(later, _roots()))


@pytest.mark.parametrize(
    "plan_changes, message",
    [
        (
            {"fixed_target_input": dataclasses.replace(IN_T1, content_hash="e" * 64)},
            "fixed",
        ),
        (
            {
                "current_stems": (
                    dataclasses.replace(S1, location="outputs/other.wav"),
                    S2,
                )
            },
            "stem",
        ),
        ({"current_mix_artifact_id": ArtifactId("mix-missing")}, "mix"),
    ],
    ids=["fixed-input-metadata", "current-stem-metadata", "unknown-mix"],
)
def test_plan_inputs_must_match_the_before_state_exactly(plan_changes, message):
    with pytest.raises(ValueError, match=message):
        _render_request(plan=dataclasses.replace(PLAN, **plan_changes))


def test_fixed_target_input_must_be_the_configured_processing_baseline():
    # The current stem is a valid member but is not the fixed input for an absolute gain edit.
    with pytest.raises(ValueError, match="fixed"):
        _render_request(plan=dataclasses.replace(PLAN, fixed_target_input=S1))


def test_current_mix_id_cannot_name_a_stem():
    with pytest.raises(ValueError, match="mix"):
        _render_request(plan=dataclasses.replace(PLAN, current_mix_artifact_id=S1.artifact_id))


def test_current_mix_must_cover_the_recipe_contributors():
    incomplete_mix = dataclasses.replace(MIX, source_track_ids=(T1,))
    state = _state(members=(S1, S2, incomplete_mix))
    required = (S1, S2, incomplete_mix, IN_T1, IN_T2)
    with pytest.raises(ValueError, match="mix"):
        _render_request(
            before=StateIntegrityRequest(state, _roots(required)),
            protected_artifacts=required,
        )


@pytest.mark.parametrize(
    "protected",
    [
        REQUIRED[:-1],
        REQUIRED[1:],
        (dataclasses.replace(S1, location="outputs/moved.wav"), *REQUIRED[1:]),
    ],
    ids=["missing-fixed-input", "missing-member", "altered-metadata"],
)
def test_every_required_before_artifact_must_be_protected_with_equal_metadata(
    protected,
):
    with pytest.raises(ValueError, match="protect"):
        _render_request(protected_artifacts=protected)


@pytest.mark.parametrize("candidate_id", ["s1-r2", "mix-r2"])
def test_candidate_output_ids_must_not_reuse_protected_ids(candidate_id):
    retained = _artifact(candidate_id, "r0", ("t2",))
    with pytest.raises(ValueError, match="candidate"):
        _render_request(protected_artifacts=(*REQUIRED, retained))


def _output(aid, tracks, kind) -> AudioArtifact:
    return dataclasses.replace(
        _artifact(aid, "r2", tracks, kind),
        location=f"work/e1/{aid}.wav",
        content_hash="c" * 64,
    )


def _evidence(aid, inputs) -> RenderEvidence:
    return RenderEvidence(ArtifactId(aid), tuple(ArtifactId(i) for i in inputs), True, True, 0.5,
                          VersionRef("processor", "1"), PROCESSING)  # fmt: skip


RENDERED = RenderedCandidate(
    _output("s1-r2", ("t1",), ArtifactKind.STEM),
    _output("mix-r2", ("t1", "t2"), ArtifactKind.MIX),
    (_evidence("s1-r2", ("in-t1",)), _evidence("mix-r2", ("s1-r2", "s2"))),
)


class _ScriptedRenderer:
    def __init__(self, outcome):
        self.outcome = outcome

    def process(self, request):
        return self.outcome


@pytest.mark.parametrize(
    "outcome",
    [
        RENDERED,
        RenderFailure(ExecutionStatus.FAILED, ExecutionIssue("render", "labelled failure")),
        RenderFailure(ExecutionStatus.UNCERTAIN, ExecutionIssue("render", "labelled uncertainty")),
        RenderPreparationFailure(ExecutionIssue("prepare", "labelled refusal"), None),
        RenderPreparationFailure(ExecutionIssue("prepare", "labelled refusal"), WORKSPACE),
    ],
)
def test_valid_render_responses(outcome):
    renderer: RunRenderer = _ScriptedRenderer(outcome)
    assert validate_run_render_response(RENDER_REQUEST, renderer.process(RENDER_REQUEST)) is None


def test_rendered_candidate_is_checked_against_the_plan():
    wrong = dataclasses.replace(RENDERED, target_artifact=_output("other-id", ("t1",), ArtifactKind.STEM),
                                evidence=(_evidence("other-id", ("in-t1",)), _evidence("mix-r2", ("other-id", "s2"))))  # fmt: skip
    with pytest.raises(ValueError):
        validate_run_render_response(RENDER_REQUEST, wrong)


def test_preparation_failure_workspace_must_be_the_plan_workspace():
    other = ExecutionWorkspace(ExecutionId("e1"), "work", "other-token")
    with pytest.raises(ValueError, match="workspace"):
        validate_run_render_response(
            RENDER_REQUEST,
            RenderPreparationFailure(ExecutionIssue("prepare", "x"), other),
        )


@pytest.mark.parametrize("args", [(PLAN, RENDERED), (RENDER_REQUEST, "rendered"), (RENDER_REQUEST, None)])
def test_render_response_types(args):
    with pytest.raises(ValueError):
        validate_run_render_response(*args)


# --- clock and identity protocols --------------------------------------------------------------------


def test_protocol_shapes():
    for protocol in (DiagnosisSource, RunRenderer, RunClock, IdentitySource):
        assert typing.Protocol in protocol.__mro__
        assert not getattr(protocol, "_is_runtime_protocol", False)
    assert typing.get_type_hints(DiagnosisSource.diagnose) == {
        "request": DiagnosisRequest,
        "return": PortResult[DiagnosisReport],
    }
    assert typing.get_type_hints(RunRenderer.process) == {
        "request": RunRenderRequest,
        "return": RunRenderOutcome,
    }
    assert typing.get_type_hints(RunClock.read) == {"return": ClockReading}
    assert typing.get_type_hints(IdentitySource.next_token) == {"return": str}


class _ScriptedClock:
    def read(self):
        return ClockReading(1.5, UTC_NOW, False)


class _ScriptedIdentities:
    def __init__(self):
        self.count = 0

    def next_token(self):
        self.count += 1
        return f"id-{self.count}"


def test_clock_and_identity_doubles_satisfy_their_protocols():
    clock: RunClock = _ScriptedClock()
    identities: IdentitySource = _ScriptedIdentities()
    assert clock.read().monotonic_seconds == 1.5
    assert [identities.next_token() for _ in range(2)] == ["id-1", "id-2"]


# --- output root (controlled-coordinator composition plan, 2026-11-09) -------------------------------


@pytest.mark.parametrize("key", ["", " ", "\t"])
def test_output_root_key_must_be_nonblank(key):
    with pytest.raises(ValueError):
        _run_request(output_root_key=key)


def test_output_root_key_is_an_explicit_logical_root_distinct_from_input_roots():
    request = _run_request(output_root_key="renders")
    assert request.output_root_key == "renders"
    assert {binding.root_key for binding in request.root_bindings} == {"exports"}


def test_output_root_key_may_equal_an_input_root():
    assert _run_request(output_root_key="exports").output_root_key == "exports"


# --- explicit protected root bindings (file-run-renderer plan, 2026-11-10) ---------------------------

RETAINED_OUTPUT = _artifact("older-output", "r0", ("t2",))


def test_extra_retained_artifact_needs_and_keeps_its_explicit_root():
    bindings = (*BEFORE.bindings, ArtifactRootBinding(RETAINED_OUTPUT.artifact_id, "archive"))
    request = _render_request(protected_artifacts=(*REQUIRED, RETAINED_OUTPUT), protected_root_bindings=bindings)
    assert request.protected_root_bindings is bindings


def test_reordered_complete_bindings_are_accepted_unchanged():
    bindings = tuple(reversed((*BEFORE.bindings, ArtifactRootBinding(RETAINED_OUTPUT.artifact_id, "archive"))))
    request = _render_request(protected_artifacts=(*REQUIRED, RETAINED_OUTPUT), protected_root_bindings=bindings)
    assert request.protected_root_bindings is bindings


@pytest.mark.parametrize(
    "bindings",
    [
        list(BEFORE.bindings),
        (*BEFORE.bindings, "s1"),
        (*BEFORE.bindings, ArtifactId("older-output")),
    ],
    ids=["list", "string-element", "identifier-element"],
)
def test_protected_root_bindings_types(bindings):
    with pytest.raises(ValueError):
        _render_request(protected_artifacts=(*REQUIRED, RETAINED_OUTPUT), protected_root_bindings=bindings)


@pytest.mark.parametrize(
    "bindings, message",
    [
        ((*BEFORE.bindings,), "older-output"),  # the retained artifact has no binding
        ((*BEFORE.bindings[1:], ArtifactRootBinding(RETAINED_OUTPUT.artifact_id, "archive")), "s1"),  # required one missing
        ((*BEFORE.bindings, ArtifactRootBinding(RETAINED_OUTPUT.artifact_id, "archive"),
          ArtifactRootBinding(RETAINED_OUTPUT.artifact_id, "archive")), "duplicate"),
        ((*BEFORE.bindings, ArtifactRootBinding(RETAINED_OUTPUT.artifact_id, "archive"),
          ArtifactRootBinding(ArtifactId("stray"), "archive")), "stray"),
    ],
    ids=["missing-retained", "missing-required", "duplicate", "extra"],
)  # fmt: skip
def test_every_protected_artifact_has_exactly_one_binding(bindings, message):
    with pytest.raises(ValueError, match=message):
        _render_request(protected_artifacts=(*REQUIRED, RETAINED_OUTPUT), protected_root_bindings=bindings)


def test_required_artifact_binding_must_use_the_before_root():
    conflicting = tuple(
        ArtifactRootBinding(b.artifact_id, "elsewhere") if b.artifact_id == S1.artifact_id else b
        for b in BEFORE.bindings
    )
    with pytest.raises(ValueError, match="s1"):
        _render_request(protected_root_bindings=conflicting)


@pytest.mark.parametrize("key", ["", " "])
def test_blank_protected_root_keys_are_rejected_by_the_binding_invariant(key):
    with pytest.raises(ValueError):
        ArtifactRootBinding(RETAINED_OUTPUT.artifact_id, key)


def test_protected_bindings_do_not_relax_existing_protection_checks():
    with pytest.raises(ValueError, match="protect"):  # metadata equality is still enforced
        _render_request(protected_artifacts=REQUIRED[:-1], protected_root_bindings=BEFORE.bindings[:-1])
    retained = _artifact("s1-r2", "r0", ("t2",))
    with pytest.raises(ValueError, match="candidate"):  # candidate IDs still may not reuse protected IDs
        _render_request(protected_artifacts=(*REQUIRED, retained),
                        protected_root_bindings=(*BEFORE.bindings, ArtifactRootBinding(retained.artifact_id, "x")))  # fmt: skip
