"""Architecture tests for the application component packages and the infrastructure audio modules.

Contains the test-only import checker ``find_import_violations``. It resolves absolute, relative,
from-package and aliased imports against the real source tree (plus fixture-only look-alike modules),
matches exact package segments and enforces:

- the application component matrix (shared: none; observation: shared; decision: shared;
  admission: decision; execution: decision, admission), public-interface-only access across
  components, sibling-module (not own-interface) access within a component;
- the compatibility exception: the application root and the legacy modules may re-export from
  component interfaces, and component code never imports them;
- the infrastructure source rules: the descriptive ``infrastructure`` initializer imports nothing;
  ``audio`` re-exports its two leaves; ``dsp`` uses the standard library and NumPy only;
  ``verification`` uses the standard library, NumPy, soundfile, the ``domain.project`` interface and
  the ``filesystem`` interface (never ``filesystem.locations`` directly); the ``filesystem``
  initializer re-exports ``locations`` only; ``locations`` uses the standard library and the
  ``domain.project`` interface only;
- the ``workspaces`` initializer re-exports ``allocation`` and ``validation`` only; ``allocation`` uses
  the standard library and the ``domain.project``, ``application.execution`` and ``filesystem``
  interfaces only (never its own facade or ``validation``). ``validation`` may use the standard
  library, its ``allocation`` sibling (records and private policy helpers) and the same existing
  interfaces, never its own facade, audio or ``wav_output``; in fact it uses only ``allocation``.
  ``allocation`` is the only production module outside the application that imports an application
  module;
- the ``wav_output`` initializer re-exports ``writing`` only; ``writing`` uses the standard library,
  NumPy, soundfile and the ``domain.project``, ``domain.observation`` and ``workspaces`` public
  interfaces only (never application, audio, filesystem or workspace internals). ``writing`` is the
  only module outside ``workspaces`` that imports it besides ``gain_mix``;
- the ``processing`` initializer re-exports ``gain_mix`` only; ``gain_mix`` uses the standard library,
  NumPy, the ``domain.project``, ``domain.shared`` and ``domain.action`` interfaces, the
  ``application.execution`` interface and the ``audio``, ``filesystem``, ``workspaces`` and ``wav_output``
  interfaces only (ADR-026). It is the only module importing ``wav_output``, and nothing imports it.

These are source-import rules. The ``audio`` facade loads both leaves eagerly, so importing it is not
claimed to avoid loading soundfile.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
PKG = "autonomous_mixing"
APPLICATION = f"{PKG}.application"
INFRASTRUCTURE = f"{PKG}.infrastructure"
AUDIO = f"{INFRASTRUCTURE}.audio"
DSP = f"{AUDIO}.dsp"
VERIFICATION = f"{AUDIO}.verification"
FILESYSTEM = f"{INFRASTRUCTURE}.filesystem"
LOCATIONS = f"{FILESYSTEM}.locations"
WORKSPACES = f"{INFRASTRUCTURE}.workspaces"
ALLOCATION = f"{WORKSPACES}.allocation"
VALIDATION = f"{WORKSPACES}.validation"
WAV_OUTPUT = f"{INFRASTRUCTURE}.wav_output"
WRITING = f"{WAV_OUTPUT}.writing"
PROCESSING = f"{INFRASTRUCTURE}.processing"
GAIN_MIX = f"{PROCESSING}.gain_mix"
DOMAIN = f"{PKG}.domain"
# ADR-027 migration step M1: domain and application records moved here; the new rules for this
# package are enforced by tests/architecture/test_core_records.py.
RECORDS = f"{PKG}.core.records"
CORE = f"{PKG}.core"  # ADR-027 step M2: behavior moved to core packages
DOMAIN_COMPONENTS = ("shared", "project", "context", "observation", "action", "decision", "validation", "run")

COMPONENTS = ("shared", "observation", "decision", "admission", "execution")
ALLOWED = {
    "shared": frozenset(),
    "observation": frozenset({"shared"}),
    "decision": frozenset({"shared"}),
    "admission": frozenset({"decision"}),
    "execution": frozenset({"decision", "admission"}),
}
LEGACY_FACADES = frozenset(
    {
        f"{APPLICATION}.contracts",
        f"{APPLICATION}.results",
        f"{APPLICATION}.execution_contracts",
        f"{APPLICATION}.execution_preflight",
        f"{APPLICATION}.ports",
        f"{APPLICATION}.ports.analysis",
        f"{APPLICATION}.ports.decision",
        f"{APPLICATION}.ports.processing",
    }
)
FACADES = LEGACY_FACADES | {APPLICATION}


def _source_modules(root: Path) -> dict[str, Path]:
    """Map every module under ``root`` (recursively, caches excluded) to its file."""
    modules = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = path.relative_to(SRC).with_suffix("").parts
        modules[".".join(parts[:-1] if parts[-1] == "__init__" else parts)] = path
    return modules


ALL_MODULES = _source_modules(SRC / PKG)
KNOWN = frozenset(ALL_MODULES)


def _within(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")


def _app_component(name: str) -> str | None:
    parts = name.split(".")
    if len(parts) >= 3 and parts[:2] == [PKG, "application"] and parts[2] in COMPONENTS:
        return parts[2]
    return None


def _resolve_targets(node: ast.AST, package: list[str], known_modules: frozenset[str]) -> list[tuple[str | None, bool]]:
    """Return (target module or None if unresolvable, is_relative) for one import statement."""
    if isinstance(node, ast.Import):
        return [(alias.name, False) for alias in node.names]
    assert isinstance(node, ast.ImportFrom)
    relative = node.level > 0
    if relative:
        if node.level - 1 >= len(package):
            return [(None, True)]
        base_parts = package[: len(package) - (node.level - 1)]
        base = ".".join(base_parts + ([node.module] if node.module else []))
    else:
        base = node.module or ""
    targets = []
    for alias in node.names:
        submodule = f"{base}.{alias.name}"
        # ``from package import name`` targets the submodule when ``name`` is a module, else the package itself.
        targets.append((submodule if submodule in known_modules else base, relative))
    return targets


def _top(target: str) -> str:
    return target.split(".")[0]


def _is_stdlib(target: str, relative: bool) -> bool:
    return not relative and _top(target) in sys.stdlib_module_names


def _domain_rule(target: str) -> str | None:
    """Domain access for application code: public component interfaces only (ADR-017)."""
    if target == DOMAIN or target in {f"{DOMAIN}.values", f"{DOMAIN}.execution"}:
        return "domain compatibility surface"
    parts = target.split(".")
    if len(parts) == 3 and parts[2] in DOMAIN_COMPONENTS:
        return None
    return "domain internals or unknown domain module"


def _application_rule(source: str, target: str, relative: bool, known_modules: frozenset[str]) -> str | None:
    if source not in FACADES and _app_component(source) is None:
        return "unclassified application module"
    if _is_stdlib(target, relative):
        return None
    if _top(target) != PKG:
        return "third-party import"
    if target not in known_modules:
        return "unresolved import"
    if _within(target, DOMAIN):
        return _domain_rule(target)
    if _within(target, CORE):
        return None if source not in FACADES else "compatibility facades re-export component interfaces only"
    if not _within(target, APPLICATION):
        return "foreign layer import"  # infrastructure, M0 audio, CLI, package root, look-alike packages
    source_component, target_component = _app_component(source), _app_component(target)
    if source in FACADES:
        if target_component is not None:
            return None if target == f"{APPLICATION}.{target_component}" else "non-public component internals"
        if _within(source, f"{APPLICATION}.ports") and _within(target, f"{APPLICATION}.ports") and source != target:
            return None if source == f"{APPLICATION}.ports" else "compatibility facade chaining"
        if target in FACADES:
            return "compatibility facade chaining"
        return "unclassified application module"
    if source_component is None:
        return "unclassified application module"
    if target in FACADES:
        return "compatibility surface used by component code"
    if target_component is None:
        return "unclassified application module"
    if target_component == source_component:
        own_interface = f"{APPLICATION}.{source_component}"
        return "own interface imported inside its component" if target == own_interface else None
    if target != f"{APPLICATION}.{target_component}":
        return "non-public component internals"
    return None if target_component in ALLOWED[source_component] else "dependency matrix"


# ADR-027 step M3: infrastructure leaves moved to adapters.rendering; their exact edges are now enforced by
# tests/architecture/test_adapters.py (ledger G10).
LEGACY_LEAF_ADAPTERS = {
    "autonomous_mixing.infrastructure.audio.dsp": "autonomous_mixing.adapters.rendering.dsp",
    "autonomous_mixing.infrastructure.audio.verification": "autonomous_mixing.adapters.rendering.integrity",
    "autonomous_mixing.infrastructure.filesystem.locations": "autonomous_mixing.adapters.rendering.locations",
    "autonomous_mixing.infrastructure.workspaces.allocation": "autonomous_mixing.adapters.rendering.workspaces",
    "autonomous_mixing.infrastructure.workspaces.validation": "autonomous_mixing.adapters.rendering.revalidation",
    "autonomous_mixing.infrastructure.wav_output.writing": "autonomous_mixing.adapters.rendering.wav_output",
    "autonomous_mixing.infrastructure.processing.gain_mix": "autonomous_mixing.adapters.rendering.gain_mix",
}


GAIN_MIX_TARGETS = frozenset(
    {
        f"{DOMAIN}.project",
        f"{DOMAIN}.shared",
        f"{DOMAIN}.action",
        f"{APPLICATION}.execution",
        AUDIO,
        FILESYSTEM,
        WORKSPACES,
        WAV_OUTPUT,
    }
)


def _infrastructure_rule(source: str, target: str, relative: bool, known_modules: frozenset[str]) -> str | None:
    if source == INFRASTRUCTURE:
        return "descriptive infrastructure initializer imports nothing"
    if source == AUDIO:
        return None if target in {DSP, VERIFICATION} else "audio facade may re-export only dsp and verification"
    if source == DSP:
        if _is_stdlib(target, relative) or _top(target) == "numpy":
            return None
        return "dsp may import only the standard library and NumPy"
    if source == VERIFICATION:
        if _is_stdlib(target, relative) or _top(target) in {"numpy", "soundfile"}:
            return None
        if target in {f"{DOMAIN}.project", FILESYSTEM} and target in known_modules:
            return None
        return "verification may import only the standard library, NumPy, soundfile, domain.project and filesystem"
    if source == FILESYSTEM:
        return None if target == LOCATIONS else "filesystem initializer may re-export only locations"
    if source == LOCATIONS:
        if _is_stdlib(target, relative):
            return None
        if target == f"{DOMAIN}.project" and target in known_modules:
            return None
        return "locations may import only the standard library and domain.project"
    if source == WORKSPACES:
        if target in {ALLOCATION, VALIDATION}:
            return None
        return "workspaces initializer may re-export only allocation and validation"
    if source == ALLOCATION:
        if _is_stdlib(target, relative):
            return None
        if target in {f"{DOMAIN}.project", f"{APPLICATION}.execution", FILESYSTEM} and target in known_modules:
            return None
        return "allocation may import only the standard library, domain.project, application.execution and filesystem"
    if source == VALIDATION:
        if _is_stdlib(target, relative):
            return None
        allowed = {ALLOCATION, f"{DOMAIN}.project", f"{APPLICATION}.execution", FILESYSTEM}
        if target in allowed and target in known_modules:
            return None
        return (
            "validation may import only the standard library, its allocation sibling, domain.project, "
            "application.execution and filesystem"
        )
    if source == WAV_OUTPUT:
        return None if target == WRITING else "wav_output initializer may re-export only writing"
    if source == WRITING:
        if _is_stdlib(target, relative) or _top(target) in {"numpy", "soundfile"}:
            return None
        if target in {f"{DOMAIN}.project", f"{DOMAIN}.observation", WORKSPACES} and target in known_modules:
            return None
        return (
            "writing may import only the standard library, NumPy, soundfile, domain.project, "
            "domain.observation and the workspaces interface"
        )
    if source == PROCESSING:
        return None if target == GAIN_MIX else "processing initializer may re-export only gain_mix"
    if source == GAIN_MIX:
        if _is_stdlib(target, relative) or _top(target) == "numpy":
            return None
        if target in GAIN_MIX_TARGETS and target in known_modules:
            return None
        return (
            "gain_mix may import only the standard library, NumPy, domain.project/shared/action, "
            "application.execution and the audio, filesystem, workspaces and wav_output interfaces"
        )
    return "unclassified infrastructure module needs an explicit rule"


def _rule(source: str, target: str | None, relative: bool, known_modules: frozenset[str]) -> str | None:
    if target is None:
        return "unresolvable relative import"
    if _within(source, INFRASTRUCTURE):
        return _infrastructure_rule(source, target, relative, known_modules)
    if _within(source, APPLICATION):
        return _application_rule(source, target, relative, known_modules)
    raise ValueError(f"{source} is outside the application and infrastructure layers")


def find_import_violations(
    source: str, module_name: str, is_package: bool, known_modules: frozenset[str]
) -> tuple[str, ...]:
    """Return one ``"source -> target: rule"`` string per forbidden import in ``source``."""
    package = module_name.split(".") if is_package else module_name.split(".")[:-1]
    violations = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for target, relative in _resolve_targets(node, package, known_modules):
            rule = _rule(module_name, target, relative, known_modules)
            if rule is not None:
                violations.append(f"{module_name} -> {target}: {rule}")
    return tuple(violations)


def find_cycles(edges: dict[str, set[str]]) -> list[list[str]]:
    """Return dependency cycles (as node paths) in a directed graph; empty if acyclic."""
    cycles, state = [], {}

    def visit(node: str, path: list[str]) -> None:
        state[node] = "active"
        for nxt in sorted(edges.get(node, ())):
            if state.get(nxt) == "active":
                cycles.append(path[path.index(nxt) :] + [nxt])
            elif nxt not in state:
                visit(nxt, path + [nxt])
        state[node] = "done"

    for start in sorted(edges):
        if start not in state:
            visit(start, [start])
    return cycles


# --- checker fixtures ----------------------------------------------------------------------

# Look-alike modules exist only in the fixture inventory; each shares a prefix with a real package.
FIXTURE_MODULES = KNOWN | {
    f"{PKG}.application_extra",
    f"{APPLICATION}.decision_extra",
    f"{APPLICATION}.helpers",
    f"{PKG}.infrastructure_extra",
    f"{AUDIO}.dsp_extra",
    f"{AUDIO}.writer",
    f"{DOMAIN}.project_extra",
    f"{INFRASTRUCTURE}.filesystem_extra",
    f"{FILESYSTEM}.locations_extra",
    f"{FILESYSTEM}.workspace",
    f"{INFRASTRUCTURE}.workspaces_extra",
    f"{WORKSPACES}.allocation_extra",
    f"{WORKSPACES}.registry",
    f"{WORKSPACES}.validation_extra",
    f"{INFRASTRUCTURE}.wav_output_extra",
    f"{WAV_OUTPUT}.writing_extra",
    f"{WAV_OUTPUT}.publication",
    f"{INFRASTRUCTURE}.processing_extra",
    f"{PROCESSING}.gain_mix_extra",
    f"{PROCESSING}.eq",
}
PACKAGES = frozenset(name for name, path in ALL_MODULES.items() if path.name == "__init__.py")


def _violations(source: str, module: str) -> tuple[str, ...]:
    return find_import_violations(source, module, module in PACKAGES, FIXTURE_MODULES)


OBS_CONTRACTS = f"{APPLICATION}.observation.contracts"
DEC_CONTRACTS = f"{APPLICATION}.decision.contracts"
ADM_CONTRACTS = f"{APPLICATION}.admission.contracts"
ADM_IMPL = f"{APPLICATION}.admission.admission"
EXE_REQUESTS = f"{APPLICATION}.execution.requests"
EXE_PROCESSING = f"{APPLICATION}.execution.processing"
EXE_PORTS = f"{APPLICATION}.execution.ports"
SHARED_RESULTS = f"{APPLICATION}.shared.results"

FORBIDDEN = [
    # observation -> decision (matrix), in absolute, relative, from-package and aliased forms
    ("from autonomous_mixing.application.decision import DecisionRequest", OBS_CONTRACTS, "matrix"),
    ("from ..decision import DecisionRequest", OBS_CONTRACTS, "matrix"),
    ("from autonomous_mixing.application import decision", OBS_CONTRACTS, "matrix"),
    ("from .. import decision", OBS_CONTRACTS, "matrix"),
    ("import autonomous_mixing.application.decision as decision_component", OBS_CONTRACTS, "matrix"),
    # decision -> observation (no such edge; decision keeps its own uniqueness check)
    ("from autonomous_mixing.application.observation import AnalysisRequest", DEC_CONTRACTS, "matrix"),
    ("from ..observation import AnalysisRequest", DEC_CONTRACTS, "matrix"),
    # shared -> anything in the application
    ("from autonomous_mixing.application.observation import AnalysisRequest", SHARED_RESULTS, "matrix"),
    ("from ..decision import DecisionRequest", SHARED_RESULTS, "matrix"),
    # admission -> execution, observation, shared (only decision is allowed)
    ("from autonomous_mixing.application.execution import ExecutionRequest", ADM_CONTRACTS, "matrix"),
    ("from ..execution import ExecutionRequest", ADM_CONTRACTS, "matrix"),
    ("from autonomous_mixing.application import execution", ADM_IMPL, "matrix"),
    ("import autonomous_mixing.application.execution as execution_component", ADM_IMPL, "matrix"),
    ("from autonomous_mixing.application.observation import AnalysisRequest", ADM_CONTRACTS, "matrix"),
    ("from autonomous_mixing.application.shared import Success", ADM_CONTRACTS, "matrix"),
    # execution -> observation / shared
    ("from autonomous_mixing.application.observation import AudioAnalyzer", EXE_PORTS, "matrix"),
    ("from ..shared import PortResult", EXE_PORTS, "matrix"),
    # cross-component internal-module access
    ("from autonomous_mixing.application.decision.contracts import DecisionRequest", ADM_CONTRACTS, "internals"),
    ("from autonomous_mixing.application.decision import contracts", ADM_CONTRACTS, "internals"),
    ("from ..decision.contracts import DecisionRequest", ADM_CONTRACTS, "internals"),
    ("import autonomous_mixing.application.admission.policy as policy", EXE_REQUESTS, "internals"),
    ("from ..admission.admission import admit_action", f"{APPLICATION}.execution.preflight", "internals"),
    ("from autonomous_mixing.application.shared.results import PortResult", OBS_CONTRACTS, "internals"),
    # a component importing its own package interface instead of sibling modules
    ("from autonomous_mixing.application.execution import ProcessingPlan", EXE_PORTS, "own interface"),
    ("from . import ProcessingPlan", EXE_PORTS, "own interface"),
    ("import autonomous_mixing.application.execution", EXE_PORTS, "own interface"),
    # component -> legacy shim or root facade
    ("from autonomous_mixing.application.contracts import DecisionRequest", ADM_CONTRACTS, "compatibility"),
    ("from autonomous_mixing.application.results import PortResult", f"{APPLICATION}.observation.ports", "compat"),
    ("from ..results import PortResult", f"{APPLICATION}.observation.ports", "compatibility"),
    ("from autonomous_mixing.application.ports import AudioAnalyzer", OBS_CONTRACTS, "compatibility"),
    ("from autonomous_mixing.application import ports", OBS_CONTRACTS, "compatibility"),
    ("from autonomous_mixing.application.ports.processing import AudioProcessor", EXE_PROCESSING, "compatibility"),
    ("import autonomous_mixing.application.execution_preflight as legacy", EXE_REQUESTS, "compatibility"),
    ("from ..execution_contracts import ProcessingPlan", EXE_PORTS, "compatibility"),
    ("from autonomous_mixing.application import DecisionRequest", ADM_CONTRACTS, "compatibility"),
    ("from .. import DecisionRequest", ADM_CONTRACTS, "compatibility"),
    ("import autonomous_mixing.application", ADM_CONTRACTS, "compatibility"),
    # facades re-export component interfaces only, never internals or foreign layers
    (
        "from autonomous_mixing.application.execution.preflight import preflight_execution",
        f"{APPLICATION}.execution_preflight",
        "internals",
    ),
    ("from autonomous_mixing.application.shared.results import Success", APPLICATION, "internals"),
    ("from autonomous_mixing.infrastructure.audio import sum_stems", f"{APPLICATION}.contracts", "layer"),
    ("from autonomous_mixing.application.contracts import DecisionRequest", APPLICATION, "compatibility"),
    # application -> infrastructure, M0 audio, CLI, third-party
    ("import autonomous_mixing.infrastructure.audio", EXE_PORTS, "layer"),
    ("from autonomous_mixing.infrastructure.audio import sum_stems", EXE_PROCESSING, "layer"),
    ("import autonomous_mixing.infrastructure.audio.dsp as dsp", EXE_PROCESSING, "layer"),
    ("from ...infrastructure import audio", EXE_PORTS, "layer"),
    ("from autonomous_mixing.audio.io import load_stem", OBS_CONTRACTS, "layer"),
    ("from ...audio import features", f"{APPLICATION}.observation.validation", "layer"),
    ("from autonomous_mixing import cli", DEC_CONTRACTS, "layer"),
    ("import autonomous_mixing", DEC_CONTRACTS, "layer"),
    ("import numpy", EXE_PROCESSING, "third-party"),
    ("import soundfile as sf", EXE_PROCESSING, "third-party"),
    ("import anthropic", DEC_CONTRACTS, "third-party"),
    # domain compatibility surfaces and domain internals
    ("from autonomous_mixing.domain import Decision", DEC_CONTRACTS, "domain compatibility"),
    ("from autonomous_mixing.domain.values import require_text", SHARED_RESULTS, "domain compatibility"),
    ("from autonomous_mixing.domain.execution import ExecutionStatus", EXE_PROCESSING, "domain compatibility"),
    ("from autonomous_mixing.domain.project.scope import AnalysisScope", OBS_CONTRACTS, "domain internals"),
    ("from autonomous_mixing.domain.project import scope", OBS_CONTRACTS, "domain internals"),
    # look-alike path segments are not their namesakes
    ("from autonomous_mixing.application_extra import x", ADM_CONTRACTS, "layer"),
    ("import autonomous_mixing.infrastructure_extra as extra", EXE_PROCESSING, "layer"),
    ("from autonomous_mixing.application.decision_extra import x", ADM_CONTRACTS, "unclassified"),
    ("from ..decision_extra import x", ADM_CONTRACTS, "unclassified"),
    ("from autonomous_mixing.application.helpers import x", EXE_PORTS, "unclassified"),
    ("from autonomous_mixing.domain.project_extra import x", OBS_CONTRACTS, "domain"),
    ("from autonomous_mixing.application.missing import x", OBS_CONTRACTS, "unresolved"),
    ("from ......... import x", OBS_CONTRACTS, "unresolvable"),
    # unclassified application modules are rejected until their ownership is decided
    ("import math", f"{APPLICATION}.helpers", "unclassified"),
    # infrastructure: descriptive initializer imports nothing
    ("from .audio import dsp", INFRASTRUCTURE, "descriptive"),
    ("import os", INFRASTRUCTURE, "descriptive"),
    # audio initializer re-exports its two leaves only
    ("from autonomous_mixing.domain.project import AudioArtifact", AUDIO, "audio facade"),
    ("import numpy as np", AUDIO, "audio facade"),
    ("from .dsp_extra import x", AUDIO, "audio facade"),
    ("from .writer import write_wav", AUDIO, "audio facade"),
    ("from autonomous_mixing.application.execution import AudioProcessor", AUDIO, "audio facade"),
    # dsp: standard library and NumPy only
    ("from autonomous_mixing.domain.project import AudioArtifact", DSP, "dsp"),
    ("from autonomous_mixing.domain import project", DSP, "dsp"),
    ("import soundfile as sf", DSP, "dsp"),
    ("from .verification import verify_wav_artifact", DSP, "dsp"),
    ("from . import verification", DSP, "dsp"),
    ("from autonomous_mixing.infrastructure.audio import verification", DSP, "dsp"),
    ("import autonomous_mixing.infrastructure.audio.verification as verification", DSP, "dsp"),
    ("from autonomous_mixing.application.execution import ProcessingPlan", DSP, "dsp"),
    ("import scipy.signal", DSP, "dsp"),
    ("import numpy_extra", DSP, "dsp"),
    ("from autonomous_mixing.audio.io import load_stem", DSP, "dsp"),
    # verification: standard library, NumPy, soundfile and the domain.project interface only
    ("from autonomous_mixing.application.execution import ProcessingPlan", VERIFICATION, "verification"),
    ("from ...application import execution", VERIFICATION, "verification"),
    ("import autonomous_mixing.application.shared as shared", VERIFICATION, "verification"),
    ("from .dsp import sum_stems", VERIFICATION, "verification"),
    ("from . import dsp", VERIFICATION, "verification"),
    ("import autonomous_mixing.infrastructure.audio.dsp as dsp", VERIFICATION, "verification"),
    ("from .dsp_extra import x", VERIFICATION, "verification"),
    ("from autonomous_mixing.audio.io import load_stem", VERIFICATION, "verification"),
    ("from ...audio import io", VERIFICATION, "verification"),
    ("from autonomous_mixing import cli", VERIFICATION, "verification"),
    ("from autonomous_mixing.domain.project.artifacts import AudioArtifact", VERIFICATION, "verification"),
    ("from autonomous_mixing.domain.decision import Decision", VERIFICATION, "verification"),
    ("from autonomous_mixing.domain import AudioArtifact", VERIFICATION, "verification"),
    ("from autonomous_mixing.domain.project_extra import x", VERIFICATION, "verification"),
    ("import soundfile_extra", VERIFICATION, "verification"),
    ("import scipy", VERIFICATION, "verification"),
    ("import anthropic", VERIFICATION, "verification"),
    # verification uses the filesystem public interface only, never its internals or look-alikes
    (
        "from autonomous_mixing.infrastructure.filesystem.locations import resolve_artifact_location",
        VERIFICATION,
        "verif",
    ),
    ("from autonomous_mixing.infrastructure.filesystem import locations", VERIFICATION, "verification"),
    ("from ..filesystem.locations import ResolvedArtifactLocation", VERIFICATION, "verification"),
    ("from ..filesystem import locations", VERIFICATION, "verification"),
    ("import autonomous_mixing.infrastructure.filesystem.locations as locations", VERIFICATION, "verification"),
    ("from autonomous_mixing.infrastructure.filesystem_extra import x", VERIFICATION, "verification"),
    ("from ..filesystem_extra import x", VERIFICATION, "verification"),
    ("from autonomous_mixing.infrastructure.filesystem.locations_extra import x", VERIFICATION, "verification"),
    ("from ..filesystem.workspace import allocate", VERIFICATION, "verification"),
    ("from ..filesystem_missing import x", VERIFICATION, "verification"),
    ("from autonomous_mixing.infrastructure import audio", VERIFICATION, "verification"),
    # dsp and the descriptive initializers never reach the filesystem component
    ("from autonomous_mixing.infrastructure.filesystem import resolve_artifact_location", DSP, "dsp"),
    ("from ..filesystem import ResolvedArtifactLocation", DSP, "dsp"),
    ("import autonomous_mixing.infrastructure.filesystem.locations", DSP, "dsp"),
    ("from .filesystem import resolve_artifact_location", INFRASTRUCTURE, "descriptive"),
    ("from . import filesystem", INFRASTRUCTURE, "descriptive"),
    ("from ..filesystem import resolve_artifact_location", AUDIO, "audio facade"),
    ("from autonomous_mixing.infrastructure.filesystem import ResolvedArtifactLocation", AUDIO, "audio facade"),
    # filesystem initializer re-exports locations only
    ("from autonomous_mixing.domain.project import AudioArtifact", FILESYSTEM, "filesystem initializer"),
    ("import os", FILESYSTEM, "filesystem initializer"),
    ("from .locations_extra import x", FILESYSTEM, "filesystem initializer"),
    ("from .workspace import allocate", FILESYSTEM, "filesystem initializer"),
    ("from ..audio import verify_wav_artifact", FILESYSTEM, "filesystem initializer"),
    ("from .missing import x", FILESYSTEM, "filesystem initializer"),
    # locations: standard library and the domain.project interface only
    ("from autonomous_mixing.infrastructure.audio import verify_wav_artifact", LOCATIONS, "locations"),
    ("from ..audio import verification", LOCATIONS, "locations"),
    ("import autonomous_mixing.infrastructure.audio.dsp as dsp", LOCATIONS, "locations"),
    ("from autonomous_mixing.application.execution import ExecutionWorkspace", LOCATIONS, "locations"),
    ("from ...application import execution", LOCATIONS, "locations"),
    ("import numpy as np", LOCATIONS, "locations"),
    ("import soundfile", LOCATIONS, "locations"),
    ("import numpy_extra", LOCATIONS, "locations"),
    ("import pathlib_extra", LOCATIONS, "locations"),
    ("from autonomous_mixing.domain.project.artifacts import AudioArtifact", LOCATIONS, "locations"),
    ("from autonomous_mixing.domain.project import artifacts", LOCATIONS, "locations"),
    ("from autonomous_mixing.domain.shared import require_text", LOCATIONS, "locations"),
    ("from autonomous_mixing.domain import AudioArtifact", LOCATIONS, "locations"),
    ("from autonomous_mixing.domain.project_extra import x", LOCATIONS, "locations"),
    ("from autonomous_mixing.audio.io import load_stem", LOCATIONS, "locations"),
    ("from autonomous_mixing import cli", LOCATIONS, "locations"),
    ("from . import workspace", LOCATIONS, "locations"),
    ("from .missing import x", LOCATIONS, "locations"),
    ("from . import locations_extra", LOCATIONS, "locations"),
    ("from ......... import x", LOCATIONS, "unresolvable"),
    ("from ......... import x", FILESYSTEM, "unresolvable"),
    # unclassified infrastructure modules need an explicit rule first
    ("import math", f"{FILESYSTEM}.workspace", "unclassified infrastructure"),
    ("import math", f"{INFRASTRUCTURE}.filesystem_extra", "unclassified infrastructure"),
    ("import math", f"{AUDIO}.writer", "unclassified infrastructure"),
    ("import math", f"{WORKSPACES}.registry", "unclassified infrastructure"),
    ("import math", f"{INFRASTRUCTURE}.workspaces_extra", "unclassified infrastructure"),
    # workspaces initializer re-exports allocation only
    ("from autonomous_mixing.application.execution import ExecutionWorkspace", WORKSPACES, "workspaces initializer"),
    ("from autonomous_mixing.infrastructure.filesystem import resolve_artifact_location", WORKSPACES, "workspaces"),
    ("import os", WORKSPACES, "workspaces initializer"),
    ("from .allocation_extra import x", WORKSPACES, "workspaces initializer"),
    ("from .registry import x", WORKSPACES, "workspaces initializer"),
    ("from .missing import x", WORKSPACES, "workspaces initializer"),
    ("from ..audio import verify_wav_artifact", WORKSPACES, "workspaces initializer"),
    ("from ......... import x", WORKSPACES, "unresolvable"),
    # allocation -> application internals, facades or other components
    ("from autonomous_mixing.application.execution.identity import ExecutionWorkspace", ALLOCATION, "allocation"),
    ("from autonomous_mixing.application.execution import identity", ALLOCATION, "allocation"),
    ("from ...application.execution.identity import ExecutionWorkspace", ALLOCATION, "allocation"),
    ("import autonomous_mixing.application.execution.preflight as preflight", ALLOCATION, "allocation"),
    ("from autonomous_mixing.application import ExecutionWorkspace", ALLOCATION, "allocation"),
    ("import autonomous_mixing.application", ALLOCATION, "allocation"),
    ("from autonomous_mixing.application.execution_contracts import ProcessingPlan", ALLOCATION, "allocation"),
    ("from autonomous_mixing.application.execution_preflight import preflight_execution", ALLOCATION, "allocation"),
    ("from autonomous_mixing.application.admission import admit_action", ALLOCATION, "allocation"),
    ("from autonomous_mixing.application.decision import DecisionRequest", ALLOCATION, "allocation"),
    ("from autonomous_mixing.application.shared import PortResult", ALLOCATION, "allocation"),
    ("from ...application import observation", ALLOCATION, "allocation"),
    # allocation -> filesystem internals or look-alikes
    ("from autonomous_mixing.infrastructure.filesystem.locations import _lexical_problem", ALLOCATION, "allocation"),
    ("from autonomous_mixing.infrastructure.filesystem import locations", ALLOCATION, "allocation"),
    ("from ..filesystem.locations import resolve_artifact_location", ALLOCATION, "allocation"),
    ("from ..filesystem import locations", ALLOCATION, "allocation"),
    ("import autonomous_mixing.infrastructure.filesystem.locations as locations", ALLOCATION, "allocation"),
    ("from ..filesystem_extra import x", ALLOCATION, "allocation"),
    ("from autonomous_mixing.infrastructure.filesystem.locations_extra import x", ALLOCATION, "allocation"),
    ("from ..filesystem.workspace import allocate", ALLOCATION, "allocation"),
    # allocation -> audio, DSP, M0, CLI, providers, third-party, domain internals and other components
    ("from autonomous_mixing.infrastructure.audio import verify_wav_artifact", ALLOCATION, "allocation"),
    ("from ..audio import dsp", ALLOCATION, "allocation"),
    ("import autonomous_mixing.infrastructure.audio.dsp as dsp", ALLOCATION, "allocation"),
    ("from ..audio.verification import verify_resolved_wav_artifact", ALLOCATION, "allocation"),
    ("from autonomous_mixing.infrastructure import audio", ALLOCATION, "allocation"),
    ("from autonomous_mixing.audio.io import load_stem", ALLOCATION, "allocation"),
    ("from ...audio import features", ALLOCATION, "allocation"),
    ("from autonomous_mixing import cli", ALLOCATION, "allocation"),
    ("import autonomous_mixing", ALLOCATION, "allocation"),
    ("import anthropic", ALLOCATION, "allocation"),
    ("import numpy as np", ALLOCATION, "allocation"),
    ("import soundfile", ALLOCATION, "allocation"),
    ("import pathlib_extra", ALLOCATION, "allocation"),
    ("from autonomous_mixing.domain.project.artifacts import AudioArtifact", ALLOCATION, "allocation"),
    ("from autonomous_mixing.domain.shared import require_text", ALLOCATION, "allocation"),
    ("from autonomous_mixing.domain import ProjectState", ALLOCATION, "allocation"),
    ("from autonomous_mixing.domain.project_extra import x", ALLOCATION, "allocation"),
    ("from autonomous_mixing.domain.execution import ExecutionStatus", ALLOCATION, "allocation"),
    # allocation never imports its own facade, unknown workspace modules or look-alikes
    ("from autonomous_mixing.infrastructure.workspaces import WorkspaceRoot", ALLOCATION, "allocation"),
    ("from . import WorkspaceRoot", ALLOCATION, "allocation"),
    ("import autonomous_mixing.infrastructure.workspaces", ALLOCATION, "allocation"),
    ("from .registry import x", ALLOCATION, "allocation"),
    ("from .allocation_extra import x", ALLOCATION, "allocation"),
    ("from ..workspaces_extra import x", ALLOCATION, "allocation"),
    ("from .missing import x", ALLOCATION, "allocation"),
    ("from ......... import x", ALLOCATION, "unresolvable"),
    # filesystem, audio and the descriptive initializer never import workspaces
    ("from autonomous_mixing.infrastructure.workspaces import allocate_workspace", LOCATIONS, "locations"),
    ("from ..workspaces import allocation", LOCATIONS, "locations"),
    ("from ..workspaces import allocate_workspace", FILESYSTEM, "filesystem initializer"),
    ("import autonomous_mixing.infrastructure.workspaces.allocation", FILESYSTEM, "filesystem initializer"),
    ("from ..workspaces import WorkspaceRoot", VERIFICATION, "verification"),
    ("import autonomous_mixing.infrastructure.workspaces as workspaces", VERIFICATION, "verification"),
    ("from ..workspaces.allocation import allocate_workspace", DSP, "dsp"),
    ("from ..workspaces import allocate_workspace", AUDIO, "audio facade"),
    ("from . import workspaces", INFRASTRUCTURE, "descriptive"),
    # application code never imports workspaces (layer rule unchanged)
    ("from autonomous_mixing.infrastructure.workspaces import allocate_workspace", EXE_PORTS, "layer"),
    ("from ...infrastructure import workspaces", EXE_PORTS, "layer"),
    # workspaces initializer re-exports allocation and validation only
    ("from .validation_extra import x", WORKSPACES, "workspaces initializer"),
    ("from ..wav_output import write_wav_output", WORKSPACES, "workspaces initializer"),
    # allocation never imports the validation sibling
    ("from .validation import validate_allocated_workspace", ALLOCATION, "allocation"),
    ("from . import validation", ALLOCATION, "allocation"),
    ("import autonomous_mixing.infrastructure.workspaces.validation as validation", ALLOCATION, "allocation"),
    ("from ..wav_output import write_wav_output", ALLOCATION, "allocation"),
    # validation: standard library, its allocation sibling and the existing workspace policy interfaces
    ("from autonomous_mixing.infrastructure.workspaces import allocate_workspace", VALIDATION, "validation"),
    ("from . import allocate_workspace", VALIDATION, "validation"),
    ("import autonomous_mixing.infrastructure.workspaces", VALIDATION, "validation"),
    ("from .registry import x", VALIDATION, "validation"),
    ("from .validation_extra import x", VALIDATION, "validation"),
    ("from .missing import x", VALIDATION, "validation"),
    ("from ..wav_output import write_wav_output", VALIDATION, "validation"),
    ("from ..wav_output.writing import WrittenWav", VALIDATION, "validation"),
    ("from ..audio import verify_wav_artifact", VALIDATION, "validation"),
    ("import autonomous_mixing.infrastructure.audio.dsp as dsp", VALIDATION, "validation"),
    ("from ..filesystem.locations import _lexical_problem", VALIDATION, "validation"),
    ("from autonomous_mixing.application.execution.identity import ExecutionWorkspace", VALIDATION, "validation"),
    ("from autonomous_mixing.application import ExecutionWorkspace", VALIDATION, "validation"),
    ("from autonomous_mixing.application.decision import DecisionRequest", VALIDATION, "validation"),
    ("from autonomous_mixing.domain.project.state import ProjectState", VALIDATION, "validation"),
    ("from autonomous_mixing.audio.io import load_stem", VALIDATION, "validation"),
    ("from autonomous_mixing import cli", VALIDATION, "validation"),
    ("import numpy as np", VALIDATION, "validation"),
    ("import soundfile", VALIDATION, "validation"),
    ("from ......... import x", VALIDATION, "unresolvable"),
    # wav_output initializer re-exports writing only
    ("import os", WAV_OUTPUT, "wav_output initializer"),
    ("from .publication import x", WAV_OUTPUT, "wav_output initializer"),
    ("from .writing_extra import x", WAV_OUTPUT, "wav_output initializer"),
    ("from .missing import x", WAV_OUTPUT, "wav_output initializer"),
    ("from ..workspaces import validate_allocated_workspace", WAV_OUTPUT, "wav_output initializer"),
    ("from ..audio import sum_stems", WAV_OUTPUT, "wav_output initializer"),
    ("from autonomous_mixing.domain.project import ProcessingSpec", WAV_OUTPUT, "wav_output initializer"),
    ("from ......... import x", WAV_OUTPUT, "unresolvable"),
    # writing: standard library, NumPy, soundfile, domain.project/observation and the workspaces interface
    ("from autonomous_mixing.application.execution import ExecutionWorkspace", WRITING, "writing"),
    ("from ...application import execution", WRITING, "writing"),
    ("import autonomous_mixing.application as application", WRITING, "writing"),
    ("from autonomous_mixing.application.shared import PortResult", WRITING, "writing"),
    ("from autonomous_mixing.infrastructure.audio import sum_stems", WRITING, "writing"),
    ("from ..audio.dsp import apply_absolute_gain", WRITING, "writing"),
    ("from ..audio import verification", WRITING, "writing"),
    ("import autonomous_mixing.infrastructure.audio.verification as verification", WRITING, "writing"),
    ("from autonomous_mixing.infrastructure import audio", WRITING, "writing"),
    ("from autonomous_mixing.infrastructure.filesystem import resolve_artifact_location", WRITING, "writing"),
    ("from ..filesystem.locations import ResolvedArtifactLocation", WRITING, "writing"),
    ("from ..filesystem import locations", WRITING, "writing"),
    ("from autonomous_mixing.infrastructure.workspaces.allocation import AllocatedWorkspace", WRITING, "writing"),
    ("from ..workspaces.validation import validate_allocated_workspace", WRITING, "writing"),
    ("from ..workspaces import allocation", WRITING, "writing"),
    ("from ..workspaces import validation", WRITING, "writing"),
    ("import autonomous_mixing.infrastructure.workspaces.allocation as allocation", WRITING, "writing"),
    ("from ..workspaces_extra import x", WRITING, "writing"),
    ("from ..workspaces.registry import x", WRITING, "writing"),
    ("from . import WrittenWav", WRITING, "writing"),
    ("import autonomous_mixing.infrastructure.wav_output", WRITING, "writing"),
    ("from autonomous_mixing.infrastructure.wav_output import WavOutputError", WRITING, "writing"),
    ("from .publication import x", WRITING, "writing"),
    ("from .writing_extra import x", WRITING, "writing"),
    ("from autonomous_mixing.audio.io import load_stem", WRITING, "writing"),
    ("from ...audio import features", WRITING, "writing"),
    ("from autonomous_mixing import cli", WRITING, "writing"),
    ("import autonomous_mixing", WRITING, "writing"),
    ("from autonomous_mixing.domain.project.processing_spec import ProcessingSpec", WRITING, "writing"),
    ("from autonomous_mixing.domain.observation.specifications import RenderSpec", WRITING, "writing"),
    ("from autonomous_mixing.domain import RenderSpec", WRITING, "writing"),
    ("from autonomous_mixing.domain.decision import Decision", WRITING, "writing"),
    ("from autonomous_mixing.domain.execution import ExecutionStatus", WRITING, "writing"),
    ("from autonomous_mixing.domain.project_extra import x", WRITING, "writing"),
    ("import scipy", WRITING, "writing"),
    ("import soundfile_extra", WRITING, "writing"),
    ("import anthropic", WRITING, "writing"),
    ("from ......... import x", WRITING, "unresolvable"),
    # no other module imports wav_output
    ("from ..wav_output import write_wav_output", VERIFICATION, "verification"),
    ("from ..wav_output import write_wav_output", DSP, "dsp"),
    ("from ..wav_output import write_wav_output", AUDIO, "audio facade"),
    ("from ..wav_output import write_wav_output", LOCATIONS, "locations"),
    ("from ..wav_output import write_wav_output", FILESYSTEM, "filesystem initializer"),
    ("from . import wav_output", INFRASTRUCTURE, "descriptive"),
    ("from autonomous_mixing.infrastructure.wav_output import write_wav_output", EXE_PORTS, "layer"),
    ("from ...infrastructure import wav_output", EXE_PROCESSING, "layer"),
    # unclassified look-alikes need an explicit rule first
    ("import math", f"{WAV_OUTPUT}.publication", "unclassified infrastructure"),
    ("import math", f"{WAV_OUTPUT}.writing_extra", "unclassified infrastructure"),
    ("import math", f"{INFRASTRUCTURE}.wav_output_extra", "unclassified infrastructure"),
    ("import math", f"{WORKSPACES}.validation_extra", "unclassified infrastructure"),
    # processing initializer re-exports gain_mix only
    ("import os", PROCESSING, "processing initializer"),
    ("from .eq import x", PROCESSING, "processing initializer"),
    ("from .gain_mix_extra import x", PROCESSING, "processing initializer"),
    ("from ..audio import sum_stems", PROCESSING, "processing initializer"),
    ("from ......... import x", PROCESSING, "unresolvable"),
    # gain_mix: no soundfile, internals, other application components, M0, CLI or providers
    ("import soundfile as sf", GAIN_MIX, "gain_mix"),
    ("import scipy", GAIN_MIX, "gain_mix"),
    ("import anthropic", GAIN_MIX, "gain_mix"),
    ("from ..audio.dsp import sum_stems", GAIN_MIX, "gain_mix"),
    ("from ..audio import dsp", GAIN_MIX, "gain_mix"),
    ("import autonomous_mixing.infrastructure.audio.verification as verification", GAIN_MIX, "gain_mix"),
    ("from ..filesystem.locations import ResolvedArtifactLocation", GAIN_MIX, "gain_mix"),
    ("from ..workspaces.allocation import AllocatedWorkspace", GAIN_MIX, "gain_mix"),
    ("from ..workspaces import validation", GAIN_MIX, "gain_mix"),
    ("from ..wav_output.writing import write_wav_output", GAIN_MIX, "gain_mix"),
    ("from ..wav_output import writing", GAIN_MIX, "gain_mix"),
    ("from . import GainMixProcessor", GAIN_MIX, "gain_mix"),
    ("from .eq import x", GAIN_MIX, "gain_mix"),
    ("from autonomous_mixing.application.execution.processing import ProcessingPlan", GAIN_MIX, "gain_mix"),
    ("from ...application.execution import ports", GAIN_MIX, "gain_mix"),
    ("from autonomous_mixing.application.decision import Decision", GAIN_MIX, "gain_mix"),
    ("from autonomous_mixing.application.shared import PortResult", GAIN_MIX, "gain_mix"),
    ("from autonomous_mixing.application.ports import AudioProcessor", GAIN_MIX, "gain_mix"),
    ("import autonomous_mixing.application as application", GAIN_MIX, "gain_mix"),
    ("from autonomous_mixing.domain.project.artifacts import AudioArtifact", GAIN_MIX, "gain_mix"),
    ("from autonomous_mixing.domain.decision import Decision", GAIN_MIX, "gain_mix"),
    ("from autonomous_mixing.domain import RenderSpec", GAIN_MIX, "gain_mix"),
    ("from autonomous_mixing.audio.io import load_stem", GAIN_MIX, "gain_mix"),
    ("from autonomous_mixing import cli", GAIN_MIX, "gain_mix"),
    ("from ......... import x", GAIN_MIX, "unresolvable"),
    # nothing else imports processing
    ("from ..processing import GainMixProcessor", WRITING, "writing"),
    ("from ..processing import GainMixProcessor", ALLOCATION, "allocation"),
    ("from ..processing import GainMixProcessor", VERIFICATION, "verification"),
    ("from ..processing import GainMixProcessor", AUDIO, "audio facade"),
    ("from . import processing", INFRASTRUCTURE, "descriptive"),
    ("from autonomous_mixing.infrastructure.processing import GainMixProcessor", EXE_PORTS, "layer"),
    ("import math", f"{PROCESSING}.eq", "unclassified infrastructure"),
    ("import math", f"{PROCESSING}.gain_mix_extra", "unclassified infrastructure"),
    ("import math", f"{INFRASTRUCTURE}.processing_extra", "unclassified infrastructure"),
]

PERMITTED = [
    # standard library and public domain component interfaces
    ("import re", f"{APPLICATION}.execution.identity"),
    ("from dataclasses import dataclass", OBS_CONTRACTS),
    ("from typing import Protocol", EXE_PORTS),
    ("from autonomous_mixing.domain.project import ProjectState", OBS_CONTRACTS),
    ("from autonomous_mixing.domain import project", DEC_CONTRACTS),
    ("import autonomous_mixing.domain.shared as shared_ids", SHARED_RESULTS),
    # every allowed cross-component edge, through the destination interface
    ("from autonomous_mixing.application.shared import PortResult", f"{APPLICATION}.observation.ports"),
    ("from ..shared import PortResult", f"{APPLICATION}.observation.ports"),
    ("from autonomous_mixing.application import shared", f"{APPLICATION}.decision.ports"),
    ("import autonomous_mixing.application.shared as shared", f"{APPLICATION}.decision.ports"),
    ("from autonomous_mixing.application.decision import DecisionRequest", ADM_CONTRACTS),
    ("from ..decision import DecisionResponse", ADM_IMPL),
    ("import autonomous_mixing.application.decision as decision", ADM_IMPL),
    ("from autonomous_mixing.application.decision import DecisionRequest", EXE_REQUESTS),
    ("from autonomous_mixing.application.admission import ActionAdmissionPolicy", EXE_REQUESTS),
    ("from ..admission import admit_action", f"{APPLICATION}.execution.preflight"),
    ("from autonomous_mixing.application import admission", f"{APPLICATION}.execution.preflight"),
    # sibling modules within a component, including from the component's own initializer
    ("from autonomous_mixing.application.execution.identity import CandidateIdentity", EXE_PROCESSING),
    ("from .identity import CandidateIdentity", EXE_PROCESSING),
    ("from . import identity", EXE_PROCESSING),
    ("from autonomous_mixing.application.execution import identity", EXE_PROCESSING),
    ("from .contracts import DecisionRequest", f"{APPLICATION}.decision"),
    ("from autonomous_mixing.application.admission.admission import admit_action", f"{APPLICATION}.admission"),
    # facade exceptions: re-exports from component interfaces
    ("from autonomous_mixing.application.execution import preflight_execution", APPLICATION),
    ("from .shared import Success", APPLICATION),
    ("from autonomous_mixing.application.observation import AnalysisRequest", f"{APPLICATION}.contracts"),
    ("from autonomous_mixing.application.decision import DecisionRequest", f"{APPLICATION}.contracts"),
    ("from autonomous_mixing.application import shared", f"{APPLICATION}.results"),
    ("from autonomous_mixing.application.execution import ProcessingPlan", f"{APPLICATION}.execution_contracts"),
    ("from .execution import preflight_execution", f"{APPLICATION}.execution_preflight"),
    ("from autonomous_mixing.application.execution import AudioProcessor", f"{APPLICATION}.ports.processing"),
    ("from ...application.decision import DecisionController", f"{APPLICATION}.ports.decision"),
    ("from autonomous_mixing.application.ports.analysis import AudioAnalyzer", f"{APPLICATION}.ports"),
    ("from .analysis import AudioAnalyzer", f"{APPLICATION}.ports"),
    # infrastructure
    ("from .dsp import apply_absolute_gain, sum_stems", AUDIO),
    ("from .verification import verify_wav_artifact", AUDIO),
    ("from autonomous_mixing.infrastructure.audio.dsp import sum_stems", AUDIO),
    ("import math", DSP),
    ("from typing import Any", DSP),
    ("import numpy as np", DSP),
    ("from numpy.typing import NDArray", DSP),
    ("import hashlib", VERIFICATION),
    ("import numpy as np", VERIFICATION),
    ("import soundfile as sf", VERIFICATION),
    ("from autonomous_mixing.domain.project import AudioArtifact", VERIFICATION),
    ("from autonomous_mixing.domain import project", VERIFICATION),
    ("from ...domain.project import AudioArtifact", VERIFICATION),
    ("from autonomous_mixing.infrastructure.filesystem import resolve_artifact_location", VERIFICATION),
    ("from ..filesystem import LocationResolutionError, ResolvedArtifactLocation", VERIFICATION),
    ("from autonomous_mixing.infrastructure import filesystem", VERIFICATION),
    ("import autonomous_mixing.infrastructure.filesystem as filesystem", VERIFICATION),
    ("from .verification import verify_resolved_wav_artifact", AUDIO),
    ("from .locations import resolve_artifact_location", FILESYSTEM),
    ("from . import locations", FILESYSTEM),
    ("from autonomous_mixing.infrastructure.filesystem.locations import ResolvedArtifactLocation", FILESYSTEM),
    ("import errno", LOCATIONS),
    ("import os", LOCATIONS),
    ("from pathlib import Path", LOCATIONS),
    ("from dataclasses import dataclass", LOCATIONS),
    ("from autonomous_mixing.domain.project import AudioArtifact", LOCATIONS),
    ("from autonomous_mixing.domain import project", LOCATIONS),
    ("from ...domain.project import AudioArtifact", LOCATIONS),
    # workspaces: re-export-only initializer and the allocation leaf's approved interfaces
    ("from .allocation import allocate_workspace, WorkspaceRoot", WORKSPACES),
    ("from . import allocation", WORKSPACES),
    ("from autonomous_mixing.infrastructure.workspaces.allocation import AllocatedWorkspace", WORKSPACES),
    ("import errno", ALLOCATION),
    ("import os", ALLOCATION),
    ("import re", ALLOCATION),
    ("import stat", ALLOCATION),
    ("from pathlib import Path, PureWindowsPath", ALLOCATION),
    ("from dataclasses import dataclass", ALLOCATION),
    ("from enum import Enum", ALLOCATION),
    ("from autonomous_mixing.application.execution import ExecutionWorkspace", ALLOCATION),
    ("from autonomous_mixing.application import execution", ALLOCATION),
    ("import autonomous_mixing.application.execution as execution", ALLOCATION),
    ("from ...application.execution import ExecutionWorkspace", ALLOCATION),
    ("from ...application import execution", ALLOCATION),
    ("from autonomous_mixing.domain.project import AudioArtifact, ProjectState", ALLOCATION),
    ("from autonomous_mixing.domain import project", ALLOCATION),
    ("from ...domain.project import ProjectState", ALLOCATION),
    ("from autonomous_mixing.infrastructure.filesystem import resolve_artifact_location", ALLOCATION),
    ("from ..filesystem import LocationResolutionError, ResolvedArtifactLocation", ALLOCATION),
    ("from autonomous_mixing.infrastructure import filesystem", ALLOCATION),
    ("import autonomous_mixing.infrastructure.filesystem as filesystem", ALLOCATION),
    # workspaces initializer re-exports the validation leaf too
    ("from .validation import validate_allocated_workspace", WORKSPACES),
    ("from . import validation", WORKSPACES),
    # validation: standard library, allocation sibling (records and private policy helpers)
    ("import errno", VALIDATION),
    ("import os", VALIDATION),
    ("import stat", VALIDATION),
    ("from pathlib import Path", VALIDATION),
    ("from .allocation import AllocatedWorkspace, _strict_resolve", VALIDATION),
    ("from . import allocation", VALIDATION),
    ("from autonomous_mixing.infrastructure.workspaces.allocation import _check_protected", VALIDATION),
    ("from autonomous_mixing.domain.project import ProjectState", VALIDATION),
    ("from autonomous_mixing.application.execution import ExecutionWorkspace", VALIDATION),
    ("from ..filesystem import resolve_artifact_location", VALIDATION),
    # wav_output initializer re-exports writing
    ("from .writing import WavOutputError, write_wav_output", WAV_OUTPUT),
    ("from . import writing", WAV_OUTPUT),
    ("from autonomous_mixing.infrastructure.wav_output.writing import WrittenWav", WAV_OUTPUT),
    # writing
    ("import ctypes", WRITING),
    ("from ctypes import wintypes", WRITING),
    ("import msvcrt", WRITING),
    ("import errno", WRITING),
    ("import hashlib", WRITING),
    ("from pathlib import Path, PureWindowsPath", WRITING),
    ("import numpy as np", WRITING),
    ("import soundfile as sf", WRITING),
    ("from autonomous_mixing.domain.observation import RenderSpec", WRITING),
    ("from autonomous_mixing.domain.project import OutputContainer, ProcessingSpec", WRITING),
    ("from ...domain.project import SampleEncoding", WRITING),
    ("from autonomous_mixing.domain import observation", WRITING),
    ("from autonomous_mixing.infrastructure.workspaces import AllocatedWorkspace", WRITING),
    ("from ..workspaces import WorkspaceAllocationError, validate_allocated_workspace", WRITING),
    ("from autonomous_mixing.infrastructure import workspaces", WRITING),
    ("import autonomous_mixing.infrastructure.workspaces as workspaces", WRITING),
    # processing initializer re-exports gain_mix
    ("from .gain_mix import GainMixProcessor, ProcessingLimits", PROCESSING),
    ("from . import gain_mix", PROCESSING),
    # gain_mix: the approved interfaces
    ("from dataclasses import dataclass", GAIN_MIX),
    ("from typing import Any", GAIN_MIX),
    ("import numpy as np", GAIN_MIX),
    ("from autonomous_mixing.application.execution import ProcessingPlan, ProcessedCandidate", GAIN_MIX),
    ("from ...application import execution", GAIN_MIX),
    ("from autonomous_mixing.domain.action import ExecutionIssue, ExecutionStatus", GAIN_MIX),
    ("from autonomous_mixing.domain.project import AudioArtifact", GAIN_MIX),
    ("from ...domain.shared import ArtifactId", GAIN_MIX),
    ("from autonomous_mixing.infrastructure.audio import sum_stems, verify_resolved_wav_artifact", GAIN_MIX),
    ("from ..filesystem import LocationResolutionError", GAIN_MIX),
    ("from ..workspaces import AllocatedWorkspace", GAIN_MIX),
    ("from autonomous_mixing.infrastructure.wav_output import write_wav_output", GAIN_MIX),
    ("import autonomous_mixing.infrastructure.wav_output as wav_output", GAIN_MIX),
]


@pytest.mark.parametrize("source, module, rule", FORBIDDEN)
def test_checker_rejects_forbidden_imports(source, module, rule):
    violations = _violations(source, module)
    assert violations, source
    assert any(rule in v for v in violations), violations


@pytest.mark.parametrize("source, module", PERMITTED)
def test_checker_allows_permitted_imports(source, module):
    assert _violations(source, module) == ()


def test_cycle_detection_finds_a_cycle_fixture_and_accepts_the_matrix():
    assert find_cycles({"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": set()}) != []
    assert find_cycles({name: set(deps) for name, deps in ALLOWED.items()}) == []


# --- real source tree ----------------------------------------------------------------------

EXPECTED_APPLICATION_MODULES = {
    APPLICATION,
    *(f"{APPLICATION}.{name}" for name in ("contracts", "results", "execution_contracts", "execution_preflight")),
    *(f"{APPLICATION}.ports{suffix}" for suffix in ("", ".analysis", ".decision", ".processing")),
    f"{APPLICATION}.shared",
    f"{APPLICATION}.shared.results",
    *(f"{APPLICATION}.observation{suffix}" for suffix in ("", ".contracts", ".validation", ".ports")),
    *(f"{APPLICATION}.decision{suffix}" for suffix in ("", ".contracts", ".validation", ".ports")),
    *(f"{APPLICATION}.admission{suffix}" for suffix in ("", ".policy", ".contracts", ".admission")),
    *(
        f"{APPLICATION}.execution{suffix}"
        for suffix in ("", ".identity", ".requests", ".processing", ".validation", ".preflight", ".ports")
    ),
}
EXPECTED_INFRASTRUCTURE_MODULES = {
    INFRASTRUCTURE,
    AUDIO,
    DSP,
    VERIFICATION,
    FILESYSTEM,
    LOCATIONS,
    WORKSPACES,
    ALLOCATION,
    VALIDATION,
    WAV_OUTPUT,
    WRITING,
    PROCESSING,
    GAIN_MIX,
}


def _scanned(prefix: str) -> dict[str, Path]:
    return {name: path for name, path in ALL_MODULES.items() if _within(name, prefix)}


def test_recursive_discovery_covers_every_component_facade_and_infrastructure_module():
    assert set(_scanned(APPLICATION)) == EXPECTED_APPLICATION_MODULES
    assert set(_scanned(INFRASTRUCTURE)) == EXPECTED_INFRASTRUCTURE_MODULES
    assert not (SRC / PKG / "application" / "admission.py").exists(), "no flat module may compete with the package"
    assert not [p for p in ALL_MODULES.values() if "__pycache__" in p.parts]


@pytest.mark.parametrize("prefix", [APPLICATION, INFRASTRUCTURE])
def test_real_modules_have_no_import_violations(prefix):
    problems = []
    for name, path in _scanned(prefix).items():
        if name in LEGACY_LEAF_ADAPTERS:
            continue  # ADR-027 step M3: shims, checked by the legacy-leaf shim test
        problems += find_import_violations(path.read_text(encoding="utf-8"), name, path.name == "__init__.py", KNOWN)
    assert problems == []


def _resolved_imports(name: str) -> set[str]:
    path = ALL_MODULES[name]
    package = name.split(".") if path.name == "__init__.py" else name.split(".")[:-1]
    targets = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            targets |= {t for t, _relative in _resolve_targets(node, package, KNOWN) if t is not None}
    return targets


def test_real_component_graph_follows_the_matrix_and_is_acyclic():
    edges: dict[str, set[str]] = {component: set() for component in COMPONENTS}
    for name in _scanned(APPLICATION):
        source = _app_component(name)
        if source is None:
            continue
        edges[source] |= {t for t in map(_app_component, _resolved_imports(name)) if t and t != source}
    for component, deps in edges.items():
        assert deps <= ALLOWED[component], (component, deps - ALLOWED[component])
    assert find_cycles(edges) == []
    assert "observation" not in edges["decision"] and "decision" not in edges["observation"]


def test_real_module_import_graph_is_acyclic():
    names = set(_scanned(APPLICATION)) | set(_scanned(INFRASTRUCTURE))
    edges = {name: {t for t in _resolved_imports(name) if t in names and t != name} for name in names}
    assert find_cycles(edges) == []


def test_processing_records_depend_on_identities_but_not_on_requests_or_preflight():
    # ADR-027 migration step M1: identities, plans and outcomes moved to records.rendering; requests and
    # preflight results to records.admission. The same direction holds there: rendering records never
    # depend on admission records, and admission records depend on rendering identities.
    assert f"{RECORDS}.admission" not in _resolved_imports(f"{RECORDS}.rendering")
    assert f"{RECORDS}.rendering" in _resolved_imports(f"{RECORDS}.admission")
    # ADR-027 step M2: preflight moved to core.admission.preflight and reads both records modules.
    assert {f"{RECORDS}.admission", f"{RECORDS}.rendering"} <= _resolved_imports(f"{CORE}.admission.preflight")


def test_facades_only_reexport_component_interfaces_without_definitions():
    for name in sorted(FACADES):
        tree = ast.parse(ALL_MODULES[name].read_text(encoding="utf-8"))
        imported: list[str] = []
        exported: list[str] | None = None
        for index, node in enumerate(tree.body):
            if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue  # module docstring
            if isinstance(node, ast.ImportFrom):
                imported += [alias.asname or alias.name for alias in node.names]
                continue
            is_all = (
                isinstance(node, ast.Assign)
                and [t.id for t in node.targets if isinstance(t, ast.Name)] == ["__all__"]
                and isinstance(node.value, ast.List)
            )
            assert is_all, f"{name} contains a non-re-export statement at line {node.lineno}"
            exported = [element.value for element in node.value.elts]
        assert exported is not None and sorted(exported) == sorted(imported), name
        assert len(exported) == len(set(exported)), name


def test_component_modules_never_reference_compatibility_facades():
    for name in _scanned(APPLICATION):
        if _app_component(name) is not None:
            assert not (_resolved_imports(name) & FACADES), name


def test_infrastructure_initializer_is_descriptive_and_leaves_keep_their_locations():
    tree = ast.parse(ALL_MODULES[INFRASTRUCTURE].read_text(encoding="utf-8"))
    assert len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr), "only a module docstring"
    assert _resolved_imports(AUDIO) == {DSP, VERIFICATION}
    assert ALL_MODULES[DSP] == SRC / PKG / "infrastructure" / "audio" / "dsp.py"
    assert ALL_MODULES[VERIFICATION] == SRC / PKG / "infrastructure" / "audio" / "verification.py"
    assert ALL_MODULES[FILESYSTEM] == SRC / PKG / "infrastructure" / "filesystem" / "__init__.py"
    assert ALL_MODULES[LOCATIONS] == SRC / PKG / "infrastructure" / "filesystem" / "locations.py"
    assert ALL_MODULES[WORKSPACES] == SRC / PKG / "infrastructure" / "workspaces" / "__init__.py"
    assert ALL_MODULES[ALLOCATION] == SRC / PKG / "infrastructure" / "workspaces" / "allocation.py"
    assert ALL_MODULES[VALIDATION] == SRC / PKG / "infrastructure" / "workspaces" / "validation.py"
    assert ALL_MODULES[WAV_OUTPUT] == SRC / PKG / "infrastructure" / "wav_output" / "__init__.py"
    assert ALL_MODULES[WRITING] == SRC / PKG / "infrastructure" / "wav_output" / "writing.py"
    assert ALL_MODULES[PROCESSING] == SRC / PKG / "infrastructure" / "processing" / "__init__.py"
    assert ALL_MODULES[GAIN_MIX] == SRC / PKG / "infrastructure" / "processing" / "gain_mix.py"


def _project_imports(name: str) -> set[str]:
    return {t for t in _resolved_imports(name) if _top(t) == PKG}


def test_legacy_infrastructure_leaves_are_shims_of_exactly_their_adapter_module():
    # ADR-027 step M3. Replaces the former exact-edge and runtime-load tests of the moved leaves; their
    # guarantees are enforced on the adapter modules by tests/architecture/test_adapters.py (ledger G4, G10).
    for legacy, adapter in LEGACY_LEAF_ADAPTERS.items():
        assert _project_imports(legacy) == {adapter}, legacy
        assert {_top(t) for t in _resolved_imports(legacy)} - {PKG} <= set(sys.stdlib_module_names), legacy
    for name in ALL_MODULES:
        if name not in LEGACY_LEAF_ADAPTERS and not _within(name, INFRASTRUCTURE):
            assert not [t for t in _resolved_imports(name) if _within(t, INFRASTRUCTURE)], name


def _reexports(name: str) -> tuple[list[str], list[str]]:
    """Return (imported names, ``__all__``) of a re-export-only initializer; fail on any other statement."""
    tree = ast.parse(ALL_MODULES[name].read_text(encoding="utf-8"))
    imported: list[str] = []
    exported: list[str] = []
    for index, node in enumerate(tree.body):
        if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(node, ast.ImportFrom):
            imported += [alias.asname or alias.name for alias in node.names]
            continue
        is_all = (
            isinstance(node, ast.Assign)
            and [t.id for t in node.targets if isinstance(t, ast.Name)] == ["__all__"]
            and isinstance(node.value, ast.List)
        )
        assert is_all, f"{name} contains a non-re-export statement at line {node.lineno}"
        exported = [element.value for element in node.value.elts]
    return imported, exported


FILESYSTEM_EXPORTS = {
    "ResolvedArtifactLocation",
    "LocationResolutionFailureCode",
    "LocationResolutionError",
    "resolve_artifact_location",
}
AUDIO_EXPORTS = {
    "ArtifactVerificationError",
    "VerificationFailureCode",
    "VerifiedAudio",
    "apply_absolute_gain",
    "sum_stems",
    "verify_wav_artifact",
    "verify_resolved_wav_artifact",
}
WORKSPACE_EXPORTS = {
    "WorkspaceRoot",
    "WorkspaceAllocationRequest",
    "AllocatedWorkspace",
    "WorkspaceAllocationFailureCode",
    "WorkspaceAllocationError",
    "allocate_workspace",
    "validate_allocated_workspace",
}
WAV_OUTPUT_EXPORTS = {"WrittenWav", "WavOutputFailureCode", "WavOutputError", "write_wav_output"}
PROCESSING_EXPORTS = {"GAIN_MIX_PROCESSOR", "GainMixProcessor", "ProcessingLimits"}


@pytest.mark.parametrize(
    "name, expected",
    [
        (FILESYSTEM, FILESYSTEM_EXPORTS),
        (AUDIO, AUDIO_EXPORTS),
        (WORKSPACES, WORKSPACE_EXPORTS),
        (WAV_OUTPUT, WAV_OUTPUT_EXPORTS),
        (PROCESSING, PROCESSING_EXPORTS),
    ],
)
def test_infrastructure_component_initializers_only_reexport_their_public_names(name, expected):
    imported, exported = _reexports(name)
    assert sorted(imported) == sorted(exported) and len(exported) == len(set(exported))
    assert set(exported) == expected and len(exported) == len(expected)


# --- consumers -------------------------------------------------------------------------------

COMPATIBILITY_TEST = "test_application_component_compatibility.py"


def _test_import_targets(path: Path) -> list[str]:
    targets = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            targets += [f"{module}.{a.name}" if f"{module}.{a.name}" in KNOWN else module for a in node.names]
        elif isinstance(node, ast.Import):
            targets += [alias.name for alias in node.names]
    return targets


def test_only_the_compatibility_test_imports_application_facades():
    offenders = []
    for path in sorted((SRC.parent / "tests").rglob("*.py")):
        if "__pycache__" in path.parts or path.name == COMPATIBILITY_TEST:
            continue
        offenders += [f"{path.name}: {t}" for t in _test_import_targets(path) if t in FACADES]
    assert offenders == []


def test_production_code_outside_the_application_imports_no_application_module():
    # Only workspace allocation (ExecutionWorkspace) and the processor (the AudioProcessor port's
    # records, ADR-026) use application records, through the execution interface alone; any other
    # consumer needs its own explicit, reviewed exception.
    for name in ALL_MODULES:
        if not _within(name, APPLICATION):
            imported = {t for t in _resolved_imports(name) if _within(t, APPLICATION)}
            # ADR-027 step M3: allocation and the processor now import core.records; no exception remains.
            assert imported == set(), name


# --- fresh-process imports -------------------------------------------------------------------

CANONICAL_ORDER = [f"{APPLICATION}.{component}" for component in COMPONENTS]
LEGACY_ORDER = sorted(LEGACY_FACADES)
INFRA_ORDER = [DSP, VERIFICATION, AUDIO]
# ADR-027 step M3: the leaf lookups below use the adapter modules; the legacy package interfaces must still
# export the same objects. The legacy leaves themselves are shims (see the shim test).
IDENTITY_PROBE = """
import importlib
A = "autonomous_mixing.application"
app = importlib.import_module(A)
shared, observation, decision, admission, execution = (
    importlib.import_module(f"{A}.{c}") for c in ("shared", "observation", "decision", "admission", "execution")
)
contracts, results, execution_contracts, execution_preflight, ports = (
    importlib.import_module(f"{A}.{m}")
    for m in ("contracts", "results", "execution_contracts", "execution_preflight", "ports")
)
audio = importlib.import_module("autonomous_mixing.infrastructure.audio")
dsp = importlib.import_module("autonomous_mixing.adapters.rendering.dsp")
verification = importlib.import_module("autonomous_mixing.adapters.rendering.integrity")
filesystem = importlib.import_module("autonomous_mixing.infrastructure.filesystem")
locations = importlib.import_module("autonomous_mixing.adapters.rendering.locations")
assert app.PortResult is results.PortResult is shared.PortResult
assert app.DecisionRequest is contracts.DecisionRequest is decision.DecisionRequest
assert app.AnalysisResponse is contracts.AnalysisResponse is observation.AnalysisResponse
assert app.admit_action is admission.admit_action
assert app.ProcessingOutcome is execution_contracts.ProcessingOutcome is execution.ProcessingOutcome
assert app.PreflightResult is execution_preflight.PreflightResult is execution.PreflightResult
assert ports.AudioAnalyzer is observation.AudioAnalyzer
assert ports.DecisionController is decision.DecisionController
assert ports.AudioProcessor is execution.AudioProcessor
assert audio.sum_stems is dsp.sum_stems and audio.verify_wav_artifact is verification.verify_wav_artifact
assert audio.verify_resolved_wav_artifact is verification.verify_resolved_wav_artifact and len(audio.__all__) == 7
for n in ("ResolvedArtifactLocation", "LocationResolutionFailureCode", "LocationResolutionError",
          "resolve_artifact_location"):
    assert getattr(filesystem, n) is getattr(locations, n)
