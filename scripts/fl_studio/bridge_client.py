"""External Python side of the FL Studio command bridge (contract: docs/research/fl-studio-bridge-contract.md, v1).

Usage (repository root, Windows, loopMIDI ports created as in the contract):

    uv run python scripts/fl_studio/bridge_client.py ports
    uv run python scripts/fl_studio/bridge_client.py inspect --project-title "Thesis Bass Bridge Test" \
        --insert-index 2 --insert-name Bass [--log <file.jsonl>] [--out ...] [--in ...] [--timeout 2.0]
    uv run python scripts/fl_studio/bridge_client.py set-gain --project-title "Thesis Bass Bridge Test" \
        --insert-index 2 --insert-name Bass --expected-normalized 0.78 --expected-db -0.574523 \
        --target-normalized 0.76 --log <file.jsonl> [--command-id <id>] [--out ...] [--in ...] [--timeout 2.0]
    uv run python scripts/fl_studio/bridge_client.py set-gain-db --project-title "Thesis Bass Bridge Test" \
        --insert-index 2 --insert-name Bass --expected-normalized <from inspect> --expected-db <from inspect> \
        --db-change -1.0 --log <file.jsonl> [--command-id <id>] [--out ...] [--in ...] [--timeout 2.0]
    uv run python scripts/fl_studio/bridge_client.py calibrate --log <file.jsonl> [--run-id <id>] [--out ...] ...
    uv run python scripts/fl_studio/bridge_client.py calibration-restore --log <file.jsonl> --run-id <id> \
        [--start-log <calibration log>] [...]

``set-gain-db`` computes the one absolute target from the expected (inspected) dB read-back plus ``--db-change``
with the shared conversion ``bridge_protocol.CONVERSION_ID``; the script recomputes it and refuses disagreement.
An ``applied_off_target`` reply (fader where commanded, dB change off by more than 0.05 dB) is a known write: it
does not set the write guard but sets the **conversion block**, so no further ``set-gain-db`` with that conversion is
sent (rebuilt from the log); normalized ``set-gain`` stays available.

``calibrate`` runs the pinned calibration plan (``bridge_protocol.CALIBRATION_*``): a fresh inspect of the plan's
insert, one ``probe_gain`` per sequence position in order (each expecting the previous read-back), then a restore
probe to the exact starting normalized gain from that inspect and a final inspect. It stops at the first probe that is
not ``applied``. After a known refusal (nothing written) it still restores; after an unknown outcome (timeout,
invalid reply, ``uncertain``) it stops without writing, and ``calibration-restore`` later inspects and restores to
the start recorded in the log. Evaluation is separate: ``evaluate_calibration.py``.

Sends one command and waits for the reply with the same ID. Replies with other IDs are recorded as stray and
ignored; undecodable replies are recorded as malformed and ignored. A reply that carries the command's ID but fails
validation, or contradicts the command (``reply_consistency``), ends the command as ``invalid_reply`` and is not
accepted. With no matching reply within the timeout the outcome is ``timeout``.

Write guard: after a write whose outcome is ``timeout``, ``invalid_reply`` or an ``uncertain`` reply, no further
write is sent until an ``inspect`` of the same project title and insert returns ``ok``. Every write is journaled as
``pending`` *before* it is dispatched, and a pending write blocks like an uncertain one until its own outcome
(any accepted reply that is not ``uncertain``) or an ``ok`` inspect resolves it; so a process that dies, or a send
that fails, between dispatch and logging still leaves the guard set. ``set-gain`` therefore requires ``--log``: the
pending record and the outcome go there, and the guard is rebuilt from it before every write.
``--command-id`` resends a command with a chosen ID (duplicate and conflict trials).

Exit codes: 0 valid matching reply (any status except ``uncertain``), 2 timeout or ``uncertain`` reply (inspect
before writing again), 3 ports could not be opened, 4 invalid reply, 5 write blocked by the guard, 6 command refused
locally (validation or bounds), 8 ``applied_off_target``; ``calibrate``/``calibration-restore``: 0 completed with
the exact start restored, 7 otherwise. The MIDI transport uses ``winmm.dll`` through ``ctypes``: standard library only.
"""

