"""Architecture tests for the adapters (ADR-027 rule c; migration step M3).

Guarantee-ledger rows covered here: G1 for adapters (rule c: adapters import only the standard library,
third-party libraries, ``core.records``, ``core.ports`` and their own package; never another adapter,
the loop or a stage), G10 (the exact internal edges and third-party dependencies of the rendering
adapter, carried over from the ADR-022 to ADR-026 infrastructure rules) and G4 for adapters (runtime
load isolation in fresh processes). ADR-028 (Proposed) adds one narrow exception to rule (c): the listed
rendering modules and ``dsp_analysis.file_analyzer`` may import the listed public modules of the shared ``adapters.audio_io`` component, and
no other module may import its private names (``test_shared_audio_access.py`` holds its own edges and probes).
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from import_resolver import module_files, resolve_imports

PKG = "autonomous_mixing"
ADAPTERS = f"{PKG}.adapters"
RENDERING = f"{ADAPTERS}.rendering"
ANALYSIS = f"{ADAPTERS}.dsp_analysis"
JOURNAL = f"{ADAPTERS}.journal"
AUDIO_IO = f"{ADAPTERS}.audio_io"
RECORDS = f"{PKG}.core.records"
PORTS = f"{PKG}.core.ports"
SRC = Path(__file__).resolve().parents[2] / "src"
MODULES = module_files(SRC, SRC / PKG / "adapters")
KNOWN = frozenset(module_files(SRC, SRC / PKG))


def _adapter(name: str) -> str | None:
    parts = name.split(".")
    return ".".join(parts[:3]) if len(parts) > 2 and parts[:2] == [PKG, "adapters"] else None


# ADR-028 (Proposed): the only permitted cross-adapter edges, client module -> public shared modules.
SHARED_CLIENTS = {
    f"{ADAPTERS}.rendering.workspaces": {f"{AUDIO_IO}.locations"},
    f"{ADAPTERS}.rendering.gain_mix": {f"{AUDIO_IO}.locations", f"{AUDIO_IO}.integrity"},
    f"{ADAPTERS}.rendering.state_integrity": {f"{AUDIO_IO}.locations", f"{AUDIO_IO}.integrity", f"{AUDIO_IO}.limits"},
    f"{ADAPTERS}.rendering.run_renderer": {f"{AUDIO_IO}.locations"},
    # File-backed level analyzer (file-audio-analyzer design, 2026-11-11): the edge ADR-028 reserved for it.
    f"{ADAPTERS}.dsp_analysis.file_analyzer": {f"{AUDIO_IO}.locations", f"{AUDIO_IO}.integrity", f"{AUDIO_IO}.limits"},
}


def _private_shared_names(source: str, module: str, is_package: bool = False) -> list[str]:
    """Private names another component imports from the shared audio component.

    Relative bases are resolved exactly as ``resolve_imports`` does, so ``from ..audio_io.limits import _x``
    is checked like its absolute form; an escaping relative import is reported by ``adapter_violations``.
    """
    if _adapter(module) == AUDIO_IO:
        return []
    package = module.split(".") if is_package else module.split(".")[:-1]
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            keep = len(package) - (node.level - 1)
            if keep < 1:
                continue
            base = ".".join(package[:keep] + ([node.module] if node.module else []))
        elif node.module:
            base = node.module
        else:
            continue
        if base == AUDIO_IO or base.startswith(AUDIO_IO + "."):
            found += [f"{base}.{a.name}" for a in node.names if a.name.startswith("_")]
    return found


def adapter_violations(source: str, module: str, is_package: bool = False, known: frozenset[str] = KNOWN) -> list[str]:
    """Rule (c) violations of one adapter module's source, with the exact ADR-028 shared-client edges."""
    own = _adapter(module)
    allowed_shared = SHARED_CLIENTS.get(module, set())
    violations = [f"private shared name: {name}" for name in _private_shared_names(source, module, is_package)]
    for target in resolve_imports(source, module, is_package, known):
        if target is None:
            violations.append("relative import escapes the package")
        elif target.split(".")[0] != PKG:
            continue  # standard library or third-party
        elif target.startswith(RECORDS + ".") or target.startswith(PORTS + "."):
            continue
        elif own is not None and (target == own or target.startswith(own + ".")):
            continue
        elif target in allowed_shared:
            continue
        else:
            violations.append(f"rule (c): {target}")
    return violations


