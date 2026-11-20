"""Increment 3 of the coordinator-integration plan (amended): continuation of a suspended DAW run (``resume_daw``).

Real adapters throughout (FL controller on a fake host, export handoff, file analyzer, integrity inspector) over
temporary synthetic WAVs; labelled doubles for decisions and diagnosis. Orchestration evidence only, not live FL.
"""

import json
from datetime import timedelta

import pytest
from daw_continuation_support import (
    ACCEPT,
    INTERMEDIATE,
    NO_IMPROVEMENT,
    SESSION,
    SILENT,
    UTC,
    Continuation,
    other_manifest,
)
from fl_runtime_support import make_request
from test_coordinator import Clock, no_change, propose
from test_fl_runtime_recovery_controller import live_curve

import autonomous_mixing.core.loop.daw_continuation as daw_continuation
from autonomous_mixing.adapters.fl_studio.serialization import request_json
from autonomous_mixing.adapters.journal.run_memory import InMemoryRunJournal
from autonomous_mixing.core.records.acceptance import (
    AcceptanceAuthorityUnknown,
    AcceptanceCommitted,
    AcceptanceCommitUnknown,
    AcceptanceEvent,
    AcceptedReference,
)
from autonomous_mixing.core.records.actions import ExecutionReceipt, RollbackScope, RollbackStatus
from autonomous_mixing.core.records.admission import PreflightCode, PreflightRejected
from autonomous_mixing.core.records.coordinator_runtime import ClockReading
from autonomous_mixing.core.records.daw_control import DawRefusalCode, DawWriteRefused
from autonomous_mixing.core.records.daw_correction import (
    DawContinuationDisposition,
    DawContinuationFinished,
    DawContinuationRefusalCode,
    DawContinuationRefused,
    DawContinuationStarted,
)
from autonomous_mixing.core.records.daw_export import DawImportRejected, DawImportVerified
from autonomous_mixing.core.records.run import AttemptDisposition, CorrectionAttempt, FinalProjectState
from autonomous_mixing.core.records.run_report import RunReport, RunSuspended
from autonomous_mixing.core.records.run_trace import (
    OperationKind,
    OperationObserved,
    TerminalSnapshot,
    TraceNotAppended,
)
from autonomous_mixing.core.records.termination import TerminationReason as R


@pytest.fixture
def harness(tmp_path):
    made = []

    def make(**kwargs):
        made.append(Continuation(tmp_path, **kwargs))
        return made[-1]

    yield make
    for item in made:
        item.close()


def payloads(result, kind):
    return [e.payload for e in result.trace.events if isinstance(e.payload, kind)]


def terminal_of(h):
    return h.real_journal.snapshot().value.events[-1].payload


def suspend(h):
    first = h.start()
    assert isinstance(first, RunSuspended), first
    return first


# --- the accepted path ------------------------------------------------------------------------------------------


def test_a_verified_export_is_analysed_verified_and_accepted(harness):
    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)
    report = h.resume(suspended, h.operator_export(suspended, ACCEPT))
    assert isinstance(report, RunReport), report
    run = report.final_run
    assert (run.termination_reason, run.final_project_state) == (R.CANDIDATE_CRITERIA_MET,
                                                                 FinalProjectState.ACCEPTED_EDITS_RETAINED)  # fmt: skip
    receipt = payloads(report, ExecutionReceipt)[0]
    assert [a.value for a in receipt.superseded_artifact_ids] == ["s1", "mix"]
    accepted = report.authoritative.state
    anchor = suspended.checkpoint_event.payload.awaiting.request.anchor
    assert accepted.daw.track(SESSION.binding.track_id).confirmed_fader == anchor.reading
    assert all(a.channel_count == 2 for a in receipt.candidate_artifacts)  # native stereo kept
    started = payloads(report, DawContinuationStarted)[0]
    finished = payloads(report, DawContinuationFinished)[0]
    assert finished.disposition is DawContinuationDisposition.ACCEPTED and finished.export_id == started.export_id
    assert payloads(report, DawImportVerified) and report.verified == report.authoritative


