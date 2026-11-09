"""Terminal/completion contracts using labelled immutable metadata fixtures."""

import dataclasses
from datetime import timedelta

import pytest
from test_run_recording_contracts import (
    INITIAL,
    RUN_REQUEST,
    UTC_NOW,
    make_acceptance,
    make_event,
    make_run,
    make_snapshot,
    make_terminal,
)

from autonomous_mixing.core.records.budgets import BudgetUsage
from autonomous_mixing.core.records.coordinator_runtime import ClockReading
from autonomous_mixing.core.records.identifiers import RunEventId, RunId
from autonomous_mixing.core.records.run import FinalProjectState, RunOutcome
from autonomous_mixing.core.records.run_report import RunReport
from autonomous_mixing.core.records.run_trace import (
    TerminalSnapshot,
    TraceAppended,
    TraceAppendUnknown,
    TraceNotAppended,
)
from autonomous_mixing.core.records.termination import TerminationReason


def report_fields(**overrides):
    terminal = make_terminal()
    values = dict(
        request=RUN_REQUEST,
        trace=make_snapshot((terminal,)),
        terminal_event=terminal,
        terminal_append=TraceAppended(terminal),
        completion=ClockReading(101.0, UTC_NOW, False),
        final_run=dataclasses.replace(terminal.payload.run, usage=BudgetUsage(0, 0, 0, 0, 1.0)),
        authoritative=INITIAL,
        verified=INITIAL,
        unacknowledged_events=(),
    )
    values.update(overrides)
    return values


def test_successful_completion_preserves_records_and_is_non_durable():
    values = report_fields()
    report = RunReport(**values)
    assert report.durability == "non_durable"
    for name, value in values.items():
        assert getattr(report, name) is value
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.final_run = make_run()


def test_report_has_the_exact_required_fields():
    assert tuple(field.name for field in dataclasses.fields(RunReport)) == (
        "request",
        "trace",
        "terminal_event",
        "terminal_append",
        "completion",
        "final_run",
        "authoritative",
        "verified",
        "unacknowledged_events",
    )
    assert all(
        field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING
        for field in dataclasses.fields(RunReport)
    )


@pytest.mark.parametrize("response_type", [TraceNotAppended, TraceAppendUnknown])
def test_failed_terminal_recording_preserves_committed_candidate(response_type):
    committed = make_acceptance()
    reference = committed.accepted
    run = make_run(TerminationReason.CANDIDATE_CRITERIA_MET, final=FinalProjectState.ACCEPTED_EDITS_RETAINED)
    terminal = make_event(TerminalSnapshot(run, reference, reference))
    final_run = dataclasses.replace(
        run,
        outcome=RunOutcome.FAILED,
        termination_reason=TerminationReason.RECORDING_FAILED,
        termination_explanation="terminal acknowledgement failed",
    )
    report = RunReport(
        **report_fields(
            trace=make_snapshot(acceptances=(committed,)),
            terminal_event=terminal,
            terminal_append=response_type(terminal.event_id, "not acknowledged"),
            final_run=final_run,
            authoritative=reference,
            verified=reference,
        )
    )
    assert report.authoritative is reference and report.verified is reference
    assert report.final_run.outcome is RunOutcome.FAILED


@pytest.mark.parametrize(
    "field,value",
    [
        ("request", None),
        ("trace", ()),
        ("terminal_event", "terminal"),
        ("terminal_append", None),
        ("completion", UTC_NOW),
        ("final_run", None),
        ("authoritative", "state"),
        ("verified", "state"),
        ("unacknowledged_events", []),
    ],
)
def test_report_types_are_checked(field, value):
    with pytest.raises(ValueError):
        RunReport(**report_fields(**{field: value}))


@pytest.mark.parametrize(
    "change",
    [
        {"run_id": RunId("another")},
        {"initial_revision": INITIAL.state.revision.__class__("other")},
        {
            "configuration": dataclasses.replace(
                RUN_REQUEST.configuration,
                budget_limits=dataclasses.replace(RUN_REQUEST.configuration.budget_limits, max_attempts=99),
            )
        },
    ],
)
def test_final_run_is_bound_to_the_request(change):
    values = report_fields()
    values["final_run"] = dataclasses.replace(values["final_run"], **change)
    with pytest.raises(ValueError):
        RunReport(**values)


