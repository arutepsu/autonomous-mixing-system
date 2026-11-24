"""The operator DAW commands behind ``autonomous-mixing daw`` (Increment 6, ADR-040).

``status`` and recovery phase 1 open both stores read-only (no lock, no write) and never open a MIDI connection.
Recovery phase 2 takes ownership of the run store and the FL journal, rebuilds the view from the owned stores, refuses
unless its fingerprint equals the one the operator authorized, and performs exactly the chosen shown option. Nothing
is adopted or restored automatically.

``start`` (R5C C2, ADR-043 Proposed) composes the existing ``DawAgentRuntime``: it checks the journal with the existing
run-start rule, inspects the DAW once, requires the exact first-session confirmation, runs to a recorded outcome or
suspension and describes it from the records. ``continue`` (R5C C3) reopens the recorded suspension with its recorded
provider identity, hashes only the declared export files after the importer's own file checks, and continues it with
the operator's attestation through the existing ``continue_export``.
"""

import hashlib
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from autonomous_mixing.adapters.fl_studio.journal import JournalError
from autonomous_mixing.adapters.journal.record_codec import dumps, encode
from autonomous_mixing.adapters.journal.sqlite_run import RunStoreBusy, RunStoreError, SqliteRunJournal
from autonomous_mixing.adapters.rendering.state_integrity import FileStateIntegrityInspector
from autonomous_mixing.core.admission.daw import daw_start_check, exact_fader
from autonomous_mixing.core.loop.operator_recovery import orphan_facts, orphan_view, perform, run_view
from autonomous_mixing.core.loop.restart import classify_run, restart_facts
from autonomous_mixing.core.ports.daw import validate_daw_inspection
from autonomous_mixing.core.ports.daw_recovery import validate_daw_control_state, validate_daw_latest_state
from autonomous_mixing.core.records.coordinator_runtime import RunRequest
from autonomous_mixing.core.records.daw_binding import DawFaderReading
from autonomous_mixing.core.records.daw_control import DawApplyRequest, DawInspection, DawInspectRequest
from autonomous_mixing.core.records.daw_correction import DawContinuationRefused
from autonomous_mixing.core.records.daw_export import DAW_EXPORT_REQUEST_FILE, DawExportManifest, DawImportRejected
from autonomous_mixing.core.records.identifiers import RunId, VersionRef
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, Success
from autonomous_mixing.core.records.run import FinalProjectState, RunOutcome
from autonomous_mixing.core.records.run_report import RunReport, RunSuspended
from autonomous_mixing.core.records.run_restart import DawRestartFacts, OperatorRecoveryView
from autonomous_mixing.core.records.run_trace import DispatchIntent, TraceAppended
from autonomous_mixing.core.records.termination import TerminationReason
from autonomous_mixing.drivers.daw_runtime import (
    ConfigError,
    DawRunConfig,
    DawToolConfig,
    SystemClock,
    UuidIdentitySource,
    interrupt_cancels,
    open_fl,
)


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


# --- R5C C2: daw start (ADR-043 Proposed; plan sections 6, 8 and C2.1-C2.9) -------------------------------------------

REFUSED_CLAUSE = "no provider call or fader write was made by this command"
# Pre-check controllers that could not release a MIDI transport. They hold the unreleased transport and their in-memory
# block for the rest of the process: neither is dropped, closed again or replaced by a fresh controller (review fix).
_UNRELEASED_CONTROLLERS: list = []
ATTESTATION_VERSION = "daw-cli-attestation-1"  # the version of the operator identity in a CLI-built manifest (D8)


def _refuse_if_blocked() -> None:
    """The process-local MIDI block shared by ``start`` and ``continue``: refuse before anything is opened."""
    from autonomous_mixing.drivers.agent_runtime import RuntimeRefusal

    if _UNRELEASED_CONTROLLERS:
        raise RuntimeRefusal(
            f"this process still holds {len(_UNRELEASED_CONTROLLERS)} unreleased MIDI transport(s) from an earlier "
            "command whose cleanup failed; no further run can start or continue in this process. Check the MIDI "
            "connection, then run the command again in a new process"
        )


