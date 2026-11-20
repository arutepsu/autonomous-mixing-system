"""Authorized DAW recovery routing (coordinator-integration Increment 4, ADR-038).

``recover_daw`` replaces the interim "recovery not attempted" stops. It recovers one DAW attempt whose write was
confirmed, landed off target or is uncertain, from journal-backed evidence only:

1. the recovery-lineage query decides whether this run's own lineage is still the latest (else recovery is withheld);
2. an uncertain root is reconciled once and ``daw_recovery_decision`` maps the finding to the permitted steps;
3. at most one exact restoration to the journaled target, gated by ``gate_daw_restore`` and charged once;
4. resolutions only where the journal's evidence rules permit them;
5. fader evidence only from one final coherent recovery snapshot: its fresh reading must equal the saved target (and a
   confirmed restoration's reading) exactly; a resolution or a tolerance match is never evidence;
6. the retained-audio integrity inspection (``recover_saved``), only when the fader evidence passes.

A VERIFIED selected-state rollback needs both 5 and 6. Only a candidate rejected by verification continues the loop
after it; every other case terminates by the precedence in ``_reason``. ``pending_effect`` always names what an
unexpected exception would leave behind and is set from each validated reply before that reply is recorded.
"""

import math
import re
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace

from autonomous_mixing.core.admission.daw import daw_recovery_decision, gate_daw_restore
from autonomous_mixing.core.loop.budgets import check_budget
from autonomous_mixing.core.loop.feedback import add_feedback, copied
from autonomous_mixing.core.loop.recovery import recover_saved
from autonomous_mixing.core.loop.runtime import _invalid, gate, observed, record
from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource, RunClock
from autonomous_mixing.core.ports.daw import DawParameterController, validate_daw_inspection
from autonomous_mixing.core.ports.daw_recovery import (
    DawRecoveryController,
    validate_daw_control_state,
    validate_daw_reconciliation,
    validate_daw_recovery_lineage,
    validate_daw_recovery_snapshot,
    validate_daw_resolution,
    validate_daw_restore_outcome,
)
from autonomous_mixing.core.ports.integrity import StateIntegrityInspector
from autonomous_mixing.core.ports.run_journal import RunJournal
from autonomous_mixing.core.records.acceptance import AcceptedReference
from autonomous_mixing.core.records.actions import (
    DawRollbackEvidenceKind,
    DawRollbackFaderEvidence,
    ExecutionReceipt,
    ExecutionStatus,
    RollbackReceipt,
    RollbackScope,
    RollbackStatus,
    daw_rollback_evidence_problem,
)
from autonomous_mixing.core.records.budgets import BudgetGrant, BudgetOperation
from autonomous_mixing.core.records.coordinator_progress import PreparedCorrection, RuntimeProgress, Stop
from autonomous_mixing.core.records.daw_control import DawApplyRequest, DawInspectRequest
from autonomous_mixing.core.records.daw_recovery import (
    DawControlState,
    DawLineageQuery,
    DawReconcileRequest,
    DawRecoveryAuthorization,
    DawRecoveryEntry,
    DawRecoveryPlan,
    DawResolutionAuthorization,
    DawResolutionKind,
    DawResolutionRecorded,
    DawResolutionRequest,
    DawRestoreConfirmed,
    DawRestoreNoWrite,
    DawRestorePermitted,
    DawRestoreRefused,
    DawRestoreRejected,
    DawRestoreRequest,
    DawRootStatus,
)
from autonomous_mixing.core.records.daw_rollback import DawRollbackRecord
from autonomous_mixing.core.records.feedback import ActionFeedback, ActionFeedbackDisposition, FeedbackStage
from autonomous_mixing.core.records.project import ProjectState
from autonomous_mixing.core.records.results import Success
from autonomous_mixing.core.records.run import AttemptDisposition, CorrectionAttempt
from autonomous_mixing.core.records.run_trace import OperationKind
from autonomous_mixing.core.records.termination import TerminationReason
from autonomous_mixing.core.records.verification import VerificationResult