def test_terminal_snapshot_and_append_are_bound_exactly():
    values = report_fields()
    changed = dataclasses.replace(values["terminal_event"], created_at=UTC_NOW + timedelta(seconds=1))
    with pytest.raises(ValueError):
        RunReport(**report_fields(terminal_append=TraceAppended(changed)))
    with pytest.raises(ValueError):
        RunReport(**report_fields(terminal_append=TraceAppendUnknown(RunEventId("another"), "unknown")))
    with pytest.raises(ValueError):
        RunReport(**report_fields(terminal_event=make_event(RUN_REQUEST)))


def test_acknowledged_terminal_event_must_occur_in_trace():
    with pytest.raises(ValueError):
        RunReport(**report_fields(trace=make_snapshot()))


def test_unknown_append_does_not_establish_presence_or_absence():
    values = report_fields()
    terminal = values["terminal_event"]
    failed = dataclasses.replace(
        values["final_run"], outcome=RunOutcome.FAILED, termination_reason=TerminationReason.RECORDING_FAILED
    )
    for trace in (make_snapshot(), make_snapshot((terminal,))):
        RunReport(
            **report_fields(
                trace=trace,
                terminal_append=TraceAppendUnknown(terminal.event_id, "unknown"),
                final_run=failed,
                unacknowledged_events=(terminal,),
            )
        )


def test_nonacknowledged_terminal_write_cannot_succeed_or_hide_recording_failure():
    terminal = make_terminal()
    for final_run in (make_run(), make_run(TerminationReason.ELAPSED_TIME_LIMIT, outcome=RunOutcome.LIMIT_REACHED)):
        with pytest.raises(ValueError):
            RunReport(
                **report_fields(terminal_append=TraceNotAppended(terminal.event_id, "failed"), final_run=final_run)
            )


def test_higher_priority_unknown_authority_is_preserved_after_append_failure():
    final_run = make_run(
        TerminationReason.ACCEPTANCE_COMMIT_UNCERTAIN, outcome=RunOutcome.FAILED, final=FinalProjectState.UNKNOWN
    )
    terminal = make_event(TerminalSnapshot(final_run, None, None))
    report = RunReport(
        **report_fields(
            trace=make_snapshot(),
            terminal_event=terminal,
            terminal_append=TraceNotAppended(terminal.event_id, "failed"),
            final_run=final_run,
            authoritative=None,
            verified=None,
        )
    )
    assert report.final_run.termination_reason is TerminationReason.ACCEPTANCE_COMMIT_UNCERTAIN


def test_elapsed_progress_and_counts_are_observed_after_finalization():
    values = report_fields()
    terminal_usage = BudgetUsage(1, 1, 1, 0, 5.0)
    terminal_run = dataclasses.replace(values["terminal_event"].payload.run, usage=terminal_usage)
    terminal = make_event(TerminalSnapshot(terminal_run, INITIAL, INITIAL), usage=terminal_usage)
    base = report_fields(
        trace=make_snapshot((terminal,)), terminal_event=terminal, terminal_append=TraceAppended(terminal)
    )
    for usage in (BudgetUsage(1, 1, 1, 0, 4.0), BudgetUsage(2, 1, 1, 0, 6.0)):
        with pytest.raises(ValueError):
            RunReport(**{**base, "final_run": dataclasses.replace(terminal_run, usage=usage)})
    report = RunReport(
        **{
            **base,
            "final_run": dataclasses.replace(terminal_run, usage=BudgetUsage(1, 1, 1, 0, 6.0)),
            "completion": ClockReading(106.0, UTC_NOW - timedelta(seconds=5), False),
        }
    )
    assert report.final_run.usage.elapsed_seconds == 6.0


def test_success_is_blocked_at_deadline_even_after_acknowledgement():
    values = report_fields()
    with pytest.raises(ValueError):
        RunReport(
            **{
                **values,
                "final_run": dataclasses.replace(
                    values["final_run"],
                    usage=BudgetUsage(0, 0, 0, 0, RUN_REQUEST.configuration.budget_limits.max_elapsed_seconds),
                ),
            }
        )


def test_known_authority_must_match_acceptance_prefix():
    committed = make_acceptance()
    with pytest.raises(ValueError):
        RunReport(**report_fields(trace=make_snapshot((make_terminal(),), (committed,))))