def test_an_accepted_intermediate_candidate_iterates_to_another_suspension_and_duplicates_are_refused(harness):
    h = harness(decisions=(propose(0.0), propose(-0.5)))
    first = suspend(h)
    manifest = h.operator_export(first, INTERMEDIATE)
    second = h.resume(first, manifest)
    assert isinstance(second, RunSuspended), second
    events = second.trace.events
    finished = [i for i, e in enumerate(events) if isinstance(e.payload, DawContinuationFinished)]
    assert len(finished) == 1 and finished[0] < len(events) - 1  # the marker precedes the second checkpoint
    sent, appended = len(h.world.sent), len(h.real_journal.snapshot().value.events)
    duplicate = h.resume(first, manifest)
    assert isinstance(duplicate, DawContinuationRefused)
    assert duplicate.code is DawContinuationRefusalCode.ALREADY_CONTINUED
    assert duplicate.evidence_event_id == events[finished[0]].event_id
    conflict = h.resume(first, other_manifest(manifest))
    assert conflict.code is DawContinuationRefusalCode.MANIFEST_CONFLICT
    assert (len(h.world.sent), len(h.real_journal.snapshot().value.events)) == (sent, appended)
    final = h.resume(second, h.operator_export(second, ACCEPT * 0.5))
    assert isinstance(final, RunReport) and final.final_run.termination_reason is R.CANDIDATE_CRITERIA_MET


def test_a_duplicate_after_a_run_ending_continuation_is_already_continued(harness):
    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)
    manifest = h.operator_export(suspended, ACCEPT)
    report = h.resume(suspended, manifest)
    assert isinstance(report, RunReport)
    again = h.resume(suspended, manifest)
    assert again.code is DawContinuationRefusalCode.ALREADY_CONTINUED
    assert h.resume(suspended, other_manifest(manifest)).code is DawContinuationRefusalCode.MANIFEST_CONFLICT


# --- candidate failures are recovered (Increment 4, ADR-038, superseding the interim stops) -----------------------


def assert_stopped_safely(report, reason):
    """Stopped with ``reason`` after an exact restoration and a VERIFIED selected-state rollback; nothing accepted."""
    assert isinstance(report, RunReport) and report.final_run.termination_reason is reason
    assert report.final_run.final_project_state is FinalProjectState.RESTORED_TO_LAST_ACCEPTED
    assert report.verified == report.authoritative and report.authoritative.sequence == 0
    assert not payloads(report, AcceptanceCommitted)


@pytest.mark.parametrize("master", [NO_IMPROVEMENT, SILENT])
def test_a_rejected_or_inconclusive_candidate_is_restored_with_a_verified_selected_state_rollback(harness, master):
    h = harness(decisions=(propose(0.0), no_change))
    suspended = suspend(h)
    report = h.resume(suspended, h.operator_export(suspended, master))
    reason = report.final_run.termination_reason
    # a rejected candidate continues the loop (here: no change); an inconclusive or failed one terminates
    assert reason in (R.NO_CHANGE_PROPOSED, R.VERIFICATION_INCONCLUSIVE, R.CANDIDATE_ANALYSIS_FAILED)
    assert_stopped_safely(report, reason)
    attempt = payloads(report, CorrectionAttempt)[-1]
    assert attempt.disposition is AttemptDisposition.ROLLED_BACK
    rollback = attempt.rollback
    assert (rollback.status, rollback.scope, rollback.retained_audio) == (
        RollbackStatus.VERIFIED, RollbackScope.SELECTED_STATE_WITH_FADER, RollbackStatus.VERIFIED)  # fmt: skip
    assert len(rollback.fader_evidence) == 1 and len(h.sent("restore_gain")) == 1
    assert not payloads(report, AcceptanceEvent)


