"""Increment 5 (ADR-039): restart of a suspended DAW run, interruption classification, durability provenance and the
run-start check, in-process. Subprocess crash and ownership evidence: ``test_run_restart_subprocess.py``.

Real adapters throughout (durable run store, FL journal and controller on a persistent fake host, export handoff,
file analyzer, integrity inspector) over local temporary storage; labelled doubles for decisions and diagnosis.
"""

import math
from dataclasses import replace

import pytest
from daw_continuation_support import ACCEPT, INTERMEDIATE, NO_IMPROVEMENT, SESSION, Continuation, limits
from restart_support import close, durable, facts, reopen
from test_coordinator import no_change, propose
from test_fl_runtime_recovery_controller import live_curve

from autonomous_mixing.core.admission.daw import daw_start_check
from autonomous_mixing.core.loop.restart import classify_run
from autonomous_mixing.core.records.acceptance import (
    AcceptanceCommitted,
    AcceptanceCommitUnknown,
    AcceptanceEvent,
)
from autonomous_mixing.core.records.daw_binding import DawFaderReading
from autonomous_mixing.core.records.daw_control import DawWriteConfirmed
from autonomous_mixing.core.records.daw_correction import DawContinuationStarted, DawCorrectionSuspended
from autonomous_mixing.core.records.daw_recovery import DawLineageQuery
from autonomous_mixing.core.records.run import FinalProjectState
from autonomous_mixing.core.records.run_report import RunReport, RunSuspended
from autonomous_mixing.core.records.run_restart import RunInterrupted
from autonomous_mixing.core.records.run_restart import RunInterruptionCode as C
from autonomous_mixing.core.records.run_trace import (
    DispatchIntent,
    OperationKind,
    RunJournalSnapshot,
    StoreDurability,
    TerminalSnapshot,
    TraceNotAppended,
)
from autonomous_mixing.core.records.termination import TerminationReason as R

LOCAL = "local_sqlite_process_crash"


class Wrapped:
    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)


class UnknownCommit(Wrapped):
    """The commit is written, but its acknowledgement is lost (the query then finds it)."""

    def commit(self, event):
        self.inner.commit(event)
        return AcceptanceCommitUnknown(event.acceptance_id, "labelled lost acknowledgement")


@pytest.fixture
def made():
    harnesses = []
    yield harnesses
    for h in harnesses:
        try:
            close(h)
        except Exception:
            pass


def start(made, directory, **kwargs):
    made.append(durable(directory, fresh=True, **kwargs))
    return made[-1]


def restarted(made, directory, **kwargs):
    made.append(durable(directory, fresh=False, identity_start=1000, **kwargs))
    return made[-1]


def applies(h):
    """Distinct apply commands the FL journal ever reserved (each one at most one device write)."""
    return [cid for cid, _ in h.fl_journal._entries() if cid.startswith("apply-")]


# --- the main path: suspend, end the process, restart, resume ---------------------------------------------------


def test_a_suspended_run_resumes_after_a_restart_without_repeating_its_write(made, tmp_path):
    first = start(made, tmp_path, decisions=(propose(0.0),))
    suspended = first.start()
    assert isinstance(suspended, RunSuspended) and suspended.durability == LOCAL
    usage = suspended.checkpoint_event.usage
    close(first)
    second = restarted(made, tmp_path, decisions=(propose(0.0),))
    reloaded = reopen(second)
    assert isinstance(reloaded, RunSuspended) and reloaded.durability == LOCAL
    assert reloaded.checkpoint_event == suspended.checkpoint_event and reloaded.request == suspended.request
    report = second.resume(reloaded, second.operator_export(reloaded, ACCEPT))
    assert isinstance(report, RunReport) and report.durability == LOCAL
    assert report.final_run.termination_reason is R.CANDIDATE_CRITERIA_MET
    assert len(applies(second)) == 1 and len(second.world.host.volumes.writes) == 1  # one write in both processes
    assert len(report.trace.acceptances) == 1
    assert report.final_run.usage.decision_calls == usage.decision_calls and (
        report.final_run.usage.daw_writes == usage.daw_writes == 1)  # fmt: skip
    assert report.final_run.usage.elapsed_seconds >= usage.elapsed_seconds
    again = reopen(second)
    assert isinstance(again, RunInterrupted) and again.code is C.FINISHED