_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
_UNRESOLVED = TerminationReason.DAW_COMMAND_UNRESOLVED
_BLOCKED = TerminationReason.DAW_CONVERSION_BLOCKED
_NOT_PERFORMED = TerminationReason.REQUIRED_RECOVERY_NOT_PERFORMED
_RESTORED_UNVERIFIED = TerminationReason.RESTORATION_UNCERTAIN
_ENTRY_MARKER = {
    DawRecoveryEntry.CONFIRMED_WRITE: _NOT_PERFORMED,
    DawRecoveryEntry.OFF_TARGET: _BLOCKED,
    DawRecoveryEntry.UNCERTAIN_WRITE: _UNRESOLVED,
}
_ROOT_STATUS = {
    DawRecoveryEntry.CONFIRMED_WRITE: (DawRootStatus.CONFIRMED,),
    DawRecoveryEntry.OFF_TARGET: (DawRootStatus.OFF_TARGET,),
    # an uncertain or invalid reply: the journal decides what was written
    DawRecoveryEntry.UNCERTAIN_WRITE: tuple(DawRootStatus),
}
_JOURNAL_ENTRY = {
    DawRootStatus.CONFIRMED: DawRecoveryEntry.CONFIRMED_WRITE,
    DawRootStatus.OFF_TARGET: DawRecoveryEntry.OFF_TARGET,
    DawRootStatus.UNCERTAIN: DawRecoveryEntry.UNCERTAIN_WRITE,
    DawRootStatus.PENDING: DawRecoveryEntry.UNCERTAIN_WRITE,
}


def recovery_ids(token: str) -> dict[str, str]:
    """The stable recovery IDs of one DAW attempt, derived from its execution token (the token of ``apply-<token>``).

    A replay of any step reuses the same ID, so the FL journal replays it instead of writing or charging again.
    Raises ``ValueError`` when a derived ID would fall outside the 8-64 character ID rule.
    """
    ids = {
        "reconcile_root": f"rec-{token}-a1",
        "reconcile_restore": f"rec-{token}-r1",
        "authorization": f"rauth-{token}-1",
        "restore": f"rst-{token}-1",
        "resolve_root_authorization": f"rsa-{token}-a1",
        "resolve_restore_authorization": f"rsa-{token}-r1",
        "resolve_root": f"res-{token}-a1",
        "resolve_restore": f"res-{token}-r1",
    }
    if not isinstance(token, str) or not all(_ID.fullmatch(value) for value in ids.values()):
        raise ValueError("the execution token does not fit the recovery IDs")
    return ids


def require_recovery_authority(progress: RuntimeProgress, expected: AcceptedReference) -> None:
    """Recovery may run only while the attempt's own before reference is the known authoritative one.

    Established acceptance stays authoritative: after a committed or found acceptance, or while the acceptance
    authority is unknown, the previous state is never restored through the unaccepted-candidate path.
    """
    if not progress.authority_known or progress.accepted != expected:
        raise ValueError("recovery runs only while the attempt's before reference is the known authoritative one")


def _exactly(a: float, b: float) -> bool:
    return a == b and math.copysign(1.0, a) == math.copysign(1.0, b)


def _same_reading(a, b) -> bool:
    return _exactly(a.normalized, b.normalized) and _exactly(a.db, b.db)