@pytest.mark.parametrize("problem", ["stray", "hash", "mono"])
def test_a_rejected_import_stops_without_any_candidate(harness, problem):
    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)
    manifest = h.operator_export(suspended, ACCEPT, extra="notes.txt" if problem == "stray" else None,
                                 tamper=problem == "hash", mono=problem == "mono")  # fmt: skip
    report = h.resume(suspended, manifest)
    assert_stopped_safely(report, R.EXPORT_IMPORT_REJECTED)
    assert payloads(report, DawImportRejected) and not payloads(report, ExecutionReceipt)


def test_a_fader_moved_during_the_pause_breaks_continuity(harness):
    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)
    h.world.host.volumes[2] += 1e-6
    report = h.resume(suspended, h.operator_export(suspended, ACCEPT))
    assert_stopped_safely(report, R.DAW_CONTINUITY_FAILED)
    assert h.fl_journal.control_state().unresolved == () and len(h.sent("restore_gain")) == 1


def test_a_ledger_entry_during_the_pause_breaks_continuity(harness):
    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)
    refused = make_request("apply_local_009")
    h.fl_journal._remember_refusal("apply_local_009", request_json(refused),
                                   json.dumps({"id": "apply_local_009", "local_refusal": True, "type": "set_gain_db"},
                                              sort_keys=True),
                                   DawWriteRefused("apply_local_009", DawRefusalCode.UNAVAILABLE, "local", None))  # fmt: skip
    report = h.resume(suspended, h.operator_export(suspended, ACCEPT))
    assert_stopped_safely(report, R.DAW_CONTINUITY_FAILED)


# --- pause accounting ---------------------------------------------------------------------------------------------


class LateClock(Clock):
    """A fresh process clock: monotonic far beyond the elapsed limit, UTC two hours after the suspension."""

    def read(self):
        index = self.reads
        self.reads += 1
        return ClockReading(10_000.0 + index * 0.001, UTC + timedelta(hours=2, seconds=index * 0.001), False)


def test_the_operator_pause_is_recorded_but_never_charged(harness):
    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)
    before = suspended.checkpoint_event.usage.elapsed_seconds
    report = h.resume(suspended, h.operator_export(suspended, ACCEPT), clock=LateClock())
    assert report.final_run.termination_reason is R.CANDIDATE_CRITERIA_MET  # not ELAPSED_TIME_LIMIT
    assert before <= report.final_run.usage.elapsed_seconds < before + 1.0
    started = payloads(report, DawContinuationStarted)[0]
    assert started.resumed_at_utc - started.paused_at_utc >= timedelta(hours=1)


# --- acceptance authority (amendment A1 and the approval's constraint) -------------------------------------------


class Wrapped:
    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)


class CommitThenRaise(Wrapped):
    def commit(self, event):
        self.inner.commit(event)
        raise RuntimeError("labelled failure after the commit was written")


def test_a_commit_that_succeeds_then_raises_keeps_acceptance_uncertain(harness):
    h = harness(decisions=(propose(0.0),), journal=CommitThenRaise)
    suspended = suspend(h)
    with pytest.raises(RuntimeError, match="after the commit"):
        h.resume(suspended, h.operator_export(suspended, ACCEPT))
    snapshot = terminal_of(h)
    assert isinstance(snapshot, TerminalSnapshot)
    assert snapshot.run.termination_reason is R.ACCEPTANCE_COMMIT_UNCERTAIN
    assert snapshot.authoritative is None and snapshot.run.final_project_state is FinalProjectState.UNKNOWN
    assert len(h.real_journal.snapshot().value.acceptances) == 1 and not h.sent("restore_gain")


class UnknownCommit(Wrapped):
    def __init__(self, inner, *, write, answer=None):
        super().__init__(inner)
        self.write, self.answer = write, answer

    def commit(self, event):
        if self.write:
            self.inner.commit(event)
        return AcceptanceCommitUnknown(event.acceptance_id, "labelled lost acknowledgement")

    def query(self, request):
        if self.answer == "unknown":
            return AcceptanceAuthorityUnknown(request.run_id, request.acceptance_id, "labelled unknown authority")
        return self.inner.query(request)


