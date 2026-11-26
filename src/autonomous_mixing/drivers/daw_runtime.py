"""Runtime components and validated configuration for the operator DAW commands (Increment 6, ADR-040).

Production pieces the core ports need and no adapter provides: a system clock whose cancellation the operator
triggers with Ctrl+C, and process-unique identities (UUID tokens; UUID-based FL inspection IDs). The configuration
(JSON, schema 1 or 2) covers what operator tooling uses: the run-store directory, the FL command journal and session,
the calibrated profile's reference and (schema 2 only) its explicit selection ``daw_profile``, the MIDI ports, the audio roots and read limits. Network and synced folders are
unsupported (UNC paths are refused; synced folders are documented, not detected). It has no field for hidden
evaluation data (SR-10): unknown keys are refused by name.

R5C increment C1 (ADR-043, Proposed) adds the separate run-configuration file read by ``load_run_config``. It is a
strict, bounded, allowlisted JSON envelope (schema 1) of codec-encoded core records. Every value derived from
preparation or the tool configuration (the analysis specification and its prepared digest, the diagnosis jobs, the DAW
profile, the provider binding, the read limits and the output root) is absent from it, so the run file cannot declare
them. ``daw start`` (R5C C2) loads it once; ``daw continue`` is not yet available (R5C C3).
"""

import codecs
import json
import math
import os
import re
import signal
import stat
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from autonomous_mixing.adapters.decision_agent.config import ClaudeConfig
from autonomous_mixing.adapters.fl_studio.controller import FlStudioParameterController
from autonomous_mixing.adapters.fl_studio.journal import SqliteCommandJournal
from autonomous_mixing.adapters.fl_studio.profile import EXISTING_PROFILE, PROFILE_NAMES, supported_profile
from autonomous_mixing.adapters.fl_studio.transport import WinMMTransport
from autonomous_mixing.adapters.journal.record_codec import decode
from autonomous_mixing.core.records.admission import ActionAdmissionPolicy, StemBinding, TrackGainPermission
from autonomous_mixing.core.records.budgets import BudgetLimits
from autonomous_mixing.core.records.capabilities import RendererCapabilities
from autonomous_mixing.core.records.context import MusicalContext
from autonomous_mixing.core.records.coordinator_runtime import ClockReading, DecisionRetryPolicy, RunRequest
from autonomous_mixing.core.records.daw_binding import DawTrackBinding
from autonomous_mixing.core.records.daw_correction import DawSessionConfig
from autonomous_mixing.core.records.daw_export import DawExportFormat, DawExportTimeline, DawRoutingDeclaration
from autonomous_mixing.core.records.decision import ControllerIdentity
from autonomous_mixing.core.records.identifiers import ArtifactId, ConditionId, RunId, VersionRef
from autonomous_mixing.core.records.integrity import ArtifactRootBinding
from autonomous_mixing.core.records.masking_measurement import (
    P1_EXPERIMENTAL_METHOD,
    ExperimentalMaskingPolicy,
    MaskingPreparationJob,
    MaskingPreparationRequest,
    QualificationStatus,
)
from autonomous_mixing.core.records.mix import MixRecipe, ProcessingSpec
from autonomous_mixing.core.records.observation import AnalysisScope, AnalysisSpec, RenderSpec
from autonomous_mixing.core.records.project import ProjectState
from autonomous_mixing.core.records.run import RunConfiguration
from autonomous_mixing.core.records.verification import AcceptanceCriteria, MetricSelector
from autonomous_mixing.core.records.verification_assessment import ConditionMeasurement
from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition, SourceRequirement

