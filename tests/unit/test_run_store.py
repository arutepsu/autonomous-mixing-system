"""Increment 5 (ADR-039): the exact record codec and the durable SQLite run journal (in-process contracts).

Local temporary storage only (network and synced folders are unsupported). Subprocess crash and ownership evidence
lives in ``test_run_restart.py``. No power-loss claim is made.
"""

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from daw_continuation_support import ACCEPT, NO_IMPROVEMENT, Continuation
from test_coordinator import no_change, propose
from test_run_recording_contracts import INITIAL, RUN_REQUEST, make_acceptance, make_event, make_intent

from autonomous_mixing.adapters.journal import sqlite_run
from autonomous_mixing.adapters.journal.record_codec import decode, dumps, encode, loads
from autonomous_mixing.adapters.journal.sqlite_run import RunStoreBusy, RunStoreError, SqliteRunJournal
from autonomous_mixing.core.records.acceptance import (
    AcceptanceAbsent,
    AcceptanceAuthorityUnknown,
    AcceptanceCommitted,
    AcceptanceCommitUnknown,
    AcceptanceFound,
    AcceptanceNotCommitted,
    AcceptanceQuery,
)
from autonomous_mixing.core.records.daw_binding import DawFaderReading
from autonomous_mixing.core.records.identifiers import AcceptanceId, RunId
from autonomous_mixing.core.records.results import PortFailure, Success
from autonomous_mixing.core.records.run_trace import (
    OperationKind,
    StoreDurability,
    TraceAppended,
    TraceAppendUnknown,
    TraceNotAppended,
)


@pytest.fixture
def store(tmp_path):
    made = []

    def make(name="run.sqlite", run_id=RUN_REQUEST.run_id, initial=INITIAL):
        made.append(SqliteRunJournal.create(tmp_path / name, run_id, initial))
        return made[-1]

    yield make
    for item in made:
        item.close()


def first_events():
    """A valid two-event prefix: the run request and one decision intent."""
    return (make_event(RUN_REQUEST, 1), make_event(make_intent(OperationKind.DECIDE), 2))


# --- the codec -------------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_traces(tmp_path_factory):
    """Real DAW traces: accepted, and rejected then rolled back (Increments 2-4)."""
    traces = []
    for master, decisions in ((ACCEPT, (propose(0.0),)), (NO_IMPROVEMENT, (propose(0.0), no_change))):
        h = Continuation(tmp_path_factory.mktemp("trace"), decisions=decisions)
        suspended = h.start()
        report = h.resume(suspended, h.operator_export(suspended, master))
        traces.append(report.trace)
        h.close()
    return traces


def test_every_record_of_real_traces_round_trips_exactly(real_traces):
    count = 0
    for trace in real_traces:
        for item in (*trace.events, *trace.acceptances, trace.initial, trace):
            text = dumps(encode(item))
            assert decode(loads(text)) == item and type(decode(loads(text))) is type(item)
            assert dumps(encode(decode(loads(text)))) == text  # canonical: a second round is byte-identical
            count += 1
    assert count > 40


def test_values_round_trip_exactly_and_lossy_values_are_refused():
    for value in (-0.0, 0.0, 1e-300, 0.1 + 0.2, math.nextafter(1.0, 2.0)):
        back = decode(loads(dumps(encode(value))))
        assert back == value and math.copysign(1.0, back) == math.copysign(1.0, value)
    moment = datetime(2026, 11, 20, 12, 0, 0, 123456, tzinfo=timezone.utc)
    assert decode(loads(dumps(encode(moment)))) == moment
    offset = datetime(2026, 11, 20, 13, 0, tzinfo=timezone(timedelta(hours=1)))
    assert decode(loads(dumps(encode(offset)))).utcoffset() == timedelta(hours=1)
    assert decode(loads(dumps(encode(frozenset({"b", "a"}))))) == frozenset({"a", "b"})
    assert dumps(encode(frozenset({"b", "a"}))) == dumps(encode(frozenset({"a", "b"})))
    reading = DawFaderReading(0.76, -0.0)
    assert math.copysign(1.0, decode(loads(dumps(encode(reading)))).db) == -1.0
    for bad in (datetime(2026, 11, 20), float("nan"), float("inf"), [1], {"a": 1}, object()):
        with pytest.raises(ValueError):
            encode(bad)
    with pytest.raises(ValueError):
        decode({"$t": "nowhere.Unknown", "f": {}})
    with pytest.raises(ValueError):
        decode({"$e": "termination.TerminationReason", "v": "no_such_reason"})


def test_decoding_reruns_record_validation():
    tampered = encode(DawFaderReading(0.76, -1.0))
    tampered["f"]["normalized"] = float("1e9")
    with pytest.raises(ValueError):
        decode(tampered)


# --- the store: contracts shared with the in-memory journal ---------------------------------------------------------