def _retain_if_blocked(runtime) -> None:
    """Keep a runtime controller that could not release a transport for the rest of the process (C3, D-C3-4)."""
    controller = getattr(runtime, "controller", None)
    if controller is not None and controller.blocked and controller not in _UNRELEASED_CONTROLLERS:
        _UNRELEASED_CONTROLLERS.append(controller)


def _close(runtime) -> str | None:
    """Retain a blocked controller, then close the runtime; the type of a close failure, if any."""
    _retain_if_blocked(runtime)
    try:
        runtime.close()
    except Exception as error:
        return type(error).__name__
    return None


def _run_owned(runtime, run_id: str, clock, action):
    """Run ``action()`` (which may dispatch and write) and always close; exceptions become ``RunStopped``."""
    result = error_type = None
    last: tuple[str, ...] = ()
    try:
        try:
            with interrupt_cancels(clock) if isinstance(clock, SystemClock) else nullcontext():
                result = action()
        except Exception as error:
            error_type = type(error).__name__
            last = _last_recorded(runtime)
    finally:
        cleanup_type = _close(runtime)
    if error_type is not None:
        raise RunStopped(run_id, error_type, cleanup_type, last) from None
    return result, cleanup_type


_UNCERTAIN_REASONS = frozenset({TerminationReason.ACCEPTANCE_COMMIT_UNCERTAIN, TerminationReason.RESTORATION_UNCERTAIN,
                                TerminationReason.ACCEPTED_STATE_INTEGRITY_UNCERTAIN})  # fmt: skip


class StartRefused(Exception):
    """An unexpected failure before any dispatch or fader write (DAW pre-check or run assembly), reported by type only:
    its text could carry data from an external library. ``retained`` names runtime artifacts left for diagnosis."""

    def __init__(self, run_id: str, stage: str, error_type: str, retained: Path | None = None) -> None:
        super().__init__(f"{stage} failed ({error_type})")
        self.run_id, self.stage, self.error_type, self.retained = run_id, stage, error_type, retained


class RunStopped(Exception):
    """An exception after the run started: a decision may have been dispatched, the fader written or an acceptance
    recorded. Only exception types are kept; ``last`` holds the last recorded usage lines, if they could be read."""

    def __init__(self, run_id: str, error_type: str, cleanup_type: str | None, last: tuple[str, ...]) -> None:
        super().__init__(f"run {run_id} stopped ({error_type})")
        self.run_id, self.error_type, self.cleanup_type, self.last = run_id, error_type, cleanup_type, last


@dataclass(frozen=True)
class StartResult:
    """A recorded outcome of ``start`` and, if closing the runtime failed afterwards, that failure's type."""

    result: RunReport | RunSuspended
    cleanup_type: str | None


def _reading_text(reading) -> str:
    return f"normalized {reading.normalized!r}, dB {reading.db!r}"


