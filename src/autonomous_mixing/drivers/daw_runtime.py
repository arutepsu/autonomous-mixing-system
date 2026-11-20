"""Runtime components and validated configuration for the operator DAW commands (Increment 6, ADR-040).

Production pieces the core ports need and no adapter provides: a system clock whose cancellation the operator
triggers with Ctrl+C, and process-unique identities (UUID tokens; UUID-based FL inspection IDs). The configuration
(JSON, schema 1) covers what operator tooling uses: the run-store directory, the FL command journal and session, the
calibrated profile's reference, the MIDI ports, the audio roots and read limits. Network and synced folders are
unsupported (UNC paths are refused; synced folders are documented, not detected). It has no field for hidden
evaluation data (SR-10): unknown keys are refused by name.

Autonomous runs are unavailable: no ``DecisionMaker`` or ``DiagnosisSource`` implementation exists yet, so nothing
here composes one and ``daw start`` / ``daw continue`` refuse before opening anything.
"""

import json
import math
import re
import signal
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from autonomous_mixing.adapters.fl_studio.controller import FlStudioParameterController
from autonomous_mixing.adapters.fl_studio.journal import SqliteCommandJournal
from autonomous_mixing.adapters.fl_studio.profile import calibrated_profile
from autonomous_mixing.adapters.fl_studio.transport import WinMMTransport
from autonomous_mixing.core.records.coordinator_runtime import ClockReading
from autonomous_mixing.core.records.identifiers import VersionRef

SCHEMA = 1
_RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
UNAVAILABLE = (
    "unavailable: autonomous DAW runs need a DecisionMaker and a DiagnosisSource, and no implementation of either "
    "exists yet (adapters.decision_agent and core.diagnosis are planned). Operator tooling is ready: use "
    "'daw status' and 'daw recover'. Nothing was opened or changed."
)


class SystemClock:
    """``RunClock``: monotonic seconds, UTC time and an operator cancellation flag."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def read(self) -> ClockReading:
        return ClockReading(time.monotonic(), datetime.now(timezone.utc), self._cancelled.is_set())

    def cancel(self) -> None:
        self._cancelled.set()


@contextmanager
def interrupt_cancels(clock: SystemClock):
    """While active, Ctrl+C sets the clock's cancellation instead of raising; the run stops at its next gate."""
    previous = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, lambda signum, frame: clock.cancel())
    try:
        yield clock
    finally:
        signal.signal(signal.SIGINT, previous)


class UuidIdentitySource:
    """``IdentitySource``: a fresh UUID4 per token (32 hex characters), unique across processes."""

    def next_token(self) -> str:
        return uuid.uuid4().hex


def unique_inspect_id() -> str:
    """A process-unique FL inspection ID; the FL journal records inspection IDs (ADR-039 driver obligation)."""
    return f"inspect_{uuid.uuid4().hex}"


class ConfigError(ValueError):
    """An invalid configuration; the message names the offending key path."""


@dataclass(frozen=True)
class DawToolConfig:
    """Driver-local, validated configuration of the operator DAW commands."""

    path: Path
    run_store_dir: Path
    fl_journal: Path
    session_key: str
    profile_reference: VersionRef
    midi_output: str
    midi_input: str
    timeout_s: float
    roots: tuple[tuple[str, str], ...]
    max_file_bytes: int
    max_decoded_bytes: int

    def store_path(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ConfigError("run_id: must be 1-64 letters, digits, '-' or '_'")
        return self.run_store_dir / f"{run_id}.sqlite"


def _object(value, key, keys, optional=()):
    if not isinstance(value, dict):
        raise ConfigError(f"{key or 'configuration'}: must be an object")
    for name in value:
        if name not in keys and name not in optional:
            raise ConfigError(f"{key + '.' if key else ''}{name}: unknown key")
    for name in keys:
        if name not in value:
            raise ConfigError(f"{key + '.' if key else ''}{name}: missing")
    return value


def _text(value, key):
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key}: must be nonblank text")
    return value


