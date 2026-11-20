"""Wire protocol of the FL Studio command bridge (contract: docs/research/fl-studio-bridge-contract.md, version 1).

Shared by the external Python client (``bridge_client.py``) and the FL Studio controller script
(``device_bridge.py``), which loads this file from its own script folder. Standard library only (``json``, ``math``,
``re``), because FL Studio 2026 runs scripts on its embedded Python 3.12.

Encoding: one MIDI System Exclusive message per command or reply::

    F0 7D <payload> F7

``7D`` is the MIDI manufacturer ID reserved for non-commercial and educational use. The payload is compact JSON
(``separators=(",", ":")``, sorted keys) encoded as ASCII with non-ASCII characters escaped (``ensure_ascii``), so
every payload byte is printable ASCII (0x20-0x7E) and therefore a valid 7-bit SysEx data byte. A whole message,
framing included, is at most ``MAX_MESSAGE_BYTES`` (pilot setting; FL Studio's own SysEx limit is not documented).
Text limits count *escaped* bytes (a non-ASCII character costs six), so the worst-case command and reply sizes are
bounded and tested against ``MAX_MESSAGE_BYTES``.

Commands: ``inspect`` (read only), ``set_gain`` (one bounded absolute normalized fader write, feasibility
increment), ``probe_gain`` (one absolute write to a position of the pinned calibration plan below, or back to the
calibration's starting value) and ``set_gain_db`` (one absolute write whose target is computed from FL Studio's
starting dB read-back plus a requested cut, by the versioned conversion ``CONVERSION_ID`` below; client and script
compute the same target and the script rejects any disagreement).
"""

import json
import math
import re

SYSEX_START, SYSEX_END = 0xF0, 0xF7
MANUFACTURER_ID = 0x7D  # non-commercial / educational
PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 1024  # pilot setting, framing included
MAX_TITLE_BYTES = 128  # escaped JSON bytes, commands and observations
MAX_NAME_BYTES = 64
MAX_VERSION_BYTES = 64
MAX_ERROR_BYTES = 64
COMMAND_TYPES = ("inspect", "set_gain", "probe_gain", "set_gain_db", "restore_gain")
WRITE_TYPES = ("set_gain", "probe_gain", "set_gain_db", "restore_gain")
STATUSES = ("ok", "applied", "applied_off_target", "identity_mismatch", "precondition_failed", "uncertain",
            "invalid_command", "unsupported", "error")  # fmt: skip
STATUSES_FOR = {
    "inspect": ("ok", "identity_mismatch", "invalid_command", "unsupported", "error"),
    "set_gain": ("applied", "precondition_failed", "uncertain", "identity_mismatch", "invalid_command", "unsupported",
                 "error"),
}  # fmt: skip
STATUSES_FOR["probe_gain"] = STATUSES_FOR["set_gain"]
STATUSES_FOR["set_gain_db"] = (*STATUSES_FOR["set_gain"], "applied_off_target")
# restore_gain (runtime protocol v2 snapshot, ADR-032): one exact absolute write back to a journaled reading.
STATUSES_FOR["restore_gain"] = STATUSES_FOR["set_gain"]

# set_gain bounds and starting-state tolerances: the gain trial's settings (device_gain_trial.py), unchanged
START_TOLERANCE_NORMALIZED = 0.001
START_TOLERANCE_DB = 0.05
MIN_CUT_EXCLUSIVE = 2 * START_TOLERANCE_NORMALIZED  # a cut must exceed 0.002 normalized
MAX_CUT = 0.03
DECIMALS = 9  # bounds and tolerances are compared after rounding differences to 1e-9 (no float-edge surprises)