def daw_precheck(config: DawToolConfig, request: RunRequest, accept: DawFaderReading | None, *,
                 fl_transport_factory=None) -> DawInspection:  # fmt: skip
    """S6: the existing journal start rule, one fresh inspection and the exact first-session confirmation.

    Writes only the controller's inspect-ID claim to the FL journal; never applies, restores, resolves or anchors.
    Raises ``RuntimeRefusal`` for DAW/journal state and ``ConfigError`` for a confirmation on an anchored journal.
    """
    from autonomous_mixing.drivers.agent_runtime import RuntimeRefusal

    session = request.daw_session
    binding, profile = session.binding, session.profile
    declared = request.initial_state.daw.track(binding.track_id).confirmed_fader
    try:
        journal, controller = open_fl(config, read_only=False, transport_factory=fl_transport_factory)
    except JournalError as error:
        raise RuntimeRefusal(f"the FL command journal is unavailable: {error}") from error
    try:
        control = controller.control_state()
        try:
            validate_daw_control_state(control)
        except ValueError as error:
            raise RuntimeRefusal(f"invalid FL control state: {error}") from error
        if not isinstance(control, Success):
            raise RuntimeRefusal(f"the FL control state is unavailable: {control.message}")
        latest = controller.latest_anchor()
        try:
            validate_daw_latest_state(latest)
        except ValueError as error:
            raise RuntimeRefusal(f"invalid latest FL state: {error}") from error
        if isinstance(latest, PortFailure) and latest.code is PortFailureCode.UNAVAILABLE:
            raise RuntimeRefusal(f"the latest FL state is unavailable: {latest.message}")
        latest_state = latest.value if isinstance(latest, Success) else None
        refused = daw_start_check(control.value, latest_state, binding=binding, profile=profile, confirmed=declared)
        if refused is not None:
            hint = (
                " (resolve with 'daw recover' first)"
                if refused.reason is TerminationReason.DAW_COMMAND_UNRESOLVED
                else ""
            )
            raise RuntimeRefusal(refused.explanation + hint)
        anchor = latest_state.anchor if latest_state is not None else None
        if anchor is not None and accept is not None:
            raise ConfigError("--accept-current-fader is only for an FL journal without a recorded fader state; "
                              "this journal records one, which the declared state must equal exactly")  # fmt: skip
        inspect_request = DawInspectRequest(binding, profile)
        inspected = controller.inspect(inspect_request)
        if controller.blocked:  # checked before the result: a failed inspection can also leave a transport held
            _UNRELEASED_CONTROLLERS.append(controller)
            raise RuntimeRefusal(
                "the FL controller could not release its MIDI transport after the pre-check inspection and now blocks "
                "further device operations; the run stops before runtime creation. This process keeps the unreleased "
                "transport until it exits; check the MIDI connection before starting again"
            )
        try:
            validate_daw_inspection(inspect_request, inspected)
        except ValueError as error:
            raise RuntimeRefusal(f"invalid fresh inspection: {error}") from error
        if not isinstance(inspected, Success):
            raise RuntimeRefusal(f"the fresh inspection failed: {inspected.message}")
        inspection = inspected.value
        observed = (f"inspected build {inspection.build.product_version} (API {inspection.build.scripting_api}), "
                    f"project {inspection.project_title!r}, insert {inspection.insert_index} "
                    f"{inspection.insert_name!r}")  # fmt: skip
        if not profile.covers(binding) or not inspection.matches_binding or inspection.build != profile.build:
            raise RuntimeRefusal(f"the DAW is not the calibrated build/project/insert of this run: {observed}")
        fresh = inspection.fader
        if fresh is None:
            raise RuntimeRefusal(f"the fresh inspection returned no fader reading: {observed}")
        if anchor is not None:
            if not exact_fader(fresh, anchor.reading):
                raise RuntimeRefusal(f"the live fader ({_reading_text(fresh)}) differs from its journaled state "
                                     f"({_reading_text(anchor.reading)}): the DAW state is unexplained")  # fmt: skip
            return inspection
        if accept is None:
            raise RuntimeRefusal(
                f"first session: the FL journal records no fader state. The fresh inspection reads "
                f"{_reading_text(fresh)}. If this is the intended starting state, run the same command again with "
                f"--accept-current-fader {fresh.normalized!r} {fresh.db!r}"
            )
        if not exact_fader(accept, fresh):
            raise RuntimeRefusal(f"the confirmation ({_reading_text(accept)}) differs from the fresh reading "
                                 f"({_reading_text(fresh)}); the declared state is {_reading_text(declared)}")  # fmt: skip
        if not exact_fader(declared, fresh):
            raise RuntimeRefusal(f"the declared starting fader ({_reading_text(declared)}) differs from the fresh "
                                 f"reading ({_reading_text(fresh)}); correct the run file's initial state")  # fmt: skip
        return inspection
    finally:
        journal.close()


def _usage_line(usage, limits) -> str:
    return (f"usage: decision calls {usage.decision_calls}/{limits.max_decision_calls}, attempts {usage.attempts}/"
            f"{limits.max_attempts}, recovery operations {usage.recovery_operations}/"
            f"{limits.max_recovery_operations}, DAW writes {usage.daw_writes}/{limits.max_daw_writes}, "
            f"elapsed {usage.elapsed_seconds:.3f} s of {limits.max_elapsed_seconds:g} s")  # fmt: skip