def recover_daw(
    progress: RuntimeProgress,
    ctx: PreparedCorrection,
    entry: DawRecoveryEntry,
    original: Stop,
    *,
    expected: AcceptedReference,
    apply: DawApplyRequest,
    receipt: ExecutionReceipt | None = None,
    candidate: ProjectState | None = None,
    verification: VerificationResult | None = None,
    bundles: tuple = (),
    stage: FeedbackStage = FeedbackStage.RENDER,
    iterate: bool = False,
    clock: RunClock,
    identities: IdentitySource,
    integrity_inspector: StateIntegrityInspector,
    journal: RunJournal,
    daw_controller: DawParameterController,
    recovery_controller: DawRecoveryController,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop | None]:
    """Recover one DAW attempt; None means a rejected candidate was rolled back VERIFIED and the loop continues."""
    require_recovery_authority(progress, expected)
    session = progress.request.daw_session
    root = apply.command_id
    ids = recovery_ids(ctx.plan.workspace.directory_token)
    saved = expected.state.daw.track(session.binding.track_id)
    rec = dict(identities=identities, journal=journal, on_progress=on_progress)
    env = dict(call=ctx.call_id, attempt=ctx.attempt_id, execution=ctx.execution_id, clock=clock, **rec)
    # What this recovery has established so far: function-local working state, never a record.
    facts = SimpleNamespace(unresolved=entry is DawRecoveryEntry.UNCERTAIN_WRITE,
                            blocked=entry is DawRecoveryEntry.OFF_TARGET, performed=False, contract=None)  # fmt: skip

    def mark(progress, reason):
        progress = replace(progress, verified=None, pending_effect=reason)
        on_progress(progress)
        return progress

    def settled_marker():
        return settled(facts)

    def finish(progress, status, explanation, evidence=(), retained=RollbackStatus.UNCERTAIN):
        return _finish(progress, ctx, entry, original, facts, status, explanation, evidence, retained,
                       expected=expected, receipt=receipt, candidate=candidate, verification=verification,
                       bundles=bundles, stage=stage, iterate=iterate, **rec)  # fmt: skip

    progress = mark(progress, _ENTRY_MARKER[entry])

    # 1. the run's own lineage must be the latest (journal-backed; export anchors are not involved)
    query = DawLineageQuery(root, False)
    progress, (intent_id, _, stop, _) = gate(progress, OperationKind.DAW_STATE, lambda c: query, None, **env)
    if stop:
        return finish(progress, RollbackStatus.UNCERTAIN, f"recovery not performed: the lineage query was not "
                      f"dispatched ({stop[0].value})")  # fmt: skip
    found = recovery_controller.recovery_lineage(root)
    try:
        validate_daw_recovery_lineage(root, found)
        outcome = found.value if isinstance(found, Success) else found
    except ValueError as error:
        found, outcome = None, _invalid(found, error)
    progress, stop = observed(progress, intent_id, OperationKind.DAW_STATE, outcome, clock=clock, **rec)
    lineage = found.value if isinstance(found, Success) else None
    if lineage is None or stop:
        return finish(progress, RollbackStatus.UNCERTAIN, "recovery not performed: the recovery lineage is unavailable")
    facts.unresolved, facts.blocked = bool(lineage.unresolved), lineage.conversion_blocked
    if lineage.apply != apply or lineage.status not in _ROOT_STATUS[entry]:
        facts.contract = "the journal's lineage does not describe this attempt's write"
        return finish(progress, RollbackStatus.UNCERTAIN, f"recovery withheld: {facts.contract}")
    if not lineage.own_latest():
        return finish(progress, RollbackStatus.UNCERTAIN, "recovery withheld: another command followed this run's "
                      "write, so the fader's later history is not this run's to undo")  # fmt: skip
    journal_entry = _JOURNAL_ENTRY[lineage.status] if entry is DawRecoveryEntry.UNCERTAIN_WRITE else entry

    # 2. an uncertain root: one fresh reconciliation decides which steps are permitted
    reconciliation = None
    if journal_entry is DawRecoveryEntry.UNCERTAIN_WRITE:
        progress, reconciliation = _reconcile(progress, ids["reconcile_root"], root, session, recovery_controller,
                                              facts, env=env, clock=clock, rec=rec)  # fmt: skip
        if reconciliation is None:
            return finish(progress, RollbackStatus.UNCERTAIN, "the uncertain write could not be reconciled")
    decision = daw_recovery_decision(journal_entry, root, reconciliation)
    progress, stop = record(progress, decision, **rec)
    if stop:
        return finish(progress, RollbackStatus.UNCERTAIN, "the recovery decision could not be recorded")
    if decision.plan is DawRecoveryPlan.NONE:
        return finish(progress, RollbackStatus.UNCERTAIN, "the reading is not of the bound insert: nothing may be "
                      "restored or resolved")  # fmt: skip

    # 3. at the recorded start: resolve first (clears uncertainty only, never evidence)
    if decision.plan is DawRecoveryPlan.RESOLVE_THEN_RESTORE:
        progress, resolved = _resolve(progress, ids["resolve_root"], ids["resolve_root_authorization"], root,
                                      DawResolutionKind.NOT_APPLIED_OR_REVERTED, reconciliation.reconciliation_id,
                                      recovery_controller, facts, remaining=False, env=env, clock=clock,
                                      rec=rec)  # fmt: skip
        if not resolved:
            return finish(progress, RollbackStatus.UNCERTAIN, "the uncertain write could not be resolved")

    # 4. at most one exact restoration to the journaled target
    progress, restoration = _restore(progress, ids, root, session, journal_entry, daw_controller, recovery_controller,
                                     facts, mark=mark, env=env, clock=clock, rec=rec)  # fmt: skip
    if isinstance(restoration, tuple):
        status, explanation = restoration
        return finish(progress, status, explanation)
    if decision.plan is DawRecoveryPlan.RESTORE_THEN_RESOLVE:
        if not isinstance(restoration, DawRestoreConfirmed):
            return finish(progress, RollbackStatus.UNCERTAIN, "no confirmed restoration of the uncertain write "
                          "exists, so it stays unresolved")  # fmt: skip
        progress, resolved = _resolve(progress, ids["resolve_root"], ids["resolve_root_authorization"], root,
                                      DawResolutionKind.RESTORED_TO_RECORDED_START, ids["restore"],
                                      recovery_controller, facts, remaining=False, env=env, clock=clock,
                                      rec=rec)  # fmt: skip
        if not resolved:
            return finish(progress, RollbackStatus.UNCERTAIN, "the restored write could not be resolved")
    progress = mark(progress, settled_marker())

    # 5. fader evidence only from one final coherent recovery snapshot
    progress, evidence, problem = _evidence(progress, root, ids["restore"], apply, saved, restoration,
                                            recovery_controller, facts, env=env, clock=clock, rec=rec)  # fmt: skip
    facts.performed = facts.performed or evidence is not None  # exact evidence is itself a recovery result
    progress = mark(progress, settled_marker())
    if evidence is None:
        status = RollbackStatus.FAILED if problem[0] else RollbackStatus.UNCERTAIN
        return finish(progress, status, problem[1])

    # 6. retained-audio integrity of the saved accepted state
    progress, (retained, inner) = recover_saved(progress, clock=clock, identities=identities,
                                                integrity_inspector=integrity_inspector, journal=journal,
                                                on_progress=on_progress)  # fmt: skip
    if retained is not RollbackStatus.VERIFIED:
        status = RollbackStatus.FAILED if retained is RollbackStatus.FAILED else RollbackStatus.UNCERTAIN
        detail = "was not performed" if retained is None else f"is {retained.value}"
        return finish(progress, status, f"the fader is exactly at the saved target, but the retained audio's "
                      f"integrity inspection {detail}", (evidence,), retained or RollbackStatus.UNCERTAIN)  # fmt: skip
    if inner:
        original_stop = inner
        return _finish(progress, ctx, entry, original_stop, facts, RollbackStatus.VERIFIED,
                       "fader restored exactly and retained audio verified; project-level restoration unverified",
                       (evidence,), RollbackStatus.VERIFIED, expected=expected, receipt=receipt, candidate=candidate,
                       verification=verification, bundles=bundles, stage=stage, iterate=False, **rec)  # fmt: skip
    return finish(progress, RollbackStatus.VERIFIED, "fader restored exactly and retained audio verified; "
                  "project-level restoration unverified", (evidence,), RollbackStatus.VERIFIED)  # fmt: skip


