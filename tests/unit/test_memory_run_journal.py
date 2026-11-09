"""In-memory storage contracts using generic metadata and controlled acknowledgement failures."""

import dataclasses
import threading

import pytest
from test_run_recording_contracts import (
    INITIAL,
    RUN_REQUEST,
    make_acceptance,
    make_event,
    make_intent,
    make_snapshot,
    make_terminal,
)

from autonomous_mixing.adapters.journal import run_memory as journal_module
from autonomous_mixing.adapters.journal.run_memory import InMemoryRunJournal
from autonomous_mixing.core.ports.acceptance import (
    validate_acceptance_commit_response,
    validate_acceptance_query_response,
)
from autonomous_mixing.core.ports.run_journal import validate_run_journal_snapshot, validate_trace_append_response
from autonomous_mixing.core.records.acceptance import (
    AcceptanceAbsent,
    AcceptanceAuthorityUnknown,
    AcceptanceCommitted,
    AcceptanceFound,
    AcceptanceNotCommitted,
    AcceptanceQuery,
)
from autonomous_mixing.core.records.budgets import BudgetUsage
from autonomous_mixing.core.records.identifiers import AcceptanceId, RunEventId, RunId
from autonomous_mixing.core.records.results import Success
from autonomous_mixing.core.records.run_trace import (
    DispatchSkipped,
    OperationKind,
    OperationObserved,
    TraceAppended,
    TraceAppendUnknown,
    TraceNotAppended,
)
from autonomous_mixing.core.records.termination import TerminationReason


def journal():
    return InMemoryRunJournal(RUN_REQUEST.run_id, INITIAL)


def snapshot(storage):
    response = storage.snapshot()
    assert isinstance(response, Success)
    validate_run_journal_snapshot(RUN_REQUEST.run_id, INITIAL, response.value)
    return response.value


def test_empty_journal_exposes_read_only_initial_state_and_non_durable_mode():
    storage = journal()
    assert snapshot(storage) == make_snapshot()
    assert storage.run_id == RUN_REQUEST.run_id
    assert storage.initial is INITIAL and storage.accepted is INITIAL
    assert storage.commits == () and storage.durability == "non_durable"
    for field in ("run_id", "initial", "accepted", "commits", "durability"):
        with pytest.raises(AttributeError):
            setattr(storage, field, None)


@pytest.mark.parametrize(
    "run_id,initial",
    [("run", INITIAL), (RUN_REQUEST.run_id, None), (RUN_REQUEST.run_id, dataclasses.replace(INITIAL, sequence=1))],
)
def test_constructor_reuses_the_acceptance_journal_contract(run_id, initial):
    with pytest.raises(ValueError):
        InMemoryRunJournal(run_id, initial)


def test_append_and_historical_duplicate_return_the_original_acknowledgement():
    storage = journal()
    first = make_event(RUN_REQUEST)
    acknowledgement = storage.append(first)
    assert isinstance(acknowledgement, TraceAppended) and acknowledgement.event is first
    storage.append(make_event(RUN_REQUEST, 2))
    assert storage.append(dataclasses.replace(first)) is acknowledgement
    assert snapshot(storage).events[0] is first
    validate_trace_append_response(first, acknowledgement)


def test_same_id_with_changed_content_is_refused_without_change():
    storage = journal()
    event = make_event(RUN_REQUEST)
    storage.append(event)
    before = snapshot(storage)
    changed = dataclasses.replace(event, usage=BudgetUsage(1, 0, 0, 0, 1.0))
    result = storage.append(changed)
    assert isinstance(result, TraceNotAppended)
    validate_trace_append_response(changed, result)
    assert snapshot(storage) == before


@pytest.mark.parametrize("sequence", [2, 9])
def test_out_of_order_append_changes_nothing(sequence):
    storage = journal()
    event = make_event(RUN_REQUEST, sequence)
    assert isinstance(storage.append(event), TraceNotAppended)
    assert snapshot(storage).events == ()


def test_wrong_run_is_refused_without_taking_the_sequence():
    storage = journal()
    other = dataclasses.replace(RUN_REQUEST, run_id=RunId("other"))
    wrong = make_event(other, run_id=other.run_id)
    result = storage.append(wrong)
    assert isinstance(result, TraceNotAppended)
    assert isinstance(storage.append(make_event(RUN_REQUEST)), TraceAppended)


