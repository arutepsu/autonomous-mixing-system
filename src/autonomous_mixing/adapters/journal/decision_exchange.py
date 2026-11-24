"""Local append-only decision-exchange store: one SQLite file per run, separate from the run store (R3 Task 4).

Implements ``DecisionAuditTrail``. Schema version 1: a ``metadata`` row (schema version, the run, a fingerprint) and
``phases`` rows (exchange ``request``, ``response``, ``completion``), each the exact canonical record text of the
journal's record codec with its SHA-256 fingerprint; ``UNIQUE(ordinal, phase)``. Rows are only inserted. Every write
is one ``BEGIN IMMEDIATE`` transaction whose success is reported only after a confirmed ``COMMIT``.

Settings, ownership and failure handling follow the run store (ADR-039): ``journal_mode=DELETE``,
``synchronous=EXTRA``, ``locking_mode=EXCLUSIVE`` verified at open; the open connection keeps the exclusive lock, so a
second opener in any process gets ``DecisionExchangeStoreBusy``. After a failure inside a write transaction the
connection is rolled back if possible and closed, the write is reported as unknown, and every later operation first
reopens with all rows re-verified; an uncommitted row is never reported. Opening decodes and cross-checks every row.

Phase rules: ``begin`` needs no prior exchange for the call and an unused exchange ID; ``record_response`` needs the
matching request and no completion; ``finish`` needs the matching request and must reference exactly the recorded
response body (or none). An identical replay of a recorded phase returns ``AuditRecorded(newly_recorded=False)``;
different content is a conflict and nothing is overwritten. A pending exchange found after a crash stays pending:
``begin`` replays it without permitting a dispatch (``permits_dispatch``).

Encoded record text is bounded by ``max_record_bytes`` before any write, and every stored row is checked against the
same bound when the store is opened. Durability: subprocess-crash evidence only (``local_sqlite_process_crash``), no
power-loss claim; local storage only (network and synced folders unsupported, not detected).
"""

import hashlib
import sqlite3
import threading
from pathlib import Path

from autonomous_mixing.adapters.journal.record_codec import decode, dumps, encode, loads
from autonomous_mixing.core.records.decision_exchange import (
    AuditNotRecorded,
    AuditRecorded,
    AuditRecordUnknown,
    AuditRefusal,
    AuditWriteResult,
    CompletedDecisionExchange,
    DecisionCallKey,
    DecisionExchangeRecord,
    DecisionExchangeRequest,
    ExchangeAbsent,
    ExchangeFound,
    ExchangeReadResult,
    ExchangeReadUnavailable,
    RecordedDecisionResponse,
)
from autonomous_mixing.core.records.identifiers import RunId
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.run_trace import StoreDurability

SCHEMA_VERSION = 1
LOCAL_SQLITE_PROCESS_CRASH = StoreDurability.LOCAL_SQLITE_PROCESS_CRASH.value
_SETTINGS = {"journal_mode": "delete", "synchronous": 3, "locking_mode": "exclusive"}
_PHASES = {"request": DecisionExchangeRequest, "response": RecordedDecisionResponse,
           "completion": CompletedDecisionExchange}  # fmt: skip


class DecisionExchangeStoreError(Exception):
    """The store cannot be created or opened as this run's verified exchange store."""


class DecisionExchangeStoreBusy(DecisionExchangeStoreError):
    """Another connection (in this or another process) holds the store's exclusive lock."""


class _Unverified(Exception):
    pass


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _exact_text(record: object) -> str:
    text = dumps(encode(record))
    back = decode(loads(text))
    if type(back) is not type(record) or back != record:
        raise ValueError("the record is not exactly serializable")
    return text