def _reconcile(progress, reconciliation_id, subject, session, recovery_controller, facts, *, env, clock, rec):
    request = DawReconcileRequest(reconciliation_id, subject, session.binding, session.profile)
    progress, (intent_id, _, stop, _) = gate(progress, OperationKind.DAW_RECOVERY, lambda c: request, None, **env)
    if stop:
        return (progress, None)
    result = recovery_controller.reconcile(request)
    try:
        validate_daw_reconciliation(request, result)
        outcome = result.value if isinstance(result, Success) else result
    except ValueError as error:
        result, outcome = None, _invalid(result, error)
    facts.performed = True
    progress, stop = observed(progress, intent_id, OperationKind.DAW_RECOVERY, outcome, clock=clock, **rec)
    if stop or not isinstance(result, Success):
        return (progress, None)
    return (progress, result.value)


def _charged(recovery_controller, restore_id):
    """Whether the journal holds a recovery charge for ``restore_id`` (unknown counts as charged: fail closed)."""
    state = recovery_controller.control_state()
    if not isinstance(state, Success) or not isinstance(state.value, DawControlState):
        return True
    return any(c.recovery_command_id == restore_id for c in state.value.recovery_charges)


def settled(facts):
    """The marker once nothing is in flight: what remains if the run stopped now."""
    if facts.unresolved:
        return _UNRESOLVED
    if facts.blocked:
        return _BLOCKED
    return _RESTORED_UNVERIFIED if facts.performed else _NOT_PERFORMED


