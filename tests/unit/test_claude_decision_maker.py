"""R4 orchestration tests with a real local audit store; no provider calls."""

import base64
import hashlib
import json
from dataclasses import replace

import pytest
from decision_support import Clock, Tokens, situation
from test_claude_config import config

from autonomous_mixing.adapters.decision_agent.claude import ClaudeDecisionMaker
from autonomous_mixing.adapters.decision_agent.transport import TransportResult
from autonomous_mixing.adapters.journal.decision_exchange import SqliteDecisionExchangeStore
from autonomous_mixing.core.records.decision_exchange import (
    AuditNotRecorded,
    AuditRecordUnknown,
    AuditRefusal,
    DecisionCallKey,
    ExchangeCompletion,
    ExchangeStatus,
)
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, Success

NO_CHANGE = '{"type":"no_change","rationale":"fixture","evidence":[],"explanation":"no supported change"}'
ABSTAIN = (
    '{"type":"abstain","rationale":"fixture","evidence":[],"reason":"insufficient_evidence","requested_context":[]}'
)


def received(text=NO_CHANGE, *, stop="end_turn", content=None):
    body = json.dumps(
        dict(
            type="message",
            role="assistant",
            model="resolved-test-model",
            stop_reason=stop,
            content=[dict(type="text", text=text)] if content is None else content,
            usage=dict(input_tokens=10, output_tokens=20),
        ),
        separators=(",", ":"),
    ).encode()
    return TransportResult(body, 200, "req-1", "1.13.0")


class Transport:
    """Labelled test double, never a runtime decision source."""

    def __init__(self, value=None):
        self.calls = []
        self.config = config()
        self.value = received() if value is None else value

    def send(self, body, *, timeout_seconds):
        self.calls.append((body, timeout_seconds))
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


@pytest.fixture
def store(tmp_path):
    opened = SqliteDecisionExchangeStore.create(
        tmp_path / "exchange.sqlite", situation().run_id, max_record_bytes=1000000
    )
    yield opened
    opened.close()


def maker(store, transport=None, **kwargs):
    return ClaudeDecisionMaker(
        config=config(),
        transport=transport or Transport(),
        audit=store,
        identities=kwargs.pop("identities", Tokens()),
        clock=kwargs.pop("clock", Clock()),
        **kwargs,
    )


def record(store):
    return store.read(DecisionCallKey(situation().run_id, situation().usage.decision_calls)).record


@pytest.mark.parametrize("text", [NO_CHANGE, ABSTAIN])
def test_valid_decisions_completed_and_replayed_without_new_ids_or_send(store, text):
    t, ids = Transport(received(text)), Tokens()
    m = maker(store, t, identities=ids)
    first = m.decide(situation())
    assert isinstance(first, Success)
    done = record(store)
    assert done.status is ExchangeStatus.COMPLETED
    assert done.completion.completion is ExchangeCompletion.VALID_DECISION
    assert done.completion.decision == first.value.decision
    assert done.response.body == text.encode()
    metadata = json.loads(done.response.metadata)
    assert base64.b64decode(metadata["provider_body_base64"]) == t.value.body
    assert hashlib.sha256(t.value.body).hexdigest() == metadata["provider_body_sha256"]
    count = ids.count
    assert m.decide(situation()) == first and ids.count == count and len(t.calls) == 1
    assert t.calls[0][0] == done.request.request_text.encode()


def test_replay_checks_changed_content_and_configuration(store):
    m = maker(store)
    assert isinstance(m.decide(situation()), Success)
    original = situation()
    changed = replace(
        original,
        observations=(
            replace(original.observations[0], result=replace(original.observations[0].result, value=-999.0)),
            *original.observations[1:],
        ),
    )
    with pytest.raises(ValueError, match="conflict"):
        m.decide(changed)
    t = Transport()
    t.config = config(max_tokens=200)
    other = ClaudeDecisionMaker(config=t.config, transport=t, audit=store, clock=Clock(), identities=Tokens())
    with pytest.raises(ValueError, match="conflict"):
        other.decide(situation())
    assert not t.calls


@pytest.mark.parametrize(
    "text", ["not JSON", '{"type":[]}', "[" * 1200 + "0" + "]" * 1200, '{"gain":' + "1" * 5000 + "}"]
)
def test_invalid_response_recorded_without_repair(store, text):
    t = Transport(received(text))
    result = maker(store, t).decide(situation())
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.INVALID_RESPONSE and not result.retryable
    assert record(store).completion.completion is ExchangeCompletion.INVALID_RESPONSE
    assert len(t.calls) == 1