def test_unacknowledged_records_are_typed_unique_and_cannot_conflict():
    terminal = make_terminal()
    for attempts in (
        (terminal, terminal),
        (dataclasses.replace(terminal, created_at=UTC_NOW + timedelta(seconds=1)),),
        ("event",),
    ):
        with pytest.raises(ValueError):
            RunReport(**report_fields(unacknowledged_events=attempts))


def test_confirmed_unwritten_terminal_event_cannot_be_in_committed_trace():
    terminal = make_terminal()
    failed = make_run(TerminationReason.RECORDING_FAILED, outcome=RunOutcome.FAILED)
    with pytest.raises(ValueError):
        RunReport(**report_fields(terminal_append=TraceNotAppended(terminal.event_id, "not stored"), final_run=failed))


def test_report_request_cannot_rewrite_recorded_root_bindings():
    from autonomous_mixing.core.records.integrity import ArtifactRootBinding

    start = make_event(RUN_REQUEST)
    terminal = dataclasses.replace(make_terminal(), sequence=2, event_id=RunEventId("terminal"))
    modified = dataclasses.replace(
        RUN_REQUEST,
        root_bindings=tuple(
            ArtifactRootBinding(binding.artifact_id, "different-root") for binding in RUN_REQUEST.root_bindings
        ),
    )
    with pytest.raises(ValueError):
        RunReport(
            **report_fields(
                request=modified,
                trace=make_snapshot((start, terminal)),
                terminal_event=terminal,
                terminal_append=TraceAppended(terminal),
            )
        )


@pytest.mark.parametrize(
    "reason,final",
    [
        (TerminationReason.ACCEPTANCE_COMMIT_UNCERTAIN, FinalProjectState.UNKNOWN),
        (TerminationReason.RENDER_FAILED, FinalProjectState.UNCHANGED),
    ],
)
def test_finalization_cannot_upgrade_a_failed_terminal_snapshot(reason, final):
    reference = None if final is FinalProjectState.UNKNOWN else INITIAL
    failed = make_run(reason, outcome=RunOutcome.FAILED, final=final)
    terminal = make_event(TerminalSnapshot(failed, reference, reference))
    with pytest.raises(ValueError):
        RunReport(
            **report_fields(
                trace=make_snapshot((terminal,)), terminal_event=terminal, terminal_append=TraceAppended(terminal)
            )
        )


def test_finalization_cannot_change_known_authority_or_integrity():
    final = make_run(TerminationReason.RECORDING_FAILED, outcome=RunOutcome.FAILED, final=FinalProjectState.UNKNOWN)
    with pytest.raises(ValueError):
        RunReport(
            **report_fields(
                final_run=final,
                authoritative=None,
                verified=None,
                terminal_append=TraceAppendUnknown(RunEventId("event-1"), "unknown"),
            )
        )


def test_an_acknowledged_completion_cannot_invent_a_new_abstention():
    abstained = make_run(TerminationReason.NO_CHANGE_PROPOSED, outcome=RunOutcome.ABSTAINED)
    with pytest.raises(ValueError):
        RunReport(**report_fields(final_run=abstained))


def test_success_can_downgrade_to_elapsed_limit_after_finalization():
    usage = BudgetUsage(0, 0, 0, 0, RUN_REQUEST.configuration.budget_limits.max_elapsed_seconds)
    final = make_run(TerminationReason.ELAPSED_TIME_LIMIT, outcome=RunOutcome.LIMIT_REACHED, usage=usage)
    assert RunReport(**report_fields(final_run=final)).final_run is final


def test_failed_stage_reason_keeps_priority_over_a_finalization_time_limit():
    failed = make_run(TerminationReason.RENDER_FAILED, outcome=RunOutcome.FAILED)
    terminal = make_event(TerminalSnapshot(failed, INITIAL, INITIAL))
    usage = BudgetUsage(0, 0, 0, 0, RUN_REQUEST.configuration.budget_limits.max_elapsed_seconds)
    limited = make_run(TerminationReason.ELAPSED_TIME_LIMIT, outcome=RunOutcome.LIMIT_REACHED, usage=usage)
    with pytest.raises(ValueError):
        RunReport(
            **report_fields(
                trace=make_snapshot((terminal,)),
                terminal_event=terminal,
                terminal_append=TraceAppended(terminal),
                final_run=limited,
            )
        )
