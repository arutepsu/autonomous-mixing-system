"""FL parameter and recovery ports: intent before send, stable replay, explicit reconciliation, no audio authority."""

import json
import re
import threading
from collections.abc import Callable
from dataclasses import asdict, replace

from autonomous_mixing.adapters.fl_studio import protocol
from autonomous_mixing.adapters.fl_studio.exchange import exchange
from autonomous_mixing.adapters.fl_studio.journal import JournalError, JournalRefusal, SqliteCommandJournal
from autonomous_mixing.adapters.fl_studio.profile import is_supported_profile, make_apply_command, same_calibrated_scope
from autonomous_mixing.adapters.fl_studio.serialization import (
    _dumps,
    _plain,
    grant_json,
    load_apply_request,
    load_binding,
    load_execution_id,
    load_profile,
    request_json,
    restore_request_json,
)
from autonomous_mixing.adapters.fl_studio.transport import MidiTransport, TransportCleanupError
from autonomous_mixing.core.ports.daw import validate_daw_apply_outcome, validate_daw_inspection
from autonomous_mixing.core.ports.daw_recovery import (
    validate_daw_continuity_snapshot,
    validate_daw_latest_state,
    validate_daw_reconciliation,
    validate_daw_recovery_lineage,
    validate_daw_recovery_snapshot,
    validate_daw_resolution,
    validate_daw_restore_outcome,
    validate_daw_state_anchor,
)
from autonomous_mixing.core.records.daw_binding import DawFaderReading
from autonomous_mixing.core.records.daw_control import (
    DawApplyOutcome,
    DawApplyRequest,
    DawBuild,
    DawControlProfile,
    DawInspection,
    DawInspectRequest,
    DawRefusalCode,
    DawUncertainReason,
    DawWriteConfirmed,
    DawWriteOffTarget,
    DawWriteRefused,
    DawWriteUncertain,
)
from autonomous_mixing.core.records.daw_export import DawContinuitySnapshot, DawStateAnchor, DawStateBasis
from autonomous_mixing.core.records.daw_recovery import (
    DawControlState,
    DawReconcileFinding,
    DawReconcileRequest,
    DawReconciliation,
    DawRecoveryLineage,
    DawRecoverySnapshot,
    DawResolutionKind,
    DawResolutionOutcome,
    DawResolutionRecorded,
    DawResolutionRefusalCode,
    DawResolutionRefused,
    DawResolutionRequest,
    DawRestoreConfirmed,
    DawRestoreMismatch,
    DawRestoreOutcome,
    DawRestoreRefused,
    DawRestoreRequest,
    DawRestoreTarget,
    DawRestoreUncertain,
    classify_reconciliation,
)
from autonomous_mixing.core.records.invariants import require_finite, require_instance
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, PortResult, Success
from autonomous_mixing.core.records.run_restart import DawLatestState

_SOURCE_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
_REFUSALS = {
    "transport_playing": DawRefusalCode.TRANSPORT_PLAYING,
    "edit_unavailable": DawRefusalCode.EDIT_UNAVAILABLE,
    "start_gain": DawRefusalCode.START_GAIN,
    "range": DawRefusalCode.OUT_OF_RANGE,
    "uncalibrated_build": DawRefusalCode.UNCALIBRATED_BUILD,
    "calibration_api": DawRefusalCode.UNCALIBRATED_BUILD,  # HT01 pins API 45 with its build
    "uncalibrated_start": DawRefusalCode.UNCALIBRATED_START,
    "cut_bounds": DawRefusalCode.BOUNDS,
    "bounds": DawRefusalCode.BOUNDS,
    "conversion": DawRefusalCode.CONVERSION,
    "conversion_mismatch": DawRefusalCode.CONVERSION_MISMATCH,
}


def _replay(outcome):
    written = (DawWriteConfirmed, DawWriteOffTarget, DawRestoreConfirmed, DawRestoreMismatch)
    return replace(outcome, replayed=True) if isinstance(outcome, written) else outcome


# Pre-reservation journal refusals of a restoration (nothing written, nothing charged) -> core refusal codes.
_RESTORE_JOURNAL_REFUSALS = {
    "duplicate_id_conflict": DawRefusalCode.DUPLICATE_ID_CONFLICT,
    "unresolved_other": DawRefusalCode.UNAVAILABLE,
    "foreign_history": DawRefusalCode.UNAVAILABLE,  # ADR-038 review fix: refused before reservation
    "agreement": DawRefusalCode.UNSUPPORTED,
    "unknown_root": DawRefusalCode.UNSUPPORTED,
    "no_target": DawRefusalCode.UNSUPPORTED,
}
_RESTORE_WIRE_REFUSALS = {
    **_REFUSALS,
    "conversion_scope": DawRefusalCode.UNSUPPORTED,
    "type": DawRefusalCode.UNSUPPORTED,
}


