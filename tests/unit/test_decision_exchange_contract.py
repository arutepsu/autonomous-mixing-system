"""Contract tests for decision-packet and exchange-audit records (R3 Task 1).

Labelled contract fixtures only (``decision_support``); no provider was called and no text here is a model answer.
"""

import base64
import dataclasses
import hashlib
import json
from datetime import timedelta

import pytest
from decision_support import CONTROLLER, RUN, UTC, P, situation

from autonomous_mixing.core.ports.decision_audit import permits_dispatch
from autonomous_mixing.core.records.actions import Action, SetTrackGain
from autonomous_mixing.core.records.decision import ActionJustification, Decision, NoChange, ProposeAction
from autonomous_mixing.core.records.decision_exchange import (
    AliasBinding,
    AliasKind,
    AuditNotRecorded,
    AuditRecorded,
    AuditRefusal,
    CompletedDecisionExchange,
    DecisionCallKey,
    DecisionExchangeRecord,
    DecisionExchangeRequest,
    DecisionPacket,
    DecisionPacketBinding,
    ExchangeCompletion,
    ExchangeStatus,
    ProviderRequestIdentity,
    RecordedDecisionResponse,
    ResponseEnvelope,
    WithheldDetail,
    alias_fingerprint,
    call_key_of,
    canonical_json,
)
from autonomous_mixing.core.records.identifiers import (
    ActionId,
    ContextId,
    DecisionId,
    ObservationId,
    ProblemCandidateId,
    Revision,
    RunId,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode

KEY = DecisionCallKey(RUN, 3)
SCHEMA, STRATEGY = VersionRef("packet-schema", "1"), VersionRef("alias-strategy", "1")
ALIASES = (
    AliasBinding("trk-a", AliasKind.TRACK, "t1", None),
    AliasBinding("obs-a", AliasKind.CURRENT_OBSERVATION, "o-1", None),
    AliasBinding("hob-a", AliasKind.HISTORICAL_OBSERVATION, "o-1", Revision("r1")),
    AliasBinding("cand-a", AliasKind.CANDIDATE, "cand-1", None),
)


def sha(text: str | bytes) -> str:
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


def binding(**overrides) -> DecisionPacketBinding:
    fields = dict(call=KEY, project_id=P, revision=Revision("r2"), context_id=ContextId("ctx-1"), context_version="1",
                  controller=CONTROLLER, aliases=ALIASES, alias_fingerprint=alias_fingerprint(ALIASES),
                  withheld=())  # fmt: skip
    fields.update(overrides)
    return DecisionPacketBinding(**fields)


def packet(payload='{"a":1}', **overrides) -> DecisionPacket:
    fields = dict(schema=SCHEMA, alias_strategy=STRATEGY, payload=payload, payload_sha256=sha(payload),
                  binding=binding())  # fmt: skip
    fields.update(overrides)
    return DecisionPacket(**fields)


REQUEST_TEXT = '{"messages":[{"content":"{\\"a\\":1}"}]}'


def request(**overrides) -> DecisionExchangeRequest:
    fields = dict(call=KEY, exchange_id="ex-1", packet=packet(), request_text=REQUEST_TEXT,
                  request_sha256=sha(REQUEST_TEXT), prompt=VersionRef("prompt", "1"),
                  response_schema=VersionRef("response-schema", "1"),
                  provider=ProviderRequestIdentity("provider-x", "api-1", "model-x", '{"max_tokens":100}'),
                  requested_at=UTC)  # fmt: skip
    fields.update(overrides)
    return DecisionExchangeRequest(**fields)


BODY = b'{"type":"no_change"}'


def response(body=BODY, envelope=ResponseEnvelope.COMPLETE, **overrides) -> RecordedDecisionResponse:
    received = body is not None
    fields = dict(call=KEY, exchange_id="ex-1", request_sha256=sha(REQUEST_TEXT), envelope=envelope,
                  body_base64=base64.b64encode(body).decode() if received else None,
                  body_sha256=sha(body) if received else None, stop_reason="end_turn" if received else None,
                  provider_request_id=None, resolved_model="model-x" if received else None, metadata="{}",
                  error_code=None if received else "timeout", recorded_at=UTC + timedelta(seconds=2),
                  elapsed_seconds=2.0)  # fmt: skip
    fields.update(overrides)
    return RecordedDecisionResponse(**fields)


def decision(outcome=None, evidence=()) -> Decision:
    return Decision(DecisionId("d-local-1"), P, Revision("r2"), ContextId("ctx-1"), "1", evidence, "public rationale",
                    CONTROLLER, outcome or NoChange("keep"))  # fmt: skip


def completion(kind=ExchangeCompletion.VALID_DECISION, **overrides) -> CompletedDecisionExchange:
    valid = kind is ExchangeCompletion.VALID_DECISION
    failure = None if valid else PortFailure(PortFailureCode.INVALID_RESPONSE, "bad", False)
    fields = dict(call=KEY, exchange_id="ex-1", request_sha256=sha(REQUEST_TEXT), completion=kind,
                  response_sha256=sha(BODY), decision=decision() if valid else None, failure=failure,
                  detail="local classification", finished_at=UTC + timedelta(seconds=3))  # fmt: skip
    fields.update(overrides)
    return CompletedDecisionExchange(**fields)


# --- call key ----------------------------------------------------------------------------------------------------


def test_call_key_is_the_charged_decision_call_of_the_situation():
    s = situation()
    assert call_key_of(s) == DecisionCallKey(RUN, s.usage.decision_calls) == DecisionCallKey(RUN, 3)


@pytest.mark.parametrize("ordinal", [0, -1, True, 1.0])
def test_call_ordinal_is_a_positive_int(ordinal):
    with pytest.raises(ValueError):
        DecisionCallKey(RUN, ordinal)


# --- packet and binding --------------------------------------------------------------------------------------------


def test_packet_payload_hash_and_canonical_text_are_checked():
    assert packet().payload_sha256 == sha('{"a":1}')
    with pytest.raises(ValueError, match="SHA-256"):
        packet(payload_sha256="0" * 64)
    for bad in ('{"a": 1}', "[1]", "not json", '{"a":NaN}', ""):
        with pytest.raises(ValueError):
            packet(payload=bad)


def test_canonical_json_is_sorted_compact_and_finite():
    assert canonical_json({"b": 1, "a": [1.5, "x"]}) == '{"a":[1.5,"x"],"b":1}'
    with pytest.raises(ValueError):
        canonical_json({"a": float("nan")})


def test_binding_fingerprint_and_alias_uniqueness():
    with pytest.raises(ValueError, match="fingerprint"):
        binding(alias_fingerprint="0" * 64)
    duplicate_alias = (*ALIASES, AliasBinding("trk-a", AliasKind.TRACK, "t2", None))
    with pytest.raises(ValueError, match="alias"):
        binding(aliases=duplicate_alias, alias_fingerprint=alias_fingerprint(duplicate_alias))
    duplicate_source = (*ALIASES, AliasBinding("trk-b", AliasKind.TRACK, "t1", None))
    with pytest.raises(ValueError, match="source"):
        binding(aliases=duplicate_source, alias_fingerprint=alias_fingerprint(duplicate_source))


def test_historical_observation_aliases_carry_their_revision_and_current_ones_do_not():
    with pytest.raises(ValueError):
        AliasBinding("hob-x", AliasKind.HISTORICAL_OBSERVATION, "o-1", None)
    with pytest.raises(ValueError):
        AliasBinding("obs-x", AliasKind.CURRENT_OBSERVATION, "o-1", Revision("r1"))
    b = binding()
    assert b.resolve("hob-a").kind is AliasKind.HISTORICAL_OBSERVATION and b.resolve("nope") is None


def test_data_identity_and_local_binding_identity_are_separate():
    one, other = packet(), packet(binding=binding(call=DecisionCallKey(RUN, 4)))
    assert one.payload_sha256 == other.payload_sha256  # same outbound data
    assert one.binding.fingerprint != other.binding.fingerprint  # different local call binding
    with pytest.raises(ValueError):
        dataclasses.replace(one, binding=None)


def test_packet_records_hold_only_immutable_values():
    p = packet()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.payload = "{}"  # type: ignore[misc]
    assert isinstance(p.binding.aliases, tuple)


# --- exchange phases ------------------------------------------------------------------------------------------------


def test_request_binds_call_packet_and_request_fingerprint():
    r = request()
    assert r.request_sha256 == sha(REQUEST_TEXT)
    with pytest.raises(ValueError, match="SHA-256|fingerprint"):
        request(request_sha256="0" * 64)
    with pytest.raises(ValueError, match="call"):
        request(call=DecisionCallKey(RUN, 4))
    with pytest.raises(ValueError):
        request(exchange_id=" ")


def test_provider_identity_parameters_are_canonical_and_never_credentials():
    with pytest.raises(ValueError):
        ProviderRequestIdentity("p", "api", "m", "{'not': json}")
    with pytest.raises(ValueError, match="credential"):
        ProviderRequestIdentity("p", "api", "m", '{"api_key":"sk-123"}')


def test_response_received_and_not_received_are_distinct_facts():
    received = response()
    assert received.body == BODY and received.envelope is ResponseEnvelope.COMPLETE
    missing = response(body=None, envelope=ResponseEnvelope.NOT_RECEIVED)
    assert missing.body is None and missing.error_code == "timeout"
    with pytest.raises(ValueError):
        response(body=None, envelope=ResponseEnvelope.COMPLETE)  # no body, yet "complete"
    with pytest.raises(ValueError):
        response(body=None, envelope=ResponseEnvelope.NOT_RECEIVED, error_code=None)
    with pytest.raises(ValueError):
        response(envelope=ResponseEnvelope.NOT_RECEIVED)  # body, yet "not received"
    with pytest.raises(ValueError, match="SHA-256"):
        response(body_sha256="0" * 64)
    with pytest.raises(ValueError):
        response(body_base64="***")


def test_completion_categories_have_their_shapes():
    assert completion().decision is not None
    invalid = completion(ExchangeCompletion.INVALID_RESPONSE)
    assert invalid.decision is None and invalid.failure.code is PortFailureCode.INVALID_RESPONSE
    with pytest.raises(ValueError):  # a valid decision without a decision
        completion(decision=None)
    with pytest.raises(ValueError):  # an invalid response that claims retryable
        completion(
            ExchangeCompletion.INVALID_RESPONSE, failure=PortFailure(PortFailureCode.INVALID_RESPONSE, "x", True)
        )
    with pytest.raises(ValueError):  # a failure carrying a decision
        completion(ExchangeCompletion.PROVIDER_FAILURE, decision=decision())
    with pytest.raises(ValueError):  # an interpreted response must name it
        completion(ExchangeCompletion.INVALID_RESPONSE, response_sha256=None)
    provider = completion(ExchangeCompletion.PROVIDER_FAILURE, response_sha256=None,
                          failure=PortFailure(PortFailureCode.UNAVAILABLE, "timeout", True))  # fmt: skip
    assert provider.failure.retryable  # not a fabricated Abstain


def test_completion_records_local_identities_of_a_proposal():
    proposal = ProposeAction(Action(ActionId("a-local-1"), P, Revision("r2"), SetTrackGain(TrackId("t1"), -1.0)),
                             "less overlap", (ProblemCandidateId("cand-1"),),
                             ActionJustification("because", "uncalibrated", (), ()))  # fmt: skip
    done = completion(decision=decision(proposal, (ObservationId("o-1"), ObservationId("o-2"))))
    assert done.decision.decision_id == DecisionId("d-local-1")
    assert done.decision.outcome.action.action_id == ActionId("a-local-1")


def test_phases_must_share_call_exchange_and_request_fingerprint():
    record = DecisionExchangeRecord(request(), response(), completion())
    assert record.status is ExchangeStatus.COMPLETED
    assert DecisionExchangeRecord(request(), None, None).status is ExchangeStatus.PENDING
    assert DecisionExchangeRecord(request(), response(), None).status is ExchangeStatus.RESPONSE_RECORDED
    for bad in (response(exchange_id="ex-2"), response(request_sha256="0" * 64),
                response(call=DecisionCallKey(RUN, 4))):  # fmt: skip
        with pytest.raises(ValueError):
            DecisionExchangeRecord(request(), bad, None)
    with pytest.raises(ValueError):
        DecisionExchangeRecord(request(), response(), completion(exchange_id="ex-2"))


def test_completion_cannot_hide_a_received_response():
    with pytest.raises(ValueError, match="response"):
        DecisionExchangeRecord(request(), response(), completion(response_sha256=sha(b"other")))
    with pytest.raises(ValueError, match="response"):  # interprets a response that was never recorded
        DecisionExchangeRecord(request(), None, completion())
    no_body = response(body=None, envelope=ResponseEnvelope.NOT_RECEIVED)
    failure = completion(ExchangeCompletion.PROVIDER_FAILURE, response_sha256=None,
                         failure=PortFailure(PortFailureCode.UNAVAILABLE, "timeout", True))  # fmt: skip
    assert DecisionExchangeRecord(request(), no_body, failure).status is ExchangeStatus.COMPLETED
    with pytest.raises(ValueError):
        DecisionExchangeRecord(request(), response(), failure)  # a received body must be referenced


def test_valid_decision_must_match_the_packet_binding_and_a_complete_envelope():
    wrong = dataclasses.replace(decision(), expected_revision=Revision("r1"))
    with pytest.raises(ValueError, match="binding"):
        DecisionExchangeRecord(request(), response(), completion(decision=wrong))
    incomplete = response(envelope=ResponseEnvelope.INCOMPLETE)
    with pytest.raises(ValueError, match="complete"):
        DecisionExchangeRecord(request(), incomplete, completion())


# --- audit results and dispatch permission ------------------------------------------------------------------------


def test_only_a_newly_recorded_pending_request_permits_dispatch():
    r = request()
    pending = DecisionExchangeRecord(r, None, None)
    assert permits_dispatch(AuditRecorded(pending, True), r)
    assert not permits_dispatch(AuditRecorded(pending, False), r)  # replayed pending: not permission to resend
    assert not permits_dispatch(AuditRecorded(DecisionExchangeRecord(r, response(), None), True), r)
    assert not permits_dispatch(AuditRecorded(pending, True), request(exchange_id="ex-9"))
    assert not permits_dispatch(AuditNotRecorded(KEY, AuditRefusal.CONFLICT, "different request"), r)


def test_canonical_metadata_text_is_required():
    with pytest.raises(ValueError):
        response(metadata="{ }")
    assert json.loads(response(metadata='{"input_tokens":3}').metadata) == {"input_tokens": 3}
    with pytest.raises(ValueError):
        DecisionCallKey(RunId(" "), 1)


def test_withheld_text_is_local_bound_and_attached_to_known_aliases():
    detail = WithheldDetail("obs-a", "unavailable_reason", "could not read /HIDDEN/x.wav")
    with_text = binding(withheld=(detail,))
    assert with_text.fingerprint != binding().fingerprint  # bound into the local identity
    with pytest.raises(ValueError, match="unknown aliases"):
        binding(withheld=(WithheldDetail("nope", "explanation", "x"),))
    with pytest.raises(ValueError, match="twice"):
        binding(withheld=(detail, WithheldDetail("obs-a", "unavailable_reason", "other")))