def _authority(reference) -> str:
    if reference is None:
        return "unknown"
    return f"{reference.state.revision.value} (sequence {reference.sequence})"


def _last_recorded(runtime) -> tuple[str, ...]:
    """Best-effort, read in-process after an exception: the last recorded usage and authority, labelled as such."""
    try:
        snapshot = runtime.journal.snapshot()
        if not isinstance(snapshot, Success) or not snapshot.value.events:
            return ()
        snapshot = snapshot.value
        limits = runtime.request.configuration.budget_limits
        return (f"last recorded {_usage_line(snapshot.events[-1].usage, limits)}",
                f"last recorded authoritative: {_authority(snapshot.accepted)}")  # fmt: skip
    except Exception:
        return ()


def start(config: DawToolConfig, run: DawRunConfig, run_id: str, accept: DawFaderReading | None, api_key: str, *,
          provider_transport_factory=None, fl_transport_factory=None, clock=None, identities=None) -> StartResult:  # fmt: skip
    """``daw start`` after the CLI has parsed arguments (S0), loaded both files once (S1-S2) and read the credential (S3).

    S4 no existing artifacts; S5 the provisional request and the pre-write check (``ConfigError``); S6 ``daw_precheck``;
    S7 ``DawAgentRuntime.create``; S8 ``start``; S9 ``close``. Refusals before S8 raise ``ConfigError``,
    ``RuntimeRefusal`` or ``StartRefused``; after S8 began, an exception becomes ``RunStopped``. A failure to close after
    a recorded outcome never hides that outcome.
    """
    from autonomous_mixing.drivers.agent_runtime import DawAgentRuntime, RuntimeRefusal

    require_instance(run, DawRunConfig, "run configuration")
    _refuse_if_blocked()  # process-local block: refuse before any store, MIDI transport, runtime or provider
    retained = config.run_store_dir / f"{run_id}.runtime"
    if config.store_path(run_id).exists() or retained.exists():
        raise RuntimeRefusal("run store or runtime artifacts already exist for this run ID; they are never overwritten "
                             "or reused: choose a new run ID")  # fmt: skip
    rid = RunId(run_id)
    provisional = run.provisional_request(rid, config)
    try:
        DawAgentRuntime.check(config=config, request=provisional, preparation=run.preparation)
    except ValueError as error:  # native-export metadata, baseline integrity/location: the declaration is invalid
        raise ConfigError(f"run: {error}") from error
    try:
        daw_precheck(config, provisional, accept, fl_transport_factory=fl_transport_factory)
    except (ConfigError, RuntimeRefusal):
        raise
    except Exception as error:
        raise StartRefused(run_id, "the DAW pre-check", type(error).__name__) from None
    clock = clock or SystemClock()
    identities = identities or UuidIdentitySource()
    try:
        runtime = DawAgentRuntime.create(
            config=config, run_id=rid, preparation=run.preparation, request_factory=run.request_factory(rid, config),
            provider_config=run.provider, api_key=api_key, max_evidence_bytes=run.max_evidence_bytes,
            max_audit_record_bytes=run.max_audit_record_bytes, known_alignments=run.known_alignments, clock=clock,
            identities=identities, provider_transport_factory=provider_transport_factory,
            fl_transport_factory=fl_transport_factory,
        )  # fmt: skip
    except (ConfigError, RuntimeRefusal):
        raise
    except Exception as error:
        raise StartRefused(run_id, "assembling the run", type(error).__name__,
                           retained if retained.exists() else None) from None  # fmt: skip
    result, cleanup_type = _run_owned(runtime, run_id, clock, runtime.start)
    return StartResult(result, cleanup_type)


