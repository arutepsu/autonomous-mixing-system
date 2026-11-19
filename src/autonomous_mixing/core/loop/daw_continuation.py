"""Continuation of a suspended DAW run (coordinator-integration Increment 3 as amended; ADR-037).

``continue_daw`` takes the operator's manifest through these steps:
1. a coherent continuity snapshot;
2. the verified file import;
3. a second snapshot;
4. ``assess_daw_import``;
5. the DAW receipt and candidate state;
6. one candidate analysis;
7. the ATTEMPT verification;
8. DAW acceptance.

The offline acceptance, reconciliation, recovery and promotion functions are not used and stay unchanged. Until
Increment 4, an unaccepted candidate never claims rollback. It ends the run with an UNRESOLVED attempt whose DAW-scope
rollback receipt is UNCERTAIN and says that recovery was not attempted. The fader is not restored, nothing is cleared
in the FL journal, and the final state is UNKNOWN.

**Acceptance authority (amendment A1).**
- **Before the commit call:** the authority is marked unknown, with ``ACCEPTANCE_COMMIT_UNCERTAIN`` pending.
- **A committed or found acceptance** first selects the new reference (``accepted``, ``authority_known``) before any
  promotion or recording. ``verified`` stays ``None`` until promotion completes (committed) or the found state passes
  its integrity inspection. Later failures keep that accepted authority.
- **A confirmed-absent acceptance** is never a restoration.
"""

from collections.abc import Callable
from dataclasses import replace

from autonomous_mixing.core.admission.daw_export import assess_daw_import
from autonomous_mixing.core.loop.acceptance_flow import finish_acceptance
from autonomous_mixing.core.loop.daw_transitions import (
    daw_execution_receipt,
    derive_daw_candidate_state,
    validate_daw_acceptance_event,
)
from autonomous_mixing.core.loop.feedback import add_feedback
from autonomous_mixing.core.loop.observation import analysis_bindings, analyze, evidence_for, inspect
from autonomous_mixing.core.loop.runtime import (
    _invalid,
    _updated_roots,
    _updated_stems,
    checkpoint,
    fresh,
    gate,
    identity_failure,
    observed,
    record,
)
from autonomous_mixing.core.loop.verification_routing import route_verification
from autonomous_mixing.core.ports.acceptance import (
    validate_acceptance_commit_response,
    validate_acceptance_query_response,
)
from autonomous_mixing.core.ports.analysis import AudioAnalyzer
from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource, RunClock
from autonomous_mixing.core.ports.daw_export import DawExportHandoff, validate_daw_files_verified
from autonomous_mixing.core.ports.daw_recovery import DawRecoveryController, validate_daw_continuity_snapshot
from autonomous_mixing.core.ports.integrity import StateIntegrityInspector
from autonomous_mixing.core.ports.run_journal import RunJournal
from autonomous_mixing.core.records.acceptance import (
    AcceptanceAbsent,
    AcceptanceAuthorityUnknown,
    AcceptanceCommitted,
    AcceptanceCommitUnknown,
    AcceptanceEvent,
    AcceptanceFound,
    AcceptanceNotCommitted,
    AcceptanceQuery,
    AcceptedReference,
)
from autonomous_mixing.core.records.actions import ExecutionStatus, RollbackReceipt, RollbackScope, RollbackStatus
from autonomous_mixing.core.records.artifacts import ArtifactKind
from autonomous_mixing.core.records.budgets import BudgetOperation
from autonomous_mixing.core.records.coordinator_progress import (
    AssessedCorrection,
    PreparedCorrection,
    RuntimeProgress,
    Stop,
)
from autonomous_mixing.core.records.daw_correction import (
    DawContinuationFinished,
    DawContinuationRefusalCode,
    DawContinuationRefused,
    DawContinuationStarted,
    DawCorrectionSuspended,
    DawStateQuery,
)
from autonomous_mixing.core.records.daw_export import (
    DawExportManifest,
    DawImportRejected,
    DawImportRejectionCode,
    DawImportRequest,
    DawImportVerified,
)
from autonomous_mixing.core.records.feedback import ActionFeedback, ActionFeedbackDisposition, FeedbackStage
from autonomous_mixing.core.records.identifiers import AcceptanceId, CheckpointId, VerificationResultId
from autonomous_mixing.core.records.integrity import ArtifactRootBinding, IntegrityStatus
from autonomous_mixing.core.records.results import PortFailure, Success
from autonomous_mixing.core.records.run import AttemptDisposition, CorrectionAttempt
from autonomous_mixing.core.records.run_trace import OperationKind, TerminalSnapshot
from autonomous_mixing.core.records.termination import TerminationReason
from autonomous_mixing.core.records.verification import AssessmentMode
from autonomous_mixing.core.records.verification_assessment import VerificationAssessment
from autonomous_mixing.core.records.verification_routing import VerificationRoute
from autonomous_mixing.core.verification.assessment import assess_verification