# Calibration plan (docs/research/fl-studio-db-conversion-design.md, section 5), pinned before any run. A probe may
# write only CALIBRATION_SEQUENCE[step] (exact float) or, as step "restore", the calibration's starting value. The
# sequence was drawn once with random.Random(CALIBRATION_SEED).shuffle over the primary points twice and the wide
# points once, repeated until the first position is a wide point, no position repeats its predecessor and every primary
# point is approached once from above and once from below (2,661,526 shuffles); this literal is authoritative.
CALIBRATION_PLAN_ID = "fl-mixer-cal-v1"
CALIBRATION_PROJECT_TITLE = "Thesis Bass Bridge Test"  # the disposable trial project bass-bridge-test.flp
CALIBRATION_INSERT = {"index": 2, "name": "Bass"}
CALIBRATION_PRIMARY = (0.62, 0.65, 0.68, 0.71, 0.73, 0.75, 0.77, 0.79, 0.81, 0.83, 0.86)
CALIBRATION_WIDE = (0.30, 0.45, 0.55, 0.92, 1.00)
CALIBRATION_SEED = 20261118
CALIBRATION_SEQUENCE = (0.55, 0.62, 0.73, 0.71, 0.81, 1.0, 0.86, 0.81, 0.86, 0.83, 0.77, 0.79, 0.68, 0.71, 0.45, 0.75,
                        0.83, 0.73, 0.92, 0.79, 0.75, 0.65, 0.3, 0.65, 0.68, 0.62, 0.77)  # fmt: skip
RESTORE_STEP = "restore"

# Versioned dB conversion (docs/research/fl-studio-db-conversion-design.md, sections 3-4), accepted by the live
# calibration run live20261118codex of plan fl-mixer-cal-v1 (verdict H_accepted, max |H - read-back| 2.4e-6 dB at the
# primary points) for the normalized range [0.62, 0.86], on this FL Studio build, project title and insert only.
# A new calibration or a different curve gets a new CONVERSION_ID; nothing else is accepted.
CONVERSION_ID = "fl-mixer-h125-v1"
CONVERSION_PLAN = CALIBRATION_PLAN_ID
CONVERSION_RANGE = (0.62, 0.86)
CONVERSION_SCOPE = {"project_title": CALIBRATION_PROJECT_TITLE, "insert": CALIBRATION_INSERT,
                    "fl_version": "Producer Edition v26.1.7 [build 5654]"}  # fmt: skip
CONVERSION_MODEL_TOLERANCE_DB = 0.01  # |H(v) - read-back dB| at the start (the calibration's acceptance tolerance)
DB_CUT_RANGE = (-3.0, -0.1)  # requested change in dB: a cut of at least 0.1 dB and at most 3 dB
DB_SUCCESS_TOLERANCE = 0.05  # |achieved - requested| dB change (the trial's DB_TOLERANCE)
TARGET_AGREEMENT = 1e-9  # client's and script's targets must agree within this (normalized)

_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
_INSPECT_OBSERVED = {"project_title", "fl_version", "api", "insert", "gain"}
_WRITE_OBSERVED = {"project_title", "fl_version", "api", "insert", "before", "after", "db_change", "replayed"}


class ProtocolError(ValueError):
    """A message that does not follow the wire protocol; ``code`` is a stable machine-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def is_bridge_sysex(raw) -> bool:
    """True for a SysEx message carrying this bridge's manufacturer ID (others are left to other scripts)."""
    data = bytes(raw)
    return len(data) >= 2 and data[0] == SYSEX_START and data[1] == MANUFACTURER_ID


def encode(message: dict) -> bytes:
    try:
        text = json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True)
    except ValueError:
        raise ProtocolError("json", "NaN and infinity are not JSON") from None
    raw = bytes([SYSEX_START, MANUFACTURER_ID]) + text.encode("ascii") + bytes([SYSEX_END])
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ProtocolError("too_large", f"{len(raw)} bytes exceed {MAX_MESSAGE_BYTES}")
    return raw


def _reject_constant(name: str):
    raise ValueError(f"{name} is not JSON")


def decode(raw) -> dict:
    data = bytes(raw)
    if len(data) > MAX_MESSAGE_BYTES:
        raise ProtocolError("too_large", f"{len(data)} bytes exceed {MAX_MESSAGE_BYTES}")
    if len(data) < 4 or data[0] != SYSEX_START or data[-1] != SYSEX_END:
        raise ProtocolError("framing", "not a complete SysEx message (F0 ... F7)")
    if data[1] != MANUFACTURER_ID:
        raise ProtocolError("manufacturer", f"manufacturer ID {data[1]:#04x}, expected {MANUFACTURER_ID:#04x}")
    payload = data[2:-1]
    if any(b < 0x20 or b > 0x7E for b in payload):
        raise ProtocolError("encoding", "payload must be printable 7-bit ASCII")
    try:
        message = json.loads(payload.decode("ascii"), parse_constant=_reject_constant)
    except ValueError as exc:
        raise ProtocolError("json", f"payload is not JSON: {exc}") from None
    if not isinstance(message, dict):
        raise ProtocolError("json", "payload must be a JSON object")
    return message


