"""Coordinator responsibilities of the controlled serial coordinator.

``run`` is the offline path (unchanged). ``run_daw`` (coordinator-integration Increment 2, ADR-036) is the
operator-assisted DAW path: the same observation, decision and admission stages, then ``execute_daw`` in place of
rendering. It returns ``RunSuspended`` when a confirmed fader write awaits the operator's export.
"""

from collections.abc import Callable
from dataclasses import replace
from functools import partial

from autonomous_mixing.core.loop.completion import finalize
from autonomous_mixing.core.loop.correction import render
from autonomous_mixing.core.loop.daw_continuation import admit_continuation, continue_daw
from autonomous_mixing.core.loop.daw_execution import daw_start, execute_daw
from autonomous_mixing.core.loop.decision_round import decision_round
from autonomous_mixing.core.loop.observation import (
    _missing_baselines,
    analysis_bindings,
    analyze,
    evidence_for,
    inspect,
)
from autonomous_mixing.core.loop.runtime import fresh, identity_failure, record
from autonomous_mixing.core.loop.verification_routing import route_verification
from autonomous_mixing.core.ports.analysis import AudioAnalyzer
from autonomous_mixing.core.ports.coordinator_runtime import DiagnosisSource, IdentitySource, RunClock, RunRenderer
from autonomous_mixing.core.ports.daw import DawParameterController
from autonomous_mixing.core.ports.daw_export import DawExportHandoff
from autonomous_mixing.core.ports.daw_recovery import DawRecoveryController
from autonomous_mixing.core.ports.decision import DecisionMaker
from autonomous_mixing.core.ports.integrity import StateIntegrityInspector
from autonomous_mixing.core.ports.run_journal import (
    RunJournal,
    require_snapshot_durability,
    validate_run_journal_snapshot,
)
from autonomous_mixing.core.records.acceptance import AcceptedReference
from autonomous_mixing.core.records.budgets import BudgetUsage
from autonomous_mixing.core.records.coordinator_progress import PreparedCorrection, RuntimeProgress, Stop
from autonomous_mixing.core.records.coordinator_runtime import ClockReading, RunRequest
from autonomous_mixing.core.records.daw_correction import (
    DawContinuationDisposition,
    DawContinuationFinished,
    DawContinuationRefusalCode,
    DawContinuationRefused,
    DawContinuationStarted,
)
from autonomous_mixing.core.records.daw_export import DawExportManifest
from autonomous_mixing.core.records.identifiers import VerificationResultId
from autonomous_mixing.core.records.integrity import IntegrityStatus
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.results import Success
from autonomous_mixing.core.records.run_report import RunReport, RunSuspended
from autonomous_mixing.core.records.run_trace import RunJournalSnapshot, RunTraceEvent
from autonomous_mixing.core.records.termination import TerminationReason
from autonomous_mixing.core.records.verification import AssessmentMode
from autonomous_mixing.core.records.verification_assessment import VerificationAssessment
from autonomous_mixing.core.records.verification_routing import VerificationRoute
from autonomous_mixing.core.verification.assessment import assess_verification