import argparse
import ctypes
import dataclasses
import json
import pathlib
import re
import sys
import time
import uuid
from collections import deque
from ctypes import wintypes

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bridge_protocol as protocol  # noqa: E402  (sibling module, shared with the FL Studio script)

DEFAULT_OUT, DEFAULT_IN = "Thesis Bridge Cmd", "Thesis Bridge Reply"
DEFAULT_TIMEOUT_S = 2.0  # pilot setting: FL Studio handles MIDI in its event loop


@dataclasses.dataclass
class Outcome:
    """Result of one command: ``replied`` (valid matching reply), ``invalid_reply``, ``timeout`` or ``blocked``."""

    status: str
    command: dict
    reply: dict | None
    elapsed_s: float
    stray_replies: list
    malformed_replies: list


def next_write_block(block: dict | None, status: str, command: dict, reply: dict | None) -> dict | None:
    """The write guard after one outcome (or a ``pending`` record, written before a write is dispatched).

    A pending write, or a write whose effect is unknown (timeout, invalid reply, ``uncertain``), sets the block. A
    known outcome of that same write (an accepted reply other than ``uncertain``) resolves its pending block; an
    ``ok`` inspect of the blocked project title and insert clears any block. Everything else leaves it as it was.
    """
    target = {"project_title": command["project_title"], "insert": command["insert"]}
    if command["type"] in protocol.WRITE_TYPES:
        if status == "blocked":
            return block  # not sent
        unknown = status in ("pending", "timeout", "invalid_reply") or reply["status"] == "uncertain"
        if unknown:
            return {**target, "command_id": command["id"], "reason": reply["error"] if reply else status}
        if block is not None and block["command_id"] == command["id"]:
            return None  # this write's own known outcome
        return block
    if block is not None and status == "replied" and reply["status"] == "ok":
        if target == {"project_title": block["project_title"], "insert": block["insert"]}:
            return None
    return block


def next_conversion_block(block: dict | None, status: str, command: dict, reply: dict | None) -> dict | None:
    """A known dB write that missed its requested change blocks further ``set_gain_db`` with that conversion."""
    if command["type"] == "set_gain_db" and status == "replied" and reply["status"] == "applied_off_target":
        return {"conversion": command["conversion"], "command_id": command["id"], "reason": reply["error"]}
    return block


def conversion_block_from_records(records) -> dict | None:
    block = None
    for record in records:
        block = next_conversion_block(block, record["status"], record["command"], record.get("reply"))
    return block


def write_block_from_records(records) -> dict | None:
    """Rebuild the write guard from logged outcomes (oldest first)."""
    block = None
    for record in records:
        block = next_write_block(block, record["status"], record["command"], record.get("reply"))
    return block


