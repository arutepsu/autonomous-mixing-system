"""Provider-backed proposal adapter: one charged call, mandatory append-only exchange evidence.

Only structural envelope extraction precedes response persistence, to retain R3's decision-text body contract.
The exact bounded provider entity bytes are preserved alongside it in metadata. No decision parser or identity
assignment runs until that record is acknowledged. Invalid extraction retains the raw body, never invents text.
"""

import base64
import hashlib
import json

from autonomous_mixing.adapters.decision_agent.config import ClaudeConfig
from autonomous_mixing.adapters.decision_agent.prompt import PROMPT, ClaudeRequestTooLarge, build_claude_request
from autonomous_mixing.adapters.decision_agent.request import DecisionPacketTooLarge
from autonomous_mixing.adapters.decision_agent.response import (
    RESPONSE_SCHEMA,
    ResponseLimits,
    parse_decision_response,
)
from autonomous_mixing.adapters.decision_agent.transport import TransportResult
from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource, RunClock
from autonomous_mixing.core.ports.decision_audit import DecisionAuditTrail, permits_dispatch
from autonomous_mixing.core.records.decision import DecisionResponse, Situation
from autonomous_mixing.core.records.decision_exchange import (
    AuditRecorded,
    CompletedDecisionExchange,
    DecisionCallKey,
    DecisionExchangeRecord,
    ExchangeAbsent,
    ExchangeCompletion,
    ExchangeFound,
    ExchangeStatus,
    RecordedDecisionResponse,
    ResponseEnvelope,
    canonical_json,
)
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, Success


def _failure(message, *, invalid=False, retryable=False):
    return PortFailure(PortFailureCode.INVALID_RESPONSE if invalid else PortFailureCode.UNAVAILABLE, message, retryable)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate provider envelope key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("non-finite provider envelope value")


def _public_text(value):
    return value if isinstance(value, str) and value.strip() and len(value.encode("utf-8")) <= 1024 else None


def _response(request, result, end, elapsed, maximum):
    """Lossless bounded envelope retention plus structural text extraction; no decision interpretation."""
    raw = result.body
    metadata = {
        "sdk_version": result.sdk_version,
        "api_version": request.provider.api_version,
        "http_status": result.status,
        "usage": None,
        "provider_body_base64": None,
        "provider_body_sha256": None,
        "provider_body_complete": raw is not None and result.error_code in (None, f"http_{result.status}"),
    }
    error, stop, model = result.error_code, None, None
    envelope = ResponseEnvelope.NOT_RECEIVED if raw is None else ResponseEnvelope.INCOMPLETE
    body = raw
    if raw is not None:
        if len(raw) > maximum + 1:
            raise ValueError("transport violated its bounded response contract")
        metadata["provider_body_base64"] = base64.b64encode(raw).decode("ascii")
        metadata["provider_body_sha256"] = hashlib.sha256(raw).hexdigest()
        if not error:
            try:
                document = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
                if not isinstance(document, dict):
                    raise ValueError("provider envelope is not an object")
                stop = _public_text(document.get("stop_reason"))
                model = _public_text(document.get("model"))
                usage = document.get("usage")
                if usage is not None:
                    if not isinstance(usage, dict) or any(
                        type(usage.get(k)) is not int or usage[k] < 0 for k in ("input_tokens", "output_tokens")
                    ):
                        raise ValueError("invalid token usage")
                    # Preserve only documented usage counters, not arbitrary provider fields.
                    metadata["usage"] = {k: usage[k] for k in ("input_tokens", "output_tokens")}
                content = document.get("content")
                valid = (
                    document.get("type") == "message"
                    and document.get("role") == "assistant"
                    and model is not None
                    and isinstance(content, list)
                    and len(content) == 1
                    and isinstance(content[0], dict)
                    and content[0].get("type") == "text"
                    and isinstance(content[0].get("text"), str)
                )
                if valid:
                    body = content[0]["text"].encode("utf-8")
                    if stop == "end_turn":
                        envelope = ResponseEnvelope.COMPLETE
                    elif stop == "refusal":
                        envelope = ResponseEnvelope.REFUSED
                if envelope is ResponseEnvelope.INCOMPLETE:
                    error = "incomplete_or_invalid_envelope"
            except (ValueError, RecursionError, UnicodeError):
                error = "invalid_provider_envelope"
    return RecordedDecisionResponse(
        request.call,
        request.exchange_id,
        request.request_sha256,
        envelope,
        None if body is None else base64.b64encode(body).decode("ascii"),
        None if body is None else hashlib.sha256(body).hexdigest(),
        stop,
        _public_text(result.request_id),
        model,
        canonical_json(metadata),
        error,
        end.utc,
        elapsed,
    )


