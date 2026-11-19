"""DAW execution of a prepared correction, up to the operator-export suspension (plan Increment 2; ADR-036).

Order: the calibrated capability view (``daw_capabilities``), then admission and the DAW preflight (``prepare_daw``).
After that, ``execute_daw`` runs these steps:
1. the journal control state;
2. one fresh inspection;
3. the capability gate;
4. one stable-ID fader write;
5. the journal-backed anchor of the confirmed write;
6. the export handoff;
7. the ``DawCorrectionSuspended`` checkpoint.

**Outcomes.** Each ends differently, and none produces a candidate: a confirmed fader write yields no receipt,
analysis or acceptance.
- **Gate rejection and ``DawNoWrite``:** nothing is dispatched; feedback is recorded and the loop continues.
- **Pre-write refusal:** nothing was written; a failed attempt is recorded and the loop continues.
- **Off-target, uncertain or invalid writes, and failures after a confirmed write:** the run ends at once (interim
  policy, until Increment 4). There is no restoration, reconciliation, resolution or rollback receipt, and nothing is
  cleared in the FL journal. The verified reference stays cleared, so the final state is ``UNKNOWN``.
"""

import re
from collections.abc import Callable
from dataclasses import replace

from autonomous_mixing.core.admission.daw import daw_gain_bounds, gate_daw_apply
from autonomous_mixing.core.admission.daw_correction import preflight_daw_correction
from autonomous_mixing.core.loop.feedback import _rejection, add_feedback
from autonomous_mixing.core.loop.runtime import _invalid, fresh, gate, identity_failure, observed, record
from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource, RunClock
from autonomous_mixing.core.ports.daw import DawParameterController, validate_daw_apply_outcome, validate_daw_inspection
from autonomous_mixing.core.ports.daw_export import DawExportHandoff, validate_daw_export_preparation
from autonomous_mixing.core.ports.daw_recovery import (
    DawRecoveryController,
    validate_daw_control_state,
    validate_daw_state_anchor,
)
from autonomous_mixing.core.ports.run_journal import RunJournal
from autonomous_mixing.core.records.actions import ExecutionStatus
from autonomous_mixing.core.records.admission import AdmittedAction, PreflightRejected
from autonomous_mixing.core.records.budgets import BudgetOperation
from autonomous_mixing.core.records.capabilities import RendererCapabilities
from autonomous_mixing.core.records.coordinator_progress import PreparedCorrection, RuntimeProgress, Stop
from autonomous_mixing.core.records.daw import DawTrackState
from autonomous_mixing.core.records.daw_control import (
    DawApplyPermitted,
    DawApplyRejected,
    DawInspectRequest,
    DawNoWrite,
    DawWriteConfirmed,
    DawWriteOffTarget,
    DawWriteRefused,
)
from autonomous_mixing.core.records.daw_correction import DawCorrectionSuspended, DawSessionConfig, DawStateQuery
from autonomous_mixing.core.records.daw_export import DawExportAwaiting, DawExportRequest, DawStateBasis
from autonomous_mixing.core.records.decision import Decision, Situation
from autonomous_mixing.core.records.feedback import ActionFeedback, ActionFeedbackDisposition, FeedbackStage
from autonomous_mixing.core.records.identifiers import ArtifactId, AttemptId, DecisionCallId, ExecutionId, Revision
from autonomous_mixing.core.records.project import ProjectState
from autonomous_mixing.core.records.rendering import ExecutionWorkspace
from autonomous_mixing.core.records.results import Success
from autonomous_mixing.core.records.run_trace import OperationKind, RunTraceEvent
from autonomous_mixing.core.records.termination import TerminationReason

_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")


def daw_track_state(state: ProjectState, session: DawSessionConfig) -> DawTrackState:
    """The bound track's selected and confirmed DAW state in ``state`` (its DAW parameter state, Option A)."""
    parameter = state.daw.track(session.binding.track_id) if state.daw is not None else None
    if parameter is None or parameter.binding != session.binding:
        raise ValueError("the state holds no DAW parameter state for the session's binding")
    return DawTrackState(parameter.binding, state.revision, parameter.selected_gain_db, parameter.confirmed_fader)


def daw_capabilities(state: ProjectState, session: DawSessionConfig) -> RendererCapabilities:
    """The capability view's execution bounds: the calibrated window of the confirmed state (``daw_gain_bounds``)."""
    bounds = daw_gain_bounds(profile=session.profile, state=daw_track_state(state, session),
                             permission=session.permission)  # fmt: skip
    return RendererCapabilities(session.profile.reference, bounds)


