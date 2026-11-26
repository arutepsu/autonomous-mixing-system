"""Tests for strict structured-response parsing (R3 Task 3).

Response bodies here are labelled contract fixtures written for the tests, not answers from any model. A parsed
proposal is a proposal: it is never an admitted action, an execution receipt or permission to call a tool.
"""

import base64
import dataclasses
import hashlib
import json
from datetime import timedelta

import pytest
from decision_support import UTC, Tokens, criteria, diagnosis, observation, request_for, situation

import autonomous_mixing.adapters.decision_agent.response as response_module
from autonomous_mixing.adapters.decision_agent.request import PacketLimits, build_decision_packet
from autonomous_mixing.adapters.decision_agent.response import (
    RESPONSE_JSON_SCHEMA,
    RESPONSE_SCHEMA,
    ResponseLimits,
    parse_decision_response,
)
from autonomous_mixing.core.records.actions import Action, ExecutionReceipt
from autonomous_mixing.core.records.budgets import BudgetUsage
from autonomous_mixing.core.records.decision import (
    Abstain,
    AbstentionReason,
    ControllerIdentity,
    DecisionResponse,
    NoChange,
    ProposeAction,
)
from autonomous_mixing.core.records.decision_exchange import (
    AliasKind,
    DecisionCallKey,
    RecordedDecisionResponse,
    ResponseEnvelope,
)
from autonomous_mixing.core.records.feedback import DecisionHistory
from autonomous_mixing.core.records.identifiers import ActionId, DecisionId, ProblemCandidateId, Revision, TrackId
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, Success

LIMITS = ResponseLimits(max_response_bytes=1 << 16)


@pytest.fixture
def s():
    return situation()


@pytest.fixture
def packet(s):
    return build_decision_packet(s, limits=PacketLimits(1 << 20))


def alias(packet, kind, source, revision=None):
    (found,) = [a.alias for a in packet.binding.aliases
                if a.kind is kind and a.source_id == source and a.revision == revision]  # fmt: skip
    return found


def recorded(
    body: bytes | None, packet, *, envelope=ResponseEnvelope.COMPLETE, request=None
) -> RecordedDecisionResponse:
    received = body is not None
    request = request or request_for(packet)
    return RecordedDecisionResponse(request.call, request.exchange_id, request.request_sha256, envelope,
                                    base64.b64encode(body).decode() if received else None,
                                    hashlib.sha256(body).hexdigest() if received else None, None, None, None, "{}",
                                    None if received else "timeout", UTC + timedelta(seconds=1), 1.0)  # fmt: skip


def proposal(packet, **overrides) -> dict:
    document = {
        "type": "propose_action",
        "action": {"operation": "set_track_gain", "track": alias(packet, AliasKind.TRACK, "t1"), "gain_db": -1.5},
        "addressed_problem_candidates": [alias(packet, AliasKind.CANDIDATE, "cand-1")],
        "evidence_refs": [alias(packet, AliasKind.CURRENT_OBSERVATION, o) for o in ("o-1", "o-2")],
        "expected_effect": "lower overlap of t1 with the pad in the candidate region",
        "rationale": "the labelled candidate cites both observations",
        "justification": {
            "reasoning": "a small cut addresses the candidate",
            "uncertainty": "uncalibrated evidence",
            "assumptions": ["the role labels are accurate"],
            "limitations": [],
        },  # fmt: skip
    }
    document.update(overrides)
    return document


def parse(document, packet, s, *, identities=None, raw=None, envelope=ResponseEnvelope.COMPLETE, limits=LIMITS):
    body = raw if raw is not None else json.dumps(document).encode()
    return parse_decision_response(recorded(body, packet, envelope=envelope), request=request_for(packet), situation=s,
                                   identities=identities or Tokens("local"), limits=limits)  # fmt: skip


def invalid(result) -> str:
    assert isinstance(result, PortFailure), result
    assert result.code is PortFailureCode.INVALID_RESPONSE and result.retryable is False
    return result.message


# --- valid outcomes -------------------------------------------------------------------------------------------------


