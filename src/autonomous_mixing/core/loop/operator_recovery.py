"""Operator-authorized recovery of interrupted DAW runs and of FL commands no run explains (Increment 6, ADR-040).

Phase 1 builds an ``OperatorRecoveryView`` from journal facts only (read-only; nothing is written or sent). Phase 2
performs exactly one shown option, after the caller has bound the operator's authorization to the fingerprint of
the view it shows: the authorization is recorded in the run's trace first, then the operation runs through the
existing contracts (reconciliation, the restore gate with ``own_lineage_only``, resolution, integrity inspection),
each recorded as an intent and its observation. ``CLOSE`` records a terminal snapshot of facts only: the
authoritative reference is the committed acceptance head, the final state is UNKNOWN and nothing is restored.
Nothing here adopts a state, restores anything automatically, clears a conversion block or resubmits an acceptance.
"""

from dataclasses import replace

from autonomous_mixing.core.admission.daw import gate_daw_restore, operator_recovery_options
from autonomous_mixing.core.loop.restart import classify_run
from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource, RunClock
from autonomous_mixing.core.ports.daw import DawParameterController, validate_daw_inspection
from autonomous_mixing.core.ports.daw_recovery import (
    DawRecoveryController,
    validate_daw_control_state,
    validate_daw_reconciliation,
    validate_daw_resolution,
    validate_daw_restore_outcome,
)
from autonomous_mixing.core.ports.integrity import StateIntegrityInspector, validate_state_integrity_response
from autonomous_mixing.core.ports.run_journal import RunJournal
from autonomous_mixing.core.records.budgets import BudgetGrant, BudgetOperation, BudgetUsage
from autonomous_mixing.core.records.coordinator_runtime import ClockReading, RunRequest
from autonomous_mixing.core.records.daw_control import DawApplyRequest, DawInspectRequest
from autonomous_mixing.core.records.daw_recovery import (
    DawReconcileRequest,
    DawReconciliation,
    DawRecoveryAuthorization,
    DawResolutionAuthorization,
    DawResolutionRequest,
    DawRestorePermitted,
)
from autonomous_mixing.core.records.identifiers import RunEventId, VersionRef
from autonomous_mixing.core.records.integrity import ArtifactRootBinding, StateIntegrityRequest
from autonomous_mixing.core.records.results import Success
from autonomous_mixing.core.records.run import _REASON_OUTCOMES, FinalProjectState, RunRecord, RunState
from autonomous_mixing.core.records.run_report import RunSuspended
from autonomous_mixing.core.records.run_restart import (
    DawRestartFacts,
    OperatorOperation,
    OperatorRecoveryAuthorization,
    OperatorRecoveryOption,
    OperatorRecoveryView,
    RunInterruptionCode,
)
from autonomous_mixing.core.records.run_trace import (
    DispatchIntent,
    OperationKind,
    OperationObserved,
    RunJournalSnapshot,
    RunTraceEvent,
    TerminalSnapshot,
)
from autonomous_mixing.core.records.termination import TerminationReason

C = RunInterruptionCode
_POSSIBLY_WRITTEN = frozenset({C.INTERRUPTED_WRITE_UNRESOLVED, C.INTERRUPTED_AFTER_WRITE, C.INTERRUPTED_BEFORE_SUSPENSION,
                               C.CONTINUATION_INTERRUPTED, C.INTERRUPTED_RECOVERY, C.SUSPENDED_DAW_STATE_UNEXPLAINED,
                               C.INTERRUPTED_BEFORE_ACCEPTANCE_COMMIT})  # fmt: skip


def _reconciliations(snapshot: RunJournalSnapshot) -> tuple[DawReconciliation, ...]:
    return tuple(e.payload.outcome for e in snapshot.events
                 if isinstance(e.payload, OperationObserved) and isinstance(e.payload.outcome, DawReconciliation))  # fmt: skip