def prepare_daw(
    progress: RuntimeProgress,
    call_id: DecisionCallId,
    situation: Situation,
    decision: Decision,
    attempt_id: AttemptId,
    reject: Callable,
    *,
    identities: IdentitySource,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop | PreparedCorrection | None]:
    """DAW identities and preflight in place of the offline render preflight; ``reject`` records a rejection."""
    session = progress.request.daw_session
    state = situation.state
    progress, execution_id = fresh(progress, ExecutionId, identities=identities, on_progress=on_progress)
    token = identities.next_token()
    progress, revision = fresh(progress, Revision, identities=identities, on_progress=on_progress)
    output_ids = []
    for _ in range(len(session.routing.dependent_track_ids) + 2):
        progress, artifact_id = fresh(progress, ArtifactId, identities=identities, on_progress=on_progress)
        output_ids.append(artifact_id)
    command_id = f"apply-{token}" if isinstance(token, str) else None
    if (
        None in (execution_id, revision, *output_ids)
        or not isinstance(token, str)
        or token in progress.tokens
        or not _ID.fullmatch(command_id)
        or not _ID.fullmatch(f"export-{token}")
    ):
        return (progress, identity_failure("DAW execution identity"))
    progress = replace(progress, tokens=progress.tokens | {token})
    on_progress(progress)
    try:
        workspace = ExecutionWorkspace(execution_id, session.export_root_key, token)
    except ValueError as error:
        return (progress, (TerminationReason.RUNTIME_CONTRACT_FAILED, f"generated DAW identity is invalid: {error}"))
    ready = preflight_daw_correction(
        execution_id=execution_id, command_id=command_id, situation=situation, decision=decision,
        policy=progress.request.policy, session=session, workspace=workspace, candidate_revision=revision,
        output_ids=tuple(output_ids),
    )  # fmt: skip
    if isinstance(ready, PreflightRejected):
        return reject(progress, ready, FeedbackStage.PREFLIGHT, ready.code.value, identities=identities,
                      journal=journal, on_progress=on_progress)  # fmt: skip
    progress, stop = record(progress, ready, identities=identities, journal=journal, on_progress=on_progress)
    if stop:
        return (progress, stop)
    return (progress, PreparedCorrection(call_id, state, decision, attempt_id, ready.plan))


