"""Focused tests for run configuration, lifecycle state and budget records (Task 2C).

Limits, usage values, metrics and thresholds are arbitrary structural fixtures, not recommended settings.
"""

import dataclasses
import math
from datetime import datetime, timedelta, timezone

import pytest

from autonomous_mixing.core.records.budgets import BudgetLimits, BudgetUsage
from autonomous_mixing.core.records.context import AssertionSource, MusicalContext, TrackRole
from autonomous_mixing.core.records.decision import ControllerIdentity
from autonomous_mixing.core.records.identifiers import (
    ConditionId,
    ContextId,
    CriteriaId,
    ProjectId,
    Revision,
    RunId,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.observation import (
    AnalysisScope,
    AnalysisSpec,
    FrequencyRange,
    MeasuredScalar,
    RenderSpec,
    TimeRange,
)
from autonomous_mixing.core.records.project import ProjectSnapshot, Track
from autonomous_mixing.core.records.run import FinalProjectState, RunConfiguration, RunOutcome, RunRecord, RunState
from autonomous_mixing.core.records.termination import TerminationReason
from autonomous_mixing.core.records.verification import (
    AbsoluteGuard,
    AcceptanceCriteria,
    BaselineReference,
    ComparisonOperator,
    DeltaGuard,
    ImprovementCondition,
    ImprovementDirection,
    MetricSelector,
    TargetCondition,
)

UTC_NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
_DEFAULT = object()
METRIC_A, METRIC_B = VersionRef("metric-a", "1"), VersionRef("metric-b", "2")


def _snapshot() -> ProjectSnapshot:
    names = ("Field recording", "Choir", "Tape loop", "Room mic")
    tracks = tuple(Track(TrackId(f"t{i}"), name) for i, name in enumerate(names, start=1))
    return ProjectSnapshot(ProjectId("p1"), Revision("r1"), UTC_NOW, tracks)


def _context(protected: frozenset = frozenset()) -> MusicalContext:
    source = AssertionSource("producer", UTC_NOW)
    roles = tuple(TrackRole(TrackId(t), r, source) for t, r in [("t1", "ambience"), ("t2", "lead"), ("t3", "texture")])
    return MusicalContext(ContextId("c1"), "1", roles, (), protected)


def _scope(tracks=("t1", "t2", "t3"), start=0.0, end=60.0, freq=None) -> AnalysisScope:
    return AnalysisScope(tuple(TrackId(t) for t in tracks), TimeRange(start, end), freq)


def _selector(metric=METRIC_A, **scope_kwargs) -> MetricSelector:
    return MetricSelector(metric, _scope(**scope_kwargs))


def _criteria(target_selector=_DEFAULT, guard_selector=_DEFAULT) -> AcceptanceCriteria:
    target_sel = (
        _selector(tracks=("t1", "t2"), start=10.0, end=20.0) if target_selector is _DEFAULT else target_selector
    )
    guard_sel = _selector(METRIC_B, tracks=("t3",)) if guard_selector is _DEFAULT else guard_selector
    return AcceptanceCriteria(
        CriteriaId("crit"),
        "1",
        (TargetCondition(ConditionId("target"), target_sel, ComparisonOperator.LE, MeasuredScalar(-6.0, "unit-a")),),
        (
            ImprovementCondition(
                ConditionId("improve"), _selector(), ImprovementDirection.LOWER, MeasuredScalar(1.0, "unit-a")
            ),
        ),
        (
            AbsoluteGuard(ConditionId("abs"), guard_sel, ComparisonOperator.LT, MeasuredScalar(0.0, "unit-b")),
            DeltaGuard(
                ConditionId("delta"), _selector(METRIC_B), BaselineReference.INITIAL, MeasuredScalar(2, "unit-b")
            ),
        ),
    )


def _limits(**overrides) -> BudgetLimits:
    fields = dict(
        max_decision_calls=4, max_attempts=2, max_render_calls=6, max_recovery_operations=1, max_elapsed_seconds=300.0
    )
    fields.update(overrides)
    return BudgetLimits(**fields)


def _usage(**overrides) -> BudgetUsage:
    fields = dict(decision_calls=0, attempts=0, render_calls=0, recovery_operations=0, elapsed_seconds=0.0)
    fields.update(overrides)
    return BudgetUsage(**fields)


def _configuration(**overrides) -> RunConfiguration:
    fields = dict(
        scope=_scope(),
        analysis_spec=AnalysisSpec(VersionRef("spec", "1"), (METRIC_A, METRIC_B)),
        render_spec=RenderSpec(VersionRef("render", "1"), 48000, 2),
        context=_context(),
        criteria=_criteria(),
        controller=ControllerIdentity("deterministic-baseline", "0.1"),
        budget_limits=_limits(),
    )
    fields.update(overrides)
    return RunConfiguration(**fields)


def _run(state=RunState.OBSERVING, outcome=None, final=None, reason=None, **overrides) -> RunRecord:
    fields = dict(
        run_id=RunId("run-1"),
        project_id=ProjectId("p1"),
        configuration=_configuration(),
        initial_revision=Revision("r1"),
        created_at=UTC_NOW,
        state=state,
        usage=_usage(),
        outcome=outcome,
        final_project_state=final,
        termination_reason=reason,
        termination_explanation="recorded explanation" if state is RunState.FINISHED else None,
    )
    fields.update(overrides)
    return RunRecord(**fields)


_DEFAULT_REASON = object()


def _finished(outcome, final, reason=_DEFAULT_REASON) -> RunRecord:
    if reason is _DEFAULT_REASON:
        reason = {
            RunOutcome.SUCCEEDED: TerminationReason.INITIAL_CRITERIA_MET,
            RunOutcome.ABSTAINED: TerminationReason.NO_JUSTIFIED_ACTION,
            RunOutcome.FAILED: TerminationReason.RECORDING_FAILED,
            RunOutcome.CANCELLED: TerminationReason.USER_CANCELLED,
            RunOutcome.LIMIT_REACHED: TerminationReason.ELAPSED_TIME_LIMIT,
        }[outcome]
    return _run(RunState.FINISHED, outcome, final, reason)


# --- identifiers and enums -------------------------------------------------------


@pytest.mark.parametrize("raw", ["", " ", 1, None])
def test_run_id_rejects_blank_and_non_string(raw):
    with pytest.raises(ValueError):
        RunId(raw)


def test_run_id_is_a_distinct_identifier_kind():
    assert RunId("x") == RunId("x")
    assert RunId("x") != ProjectId("x")


@pytest.mark.parametrize(
    "enum, names",
    [
        (
            RunState,
            {
                "INITIALIZING",
                "OBSERVING",
                "DECIDING",
                "CHECKING_ACTION",
                "PREPARING_EXECUTION",
                "EXECUTING",
                "RECONCILING",
                "ANALYZING_RESULT",
                "VALIDATING",
                "RESTORING",
                "FINISHED",
            },
        ),
        (RunOutcome, {"SUCCEEDED", "ABSTAINED", "LIMIT_REACHED", "FAILED", "CANCELLED"}),
        (FinalProjectState, {"UNCHANGED", "ACCEPTED_EDITS_RETAINED", "RESTORED_TO_LAST_ACCEPTED", "UNKNOWN"}),
    ],
)
def test_enums_have_exactly_the_specified_members(enum, names):
    assert {member.name for member in enum} == names


# --- configuration ---------------------------------------------------------------


def test_valid_configuration_with_arbitrary_tracks_and_subset_condition_scopes():
    configuration = _configuration()
    configuration.validate_against(_snapshot())
    assert configuration.criteria.guards[0].selector.scope.track_ids == (TrackId("t3"),)


def test_condition_scopes_may_equal_the_run_scope_boundaries():
    configuration = _configuration(criteria=_criteria(target_selector=_selector(start=0.0, end=60.0)))
    configuration.validate_against(_snapshot())


@pytest.mark.parametrize(
    "overrides",
    [
        {"scope": "scope"},
        {"analysis_spec": VersionRef("spec", "1")},
        {"render_spec": None},
        {"context": ContextId("c1")},
        {"criteria": (_criteria(),)},
        {"controller": ("controller", "1")},
        {"budget_limits": {"max_attempts": 1}},
    ],
)
def test_configuration_rejects_wrong_field_types(overrides):
    with pytest.raises(ValueError):
        _configuration(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"scope": _scope(tracks=("t1", "t9"))},
        {"context": _context(protected=frozenset({TrackId("t9")}))},
        {"criteria": _criteria(guard_selector=_selector(METRIC_B, tracks=("t9",)))},
    ],
)
def test_configuration_rejects_unknown_scope_context_and_condition_tracks(overrides):
    with pytest.raises(ValueError, match="t9"):
        _configuration(**overrides).validate_against(_snapshot())


