"""Offline acceptance authority under unexpected collaborator errors.

Uses the labelled coordinator doubles and real in-memory journal from test_coordinator. No audio,
scientific diagnosis, provider call or DAW host is involved. Exceptions deliberately preserve the
uncertainty between a lost request and a successful commit with a lost acknowledgement.
"""

import dataclasses

import pytest
from test_coordinator import INITIAL_VALUE, SATISFYING, FaultyJournal, Harness, propose

from autonomous_mixing.core.records.acceptance import (
    AcceptanceCommitUnknown,
    AcceptanceEvent,
    AcceptanceFound,
    AcceptanceNotCommitted,
)
from autonomous_mixing.core.records.identifiers import DecisionId
from autonomous_mixing.core.records.run import FinalProjectState
from autonomous_mixing.core.records.run_trace import OperationKind, OperationObserved, TerminalSnapshot
from autonomous_mixing.core.records.termination import TerminationReason


def terminal_of(harness):
    snapshot = harness.real_journal.snapshot().value
    terminal = snapshot.events[-1].payload
    assert isinstance(terminal, TerminalSnapshot)
    return terminal


class RaisingCommit(FaultyJournal):
    def __init__(self, inner, *, persist, error):
        super().__init__(inner)
        self.persist, self.error, self.commit_calls, self.query_calls = persist, error, 0, 0

    def commit(self, event):
        self.commit_calls += 1
        if self.persist:
            self.inner.commit(event)
        raise self.error

    def query(self, request):
        self.query_calls += 1
        return super().query(request)


@pytest.mark.parametrize("persist", [False, True], ids=["before-commit", "after-commit"])
def test_commit_exception_keeps_authority_unknown_and_propagates_original(persist):
    error = RuntimeError("labelled acceptance dispatch failure")
    harness = Harness(
        analysis=(INITIAL_VALUE, SATISFYING),
        decisions=(propose(-3.0),),
        journal=lambda inner: RaisingCommit(inner, persist=persist, error=error),
    )
    with pytest.raises(RuntimeError) as raised:
        harness.run()
    assert raised.value is error
    terminal = terminal_of(harness)
    assert terminal.run.termination_reason is TerminationReason.ACCEPTANCE_COMMIT_UNCERTAIN
    assert terminal.authoritative is None and terminal.verified is None
    assert terminal.run.final_project_state is FinalProjectState.UNKNOWN
    assert "acceptance" in terminal.run.termination_explanation.lower()
    assert "RuntimeError" in terminal.run.termination_explanation and "DAW" not in terminal.run.termination_explanation
    assert harness.journal.commit_calls == 1 and harness.journal.query_calls == 0
    assert len(harness.real_journal.commits) == int(persist)
    assert len(harness.inspector.requests) == 1  # only initial integrity, no speculative recovery


def test_confirmed_commit_is_authoritative_before_fallible_promotion(monkeypatch):
    import autonomous_mixing.core.loop.acceptance_flow as flow

    error = OSError("labelled promotion failure")

    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(flow, "promote", broken)
    harness = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),))
    with pytest.raises(OSError) as raised:
        harness.run()
    assert raised.value is error
    terminal = terminal_of(harness)
    (committed,) = harness.real_journal.commits
    assert terminal.authoritative == committed.accepted
    assert terminal.authoritative.sequence == 1
    assert terminal.verified is None and terminal.run.final_project_state is FinalProjectState.UNKNOWN
    assert terminal.run.termination_reason is TerminationReason.RUNTIME_CONTRACT_FAILED
    assert len(harness.inspector.requests) == 1


class RaisingObservation(FaultyJournal):
    def __init__(self, inner, *, commit_fault, payload_type=None, on_query=False, error):
        super().__init__(inner, commit_fault=commit_fault)
        self.payload_type, self.on_query, self.error = payload_type, on_query, error

    def append(self, event):
        payload = event.payload
        if (self.payload_type is not None and isinstance(payload, self.payload_type)) or (
            self.on_query
            and isinstance(payload, OperationObserved)
            and payload.operation is OperationKind.QUERY_ACCEPTANCE
        ):
            raise self.error
        return super().append(event)


@pytest.mark.parametrize("persist", [False, True], ids=["absent", "found"])
def test_query_answer_establishes_authority_before_recording_it(persist):
    error = RuntimeError("labelled query observation failure")
    harness = Harness(
        analysis=(INITIAL_VALUE, SATISFYING),
        decisions=(propose(-3.0),),
        journal=lambda inner: RaisingObservation(
            inner,
            commit_fault="unknown-after-write" if persist else "unknown-before-write",
            on_query=True,
            error=error,
        ),
    )
    with pytest.raises(RuntimeError) as raised:
        harness.run()
    assert raised.value is error
    terminal = terminal_of(harness)
    expected = harness.real_journal.commits[0].accepted if persist else harness.real_journal.initial
    assert terminal.authoritative == expected
    assert terminal.verified is None and terminal.run.final_project_state is FinalProjectState.UNKNOWN
    assert terminal.run.termination_reason is TerminationReason.RUNTIME_CONTRACT_FAILED
    assert len(harness.inspector.requests) == 1


def test_unknown_commit_remains_unknown_if_recording_its_response_raises():
    error = RuntimeError("labelled unknown-response recording failure")
    harness = Harness(
        analysis=(INITIAL_VALUE, SATISFYING),
        decisions=(propose(-3.0),),
        journal=lambda inner: RaisingObservation(
            inner,
            commit_fault="unknown-after-write",
            payload_type=AcceptanceCommitUnknown,
            error=error,
        ),
    )
    with pytest.raises(RuntimeError) as raised:
        harness.run()
    assert raised.value is error
    terminal = terminal_of(harness)
    assert terminal.authoritative is None and terminal.verified is None
    assert terminal.run.termination_reason is TerminationReason.ACCEPTANCE_COMMIT_UNCERTAIN


@pytest.mark.parametrize("before_dispatch", [False, True], ids=["confirmed-refusal", "before-dispatch"])
def test_known_noncommit_does_not_retain_an_acceptance_uncertainty_marker(before_dispatch):
    error = RuntimeError("labelled noncommit recording failure")
    harness = Harness(
        analysis=(INITIAL_VALUE, SATISFYING),
        decisions=(propose(-3.0),),
        journal=lambda inner: RaisingObservation(
            inner,
            commit_fault="refuse",
            payload_type=AcceptanceEvent if before_dispatch else AcceptanceNotCommitted,
            error=error,
        ),
    )
    with pytest.raises(RuntimeError) as raised:
        harness.run()
    assert raised.value is error
    terminal = terminal_of(harness)
    assert terminal.authoritative is not None and terminal.authoritative.sequence == 0
    assert terminal.run.termination_reason is TerminationReason.RUNTIME_CONTRACT_FAILED
    assert harness.real_journal.commits == ()


def test_found_answer_with_different_event_contents_never_establishes_authority():
    class WrongEvent(FaultyJournal):
        def query(self, request):
            answer = super().query(request)
            changed = dataclasses.replace(answer.event, decision_id=DecisionId("other-decision"))
            return AcceptanceFound(changed, answer.accepted)

    harness = Harness(
        analysis=(INITIAL_VALUE, SATISFYING),
        decisions=(propose(-3.0),),
        journal=lambda inner: WrongEvent(inner, commit_fault="unknown-after-write"),
    )
    report = harness.run()
    assert report.final_run.termination_reason is TerminationReason.ACCEPTANCE_COMMIT_UNCERTAIN
    assert report.authoritative is None and report.verified is None
    assert len(harness.inspector.requests) == 1