class BridgeClient:
    def __init__(self, transport, clock=time.monotonic, write_block: dict | None = None, journal=None,
                 conversion_block: dict | None = None):  # fmt: skip
        """``journal(record)``, if given, receives a ``pending`` record for every write before it is dispatched."""
        self.transport, self.clock, self.write_block, self.journal = transport, clock, write_block, journal
        self.conversion_block = conversion_block

    def inspect(self, project_title: str, insert_index: int, insert_name: str, timeout_s: float = DEFAULT_TIMEOUT_S,
                command_id: str | None = None) -> Outcome:  # fmt: skip
        command = protocol.inspect_command(command_id or uuid.uuid4().hex, project_title, insert_index, insert_name)
        return self.request(command, timeout_s)

    def set_gain(self, project_title: str, insert_index: int, insert_name: str, expected_normalized: float,
                 expected_db: float, target_normalized: float, timeout_s: float = DEFAULT_TIMEOUT_S,
                 command_id: str | None = None) -> Outcome:  # fmt: skip
        """One bounded absolute write; raises ProtocolError (nothing sent) if the command is invalid or out of bounds."""
        command = protocol.set_gain_command(command_id or uuid.uuid4().hex, project_title, insert_index, insert_name,
                                            expected_normalized, expected_db, target_normalized)  # fmt: skip
        if self.write_block is not None:
            return Outcome("blocked", command, None, 0.0, [], [])
        return self.request(command, timeout_s)

    def set_gain_db(self, project_title: str, insert_index: int, insert_name: str, expected_normalized: float,
                    expected_db: float, db_change: float, timeout_s: float = DEFAULT_TIMEOUT_S,
                    command_id: str | None = None) -> Outcome:  # fmt: skip
        """One calibrated dB cut; raises ProtocolError (nothing sent) for an out-of-bounds or out-of-range request."""
        command = protocol.set_gain_db_command(command_id or uuid.uuid4().hex, project_title, insert_index,
                                               insert_name, expected_normalized, expected_db, db_change)  # fmt: skip
        blocked = self.conversion_block is not None and self.conversion_block["conversion"] == command["conversion"]
        if self.write_block is not None or blocked:
            return Outcome("blocked", command, None, 0.0, [], [])
        return self.request(command, timeout_s)

    def probe(self, step, expected_normalized: float, expected_db: float, timeout_s: float = DEFAULT_TIMEOUT_S,
              command_id: str | None = None, target_normalized: float | None = None) -> Outcome:  # fmt: skip
        """One calibration probe of the pinned plan (``step`` index, or ``"restore"`` with an explicit target)."""
        command = protocol.probe_command(command_id or uuid.uuid4().hex, expected_normalized, expected_db, step,
                                         target_normalized)  # fmt: skip
        if self.write_block is not None:
            return Outcome("blocked", command, None, 0.0, [], [])
        return self.request(command, timeout_s)

    def request(self, command: dict, timeout_s: float) -> Outcome:
        if command["type"] in protocol.WRITE_TYPES:
            pending = Outcome("pending", command, None, 0.0, [], [])
            if self.journal is not None:
                self.journal(_record(pending))  # before dispatch: a failure here sends nothing
            self.write_block = next_write_block(self.write_block, "pending", command, None)
        outcome = self._exchange(command, timeout_s)  # an exception leaves the write pending (and blocked)
        self.write_block = next_write_block(self.write_block, outcome.status, command, outcome.reply)
        self.conversion_block = next_conversion_block(self.conversion_block, outcome.status, command, outcome.reply)
        return outcome

    def _exchange(self, command: dict, timeout_s: float) -> Outcome:
        start = self.clock()
        self.transport.send(protocol.encode(command))
        strays, malformed = [], []
        while True:
            remaining = timeout_s - (self.clock() - start)
            if remaining <= 0:
                return Outcome("timeout", command, None, self.clock() - start, strays, malformed)
            raw = self.transport.receive(remaining)
            if raw is None:
                continue  # the loop re-checks the deadline
            if not protocol.is_bridge_sysex(raw):
                continue  # traffic of other devices on the reply port
            try:
                decoded = protocol.decode(raw)
            except protocol.ProtocolError as exc:
                malformed.append({"error": exc.code, "bytes": bytes(raw).hex()})
                continue
            mine = decoded.get("id") == command["id"]
            try:
                message = protocol.validate_reply(decoded)
                problem = protocol.reply_consistency(command, message) if mine else None
            except protocol.ProtocolError as exc:
                problem, message = exc.code, None
            if problem is not None:
                malformed.append({"error": problem, "bytes": bytes(raw).hex()})
                if mine:  # the script answered this command, but not acceptably: stop, do not wait for more
                    return Outcome("invalid_reply", command, None, self.clock() - start, strays, malformed)
                continue
            if not mine:
                strays.append(message)  # a late reply to an earlier command, or another client's
                continue
            return Outcome("replied", command, message, self.clock() - start, strays, malformed)


# --- Windows MIDI transport (winmm.dll via ctypes) -----------------------------------------------------------------

MHDR_DONE = 0x00000001
CALLBACK_NULL = 0
MMSYSERR_NOERROR = 0


class MIDIHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_size_t),
        ("dwFlags", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_size_t),
        ("dwOffset", wintypes.DWORD),
        ("dwReserved", ctypes.c_size_t * 8),
    ]


class MIDIINCAPSW(ctypes.Structure):
    _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                ("szPname", wintypes.WCHAR * 32), ("dwSupport", wintypes.DWORD)]  # fmt: skip