@pytest.mark.parametrize(
    "selector, match",
    [
        (_selector(tracks=("t1", "t4")), "t4"),
        (_selector(start=0.0, end=60.5), "time"),
        (_selector(start=-0.0, end=61.0), "time"),
    ],
)
def test_condition_scope_must_lie_within_run_tracks_and_time(selector, match):
    # t4 exists in the project but is outside the run scope.
    configuration = _configuration(criteria=_criteria(target_selector=selector))
    with pytest.raises(ValueError, match=match):
        configuration.validate_against(_snapshot())


def test_condition_scope_starting_before_run_scope_is_rejected():
    configuration = _configuration(
        scope=_scope(start=5.0, end=60.0),
        criteria=_criteria(target_selector=_selector(start=4.0, end=20.0)),
    )
    with pytest.raises(ValueError, match="time"):
        configuration.validate_against(_snapshot())


def _band_criteria(target_freq, guard_freq=FrequencyRange(50.0, 500.0), other_freq=FrequencyRange(20.0, 1000.0)):
    return AcceptanceCriteria(
        CriteriaId("crit"),
        "1",
        (
            TargetCondition(
                ConditionId("target"), _selector(freq=target_freq), ComparisonOperator.LE, MeasuredScalar(1, "u")
            ),
        ),
        (
            ImprovementCondition(
                ConditionId("improve"), _selector(freq=other_freq), ImprovementDirection.HIGHER, MeasuredScalar(1, "u")
            ),
        ),
        (
            AbsoluteGuard(
                ConditionId("abs"), _selector(METRIC_B, freq=guard_freq), ComparisonOperator.GE, MeasuredScalar(0, "u")
            ),
        ),
    )


