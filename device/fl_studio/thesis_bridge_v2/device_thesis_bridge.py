# name=Thesis Bridge v2
"""FL Studio controller script v2 for the command bridge, owned by ``adapters.fl_studio`` (ADR-032, Proposed).

Derived from the frozen, reviewed v1 research script (``scripts/fl_studio/device_bridge.py``, unchanged) by explicit
substitutions recorded in the recovery plan; all v1 commands behave as before. Addition: ``restore_gain``.

Install this file together with ``thesis_bridge_protocol.py`` (a byte-identical copy of the runtime
``adapters/fl_studio/protocol.py``) in ``Documents/Image-Line/FL Studio/Settings/Hardware/Thesis Bridge v2/``.
Installing it is a separately authorized step; nothing in the repository installs it. This file is not part of the
importable runtime package: FL host modules (``general``, ``mixer``, ``transport``, ``ui``, ``device``) are imported
only inside functions, and the only top-level import is the protocol module by its plain name.

- ``restore_gain`` writes a journaled reading back exactly, upward or downward. Before writing it checks, in order:
  API, project title, insert, the calibrated build (``uncalibrated_build``), stopped transport, ``safeToEdit`` and
  the start against ``expected`` (``start_gain``); the target's range and curve are validated with the command. It
  makes one ``setTrackVolume`` of the exact target normalized value, reads back, and replies ``applied`` (with
  before/after) when the fader reads the target within 0.001, otherwise ``uncertain``. Exactness (both readings
  equal the target) is judged by the runtime, not here. Every failure after the setter is ``uncertain``.

Duplicate history, identity (metadata title only) and the uncertain boundary are as in v1. Restoring a fader proves
only its readings, never routing, plugins, the project or audio.
"""

import thesis_bridge_protocol as protocol

DB_MODE = 1  # getTrackVolume mode 1 returns dB (documented; confirmed live on API 45)
MIN_API_VERSION = 38  # getProjectTitle (safeToEdit needs 29)
_HISTORY = {}  # command ID -> (payload key, recorded write reply or None); lost on script reload
_START_KEY = "calibration_start"  # history[(_START_KEY, plan)] = normalized gain read before step 0 (tuple key)


def _identity(host, insert_index: int):
    """Raw project title and insert name (for the checks) and their clipped observation (for the reply)."""
    count = host.mixer.trackCount()
    in_range = 1 <= insert_index <= count - 2  # not Master (0), not the last "Current" track
    title = host.general.getProjectTitle()
    name = host.mixer.getTrackName(insert_index) if in_range else None
    observed = {
        "project_title": protocol.clip_text(title, protocol.MAX_TITLE_BYTES),
        "fl_version": protocol.clip_text(host.ui.getVersion(5), protocol.MAX_VERSION_BYTES),
        "api": host.general.getVersion(),
        "insert": {"index": insert_index, "name": protocol.clip_text(name, protocol.MAX_NAME_BYTES)},
    }
    return title, name, observed


def _read(host, insert_index: int) -> dict:
    return {
        "normalized": host.mixer.getTrackVolume(insert_index, 0),
        "db": host.mixer.getTrackVolume(insert_index, DB_MODE),
    }


def _error_name(exc: Exception, prefix: str = "") -> str:
    return (prefix + type(exc).__name__)[: protocol.MAX_ERROR_BYTES]


def _finish(answer: dict, wrote: bool, command: dict) -> dict:
    """``answer`` if it is a valid, encodable reply; otherwise a safe fallback that never hides a write."""
    try:
        protocol.validate_reply(answer)
        protocol.encode(answer)
        return answer
    except protocol.ProtocolError:
        pass
    if not wrote:
        return protocol.reply(command["id"], "error", error="invalid_observation")
    return _post_write_uncertain(command, answer["observed"], "invalid_observation")