class MIDIOUTCAPSW(ctypes.Structure):
    _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                ("szPname", wintypes.WCHAR * 32), ("wTechnology", wintypes.WORD), ("wVoices", wintypes.WORD),
                ("wNotes", wintypes.WORD), ("wChannelMask", wintypes.WORD), ("dwSupport", wintypes.DWORD)]  # fmt: skip


def _winmm():
    if sys.platform != "win32":
        raise OSError("the WinMM transport needs Windows")
    return ctypes.WinDLL("winmm")


def list_ports() -> dict:
    lib = _winmm()
    inputs, outputs = [], []
    for i in range(lib.midiInGetNumDevs()):
        caps = MIDIINCAPSW()
        if lib.midiInGetDevCapsW(ctypes.c_size_t(i), ctypes.byref(caps), ctypes.sizeof(caps)) == MMSYSERR_NOERROR:
            inputs.append(caps.szPname)
    for i in range(lib.midiOutGetNumDevs()):
        caps = MIDIOUTCAPSW()
        if lib.midiOutGetDevCapsW(ctypes.c_size_t(i), ctypes.byref(caps), ctypes.sizeof(caps)) == MMSYSERR_NOERROR:
            outputs.append(caps.szPname)
    return {"inputs": inputs, "outputs": outputs}


def _check(result: int, what: str) -> None:
    if result != MMSYSERR_NOERROR:
        raise OSError(f"{what} failed with MMRESULT {result}")


class WinMMTransport:
    """One SysEx output port and one SysEx input port, opened by exact name; input polled without callbacks."""

    def __init__(self, out_name: str, in_name: str, buffers: int = 4, buffer_bytes: int = protocol.MAX_MESSAGE_BYTES):
        self.lib = _winmm()
        ports = list_ports()
        if out_name not in ports["outputs"] or in_name not in ports["inputs"]:
            raise OSError(f"ports not found: output {out_name!r}, input {in_name!r}; available {ports}")
        self.hout, self.hin = ctypes.c_void_p(), ctypes.c_void_p()
        _check(self.lib.midiOutOpen(ctypes.byref(self.hout), ports["outputs"].index(out_name), 0, 0, CALLBACK_NULL),
               "midiOutOpen")  # fmt: skip
        try:
            _check(self.lib.midiInOpen(ctypes.byref(self.hin), ports["inputs"].index(in_name), 0, 0, CALLBACK_NULL),
                   "midiInOpen")  # fmt: skip
        except OSError:
            self.lib.midiOutClose(self.hout)
            raise
        self.queue = deque()
        self.storage = []
        for _ in range(buffers):
            data = ctypes.create_string_buffer(buffer_bytes)
            header = MIDIHDR(lpData=ctypes.cast(data, ctypes.c_void_p), dwBufferLength=buffer_bytes)
            self.storage.append((data, header))
            self._add(header)
        _check(self.lib.midiInStart(self.hin), "midiInStart")

    def _add(self, header: MIDIHDR) -> None:
        header.dwFlags, header.dwBytesRecorded = 0, 0
        _check(self.lib.midiInPrepareHeader(self.hin, ctypes.byref(header), ctypes.sizeof(header)), "prepare in")
        _check(self.lib.midiInAddBuffer(self.hin, ctypes.byref(header), ctypes.sizeof(header)), "midiInAddBuffer")
        self.queue.append(header)

    def send(self, raw: bytes) -> None:
        data = ctypes.create_string_buffer(raw, len(raw))
        header = MIDIHDR(lpData=ctypes.cast(data, ctypes.c_void_p), dwBufferLength=len(raw))
        _check(self.lib.midiOutPrepareHeader(self.hout, ctypes.byref(header), ctypes.sizeof(header)), "prepare out")
        try:
            _check(self.lib.midiOutLongMsg(self.hout, ctypes.byref(header), ctypes.sizeof(header)), "midiOutLongMsg")
            deadline = time.monotonic() + 2.0
            while not header.dwFlags & MHDR_DONE and time.monotonic() < deadline:
                time.sleep(0.001)
        finally:
            self.lib.midiOutUnprepareHeader(self.hout, ctypes.byref(header), ctypes.sizeof(header))

    def receive(self, timeout_s: float) -> bytes | None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            header = self.queue[0]
            if header.dwFlags & MHDR_DONE:
                self.queue.popleft()
                raw = ctypes.string_at(header.lpData, header.dwBytesRecorded)
                self.lib.midiInUnprepareHeader(self.hin, ctypes.byref(header), ctypes.sizeof(header))
                self._add(header)
                if raw:
                    return raw
                continue
            time.sleep(0.002)
        return None

    def close(self) -> None:
        self.lib.midiInStop(self.hin)
        self.lib.midiInReset(self.hin)  # returns all queued buffers
        for _, header in self.storage:
            self.lib.midiInUnprepareHeader(self.hin, ctypes.byref(header), ctypes.sizeof(header))
        self.lib.midiInClose(self.hin)
        self.lib.midiOutReset(self.hout)
        self.lib.midiOutClose(self.hout)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# --- command line ---------------------------------------------------------------------------------------------------