def _applies(snapshot: RunJournalSnapshot) -> list[RunTraceEvent]:
    return [e for e in snapshot.events
            if isinstance(e.payload, DispatchIntent) and isinstance(e.payload.request, DawApplyRequest)]  # fmt: skip


def run_view(snapshot: RunJournalSnapshot, facts: DawRestartFacts) -> OperatorRecoveryView | RunSuspended:
    """Phase 1 for a stored run (read-only): the classification, the targets and the permitted options.

    A resumable suspension is returned as such: it continues through ``daw continue``, not through recovery.
    """
    classified = classify_run(snapshot, facts)
    if isinstance(classified, RunSuspended):
        return classified
    request = snapshot.events[0].payload if snapshot.events and isinstance(snapshot.events[0].payload,
                                                                           RunRequest) else None  # fmt: skip
    proposed = saved = None
    applies = _applies(snapshot)
    if request is not None and request.daw_session is not None:
        saved = snapshot.accepted.state.daw.track(request.daw_session.binding.track_id).confirmed_fader
        if applies:
            lineage = facts.lineage(applies[-1].payload.request.command_id)
            proposed = lineage.recorded_start if lineage is not None else None
    options = operator_recovery_options(classified, facts, _reconciliations(snapshot), _accepted(snapshot))
    return OperatorRecoveryView(classified, facts.control_state, facts.lineages, proposed, saved, options)


def _accepted(snapshot: RunJournalSnapshot) -> frozenset:
    """Executions a committed acceptance holds: their edits are authoritative and are never restored."""
    return frozenset(c.event.execution.execution_id for c in snapshot.acceptances)


def orphan_facts(recovery_controller: DawRecoveryController, explained: frozenset[str]) -> DawRestartFacts:
    """FL facts for roots no run explains: every journaled apply root (pending, uncertain, confirmed or off target)
    that no run store records, with its lineage (review fix 2026-11-20: not only unresolved ones). ``explained`` holds
    every apply command ID the run stores record; a run's root, accepted or not, is never an orphan."""
    control = recovery_controller.control_state()
    try:
        validate_daw_control_state(control)
        state = control.value if isinstance(control, Success) else None
    except ValueError:
        state = None
    roots = recovery_controller.apply_roots()
    available = state is not None and isinstance(roots, Success)
    lineages, unknown = [], []
    for root in roots.value if isinstance(roots, Success) else ():
        if root in explained:
            continue
        found = recovery_controller.recovery_lineage(root)
        if isinstance(found, Success):
            lineages.append(found.value)
        else:
            unknown.append(root)
    return DawRestartFacts(state, tuple(lineages), tuple(unknown), available)


def orphan_view(facts: DawRestartFacts) -> OperatorRecoveryView:
    """Phase 1 for FL roots no run explains (read-only); the proposed target is the latest such root's journaled
    start when its lineage is the latest."""
    latest = [lineage for lineage in facts.lineages if lineage.own_latest()]
    proposed = latest[-1].recorded_start if latest else None
    options = operator_recovery_options(None, facts, ())
    return OperatorRecoveryView(None, facts.control_state, facts.lineages, proposed, None, options)