_CONTINUITY_CODES = frozenset({DawImportRejectionCode.STATE_DISCONTINUITY, DawImportRejectionCode.UNRESOLVED_COMMAND,
                               DawImportRejectionCode.CONVERSION_BLOCKED})  # fmt: skip
_RECOVERY = TerminationReason.REQUIRED_RECOVERY_NOT_PERFORMED
_NOT_ATTEMPTED = "recovery not attempted: the fader is not restored and the selected state is not re-established"


def continue_daw(
    progress: RuntimeProgress,
    suspended: DawCorrectionSuspended,
    manifest: DawExportManifest,
    *,
    analyzer: AudioAnalyzer,
    clock: RunClock,
    identities: IdentitySource,
    integrity_inspector: StateIntegrityInspector,
    journal: RunJournal,
    recovery_controller: DawRecoveryController,
    export_handoff: DawExportHandoff,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop | None]:
    """None: the candidate was accepted and another decision round follows. A stop ends the run."""
    ctx = PreparedCorrection(suspended.call_id, suspended.before, suspended.decision, suspended.attempt_id,
                             suspended.plan)  # fmt: skip
    awaiting, source = suspended.awaiting, suspended.awaiting.request.anchor.source_id
    env = dict(clock=clock, identities=identities, journal=journal, on_progress=on_progress)
    rec = dict(identities=identities, journal=journal, on_progress=on_progress)
    ids = dict(call=ctx.call_id, attempt=ctx.attempt_id, execution=ctx.execution_id, **env)

    def ended(progress, reason, explanation):
        """Before any receipt: no candidate exists; possibly away from the selected state, nothing claimed."""
        outcome = ActionFeedback(ctx.action, ctx.attempt_id, ctx.execution_id, ActionFeedbackDisposition.UNRESOLVED,
                                 FeedbackStage.RENDER, ExecutionStatus.UNCERTAIN, None, None, reason.value,
                                 f"{explanation}; {_NOT_ATTEMPTED}", ())  # fmt: skip
        progress, _ = add_feedback(progress, ctx.call_id, ctx.before, outcome, decision_id=ctx.decision.decision_id,
                                   **rec)  # fmt: skip
        return (progress, (reason, f"{explanation}; {_NOT_ATTEMPTED}"))

    def snapshot(progress, when):
        query = DawStateQuery(source, True)
        for attempt in (1, 2):  # one retry of a retryable capture failure
            progress, (intent_id, _, stop, _) = gate(progress, OperationKind.DAW_STATE, lambda c: query, None, **ids)
            if stop:
                return progress, None, f"the {when} continuity snapshot was not taken ({stop[0].value}: {stop[1]})"
            result = recovery_controller.continuity_snapshot(source)
            try:
                validate_daw_continuity_snapshot(source, result)
                outcome = result.value if isinstance(result, Success) else result
            except ValueError as error:
                result, outcome = None, _invalid(result, error)
            progress, stop = observed(progress, intent_id, OperationKind.DAW_STATE, outcome, clock=clock, **rec)
            if isinstance(result, Success) and not stop:
                return progress, result.value, None
            if stop or not (isinstance(result, PortFailure) and result.retryable and attempt == 1):
                return progress, None, f"the {when} continuity snapshot failed"
        return progress, None, f"the {when} continuity snapshot failed"

    progress, before_import, problem = snapshot(progress, "before-import")
    if before_import is None:
        return ended(progress, TerminationReason.DAW_CONTINUITY_FAILED, problem)
    import_request = DawImportRequest(awaiting, manifest)
    progress, (intent_id, _, stop, _) = gate(progress, OperationKind.EXPORT_IMPORT, lambda c: import_request, None,
                                             **ids)  # fmt: skip
    if stop:
        return ended(progress, _RECOVERY, f"the export import was not dispatched ({stop[0].value}: {stop[1]})")
    files = export_handoff.import_files(import_request)
    try:
        validate_daw_files_verified(import_request, files)
        outcome = files
    except ValueError as error:
        files, outcome = None, _invalid(files, error)
    progress, stop = observed(progress, intent_id, OperationKind.EXPORT_IMPORT, outcome, clock=clock, **rec)
    if files is None:
        return ended(progress, TerminationReason.EXPORT_IMPORT_REJECTED, "the export import response was invalid")
    if stop:
        return ended(progress, _RECOVERY, f"the export import could not be recorded ({stop[0].value})")
    progress, after_import, problem = snapshot(progress, "after-import")
    if after_import is None:
        return ended(progress, TerminationReason.DAW_CONTINUITY_FAILED, problem)
    assessed = assess_daw_import(awaiting, files, before_import=before_import, after_import=after_import)
    progress, stop = record(progress, assessed, **rec)
    if isinstance(assessed, DawImportRejected):
        reason = (TerminationReason.DAW_CONTINUITY_FAILED if assessed.code in _CONTINUITY_CODES
                  else TerminationReason.EXPORT_IMPORT_REJECTED)  # fmt: skip
        return ended(
            progress, reason, f"the export import was rejected ({assessed.code.value}: {assessed.explanation})"
        )
    if stop:
        return ended(progress, _RECOVERY, f"the import assessment could not be recorded ({stop[0].value})")
    return _assess_candidate(progress, ctx, suspended, assessed, analyzer=analyzer,
                             integrity_inspector=integrity_inspector, **env)  # fmt: skip