def _post_write_uncertain(command: dict, observed, error: str, before=None) -> dict:
    """An ``uncertain`` reply for a write that may have happened, built so that it cannot fail.

    Identity comes from the validated command (the script checked it before writing). Only an FL version text and
    API number that are already valid are carried over; otherwise the version is empty and the API is the minimum
    the script checked before writing. ``before`` (read and checked before the write) is kept if it is valid; no
    post-write value is reported, since those are what could not be processed.
    """
    fl_version, api = "", MIN_API_VERSION
    if isinstance(observed, dict):
        text = observed.get("fl_version")
        if isinstance(text, str) and protocol.escaped_bytes(text) <= protocol.MAX_VERSION_BYTES:
            fl_version = text
        number = observed.get("api")
        if isinstance(number, int) and not isinstance(number, bool) and number >= 1:
            api = number
    try:
        before = protocol._gain(before, "before") if before is not None else None
    except Exception:
        before = None
    fallback = {
        "project_title": command["project_title"],
        "fl_version": fl_version,
        "api": api,
        "insert": {"index": command["insert"]["index"], "name": command["insert"]["name"]},
        "before": before,
        "after": None,
        "db_change": None,
        "replayed": False,
    }
    return protocol.reply(command["id"], "uncertain", fallback, error[: protocol.MAX_ERROR_BYTES])


def _inspect(command: dict, host) -> dict:
    command_id, index = command["id"], command["insert"]["index"]
    title, name, observed = _identity(host, index)
    if title != command["project_title"] or name != command["insert"]["name"]:
        observed["gain"] = None
        error = "project_title" if title != command["project_title"] else "insert"
        return protocol.reply(command_id, "identity_mismatch", observed, error)
    observed["gain"] = _read(host, index)
    return _finish(protocol.reply(command_id, "ok", observed), False, command)


def _write(command: dict, host, state: dict, history: dict) -> dict:
    """One bounded write. ``state["attempted"]`` turns true just before the setter is called: from then on every
    failure, wherever it happens, is reported ``uncertain`` (here, or by ``handle`` as a last resort)."""
    restore = command["type"] == "restore_gain"
    command_id, index = command["id"], command["insert"]["index"]
    target = command["target"]["normalized"] if restore else command["target_normalized"]
    title, name, identity = _identity(host, index)
    observed = {**identity, "before": None, "after": None, "db_change": None, "replayed": False}

    def refuse(status, error, wrote=False):
        return _finish(protocol.reply(command_id, status, observed, error), wrote, command)

    if title != command["project_title"]:
        return refuse("identity_mismatch", "project_title")
    if name != command["insert"]["name"]:
        return refuse("identity_mismatch", "insert")
    db_command = command["type"] == "set_gain_db"
    if (db_command or restore) and host.ui.getVersion(5) != protocol.CONVERSION_SCOPE["fl_version"]:
        return refuse("precondition_failed", "uncalibrated_build")
    if host.transport.isPlaying():
        return refuse("precondition_failed", "transport_playing")
    if not host.general.safeToEdit():
        return refuse("precondition_failed", "edit_unavailable")
    before = _read(host, index)
    observed["before"] = before
    if not protocol.start_matches(before, command["expected"]):
        return refuse("precondition_failed", "start_gain")
    probe = command["type"] == "probe_gain"
    if probe:
        start_key = (_START_KEY, command["plan"])
        if command["step"] == protocol.RESTORE_STEP:
            recorded = history.get(start_key)
            if recorded is not None and target != recorded:
                return refuse("precondition_failed", "restore_target")
    elif restore:
        pass  # target range and curve were validated with the command; a restoration may start anywhere
    elif db_command:
        if not protocol.in_conversion_range(before["normalized"]):
            return refuse("precondition_failed", "range")
        if not protocol.start_on_curve(before):
            return refuse("precondition_failed", "uncalibrated_start")
    elif not protocol.cut_in_bounds(before["normalized"], target):
        return refuse("precondition_failed", "cut_bounds")
    state["attempted"] = True
    if probe and command["step"] == 0:
        history[start_key] = before["normalized"]
    try:  # everything after this point runs inside the uncertain-outcome boundary
        host.mixer.setTrackVolume(index, target)  # the only write
        after = _read(host, index)
        observed["after"] = after
        observed["db_change"] = after["db"] - before["db"]
        if probe or db_command or restore:
            landed = protocol.probe_readback_agrees(after, target)
        else:
            landed = protocol.readback_agrees(before, after, target)
        if not landed:
            return refuse("uncertain", "readback_disagrees", wrote=True)
        if db_command:
            miss = protocol.db_miss(before, after, command["intended_db_change"])
            if not round(abs(miss), protocol.DECIMALS) <= protocol.DB_SUCCESS_TOLERANCE:
                return refuse("applied_off_target", "db_off_target", wrote=True)
        return _finish(protocol.reply(command_id, "applied", observed), True, command)
    except Exception as exc:  # the setter may have run: never report this as a plain error
        return _post_write_uncertain(command, identity, _error_name(exc, "exception_after_write:"), before)


