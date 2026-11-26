"""Regression tests for decision interface v2 (ADR-045, Proposed): policy A, exact references, normal abstention.

Deterministic regression evidence only: no provider is called and nothing here measures provider reliability. The one
captured artifact is ``tests/fixtures/ht01_observe_001_model_text_v1.json``: the verbatim model text of the failed live
exchange ``ht01-observe-001`` (owner-approved; model text only, no credentials, audio or local bindings). Its aliases
are pseudonyms of that run and do not resolve in the labelled fixtures here; where a test needs the failure *shape*
against a fixture packet, it substitutes a fixture alias for the leading alias token and keeps the captured trailing
text, which is stated at each such test. Everything else uses the labelled contract fixtures of ``decision_support``.
"""

import dataclasses
import hashlib
import json
import re
from pathlib import Path

import pytest
from decision_support import LIMITS, UTC, Clock, Tokens, diagnosis, request_for, situation, state
from test_claude_config import config

import autonomous_mixing.adapters.decision_agent.claude as claude_module
import autonomous_mixing.adapters.decision_agent.prompt as prompt_module
from autonomous_mixing.adapters.decision_agent.claude import ClaudeDecisionMaker
from autonomous_mixing.adapters.decision_agent.prompt import PROMPT, SYSTEM, build_claude_request
from autonomous_mixing.adapters.decision_agent.request import (
    PACKET_SCHEMA,
    PROPOSAL_BASIS,
    PacketLimits,
    build_decision_packet,
)
from autonomous_mixing.adapters.decision_agent.response import (
    RESPONSE_JSON_SCHEMA,
    RESPONSE_SCHEMA,
    ResponseLimits,
    parse_decision_response,
    response_schema_for,
)
from autonomous_mixing.adapters.journal.decision_exchange import SqliteDecisionExchangeStore
from autonomous_mixing.core.admission.decision_gate import assess_decision
from autonomous_mixing.core.records.budgets import BudgetLimits, BudgetUsage
from autonomous_mixing.core.records.daw_binding import DawFaderReading, DawTrackBinding
from autonomous_mixing.core.records.decision import Abstain, AbstentionReason, NoChange
from autonomous_mixing.core.records.decision_exchange import AliasKind, DecisionCallKey, ExchangeCompletion
from autonomous_mixing.core.records.decision_gate import DeclineAction, KeepState, ReadyForAdmission, RequestContext
from autonomous_mixing.core.records.identifiers import Revision, VersionRef
from autonomous_mixing.core.records.project import DawParameterState, DawTrackParameter
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, Success

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ht01_observe_001_model_text_v1.json"
CAPTURED_SHA256 = "87d04fd69aa63c410efd2d08191cde5c8c9d3767b87941553706b0176919125d"
LEADING_ALIAS = re.compile(r"^(obs|cov|trk|cand|cond)-[0-9a-f]{12}")
BIG = PacketLimits(1 << 20)
RESPONSE_LIMITS = ResponseLimits(1 << 16)


def captured_text() -> bytes:
    body = FIXTURE.read_bytes()
    assert hashlib.sha256(body).hexdigest() == CAPTURED_SHA256  # verbatim, never edited
    return body


def packet_of(s):
    return build_decision_packet(s, limits=BIG)


def alias(packet, kind, source, revision=None) -> str:
    (found,) = [a.alias for a in packet.binding.aliases
                if a.kind is kind and a.source_id == source and a.revision == revision]  # fmt: skip
    return found


def parse(document_or_body, s, *, packet=None, request=None):
    from test_decision_response_parser import recorded

    packet = packet or packet_of(s)
    body = document_or_body if isinstance(document_or_body, bytes) else json.dumps(document_or_body).encode()
    request = request or request_for(packet)
    return parse_decision_response(recorded(body, packet, request=request), request=request, situation=s,
                                   identities=Tokens("local"), limits=RESPONSE_LIMITS)  # fmt: skip