def test_the_rebuilt_progress_equals_the_in_process_rebuild(made, tmp_path, monkeypatch):
    """The progress ``resume_daw`` rebuilds from the reloaded snapshot equals the one from the original snapshot."""
    import autonomous_mixing.core.loop.coordinator as coordinator

    captured = []
    real = coordinator._complete

    def capture(progress, step, **kwargs):
        captured.append(progress)
        raise RuntimeError("stop after the rebuild")

    first = start(made, tmp_path, decisions=(propose(0.0),))
    suspended = first.start()
    monkeypatch.setattr(coordinator, "_complete", capture)
    manifest = first.operator_export(suspended, ACCEPT)
    with pytest.raises(RuntimeError):
        first.resume(suspended, manifest)
    close(first)
    second = restarted(made, tmp_path, decisions=(propose(0.0),))
    reloaded = reopen(second)
    with pytest.raises(RuntimeError):
        second.resume(reloaded, manifest)
    monkeypatch.setattr(coordinator, "_complete", real)
    a, b = captured
    differing = [f for f in a.__dataclass_fields__ if f not in ("first", "last", "segment_start")
                 and getattr(a, f) != getattr(b, f)]  # fmt: skip
    assert differing == []  # only the new process's clock readings differ


# --- durability provenance (amendment A3) --------------------------------------------------------------------------


def test_in_memory_results_keep_their_non_durable_label(tmp_path):
    h = Continuation(tmp_path, decisions=(propose(0.0),))
    try:
        suspended = h.start()
        assert suspended.durability == "non_durable"
        report = h.resume(suspended, h.operator_export(suspended, ACCEPT))
        assert report.durability == "non_durable" and report.trace.durability is StoreDurability.NON_DURABLE
    finally:
        h.close()


class RefuseTerminal(Wrapped):
    def append(self, event):
        if isinstance(event.payload, TerminalSnapshot):
            return TraceNotAppended(event.event_id, "labelled refusal")
        return self.inner.append(event)


def test_durability_is_independent_of_whether_every_event_was_acknowledged(made, tmp_path):
    h = start(made, tmp_path, decisions=(propose(0.0),), journal=RefuseTerminal)
    suspended = h.start()
    report = h.resume(suspended, h.operator_export(suspended, ACCEPT))
    assert report.durability == LOCAL and report.unacknowledged_events == (report.terminal_event,)


class OtherDurability(Wrapped):
    def snapshot(self):
        result = self.inner.snapshot()
        return type(result)(replace(result.value, durability=StoreDurability.NON_DURABLE))


def test_a_snapshot_whose_durability_differs_from_its_journal_is_refused(made, tmp_path):
    h = start(made, tmp_path, decisions=(propose(0.0),), journal=OtherDurability)
    with pytest.raises(ValueError, match="durability"):
        h.start()


# --- the clarification: a recorded AcceptanceFound is trace evidence of acceptance ----------------------------------


def test_a_found_acceptance_then_a_second_suspension_resumes_after_a_restart(made, tmp_path):
    """Unknown commit, acceptance found, intermediate candidate accepted, second suspension, restart, resume."""
    # four recovery slots: the authority query already used one, and the next write must reserve three (ADR-038)
    first = start(
        made,
        tmp_path,
        decisions=(propose(0.0), propose(-0.5)),
        journal=UnknownCommit,
        budget=limits(max_recovery_operations=4),
    )
    suspended = first.start()
    second_suspension = first.resume(suspended, first.operator_export(suspended, INTERMEDIATE))
    assert isinstance(second_suspension, RunSuspended), second_suspension
    trace = second_suspension.trace
    assert not [e for e in trace.events if isinstance(e.payload, AcceptanceCommitted)]  # only the Found answer
    assert len(trace.acceptances) == 1
    close(first)
    second = restarted(made, tmp_path, decisions=(propose(-0.5),), budget=limits(max_recovery_operations=4))
    reloaded = reopen(second)
    assert isinstance(reloaded, RunSuspended), reloaded
    assert reloaded.checkpoint_event == second_suspension.checkpoint_event
    report = second.resume(reloaded, second.operator_export(reloaded, ACCEPT))
    assert isinstance(report, RunReport) and report.final_run.final_project_state is (
        FinalProjectState.ACCEPTED_EDITS_RETAINED)  # fmt: skip
    assert len(applies(second)) == 2 and len(report.trace.acceptances) == 2


