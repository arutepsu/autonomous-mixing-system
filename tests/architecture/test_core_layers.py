"""Architecture tests for the core packages around the records (ADR-027 points 2 to 4; migration step M2).

Guarantee-ledger rows covered here: G1 for the whole core (rule a: ``core`` imports only the standard
library and ``core``; rule b: ports import only records, stage packages import records and ports, only
the loop composes stages and ports), G6 (package initializers re-export nothing, so every name has one
import path), G9 for behavior (every moved function is defined exactly once, in its listed module) and
G4 for the whole core (importing every core module loads no third-party code).
"""

import ast
import importlib
import subprocess
import sys
from pathlib import Path

PKG = "autonomous_mixing"
CORE = f"{PKG}.core"
SRC = Path(__file__).resolve().parents[2] / "src"
CORE_DIR = SRC / PKG / "core"

STAGES = ("admission", "verification", "diagnosis")
# Which core packages each core package may import (rule b); the loop may import all of them.
ALLOWED_PACKAGES = {
    "records": {"records"},
    "ports": {"records", "ports"},
    "admission": {"records", "ports", "admission"},
    "verification": {"records", "ports", "verification"},
    "diagnosis": {"records", "ports", "diagnosis"},
    "loop": {"records", "ports", "admission", "verification", "diagnosis", "loop"},
}

# Behavior modules and their public names (G9 for behavior); private helpers are not listed.
BEHAVIOR_INVENTORY = {
    "ports.analysis": ("AudioAnalyzer", "validate_analysis_response"),
    "ports.decision": ("DecisionController", "validate_decision_response", "validate_decision_references"),
    "ports.rendering": ("AudioProcessor", "validate_processed_candidate"),
    "admission.admission": ("admit_action",),
    "admission.preflight": ("preflight_execution",),
    "verification.consistency": ("validate_validation_references",),
    "loop.transitions": ("validate_gain_execution", "derive_candidate_project_state"),
    "loop.references": ("validate_attempt_references",),
}


def _modules() -> dict[str, Path]:
    files = {}
    for path in sorted(CORE_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = path.relative_to(SRC).with_suffix("").parts
        files[".".join(parts[:-1] if parts[-1] == "__init__" else parts)] = path
    return files


MODULES = _modules()


def _imports(name: str, path: Path) -> set[str]:
    package = name.split(".") if path.name == "__init__.py" else name.split(".")[:-1]
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                found.add(".".join(base + ([node.module] if node.module else [])))
            else:
                found.add(node.module or "")
    return found


def _package(name: str) -> str | None:
    parts = name.split(".")
    return parts[2] if len(parts) > 2 else None


def test_the_core_packages_are_the_planned_ones():
    present = {_package(name) for name in MODULES if _package(name)}
    assert present <= set(ALLOWED_PACKAGES), present
    behavior = {name.split(".", 2)[2] for name in MODULES if _package(name) not in (None, "records")}
    behavior -= {"ports", "admission", "verification", "loop"}
    assert behavior == set(BEHAVIOR_INVENTORY)


def test_core_imports_only_the_standard_library_and_core():  # rule (a)
    for name, path in MODULES.items():
        for target in _imports(name, path):
            if target.split(".")[0] in sys.stdlib_module_names:
                continue
            assert target == CORE or target.startswith(CORE + "."), f"{name} imports {target}"


def test_core_packages_import_only_their_allowed_core_packages():  # rule (b)
    for name, path in MODULES.items():
        source = _package(name)
        if source is None:
            continue
        for target in _imports(name, path):
            if target.startswith(CORE + "."):
                assert _package(target) in ALLOWED_PACKAGES[source], f"{name} imports {target}"


def test_behavior_package_initializers_re_export_nothing():
    for package in ("ports", "admission", "verification", "loop"):
        tree = ast.parse((CORE_DIR / package / "__init__.py").read_text(encoding="utf-8"))
        statements = [node for node in tree.body if not (isinstance(node, ast.Expr) and node is tree.body[0])]
        assert statements == [], f"{package}/__init__.py must contain only its docstring"


def test_every_moved_function_is_defined_exactly_once_in_its_listed_module():
    definitions = {}
    for name, path in MODULES.items():
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                definitions.setdefault(node.name, []).append(name)
    for module, names in BEHAVIOR_INVENTORY.items():
        loaded = importlib.import_module(f"{CORE}.{module}")
        for name in names:
            assert definitions.get(name) == [f"{CORE}.{module}"], f"{name}: {definitions.get(name)}"
            assert callable(getattr(loaded, name)), name


def test_importing_the_whole_core_loads_only_the_standard_library_and_core():  # G4
    imports = "".join(f"import {name}\n" for name in sorted(MODULES))
    script = f"import sys\nbefore = set(sys.modules)\n{imports}print(chr(10).join(sorted(set(sys.modules) - before)))\n"
    loaded = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True).stdout.split()
    assert loaded, "the probe must observe newly loaded modules"
    for name in loaded:
        ok = name.split(".")[0] in sys.stdlib_module_names or name == PKG or name.startswith(CORE)
        assert ok, name
