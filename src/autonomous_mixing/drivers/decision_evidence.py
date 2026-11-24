"""Read-only R5A provenance checks; owns no provider transport, DSP, coordinator or journal writes."""

from autonomous_mixing.adapters.decision_agent.config import ClaudeConfig
from autonomous_mixing.adapters.decision_agent.prompt import PROMPT, build_claude_request, provider_identity
from autonomous_mixing.adapters.decision_agent.response import RESPONSE_SCHEMA
from autonomous_mixing.core.records.analysis import AnalysisResponse
from autonomous_mixing.core.records.coordinator_runtime import RunRequest
from autonomous_mixing.core.records.daw_correction import DawCorrectionSuspended
from autonomous_mixing.core.records.decision import DecisionResponse
from autonomous_mixing.core.records.decision_evidence import DecisionEvidenceRejected, DecisionEvidenceVerified
from autonomous_mixing.core.records.decision_exchange import ExchangeFound, call_key_of
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.provider_run import ProviderRunBinding
from autonomous_mixing.core.records.results import PortFailure, Success
from autonomous_mixing.core.records.run_trace import DispatchIntent, OperationKind, OperationObserved
from autonomous_mixing.core.records.verification_evidence import StateEvidence


class ConfiguredDecisionEvidenceSource:
    """Rebuild the full exact R4 request, including public payload and withheld local binding.

    analysis_verifier is the configured directional analyzer (verify_evidence); no second file reader or database.
    It is required when a stored response contains P1 observations. Constructor performs no I/O.
    """

    def __init__(self, *, binding: ProviderRunBinding, config: ClaudeConfig, audit, analysis_verifier=None):
        require_instance(binding, ProviderRunBinding, "provider binding")
        require_instance(config, ClaudeConfig, "provider config")
        if (binding.provider, binding.prompt, binding.response_schema) != (
            provider_identity(config),
            PROMPT,
            RESPONSE_SCHEMA,
        ):
            raise ValueError("provider configuration differs from frozen run binding")
        self._binding, self._config, self._audit, self._analysis = binding, config, audit, analysis_verifier

    @property
    def binding(self):
        return self._binding

    def verify(self, situation, result):
        call = call_key_of(situation)
        value = result.value if isinstance(result, Success) else result
        if not isinstance(value, (DecisionResponse, PortFailure)):
            return DecisionEvidenceRejected(call, "invalid decision port result")
        found = self._audit.read(call)
        if not isinstance(found, ExchangeFound):
            return DecisionEvidenceRejected(call, "exchange is missing or unavailable; no provider-backed use/retry")
        record = found.record
        try:
            expected = build_claude_request(
                situation,
                config=self._config,
                exchange_id=record.request.exchange_id,
                requested_at=record.request.requested_at,
            )
            if expected != record.request:
                raise ValueError("exact Situation-derived request differs from the recorded exchange")
            return DecisionEvidenceVerified(situation, value, record)
        except ValueError as error:
            return DecisionEvidenceRejected(call, str(error))

    def verify_run(self, snapshot):
        """No writes and no reconstruction of missing evidence; fail closed across both stores."""
        requests = [e.payload for e in snapshot.events if isinstance(e.payload, RunRequest)]
        if len(requests) != 1 or requests[0].provider_binding != self.binding:
            return "stored run/provider binding is missing or changed"
        intents = {}
        proofs = {}
        observed_calls = set()
        recorded_evidence = ()
        try:
            for event in snapshot.events:
                payload = event.payload
                if isinstance(payload, DispatchIntent):
                    intents[event.event_id] = payload
                elif isinstance(payload, DecisionEvidenceVerified):
                    result = payload.result if isinstance(payload.result, PortFailure) else Success(payload.result)
                    checked = self.verify(payload.situation, result)
                    if checked != payload:
                        return "stored provider exchange is missing, changed or incomplete"
                    matching = [
                        i
                        for i in intents.values()
                        if i.operation is OperationKind.DECIDE and i.request == payload.situation
                    ]
                    if len(matching) != 1:
                        return "provider evidence has no unique earlier decision intent"
                    call = call_key_of(payload.situation)
                    if call in proofs:
                        return "duplicate provider evidence"
                    proofs[call] = payload
                elif isinstance(payload, DawCorrectionSuspended):
                    if payload.observations != recorded_evidence:
                        return "checkpoint observation cache differs from the exact recorded analysis evidence"
                elif isinstance(payload, OperationObserved):
                    intent = intents.get(payload.intent_id)
                    if intent is None:
                        return "evidence observation has no intent"
                    if payload.operation is OperationKind.ANALYZE and isinstance(payload.outcome, AnalysisResponse):
                        response = payload.outcome
                        state = intent.request.state
                        recorded_evidence = tuple(
                            e for e in recorded_evidence if e.state.revision != state.revision
                        ) + (StateEvidence(state, response.observations, requests[0].alignment),)
                        required = {o.observation_id for o in response.observations if o.metric.name.startswith("p1.")}
                        refs = {r.observation_id: r for r in response.evidence}
                        frozen_digest = self.binding.diagnosis_policy.measurement_plan.prepared_digest
                        if not required <= set(refs) or any(refs[i].prepared_digest != frozen_digest for i in required):
                            return "P1 evidence associations are missing or use another frozen plan"
                        if required:
                            if self._analysis is None:
                                return "no configured P1 evidence verifier"
                            self._analysis.verify_evidence(response, state=intent.request.state)
                    elif payload.operation is OperationKind.DECIDE:
                        proof = proofs.get(call_key_of(intent.request))
                        if proof is None or proof.result != payload.outcome:
                            return "decision observation lacks its exact preceding provider evidence"
                        observed_calls.add(call_key_of(intent.request))
        except ValueError as error:
            return str(error)
        dispatched = {call_key_of(i.request) for i in intents.values() if i.operation is OperationKind.DECIDE}
        if dispatched != set(proofs) or dispatched != observed_calls:
            return "interrupted decision dispatch/proof/observation; never resume or resend"
        return None