@pytest.mark.parametrize(
    "write, answer, reason, final",
    [
        (True, None, R.CANDIDATE_CRITERIA_MET, FinalProjectState.ACCEPTED_EDITS_RETAINED),  # found: DAW promotion
        # absent: the absence restores nothing; DAW recovery then restores exactly (Increment 4, ADR-038)
        (False, None, R.RECORDING_FAILED, FinalProjectState.RESTORED_TO_LAST_ACCEPTED),
        (True, "unknown", R.ACCEPTANCE_COMMIT_UNCERTAIN, FinalProjectState.UNKNOWN),
    ],
)
def test_an_unknown_commit_is_reconciled_without_offline_recovery(harness, write, answer, reason, final):
    h = harness(decisions=(propose(0.0),), journal=lambda inner: UnknownCommit(inner, write=write, answer=answer))
    suspended = suspend(h)
    report = h.resume(suspended, h.operator_export(suspended, ACCEPT))
    assert (report.final_run.termination_reason, report.final_run.final_project_state) == (reason, final)
    restored = final is FinalProjectState.RESTORED_TO_LAST_ACCEPTED
    assert len(h.sent("restore_gain")) == (1 if restored else 0)  # never after a found or unknown acceptance


class RefuseCommittedRecord(Wrapped):
    def append(self, event):
        if isinstance(event.payload, AcceptanceCommitted):
            return TraceNotAppended(event.event_id, "labelled refusal")
        return self.inner.append(event)


def test_a_recording_failure_after_an_acknowledged_acceptance_keeps_the_accepted_edit(harness):
    h = harness(decisions=(propose(0.0),), journal=RefuseCommittedRecord)
    suspended = suspend(h)
    report = h.resume(suspended, h.operator_export(suspended, ACCEPT))
    assert report.final_run.termination_reason is R.RECORDING_FAILED
    assert report.final_run.final_project_state is FinalProjectState.ACCEPTED_EDITS_RETAINED
    assert report.authoritative.sequence == 1 and report.verified == report.authoritative


def test_an_exception_during_promotion_never_reports_the_previous_reference(harness, monkeypatch):
    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)

    def broken(*args, **kwargs):
        raise RuntimeError("labelled promotion failure")

    monkeypatch.setattr(daw_continuation, "promote_daw", broken)
    with pytest.raises(RuntimeError, match="promotion"):
        h.resume(suspended, h.operator_export(suspended, ACCEPT))
    snapshot = terminal_of(h)
    assert snapshot.authoritative is not None and snapshot.authoritative.sequence == 1  # the new reference
    assert snapshot.verified is None and snapshot.run.final_project_state is FinalProjectState.UNKNOWN


# --- duplicate and concurrent continuations (amendment A2) --------------------------------------------------------


class RefuseAfterStart(Wrapped):
    """Accepts appends up to and including the continuation start, then refuses every append (terminal too)."""

    started = False

    def append(self, event):
        if self.started:
            return TraceNotAppended(event.event_id, "labelled refusal")
        response = self.inner.append(event)
        if isinstance(event.payload, DawContinuationStarted):
            self.started = True
        return response


def test_a_continuation_without_completion_evidence_is_interrupted(harness):
    h = harness(decisions=(propose(0.0),), journal=RefuseAfterStart)
    suspended = suspend(h)
    manifest = h.operator_export(suspended, ACCEPT)
    report = h.resume(suspended, manifest)
    assert report.final_run.termination_reason is R.RECORDING_FAILED
    again = h.resume(suspended, manifest)
    assert again.code is DawContinuationRefusalCode.CONTINUATION_INTERRUPTED


def test_a_concurrent_continuation_is_refused_while_the_run_lock_is_held(harness):
    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)
    manifest = h.operator_export(suspended, ACCEPT)
    inner, seen = h.handoff, []

    class Reentrant(Wrapped):
        def import_files(self, request):
            seen.append(h.resume(suspended, manifest))
            return inner.import_files(request)

    h.handoff = Reentrant(inner)
    report = h.resume(suspended, manifest)
    assert seen[0].code is DawContinuationRefusalCode.CONTINUATION_IN_PROGRESS
    assert isinstance(report, RunReport) and report.final_run.termination_reason is R.CANDIDATE_CRITERIA_MET


