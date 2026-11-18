"""WinMM ownership under a fake DLL; never enumerates or opens real devices."""

import ctypes

import pytest

from autonomous_mixing.adapters.fl_studio import transport as native


class Function:
    def __init__(self, callback=lambda *args: 0):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class Library:
    def __init__(self):
        self.calls = []
        self.fail = None
        self.done = True
        for name in native._SIGNATURES:
            setattr(self, name, Function(lambda *args, name=name: self.call(name, *args)))

    def call(self, name, *args):
        self.calls.append(name)
        if name == self.fail or (isinstance(self.fail, tuple) and name in self.fail):
            return 1
        if name in ("midiInGetNumDevs", "midiOutGetNumDevs"):
            return 1
        if name == "midiInGetDevCapsW":
            ctypes.cast(args[1], ctypes.POINTER(native.MIDIINCAPSW)).contents.szPname = "Reply"
        if name == "midiOutGetDevCapsW":
            ctypes.cast(args[1], ctypes.POINTER(native.MIDIOUTCAPSW)).contents.szPname = "Cmd"
        if name in ("midiInOpen", "midiOutOpen"):
            ctypes.cast(args[0], ctypes.POINTER(ctypes.c_void_p)).contents.value = 123
        if name == "midiOutLongMsg" and self.done:
            ctypes.cast(args[1], ctypes.POINTER(native.MIDIHDR)).contents.dwFlags |= native.MHDR_DONE
        if name == "midiInPrepareHeader":
            ctypes.cast(args[1], ctypes.POINTER(native.MIDIHDR)).contents.dwFlags |= 2
        if name == "midiInAddBuffer" and not ctypes.cast(args[1], ctypes.POINTER(native.MIDIHDR)).contents.dwFlags & 2:
            return 1
        return 0


@pytest.fixture
def lib(monkeypatch):
    result = Library()
    monkeypatch.setattr(native, "_load_winmm", lambda: result)
    return result


def test_open_send_close_and_pointer_widths(lib):
    transport = native.WinMMTransport("Cmd", "Reply")
    transport.send(b"\xf0\x7d{}\xf7")
    transport.close()
    calls = list(lib.calls)
    transport.close()
    assert lib.calls == calls
    assert lib.midiOutOpen.argtypes[0] == ctypes.POINTER(ctypes.c_void_p)
    assert dict(native.MIDIHDR._fields_)["dwUser"] is ctypes.c_size_t
    assert "midiOutClose" in calls and "midiInClose" in calls


@pytest.mark.parametrize("failure", ["midiInOpen", "midiInPrepareHeader", "midiInAddBuffer", "midiInStart"])
def test_partial_initialization_releases_owned_handles(lib, failure):
    lib.fail = failure
    with pytest.raises(OSError):
        native.WinMMTransport("Cmd", "Reply")
    assert "midiOutClose" in lib.calls


def test_missing_and_duplicate_port_names_are_refused(lib, monkeypatch):
    with pytest.raises(OSError):
        native.WinMMTransport("Absent", "Reply")
    monkeypatch.setattr(native, "_port_names", lambda lib: (["Reply"], ["Cmd", "Cmd"]))
    with pytest.raises(OSError, match="ambiguous"):
        native.WinMMTransport("Cmd", "Reply")


def test_timeout_and_reset_failure_retain_output_buffer(lib, monkeypatch):
    transport = native.WinMMTransport("Cmd", "Reply")
    lib.done = False
    lib.fail = "midiOutReset"
    ticks = iter([0, 3])
    monkeypatch.setattr(native.time, "monotonic", lambda: next(ticks))
    with pytest.raises(OSError):
        transport.send(b"\xf0\x7d{}\xf7")
    assert transport._outgoing is not None
    assert transport in native._quarantined
    lib.fail = None
    transport.close()


def test_cleanup_failure_retains_transport_until_retried(lib):
    transport = native.WinMMTransport("Cmd", "Reply")
    lib.fail = "midiInReset"
    with pytest.raises(OSError):
        transport.close()
    assert transport in native._quarantined
    assert transport._storage
    lib.fail = None
    transport.close()


def test_receive_requeues_a_prepared_buffer_without_losing_native_flags(lib):
    transport = native.WinMMTransport("Cmd", "Reply")
    header = transport._queue[0]
    ctypes.memmove(header.lpData, b"test", 4)
    header.dwBytesRecorded = 4
    header.dwFlags |= native.MHDR_DONE
    try:
        assert transport.receive(0.1) == b"test"
        assert header.dwFlags & 2  # MHDR_PREPARED is retained
    finally:
        transport.close()


def test_failed_construction_reports_failed_cleanup_and_retains_resources(lib):
    lib.fail = ("midiInStart", "midiInReset")
    with pytest.raises(native.TransportCleanupError) as captured:
        native.WinMMTransport("Cmd", "Reply")
    retained = captured.value.transport
    assert retained in native._quarantined and retained._input_open
    lib.fail = None
    retained.close()
