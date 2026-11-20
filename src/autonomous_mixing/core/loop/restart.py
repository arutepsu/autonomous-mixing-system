"""Reopening a stored run after a process restart (coordinator-integration Increment 5, ADR-039).

``classify_run`` decides, with a fixed first-match precedence (plan amendment A2), whether a stored run may resume.
Only a run whose last event is its suspension checkpoint, with FL facts that explain it, resumes (through the
unchanged ``resume_daw`` admission); every other non-terminal run is classified and refused for explicit operator
recovery. Nothing here restores, adopts, resolves, commits or re-dispatches anything. The authoritative reference is
always the committed acceptance head; a recorded ``AcceptanceCommitted`` or a recorded, validated ``AcceptanceFound``
answer is the trace's evidence of an acceptance.
"""

from autonomous_mixing.core.loop.daw_recovery import recovery_ids
from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource, RunClock
from autonomous_mixing.core.ports.daw_recovery import (
    DawRecoveryController,
    validate_daw_control_state,
    validate_daw_recovery_lineage,
)
from autonomous_mixing.core.ports.run_journal import RunJournal, require_snapshot_durability
from autonomous_mixing.core.records.acceptance import (
    AcceptanceCommitted,
    AcceptanceEvent,
    AcceptanceFound,
    AcceptanceNotCommitted,
)
from autonomous_mixing.core.records.actions import RollbackScope
from autonomous_mixing.core.records.coordinator_runtime import ClockReading, RunRequest
from autonomous_mixing.core.records.daw_control import DawApplyRequest, DawWriteConfirmed, DawWriteOffTarget
from autonomous_mixing.core.records.daw_correction import (
    DawContinuationFinished,
    DawContinuationStarted,
    DawCorrectionSuspended,
)
from autonomous_mixing.core.records.daw_recovery import DawLineageQuery, DawRestoreRequest, DawRootStatus
from autonomous_mixing.core.records.daw_rollback import DawRollbackRecord
from autonomous_mixing.core.records.identifiers import RunEventId
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, Success
from autonomous_mixing.core.records.run import CorrectionAttempt
from autonomous_mixing.core.records.run_report import RunSuspended
from autonomous_mixing.core.records.run_restart import DawRestartFacts, RunInterrupted, RunInterruptionCode
from autonomous_mixing.core.records.run_trace import (
    DispatchIntent,
    OperationKind,
    OperationObserved,
    RunJournalSnapshot,
    RunTraceEvent,
    TerminalSnapshot,
)

C = RunInterruptionCode


def _closure(payload) -> bool:
    """A record that closes one DAW attempt's recovery (ADR-038): its rollback, on the attempt or standalone."""
    if isinstance(payload, DawRollbackRecord):
        return True
    return (
        isinstance(payload, CorrectionAttempt)
        and payload.rollback is not None
        and (payload.rollback.scope is RollbackScope.SELECTED_STATE_WITH_FADER)
    )


def _recovery_start(payload) -> bool:
    return (
        isinstance(payload, DispatchIntent)
        and payload.operation is OperationKind.DAW_STATE
        and isinstance(payload.request, DawLineageQuery)
        and not payload.request.fresh_inspection
    )


def classify_run(snapshot: RunJournalSnapshot, facts: DawRestartFacts | None) -> RunSuspended | RunInterrupted:
    """The first-match classification of a stored run (plan amendment A2); a ``RunSuspended`` only when resumable."""
    try:
        return _classify(snapshot, facts)
    except ValueError as error:  # fail closed: a structure no rule describes
        events = snapshot.events
        run_id = events[0].payload.run_id if events and isinstance(events[0].payload, RunRequest) else snapshot.run_id
        return RunInterrupted(run_id, C.UNCLASSIFIED, snapshot.accepted, (), (), f"unclassified stored run: {error}")