def test_the_run_lock_is_one_authority_per_run_even_across_journal_instances(harness):
    h = harness(decisions=(propose(0.0),))
    other = InMemoryRunJournal(h.request.run_id, AcceptedReference(h.initial, 0))
    assert h.real_journal.try_acquire_exclusive()
    assert not other.try_acquire_exclusive() and not h.real_journal.try_acquire_exclusive()
    h.real_journal.release_exclusive()
    assert other.try_acquire_exclusive()
    other.release_exclusive()
    with pytest.raises(ValueError):
        other.release_exclusive()  # not held


def test_a_suspension_that_is_not_in_the_journal_is_refused(harness):
    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)
    stranger = Continuation(h.tmp / "stranger", decisions=())
    try:
        refused = stranger.resume(suspended, h.operator_export(suspended, ACCEPT))
        assert refused.code is DawContinuationRefusalCode.NOT_SUSPENDED
    finally:
        stranger.close()


# --- exceptions, channels and offline preservation ----------------------------------------------------------------


def test_an_exception_during_the_import_keeps_recovery_required(harness):
    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)

    class Raising(Wrapped):
        def import_files(self, request):
            raise OSError("labelled disk failure")

    h.handoff = Raising(h.handoff)
    with pytest.raises(OSError):
        h.resume(suspended, h.operator_export(suspended, ACCEPT))
    snapshot = terminal_of(h)
    assert snapshot.run.termination_reason is R.REQUIRED_RECOVERY_NOT_PERFORMED
    assert snapshot.run.final_project_state is FinalProjectState.UNKNOWN


def test_baselines_with_other_export_settings_are_refused_before_any_write(harness, monkeypatch):
    from dataclasses import replace

    from autonomous_mixing.core.records.identifiers import VersionRef

    h = harness(decisions=(propose(0.0), no_change))
    h.request = replace(h.request, daw_session=replace(SESSION, render_spec=VersionRef("fl-export", "2")))
    report = h.start()
    rejected = payloads(report, PreflightRejected)
    assert rejected and rejected[0].code is PreflightCode.INCOMPATIBLE_AUDIO and not h.sent("set_gain_db")


def test_an_off_target_curve_is_not_part_of_this_increment(harness):
    """Guard: an off-target write still ends before any export; since Increment 4 it is restored first."""
    h = harness(decisions=(propose(0.0),))
    h.world.host.curve = lambda v: live_curve(v) + (0.0 if v == 0.76 else 0.2)
    report = h.start()
    assert isinstance(report, RunReport) and report.final_run.termination_reason is R.DAW_CONVERSION_BLOCKED


# --- each continuity snapshot and the promotion's root binding, pinned individually ---------------------------


def _moving_import(h, *, before, during):
    """The fader is set to ``before`` ahead of the continuation and to ``during`` inside the import (labelled)."""
    inner, host = h.handoff, h.world.host
    original = host.volumes[2]

    class Moving(Wrapped):
        def import_files(self, request):
            host.volumes[2] = original + during
            return inner.import_files(request)

    host.volumes[2] = original + before
    h.handoff = Moving(inner)


def test_a_fader_moved_only_during_the_import_is_caught_after_it(harness):
    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)
    manifest = h.operator_export(suspended, ACCEPT)
    _moving_import(h, before=0.0, during=1e-6)
    assert_stopped_safely(h.resume(suspended, manifest), R.DAW_CONTINUITY_FAILED)


def test_a_fader_moved_back_during_the_import_is_caught_before_it(harness):
    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)
    manifest = h.operator_export(suspended, ACCEPT)
    _moving_import(h, before=1e-6, during=0.0)
    assert_stopped_safely(h.resume(suspended, manifest), R.DAW_CONTINUITY_FAILED)