def test_frequency_restricted_run_accepts_contained_condition_bands():
    configuration = _configuration(
        scope=_scope(freq=FrequencyRange(20.0, 1000.0)), criteria=_band_criteria(FrequencyRange(20.0, 1000.0))
    )
    configuration.validate_against(_snapshot())


@pytest.mark.parametrize(
    "target_freq",
    [None, FrequencyRange(10.0, 500.0), FrequencyRange(100.0, 1000.5)],
)
def test_frequency_restricted_run_requires_explicit_contained_condition_bands(target_freq):
    configuration = _configuration(
        scope=_scope(freq=FrequencyRange(20.0, 1000.0)), criteria=_band_criteria(target_freq)
    )
    with pytest.raises(ValueError, match="frequency"):
        configuration.validate_against(_snapshot())


def test_unrestricted_run_allows_condition_bands_below_nyquist():
    configuration = _configuration(
        render_spec=RenderSpec(VersionRef("render", "1"), 8000, 1),
        criteria=_band_criteria(FrequencyRange(0.0, 4000.0), other_freq=None),
    )
    configuration.validate_against(_snapshot())


def test_condition_band_above_render_nyquist_is_rejected():
    configuration = _configuration(
        render_spec=RenderSpec(VersionRef("render", "1"), 8000, 1),
        criteria=_band_criteria(FrequencyRange(0.0, 4000.5), other_freq=None),
    )
    with pytest.raises(ValueError, match="Nyquist"):
        configuration.validate_against(_snapshot())