def _assess_candidate(progress, ctx, suspended, verified: DawImportVerified, *, analyzer, integrity_inspector,
                      clock, identities, journal, on_progress):  # fmt: skip
    """The DAW receipt and candidate, one analysis, the ATTEMPT verification and its route (no offline recovery)."""
    env = dict(clock=clock, identities=identities, journal=journal, on_progress=on_progress)
    rec = dict(identities=identities, journal=journal, on_progress=on_progress)
    before, apply, session = ctx.before, suspended.apply, progress.request.daw_session
    progress, checkpoint_id = fresh(progress, CheckpointId, identities=identities, on_progress=on_progress)
    if checkpoint_id is None:
        return _unaccepted(progress, ctx, None, None, None, identity_failure("checkpoint ID")[1],
                           FeedbackStage.RECORDING, **rec)  # fmt: skip
    try:
        receipt = daw_execution_receipt(before, verified, apply)
        candidate = derive_daw_candidate_state(before, verified, apply, checkpoint_id, progress.last.utc)
    except ValueError as error:
        return _unaccepted(progress, ctx, None, None, None, f"the verified export does not form a candidate ({error})",
                           FeedbackStage.RENDER, **rec)  # fmt: skip
    progress, stop = record(progress, receipt, **rec)
    if stop:
        return _unaccepted(progress, ctx, receipt, candidate, None, "the receipt could not be recorded",
                           FeedbackStage.RECORDING, **rec)  # fmt: skip
    produced = tuple(ArtifactRootBinding(a.artifact_id, session.export_root_key) for a in receipt.candidate_artifacts)
    progress, (observations, stop) = analyze(
        progress, candidate, TerminationReason.CANDIDATE_ANALYSIS_FAILED,
        bindings=analysis_bindings(candidate, progress.roots, produced), analyzer=analyzer, **env,
    )  # fmt: skip
    if observations is None or stop:
        cause = f"the candidate analysis failed ({stop[1] if stop else 'no observations'})"
        return _unaccepted(progress, ctx, receipt, candidate, None, cause, FeedbackStage.VERIFICATION, **rec)
    progress, result_id = fresh(progress, VerificationResultId, identities=identities, on_progress=on_progress)
    if result_id is None:
        return _unaccepted(progress, ctx, receipt, candidate, None, "no verification ID", FeedbackStage.VERIFICATION,
                           **rec)  # fmt: skip
    bundles = (evidence_for(progress, candidate), evidence_for(progress, before),
               evidence_for(progress, progress.request.initial_state))  # fmt: skip
    try:
        assessment = VerificationAssessment(result_id, AssessmentMode.ATTEMPT, progress.request.configuration.criteria,
                                            progress.request.measurements, *bundles)  # fmt: skip
    except ValueError as error:
        return _unaccepted(progress, ctx, receipt, candidate, None, f"candidate evidence is inconsistent ({error})",
                           FeedbackStage.VERIFICATION, **rec)  # fmt: skip
    verification = assess_verification(assessment)
    progress, stop = record(progress, assessment, **rec)
    if not stop:
        progress, stop = record(progress, verification, **rec)
    if stop:
        return _unaccepted(progress, ctx, receipt, candidate, verification, "the verification could not be recorded",
                           FeedbackStage.RECORDING, **rec)  # fmt: skip
    actx = AssessedCorrection(ctx.call_id, before, ctx.decision, ctx.attempt_id, ctx.plan, receipt, candidate,
                              assessment, verification)  # fmt: skip
    route = route_verification(verification, progress.request.configuration.criteria)
    match route:
        case VerificationRoute.COMMIT_THEN_FINISH | VerificationRoute.COMMIT_THEN_ITERATE:
            return accept_daw(progress, actx, route, verified, apply, integrity_inspector=integrity_inspector, **env)
        case VerificationRoute.RESTORE_THEN_CONSIDER_ITERATION:
            return _unaccepted(progress, ctx, receipt, candidate, verification, "the candidate was rejected by "
                               "verification", FeedbackStage.VERIFICATION, **rec)  # fmt: skip
        case VerificationRoute.RECONCILE_THEN_FINISH_INCONCLUSIVE:
            return _unaccepted(progress, ctx, receipt, candidate, verification, "the candidate verification is "
                               "inconclusive", FeedbackStage.VERIFICATION, **rec)  # fmt: skip
        case _:
            raise ValueError(f"an ATTEMPT verification cannot take route {route.name}")