@pytest.mark.parametrize(
    "value,code,retryable",
    [
        (received(stop="max_tokens"), PortFailureCode.INVALID_RESPONSE, False),
        (received(stop="refusal"), PortFailureCode.UNAVAILABLE, False),
        (received(content=[]), PortFailureCode.INVALID_RESPONSE, False),
        (received(content=[dict(type="tool_use", name="x")]), PortFailureCode.INVALID_RESPONSE, False),
        (TransportResult(b"bad envelope", 200, None, "1.13.0"), PortFailureCode.INVALID_RESPONSE, False),
        (TransportResult(None, None, None, "1.13.0", "connection_or_timeout", True), PortFailureCode.UNAVAILABLE, True),
        (
            TransportResult(b'{"error":"rate"}', 429, None, "1.13.0", "http_429", True),
            PortFailureCode.UNAVAILABLE,
            True,
        ),
        (
            TransportResult(b'{"error":"auth"}', 401, None, "1.13.0", "http_401", False),
            PortFailureCode.UNAVAILABLE,
            False,
        ),
        (
            TransportResult(b"x" * 50001, 200, None, "1.13.0", "response_too_large", False),
            PortFailureCode.INVALID_RESPONSE,
            False,
        ),
    ],
)
def test_failure_table_audited_not_fabricated_abstention(store, value, code, retryable):
    result = maker(store, Transport(value)).decide(situation())
    assert isinstance(result, PortFailure) and result.code is code and result.retryable is retryable
    assert record(store).completion.failure == result and record(store).completion.decision is None


@pytest.mark.parametrize("phase", ["begin", "record_response", "finish"])
@pytest.mark.parametrize("unknown", [False, True])
def test_recording_failure_releases_nothing_and_stops_before_next_phase(store, monkeypatch, phase, unknown):
    t, ids = Transport(), Tokens()

    def refused(value):
        return (
            AuditRecordUnknown(value.call, "unknown")
            if unknown
            else AuditNotRecorded(value.call, AuditRefusal.STORE_UNAVAILABLE, "no")
        )

    monkeypatch.setattr(store, phase, refused)
    result = maker(store, t, identities=ids).decide(situation())
    assert isinstance(result, PortFailure) and not result.retryable
    assert len(t.calls) == (0 if phase == "begin" else 1)
    assert ids.count == (2 if phase == "finish" else 1)


@pytest.mark.parametrize("phase", ["record_response", "finish"])
def test_incomplete_exchange_never_resends_on_fresh_adapter(store, monkeypatch, phase):
    t = Transport()
    original = getattr(store, phase)
    monkeypatch.setattr(store, phase, lambda value: AuditRecordUnknown(value.call, "unknown"))
    assert isinstance(maker(store, t).decide(situation()), PortFailure)
    monkeypatch.setattr(store, phase, original)
    second = Transport()
    assert isinstance(maker(store, second).decide(situation()), PortFailure)
    assert not second.calls


def test_programming_exception_propagates_leaving_pending_evidence(store):
    error = RuntimeError("bug")
    with pytest.raises(RuntimeError) as caught:
        maker(store, Transport(error)).decide(situation())
    assert caught.value is error and record(store).status is ExchangeStatus.PENDING


def test_cancelled_or_expired_call_sends_nothing(store):
    clock = Clock()
    clock.read = lambda: replace(Clock().read(), cancelled=True)
    t = Transport()
    assert isinstance(maker(store, t, clock=clock).decide(situation()), PortFailure)
    expired = replace(situation(), usage=replace(situation().usage, elapsed_seconds=600.0))
    assert isinstance(maker(store, t).decide(expired), PortFailure)
    assert not t.calls


def test_valid_proposal_uses_the_exact_packet_and_local_identities(store):
    from test_decision_response_parser import proposal

    from autonomous_mixing.adapters.decision_agent.request import PacketLimits, build_decision_packet
    from autonomous_mixing.core.records.decision import ProposeAction

    packet = build_decision_packet(situation(), limits=PacketLimits(200000))
    ids = Tokens()
    result = maker(
        store, Transport(received(json.dumps(proposal(packet), separators=(",", ":")))), identities=ids
    ).decide(situation())
    assert isinstance(result, Success) and isinstance(result.value.decision.outcome, ProposeAction)
    assert ids.count == 3
    assert record(store).completion.decision == result.value.decision


@pytest.mark.parametrize("phase", ["begin", "record_response", "finish"])
def test_success_acknowledgement_for_different_content_never_releases_decision(store, monkeypatch, phase):
    from autonomous_mixing.core.records.decision_exchange import AuditRecorded, DecisionExchangeRecord

    original = getattr(store, phase)

    def wrong(value):
        result = original(value)
        request = replace(result.record.request, exchange_id="other-exchange")
        return AuditRecorded(DecisionExchangeRecord(request, None, None), True)

    monkeypatch.setattr(store, phase, wrong)
    t = Transport()
    result = maker(store, t).decide(situation())
    assert isinstance(result, PortFailure) and not result.retryable
    assert len(t.calls) == (0 if phase == "begin" else 1)