def test_usage_and_observation_prefix_rules_are_enforced():
    storage = journal()
    first = make_event(make_intent(OperationKind.ANALYZE), usage=BudgetUsage(1, 0, 0, 0, 1.0))
    assert isinstance(storage.append(first), TraceAppended)
    skipped = DispatchSkipped(TerminationReason.USER_CANCELLED, "controlled cancellation")
    observed = make_event(OperationObserved(first.event_id, OperationKind.ANALYZE, skipped), 2, usage=first.usage)
    assert isinstance(
        storage.append(dataclasses.replace(observed, usage=BudgetUsage(0, 0, 0, 0, 0.0))), TraceNotAppended
    )
    assert isinstance(storage.append(observed), TraceAppended)
    repeated = make_event(observed.payload, 3, usage=first.usage)
    assert isinstance(storage.append(repeated), TraceNotAppended)
    orphan = make_event(OperationObserved(RunEventId("missing"), OperationKind.ANALYZE, skipped), 3, usage=first.usage)
    assert isinstance(storage.append(orphan), TraceNotAppended)
    assert len(snapshot(storage).events) == 2


def test_terminal_closes_trace_but_exact_duplicate_still_acknowledges():
    storage = journal()
    terminal = make_terminal()
    acknowledgement = storage.append(terminal)
    assert isinstance(acknowledgement, TraceAppended)
    assert isinstance(storage.append(make_event(RUN_REQUEST, 2)), TraceNotAppended)
    assert storage.append(terminal) is acknowledgement


def test_acceptance_sequence_is_independent_and_rejected_append_keeps_accepted_edits():
    storage = journal()
    event = make_event(RUN_REQUEST)
    storage.append(event)
    first = make_acceptance()
    response = storage.commit(first.event)
    validate_acceptance_commit_response(first.event, response)
    assert isinstance(response, AcceptanceCommitted)
    assert storage.accepted is response.accepted
    assert isinstance(storage.append(make_event(RUN_REQUEST, 99)), TraceNotAppended)
    assert snapshot(storage).acceptances == (response,)
    assert len(snapshot(storage).events) == 1
    second = make_acceptance(response.accepted, 2)
    storage.commit(second.event)
    assert storage.commit(first.event) is response
    found = storage.query(AcceptanceQuery(RUN_REQUEST.run_id, first.event.acceptance_id))
    assert isinstance(found, AcceptanceFound) and found.accepted is response.accepted
    assert snapshot(storage).accepted.sequence == 2


def test_commit_refusals_and_query_results_preserve_both_prefixes():
    storage = journal()
    committed = make_acceptance()
    storage.commit(committed.event)
    before = snapshot(storage)
    conflict = dataclasses.replace(committed.event, attempt_id=committed.event.attempt_id.__class__("other"))
    stale = dataclasses.replace(committed.event, acceptance_id=AcceptanceId("stale"))
    wrong = dataclasses.replace(committed.event, run_id=RunId("other"))
    for event in (conflict, stale, wrong):
        response = storage.commit(event)
        assert isinstance(response, AcceptanceNotCommitted)
        validate_acceptance_commit_response(event, response)
        assert snapshot(storage) == before
    for request, result_type in (
        (AcceptanceQuery(RUN_REQUEST.run_id, AcceptanceId("absent")), AcceptanceAbsent),
        (AcceptanceQuery(RunId("other"), committed.event.acceptance_id), AcceptanceAuthorityUnknown),
    ):
        response = storage.query(request)
        assert isinstance(response, result_type)
        validate_acceptance_query_response(request, response)


@pytest.mark.parametrize("method", ["append", "commit", "query"])
def test_methods_reject_invalid_arguments(method):
    storage = journal()
    with pytest.raises(ValueError):
        getattr(storage, method)(None)
    assert snapshot(storage) == make_snapshot()


@pytest.mark.parametrize("error", [MemoryError("memory"), TypeError("bug"), OSError("io")])
def test_unexpected_prefix_validation_error_propagates_without_publishing(monkeypatch, error):
    storage = journal()
    before = snapshot(storage)
    with monkeypatch.context() as patch:

        def fail(*args):
            raise error

        patch.setattr(journal_module, "RunJournalSnapshot", fail)
        with pytest.raises(type(error)) as caught:
            storage.append(make_event(RUN_REQUEST))
        assert caught.value is error
    assert snapshot(storage) == before