assert verification.ResolvedArtifactLocation is filesystem.ResolvedArtifactLocation
assert verification.resolve_artifact_location is locations.resolve_artifact_location
assert len(filesystem.__all__) == 4
workspaces = importlib.import_module("autonomous_mixing.infrastructure.workspaces")
allocation = importlib.import_module("autonomous_mixing.adapters.rendering.workspaces")
validation = importlib.import_module("autonomous_mixing.adapters.rendering.revalidation")
assert len(workspaces.__all__) == 7
for n in workspaces.__all__:
    owner = validation if n == "validate_allocated_workspace" else allocation
    assert getattr(workspaces, n) is getattr(owner, n)
assert validation.AllocatedWorkspace is allocation.AllocatedWorkspace
assert validation._check_protected is allocation._check_protected
assert allocation.ExecutionWorkspace is execution.ExecutionWorkspace
assert allocation.resolve_artifact_location is filesystem.resolve_artifact_location
wav_output = importlib.import_module("autonomous_mixing.infrastructure.wav_output")
writing = importlib.import_module("autonomous_mixing.adapters.rendering.wav_output")
assert len(wav_output.__all__) == 4
for n in wav_output.__all__:
    assert getattr(wav_output, n) is getattr(writing, n)
assert writing.validate_allocated_workspace is workspaces.validate_allocated_workspace
assert writing.AllocatedWorkspace is workspaces.AllocatedWorkspace
assert writing.WorkspaceAllocationError is allocation.WorkspaceAllocationError
processing = importlib.import_module("autonomous_mixing.infrastructure.processing")
gain_mix = importlib.import_module("autonomous_mixing.adapters.rendering.gain_mix")
assert len(processing.__all__) == 3
for n in processing.__all__:
    assert getattr(processing, n) is getattr(gain_mix, n)