def _unaccepted(progress, ctx, receipt, candidate, verification, cause, stage, *, identities, journal, on_progress):
    """The interim handler for an unaccepted DAW candidate: recovery not attempted, nothing restored or claimed."""
    rec = dict(identities=identities, journal=journal, on_progress=on_progress)
    explanation = f"{cause}; {_NOT_ATTEMPTED} (recovery routing is Increment 4)"
    progress = replace(progress, verified=None, pending_effect=_RECOVERY)
    on_progress(progress)
    if receipt is None:
        outcome = ActionFeedback(ctx.action, ctx.attempt_id, ctx.execution_id, ActionFeedbackDisposition.UNRESOLVED,
                                 FeedbackStage.RENDER, ExecutionStatus.UNCERTAIN, None, None, _RECOVERY.value,
                                 explanation, ())  # fmt: skip
    else:
        rollback = RollbackReceipt(ctx.execution_id, ctx.before.audio.checkpoint_id, RollbackStatus.UNCERTAIN, None,
                                   explanation, RollbackScope.SELECTED_STATE_WITH_FADER, RollbackStatus.UNCERTAIN, ())  # fmt: skip
        attempt = CorrectionAttempt(ctx.attempt_id, progress.request.run_id, ctx.decision.decision_id,
                                    ctx.action.action_id, ctx.before, receipt, candidate, verification, rollback,
                                    AttemptDisposition.UNRESOLVED)  # fmt: skip
        progress, _ = record(progress, attempt, **rec)
        unresolved_stage = stage if stage in (FeedbackStage.VERIFICATION, FeedbackStage.ACCEPTANCE) else (
            FeedbackStage.RECOVERY)  # fmt: skip
        outcome = ActionFeedback(ctx.action, ctx.attempt_id, ctx.execution_id, ActionFeedbackDisposition.UNRESOLVED,
                                 unresolved_stage, ExecutionStatus.CANDIDATE_READY, None, RollbackStatus.UNCERTAIN,
                                 _RECOVERY.value, explanation, ())  # fmt: skip
    progress, _ = add_feedback(progress, ctx.call_id, ctx.before, outcome, decision_id=ctx.decision.decision_id,
                               candidate=None if receipt is None else ctx.candidate_revision, selected=None,
                               **rec)  # fmt: skip
    return (progress, (_RECOVERY, explanation))