class ClaudeDecisionMaker:
    """No execution authority, retries, prompt repair, fallback or automatic pending-exchange recovery."""

    def __init__(
        self, *, config: ClaudeConfig, transport, audit: DecisionAuditTrail, identities: IdentitySource, clock: RunClock
    ):
        if not isinstance(config, ClaudeConfig) or transport.config != config:
            raise ValueError("transport and decision maker configuration must agree")
        self.config, self.transport, self.audit = config, transport, audit
        self.identities, self.clock = identities, clock

    def decide(self, situation: Situation):
        start = self.clock.read()
        call = DecisionCallKey(situation.run_id, situation.usage.decision_calls)
        found = self.audit.read(call)
        if isinstance(found, ExchangeFound):
            record = found.record
            if record.request.call != call:
                return _failure("audit answered another call")
            versions = (record.request.prompt, record.request.response_schema)
            if versions != (PROMPT, RESPONSE_SCHEMA):
                # A stored exchange stays readable under its own labels; it is never replayed or revalidated here.
                raise ValueError(
                    f"recorded exchange uses prompt {versions[0].name}@{versions[0].version} and response schema "
                    f"{versions[1].name}@{versions[1].version}; this adapter implements {PROMPT.name}@{PROMPT.version} "
                    f"and {RESPONSE_SCHEMA.name}@{RESPONSE_SCHEMA.version} and does not revalidate it"
                )
            expected = build_claude_request(
                situation,
                config=self.config,
                exchange_id=record.request.exchange_id,
                requested_at=record.request.requested_at,
            )
            if expected != record.request:
                raise ValueError("decision call conflict: situation or configuration changed")
            if record.status is not ExchangeStatus.COMPLETED:
                return _failure("incomplete recorded exchange; automatic resend is forbidden")
            done = record.completion
            return Success(DecisionResponse(done.decision)) if done.decision is not None else done.failure
        if not isinstance(found, ExchangeAbsent) or found.call != call:
            return _failure("exchange store could not establish absence")
        remaining = situation.limits.max_elapsed_seconds - situation.usage.elapsed_seconds
        if start.cancelled or remaining <= 0:
            return _failure("decision call cancelled or runtime allowance exhausted")
        exchange_id = self.identities.next_token()
        try:
            request = build_claude_request(
                situation, config=self.config, exchange_id=exchange_id, requested_at=start.utc
            )
        except (DecisionPacketTooLarge, ClaudeRequestTooLarge) as error:
            # Construction failures are local input/size failures; no provider request occurred.
            return PortFailure(PortFailureCode.INVALID_INPUT, str(error), False)
        began = self.audit.begin(request)
        if not permits_dispatch(began, request):
            return _failure("request persistence did not authorize dispatch")
        before_send = self.clock.read()
        spent = before_send.monotonic_seconds - start.monotonic_seconds
        if spent < 0 or before_send.utc < start.utc:
            raise ValueError("clock moved backwards")
        remaining -= spent
        if before_send.cancelled or remaining <= 0:
            result = TransportResult(None, None, None, "not_dispatched", "cancelled_or_expired", False)
        else:
            result = self.transport.send(
                request.request_text.encode("utf-8"), timeout_seconds=min(self.config.timeout_seconds, remaining)
            )
        if not isinstance(result, TransportResult):
            raise ValueError("invalid transport result")
        end = self.clock.read()
        elapsed = end.monotonic_seconds - start.monotonic_seconds
        if elapsed < spent or end.utc < before_send.utc:
            raise ValueError("clock moved backwards")
        response = _response(request, result, end, elapsed, self.config.max_response_bytes)
        saved = self.audit.record_response(response)
        if not isinstance(saved, AuditRecorded) or saved.record != DecisionExchangeRecord(request, response, None):
            return _failure("response persistence did not confirm this exchange")
        if result.error_code is not None:
            invalid = result.error_code in ("response_too_large", "unsupported_content_encoding")
            outcome = _failure(
                f"provider transport: {result.error_code}", invalid=invalid, retryable=result.retryable and not invalid
            )
        elif end.cancelled or situation.usage.elapsed_seconds + elapsed >= situation.limits.max_elapsed_seconds:
            outcome = _failure("response arrived after cancellation or runtime allowance", retryable=False)
        else:
            outcome = parse_decision_response(
                response,
                request=request,
                situation=situation,
                identities=self.identities,
                limits=ResponseLimits(self.config.max_response_bytes),
            )
        if isinstance(outcome, Success):
            kind, decision, failure = ExchangeCompletion.VALID_DECISION, outcome.value.decision, None
        else:
            kind = (
                ExchangeCompletion.INVALID_RESPONSE
                if outcome.code is PortFailureCode.INVALID_RESPONSE
                else ExchangeCompletion.PROVIDER_FAILURE
            )
            decision, failure = None, outcome
        completion = CompletedDecisionExchange(
            call,
            request.exchange_id,
            request.request_sha256,
            kind,
            response.body_sha256,
            decision,
            failure,
            "validated decision" if failure is None else failure.message,
            end.utc,
        )
        finished = self.audit.finish(completion)
        if not isinstance(finished, AuditRecorded) or finished.record != DecisionExchangeRecord(
            request, response, completion
        ):
            return _failure("completion persistence did not confirm this exchange")
        return outcome