def test_adapter_modules_follow_rule_c():
    assert MODULES, "the probe must see the adapter modules"
    for name, path in MODULES.items():
        assert adapter_violations(path.read_text(encoding="utf-8"), name, path.name == "__init__.py") == [], name


GAIN_MIX = f"{RENDERING}.gain_mix"


@pytest.mark.parametrize(
    "source",
    [
        "from autonomous_mixing.core import loop",
        "from autonomous_mixing.core.loop import transitions",
        "from autonomous_mixing.core.admission.admission import admit_action",
        "from autonomous_mixing.core import records",
        "from autonomous_mixing.adapters.dsp_analysis import io",
        "from autonomous_mixing.adapters import dsp_analysis",
        "from ..dsp_analysis import features",
        "from autonomous_mixing.domain.project import AudioArtifact",
        "from autonomous_mixing.application.execution import RenderPlan",
        "from autonomous_mixing.infrastructure.audio import dsp",
        "from autonomous_mixing import cli",
        "from autonomous_mixing.drivers import cli",
        "from ..... import anything",
    ],
)
def test_checker_rejects_forbidden_adapter_imports(source):
    assert adapter_violations(source, GAIN_MIX), source


@pytest.mark.parametrize(
    "source",
    [
        "import numpy as np",
        "from autonomous_mixing.core.records import rendering",
        "from autonomous_mixing.core.records.rendering import RenderPlan",
        "from autonomous_mixing.core.ports.rendering import Renderer",
        "from . import dsp",
        "from .wav_output import write_wav_output",
        "from autonomous_mixing.adapters.rendering.workspaces import AllocatedWorkspace",
    ],
)
def test_checker_allows_permitted_adapter_imports(source):
    assert adapter_violations(source, GAIN_MIX) == [], source


def _edges(name: str) -> tuple[set[str], set[str]]:
    """(project modules, third-party top-level packages) imported by one module."""
    targets = {t for t in resolve_imports(MODULES[name].read_text(encoding="utf-8"), name, False, KNOWN) if t}
    project = {t for t in targets if t.split(".")[0] == PKG}
    third_party = {t.split(".")[0] for t in targets if t.split(".")[0] not in sys.stdlib_module_names} - {PKG}
    return project, third_party


def _r(*names: str) -> set[str]:
    return {f"{RECORDS}.{n}" for n in names}


def _a(*names: str) -> set[str]:
    return {f"{RENDERING}.{n}" for n in names}


def _s(*names: str) -> set[str]:
    return {f"{AUDIO_IO}.{n}" for n in names}


