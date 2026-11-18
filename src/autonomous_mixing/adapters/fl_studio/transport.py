"""WinMM SysEx ownership. DLL loading and device access occur only on construction."""

import ctypes
import sys
import time
from collections import deque
from ctypes import wintypes
from typing import Protocol

from autonomous_mixing.adapters.fl_studio.protocol import MAX_MESSAGE_BYTES


class MidiTransport(Protocol):
    def send(self, raw: bytes) -> None: ...
    def receive(self, timeout_s: float) -> bytes | None: ...
    def close(self) -> None: ...


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
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * 32),
        ("dwSupport", wintypes.DWORD),
    ]


class MIDIOUTCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * 32),
        ("wTechnology", wintypes.WORD),
        ("wVoices", wintypes.WORD),
        ("wNotes", wintypes.WORD),
        ("wChannelMask", wintypes.WORD),
        ("dwSupport", wintypes.DWORD),
    ]


MHDR_DONE = 1
_HANDLE = ctypes.c_void_p
_SIZE = ctypes.c_size_t
_UINT = wintypes.UINT
_HEADER = ctypes.POINTER(MIDIHDR)
_SIGNATURES = {
    "midiInGetNumDevs": [],
    "midiOutGetNumDevs": [],
    "midiInGetDevCapsW": [_SIZE, ctypes.POINTER(MIDIINCAPSW), _UINT],
    "midiOutGetDevCapsW": [_SIZE, ctypes.POINTER(MIDIOUTCAPSW), _UINT],
    "midiInOpen": [ctypes.POINTER(_HANDLE), _UINT, _SIZE, _SIZE, wintypes.DWORD],
    "midiOutOpen": [ctypes.POINTER(_HANDLE), _UINT, _SIZE, _SIZE, wintypes.DWORD],
    **{
        name: [_HANDLE, _HEADER, _UINT]
        for name in (
            "midiInPrepareHeader",
            "midiInUnprepareHeader",
            "midiInAddBuffer",
            "midiOutPrepareHeader",
            "midiOutUnprepareHeader",
            "midiOutLongMsg",
        )
    },
    **{
        name: [_HANDLE]
        for name in ("midiInStart", "midiInStop", "midiInReset", "midiInClose", "midiOutReset", "midiOutClose")
    },
}
# Error-only quarantine: failed driver cleanup must not let Python release driver-owned memory.
# A successful close removes the object. Otherwise it remains retained until process exit.
_quarantined = []


def _load_winmm():
    if sys.platform != "win32":
        raise OSError("WinMM transport requires Windows")
    return ctypes.WinDLL("winmm")


def _check(result, operation):
    if result != 0:
        raise OSError(f"{operation} failed with MMRESULT {result}")


def _port_names(lib):
    inputs, outputs = [], []
    for name, caps_type, target in (("In", MIDIINCAPSW, inputs), ("Out", MIDIOUTCAPSW, outputs)):
        for index in range(getattr(lib, f"midi{name}GetNumDevs")()):
            caps = caps_type()
            _check(
                getattr(lib, f"midi{name}GetDevCapsW")(index, ctypes.byref(caps), ctypes.sizeof(caps)), "device caps"
            )
            target.append(caps.szPname)
    return inputs, outputs


class TransportCleanupError(OSError):
    """Partial native construction left retained resources; no further transport may be opened."""

    def __init__(self, message: str, transport):
        super().__init__(message)
        self.transport = transport


