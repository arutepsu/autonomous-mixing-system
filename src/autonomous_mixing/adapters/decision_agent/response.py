"""Strict structured-response parsing: recorded public response -> ``DecisionResponse`` (R3 Task 3; plan section 4).

Version 2 (ADR-045, Proposed): the reference fields are named ``addressed_problem_candidates`` and ``evidence_refs``;
``response_schema_for`` restricts the provider-side schema of one call to that packet's exact aliases (string enums,
never empty) and omits ``propose_action`` when the packet states it is unavailable. That restriction only assists the
provider. Contextual shape: when the packet has no current observation, ``evidence_refs`` is not part of any variant;
the parser then requires its absence and builds an empty evidence tuple (an explicit shape, not repair). Otherwise
``evidence_refs`` is required. This parser stays the authority and is otherwise exactly as strict as before. It refuses requests recorded
under another response-schema version (a wiring error): stored v1 exchanges are never reinterpreted or revalidated.
Every invalid response message names a first-failure category (``invalid decision response [category]: ...``).

Input is the ``RecordedDecisionResponse`` (already persisted public bytes), never an SDK object. Order:

1. Wiring (``ValueError``, a programming error), before any interpretation: the response must belong to ``request``
   (same call, exchange ID and full request fingerprint, which is distinct from the packet-data hash), and the
   request's packet must be exactly the re-projection of ``situation`` (payload contents and local binding, including
   withheld text), so changed criteria, measurements or other inputs behind unchanged IDs are refused. Review fix
   2026-11-22: only identifiers and the call were checked first.
2. Envelope: ``REFUSED`` -> non-retryable ``UNAVAILABLE`` (a provider refusal, never a model abstention);
   ``NOT_RECEIVED`` -> non-retryable ``UNAVAILABLE`` (the transport adapter classifies retries); ``INCOMPLETE`` ->
   ``INVALID_RESPONSE``.
3. Bounded strict text: at most ``max_response_bytes``, strict UTF-8, exactly one JSON object with no surrounding
   whitespace, prose, fence or trailing data; duplicate keys and NaN/Infinity refused; nesting beyond the decoder's
   depth limit and integer literals beyond the interpreter's digit limit are invalid (review fix 2026-11-22), with no
   global limit raised.
4. Schema ``autonomous_mixing.decision_response@2`` (``RESPONSE_JSON_SCHEMA``): exact keys per variant
   (``propose_action``, ``no_change``, ``abstain``), typed values, finite numbers (booleans are not numbers), exactly
   one ``set_track_gain`` action. The model cannot supply identities, bindings, policy, criteria or budgets.
5. Aliases: the target must be a track alias, candidates candidate aliases, evidence **current** observation aliases;
   unknown, duplicated, wrong-kind and historical references are refused. No fuzzy matching.
6. Local construction: ``DecisionId`` then (for a proposal) ``ActionId`` from ``identities``; project, revision,
   context and controller from ``situation``. Core record errors and ``validate_decision_response`` failures are
   ``INVALID_RESPONSE``.

Every invalid response is ``PortFailure(INVALID_RESPONSE, retryable=False)``. No repair, clamping, substitution or
invented evidence. A valid proposal is a proposal only: evidence sufficiency, protection, bounds and runtime permission
are decided by the existing gate, admission and capability checks, which this module does not import.
"""

import json
import math
from dataclasses import dataclass

