"""Single-owner local SQLite command journal, separate from audio acceptance authority (schema v2).

Intent commits use DELETE/EXTRA. Durability relies on SQLite's VFS and honest local storage; subprocess tests
demonstrate process-crash persistence, not hardware power-loss guarantees.

Schema v2 (recovery plan 2026-11-18, ADR-032) adds one append-only ``ledger`` that orders every write reservation,
recovery budget charge, reconciliation and resolution in a single transactional sequence, and lineage columns on
``commands`` (``kind``, ``root_command_id``, ``attempt``). Ledger rows are only ever inserted. The one permitted
change to an existing command row is completion: filling a still-empty outcome, guarded in SQL. Everything that
blocks (unresolved commands, conversion blocks) is recomputed from all rows on every call. Conversion blocks have
no clearing transition in this version.
"""

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from autonomous_mixing.adapters.fl_studio.serialization import (
    load_apply_request,
    load_grant,
    load_outcome,
    outcome_json,
)
from autonomous_mixing.core.records.budgets import BudgetOperation
from autonomous_mixing.core.records.daw_binding import DawFaderReading
from autonomous_mixing.core.records.daw_control import (
    DawApplyOutcome,
    DawWriteConfirmed,
    DawWriteOffTarget,
    DawWriteRefused,
    DawWriteUncertain,
)
from autonomous_mixing.core.records.daw_recovery import (
    DawCommandKind,
    DawControlState,
    DawLedgerEntry,
    DawLedgerKind,
    DawLineageRestoration,
    DawRecoveryCharge,
    DawRecoveryLineage,
    DawRestoreConfirmed,
    DawRootStatus,
    DawTargetBasis,
    DawUnresolvedCommand,
)
from autonomous_mixing.core.records.identifiers import VersionRef

_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
_SCHEMA_VERSION = 2
# LOCAL_REFUSAL marks the only commands allowed to lack a reservation; RESOLUTION_AUTHORIZED registers a consumed
# resolution authorization ID in the shared namespace (review fixes 2026-11-18).
_LEDGER_KINDS = ("RESERVED", "LOCAL_REFUSAL", "RECOVERY_CHARGED", "RECONCILED", "RESOLVED", "RESOLUTION_AUTHORIZED")
_KIND_FOR_FINDING = {"at_commanded": "applied_as_commanded", "at_recorded_start": "not_applied_or_reverted"}


class JournalError(OSError):
    """The command journal cannot establish or persist its authoritative state."""


