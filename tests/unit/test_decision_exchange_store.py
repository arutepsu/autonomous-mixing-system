"""Tests for the local append-only decision-exchange store (R3 Task 4).

Labelled contract fixtures only; nothing here calls a provider. Crash evidence is from a real child process that exits
without closing; no power-loss or network-storage claim is made.
"""

import base64
import dataclasses
import hashlib
import json
import sqlite3
import subprocess
import sys
import textwrap
from datetime import timedelta
from pathlib import Path

import pytest
from decision_support import UTC, Tokens, situation

import autonomous_mixing.adapters.journal.decision_exchange as store_module
from autonomous_mixing.adapters.decision_agent.request import PacketLimits, build_decision_packet
from autonomous_mixing.adapters.decision_agent.response import ResponseLimits, parse_decision_response
from autonomous_mixing.adapters.journal.decision_exchange import (
    SCHEMA_VERSION,
    DecisionExchangeStoreBusy,
    DecisionExchangeStoreError,
    SqliteDecisionExchangeStore,
)
from autonomous_mixing.core.ports.decision_audit import DecisionAuditTrail, permits_dispatch
from autonomous_mixing.core.records.decision_exchange import (
    AliasKind,
    AuditNotRecorded,
    AuditRecorded,
    AuditRecordUnknown,
    AuditRefusal,
    CompletedDecisionExchange,
    DecisionCallKey,
    DecisionExchangeRequest,
    ExchangeAbsent,
    ExchangeCompletion,
    ExchangeFound,
    ExchangeReadUnavailable,
    ExchangeStatus,
    ProviderRequestIdentity,
    RecordedDecisionResponse,
    ResponseEnvelope,
    canonical_json,
    sha256_text,
)
from autonomous_mixing.core.records.identifiers import RunId, VersionRef
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, Success

MAX = 1 << 20
SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture
def s():
    return situation()


@pytest.fixture
def packet(s):
    return build_decision_packet(s, limits=PacketLimits(1 << 20))


@pytest.fixture
def path(tmp_path) -> Path:
    return tmp_path / "exchanges.sqlite"


@pytest.fixture
def store(path, s):
    opened = SqliteDecisionExchangeStore.create(path, s.run_id, max_record_bytes=MAX)
    yield opened
    opened.close()


def request_of(packet, exchange_id="ex-1") -> DecisionExchangeRequest:
    text = canonical_json({"model": "labelled-model", "messages": [{"role": "user", "content": packet.payload}]})
    return DecisionExchangeRequest(packet.binding.call, exchange_id, packet, text, sha256_text(text),
                                   VersionRef("prompt", "1"), VersionRef("autonomous_mixing.decision_response", "2"),
                                   ProviderRequestIdentity("labelled-provider", "api-1", "labelled-model",
                                                           '{"max_tokens":1000,"temperature":0}'),
                                   UTC)  # fmt: skip


def alias(packet, kind, source):
    (found,) = [a.alias for a in packet.binding.aliases if a.kind is kind and a.source_id == source]
    return found


def body_of(packet) -> bytes:
    return json.dumps({
        "type": "propose_action",
        "action": {"operation": "set_track_gain", "track": alias(packet, AliasKind.TRACK, "t1"), "gain_db": -1.5},
        "addressed_problem_candidates": [alias(packet, AliasKind.CANDIDATE, "cand-1")],
        "evidence_refs": [alias(packet, AliasKind.CURRENT_OBSERVATION, o) for o in ("o-1", "o-2")],
        "expected_effect": "less overlap", "rationale": "labelled fixture",
        "justification": {"reasoning": "fixture", "uncertainty": "uncalibrated", "assumptions": [], "limitations": []},
    }).encode()  # fmt: skip


def response_of(request, body=None, envelope=ResponseEnvelope.COMPLETE) -> RecordedDecisionResponse:
    received = body is not None
    return RecordedDecisionResponse(request.call, request.exchange_id, request.request_sha256, envelope,
                                    base64.b64encode(body).decode() if received else None,
                                    hashlib.sha256(body).hexdigest() if received else None,
                                    "end_turn" if received else None, "req-123" if received else None,
                                    "labelled-model-2026" if received else None, '{"input_tokens":10}',
                                    None if received else "timeout", UTC + timedelta(seconds=2), 2.0)  # fmt: skip