def perform(
    view: OperatorRecoveryView,
    option: OperatorRecoveryOption,
    *,
    operator: str,
    view_fingerprint: str,
    snapshot: RunJournalSnapshot | None,
    journal: RunJournal | None,
    recovery_controller: DawRecoveryController,
    daw_controller: DawParameterController | None,
    integrity_inspector: StateIntegrityInspector | None,
    identities: IdentitySource,
    clock: RunClock,
) -> object:
    """Phase 2: exactly one shown option, after the caller verified the operator's fingerprint of ``view``.

    For a run, the authorization and every effect are recorded in its trace (the caller owns the store). For orphans
    (``snapshot`` None) the FL journal records the effect and its authorization; the operator is the ``authorized_by``.
    Returns the operation's validated outcome (or the recorded terminal snapshot for ``CLOSE``).
    """
    if option not in view.options:
        raise ValueError("the operator may authorize only an option the view shows")
    reading = clock.read()
    if not isinstance(reading, ClockReading):
        raise ValueError("the clock's reading is not a ClockReading")
    events = list(snapshot.events) if snapshot is not None else []

    def token(prefix):
        value = identities.next_token()
        if not isinstance(value, str) or not value.strip():
            raise ValueError("the identity source supplied an invalid token")
        return f"{prefix}-{value}"

    def append(payload, usage=None):
        if snapshot is None:
            return None
        last = events[-1]
        event = RunTraceEvent(RunEventId(token("op")), last.sequence + 1, snapshot.run_id, reading.utc,
                              usage or last.usage, payload)  # fmt: skip
        response = journal.append(event)
        if type(response).__name__ != "TraceAppended":
            raise ValueError(f"the run store did not record the operator's step: {response!r}")
        events.append(event)
        return event

    authorized_by = VersionRef("operator", operator)
    if snapshot is not None:
        append(OperatorRecoveryAuthorization(snapshot.run_id, operator, view_fingerprint, option, reading.utc))
    lineage = next((item for item in view.lineages if option.subject_command_id in item.members), None)
    ids = _attempt_ids(snapshot, lineage.root_command_id if lineage is not None else None)

    def dispatched(kind, request, call, validate, usage=None):
        """Intent, effect, validation, then the observation (a validation error is raised before recording)."""
        intent = append(DispatchIntent(kind, request, snapshot.accepted, *ids), usage) if snapshot else None
        outcome = call(request)
        validate(request, outcome)
        if intent is not None:
            append(OperationObserved(intent.event_id, kind, outcome.value if isinstance(outcome, Success) else outcome))
        return outcome

    if option.operation is OperatorOperation.CLOSE:
        return append(_closing(view, snapshot, reading))
    if option.operation is OperatorOperation.INSPECT_INTEGRITY:
        request = snapshot.events[0].payload
        state = snapshot.accepted.state
        roots = {b.artifact_id: b.root_key for b in request.root_bindings}
        bindings = tuple(ArtifactRootBinding(a.artifact_id, roots.get(a.artifact_id,
                                                                      request.daw_session.export_root_key))
                         for a in state.audio.artifacts)  # fmt: skip
        inspect = StateIntegrityRequest(state, bindings)
        intent = append(DispatchIntent(OperationKind.INSPECT_STATE, inspect, snapshot.accepted, None, None, None))
        report = integrity_inspector.inspect(inspect)
        validate_state_integrity_response(inspect, report)
        append(OperationObserved(intent.event_id, OperationKind.INSPECT_STATE, report))
        return report
    if lineage is None:
        raise ValueError("the option's command has no lineage in the shown facts")
    if option.operation is OperatorOperation.RECONCILE:
        request = DawReconcileRequest(token("opr"), option.subject_command_id, lineage.binding, lineage.profile)
        return dispatched(OperationKind.DAW_RECOVERY, request, recovery_controller.reconcile,
                          validate_daw_reconciliation)  # fmt: skip
    if option.operation is OperatorOperation.RESOLVE:
        request = DawResolutionRequest(token("opv"), option.subject_command_id, option.resolution_kind,
                                       option.evidence_id, DawResolutionAuthorization(token("opz"), authorized_by))  # fmt: skip
        return dispatched(OperationKind.DAW_RECOVERY, request, recovery_controller.resolve, validate_daw_resolution)
    return _restore(lineage, option, snapshot, events, recovery_controller, daw_controller, authorized_by, token,
                    dispatched)  # fmt: skip