def _resolve(progress, resolution_id, authorization_id, subject, kind, evidence_id, recovery_controller, facts, *,
             remaining, env, clock, rec):  # fmt: skip
    """One resolution. ``remaining`` says whether a command stays unresolved once this one is resolved; the facts and
    the marker are settled from the validated reply before it is recorded (review fix 2026-11-20)."""
    policy = progress.request.policy.reference
    request = DawResolutionRequest(resolution_id, subject, kind, evidence_id,
                                   DawResolutionAuthorization(authorization_id, policy))  # fmt: skip
    progress, (intent_id, _, stop, _) = gate(progress, OperationKind.DAW_RECOVERY, lambda c: request, None, **env)
    if stop:
        return (progress, False)
    outcome = recovery_controller.resolve(request)
    try:
        validate_daw_resolution(request, outcome)
        recorded = outcome
    except ValueError as error:
        outcome, recorded = None, _invalid(outcome, error)
    facts.performed = True
    resolved = isinstance(outcome, DawResolutionRecorded)
    if resolved:  # classified before it is recorded, for a root and for a restoration alike
        facts.unresolved = remaining
        progress = replace(progress, pending_effect=settled(facts))
        rec["on_progress"](progress)
    progress, stop = observed(progress, intent_id, OperationKind.DAW_RECOVERY, recorded, clock=clock, **rec)
    return (progress, resolved and not stop)