def complete(request, response, s, packet, identities=None) -> CompletedDecisionExchange:
    result = parse_decision_response(response, request=request, situation=s, identities=identities or Tokens("local"),
                                     limits=ResponseLimits(1 << 16))  # fmt: skip
    if isinstance(result, Success):
        return CompletedDecisionExchange(request.call, request.exchange_id, request.request_sha256,
                                         ExchangeCompletion.VALID_DECISION, response.body_sha256,
                                         result.value.decision, None, "valid decision", UTC + timedelta(seconds=3))  # fmt: skip
    return CompletedDecisionExchange(request.call, request.exchange_id, request.request_sha256,
                                     ExchangeCompletion.INVALID_RESPONSE, response.body_sha256, None, result,
                                     result.message, UTC + timedelta(seconds=3))  # fmt: skip


def failure_completion(request, response_sha=None) -> CompletedDecisionExchange:
    return CompletedDecisionExchange(request.call, request.exchange_id, request.request_sha256,
                                     ExchangeCompletion.PROVIDER_FAILURE, response_sha, None,
                                     PortFailure(PortFailureCode.UNAVAILABLE, "timeout", True),
                                     "transport timeout; server execution unknown", UTC + timedelta(seconds=3))  # fmt: skip


def found(store, call) -> object:
    result = store.read(call)
    assert isinstance(result, ExchangeFound), result
    return result.record


# --- round trip -----------------------------------------------------------------------------------------------------


def test_the_store_satisfies_the_audit_port(store):
    port: DecisionAuditTrail = store
    assert callable(port.begin) and callable(port.finish)


def test_three_phases_round_trip_and_survive_reopen(path, store, s, packet):
    request = request_of(packet)
    begun = store.begin(request)
    assert isinstance(begun, AuditRecorded) and begun.newly_recorded and permits_dispatch(begun, request)
    response = response_of(request, body_of(packet))
    assert isinstance(store.record_response(response), AuditRecorded)
    done = complete(request, response, s, packet)
    assert store.finish(done).record.status is ExchangeStatus.COMPLETED
    store.close()
    reopened = SqliteDecisionExchangeStore.open(path, run_id=s.run_id, max_record_bytes=MAX)
    record = found(reopened, request.call)
    assert (record.request, record.response, record.completion) == (request, response, done)
    assert record.response.body == body_of(packet)
    reopened.close()


def test_replay_reconstructs_request_response_and_exact_identities(path, store, s, packet):
    request = request_of(packet)
    response = response_of(request, body_of(packet))
    for write in (store.begin(request), store.record_response(response),
                  store.finish(complete(request, response, s, packet, Tokens("run-1-call-3")))):  # fmt: skip
        assert isinstance(write, AuditRecorded)
    store.close()
    reopened = SqliteDecisionExchangeStore.open(path, run_id=s.run_id, max_record_bytes=MAX)
    record = found(reopened, request.call)
    reopened.close()
    # the projection reproduces the recorded packet exactly
    assert build_decision_packet(s, limits=PacketLimits(1 << 20)) == record.request.packet
    decision = record.completion.decision
    tokens = [decision.decision_id.value, decision.outcome.action.action_id.value]
    replayed = parse_decision_response(record.response, request=record.request, situation=s,
                                       identities=Tokens(tokens=tokens), limits=ResponseLimits(1 << 16))  # fmt: skip
    assert replayed.value.decision == decision  # exact identities, not merely similar semantics


def test_invalid_responses_and_provider_failures_are_kept(store, s, packet):
    request = request_of(packet)
    store.begin(request)
    malformed = response_of(request, b"```json {} ```")
    store.record_response(malformed)
    done = complete(request, malformed, s, packet)
    assert done.completion is ExchangeCompletion.INVALID_RESPONSE
    assert isinstance(store.finish(done), AuditRecorded)
    next_call = DecisionCallKey(s.run_id, 4)
    assert isinstance(store.read(next_call), ExchangeAbsent)


def test_no_response_is_its_own_recorded_fact(store, packet):
    request = request_of(packet)
    store.begin(request)
    missing = response_of(request, None, ResponseEnvelope.NOT_RECEIVED)
    assert isinstance(store.record_response(missing), AuditRecorded)
    assert isinstance(store.finish(failure_completion(request)), AuditRecorded)
    record = found(store, request.call)
    assert record.response.body is None and record.response.error_code == "timeout"
    assert record.completion.decision is None and record.completion.failure.retryable


# --- ordering, idempotence and conflicts ---------------------------------------------------------------------------