def test_valid_proposal_is_bound_locally_with_local_identities(packet, s):
    result = parse(proposal(packet), packet, s, identities=Tokens("local"))
    assert isinstance(result, Success) and isinstance(result.value, DecisionResponse)
    decision = result.value.decision
    assert decision.decision_id == DecisionId("local-1")  # decision ID first, then the action ID
    assert (decision.project_id, decision.expected_revision) == (s.state.project_id, s.state.revision)
    assert (decision.context_id, decision.context_version, decision.controller) == (
        s.context.context_id, s.context.version, s.controller)  # fmt: skip
    outcome = decision.outcome
    assert isinstance(outcome, ProposeAction)
    assert outcome.action.action_id == ActionId("local-2") and outcome.action.operation.track_id == TrackId("t1")
    assert outcome.action.operation.value_db == -1.5 and outcome.candidate_ids == (ProblemCandidateId("cand-1"),)
    assert [str(e) for e in decision.evidence_ids] == ["o-1", "o-2"]
    assert outcome.justification.assumptions == ("the role labels are accurate",)


def test_a_valid_proposal_is_never_an_admitted_action_receipt_or_tool_permission(packet, s):
    value = parse(proposal(packet), packet, s).value
    assert type(value) is DecisionResponse and not isinstance(value, ExecutionReceipt)
    assert isinstance(value.decision.outcome.action, Action)  # a proposed action record, not admission
    assert not hasattr(value, "admitted") and not hasattr(value.decision, "tool")
    imported = set(vars(response_module))
    assert not any("admission" in name or "admit" in name for name in imported)


def test_out_of_bounds_gain_stays_a_proposal_for_admission_to_judge(packet, s):
    document = proposal(packet, action={"operation": "set_track_gain", "track": alias(packet, AliasKind.TRACK, "t1"),
                                        "gain_db": 40})  # fmt: skip
    value = parse(document, packet, s).value
    assert value.decision.outcome.action.operation.value_db == 40.0  # never clamped


def test_no_change(packet, s):
    document = {"type": "no_change", "rationale": "current gains meet the stated preference", "evidence_refs": [],
                "explanation": "keep the accepted state"}  # fmt: skip
    decision = parse(document, packet, s).value.decision
    assert decision.outcome == NoChange("keep the accepted state") and decision.evidence_ids == ()


@pytest.mark.parametrize("reason", [r for r in AbstentionReason if r is not AbstentionReason.MISSING_CONTEXT])
def test_abstain(packet, s, reason):
    document = {"type": "abstain", "rationale": "public reason", "evidence_refs": [], "reason": reason.value,
                "requested_context": []}  # fmt: skip
    assert parse(document, packet, s).value.decision.outcome == Abstain(reason)


def test_missing_context_requires_questions(packet, s):
    document = {"type": "abstain", "rationale": "role of t3 is unknown", "evidence_refs": [], "reason": "missing_context",
                "requested_context": ["Which role does the third track have?"]}  # fmt: skip
    outcome = parse(document, packet, s).value.decision.outcome
    assert outcome.reason is AbstentionReason.MISSING_CONTEXT and outcome.requested_context
    document["requested_context"] = []
    invalid(parse(document, packet, s))


def test_insufficient_evidence_is_neither_changed_nor_replaced(s):
    insufficient = situation(diagnosis=diagnosis(insufficient=True))
    packet = build_decision_packet(insufficient, limits=PacketLimits(1 << 20))
    value = parse(proposal(packet), packet, insufficient).value  # structurally valid; the gate decides sufficiency
    assert insufficient.diagnosis.candidates[0].evidence.insufficient_evidence is True
    assert value.decision.outcome.candidate_ids == (ProblemCandidateId("cand-1"),)


def test_replayed_identities_reproduce_the_exact_recorded_decision(packet, s):
    first = parse(proposal(packet), packet, s, identities=Tokens("run")).value
    again = parse(proposal(packet), packet, s, identities=Tokens(tokens=["run-1", "run-2"])).value
    assert first == again