# --- classification: precedence, overlaps and prefix coverage (amendment A2) --------------------------------------


def scenario(directory, decisions, masters, *, journal=None, budget=None, setup=None):
    """Run a real in-memory DAW scenario; returns (final trace, facts captured at each suspension)."""
    h = Continuation(directory, decisions=decisions, journal=journal, budget=budget)
    if setup:
        setup(h)
    captured = {}
    try:
        result = h.start()
        for master in masters:
            if not isinstance(result, RunSuspended):
                break
            captured[len(result.trace.events)] = facts(h)
            result = h.resume(result, h.operator_export(result, master))
        if isinstance(result, RunSuspended):
            captured[len(result.trace.events)] = facts(h)
        return h.real_journal.snapshot().value, captured
    finally:
        h.close()


SCENARIOS = {
    "accepted": dict(decisions=(propose(0.0),), masters=(ACCEPT,)),
    "rejected_then_no_change": dict(decisions=(propose(0.0), no_change), masters=(NO_IMPROVEMENT,)),
    "intermediate_then_accepted": dict(decisions=(propose(0.0), propose(-0.5)), masters=(INTERMEDIATE, ACCEPT)),
    "found_after_unknown": dict(decisions=(propose(0.0),), masters=(ACCEPT,), journal=UnknownCommit),
    "off_target": dict(
        decisions=(propose(0.0), no_change),
        masters=(),
        setup=lambda h: setattr(h.world.host, "curve", lambda v: live_curve(v) + (0.0 if v == 0.76 else 0.2)),
    ),
}


def table_at(trace: RunJournalSnapshot, k: int, *, ahead: bool):
    """The acceptance table at prefix k: commits recorded (or found) before k; ``ahead`` adds the commit of an
    acceptance event at the prefix's end (a crash right after a commit, before its record)."""
    events = trace.events[:k]
    ids = set()
    for e in events:
        if isinstance(e.payload, AcceptanceCommitted):
            ids.add(e.payload.event.acceptance_id)
        outcome = getattr(e.payload, "outcome", None)
        if outcome is not None and type(outcome).__name__ == "AcceptanceFound":
            ids.add(outcome.event.acceptance_id)
    if ahead and events and isinstance(events[-1].payload, AcceptanceEvent):
        ids.add(events[-1].payload.acceptance_id)
    return tuple(c for c in trace.acceptances if c.event.acceptance_id in ids)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_prefix_of_real_traces_is_classified_by_the_precedence(tmp_path, name):
    spec = dict(SCENARIOS[name])
    trace, captured = scenario(tmp_path, spec.pop("decisions"), spec.pop("masters"), **spec)
    full = len(trace.events)
    seen = set()
    for k in range(0, full + 1):
        for ahead in (False, True):
            acceptances = table_at(trace, k, ahead=ahead)
            snapshot = RunJournalSnapshot(trace.run_id, trace.initial, trace.events[:k], acceptances)
            result = classify_run(snapshot, None)
            code = result.code if isinstance(result, RunInterrupted) else None
            assert code is not C.UNCLASSIFIED, (name, k)
            assert (code is C.FINISHED) == (k == full and isinstance(trace.events[-1].payload, TerminalSnapshot))
            traced = len(table_at(trace, k, ahead=False))
            if len(acceptances) > traced:
                assert code is C.INTERRUPTED_AFTER_ACCEPTANCE, (name, k)
                assert result.authoritative == acceptances[-1].accepted
            if k and isinstance(trace.events[k - 1].payload, DawCorrectionSuspended) and len(acceptances) == traced:
                assert code is C.SUSPENDED_DAW_STATE_UNEXPLAINED  # without FL facts, never resumable
                if k in captured:
                    assert isinstance(classify_run(snapshot, captured[k]), RunSuspended), (name, k)
            seen.add(code)
    assert C.UNCLASSIFIED not in seen


