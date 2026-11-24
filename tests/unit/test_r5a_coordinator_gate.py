"""Audit gate integration; real provider adapter/audit, labelled external responses. No production composition."""

import sqlite3
from dataclasses import replace

import pytest
from masking_diagnosis_support import experimental_plan, experimental_policy, support
from masking_support import Tokens
from test_claude_config import config
from test_claude_decision_maker import Transport, received
from test_coordinator import Diagnosis, Harness, Identities, no_change, payloads
from test_r5a_decision_evidence import binding
from test_run_recording_contracts import T1, T2

from autonomous_mixing.adapters.decision_agent.claude import ClaudeDecisionMaker
from autonomous_mixing.adapters.decision_agent.transport import TransportResult
from autonomous_mixing.adapters.journal.decision_exchange import SqliteDecisionExchangeStore
from autonomous_mixing.adapters.journal.sqlite_run import RunStoreError, SqliteRunJournal
from autonomous_mixing.core.loop.coordinator import run
from autonomous_mixing.core.records.decision import DecisionResponse
from autonomous_mixing.core.records.decision_evidence import DecisionEvidenceRejected, DecisionEvidenceVerified
from autonomous_mixing.core.records.decision_exchange import call_key_of
from autonomous_mixing.core.records.results import Success
from autonomous_mixing.core.records.run_trace import OperationKind, OperationObserved
from autonomous_mixing.core.records.termination import TerminationReason as R
from autonomous_mixing.drivers.decision_evidence import ConfiguredDecisionEvidenceSource
from autonomous_mixing.drivers.diagnosis_runtime import ConfiguredDiagnosisSource


def bound_harness(tmp_path):
    h = Harness(decisions=(no_change,))
    pol = experimental_policy(experimental_plan(geometries=(support((T1, T2)),)))
    b = replace(binding(), diagnosis_policy=pol)
    h.request = replace(h.request, provider_binding=b)
    h.diagnosis = ConfiguredDiagnosisSource(policy=pol, identities=Tokens("diagnosis"))
    store = SqliteDecisionExchangeStore.create(tmp_path / "audit.sqlite", h.request.run_id, max_record_bytes=2000000)
    return h, store


def execute(h, decider, evidence=None):
    return run(
        h.request,
        analyzer=h.analyzer,
        diagnosis_source=h.diagnosis,
        decision_maker=decider,
        renderer=h.renderer,
        integrity_inspector=h.inspector,
        journal=h.journal,
        clock=h.clock,
        identities=h.identities,
        decision_evidence=evidence,
    )


def test_provider_binding_without_evidence_source_refuses_before_dispatch(tmp_path):
    h, store = bound_harness(tmp_path)
    try:
        with pytest.raises(ValueError, match="evidence"):
            execute(h, h.decider)
        assert h.decider.situations == [] and h.analyzer.requests == []
    finally:
        store.close()


def test_matching_audit_is_recorded_before_provider_result_is_used(tmp_path):
    h, store = bound_harness(tmp_path)
    transport = Transport()
    maker = ClaudeDecisionMaker(
        config=config(), transport=transport, audit=store, identities=Identities(), clock=h.clock
    )
    evidence = ConfiguredDecisionEvidenceSource(binding=h.request.provider_binding, config=config(), audit=store)
    try:
        report = execute(h, maker, evidence)
        assert report.final_run.termination_reason is R.NO_CHANGE_PROPOSED
        assert len(transport.calls) == 1
        proof = payloads(report, DecisionEvidenceVerified)
        assert len(proof) == 1
        rows = report.trace.events
        proof_index = next(i for i, e in enumerate(rows) if isinstance(e.payload, DecisionEvidenceVerified))
        observed_index = next(
            i
            for i, e in enumerate(rows)
            if isinstance(e.payload, OperationObserved) and e.payload.operation is OperationKind.DECIDE
        )
        assert proof_index < observed_index
        assert evidence.verify_run(report.trace) is None
        assert h.renderer.requests == []
    finally:
        store.close()