def _restore(lineage, option, snapshot, events, recovery_controller, daw_controller, authorized_by, token, dispatched):
    if snapshot is not None and lineage.apply.execution_id in _accepted(snapshot):  # enforced again (review fix)
        raise ValueError("an accepted execution is never restored: its edit is the authoritative state")
    target = recovery_controller.restore_target(lineage.root_command_id)
    if not isinstance(target, Success):
        raise ValueError(f"the journaled restore target is unavailable: {target!r}")
    inspect_request = DawInspectRequest(lineage.binding, lineage.profile)
    inspected = daw_controller.inspect(inspect_request)
    validate_daw_inspection(inspect_request, inspected)
    if not isinstance(inspected, Success):
        raise ValueError(f"the fader could not be inspected: {inspected!r}")
    control = recovery_controller.control_state()
    validate_daw_control_state(control)
    if not isinstance(control, Success):
        raise ValueError("the DAW journal state is unavailable")
    usage = events[-1].usage if events else None
    charged = (replace(usage, recovery_operations=usage.recovery_operations + 1) if usage is not None
               else _orphan_usage())  # fmt: skip
    grant = BudgetGrant(BudgetOperation.RECOVERY_OPERATION, charged)  # H6: recorded, not limited by the run budget
    execution = lineage.apply.execution_id
    authorization = DawRecoveryAuthorization(token("opa"), execution, authorized_by, target.value, grant)
    verdict = gate_daw_restore(authorization, binding=lineage.binding, inspection=inspected.value,
                               profile=lineage.profile, recovery_command_id=token("ops"),
                               control_state=control.value, own_lineage_only=True)  # fmt: skip
    if not isinstance(verdict, DawRestorePermitted):
        return verdict  # NoWrite or Rejected: nothing is written or charged
    return dispatched(OperationKind.DAW_RECOVERY, verdict.request, recovery_controller.restore,
                      validate_daw_restore_outcome, usage=charged)  # fmt: skip


def _orphan_usage():
    """The charge an orphan restoration records in the FL journal (no run budget exists for it)."""
    return BudgetUsage(0, 0, 0, 1, 0.0)


def _attempt_ids(snapshot, root):
    """The call, attempt and execution IDs of the run's attempt that dispatched ``root`` (for recorded intents)."""
    if snapshot is not None and root is not None:
        for event in _applies(snapshot):
            if event.payload.request.command_id == root:
                return (event.payload.call_id, event.payload.attempt_id, event.payload.execution_id)
    return (None, None, None)


def _closing(view: OperatorRecoveryView, snapshot: RunJournalSnapshot, reading: ClockReading) -> TerminalSnapshot:
    """A terminal snapshot of facts only: the committed head is authoritative, the final state UNKNOWN."""
    request = snapshot.events[0].payload
    control = view.control_state
    code = view.run.code
    if control is not None and control.unresolved:
        reason = TerminationReason.DAW_COMMAND_UNRESOLVED
    elif (
        control is not None
        and request.daw_session is not None
        and (request.daw_session.profile.conversion in control.blocked_conversions)
    ):
        reason = TerminationReason.DAW_CONVERSION_BLOCKED
    elif code is C.INTERRUPTED_AFTER_ACCEPTANCE:
        reason = TerminationReason.ACCEPTED_STATE_INTEGRITY_UNCERTAIN
    elif code in _POSSIBLY_WRITTEN:
        reason = TerminationReason.REQUIRED_RECOVERY_NOT_PERFORMED
    else:
        reason = TerminationReason.RUNTIME_CONTRACT_FAILED
    explanation = (f"closed by the operator after a process interruption ({code.value}); recorded facts only, "
                   "nothing was restored and the final state is not established")  # fmt: skip
    record = RunRecord(snapshot.run_id, request.initial_state.project_id, request.configuration,
                       request.initial_state.revision, snapshot.events[0].created_at, RunState.FINISHED,
                       snapshot.events[-1].usage, _REASON_OUTCOMES[reason], FinalProjectState.UNKNOWN, reason,
                       explanation)  # fmt: skip
    return TerminalSnapshot(record, snapshot.accepted, None)
