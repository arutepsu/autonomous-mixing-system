"""Exact local audit use; only a labelled provider transport is called."""

from dataclasses import replace

import pytest
from decision_support import situation
from masking_diagnosis_support import experimental_policy
from test_claude_config import config
from test_claude_decision_maker import Transport, maker

from autonomous_mixing.adapters.decision_agent.prompt import PROMPT, provider_identity
from autonomous_mixing.adapters.decision_agent.response import RESPONSE_SCHEMA
from autonomous_mixing.adapters.journal.decision_exchange import SqliteDecisionExchangeStore
from autonomous_mixing.core.records.decision_evidence import DecisionEvidenceRejected, DecisionEvidenceVerified
from autonomous_mixing.core.records.decision_exchange import ExchangeFound, ExchangeReadUnavailable, call_key_of
from autonomous_mixing.core.records.provider_run import ProviderRunBinding
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode
from autonomous_mixing.drivers.decision_evidence import ConfiguredDecisionEvidenceSource


def binding():
    return ProviderRunBinding(provider_identity(config()), PROMPT, RESPONSE_SCHEMA, experimental_policy())


@pytest.fixture
def exchange(tmp_path):
    store = SqliteDecisionExchangeStore.create(tmp_path / "audit.sqlite", situation().run_id, max_record_bytes=1000000)
    yield store
    store.close()


def source(store, **kw):
    return ConfiguredDecisionEvidenceSource(binding=binding(), config=config(), audit=store, **kw)


def test_exact_completed_exchange_permits_only_its_own_returned_result(exchange):
    s = situation()
    outcome = maker(exchange).decide(s)
    proof = source(exchange).verify(s, outcome)
    assert isinstance(proof, DecisionEvidenceVerified)
    assert proof.situation == s
    assert proof.result == outcome.value
    assert proof.exchange == exchange.read(call_key_of(s)).record
    other = replace(outcome.value, decision=replace(outcome.value.decision, rationale="different"))
    assert isinstance(source(exchange).verify(s, type(outcome)(other)), DecisionEvidenceRejected)


@pytest.mark.parametrize(
    "change",
    [
        lambda s: replace(s, limits=replace(s.limits, max_decision_calls=s.limits.max_decision_calls + 1)),
        lambda s: replace(
            s,
            observations=tuple(
                replace(o, result=replace(o.result, value=o.result.value + 1)) if hasattr(o.result, "value") else o
                for o in s.observations
            ),
        ),
        lambda s: replace(
            s,
            context=replace(s.context, version="different"),
            capabilities=replace(s.capabilities, context_version="different"),
        ),
    ],
)
def test_same_call_or_identifiers_do_not_hide_changed_input(exchange, change):
    s = situation()
    outcome = maker(exchange).decide(s)
    assert isinstance(source(exchange).verify(change(s), outcome), DecisionEvidenceRejected)


def test_missing_or_unreadable_audit_never_permits_use(exchange):
    failure = PortFailure(PortFailureCode.UNAVAILABLE, "not recorded", True)
    assert isinstance(source(exchange).verify(situation(), failure), DecisionEvidenceRejected)

    class Unreadable:
        def read(self, call):
            return ExchangeReadUnavailable(call, "unavailable")

    assert isinstance(source(Unreadable()).verify(situation(), failure), DecisionEvidenceRejected)


def test_recorded_failure_is_correlated_without_fabricated_abstention(exchange):
    transport = Transport(
        value=__import__(
            "autonomous_mixing.adapters.decision_agent.transport", fromlist=["TransportResult"]
        ).TransportResult(None, None, None, "test", "connection_failed", True)
    )
    outcome = maker(exchange, transport).decide(situation())
    proof = source(exchange).verify(situation(), outcome)
    assert isinstance(proof, DecisionEvidenceVerified)
    assert proof.result == outcome and proof.exchange.completion.failure == outcome
    assert isinstance(source(exchange).verify(situation(), replace(outcome, message="other")), DecisionEvidenceRejected)


def test_pending_and_answered_exchange_are_not_completed(exchange):
    s = situation()
    outcome = maker(exchange).decide(s)
    record = exchange.read(call_key_of(s)).record

    class View:
        def __init__(self, r):
            self.r = r

        def read(self, call):
            return ExchangeFound(self.r)

    for r in (replace(record, response=None, completion=None), replace(record, completion=None)):
        assert isinstance(source(View(r)).verify(s, outcome), DecisionEvidenceRejected)


def test_config_must_equal_the_frozen_provider_binding(exchange):
    with pytest.raises(ValueError):
        ConfiguredDecisionEvidenceSource(binding=binding(), config=replace(config(), model="other"), audit=exchange)


@pytest.mark.parametrize("field", ["prompt", "response_schema", "provider", "withheld", "payload"])
def test_complete_request_binding_is_checked_not_just_call_and_outcome(exchange, field):
    from autonomous_mixing.core.records.decision_exchange import sha256_text
    from autonomous_mixing.core.records.identifiers import VersionRef

    s = situation()
    outcome = maker(exchange).decide(s)
    record = exchange.read(call_key_of(s)).record
    request = record.request
    if field in ("prompt", "response_schema"):
        request = replace(request, **{field: VersionRef("other", "1")})
    elif field == "provider":
        request = replace(request, provider=replace(request.provider, requested_model="other"))
    elif field == "withheld":
        local = request.packet.binding
        assert local.withheld
        local = replace(local, withheld=(replace(local.withheld[0], text="changed local only"), *local.withheld[1:]))
        request = replace(request, packet=replace(request.packet, binding=local))
    else:
        # A canonical but changed public data packet, under the same call and exact returned outcome.
        payload = request.packet.payload.replace('"decision_call":3', '"decision_call":99')
        assert payload != request.packet.payload
        request = replace(request, packet=replace(request.packet, payload=payload, payload_sha256=sha256_text(payload)))

    class OtherAudit:
        def read(self, call):
            return ExchangeFound(replace(record, request=request))

    assert isinstance(source(OtherAudit()).verify(s, outcome), DecisionEvidenceRejected)