def _classify(snapshot: RunJournalSnapshot, facts: DawRestartFacts | None) -> RunSuspended | RunInterrupted:
    authoritative = snapshot.accepted
    events = tuple(e for e in snapshot.events if not isinstance(e.payload, RunInterrupted))

    def interrupted(code, explanation, evidence=(), commands=()):
        ids = tuple(dict.fromkeys(e.event_id for e in evidence))
        return RunInterrupted(snapshot.run_id, code, authoritative, ids, tuple(dict.fromkeys(commands)), explanation)

    # row 0: nothing to resume
    if not events or not isinstance(events[0].payload, RunRequest):
        return interrupted(C.NOT_STARTED, "the store holds no recorded run request")
    request = events[0].payload
    # row 1
    if isinstance(events[-1].payload, TerminalSnapshot):
        return interrupted(C.FINISHED, "the run is finished", (events[-1],))
    # row 2: a committed acceptance the trace does not record (AcceptanceCommitted, or a validated AcceptanceFound)
    recorded = set()
    for event in events:
        payload = event.payload
        if isinstance(payload, AcceptanceCommitted):
            recorded.add(payload.event.acceptance_id)
        elif isinstance(payload, OperationObserved) and isinstance(payload.outcome, AcceptanceFound):
            recorded.add(payload.outcome.event.acceptance_id)
    unrecorded = [c for c in snapshot.acceptances if c.event.acceptance_id not in recorded]
    if unrecorded:
        return interrupted(C.INTERRUPTED_AFTER_ACCEPTANCE, "an acceptance was committed but its outcome is not "
                           "recorded; the committed state is authoritative and is never restored")  # fmt: skip
    # row 3
    if request.daw_session is None:
        return interrupted(C.INTERRUPTED_OFFLINE, "an offline run cannot resume", (events[-1],))

    intents = {e.event_id: (i, e) for i, e in enumerate(events) if isinstance(e.payload, DispatchIntent)}
    observed = {e.payload.intent_id for e in events if isinstance(e.payload, OperationObserved)}
    open_intents = [(i, e) for event_id, (i, e) in intents.items() if event_id not in observed]

    def last_index(predicate):
        return max((i for i, e in enumerate(events) if predicate(e.payload)), default=-1)

    # row 4: recovery under way
    open_recovery = [e for _, e in open_intents if e.payload.operation is OperationKind.DAW_RECOVERY]
    started = last_index(_recovery_start)
    if open_recovery or (started >= 0 and last_index(_closure) < started):
        return interrupted(C.INTERRUPTED_RECOVERY, "recovery of a DAW attempt was interrupted",
                           open_recovery or (events[started],))  # fmt: skip
    # row 5: an acceptance event whose commit outcome is neither recorded nor in the acceptance table
    accepted_at = last_index(lambda p: isinstance(p, AcceptanceEvent))
    if accepted_at >= 0:
        acceptance_id = events[accepted_at].payload.acceptance_id
        committed = any(c.event.acceptance_id == acceptance_id for c in snapshot.acceptances)
        resolved = any(_answers(e.payload, acceptance_id) for e in events[accepted_at + 1 :])
        if not committed and not resolved:
            return interrupted(C.INTERRUPTED_BEFORE_ACCEPTANCE_COMMIT, "an acceptance commit was interrupted; the "
                               "acceptance table shows it absent", (events[accepted_at],))  # fmt: skip
    # row 6: an open write intent, by what the FL journal holds
    open_writes = [e for _, e in open_intents if e.payload.operation is OperationKind.DAW_APPLY]
    if open_writes:
        command = open_writes[-1].payload.request.command_id
        lineage = facts.lineage(command) if facts is not None else None
        if facts is not None and facts.available and command in facts.unknown_roots:
            code, what = C.INTERRUPTED_BEFORE_WRITE, "the FL journal holds no write for it"
        elif lineage is not None and lineage.status in (DawRootStatus.CONFIRMED, DawRootStatus.OFF_TARGET):
            code, what = C.INTERRUPTED_AFTER_WRITE, f"the FL journal holds a {lineage.status.value} write"
        else:
            code, what = C.INTERRUPTED_WRITE_UNRESOLVED, "the FL write is unresolved or its state is unavailable"
        return interrupted(code, f"a DAW write was interrupted: {what}", open_writes[-1:], (command,))
    # row 7: an observed write with neither a checkpoint nor a recovery closure after it
    written = last_index(lambda p: isinstance(p, OperationObserved) and p.operation is OperationKind.DAW_APPLY
                         and isinstance(p.outcome, (DawWriteConfirmed, DawWriteOffTarget)))  # fmt: skip
    if (
        written >= 0
        and last_index(lambda p: isinstance(p, DawCorrectionSuspended)) < written
        and (last_index(_closure) < written)
    ):
        return interrupted(C.INTERRUPTED_BEFORE_SUSPENSION, "a DAW write took effect but the run was not suspended",
                           (events[written],), (events[written].payload.outcome.command_id,))  # fmt: skip
    # row 8: a continuation without completion evidence
    begun = last_index(lambda p: isinstance(p, DawContinuationStarted))
    if begun >= 0 and last_index(lambda p: isinstance(p, (DawContinuationFinished, DawCorrectionSuspended))) < begun:
        return interrupted(C.CONTINUATION_INTERRUPTED, "a continuation was interrupted", (events[begun],))
    # row 9: suspended at a checkpoint
    if isinstance(events[-1].payload, DawCorrectionSuspended):
        return _suspended(snapshot, events, request, facts, interrupted)
    # rows 10 and 11: every write attempt is closed
    if any(isinstance(e.payload, DispatchIntent) and e.payload.operation is OperationKind.DAW_APPLY for e in events):
        return interrupted(C.INTERRUPTED_BETWEEN_ROUNDS, "the run was interrupted between decision rounds; nothing is "
                           "written since its last closed attempt", (events[-1],))  # fmt: skip
    return interrupted(C.INTERRUPTED_BEFORE_FIRST_WRITE, "the run was interrupted before its first DAW write intent",
                       (events[-1],))  # fmt: skip