def execute_daw(
    progress: RuntimeProgress,
    ctx: PreparedCorrection,
    *,
    clock: RunClock,
    identities: IdentitySource,
    journal: RunJournal,
    daw_controller: DawParameterController,
    recovery_controller: DawRecoveryController,
    export_handoff: DawExportHandoff,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop | RunTraceEvent | None]:
    """None: another decision round follows. A stop ends the run. A ``RunTraceEvent``: the recorded suspension."""
    session, plan, state, action = progress.request.daw_session, ctx.plan, ctx.before, ctx.action
    ids = dict(call=ctx.call_id, attempt=ctx.attempt_id, execution=plan.execution_id, clock=clock,
               identities=identities, journal=journal, on_progress=on_progress)  # fmt: skip
    rec = dict(identities=identities, journal=journal, on_progress=on_progress)

    def feedback(progress, outcome):
        return add_feedback(progress, ctx.call_id, state, outcome, decision_id=ctx.decision.decision_id, **rec)

    def not_written(progress, stage, code, explanation, stop, *, rejected=False):
        outcome = _rejection(action, ctx.attempt_id, stage, code, explanation, not_executed=not rejected)
        progress, feedback_stop = feedback(progress, outcome)
        return (progress, stop or feedback_stop)

    def unresolved(progress, reason, explanation):
        """Interim termination (ADR-036): possibly written, not restored, nothing cleared, no rollback claimed."""
        outcome = ActionFeedback(action, ctx.attempt_id, plan.execution_id, ActionFeedbackDisposition.UNRESOLVED,
                                 FeedbackStage.RENDER, ExecutionStatus.UNCERTAIN, None, None, reason.value, explanation,
                                 ())  # fmt: skip
        progress, _ = feedback(progress, outcome)
        return (progress, (reason, explanation))

    def after_write(progress, cause):
        return unresolved(progress, TerminationReason.REQUIRED_RECOVERY_NOT_PERFORMED,
                          f"the fader write was confirmed but {cause}; the fader is not restored and recovery is "
                          "not performed in this increment")  # fmt: skip

    # 1. the FL journal's control state (unresolved commands, blocked conversions)
    preparation = TerminationReason.EXECUTION_PREPARATION_FAILED
    control = recovery_controller.control_state()
    try:
        validate_daw_control_state(control)
    except ValueError as error:
        return not_written(progress, FeedbackStage.PREFLIGHT, "daw_control_state_invalid", str(error),
                           (preparation, f"invalid DAW control state: {error}"))  # fmt: skip
    if not isinstance(control, Success):
        return not_written(progress, FeedbackStage.PREFLIGHT, "daw_control_state_unavailable", control.message,
                           (preparation, f"the DAW journal state is unavailable: {control.message}"))  # fmt: skip
    control = control.value
    progress, stop = record(progress, control, **rec)
    if stop:
        return (progress, stop)

    # 2. one fresh inspection of the bound insert
    inspect_request = DawInspectRequest(session.binding, session.profile)
    progress, (intent_id, _, stop, _) = gate(progress, OperationKind.DAW_INSPECT, lambda c: inspect_request,
                                                   None, **ids)  # fmt: skip
    if stop:
        return not_written(progress, FeedbackStage.PREFLIGHT, stop[0].value, stop[1], stop)
    inspected = daw_controller.inspect(inspect_request)
    try:
        validate_daw_inspection(inspect_request, inspected)
        valid, recorded = True, inspected.value if isinstance(inspected, Success) else inspected
    except ValueError as error:
        valid, recorded = False, _invalid(inspected, error)
    progress, stop = observed(progress, intent_id, OperationKind.DAW_INSPECT, recorded, clock=clock, **rec)
    if not valid or not isinstance(inspected, Success):
        explanation = "the DAW inspection failed or was invalid"
        return not_written(progress, FeedbackStage.PREFLIGHT, "daw_inspection_unavailable", explanation,
                           stop or (preparation, explanation))  # fmt: skip
    if stop:
        return not_written(progress, FeedbackStage.PREFLIGHT, stop[0].value, stop[1], stop)

    # 3. the capability gate against the fresh inspection and the journal state
    verdict = gate_daw_apply(
        AdmittedAction(action, progress.request.policy.reference), state=daw_track_state(state, session),
        inspection=inspected.value, profile=session.profile, execution_id=plan.execution_id,
        command_id=plan.command_id, blocked_conversions=control.blocked_conversions,
        unresolved_command_id=control.unresolved[0].command_id if control.unresolved else None,
    )  # fmt: skip
    progress, stop = record(progress, verdict, **rec)
    if stop:
        return (progress, stop)
    if isinstance(verdict, DawApplyRejected):
        return not_written(progress, FeedbackStage.PREFLIGHT, verdict.code.value, verdict.explanation, None,
                           rejected=True)  # fmt: skip
    if isinstance(verdict, DawNoWrite):
        return not_written(progress, FeedbackStage.PREFLIGHT, "daw_no_write", "the resolved change is exactly 0 dB",
                           None)  # fmt: skip
    if not isinstance(verdict, DawApplyPermitted):
        raise ValueError(f"unknown DAW gate result {verdict!r}")
    request = verdict.request

    # 4. one stable-ID write; the FL journal commits its own intent before any byte is sent
    progress, (intent_id, _, stop, skipped) = gate(progress, OperationKind.DAW_APPLY, lambda c: request,
                                                   BudgetOperation.DAW_WRITE, **ids)  # fmt: skip
    if stop:
        stage = FeedbackStage.RENDER if skipped else FeedbackStage.PREPARATION
        return not_written(progress, stage, stop[0].value, stop[1], stop)
    verified_before = progress.verified
    # From here an unexpected exception keeps the uncertain-command classification (review fix 2026-11-19).
    progress = replace(progress, verified=None, pending_effect=TerminationReason.DAW_COMMAND_UNRESOLVED)
    on_progress(progress)
    written = daw_controller.apply(request)
    try:
        validate_daw_apply_outcome(request, written)
        recorded = written
    except ValueError as error:
        written, recorded = None, _invalid(written, error)
    # Classify the validated reply before anything else can fail, recording included (review fix 2026-11-19):
    # refused -> nothing pending and the verified reference re-established; off-target -> conversion blocked;
    # confirmed -> recovery required; uncertain or invalid -> stays unresolved (as set before the apply).
    if isinstance(written, DawWriteRefused):
        progress = replace(progress, verified=verified_before, pending_effect=None)
    elif isinstance(written, DawWriteOffTarget):
        progress = replace(progress, pending_effect=TerminationReason.DAW_CONVERSION_BLOCKED)
    elif isinstance(written, DawWriteConfirmed):
        progress = replace(progress, pending_effect=TerminationReason.REQUIRED_RECOVERY_NOT_PERFORMED)
    on_progress(progress)
    progress, stop = observed(progress, intent_id, OperationKind.DAW_APPLY, recorded, clock=clock, **rec)
    if isinstance(written, DawWriteRefused):
        outcome = ActionFeedback(action, ctx.attempt_id, plan.execution_id, ActionFeedbackDisposition.FAILED,
                                 FeedbackStage.RENDER, ExecutionStatus.NOT_STARTED, None, None, written.code.value,
                                 written.explanation, ())  # fmt: skip
        progress, feedback_stop = feedback(progress, outcome)
        return (progress, stop or feedback_stop)
    if isinstance(written, DawWriteOffTarget):
        return unresolved(progress, TerminationReason.DAW_CONVERSION_BLOCKED,
                          "the fader landed off target; its conversion is blocked and the fader is not restored")  # fmt: skip
    if not isinstance(written, DawWriteConfirmed):
        return unresolved(progress, TerminationReason.DAW_COMMAND_UNRESOLVED,
                          "the fader write is uncertain or invalid; the command stays unresolved")  # fmt: skip
    if stop:
        return after_write(progress, f"its outcome could not be recorded ({stop[0].value})")

    # 5. the journal-backed anchor of exactly this confirmed write
    query = DawStateQuery(request.command_id, False)
    progress, (intent_id, _, stop, _) = gate(progress, OperationKind.DAW_STATE, lambda c: query, None, **ids)
    if stop:
        return after_write(progress, f"the anchor could not be queried ({stop[0].value}: {stop[1]})")
    anchored = recovery_controller.state_anchor(request.command_id)
    try:
        validate_daw_state_anchor(request.command_id, anchored)
        recorded = anchored.value if isinstance(anchored, Success) else anchored
    except ValueError as error:
        anchored, recorded = None, _invalid(anchored, error)
    progress, stop = observed(progress, intent_id, OperationKind.DAW_STATE, recorded, clock=clock, **rec)
    anchor = anchored.value if isinstance(anchored, Success) else None
    if (
        anchor is None
        or anchor.basis is not DawStateBasis.CONFIRMED_WRITE
        or anchor.apply != request
        or (anchor.reading != written.after)
    ):
        return after_write(progress, "its journal-backed anchor is unavailable or does not match the write")
    if stop:
        return after_write(progress, f"its anchor could not be recorded ({stop[0].value})")

    # 6. the export handoff: persisted request, fresh workspace, awaiting the operator
    try:
        export = DawExportRequest(f"export-{plan.workspace.directory_token}", plan.execution_id, state.audio,
                                  plan.candidate_revision, plan.binding, plan.profile, anchor, plan.routing,
                                  plan.outputs, plan.timeline, plan.format, plan.render_spec, plan.workspace)  # fmt: skip
    except ValueError as error:
        return after_write(progress, f"the export request could not be built ({error})")
    progress, (intent_id, _, stop, _) = gate(progress, OperationKind.EXPORT_PREPARE, lambda c: export, None, **ids)
    if stop:
        return after_write(progress, f"the export could not be prepared ({stop[0].value}: {stop[1]})")
    prepared = export_handoff.prepare(export)
    try:
        validate_daw_export_preparation(export, prepared)
        recorded = prepared
    except ValueError as error:
        prepared, recorded = None, _invalid(prepared, error)
    progress, stop = observed(progress, intent_id, OperationKind.EXPORT_PREPARE, recorded, clock=clock, **rec)
    if not isinstance(prepared, DawExportAwaiting):
        detail = prepared.code.value if prepared is not None else "invalid response"
        return after_write(progress, f"the export preparation failed ({detail})")
    if stop:
        return after_write(progress, f"the export preparation could not be recorded ({stop[0].value})")

    # 7. the complete checkpoint; the run waits for the operator
    try:
        checkpoint = DawCorrectionSuspended(progress.request.run_id, ctx.call_id, ctx.decision, ctx.attempt_id, state,
                                            plan, progress.accepted, progress.tokens, progress.history,
                                            progress.observations, request, written, prepared,
                                            progress.last.utc, progress.stems, progress.mix_id,
                                            progress.roots)  # fmt: skip
    except ValueError as error:
        return after_write(progress, f"the suspension checkpoint is inconsistent ({error})")
    progress, stop = record(progress, checkpoint, **rec)
    if stop:
        return after_write(progress, f"the suspension checkpoint could not be recorded ({stop[0].value})")
    return (progress, progress.known[-1])