# --- completeness regressions against plan 4.3/4.4 (2026-11-23) ------------------------------------------------------


def _changed_preference(s):
    first = s.context.preferences[0]
    return replace(s, context=replace(s.context, preferences=(replace(first, description="a different intent"),
                                                               *s.context.preferences[1:])))  # fmt: skip


def _changed_threshold(s):
    target = s.criteria.targets[0]
    changed = replace(target, threshold=replace(target.threshold, value=target.threshold.value - 1.0))
    return replace(s, criteria=replace(s.criteria, targets=(changed, *s.criteria.targets[1:])))


def _changed_capability(s):
    first = s.capabilities.gain_actions[0]
    bounds = replace(first.bounds, minimum_db=first.bounds.minimum_db - 1.0)
    return replace(s, capabilities=replace(s.capabilities, gain_actions=(replace(first, bounds=bounds),
                                                                        *s.capabilities.gain_actions[1:])))  # fmt: skip


def _changed_history(s):
    entry = s.history.entries[0]
    changed = replace(entry, outcome=replace(entry.outcome, reason_code="a_different_recorded_reason"))
    return replace(s, history=replace(s.history, entries=(changed, *s.history.entries[1:])))


@pytest.mark.parametrize("change", [_changed_preference, _changed_threshold, _changed_capability, _changed_history])
def test_changed_intent_threshold_capability_or_history_behind_unchanged_ids_is_rejected(exchange, change):
    s = situation()
    outcome = maker(exchange).decide(s)
    assert isinstance(source(exchange).verify(s, outcome), DecisionEvidenceVerified)
    changed = change(s)
    assert changed != s and changed.run_id == s.run_id and changed.usage == s.usage
    assert isinstance(source(exchange).verify(changed, outcome), DecisionEvidenceRejected)


def test_local_oversized_request_leaves_no_exchange_and_is_never_verified(exchange):
    from decision_support import Clock, Tokens

    from autonomous_mixing.adapters.decision_agent.claude import ClaudeDecisionMaker
    from autonomous_mixing.core.records.decision_exchange import ExchangeAbsent

    s = situation()
    transport = Transport()
    transport.config = config(max_request_bytes=64)
    small = ClaudeDecisionMaker(config=transport.config, transport=transport, audit=exchange, identities=Tokens(),
                                clock=Clock())  # fmt: skip
    outcome = small.decide(s)
    assert isinstance(outcome, PortFailure) and outcome.code is PortFailureCode.INVALID_INPUT
    assert transport.calls == []  # no provider request occurred
    assert isinstance(exchange.read(call_key_of(s)), ExchangeAbsent)  # nothing fabricated
    assert isinstance(source(exchange).verify(s, outcome), DecisionEvidenceRejected)


def test_response_recording_failure_stays_pending_and_unverified(exchange):
    from autonomous_mixing.core.records.decision_exchange import AuditNotRecorded, AuditRefusal, ExchangeStatus

    class RefusesResponses:
        def __getattr__(self, name):
            return getattr(exchange, name)

        def record_response(self, response):
            return AuditNotRecorded(response.call, AuditRefusal.STORE_UNAVAILABLE, "labelled recording failure")

    s = situation()
    outcome = maker(RefusesResponses()).decide(s)
    assert isinstance(outcome, PortFailure)
    assert exchange.read(call_key_of(s)).record.status is ExchangeStatus.PENDING  # recorded request, no completion
    assert isinstance(source(exchange).verify(s, outcome), DecisionEvidenceRejected)


def test_decision_exchange_store_stays_schema_one_with_unchanged_record_shapes(tmp_path):
    from dataclasses import fields

    from autonomous_mixing.adapters.journal import decision_exchange as store_module
    from autonomous_mixing.core.records import decision_exchange as records

    assert store_module.SCHEMA_VERSION == 1
    pinned = {
        records.DecisionExchangeRequest: ("call", "exchange_id", "packet", "request_text", "request_sha256", "prompt",
                                          "response_schema", "provider", "requested_at"),
        records.RecordedDecisionResponse: ("call", "exchange_id", "request_sha256", "envelope", "body_base64",
                                           "body_sha256", "stop_reason", "provider_request_id", "resolved_model",
                                           "metadata", "error_code", "recorded_at", "elapsed_seconds"),
        records.CompletedDecisionExchange: ("call", "exchange_id", "request_sha256", "completion", "response_sha256",
                                            "decision", "failure", "detail", "finished_at"),
        records.DecisionPacket: ("schema", "alias_strategy", "payload", "payload_sha256", "binding"),
        records.DecisionPacketBinding: ("call", "project_id", "revision", "context_id", "context_version",
                                        "controller", "aliases", "alias_fingerprint", "withheld"),
    }  # fmt: skip
    for cls, names in pinned.items():
        assert tuple(f.name for f in fields(cls)) == names, cls.__name__
    # a schema-1 exchange written now reopens and round-trips exactly
    s = situation()
    path = tmp_path / "old-shape.sqlite"
    store = SqliteDecisionExchangeStore.create(path, s.run_id, max_record_bytes=1000000)
    maker(store).decide(s)
    written = store.read(call_key_of(s)).record
    store.close()
    reopened = SqliteDecisionExchangeStore.open(path, run_id=s.run_id, max_record_bytes=1000000)
    assert reopened.read(call_key_of(s)).record == written
    reopened.close()