# --- malformed text and JSON ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff\xfe{}",  # invalid UTF-8
        b'```json\n{"type":"no_change"}\n```',  # fenced prose
        b'Here is my answer: {"type":"no_change"}',
        b'{"type":"no_change","rationale":"r","evidence_refs":[],"explanation":"e"} {"type":"no_change"}',  # two objects
        b'{"type":"no_change","rationale":"r","evidence_refs":[],"explanation":"e"}\n',  # trailing data
        b' {"type":"no_change","rationale":"r","evidence_refs":[],"explanation":"e"}',
        b'{"type":"no_change","rationale":"r","evidence_refs":[],"explanation":"e","explanation":"f"}',  # duplicate key
        b'[{"type":"no_change"}]',
        b'{"type":"no_change","rationale":"r","evidence_refs":[],"explanation":"e"',  # truncated
        b"",
    ],
)
def test_malformed_text_is_invalid_without_repair(packet, s, raw):
    invalid(parse(None, packet, s, raw=raw))


@pytest.mark.parametrize("gain", ["NaN", "Infinity", "-Infinity", "1e400", "true", '"-1.5"', "null", "[1]"])
def test_non_finite_or_wrongly_typed_gain_is_invalid(packet, s, gain):
    text = json.dumps(proposal(packet)).replace("-1.5", gain).encode()
    invalid(parse(None, packet, s, raw=text))


def test_integer_gain_is_a_number(packet, s):
    document = proposal(packet, action={"operation": "set_track_gain", "track": alias(packet, AliasKind.TRACK, "t1"),
                                        "gain_db": -2})  # fmt: skip
    assert parse(document, packet, s).value.decision.outcome.action.operation.value_db == -2.0


@pytest.mark.parametrize(
    "change",
    [
        {"extra": 1},
        {"type": "propose_actions"},
        {"type": "no_change"},  # wrong variant for these keys
        {"decision_id": "d-9"},  # the model may not supply identities
        {"project": "p"},
        {"criteria": []},
        {"actions": []},  # multiple proposals
        {"evidence_refs": "o-1"},
        {"addressed_problem_candidates": []},
        {"expected_effect": ""},
        {"rationale": 3},
        {"justification": {"reasoning": "r", "uncertainty": "u", "assumptions": []}},  # missing key
        {
            "justification": {
                "reasoning": "r",
                "uncertainty": "u",
                "assumptions": [],
                "limitations": [],
                "chain_of_thought": "x",
            }
        },  # fmt: skip
    ],
)
def test_schema_violations_are_invalid(packet, s, change):
    invalid(parse(proposal(packet, **change), packet, s))


def test_missing_keys_are_invalid(packet, s):
    for key in ("type", "action", "addressed_problem_candidates", "evidence_refs", "expected_effect", "rationale", "justification"):
        document = proposal(packet)
        del document[key]
        invalid(parse(document, packet, s))


@pytest.mark.parametrize(
    "action",
    [
        {"operation": "set_track_pan", "track": None, "gain_db": -1.0},
        {"operation": "set_track_gain", "gain_db": -1.0},
        {"operation": "set_track_gain", "track": None, "gain_db": -1.0, "normalized_fader": 0.5},
    ],
)
def test_unsupported_operations_and_action_shapes_are_invalid(packet, s, action):
    if "track" in action:
        action["track"] = alias(packet, AliasKind.TRACK, "t1")
    invalid(parse(proposal(packet, action=action), packet, s))


# --- references -----------------------------------------------------------------------------------------------------


def test_unknown_duplicate_and_wrong_kind_references_are_invalid(packet, s):
    t1 = alias(packet, AliasKind.TRACK, "t1")
    cand = alias(packet, AliasKind.CANDIDATE, "cand-1")
    o1, o2 = (alias(packet, AliasKind.CURRENT_OBSERVATION, o) for o in ("o-1", "o-2"))
    for change in (
        {"action": {"operation": "set_track_gain", "track": "trk-000000000000", "gain_db": -1.0}},
        {"action": {"operation": "set_track_gain", "track": "t1", "gain_db": -1.0}},  # raw ID, not an alias
        {"action": {"operation": "set_track_gain", "track": cand, "gain_db": -1.0}},  # wrong kind
        {"addressed_problem_candidates": [cand, cand]},
        {"addressed_problem_candidates": [t1]},
        {"evidence_refs": [o1, o1, o2]},
        {"evidence_refs": [o1, "obs-ffffffffffff"]},
        {"evidence_refs": [o1]},  # does not cite all of the candidate's evidence (core validation)
    ):
        message = invalid(parse(proposal(packet, **change), packet, s))
        assert message


