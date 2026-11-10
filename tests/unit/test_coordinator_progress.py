from dataclasses import FrozenInstanceError, replace

import pytest
from test_run_recording_contracts import INITIAL, RUN_REQUEST, UTC_NOW

from autonomous_mixing.core.records.budgets import BudgetUsage
from autonomous_mixing.core.records.coordinator_progress import RuntimeProgress
from autonomous_mixing.core.records.coordinator_runtime import ClockReading


def progress():
    clock = ClockReading(0.0, UTC_NOW, False)
    return RuntimeProgress(
        RUN_REQUEST,
        INITIAL,
        clock,
        clock,
        BudgetUsage(0, 0, 0, 0, 0.0),
        (),
        (),
        (),
        True,
        INITIAL,
        True,
        None,
        False,
        frozenset(),
        (),
        RUN_REQUEST.stem_bindings,
        RUN_REQUEST.mix_artifact_id,
        RUN_REQUEST.root_bindings,
        (),
        None,
    )


def test_progress_is_immutable_and_replaced_without_affecting_previous_snapshot():
    before = progress()
    after = replace(before, tokens=frozenset({"fresh"}))
    assert before.tokens == frozenset() and after.tokens == frozenset({"fresh"})
    with pytest.raises(FrozenInstanceError):
        before.recording_ok = False


@pytest.mark.parametrize(
    "changes",
    [
        {"known": []},
        {"tokens": {"mutable"}},
        {"tokens": frozenset({1})},
        {"history": (None,)},
        {"recording_ok": 1},
        {"accepted": None},
        {"stems": []},
        {"observations": (None,)},
        {"diagnosed": ("revision", None)},
        {"last": None},
    ],
)
def test_invalid_progress_fields_are_rejected(changes):
    with pytest.raises(ValueError):
        replace(progress(), **changes)


@pytest.fixture
def assessed_correction():
    from test_coordinator import INITIAL_VALUE, SATISFYING, Harness, payloads, propose

    from autonomous_mixing.core.records.coordinator_progress import AssessedCorrection
    from autonomous_mixing.core.records.decision import DecisionResponse
    from autonomous_mixing.core.records.run_trace import DispatchIntent, OperationKind, OperationObserved

    report = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),)).run()
    intent = next(p for p in payloads(report, DispatchIntent) if p.operation is OperationKind.RENDER)
    decision = next(
        p.outcome.decision for p in payloads(report, OperationObserved) if isinstance(p.outcome, DecisionResponse)
    )
    (commit,) = report.trace.acceptances
    event = commit.event
    return AssessedCorrection(
        intent.call_id,
        event.expected.state,
        decision,
        intent.attempt_id,
        intent.request.plan,
        event.execution,
        commit.accepted.state,
        event.assessment,
        event.verification,
    )


def test_prepared_and_assessed_phases_are_frozen_and_have_explicit_fields(assessed_correction):
    from autonomous_mixing.core.records.coordinator_progress import PreparedCorrection

    ctx = assessed_correction
    prepared = PreparedCorrection(ctx.call_id, ctx.before, ctx.decision, ctx.attempt_id, ctx.plan)
    assert prepared.action is ctx.decision.outcome.action
    assert prepared.execution_id == ctx.receipt.execution_id
    assert ctx.bundles == (ctx.assessment.assessed, ctx.assessment.before, ctx.assessment.initial)
    with pytest.raises(FrozenInstanceError):
        ctx.receipt = None


def test_assessed_phase_rejects_a_receipt_for_another_execution(assessed_correction):
    from autonomous_mixing.core.records.identifiers import ExecutionId

    with pytest.raises(ValueError):
        replace(
            assessed_correction,
            receipt=replace(assessed_correction.receipt, execution_id=ExecutionId("another-execution")),
        )


def test_prepared_phase_rejects_an_action_different_from_the_render_plan(assessed_correction):
    ctx = assessed_correction
    wrong_action = replace(ctx.action, operation=replace(ctx.action.operation, value_db=-7.0))
    wrong_decision = replace(ctx.decision, outcome=replace(ctx.decision.outcome, action=wrong_action))
    with pytest.raises(ValueError):
        replace(ctx, decision=wrong_decision)


def test_unexpected_error_after_commit_finalizes_with_the_latest_accepted_state():
    from test_coordinator import INITIAL_VALUE, SATISFYING, FaultyJournal, Harness, propose

    from autonomous_mixing.core.records.run import CorrectionAttempt
    from autonomous_mixing.core.records.run_trace import TerminalSnapshot
    from autonomous_mixing.core.records.termination import TerminationReason

    error = RuntimeError("after actual acceptance")

    def after_acceptance(event):
        if isinstance(event.payload, CorrectionAttempt):
            raise error

    harness = Harness(
        analysis=(INITIAL_VALUE, SATISFYING),
        decisions=(propose(-3.0),),
        journal=lambda inner: FaultyJournal(inner, append_fault=after_acceptance),
    )
    with pytest.raises(RuntimeError) as caught:
        harness.run()
    assert caught.value is error
    snapshot = harness.real_journal.snapshot().value
    terminal = snapshot.events[-1].payload
    assert isinstance(terminal, TerminalSnapshot)
    assert terminal.authoritative == snapshot.accepted and terminal.authoritative.sequence == 1
    assert terminal.run.termination_reason is TerminationReason.RUNTIME_CONTRACT_FAILED