def _render_8k_band_run(run_high_hz: float) -> RunConfiguration:
    # Render Nyquist is 4000 Hz; every condition band lies well below it.
    return _configuration(
        scope=_scope(freq=FrequencyRange(20.0, run_high_hz)),
        render_spec=RenderSpec(VersionRef("render", "1"), 8000, 1),
        criteria=_band_criteria(FrequencyRange(20.0, 1000.0)),
    )


def test_run_scope_band_above_render_nyquist_is_rejected():
    with pytest.raises(ValueError, match="run scope.*Nyquist"):
        _render_8k_band_run(4000.5).validate_against(_snapshot())


def test_run_scope_band_ending_exactly_at_render_nyquist_is_permitted():
    _render_8k_band_run(4000.0).validate_against(_snapshot())


def test_unrestricted_run_scope_is_permitted_with_low_render_rate():
    configuration = _configuration(
        scope=_scope(freq=None),
        render_spec=RenderSpec(VersionRef("render", "1"), 8000, 1),
        criteria=_band_criteria(FrequencyRange(20.0, 1000.0), other_freq=None),
    )
    assert configuration.scope.frequency_range is None
    configuration.validate_against(_snapshot())


def test_condition_metric_must_be_listed_in_analysis_spec():
    configuration = _configuration(analysis_spec=AnalysisSpec(VersionRef("spec", "1"), (METRIC_A,)))
    with pytest.raises(ValueError, match="metric-b"):
        configuration.validate_against(_snapshot())
    other_version = AnalysisSpec(VersionRef("spec", "1"), (METRIC_A, VersionRef("metric-b", "3")))
    with pytest.raises(ValueError, match="metric-b"):
        _configuration(analysis_spec=other_version).validate_against(_snapshot())


# --- budgets ---------------------------------------------------------------------


def test_zero_count_limits_and_zero_usage_are_valid():
    limits = _limits(max_decision_calls=0, max_attempts=0, max_render_calls=0, max_recovery_operations=0)
    assert limits.max_attempts == 0
    assert _usage().elapsed_seconds == 0.0


@pytest.mark.parametrize("field", ["max_decision_calls", "max_attempts", "max_render_calls", "max_recovery_operations"])
@pytest.mark.parametrize("value", [-1, True, 1.0, 1.5, "1", None, math.nan])
def test_limit_counts_reject_negative_boolean_fractional_and_wrong_types(field, value):
    with pytest.raises(ValueError):
        _limits(**{field: value})


@pytest.mark.parametrize("value", [0, 0.0, -1.0, True, math.nan, math.inf, "60", None])
def test_elapsed_limit_must_be_finite_and_strictly_positive(value):
    with pytest.raises(ValueError):
        _limits(max_elapsed_seconds=value)


@pytest.mark.parametrize("field", ["decision_calls", "attempts", "render_calls", "recovery_operations"])
@pytest.mark.parametrize("value", [-1, True, 2.0, 0.5, "1", None])
def test_usage_counts_reject_negative_boolean_fractional_and_wrong_types(field, value):
    with pytest.raises(ValueError):
        _usage(**{field: value})


@pytest.mark.parametrize("value", [-0.1, True, math.nan, math.inf, "1", None])
def test_usage_elapsed_must_be_finite_and_nonnegative(value):
    with pytest.raises(ValueError):
        _usage(elapsed_seconds=value)


def test_budget_records_require_every_field():
    with pytest.raises(TypeError):
        BudgetUsage(decision_calls=0, attempts=0, render_calls=0, recovery_operations=0)
    with pytest.raises(TypeError):
        BudgetLimits(max_decision_calls=1, max_attempts=1, max_render_calls=1, max_recovery_operations=1)


def test_usage_exceeding_limits_remains_representable():
    limits = _limits()
    usage = _usage(decision_calls=9, attempts=5, render_calls=40, recovery_operations=3, elapsed_seconds=301.5)
    run = _run(usage=usage, configuration=_configuration(budget_limits=limits))
    assert run.usage.attempts > run.configuration.budget_limits.max_attempts


# --- run records -----------------------------------------------------------------