assert gain_mix.ProcessingPlan is execution.ProcessingPlan
assert gain_mix.write_wav_output is writing.write_wav_output
assert gain_mix.verify_resolved_wav_artifact is verification.verify_resolved_wav_artifact
assert gain_mix.AllocatedWorkspace is allocation.AllocatedWorkspace
print("ok")
"""


def _start(order: list[str]) -> subprocess.Popen:
    script = "import importlib\n" + "".join(f"importlib.import_module({m!r})\n" for m in order) + IDENTITY_PROBE
    # The probes run in parallel and each loads NumPy; one OpenBLAS thread per probe avoids the intermittent
    # "OpenBLAS error: Memory allocation still failed" seen when every probe starts a full thread pool.
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1"}
    return subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )


def test_fresh_process_imports_succeed_in_every_order_without_partial_initialization():
    orders = {
        "forward components": CANONICAL_ORDER,
        "reverse components": CANONICAL_ORDER[::-1],
        "shims first": LEGACY_ORDER + [APPLICATION] + CANONICAL_ORDER,
        "canonical first": CANONICAL_ORDER + [APPLICATION] + LEGACY_ORDER,
        "reverse shims first": LEGACY_ORDER[::-1] + CANONICAL_ORDER[::-1],
        "infrastructure leaves first": INFRA_ORDER + CANONICAL_ORDER,
        "infrastructure facade first": INFRA_ORDER[::-1] + LEGACY_ORDER,
        "filesystem first": [FILESYSTEM, LOCATIONS, AUDIO, VERIFICATION, DSP],
        "filesystem leaf first": [LOCATIONS, VERIFICATION, FILESYSTEM, AUDIO],
        "audio first": [AUDIO, VERIFICATION, FILESYSTEM, LOCATIONS],
        "verification first": [VERIFICATION, LOCATIONS, FILESYSTEM],
        "execution internals first": [f"{APPLICATION}.execution.{m}" for m in ("ports", "preflight", "processing")],
        "audio first, then workspaces": [AUDIO, FILESYSTEM, WORKSPACES, ALLOCATION],
        "workspace leaf first": [ALLOCATION, WORKSPACES, AUDIO, FILESYSTEM] + CANONICAL_ORDER,
        "workspace facade first": [WORKSPACES, APPLICATION, AUDIO, LOCATIONS],
        "execution then workspaces": [f"{APPLICATION}.execution", ALLOCATION, WORKSPACES],
        "validation leaf first": [VALIDATION, ALLOCATION, WORKSPACES],
        "validation before allocation facade": [VALIDATION, WORKSPACES, WRITING],
        "wav_output facade first": [WAV_OUTPUT, WORKSPACES, AUDIO, FILESYSTEM],
        "writing leaf first": [WRITING, WAV_OUTPUT, VALIDATION, ALLOCATION],
        "audio then wav_output": [AUDIO, DSP, WAV_OUTPUT, WRITING],
        "workspaces then wav_output": [WORKSPACES, ALLOCATION, WRITING, WAV_OUTPUT] + CANONICAL_ORDER,
        "execution then wav_output": [f"{APPLICATION}.execution", WRITING, WORKSPACES],
        "processing facade first": [PROCESSING, AUDIO, WAV_OUTPUT, WORKSPACES],
        "gain_mix leaf first": [GAIN_MIX, PROCESSING, WRITING, VERIFICATION],
        "infrastructure then processing": [AUDIO, FILESYSTEM, WORKSPACES, WAV_OUTPUT, GAIN_MIX] + CANONICAL_ORDER,
        "execution then processing": [f"{APPLICATION}.execution", GAIN_MIX, PROCESSING],
    }
    # Every module must also import on its own as the first import of a fresh process.
    for name in sorted(EXPECTED_APPLICATION_MODULES | EXPECTED_INFRASTRUCTURE_MODULES):
        orders[f"alone: {name}"] = [name]
    processes = {label: _start(order) for label, order in orders.items()}
    failures = {}
    for label, process in processes.items():
        out, err = process.communicate(timeout=120)
        if process.returncode != 0 or out.strip() != "ok":
            failures[label] = err.strip().splitlines()[-1:] if err else out
    assert failures == {}


def _loaded_after(statement: str) -> list[str]:
    script = (
        f"import sys\nbefore = set(sys.modules)\n{statement}\nprint(chr(10).join(sorted(set(sys.modules) - before)))\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    return [line for line in result.stdout.splitlines() if line]


def test_application_components_and_facades_load_only_stdlib_domain_and_application_at_runtime():
    modules = ", ".join(sorted(EXPECTED_APPLICATION_MODULES))
    for name in _loaded_after(f"import {modules}"):
        layer_ok = name == PKG or _within(name, DOMAIN) or _within(name, APPLICATION)
        layer_ok = layer_ok or _within(name, CORE)  # ADR-027 steps M1/M2
        assert layer_ok or _top(name) in sys.stdlib_module_names, name


def test_infrastructure_audio_loads_no_application_m0_or_cli_modules_at_runtime():
    # The facade loads both leaves (and therefore soundfile); only layer independence is asserted here.
    loaded = _loaded_after(f"import {AUDIO}")
    assert loaded, "the probe must observe newly loaded modules"
    assert not [n for n in loaded if _within(n, APPLICATION) or _within(n, f"{PKG}.audio") or n == f"{PKG}.cli"]
    assert not [n for n in loaded if _within(n, WORKSPACES) or _within(n, WAV_OUTPUT) or _within(n, PROCESSING)]


def test_infrastructure_filesystem_loads_no_audio_application_numpy_soundfile_m0_or_cli_modules_at_runtime():
    loaded = _loaded_after(f"import {FILESYSTEM}")
    assert LOCATIONS in loaded, "the probe must observe the filesystem component loading"
    forbidden = [
        n
        for n in loaded
        if _within(n, APPLICATION)
        or _within(n, AUDIO)
        or _within(n, WORKSPACES)
        or _within(n, WAV_OUTPUT)
        or _within(n, PROCESSING)
        or _within(n, f"{PKG}.audio")
        or n == f"{PKG}.cli"
        or _top(n) in {"numpy", "soundfile"}
    ]
    assert forbidden == []

    # Like the audio facade, the writer loads NumPy and soundfile (and their dependencies); only layer
    # independence is asserted. Loading application.execution runs the application package initializer,
    # exactly as importing the workspaces facade already does.