def promote_daw(progress: RuntimeProgress, reference: AcceptedReference, actx: AssessedCorrection, *,
                on_progress: Callable[[RuntimeProgress], None]) -> RuntimeProgress:  # fmt: skip
    """Move the stem, mix and root selection onto an already-selected DAW reference, then mark it verified.

    Every exported stem replaces its track's binding, the exported master becomes the selected mix, and every candidate
    artifact is bound to the session's export root (amendment C7). ``accepted`` was set before this call.
    """
    session = progress.request.daw_session
    for artifact in actx.receipt.candidate_artifacts:
        if artifact.kind is ArtifactKind.STEM:
            progress = replace(progress, stems=_updated_stems(progress.stems, artifact.source_track_ids[0],
                                                              artifact.artifact_id))  # fmt: skip
        elif artifact.kind is ArtifactKind.MIX:
            progress = replace(progress, mix_id=artifact.artifact_id)
        progress = replace(progress, roots=_updated_roots(progress.roots, artifact.artifact_id,
                                                          session.export_root_key))  # fmt: skip
        on_progress(progress)
    progress = replace(progress, verified=reference, restored_last=False)
    on_progress(progress)
    return progress


def _established(progress, reference, *, on_progress, committed=None):
    """Acceptance established: select the new reference first, before any fallible work (approval constraint)."""
    commits = progress.commits
    if committed is not None and not any(c.event == committed.event for c in commits):
        commits = commits + (committed,)
    progress = replace(progress, accepted=reference, authority_known=True, verified=None, commits=commits,
                       pending_effect=None)  # fmt: skip
    on_progress(progress)
    return progress


