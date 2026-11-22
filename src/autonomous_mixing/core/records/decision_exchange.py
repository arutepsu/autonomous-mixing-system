"""Decision packet and provider-exchange audit records (R3; diagnosis-and-decision-adapter plan, sections 4-5).

A ``DecisionPacket`` is the exact versioned outbound data text a future provider adapter sends (``payload``, canonical
JSON, with its SHA-256) plus a **local-only** ``DecisionPacketBinding``: the call, state, context and controller it
was built for and the alias map from opaque outbound aliases to typed source IDs. The binding and alias map are never
part of the outbound text.

One provider exchange has three append-only phases, each bound to the same call key, exchange ID and request
fingerprint: the pending ``DecisionExchangeRequest`` (persisted before any dispatch), the ``RecordedDecisionResponse``
(the public response bytes, or the fact that none arrived, persisted before interpretation) and the
``CompletedDecisionExchange`` (the interpretation: a valid decision with its locally assigned identities, an invalid
response, or a provider/recording failure; never a fabricated abstention). ``DecisionExchangeRecord`` checks that the
phases agree. These records describe recorded facts; constructing them does not show that a call happened.

Text payloads are kept as canonical JSON strings (immutable); no dict, list, SDK object or file handle is stored.
"""

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from autonomous_mixing.core.records.decision import ControllerIdentity, Decision, Situation
from autonomous_mixing.core.records.identifiers import ContextId, ProjectId, Revision, RunId, VersionRef
from autonomous_mixing.core.records.invariants import (
    require_count,
    require_finite,
    require_instance,
    require_text,
    require_tuple_of,
    require_utc,
)
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode

_HEX = frozenset("0123456789abcdef")
_CREDENTIAL_KEYS = frozenset(
    {"api_key", "apikey", "x-api-key", "authorization", "access_token", "auth_token", "bearer", "secret", "password",
     "credentials"}
)  # fmt: skip
_CREDENTIAL_SUFFIXES = ("_api_key", "_secret", "_password", "_credentials")


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or not set(value) <= _HEX:
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def sha256_text(text: str) -> str:
    """SHA-256 of the UTF-8 encoding of ``text``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(value: object) -> str:
    """Canonical JSON text: sorted keys, no whitespace, UTF-8 characters kept, NaN/Infinity refused."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"not canonically serializable: {error}") from error


def _refuse_constant(name: str) -> object:
    raise ValueError(f"non-finite JSON constant {name}")