def test_identical_replay_is_idempotent_and_never_dispatch_permission(store, packet):
    request = request_of(packet)
    store.begin(request)
    again = store.begin(request)
    assert isinstance(again, AuditRecorded) and not again.newly_recorded and not permits_dispatch(again, request)
    response = response_of(request, body_of(packet))
    store.record_response(response)
    replay = store.record_response(response)
    assert isinstance(replay, AuditRecorded) and not replay.newly_recorded
    assert not permits_dispatch(store.begin(request), request)  # answered: still no resend


def test_conflicting_reuse_of_call_or_exchange_ids_is_refused(store, s, packet):
    request = request_of(packet)
    store.begin(request)
    different = dataclasses.replace(request, exchange_id="ex-2")
    refused = store.begin(different)
    assert isinstance(refused, AuditNotRecorded) and refused.reason is AuditRefusal.CONFLICT
    other_call = dataclasses.replace(request, call=DecisionCallKey(s.run_id, 4),
                                     packet=dataclasses.replace(packet, binding=dataclasses.replace(
                                         packet.binding, call=DecisionCallKey(s.run_id, 4))))  # fmt: skip
    reused = store.begin(other_call)  # same exchange ID under another call
    assert isinstance(reused, AuditNotRecorded) and reused.reason is AuditRefusal.CONFLICT
    response = response_of(request, body_of(packet))
    store.record_response(response)
    changed = response_of(request, b'{"other":1}')
    assert store.record_response(changed).reason is AuditRefusal.CONFLICT
    assert found(store, request.call).response == response  # never overwritten


def test_phases_require_a_matching_request_and_are_ordered(store, s, packet):
    request = request_of(packet)
    response = response_of(request, body_of(packet))
    assert store.record_response(response).reason is AuditRefusal.NO_MATCHING_REQUEST
    assert store.finish(failure_completion(request)).reason is AuditRefusal.NO_MATCHING_REQUEST
    store.begin(request)
    mismatched = dataclasses.replace(response, request_sha256="0" * 64)
    assert store.record_response(mismatched).reason is AuditRefusal.NO_MATCHING_REQUEST
    store.record_response(response)
    # completing without referencing the received response would hide it
    hidden = failure_completion(request)
    assert store.finish(hidden).reason is AuditRefusal.INVALID_RECORD
    done = complete(request, response, s, packet)
    store.finish(done)
    assert store.record_response(response_of(request, b"{}")).reason in (AuditRefusal.CONFLICT,
                                                                         AuditRefusal.ALREADY_COMPLETED)  # fmt: skip
    assert store.finish(dataclasses.replace(done, detail="rewritten")).reason is AuditRefusal.CONFLICT
    assert found(store, request.call).completion == done


def test_wrong_run_is_refused(store, packet):
    request = request_of(packet)
    foreign = dataclasses.replace(request, call=DecisionCallKey(RunId("run-9"), 3),
                                  packet=dataclasses.replace(packet, binding=dataclasses.replace(
                                      packet.binding, call=DecisionCallKey(RunId("run-9"), 3))))  # fmt: skip
    assert store.begin(foreign).reason is AuditRefusal.WRONG_RUN
    assert isinstance(store.read(DecisionCallKey(RunId("run-9"), 3)), ExchangeReadUnavailable)


# --- bounds ---------------------------------------------------------------------------------------------------------


def test_oversize_records_are_refused_before_writing_and_reader_bounds_are_symmetric(path, s, packet):
    small = SqliteDecisionExchangeStore.create(path, s.run_id, max_record_bytes=2000)
    request = request_of(packet)
    refused = small.begin(request)
    assert isinstance(refused, AuditNotRecorded) and refused.reason is AuditRefusal.TOO_LARGE
    assert isinstance(small.read(request.call), ExchangeAbsent)
    small.close()
    big = SqliteDecisionExchangeStore.open(path, run_id=s.run_id, max_record_bytes=MAX)
    big.begin(request)
    big.close()
    with pytest.raises(DecisionExchangeStoreError, match="bound"):
        SqliteDecisionExchangeStore.open(path, run_id=s.run_id, max_record_bytes=2000)


# --- tampering, schema and ownership --------------------------------------------------------------------------------


def _tamper(path: Path, sql: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute(sql)
    connection.commit()
    connection.close()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE phases SET record_json = replace(record_json, 'labelled-model', 'other-model')",
        "UPDATE phases SET fingerprint = '" + "0" * 64 + "'",
        f"UPDATE metadata SET schema_version = {SCHEMA_VERSION + 1}",
        "UPDATE phases SET phase = 'response'",
        "DELETE FROM metadata",
    ],
)
def test_tampered_or_unsupported_stores_are_refused(path, store, s, packet, sql):
    store.begin(request_of(packet))
    store.close()
    _tamper(path, sql)
    with pytest.raises(DecisionExchangeStoreError):
        SqliteDecisionExchangeStore.open(path, run_id=s.run_id, max_record_bytes=MAX)


