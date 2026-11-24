"""Regressions from the R5A fresh review; labelled local claims, no live provider/device."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from masking_diagnosis_support import experimental_plan, experimental_policy, support
from test_claude_config import config
from test_claude_decision_maker import Transport, maker
from test_coordinator import Identities, propose
from test_r5a_coordinator_gate import bound_harness, execute
from test_r5a_decision_evidence import binding
from test_run_recording_contracts import T1, T2

from autonomous_mixing.adapters.decision_agent.claude import ClaudeDecisionMaker
from autonomous_mixing.adapters.journal.decision_exchange import SqliteDecisionExchangeStore
from autonomous_mixing.core.records.daw_correction import DawCorrectionSuspended
from autonomous_mixing.core.records.decision import DecisionResponse
from autonomous_mixing.core.records.decision_evidence import DecisionEvidenceVerified
from autonomous_mixing.core.records.decision_exchange import ExchangeFound, call_key_of
from autonomous_mixing.core.records.identifiers import RunEventId, VersionRef
from autonomous_mixing.core.records.run_trace import DispatchIntent, OperationKind, OperationObserved, RunTraceEvent
from autonomous_mixing.core.records.termination import TerminationReason as R
from autonomous_mixing.drivers.decision_evidence import ConfiguredDecisionEvidenceSource


@pytest.mark.parametrize("field", ["provider", "prompt", "response_schema"])
def test_core_checks_proof_settings_against_the_frozen_run_binding(tmp_path, field):
    h, store = bound_harness(tmp_path)

    class ForgingSource(ConfiguredDecisionEvidenceSource):
        def verify(self, situation, result):
            proof = super().verify(situation, result)
            request = proof.exchange.request
            value = (
                replace(request.provider, requested_model="other") if field == "provider" else VersionRef("other", "1")
            )
            return replace(proof, exchange=replace(proof.exchange, request=replace(request, **{field: value})))

    source = ForgingSource(binding=h.request.provider_binding, config=config(), audit=store)
    decider = ClaudeDecisionMaker(
        config=config(), transport=Transport(), audit=store, identities=Identities(), clock=h.clock
    )
    try:
        report = execute(h, decider, source)
        assert report.final_run.termination_reason is R.DECISION_FAILED
        assert not [
            e
            for e in report.trace.events
            if isinstance(e.payload, OperationObserved) and e.payload.operation is OperationKind.DECIDE
        ]
    finally:
        store.close()


def checkpoint_trace(tmp_path):
    """Real numerical-WAV/FL-fake suspension, with a labelled completed audit claim for its existing decision.

    This deliberately constructs audit records structurally to isolate checkpoint-cache consistency. It is not a
    model/parser integration result: the provider's interpretation is an explicitly labelled local test claim.
    """
    from daw_continuation_support import Continuation

    h = Continuation(tmp_path, decisions=(propose(0.0),))
    suspended = h.start()
    snapshot = suspended.trace
    intent = next(
        e
        for e in snapshot.events
        if isinstance(e.payload, DispatchIntent) and e.payload.operation is OperationKind.DECIDE
    )
    situation = intent.payload.request
    audit = SqliteDecisionExchangeStore.create(
        tmp_path / "audit-test.sqlite", h.request.run_id, max_record_bytes=2000000
    )
    maker(audit).decide(situation)
    original = audit.read(call_key_of(situation)).record
    audit.close()
    decision = suspended.checkpoint_event.payload.decision
    record = replace(original, completion=replace(original.completion, decision=decision))
    proof = DecisionEvidenceVerified(situation, DecisionResponse(decision), record)

    class AuditView:
        def read(self, call):
            return ExchangeFound(record)

    b = replace(binding(), diagnosis_policy=experimental_policy(experimental_plan(geometries=(support((T1, T2)),))))
    events = []
    for event in snapshot.events:
        if isinstance(event.payload, OperationObserved) and event.payload.operation is OperationKind.DECIDE:
            events.append(
                RunTraceEvent(
                    RunEventId("review-proof"), len(events) + 1, event.run_id, event.created_at, event.usage, proof
                )
            )
        if event.sequence == 1:
            event = replace(event, payload=replace(event.payload, provider_binding=b))
        events.append(replace(event, sequence=len(events) + 1))
    snapshot = replace(snapshot, events=tuple(events))
    return h, snapshot, ConfiguredDecisionEvidenceSource(binding=b, config=config(), audit=AuditView())


@pytest.mark.parametrize("change", ["scalar", "alignment"])
def test_checkpoint_evidence_cache_must_equal_the_recorded_analysis(tmp_path, change):
    h, snapshot, verifier = checkpoint_trace(tmp_path)
    try:
        assert verifier.verify_run(snapshot) is None
        event = snapshot.events[-1]
        checkpoint = event.payload
        assert isinstance(checkpoint, DawCorrectionSuspended)
        cached = checkpoint.observations[-1]
        if change == "scalar":
            obs = cached.observations[0]
            cached = replace(
                cached,
                observations=(
                    replace(obs, result=replace(obs.result, value=obs.result.value + 0.001)),
                    *cached.observations[1:],
                ),
            )
        else:
            cached = replace(cached, alignment=VersionRef("other", "1"))
        changed = replace(checkpoint, observations=(*checkpoint.observations[:-1], cached))
        snapshot = replace(snapshot, events=(*snapshot.events[:-1], replace(event, payload=changed)))
        assert "checkpoint" in (verifier.verify_run(snapshot) or "")
    finally:
        h.close()


def test_status_does_not_claim_provider_run_is_resumable_without_provenance_verification(tmp_path, monkeypatch):
    import autonomous_mixing.drivers.daw_commands as commands

    h, snapshot, verifier = checkpoint_trace(tmp_path)

    class View:
        def snapshot(self):
            return __import__("autonomous_mixing.core.records.results", fromlist=["Success"]).Success(snapshot)

        def close(self):
            pass

    monkeypatch.setattr(commands, "_open_store", lambda *a, **kw: View())
    monkeypatch.setattr(commands, "_fl_facts", lambda *a, **kw: (None, None, None, "not read"))
    try:
        result = commands._status_of(SimpleNamespace(), snapshot.run_id.value)
        assert result["classification"] == "provider_evidence_unavailable"
        assert "resumable" not in result["explanation"]
    finally:
        h.close()


def test_provider_continuation_refuses_when_run_provenance_cannot_be_verified(tmp_path):
    """resume_daw must stop before admission/effects when verify_run reports a problem; nothing is regenerated."""
    from daw_continuation_support import Continuation

    from autonomous_mixing.core.loop.coordinator import resume_daw

    h = Continuation(tmp_path, decisions=(propose(0.0),))
    try:
        suspended = h.start()
        manifest = h.operator_export(suspended)
        b = replace(binding(), diagnosis_policy=experimental_policy(experimental_plan(geometries=(support((T1, T2)),))))
        provider_run = replace(suspended, request=replace(suspended.request, provider_binding=b))

        class Unverifiable:
            binding = b
            checked = []

            def verify(self, situation, result):
                pytest.fail("continuation must not verify decisions before its run provenance is verified")

            def verify_run(self, snapshot):
                self.checked.append(snapshot)
                return "labelled missing provider evidence"

        sent_before, events_before = len(h.world.sent), len(h.journal.snapshot().value.events)
        with pytest.raises(ValueError, match="cannot be verified"):
            resume_daw(provider_run, manifest, **{**h.collaborators(), "decision_evidence": Unverifiable()})
        assert len(Unverifiable.checked) == 1
        assert len(h.world.sent) == sent_before and len(h.journal.snapshot().value.events) == events_before
    finally:
        h.close()