def _answers(payload, acceptance_id) -> bool:
    """A recorded, definite commit outcome for ``acceptance_id``: committed, not committed, or an authority answer."""
    if isinstance(payload, AcceptanceCommitted):
        return payload.event.acceptance_id == acceptance_id
    if isinstance(payload, AcceptanceNotCommitted):
        return payload.acceptance_id == acceptance_id
    return isinstance(payload, OperationObserved) and payload.operation is OperationKind.QUERY_ACCEPTANCE


def _suspended(snapshot, events, request, facts, interrupted):
    checkpoint = events[-1]
    apply = checkpoint.payload.apply
    problem = None
    lineage = facts.lineage(apply.command_id) if facts is not None else None
    if facts is None or not facts.available or facts.control_state is None:
        problem = "the FL journal's facts are unavailable"
    elif facts.control_state.unresolved:
        problem = "an FL command is unresolved"
    elif lineage is None or lineage.status is not DawRootStatus.CONFIRMED or lineage.apply != apply:
        problem = "the FL journal does not hold the checkpoint's confirmed write"
    elif not lineage.own_latest() or lineage.restorations or lineage.reservation_watermark != lineage.root_seq:
        problem = "another FL command followed the checkpoint's write"
    else:
        problem = _unexplained_charge(events, facts)
    if problem is not None:
        return interrupted(C.SUSPENDED_DAW_STATE_UNEXPLAINED, f"the suspended run's DAW state is unexplained: {problem}",
                           (checkpoint,), (apply.command_id,))  # fmt: skip
    return RunSuspended(request, snapshot, checkpoint)


def _unexplained_charge(events, facts) -> str | None:
    """An FL recovery charge under one of this run's recovery IDs with no matching recorded restoration intent."""
    tokens = [
        e.payload.request.command_id[len("apply-"):]
        for e in events
        if isinstance(e.payload, DispatchIntent) and isinstance(e.payload.request, DawApplyRequest)
    ]  # fmt: skip
    ours = {value for token in tokens for value in _ids(token)}
    recorded = {
        e.payload.request.recovery_command_id: e.payload.request.authorization.budget_grant
        for e in events
        if isinstance(e.payload, DispatchIntent) and isinstance(e.payload.request, DawRestoreRequest)
    }  # fmt: skip
    for charge in facts.control_state.recovery_charges:
        if charge.recovery_command_id in ours and recorded.get(charge.recovery_command_id) != charge.budget_grant:
            return f"the FL recovery charge {charge.recovery_command_id} has no matching recorded restoration"
    return None


