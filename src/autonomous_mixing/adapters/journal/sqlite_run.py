"""Durable run journal: one local SQLite file per run for the trace and the acceptance authority (ADR-039).

Settings as the FL command journal: ``journal_mode=DELETE``, ``synchronous=EXTRA``, ``locking_mode=EXCLUSIVE``,
verified at open. Opening takes the exclusive lock with an immediate write transaction and keeps it while open; the
operating system releases it when the process dies. A second opener, in any process, gets ``RunStoreBusy``.

Rows are only inserted, each with the SHA-256 fingerprint of its canonical record text; every read re-checks the
fingerprints, and opening decodes every row through its record constructor. Every write is one ``BEGIN IMMEDIATE``
transaction; success is reported only after a confirmed ``COMMIT``. After any exception inside a write transaction (a
storage failure is returned as unknown; any other exception propagates after the same cleanup, review fix 2026-11-20) the
connection is rolled back if possible and closed, and every later operation first reopens on a fresh connection with
the lock and all rows re-verified (amendment A1): no read is ever answered on a connection that had a failed
transaction, so an uncommitted row can never be reported. If reopening fails, the instance answers nothing.

Durability label ``LOCAL_SQLITE_PROCESS_CRASH``: subprocess-crash evidence only, no power-loss claim. Local storage
only: network and synced folders are unsupported (documented, not detected).
"""

import hashlib
import sqlite3
import threading
from pathlib import Path

from autonomous_mixing.adapters.journal.record_codec import decode, dumps, encode, loads
from autonomous_mixing.core.records.acceptance import (
    AcceptanceAbsent,
    AcceptanceAuthorityUnknown,
    AcceptanceCommitResult,
    AcceptanceCommitted,
    AcceptanceCommitUnknown,
    AcceptanceEvent,
    AcceptanceFound,
    AcceptanceNotCommitted,
    AcceptanceQuery,
    AcceptanceQueryResult,
    AcceptedReference,
)
from autonomous_mixing.core.records.identifiers import RunId
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, PortResult, Success
from autonomous_mixing.core.records.run_trace import (
    RunJournalSnapshot,
    RunTraceEvent,
    StoreDurability,
    TraceAppended,
    TraceAppendResult,
    TraceAppendUnknown,
    TraceNotAppended,
)

SCHEMA_VERSION = 1
LOCAL_SQLITE_PROCESS_CRASH = StoreDurability.LOCAL_SQLITE_PROCESS_CRASH.value
_SETTINGS = {"journal_mode": "delete", "synchronous": 3, "locking_mode": "exclusive"}

# One in-process exclusive-ownership authority per run (RunExclusion), as for the in-memory journal; across processes
# the SQLite exclusive lock held by the open connection excludes every other opener.
_REGISTRY_LOCK = threading.Lock()
_RUN_LOCKS: dict[RunId, threading.Lock] = {}


class RunStoreError(Exception):
    """The store cannot be created or opened as this run's verified store."""


class RunStoreBusy(RunStoreError):
    """Another connection (in this or another process) holds the store's exclusive lock."""


class _Unverified(Exception):
    pass


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _exact_text(record: object) -> str:
    """The canonical text of ``record``, refused unless it decodes back to exactly the same record."""
    text = dumps(encode(record))
    back = decode(loads(text))
    if type(back) is not type(record) or back != record:
        raise ValueError("the record is not exactly serializable")
    return text


def _run_lock(run_id: RunId) -> threading.Lock:
    with _REGISTRY_LOCK:
        return _RUN_LOCKS.setdefault(run_id, threading.Lock())