def _replayed(recorded: dict) -> dict:
    answer = dict(recorded)
    if isinstance(answer["observed"], dict) and "replayed" in answer["observed"]:
        answer["observed"] = {**answer["observed"], "replayed": True}
    return answer


def handle(raw: bytes, host, history: dict) -> bytes | None:
    """Answer one bridge SysEx message; None for messages of other manufacturers (left unhandled).

    ``history`` is the session's duplicate record (``_HISTORY`` inside FL Studio, a fresh dict per simulated session).
    """
    if not protocol.is_bridge_sysex(raw):
        return None
    try:
        message = protocol.decode(raw)
    except protocol.ProtocolError as exc:
        return protocol.encode(protocol.reply(None, "invalid_command", error=exc.code))
    command_id = protocol.command_id(message)
    try:
        command = protocol.validate_command(message)
    except protocol.ProtocolError as exc:
        return protocol.encode(protocol.reply(command_id, "invalid_command", error=exc.code))
    key = protocol.payload_key(command)
    prior = history.get(command_id)
    if prior is not None and prior[0] != key:
        return protocol.encode(protocol.reply(command_id, "invalid_command", error="duplicate_id_conflict"))
    if prior is not None and prior[1] is not None:
        return protocol.encode(_replayed(prior[1]))  # recorded write result: no second write
    state = {"attempted": False}
    try:
        if host.general.getVersion() < MIN_API_VERSION:
            answer = protocol.reply(command_id, "error", error="api_too_old")
        elif command["type"] == "inspect":
            answer = _inspect(command, host)
        else:
            answer = _write(command, host, state, history)
    except Exception as exc:
        answer = _failure(command, state, exc)
    try:
        raw_answer = protocol.encode(answer)
    except Exception as exc:  # last resort: a reply that cannot be sent must not hide a write
        answer = _failure(command, state, exc)
        raw_answer = protocol.encode(answer)
    history[command_id] = (key, answer if command["type"] in protocol.WRITE_TYPES else None)
    return raw_answer


def _failure(command: dict, state: dict, exc: Exception) -> dict:
    """``error`` if no write was attempted; ``uncertain`` once the setter may have run."""
    if state["attempted"]:
        return _post_write_uncertain(command, None, _error_name(exc, "exception_after_write:"))
    return protocol.reply(command["id"], "error", error=_error_name(exc))


class _Host:
    """The FL Studio modules, imported lazily so this file can be loaded outside FL Studio for tests."""

    def __init__(self):
        import general
        import mixer
        import transport
        import ui

        self.general, self.mixer, self.transport, self.ui = general, mixer, transport, ui


def OnInit():
    import device

    _HISTORY.clear()
    print(f"Thesis Bridge v2: input port {device.getPortNumber()}, output linked: {device.isAssigned()}, "
          "duplicate history empty")  # fmt: skip


def OnSysEx(msg):
    import device

    answer = handle(bytes(msg.sysex), _Host(), _HISTORY)
    if answer is None:
        return  # not a bridge message
    msg.handled = True
    if not device.isAssigned():
        print("Thesis Bridge v2: no output port linked; reply not sent")
        return
    device.midiOutSysex(answer)
    reply = protocol.decode(answer)
    observed = reply["observed"] or {}
    print(f"Thesis Bridge v2: id={reply['id']} status={reply['status']} error={reply['error']} "
          f"replayed={observed.get('replayed')} after={observed.get('after')}")  # fmt: skip