def category(result) -> str:
    assert isinstance(result, PortFailure), result
    assert result.code is PortFailureCode.INVALID_RESPONSE and result.retryable is False
    found = re.match(r"invalid decision response \[([a-z_.]+)\]: ", result.message)
    assert found, result.message
    return found.group(1)


def abstain(reason="insufficient_evidence", evidence=(), questions=(), *, omit_evidence=False) -> dict:
    document = {"type": "abstain", "rationale": "public reason", "evidence_refs": list(evidence), "reason": reason,
                "requested_context": list(questions)}  # fmt: skip
    if omit_evidence:
        del document["evidence_refs"]
    return document


def proposal(packet, *, candidates, evidence, track=None) -> dict:
    return {
        "type": "propose_action",
        "action": {
            "operation": "set_track_gain",
            "track": track or alias(packet, AliasKind.TRACK, "t1"),
            "gain_db": -0.5,
        },  # fmt: skip
        "addressed_problem_candidates": list(candidates),
        "evidence_refs": list(evidence),
        "expected_effect": "fixture effect",
        "rationale": "fixture rationale",
        "justification": {"reasoning": "r", "uncertainty": "u", "assumptions": [], "limitations": []},
    }


def enums(node):
    """Every ``enum`` list anywhere in a schema document."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "enum":
                yield value
            yield from enums(value)
    elif isinstance(node, list):
        for item in node:
            yield from enums(item)


def variants(schema) -> set[str]:
    return {v["properties"]["type"]["const"] for v in schema["oneOf"]}


# --- the captured failure -------------------------------------------------------------------------------------------


def test_the_fixture_is_the_verbatim_captured_model_text_only():
    body = captured_text()
    document = json.loads(body)
    assert set(document) == {
        "type",
        "action",
        "candidates",
        "evidence",
        "expected_effect",
        "rationale",
        "justification",
    }  # fmt: skip  (response schema v1 keys)
    lowered = body.lower()
    for forbidden in (b"sk-ant", b"api_key", b"x-api-key", b"authorization", b".wav", b"riff", b"c:\\", b"source_id"):
        assert forbidden not in lowered


def test_the_captured_v1_text_is_invalid_under_v2_and_nothing_is_extracted():
    s = situation()
    result = parse(captured_text(), s)
    assert category(result) == "shape" and "evidence_refs" in result.message


def test_the_captured_reference_failures_stay_invalid_with_fixture_aliases():
    # Derived from the captured text: v2 key names, and each leading alias token replaced by a fixture alias of the
    # same kind; the captured trailing commentary and the captured "candidates" (action descriptions) are unchanged.
    s = situation()
    packet = packet_of(s)
    document = json.loads(captured_text())
    substitute = {"obs": alias(packet, AliasKind.CURRENT_OBSERVATION, "o-1"),
                  "cov": alias(packet, AliasKind.COVERAGE, "cov-1")}  # fmt: skip
    evidence = [LEADING_ALIAS.sub(lambda m: substitute.get(m.group(1), m.group(0)), item)
                for item in document["evidence"]]  # fmt: skip
    first = evidence[0]
    bare = substitute["obs"]
    assert first.startswith(bare + ":") and first != bare  # the alias exists; the appended prose makes it no alias
    v2 = proposal(packet, candidates=document["candidates"], evidence=evidence)
    result = parse(v2, s)
    assert category(result) == "reference.unknown" and repr(first) in result.message  # no prefix extraction
    only_candidates = proposal(packet, candidates=document["candidates"],
                               evidence=[alias(packet, AliasKind.CURRENT_OBSERVATION, o) for o in ("o-1", "o-2")])  # fmt: skip
    result = parse(only_candidates, s)
    assert category(result) == "reference.unknown" and "addressed_problem_candidates" in result.message


@pytest.mark.parametrize(
    "decorate",
    [
        lambda a: f"{a}: measured value",
        lambda a: f" {a}",
        lambda a: f"{a} ",
        lambda a: a.upper(),
        lambda a: f"{a} (current)",
        lambda a: f"{a}, {a}",
        lambda a: a[:-1],
    ],
)
def test_decorated_or_altered_aliases_are_refused_without_fuzzy_matching(decorate):
    s = situation()
    packet = packet_of(s)
    o1 = alias(packet, AliasKind.CURRENT_OBSERVATION, "o-1")
    assert category(parse(abstain(evidence=[decorate(o1)]), s)) == "reference.unknown"


def test_wrong_kind_historical_and_duplicate_references_are_categorized():
    s = situation()
    packet = packet_of(s)
    o1 = alias(packet, AliasKind.CURRENT_OBSERVATION, "o-1")
    for wrong in (alias(packet, AliasKind.COVERAGE, "cov-1"), alias(packet, AliasKind.CONDITION, "target-1"),
                  alias(packet, AliasKind.CANDIDATE, "cand-1")):  # fmt: skip
        assert category(parse(abstain(evidence=[wrong]), s)) == "reference.wrong_kind"
    historical = alias(packet, AliasKind.HISTORICAL_OBSERVATION, "o-1", Revision("r2"))
    assert category(parse(abstain(evidence=[historical]), s)) == "reference.historical"
    assert category(parse(abstain(evidence=[o1, o1]), s)) == "reference.duplicate"


# --- policy A: proposals need a supplied problem candidate ----------------------------------------------------------


def ht01_shaped():
    """Labelled fixture with the shape of the failed trial: an advertised gain control and declared criteria, but no
    supplied problem candidate (the masking coverage is assessed without candidates)."""
    return situation(diagnosis=diagnosis(candidates=False))


def test_an_empty_diagnosis_makes_proposals_unavailable_but_keeps_the_capability_visible():
    s = ht01_shaped()
    doc = json.loads(packet_of(s).payload)
    assert doc["schema"] == [PACKET_SCHEMA.name, "2"] and set(doc["diagnosis"]) == {"coverage", "problem_candidates"}
    assert doc["diagnosis"]["problem_candidates"] == [] and doc["actions"]["set_track_gain"]  # capability stays listed
    assert doc["criteria"]["targets"]  # criteria are still sent, as verification criteria
    options = doc["decision_options"]
    assert options["proposal_basis"] == PROPOSAL_BASIS == "supplied_problem_candidate"
    assert options["propose_action"] == {"available": False, "unavailable_because": ["no_problem_candidates"],
                                         "execution_limits": []}  # fmt: skip
    assert options["no_change"] == {"available": True} and options["abstain"]["available"] is True
    semantics = doc["semantics"]
    assert "not permission" in semantics["capability_vs_permission"]
    assert "not a diagnosed problem" in semantics["criteria_are_not_problems"]
    assert "not actions" in semantics["problem_candidates"]


def test_an_empty_diagnosis_schema_offers_only_no_change_and_abstain():
    s = ht01_shaped()
    packet = packet_of(s)
    schema = response_schema_for(packet)
    assert variants(schema) == {"no_change", "abstain"}
    body = json.loads(build_claude_request(s, config=config(), exchange_id="ex-1", requested_at=UTC)
                      .request_text)  # fmt: skip
    sent = body["output_config"]["format"]["schema"]
    assert "$id" not in json.dumps(sent) and "oneOf" not in sent
    assert {v["properties"]["type"]["enum"][0] for v in sent["anyOf"]} == {"no_change", "abstain"}


def test_a_proposal_without_a_supplied_candidate_is_still_refused_locally():
    s = ht01_shaped()
    packet = packet_of(s)
    o1, o2 = (alias(packet, AliasKind.CURRENT_OBSERVATION, o) for o in ("o-1", "o-2"))
    target = alias(packet, AliasKind.CONDITION, "target-1")
    assert category(parse(proposal(packet, candidates=[], evidence=[o1]), s)) == "contract"
    assert category(parse(proposal(packet, candidates=["cand-000000000000"], evidence=[o1]), s)) == "reference.unknown"
    # an unmet objective is not a proposal basis under policy A: a condition alias is the wrong kind
    assert category(parse(proposal(packet, candidates=[target], evidence=[o1, o2]), s)) == "reference.wrong_kind"


def test_with_a_supplied_candidate_a_valid_proposal_reaches_admission_routing():
    s = situation()
    packet = packet_of(s)
    assert variants(response_schema_for(packet)) == {"propose_action", "no_change", "abstain"}
    document = proposal(packet, candidates=[alias(packet, AliasKind.CANDIDATE, "cand-1")],
                        evidence=[alias(packet, AliasKind.CURRENT_OBSERVATION, o) for o in ("o-1", "o-2")])  # fmt: skip
    result = parse(document, s)
    assert isinstance(result, Success)
    assert isinstance(assess_decision(s, result.value), ReadyForAdmission)  # not permission; admission still decides


def test_no_advertised_gain_control_is_a_factual_unavailability_reason():
    s = situation(capabilities=dataclasses.replace(situation().capabilities, gain_actions=()))
    packet = packet_of(s)
    options = json.loads(packet.payload)["decision_options"]["propose_action"]
    assert options["available"] is False and options["unavailable_because"] == ["no_advertised_gain_control"]
    assert variants(response_schema_for(packet)) == {"no_change", "abstain"}


def test_exhausted_execution_budgets_are_explained_without_hiding_the_proposal_option():
    daw_binding = DawTrackBinding(VersionRef("daw-binding", "1"), state().project_id, state().snapshot.tracks[0].track_id,
                                  "project", 3, "insert", 0.0, DawFaderReading(0.8, 0.0))  # fmt: skip
    daw = DawParameterState(
        state().project_id, Revision("r2"), (DawTrackParameter(daw_binding, 0.0, DawFaderReading(0.8, 0.0)),)
    )
    zero_write = BudgetLimits(LIMITS.max_decision_calls, LIMITS.max_attempts, LIMITS.max_render_calls,
                              LIMITS.max_recovery_operations, LIMITS.max_elapsed_seconds, 0)  # fmt: skip
    s = situation(state=dataclasses.replace(state(), daw=daw), limits=zero_write)
    options = json.loads(packet_of(s).payload)["decision_options"]["propose_action"]
    assert options == {"available": True, "unavailable_because": [], "execution_limits": ["no_remaining_daw_writes"]}
    offline = situation(limits=dataclasses.replace(LIMITS, max_attempts=1, max_render_calls=1),
                        usage=BudgetUsage(3, 1, 1, 0, 42.5))  # fmt: skip
    options = json.loads(packet_of(offline).payload)["decision_options"]["propose_action"]
    assert options["execution_limits"] == ["no_remaining_attempts", "no_remaining_render_calls"]
    assert "nothing would be executed" in json.loads(packet_of(s).payload)["semantics"]["decision_options"]


# --- valid abstention and empty alias sets --------------------------------------------------------------------------


def test_valid_abstentions_with_exact_evidence_route_as_normal_outcomes():
    s = ht01_shaped()
    packet = packet_of(s)
    o1 = alias(packet, AliasKind.CURRENT_OBSERVATION, "o-1")
    declined = parse(abstain(evidence=[o1]), s)
    assert isinstance(assess_decision(s, declined.value), DeclineAction)
    asked = parse(abstain("missing_context", [o1], ["Which role does the third track have?"]), s)
    assert isinstance(assess_decision(s, asked.value), RequestContext)
    assert category(parse(abstain("missing_context", [o1]), s)) == "contract"


def test_call_schema_enumerates_exactly_the_packet_aliases_and_is_deterministic():
    s = situation()
    packet = packet_of(s)
    schema = response_schema_for(packet)
    (proposing,) = [v for v in schema["oneOf"] if v["properties"]["type"]["const"] == "propose_action"]
    props = proposing["properties"]
    assert props["evidence_refs"]["items"]["enum"] == sorted(
        alias(packet, AliasKind.CURRENT_OBSERVATION, o) for o in ("o-1", "o-2", "o-3"))  # fmt: skip
    assert props["addressed_problem_candidates"]["items"]["enum"] == [alias(packet, AliasKind.CANDIDATE, "cand-1")]
    assert props["action"]["properties"]["track"]["enum"] == sorted(
        alias(packet, AliasKind.TRACK, t) for t in ("t1", "t2")
    )  # fmt: skip  (t3 has no advertised control)
    historical = alias(packet, AliasKind.HISTORICAL_OBSERVATION, "o-1", Revision("r2"))
    assert historical not in json.dumps(schema)
    assert response_schema_for(packet_of(situation())) == schema
    first = build_claude_request(s, config=config(), exchange_id="ex-1", requested_at=UTC)
    again = build_claude_request(s, config=config(), exchange_id="ex-1", requested_at=UTC)
    assert first == again and (first.prompt, first.response_schema) == (PROMPT, RESPONSE_SCHEMA)
    assert (PROMPT.version, RESPONSE_SCHEMA.version, PACKET_SCHEMA.version) == ("2", "2", "2")


def test_abstention_meanings_cover_exactly_the_core_reasons():
    reasons = json.loads(packet_of(situation()).payload)["decision_options"]["abstain"]["reasons"]
    assert set(reasons) == {r.value for r in AbstentionReason}


def test_the_prompt_states_policy_references_and_illustrative_placeholders():
    for phrase in ("at least one supplied diagnosis problem candidate", "not actions or alternatives",
                   "capability, not evidence or permission", "exactly one alias", "normal outcomes, not failures",
                   "placeholder aliases exist in no packet"):  # fmt: skip
        assert phrase in SYSTEM
    for placeholder in re.findall(r"(?:obs|trk|cand)-[0-9a-f]{12}", SYSTEM):
        assert set(placeholder.split("-")[1]) == {"0"}


# --- historical records ---------------------------------------------------------------------------------------------


def test_a_request_recorded_under_response_schema_v1_is_never_reinterpreted():
    s = situation()
    packet = packet_of(s)
    v1 = dataclasses.replace(
        request_for(packet), response_schema=VersionRef("autonomous_mixing.decision_response", "1")
    )
    with pytest.raises(ValueError, match=r"decision_response@1.*never reinterprets or revalidates"):
        parse(abstain(), s, packet=packet, request=v1)


def test_a_stored_v1_exchange_stays_readable_and_labelled_but_is_not_replayed_as_valid(tmp_path, monkeypatch):
    s = situation()
    store = SqliteDecisionExchangeStore.create(tmp_path / "exchange.sqlite", s.run_id, max_record_bytes=1000000)
    try:
        from test_claude_decision_maker import ABSTAIN, Transport, received

        v1 = VersionRef("autonomous_mixing.claude_prompt", "1")
        monkeypatch.setattr(prompt_module, "PROMPT", v1)
        monkeypatch.setattr(claude_module, "PROMPT", v1)
        maker = ClaudeDecisionMaker(config=config(), transport=Transport(received(ABSTAIN)), audit=store,
                                    identities=Tokens(), clock=Clock())  # fmt: skip
        assert isinstance(maker.decide(s), Success)
        monkeypatch.undo()
        stored = store.read(DecisionCallKey(s.run_id, s.usage.decision_calls)).record
        assert stored.request.prompt == v1 and stored.completion.completion is ExchangeCompletion.VALID_DECISION
        transport = Transport()
        current = ClaudeDecisionMaker(config=config(), transport=transport, audit=store, identities=Tokens(),
                                      clock=Clock())  # fmt: skip
        with pytest.raises(ValueError, match=r"claude_prompt@1.*does not revalidate"):
            current.decide(s)
        assert transport.calls == []
        again = store.read(DecisionCallKey(s.run_id, s.usage.decision_calls)).record
        assert again == stored  # unchanged, still labelled with its own versions
    finally:
        store.close()


def test_a_run_bound_to_prompt_v1_is_refused_on_reopen(tmp_path, monkeypatch):
    from agent_runtime_support import Setup, provider_config

    from autonomous_mixing.drivers import agent_runtime, decision_evidence
    from autonomous_mixing.drivers.agent_runtime import DawAgentRuntime

    setup = Setup(tmp_path, silent=True)  # stops before any decision call: only the frozen binding carries versions
    for module in (agent_runtime, decision_evidence):
        monkeypatch.setattr(module, "PROMPT", VersionRef("autonomous_mixing.claude_prompt", "1"))
        monkeypatch.setattr(module, "RESPONSE_SCHEMA", VersionRef("autonomous_mixing.decision_response", "1"))
    with setup.create() as runtime:
        runtime.start()
    monkeypatch.undo()
    opened = []
    with pytest.raises(ValueError, match="drift"):
        DawAgentRuntime.reopen(config=setup.config, run_id=setup.request_id, provider_config=provider_config(),
                               api_key="unit-test-only", provider_transport_factory=lambda *a: opened.append(a),
                               fl_transport_factory=lambda *a: opened.append(a))  # fmt: skip
    assert opened == []


# --- evidence_refs shape by context ---------------------------------------------------------------------------------

# Local checks only. ``_provider_keywords_only`` compares the converted schema with the provider's *documented*
# structured-output subset (checked 2026-11-26: scalar enum/const, anyOf, required, additionalProperties false,
# descriptions, minItems 0 or 1); ``_accepts`` is a minimal in-test validator for that subset. Neither establishes that
# the provider's grammar compiler accepts a schema: that needs a separately authorized live call.
_PROVIDER_KEYWORDS = {"type", "properties", "required", "additionalProperties", "items", "enum", "anyOf",
                      "description", "minItems"}  # fmt: skip


def _provider_keywords_only(node) -> None:
    if isinstance(node, dict):
        assert set(node) <= _PROVIDER_KEYWORDS, set(node) - _PROVIDER_KEYWORDS
        if "enum" in node:
            assert node["enum"] and all(v is None or isinstance(v, (str, int, float, bool)) for v in node["enum"])
        if "minItems" in node:
            assert node["minItems"] in (0, 1)
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            assert set(node.get("required", ())) <= set(node.get("properties", {}))
        for key, value in node.items():
            if key == "properties":
                for child in value.values():
                    _provider_keywords_only(child)
            elif key == "items":
                _provider_keywords_only(value)
            elif key == "anyOf":
                for child in value:
                    _provider_keywords_only(child)


def _accepts(schema: dict, value) -> bool:
    """A minimal local validator for exactly the keyword subset above (no third-party dependency)."""
    if "anyOf" in schema:
        return any(_accepts(branch, value) for branch in schema["anyOf"])
    kind = schema.get("type")
    checks = {"object": dict, "array": list, "string": str, "null": type(None)}
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
    elif kind is not None and not isinstance(value, checks[kind]):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            return False
        return all(_accepts(schema.get("items", {}), item) for item in value)
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            return False
        if any(key not in value for key in schema.get("required", ())):
            return False
        return all(_accepts(properties[k], v) for k, v in value.items() if k in properties)
    return True


def _provider_schema_of(s) -> dict:
    request = build_claude_request(s, config=config(), exchange_id="ex-1", requested_at=UTC)
    return json.loads(request.request_text)["output_config"]["format"]["schema"]


def _by_type(schema: dict, key: str) -> dict:
    return {v["properties"]["type"][key][0] if key == "enum" else v["properties"]["type"][key]: v for v in schema[
        "anyOf" if key == "enum" else "oneOf"]}  # fmt: skip


def no_observations():
    """Labelled fixture: no current observation (and therefore no supplied candidate)."""
    return situation(observations=(), diagnosis=diagnosis(candidates=False), entries=())


def test_without_current_observations_evidence_refs_is_omitted_from_the_schema():
    s = no_observations()
    schema = response_schema_for(packet_of(s))
    provider = _provider_schema_of(s)
    _provider_keywords_only(provider)
    assert variants(schema) == {"no_change", "abstain"}
    assert all(values for values in enums(schema))  # no empty enum anywhere
    for document in (*_by_type(schema, "const").values(), *_by_type(provider, "enum").values()):
        assert "evidence_refs" not in document["properties"] and "evidence_refs" not in document["required"]
    sent = _by_type(provider, "enum")
    abstention = {"type": "abstain", "rationale": "r", "reason": "insufficient_evidence", "requested_context": []}
    assert _accepts(sent["abstain"], abstention)  # local semantics only
    assert not _accepts(sent["abstain"], {**abstention, "evidence_refs": []})
    assert "evidence_refs" in json.loads(RESPONSE_JSON_SCHEMA)["oneOf"][0]["required"]  # the family schema is unchanged


def test_without_current_observations_the_parser_requires_the_omission_and_builds_empty_evidence():
    s = no_observations()
    result = parse(abstain(omit_evidence=True), s)
    assert isinstance(result, Success) and result.value.decision.evidence_ids == ()
    assert result.value.decision.outcome == Abstain(AbstentionReason.INSUFFICIENT_EVIDENCE)
    assert isinstance(assess_decision(s, result.value), DeclineAction)
    kept = parse({"type": "no_change", "rationale": "r", "explanation": "keep"}, s)
    assert kept.value.decision.outcome == NoChange("keep") and kept.value.decision.evidence_ids == ()
    assert isinstance(assess_decision(s, kept.value), KeepState)
    for present in ([], ["obs-000000000000"]):  # the field must be absent, not empty or invented
        assert category(parse(abstain(evidence=present), s)) == "shape"
    assert category(parse({**abstain(omit_evidence=True), "notes": "x"}, s)) == "shape"


def test_with_current_observations_evidence_refs_stays_required_and_restricted_to_exact_aliases():
    s = ht01_shaped()
    packet = packet_of(s)
    o1 = alias(packet, AliasKind.CURRENT_OBSERVATION, "o-1")
    provider = _provider_schema_of(s)
    _provider_keywords_only(provider)
    for name, document in _by_type(provider, "enum").items():
        assert "evidence_refs" in document["required"], name
        field = document["properties"]["evidence_refs"]
        assert _accepts(field, [o1]) and _accepts(field, [])  # local semantics only
        assert not _accepts(field, [o1 + ": prose"]) and not _accepts(field, ["obs-000000000000"])
    assert category(parse(abstain(omit_evidence=True), s)) == "shape"
    assert isinstance(parse(abstain(evidence=[]), s), Success)
    assert isinstance(parse(abstain(evidence=[o1]), s), Success)


def test_documented_min_items_one_survives_the_provider_conversion():
    s = situation()
    provider = _provider_schema_of(s)
    _provider_keywords_only(provider)
    proposing = _by_type(provider, "enum")["propose_action"]["properties"]
    assert proposing["addressed_problem_candidates"]["minItems"] == 1
    assert proposing["evidence_refs"]["minItems"] == 1
    assert "minLength" not in json.dumps(provider)  # undocumented string constraints stay enforced locally only
    packet = packet_of(s)
    document = proposal(packet, candidates=[], evidence=[alias(packet, AliasKind.CURRENT_OBSERVATION, "o-1")])
    assert category(parse(document, s)) == "contract"  # the parser enforces the same bound itself