class SqliteDecisionExchangeStore:
    """Durable ``DecisionAuditTrail`` of one run."""

    def __init__(self, path: Path, run_id: RunId | None, *, create: bool, max_record_bytes: int) -> None:
        if type(max_record_bytes) is not int or max_record_bytes < 1:
            raise ValueError("max_record_bytes must be a positive int")
        self._path = Path(path)
        self._max = max_record_bytes
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._needs_reopen = False
        self._unavailable = False
        self._run_id = run_id
        self._records: dict[int, DecisionExchangeRecord] = {}
        self._connect(create=create)

    # --- construction -----------------------------------------------------------------------------------------------

    @classmethod
    def create(cls, path: Path, run_id: RunId, *, max_record_bytes: int) -> "SqliteDecisionExchangeStore":
        """A new store for ``run_id``; refused if the file exists."""
        require_instance(run_id, RunId, "run_id")
        if Path(path).exists():
            raise DecisionExchangeStoreError("an exchange store already exists at this path")
        return cls(path, run_id, create=True, max_record_bytes=max_record_bytes)

    @classmethod
    def open(cls, path: Path, *, run_id: RunId, max_record_bytes: int) -> "SqliteDecisionExchangeStore":
        """An existing store of ``run_id``, every row verified against ``max_record_bytes``."""
        require_instance(run_id, RunId, "run_id")
        if not Path(path).exists():
            raise DecisionExchangeStoreError("no exchange store exists at this path")
        store = cls(path, None, create=False, max_record_bytes=max_record_bytes)
        if store._run_id != run_id:
            store.close()
            raise DecisionExchangeStoreError("the exchange store belongs to another run")
        return store

    def _connect(self, *, create: bool) -> None:
        try:
            connection = sqlite3.connect(self._path, timeout=0, isolation_level=None, check_same_thread=False)
        except sqlite3.Error as error:
            raise DecisionExchangeStoreError(f"cannot open the exchange store: {error}") from error
        try:
            connection.execute("PRAGMA locking_mode=EXCLUSIVE")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=EXTRA")
            settings = {name: connection.execute(f"PRAGMA {name}").fetchone()[0]
                        for name in ("journal_mode", "synchronous", "locking_mode")}  # fmt: skip
            if settings != _SETTINGS:
                raise DecisionExchangeStoreError("required SQLite synchronization or locking settings are unavailable")
            connection.execute("BEGIN EXCLUSIVE")  # takes and, in EXCLUSIVE locking mode, keeps the lock
            if create:
                connection.execute("CREATE TABLE metadata (schema_version INTEGER NOT NULL, run_json TEXT NOT NULL, "
                                   "fingerprint TEXT NOT NULL)")  # fmt: skip
                connection.execute("CREATE TABLE phases (position INTEGER PRIMARY KEY, ordinal INTEGER NOT NULL, "
                                   "exchange_id TEXT NOT NULL, phase TEXT NOT NULL, record_json TEXT NOT NULL, "
                                   "fingerprint TEXT NOT NULL, UNIQUE (ordinal, phase))")  # fmt: skip
                run_json = _exact_text(self._run_id)
                connection.execute("INSERT INTO metadata VALUES (?,?,?)",
                                   (SCHEMA_VERSION, run_json, _fingerprint(run_json)))  # fmt: skip
            connection.execute("COMMIT")
            self._connection = connection
            self._verify_all()
        except sqlite3.OperationalError as error:
            connection.close()
            self._connection = None
            if "locked" in str(error) or "busy" in str(error):
                raise DecisionExchangeStoreBusy("another connection holds the exchange store") from error
            raise DecisionExchangeStoreError(f"cannot open the exchange store: {error}") from error
        except (DecisionExchangeStoreError, _Unverified, ValueError, sqlite3.Error) as error:
            connection.close()
            self._connection = None
            if isinstance(error, DecisionExchangeStoreError):
                raise
            raise DecisionExchangeStoreError(f"the exchange store failed verification: {error}") from error

    def _verify_all(self) -> None:
        rows = self._connection.execute("SELECT schema_version, run_json, fingerprint FROM metadata").fetchall()
        if len(rows) != 1 or rows[0][0] != SCHEMA_VERSION:
            raise _Unverified("unknown schema version or metadata")
        _, run_json, digest = rows[0]
        if _fingerprint(run_json) != digest:
            raise _Unverified("metadata fingerprint mismatch")
        run_id = decode(loads(run_json))
        if not isinstance(run_id, RunId) or (self._run_id is not None and run_id != self._run_id):
            raise _Unverified("the store belongs to another run")
        self._run_id = run_id
        self._records = self._read()

    def _read(self) -> dict[int, DecisionExchangeRecord]:
        """Every committed row: bound, fingerprint, decode, table columns and phase agreement re-checked."""
        phases: dict[int, dict[str, object]] = {}
        exchanges: dict[str, int] = {}
        query = "SELECT ordinal, exchange_id, phase, record_json, fingerprint FROM phases ORDER BY position"
        for ordinal, exchange_id, phase, text, digest in self._connection.execute(query):
            if len(text.encode("utf-8")) > self._max:
                raise _Unverified(f"a stored row exceeds the {self._max}-byte record bound")
            if _fingerprint(text) != digest:
                raise _Unverified("phase row fingerprint mismatch")
            record = decode(loads(text))
            if phase not in _PHASES or not isinstance(record, _PHASES[phase]):
                raise _Unverified("a stored row is not the phase its column names")
            if record.call != DecisionCallKey(self._run_id, ordinal) or record.exchange_id != exchange_id:
                raise _Unverified("a stored row's columns differ from its record")
            if phase == "request" and exchanges.setdefault(exchange_id, ordinal) != ordinal:
                raise _Unverified("an exchange ID is recorded for two calls")
            phases.setdefault(ordinal, {})[phase] = record
        records = {}
        for ordinal, found in phases.items():
            if "request" not in found:
                raise _Unverified("a response or completion has no recorded request")
            records[ordinal] = DecisionExchangeRecord(found["request"], found.get("response"), found.get("completion"))
        return records

    # --- failure handling -------------------------------------------------------------------------------------------

    def _commit(self) -> None:
        """The single COMMIT step (a labelled fault seam in tests)."""
        self._connection.execute("COMMIT")

    def _fail(self) -> None:
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
        if self._unavailable:
            return False
        if not self._needs_reopen:
            return self._connection is not None
        try:
            self._connect(create=False)
        except DecisionExchangeStoreError:
            self._unavailable = True
            return False
        self._needs_reopen = False
        return True

    @property
    def run_id(self) -> RunId:
        return self._run_id

    @property
    def durability(self) -> str:
        return LOCAL_SQLITE_PROCESS_CRASH

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            self._unavailable = True

    # --- DecisionAuditTrail -----------------------------------------------------------------------------------------

    def begin(self, request: DecisionExchangeRequest) -> AuditWriteResult:
        require_instance(request, DecisionExchangeRequest, "request")
        return self._write("request", request)

    def record_response(self, response: RecordedDecisionResponse) -> AuditWriteResult:
        require_instance(response, RecordedDecisionResponse, "response")
        return self._write("response", response)

    def finish(self, completion: CompletedDecisionExchange) -> AuditWriteResult:
        require_instance(completion, CompletedDecisionExchange, "completion")
        return self._write("completion", completion)

    def read(self, call: DecisionCallKey) -> ExchangeReadResult:
        require_instance(call, DecisionCallKey, "call")
        with self._lock:
            if not self._ready():
                return ExchangeReadUnavailable(call, "the exchange store is unavailable")
            if call.run_id != self._run_id:
                return ExchangeReadUnavailable(call, "the call belongs to another run's store")
            try:
                records = self._read()
            except (sqlite3.Error, _Unverified, ValueError) as error:
                self._fail()
                return ExchangeReadUnavailable(call, f"the exchange store failed verification: {error}")
            record = records.get(call.decision_call_ordinal)
            return ExchangeAbsent(call) if record is None else ExchangeFound(record)

    def _write(self, phase: str, record) -> AuditWriteResult:
        call = record.call
        with self._lock:
            if not self._ready():
                return AuditNotRecorded(call, AuditRefusal.STORE_UNAVAILABLE, "the exchange store is unavailable")
            if call.run_id != self._run_id:
                return AuditNotRecorded(call, AuditRefusal.WRONG_RUN, "the record names another run")
            try:
                text = _exact_text(record)
            except ValueError as error:
                return AuditNotRecorded(call, AuditRefusal.INVALID_RECORD, str(error))
            size = len(text.encode("utf-8"))
            if size > self._max:
                return AuditNotRecorded(call, AuditRefusal.TOO_LARGE,
                                        f"the encoded {phase} is {size} bytes, above the {self._max}-byte bound")  # fmt: skip
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                records = self._read()
                outcome = _admit(phase, record, records)
                if not isinstance(outcome, DecisionExchangeRecord):
                    self._connection.execute("ROLLBACK")
                    return outcome
                self._connection.execute("INSERT INTO phases (ordinal, exchange_id, phase, record_json, fingerprint) "
                                         "VALUES (?,?,?,?,?)", (call.decision_call_ordinal, record.exchange_id, phase,
                                                                text, _fingerprint(text)))  # fmt: skip
                self._commit()
            except (sqlite3.Error, _Unverified, ValueError):
                self._fail()
                return AuditRecordUnknown(call, f"the {phase} write did not confirm its commit")
            except BaseException:
                self._fail()
                raise
            return AuditRecorded(outcome, True)


