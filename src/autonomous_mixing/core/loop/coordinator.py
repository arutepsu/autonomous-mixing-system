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
from autonomous_mixing.core.loop.daw_execution import execute_daw
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
from autonomous_mixing.core.ports.run_journal import RunJournal, validate_run_journal_snapshot
from autonomous_mixing.core.records.acceptance import AcceptedReference
from autonomous_mixing.core.records.budgets import BudgetUsage
from autonomous_mixing.core.records.coordinator_progress import PreparedCorrection, RuntimeProgress, Stop
from autonomous_mixing.core.records.coordinator_runtime import ClockReading, RunRequest
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
) -> tuple[RuntimeProgress, Stop | RunTraceEvent]:
    """``execute(progress, prepared, on_progress=...)`` runs one prepared correction: the offline render or the DAW write."""
    progress, stop = record(progress, progress.request, identities=identities, journal=journal, on_progress=on_progress)
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
    return (progress, None)


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
                      recovery_controller=recovery_controller, export_handoff=export_handoff)  # fmt: skip

    return _run(request, analyzer=analyzer, diagnosis_source=diagnosis_source, decision_maker=decision_maker,
                integrity_inspector=integrity_inspector, journal=journal, clock=clock, identities=identities,
                execute=execute)  # fmt: skip


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
) -> RunReport | RunSuspended:
    initial = AcceptedReference(request.initial_state, 0)
    opened = journal.snapshot()
    if not isinstance(opened, Success) or not isinstance(opened.value, RunJournalSnapshot):
        raise ValueError("the journal could not confirm its initialization for this run")
    validate_run_journal_snapshot(request.run_id, initial, opened.value)
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
    latest = progress

    def remember(progress: RuntimeProgress) -> None:
        """Exception progress observer: keeps the latest immutable progress for unexpected-error finalization.

        Helpers call it after every replacement; normal control flow uses the progress they return instead.
        """
        nonlocal latest
        latest = progress

    try:
        progress, stop = ordinary(
            progress,
            analyzer=analyzer,
            clock=clock,
            decision_maker=decision_maker,
            diagnosis_source=diagnosis_source,
            identities=identities,
            integrity_inspector=integrity_inspector,
            journal=journal,
            execute=execute,
            on_progress=remember,
        )
    except Exception as error:
        # Phase-aware (review fix 2026-11-19): once a DAW write may have taken effect, the pending reason is kept; the
        # original exception still propagates, nothing is restored and no journal state is cleared.
        reason, explanation = TerminationReason.RUNTIME_CONTRACT_FAILED, "an unexpected error interrupted the run"
        if latest.pending_effect is not None:
            reason = latest.pending_effect
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
    snapshot = RunJournalSnapshot(
        progress.request.run_id, progress.initial, tuple(progress.known), tuple(progress.commits)
    )
    opened = journal.snapshot()
    if isinstance(opened, Success) and isinstance(opened.value, RunJournalSnapshot):
        try:
            validate_run_journal_snapshot(progress.request.run_id, progress.initial, opened.value)
            if opened.value.events and opened.value.events[-1] == checkpoint:
                snapshot = opened.value
        except ValueError:
            pass
    return RunSuspended(progress.request, snapshot, checkpoint)