# --- field checks ---------------------------------------------------------------------------------------------------


def escaped_bytes(text: str) -> int:
    """Bytes the string occupies inside an encoded message, quotes excluded."""
    return len(json.dumps(text, ensure_ascii=True)) - 2


def clip_text(text, limit: int):
    """``text`` shortened until its escaped size fits ``limit`` (observations only; non-strings returned as is)."""
    if not isinstance(text, str):
        return text
    while escaped_bytes(text) > limit:
        text = text[:-1]
    return text


def _text(value, field: str, limit: int, empty: bool = False) -> str:
    if not isinstance(value, str) or (not value and not empty) or escaped_bytes(value) > limit:
        raise ProtocolError("field", f"{field} must be a {'' if empty else 'non-empty '}string of at most {limit} "
                                     "escaped bytes")  # fmt: skip
    return value


def _index(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ProtocolError("field", f"{field} must be an integer >= 1")
    return value


def _number(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProtocolError("field", f"{field} must be a finite number")
    return value


def _gain(value, field: str) -> dict:
    """``{"normalized", "db"}``: finite numbers, normalized within the documented fader range [0, 1]."""
    if not isinstance(value, dict) or set(value) != {"normalized", "db"}:
        raise ProtocolError("field", f"{field} must have exactly normalized and db")
    if not 0.0 <= _number(value["normalized"], f"{field}.normalized") <= 1.0:
        raise ProtocolError("field", f"{field}.normalized must lie in [0, 1]")
    _number(value["db"], f"{field}.db")
    return value


def _round(value: float) -> float:
    return round(value, DECIMALS)


def h_db(v: float) -> float:
    """Conversion ``fl-mixer-h125-v1``: fader dB of normalized ``v`` (accepted only inside CONVERSION_RANGE)."""
    return 20.0 * math.log10((math.exp(1.25 * v * math.log(11.0)) - 1.0) / 10.0)


def h_inverse(db: float) -> float:
    """Normalized fader position whose ``h_db`` is ``db``."""
    return math.log(10.0 * 10.0 ** (db / 20.0) + 1.0) / (1.25 * math.log(11.0))


def db_target(start_db: float, db_change: float) -> float:
    """The one absolute target: from FL Studio's starting dB *read-back* plus the requested change."""
    return h_inverse(start_db + db_change)


def in_conversion_range(v: float) -> bool:
    low, high = CONVERSION_RANGE
    return _round(v - low) >= 0.0 and _round(high - v) >= 0.0


def start_on_curve(gain: dict) -> bool:
    return _round(abs(h_db(gain["normalized"]) - gain["db"])) <= CONVERSION_MODEL_TOLERANCE_DB


def db_miss(before: dict, after: dict, db_change: float) -> float:
    """Achieved minus requested dB change."""
    return (after["db"] - before["db"]) - db_change


def cut_in_bounds(start_normalized: float, target_normalized: float) -> bool:
    """The trial's bounded cut: target > 0 and start - target in (0.002, 0.03]."""
    return target_normalized > 0.0 and MIN_CUT_EXCLUSIVE < _round(start_normalized - target_normalized) <= MAX_CUT


def start_matches(observed: dict, expected: dict) -> bool:
    """The observed starting gain equals the expected one within the trial's tolerances (0.001 and 0.05 dB)."""
    return (
        _round(abs(observed["normalized"] - expected["normalized"])) <= START_TOLERANCE_NORMALIZED
        and _round(abs(observed["db"] - expected["db"])) <= START_TOLERANCE_DB
    )


def readback_agrees(before: dict, after: dict, target_normalized: float) -> bool:
    """The trial's read-back rule: the fader reads the target within 0.001 and dB fell by more than 0.05 dB."""
    return (
        _round(abs(after["normalized"] - target_normalized)) <= START_TOLERANCE_NORMALIZED
        and _round(before["db"] - after["db"]) > START_TOLERANCE_DB
    )


# --- commands ---------------------------------------------------------------------------------------------------------


def command_id(message: dict) -> str | None:
    """The command's ID if it is well-formed, else None (so a reply can still be correlated when possible)."""
    value = message.get("id") if isinstance(message, dict) else None
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def validate_command(message: dict) -> dict:
    """A well-formed version-1 command; raises ProtocolError (``version``, ``field``, ``type`` or ``bounds``)."""
    if message.get("v") != PROTOCOL_VERSION or isinstance(message.get("v"), bool):
        raise ProtocolError("version", f"v must be {PROTOCOL_VERSION}")
    if command_id(message) is None:
        raise ProtocolError("field", "id must be 8-64 characters of A-Z, a-z, 0-9, '_' or '-'")
    if message.get("type") not in COMMAND_TYPES:
        raise ProtocolError("type", f"type must be one of {COMMAND_TYPES}")
    _text(message.get("project_title"), "project_title", MAX_TITLE_BYTES)
    insert = message.get("insert")
    if not isinstance(insert, dict) or set(insert) != {"index", "name"}:
        raise ProtocolError("field", "insert must be an object with exactly index and name")
    _index(insert["index"], "insert.index")
    _text(insert["name"], "insert.name", MAX_NAME_BYTES)
    fields = {"v", "id", "type", "project_title", "insert"}
    if message["type"] in WRITE_TYPES and message["type"] != "restore_gain":
        fields |= {"expected", "target_normalized"}
    if message["type"] == "restore_gain":
        fields |= {"expected", "target"}
    if message["type"] == "probe_gain":
        fields |= {"plan", "step"}
    if message["type"] == "set_gain_db":
        fields |= {"intended_db_change", "conversion"}
    if set(message) != fields:
        raise ProtocolError("field", f"a {message['type']} command has exactly the fields {sorted(fields)}")
    if message["type"] == "probe_gain":
        _validate_probe(message)
    if message["type"] == "set_gain_db":
        _validate_db(message)
    if message["type"] == "restore_gain":
        _validate_restore(message)
    if message["type"] == "set_gain":
        expected = _gain(message["expected"], "expected")
        target = _number(message["target_normalized"], "target_normalized")
        if not (target <= 1.0 and cut_in_bounds(expected["normalized"], target)):
            raise ProtocolError("bounds", f"target must be > 0 and below expected by more than {MIN_CUT_EXCLUSIVE} "
                                          f"and at most {MAX_CUT} (normalized)")  # fmt: skip
    return message


def _validate_probe(message: dict) -> None:
    """A probe follows the pinned plan exactly: plan, project, insert, step and its target."""
    _gain(message["expected"], "expected")
    target = _number(message["target_normalized"], "target_normalized")
    if message["plan"] != CALIBRATION_PLAN_ID:
        raise ProtocolError("plan", f"plan must be {CALIBRATION_PLAN_ID}")
    if message["project_title"] != CALIBRATION_PROJECT_TITLE or message["insert"] != CALIBRATION_INSERT:
        raise ProtocolError("plan", "probes run only on the plan's project title and insert")
    step = message["step"]
    if step == RESTORE_STEP:
        if not 0.0 < target <= 1.0:
            raise ProtocolError("bounds", "a restore target must lie in (0, 1]")
        return
    if isinstance(step, bool) or not isinstance(step, int):
        raise ProtocolError("field", f"step must be an integer or {RESTORE_STEP!r}")
    if not 0 <= step < len(CALIBRATION_SEQUENCE) or target != CALIBRATION_SEQUENCE[step]:
        raise ProtocolError("plan", "step and target must be a position of the pinned sequence")


def _validate_db(message: dict) -> None:
    """Conversion ID and scope, cut bounds, start inside the range and on the curve, the shared target, its range."""
    if message["conversion"] != CONVERSION_ID:
        raise ProtocolError("conversion", f"conversion must be {CONVERSION_ID}")
    scope = {"project_title": message["project_title"], "insert": message["insert"]}
    if scope != {"project_title": CONVERSION_SCOPE["project_title"], "insert": CONVERSION_SCOPE["insert"]}:
        raise ProtocolError("conversion_scope", "the conversion is calibrated only for its project title and insert")
    expected = _gain(message["expected"], "expected")
    change = _number(message["intended_db_change"], "intended_db_change")
    target = _number(message["target_normalized"], "target_normalized")
    low, high = DB_CUT_RANGE
    if not (_round(change - low) >= 0.0 and _round(high - change) >= 0.0):
        raise ProtocolError("bounds", f"intended_db_change must lie in [{low}, {high}] dB")
    if not in_conversion_range(expected["normalized"]):
        raise ProtocolError("range", f"the starting fader must lie in the calibrated range {CONVERSION_RANGE}")
    if not start_on_curve(expected):
        raise ProtocolError("uncalibrated_start", "the starting read-back is not on the calibrated curve")
    if abs(target - db_target(expected["db"], change)) > TARGET_AGREEMENT:
        raise ProtocolError("conversion_mismatch", "target_normalized disagrees with the conversion")
    if not in_conversion_range(target):
        raise ProtocolError("range", f"the target must lie in the calibrated range {CONVERSION_RANGE}")
    if not _round(expected["normalized"] - target) > MIN_CUT_EXCLUSIVE:
        raise ProtocolError("bounds", f"the fader must move by more than {MIN_CUT_EXCLUSIVE} (normalized)")


def _validate_restore(message: dict) -> None:
    """An exact restoration: the calibrated scope, a target inside the range and on the curve, and a real move.

    No conversion field and no cut bounds: the target is a reading the runtime journaled, written back exactly.
    """
    scope = {"project_title": message["project_title"], "insert": message["insert"]}
    if scope != {"project_title": CONVERSION_SCOPE["project_title"], "insert": CONVERSION_SCOPE["insert"]}:
        raise ProtocolError("conversion_scope", "restoration is supported only on the calibrated project and insert")
    expected = _gain(message["expected"], "expected")
    target = _gain(message["target"], "target")
    if not in_conversion_range(target["normalized"]):
        raise ProtocolError("range", f"the restore target must lie in the calibrated range {CONVERSION_RANGE}")
    if not start_on_curve(target):
        raise ProtocolError("conversion_mismatch", "the restore target's dB is not on the calibrated curve")
    if target == expected:
        raise ProtocolError("bounds", "the fader already reads the restore target")


def payload_key(command: dict) -> str:
    """Canonical text of a validated command, used to tell a duplicate from a conflicting reuse of its ID."""
    return json.dumps(command, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def inspect_command(command_id_: str, project_title: str, insert_index: int, insert_name: str) -> dict:
    message = {"v": PROTOCOL_VERSION, "id": command_id_, "type": "inspect", "project_title": project_title,
               "insert": {"index": insert_index, "name": insert_name}}  # fmt: skip
    return validate_command(message)


def set_gain_command(command_id_: str, project_title: str, insert_index: int, insert_name: str,
                     expected_normalized: float, expected_db: float, target_normalized: float) -> dict:  # fmt: skip
    message = {"v": PROTOCOL_VERSION, "id": command_id_, "type": "set_gain", "project_title": project_title,
               "insert": {"index": insert_index, "name": insert_name},
               "expected": {"normalized": expected_normalized, "db": expected_db},
               "target_normalized": target_normalized}  # fmt: skip
    return validate_command(message)


# --- replies ----------------------------------------------------------------------------------------------------------


def probe_command(command_id_: str, expected_normalized: float, expected_db: float, step,
                  target_normalized: float | None = None) -> dict:  # fmt: skip
    """A calibration probe: target from the pinned sequence, or an explicit target for the restore step."""
    if step != RESTORE_STEP and target_normalized is None:
        target_normalized = CALIBRATION_SEQUENCE[step] if isinstance(step, int) and 0 <= step < len(
            CALIBRATION_SEQUENCE) else -1.0  # fmt: skip
    message = {"v": PROTOCOL_VERSION, "id": command_id_, "type": "probe_gain",
               "project_title": CALIBRATION_PROJECT_TITLE, "insert": dict(CALIBRATION_INSERT),
               "plan": CALIBRATION_PLAN_ID, "step": step,
               "expected": {"normalized": expected_normalized, "db": expected_db},
               "target_normalized": target_normalized}  # fmt: skip
    return validate_command(message)


def set_gain_db_command(command_id_: str, project_title: str, insert_index: int, insert_name: str,
                        expected_normalized: float, expected_db: float, db_change: float) -> dict:  # fmt: skip
    """A dB cut; the target is computed here by the shared conversion (the script recomputes and compares)."""
    try:
        target = db_target(float(expected_db), float(db_change))
    except (TypeError, ValueError, OverflowError):
        target = -1.0  # validation reports the bad field
    message = {"v": PROTOCOL_VERSION, "id": command_id_, "type": "set_gain_db", "project_title": project_title,
               "insert": {"index": insert_index, "name": insert_name},
               "expected": {"normalized": expected_normalized, "db": expected_db}, "intended_db_change": db_change,
               "target_normalized": target, "conversion": CONVERSION_ID}  # fmt: skip
    return validate_command(message)


def restore_gain_command(command_id_: str, project_title: str, insert_index: int, insert_name: str,
                         expected_normalized: float, expected_db: float, target_normalized: float,
                         target_db: float) -> dict:  # fmt: skip
    """An exact restoration of a journaled reading (both coordinates), from the expected current reading."""
    message = {"v": PROTOCOL_VERSION, "id": command_id_, "type": "restore_gain", "project_title": project_title,
               "insert": {"index": insert_index, "name": insert_name},
               "expected": {"normalized": expected_normalized, "db": expected_db},
               "target": {"normalized": target_normalized, "db": target_db}}  # fmt: skip
    return validate_command(message)


def reply(command_id_: str | None, status: str, observed: dict | None = None, error: str | None = None) -> dict:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    return {"v": PROTOCOL_VERSION, "id": command_id_, "type": "reply", "status": status, "observed": observed,
            "error": error}  # fmt: skip


def _identity(observed) -> None:
    if not isinstance(observed, dict):
        raise ProtocolError("field", "observed must be an object")
    _text(observed["project_title"], "observed.project_title", MAX_TITLE_BYTES, empty=True)
    _text(observed["fl_version"], "observed.fl_version", MAX_VERSION_BYTES, empty=True)
    _index(observed["api"], "observed.api")
    insert = observed["insert"]
    if not isinstance(insert, dict) or set(insert) != {"index", "name"}:
        raise ProtocolError("field", "observed.insert must have exactly index and name")
    _index(insert["index"], "observed.insert.index")
    if insert["name"] is not None:
        _text(insert["name"], "observed.insert.name", MAX_NAME_BYTES, empty=True)


def _shape(observed, keys: set, what: str) -> None:
    if not isinstance(observed, dict) or set(observed) != keys:
        raise ProtocolError("field", f"observed must have exactly {sorted(keys)} for {what}")
    _identity(observed)


def _optional_gain(value, field: str) -> None:
    if value is not None:
        _gain(value, field)


def _write_observed(observed: dict, status: str) -> None:
    _shape(observed, _WRITE_OBSERVED, status)
    if not isinstance(observed["replayed"], bool):
        raise ProtocolError("field", "observed.replayed must be true or false")
    before, after, change = observed["before"], observed["after"], observed["db_change"]
    if status in ("applied", "applied_off_target"):
        if observed["insert"]["name"] is None:
            raise ProtocolError("field", f"an {status} reply must name the insert")
        _gain(before, "observed.before")
        _gain(after, "observed.after")
        _number(change, "observed.db_change")
    elif status == "identity_mismatch":
        if before is not None or after is not None or change is not None:
            raise ProtocolError("field", "an identity_mismatch reply must not carry gain")
    elif status == "precondition_failed":
        _optional_gain(before, "observed.before")
        if after is not None or change is not None:
            raise ProtocolError("field", "a precondition_failed reply wrote nothing and carries no after or change")
    else:  # uncertain: whatever could be read
        _optional_gain(before, "observed.before")
        _optional_gain(after, "observed.after")
        if change is not None:
            _number(change, "observed.db_change")


def validate_reply(message: dict) -> dict:
    """A well-formed version-1 reply.

    Structure only: the client separately checks that the ID is its pending command's and that the reply is
    consistent with that command (``reply_consistency``). ``ok`` carries the inspect shape (``gain``); ``applied``,
    ``precondition_failed`` and ``uncertain`` carry the write shape (``before``/``after``/``db_change``/``replayed``);
    ``identity_mismatch`` carries either shape without gain; every other status carries no observations.
    """
    if message.get("v") != PROTOCOL_VERSION or message.get("type") != "reply":
        raise ProtocolError("type", "not a version-1 reply")
    if set(message) != {"v", "id", "type", "status", "observed", "error"}:
        raise ProtocolError("field", "reply must have exactly v, id, type, status, observed and error")
    status, observed = message["status"], message["observed"]
    if status not in STATUSES:
        raise ProtocolError("field", f"status must be one of {STATUSES}")
    if not (message["id"] is None or command_id(message) is not None):
        raise ProtocolError("field", "id must be a valid command ID or null")
    if status in ("ok", "applied", "applied_off_target", "identity_mismatch", "precondition_failed", "uncertain"):
        if message["id"] is None:
            raise ProtocolError("field", f"a {status} reply must carry the command ID")
        if status == "ok":
            _shape(observed, _INSPECT_OBSERVED, status)
            if observed["insert"]["name"] is None:
                raise ProtocolError("field", "an ok reply must name the insert")
            _gain(observed["gain"], "observed.gain")
        elif status == "identity_mismatch" and isinstance(observed, dict) and "gain" in observed:
            _shape(observed, _INSPECT_OBSERVED, status)
            if observed["gain"] is not None:
                raise ProtocolError("field", "an identity_mismatch reply must not carry a gain")
        else:
            _write_observed(observed, status)
    elif observed is not None:
        raise ProtocolError("field", f"a {status} reply must not carry observations")
    if status in ("ok", "applied"):
        if message["error"] is not None:
            raise ProtocolError("field", f"an {status} reply must not carry an error")
    else:
        _text(message["error"], "error", MAX_ERROR_BYTES)
    return message


def probe_readback_agrees(after: dict, target_normalized: float) -> bool:
    """A probe landed: the fader reads its target within the normalized tolerance (dB may move either way)."""
    return _round(abs(after["normalized"] - target_normalized)) <= START_TOLERANCE_NORMALIZED


def _applied_consistent(command: dict, observed: dict, status: str = "applied") -> bool:
    before, after = observed["before"], observed["after"]
    if command["type"] == "set_gain_db":
        miss_ok = _round(abs(db_miss(before, after, command["intended_db_change"]))) <= DB_SUCCESS_TOLERANCE
        return (
            start_matches(before, command["expected"])
            and probe_readback_agrees(after, command["target_normalized"])
            and abs(observed["db_change"] - (after["db"] - before["db"])) <= 1e-9
            and miss_ok == (status == "applied")
        )
    if command["type"] == "restore_gain":
        return (
            start_matches(before, command["expected"])
            and probe_readback_agrees(after, command["target"]["normalized"])
            and abs(observed["db_change"] - (after["db"] - before["db"])) <= 1e-9
        )
    if command["type"] == "probe_gain":
        return (
            start_matches(before, command["expected"])
            and probe_readback_agrees(after, command["target_normalized"])
            and abs(observed["db_change"] - (after["db"] - before["db"])) <= 1e-9
        )
    return (
        start_matches(before, command["expected"])
        and readback_agrees(before, after, command["target_normalized"])
        and abs(observed["db_change"] - (after["db"] - before["db"])) <= 1e-9
    )


def reply_consistency(command: dict, message: dict) -> str | None:
    """Why a structurally valid reply contradicts its command, or None.

    - The status must be one the command type can produce, and the observation shape must match the type.
    - ``ok``: exactly the expected project title and insert. ``identity_mismatch``: actually different.
    - ``applied``, ``precondition_failed``, ``uncertain``: the expected identity (the script checks it first).
    - ``applied``: starting gain within the tolerances of ``expected``, read-back at the target with a dB decrease,
      and ``db_change`` equal to after minus before. An ``uncertain`` reply only reports, so its values are not judged.
    """
    status, observed, kind = message["status"], message["observed"], command["type"]
    if status not in STATUSES_FOR[kind]:
        return "status_not_for_type"
    if observed is None:
        return None
    if ("before" in observed) != (kind in WRITE_TYPES):
        return "shape_differs"
    same = observed["project_title"] == command["project_title"] and observed["insert"] == command["insert"]
    if status == "identity_mismatch":
        return "mismatch_identity_equal" if same else None
    if kind == "inspect":
        return None if same else "ok_identity_differs"
    if not same:
        return "write_identity_differs"
    if status in ("applied", "applied_off_target") and not _applied_consistent(command, observed, status):
        return "applied_values_inconsistent"
    return None