# G10: exact edges inside the rendering adapter (module -> (project imports, third-party packages)).
RENDERING_EDGES = {
    "dsp": (set(), {"numpy"}),
    # locations and integrity moved to adapters.audio_io (ADR-028); their edges are AUDIO_IO_EDGES.
    "workspaces": (_s("locations") | _r("artifacts", "project", "rendering"), set()),
    "revalidation": (_a("workspaces"), set()),
    "wav_output": (_a("workspaces", "revalidation") | _r("mix", "observation"), {"numpy", "soundfile"}),
    "gain_mix": (
        _a("dsp", "wav_output", "workspaces")
        | _s("integrity", "locations")
        | _r("actions", "artifacts", "identifiers", "rendering"),
        {"numpy"},
    ),
    # File-backed state inspector (file-state-inspector plan, 2026-11-09).
    "state_integrity": (_s("integrity", "limits", "locations") | _r("artifacts", "integrity", "invariants"), set()),
    # File-backed RunRenderer (file-run-renderer plan, 2026-11-10): sibling preparation and processing only.
    "run_renderer": (
        _a("gain_mix", "workspaces") | _s("locations") | _r("actions", "coordinator_runtime", "invariants"),
        set(),
    ),
}
ANALYSIS_EDGES = {
    "io": (set(), {"numpy", "soundfile"}),
    "features": (set(), {"numpy", "scipy"}),
    "analysis": ({f"{ANALYSIS}.features", f"{ANALYSIS}.io"}, set()),
    # Versioned level kernels and the file-backed AudioAnalyzer (file-audio-analyzer design, 2026-11-11).
    "levels": (_r("observation"), {"numpy"}),
    # Directional masking-margin kernel (directional-masking-margin plan, Task 1, 2026-11-11): stdlib and NumPy only.
    "masking": (set(), {"numpy"}),
    # dist10 long-block sample framing (front-end contract revision 2, Task 3A, 2026-11-11): stdlib and NumPy only.
    "framing": (set(), {"numpy"}),
    # dist10 long-window spectral kernel (front-end contract revision 3, Task 3C, 2026-11-12): stdlib and NumPy only.
    "long_spectrum": (set(), {"numpy"}),
    # dist10 short-window spectral kernel (front-end contract §3.2, Task 3E, 2026-11-12): stdlib and NumPy only.
    "short_spectrum": (set(), {"numpy"}),
    # dist10 spectral unpredictability cw (front-end contract §3.2, Task 3F, 2026-11-12): stdlib and NumPy only.
    "unpredictability": (set(), {"numpy"}),
    # dist10 long partition accumulator eb/cb (front-end contract §3.3, Task 3H, 2026-11-12): stdlib and NumPy only.
    "partitions": (set(), {"numpy"}),
    "file_analyzer": (
        {f"{ANALYSIS}.levels", f"{PORTS}.analysis", f"{PORTS}.coordinator_runtime"}
        | _s("integrity", "limits", "locations")
        | _r(
            "analysis",
            "artifacts",
            "coordinator_runtime",
            "identifiers",
            "invariants",
            "observation",
            "results",
            "verification_evidence",
        ),
        {"numpy"},
    ),  # fmt: skip
}
# Shared read-only audio component (ADR-028, Proposed): carried over from the former rendering modules.
AUDIO_IO_EDGES = {
    "locations": (_r("artifacts"), set()),
    "limits": (set(), set()),
    "integrity": (_s("locations", "limits") | _r("artifacts"), {"numpy", "soundfile"}),
}
# In-memory acceptance journal (memory-acceptance-journal plan, 2026-11-09): records and standard library only.
JOURNAL_EDGES = {
    "memory": (_r("acceptance", "identifiers", "invariants"), set()),
    "run_memory": (
        {f"{JOURNAL}.memory"} | _r("acceptance", "identifiers", "invariants", "results", "run_trace"),
        set(),
    ),
}


def test_rendering_and_analysis_modules_are_exactly_the_planned_ones():
    assert {n for n in MODULES if n.startswith(RENDERING + ".")} == {f"{RENDERING}.{m}" for m in RENDERING_EDGES}
    assert {n for n in MODULES if n.startswith(ANALYSIS + ".")} == {f"{ANALYSIS}.{m}" for m in ANALYSIS_EDGES}
    assert {n for n in MODULES if n.startswith(JOURNAL + ".")} == {f"{JOURNAL}.{m}" for m in JOURNAL_EDGES}
    assert {n for n in MODULES if n.startswith(AUDIO_IO + ".")} == {f"{AUDIO_IO}.{m}" for m in AUDIO_IO_EDGES}
    adapter_packages = {n for n in MODULES if n.count(".") == 2 and n.startswith(ADAPTERS + ".")}
    assert adapter_packages == {RENDERING, ANALYSIS, JOURNAL, AUDIO_IO}