def _restore(progress, ids, root, session, entry, daw_controller, recovery_controller, facts, *, mark, env, clock,
             rec):  # fmt: skip
    """At most one exact restoration. Returns the confirmed outcome, None (no write was needed or the restoration
    was not confirmed), or a (status, explanation) pair that ends the recovery."""
    target = recovery_controller.restore_target(root)
    if not isinstance(target, Success) or target.value.source_command_id != root:
        return (progress, (RollbackStatus.UNCERTAIN, "the journaled restore target is unavailable"))
    progress, stop = record(progress, target.value, **rec)
    if stop:
        return (progress, (RollbackStatus.UNCERTAIN, "the restore target could not be recorded"))
    inspect_request = DawInspectRequest(session.binding, session.profile)
    progress, (intent_id, _, stop, _) = gate(progress, OperationKind.DAW_INSPECT, lambda c: inspect_request, None,
                                             **env)  # fmt: skip
    if stop:
        return (progress, (RollbackStatus.UNCERTAIN, f"the fader could not be inspected ({stop[0].value})"))
    inspected = daw_controller.inspect(inspect_request)
    try:
        validate_daw_inspection(inspect_request, inspected)
        outcome = inspected.value if isinstance(inspected, Success) else inspected
    except ValueError as error:
        inspected, outcome = None, _invalid(inspected, error)
    progress, stop = observed(progress, intent_id, OperationKind.DAW_INSPECT, outcome, clock=clock, **rec)
    if stop or not isinstance(inspected, Success):
        return (progress, (RollbackStatus.UNCERTAIN, "the fader inspection before the restoration failed"))
    control = recovery_controller.control_state()
    try:
        validate_daw_control_state(control)
    except ValueError:
        control = None
    if not isinstance(control, Success):
        return (progress, (RollbackStatus.UNCERTAIN, "the DAW journal state is unavailable before the restoration"))
    progress, stop = record(progress, control.value, **rec)
    if stop:
        return (progress, (RollbackStatus.UNCERTAIN, "the DAW journal state could not be recorded"))
    restore_id = ids["restore"]
    existing = next((c.budget_grant for c in control.value.recovery_charges if c.recovery_command_id == restore_id),
                    None)  # fmt: skip
    proposed = existing
    if proposed is None:
        assessment = check_budget(BudgetOperation.RECOVERY_OPERATION, limits=progress.request.configuration.budget_limits,
                                  usage=progress.usage, elapsed_seconds=progress.usage.elapsed_seconds)  # fmt: skip
        proposed = (assessment if isinstance(assessment, BudgetGrant) else BudgetGrant(
            BudgetOperation.RECOVERY_OPERATION, replace(assessment.usage, recovery_operations=(
                assessment.usage.recovery_operations + 1))))  # fmt: skip
    authorization = DawRecoveryAuthorization(ids["authorization"], env["execution"], progress.request.policy.reference,
                                             target.value, proposed)  # fmt: skip
    verdict = gate_daw_restore(authorization, binding=session.binding, inspection=inspected.value,
                               profile=session.profile, recovery_command_id=restore_id,
                               control_state=control.value)  # fmt: skip
    progress, stop = record(progress, verdict, **rec)
    if stop:
        return (progress, (RollbackStatus.UNCERTAIN, "the restore gate verdict could not be recorded"))
    if isinstance(verdict, DawRestoreNoWrite):
        return (progress, None)
    if isinstance(verdict, DawRestoreRejected):
        status = RollbackStatus.FAILED if inspected.value.matches_binding else RollbackStatus.UNCERTAIN
        return (progress, (status, f"the restoration was refused by its gate ({verdict.code.value})"))
    if not isinstance(verdict, DawRestorePermitted):
        raise ValueError(f"unknown restore gate result {verdict!r}")

    def build(charged):
        grant = existing or BudgetGrant(BudgetOperation.RECOVERY_OPERATION, charged)
        return DawRestoreRequest(restore_id, replace(authorization, budget_grant=grant), session.binding,
                                 session.profile, inspected.value, own_lineage_only=True)  # fmt: skip

    progress, (intent_id, request, stop, _) = gate(
        progress, OperationKind.DAW_RECOVERY, build, None if existing else BudgetOperation.RECOVERY_OPERATION, **env
    )  # fmt: skip
    if intent_id is None or stop:
        if intent_id is None:
            return (progress, (RollbackStatus.UNCERTAIN, f"recovery not performed: the restoration was not dispatched "
                               f"({stop[0].value}: {stop[1]})"))  # fmt: skip
        return (progress, (RollbackStatus.UNCERTAIN, f"the restoration was not dispatched ({stop[0].value})"))
    progress = mark(progress, _UNRESOLVED)  # the reserved restoration is unresolved until it is confirmed
    restored = recovery_controller.restore(request)
    try:
        validate_daw_restore_outcome(request, restored)
        recorded = restored
    except ValueError as error:
        restored, recorded = None, _invalid(restored, error)
    # A refusal before reservation wrote and charged nothing (review fix 2026-11-20): the journal shows no charge.
    reserved = not isinstance(restored, DawRestoreRefused) or _charged(recovery_controller, restore_id)
    if isinstance(restored, DawRestoreConfirmed):  # classified before it is recorded
        facts.performed = True
        if entry is not DawRecoveryEntry.UNCERTAIN_WRITE or not facts.unresolved:
            progress = mark(progress, _BLOCKED if facts.blocked else _RESTORED_UNVERIFIED)
    elif reserved:
        facts.performed = True
        facts.unresolved = True
    else:
        progress = mark(progress, settled(facts))
    progress, stop = observed(progress, intent_id, OperationKind.DAW_RECOVERY, recorded, clock=clock, **rec)
    if not reserved:
        return (progress, (RollbackStatus.UNCERTAIN, f"recovery withheld: the restoration was refused before its "
                           f"reservation ({restored.explanation}); nothing was written or charged"))  # fmt: skip
    if stop:
        return (progress, (RollbackStatus.UNCERTAIN, "the restoration's outcome could not be recorded"))
    charges = recovery_controller.control_state()
    if not isinstance(charges, Success):
        facts.contract = "the recovery charge could not be checked"
        return (progress, (RollbackStatus.UNCERTAIN, facts.contract))
    matching = [c for c in charges.value.recovery_charges if c.recovery_command_id == restore_id]
    if len(matching) != 1 or matching[0].budget_grant != request.authorization.budget_grant:
        facts.contract = "the journal's recovery charge disagrees with the run's grant"
        return (progress, (RollbackStatus.UNCERTAIN, facts.contract))
    if isinstance(restored, DawRestoreConfirmed):
        return (progress, restored)
    # 3'. a restoration that was not confirmed: one reconciliation, and a resolution only where its finding permits
    progress, finding = _reconcile(progress, ids["reconcile_restore"], restore_id, session, recovery_controller,
                                   facts, env=env, clock=clock, rec=rec)  # fmt: skip
    kind = None if finding is None else {
        "at_commanded": DawResolutionKind.APPLIED_AS_COMMANDED,
        "at_recorded_start": DawResolutionKind.NOT_APPLIED_OR_REVERTED,
    }.get(finding.finding.value)  # fmt: skip
    if kind is not None:
        remaining = entry is DawRecoveryEntry.UNCERTAIN_WRITE and _root_open(progress, root)
        progress, _ = _resolve(progress, ids["resolve_restore"], ids["resolve_restore_authorization"], restore_id,
                               kind, finding.reconciliation_id, recovery_controller, facts, remaining=remaining,
                               env=env, clock=clock, rec=rec)  # fmt: skip
    return (progress, None if not facts.unresolved else (RollbackStatus.UNCERTAIN, "the restoration was not "
                                                         "confirmed and stays unresolved"))  # fmt: skip