@pytest.mark.parametrize("state", [s for s in RunState if s is not RunState.FINISHED])
def test_active_states_have_no_terminal_fields(state):
    assert _run(state).outcome is None
    for fields in [
        {"outcome": RunOutcome.FAILED},
        {"final": FinalProjectState.UNCHANGED},
        {"reason": "stopped"},
    ]:
        with pytest.raises(ValueError):
            _run(state, **fields)


@pytest.mark.parametrize(
    "outcome, final, reason",
    [
        (None, FinalProjectState.UNCHANGED, "stop"),
        (RunOutcome.ABSTAINED, None, "stop"),
        (RunOutcome.ABSTAINED, FinalProjectState.UNCHANGED, None),
        (RunOutcome.ABSTAINED, FinalProjectState.UNCHANGED, "  "),
        ("ABSTAINED", FinalProjectState.UNCHANGED, "stop"),
        (RunOutcome.ABSTAINED, "UNCHANGED", "stop"),
    ],
)
def test_finished_run_requires_typed_terminal_fields(outcome, final, reason):
    with pytest.raises(ValueError):
        _finished(outcome, final, reason)


@pytest.mark.parametrize(
    "outcome, final",
    [
        (RunOutcome.FAILED, FinalProjectState.UNCHANGED),
        (RunOutcome.FAILED, FinalProjectState.UNKNOWN),
        (RunOutcome.CANCELLED, FinalProjectState.ACCEPTED_EDITS_RETAINED),
        (RunOutcome.LIMIT_REACHED, FinalProjectState.ACCEPTED_EDITS_RETAINED),
        (RunOutcome.LIMIT_REACHED, FinalProjectState.RESTORED_TO_LAST_ACCEPTED),
        (RunOutcome.ABSTAINED, FinalProjectState.UNCHANGED),
        (RunOutcome.SUCCEEDED, FinalProjectState.UNCHANGED),
        (RunOutcome.SUCCEEDED, FinalProjectState.ACCEPTED_EDITS_RETAINED),
        (RunOutcome.SUCCEEDED, FinalProjectState.RESTORED_TO_LAST_ACCEPTED),
    ],
)
def test_finished_outcome_and_project_state_combinations_are_representable(outcome, final):
    run = _finished(outcome, final)
    assert (run.outcome, run.final_project_state) == (outcome, final)


def test_succeeded_run_cannot_have_unknown_final_state():
    with pytest.raises(ValueError, match="SUCCEEDED"):
        _finished(RunOutcome.SUCCEEDED, FinalProjectState.UNKNOWN)


@pytest.mark.parametrize(
    "overrides",
    [
        {"run_id": "run-1"},
        {"project_id": "p1"},
        {"configuration": None},
        {"initial_revision": "r1"},
        {"created_at": datetime(2026, 10, 19, 12, 0)},
        {"created_at": datetime(2026, 10, 19, 12, 0, tzinfo=timezone(timedelta(hours=2)))},
        {"state": "OBSERVING"},
        {"usage": _limits()},
    ],
)
def test_run_rejects_wrong_types_and_non_utc_timestamps(overrides):
    with pytest.raises(ValueError):
        _run(**overrides)


def test_run_has_no_transition_methods():
    assert not any(name in dir(RunRecord) for name in ("transition", "finish", "advance"))


def test_configuration_and_nested_collections_are_frozen():
    run = _finished(RunOutcome.ABSTAINED, FinalProjectState.UNCHANGED)
    configuration = run.configuration
    assert isinstance(configuration.criteria.targets, tuple)
    assert isinstance(configuration.analysis_spec.metric_ids, tuple)
    assert isinstance(configuration.context.protected_track_ids, frozenset)
    with pytest.raises(dataclasses.FrozenInstanceError):
        run.state = RunState.OBSERVING
    with pytest.raises(dataclasses.FrozenInstanceError):
        configuration.budget_limits = _limits(max_attempts=99)
    with pytest.raises(dataclasses.FrozenInstanceError):
        configuration.budget_limits.max_attempts = 99
    with pytest.raises(dataclasses.FrozenInstanceError):
        run.usage.attempts = 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        configuration.scope.time_range.end_seconds = 1.0


# --- dependency boundary ---------------------------------------------------------