def test_missing_audit_blocks_even_a_valid_port_result_without_retry_or_attempt(tmp_path):
    h, store = bound_harness(tmp_path)
    evidence = ConfiguredDecisionEvidenceSource(binding=h.request.provider_binding, config=config(), audit=store)
    try:
        report = execute(h, h.decider, evidence)
        assert report.final_run.termination_reason is R.DECISION_FAILED
        assert len(h.decider.situations) == 1
        assert report.final_run.usage.attempts == 0 and h.renderer.requests == []
        assert len(payloads(report, DecisionEvidenceRejected)) == 1
        assert not [e for e in payloads(report, OperationObserved) if e.operation is OperationKind.DECIDE]
    finally:
        store.close()


def test_only_completed_retryable_failure_gets_a_fresh_charged_retry(tmp_path):
    h, store = bound_harness(tmp_path)

    class RetryTransport(Transport):
        def send(self, body, *, timeout_seconds):
            self.calls.append((body, timeout_seconds))
            return (
                TransportResult(None, None, None, "test", "connection_failed", True)
                if len(self.calls) == 1
                else received()
            )

    transport = RetryTransport()
    maker = ClaudeDecisionMaker(
        config=config(), transport=transport, audit=store, identities=Identities(), clock=h.clock
    )
    evidence = ConfiguredDecisionEvidenceSource(binding=h.request.provider_binding, config=config(), audit=store)
    try:
        report = execute(h, maker, evidence)
        assert report.final_run.termination_reason is R.NO_CHANGE_PROPOSED
        assert report.final_run.usage.decision_calls == len(transport.calls) == 2
        assert len(payloads(report, DecisionEvidenceVerified)) == 2
    finally:
        store.close()


def test_new_run_store_explicitly_refuses_legacy_schema_before_decoding(tmp_path):
    h = Harness()
    path = tmp_path / "run.sqlite"
    store = SqliteRunJournal.create(path, h.request.run_id, h.real_journal.snapshot().value.initial)
    store.close()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT schema_version FROM metadata").fetchone()[0] == 2
        db.execute("UPDATE metadata SET schema_version=1")
    before = path.read_bytes()
    with pytest.raises(RunStoreError, match="legacy|unsupported"):
        SqliteRunJournal.open_read_only(path)
    assert path.read_bytes() == before


def test_provider_run_refuses_diagnosis_from_another_method_before_decision(tmp_path):
    h, store = bound_harness(tmp_path)
    h.diagnosis = Diagnosis()
    evidence = ConfiguredDecisionEvidenceSource(binding=h.request.provider_binding, config=config(), audit=store)
    try:
        report = execute(h, h.decider, evidence)
        assert report.final_run.termination_reason is R.RUNTIME_CONTRACT_FAILED
        assert h.decider.situations == []
    finally:
        store.close()


def test_interrupted_dispatch_prefix_cannot_be_verified_as_complete(tmp_path):
    h, store = bound_harness(tmp_path)
    maker = ClaudeDecisionMaker(
        config=config(), transport=Transport(), audit=store, identities=Identities(), clock=h.clock
    )
    evidence = ConfiguredDecisionEvidenceSource(binding=h.request.provider_binding, config=config(), audit=store)
    try:
        report = execute(h, maker, evidence)
        index = next(i for i, e in enumerate(report.trace.events) if isinstance(e.payload, DecisionEvidenceVerified))
        prefix = replace(report.trace, events=report.trace.events[:index])
        assert evidence.verify_run(prefix) is not None
    finally:
        store.close()


def test_lost_proof_append_acknowledgement_stops_before_using_the_decision(tmp_path):
    from test_coordinator import FaultyJournal

    h, store = bound_harness(tmp_path)
    h.journal = FaultyJournal(
        h.real_journal,
        append_fault=lambda event: (
            "unknown-after-write" if isinstance(event.payload, DecisionEvidenceVerified) else None
        ),
    )
    maker = ClaudeDecisionMaker(
        config=config(), transport=Transport(), audit=store, identities=Identities(), clock=h.clock
    )
    evidence = ConfiguredDecisionEvidenceSource(binding=h.request.provider_binding, config=config(), audit=store)
    try:
        report = execute(h, maker, evidence)
        assert report.final_run.termination_reason is R.RECORDING_FAILED
        assert not [e for e in payloads(report, OperationObserved) if e.operation is OperationKind.DECIDE]
        assert report.final_run.usage.attempts == 0 and h.renderer.requests == []
    finally:
        store.close()