def continue_run(config: DawToolConfig, run_id: str, export_id: str, operator: str, attestation: str, api_key: str,
                 *, provider_transport_factory=None, fl_transport_factory=None, clock=None,
                 identities=None) -> StartResult:  # fmt: skip
    """``daw continue`` after the CLI has validated its arguments, the tool configuration and the credential (K0-K1).

    K2 the process-local MIDI block; K3 both run artifacts exist; K4 ``reopen`` with the recorded provider identity
    (drift, evidence, manifest and classification refusals); K5 the export ID before any file is read; K6 safe
    digests of the declared outputs; K7 the operator's manifest; K8 ``continue_export``; K9 retain-if-blocked and close.
    Refusals before K8, and continuation refusals by admission, raise ``RuntimeRefusal`` (or ``StartRefused`` by type);
    an exception during K8 becomes ``RunStopped``. A cleanup failure after a recorded outcome keeps that outcome.
    """
    from autonomous_mixing.drivers.agent_runtime import DawAgentRuntime, RuntimeRefusal

    _refuse_if_blocked()
    if not config.store_path(run_id).is_file() or not (config.run_store_dir / f"{run_id}.runtime").is_dir():
        raise RuntimeRefusal(f"run {run_id} has no run store and runtime directory here; there is nothing to continue")
    clock = clock or SystemClock()
    identities = identities or UuidIdentitySource()
    try:
        runtime = DawAgentRuntime.reopen(config=config, run_id=RunId(run_id), api_key=api_key, clock=clock,
                                         identities=identities, provider_transport_factory=provider_transport_factory,
                                         fl_transport_factory=fl_transport_factory)  # fmt: skip
    except (ConfigError, RuntimeRefusal):
        raise
    except Exception as error:
        raise StartRefused(run_id, "reopening the run", type(error).__name__) from None
    try:
        suspended = runtime.suspended
        awaiting = suspended.checkpoint_event.payload.awaiting
        if export_id != awaiting.request.export_id:
            raise RuntimeRefusal(f"--export-id {export_id!r} is not the export this run awaits "
                                 f"({awaiting.request.export_id}); no file was read")  # fmt: skip
        digests = runtime.export_handoff.attestation_digests(awaiting)
        if isinstance(digests, DawImportRejected):
            raise RuntimeRefusal(f"{digests.code.value}: {digests.explanation}; nothing was recorded and the run "
                                 "still awaits this export")  # fmt: skip
        try:
            manifest = DawExportManifest(export_id, awaiting.request_fingerprint, digests,
                                         VersionRef(operator, ATTESTATION_VERSION), clock.read().utc, attestation)  # fmt: skip
        except ValueError as error:
            raise ConfigError(f"the operator attestation is invalid: {error}") from error
    except (ConfigError, RuntimeRefusal) as refusal:
        cleanup_type = _close(runtime)
        if cleanup_type is not None:
            raise RuntimeRefusal(f"{refusal} (cleanup failed: {cleanup_type})") from None
        raise
    except Exception as error:
        _close(runtime)
        raise StartRefused(run_id, "preparing the export manifest", type(error).__name__) from None
    result, cleanup_type = _run_owned(runtime, run_id, clock, lambda: runtime.continue_export(suspended, manifest))
    if isinstance(result, DawContinuationRefused):  # admission refused it without any effect
        message = f"{result.code.value}: {result.explanation}"
        raise RuntimeRefusal(message if cleanup_type is None else f"{message} (cleanup failed: {cleanup_type})")
    return StartResult(result, cleanup_type)


def _keyword(report: RunReport) -> str:
    run = report.final_run
    reason = run.termination_reason
    if reason is TerminationReason.CANDIDATE_CRITERIA_MET:
        return "ACCEPTED: the latest candidate met the declared criteria"
    if reason is TerminationReason.INITIAL_CRITERIA_MET:
        return "NO CHANGE NEEDED: the initial state already met the declared criteria"
    label = {RunOutcome.ABSTAINED: "ABSTAINED", RunOutcome.LIMIT_REACHED: "LIMIT REACHED",
             RunOutcome.CANCELLED: "CANCELLED"}.get(run.outcome, "FAILED")  # fmt: skip
    return f"{label}: {reason.value if reason is not None else 'unknown'}"