from autonomous_mixing.adapters.decision_agent.request import project_decision_input
from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource
from autonomous_mixing.core.ports.decision import validate_decision_response
from autonomous_mixing.core.records.actions import Action, SetTrackGain
from autonomous_mixing.core.records.decision import (
    Abstain,
    AbstentionReason,
    ActionJustification,
    Decision,
    DecisionResponse,
    NoChange,
    ProposeAction,
    Situation,
)
from autonomous_mixing.core.records.decision_exchange import (
    AliasKind,
    DecisionExchangeRequest,
    DecisionPacket,
    DecisionPacketBinding,
    RecordedDecisionResponse,
    ResponseEnvelope,
    canonical_json,
)
from autonomous_mixing.core.records.identifiers import (
    ActionId,
    DecisionId,
    ObservationId,
    ProblemCandidateId,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, PortResult, Success

RESPONSE_SCHEMA = VersionRef("autonomous_mixing.decision_response", "2")
_TEXT = {"type": "string", "minLength": 1}
_TEXTS = {"type": "array", "items": _TEXT}
_REFERENCE_RULE = "Each item is exactly one alias copied unchanged from the packet, with no label, value or commentary."
_EVIDENCE = f"Current observation aliases (obs-...) cited as evidence. {_REFERENCE_RULE}"
_CANDIDATES = (
    "Aliases (cand-...) of supplied diagnosis problem_candidates this action addresses; never actions or "
    f"alternatives. {_REFERENCE_RULE}"
)
_TRACK = "The alias (trk-...) of one track with an advertised set_track_gain control, copied unchanged."


def _references(aliases: tuple[str, ...] | None, description: str, *, nonempty: bool = False) -> dict:
    """An alias array: ``None`` means unrestricted (the family schema). An empty alias set is never represented: the
    caller omits the field (evidence) or the whole variant (candidates, tracks), so no enum is ever empty."""
    if aliases is None:
        items = _TEXT
    elif aliases:
        items = {"type": "string", "enum": list(aliases)}
    else:
        raise ValueError("an empty alias set has no reference field; omit the field or the variant instead")
    return {"type": "array", "items": items, "description": description, **({"minItems": 1} if nonempty else {})}


def _proposal_variant(evidence, candidates, tracks) -> dict:
    track = {"type": "string", "description": _TRACK}
    if tracks is not None:
        track["enum"] = list(tracks)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "type",
            "action",
            "addressed_problem_candidates",
            "evidence_refs",
            "expected_effect",
            "rationale",
            "justification",
        ],  # fmt: skip
        "properties": {
            "type": {"const": "propose_action"},
            "action": {
                "type": "object",
                "additionalProperties": False,
                "required": ["operation", "track", "gain_db"],
                "properties": {
                    "operation": {"const": "set_track_gain"},
                    "track": track,
                    "gain_db": {"type": "number", "description": "Absolute application gain in dB."},
                },
            },
            "addressed_problem_candidates": _references(candidates, _CANDIDATES, nonempty=True),
            "evidence_refs": _references(evidence, _EVIDENCE, nonempty=True),
            "expected_effect": _TEXT,
            "rationale": _TEXT,
            "justification": {
                "type": "object",
                "additionalProperties": False,
                "required": ["reasoning", "uncertainty", "assumptions", "limitations"],
                "properties": {
                    "reasoning": _TEXT,
                    "uncertainty": _TEXT,
                    "assumptions": _TEXTS,
                    "limitations": _TEXTS,
                },  # fmt: skip
            },
        },
    }


def _schema_document(evidence, candidates, tracks, *, proposal: bool) -> dict:
    omit_evidence = evidence == ()  # no current observation: evidence_refs is not part of this call's shape
    evidence = None if omit_evidence else evidence
    no_change = {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "rationale", "evidence_refs", "explanation"],
        "properties": {
            "type": {"const": "no_change"},
            "rationale": _TEXT,
            "evidence_refs": _references(evidence, _EVIDENCE),
            "explanation": _TEXT,
        },  # fmt: skip
    }
    abstain = {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "rationale", "evidence_refs", "reason", "requested_context"],
        "properties": {
            "type": {"const": "abstain"},
            "rationale": _TEXT,
            "evidence_refs": _references(evidence, _EVIDENCE),
            "reason": {"enum": [r.value for r in AbstentionReason]},
            "requested_context": _TEXTS,
        },  # fmt: skip
    }
    variants = [*([_proposal_variant(evidence, candidates, tracks)] if proposal else []), no_change, abstain]
    if omit_evidence:
        for variant in variants:
            del variant["properties"]["evidence_refs"]
            variant["required"].remove("evidence_refs")
    return {"$id": f"{RESPONSE_SCHEMA.name}@{RESPONSE_SCHEMA.version}", "oneOf": variants}


_SCHEMA_DOCUMENT = _schema_document(None, None, None, proposal=True)
RESPONSE_JSON_SCHEMA = canonical_json(_SCHEMA_DOCUMENT)