def accept_daw(progress, actx: AssessedCorrection, route, verified: DawImportVerified, apply, *, clock, identities,
               integrity_inspector, journal, on_progress):  # fmt: skip
    """DAW acceptance (amendment A1): no offline recovery on any branch."""
    rec = dict(identities=identities, journal=journal, on_progress=on_progress)
    unaccepted = dict(stage=FeedbackStage.ACCEPTANCE, **rec)
    args = (actx, actx.receipt, actx.candidate_state, actx.verification)
    progress, stop = checkpoint(progress, clock=clock, on_progress=on_progress)
    limit = progress.request.configuration.budget_limits.max_elapsed_seconds
    if stop or progress.last.cancelled or progress.usage.elapsed_seconds >= limit:
        cause = stop[1] if stop else "cancelled or out of time before the acceptance commit"
        return _unaccepted(progress, *args, cause, **unaccepted)
    progress, acceptance_id = fresh(progress, AcceptanceId, identities=identities, on_progress=on_progress)
    if acceptance_id is None:
        return _unaccepted(progress, *args, "no acceptance ID", **unaccepted)
    event = AcceptanceEvent(acceptance_id, progress.request.run_id, actx.attempt_id, actx.decision.decision_id,
                            progress.accepted, actx.action, actx.receipt, actx.assessment, actx.verification)  # fmt: skip
    validate_daw_acceptance_event(event, verified, apply)
    progress, stop = record(progress, event, **rec)
    if stop:
        return _unaccepted(progress, *args, "the acceptance event could not be recorded", **unaccepted)
    progress, stop = checkpoint(progress, clock=clock, on_progress=on_progress)
    if stop or progress.last.cancelled or progress.usage.elapsed_seconds >= limit:
        return _unaccepted(progress, *args, "cancelled or out of time after recording the acceptance event; not "
                           "committed", **unaccepted)  # fmt: skip
    # Immediately before the commit call: the authority is unknown until a definitive answer (amendment A1).
    progress = replace(progress, authority_known=False, verified=None,
                       pending_effect=TerminationReason.ACCEPTANCE_COMMIT_UNCERTAIN)  # fmt: skip
    on_progress(progress)
    response = journal.commit(event)
    try:
        validate_acceptance_commit_response(event, response)
    except ValueError as error:
        response = AcceptanceCommitUnknown(event.acceptance_id, f"invalid commit response: {error}")
    if isinstance(response, AcceptanceCommitted):
        progress = _established(progress, response.accepted, committed=response, on_progress=on_progress)
        progress = promote_daw(progress, response.accepted, actx, on_progress=on_progress)
        progress, stop = record(progress, response, **rec)
        if stop:
            return (progress, stop)
        return finish_acceptance(progress, actx, route, **rec)
    if isinstance(response, AcceptanceNotCommitted):
        progress = replace(progress, authority_known=True, pending_effect=_RECOVERY)
        on_progress(progress)
        progress, _ = record(progress, response, **rec)
        return _unaccepted(progress, *args, f"the acceptance was not committed ({response.explanation})", **unaccepted)
    progress, _ = record(progress, response, **rec)
    progress, result = reconcile_daw_acceptance(progress, actx, event, clock=clock, identities=identities,
                                                integrity_inspector=integrity_inspector, journal=journal,
                                                on_progress=on_progress)  # fmt: skip
    if result == "accepted":
        return finish_acceptance(progress, actx, route, **rec)
    if result == "absent":
        return _unaccepted(progress, *args, "the acceptance was confirmed absent after a lost reply", **unaccepted)
    return (progress, result)


def reconcile_daw_acceptance(progress, actx, event, *, clock, identities, integrity_inspector, journal, on_progress):
    """One authority query after an unknown commit (amendment A1).

    Returns ``"accepted"`` (found, intact and promoted), ``"absent"`` (confirmed not committed; never a restoration),
    or a stop: unknown authority, or the found acceptance's integrity failed or could not be established.
    """
    rec = dict(identities=identities, journal=journal, on_progress=on_progress)
    uncertain = TerminationReason.ACCEPTANCE_COMMIT_UNCERTAIN
    query = AcceptanceQuery(progress.request.run_id, event.acceptance_id)
    progress, (intent_id, _, stop, _) = gate(progress, OperationKind.QUERY_ACCEPTANCE, lambda c: query,
                                             BudgetOperation.RECOVERY_OPERATION, clock=clock, **rec)  # fmt: skip
    if intent_id is None or stop:
        return (progress, (uncertain, "the acceptance authority could not be queried after an unknown commit"))
    answer = journal.query(query)
    try:
        validate_acceptance_query_response(query, answer)
        outcome = answer
    except ValueError as error:
        outcome = _invalid(answer, error)
        answer = AcceptanceAuthorityUnknown(progress.request.run_id, event.acceptance_id, f"invalid answer: {error}")
    # Validate the answer against the submitted event and establish what it says BEFORE recording it (review fix
    # 2026-11-19): an exception while recording a found answer must not leave the authority reported as unknown.
    if isinstance(answer, AcceptanceFound):
        if answer.event != event:
            raise ValueError("the acceptance authority answered for another event")
        progress = _established(progress, answer.accepted, committed=AcceptanceCommitted(answer.event,
                                                                                          answer.accepted),
                                on_progress=on_progress)  # fmt: skip
    elif isinstance(answer, AcceptanceAbsent):
        progress = replace(progress, authority_known=True, pending_effect=_RECOVERY)
        on_progress(progress)
    progress, query_stop = observed(progress, intent_id, OperationKind.QUERY_ACCEPTANCE, outcome, clock=clock, **rec)
    if isinstance(answer, AcceptanceAuthorityUnknown):
        return (progress, (uncertain, f"acceptance authority unknown: {answer.explanation}"))
    if isinstance(answer, AcceptanceAbsent):
        return (progress, "absent")
    if query_stop:
        return (progress, (TerminationReason.ACCEPTED_STATE_INTEGRITY_UNCERTAIN,
                           f"the found acceptance's integrity was not inspected ({query_stop[0].value})"))  # fmt: skip
    # Locate the found state's exports for its integrity inspection: root bindings are location facts, not authority.
    export_root = progress.request.daw_session.export_root_key
    for artifact in actx.receipt.candidate_artifacts:
        progress = replace(progress, roots=_updated_roots(progress.roots, artifact.artifact_id, export_root))
    on_progress(progress)
    progress, (status, report, _, inspect_stop, performed) = inspect(
        progress, answer.accepted.state, None, clock=clock, integrity_inspector=integrity_inspector, **rec
    )  # fmt: skip
    if not performed or report is None or status is IntegrityStatus.UNCERTAIN:
        return (progress, (TerminationReason.ACCEPTED_STATE_INTEGRITY_UNCERTAIN, "the found acceptance's integrity "
                           "could not be established"))  # fmt: skip
    if status is IntegrityStatus.FAILED:
        return (progress, (TerminationReason.ACCEPTED_STATE_INTEGRITY_FAILED, "the found acceptance failed integrity"))
    progress = promote_daw(progress, answer.accepted, actx, on_progress=on_progress)
    if inspect_stop:
        return (progress, inspect_stop)
    return (progress, "accepted")