def ordinary(
    progress: RuntimeProgress,
    *,
    analyzer: AudioAnalyzer,
    clock: RunClock,
    decision_maker: DecisionMaker,
    diagnosis_source: DiagnosisSource,
    identities: IdentitySource,
    integrity_inspector: StateIntegrityInspector,
    journal: RunJournal,
    execute: Callable,
    on_progress: Callable[[RuntimeProgress], None],
    start_check: Callable | None = None,
) -> tuple[RuntimeProgress, Stop | RunTraceEvent]:
    """``execute(progress, prepared, on_progress=...)`` runs one prepared correction: the offline render or the DAW write.

    ``start_check(progress, on_progress=...)`` (the DAW path, ADR-039) runs right after the request is recorded and
    before anything else; a stop ends the run before any effect.
    """
    progress, stop = record(progress, progress.request, identities=identities, journal=journal, on_progress=on_progress)
    if stop:
        return (progress, stop)
    if start_check is not None:
        progress, stop = start_check(progress, on_progress=on_progress)
        if stop:
            return (progress, stop)
    progress, (status, _, _, stop, performed) = inspect(
        progress,
        progress.request.initial_state,
        None,
        clock=clock,
        identities=identities,
        integrity_inspector=integrity_inspector,
        journal=journal,
        on_progress=on_progress,
    )
    if not performed:
        return (progress, stop)
    if status is IntegrityStatus.VERIFIED:
        progress = replace(progress, verified=progress.initial)
        on_progress(progress)
    if stop:
        return (progress, stop)
    if status is not IntegrityStatus.VERIFIED:
        if status is IntegrityStatus.FAILED:
            return (
                progress,
                (TerminationReason.ACCEPTED_STATE_INTEGRITY_FAILED, "integrity of the initial state failed"),
            )
        return (
            progress,
            (TerminationReason.ACCEPTED_STATE_INTEGRITY_UNCERTAIN, "integrity of the initial state is uncertain"),
        )
    progress, (initial_observations, stop) = analyze(
        progress,
        progress.request.initial_state,
        TerminationReason.INITIAL_ANALYSIS_FAILED,
        bindings=analysis_bindings(progress.request.initial_state, progress.roots),
        analyzer=analyzer,
        clock=clock,
        identities=identities,
        journal=journal,
        on_progress=on_progress,
    )
    if stop:
        return (progress, stop)
    progress, result_id = fresh(progress, VerificationResultId, identities=identities, on_progress=on_progress)
    if result_id is None:
        return (progress, identity_failure("verification ID"))
    initial_evidence = evidence_for(progress, progress.request.initial_state)
    assessment = VerificationAssessment(
        result_id,
        AssessmentMode.INITIAL,
        progress.request.configuration.criteria,
        progress.request.measurements,
        initial_evidence,
        None,
        None,
    )
    verification = assess_verification(assessment)
    progress, stop = record(progress, assessment, identities=identities, journal=journal, on_progress=on_progress)
    if not stop:
        progress, stop = record(progress, verification, identities=identities, journal=journal, on_progress=on_progress)
    if stop:
        return (progress, stop)
    route = route_verification(verification, progress.request.configuration.criteria)
    match route:
        case VerificationRoute.FINISH_INITIAL_SUCCESS:
            return (progress, (TerminationReason.INITIAL_CRITERIA_MET, "the initial state satisfies every target"))
        case VerificationRoute.FINISH_INITIAL_INCONCLUSIVE:
            return (
                progress,
                (TerminationReason.INITIAL_ASSESSMENT_INCONCLUSIVE, "the initial assessment is inconclusive"),
            )
        case VerificationRoute.CHECK_BASELINE_READINESS:
            missing = _missing_baselines(
                progress.request.configuration.criteria,
                {m.condition_id: m.definition for m in progress.request.measurements},
                initial_evidence,
            )
            if missing:
                return (
                    progress,
                    (
                        TerminationReason.REQUIRED_BASELINE_UNAVAILABLE,
                        f"comparison baselines are unavailable for: {', '.join(missing)}",
                    ),
                )
        case _:
            raise ValueError(f"an INITIAL verification cannot take route {route.name}")
    return _rounds(progress, clock=clock, decision_maker=decision_maker, diagnosis_source=diagnosis_source,
                   identities=identities, journal=journal, execute=execute, on_progress=on_progress)  # fmt: skip