def test_real_sdk_mock_http_and_audit_have_the_same_request_body(store):
    httpx = pytest.importorskip("httpx2")
    from autonomous_mixing.adapters.decision_agent.transport import ClaudeMessagesTransport

    seen = []

    def handler(request):
        # Pending must already be committed when HTTP dispatch begins.
        assert record(store).status is ExchangeStatus.PENDING
        seen.append(request.content)
        return httpx.Response(200, content=received().body, headers={"request-id": "req-mock"})

    with ClaudeMessagesTransport(config(), api_key="test-only", http_transport=httpx.MockTransport(handler)) as t:
        result = maker(store, t).decide(situation())
    assert isinstance(result, Success) and seen == [record(store).request.request_text.encode()]


@pytest.mark.parametrize(
    "content,stop",
    [
        ([dict(type="text", text=NO_CHANGE), dict(type="text", text=NO_CHANGE)], "end_turn"),
        ([dict(type="text", text=NO_CHANGE)], "tool_use"),
        ([dict(type="text", text=NO_CHANGE)], None),
        ([dict(type="text", text=NO_CHANGE)], []),
    ],
)
def test_unexpected_stop_or_blocks_cannot_release_a_decision(store, content, stop):
    result = maker(store, Transport(received(content=content, stop=stop))).decide(situation())
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.INVALID_RESPONSE


def test_missing_usage_stays_unknown(store):
    value = received()
    doc = json.loads(value.body)
    del doc["usage"]
    result = maker(store, Transport(replace(value, body=json.dumps(doc).encode()))).decide(situation())
    assert isinstance(result, Success)
    assert json.loads(record(store).response.metadata)["usage"] is None


def test_identity_source_programming_error_is_not_an_input_failure(store):
    class Broken:
        def next_token(self):
            raise ValueError("identity source bug")

    with pytest.raises(ValueError, match="identity source bug"):
        maker(store, identities=Broken()).decide(situation())


def test_complete_request_too_large_is_refused_without_dispatch(store):
    t = Transport()
    t.config = config(max_request_bytes=100)
    m = ClaudeDecisionMaker(config=t.config, transport=t, audit=store, identities=Tokens(), clock=Clock())
    result = m.decide(situation())
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.INVALID_INPUT and not t.calls


@pytest.mark.parametrize("at", [2, 3])
def test_cancellation_during_audit_or_after_response_never_releases_decision(store, at):
    class CancelClock(Clock):
        def read(self):
            result = super().read()
            return replace(result, cancelled=self.reads >= at)

    ids, t = Tokens(), Transport()
    result = maker(store, t, clock=CancelClock(), identities=ids).decide(situation())
    assert isinstance(result, PortFailure) and not result.retryable
    assert len(t.calls) == (0 if at == 2 else 1)
    assert record(store).status is ExchangeStatus.COMPLETED and ids.count == 1


def test_timeout_uses_remaining_allowance_including_preparation(store):
    t = Transport()
    s = replace(situation(), usage=replace(situation().usage, elapsed_seconds=595.0))
    assert isinstance(maker(store, t).decide(s), Success)
    assert t.calls[0][1] == 4.0


def test_provider_failure_replay_preserves_failure_without_send(store):
    first = maker(store, Transport(TransportResult(None, None, None, "1.13.0", "timeout", True))).decide(situation())
    t = Transport()
    assert maker(store, t).decide(situation()) == first and first.retryable and not t.calls


@pytest.mark.parametrize(
    "body", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":' + b"1" * 5000 + b"}", b"[" * 1200 + b"0" + b"]" * 1200]
)
def test_invalid_provider_envelope_is_retained_not_a_crash(store, body):
    result = maker(store, Transport(TransportResult(body, 200, None, "1.13.0"))).decide(situation())
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.INVALID_RESPONSE
    assert base64.b64decode(json.loads(record(store).response.metadata)["provider_body_base64"]) == body


def test_http_error_body_can_be_complete_even_though_the_call_failed(store):
    value = TransportResult(b'{"error":"busy"}', 503, None, "1.13.0", "http_503", True)
    result = maker(store, Transport(value)).decide(situation())
    assert isinstance(result, PortFailure)
    assert json.loads(record(store).response.metadata)["provider_body_complete"] is True


@pytest.mark.parametrize("role", [None, "user", "system", ["assistant"], "MISSING"])
def test_only_an_explicit_assistant_message_can_release_a_decision(store, role):
    value = received()
    doc = json.loads(value.body)
    if role == "MISSING":
        doc.pop("role", None)
    else:
        doc["role"] = role
    result = maker(store, Transport(replace(value, body=json.dumps(doc).encode()))).decide(situation())
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.INVALID_RESPONSE
    assert record(store).completion.decision is None