SCHEMA = 1
TOOL_SCHEMAS = (1, 2)  # tool configuration: schema 2 adds the explicit daw_profile selection
_RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


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
    daw_profile: str  # one of PROFILE_NAMES: schema 1 always means the existing profile; schema 2 names it

    def daw_control_profile(self):
        """The calibrated profile this configuration selects (one of exactly two literal declarations)."""
        return supported_profile(self.daw_profile, reference=self.profile_reference)

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
    """Parse and validate ``path`` (JSON, schema 1 or 2); raises ``ConfigError`` naming the key path.

    Schema 1 always selects the existing calibrated profile; schema 2 requires ``daw_profile``, one of exactly two
    names (HT01 production-profile plan 2026-11-26). No other profile can be declared.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfigError(f"configuration: cannot read {path}: {error}") from error
    base = path.parent.resolve()
    keys = ("schema", "run_store_dir", "fl_journal", "profile_reference", "midi", "roots", "read_limits")
    _object(data, "", keys, optional=("daw_profile",))
    schema = data["schema"]
    if type(schema) is not int or schema not in TOOL_SCHEMAS:
        raise ConfigError(f"schema: must be one of {TOOL_SCHEMAS}")
    if schema == 1:
        if "daw_profile" in data:
            raise ConfigError("daw_profile: not allowed in schema 1 (it always means the existing profile)")
        daw_profile = EXISTING_PROFILE
    else:
        if "daw_profile" not in data:
            raise ConfigError("daw_profile: missing (required by schema 2)")
        daw_profile = data["daw_profile"]
        if type(daw_profile) is not str or daw_profile not in PROFILE_NAMES:
            raise ConfigError(f"daw_profile: must be one of {', '.join(PROFILE_NAMES)}")
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
                         _positive_int(limits["max_decoded_bytes"], "read_limits.max_decoded_bytes"),
                         daw_profile)  # fmt: skip


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
    try:
        controller = FlStudioParameterController(
            profile=config.daw_control_profile(), journal=journal, transport_factory=factory,
            timeout_s=config.timeout_s, clock=time.monotonic, next_inspect_id=unique_inspect_id,
        )  # fmt: skip
    except BaseException:  # an incompatible journal scope is refused before MIDI; never leave the journal open
        journal.close()
        raise
    return journal, controller


# --- R5C increment C1: strict bounded reading and the run-configuration file (ADR-043, Proposed) ----------------------

# Resource bounds, not scientific thresholds. The 37 KB example has nesting depth 14 and integers of at most 8 digits.
MAX_RUN_CONFIG_BYTES = 4 << 20
MAX_RUN_CONFIG_DEPTH = 64
MAX_INT_DIGITS = 20
RUN_CONFIG_KIND = "autonomous_mixing.daw_agent_run"
# The single reviewed, uncalibrated experimental screen (R5 plan section 1). A run file must repeat it exactly; it is
# visible and recorded, but a different policy needs a reviewed code change rather than a configuration edit.
REVIEWED_EXPERIMENTAL_POLICY = ExperimentalMaskingPolicy(P1_EXPERIMENTAL_METHOD, 40.0, 0.02, 0.02, 100, 0.25, 4)
# Validates the declared run before preparation exists; never recorded, replaced by the prepared specification.
PROVISIONAL_ANALYSIS_SPEC = VersionRef("autonomous_mixing.daw_agent_analysis", "1+unprepared")
_EVALUATION_NAMES = ("reference", "hidden", "evaluation", "ground_truth", "clean", "target_mix", "injected")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError(f"configuration: duplicate key {key!r}")
        result[key] = value
    return result


def _no_constant(name):
    raise ConfigError(f"configuration: non-finite number {name} is not allowed")


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ConfigError(f"configuration: non-finite number {text[:32]} is not allowed")
    return value


def _check_depth(text: str, max_depth: int) -> None:
    """Linear scan of structural nesting outside string literals, before any recursive parser runs."""
    depth, in_string, escaped = 0, False, False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > max_depth:
                raise ConfigError(f"configuration: nested deeper than {max_depth} levels")
        elif char in "]}":
            depth -= 1


def read_strict_json(
    path: Path,
    *,
    max_bytes: int = MAX_RUN_CONFIG_BYTES,
    max_depth: int = MAX_RUN_CONFIG_DEPTH,
    max_int_digits: int = MAX_INT_DIGITS,
) -> object:
    """Parse a regular, unlinked UTF-8 JSON file of at most ``max_bytes`` without duplicate keys, non-finite numbers,
    integers longer than ``max_int_digits`` digits or nesting deeper than ``max_depth``; ``ConfigError`` otherwise.

    The file is checked with ``lstat``, opened without following links where the platform allows, and its identity is
    compared with the ``lstat`` result before at most ``max_bytes + 1`` bytes are read. Nothing is written.
    """
    path = Path(path)
    try:
        status = os.lstat(path)
    except OSError as error:
        raise ConfigError(f"configuration: cannot read {path}: {error}") from error
    linked = stat.S_ISLNK(status.st_mode) or bool(getattr(status, "st_file_attributes", 0) & _REPARSE)
    if linked or not stat.S_ISREG(status.st_mode):
        raise ConfigError(f"configuration: {path} must be a regular file, not a link, reparse point or directory")
    try:
        descriptor = os.open(path, _READ_FLAGS)
    except OSError as error:
        raise ConfigError(f"configuration: cannot read {path}: {error}") from error
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (status.st_dev, status.st_ino) or not stat.S_ISREG(opened.st_mode):
            raise ConfigError(f"configuration: {path} changed while it was opened")
        chunks, remaining = [], max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(1 << 16, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError as error:  # expected file I/O failure; anything else (a programming error) propagates unchanged
        raise ConfigError(f"configuration: cannot read {path}: {error}") from error
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if len(raw) > max_bytes:
        raise ConfigError(f"configuration: {path} is larger than {max_bytes} bytes")
    if raw.startswith(codecs.BOM_UTF8):
        raise ConfigError("configuration: a UTF-8 byte-order mark is not allowed")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ConfigError(f"configuration: not valid UTF-8: {error}") from error
    _check_depth(text, max_depth)

    def bounded_int(literal):
        if len(literal.lstrip("-")) > max_int_digits:
            raise ConfigError(f"configuration: integer with more than {max_int_digits} digits is not allowed")
        return int(literal)

    try:
        return json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant,
                          parse_float=_finite_float, parse_int=bounded_int)  # fmt: skip
    except ConfigError:
        raise
    except RecursionError as error:  # defence in depth; the scan above bounds nesting first
        raise ConfigError("configuration: nested too deeply") from error
    except ValueError as error:
        raise ConfigError(f"configuration: invalid JSON: {error}") from error


def _envelope(value, key, keys):
    """An allowlisted plain object: evaluation/hidden names, unknown and missing keys are refused by key path."""
    if not isinstance(value, dict):
        raise ConfigError(f"{key or 'configuration'}: must be an object")
    prefix = key + "." if key else ""
    for name in value:
        if any(word in name.lower() for word in _EVALUATION_NAMES):
            raise ConfigError(f"{prefix}{name}: evaluation/hidden inputs are not runtime configuration")
    for name in value:
        if name not in keys:
            raise ConfigError(f"{prefix}{name}: unknown key")
    for name in keys:
        if name not in value:
            raise ConfigError(f"{prefix}{name}: missing")
    return value


def _record(value, key, kind):
    try:
        decoded = decode(value)
    except ValueError as error:
        raise ConfigError(f"{key}: invalid record: {error}") from error
    if not isinstance(decoded, kind):
        raise ConfigError(f"{key}: must be a {kind.__name__}")
    return decoded


def _records(value, key, kind, *, nonempty=True):
    if not isinstance(value, list) or (nonempty and not value):
        raise ConfigError(f"{key}: must be a {'nonempty ' if nonempty else ''}list")
    items = tuple(_record(item, f"{key}.{index}", object) for index, item in enumerate(value))
    if any(not isinstance(item, kind) for item in items):
        raise ConfigError(f"{key}: every item must be a {kind.__name__}")
    return items


def _positive_number(value, key):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{key}: must be a positive finite number")
    return value


_TOP = ("schema", "kind", "run", "preparation", "provider", "limits")
_RUN = ("initial_state", "root_bindings", "scope", "render_spec", "context", "criteria", "controller", "budget_limits",
        "retry_policy", "policy", "renderer_capabilities", "processing_spec", "recipe", "stem_bindings",
        "mix_artifact_id", "alignment", "definitions", "measurements", "daw_session")  # fmt: skip
_RUN_RECORDS = {"initial_state": ProjectState, "scope": AnalysisScope, "render_spec": RenderSpec,
                "context": MusicalContext, "criteria": AcceptanceCriteria, "controller": ControllerIdentity,
                "budget_limits": BudgetLimits, "retry_policy": DecisionRetryPolicy, "policy": ActionAdmissionPolicy,
                "renderer_capabilities": RendererCapabilities, "processing_spec": ProcessingSpec, "recipe": MixRecipe,
                "mix_artifact_id": ArtifactId, "alignment": VersionRef}  # fmt: skip
_DEFINITION = ("name", "selector", "unit", "analyzer", "alignment", "sources")
_MEASUREMENT = ("condition_id", "definition")
_SESSION = {"binding": DawTrackBinding, "permission": TrackGainPermission, "routing": DawRoutingDeclaration,
            "export_format": DawExportFormat, "export_timeline": DawExportTimeline, "render_spec": VersionRef}  # fmt: skip
_PREPARATION = ("profile", "jobs", "experimental_policy", "max_elapsed_seconds", "known_alignments")
_PROVIDER = ("model", "api_version", "max_tokens", "temperature", "timeout_seconds", "max_request_bytes",
             "max_response_bytes")  # fmt: skip
_LIMITS = ("max_evidence_bytes", "max_audit_record_bytes")


@dataclass(frozen=True)
class DeclaredDefinition:
    """One declared verification/guard measurement job without its analysis specification (derived on preparation)."""

    selector: MetricSelector
    unit: str
    analyzer: VersionRef
    alignment: VersionRef
    sources: tuple[SourceRequirement, ...]

    def with_specification(self, specification: VersionRef) -> MeasurementDefinition:
        return MeasurementDefinition(self.selector, self.unit, specification, self.analyzer, self.alignment,
                                     self.sources)  # fmt: skip


@dataclass(frozen=True)
class DawRunConfig:
    """Driver-local, validated run declaration (schema 1). Everything is explicit; nothing has a default.

    ``run`` and ``session`` map envelope names to decoded core records (driver-local data, not core records).
    """

    path: Path
    run: dict
    definitions: tuple[DeclaredDefinition, ...]
    measurements: tuple[tuple[ConditionId, int], ...]
    session: dict
    preparation: MaskingPreparationRequest
    known_alignments: frozenset[VersionRef]
    provider: ClaudeConfig
    max_evidence_bytes: int
    max_audit_record_bytes: int

    def _request(self, run_id: RunId, tool: DawToolConfig, specification: VersionRef, diagnostics) -> RunRequest:
        run, session = self.run, self.session
        declared = tuple(d.with_specification(specification) for d in self.definitions)
        metrics = tuple(dict.fromkeys([*(d.selector.metric for d in declared),
                                       *(d.selector.metric for d in diagnostics)]))  # fmt: skip
        configuration = RunConfiguration(run["scope"], AnalysisSpec(specification, metrics), run["render_spec"],
                                         run["context"], run["criteria"], run["controller"], run["budget_limits"])  # fmt: skip
        daw_session = DawSessionConfig(session["binding"], tool.daw_control_profile(),
                                       session["permission"], session["routing"], session["export_format"],
                                       session["export_timeline"], session["render_spec"], session["export_root_key"])  # fmt: skip
        return RunRequest(
            run_id, run["initial_state"], configuration, run["policy"], run["renderer_capabilities"],
            run["processing_spec"], run["recipe"], run["stem_bindings"], run["mix_artifact_id"],
            tuple(ConditionMeasurement(condition, declared[index]) for condition, index in self.measurements),
            (*declared, *diagnostics), run["alignment"], run["root_bindings"], session["export_root_key"],
            run["retry_policy"], daw_session,
        )  # fmt: skip

    def provisional_request(self, run_id: RunId, tool: DawToolConfig) -> RunRequest:
        """The complete request with ``PROVISIONAL_ANALYSIS_SPEC`` and no diagnosis jobs or provider binding: every
        structural invariant runs before any write. It is discarded; it is never recorded."""
        try:
            return self._request(run_id, tool, PROVISIONAL_ANALYSIS_SPEC, ())
        except ValueError as error:
            raise ConfigError(f"run: {error}") from error

    def request_factory(self, run_id: RunId, tool: DawToolConfig) -> Callable[..., RunRequest]:
        """The typed factory ``DawAgentRuntime.create`` calls with the prepared plan and its diagnosis jobs. It adds
        the diagnosis jobs as analysis jobs only, never as conditions, and copies no diagnosis threshold."""

        def factory(plan, diagnostics):
            return self._request(run_id, tool, plan.specification, tuple(diagnostics))

        return factory


def load_run_config(path: Path, tool: DawToolConfig) -> DawRunConfig:
    """Read, validate and decode a run file (schema 1) against the tool configuration ``tool``.

    Raises ``ConfigError`` naming the key path; nothing is written and no device or provider is contacted.
    """
    from autonomous_mixing.adapters.dsp_analysis.file_analyzer import ANALYZER as LEVEL_ANALYZER

    path = Path(path)
    data = _envelope(read_strict_json(path), "", _TOP)
    if type(data["schema"]) is not int or data["schema"] != SCHEMA:
        raise ConfigError(f"schema: must be {SCHEMA}")
    if data["kind"] != RUN_CONFIG_KIND:
        raise ConfigError(f"kind: must be {RUN_CONFIG_KIND!r}")
    roots = dict(tool.roots)

    raw = _envelope(data["run"], "run", _RUN)
    run = {name: _record(raw[name], f"run.{name}", kind) for name, kind in _RUN_RECORDS.items()}
    run["root_bindings"] = _records(raw["root_bindings"], "run.root_bindings", ArtifactRootBinding)
    run["stem_bindings"] = _records(raw["stem_bindings"], "run.stem_bindings", StemBinding)
    for binding in run["root_bindings"]:
        if binding.root_key not in roots:
            raise ConfigError(f"run.root_bindings: root key {binding.root_key!r} is not a configured root")

    if not isinstance(raw["definitions"], list) or not raw["definitions"]:
        raise ConfigError("run.definitions: must be a nonempty list")
    definitions, names = [], {}
    for index, item in enumerate(raw["definitions"]):
        key = f"run.definitions.{index}"
        item = _envelope(item, key, _DEFINITION)
        name = _text(item["name"], f"{key}.name")
        if name in names:
            raise ConfigError(f"{key}.name: duplicate definition name {name!r}")
        names[name] = index
        analyzer = _record(item["analyzer"], f"{key}.analyzer", VersionRef)
        if analyzer != LEVEL_ANALYZER:
            raise ConfigError(f"{key}.analyzer: must be the level analyzer {LEVEL_ANALYZER.name}@"
                              f"{LEVEL_ANALYZER.version}; P1 metrics are diagnosis jobs in this version")  # fmt: skip
        definitions.append(DeclaredDefinition(
            _record(item["selector"], f"{key}.selector", MetricSelector), _text(item["unit"], f"{key}.unit"),
            analyzer, _record(item["alignment"], f"{key}.alignment", VersionRef),
            _records(item["sources"], f"{key}.sources", SourceRequirement),
        ))  # fmt: skip
    if not isinstance(raw["measurements"], list):
        raise ConfigError("run.measurements: must be a list")
    measurements = []
    for index, item in enumerate(raw["measurements"]):
        key = f"run.measurements.{index}"
        item = _envelope(item, key, _MEASUREMENT)
        name = _text(item["definition"], f"{key}.definition")
        if name not in names:
            raise ConfigError(f"{key}.definition: names no declared definition ({name!r})")
        measurements.append((_record(item["condition_id"], f"{key}.condition_id", ConditionId), names[name]))

    raw_session = _envelope(raw["daw_session"], "run.daw_session", (*_SESSION, "export_root_key"))
    session = {name: _record(raw_session[name], f"run.daw_session.{name}", kind) for name, kind in _SESSION.items()}
    export_root = _text(raw_session["export_root_key"], "run.daw_session.export_root_key")
    if export_root not in roots:
        raise ConfigError(f"run.daw_session.export_root_key: {export_root!r} is not a configured root")
    session["export_root_key"] = export_root

    prep = _envelope(data["preparation"], "preparation", _PREPARATION)
    policy = _record(prep["experimental_policy"], "preparation.experimental_policy", ExperimentalMaskingPolicy)
    if policy != REVIEWED_EXPERIMENTAL_POLICY:
        raise ConfigError("preparation.experimental_policy: must equal the reviewed experimental policy "
                          "(p1.experimental_screen@1: 40 / 0.02 / 0.02 / 100 / 0.25 / 4)")  # fmt: skip
    if not isinstance(prep["known_alignments"], list) or not prep["known_alignments"]:
        raise ConfigError("preparation.known_alignments: must be a nonempty list")
    alignments = _records(prep["known_alignments"], "preparation.known_alignments", VersionRef)
    if len(set(alignments)) != len(alignments):
        raise ConfigError("preparation.known_alignments: duplicate method")
    profile = _record(prep["profile"], "preparation.profile", VersionRef)
    jobs = _records(prep["jobs"], "preparation.jobs", MaskingPreparationJob)
    elapsed = _positive_number(prep["max_elapsed_seconds"], "preparation.max_elapsed_seconds")
    try:
        preparation = MaskingPreparationRequest(run["initial_state"], run["root_bindings"], profile, jobs,
                                                QualificationStatus.UNCALIBRATED, tool.max_file_bytes,
                                                tool.max_decoded_bytes, elapsed, policy)  # fmt: skip
    except ValueError as error:
        raise ConfigError(f"preparation: {error}") from error

    provider = _envelope(data["provider"], "provider", _PROVIDER)
    try:
        claude = ClaudeConfig(**provider)
    except (TypeError, ValueError) as error:
        raise ConfigError(f"provider: {error}") from error
    limits = _envelope(data["limits"], "limits", _LIMITS)
    return DawRunConfig(
        path, run, tuple(definitions), tuple(measurements), session, preparation, frozenset(alignments), claude,
        _positive_int(limits["max_evidence_bytes"], "limits.max_evidence_bytes"),
        _positive_int(limits["max_audit_record_bytes"], "limits.max_audit_record_bytes"),
    )  # fmt: skip