def test_a_store_of_another_run_or_a_missing_store_is_refused(path, store, s):
    store.close()
    with pytest.raises(DecisionExchangeStoreError, match="another run"):
        SqliteDecisionExchangeStore.open(path, run_id=RunId("run-9"), max_record_bytes=MAX)
    with pytest.raises(DecisionExchangeStoreError):
        SqliteDecisionExchangeStore.open(path.with_name("absent.sqlite"), run_id=s.run_id, max_record_bytes=MAX)
    with pytest.raises(DecisionExchangeStoreError, match="exists"):
        SqliteDecisionExchangeStore.create(path, s.run_id, max_record_bytes=MAX)


def test_second_opener_is_busy(path, store, s):
    with pytest.raises(DecisionExchangeStoreBusy):
        SqliteDecisionExchangeStore.open(path, run_id=s.run_id, max_record_bytes=MAX)


def test_the_run_store_schema_is_untouched(path, store):
    tables = {row[0] for row in store._connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"metadata", "phases"}  # its own database, not the run/acceptance store


# --- failures around commit -----------------------------------------------------------------------------------------


def test_failure_before_commit_is_unknown_then_verified_absent(store, packet, monkeypatch):
    request = request_of(packet)

    def refuse():
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store, "_commit", refuse)
    result = store.begin(request)
    assert isinstance(result, AuditRecordUnknown) and not permits_dispatch(result, request)
    monkeypatch.undo()
    assert isinstance(store.read(request.call), ExchangeAbsent)  # read only after a verified reopen


def test_failure_after_commit_is_unknown_then_verified_present(store, packet, monkeypatch):
    request = request_of(packet)
    original = store._commit

    def commit_then_fail():
        original()
        raise sqlite3.OperationalError("acknowledgement lost")

    monkeypatch.setattr(store, "_commit", commit_then_fail)
    assert isinstance(store.begin(request), AuditRecordUnknown)
    monkeypatch.undo()
    record = found(store, request.call)
    assert record.status is ExchangeStatus.PENDING  # recorded, but this is not permission to dispatch
    assert not permits_dispatch(store.begin(request), request)


def test_unexpected_exceptions_propagate_after_cleanup(store, packet, monkeypatch):
    def boom():
        raise RuntimeError("programming error")

    monkeypatch.setattr(store, "_commit", boom)
    with pytest.raises(RuntimeError):
        store.begin(request_of(packet))
    monkeypatch.undo()
    assert isinstance(store.read(request_of(packet).call), ExchangeAbsent)


def test_closed_store_answers_unavailable(store, packet):
    store.close()
    request = request_of(packet)
    assert store.begin(request).reason is AuditRefusal.STORE_UNAVAILABLE
    assert isinstance(store.read(request.call), ExchangeReadUnavailable)


# --- crash ------------------------------------------------------------------------------------------------------------


def test_pending_exchange_after_a_process_crash_is_incomplete_and_not_resent(path, s, packet):
    child = textwrap.dedent(f"""
        import os, sys
        sys.path[:0] = [{str(SRC)!r}, {str(Path(__file__).parent)!r}]
        from decision_support import situation
        from test_decision_exchange_store import request_of
        from autonomous_mixing.adapters.decision_agent.request import PacketLimits, build_decision_packet
        from autonomous_mixing.adapters.journal.decision_exchange import SqliteDecisionExchangeStore
        s = situation()
        store = SqliteDecisionExchangeStore.create({str(path)!r}, s.run_id, max_record_bytes={MAX})
        result = store.begin(request_of(build_decision_packet(s, limits=PacketLimits(1 << 20))))
        print(type(result).__name__, result.newly_recorded, flush=True)
        os._exit(0)  # dies holding the lock, before any dispatch, without closing
    """)
    run = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True, timeout=120)
    assert run.stdout.strip() == "AuditRecorded True", run.stderr
    store = SqliteDecisionExchangeStore.open(path, run_id=s.run_id, max_record_bytes=MAX)
    request = request_of(packet)
    assert found(store, request.call).status is ExchangeStatus.PENDING
    assert not permits_dispatch(store.begin(request), request)  # reopening never resubmits
    store.close()
    assert store_module.LOCAL_SQLITE_PROCESS_CRASH == "local_sqlite_process_crash"
