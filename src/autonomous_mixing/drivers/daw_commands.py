"""The operator DAW commands behind ``autonomous-mixing daw`` (Increment 6, ADR-040).

``status`` and recovery phase 1 open both stores read-only (no lock, no write) and never open a MIDI connection.
Recovery phase 2 takes ownership of the run store and the FL journal, rebuilds the view from the owned stores, refuses
unless its fingerprint equals the one the operator authorized, and performs exactly the chosen shown option. Nothing
is adopted or restored automatically. Autonomous runs (``start``, ``continue``) are unavailable: no decision maker or
diagnosis source is implemented.
"""

import hashlib

from autonomous_mixing.adapters.fl_studio.journal import JournalError
from autonomous_mixing.adapters.journal.record_codec import dumps, encode
from autonomous_mixing.adapters.journal.sqlite_run import RunStoreBusy, RunStoreError, SqliteRunJournal
from autonomous_mixing.adapters.rendering.state_integrity import FileStateIntegrityInspector
from autonomous_mixing.core.loop.operator_recovery import orphan_facts, orphan_view, perform, run_view
from autonomous_mixing.core.loop.restart import classify_run, restart_facts
from autonomous_mixing.core.records.coordinator_runtime import RunRequest
from autonomous_mixing.core.records.daw_control import DawApplyRequest
from autonomous_mixing.core.records.results import Success
from autonomous_mixing.core.records.run_report import RunSuspended
from autonomous_mixing.core.records.run_restart import DawRestartFacts, OperatorRecoveryView
from autonomous_mixing.core.records.run_trace import DispatchIntent
from autonomous_mixing.drivers.daw_runtime import DawToolConfig, SystemClock, UuidIdentitySource, open_fl


class RecoveryRefused(Exception):
    """A phase-2 authorization that cannot be honoured; nothing was done."""


class StoreInUse(Exception):
    """Another process owns the store; a read-only view could not read it."""


def fingerprint(view: OperatorRecoveryView) -> str:
    """SHA-256 of the canonical encoding of everything phase 1 shows; the operator's authorization binds to it."""
    return hashlib.sha256(dumps(encode(view)).encode("utf-8")).hexdigest()


def _reading(reading):
    return None if reading is None else {"normalized": reading.normalized, "db": reading.db}


def _fl_facts(snapshot, config, *, read_only, transport_factory=None):
    try:
        journal, controller = open_fl(config, read_only=read_only, transport_factory=transport_factory)
    except JournalError as error:
        if read_only:
            return None, None, None, str(error)
        raise StoreInUse(f"the FL command journal is unavailable: {error}") from error
    if snapshot is not None:
        facts = restart_facts(snapshot, controller)
    else:
        facts = orphan_facts(controller, _explained(config))
    return journal, controller, facts, None


def _explained(config: DawToolConfig) -> frozenset[str]:
    """Every apply command ID any run store in the directory records (read-only). A store that cannot be read makes
    orphan analysis impossible: refused, failing closed, so a run's root is never mistaken for an orphan."""
    explained = set()
    for path in sorted(config.run_store_dir.glob("*.sqlite")):
        try:
            store = SqliteRunJournal.open_read_only(path)
        except RunStoreError as error:
            raise RecoveryRefused(
                f"run store {path.name} cannot be read, so orphans cannot be established: {error}"
            ) from error
        try:
            snapshot = store.snapshot()
        finally:
            store.close()
        if not isinstance(snapshot, Success):
            raise RecoveryRefused(f"run store {path.name} cannot be read, so orphans cannot be established")
        explained |= {e.payload.request.command_id for e in snapshot.value.events
                      if isinstance(e.payload, DispatchIntent) and isinstance(e.payload.request, DawApplyRequest)}  # fmt: skip
    return frozenset(explained)