def _uncertainty(report: RunReport) -> list[str]:
    reasons = []
    if not isinstance(report.terminal_append, TraceAppended):
        reasons.append("the terminal record may not be durable")
    if report.unacknowledged_events:
        reasons.append(f"{len(report.unacknowledged_events)} unacknowledged recorded events")
    if report.final_run.termination_reason in _UNCERTAIN_REASONS:
        reasons.append(f"termination {report.final_run.termination_reason.value}")
    if report.final_run.final_project_state is FinalProjectState.UNKNOWN:
        reasons.append("final project state unknown")
    return reasons


def _instructions(suspended: RunSuspended, *, run_id: str, tool_path: str) -> list[str]:
    payload = suspended.checkpoint_event.payload
    awaiting, write = payload.awaiting, payload.write
    export = awaiting.request
    binding, timeline, format_ = export.binding, export.timeline, export.format
    lines = [
        "manual export (saving and exporting are manual; this system never saves or exports):",
        f"  1. FL Studio project {binding.project_title!r}, insert {binding.insert_index} {binding.insert_name!r}: "
        f"leave the fader where the run set it: normalized {write.after.normalized!r}, dB {write.after.db!r} "
        f"(command {write.command_id}).",
        f"  2. format {format_.container}/{format_.subtype}; sample rate {timeline.sample_rate_hz} Hz; start "
        f"{timeline.start_seconds!r} s; {timeline.frame_count} frames "
        f"({timeline.frame_count / timeline.sample_rate_hz:.6f} s); render spec "
        f"{export.render_spec.name}@{export.render_spec.version}.",
        f"  3. destination (export into exactly this folder): {awaiting.workspace_directory}",
        f"     do not delete {DAW_EXPORT_REQUEST_FILE} and add no other files.",
        f"  4. files to export (export ID {export.export_id}):",
    ]
    for output in export.outputs:
        tracks = ", ".join(track.value for track in output.source_track_ids)
        lines.append(f"     - {output.file_name}: {output.kind.value} of tracks {tracks}; {output.channel_count} "
                     f"channels; replaces {output.replaces.value}")  # fmt: skip
    lines += [
        "  5. Saving and exporting are manual. Save the project yourself if you want to keep this fader position.",
        "  6. next, after exporting:",
        f'     autonomous-mixing daw continue --config "{tool_path}" --run-id {run_id} --export-id '
        f'{export.export_id} --operator <NAME> --attestation "<what you exported>"',
    ]
    return lines


def describe_outcome(result: RunReport | RunSuspended, *, run_id: str, tool_path: str) -> str:
    """The first-line keyword, the recorded-state block read from the records and, for a suspension, the manual
    export instructions from the saved checkpoint. The keyword names the termination only, never the run's history."""
    limits = result.request.configuration.budget_limits
    if isinstance(result, RunSuspended):
        payload = result.checkpoint_event.payload
        lines = [f"SUSPENDED: awaiting your manual export (run {run_id})", "recorded state:",
                 f"  {_usage_line(result.checkpoint_event.usage, limits)}",
                 f"  acceptances: {len(result.trace.acceptances)}",
                 f"  authoritative: {_authority(payload.accepted)}",
                 f"  awaiting export of the write by command {payload.write.command_id}"]  # fmt: skip
        return "\n".join(lines + _instructions(result, run_id=run_id, tool_path=tool_path))
    run = result.final_run
    lines = [f"{_keyword(result)} (run {run_id})"]
    if run.termination_explanation:
        lines.append(f"  {run.termination_explanation}")
    if run.termination_reason is TerminationReason.CANDIDATE_CRITERIA_MET:
        lines.append("  engineering criteria only, not a quality judgement")
    lines += ["recorded state:", f"  {_usage_line(run.usage, limits)}",
              f"  acceptances: {len(result.trace.acceptances)}",
              f"  authoritative: {_authority(result.authoritative)}",
              f"  verified: {result.verified.state.revision.value if result.verified is not None else 'not verified'}",
              f"  final project state: {run.final_project_state.value if run.final_project_state else 'unknown'}"]  # fmt: skip
    uncertain = _uncertainty(result)
    if uncertain:
        lines.append(f"  uncertain: {'; '.join(uncertain)}: inspect with 'daw status'; use 'daw recover' if it reports "
                     "an interruption")  # fmt: skip
    return "\n".join(lines)