def test_historical_observations_cannot_be_cited_as_current_evidence(packet, s):
    historical = alias(packet, AliasKind.HISTORICAL_OBSERVATION, "o-1", Revision("r2"))
    o2 = alias(packet, AliasKind.CURRENT_OBSERVATION, "o-2")
    message = invalid(parse(proposal(packet, evidence_refs=[historical, o2]), packet, s))
    assert "historical" in message


def test_envelope_must_be_complete_and_refusal_is_not_an_abstention(packet, s):
    body = json.dumps(proposal(packet)).encode()
    invalid(parse(None, packet, s, raw=body, envelope=ResponseEnvelope.INCOMPLETE))
    refused = parse(None, packet, s, raw=body, envelope=ResponseEnvelope.REFUSED)
    assert isinstance(refused, PortFailure) and refused.code is PortFailureCode.UNAVAILABLE
    assert not refused.retryable and "refus" in refused.message
    missing = parse_decision_response(recorded(None, packet, envelope=ResponseEnvelope.NOT_RECEIVED),
                                      request=request_for(packet), situation=s, identities=Tokens(),
                                      limits=LIMITS)  # fmt: skip
    assert isinstance(missing, PortFailure) and missing.code is PortFailureCode.UNAVAILABLE


def test_oversize_body_is_invalid(packet, s):
    body = json.dumps(proposal(packet)).encode()
    assert isinstance(parse(None, packet, s, raw=body, limits=ResponseLimits(len(body))), Success)
    invalid(parse(None, packet, s, raw=body, limits=ResponseLimits(len(body) - 1)))


# --- stale bindings -------------------------------------------------------------------------------------------------


def test_a_packet_for_another_call_or_state_is_a_wiring_error(packet, s):
    usage_is_data = build_decision_packet(situation(usage=BudgetUsage(3, 2, 1, 0, 42.5)), limits=PacketLimits(1 << 20))
    assert usage_is_data.binding == packet.binding  # usage changes the data, not the binding
    earlier_call = situation(history=DecisionHistory(s.run_id, s.history.project_id, s.history.entries[:1]),
                             usage=BudgetUsage(2, 1, 1, 0, 42.5))  # fmt: skip
    with pytest.raises(ValueError, match="binding"):
        parse(proposal(packet), packet, earlier_call)
    other_body = b"{}"
    wrong_call = RecordedDecisionResponse(DecisionCallKey(s.run_id, 9), "ex-1", request_for(packet).request_sha256,
                                          ResponseEnvelope.COMPLETE,
                                          base64.b64encode(other_body).decode(), hashlib.sha256(other_body).hexdigest(),
                                          None, None, None, "{}", None, UTC, 0.0)  # fmt: skip
    with pytest.raises(ValueError, match="call"):
        parse_decision_response(wrong_call, request=request_for(packet), situation=s, identities=Tokens(),
                                limits=LIMITS)  # fmt: skip


def test_response_schema_is_versioned_canonical_and_closed():
    schema = json.loads(RESPONSE_JSON_SCHEMA)
    assert RESPONSE_SCHEMA.version == "2"
    variants = {v["properties"]["type"]["const"] for v in schema["oneOf"]}
    assert variants == {"propose_action", "no_change", "abstain"}
    assert all(v["additionalProperties"] is False for v in schema["oneOf"])


def test_identity_source_errors_are_wiring_errors_not_invalid_responses(packet, s):
    class Broken:
        def next_token(self):
            return 7

    with pytest.raises(ValueError, match="string token"):
        parse(proposal(packet), packet, s, identities=Broken())


# --- review regressions (Codex P2, 2026-11-22) -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "document",
    [
        {"type": []},
        {"type": {}},
        {"type": None},
        {"type": "abstain", "rationale": "r", "evidence_refs": [], "reason": [], "requested_context": []},
        {"type": "abstain", "rationale": "r", "evidence_refs": [], "reason": {}, "requested_context": []},
        {
            "type": "propose_action",
            "action": {"operation": [], "track": "x", "gain_db": 1},
            "addressed_problem_candidates": [],
            "evidence_refs": [],
            "expected_effect": "e",
            "rationale": "r",
            "justification": {"reasoning": "r", "uncertainty": "u", "assumptions": [], "limitations": []},
        },
    ],
)
def test_wrongly_typed_discriminators_are_invalid_not_crashes(packet, s, document):
    invalid(parse(document, packet, s))