def test_a_new_store_is_empty_durable_and_verifies_its_settings(store):
    journal = store()
    assert journal.store_durability is StoreDurability.LOCAL_SQLITE_PROCESS_CRASH
    assert journal.settings == {"journal_mode": "delete", "synchronous": 3, "locking_mode": "exclusive"}
    snapshot = journal.snapshot()
    assert isinstance(snapshot, Success) and snapshot.value.events == () and snapshot.value.acceptances == ()
    assert snapshot.value.durability is StoreDurability.LOCAL_SQLITE_PROCESS_CRASH


def test_appends_survive_reopening_and_duplicates_return_the_original_acknowledgement(store, tmp_path):
    journal = store()
    events = first_events()
    acks = [journal.append(e) for e in events]
    assert all(isinstance(a, TraceAppended) for a in acks)
    assert journal.append(events[0]) == acks[0]  # exact duplicate
    conflict = make_event(make_intent(OperationKind.DECIDE), 1, event_id=events[0].event_id.value)
    assert isinstance(journal.append(conflict), TraceNotAppended)
    assert isinstance(journal.append(make_event(make_intent(OperationKind.DECIDE), 5)), TraceNotAppended)  # gap
    journal.close()
    reopened = SqliteRunJournal.open(tmp_path / "run.sqlite")
    try:
        assert reopened.snapshot().value.events == events and reopened.append(events[1]) == acks[1]
        assert (reopened.run_id, reopened.initial) == (RUN_REQUEST.run_id, INITIAL)
    finally:
        reopened.close()


def test_acceptances_survive_reopening_with_duplicate_conflict_and_stale_rules(store, tmp_path):
    journal = store()
    for e in first_events():
        journal.append(e)
    event = make_acceptance().event
    committed = journal.commit(event)
    assert isinstance(committed, AcceptanceCommitted)
    assert journal.commit(event) == committed
    assert isinstance(journal.commit(make_acceptance(number=2).event), AcceptanceNotCommitted)  # stale expected head
    journal.close()
    reopened = SqliteRunJournal.open(tmp_path / "run.sqlite")
    try:
        assert reopened.commit(event) == committed
        found = reopened.query(AcceptanceQuery(RUN_REQUEST.run_id, event.acceptance_id))
        assert found == AcceptanceFound(committed.event, committed.accepted)
        assert isinstance(reopened.query(AcceptanceQuery(RUN_REQUEST.run_id, AcceptanceId("other"))), AcceptanceAbsent)
        assert isinstance(reopened.query(AcceptanceQuery(RunId("other-run"), event.acceptance_id)),
                          AcceptanceAuthorityUnknown)  # fmt: skip
    finally:
        reopened.close()


def test_another_run_or_initial_reference_and_an_existing_file_are_refused(store, tmp_path):
    store().close()
    with pytest.raises(RunStoreError):
        SqliteRunJournal.create(tmp_path / "run.sqlite", RUN_REQUEST.run_id, INITIAL)
    with pytest.raises(RunStoreError):
        SqliteRunJournal.open(tmp_path / "run.sqlite", run_id=RunId("other-run"))
    with pytest.raises(RunStoreError):
        SqliteRunJournal.open(tmp_path / "missing.sqlite")


def test_a_tampered_row_is_refused_at_open(store, tmp_path):
    journal = store()
    for e in first_events():
        journal.append(e)
    journal.close()
    connection = sqlite3.connect(tmp_path / "run.sqlite")
    row = connection.execute("SELECT record_json FROM events WHERE seq = 2").fetchone()[0]
    connection.execute("UPDATE events SET record_json = ? WHERE seq = 2", (row.replace("call", "cell", 1),))
    connection.commit()
    connection.close()
    with pytest.raises(RunStoreError, match="fingerprint"):
        SqliteRunJournal.open(tmp_path / "run.sqlite")


def test_a_second_opener_is_refused_while_the_store_is_open(store, tmp_path):
    journal = store()
    with pytest.raises(RunStoreBusy):
        SqliteRunJournal.open(tmp_path / "run.sqlite")
    journal.close()
    SqliteRunJournal.open(tmp_path / "run.sqlite").close()


def test_a_record_that_does_not_round_trip_exactly_is_refused(store, monkeypatch):
    journal = store()
    monkeypatch.setattr(sqlite_run, "decode", lambda data: None)
    assert isinstance(journal.append(first_events()[0]), TraceNotAppended)


# --- amendment A1: a failed COMMIT never leaks an uncommitted row -----------------------------------------------------


def prepared(store):
    journal = store()
    for e in first_events():
        journal.append(e)
    return journal


def fail_commit(monkeypatch, journal, *, after):
    real = journal._commit
    calls = []

    def failing():
        calls.append(1)
        if len(calls) == 1:
            if after:
                real()
            raise sqlite3.OperationalError("labelled commit failure")
        return real()

    monkeypatch.setattr(journal, "_commit", failing)


