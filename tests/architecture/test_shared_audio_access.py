"""Architecture tests for the shared read-only audio component (ADR-028, Proposed; ADR-027 rule c otherwise).

``adapters.audio_io`` owns location resolution, WAV integrity reading and read limits. Only the listed
rendering modules and ``dsp_analysis.file_analyzer`` may import its listed public modules; every other cross-adapter import stays forbidden,
another component may not import its private names, and the shared modules import no consumer, stage,
loop, driver or evaluation code. Guarantees carried over from the former rendering-owned modules: exact
internal edges and third-party dependencies (G10), one definition per moved name (G9) and fresh-process
load isolation (G4).
"""

import ast
from pathlib import Path

import pytest
from import_resolver import module_files
from test_adapters import (
    ADAPTERS,
    AUDIO_IO,
    AUDIO_IO_EDGES,
    MODULES,
    PKG,
    RENDERING,
    _edges,
    _forbidden_layers,
    _loaded_after,
    adapter_violations,
)

SRC = Path(__file__).resolve().parents[2] / "src"
INTEGRITY = f"{AUDIO_IO}.integrity"
LOCATIONS = f"{AUDIO_IO}.locations"
LIMITS = f"{AUDIO_IO}.limits"
FILE_ANALYZER = f"{ADAPTERS}.dsp_analysis.file_analyzer"


def test_audio_io_modules_are_exactly_the_planned_ones():
    assert {n for n in MODULES if n == AUDIO_IO or n.startswith(AUDIO_IO + ".")} == {
        AUDIO_IO,
        *(f"{AUDIO_IO}.{m}" for m in AUDIO_IO_EDGES),
    }


@pytest.mark.parametrize("module", sorted(AUDIO_IO_EDGES))
def test_audio_io_edges_are_exactly_the_permitted_ones(module):
    assert _edges(f"{AUDIO_IO}.{module}") == AUDIO_IO_EDGES[module]


def test_the_old_rendering_paths_are_gone():
    rendering = SRC / PKG / "adapters" / "rendering"
    assert not (rendering / "locations.py").exists() and not (rendering / "integrity.py").exists()
    assert f"{RENDERING}.locations" not in MODULES and f"{RENDERING}.integrity" not in MODULES


MOVED = {
    LOCATIONS: (
        "ResolvedArtifactLocation",
        "LocationResolutionFailureCode",
        "LocationResolutionError",
        "resolve_artifact_location",
    ),
    INTEGRITY: (
        "CheckedAudio",
        "IntegrityFailureCode",
        "IntegrityError",
        "check_wav_integrity",
        "check_resolved_wav_integrity",
    ),
    LIMITS: ("AudioReadLimits", "_require_limit"),
}


def test_every_moved_definition_has_exactly_one_owner():
    definitions: dict[str, list[str]] = {}
    for name, path in module_files(SRC, SRC / PKG).items():
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.ClassDef | ast.FunctionDef):
                definitions.setdefault(node.name, []).append(name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        definitions.setdefault(target.id, []).append(name)
    for owner, names in MOVED.items():
        for name in names:
            if name == "_require_limit":  # rendering.gain_mix keeps its own ProcessingLimits helper (unchanged)
                assert owner in definitions[name] and f"{RENDERING}.gain_mix" in definitions[name], definitions[name]
                assert len(definitions[name]) == 2
                continue
            assert definitions.get(name) == [owner], (name, definitions.get(name))
    assert definitions.get("WAV_CONTAINERS") == [INTEGRITY]


def test_the_audio_io_initializer_contains_only_a_docstring():
    body = ast.parse(MODULES[AUDIO_IO].read_text(encoding="utf-8")).body
    assert len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value.value, str)


# --- the exact client policy -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "client, source",
    [
        (f"{RENDERING}.workspaces", "from autonomous_mixing.adapters.audio_io.locations import resolve_artifact_location"),
        (f"{RENDERING}.workspaces", "from autonomous_mixing.adapters.audio_io import locations"),
        (f"{RENDERING}.gain_mix", "from autonomous_mixing.adapters.audio_io.integrity import CheckedAudio"),
        (f"{RENDERING}.gain_mix", "import autonomous_mixing.adapters.audio_io.locations"),
        (f"{RENDERING}.state_integrity", "from autonomous_mixing.adapters.audio_io.limits import AudioReadLimits"),
        (f"{RENDERING}.state_integrity", "from autonomous_mixing.adapters.audio_io.integrity import IntegrityError"),
        (f"{RENDERING}.run_renderer", "from autonomous_mixing.adapters.audio_io.locations import LocationResolutionError"),
        (INTEGRITY, "from autonomous_mixing.adapters.audio_io.limits import _require_limit"),  # own component
        (INTEGRITY, "from .locations import resolve_artifact_location"),
        (INTEGRITY, "from .limits import _require_limit"),  # relative, own component
        (f"{RENDERING}.state_integrity", "from ..audio_io.limits import AudioReadLimits"),  # relative public name
        (f"{RENDERING}.workspaces", "from ..audio_io import locations"),  # relative package form, listed child
        (LOCATIONS, "from autonomous_mixing.core.records.artifacts import AudioArtifact"),
        (FILE_ANALYZER, "from autonomous_mixing.adapters.audio_io.integrity import check_resolved_wav_integrity"),
        (FILE_ANALYZER, "from autonomous_mixing.adapters.audio_io.locations import resolve_artifact_location"),
        (FILE_ANALYZER, "from ..audio_io.limits import AudioReadLimits"),
    ],
)  # fmt: skip
def test_listed_public_clients_are_allowed(client, source):
    assert adapter_violations(source, client) == [], source