def _open_store(config, run_id, *, read_only):
    path = config.store_path(run_id)
    try:
        return (SqliteRunJournal.open_read_only if read_only else SqliteRunJournal.open)(path)
    except RunStoreBusy as error:
        raise StoreInUse(f"run {run_id} is in use by another process") from error
    except RunStoreError as error:
        raise RecoveryRefused(f"run store cannot be read: {error}") from error


def _status_of(config: DawToolConfig, run_id: str) -> dict:
    store = _open_store(config, run_id, read_only=True)
    try:
        snapshot = store.snapshot().value
    finally:
        store.close()
    request = (
        snapshot.events[0].payload if snapshot.events and isinstance(snapshot.events[0].payload, RunRequest) else None
    )
    if request is not None and request.provider_binding is not None:
        # R5A has no operational composition/configuration for opening all provenance stores. Do not infer
        # resumability or verified termination from run/FL records alone; status stays read-only, without MIDI.
        return {
            "run_id": snapshot.run_id.value,
            "durability": snapshot.durability.value,
            "classification": "provider_evidence_unavailable",
            "explanation": "provider-run provenance was not verified by this operator configuration; no continuation is authorized",
            "authoritative": {
                "revision": snapshot.accepted.state.revision.value,
                "sequence": snapshot.accepted.sequence,
            },
        }
    journal, controller, facts, fl_problem = _fl_facts(snapshot, config, read_only=True)
    try:
        latest = controller.latest_anchor() if controller is not None else None
    finally:
        if journal is not None:
            journal.close()
    result = classify_run(snapshot, facts)
    request = snapshot.events[0].payload if snapshot.events and isinstance(snapshot.events[0].payload,
                                                                           RunRequest) else None  # fmt: skip
    shown = {
        "run_id": str(snapshot.run_id.value),
        "durability": snapshot.durability.value,
        "classification": "suspended_resumable" if isinstance(result, RunSuspended) else result.code.value,
        "explanation": ("suspended at its checkpoint, awaiting the operator's export; continuing needs the "
                        "unavailable decision maker" if isinstance(result, RunSuspended) else result.explanation),
        "authoritative": {"revision": snapshot.accepted.state.revision.value, "sequence": snapshot.accepted.sequence},
        "usage": dict(vars(snapshot.events[-1].usage)) if snapshot.events else None,
        "limits": dict(vars(request.configuration.budget_limits)) if request is not None else None,
        "fl": {
            "available": facts is not None and facts.available,
            "problem": fl_problem,
            "unresolved": [c.command_id for c in facts.control_state.unresolved] if facts and facts.control_state
            else [],
            "blocked_conversions": sorted(f"{c.name}@{c.version}" for c in facts.control_state.blocked_conversions)
            if facts and facts.control_state else [],
            "latest_anchor": latest.value.anchor.source_id if isinstance(latest, Success) and latest.value.anchor
            else None,
        },
    }  # fmt: skip
    if isinstance(result, RunSuspended):
        awaiting = result.checkpoint_event.payload.awaiting
        shown["awaiting_export"] = {"export_id": awaiting.request.export_id, "workspace": awaiting.workspace_directory,
                                    "files": [o.file_name for o in awaiting.request.outputs]}  # fmt: skip
    return shown


def status(config: DawToolConfig, run_id: str | None) -> list[dict]:
    """Read-only: one run's status, or one line per run store in the directory (a busy store shows as in use)."""
    run_ids = [run_id] if run_id is not None else sorted(p.stem for p in config.run_store_dir.glob("*.sqlite"))
    shown = []
    for name in run_ids:
        try:
            shown.append(_status_of(config, name))
        except StoreInUse as error:
            shown.append({"run_id": name, "classification": "in_use", "explanation": str(error)})
        except RecoveryRefused as error:
            shown.append(
                {
                    "run_id": name,
                    "classification": "unsupported" if "legacy/unsupported" in str(error) else "unavailable",
                    "explanation": str(error),
                }
            )
        except RunStoreError as error:
            shown.append({"run_id": name, "classification": "unreadable", "explanation": str(error)})
    return shown