class WinMMTransport:
    """Exact-name ports, one outstanding send, and retained buffers until native ownership ends."""

    def __init__(self, out_name: str, in_name: str):
        self.lib = _load_winmm()
        for name, arguments in _SIGNATURES.items():
            function = getattr(self.lib, name)
            function.argtypes, function.restype = arguments, _UINT
        self._hin, self._hout = _HANDLE(), _HANDLE()
        self._input_open = self._output_open = False
        self._storage, self._queue, self._outgoing = [], deque(), None
        inputs, outputs = _port_names(self.lib)
        for names, wanted in ((inputs, in_name), (outputs, out_name)):
            if names.count(wanted) != 1:
                raise OSError(f"missing or ambiguous MIDI port {wanted!r}")
        try:
            _check(self.lib.midiOutOpen(ctypes.byref(self._hout), outputs.index(out_name), 0, 0, 0), "open output")
            self._output_open = True
            _check(self.lib.midiInOpen(ctypes.byref(self._hin), inputs.index(in_name), 0, 0, 0), "open input")
            self._input_open = True
            for _ in range(4):
                data = ctypes.create_string_buffer(MAX_MESSAGE_BYTES)
                header = MIDIHDR(lpData=ctypes.cast(data, ctypes.c_void_p), dwBufferLength=MAX_MESSAGE_BYTES)
                _check(
                    self.lib.midiInPrepareHeader(self._hin, ctypes.byref(header), ctypes.sizeof(header)),
                    "prepare input",
                )
                self._storage.append((data, header))
                _check(self.lib.midiInAddBuffer(self._hin, ctypes.byref(header), ctypes.sizeof(header)), "queue input")
                self._queue.append(header)
            _check(self.lib.midiInStart(self._hin), "start input")
        except BaseException:
            try:
                self.close()
            except OSError as cleanup_error:
                raise TransportCleanupError(
                    "partial construction cleanup failed: " + str(cleanup_error), self
                ) from cleanup_error
            raise

    def send(self, raw: bytes) -> None:
        if not self._output_open or self._outgoing is not None:
            raise OSError("output closed or previous buffer unresolved")
        data = ctypes.create_string_buffer(raw, len(raw))
        header = MIDIHDR(lpData=ctypes.cast(data, ctypes.c_void_p), dwBufferLength=len(raw))
        _check(self.lib.midiOutPrepareHeader(self._hout, ctypes.byref(header), ctypes.sizeof(header)), "prepare output")
        self._outgoing = (data, header)
        try:
            _check(self.lib.midiOutLongMsg(self._hout, ctypes.byref(header), ctypes.sizeof(header)), "send output")
            deadline = time.monotonic() + 2.0
            while not header.dwFlags & MHDR_DONE:
                if time.monotonic() >= deadline:
                    raise OSError("native send did not finish before its cooperative deadline")
                time.sleep(0.001)
            _check(
                self.lib.midiOutUnprepareHeader(self._hout, ctypes.byref(header), ctypes.sizeof(header)),
                "unprepare output",
            )
            self._outgoing = None
        except BaseException:
            try:
                _check(self.lib.midiOutReset(self._hout), "reset output")
                _check(
                    self.lib.midiOutUnprepareHeader(self._hout, ctypes.byref(header), ctypes.sizeof(header)),
                    "unprepare output",
                )
                self._outgoing = None
            except OSError:
                if self not in _quarantined:
                    _quarantined.append(self)
            raise

    def receive(self, timeout_s: float) -> bytes | None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not self._input_open:
                raise OSError("input closed")
            header = self._queue[0]
            if header.dwFlags & MHDR_DONE:
                self._queue.popleft()
                raw = ctypes.string_at(header.lpData, header.dwBytesRecorded)
                header.dwFlags &= ~MHDR_DONE  # retain MHDR_PREPARED while reusing the native buffer
                header.dwBytesRecorded = 0
                _check(
                    self.lib.midiInAddBuffer(self._hin, ctypes.byref(header), ctypes.sizeof(header)), "requeue input"
                )
                self._queue.append(header)
                if raw:
                    return raw
            time.sleep(0.002)
        return None

    def close(self) -> None:
        try:
            if self._input_open:
                _check(self.lib.midiInStop(self._hin), "stop input")
                _check(self.lib.midiInReset(self._hin), "reset input")
                while self._storage:
                    _, header = self._storage[-1]
                    _check(
                        self.lib.midiInUnprepareHeader(self._hin, ctypes.byref(header), ctypes.sizeof(header)),
                        "unprepare input",
                    )
                    self._storage.pop()
                _check(self.lib.midiInClose(self._hin), "close input")
                self._input_open = False
            if self._output_open:
                _check(self.lib.midiOutReset(self._hout), "reset output")
                if self._outgoing is not None:
                    _, header = self._outgoing
                    _check(
                        self.lib.midiOutUnprepareHeader(self._hout, ctypes.byref(header), ctypes.sizeof(header)),
                        "unprepare output",
                    )
                    self._outgoing = None
                _check(self.lib.midiOutClose(self._hout), "close output")
                self._output_open = False
        except OSError:
            if self not in _quarantined:
                _quarantined.append(self)
            raise
        if self in _quarantined:
            _quarantined.remove(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