def test_both_stores_reopen_with_exact_provider_binding_and_trace_evidence(tmp_path):
    from autonomous_mixing.core.records.acceptance import AcceptedReference

    h, audit = bound_harness(tmp_path)
    path = tmp_path / "durable.sqlite"
    journal = SqliteRunJournal.create(path, h.request.run_id, AcceptedReference(h.request.initial_state, 0))
    h.journal = journal
    transport = Transport()
    maker = ClaudeDecisionMaker(
        config=config(), transport=transport, audit=audit, identities=Identities(), clock=h.clock
    )
    evidence = ConfiguredDecisionEvidenceSource(binding=h.request.provider_binding, config=config(), audit=audit)
    try:
        report = execute(h, maker, evidence)
        assert evidence.verify_run(report.trace) is None
    finally:
        journal.close()
        audit.close()
    run_before = path.read_bytes()
    audit_path = tmp_path / "audit.sqlite"
    audit_before = audit_path.read_bytes()
    reopened = SqliteRunJournal.open_read_only(path)
    # Exchange store currently owns its existing exclusive read protocol; no provider transport is constructed.
    reopened_audit = SqliteDecisionExchangeStore.open(audit_path, run_id=h.request.run_id, max_record_bytes=2000000)
    try:
        snapshot = reopened.snapshot().value
        verifier = ConfiguredDecisionEvidenceSource(
            binding=h.request.provider_binding, config=config(), audit=reopened_audit
        )
        assert snapshot.events[0].payload.provider_binding == h.request.provider_binding
        assert verifier.verify_run(snapshot) is None
        assert len(transport.calls) == 1
    finally:
        reopened.close()
        reopened_audit.close()
    assert path.read_bytes() == run_before and audit_path.read_bytes() == audit_before


@pytest.mark.parametrize("lie", ["situation", "result"])
def test_a_proof_for_another_input_or_result_is_refused_by_core(tmp_path, lie):
    """Core compares the returned proof with the exact call it made; an evidence source cannot vouch for another."""
    h, store = bound_harness(tmp_path)
    transport = Transport()
    maker = ClaudeDecisionMaker(
        config=config(), transport=transport, audit=store, identities=Identities(), clock=h.clock
    )
    honest = ConfiguredDecisionEvidenceSource(binding=h.request.provider_binding, config=config(), audit=store)

    class Lying:
        binding = honest.binding

        def verify(self, situation, result):
            if lie == "situation":
                proof = honest.verify(situation, result)
                assert isinstance(proof, DecisionEvidenceVerified)
                changed = replace(
                    situation, limits=replace(situation.limits, max_attempts=situation.limits.max_attempts + 1)
                )
                return replace(proof, situation=changed)
            # vouch for the recorded decision, not the (altered) result the decision maker actually returned
            recorded = store.read(call_key_of(situation)).record.completion.decision
            return honest.verify(situation, Success(DecisionResponse(recorded)))

        def verify_run(self, snapshot):
            return honest.verify_run(snapshot)

    class Altering:
        def decide(self, situation):
            outcome = maker.decide(situation)
            if lie == "result":
                decision = replace(outcome.value.decision, rationale="a different rationale")
                return Success(DecisionResponse(decision))
            return outcome

    try:
        report = execute(h, Altering(), Lying())
        assert report.final_run.termination_reason is R.DECISION_FAILED
        assert len(transport.calls) == 1 and h.renderer.requests == []
        rejected = payloads(report, DecisionEvidenceRejected)
        assert len(rejected) == 1 and "foreign" in rejected[0].reason
        assert not payloads(report, DecisionEvidenceVerified)
        assert not [e for e in payloads(report, OperationObserved) if e.operation is OperationKind.DECIDE]
    finally:
        store.close()