def _root_open(progress, root):
    """Whether this run's trace shows the uncertain root still unresolved (no recorded resolution of it)."""
    for event in progress.known:
        outcome = getattr(event.payload, "outcome", None)
        if isinstance(outcome, DawResolutionRecorded) and outcome.subject_command_id == root:
            return False
    return True


def _evidence(progress, root, restore_id, apply, saved, restoration, recovery_controller, facts, *, env, clock, rec):
    """(progress, evidence | None, (known_miss, explanation)). Only the final fresh reading can be evidence."""
    query = DawLineageQuery(root, True)
    progress, (intent_id, _, stop, _) = gate(progress, OperationKind.DAW_STATE, lambda c: query, None, **env)
    if stop:
        return (progress, None, (False, f"the final recovery snapshot was not taken ({stop[0].value})"))
    result = recovery_controller.recovery_snapshot(root)
    try:
        validate_daw_recovery_snapshot(root, result)
        outcome = result.value if isinstance(result, Success) else result
    except ValueError as error:
        result, outcome = None, _invalid(result, error)
    progress, stop = observed(progress, intent_id, OperationKind.DAW_STATE, outcome, clock=clock, **rec)
    if not isinstance(result, Success) or stop:
        return (progress, None, (False, "the final recovery snapshot failed"))
    snapshot = result.value
    lineage, inspection = snapshot.lineage, snapshot.inspection
    facts.unresolved, facts.blocked = bool(lineage.unresolved), lineage.conversion_blocked
    if lineage.unresolved:
        return (progress, None, (False, "a command is still unresolved, so no fader evidence exists"))
    if not lineage.own_latest() or lineage.apply != apply:
        return (progress, None, (False, "another command followed the recovery, so no fader evidence exists"))
    if (
        lineage.binding != saved.binding
        or not inspection.matches_binding
        or (inspection.build != inspection.request.profile.build)
    ):
        return (progress, None, (False, "the final reading is not of the saved fader's complete binding"))
    reading, target = inspection.fader, saved.confirmed_fader
    restored = lineage.restoration(restore_id)
    if restoration is not None:
        if restored is None or not isinstance(restored.outcome, DawRestoreConfirmed):
            return (progress, None, (False, "the confirmed restoration is not in the journal's lineage"))
        if not _same_reading(restored.outcome.after, reading):
            return (progress, None, (True, "the fader moved after the confirmed restoration: the final fresh reading "
                                     "differs from the restoration's reading"))  # fmt: skip
        if restored.reserved_seq != lineage.reservation_watermark:
            return (progress, None, (False, "the restoration is not the latest write reservation"))
        evidence = DawRollbackFaderEvidence(saved.binding, target, DawRollbackEvidenceKind.EXACT_RESTORE, restore_id,
                                            reading, lineage.reservation_watermark, restored.reserved_seq)  # fmt: skip
    elif restored is not None:  # a restoration was dispatched but never confirmed: never evidence
        known = not _same_reading(reading, target)
        return (progress, None, (known, "the restoration was not confirmed, so the fader reading is not evidence"))
    else:
        evidence = DawRollbackFaderEvidence(saved.binding, target, DawRollbackEvidenceKind.EXACT_OBSERVATION,
                                            progress.known[-1].event_id.value, reading,
                                            lineage.reservation_watermark, lineage.ledger_watermark)  # fmt: skip
    problem = daw_rollback_evidence_problem(evidence)
    if problem is not None:
        known = not _same_reading(reading, target)
        return (progress, None, (known, f"the final fresh reading is not exact rollback evidence: {problem}"))
    return (progress, evidence, None)