def _read_log(path: pathlib.Path) -> list:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _record(outcome: Outcome) -> dict:
    return {"at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **dataclasses.asdict(outcome)}


def _append(path: pathlib.Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def record_of(outcome: Outcome) -> dict:
    """The log record of an outcome (as the CLI appends it)."""
    return _record(outcome)


# --- calibration runner -------------------------------------------------------------------------------------------

_RUN_ID = re.compile(r"[A-Za-z0-9_]{1,40}")  # no '-': probe IDs are "cal-<run>-<step>"
_NOTHING_WRITTEN = ("precondition_failed", "identity_mismatch", "invalid_command", "unsupported", "error")


def probe_id(run_id: str, step) -> str:
    return f"cal-{run_id}-{step:02d}" if isinstance(step, int) else f"cal-{run_id}-{step}"


def _check_run_id(run_id: str) -> None:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must be 1-40 characters of A-Z, a-z, 0-9 or '_'")


def _gain_of(outcome: Outcome) -> dict | None:
    if outcome.status == "replied" and outcome.reply["status"] == "ok":
        return dict(outcome.reply["observed"]["gain"])
    return None


def _reason(outcome: Outcome) -> str:
    if outcome.status != "replied":
        return outcome.status
    if outcome.reply["status"] in ("uncertain", "applied", "ok"):
        return outcome.reply["status"]
    return f"{outcome.reply['status']}:{outcome.reply['error']}"


def _inspect_plan(client, timeout_s, emit) -> Outcome:
    insert = protocol.CALIBRATION_INSERT
    outcome = client.inspect(protocol.CALIBRATION_PROJECT_TITLE, insert["index"], insert["name"], timeout_s)
    emit(outcome)
    return outcome


def _restore(client, run_id: str, attempt: int, current: dict, start: dict, timeout_s, emit) -> dict:
    """Restore probe to ``start["normalized"]`` (exact), then a final inspect; reports whether the start is back."""
    outcome = client.probe(protocol.RESTORE_STEP, current["normalized"], current["db"], timeout_s,
                           probe_id(run_id, f"restore-{attempt}"), start["normalized"])  # fmt: skip
    emit(outcome)
    result = {"restore": _reason(outcome), "final": None, "restored_exact": False}
    if not (outcome.status == "replied" and outcome.reply["status"] == "applied"):
        return result
    final = _gain_of(_inspect_plan(client, timeout_s, emit))
    result["final"] = final
    result["restored_exact"] = final is not None and final == {"normalized": start["normalized"], "db": start["db"]}
    return result


def run_calibration(client, run_id: str, timeout_s: float = DEFAULT_TIMEOUT_S, on_outcome=None) -> dict:
    """Run the pinned plan once (see module docstring); ``on_outcome`` receives every outcome as it happens."""
    _check_run_id(run_id)
    emit = on_outcome or (lambda outcome: None)
    summary = {"run_id": run_id, "plan": protocol.CALIBRATION_PLAN_ID, "status": "aborted", "reason": None,
               "start": None, "steps_applied": 0, "restore": None, "final": None, "restored_exact": False}  # fmt: skip
    first = _inspect_plan(client, timeout_s, emit)
    start = _gain_of(first)
    if start is None:
        summary["reason"] = f"inspect_failed:{_reason(first)}"
        return summary
    if client.write_block is not None:
        summary["reason"] = "blocked"
        return summary
    summary["start"], current = start, start
    for step in range(len(protocol.CALIBRATION_SEQUENCE)):
        outcome = client.probe(step, current["normalized"], current["db"], timeout_s, probe_id(run_id, step))
        emit(outcome)
        if outcome.status == "replied" and outcome.reply["status"] == "applied":
            current = dict(outcome.reply["observed"]["after"])
            summary["steps_applied"] += 1
            continue
        summary["reason"] = _reason(outcome)
        known = outcome.status == "replied" and outcome.reply["status"] in _NOTHING_WRITTEN
        if not known:
            return summary  # the fader's state is unknown: no restore write; inspect, then calibration-restore
        break
    summary.update(_restore(client, run_id, 1, current, start, timeout_s, emit))
    if summary["reason"] is None and summary["restored_exact"]:
        summary["status"] = "completed"
    return summary


def restore_calibration(client, records, run_id: str, timeout_s: float = DEFAULT_TIMEOUT_S, on_outcome=None,
                        records_for_attempts=None) -> dict:  # fmt: skip
    """Restore a run's start from the log: fresh inspect (clears the guard if ok), then one restore probe.

    ``records`` supply the start (the run's step-0 ``expected``); restore attempts are numbered from ``records`` plus
    ``records_for_attempts`` (the log being written, when it is another file)."""
    _check_run_id(run_id)
    emit = on_outcome or (lambda outcome: None)
    first_id = probe_id(run_id, 0)
    starts = [r["command"]["expected"] for r in records if r["command"].get("id") == first_id]
    if not starts:
        return {"status": "failed", "reason": "no_start_in_log", "restored_exact": False}
    start = dict(starts[0])
    attempts = {r["command"]["id"] for r in [*records, *(records_for_attempts or [])]
                if str(r["command"].get("id", "")).startswith(f"cal-{run_id}-restore-")}  # fmt: skip
    current = _gain_of(_inspect_plan(client, timeout_s, emit))
    if current is None or client.write_block is not None:
        return {"status": "failed", "reason": "inspect_failed", "restored_exact": False}
    result = _restore(client, run_id, len(attempts) + 1, current, start, timeout_s, emit)
    return {"status": "restored" if result["restored_exact"] else "failed", "start": start, **result}


def _exit_code(outcome: Outcome) -> int:
    if outcome.status == "replied":
        return {"uncertain": 2, "applied_off_target": 8}.get(outcome.reply["status"], 0)
    return {"timeout": 2, "invalid_reply": 4, "blocked": 5}[outcome.status]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ports", help="list WinMM MIDI input and output port names")
    for name, text in (("inspect", "send one inspect command"), ("set-gain", "send one bounded absolute write"),
                       ("set-gain-db", "send one calibrated dB cut")):  # fmt: skip
        p = sub.add_parser(name, help=f"{text} and wait for its reply")
        p.add_argument("--project-title", required=True)
        p.add_argument("--insert-index", type=int, required=True)
        p.add_argument("--insert-name", required=True)
        p.add_argument("--out", default=DEFAULT_OUT)
        p.add_argument("--in", dest="in_", default=DEFAULT_IN)
        p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
        p.add_argument("--command-id")
        if name in ("set-gain", "set-gain-db"):
            p.add_argument("--expected-normalized", type=float, required=True)
            p.add_argument("--expected-db", type=float, required=True)
            if name == "set-gain":
                p.add_argument("--target-normalized", type=float, required=True)
            else:
                p.add_argument("--db-change", type=float, required=True, help="requested change, -3.0 to -0.1 dB")
            p.add_argument("--log", type=pathlib.Path, required=True, help="JSON-lines log; also the write guard")
        else:
            p.add_argument("--log", type=pathlib.Path)
    for name, text in (("calibrate", "run the pinned calibration plan"),
                       ("calibration-restore", "restore a calibration run's start from the log")):  # fmt: skip
        p = sub.add_parser(name, help=text)
        p.add_argument("--log", type=pathlib.Path, required=True, help="JSON-lines log; also the write guard")
        p.add_argument("--run-id", required=name == "calibration-restore")
        if name == "calibration-restore":
            p.add_argument("--start-log", type=pathlib.Path, help="log holding the run's start (default: --log)")
        p.add_argument("--out", default=DEFAULT_OUT)
        p.add_argument("--in", dest="in_", default=DEFAULT_IN)
        p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    args = parser.parse_args(argv)
    if args.command == "ports":
        print(json.dumps(list_ports(), indent=2))
        return 0
    if args.command in ("calibrate", "calibration-restore"):
        return _calibration_main(args)
    records = _read_log(args.log) if args.log else []
    block = write_block_from_records(records)
    conversion_block = conversion_block_from_records(records)
    try:
        if args.command == "set-gain-db":
            protocol.set_gain_db_command(args.command_id or "0" * 8, args.project_title, args.insert_index,
                                         args.insert_name, args.expected_normalized, args.expected_db,
                                         args.db_change)  # fmt: skip
        elif args.command == "set-gain":
            protocol.set_gain_command(args.command_id or "0" * 8, args.project_title, args.insert_index,
                                      args.insert_name, args.expected_normalized, args.expected_db,
                                      args.target_normalized)  # fmt: skip
        else:
            protocol.inspect_command(args.command_id or "0" * 8, args.project_title, args.insert_index,
                                     args.insert_name)  # fmt: skip
    except protocol.ProtocolError as exc:
        print(f"bridge_client: command refused locally: {exc}", file=sys.stderr)
        return 6
    if args.command in ("set-gain", "set-gain-db") and block is not None:
        print(f"bridge_client: write blocked: an earlier write's outcome is unknown ({block}); run inspect on that "
              "project title and insert first", file=sys.stderr)  # fmt: skip
        return 5
    if args.command == "set-gain-db" and conversion_block is not None:
        print(f"bridge_client: conversion blocked: an earlier dB write missed its target ({conversion_block}); "
              "re-calibrate before another set-gain-db", file=sys.stderr)  # fmt: skip
        return 5
    try:
        transport = WinMMTransport(args.out, args.in_)
    except OSError as exc:
        print(f"bridge_client: {exc}", file=sys.stderr)
        return 3
    with transport:
        journal = (lambda record: _append(args.log, record)) if args.log else None
        client = BridgeClient(transport, write_block=block, journal=journal, conversion_block=conversion_block)
        if args.command == "set-gain-db":
            outcome = client.set_gain_db(args.project_title, args.insert_index, args.insert_name,
                                         args.expected_normalized, args.expected_db, args.db_change, args.timeout,
                                         args.command_id)  # fmt: skip
        elif args.command == "set-gain":
            outcome = client.set_gain(args.project_title, args.insert_index, args.insert_name,
                                      args.expected_normalized, args.expected_db, args.target_normalized,
                                      args.timeout, args.command_id)  # fmt: skip
        else:
            outcome = client.inspect(args.project_title, args.insert_index, args.insert_name, args.timeout,
                                     args.command_id)  # fmt: skip
    record = _record(outcome)
    if args.log:
        _append(args.log, record)
    print(json.dumps(record, indent=2))
    return _exit_code(outcome)


def _calibration_main(args) -> int:
    run_id = args.run_id or time.strftime("r%Y%m%dT%H%M%SZ", time.gmtime())
    try:
        _check_run_id(run_id)
    except ValueError as exc:
        print(f"bridge_client: {exc}", file=sys.stderr)
        return 6
    records = _read_log(args.log)
    try:
        transport = WinMMTransport(args.out, args.in_)
    except OSError as exc:
        print(f"bridge_client: {exc}", file=sys.stderr)
        return 3

    def emit(outcome):
        _append(args.log, _record(outcome))

    with transport:
        client = BridgeClient(transport, write_block=write_block_from_records(records),
                              journal=lambda record: _append(args.log, record))  # fmt: skip
        if args.command == "calibrate":
            result = run_calibration(client, run_id, args.timeout, emit)
            ok = result["status"] == "completed"
        else:
            start_records = _read_log(args.start_log) if args.start_log else records
            result = restore_calibration(client, start_records, run_id, args.timeout, emit,
                                         records_for_attempts=records if args.start_log else None)  # fmt: skip
            ok = result["status"] == "restored"
    print(json.dumps(result, indent=2))
    return 0 if ok else 7


if __name__ == "__main__":
    sys.exit(main())
