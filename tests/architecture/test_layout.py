"""Whole-tree architecture tests (ADR-027; migration step M4).

Guarantee-ledger row G3 (completed here): every module belongs to exactly one ring, every project import
resolves to an existing module, look-alike packages are not mistaken for project packages, and nothing
imports the drivers. Also asserts that the pre-ADR-027 layout is gone (G7 closed by the class (b) step)
and that the console script points at the driver.
"""

import ast
import os
import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path

import pytest
from import_resolver import missing_package_children, module_files, resolve_imports

PKG = "autonomous_mixing"
SRC = Path(__file__).resolve().parents[2] / "src"
MODULES = module_files(SRC, SRC / PKG)
KNOWN = frozenset(MODULES)
# Project packages; their initializers define nothing (asserted below), so they export only submodules.
PACKAGES = frozenset(name for name, path in MODULES.items() if path.name == "__init__.py")
RINGS = ("core", "adapters", "drivers")


def ring(name: str) -> str | None:
    parts = name.split(".")
    return parts[1] if len(parts) > 1 and parts[0] == PKG and parts[1] in RINGS else None


def project_import_problems(source: str, module: str, is_package: bool, known: frozenset[str] = KNOWN) -> list[str]:
    """Unresolved project imports and imports of the drivers from outside the drivers."""
    problems = [f"unresolved: {name}" for name in missing_package_children(source, module, is_package, PACKAGES, known)]
    for target in resolve_imports(source, module, is_package, known):
        if target is None:
            problems.append("relative import escapes the package")
        elif target.split(".")[0] != PKG:
            continue  # standard library, third-party, or a look-alike top-level package such as autonomous_mixing_extra
        elif target not in known:
            problems.append(f"unresolved: {target}")
        elif ring(target) == "drivers" and ring(module) != "drivers":
            problems.append(f"imports a driver: {target}")
    return problems


def test_every_module_belongs_to_exactly_one_ring():
    unclassified = [name for name in MODULES if name != PKG and ring(name) is None]
    assert unclassified == []


def test_every_package_initializer_contains_only_a_docstring():
    # Precondition of the unresolved-import check: packages export only their submodules (G6).
    for name in sorted(PACKAGES):
        body = ast.parse(MODULES[name].read_text(encoding="utf-8")).body
        assert len(body) == 1 and isinstance(body[0], ast.Expr), name
        assert isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str), name


def test_the_pre_adr_027_layout_is_gone():
    for old in ("domain", "application", "infrastructure", "audio"):
        assert not (SRC / PKG / old).exists(), old
    assert not (SRC / PKG / "cli.py").exists()


def test_every_project_import_resolves_and_no_module_outside_the_drivers_imports_them():
    for name, path in MODULES.items():
        assert project_import_problems(path.read_text(encoding="utf-8"), name, path.name == "__init__.py") == [], name


GAIN_MIX = f"{PKG}.adapters.rendering.gain_mix"


@pytest.mark.parametrize(
    "source",
    [
        "from autonomous_mixing.core.records import nothing_here",
        "import autonomous_mixing.domain.project",
        "from autonomous_mixing.application.execution import ProcessingPlan",
        "from autonomous_mixing import cli",
        "from autonomous_mixing.drivers import cli",
        "import autonomous_mixing.drivers.cli",
        "from ..... import anything",
    ],
)
def test_checker_rejects_unresolved_imports_and_driver_imports(source):
    assert project_import_problems(source, GAIN_MIX, False), source


@pytest.mark.parametrize(
    "source",
    [
        "import numpy",
        "from autonomous_mixing.core.records.rendering import ProcessingPlan",
        "from autonomous_mixing.core.records import rendering",
        "import autonomous_mixing_extra",  # look-alike top-level package: not the project
        "from autonomous_mixing_extra.core import loop",
    ],
)
def test_checker_accepts_resolved_and_look_alike_imports(source):
    assert project_import_problems(source, GAIN_MIX, False) == [], source


def test_look_alike_packages_are_not_classified_as_rings():
    assert ring("autonomous_mixing_extra.core") is None
    assert ring(f"{PKG}.core_extra.records") is None
    assert ring(f"{PKG}.core.records") == "core"


def test_the_console_script_runs_the_driver():
    scripts = [e.value for e in entry_points(group="console_scripts") if e.name == "autonomous-mixing"]
    assert scripts == [f"{PKG}.drivers.cli:main"]


def test_the_standard_library_list_is_available():
    # The rules above rely on sys.stdlib_module_names (Python 3.10+).
    assert "dataclasses" in sys.stdlib_module_names


def test_every_module_imports_alone_as_the_first_import_of_a_fresh_process():
    # G5, carried over from the retired ADR-022 boundary test: catches import cycles that only fail
    # depending on which module is imported first. One OpenBLAS thread per probe avoids the memory
    # exhaustion seen when many parallel probes load NumPy (research log, 2026-11-03).
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1"}
    probes = {
        name: subprocess.Popen(
            [sys.executable, "-c", f"import {name}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
        )
        for name in sorted(MODULES)
    }
    failures = {}
    for name, probe in probes.items():
        _out, err = probe.communicate(timeout=120)
        if probe.returncode != 0:
            failures[name] = err.strip().splitlines()[-1:]
    assert len(probes) == len(MODULES) and failures == {}