def test_a_commit_failing_before_it_takes_effect_is_unknown_then_absent_never_found(store, monkeypatch):
    journal = prepared(store)
    event = make_acceptance().event
    fail_commit(monkeypatch, journal, after=False)
    assert isinstance(journal.commit(event), AcceptanceCommitUnknown)
    assert isinstance(journal.query(AcceptanceQuery(RUN_REQUEST.run_id, event.acceptance_id)), AcceptanceAbsent)
    assert journal.snapshot().value.acceptances == ()
    committed = journal.commit(event)  # an exact resubmission commits once
    assert isinstance(committed, AcceptanceCommitted) and len(journal.snapshot().value.acceptances) == 1


def test_a_commit_whose_acknowledgement_is_lost_is_unknown_then_found(store, monkeypatch):
    journal = prepared(store)
    event = make_acceptance().event
    fail_commit(monkeypatch, journal, after=True)
    assert isinstance(journal.commit(event), AcceptanceCommitUnknown)
    found = journal.query(AcceptanceQuery(RUN_REQUEST.run_id, event.acceptance_id))
    assert isinstance(found, AcceptanceFound) and found.event == event
    again = journal.commit(event)
    assert isinstance(again, AcceptanceCommitted) and len(journal.snapshot().value.acceptances) == 1


@pytest.mark.parametrize("after", [False, True])
def test_a_trace_append_failing_at_commit_is_unknown_then_resolved_by_snapshot(store, monkeypatch, after):
    journal = store()
    event = first_events()[0]
    fail_commit(monkeypatch, journal, after=after)
    assert isinstance(journal.append(event), TraceAppendUnknown)
    assert journal.snapshot().value.events == ((event,) if after else ())
    assert isinstance(journal.append(event), TraceAppended)
    assert journal.snapshot().value.events == (event,)


def test_when_reopening_is_refused_the_instance_answers_nothing(store, tmp_path, monkeypatch):
    journal = prepared(store)
    event = make_acceptance().event
    fail_commit(monkeypatch, journal, after=True)
    assert isinstance(journal.commit(event), AcceptanceCommitUnknown)
    thief = SqliteRunJournal.open(tmp_path / "run.sqlite")  # takes the lock released by the failed connection
    try:
        assert isinstance(journal.query(AcceptanceQuery(RUN_REQUEST.run_id, event.acceptance_id)),
                          AcceptanceAuthorityUnknown)  # fmt: skip
        assert isinstance(journal.snapshot(), PortFailure)
        assert isinstance(journal.commit(make_acceptance(number=2).event), AcceptanceNotCommitted)
        assert isinstance(journal.append(make_event(make_intent(OperationKind.DECIDE), 3)), TraceNotAppended)
    finally:
        thief.close()


def test_the_canonical_text_is_stable_json(store):
    text = dumps(encode(first_events()[0]))
    assert json.loads(text) == loads(text) and " " not in text.split('"')[0]


# --- review fix 2026-11-20: a non-SQLite exception inside a write transaction ----------------------------------------


def raise_at_commit(monkeypatch, journal, *, after):
    real = journal._commit
    calls = []

    def failing():
        calls.append(1)
        if len(calls) == 1:
            if after:
                real()
            raise RuntimeError("labelled unexpected failure")
        return real()

    monkeypatch.setattr(journal, "_commit", failing)


@pytest.mark.parametrize("after", [False, True])
def test_an_unexpected_exception_at_an_acceptance_commit_propagates_and_never_leaks(store, monkeypatch, after):
    journal = prepared(store)
    event = make_acceptance().event
    raise_at_commit(monkeypatch, journal, after=after)
    with pytest.raises(RuntimeError, match="labelled unexpected"):
        journal.commit(event)
    answer = journal.query(AcceptanceQuery(RUN_REQUEST.run_id, event.acceptance_id))
    assert isinstance(answer, AcceptanceFound if after else AcceptanceAbsent)  # never Found from an open transaction
    assert len(journal.snapshot().value.acceptances) == (1 if after else 0)
    again = journal.commit(event)  # an exact resubmission stores it exactly once
    assert isinstance(again, AcceptanceCommitted) and len(journal.snapshot().value.acceptances) == 1


@pytest.mark.parametrize("after", [False, True])
def test_an_unexpected_exception_at_a_trace_append_propagates_and_never_leaks(store, monkeypatch, after):
    journal = store()
    event = first_events()[0]
    raise_at_commit(monkeypatch, journal, after=after)
    with pytest.raises(RuntimeError, match="labelled unexpected"):
        journal.append(event)
    assert journal.snapshot().value.events == ((event,) if after else ())
    assert isinstance(journal.append(event), TraceAppended) and journal.snapshot().value.events == (event,)


def test_an_unexpected_exception_inside_the_transaction_before_the_insert_is_cleaned_up(store, monkeypatch):
    journal = prepared(store)
    event = make_acceptance().event
    monkeypatch.setattr(sqlite_run, "_exact_text", lambda record: (_ for _ in ()).throw(RuntimeError("labelled")))
    with pytest.raises(RuntimeError):
        journal.commit(event)
    monkeypatch.undo()
    assert isinstance(journal.query(AcceptanceQuery(RUN_REQUEST.run_id, event.acceptance_id)), AcceptanceAbsent)
    assert not journal._connection.in_transaction