class JournalRefusal(JournalError):
    """A precondition refused before any row was written (the transaction rolled back); ``code`` names it."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class JournalEntry:
    request_json: str
    command_json: str
    outcome: DawApplyOutcome | None


@dataclass(frozen=True)
class _Row:
    command_id: str
    kind: str
    root: str | None
    attempt: int | None
    request: dict
    command: dict
    outcome: object
    reserved_seq: int | None
    request_json: str
    command_json: str
    conversion: VersionRef


def _fingerprint(request, command):
    return hashlib.sha256((request + "\0" + command).encode("utf-8")).hexdigest()


def _ledger_fingerprint(kind, entry_id, subject, record):
    return hashlib.sha256("\0".join((kind, entry_id, subject, record)).encode("utf-8")).hexdigest()


def _dumps(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True)


def _identity(command_id, request, command, kind="apply"):
    """Parsed request, parsed wire command and conversion of one command row; fails closed on any mismatch."""
    try:
        data = json.loads(request)
        wire = json.loads(command)
        if type(data["v"]) is not int or data["v"] != 1:
            raise ValueError("unknown request schema")
        if kind == "apply":
            if set(data) != {"v", "request"} or data["request"]["command_id"] != command_id:
                raise ValueError("apply identity mismatch")
            if wire["id"] != command_id or wire["type"] != "set_gain_db":
                raise ValueError("apply wire identity mismatch")
            conversion = data["request"]["profile"]["conversion"]
        elif kind == "restore":
            if set(data) != {"v", "restore"} or data["restore"]["recovery_command_id"] != command_id:
                raise ValueError("restore identity mismatch")
            if wire["id"] != command_id or wire["type"] != "restore_gain":
                raise ValueError("restore wire identity mismatch")
            conversion = data["restore"]["authorization"]["target"]["conversion"]
        else:
            raise ValueError("unknown command kind")
        return data, wire, VersionRef(conversion["name"], conversion["version"])
    except (KeyError, TypeError, ValueError) as error:
        raise JournalError("invalid command journal identity") from error


def _reading(value) -> DawFaderReading:
    return DawFaderReading(value["normalized"], value["db"])


def _plain_reading(reading: DawFaderReading) -> dict:
    return {"normalized": reading.normalized, "db": reading.db}


class SqliteCommandJournal:
    """One locked journal per configured DAW session, retaining IDs, lineage, charges and blocks across restarts."""

    def __init__(self, path: Path, *, session_key: str):
        if not isinstance(path, Path) or not path.is_absolute() or str(path).startswith("\\\\"):
            raise ValueError("journal path must be an absolute local Path")
        if not isinstance(session_key, str) or not session_key.strip():
            raise ValueError("session_key must be nonblank")
        if not path.parent.is_dir():
            raise ValueError("journal parent directory must exist")
        self._lock = threading.RLock()
        self._connection = None
        try:
            self._connection = sqlite3.connect(path, timeout=0, isolation_level=None, check_same_thread=False)
            connection = self._connection
            connection.execute("PRAGMA busy_timeout=0")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=EXTRA")
            connection.execute("PRAGMA locking_mode=EXCLUSIVE")
            self.settings = {
                name: connection.execute(f"PRAGMA {name}").fetchone()[0]
                for name in ("journal_mode", "synchronous", "locking_mode")
            }
            if self.settings != {"journal_mode": "delete", "synchronous": 3, "locking_mode": "exclusive"}:
                raise JournalError("required SQLite synchronization/locking settings unavailable")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, _SCHEMA_VERSION):
                raise JournalError("unknown command journal version")
            with self._transaction():
                if version == 0:
                    # Do not reinterpret an existing unrelated database as an empty command journal.
                    if connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                        raise JournalError("unrecognized existing database")
                    connection.execute("CREATE TABLE inspections (command_id TEXT PRIMARY KEY)")
                    connection.execute("CREATE TABLE metadata (session_key TEXT NOT NULL)")
                    connection.execute("INSERT INTO metadata VALUES (?)", (session_key,))
                    connection.execute("""CREATE TABLE commands (
                        command_id TEXT PRIMARY KEY, request_json TEXT NOT NULL, command_json TEXT NOT NULL,
                        fingerprint TEXT NOT NULL, outcome_json TEXT)""")
                    self._create_v2_additions()
                elif version == 1:
                    self._migrate_from_v1()
                if connection.execute("SELECT session_key FROM metadata").fetchall() != [(session_key,)]:
                    raise JournalError("journal belongs to another configured session")
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise JournalError("journal integrity check failed")
            self._entries()  # validate persisted payloads before any operation can dispatch
        except (sqlite3.Error, JournalError, ValueError) as error:
            self.close()
            raise JournalError("cannot initialize command journal: " + str(error)) from error

    # --- schema --------------------------------------------------------------------------------------------------

    def _create_v2_additions(self):
        connection = self._connection
        connection.execute("ALTER TABLE commands ADD COLUMN kind TEXT NOT NULL DEFAULT 'apply'")
        connection.execute("ALTER TABLE commands ADD COLUMN root_command_id TEXT")
        connection.execute("ALTER TABLE commands ADD COLUMN attempt INTEGER")
        connection.execute("""CREATE TABLE ledger (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, entry_kind TEXT NOT NULL, entry_id TEXT NOT NULL,
            subject_command_id TEXT NOT NULL, record_json TEXT NOT NULL, fingerprint TEXT NOT NULL,
            UNIQUE(entry_kind, entry_id))""")
        connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")

    def _migrate_from_v1(self):
        """v1 -> v2 in one transaction; existing field contents unchanged, RESERVED rows in v1 rowid order."""
        connection = self._connection
        columns = [row[1] for row in connection.execute("PRAGMA table_info(commands)")]
        if columns != ["command_id", "request_json", "command_json", "fingerprint", "outcome_json"]:
            raise JournalError("unexpected v1 commands table")
        existing = connection.execute("SELECT command_id FROM commands ORDER BY rowid").fetchall()
        self._create_v2_additions()
        record = _dumps({"migrated_from_v1": True})
        for (command_id,) in existing:
            self._append("RESERVED", command_id, command_id, record)

    def _append(self, kind, entry_id, subject, record):
        cursor = self._connection.execute(
            "INSERT INTO ledger (entry_kind,entry_id,subject_command_id,record_json,fingerprint) VALUES (?,?,?,?,?)",
            (kind, entry_id, subject, record, _ledger_fingerprint(kind, entry_id, subject, record)),
        )
        return cursor.lastrowid

    def _commit(self):
        self._connection.execute("COMMIT")

    @contextmanager
    def _transaction(self):
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                yield
                self._commit()
            except JournalRefusal:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
            except (sqlite3.Error, ValueError, JournalError) as error:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise JournalError(str(error)) from error

    # --- reading and validation ----------------------------------------------------------------------------------

    def _ledger(self):
        with self._lock:
            try:
                rows = self._connection.execute(
                    "SELECT seq,entry_kind,entry_id,subject_command_id,record_json,fingerprint FROM ledger ORDER BY seq"
                ).fetchall()
            except (sqlite3.Error, AttributeError) as error:
                raise JournalError("cannot read command ledger") from error
        entries = []
        for seq, kind, entry_id, subject, record, digest in rows:
            if kind not in _LEDGER_KINDS or _ledger_fingerprint(kind, entry_id, subject, record) != digest:
                raise JournalError("invalid command ledger row")
            try:
                parsed = json.loads(record)
            except ValueError as error:
                raise JournalError("invalid command ledger record") from error
            if not isinstance(parsed, dict):
                raise JournalError("invalid command ledger record")
            entries.append((seq, kind, entry_id, subject, parsed))
        return entries

    def _rows(self):
        with self._lock:
            try:
                rows = self._connection.execute(
                    "SELECT command_id,kind,root_command_id,attempt,request_json,command_json,fingerprint,"
                    "outcome_json FROM commands ORDER BY rowid"
                ).fetchall()
            except (sqlite3.Error, AttributeError) as error:
                raise JournalError("cannot read command journal") from error
            ledger = self._ledger()
        reserved = {entry_id: seq for seq, kind, entry_id, _, _ in ledger if kind == "RESERVED"}
        result = []
        for command_id, kind, root, attempt, request, command, digest, outcome in rows:
            data, wire, conversion = _identity(command_id, request, command, kind)
            if _fingerprint(request, command) != digest:
                raise JournalError("command journal fingerprint mismatch")
            if kind == "restore":
                expected_root = data["restore"]["authorization"]["target"]["source_command_id"]
                if root != expected_root or type(attempt) is not int or attempt < 1:
                    raise JournalError("invalid restoration lineage")
            elif root is not None or attempt is not None:
                raise JournalError("an apply has no lineage columns")
            try:
                decoded = load_outcome(outcome) if outcome is not None else None
            except ValueError as error:
                raise JournalError("invalid command journal outcome") from error
            if decoded is not None and getattr(
                decoded, "command_id", getattr(decoded, "recovery_command_id", None)
            ) != (command_id):
                raise JournalError("outcome identity mismatch")
            result.append(_Row(command_id, kind, root, attempt, data, wire, decoded, reserved.get(command_id), request,
                               command, conversion))  # fmt: skip
        self._check_consistency(result, ledger)
        return result

    @staticmethod
    def _check_consistency(rows, ledger):
        """Every command has exactly one marker: a reservation, or (only a refused apply) an explicit local refusal.

        Each reserved restoration has exactly one recovery charge; every marker, charge and consumed resolution
        authorization refers to an existing entry. Anything else fails closed: a missing reservation must never read
        as "nothing unresolved".
        """
        by_id = {row.command_id: row for row in rows}
        markers, charges = {}, {}
        resolutions = {entry_id: record for _, kind, entry_id, _, record in ledger if kind == "RESOLVED"}
        for _, kind, entry_id, subject, record in ledger:
            if kind in ("RESERVED", "LOCAL_REFUSAL"):
                if entry_id != subject or entry_id not in by_id or entry_id in markers:
                    raise JournalError("command marker without exactly one command")
                markers[entry_id] = kind
            elif kind == "RECOVERY_CHARGED":
                row = by_id.get(subject)
                if row is None or row.kind != "restore" or subject in charges:
                    raise JournalError("recovery charge without exactly one restoration")
                charges[subject] = entry_id
            elif kind == "RESOLUTION_AUTHORIZED":
                if resolutions.get(subject, {}).get("authorization_id") != entry_id:
                    raise JournalError("consumed authorization without its resolution")
        for row in rows:
            marker = markers.get(row.command_id)
            if marker is None:
                raise JournalError(f"command {row.command_id} has neither a reservation nor a local-refusal marker")
            if marker == "LOCAL_REFUSAL" and (row.kind != "apply" or not isinstance(row.outcome, DawWriteRefused)):
                raise JournalError("only a refused apply can be a local refusal")
            if row.kind == "restore" and (marker != "RESERVED" or row.command_id not in charges):
                raise JournalError("a restoration needs its reservation and exactly one recovery charge")
        consumed = {entry_id for _, kind, entry_id, _, _ in ledger if kind == "RESOLUTION_AUTHORIZED"}
        if any(record.get("authorization_id") not in consumed for record in resolutions.values()):
            raise JournalError("a resolution's authorization is not registered in the shared namespace")

    def _entries(self):
        with self._lock:
            try:
                for (inspect_id,) in self._connection.execute("SELECT command_id FROM inspections"):
                    if not isinstance(inspect_id, str) or not _ID.fullmatch(inspect_id):
                        raise JournalError("invalid persisted inspection ID")
                rows = self._rows()
                for _, kind, _, _, record in self._ledger():
                    if kind == "RECOVERY_CHARGED":
                        load_grant(_dumps(record))
                return [(row.command_id, JournalEntry(row.request_json, row.command_json, row.outcome)) for row in rows]
            except (sqlite3.Error, ValueError, AttributeError) as error:
                raise JournalError("invalid command journal: " + str(error)) from error

    def _id_used(self, value: str) -> bool:
        with self._lock:
            try:
                connection = self._connection
                return any(
                    connection.execute(query, (value,)).fetchone() is not None
                    for query in (
                        "SELECT 1 FROM commands WHERE command_id=?",
                        "SELECT 1 FROM inspections WHERE command_id=?",
                        "SELECT 1 FROM ledger WHERE entry_id=?",
                    )
                )
            except (sqlite3.Error, AttributeError) as error:
                raise JournalError("cannot establish ID history") from error

    def _is_inspect_id(self, command_id: str) -> bool:
        with self._lock:
            try:
                return (
                    self._connection.execute("SELECT 1 FROM inspections WHERE command_id=?", (command_id,)).fetchone()
                    is not None
                )
            except (sqlite3.Error, AttributeError) as error:
                raise JournalError("cannot establish inspection ID history") from error

    def _claim_inspect_id(self, command_id: str) -> None:
        if not isinstance(command_id, str) or not _ID.fullmatch(command_id):
            raise ValueError("invalid inspection command ID")
        with self._transaction():
            if self._id_used(command_id):
                raise JournalError("inspection command ID was already used")
            self._connection.execute("INSERT INTO inspections VALUES (?)", (command_id,))

    def lookup(self, command_id: str) -> JournalEntry | None:
        return next((entry for key, entry in self._entries() if key == command_id), None)

    # --- derived state ---------------------------------------------------------------------------------------------

    @staticmethod
    def _recorded_start(row) -> DawFaderReading:
        """The basis rule: the outcome's ``before`` when recorded, else the request's inspected reading."""
        before = getattr(row.outcome, "before", None)
        if before is not None:
            return before
        request = row.request["request"] if row.kind == "apply" else row.request["restore"]
        return _reading(request["inspection"]["fader"])

    @staticmethod
    def _commanded(row) -> DawFaderReading:
        if row.kind == "apply":
            command = row.command
            return DawFaderReading(
                command["target_normalized"], command["expected"]["db"] + command["intended_db_change"]
            )
        return _reading(row.request["restore"]["authorization"]["target"]["reading"])

    def _unresolved(self, rows=None, ledger=None):
        rows = self._rows() if rows is None else rows
        ledger = self._ledger() if ledger is None else ledger
        resolutions = [(seq, subject) for seq, kind, _, subject, _ in ledger if kind == "RESOLVED"]
        result = []
        for row in rows:
            if row.reserved_seq is None:
                continue  # a local refusal: never reserved, nothing written
            if row.kind == "apply":
                open_ = row.outcome is None or isinstance(row.outcome, DawWriteUncertain)
            else:
                open_ = not isinstance(row.outcome, DawRestoreConfirmed)
            if not open_:
                continue
            resolved = any(
                subject == row.command_id or (row.kind == "restore" and subject == row.root and seq > row.reserved_seq)
                for seq, subject in resolutions
            )
            if not resolved:
                result.append(row)
        return sorted(result, key=lambda row: row.reserved_seq)

    def pending_or_uncertain(self) -> bool:
        """Any unresolved command (pending or uncertain apply; restoration not confirmed), without a resolution."""
        return bool(self._unresolved())

    def blocked_conversions(self) -> frozenset[VersionRef]:
        return frozenset(
            row.conversion for row in self._rows() if row.kind == "apply" and isinstance(row.outcome, DawWriteOffTarget)
        )

    def control_state(self) -> DawControlState:
        rows, ledger = self._rows(), self._ledger()
        unresolved = tuple(
            DawUnresolvedCommand(
                row.command_id,
                DawCommandKind(row.kind),
                row.command_id if row.kind == "apply" else row.root,
                row.attempt,
                row.reserved_seq,
                self._recorded_start(row),
                self._commanded(row),
                row.outcome,
            )
            for row in self._unresolved(rows, ledger)
        )
        charges = tuple(
            DawRecoveryCharge(entry_id, subject, seq, load_grant(_dumps(record)))
            for seq, kind, entry_id, subject, record in ledger
            if kind == "RECOVERY_CHARGED"
        )
        return DawControlState(unresolved, self.blocked_conversions(), charges)

    def lineage_scope(self, command_id: str):
        """(binding, profile) of the root apply request of ``command_id``, as journaled dictionaries."""
        rows = self._rows()
        row = next((row for row in rows if row.command_id == command_id), None)
        if row is None or row.reserved_seq is None:
            raise JournalRefusal("subject_not_unresolved", "no such reserved command")
        root = self._root(rows, row.command_id if row.kind == "apply" else row.root)
        request = root.request["request"]
        return request["resolution"]["state"]["binding"], request["profile"]

    def latest_source(self):
        """(source ID or None, ledger watermark): the latest state-establishing entry (ADR-039 run-start check).

        Candidates are every reservation whose command was not refused (a refused write wrote nothing) and every
        resolution; the latest by ledger seq wins. Refused (``JournalRefusal``) while anything is unresolved. The
        caller anchors the returned source with ``state_anchor``, whose confirmed-only and currency rules apply
        unchanged (an off-target, uncertain or superseded latest source cannot be anchored).
        """
        with self._lock:
            rows, ledger = self._rows(), self._ledger()
            if self._unresolved(rows, ledger):
                raise JournalRefusal("unresolved", "a command is unresolved: no state can be anchored")
            refused = {row.command_id for row in rows if isinstance(row.outcome, DawWriteRefused)}
            candidates = [
                (seq, entry_id) for seq, kind, entry_id, _, _ in ledger
                if (kind == "RESERVED" and entry_id not in refused) or kind == "RESOLVED"
            ]  # fmt: skip
            top = max((seq for seq, *_ in ledger), default=0)
            return (max(candidates)[1] if candidates else None), top

    def state_anchor(self, source_id: str) -> dict:
        """Journal-backed source evidence for an export anchor, from one locked read pass (export plan, revision 3).

        The source is a confirmed apply or restoration, or a resolution; binding, profile and execution come from its
        journaled request (for a resolution, from its root apply's). Refused (``JournalRefusal``): any unresolved
        command; an unknown source or one of another kind; a source superseded by a later reservation or resolution.
        Watermarks are the highest ``RESERVED`` and ``RESOLVED`` seqs (0 if none) and the highest seq of any kind.
        """
        with self._lock:
            rows, ledger = self._rows(), self._ledger()
            if self._unresolved(rows, ledger):
                raise JournalRefusal("unresolved", "a command is unresolved: no state can be anchored")
            reserved = max((seq for seq, kind, *_ in ledger if kind == "RESERVED"), default=0)
            resolved = max((seq for seq, kind, *_ in ledger if kind == "RESOLVED"), default=0)
            top = max((seq for seq, *_ in ledger), default=0)
            row = next((row for row in rows if row.command_id == source_id), None)
            if row is not None:
                confirmed = row.reserved_seq is not None and (
                    (row.kind == "apply" and isinstance(row.outcome, DawWriteConfirmed))
                    or (row.kind == "restore" and isinstance(row.outcome, DawRestoreConfirmed))
                )
                if not confirmed:
                    raise JournalRefusal("not_confirmed", "only a confirmed apply or restoration can be anchored")
                if row.kind == "apply":
                    request = row.request["request"]
                    basis, binding = "confirmed_write", request["resolution"]["state"]["binding"]
                    profile, execution = request["profile"], request["execution_id"]
                    apply_json = row.request_json  # the exact stored request text (review fix 2026-11-19)
                else:
                    request = row.request["restore"]
                    basis, binding, profile = "confirmed_restore", request["binding"], request["profile"]
                    execution = request["authorization"]["execution_id"]
                    apply_json = None
                seq, reading = row.reserved_seq, _plain_reading(row.outcome.after)
                current = reserved == seq and resolved < seq
            else:
                entry = next(((s, r) for s, k, e, _, r in ledger if k == "RESOLVED" and e == source_id), None)
                if entry is None:
                    raise JournalRefusal("unknown_source", "no confirmed command or resolution has this ID")
                seq, record = entry
                request = self._root(rows, record["root"]).request["request"]
                basis, binding = "resolved", request["resolution"]["state"]["binding"]
                profile, execution, reading = request["profile"], request["execution_id"], record["reading"]
                current = resolved == seq and reserved < seq
                apply_json = None
            if not current:
                raise JournalRefusal("superseded", "a later reservation or resolution supersedes the source")
            return {"basis": basis, "source_seq": seq, "execution_id": execution, "binding": binding,
                    "profile": profile, "reading": reading, "reservation_watermark": reserved,
                    "resolution_watermark": resolved, "ledger_watermark": top, "apply_json": apply_json}  # fmt: skip

    def ledger_entry(self, kind: str, entry_id: str):
        """(seq, subject, record) of one ledger entry, or None."""
        return next(((seq, subject, record) for seq, k, e, subject, record in self._ledger() if (k, e) == (kind, entry_id)),
                    None)  # fmt: skip

    # --- apply (schema v1 behaviour, plus the ledger reservation) --------------------------------------------------

    def reserve(self, command_id: str, request_json: str, command_json: str) -> None:
        _, _, conversion = _identity(command_id, request_json, command_json)
        with self._transaction():
            if self._id_used(command_id) or self.pending_or_uncertain() or conversion in self.blocked_conversions():
                raise JournalError("command ID exists, earlier write unresolved, or conversion blocked")
            self._connection.execute(
                "INSERT INTO commands (command_id,request_json,command_json,fingerprint,outcome_json) "
                "VALUES (?,?,?,?,NULL)",
                (command_id, request_json, command_json, _fingerprint(request_json, command_json)),
            )
            self._append("RESERVED", command_id, command_id, _dumps({"kind": "apply"}))

    def finish(self, command_id: str, outcome) -> None:
        identity = getattr(outcome, "command_id", getattr(outcome, "recovery_command_id", None))
        if identity != command_id:
            raise JournalError("outcome command mismatch")
        encoded = outcome_json(outcome)
        with self._transaction():
            entry = self.lookup(command_id)
            if entry is None or entry.outcome is not None:
                raise JournalError("only a pending command can be finished")
            cursor = self._connection.execute(
                "UPDATE commands SET outcome_json=? WHERE command_id=? AND outcome_json IS NULL", (encoded, command_id)
            )
            if cursor.rowcount != 1:
                raise JournalError("only a pending command can be finished")

    def _remember_refusal(self, command_id, request, command, outcome):
        """Persist a terminal local refusal without implying a pending dispatch: an explicit LOCAL_REFUSAL marker."""
        _identity(command_id, request, command)
        if not isinstance(outcome, DawWriteRefused):
            raise JournalError("only a refusal can be remembered locally")
        with self._transaction():
            if self._id_used(command_id):
                raise JournalError("command ID already exists")
            self._connection.execute(
                "INSERT INTO commands (command_id,request_json,command_json,fingerprint,outcome_json) "
                "VALUES (?,?,?,?,?)",
                (command_id, request, command, _fingerprint(request, command), outcome_json(outcome)),
            )
            self._append("LOCAL_REFUSAL", command_id, command_id, _dumps({"kind": "apply"}))

    # --- recovery ----------------------------------------------------------------------------------------------------

    def recovery_lineage(self, root_id: str) -> DawRecoveryLineage:
        """Everything journaled about one root apply and after it, from one locked read pass (ADR-038).

        Unlike ``state_anchor`` it describes the root whatever its outcome: confirmed, off target, uncertain or
        pending. Refused (``JournalRefusal``): an unknown or unreserved root, and a refused root (nothing written).
        It never changes the journal; export anchors keep their own, confirmed-only rule.
        """
        with self._lock:
            rows, ledger = self._rows(), self._ledger()
            root = self._root(rows, root_id)
            if isinstance(root.outcome, DawWriteRefused):
                raise JournalRefusal("no_target", "a refused apply wrote nothing: it has no recovery lineage")
            status = {DawWriteConfirmed: DawRootStatus.CONFIRMED, DawWriteOffTarget: DawRootStatus.OFF_TARGET,
                      DawWriteUncertain: DawRootStatus.UNCERTAIN, type(None): DawRootStatus.PENDING}[type(root.outcome)]  # fmt: skip
            restorations = sorted((row for row in rows if row.kind == "restore" and row.root == root.command_id),
                                  key=lambda row: row.attempt)  # fmt: skip
            members = {root.command_id, *(row.command_id for row in restorations)}
            kinds = {"RESERVED": DawLedgerKind.RESERVED, "RECONCILED": DawLedgerKind.RECONCILED,
                     "RESOLVED": DawLedgerKind.RESOLVED}  # fmt: skip
            findings, foreign = [], []
            for seq, kind, entry_id, subject, _ in ledger:
                if kind not in kinds:
                    continue
                entry = DawLedgerEntry(entry_id, kinds[kind], subject, seq)
                if kind != "RESERVED" and subject in members:
                    findings.append(entry)
                elif kind != "RECONCILED" and seq > root.reserved_seq and subject not in members:
                    foreign.append(entry)
            reserved = max((seq for seq, kind, *_ in ledger if kind == "RESERVED"), default=0)
            resolved = max((seq for seq, kind, *_ in ledger if kind == "RESOLVED"), default=0)
            top = max((seq for seq, *_ in ledger), default=0)
            blocked = frozenset(
                row.conversion for row in rows if row.kind == "apply" and isinstance(row.outcome, DawWriteOffTarget)
            )
            return DawRecoveryLineage(
                load_apply_request(root.request_json), status, root.outcome, root.reserved_seq,
                self._recorded_start(root), root.conversion in blocked,
                tuple(DawLineageRestoration(row.command_id, row.attempt, row.reserved_seq, row.outcome)
                      for row in restorations),
                tuple(findings), tuple(foreign), tuple(row.command_id for row in self._unresolved(rows, ledger)),
                reserved, resolved, top,
            )  # fmt: skip

    def _root(self, rows, root_id):
        row = next((row for row in rows if row.command_id == root_id), None)
        if row is None or row.kind != "apply" or row.reserved_seq is None:
            raise JournalRefusal("unknown_root", "the source is not a reserved apply in this journal")
        return row

    def _target(self, row):
        if isinstance(row.outcome, DawWriteRefused):
            raise JournalRefusal("no_target", "a refused apply wrote nothing: there is nothing to restore")
        before = getattr(row.outcome, "before", None)
        basis = DawTargetBasis.OUTCOME_BEFORE if before is not None else DawTargetBasis.REQUEST_INSPECTION
        return basis, self._recorded_start(row), row.conversion

    def restore_target(self, root_id: str):
        """(basis, exact reading, conversion) of the root's recorded start, recomputed from its journal row."""
        with self._lock:
            return self._target(self._root(self._rows(), root_id))

    def reserve_restore(self, command_id: str, request_json: str, command_json: str, *, authorization_id: str,
                        grant_json: str) -> None:  # fmt: skip
        """Reserve and charge one restoration in one transaction, before any byte is sent.

        Refused (``JournalRefusal``, nothing written, nothing charged): a used recovery or authorization ID; an
        unknown or refused root; any disagreement with the root's binding, profile, conversion or recomputed
        target; an unresolved command of another lineage; and, for an ``own_lineage_only`` request, any reservation
        or resolution outside the root's lineage after the root (``foreign_history``, ADR-038).
        """
        data, _, _ = _identity(command_id, request_json, command_json, "restore")
        restore = data["restore"]
        if load_grant(grant_json).operation is not BudgetOperation.RECOVERY_OPERATION:
            raise JournalRefusal("agreement", "a restoration is charged only as a recovery operation")
        charge_record = _dumps(json.loads(grant_json))
        with self._transaction():
            if self._id_used(command_id) or self._id_used(authorization_id):
                raise JournalRefusal("duplicate_id_conflict", "the recovery or authorization ID was already used")
            if restore["authorization"]["authorization_id"] != authorization_id or _dumps(
                restore["authorization"]["budget_grant"]
            ) != _dumps(json.loads(grant_json)["grant"]):
                raise JournalRefusal("agreement", "the charge does not belong to this request's authorization")
            rows = self._rows()
            target = restore["authorization"]["target"]
            root = self._root(rows, target["source_command_id"])
            basis, reading, conversion = self._target(root)
            root_request = root.request["request"]
            agreements = (
                restore["binding"] == root_request["resolution"]["state"]["binding"],
                restore["profile"] == root_request["profile"],
                target["conversion"] == {"name": conversion.name, "version": conversion.version},
                target["basis"] == basis.value,
                target["reading"] == _plain_reading(reading),
            )
            if not all(agreements):
                raise JournalRefusal("agreement", "the restoration disagrees with its root's journaled request")
            foreign = [
                row.command_id for row in self._unresolved(rows) if (row.root or row.command_id) != root.command_id
            ]
            if foreign:
                raise JournalRefusal(
                    "unresolved_other", "commands of another lineage are unresolved: " + ", ".join(foreign)
                )
            if restore.get("own_lineage_only"):  # review fix 2026-11-20 (ADR-038): the lineage rule, atomically
                members = {root.command_id} | {
                    row.command_id for row in rows if row.kind == "restore" and row.root == root.command_id
                }
                later = [
                    entry_id for seq, kind, entry_id, subject, _ in self._ledger()
                    if kind in ("RESERVED", "RESOLVED") and seq > root.reserved_seq and subject not in members
                ]  # fmt: skip
                if later:
                    raise JournalRefusal(
                        "foreign_history", "entries outside this lineage follow its root: " + ", ".join(later)
                    )
            attempt = 1 + sum(1 for row in rows if row.kind == "restore" and row.root == root.command_id)
            self._connection.execute(
                "INSERT INTO commands (command_id,request_json,command_json,fingerprint,outcome_json,kind,"
                "root_command_id,attempt) VALUES (?,?,?,?,NULL,'restore',?,?)",
                (command_id, request_json, command_json, _fingerprint(request_json, command_json), root.command_id,
                 attempt),
            )  # fmt: skip
            self._append("RESERVED", command_id, command_id, _dumps({"kind": "restore", "root": root.command_id}))
            self._append("RECOVERY_CHARGED", authorization_id, command_id, charge_record)

    # --- reconciliation and resolution -------------------------------------------------------------------------------

    def record_reconciliation(self, reconciliation_id: str, subject: str, record: dict) -> int:
        """Append one finding about an unresolved ``subject``; it clears nothing. Returns its ledger seq."""
        if not isinstance(record, dict) or record.get("finding") not in (
            "at_commanded", "at_recorded_start", "ambiguous", "neither", "identity_unverified"
        ):  # fmt: skip
            raise ValueError("a reconciliation record names its finding")
        with self._transaction():
            if self._id_used(reconciliation_id):
                raise JournalRefusal("duplicate_id_conflict", "the reconciliation ID was already used")
            if subject not in {row.command_id for row in self._unresolved()}:
                raise JournalRefusal("subject_not_unresolved", "only an unresolved command can be reconciled")
            return self._append("RECONCILED", reconciliation_id, subject, _dumps(record))

    def record_resolution(self, resolution_id: str, subject: str, kind: str, evidence_id: str, *,
                          authorization_id: str, record: dict | None = None):  # fmt: skip
        """Append a resolution if its evidence is subject-specific, latest and followed by no reservation.

        Returns ``(seq, root, resolved_reading)``. Refusals (``JournalRefusal``) append nothing.
        """
        with self._transaction():
            if self._id_used(resolution_id):
                raise JournalRefusal("duplicate_id_conflict", "the resolution ID was already used")
            ledger = self._ledger()
            if self._id_used(authorization_id) or any(
                k == "RESOLVED" and r.get("authorization_id") == authorization_id for _, k, _, _, r in ledger
            ):
                raise JournalRefusal("authorization_reused", "the resolution authorization was already used")
            rows = self._rows()
            unresolved = {row.command_id: row for row in self._unresolved(rows, ledger)}
            if subject not in unresolved:
                raise JournalRefusal("subject_not_unresolved", "only an unresolved command can be resolved")
            subject_row = unresolved[subject]
            reconciliation = next(((s, sub, r) for s, k, e, sub, r in ledger if k == "RECONCILED" and e == evidence_id),
                                  None)  # fmt: skip
            restoration = next((row for row in rows if row.command_id == evidence_id and row.kind == "restore"), None)
            if reconciliation is not None:
                evidence_seq, evidence_subject, evidence = reconciliation
                if evidence_subject != subject:
                    raise JournalRefusal("evidence_not_for_subject", "a finding resolves only the command it names")
                if _KIND_FOR_FINDING.get(evidence["finding"]) != kind:
                    raise JournalRefusal("kind_not_supported_by_evidence", "the finding does not support this kind")
                reading = evidence["reading"]
            elif restoration is not None:
                evidence_seq = restoration.reserved_seq
                if subject_row.kind != "apply" or restoration.root != subject:
                    raise JournalRefusal("evidence_not_for_subject", "only a root's own restoration resolves it")
                if not isinstance(restoration.outcome, DawRestoreConfirmed) or kind != "restored_to_recorded_start":
                    raise JournalRefusal("kind_not_supported_by_evidence", "only a confirmed restoration restores")
                reading = _plain_reading(restoration.outcome.after)
            else:
                raise JournalRefusal("unknown_evidence", "no such reconciliation or restoration")
            # Freshness is judged across the whole lineage (root and every restoration of it): a newer finding
            # about any member, or a newer restoration outcome, makes older evidence stale (review fix 2026-11-18).
            lineage_root = subject if subject_row.kind == "apply" else subject_row.root
            lineage = {lineage_root} | {
                row.command_id for row in rows if row.kind == "restore" and row.root == lineage_root
            }
            relevant = [s for s, k, _, sub, _ in ledger if k == "RECONCILED" and sub in lineage]
            relevant += [
                row.reserved_seq for row in rows if row.kind == "restore" and row.root == lineage_root and row.outcome
            ]
            if max(relevant) != evidence_seq:
                raise JournalRefusal("stale_evidence", "newer relevant evidence exists in this lineage")
            if any(s > evidence_seq for s, k, _, _, _ in ledger if k == "RESERVED"):
                raise JournalRefusal("reservation_after_evidence", "a write was reserved after the cited evidence")
            root = subject if subject_row.kind == "apply" else subject_row.root
            payload = {**(record or {}), "kind": kind, "evidence_id": evidence_id,
                       "authorization_id": authorization_id, "root": root, "reading": reading}  # fmt: skip
            seq = self._append("RESOLVED", resolution_id, subject, _dumps(payload))
            self._append(
                "RESOLUTION_AUTHORIZED", authorization_id, resolution_id, _dumps({"resolution": resolution_id})
            )
            return seq, root, _reading(reading)

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