def response_schema_for(packet: DecisionPacket) -> dict:
    """The family schema restricted to this packet's public aliases; derived only from the outbound payload.

    ``propose_action`` is omitted exactly when the packet's ``decision_options`` state it is unavailable (no supplied
    problem candidate or no advertised gain control). Without a current observation, ``evidence_refs`` is omitted from
    every variant; otherwise it is required and restricted to the current observation aliases. No enum is ever empty.
    This assists the provider only; the parser enforces the same shape and every reference rule itself.
    """
    require_instance(packet, DecisionPacket, "packet")
    document = json.loads(packet.payload)
    evidence = tuple(sorted(o["alias"] for o in document["observations"] if o["current"]))
    candidates = tuple(sorted(c["alias"] for c in document["diagnosis"]["problem_candidates"]))
    tracks = tuple(sorted(g["track"] for g in document["actions"]["set_track_gain"]))
    proposal = document["decision_options"]["propose_action"]["available"]
    if proposal is not bool(candidates and tracks):
        raise ValueError("the packet's propose_action availability contradicts its candidates and gain controls")
    return _schema_document(evidence, candidates, tracks, proposal=proposal)


_KEYS = {
    "propose_action": {
        "type",
        "action",
        "addressed_problem_candidates",
        "evidence_refs",
        "expected_effect",
        "rationale",
        "justification",
    },  # fmt: skip
    "no_change": {"type", "rationale", "evidence_refs", "explanation"},
    "abstain": {"type", "rationale", "evidence_refs", "reason", "requested_context"},
}


@dataclass(frozen=True)
class ResponseLimits:
    """Engineering bound on the response body size in bytes (not a musical threshold)."""

    max_response_bytes: int

    def __post_init__(self) -> None:
        if type(self.max_response_bytes) is not int or self.max_response_bytes < 1:
            raise ValueError("ResponseLimits.max_response_bytes must be a positive int")