@pytest.mark.parametrize("actually_write", [False, True])
def test_unknown_append_acknowledgement_is_resolved_by_snapshot(actually_write):
    storage = journal()
    accepted = storage.commit(make_acceptance().event)
    event = make_event(RUN_REQUEST)

    def uncertain_append(submitted):
        if actually_write:
            storage.append(submitted)
        return TraceAppendUnknown(submitted.event_id, "controlled lost acknowledgement")

    response = uncertain_append(event)
    validate_trace_append_response(event, response)
    captured = snapshot(storage)
    assert captured.events == ((event,) if actually_write else ())
    assert captured.accepted is accepted.accepted
    assert captured.acceptances == (accepted,)


def test_snapshots_are_immutable_and_new_instances_have_no_history():
    storage = journal()
    initial_snapshot = snapshot(storage)
    storage.append(make_event(RUN_REQUEST))
    assert initial_snapshot.events == () and len(snapshot(storage).events) == 1
    assert snapshot(journal()) == initial_snapshot


def test_competing_appends_at_one_sequence_publish_exactly_once():
    from concurrent.futures import ThreadPoolExecutor

    storage = journal()
    barrier = threading.Barrier(2)
    events = (make_event(RUN_REQUEST, event_id="left"), make_event(RUN_REQUEST, event_id="right"))

    def append(event):
        barrier.wait(timeout=5)
        return storage.append(event)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = tuple(pool.map(append, events))
    assert sum(isinstance(response, TraceAppended) for response in responses) == 1
    assert sum(isinstance(response, TraceNotAppended) for response in responses) == 1
    assert len(snapshot(storage).events) == 1


def test_acknowledgement_construction_failure_leaves_both_prefixes_unchanged(monkeypatch):
    storage = journal()
    storage.commit(make_acceptance().event)
    before = snapshot(storage)
    error = MemoryError("controlled acknowledgement allocation failure")

    def fail(*args):
        raise error

    with monkeypatch.context() as patch:
        patch.setattr(journal_module, "TraceAppended", fail)
        with pytest.raises(MemoryError) as caught:
            storage.append(make_event(RUN_REQUEST))
        assert caught.value is error
    assert snapshot(storage) == before


def test_snapshot_and_commit_are_serialized(monkeypatch):
    storage = journal()
    entered = threading.Event()
    release = threading.Event()
    writer_attempted = threading.Event()
    writer_done = threading.Event()
    actual_snapshot = journal_module.RunJournalSnapshot
    results = []
    errors = []

    class ObservedLock:
        def __init__(self):
            self.lock = threading.Lock()

        def __enter__(self):
            if threading.current_thread().name == "journal-writer":
                writer_attempted.set()
            self.lock.acquire()

        def __exit__(self, *args):
            self.lock.release()

    def gated_snapshot(*args):
        if threading.current_thread().name == "journal-reader":
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test snapshot was not released")
        return actual_snapshot(*args)

    def read():
        try:
            results.append(storage.snapshot())
        except BaseException as error:
            errors.append(error)

    def write():
        try:
            results.append(storage.commit(make_acceptance().event))
        except BaseException as error:
            errors.append(error)
        finally:
            writer_done.set()

    storage._lock = ObservedLock()
    monkeypatch.setattr(journal_module, "RunJournalSnapshot", gated_snapshot)
    reader = threading.Thread(target=read, name="journal-reader")
    writer = threading.Thread(target=write, name="journal-writer")
    reader.start()
    try:
        assert entered.wait(5)
        writer.start()
        assert writer_attempted.wait(5)
        assert not writer_done.is_set()
    finally:
        release.set()
        reader.join(5)
        if writer.ident is not None:
            writer.join(5)
    assert not reader.is_alive() and not writer.is_alive() and not errors
    assert isinstance(results[0], Success) and results[0].value.acceptances == ()
    assert isinstance(results[1], AcceptanceCommitted)
    assert snapshot(storage).acceptances == (results[1],)