class SqliteRunJournal:
    """Implements ``RunJournal`` and ``RunExclusion`` durably for one run (ADR-039)."""

    def __init__(self, path: Path, run_id: RunId, initial: AcceptedReference, *, create: bool,
                 read_only: bool = False) -> None:  # fmt: skip
        self._path = Path(path)
        self._read_only = read_only
        self._lock = threading.RLock()
        self._connection = None
        self._needs_reopen = False
        self._unavailable = False
        self._decoded: dict[str, object] = {}
        self._run_id, self._initial = run_id, initial
        self._connect(create=create)

    # --- construction -----------------------------------------------------------------------------------------------

    @classmethod
    def create(cls, path: Path, run_id: RunId, initial: AcceptedReference) -> "SqliteRunJournal":
        """A new store for ``run_id``; refused if the file exists."""
        require_instance(run_id, RunId, "run_id")
        require_instance(initial, AcceptedReference, "initial")
        if initial.sequence != 0:
            raise ValueError("the initial accepted reference must have sequence 0")
        if Path(path).exists():
            raise RunStoreError("a store already exists at this path")
        return cls(path, run_id, initial, create=True)

    @classmethod
    def open_read_only(cls, path: Path, *, run_id: RunId | None = None) -> "SqliteRunJournal":
        """A verified view of an existing store that takes no lock and writes nothing (ADR-040 ``daw status``).

        SQLite ``mode=ro``: a store another process holds raises ``RunStoreBusy``; appends and commits are refused.
        """
        if not Path(path).exists():
            raise RunStoreError("no store exists at this path")
        journal = cls(path, None, None, create=False, read_only=True)
        if run_id is not None and journal.run_id != run_id:
            journal.close()
            raise RunStoreError("the store belongs to another run")
        return journal

    @classmethod
    def open(cls, path: Path, *, run_id: RunId | None = None) -> "SqliteRunJournal":
        """An existing store, verified; ``run_id`` (optional) must match the stored run."""
        if not Path(path).exists():
            raise RunStoreError("no store exists at this path")
        journal = cls(path, None, None, create=False)
        if run_id is not None and journal.run_id != run_id:
            journal.close()
            raise RunStoreError("the store belongs to another run")
        return journal

    def _connect(self, *, create: bool) -> None:
        if self._read_only:
            return self._connect_read_only()
        try:
            connection = sqlite3.connect(self._path, timeout=0, isolation_level=None, check_same_thread=False)
        except sqlite3.Error as error:
            raise RunStoreError(f"cannot open the run store: {error}") from error
        try:
            connection.execute("PRAGMA locking_mode=EXCLUSIVE")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=EXTRA")
            settings = {
                name: connection.execute(f"PRAGMA {name}").fetchone()[0]
                for name in ("journal_mode", "synchronous", "locking_mode")
            }
            if settings != _SETTINGS:
                raise RunStoreError("required SQLite synchronization or locking settings are unavailable")
            connection.execute("BEGIN EXCLUSIVE")  # takes and, in EXCLUSIVE locking mode, keeps the lock
            if create:
                connection.execute("CREATE TABLE metadata (schema_version INTEGER NOT NULL, run_json TEXT NOT NULL, "
                                   "initial_json TEXT NOT NULL, fingerprint TEXT NOT NULL)")  # fmt: skip
                connection.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, "
                                   "record_json TEXT NOT NULL, fingerprint TEXT NOT NULL)")  # fmt: skip
                connection.execute("CREATE TABLE acceptances (position INTEGER PRIMARY KEY, acceptance_id TEXT NOT "
                                   "NULL UNIQUE, record_json TEXT NOT NULL, fingerprint TEXT NOT NULL)")  # fmt: skip
                run_json, initial_json = _exact_text(self._run_id), _exact_text(self._initial)
                connection.execute("INSERT INTO metadata VALUES (?,?,?,?)", (SCHEMA_VERSION, run_json, initial_json,
                                                                              _fingerprint(run_json + initial_json)))  # fmt: skip
            connection.execute("COMMIT")
            self.settings = settings
            self._connection = connection
            self._verify_all()
        except sqlite3.OperationalError as error:
            connection.close()
            if "locked" in str(error) or "busy" in str(error):
                raise RunStoreBusy("another connection holds the run store") from error
            raise RunStoreError(f"cannot open the run store: {error}") from error
        except (RunStoreError, _Unverified, ValueError, sqlite3.Error) as error:
            connection.close()
            self._connection = None
            if isinstance(error, RunStoreError):
                raise
            raise RunStoreError(f"the run store failed verification: {error}") from error

    def _connect_read_only(self) -> None:
        uri = f"{self._path.resolve().as_uri()}?mode=ro"
        try:
            self._connection = sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None,
                                               check_same_thread=False)  # fmt: skip
            self.settings = {"read_only": True}
            self._verify_all()
        except sqlite3.OperationalError as error:
            if self._connection is not None:
                self._connection.close()
            self._connection = None
            if "locked" in str(error) or "busy" in str(error):
                raise RunStoreBusy("another process holds the run store") from error
            raise RunStoreError(f"cannot read the run store: {error}") from error
        except (_Unverified, ValueError, sqlite3.Error) as error:
            if self._connection is not None:
                self._connection.close()
            self._connection = None
            raise RunStoreError(f"the run store failed verification: {error}") from error

    def _verify_all(self) -> None:
        """Metadata, every fingerprint and every record through its constructor (``_Unverified`` on mismatch)."""
        rows = self._connection.execute("SELECT schema_version, run_json, initial_json, fingerprint FROM metadata")
        rows = rows.fetchall()
        if len(rows) != 1 or rows[0][0] != SCHEMA_VERSION:
            raise _Unverified("unknown schema version or metadata")
        _, run_json, initial_json, digest = rows[0]
        if _fingerprint(run_json + initial_json) != digest:
            raise _Unverified("metadata fingerprint mismatch")
        run_id, initial = decode(loads(run_json)), decode(loads(initial_json))
        if self._run_id is not None and (run_id, initial) != (self._run_id, self._initial):
            raise _Unverified("the store belongs to another run or initial reference")
        self._run_id, self._initial = run_id, initial
        self._decoded = {}
        self._read()

    def _read(self) -> tuple[tuple[RunTraceEvent, ...], tuple[AcceptanceCommitted, ...]]:
        """Every committed row, fingerprints re-checked; decoding is cached by fingerprint (content-addressed)."""
        found = []
        for table, key in (("events", "seq"), ("acceptances", "position")):
            records = []
            for position, (text, digest) in enumerate(
                self._connection.execute(f"SELECT record_json, fingerprint FROM {table} ORDER BY {key}"), 1
            ):
                if _fingerprint(text) != digest:
                    raise _Unverified(f"{table} row {position}: fingerprint mismatch")
                if digest not in self._decoded:
                    self._decoded[digest] = decode(loads(text))
                records.append(self._decoded[digest])
            found.append(tuple(records))
        events, acceptances = found
        if not all(isinstance(e, RunTraceEvent) for e in events) or not all(
            isinstance(a, AcceptanceCommitted) for a in acceptances
        ):
            raise _Unverified("a stored row is not the record its table holds")
        RunJournalSnapshot(self._run_id, self._initial, events, acceptances, StoreDurability.LOCAL_SQLITE_PROCESS_CRASH)
        return events, acceptances

    # --- failure handling (amendment A1) ---------------------------------------------------------------------------

    def _commit(self) -> None:
        """The single COMMIT step (a labelled fault seam in tests)."""
        self._connection.execute("COMMIT")

    def _fail(self) -> None:
        """After a failure inside a write transaction: roll back if possible, close, and require a verified reopen."""
        connection, self._connection = self._connection, None
        try:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        try:
            if connection is not None:
                connection.close()
        except sqlite3.Error:
            pass
        self._needs_reopen = True

    def _ready(self) -> bool:
        """Complete a pending reopen; False when this instance must answer nothing."""
        if self._unavailable:
            return False
        if not self._needs_reopen:
            return self._connection is not None
        try:
            self._connect(create=False)
        except RunStoreError:
            self._unavailable = True
            return False
        self._needs_reopen = False
        return True

    # --- properties -------------------------------------------------------------------------------------------------

    @property
    def run_id(self) -> RunId:
        return self._run_id

    @property
    def initial(self) -> AcceptedReference:
        return self._initial

    @property
    def store_durability(self) -> StoreDurability:
        return StoreDurability.LOCAL_SQLITE_PROCESS_CRASH

    @property
    def durability(self) -> str:
        return LOCAL_SQLITE_PROCESS_CRASH

    def try_acquire_exclusive(self) -> bool:
        if self._read_only:
            return False  # a read-only view never owns the run
        return _run_lock(self._run_id).acquire(blocking=False)

    def release_exclusive(self) -> None:
        lock = _run_lock(self._run_id)
        if not lock.locked():
            raise ValueError("the run's exclusive ownership is not held")
        lock.release()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            self._unavailable = True

    # --- RunJournal -------------------------------------------------------------------------------------------------

    def append(self, event: RunTraceEvent) -> TraceAppendResult:
        require_instance(event, RunTraceEvent, "event")
        with self._lock:
            if self._read_only:
                return TraceNotAppended(event.event_id, "the run store is open read-only")
            if not self._ready():
                return TraceNotAppended(event.event_id, "the run store is unavailable")
            if event.run_id != self._run_id:
                return TraceNotAppended(event.event_id, "event names another run")
            try:
                text = _exact_text(event)
            except ValueError as error:
                return TraceNotAppended(event.event_id, str(error))
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                events, acceptances = self._read()
                existing = next((e for e in events if e.event_id == event.event_id), None)
                if existing is not None:
                    self._connection.execute("ROLLBACK")
                    if existing == event:
                        return TraceAppended(existing)
                    return TraceNotAppended(event.event_id, "event ID already records different contents")
                try:
                    RunJournalSnapshot(self._run_id, self._initial, (*events, event), acceptances)
                except ValueError as error:
                    self._connection.execute("ROLLBACK")
                    return TraceNotAppended(event.event_id, str(error))
                self._connection.execute("INSERT INTO events VALUES (?,?,?,?)",
                                         (event.sequence, event.event_id.value, text, _fingerprint(text)))  # fmt: skip
                self._commit()
            except (sqlite3.Error, _Unverified, ValueError):
                self._fail()
                return TraceAppendUnknown(event.event_id, "the append transaction did not confirm its commit")
            except BaseException:  # review fix 2026-11-20: any other exception also never leaves an open transaction
                self._fail()
                raise
            return TraceAppended(event)

    def commit(self, event: AcceptanceEvent) -> AcceptanceCommitResult:
        require_instance(event, AcceptanceEvent, "event")
        with self._lock:
            if self._read_only:
                return AcceptanceNotCommitted(
                    event.acceptance_id, "the run store is open read-only; nothing was stored"
                )
            if not self._ready():
                return AcceptanceNotCommitted(event.acceptance_id, "the run store is unavailable; nothing was stored")
            if event.run_id != self._run_id:
                return AcceptanceNotCommitted(event.acceptance_id, f"event names run {event.run_id}")
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                _, acceptances = self._read()
                existing = next((c for c in acceptances if c.event.acceptance_id == event.acceptance_id), None)
                head = acceptances[-1].accepted if acceptances else self._initial
                refusal = None
                if existing is not None:
                    self._connection.execute("ROLLBACK")
                    if existing.event == event:
                        return existing
                    refusal = "acceptance ID already committed with different event contents"
                elif event.expected != head:
                    self._connection.execute("ROLLBACK")
                    refusal = "the expected reference is not the current accepted reference"
                if refusal is not None:
                    return AcceptanceNotCommitted(event.acceptance_id, refusal)
                acknowledgement = AcceptanceCommitted(
                    event, AcceptedReference(event.assessment.assessed.state, event.expected.sequence + 1)
                )
                try:
                    text = _exact_text(acknowledgement)
                except ValueError as error:
                    self._connection.execute("ROLLBACK")
                    return AcceptanceNotCommitted(event.acceptance_id, str(error))
                self._connection.execute("INSERT INTO acceptances VALUES (?,?,?,?)",
                                         (len(acceptances) + 1, event.acceptance_id.value, text, _fingerprint(text)))  # fmt: skip
                self._commit()
            except (sqlite3.Error, _Unverified, ValueError):
                self._fail()
                return AcceptanceCommitUnknown(event.acceptance_id, "the commit transaction did not confirm")
            except BaseException:  # review fix 2026-11-20: cleaned up, verified reopen required, then propagated
                self._fail()
                raise
            return acknowledgement

    def query(self, request: AcceptanceQuery) -> AcceptanceQueryResult:
        require_instance(request, AcceptanceQuery, "request")
        with self._lock:
            if request.run_id != self._run_id:
                return AcceptanceAuthorityUnknown(request.run_id, request.acceptance_id,
                                                  f"this store serves run {self._run_id}")  # fmt: skip
            if not self._ready():
                return AcceptanceAuthorityUnknown(request.run_id, request.acceptance_id, "the run store is unavailable")
            try:
                _, acceptances = self._read()
            except (sqlite3.Error, _Unverified, ValueError) as error:
                self._fail()
                return AcceptanceAuthorityUnknown(request.run_id, request.acceptance_id, f"unreadable store: {error}")
            except BaseException:
                self._fail()
                raise
        existing = next((c for c in acceptances if c.event.acceptance_id == request.acceptance_id), None)
        if existing is None:
            return AcceptanceAbsent(request.run_id, request.acceptance_id)
        return AcceptanceFound(existing.event, existing.accepted)

    def snapshot(self) -> PortResult[RunJournalSnapshot]:
        with self._lock:
            if not self._ready():
                return PortFailure(PortFailureCode.UNAVAILABLE, "the run store is unavailable", False)
            try:
                events, acceptances = self._read()
            except (sqlite3.Error, _Unverified, ValueError) as error:
                self._fail()
                return PortFailure(PortFailureCode.UNAVAILABLE, f"the run store failed verification: {error}", False)
            except BaseException:
                self._fail()
                raise
            return Success(RunJournalSnapshot(self._run_id, self._initial, events, acceptances,
                                              StoreDurability.LOCAL_SQLITE_PROCESS_CRASH))  # fmt: skip