def _restore_refused(request, code, explanation):
    return DawRestoreRefused(request.recovery_command_id, code, explanation, None)


def _restore_uncertain(request, reason, explanation):
    return DawRestoreUncertain(request.recovery_command_id, reason, request.inspection.fader, None, explanation)


def _reading_dict(reading):
    return None if reading is None else {"normalized": reading.normalized, "db": reading.db}


def _uncertain(request, reason, explanation):
    return DawWriteUncertain(request.command_id, reason, request.inspection.fader, None, explanation)


def _refused(request, code, explanation):
    return DawWriteRefused(request.command_id, code, explanation, None)


class FlStudioParameterController:
    """Single-session adapter for ``DawParameterController`` and ``DawRecoveryController``.

    The caller owns journal lifetime and the authoritative session configuration. No CLI wiring or live deployment
    is supplied. Inspect never clears journal blocks; only ``resolve`` clears an unresolved command, and nothing
    clears a conversion block. A storage-failure fallback is replayed in memory only; after restart only committed
    journal evidence is authoritative. Cleanup failure poisons further dispatch. A restoration refused locally,
    before its reservation, is cached in memory only (nothing was written or charged, so it does not block); its
    ID is therefore not reserved across a restart.
    """

    def __init__(
        self,
        *,
        profile: DawControlProfile,
        journal: SqliteCommandJournal,
        transport_factory: Callable[[], MidiTransport],
        timeout_s: float,
        clock: Callable[[], float],
        next_inspect_id: Callable[[], str],
    ):
        require_instance(profile, DawControlProfile, "profile")
        require_instance(journal, SqliteCommandJournal, "journal")
        if not is_supported_profile(profile):
            raise ValueError("unsupported configured FL profile")
        # Enforced journal scope (plan 2026-11-26, D6): a journal recording another calibrated scope or conversion is
        # refused here, before any transport exists, so a profile never reuses another scope's authority, unresolved
        # state or conversion blocks. Reference labels aside; empty journals are compatible.
        if any(not same_calibrated_scope(recorded, profile) for recorded in journal.recorded_profiles()):
            raise JournalRefusal("incompatible_scope", "the FL journal records another calibrated scope or conversion")
        if require_finite(timeout_s, "timeout_s") <= 0:
            raise ValueError("timeout_s must be positive")
        if not all(callable(value) for value in (transport_factory, clock, next_inspect_id)):
            raise ValueError("transport_factory, clock and next_inspect_id must be callable")
        self.profile, self.journal = profile, journal
        self.transport_factory, self.timeout_s = transport_factory, timeout_s
        self.clock, self.next_inspect_id = clock, next_inspect_id
        self._lock, self._cache = threading.RLock(), {}
        self._poisoned = False
        self._retained_transports = []
        self.diagnostics = ()

    @property
    def blocked(self) -> bool:
        """Whether this controller refuses further device operations: a transport it could not release (kept in
        ``_retained_transports``) or an unrecorded journal state. The block is held in memory by this instance only;
        a caller that observes it must not continue with another controller in the same process (R5C C2 review)."""
        with self._lock:
            return self._poisoned

    def _open_transport(self):
        try:
            return self.transport_factory()
        except TransportCleanupError as error:
            self._poisoned = True
            self._retained_transports.append(error.transport)
            self.diagnostics = (self.diagnostics + (str(error)[:1024],))[-32:]
            raise

    def _close(self, transport):
        try:
            transport.close()
        except Exception as error:
            self._poisoned = True
            self._retained_transports.append(transport)
            self.diagnostics = (self.diagnostics + (str(error)[:1024],))[-32:]

    def inspect(self, request: DawInspectRequest) -> PortResult[DawInspection]:
        require_instance(request, DawInspectRequest, "request")
        with self._lock:
            if request.profile != self.profile:
                return PortFailure(PortFailureCode.UNSUPPORTED, "inspection uses another profile", False)
            transport = None
            try:
                command_id = self.next_inspect_id()
                if (
                    command_id in self._cache
                    or self.journal.lookup(command_id) is not None
                    or self.journal._is_inspect_id(command_id)
                ):
                    raise ValueError("inspection ID collides with an apply ID")
                command = protocol.inspect_command(
                    command_id, request.binding.project_title, request.binding.insert_index, request.binding.insert_name
                )
                if self._poisoned:
                    return PortFailure(
                        PortFailureCode.UNAVAILABLE, "native cleanup unresolved; cannot open transport", False
                    )
                self.journal._claim_inspect_id(command_id)
                transport = self._open_transport()
                result = exchange(command, transport=transport, timeout_s=self.timeout_s, clock=self.clock)
                self.diagnostics = result.diagnostics
                if result.status == "timeout":
                    return PortFailure(PortFailureCode.UNAVAILABLE, "inspect timed out", True)
                if result.status != "replied":
                    return PortFailure(PortFailureCode.INVALID_RESPONSE, "invalid inspect reply", False)
                message = json.loads(result.reply_json)
                if message["status"] not in ("ok", "identity_mismatch"):
                    code = (
                        PortFailureCode.UNSUPPORTED
                        if message["status"] == "unsupported"
                        else PortFailureCode.INVALID_RESPONSE
                    )
                    return PortFailure(code, "inspect refused: " + str(message.get("error")), False)
                observed = message["observed"]
                reading = DawFaderReading(**observed["gain"]) if observed["gain"] is not None else None
                inspection = DawInspection(
                    request,
                    DawBuild(observed["fl_version"], observed["api"]),
                    observed["project_title"],
                    observed["insert"]["index"],
                    observed["insert"]["name"],
                    reading,
                )
                response = Success(inspection)
                validate_daw_inspection(request, response)
                return response
            except (TransportCleanupError, JournalError) as error:
                return PortFailure(PortFailureCode.UNAVAILABLE, str(error) or "inspection cannot safely proceed", False)
            except OSError as error:
                return PortFailure(PortFailureCode.UNAVAILABLE, str(error) or "inspection unavailable", True)
            except Exception as error:
                return PortFailure(PortFailureCode.INVALID_RESPONSE, str(error) or "invalid inspection", False)
            finally:
                if transport is not None:
                    self._close(transport)

    def _remember_local(self, request, identity, outcome, command=None):
        # A minimal local descriptor is not a claim that a wire command was dispatched.
        descriptor = command or {"id": request.command_id, "type": "set_gain_db", "local_refusal": True}
        try:
            self.journal._remember_refusal(
                request.command_id, identity, json.dumps(descriptor, sort_keys=True), outcome
            )
        except (JournalError, ValueError):
            self._poisoned = True
            outcome = _refused(
                request, DawRefusalCode.UNAVAILABLE, "local refusal could not be journaled; no command sent"
            )
        self._cache[request.command_id] = (identity, outcome)
        return outcome

    def apply(self, request: DawApplyRequest) -> DawApplyOutcome:
        require_instance(request, DawApplyRequest, "request")
        with self._lock:
            identity = request_json(request)
            cached = self._cache.get(request.command_id)
            if cached is not None:
                if cached[0] != identity:
                    return _refused(request, DawRefusalCode.DUPLICATE_ID_CONFLICT, "command ID has another request")
                return _replay(cached[1])
            try:
                if self.journal._is_inspect_id(request.command_id):
                    return _refused(
                        request, DawRefusalCode.DUPLICATE_ID_CONFLICT, "command ID belongs to an inspection"
                    )
                entry = self.journal.lookup(request.command_id)
                if entry is not None:
                    if entry.request_json != identity:
                        return _refused(request, DawRefusalCode.DUPLICATE_ID_CONFLICT, "command ID has another request")
                    if entry.outcome is not None:
                        validate_daw_apply_outcome(request, entry.outcome)
                        self._cache[request.command_id] = (identity, entry.outcome)
                        return _replay(entry.outcome)
                    result = _uncertain(
                        request, DawUncertainReason.PENDING_UNRESOLVED, "committed intent has no outcome; never resent"
                    )
                    self.journal.finish(request.command_id, result)
                    self._cache[request.command_id] = (identity, result)
                    return result
                if self._poisoned or self.journal.pending_or_uncertain():
                    return self._remember_local(
                        request,
                        identity,
                        _refused(
                            request, DawRefusalCode.UNAVAILABLE, "controller poisoned or earlier write unresolved"
                        ),
                    )
                if request.profile.conversion in self.journal.blocked_conversions():
                    return self._remember_local(
                        request,
                        identity,
                        _refused(request, DawRefusalCode.CONVERSION, "conversion blocked by an off-target write"),
                    )
            except (JournalError, ValueError) as error:
                self._poisoned = True
                result = _uncertain(
                    request, DawUncertainReason.PENDING_UNRESOLVED, "cannot establish journal state: " + str(error)
                )
                self._cache[request.command_id] = (identity, result)
                return result
            try:
                command = make_apply_command(request)
            except protocol.ProtocolError as error:
                code = _REFUSALS.get(error.code, DawRefusalCode.UNSUPPORTED)
                return self._remember_local(request, identity, _refused(request, code, str(error)))
            except ValueError as error:
                return self._remember_local(
                    request, identity, _refused(request, DawRefusalCode.UNSUPPORTED, str(error))
                )
            if request.profile != self.profile:
                return self._remember_local(
                    request,
                    identity,
                    _refused(request, DawRefusalCode.UNSUPPORTED, "request uses another configured profile"),
                    command,
                )
            try:
                transport = self._open_transport()
            except Exception as error:
                return self._remember_local(
                    request,
                    identity,
                    _refused(request, DawRefusalCode.UNAVAILABLE, str(error) or "cannot open MIDI"),
                    command,
                )
            try:
                try:
                    self.journal.reserve(
                        request.command_id, identity, json.dumps(command, sort_keys=True, allow_nan=False)
                    )
                except (JournalError, ValueError) as error:
                    self._poisoned = True
                    result = _uncertain(
                        request, DawUncertainReason.DISPATCH_INTERRUPTED, "intent commit not established: " + str(error)
                    )
                    self._cache[request.command_id] = (identity, result)
                    return result
                try:
                    exchanged = exchange(command, transport=transport, timeout_s=self.timeout_s, clock=self.clock)
                    self.diagnostics = exchanged.diagnostics
                    if exchanged.status == "timeout":
                        result = _uncertain(request, DawUncertainReason.TIMEOUT, "reply deadline expired")
                    elif exchanged.status != "replied":
                        result = _uncertain(
                            request, DawUncertainReason.INVALID_REPLY, "malformed or inconsistent matching reply"
                        )
                    else:
                        try:
                            result = self._map_reply(request, command, json.loads(exchanged.reply_json))
                            validate_daw_apply_outcome(request, result)
                        except Exception as error:
                            result = _uncertain(
                                request, DawUncertainReason.INVALID_REPLY, str(error) or "invalid core response"
                            )
                except Exception as error:
                    result = _uncertain(
                        request, DawUncertainReason.DISPATCH_INTERRUPTED, str(error) or "exchange interrupted"
                    )
                try:
                    self.journal.finish(request.command_id, result)
                except (JournalError, ValueError) as error:
                    self._poisoned = True
                    result = _uncertain(
                        request,
                        DawUncertainReason.DISPATCH_INTERRUPTED,
                        "outcome commit not established: " + str(error),
                    )
                self._cache[request.command_id] = (identity, result)
                return result
            finally:
                self._close(transport)

    def _map_reply(self, request, command, message):
        protocol.validate_reply(message)
        if message["id"] != request.command_id or protocol.reply_consistency(command, message) is not None:
            raise ValueError("reply does not answer the dispatched command")
        status, error, observed = message["status"], message["error"], message["observed"]
        if status in ("applied", "applied_off_target"):
            if DawBuild(observed["fl_version"], observed["api"]) != self.profile.build:
                raise ValueError("written reply reports another build")
            before, after = DawFaderReading(**observed["before"]), DawFaderReading(**observed["after"])
            cls = DawWriteConfirmed if status == "applied" else DawWriteOffTarget
            return cls(
                request.command_id,
                before,
                after,
                command["target_normalized"],
                after.db - before.db,
                observed["replayed"],
            )
        if status == "identity_mismatch":
            return _refused(request, DawRefusalCode.IDENTITY_MISMATCH, str(error))
        if status == "precondition_failed" and error in _REFUSALS:
            return _refused(request, _REFUSALS[error], error)
        if status == "invalid_command" and error == "duplicate_id_conflict":
            return _refused(request, DawRefusalCode.DUPLICATE_ID_CONFLICT, error)
        if status == "invalid_command" and error in ("conversion", "conversion_mismatch", "bounds", "range"):
            return _refused(request, _REFUSALS[error], error)
        if status == "unsupported":
            return _refused(request, DawRefusalCode.UNSUPPORTED, str(error))
        if status == "error" and error == "api_too_old":
            return _refused(request, DawRefusalCode.API_TOO_OLD, error)
        if status == "uncertain":
            reason = (
                DawUncertainReason.READBACK_DISAGREES
                if error == "readback_disagrees"
                else DawUncertainReason.EXCEPTION_AFTER_WRITE
            )
            return DawWriteUncertain(
                request.command_id,
                reason,
                DawFaderReading(**observed["before"]) if observed["before"] else None,
                DawFaderReading(**observed["after"]) if observed["after"] else None,
                str(error),
            )
        raise ValueError("unrecognized reply; no pre-write guarantee")

    # --- recovery port -------------------------------------------------------------------------------------------

    def control_state(self) -> PortResult[DawControlState]:
        with self._lock:
            try:
                return Success(self.journal.control_state())
            except (JournalError, ValueError) as error:
                return PortFailure(PortFailureCode.UNAVAILABLE, str(error) or "journal state unavailable", False)

    def restore_target(self, source_command_id: str) -> PortResult[DawRestoreTarget]:
        with self._lock:
            try:
                basis, reading, conversion = self.journal.restore_target(source_command_id)
                return Success(DawRestoreTarget(source_command_id, basis, reading, conversion))
            except JournalRefusal as error:
                return PortFailure(PortFailureCode.INVALID_INPUT, str(error), False)
            except (JournalError, ValueError) as error:
                return PortFailure(PortFailureCode.UNAVAILABLE, str(error) or "journal unavailable", False)

    def _id_elsewhere(self, value):
        return self.journal._id_used(value) and self.journal.lookup(value) is None

    def restore(self, request: DawRestoreRequest) -> DawRestoreOutcome:
        require_instance(request, DawRestoreRequest, "request")
        with self._lock:
            identity = restore_request_json(request)
            command_id = request.recovery_command_id
            cached = self._cache.get(command_id)
            if cached is not None:
                if cached[0] != identity:
                    return _restore_refused(request, DawRefusalCode.DUPLICATE_ID_CONFLICT, "ID has another request")
                return _replay(cached[1])

            def local(outcome):  # refused before reservation: nothing written, nothing charged, not journaled
                self._cache[command_id] = (identity, outcome)
                return outcome

            try:
                if self._id_elsewhere(command_id):
                    return _restore_refused(
                        request, DawRefusalCode.DUPLICATE_ID_CONFLICT, "ID is used by another entry"
                    )
                entry = self.journal.lookup(command_id)
                if entry is not None:
                    if entry.request_json != identity:
                        return _restore_refused(request, DawRefusalCode.DUPLICATE_ID_CONFLICT, "ID has another request")
                    if entry.outcome is not None:
                        validate_daw_restore_outcome(request, entry.outcome)
                        self._cache[command_id] = (identity, entry.outcome)
                        return _replay(entry.outcome)
                    result = _restore_uncertain(
                        request, DawUncertainReason.PENDING_UNRESOLVED, "committed intent has no outcome; never resent"
                    )
                    self.journal.finish(command_id, result)
                    self._cache[command_id] = (identity, result)
                    return result
            except (JournalError, ValueError) as error:
                self._poisoned = True
                return _restore_uncertain(
                    request, DawUncertainReason.PENDING_UNRESOLVED, "cannot establish journal state: " + str(error)
                )
            if self._poisoned:
                return local(_restore_refused(request, DawRefusalCode.UNAVAILABLE, "controller poisoned"))
            if request.profile != self.profile:
                return local(_restore_refused(request, DawRefusalCode.UNSUPPORTED, "request uses another profile"))
            current, target = request.inspection.fader, request.target
            try:
                command = protocol.restore_gain_command(
                    command_id, request.profile.project_title, request.profile.insert_index,
                    request.profile.insert_name, current.normalized, current.db, target.normalized, target.db,
                )  # fmt: skip
            except protocol.ProtocolError as error:
                code = _RESTORE_WIRE_REFUSALS.get(error.code, DawRefusalCode.UNSUPPORTED)
                return local(_restore_refused(request, code, str(error)))
            try:
                transport = self._open_transport()
            except Exception as error:
                return local(_restore_refused(request, DawRefusalCode.UNAVAILABLE, str(error) or "cannot open MIDI"))
            try:
                try:
                    self.journal.reserve_restore(
                        command_id, identity, json.dumps(command, sort_keys=True, allow_nan=False),
                        authorization_id=request.authorization.authorization_id,
                        grant_json=grant_json(request.authorization.budget_grant),
                    )  # fmt: skip
                except JournalRefusal as error:
                    code = _RESTORE_JOURNAL_REFUSALS.get(error.code, DawRefusalCode.UNSUPPORTED)
                    return local(_restore_refused(request, code, str(error)))
                except (JournalError, ValueError) as error:
                    self._poisoned = True
                    result = _restore_uncertain(
                        request, DawUncertainReason.DISPATCH_INTERRUPTED, "intent commit not established: " + str(error)
                    )
                    self._cache[command_id] = (identity, result)
                    return result
                try:
                    exchanged = exchange(command, transport=transport, timeout_s=self.timeout_s, clock=self.clock)
                    self.diagnostics = exchanged.diagnostics
                    if exchanged.status == "timeout":
                        result = _restore_uncertain(request, DawUncertainReason.TIMEOUT, "reply deadline expired")
                    elif exchanged.status != "replied":
                        result = _restore_uncertain(
                            request, DawUncertainReason.INVALID_REPLY, "malformed or inconsistent matching reply"
                        )
                    else:
                        try:
                            result = self._map_restore_reply(request, command, json.loads(exchanged.reply_json))
                            validate_daw_restore_outcome(request, result)
                        except Exception as error:
                            result = _restore_uncertain(
                                request, DawUncertainReason.INVALID_REPLY, str(error) or "invalid core response"
                            )
                except Exception as error:
                    result = _restore_uncertain(
                        request, DawUncertainReason.DISPATCH_INTERRUPTED, str(error) or "exchange interrupted"
                    )
                try:
                    self.journal.finish(command_id, result)
                except (JournalError, ValueError) as error:
                    self._poisoned = True
                    result = _restore_uncertain(
                        request,
                        DawUncertainReason.DISPATCH_INTERRUPTED,
                        "outcome commit not established: " + str(error),
                    )
                self._cache[command_id] = (identity, result)
                return result
            finally:
                self._close(transport)

    def _map_restore_reply(self, request, command, message):
        protocol.validate_reply(message)
        if message["id"] != request.recovery_command_id or protocol.reply_consistency(command, message) is not None:
            raise ValueError("reply does not answer the dispatched restoration")
        status, error, observed = message["status"], message["error"], message["observed"]
        if status == "applied":
            if DawBuild(observed["fl_version"], observed["api"]) != self.profile.build:
                raise ValueError("written reply reports another build")
            before, after = DawFaderReading(**observed["before"]), DawFaderReading(**observed["after"])
            exact = after.normalized == request.target.normalized and after.db == request.target.db
            cls = DawRestoreConfirmed if exact else DawRestoreMismatch
            return cls(request.recovery_command_id, before, after, request.target, observed["replayed"])
        if status == "identity_mismatch":
            return _restore_refused(request, DawRefusalCode.IDENTITY_MISMATCH, str(error))
        if status == "precondition_failed" and error in _REFUSALS:
            return _restore_refused(request, _REFUSALS[error], error)
        if status == "invalid_command" and error == "duplicate_id_conflict":
            return _restore_refused(request, DawRefusalCode.DUPLICATE_ID_CONFLICT, error)
        if status == "invalid_command" and error in _RESTORE_WIRE_REFUSALS:
            # includes "type" from a v1 device script, which validates before any FL call: no write happened
            return _restore_refused(request, _RESTORE_WIRE_REFUSALS[error], error)
        if status == "unsupported":
            return _restore_refused(request, DawRefusalCode.UNSUPPORTED, str(error))
        if status == "error" and error == "api_too_old":
            return _restore_refused(request, DawRefusalCode.API_TOO_OLD, error)
        if status == "uncertain":
            reason = (
                DawUncertainReason.READBACK_DISAGREES
                if error == "readback_disagrees"
                else DawUncertainReason.EXCEPTION_AFTER_WRITE
            )
            return DawRestoreUncertain(
                request.recovery_command_id,
                reason,
                DawFaderReading(**observed["before"]) if observed["before"] else None,
                DawFaderReading(**observed["after"]) if observed["after"] else None,
                str(error),
            )
        raise ValueError("unrecognized reply; no pre-write guarantee")

    def reconcile(self, request: DawReconcileRequest) -> PortResult[DawReconciliation]:
        require_instance(request, DawReconcileRequest, "request")
        with self._lock:
            identity = _dumps(_plain(asdict(request)))
            try:
                prior = self.journal.ledger_entry("RECONCILED", request.reconciliation_id)
                if prior is not None:
                    seq, _, record = prior
                    if record.get("request") != identity:
                        return PortFailure(
                            PortFailureCode.INVALID_INPUT, "reconciliation ID has another request", False
                        )
                    result = Success(self._reconciliation_from_record(request, seq, record, replayed=True))
                    validate_daw_reconciliation(request, result)
                    return result
                if self.journal._id_used(request.reconciliation_id):
                    return PortFailure(
                        PortFailureCode.INVALID_INPUT, "reconciliation ID is used by another entry", False
                    )
                if request.profile != self.profile:
                    return PortFailure(PortFailureCode.UNSUPPORTED, "reconciliation uses another profile", False)
                state = self.journal.control_state()
                subject = next((item for item in state.unresolved if item.command_id == request.subject_command_id),
                               None)  # fmt: skip
                if subject is None:
                    return PortFailure(PortFailureCode.INVALID_INPUT, "the subject is not unresolved", False)
                binding, profile = self.journal.lineage_scope(subject.command_id)
                if (binding, profile) != (_plain(asdict(request.binding)), _plain(asdict(request.profile))):
                    return PortFailure(
                        PortFailureCode.INVALID_INPUT, "request disagrees with the subject's lineage", False
                    )
            except (JournalError, ValueError) as error:
                return PortFailure(PortFailureCode.UNAVAILABLE, str(error) or "journal unavailable", False)
            read = self.inspect(DawInspectRequest(request.binding, request.profile))
            inspection = read.value if isinstance(read, Success) else None
            verified = inspection is not None and inspection.matches_binding and inspection.build == self.profile.build
            if verified:
                finding, matches_start, matches_commanded = classify_reconciliation(
                    self.profile, subject.recorded_start, subject.commanded, inspection.fader
                )
            else:
                finding, matches_start, matches_commanded = DawReconcileFinding.IDENTITY_UNVERIFIED, False, False
            observed = None
            if inspection is not None:
                observed = {"build": _plain(asdict(inspection.build)), "project_title": inspection.project_title,
                            "insert_index": inspection.insert_index, "insert_name": inspection.insert_name,
                            "fader": _reading_dict(inspection.fader)}  # fmt: skip
            record = {
                "request": identity, "finding": finding.value, "matches_start": matches_start,
                "matches_commanded": matches_commanded, "observed": observed,
                "reading": _reading_dict(inspection.fader) if verified else None,
                "recorded_start": _reading_dict(subject.recorded_start), "commanded": _reading_dict(subject.commanded),
            }  # fmt: skip
            try:
                seq = self.journal.record_reconciliation(request.reconciliation_id, subject.command_id, record)
            except JournalRefusal as error:
                return PortFailure(PortFailureCode.INVALID_INPUT, str(error), False)
            except (JournalError, ValueError) as error:
                self._poisoned = True
                return PortFailure(PortFailureCode.UNAVAILABLE, "reconciliation not recorded: " + str(error), False)
            result = Success(self._reconciliation_from_record(request, seq, record, replayed=False))
            validate_daw_reconciliation(request, result)
            return result

    @staticmethod
    def _reconciliation_from_record(request, seq, record, *, replayed):
        observed, inspection = record["observed"], None
        if observed is not None:
            fader = observed["fader"]
            inspection = DawInspection(
                DawInspectRequest(request.binding, request.profile),
                DawBuild(observed["build"]["product_version"], observed["build"]["scripting_api"]),
                observed["project_title"],
                observed["insert_index"],
                observed["insert_name"],
                None if fader is None else DawFaderReading(**fader),
            )
        return DawReconciliation(
            request.reconciliation_id, request.subject_command_id, seq, DawReconcileFinding(record["finding"]),
            inspection, DawFaderReading(**record["recorded_start"]), DawFaderReading(**record["commanded"]),
            record["matches_start"], record["matches_commanded"], replayed,
        )  # fmt: skip

    def resolve(self, request: DawResolutionRequest) -> DawResolutionOutcome:
        require_instance(request, DawResolutionRequest, "request")
        with self._lock:
            identity = _dumps(_plain(asdict(request)))
            try:
                prior = self.journal.ledger_entry("RESOLVED", request.resolution_id)
                if prior is not None:
                    seq, subject, record = prior
                    if record.get("request") != identity:
                        return DawResolutionRefused(request.resolution_id, DawResolutionRefusalCode.DUPLICATE_ID_CONFLICT,
                                                    "resolution ID has another request")  # fmt: skip
                    outcome = DawResolutionRecorded(
                        request.resolution_id, subject, record["root"], DawResolutionKind(record["kind"]),
                        DawFaderReading(**record["reading"]), record["evidence_id"], seq, True,
                    )  # fmt: skip
                    validate_daw_resolution(request, outcome)
                    return outcome
                seq, root, reading = self.journal.record_resolution(
                    request.resolution_id, request.subject_command_id, request.kind.value, request.evidence_id,
                    authorization_id=request.authorization.authorization_id, record={"request": identity},
                )  # fmt: skip
            except JournalRefusal as error:
                return DawResolutionRefused(request.resolution_id, DawResolutionRefusalCode(error.code), str(error))
            except (JournalError, ValueError) as error:
                self._poisoned = True
                return DawResolutionRefused(request.resolution_id, DawResolutionRefusalCode.UNAVAILABLE,
                                            "resolution not established: " + str(error))  # fmt: skip
            outcome = DawResolutionRecorded(request.resolution_id, request.subject_command_id, root, request.kind,
                                            reading, request.evidence_id, seq, False)  # fmt: skip
            validate_daw_resolution(request, outcome)
            return outcome

    # --- export anchors and coherent continuity snapshots (export plan 2026-11-18, revision 3) ---------------------

    def _anchor(self, source_id):
        data = self.journal.state_anchor(source_id)
        return DawStateAnchor(
            source_id, DawStateBasis(data["basis"]), data["source_seq"], load_execution_id(data["execution_id"]),
            load_binding(data["binding"]), load_profile(data["profile"]), DawFaderReading(**data["reading"]),
            data["reservation_watermark"], data["resolution_watermark"], data["ledger_watermark"],
            None if data["apply_json"] is None else load_apply_request(data["apply_json"]),
        )  # fmt: skip

    def state_anchor(self, source_id: str) -> PortResult[DawStateAnchor]:
        """Journal-backed source evidence only: nothing is sent to the DAW."""
        with self._lock:
            if not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id):
                return PortFailure(PortFailureCode.INVALID_INPUT, "invalid anchor source ID", False)
            try:
                result = Success(self._anchor(source_id))
            except JournalRefusal as error:
                return PortFailure(PortFailureCode.INVALID_INPUT, str(error), False)
            except (JournalError, ValueError) as error:
                return PortFailure(PortFailureCode.UNAVAILABLE, str(error) or "journal unavailable", False)
            validate_daw_state_anchor(source_id, result)
            return result

    def continuity_snapshot(self, source_id: str) -> PortResult[DawContinuitySnapshot]:
        """Anchor and control state, one fresh inspection, then both again, all under the controller lock.

        A snapshot is returned only when the two journal queries agree exactly. A ledger entry of any kind appended
        in between (by any writer of this journal) yields a retryable ``PortFailure``, never a mixed snapshot.
        """
        with self._lock:
            opened = self.state_anchor(source_id)
            if isinstance(opened, PortFailure):
                return opened
            try:
                opening_state = self.journal.control_state()
            except (JournalError, ValueError) as error:
                return PortFailure(PortFailureCode.UNAVAILABLE, str(error) or "journal unavailable", False)
            anchor = opened.value
            read = self.inspect(DawInspectRequest(anchor.binding, anchor.profile))
            if isinstance(read, PortFailure):
                return read
            try:
                closing, closing_state = self._anchor(source_id), self.journal.control_state()
            except JournalRefusal as error:
                return PortFailure(PortFailureCode.UNAVAILABLE, "the journal changed during the capture: " + str(error),
                                   True)  # fmt: skip
            except (JournalError, ValueError) as error:
                return PortFailure(PortFailureCode.UNAVAILABLE, str(error) or "journal unavailable", False)
            if (closing, closing_state) != (anchor, opening_state):
                return PortFailure(PortFailureCode.UNAVAILABLE, "the journal changed during the capture", True)
            result = Success(DawContinuitySnapshot(closing, read.value, closing_state))
            validate_daw_continuity_snapshot(source_id, result)
            return result

    # --- recovery lineage and coherent recovery snapshots (coordinator Increment 4, ADR-038) ---------------------

    def recovery_lineage(self, root_command_id: str) -> PortResult[DawRecoveryLineage]:
        """Journal-backed facts about one root apply, whatever its outcome; nothing is sent to the DAW."""
        with self._lock:
            if not isinstance(root_command_id, str) or not _SOURCE_ID.fullmatch(root_command_id):
                return PortFailure(PortFailureCode.INVALID_INPUT, "invalid root command ID", False)
            try:
                result = Success(self.journal.recovery_lineage(root_command_id))
            except JournalRefusal as error:
                return PortFailure(PortFailureCode.INVALID_INPUT, str(error), False)
            except (JournalError, ValueError) as error:
                return PortFailure(PortFailureCode.UNAVAILABLE, str(error) or "journal unavailable", False)
            validate_daw_recovery_lineage(root_command_id, result)
            return result

    def recovery_snapshot(self, root_command_id: str) -> PortResult[DawRecoverySnapshot]:
        """Lineage and control state, one fresh inspection, then both again, all under the controller lock.

        As ``continuity_snapshot``: a snapshot is returned only when the two journal reads agree exactly; a ledger entry
        of any kind appended in between yields a retryable ``PortFailure``, never a mixed snapshot.
        """
        with self._lock:
            opened = self.recovery_lineage(root_command_id)
            if isinstance(opened, PortFailure):
                return opened
            try:
                opening_state = self.journal.control_state()
            except (JournalError, ValueError) as error:
                return PortFailure(PortFailureCode.UNAVAILABLE, str(error) or "journal unavailable", False)
            lineage = opened.value
            read = self.inspect(DawInspectRequest(lineage.binding, lineage.profile))
            if isinstance(read, PortFailure):
                return read
            try:
                closing = self.journal.recovery_lineage(root_command_id)
                closing_state = self.journal.control_state()
            except JournalRefusal as error:
                return PortFailure(PortFailureCode.UNAVAILABLE, "the journal changed during the capture: " + str(error),
                                   True)  # fmt: skip
            except (JournalError, ValueError) as error:
                return PortFailure(PortFailureCode.UNAVAILABLE, str(error) or "journal unavailable", False)
            if (closing, closing_state) != (lineage, opening_state):
                return PortFailure(PortFailureCode.UNAVAILABLE, "the journal changed during the capture", True)
            result = Success(DawRecoverySnapshot(closing, read.value, closing_state))
            validate_daw_recovery_snapshot(root_command_id, result)
            return result

    # --- the latest state-establishing event (Increment 5, ADR-039: the run-start check) -----------------------------

    def latest_anchor(self) -> PortResult[DawLatestState]:
        """The latest state-establishing FL event as an anchor (``None`` for none), journal-only; never sends."""
        with self._lock:
            try:
                source, top = self.journal.latest_source()
                result = Success(DawLatestState(None if source is None else self._anchor(source), top))
            except JournalRefusal as error:
                return PortFailure(PortFailureCode.INVALID_INPUT, str(error), False)
            except (JournalError, ValueError) as error:
                return PortFailure(PortFailureCode.UNAVAILABLE, str(error) or "journal unavailable", False)
            validate_daw_latest_state(result)
            return result

    def apply_roots(self) -> PortResult[tuple[str, ...]]:
        """Every journaled apply that may have written, journal-only (ADR-040 orphan discovery); never sends."""
        with self._lock:
            try:
                return Success(self.journal.apply_roots())
            except (JournalError, ValueError) as error:
                return PortFailure(PortFailureCode.UNAVAILABLE, str(error) or "journal unavailable", False)