def _ids(token: str) -> tuple[str, ...]:
    try:
        return tuple(recovery_ids(token).values())
    except ValueError:
        return ()


def restart_facts(snapshot: RunJournalSnapshot, recovery_controller: DawRecoveryController) -> DawRestartFacts:
    """Journal-only FL facts for a stored DAW run: the control state and the lineage of every dispatched apply."""
    available = True
    control = recovery_controller.control_state()
    try:
        validate_daw_control_state(control)
        state = control.value if isinstance(control, Success) else None
    except ValueError:
        state = None
    available = available and state is not None
    lineages, unknown = [], []
    roots = dict.fromkeys(
        e.payload.request.command_id
        for e in snapshot.events
        if isinstance(e.payload, DispatchIntent) and isinstance(e.payload.request, DawApplyRequest)
    )  # fmt: skip
    for root in roots:
        found = recovery_controller.recovery_lineage(root)
        try:
            validate_daw_recovery_lineage(root, found)
        except ValueError:
            available = False
            continue
        if isinstance(found, Success):
            lineages.append(found.value)
        elif isinstance(found, PortFailure) and found.code is PortFailureCode.INVALID_INPUT:
            unknown.append(root)  # never reserved, or refused before any write: nothing written
        else:
            available = False
    return DawRestartFacts(state, tuple(lineages), tuple(unknown), available)


def reopen_daw_run(
    journal: RunJournal,
    recovery_controller: DawRecoveryController | None,
    *,
    identities: IdentitySource,
    clock: RunClock,
    record_interruption: bool = True,
) -> RunSuspended | RunInterrupted:
    """Classify the journal's stored run after a restart; a ``RunSuspended`` may then go to ``resume_daw``.

    A non-terminal, started run that may not resume is recorded as a ``RunInterrupted`` event (evidence for operator
    recovery, never a terminal snapshot) when the run's ownership can be taken. Nothing else is written anywhere.
    """
    opened = journal.snapshot()
    if not isinstance(opened, Success) or not isinstance(opened.value, RunJournalSnapshot):
        raise ValueError("the run store could not provide its trace")
    snapshot = opened.value
    require_snapshot_durability(journal, snapshot)
    daw = (
        bool(snapshot.events)
        and isinstance(snapshot.events[0].payload, RunRequest)
        and (snapshot.events[0].payload.daw_session is not None)
    )
    facts = restart_facts(snapshot, recovery_controller) if daw and recovery_controller is not None else None
    result = classify_run(snapshot, facts)
    if (
        record_interruption
        and isinstance(result, RunInterrupted)
        and result.code not in (C.NOT_STARTED, C.FINISHED)
        and journal.try_acquire_exclusive()
    ):
        try:
            _record(journal, snapshot, result, identities=identities, clock=clock)
        finally:
            journal.release_exclusive()
    return result


def _record(journal, snapshot, result: RunInterrupted, *, identities: IdentitySource, clock: RunClock) -> None:
    """Best effort: one ``RunInterrupted`` event after the stored trace; a failure leaves the store as it was."""
    token = identities.next_token()
    taken = {e.event_id.value for e in snapshot.events} | {c.event.acceptance_id.value for c in snapshot.acceptances}
    reading = clock.read()
    if not isinstance(token, str) or token in taken or not isinstance(reading, ClockReading):
        return
    last = snapshot.events[-1]
    try:
        event = RunTraceEvent(RunEventId(token), last.sequence + 1, snapshot.run_id, reading.utc, last.usage, result)
    except ValueError:
        return
    journal.append(event)