def test_specific_effects_take_precedence_over_an_interrupted_continuation(tmp_path):
    trace, _ = scenario(tmp_path / "a", (propose(0.0), propose(-0.5)), (INTERMEDIATE, ACCEPT))
    events = trace.events
    started = next(i for i, e in enumerate(events) if isinstance(e.payload, DawContinuationStarted))
    accept = next(i for i, e in enumerate(events) if isinstance(e.payload, AcceptanceEvent))
    second_write = [i for i, e in enumerate(events)
                    if isinstance(e.payload, DispatchIntent) and e.payload.operation is OperationKind.DAW_APPLY][1]  # fmt: skip
    assert started < accept < second_write

    def code(k, acceptances=None):
        return classify_run(RunJournalSnapshot(trace.run_id, trace.initial, events[:k],
                                               table_at(trace, k, ahead=False) if acceptances is None else acceptances),
                            None).code  # fmt: skip

    assert code(accept + 1, (trace.acceptances[0],)) is C.INTERRUPTED_AFTER_ACCEPTANCE  # row 2 over row 8
    assert code(accept + 1) is C.INTERRUPTED_BEFORE_ACCEPTANCE_COMMIT  # row 5 over row 8
    assert code(second_write + 1) is C.INTERRUPTED_WRITE_UNRESOLVED  # row 6 (no FL facts) over row 8
    assert code(started + 1) is C.CONTINUATION_INTERRUPTED
    finished = next(i for i, e in enumerate(events) if type(e.payload).__name__ == "DawContinuationFinished")
    assert code(finished + 2) is C.INTERRUPTED_BETWEEN_ROUNDS  # after a continuation, before the next write
    assert code(2) is C.INTERRUPTED_BEFORE_FIRST_WRITE

    rejected, _ = scenario(tmp_path / "b", (propose(0.0), no_change), (NO_IMPROVEMENT,))
    lineage_query = next(i for i, e in enumerate(rejected.events) if isinstance(e.payload, DispatchIntent)
                         and isinstance(e.payload.request, DawLineageQuery))  # fmt: skip
    snapshot = RunJournalSnapshot(rejected.run_id, rejected.initial, rejected.events[: lineage_query + 1], ())
    assert classify_run(snapshot, None).code is C.INTERRUPTED_RECOVERY  # row 4 over row 8


# --- the run-start check (G14) -------------------------------------------------------------------------------------


def test_an_orphaned_confirmed_write_refuses_the_next_runs_start(tmp_path):
    h = Continuation(tmp_path, decisions=(propose(0.0),))
    try:
        orphan = h.world.apply("apply_orphan_01")  # a write no run recorded (for example a crashed run)
        assert isinstance(orphan, DawWriteConfirmed)
        report = h.start()
        assert isinstance(report, RunReport) and report.final_run.termination_reason is R.DAW_CONTINUITY_FAILED
        assert "apply_orphan_01" in report.final_run.termination_explanation
        assert not h.decider.situations and len(h.sent("set_gain_db")) == 1  # only the orphan's own write
        assert h.fl_journal.control_state().unresolved == ()  # nothing adopted, restored or resolved
    finally:
        h.close()


def test_a_blocked_conversion_refuses_the_start(tmp_path):
    h = Continuation(tmp_path, decisions=(propose(0.0),))
    try:
        h.world.host.curve = lambda v: live_curve(v) + (0.0 if v == 0.76 else 0.2)
        h.world.apply("apply_orphan_02")  # off target: the conversion is blocked
        report = h.start()
        assert report.final_run.termination_reason is R.DAW_CONVERSION_BLOCKED and not h.decider.situations
    finally:
        h.close()