def admit_continuation(snapshot, request, checkpoint_event, manifest: DawExportManifest):
    """Decide from the journal's trace whether this continuation may run (amendment A2); None means it may.

    The supplied ``request`` must equal the run's recorded ``RunRequest`` (its first trace event) exactly, so a
    continuation cannot run under other budgets, criteria or DAW-session settings (review fix 2026-11-19).

    Completion evidence for a started continuation is its ``DawContinuationFinished``, a terminal snapshot, or a
    later ``DawCorrectionSuspended`` after its start. Callers hold the run's exclusive ownership while deciding.
    """
    checkpoint = checkpoint_event.payload
    export_id = checkpoint.awaiting.request.export_id
    run_id = snapshot.run_id

    def refused(code, explanation, evidence=None):
        return DawContinuationRefused(run_id, export_id, code, explanation, evidence)

    events = snapshot.events
    position = next((i for i, e in enumerate(events) if e == checkpoint_event), None)
    if position is None:
        return refused(DawContinuationRefusalCode.NOT_SUSPENDED, "the suspension checkpoint is not in this run's trace")
    if events[0].payload != request:
        return refused(DawContinuationRefusalCode.REQUEST_MISMATCH,
                       "the supplied request differs from the run's recorded request")  # fmt: skip
    if manifest.export_id != export_id:
        return refused(DawContinuationRefusalCode.MANIFEST_CONFLICT, "the manifest answers another export")
    later = events[position + 1 :]
    start = next((e for e in later if isinstance(e.payload, DawContinuationStarted)
                  and e.payload.checkpoint_event_id == checkpoint_event.event_id), None)  # fmt: skip
    if start is None:
        if later:
            return refused(DawContinuationRefusalCode.NOT_SUSPENDED, "the run moved on without this continuation")
        return None
    if start.payload.manifest != manifest:
        return refused(DawContinuationRefusalCode.MANIFEST_CONFLICT, "this export was continued with another manifest",
                       start.event_id)  # fmt: skip
    after = events[events.index(start) + 1 :]
    evidence = next((e for e in after
                     if (isinstance(e.payload, DawContinuationFinished) and e.payload.started_event_id == start.event_id)
                     or isinstance(e.payload, (TerminalSnapshot, DawCorrectionSuspended))), None)  # fmt: skip
    if evidence is not None:
        return refused(DawContinuationRefusalCode.ALREADY_CONTINUED, "this export was already continued",
                       evidence.event_id)  # fmt: skip
    return refused(DawContinuationRefusalCode.CONTINUATION_INTERRUPTED, "an earlier continuation of this export "
                   "stopped without completion evidence", start.event_id)  # fmt: skip