def _view(config, run_id, *, read_only, transport_factory=None):
    """(view or suspension, fingerprint, store, FL journal, controller); the caller closes what it receives."""
    store = _open_store(config, run_id, read_only=read_only) if run_id is not None else None
    try:
        snapshot = store.snapshot().value if store is not None else None
        journal, controller, facts, problem = _fl_facts(snapshot, config, read_only=read_only,
                                                        transport_factory=transport_factory)  # fmt: skip
        if facts is None:
            facts = DawRestartFacts(None, (), (), False)
        view = run_view(snapshot, facts) if snapshot is not None else orphan_view(facts)
    except BaseException:
        if store is not None:
            store.close()
        raise
    return view, store, snapshot, journal, controller


def recover_view(config: DawToolConfig, run_id: str | None) -> dict:
    """Phase 1, read-only: the interrupted state, the proposed target, the permitted options and their fingerprint."""
    view, store, snapshot, journal, _ = _view(config, run_id, read_only=True)
    for item in (store, journal):
        if item is not None:
            item.close()
    if isinstance(view, RunSuspended):
        return {"run": run_id, "classification": "suspended_resumable", "options": [],
                "explanation": "resumable: it continues through 'daw continue', which is unavailable without a "
                               "decision maker; nothing to recover"}  # fmt: skip
    return {
        "run": run_id,
        "classification": view.run.code.value if view.run is not None else None,
        "explanation": view.run.explanation if view.run is not None else "FL commands no run explains",
        "authoritative": ({"revision": view.run.authoritative.state.revision.value,
                           "sequence": view.run.authoritative.sequence} if view.run is not None else None),
        "unresolved": [c.command_id for c in view.control_state.unresolved] if view.control_state else [],
        "proposed_target": _reading(view.proposed_target),
        "saved_target": _reading(view.saved_target),
        "exact": view.exact,
        "options": [{"operation": o.operation.value, "subject_command_id": o.subject_command_id,
                     "resolution_kind": o.resolution_kind.value if o.resolution_kind else None,
                     "evidence_id": o.evidence_id, "explanation": o.explanation} for o in view.options],
        "fingerprint": fingerprint(view),
    }  # fmt: skip


def recover_apply(config: DawToolConfig, run_id: str | None, view_fingerprint: str, option_index: int,
                  operator: str, *, transport_factory=None):  # fmt: skip
    """Phase 2: take ownership, rebuild the view, require the authorized fingerprint, perform exactly one option."""
    view, store, snapshot, journal, controller = _view(config, run_id, read_only=False,
                                                       transport_factory=transport_factory)  # fmt: skip
    try:
        if isinstance(view, RunSuspended):
            raise RecoveryRefused("the run is resumable; there is nothing to recover")
        if fingerprint(view) != view_fingerprint:
            raise RecoveryRefused("the state changed since it was shown (fingerprint mismatch); show it again")
        if type(option_index) is not int or not 0 <= option_index < len(view.options):
            raise RecoveryRefused("the authorized option is not one the view shows")
        if store is not None and not store.try_acquire_exclusive():
            raise RecoveryRefused("the run is owned by another operation in this process")
        try:
            inspector = FileStateIntegrityInspector(dict(config.roots), max_file_bytes=config.max_file_bytes,
                                                    max_decoded_bytes=config.max_decoded_bytes)  # fmt: skip
            return perform(view, view.options[option_index], operator=operator, view_fingerprint=view_fingerprint,
                           snapshot=snapshot, journal=store, recovery_controller=controller,
                           daw_controller=controller, integrity_inspector=inspector,
                           identities=UuidIdentitySource(), clock=SystemClock())  # fmt: skip
        finally:
            if store is not None:
                store.release_exclusive()
    finally:
        for item in (store, journal):
            if item is not None:
                item.close()