@pytest.mark.parametrize("module", sorted(RENDERING_EDGES))
def test_rendering_edges_are_exactly_the_permitted_ones(module):
    assert _edges(f"{RENDERING}.{module}") == RENDERING_EDGES[module]


@pytest.mark.parametrize("module", sorted(ANALYSIS_EDGES))
def test_dsp_analysis_edges_are_exactly_the_permitted_ones(module):
    assert _edges(f"{ANALYSIS}.{module}") == ANALYSIS_EDGES[module]


@pytest.mark.parametrize("module", sorted(JOURNAL_EDGES))
def test_journal_edges_are_exactly_the_permitted_ones(module):
    assert _edges(f"{JOURNAL}.{module}") == JOURNAL_EDGES[module]


def test_adapter_package_initializers_contain_only_a_docstring():
    # Exact AST shape (Codex review of M3): one string expression and nothing else, so an import-free
    # assignment or call in an initializer fails too.
    for package in (ADAPTERS, RENDERING, ANALYSIS, JOURNAL, AUDIO_IO):
        body = ast.parse(MODULES[package].read_text(encoding="utf-8")).body
        assert len(body) == 1, package
        assert isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant), package
        assert isinstance(body[0].value.value, str), package


def _loaded_after(statement: str) -> list[str]:
    script = (
        f"import sys\nbefore = set(sys.modules)\n{statement}\nprint(chr(10).join(sorted(set(sys.modules) - before)))\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    return result.stdout.split()


def _forbidden_layers(loaded: list[str]) -> list[str]:
    legacy = (f"{PKG}.domain", f"{PKG}.application", f"{PKG}.infrastructure", f"{PKG}.audio", f"{PKG}.cli")
    other = (f"{PKG}.drivers", f"{PKG}.core.loop", f"{PKG}.core.admission", f"{PKG}.core.verification")
    return [n for n in loaded if any(n == p or n.startswith(p + ".") for p in legacy + other) or n == "anthropic"]


def test_rendering_loads_no_legacy_layer_other_adapter_driver_stage_or_provider():  # G4
    loaded = _loaded_after("".join(f"import {RENDERING}.{m}\n" for m in RENDERING_EDGES))
    assert f"{GAIN_MIX}" in loaded, "the probe must observe the rendering modules loading"
    assert _forbidden_layers(loaded) == []
    assert not [n for n in loaded if n.startswith(ANALYSIS)]


# The former rendering.locations case moved with its module (test_shared_audio_access.py checks audio_io.locations too).
@pytest.mark.parametrize("module", ["workspaces", "revalidation"])
def test_filesystem_parts_of_rendering_load_no_numpy_or_soundfile(module):  # G4, carried over from ADR-023/024
    loaded = _loaded_after(f"import {RENDERING}.{module}")
    assert f"{RENDERING}.{module}" in loaded
    assert not [n for n in loaded if n.split(".")[0] in {"numpy", "soundfile", "scipy"}], module


def test_dsp_analysis_loads_no_legacy_layer_rendering_driver_or_provider():  # G4
    loaded = _loaded_after(f"import {ANALYSIS}.analysis")
    assert f"{ANALYSIS}.analysis" in loaded
    assert _forbidden_layers(loaded) == []
    assert not [n for n in loaded if n.startswith(RENDERING)]


@pytest.mark.parametrize(
    "module",
    [
        "levels",
        "file_analyzer",
        "masking",
        "framing",
        "long_spectrum",
        "short_spectrum",
        "unpredictability",
        "partitions",
    ],
)
def test_level_analysis_loads_no_m0_rendering_journal_driver_stage_or_provider(module):  # G4
    loaded = _loaded_after(f"import {ANALYSIS}.{module}")
    assert f"{ANALYSIS}.{module}" in loaded
    assert _forbidden_layers(loaded) == []
    assert not [n for n in loaded if n.startswith(RENDERING) or n.startswith(JOURNAL)]
    assert not [n for n in loaded if n in {f"{ANALYSIS}.io", f"{ANALYSIS}.features", f"{ANALYSIS}.analysis"}]
    assert "scipy" not in {n.split(".")[0] for n in loaded}


@pytest.mark.parametrize(
    "module", ["masking", "framing", "long_spectrum", "short_spectrum", "unpredictability", "partitions"]
)
def test_masking_kernel_and_framing_load_no_other_project_module_soundfile_or_scipy(module):  # G4
    loaded = _loaded_after(f"import {ANALYSIS}.{module}")
    assert f"{ANALYSIS}.{module}" in loaded
    project = {n for n in loaded if n.split(".")[0] == PKG}
    assert project == {PKG, ADAPTERS, ANALYSIS, f"{ANALYSIS}.{module}"}  # package initializers and itself only
    assert not {"soundfile", "scipy"} & {n.split(".")[0] for n in loaded}


def test_memory_journal_loads_no_legacy_layer_other_adapter_driver_stage_provider_or_numpy():  # G4
    loaded = _loaded_after(f"import {JOURNAL}.memory")
    assert f"{JOURNAL}.memory" in loaded
    assert _forbidden_layers(loaded) == []
    assert not [n for n in loaded if n.startswith(RENDERING) or n.startswith(ANALYSIS)]
    assert not [n for n in loaded if n.split(".")[0] in {"numpy", "soundfile", "scipy"}]


def test_run_memory_journal_loads_no_other_adapter_driver_stage_provider_or_numpy():
    loaded = _loaded_after(f"import {JOURNAL}.run_memory")
    assert f"{JOURNAL}.run_memory" in loaded
    assert _forbidden_layers(loaded) == []
    assert not [n for n in loaded if n.startswith(RENDERING) or n.startswith(ANALYSIS)]
    assert not [n for n in loaded if n.split(".")[0] in {"numpy", "soundfile", "scipy"}]


def test_state_inspector_loads_no_other_adapter_driver_stage_or_provider():  # G4
    loaded = _loaded_after(f"import {RENDERING}.state_integrity")
    assert f"{RENDERING}.state_integrity" in loaded
    assert _forbidden_layers(loaded) == []
    assert not [n for n in loaded if n.startswith(ANALYSIS) or n.startswith(JOURNAL)]
    assert not [n for n in loaded if n.startswith(f"{RENDERING}.gain_mix") or n.startswith(f"{RENDERING}.wav_output")]


RUN_RENDERER = f"{RENDERING}.run_renderer"


@pytest.mark.parametrize(
    "source",
    [
        "from autonomous_mixing.core.loop.coordinator import run",
        "from autonomous_mixing.core import loop",
        "from autonomous_mixing.core.loop import coordinator",
        "from autonomous_mixing.core.admission.preflight import preflight_execution",
        "from autonomous_mixing.core import verification",
        "from autonomous_mixing.core.verification import assessment",
        "from autonomous_mixing.drivers import cli",
        "from autonomous_mixing import evaluation",
        "from autonomous_mixing.adapters.journal.memory import InMemoryAcceptanceJournal",
        "from autonomous_mixing.adapters import dsp_analysis",
    ],
)
def test_checker_rejects_forbidden_run_renderer_imports(source):
    assert adapter_violations(source, RUN_RENDERER), source


def test_run_renderer_loads_no_coordinator_stage_driver_other_adapter_or_provider():  # G4
    loaded = _loaded_after(f"import {RUN_RENDERER}")
    assert RUN_RENDERER in loaded
    assert _forbidden_layers(loaded) == []
    assert not [n for n in loaded if n.startswith(ANALYSIS) or n.startswith(JOURNAL)]
    assert f"{RENDERING}.state_integrity" not in loaded