def test_changed_criterion_contents_with_the_same_ids_refuse_the_old_packet(packet, s):
    stricter = criteria()
    target = stricter.targets[0]
    changed = dataclasses.replace(stricter, targets=(dataclasses.replace(
        target, threshold=dataclasses.replace(target.threshold, value=-999.0)),))  # fmt: skip
    with pytest.raises(ValueError, match="binding"):
        parse(proposal(packet), packet, situation(criteria=changed))


def test_changed_measurement_with_the_same_id_refuses_the_old_packet(packet, s):
    altered = tuple(observation("o-1", value=-3.0) if o.observation_id.value == "o-1" else o for o in s.observations)
    with pytest.raises(ValueError, match="binding"):
        parse(proposal(packet), packet, situation(observations=altered))


@pytest.mark.parametrize("change", [{"exchange_id": "ex-other"}, {"request_sha256": "b" * 64}])
def test_a_response_of_another_exchange_or_request_is_refused_before_interpretation(packet, s, change):
    request = request_for(packet)
    foreign = dataclasses.replace(recorded(json.dumps(proposal(packet)).encode(), packet, request=request), **change)
    with pytest.raises(ValueError, match="exchange|request"):
        parse_decision_response(foreign, request=request, situation=s, identities=Tokens(), limits=LIMITS)


def test_the_request_fingerprint_is_not_the_packet_data_hash(packet):
    request = request_for(packet)
    assert request.request_sha256 != packet.payload_sha256


def test_binding_only_differences_refuse_the_old_packet(packet, s):
    # neither changes the outbound payload: one is withheld text, the other a local binding field
    report = s.diagnosis
    reworded = dataclasses.replace(report, coverage=tuple(dataclasses.replace(c, explanation=f"{c.explanation} (v2)")
                                                          for c in report.coverage))  # fmt: skip
    changed = situation(diagnosis=reworded)
    assert build_decision_packet(changed, limits=PacketLimits(1 << 20)).payload == packet.payload
    with pytest.raises(ValueError, match="binding"):
        parse(proposal(packet), packet, changed)
    other_controller = situation(controller=ControllerIdentity("another-controller", "9"))
    with pytest.raises(ValueError, match="binding"):
        parse(proposal(packet), packet, other_controller)


# --- review regressions (Codex P2, second pass, 2026-11-22): bounded but hostile JSON -------------------------------


def _with_gain(packet, literal: str) -> bytes:
    return json.dumps(proposal(packet)).replace("-1.5", literal).encode()


def test_deeply_nested_json_within_the_byte_bound_is_invalid_not_a_recursion_error(packet, s):
    nested = proposal(packet)
    nested["evidence_refs"] = "@@"
    body = json.dumps(nested).replace('"@@"', "[" * 1200 + "]" * 1200).encode()
    assert len(body) < LIMITS.max_response_bytes
    message = invalid(parse(None, packet, s, raw=body))
    assert "JSON" in message


def test_a_huge_integer_within_the_byte_bound_is_invalid_not_a_value_error(packet, s):
    body = _with_gain(packet, "9" * 5000)
    assert len(body) < LIMITS.max_response_bytes
    invalid(parse(None, packet, s, raw=body))


def test_an_overflowing_but_decodable_integer_is_invalid(packet, s):
    invalid(parse(None, packet, s, raw=_with_gain(packet, "9" * 400)))


def test_decoding_errors_are_handled_without_raising_global_limits(packet, s):
    import sys

    before = (sys.getrecursionlimit(), sys.get_int_max_str_digits())
    parse(None, packet, s, raw=_with_gain(packet, "9" * 5000))
    assert (sys.getrecursionlimit(), sys.get_int_max_str_digits()) == before


def test_wiring_errors_still_propagate_around_decoding(packet, s, monkeypatch):
    def broken(*_args, **_kwargs):
        raise TypeError("programming error in the decoder wiring")

    monkeypatch.setattr(response_module.json, "loads", broken)
    with pytest.raises(TypeError, match="programming error"):
        parse(proposal(packet), packet, s)