def _require_canonical_object(text: object, field: str) -> str:
    """``text`` must be the canonical JSON text of a JSON object."""
    require_text(text, field)
    try:
        value = json.loads(text, parse_constant=_refuse_constant)
    except ValueError as error:
        raise ValueError(f"{field} is not strict JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    if canonical_json(value) != text:
        raise ValueError(f"{field} is not canonical JSON text")
    return text


def _optional_text(value: object, field: str) -> None:
    if value is not None:
        require_text(value, field)


# --- call key and aliases -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionCallKey:
    """One charged decision call: the run and ``Situation.usage.decision_calls`` of that call (positive)."""

    run_id: RunId
    decision_call_ordinal: int

    def __post_init__(self) -> None:
        require_instance(self.run_id, RunId, "DecisionCallKey.run_id")
        require_count(self.decision_call_ordinal, "DecisionCallKey.decision_call_ordinal", minimum=1)


def call_key_of(situation: Situation) -> DecisionCallKey:
    """The call key of a decision input: its run and its charged decision-call ordinal."""
    require_instance(situation, Situation, "situation")
    return DecisionCallKey(situation.run_id, situation.usage.decision_calls)


class AliasKind(Enum):
    """What an outbound alias stands for. Historical observations are never current evidence."""

    TRACK = "track"
    REVISION = "revision"
    CURRENT_OBSERVATION = "current_observation"
    HISTORICAL_OBSERVATION = "historical_observation"
    CANDIDATE = "candidate"
    COVERAGE = "coverage"
    CONDITION = "condition"
    DECISION_CALL = "decision_call"


@dataclass(frozen=True)
class AliasBinding:
    """Local map entry: an opaque outbound ``alias`` and the typed source it stands for (never sent).

    ``source_id`` is the source identifier's value for ``kind``; a historical observation also names the state
    ``revision`` it was recorded for, and no other kind does.
    """

    alias: str
    kind: AliasKind
    source_id: str
    revision: Revision | None

    def __post_init__(self) -> None:
        require_text(self.alias, "AliasBinding.alias")
        require_instance(self.kind, AliasKind, "AliasBinding.kind")
        require_text(self.source_id, "AliasBinding.source_id")
        historical = self.kind is AliasKind.HISTORICAL_OBSERVATION
        if historical != (self.revision is not None):
            raise ValueError("AliasBinding.revision is required exactly for historical observations")
        if self.revision is not None:
            require_instance(self.revision, Revision, "AliasBinding.revision")


def _alias_document(aliases: tuple[AliasBinding, ...]) -> list:
    return [[a.alias, a.kind.value, a.source_id, None if a.revision is None else a.revision.value] for a in aliases]


def alias_fingerprint(aliases: tuple[AliasBinding, ...]) -> str:
    """SHA-256 of the canonical text of an ordered alias map."""
    require_tuple_of(aliases, AliasBinding, "aliases", nonempty=False, distinct=False)
    return sha256_text(canonical_json(_alias_document(aliases)))


@dataclass(frozen=True)
class WithheldDetail:
    """Free technical text of an aliased entity that the packet withholds and the local binding retains.

    ``field`` names what the text was (for example ``unavailable_reason`` or ``explanation``); the outbound packet
    carries only a structured status for it. The text is audit evidence, never sent.
    """

    alias: str
    field: str
    text: str

    def __post_init__(self) -> None:
        require_text(self.alias, "WithheldDetail.alias")
        require_text(self.field, "WithheldDetail.field")
        require_text(self.text, "WithheldDetail.text")


@dataclass(frozen=True)
class DecisionPacketBinding:
    """Local-only binding of one packet: call, state, context, controller, the alias map with its fingerprint and the
    withheld technical text (each attached to an alias of the map)."""

    call: DecisionCallKey
    project_id: ProjectId
    revision: Revision
    context_id: ContextId
    context_version: str
    controller: ControllerIdentity
    aliases: tuple[AliasBinding, ...]
    alias_fingerprint: str
    withheld: tuple[WithheldDetail, ...]

    def __post_init__(self) -> None:
        require_instance(self.call, DecisionCallKey, "DecisionPacketBinding.call")
        require_instance(self.project_id, ProjectId, "DecisionPacketBinding.project_id")
        require_instance(self.revision, Revision, "DecisionPacketBinding.revision")
        require_instance(self.context_id, ContextId, "DecisionPacketBinding.context_id")
        require_text(self.context_version, "DecisionPacketBinding.context_version")
        require_instance(self.controller, ControllerIdentity, "DecisionPacketBinding.controller")
        require_tuple_of(self.aliases, AliasBinding, "DecisionPacketBinding.aliases", nonempty=False, distinct=True)
        names = [a.alias for a in self.aliases]
        if len(set(names)) != len(names):
            raise ValueError("DecisionPacketBinding reuses an alias for two sources")
        sources = [(a.kind, a.source_id, a.revision) for a in self.aliases]
        if len(set(sources)) != len(sources):
            raise ValueError("DecisionPacketBinding maps one source to two aliases")
        if _require_sha256(self.alias_fingerprint, "DecisionPacketBinding.alias_fingerprint") != alias_fingerprint(
            self.aliases
        ):
            raise ValueError("DecisionPacketBinding.alias_fingerprint does not match its aliases")
        require_tuple_of(self.withheld, WithheldDetail, "DecisionPacketBinding.withheld", nonempty=False, distinct=True)
        keys = [(w.alias, w.field) for w in self.withheld]
        if len(set(keys)) != len(keys):
            raise ValueError("DecisionPacketBinding withholds one field of an alias twice")
        unknown = sorted({w.alias for w in self.withheld} - set(names))
        if unknown:
            raise ValueError(f"DecisionPacketBinding withholds text of unknown aliases: {', '.join(unknown)}")

    def resolve(self, alias: str) -> AliasBinding | None:
        return next((a for a in self.aliases if a.alias == alias), None)

    @property
    def fingerprint(self) -> str:
        """The local call-binding identity (separate from the outbound data identity)."""
        return sha256_text(
            canonical_json(
                {
                    "run": self.call.run_id.value,
                    "ordinal": self.call.decision_call_ordinal,
                    "project": self.project_id.value,
                    "revision": self.revision.value,
                    "context": [self.context_id.value, self.context_version],
                    "controller": [self.controller.name, self.controller.version],
                    "aliases": self.alias_fingerprint,
                    "withheld": [[w.alias, w.field, w.text] for w in self.withheld],
                }
            )
        )


@dataclass(frozen=True)
class DecisionPacket:
    """Exact outbound data text (canonical JSON object) with its hash, schema and alias strategy, plus the local binding.

    ``payload_sha256`` is the outbound data identity; ``binding.fingerprint`` the local call-binding identity.
    """

    schema: VersionRef
    alias_strategy: VersionRef
    payload: str
    payload_sha256: str
    binding: DecisionPacketBinding

    def __post_init__(self) -> None:
        require_instance(self.schema, VersionRef, "DecisionPacket.schema")
        require_instance(self.alias_strategy, VersionRef, "DecisionPacket.alias_strategy")
        _require_canonical_object(self.payload, "DecisionPacket.payload")
        if _require_sha256(self.payload_sha256, "DecisionPacket.payload_sha256") != sha256_text(self.payload):
            raise ValueError("DecisionPacket.payload_sha256 is not the SHA-256 of its payload")
        require_instance(self.binding, DecisionPacketBinding, "DecisionPacket.binding")


# --- exchange phases ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderRequestIdentity:
    """Requested provider, API, model and explicit generation parameters (canonical JSON; never credentials)."""

    provider: str
    api_version: str
    requested_model: str
    parameters: str

    def __post_init__(self) -> None:
        require_text(self.provider, "ProviderRequestIdentity.provider")
        require_text(self.api_version, "ProviderRequestIdentity.api_version")
        require_text(self.requested_model, "ProviderRequestIdentity.requested_model")
        _require_canonical_object(self.parameters, "ProviderRequestIdentity.parameters")
        keys = {key.lower() for key in json.loads(self.parameters)}
        if keys & _CREDENTIAL_KEYS or any(key.endswith(_CREDENTIAL_SUFFIXES) for key in keys):
            raise ValueError("ProviderRequestIdentity.parameters must not contain credential-like keys")


@dataclass(frozen=True)
class DecisionExchangeRequest:
    """Pending phase: the exact public application request text and its identities, persisted before dispatch.

    ``request_text`` is the complete public request a transport sends (R4 supplies it); the data packet is only part of
    it. Provider headers and server internals are not captured.
    """

    call: DecisionCallKey
    exchange_id: str
    packet: DecisionPacket
    request_text: str
    request_sha256: str
    prompt: VersionRef
    response_schema: VersionRef
    provider: ProviderRequestIdentity | None
    requested_at: datetime

    def __post_init__(self) -> None:
        require_instance(self.call, DecisionCallKey, "DecisionExchangeRequest.call")
        require_text(self.exchange_id, "DecisionExchangeRequest.exchange_id")
        require_instance(self.packet, DecisionPacket, "DecisionExchangeRequest.packet")
        if self.packet.binding.call != self.call:
            raise ValueError("DecisionExchangeRequest.call differs from its packet's call binding")
        require_text(self.request_text, "DecisionExchangeRequest.request_text")
        if _require_sha256(self.request_sha256, "DecisionExchangeRequest.request_sha256") != sha256_text(
            self.request_text
        ):
            raise ValueError("DecisionExchangeRequest.request_sha256 is not the fingerprint of its request text")
        require_instance(self.prompt, VersionRef, "DecisionExchangeRequest.prompt")
        require_instance(self.response_schema, VersionRef, "DecisionExchangeRequest.response_schema")
        if self.provider is not None:
            require_instance(self.provider, ProviderRequestIdentity, "DecisionExchangeRequest.provider")
        require_utc(self.requested_at, "DecisionExchangeRequest.requested_at")


class ResponseEnvelope(Enum):
    """The transport's normalized envelope state (R4 maps provider stop reasons onto it)."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"  # truncated or otherwise not a finished answer
    REFUSED = "refused"  # a provider refusal: not a model abstention
    NOT_RECEIVED = "not_received"  # no response arrived; its own fact, not an empty response


@dataclass(frozen=True)
class RecordedDecisionResponse:
    """Response phase: the exact received public body (base64 of its bytes) or the fact that none arrived.

    Persisted before interpretation. ``metadata`` is canonical JSON of safe request/usage facts; ``error_code`` names a
    transport fact. No credentials or authorization headers belong here.
    """

    call: DecisionCallKey
    exchange_id: str
    request_sha256: str
    envelope: ResponseEnvelope
    body_base64: str | None
    body_sha256: str | None
    stop_reason: str | None
    provider_request_id: str | None
    resolved_model: str | None
    metadata: str
    error_code: str | None
    recorded_at: datetime
    elapsed_seconds: float

    def __post_init__(self) -> None:
        require_instance(self.call, DecisionCallKey, "RecordedDecisionResponse.call")
        require_text(self.exchange_id, "RecordedDecisionResponse.exchange_id")
        _require_sha256(self.request_sha256, "RecordedDecisionResponse.request_sha256")
        require_instance(self.envelope, ResponseEnvelope, "RecordedDecisionResponse.envelope")
        received = self.envelope is not ResponseEnvelope.NOT_RECEIVED
        if received != (self.body_base64 is not None) or received != (self.body_sha256 is not None):
            raise ValueError("RecordedDecisionResponse has a body exactly when a response was received")
        if received:
            body = self.body
            if (
                _require_sha256(self.body_sha256, "RecordedDecisionResponse.body_sha256")
                != hashlib.sha256(body).hexdigest()
            ):
                raise ValueError("RecordedDecisionResponse.body_sha256 is not the SHA-256 of its body")
        elif self.error_code is None:
            raise ValueError("a response that was not received needs an error_code naming the transport fact")
        for name in ("stop_reason", "provider_request_id", "resolved_model", "error_code"):
            _optional_text(getattr(self, name), f"RecordedDecisionResponse.{name}")
        _require_canonical_object(self.metadata, "RecordedDecisionResponse.metadata")
        require_utc(self.recorded_at, "RecordedDecisionResponse.recorded_at")
        if require_finite(self.elapsed_seconds, "RecordedDecisionResponse.elapsed_seconds") < 0:
            raise ValueError("RecordedDecisionResponse.elapsed_seconds must be non-negative")

    @property
    def body(self) -> bytes | None:
        """The exact received bytes, or None when no response arrived."""
        if self.body_base64 is None:
            return None
        if not isinstance(self.body_base64, str):
            raise ValueError("RecordedDecisionResponse.body_base64 must be text")
        try:
            return base64.b64decode(self.body_base64.encode("ascii"), validate=True)
        except (binascii.Error, UnicodeEncodeError) as error:
            raise ValueError(f"RecordedDecisionResponse.body_base64 is not strict base64: {error}") from error


class ExchangeCompletion(Enum):
    VALID_DECISION = "valid_decision"
    INVALID_RESPONSE = "invalid_response"
    PROVIDER_FAILURE = "provider_failure"  # refusal, timeout, unavailable service: never a synthetic Abstain
    RECORDING_FAILURE = "recording_failure"  # evidence could not be recorded: no decision is released


@dataclass(frozen=True)
class CompletedDecisionExchange:
    """Completion phase: how the exchange was interpreted, with the validated decision when there is one.

    A valid decision carries its locally assigned ``DecisionId``/``ActionId``, so replay recovers exact identities.
    ``response_sha256`` names the recorded body that was interpreted (required for valid and invalid responses).
    ``detail`` is the full local classification text; it is audit evidence, not outbound data.
    """

    call: DecisionCallKey
    exchange_id: str
    request_sha256: str
    completion: ExchangeCompletion
    response_sha256: str | None
    decision: Decision | None
    failure: PortFailure | None
    detail: str
    finished_at: datetime

    def __post_init__(self) -> None:
        require_instance(self.call, DecisionCallKey, "CompletedDecisionExchange.call")
        require_text(self.exchange_id, "CompletedDecisionExchange.exchange_id")
        _require_sha256(self.request_sha256, "CompletedDecisionExchange.request_sha256")
        require_instance(self.completion, ExchangeCompletion, "CompletedDecisionExchange.completion")
        if self.response_sha256 is not None:
            _require_sha256(self.response_sha256, "CompletedDecisionExchange.response_sha256")
        require_text(self.detail, "CompletedDecisionExchange.detail")
        require_utc(self.finished_at, "CompletedDecisionExchange.finished_at")
        kind = self.completion
        if kind is ExchangeCompletion.VALID_DECISION:
            require_instance(self.decision, Decision, "CompletedDecisionExchange.decision")
            if self.failure is not None:
                raise ValueError("a valid decision has no failure")
        else:
            if self.decision is not None:
                raise ValueError(f"a {kind.value} completion releases no decision")
            require_instance(self.failure, PortFailure, "CompletedDecisionExchange.failure")
        if kind in (ExchangeCompletion.VALID_DECISION, ExchangeCompletion.INVALID_RESPONSE):
            if self.response_sha256 is None:
                raise ValueError(f"a {kind.value} completion must name the recorded response it interpreted")
        if kind is ExchangeCompletion.INVALID_RESPONSE and (
            self.failure.code is not PortFailureCode.INVALID_RESPONSE or self.failure.retryable
        ):
            raise ValueError("an invalid response is a non-retryable INVALID_RESPONSE failure")


class ExchangeStatus(Enum):
    PENDING = "pending"  # incomplete: never permission to resend
    RESPONSE_RECORDED = "response_recorded"
    COMPLETED = "completed"


def _require_same_exchange(request: DecisionExchangeRequest, phase, name: str) -> None:
    if (phase.call, phase.exchange_id, phase.request_sha256) != (
        request.call,
        request.exchange_id,
        request.request_sha256,
    ):
        raise ValueError(f"the {name} belongs to another call, exchange or request fingerprint")


@dataclass(frozen=True)
class DecisionExchangeRecord:
    """The recorded phases of one exchange, checked for agreement; later phases may be absent."""

    request: DecisionExchangeRequest
    response: RecordedDecisionResponse | None
    completion: CompletedDecisionExchange | None

    def __post_init__(self) -> None:
        require_instance(self.request, DecisionExchangeRequest, "DecisionExchangeRecord.request")
        if self.response is not None:
            require_instance(self.response, RecordedDecisionResponse, "DecisionExchangeRecord.response")
            _require_same_exchange(self.request, self.response, "response")
        if self.completion is None:
            return
        done = self.completion
        require_instance(done, CompletedDecisionExchange, "DecisionExchangeRecord.completion")
        _require_same_exchange(self.request, done, "completion")
        recorded_body = None if self.response is None else self.response.body_sha256
        if done.response_sha256 != recorded_body:
            raise ValueError("the completion must reference exactly the recorded response body (or none, if none)")
        if done.completion is ExchangeCompletion.VALID_DECISION:
            if self.response is None or self.response.envelope is not ResponseEnvelope.COMPLETE:
                raise ValueError("a valid decision needs a complete recorded response envelope")
            binding, decision = self.request.packet.binding, done.decision
            bound = (binding.project_id, binding.revision, binding.context_id, binding.context_version,
                     binding.controller)  # fmt: skip
            found = (decision.project_id, decision.expected_revision, decision.context_id, decision.context_version,
                     decision.controller)  # fmt: skip
            if found != bound:
                raise ValueError("the completed decision does not match the packet binding")

    @property
    def status(self) -> ExchangeStatus:
        if self.completion is not None:
            return ExchangeStatus.COMPLETED
        return ExchangeStatus.PENDING if self.response is None else ExchangeStatus.RESPONSE_RECORDED


# --- audit port results ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditRecorded:
    """The phase is durably recorded; ``newly_recorded`` is False for an identical replay of a recorded phase."""

    record: DecisionExchangeRecord
    newly_recorded: bool

    def __post_init__(self) -> None:
        require_instance(self.record, DecisionExchangeRecord, "AuditRecorded.record")
        if not isinstance(self.newly_recorded, bool):
            raise ValueError("AuditRecorded.newly_recorded must be a bool")


class AuditRefusal(Enum):
    CONFLICT = "conflict"  # different content for a recorded call, exchange or phase
    NO_MATCHING_REQUEST = "no_matching_request"
    ALREADY_COMPLETED = "already_completed"
    TOO_LARGE = "too_large"
    WRONG_RUN = "wrong_run"
    INVALID_RECORD = "invalid_record"
    STORE_UNAVAILABLE = "store_unavailable"


@dataclass(frozen=True)
class AuditNotRecorded:
    """Definitely not recorded (nothing was written)."""

    call: DecisionCallKey
    reason: AuditRefusal
    detail: str

    def __post_init__(self) -> None:
        require_instance(self.call, DecisionCallKey, "AuditNotRecorded.call")
        require_instance(self.reason, AuditRefusal, "AuditNotRecorded.reason")
        require_text(self.detail, "AuditNotRecorded.detail")


@dataclass(frozen=True)
class AuditRecordUnknown:
    """The write did not confirm its commit; whether it is recorded must be read back after a verified reopen."""

    call: DecisionCallKey
    detail: str

    def __post_init__(self) -> None:
        require_instance(self.call, DecisionCallKey, "AuditRecordUnknown.call")
        require_text(self.detail, "AuditRecordUnknown.detail")


AuditWriteResult = AuditRecorded | AuditNotRecorded | AuditRecordUnknown


@dataclass(frozen=True)
class ExchangeFound:
    record: DecisionExchangeRecord

    def __post_init__(self) -> None:
        require_instance(self.record, DecisionExchangeRecord, "ExchangeFound.record")


@dataclass(frozen=True)
class ExchangeAbsent:
    """No exchange is recorded for the call (a verified answer, not an unavailable store)."""

    call: DecisionCallKey

    def __post_init__(self) -> None:
        require_instance(self.call, DecisionCallKey, "ExchangeAbsent.call")


@dataclass(frozen=True)
class ExchangeReadUnavailable:
    """The store could not answer; neither presence nor absence is established."""

    call: DecisionCallKey
    detail: str

    def __post_init__(self) -> None:
        require_instance(self.call, DecisionCallKey, "ExchangeReadUnavailable.call")
        require_text(self.detail, "ExchangeReadUnavailable.detail")


ExchangeReadResult = ExchangeFound | ExchangeAbsent | ExchangeReadUnavailable