def _admit(phase: str, record, records: dict[int, DecisionExchangeRecord]):
    """The new exchange record if ``record`` may be appended; otherwise the replay or refusal result."""
    call = record.call
    existing = records.get(call.decision_call_ordinal)
    if phase == "request":
        if existing is not None:
            if existing.request == record:
                return AuditRecorded(existing, False)
            return AuditNotRecorded(call, AuditRefusal.CONFLICT, "another request is recorded for this call")
        if any(r.request.exchange_id == record.exchange_id for r in records.values()):
            return AuditNotRecorded(call, AuditRefusal.CONFLICT, "the exchange ID is recorded for another call")
        return DecisionExchangeRecord(record, None, None)
    if existing is None:
        return AuditNotRecorded(call, AuditRefusal.NO_MATCHING_REQUEST, f"no request is recorded for this {phase}")
    if (existing.request.exchange_id, existing.request.request_sha256) != (record.exchange_id, record.request_sha256):
        return AuditNotRecorded(call, AuditRefusal.NO_MATCHING_REQUEST,
                                f"the {phase} names another exchange or request fingerprint")  # fmt: skip
    current = existing.response if phase == "response" else existing.completion
    if current is not None:
        if current == record:
            return AuditRecorded(existing, False)
        return AuditNotRecorded(call, AuditRefusal.CONFLICT, f"another {phase} is recorded for this exchange")
    if phase == "response":
        if existing.completion is not None:
            return AuditNotRecorded(call, AuditRefusal.ALREADY_COMPLETED, "the exchange is already completed")
        candidate = (existing.request, record, None)
    else:
        candidate = (existing.request, existing.response, record)
    try:
        return DecisionExchangeRecord(*candidate)
    except ValueError as error:
        return AuditNotRecorded(call, AuditRefusal.INVALID_RECORD, str(error))