def test_a_promoted_candidate_selects_its_exports_under_the_export_root(harness):
    h = harness(decisions=(propose(0.0), propose(-0.5)))
    first = suspend(h)
    second = h.resume(first, h.operator_export(first, INTERMEDIATE))
    checkpoint = second.checkpoint_event.payload
    exported = {o.artifact_id for o in first.checkpoint_event.payload.awaiting.request.outputs}
    roots = {b.artifact_id: b.root_key for b in checkpoint.roots}
    assert all(roots[aid] == SESSION.export_root_key for aid in exported)
    master = next(o for o in first.checkpoint_event.payload.awaiting.request.outputs if o.kind.value == "mix")
    assert checkpoint.mix_id == master.artifact_id


# --- review fixes 2026-11-19: found authority before recording; resume bound to the journaled request ---------


class UnknownCommitRaisingOnAnswer(UnknownCommit):
    """Commit lost (after or before the real write); appending the authority answer's observation raises."""

    def append(self, event):
        payload = event.payload
        if isinstance(payload, OperationObserved) and payload.operation is OperationKind.QUERY_ACCEPTANCE:
            raise RuntimeError("labelled failure while recording the authority answer")
        return self.inner.append(event)


def test_a_found_acceptance_is_authoritative_even_if_recording_its_answer_raises(harness):
    h = harness(decisions=(propose(0.0),), journal=lambda inner: UnknownCommitRaisingOnAnswer(inner, write=True))
    suspended = suspend(h)
    with pytest.raises(RuntimeError, match="authority answer"):
        h.resume(suspended, h.operator_export(suspended, ACCEPT))
    snapshot = terminal_of(h)
    assert snapshot.authoritative is not None and snapshot.authoritative.sequence == 1  # the found reference
    assert snapshot.run.termination_reason is not R.ACCEPTANCE_COMMIT_UNCERTAIN
    assert snapshot.verified is None and snapshot.run.final_project_state is FinalProjectState.UNKNOWN


def test_a_confirmed_absent_acceptance_keeps_recovery_required_if_recording_its_answer_raises(harness):
    h = harness(decisions=(propose(0.0),), journal=lambda inner: UnknownCommitRaisingOnAnswer(inner, write=False))
    suspended = suspend(h)
    with pytest.raises(RuntimeError, match="authority answer"):
        h.resume(suspended, h.operator_export(suspended, ACCEPT))
    snapshot = terminal_of(h)
    assert snapshot.run.termination_reason is R.REQUIRED_RECOVERY_NOT_PERFORMED
    assert snapshot.authoritative is not None and snapshot.authoritative.sequence == 0  # the previous reference


@pytest.mark.parametrize("substitute", ["budget", "criteria", "session"])
def test_a_continuation_with_another_request_is_refused_before_any_effect(harness, substitute):
    from dataclasses import replace

    from autonomous_mixing.core.records.observation import MeasuredScalar

    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)
    manifest = h.operator_export(suspended, ACCEPT)
    request, configuration = suspended.request, suspended.request.configuration
    if substitute == "budget":
        changed = replace(request, configuration=replace(configuration, budget_limits=replace(
            configuration.budget_limits, max_daw_writes=9)))  # fmt: skip
    elif substitute == "criteria":
        target = configuration.criteria.targets[0]
        criteria = replace(configuration.criteria, targets=(replace(target, threshold=MeasuredScalar(-20.0, "dBFS")),))
        changed = replace(request, configuration=replace(configuration, criteria=criteria))
    else:
        changed = replace(request, daw_session=replace(SESSION, export_root_key="other-exports"))
    imports = []

    class Watching(Wrapped):
        def import_files(self, request):
            imports.append(request)
            return self.inner.import_files(request)

    h.handoff = Watching(h.handoff)
    sent, appended = len(h.world.sent), len(h.real_journal.snapshot().value.events)
    refused = h.resume(RunSuspended(changed, suspended.trace, suspended.checkpoint_event), manifest)
    assert isinstance(refused, DawContinuationRefused) and refused.code is DawContinuationRefusalCode.REQUEST_MISMATCH
    assert (len(h.world.sent), len(h.real_journal.snapshot().value.events), imports) == (sent, appended, [])
    assert isinstance(h.resume(suspended, manifest), RunReport)  # the recorded request still continues