@pytest.mark.parametrize(
    "client, source",
    [
        # The shared component imports no consumer, other adapter, loop, stage, driver or evaluation code.
        (INTEGRITY, "from autonomous_mixing.adapters.rendering.gain_mix import GainMixProcessor"),
        (INTEGRITY, "from autonomous_mixing.adapters.rendering import workspaces"),
        (LOCATIONS, "from autonomous_mixing.adapters.dsp_analysis import io"),
        (LOCATIONS, "from autonomous_mixing.adapters.journal.memory import InMemoryAcceptanceJournal"),
        (INTEGRITY, "from autonomous_mixing.core.loop import coordinator"),
        (INTEGRITY, "from autonomous_mixing.core.admission.preflight import preflight_execution"),
        (INTEGRITY, "from autonomous_mixing.core import verification"),
        (LIMITS, "from autonomous_mixing.drivers import cli"),
        (LIMITS, "from autonomous_mixing import evaluation"),
        # Other adapters may not use it.
        (f"{ADAPTERS}.journal.memory", "from autonomous_mixing.adapters.audio_io.locations import resolve_artifact_location"),
        (f"{ADAPTERS}.dsp_analysis.analysis", "from autonomous_mixing.adapters.audio_io.integrity import CheckedAudio"),
        (f"{ADAPTERS}.dsp_analysis.levels", "from autonomous_mixing.adapters.audio_io.integrity import CheckedAudio"),
        (f"{ADAPTERS}.dsp_analysis.io", "from autonomous_mixing.adapters.audio_io.locations import resolve_artifact_location"),
        # The analyzer may use the public names only, and no rendering module.
        (FILE_ANALYZER, "from autonomous_mixing.adapters.audio_io.limits import _require_limit"),
        (FILE_ANALYZER, "from ..audio_io.integrity import _capture"),
        (FILE_ANALYZER, "import autonomous_mixing.adapters.audio_io"),  # the package itself is not a listed module
        (FILE_ANALYZER, "from autonomous_mixing.adapters.rendering.state_integrity import FileStateIntegrityInspector"),
        # Unlisted rendering modules and unlisted shared modules stay forbidden.
        (f"{RENDERING}.wav_output", "from autonomous_mixing.adapters.audio_io.locations import resolve_artifact_location"),
        (f"{RENDERING}.revalidation", "from autonomous_mixing.adapters.audio_io.integrity import IntegrityError"),
        (f"{RENDERING}.gain_mix", "from autonomous_mixing.adapters.audio_io.limits import AudioReadLimits"),
        (f"{RENDERING}.workspaces", "from autonomous_mixing.adapters.audio_io.integrity import CheckedAudio"),
        # Private names, package forms, unknown children and look-alike names cannot bypass the policy.
        (f"{RENDERING}.state_integrity", "from autonomous_mixing.adapters.audio_io.limits import _require_limit"),
        # Relative imports resolve to the same base before the private-name check (Codex review, 2026-11-10).
        (f"{RENDERING}.state_integrity", "from ..audio_io.limits import _require_limit"),
        (f"{RENDERING}.gain_mix", "from ..audio_io.integrity import _capture"),
        (f"{RENDERING}.run_renderer", "from ...adapters.audio_io.locations import _strict_resolve"),
        (f"{RENDERING}.gain_mix", "from autonomous_mixing.adapters.audio_io.integrity import _capture"),
        (f"{RENDERING}.workspaces", "from autonomous_mixing.adapters import audio_io"),
        (f"{RENDERING}.workspaces", "import autonomous_mixing.adapters.audio_io"),
        (f"{RENDERING}.workspaces", "from autonomous_mixing.adapters.audio_io import missing_child"),
        (f"{RENDERING}.workspaces", "from autonomous_mixing.adapters.audio_io_extra.locations import x"),
    ],
)  # fmt: skip
def test_forbidden_shared_imports_are_rejected(client, source):
    assert adapter_violations(source, client), source


# --- fresh-process load isolation (G4) ---------------------------------------------------------------


@pytest.mark.parametrize("module", [LOCATIONS, LIMITS, f"{RENDERING}.workspaces", f"{RENDERING}.revalidation"])
def test_location_resolution_and_allocation_load_no_numpy_or_soundfile(module):
    loaded = _loaded_after(f"import {module}")
    assert module in loaded
    assert not [n for n in loaded if n.split(".")[0] in {"numpy", "soundfile", "scipy"}], module


def test_shared_integrity_loads_no_consumer_coordinator_stage_journal_driver_or_provider():
    loaded = _loaded_after(f"import {INTEGRITY}")
    assert INTEGRITY in loaded and "soundfile" in loaded
    assert _forbidden_layers(loaded) == []
    others = (RENDERING, f"{ADAPTERS}.dsp_analysis", f"{ADAPTERS}.journal")
    assert not [n for n in loaded if any(n == o or n.startswith(o + ".") for o in others)]


def test_the_package_initializer_loads_no_decoding():
    loaded = _loaded_after(f"import {AUDIO_IO}")
    assert AUDIO_IO in loaded and INTEGRITY not in loaded
    assert not [n for n in loaded if n.split(".")[0] in {"numpy", "soundfile"}]