def _rounds(
    progress: RuntimeProgress,
    *,
    clock: RunClock,
    decision_maker: DecisionMaker,
    diagnosis_source: DiagnosisSource,
    identities: IdentitySource,
    journal: RunJournal,
    execute: Callable,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop | RunTraceEvent]:
    """Decision rounds until a stop or a suspension; shared by a fresh run and a continuation (Increment 3)."""
    while True:
        progress, result = decision_round(
            progress,
            clock=clock,
            decision_maker=decision_maker,
            diagnosis_source=diagnosis_source,
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
        if isinstance(result, PreparedCorrection):
            progress, result = execute(progress, result, on_progress=on_progress)
        if result:
            return (progress, result)


def run(
    request: RunRequest,
    *,
    analyzer: AudioAnalyzer,
    diagnosis_source: DiagnosisSource,
    decision_maker: DecisionMaker,
    renderer: RunRenderer,
    integrity_inspector: StateIntegrityInspector,
    journal: RunJournal,
    clock: RunClock,
    identities: IdentitySource,
) -> RunReport:
    """Execute one controlled run and return its immutable NON_DURABLE report.

    The journal must be bound to ``request.run_id`` and the request's initial state at acceptance
    sequence zero, with an empty trace and acceptance history; this API does not resume runs. A journal
    that cannot confirm that, or a clock whose first reading is invalid, is a caller contract error
    (``ValueError``) raised before any operation. Unexpected collaborator or programming exceptions
    propagate after one best-effort terminal recording. A request with a DAW session is refused: it uses
    ``run_daw`` (ADR-036).
    """
    require_instance(request, RunRequest, "request")
    if request.daw_session is not None:
        raise ValueError("run() is the offline path; a request with a DAW session uses run_daw()")

    execute = partial(render, analyzer=analyzer, clock=clock, identities=identities,
                      integrity_inspector=integrity_inspector, journal=journal, renderer=renderer)  # fmt: skip

    return _run(request, analyzer=analyzer, diagnosis_source=diagnosis_source, decision_maker=decision_maker,
                integrity_inspector=integrity_inspector, journal=journal, clock=clock, identities=identities,
                execute=execute)  # fmt: skip


def run_daw(
    request: RunRequest,
    *,
    analyzer: AudioAnalyzer,
    diagnosis_source: DiagnosisSource,
    decision_maker: DecisionMaker,
    integrity_inspector: StateIntegrityInspector,
    journal: RunJournal,
    clock: RunClock,
    identities: IdentitySource,
    daw_controller: DawParameterController,
    recovery_controller: DawRecoveryController,
    export_handoff: DawExportHandoff,
) -> RunReport | RunSuspended:
    """One operator-assisted DAW run (ADR-034, ADR-036), up to its first confirmed write's export suspension.

    The same preconditions as ``run``, and the request must carry a DAW session. A confirmed fader write produces no
    receipt, analysis or acceptance: the run returns ``RunSuspended`` with its checkpoint as the trace's last event.
    Continuation is a separate, later increment. Off-target, uncertain and post-write failures terminate at once
    without recovery (interim policy until Increment 4), leaving the FL journal's blocks and the final state UNKNOWN.
    """
    require_instance(request, RunRequest, "request")
    if request.daw_session is None:
        raise ValueError("run_daw() needs a request with a DAW session")

    execute = partial(execute_daw, clock=clock, identities=identities, journal=journal, daw_controller=daw_controller,
                      recovery_controller=recovery_controller, export_handoff=export_handoff,
                      integrity_inspector=integrity_inspector)  # fmt: skip

    start_check = partial(daw_start, clock=clock, identities=identities, journal=journal,
                          recovery_controller=recovery_controller)  # fmt: skip
    return _run(request, analyzer=analyzer, diagnosis_source=diagnosis_source, decision_maker=decision_maker,
                integrity_inspector=integrity_inspector, journal=journal, clock=clock, identities=identities,
                execute=execute, start_check=start_check)  # fmt: skip


def _run(
    request: RunRequest,
    *,
    analyzer: AudioAnalyzer,
    diagnosis_source: DiagnosisSource,
    decision_maker: DecisionMaker,
    integrity_inspector: StateIntegrityInspector,
    journal: RunJournal,
    clock: RunClock,
    identities: IdentitySource,
    execute: Callable,
    start_check: Callable | None = None,
) -> RunReport | RunSuspended:
    initial = AcceptedReference(request.initial_state, 0)
    opened = journal.snapshot()
    if not isinstance(opened, Success) or not isinstance(opened.value, RunJournalSnapshot):
        raise ValueError("the journal could not confirm its initialization for this run")
    validate_run_journal_snapshot(request.run_id, initial, opened.value)
    require_snapshot_durability(journal, opened.value)
    if opened.value.events or opened.value.acceptances:
        raise ValueError("the journal already holds a trace or acceptances; this API does not resume a run")
    first = clock.read()
    if not isinstance(first, ClockReading):
        raise ValueError("the run clock's first reading is not a ClockReading")
    progress = RuntimeProgress(
        request=request,
        initial=initial,
        first=first,
        last=first,
        usage=BudgetUsage(0, 0, 0, 0, 0.0),
        known=(),
        unacknowledged=(),
        commits=(),
        recording_ok=True,
        accepted=initial,
        authority_known=True,
        verified=None,
        restored_last=False,
        tokens=frozenset(),
        history=(),
        stems=request.stem_bindings,
        mix_id=request.mix_artifact_id,
        roots=request.root_bindings,
        observations=(),
        diagnosed=None,
    )
    step = partial(ordinary, analyzer=analyzer, clock=clock, decision_maker=decision_maker,
                   diagnosis_source=diagnosis_source, identities=identities, integrity_inspector=integrity_inspector,
                   journal=journal, execute=execute, start_check=start_check)  # fmt: skip
    return _complete(progress, step, clock=clock, identities=identities, journal=journal)


def _complete(
    progress: RuntimeProgress, step: Callable, *, clock: RunClock, identities: IdentitySource, journal: RunJournal
) -> RunReport | RunSuspended:
    """Run ``step(progress, on_progress=...)`` and finish it: a suspension, a terminal report, or, for an unexpected
    exception, one best-effort phase-aware terminal recording before the original exception propagates."""
    latest = progress

    def remember(progress: RuntimeProgress) -> None:
        """Exception progress observer: keeps the latest immutable progress for unexpected-error finalization.

        Helpers call it after every replacement; normal control flow uses the progress they return instead.
        """
        nonlocal latest
        latest = progress

    try:
        progress, stop = step(progress, on_progress=remember)
    except Exception as error:
        # Keep unresolved effects (DAW writes or acceptance dispatch) through unexpected errors.
        # The original exception propagates; no speculative retry or recovery is attempted here.
        reason, explanation = TerminationReason.RUNTIME_CONTRACT_FAILED, "an unexpected error interrupted the run"
        if latest.pending_effect is not None:
            reason = latest.pending_effect
            if latest.request.daw_session is None and reason is TerminationReason.ACCEPTANCE_COMMIT_UNCERTAIN:
                explanation = (
                    f"an unexpected error ({type(error).__name__}: {error}) interrupted the run while acceptance "
                    "authority is unknown; the commit was not retried and no rollback is claimed"
                )
            else:
                explanation = (
                    f"an unexpected error ({type(error).__name__}: {error}) interrupted the run after a DAW write may "
                    "have taken effect; the fader is not restored and no journal state is cleared"
                )
        try:
            finalize(
                latest,
                reason,
                explanation,
                clock=clock,
                identities=identities,
                journal=journal,
                on_progress=remember,
            )
        except Exception:
            pass
        raise
    if isinstance(stop, RunTraceEvent):
        return _suspended(progress, stop, journal)
    progress, report = finalize(
        progress, *stop, clock=clock, identities=identities, journal=journal, on_progress=remember
    )
    return report


def _suspended(progress: RuntimeProgress, checkpoint: RunTraceEvent, journal: RunJournal) -> RunSuspended:
    """The suspended run: the journal's acknowledged prefix ending with the checkpoint, else the locally known one."""
    snapshot = RunJournalSnapshot(progress.request.run_id, progress.initial, tuple(progress.known),
                                  tuple(progress.commits), journal.store_durability)  # fmt: skip
    opened = journal.snapshot()
    if isinstance(opened, Success) and isinstance(opened.value, RunJournalSnapshot):
        try:
            validate_run_journal_snapshot(progress.request.run_id, progress.initial, opened.value)
            if opened.value.events and opened.value.events[-1] == checkpoint:
                snapshot = opened.value
        except ValueError:
            pass
    require_snapshot_durability(journal, snapshot)
    return RunSuspended(progress.request, snapshot, checkpoint)


def resume_daw(
    suspended: RunSuspended,
    manifest: DawExportManifest,
    *,
    analyzer: AudioAnalyzer,
    diagnosis_source: DiagnosisSource,
    decision_maker: DecisionMaker,
    integrity_inspector: StateIntegrityInspector,
    journal: RunJournal,
    clock: RunClock,
    identities: IdentitySource,
    daw_controller: DawParameterController,
    recovery_controller: DawRecoveryController,
    export_handoff: DawExportHandoff,
) -> RunReport | RunSuspended | DawContinuationRefused:
    """Continue a suspended DAW run in-process with the operator's manifest (Increment 3, amended; ADR-037).

    The journal's per-run exclusive ownership (``RunExclusion``) is taken before the trace is read and held through
    admission, execution, any next suspension and finalization. Admission (``admit_continuation``) refuses a
    duplicate, conflicting, interrupted or foreign continuation without any effect. The rebuilt run starts a new clock
    segment, so the operator pause is never charged; its wall-clock length is recorded in ``DawContinuationStarted``.
    """
    require_instance(suspended, RunSuspended, "suspended")
    require_instance(manifest, DawExportManifest, "manifest")
    request, checkpoint_event = suspended.request, suspended.checkpoint_event
    checkpoint_record = checkpoint_event.payload
    if not journal.try_acquire_exclusive():
        return DawContinuationRefused(request.run_id, checkpoint_record.awaiting.request.export_id,
                                      DawContinuationRefusalCode.CONTINUATION_IN_PROGRESS,
                                      "another continuation of this run holds its exclusive ownership", None)  # fmt: skip
    try:
        opened = journal.snapshot()
        if not isinstance(opened, Success) or not isinstance(opened.value, RunJournalSnapshot):
            raise ValueError("the journal could not provide this run's trace")
        snapshot = opened.value
        initial = AcceptedReference(request.initial_state, 0)
        validate_run_journal_snapshot(request.run_id, initial, snapshot)
        require_snapshot_durability(journal, snapshot)
        refusal = admit_continuation(snapshot, request, checkpoint_event, manifest)
        if refusal is not None:
            return refusal
        if snapshot.accepted != checkpoint_record.accepted:
            raise ValueError("the journal's acceptance chain differs from the checkpoint's accepted reference")
        reading = clock.read()
        if not isinstance(reading, ClockReading):
            raise ValueError("the continuation clock's first reading is not a ClockReading")
        tokens = checkpoint_record.tokens | {e.event_id.value for e in snapshot.events}
        tokens |= {c.event.acceptance_id.value for c in snapshot.acceptances}
        progress = RuntimeProgress(
            request=request, initial=initial,
            first=ClockReading(0.0, snapshot.events[0].created_at, False), last=reading,
            usage=checkpoint_event.usage, known=snapshot.events, unacknowledged=(), commits=snapshot.acceptances,
            recording_ok=True, accepted=checkpoint_record.accepted, authority_known=True, verified=None,
            restored_last=False, tokens=frozenset(tokens), history=checkpoint_record.history,
            stems=checkpoint_record.stems, mix_id=checkpoint_record.mix_id, roots=checkpoint_record.roots,
            observations=checkpoint_record.observations, diagnosed=None,
            pending_effect=TerminationReason.REQUIRED_RECOVERY_NOT_PERFORMED,
            elapsed_before=checkpoint_event.usage.elapsed_seconds, segment_start=reading,
        )  # fmt: skip
        execute = partial(execute_daw, clock=clock, identities=identities, journal=journal,
                          daw_controller=daw_controller, recovery_controller=recovery_controller,
                          export_handoff=export_handoff, integrity_inspector=integrity_inspector)  # fmt: skip
        step = partial(_resumed, checkpoint_event=checkpoint_event, manifest=manifest, analyzer=analyzer, clock=clock,
                       decision_maker=decision_maker, diagnosis_source=diagnosis_source, identities=identities,
                       integrity_inspector=integrity_inspector, journal=journal,
                       recovery_controller=recovery_controller, export_handoff=export_handoff,
                       daw_controller=daw_controller, execute=execute)  # fmt: skip
        return _complete(progress, step, clock=clock, identities=identities, journal=journal)
    finally:
        journal.release_exclusive()


def _resumed(
    progress: RuntimeProgress,
    *,
    checkpoint_event: RunTraceEvent,
    manifest: DawExportManifest,
    analyzer: AudioAnalyzer,
    clock: RunClock,
    decision_maker: DecisionMaker,
    diagnosis_source: DiagnosisSource,
    identities: IdentitySource,
    integrity_inspector: StateIntegrityInspector,
    journal: RunJournal,
    recovery_controller: DawRecoveryController,
    export_handoff: DawExportHandoff,
    daw_controller: DawParameterController,
    execute: Callable,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop | RunTraceEvent]:
    """The continuation body: start record, continuation, completion evidence, then further decision rounds."""
    checkpoint_record = checkpoint_event.payload
    export_id = checkpoint_record.awaiting.request.export_id
    rec = dict(identities=identities, journal=journal, on_progress=on_progress)
    started = DawContinuationStarted(progress.request.run_id, checkpoint_event.event_id, export_id, manifest,
                                     checkpoint_record.paused_at_utc, progress.last.utc)  # fmt: skip
    progress, stop = record(progress, started, **rec)
    if stop:
        return (progress, (TerminationReason.REQUIRED_RECOVERY_NOT_PERFORMED,
                           f"the continuation could not be recorded ({stop[1]}); the fader is not restored"))  # fmt: skip
    started_id = progress.known[-1].event_id
    progress, result = continue_daw(
        progress, checkpoint_record, manifest, analyzer=analyzer, clock=clock, identities=identities,
        integrity_inspector=integrity_inspector, journal=journal, recovery_controller=recovery_controller,
        export_handoff=export_handoff, daw_controller=daw_controller, on_progress=on_progress,
    )  # fmt: skip
    accepted = progress.authority_known and progress.accepted.sequence > checkpoint_record.accepted.sequence
    if accepted:
        disposition = DawContinuationDisposition.ACCEPTED
    elif result is None:  # rejected and rolled back VERIFIED (ADR-038): the loop continues
        disposition = DawContinuationDisposition.ROLLED_BACK
    else:
        disposition = DawContinuationDisposition.ENDED
    finished = DawContinuationFinished(progress.request.run_id, started_id, export_id, disposition)
    progress, marker_stop = record(progress, finished, **rec)
    if result:
        return (progress, result)
    if marker_stop:
        return (progress, marker_stop)
    return _rounds(progress, clock=clock, decision_maker=decision_maker, diagnosis_source=diagnosis_source,
                   identities=identities, journal=journal, execute=execute, on_progress=on_progress)  # fmt: skip