def _reason(entry, original, facts, status, iterate):
    """The terminal reason by precedence (ADR-038): unresolved, blocked, not performed, failed, uncertain, cause."""
    if facts.unresolved:
        return (_UNRESOLVED, "a DAW command stays unresolved in the FL journal")
    if facts.blocked:
        return (_BLOCKED, "the session's fader conversion is blocked")
    if not facts.performed and status is not RollbackStatus.VERIFIED:
        if original[0] is TerminationReason.DAW_CONTINUITY_FAILED:
            return original
        return (_NOT_PERFORMED, "recovery of the DAW write was not performed")
    if facts.contract is not None:
        return (TerminationReason.RUNTIME_CONTRACT_FAILED, facts.contract)
    if status is RollbackStatus.FAILED:
        return (TerminationReason.RESTORATION_FAILED, "the selected state was not re-established exactly")
    if status is RollbackStatus.UNCERTAIN:
        return (TerminationReason.RESTORATION_UNCERTAIN, "re-establishing the selected state is not established")
    return None if iterate else original


def _finish(progress, ctx, entry, original, facts, status, explanation, evidence, retained, *, expected, receipt,
            candidate, verification, bundles, stage, iterate, identities, journal, on_progress):  # fmt: skip
    """Record the rollback (on the attempt, or standalone without a receipt), the feedback, and choose the stop."""
    rec = dict(identities=identities, journal=journal, on_progress=on_progress)
    verified = status is RollbackStatus.VERIFIED
    reason = _reason(entry, original, facts, status, iterate)
    if reason is None:
        stop = None
    elif reason == original:
        stop = (original[0], f"{original[1]}; recovery: {explanation}")
    else:
        stop = (reason[0], f"{reason[1]} ({original[0].value}: {original[1]}); recovery: {explanation}")
    if verified:
        pending = _BLOCKED if facts.blocked else None
        progress = replace(progress, pending_effect=pending)
    else:
        progress = replace(progress, verified=None, restored_last=False,
                           pending_effect=reason[0] if reason[0] in (_UNRESOLVED, _BLOCKED, _NOT_PERFORMED,
                                                                     _RESTORED_UNVERIFIED) else progress.pending_effect)  # fmt: skip
    on_progress(progress)
    rollback = RollbackReceipt(ctx.execution_id, ctx.before.audio.checkpoint_id, status,
                               ctx.before if verified else None, explanation, RollbackScope.SELECTED_STATE_WITH_FADER,
                               retained, tuple(evidence))  # fmt: skip
    if receipt is not None:
        attempt = CorrectionAttempt(ctx.attempt_id, progress.request.run_id, ctx.decision.decision_id,
                                    ctx.action.action_id, ctx.before, receipt, candidate, verification, rollback,
                                    AttemptDisposition.ROLLED_BACK if verified else AttemptDisposition.UNRESOLVED)  # fmt: skip
        progress, record_stop = record(progress, attempt, **rec)
    else:
        progress, record_stop = record(progress, DawRollbackRecord(progress.request.run_id, ctx.attempt_id,
                                                                   ctx.execution_id, ctx.before, rollback), **rec)  # fmt: skip
    code = (reason or original)[0].value
    text = stop[1] if stop else f"{original[1]}; recovery: {explanation}"
    if receipt is not None:
        execution = ExecutionStatus.CANDIDATE_READY
    elif entry is DawRecoveryEntry.UNCERTAIN_WRITE:
        execution = ExecutionStatus.UNCERTAIN
    else:
        execution = ExecutionStatus.FAILED
    if verified and execution is not ExecutionStatus.UNCERTAIN:
        kept = verification if execution is ExecutionStatus.CANDIDATE_READY else None
        outcome = ActionFeedback(ctx.action, ctx.attempt_id, ctx.execution_id, ActionFeedbackDisposition.ROLLED_BACK,
                                 FeedbackStage.RECOVERY, execution, kept, RollbackStatus.VERIFIED, code, text,
                                 copied(kept, bundles))  # fmt: skip
        selected = ...
    else:
        unresolved_stage = stage if stage in (FeedbackStage.VERIFICATION, FeedbackStage.ACCEPTANCE) else (
            FeedbackStage.RECOVERY)  # fmt: skip
        outcome = ActionFeedback(ctx.action, ctx.attempt_id, ctx.execution_id, ActionFeedbackDisposition.UNRESOLVED,
                                 unresolved_stage, execution, None, status, code, text, ())  # fmt: skip
        selected = None
    progress, feedback_stop = add_feedback(progress, ctx.call_id, ctx.before, outcome,
                                           decision_id=ctx.decision.decision_id,
                                           candidate=ctx.candidate_revision if receipt is not None else None,
                                           selected=selected, **rec)  # fmt: skip
    if stop is None:
        return (progress, record_stop or feedback_stop)
    return (progress, stop)