class _Invalid(Exception):
    """An invalid response with its first-failure category (for counting, never for repair)."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(f"[{category}]: {message}")


def _refuse_constant(name: str) -> object:
    raise _Invalid("json", f"non-finite JSON constant {name}")


def _unique_pairs(pairs: list) -> dict:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise _Invalid("json", "duplicate JSON key")
    return dict(pairs)


def _strict_document(body: bytes) -> dict:
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise _Invalid("encoding", f"the body is not UTF-8: {error}") from error
    if not text.startswith("{") or not text.endswith("}"):
        raise _Invalid("json", "the body must be exactly one JSON object, without prose, fences or surrounding "
                       "whitespace")  # fmt: skip
    # Only decoding errors of the bounded body become invalid responses: malformed JSON, an integer literal beyond the
    # interpreter's digit limit (ValueError) and nesting deeper than the recursion limit (RecursionError). Global
    # limits are not raised. Other exceptions (programming or wiring errors) propagate; the hooks raise _Invalid.
    try:
        document = json.loads(text, object_pairs_hook=_unique_pairs, parse_constant=_refuse_constant)
    except json.JSONDecodeError as error:
        raise _Invalid("json", f"the body is not one strict JSON object: {error.msg}") from error
    except ValueError as error:
        raise _Invalid("json", f"the body is not decodable JSON within interpreter limits: {error}") from error
    except RecursionError as error:
        raise _Invalid("json", "the body's JSON nesting exceeds the decoder's depth limit") from error
    if not isinstance(document, dict):
        raise _Invalid("json", "the body must be one JSON object")
    return document


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _Invalid("shape", f"{field} must be non-blank text")
    return value


def _texts(value: object, field: str, *, repeat: str = "shape") -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _Invalid("shape", f"{field} must be a list of text")
    items = tuple(_text(item, f"{field} item") for item in value)
    if len(set(items)) != len(items):
        raise _Invalid(repeat, f"{field} repeats an item")
    return items


def _exact_keys(document: dict, expected: set[str], field: str) -> None:
    if set(document) != expected:
        missing, extra = sorted(expected - set(document)), sorted(set(document) - expected)
        raise _Invalid("shape", f"{field} keys differ from the schema (missing {missing}, unexpected {extra})")


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _Invalid("value", f"{field} must be a JSON number")
    try:
        number = float(value)
    except OverflowError as error:
        raise _Invalid("value", f"{field} is not finitely representable") from error
    if not math.isfinite(number):
        raise _Invalid("value", f"{field} must be finite")
    return number


class _References:
    def __init__(self, binding: DecisionPacketBinding) -> None:
        self._binding = binding

    def has(self, kind: AliasKind) -> bool:
        return any(a.kind is kind for a in self._binding.aliases)

    def one(self, alias: object, kind: AliasKind, field: str) -> str:
        alias = _text(alias, field)
        found = self._binding.resolve(alias)
        if found is None:
            raise _Invalid("reference.unknown", f"{field} {alias!r} is not an alias of this packet")
        if found.kind is AliasKind.HISTORICAL_OBSERVATION and kind is AliasKind.CURRENT_OBSERVATION:
            raise _Invalid("reference.historical", f"{field} {alias!r} is a historical observation, not current "
                           "evidence")  # fmt: skip
        if found.kind is not kind:
            raise _Invalid("reference.wrong_kind", f"{field} {alias!r} is a {found.kind.value} alias, not a "
                           f"{kind.value} alias")  # fmt: skip
        return found.source_id

    def many(self, value: object, kind: AliasKind, field: str) -> tuple[str, ...]:
        aliases = _texts(value, field, repeat="reference.duplicate")
        return tuple(self.one(a, kind, f"{field} item") for a in aliases)


def _require_binding(packet: DecisionPacket, situation: Situation) -> None:
    """The packet must be exactly the projection of ``situation``: payload contents, binding and withheld text."""
    payload, binding = project_decision_input(situation)
    if packet.binding != binding:
        raise ValueError("the packet binding does not match this decision input (call, state, context, controller, "
                         "alias map or withheld text)")  # fmt: skip
    if packet.payload != payload:
        raise ValueError("the packet binding does not cover this decision input: its projected contents differ "
                         "(criteria, measurements, diagnosis, capabilities, budget or history)")  # fmt: skip


def _require_exchange(public_response: RecordedDecisionResponse, request: DecisionExchangeRequest) -> None:
    if public_response.call != request.call:
        raise ValueError("the recorded response belongs to another decision call than the request")
    if public_response.exchange_id != request.exchange_id:
        raise ValueError("the recorded response belongs to another exchange than the request")
    if public_response.request_sha256 != request.request_sha256:
        raise ValueError("the recorded response names another request fingerprint than the request")


def _invalid(message: str) -> PortFailure:
    return PortFailure(PortFailureCode.INVALID_RESPONSE, f"invalid decision response {message}", retryable=False)


def parse_decision_response(
    public_response: RecordedDecisionResponse,
    *,
    request: DecisionExchangeRequest,
    situation: Situation,
    identities: IdentitySource,
    limits: ResponseLimits,
) -> PortResult[DecisionResponse]:
    """Parse one recorded public response of ``request`` for ``situation`` into a validated ``DecisionResponse``."""
    require_instance(public_response, RecordedDecisionResponse, "public_response")
    require_instance(request, DecisionExchangeRequest, "request")
    packet = request.packet
    require_instance(situation, Situation, "situation")
    require_instance(limits, ResponseLimits, "limits")
    if not callable(getattr(identities, "next_token", None)):
        raise ValueError("identities must provide next_token()")
    _require_exchange(public_response, request)
    if request.response_schema != RESPONSE_SCHEMA:
        raise ValueError(f"the request was recorded under response schema {request.response_schema.name}@"
                         f"{request.response_schema.version}; this parser implements {RESPONSE_SCHEMA.name}@"
                         f"{RESPONSE_SCHEMA.version} and never reinterprets or revalidates another version")  # fmt: skip
    _require_binding(packet, situation)
    envelope = public_response.envelope
    if envelope is ResponseEnvelope.REFUSED:
        return PortFailure(PortFailureCode.UNAVAILABLE, "the provider refused the request; this is not a model "
                           "abstention", retryable=False)  # fmt: skip
    if envelope is ResponseEnvelope.NOT_RECEIVED:
        return PortFailure(PortFailureCode.UNAVAILABLE, f"no response was received ({public_response.error_code})",
                           retryable=False)  # fmt: skip
    if envelope is ResponseEnvelope.INCOMPLETE:
        return _invalid("[envelope]: the response envelope is incomplete")
    body = public_response.body
    if len(body) > limits.max_response_bytes:
        return _invalid(f"[size]: the body is {len(body)} bytes, above the {limits.max_response_bytes}-byte bound")
    try:
        document = _strict_document(body)
        parts = _interpret(document, _References(packet.binding))
    except _Invalid as error:
        return _invalid(str(error))
    return _construct(parts, situation, identities)


def _interpret(document: dict, refs: _References) -> dict:
    kind = document.get("type")
    if not isinstance(kind, str) or kind not in _KEYS:
        raise _Invalid("type", f"unsupported response type {kind!r}")
    with_evidence = refs.has(AliasKind.CURRENT_OBSERVATION)
    _exact_keys(document, _KEYS[kind] if with_evidence else _KEYS[kind] - {"evidence_refs"}, kind)
    parts = {
        "type": kind,
        "rationale": _text(document["rationale"], "rationale"),
        # contextual shape: without a current observation the field is absent (required above) and evidence is empty
        "evidence": refs.many(document["evidence_refs"], AliasKind.CURRENT_OBSERVATION, "evidence_refs")
        if with_evidence
        else (),
    }
    if kind == "no_change":
        parts["explanation"] = _text(document["explanation"], "explanation")
    elif kind == "abstain":
        reason = document["reason"]
        if not isinstance(reason, str) or reason not in {r.value for r in AbstentionReason}:
            raise _Invalid("value", f"unsupported abstention reason {reason!r}")
        parts["reason"] = AbstentionReason(reason)
        parts["requested_context"] = _texts(document["requested_context"], "requested_context")
    else:
        action = document["action"]
        if not isinstance(action, dict):
            raise _Invalid("shape", "action must be one object")
        _exact_keys(action, {"operation", "track", "gain_db"}, "action")
        if not isinstance(action["operation"], str) or action["operation"] != "set_track_gain":
            raise _Invalid("value", f"unsupported operation {action['operation']!r}")
        parts["track"] = refs.one(action["track"], AliasKind.TRACK, "action.track")
        parts["gain_db"] = _number(action["gain_db"], "action.gain_db")
        parts["candidates"] = refs.many(document["addressed_problem_candidates"], AliasKind.CANDIDATE,
                                        "addressed_problem_candidates")  # fmt: skip
        parts["expected_effect"] = _text(document["expected_effect"], "expected_effect")
        justification = document["justification"]
        if not isinstance(justification, dict):
            raise _Invalid("shape", "justification must be one object")
        _exact_keys(justification, {"reasoning", "uncertainty", "assumptions", "limitations"}, "justification")
        parts["justification"] = (
            _text(justification["reasoning"], "justification.reasoning"),
            _text(justification["uncertainty"], "justification.uncertainty"),
            _texts(justification["assumptions"], "justification.assumptions"),
            _texts(justification["limitations"], "justification.limitations"),
        )
    return parts


def _token(identities: IdentitySource) -> str:
    token = identities.next_token()
    if not isinstance(token, str):
        raise ValueError(f"identity source returned {type(token).__name__}, not a string token")
    return token


def _construct(parts: dict, situation: Situation, identities: IdentitySource) -> PortResult[DecisionResponse]:
    state, context = situation.state, situation.context
    decision_id = DecisionId(_token(identities))  # identity-source errors are wiring errors and propagate
    action_id = ActionId(_token(identities)) if parts["type"] == "propose_action" else None
    try:
        if parts["type"] == "no_change":
            outcome = NoChange(parts["explanation"])
        elif parts["type"] == "abstain":
            outcome = Abstain(parts["reason"], parts["requested_context"])
        else:
            action = Action(action_id, state.project_id, state.revision,
                            SetTrackGain(TrackId(parts["track"]), parts["gain_db"]))  # fmt: skip
            outcome = ProposeAction(action, parts["expected_effect"],
                                    tuple(ProblemCandidateId(c) for c in parts["candidates"]),
                                    ActionJustification(*parts["justification"]))  # fmt: skip
        decision = Decision(decision_id, state.project_id, state.revision, context.context_id, context.version,
                            tuple(ObservationId(o) for o in parts["evidence"]), parts["rationale"],
                            situation.controller, outcome)  # fmt: skip
        response = DecisionResponse(decision)
        validate_decision_response(situation, response)
    except ValueError as error:
        return _invalid(f"[contract]: {error}")
    return Success(response)
