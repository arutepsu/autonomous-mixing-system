"""Metadata fixtures and tests for the 2026-11-09 run-recording-contracts plan.

All records are labelled metadata fixtures on generic project and track identities: no files, clocks or
identity generators are used, and metric names, limits and values are structural examples, not
recommended settings or evaluation data. Ports are exercised with scripted test doubles that establish
response contracts only.
"""

import dataclasses
from datetime import datetime, timedelta, timezone
from typing import Protocol

import pytest

from autonomous_mixing.core.records.actions import ExecutionStatus
from autonomous_mixing.core.records.admission import (
    ActionAdmissionPolicy,
    StemBinding,
    TrackGainPermission,
)
from autonomous_mixing.core.records.analysis import AnalysisRequest, AnalysisResponse
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.budgets import BudgetLimits
from autonomous_mixing.core.records.capabilities import GainBounds, RendererCapabilities
from autonomous_mixing.core.records.context import (
    AssertionSource,
    MusicalContext,
    TrackRole,
)
from autonomous_mixing.core.records.coordinator_runtime import (
    DecisionRetryPolicy,
    DiagnosisRequest,
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
    RenderPlan,
)
from autonomous_mixing.core.records.results import (
    PortFailure,
    PortFailureCode,
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

from autonomous_mixing.core.loop.transitions import derive_candidate_project_state  # noqa: E402
from autonomous_mixing.core.ports.run_journal import (  # noqa: E402
    RunJournal,
    validate_run_journal_snapshot,
    validate_trace_append_response,
)
from autonomous_mixing.core.records.acceptance import (  # noqa: E402
    AcceptanceCommitted,
    AcceptanceEvent,
    AcceptedReference,
)
from autonomous_mixing.core.records.actions import Action, ExecutionReceipt, SetTrackGain  # noqa: E402
from autonomous_mixing.core.records.budgets import BudgetUsage  # noqa: E402
from autonomous_mixing.core.records.identifiers import (  # noqa: E402
    AcceptanceId,
    ActionId,
    AttemptId,
    DecisionCallId,
    DecisionId,
    RunEventId,
    VerificationResultId,
)
from autonomous_mixing.core.records.run import FinalProjectState, RunOutcome, RunRecord, RunState  # noqa: E402
from autonomous_mixing.core.records.run_trace import (  # noqa: E402
    DispatchIntent,
    DispatchSkipped,
    InvalidPortResponse,
    OperationKind,
    OperationObserved,
    RunJournalSnapshot,
    RunTraceEvent,
    TerminalSnapshot,
    TraceAppended,
    TraceAppendUnknown,
    TraceNotAppended,
)
from autonomous_mixing.core.records.termination import TerminationReason  # noqa: E402
from autonomous_mixing.core.records.verification import AssessmentMode  # noqa: E402
from autonomous_mixing.core.records.verification_assessment import VerificationAssessment  # noqa: E402
from autonomous_mixing.core.records.verification_evidence import StateEvidence  # noqa: E402
from autonomous_mixing.core.verification.assessment import assess_verification  # noqa: E402

INITIAL = AcceptedReference(STATE, 0)
ZERO = BudgetUsage(0, 0, 0, 0, 0.0)


def make_run(
    reason=TerminationReason.INITIAL_CRITERIA_MET,
    *,
    outcome=RunOutcome.SUCCEEDED,
    final=FinalProjectState.UNCHANGED,
    usage=ZERO,
):
    return RunRecord(
        RUN_REQUEST.run_id,
        P1,
        RUN_REQUEST.configuration,
        STATE.revision,
        UTC_NOW,
        RunState.FINISHED,
        usage,
        outcome,
        final,
        reason,
        "labelled terminal explanation",
    )


def make_event(payload, sequence=1, *, usage=ZERO, event_id=None, run_id=None):
    return RunTraceEvent(
        RunEventId(event_id or f"event-{sequence}"), sequence, run_id or RUN_REQUEST.run_id, UTC_NOW, usage, payload
    )


def make_terminal(**run_overrides):
    return make_event(TerminalSnapshot(make_run(**run_overrides), INITIAL, INITIAL))


def make_snapshot(events=(), acceptances=()):
    return RunJournalSnapshot(RUN_REQUEST.run_id, INITIAL, events, acceptances)


def make_acceptance(expected=INITIAL, number=1):
    before = expected.state
    target = dataclasses.replace(S1, artifact_id=ArtifactId(f"stem-{number}"), revision=Revision(f"r{number + 1}"))
    mix = dataclasses.replace(MIX, artifact_id=ArtifactId(f"mix-{number}"), revision=target.revision)
    current_target = next(
        a for a in before.audio.artifacts if a.kind is ArtifactKind.STEM and a.source_track_ids == (T1,)
    )
    current_mix = next(a for a in before.audio.artifacts if a.kind is ArtifactKind.MIX)
    action = Action(ActionId(f"action-{number}"), P1, before.revision, SetTrackGain(T1, -float(number)))
    receipt = ExecutionReceipt(
        ExecutionId(f"execution-{number}"),
        action.action_id,
        P1,
        before.revision,
        ExecutionStatus.CANDIDATE_READY,
        target.revision,
        (target, mix),
        (current_target.artifact_id, current_mix.artifact_id),
        None,
    )
    candidate = derive_candidate_project_state(before, receipt, action, CheckpointId(f"candidate-{number}"), UTC_NOW)

    def bundle(state, value, name):
        source = next(a for a in state.audio.artifacts if a.kind is ArtifactKind.MIX)
        observation = Observation(
            ObservationId(name),
            METRIC,
            (source.artifact_id,),
            SCOPE,
            MeasuredScalar(value, UNIT),
            SPEC,
            ANALYZER,
            UTC_NOW,
        )
        return StateEvidence(state, (observation,), ALIGNMENT)

    initial = bundle(STATE, 1.0, "initial-observation")
    before_evidence = initial if expected.sequence == 0 else bundle(before, 1.0 - expected.sequence, f"before-{number}")
    assessment = VerificationAssessment(
        VerificationResultId(f"verification-{number}"),
        AssessmentMode.ATTEMPT,
        CRITERIA,
        MEASUREMENTS,
        bundle(candidate, -expected.sequence, f"after-{number}"),
        before_evidence,
        initial,
    )
    result = assess_verification(assessment)
    event = AcceptanceEvent(
        AcceptanceId(f"acceptance-{number}"),
        RUN_REQUEST.run_id,
        AttemptId(f"attempt-{number}"),
        DecisionId(f"decision-{number}"),
        expected,
        action,
        receipt,
        assessment,
        result,
    )
    return AcceptanceCommitted(event, AcceptedReference(candidate, expected.sequence + 1))


def test_reason_values_and_groups():
    assert len(TerminationReason) == 33
    assert all(r.value == r.name.lower() for r in TerminationReason)
    for reason in TerminationReason:
        for outcome in RunOutcome:
            valid = (
                reason.name in {"INITIAL_CRITERIA_MET", "CANDIDATE_CRITERIA_MET"}
                and outcome is RunOutcome.SUCCEEDED
                or reason.name
                in {
                    "NO_CHANGE_PROPOSED",
                    "INSUFFICIENT_EVIDENCE",
                    "MISSING_CONTEXT",
                    "CONFLICTING_CONSTRAINTS",
                    "NO_JUSTIFIED_ACTION",
                }
                and outcome is RunOutcome.ABSTAINED
                or reason.name
                in {
                    "DECISION_CALL_LIMIT",
                    "ATTEMPT_LIMIT",
                    "RENDER_CALL_LIMIT",
                    "RECOVERY_CAPACITY_LIMIT",
                    "ELAPSED_TIME_LIMIT",
                }
                and outcome is RunOutcome.LIMIT_REACHED
                or reason.name == "USER_CANCELLED"
                and outcome is RunOutcome.CANCELLED
                or reason.name
                not in {
                    "INITIAL_CRITERIA_MET",
                    "CANDIDATE_CRITERIA_MET",
                    "NO_CHANGE_PROPOSED",
                    "INSUFFICIENT_EVIDENCE",
                    "MISSING_CONTEXT",
                    "CONFLICTING_CONSTRAINTS",
                    "NO_JUSTIFIED_ACTION",
                    "DECISION_CALL_LIMIT",
                    "ATTEMPT_LIMIT",
                    "RENDER_CALL_LIMIT",
                    "RECOVERY_CAPACITY_LIMIT",
                    "ELAPSED_TIME_LIMIT",
                    "USER_CANCELLED",
                }
                and outcome is RunOutcome.FAILED
            )
            if valid:
                make_run(reason, outcome=outcome)
            else:
                with pytest.raises(ValueError):
                    make_run(reason, outcome=outcome)


def test_operation_pairs_and_original_requests():
    intent = DispatchIntent(
        OperationKind.ANALYZE, AnalysisRequest(STATE, SCOPE, _configuration().analysis_spec), INITIAL, None, None, None
    )
    event = make_event(intent)
    observed = make_event(OperationObserved(event.event_id, OperationKind.ANALYZE, AnalysisResponse(())), 2)
    trace = make_snapshot((event, observed))
    assert trace.events[0] is event and trace.accepted is INITIAL
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.sequence = 4
    with pytest.raises(ValueError):
        dataclasses.replace(intent, operation=OperationKind.DECIDE)
    with pytest.raises(ValueError):
        OperationObserved(event.event_id, OperationKind.RENDER, AnalysisResponse(()))


@pytest.mark.parametrize(
    "reason",
    [
        TerminationReason.USER_CANCELLED,
        TerminationReason.ELAPSED_TIME_LIMIT,
        TerminationReason.RECORDING_FAILED,
        TerminationReason.RUNTIME_CONTRACT_FAILED,
    ],
)
def test_skipped_slots_keep_reason(reason):
    skipped = DispatchSkipped(reason, "not dispatched")
    assert skipped.reason is reason
    with pytest.raises(ValueError):
        DispatchSkipped(TerminationReason.INITIAL_CRITERIA_MET, "invalid skip")


def test_trace_prefix_rejects_gaps_duplicates_run_mismatch_and_usage_regression():
    event = make_event(RUN_REQUEST)
    for entries in (
        (dataclasses.replace(event, sequence=2),),
        (event, dataclasses.replace(event, sequence=2)),
        (
            dataclasses.replace(
                event, run_id=RunId("different"), payload=dataclasses.replace(RUN_REQUEST, run_id=RunId("different"))
            ),
        ),
    ):
        with pytest.raises(ValueError):
            make_snapshot(entries)
    first = dataclasses.replace(event, usage=BudgetUsage(1, 0, 0, 0, 3.0))
    with pytest.raises(ValueError):
        make_snapshot((first, make_event(RUN_REQUEST, 2)))


def test_pending_intent_and_observation_links():
    request = DiagnosisRequest(STATE, SCOPE, ())
    first = make_event(DispatchIntent(OperationKind.DIAGNOSE, request, INITIAL, None, None, None))
    assert len(make_snapshot((first,)).events) == 1
    failure = PortFailure(PortFailureCode.UNAVAILABLE, "fixture unavailable", False)
    second = make_event(OperationObserved(first.event_id, OperationKind.DIAGNOSE, failure), 2)
    assert len(make_snapshot((first, second)).events) == 2
    for observed in (
        OperationObserved(RunEventId("missing"), OperationKind.DIAGNOSE, failure),
        OperationObserved(first.event_id, OperationKind.ANALYZE, failure),
    ):
        with pytest.raises(ValueError):
            make_snapshot((first, make_event(observed, 2)))
    with pytest.raises(ValueError):
        make_snapshot((first, second, make_event(second.payload, 3)))


def test_independent_acceptance_chain_and_historical_sequence():
    first = make_acceptance()
    second = make_acceptance(first.accepted, 2)
    trace = make_snapshot((make_event(RUN_REQUEST),), (first, second))
    assert trace.accepted is second.accepted and len(trace.events) == 1
    for acceptances in ((second,), (first, first), (second, first)):
        with pytest.raises(ValueError):
            make_snapshot(acceptances=acceptances)


def test_terminal_snapshot_requires_verified_state_and_last_event():
    terminal = make_terminal()
    assert make_snapshot((terminal,)).events[0] is terminal
    with pytest.raises(ValueError):
        make_snapshot((terminal, make_event(RUN_REQUEST, 2)))
    with pytest.raises(ValueError):
        TerminalSnapshot(make_run(), INITIAL, None)
    with pytest.raises(ValueError):
        TerminalSnapshot(
            make_run(TerminationReason.RECORDING_FAILED, outcome=RunOutcome.FAILED, final=FinalProjectState.UNKNOWN),
            INITIAL,
            INITIAL,
        )


def test_append_and_snapshot_responses_bind_exactly():
    event = make_event(RUN_REQUEST)
    for response in (
        TraceAppended(event),
        TraceNotAppended(event.event_id, "refused"),
        TraceAppendUnknown(event.event_id, "unknown"),
    ):
        validate_trace_append_response(event, response)
    with pytest.raises(ValueError):
        validate_trace_append_response(
            event, TraceAppended(dataclasses.replace(event, created_at=UTC_NOW + timedelta(seconds=1)))
        )
    with pytest.raises(ValueError):
        validate_trace_append_response(event, TraceAppendUnknown(RunEventId("different"), "unknown"))
    validate_run_journal_snapshot(RUN_REQUEST.run_id, INITIAL, make_snapshot())
    with pytest.raises(ValueError):
        validate_run_journal_snapshot(RunId("different"), INITIAL, make_snapshot())
    assert Protocol in RunJournal.__mro__


@pytest.mark.parametrize("value", ["text", {}, None, object()])
def test_arbitrary_trace_payloads_are_rejected(value):
    with pytest.raises(ValueError):
        make_event(value)


def test_invalid_response_has_explicit_summary_fields():
    summary = InvalidPortResponse("UnexpectedType", "malformed controlled response")
    assert summary.type_name == "UnexpectedType"
    for values in (("", "reason"), ("Type", "  ")):
        with pytest.raises(ValueError):
            InvalidPortResponse(*values)


def make_intent(operation):
    integrity = StateIntegrityRequest(STATE, _roots())
    coverage = DiagnosisCoverage(
        DiagnosisCoverageId("coverage"),
        ProblemFamily.MASKING,
        SCOPE,
        DiagnosisCoverageStatus.UNAVAILABLE,
        None,
        "labelled unavailable method",
    )
    from autonomous_mixing.core.records.acceptance import AcceptanceQuery
    from autonomous_mixing.core.records.capabilities import ActionCapabilityView
    from autonomous_mixing.core.records.decision import Situation
    from autonomous_mixing.core.records.feedback import DecisionHistory

    situation = Situation(
        STATE,
        RUN_REQUEST.configuration.context,
        RUN_REQUEST.configuration.controller,
        (),
        BudgetUsage(1, 0, 0, 0, 0.0),
        RUN_REQUEST.configuration.budget_limits,
        RUN_REQUEST.run_id,
        SCOPE,
        CRITERIA,
        DiagnosisReport(P1, STATE.revision, (coverage,), ()),
        ActionCapabilityView(
            P1, STATE.revision, ContextId("c1"), "1", POLICY.reference, CAPABILITIES.reference, SCOPE, ()
        ),
        DecisionHistory(RUN_REQUEST.run_id, P1, ()),
    )
    workspace = ExecutionWorkspace(ExecutionId("execution"), "outputs", "token")
    plan = RenderPlan(
        workspace.execution_id,
        P1,
        STATE.revision,
        CandidateIdentity(Revision("r2"), ArtifactId("new-stem"), ArtifactId("new-mix")),
        T1,
        IN_T1,
        -1.0,
        (S1, S2),
        S1.artifact_id,
        MIX.artifact_id,
        RECIPE,
        RENDER_SPEC,
        PROCESSING,
        workspace,
    )
    requests = {
        OperationKind.ANALYZE: AnalysisRequest(STATE, SCOPE, RUN_REQUEST.configuration.analysis_spec),
        OperationKind.DIAGNOSE: DiagnosisRequest(STATE, SCOPE, ()),
        OperationKind.DECIDE: situation,
        OperationKind.RENDER: RunRenderRequest(plan, integrity, REQUIRED, integrity.bindings),
        OperationKind.QUERY_ACCEPTANCE: AcceptanceQuery(RUN_REQUEST.run_id, AcceptanceId("acceptance")),
        OperationKind.INSPECT_STATE: integrity,
    }
    call = DecisionCallId("call") if operation in (OperationKind.DECIDE, OperationKind.RENDER) else None
    attempt = AttemptId("attempt") if operation is OperationKind.RENDER else None
    execution = workspace.execution_id if operation is OperationKind.RENDER else None
    return DispatchIntent(operation, requests[operation], INITIAL, call, attempt, execution)


@pytest.mark.parametrize("operation", list(OperationKind))
def test_every_operation_can_be_recorded_and_complete_as_skipped(operation):
    intent = make_intent(operation)
    first = make_event(intent)
    result = DispatchSkipped(TerminationReason.USER_CANCELLED, "cancelled after intent acknowledgement")
    second = make_event(OperationObserved(first.event_id, operation, result), 2)
    assert make_snapshot((first, second)).events[1].payload.outcome is result


@pytest.mark.parametrize("operation", [OperationKind.DECIDE, OperationKind.RENDER])
def test_effect_identity_requirements(operation):
    intent = make_intent(operation)
    with pytest.raises(ValueError):
        dataclasses.replace(intent, call_id=None)
    if operation is OperationKind.RENDER:
        with pytest.raises(ValueError):
            dataclasses.replace(intent, attempt_id=None)
        with pytest.raises(ValueError):
            dataclasses.replace(intent, execution_id=ExecutionId("wrong"))
    else:
        with pytest.raises(ValueError):
            dataclasses.replace(intent, attempt_id=AttemptId("wrong"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_id", "text"),
        ("sequence", 0),
        ("sequence", True),
        ("run_id", "text"),
        ("created_at", UTC_NOW.replace(tzinfo=None)),
        ("usage", None),
    ],
)
def test_trace_event_fields_are_checked(field, value):
    with pytest.raises(ValueError):
        dataclasses.replace(make_event(RUN_REQUEST), **{field: value})


@pytest.mark.parametrize("explanation", [None, "", "  ", 2])
def test_finished_run_requires_a_human_explanation(explanation):
    with pytest.raises(ValueError):
        dataclasses.replace(make_run(), termination_explanation=explanation)


def test_active_run_cannot_carry_an_explanation():
    with pytest.raises(ValueError):
        dataclasses.replace(
            make_run(), state=RunState.OBSERVING, outcome=None, final_project_state=None, termination_reason=None
        )


@pytest.mark.parametrize(
    "final,reference",
    [
        (FinalProjectState.UNCHANGED, make_acceptance().accepted),
        (FinalProjectState.ACCEPTED_EDITS_RETAINED, INITIAL),
    ],
)
def test_terminal_disposition_cannot_erase_or_invent_accepted_edits(final, reference):
    run = make_run(TerminationReason.RECORDING_FAILED, outcome=RunOutcome.FAILED, final=final)
    with pytest.raises(ValueError):
        TerminalSnapshot(run, reference, reference)


@pytest.mark.parametrize(
    "field", ["decision_calls", "attempts", "render_calls", "recovery_operations", "elapsed_seconds"]
)
def test_every_usage_dimension_must_be_nondecreasing(field):
    fields = dict(decision_calls=0, attempts=0, render_calls=0, recovery_operations=0, elapsed_seconds=0.0)
    fields[field] = 1
    first = make_event(RUN_REQUEST, usage=BudgetUsage(**fields))
    with pytest.raises(ValueError):
        make_snapshot((first, make_event(RUN_REQUEST, 2)))


@pytest.mark.parametrize(
    "record,names",
    [
        (DispatchIntent, ("operation", "request", "accepted", "call_id", "attempt_id", "execution_id")),
        (DispatchSkipped, ("reason", "explanation")),
        (InvalidPortResponse, ("type_name", "explanation")),
        (OperationObserved, ("intent_id", "operation", "outcome")),
        (TerminalSnapshot, ("run", "authoritative", "verified")),
        (RunTraceEvent, ("event_id", "sequence", "run_id", "created_at", "usage", "payload")),
        (RunJournalSnapshot, ("run_id", "initial", "events", "acceptances")),
        (TraceAppended, ("event",)),
        (TraceNotAppended, ("event_id", "explanation")),
        (TraceAppendUnknown, ("event_id", "explanation")),
    ],
)
def test_record_shapes_are_frozen_and_all_fields_required(record, names):
    assert tuple(field.name for field in dataclasses.fields(record)) == names
    assert record.__dataclass_params__.frozen
    assert all(
        field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING
        for field in dataclasses.fields(record)
    )


def test_diagnosis_failed_reason_maps_to_failed():
    reason = TerminationReason.DIAGNOSIS_FAILED
    assert reason.value == "diagnosis_failed"
    make_run(reason, outcome=RunOutcome.FAILED)
    for outcome in RunOutcome:
        if outcome is not RunOutcome.FAILED:
            with pytest.raises(ValueError):
                make_run(reason, outcome=outcome)