def test_a_restored_state_explains_the_next_start_exactly_and_one_ulp_does_not(tmp_path):
    h = Continuation(tmp_path, decisions=(propose(0.0), no_change))
    try:
        suspended = h.start()
        h.resume(suspended, h.operator_export(suspended, NO_IMPROVEMENT))  # rolled back: exactly restored
        latest = h.world.controller.latest_anchor().value
        control = h.world.controller.control_state().value
        assert latest.anchor is not None and latest.anchor.reading == SESSION.binding.baseline_fader
        confirmed = SESSION.binding.baseline_fader
        assert daw_start_check(control, latest, binding=SESSION.binding, profile=SESSION.profile,
                               confirmed=confirmed) is None  # fmt: skip
        ulp = DawFaderReading(math.nextafter(confirmed.normalized, 1.0), confirmed.db)
        refused = daw_start_check(control, latest, binding=SESSION.binding, profile=SESSION.profile, confirmed=ulp)
        assert refused is not None and refused.reason is R.DAW_CONTINUITY_FAILED
        second = Continuation(tmp_path / "next", decisions=(no_change,))
        second.fl_journal.close()
        second.fl_journal, second.world = h.fl_journal, h.world
        report = second.start()  # a new run over the same FL journal starts normally
        assert report.final_run.termination_reason is R.NO_CHANGE_PROPOSED
    finally:
        h.close()


def test_an_unexplained_fl_recovery_charge_under_this_runs_recovery_id_refuses_the_resume(tmp_path):
    from fl_recovery_support import grant

    from autonomous_mixing.core.loop.daw_recovery import recovery_ids
    from autonomous_mixing.core.records.daw_recovery import DawRecoveryCharge

    trace, captured = scenario(tmp_path, (propose(0.0),), ())
    found = captured[len(trace.events)]
    assert isinstance(classify_run(trace, found), RunSuspended)
    apply = trace.events[-1].payload.apply
    restore_id = recovery_ids(apply.command_id[len("apply-") :])["restore"]
    charge = DawRecoveryCharge("rauth-orphan-1", restore_id, 99, grant())
    unexplained = replace(found, control_state=replace(found.control_state, recovery_charges=(charge,)))
    result = classify_run(trace, unexplained)
    assert isinstance(result, RunInterrupted) and result.code is C.SUSPENDED_DAW_STATE_UNEXPLAINED
    assert "charge" in result.explanation


@pytest.mark.parametrize("after", [False, True])
def test_an_unexpected_exception_at_the_durable_commit_never_reports_an_uncommitted_acceptance(made, tmp_path, after):
    """Review fix 2026-11-20 at coordinator level: the exception propagates, the run ends ACCEPTANCE_COMMIT_UNCERTAIN,
    and the store, reopened and verified, answers only from committed rows."""
    from autonomous_mixing.core.records.acceptance import AcceptanceAbsent, AcceptanceFound, AcceptanceQuery

    h = start(made, tmp_path, decisions=(propose(0.0),))
    suspended = h.start()
    store = h.real_journal
    real, real_commit = store._commit, store.commit
    committing = []

    def commit(event):  # only the acceptance commit's own COMMIT step fails
        committing.append(1)
        try:
            return real_commit(event)
        finally:
            committing.pop()

    def failing():
        if committing:
            if after:
                real()
            raise RuntimeError("labelled unexpected failure")
        return real()

    store._commit, store.commit = failing, commit
    with pytest.raises(RuntimeError, match="labelled unexpected"):
        h.resume(suspended, h.operator_export(suspended, ACCEPT))
    snapshot = store.snapshot().value
    terminal = snapshot.events[-1].payload
    assert isinstance(terminal, TerminalSnapshot) and terminal.run.termination_reason is R.ACCEPTANCE_COMMIT_UNCERTAIN
    event = next(e.payload for e in snapshot.events if isinstance(e.payload, AcceptanceEvent))
    answer = store.query(AcceptanceQuery(snapshot.run_id, event.acceptance_id))
    assert isinstance(answer, AcceptanceFound if after else AcceptanceAbsent)
    assert len(snapshot.acceptances) == (1 if after else 0)