def _path(value, key, base: Path, *, directory=None):
    text = _text(value, key)
    if text.startswith("\\\\") or text.startswith("//"):
        raise ConfigError(f"{key}: network paths are unsupported; use local storage")
    path = Path(text)
    path = (path if path.is_absolute() else base / path).resolve()
    if directory is True and not path.is_dir():
        raise ConfigError(f"{key}: must be an existing directory")
    if directory is False and not path.parent.is_dir():
        raise ConfigError(f"{key}: its parent directory must exist")
    return path


def _positive_int(value, key):
    if type(value) is not int or value < 1:
        raise ConfigError(f"{key}: must be a positive integer")
    return value


def load_config(path: Path) -> DawToolConfig:
    """Parse and validate ``path`` (JSON, schema 1); raises ``ConfigError`` naming the key path."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfigError(f"configuration: cannot read {path}: {error}") from error
    base = path.parent.resolve()
    keys = ("schema", "run_store_dir", "fl_journal", "profile_reference", "midi", "roots", "read_limits")
    _object(data, "", keys)
    if data["schema"] != SCHEMA or type(data["schema"]) is not int:
        raise ConfigError(f"schema: must be {SCHEMA}")
    stores = _path(data["run_store_dir"], "run_store_dir", base, directory=True)
    fl = _object(data["fl_journal"], "fl_journal", ("path", "session_key"))
    journal = _path(fl["path"], "fl_journal.path", base, directory=False)
    session = _text(fl["session_key"], "fl_journal.session_key")
    reference = _object(data["profile_reference"], "profile_reference", ("name", "version"))
    profile = VersionRef(_text(reference["name"], "profile_reference.name"),
                         _text(reference["version"], "profile_reference.version"))  # fmt: skip
    midi = _object(data["midi"], "midi", ("output_port", "input_port", "timeout_s"))
    timeout = midi["timeout_s"]
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ConfigError("midi.timeout_s: must be a positive finite number")
    roots_data = data["roots"]
    if not isinstance(roots_data, dict) or not roots_data:
        raise ConfigError("roots: must be a nonempty object of root keys to directories")
    roots = tuple((_text(k, "roots"), str(_path(v, f"roots.{k}", base, directory=True)))
                  for k, v in sorted(roots_data.items()))  # fmt: skip
    for key, root in roots:
        if stores == Path(root) or Path(root) in stores.parents:
            raise ConfigError(f"run_store_dir: must lie outside the audio root roots.{key}")
    limits = _object(data["read_limits"], "read_limits", ("max_file_bytes", "max_decoded_bytes"))
    return DawToolConfig(path, stores, journal, session, profile, _text(midi["output_port"], "midi.output_port"),
                         _text(midi["input_port"], "midi.input_port"), float(timeout), roots,
                         _positive_int(limits["max_file_bytes"], "read_limits.max_file_bytes"),
                         _positive_int(limits["max_decoded_bytes"], "read_limits.max_decoded_bytes"))  # fmt: skip


def open_fl(config: DawToolConfig, *, read_only: bool, transport_factory=None):
    """The FL command journal and the controller over it.

    A read-only open takes no lock and writes nothing; its controller is given a transport factory that refuses, so
    nothing can ever be sent. A writable open uses the Windows MIDI transport unless a factory is supplied (tests).
    """
    journal = SqliteCommandJournal(config.fl_journal, session_key=config.session_key, read_only=read_only)
    if read_only:

        def factory():
            raise RuntimeError("a read-only view never opens a MIDI connection")

    else:
        factory = transport_factory or (lambda: WinMMTransport(config.midi_output, config.midi_input))
    controller = FlStudioParameterController(
        profile=calibrated_profile(reference=config.profile_reference), journal=journal, transport_factory=factory,
        timeout_s=config.timeout_s, clock=time.monotonic, next_inspect_id=unique_inspect_id,
    )  # fmt: skip
    return journal, controller
