"""Increment 5 (ADR-039): subprocess crash and ownership evidence for the durable run store and restart.

Each child process (``restart_child.py``) runs a real scenario against the durable run store, the FL journal and a
persistent fake host, and ends abruptly with ``os._exit`` at a labelled point. The test process then reopens the stored
run and classifies it. This is process-crash evidence only: no power-loss durability is claimed. Local temporary
storage only.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from restart_support import STORE, close, durable, reopen
from test_coordinator import propose

from autonomous_mixing.adapters.journal.sqlite_run import RunStoreBusy, SqliteRunJournal
from autonomous_mixing.core.records.acceptance import AcceptanceAbsent, AcceptanceFound, AcceptanceQuery
from autonomous_mixing.core.records.run_report import RunSuspended
from autonomous_mixing.core.records.run_restart import RunInterrupted
from autonomous_mixing.core.records.run_restart import RunInterruptionCode as C

CHILD = Path(__file__).with_name("restart_child.py")
ROOT = Path(__file__).resolve().parents[2]
CRASHED = 77


def child(directory, phase, scenario="single", crash="none", *, wait=True):
    command = [sys.executable, str(CHILD), str(directory), phase, scenario, crash]
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    if not wait:
        return subprocess.Popen(command, cwd=ROOT, env=env)
    done = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    return done


def outcome(directory):
    files = sorted(directory.glob("child-*.json"))
    return json.loads(files[-1].read_text()) if files else None


def classify(directory):
    """Reopen in this process, as the next process would; returns (result, events before, events after, FL state)."""
    h = durable(directory, decisions=(propose(0.0),), fresh=False, identity_start=900_000)
    try:
        before = len(h.real_journal.snapshot().value.events)
        fl_before = (h.world.controller.control_state().value, len(h.fl_journal._ledger()))
        result = reopen(h)
        after = h.real_journal.snapshot().value
        fl_after = (h.world.controller.control_state().value, len(h.fl_journal._ledger()))
        assert fl_after == fl_before  # classification never touches the FL journal
        writes = list(h.world.host.volumes.writes)
        return result, before, after, writes, h
    finally:
        close(h)


def crashed(directory, phase, scenario, point):
    done = child(directory, phase, scenario, point)
    assert done.returncode == CRASHED, (done.returncode, done.stderr[-2000:])


def suspend_first(directory, scenario="single"):
    done = child(directory, "start", scenario)
    assert done.returncode == 0, done.stderr[-2000:]
    assert outcome(directory) == {"type": "suspended"}


def assert_refused(directory, code):
    result, before, after, writes, _ = classify(directory)
    assert isinstance(result, RunInterrupted) and result.code is code, result
    assert len(after.events) == before + 1 and isinstance(after.events[-1].payload, RunInterrupted)
    return result, writes, after


# --- the main path and duplicates -----------------------------------------------------------------------------------


def test_a_suspension_resumes_in_another_process_and_a_third_finds_it_finished(tmp_path):
    suspend_first(tmp_path)
    done = child(tmp_path, "resume", "single")
    assert done.returncode == 0, done.stderr[-2000:]
    assert outcome(tmp_path) == {"type": "report", "reason": "candidate_criteria_met"}
    done = child(tmp_path, "resume", "single")
    assert done.returncode == 0 and outcome(tmp_path) == {"type": "interrupted", "code": "finished"}
    result, _, after, writes, _ = classify(tmp_path)
    assert result.code is C.FINISHED and len(after.acceptances) == 1
    assert [w for w in writes if w[0] == 2].__len__() == 1  # exactly one fader write across all processes


# --- ownership -------------------------------------------------------------------------------------------------------


def test_a_second_process_cannot_open_the_store_until_the_holder_dies(tmp_path):
    suspend_first(tmp_path)
    holder = child(tmp_path, "hold", wait=False)
    try:
        deadline = time.monotonic() + 60
        while not (tmp_path / "holding").exists():
            assert holder.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)
        with pytest.raises(RunStoreBusy):
            SqliteRunJournal.open(tmp_path / STORE)
    finally:
        holder.kill()
        holder.wait(timeout=30)
    reopened = SqliteRunJournal.open(tmp_path / STORE)  # the operating system released the dead holder's lock
    reopened.close()


# --- crash points: only a recorded suspension resumes; everything else is refused ------------------------------------


@pytest.mark.parametrize(
    "point, code",
    [
        ("after_apply_intent", C.INTERRUPTED_BEFORE_WRITE),
        ("after_fl_reservation", C.INTERRUPTED_WRITE_UNRESOLVED),
        ("after_device_write", C.INTERRUPTED_WRITE_UNRESOLVED),
        ("after_fl_outcome", C.INTERRUPTED_AFTER_WRITE),
        ("after_export_prepare", C.INTERRUPTED_BEFORE_SUSPENSION),
    ],
)
def test_a_crash_before_the_suspension_is_refused_for_operator_recovery(tmp_path, point, code):
    crashed(tmp_path, "start", "single", point)
    assert_refused(tmp_path, code)


def test_a_crash_before_the_first_write_intent_is_refused(tmp_path):
    crashed(tmp_path, "start", "first_decision_crashes", "none")
    assert_refused(tmp_path, C.INTERRUPTED_BEFORE_FIRST_WRITE)


@pytest.mark.parametrize(
    "scenario, point, code",
    [
        ("single", "after_continuation_started", C.CONTINUATION_INTERRUPTED),
        ("single", "before_acceptance_commit", C.INTERRUPTED_BEFORE_ACCEPTANCE_COMMIT),
        ("rejected", "after_restore_reservation", C.INTERRUPTED_RECOVERY),
        ("second_decision_crashes", "none", C.INTERRUPTED_BETWEEN_ROUNDS),
        ("intermediate", "after_second_write_intent", C.INTERRUPTED_BEFORE_WRITE),
        ("intermediate", "after_second_write_observed", C.INTERRUPTED_BEFORE_SUSPENSION),
    ],
)
def test_a_crash_during_or_after_a_continuation_is_refused(tmp_path, scenario, point, code):
    suspend_first(tmp_path)
    crashed(tmp_path, "resume", scenario, point)
    assert_refused(tmp_path, code)


def test_a_crash_after_the_acceptance_commit_keeps_the_commit_authoritative_and_restores_nothing(tmp_path):
    suspend_first(tmp_path)
    crashed(tmp_path, "resume", "single", "after_acceptance_commit")
    result, writes, after = assert_refused(tmp_path, C.INTERRUPTED_AFTER_ACCEPTANCE)
    assert result.authoritative == after.acceptances[-1].accepted and result.authoritative.sequence == 1
    assert len([w for w in writes if w[0] == 2]) == 1  # the write only; nothing restored


@pytest.mark.parametrize("point, found", [("commit_before", False), ("commit_after", True)])
def test_a_crash_at_the_commit_step_is_decided_by_the_committed_rows_only(tmp_path, point, found):
    """Amendment A1, subprocess variant: before COMMIT the acceptance is absent; after it, found."""
    suspend_first(tmp_path)
    crashed(tmp_path, "resume", "single", point)
    code = C.INTERRUPTED_AFTER_ACCEPTANCE if found else C.INTERRUPTED_BEFORE_ACCEPTANCE_COMMIT
    result, _, after = assert_refused(tmp_path, code)
    event = next(e.payload for e in after.events if type(e.payload).__name__ == "AcceptanceEvent")
    journal = SqliteRunJournal.open(tmp_path / STORE)
    try:
        answer = journal.query(AcceptanceQuery(after.run_id, event.acceptance_id))
        assert isinstance(answer, AcceptanceFound if found else AcceptanceAbsent)
    finally:
        journal.close()


def test_a_refused_run_stays_refused_and_is_never_resumed(tmp_path):
    crashed(tmp_path, "start", "single", "after_fl_outcome")
    first, *_ = classify(tmp_path)
    second, *_ = classify(tmp_path)
    assert first.code is second.code is C.INTERRUPTED_AFTER_WRITE
    assert not isinstance(second, RunSuspended)
