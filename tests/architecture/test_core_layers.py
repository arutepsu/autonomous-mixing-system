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

import pytest
from import_resolver import module_files, resolve_imports

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
    "ports.run_journal": ("RunJournal", "validate_trace_append_response", "validate_run_journal_snapshot"),
    "ports.analysis": ("AudioAnalyzer", "validate_analysis_response"),
    "ports.decision": ("DecisionMaker", "validate_decision_response", "validate_decision_references"),
    "ports.rendering": ("Renderer", "validate_rendered_candidate"),
    "ports.acceptance": (
        "AcceptanceJournal",
        "validate_acceptance_commit_response",
        "validate_acceptance_query_response",
    ),
    "ports.integrity": ("StateIntegrityInspector", "validate_state_integrity_response"),
    "ports.coordinator_runtime": (
        "DiagnosisSource",
        "RunRenderer",
        "RunClock",
        "IdentitySource",
        "validate_diagnosis_response",
        "validate_run_render_response",
    ),
    "admission.admission": ("admit_action",),
    "admission.preflight": ("preflight_execution",),
    "admission.capabilities": ("build_action_capability_view",),
    "admission.decision_gate": ("assess_decision",),
    "verification.consistency": ("validate_verification_references",),
    "verification.evidence": ("match_evidence",),
    "verification.assessment": ("assess_verification",),
    "loop.transitions": ("validate_gain_execution", "derive_candidate_project_state"),
    "loop.references": ("validate_attempt_references",),
    "loop.budgets": ("check_budget",),
    "loop.verification_routing": ("route_verification",),
    "loop.acceptance": ("validate_acceptance_event",),
    "loop.reconciliation": ("reconcile_acceptance",),
}


MODULES = module_files(SRC, CORE_DIR)
KNOWN = frozenset(module_files(SRC, SRC / PKG))


def _package(name: str) -> str | None:
    parts = name.split(".")
    return parts[2] if len(parts) > 2 and parts[:2] == [PKG, "core"] else None


def core_violations(source: str, module: str, is_package: bool, known: frozenset[str] = KNOWN) -> list[str]:
    """Rule (a) and rule (b) violations of one core module's source."""
    violations = []
    source_package = _package(module)
    for target in resolve_imports(source, module, is_package, known):
        if target is None:
            violations.append("relative import escapes the package")
        elif target.split(".")[0] in sys.stdlib_module_names:
            continue
        elif not (target == CORE or target.startswith(CORE + ".")):
            violations.append(f"rule (a): {target}")
        elif source_package is not None and (
            target == CORE or _package(target) not in ALLOWED_PACKAGES[source_package]
        ):
            violations.append(f"rule (b): {target}")
    return violations


def test_the_core_packages_are_the_planned_ones():
    present = {_package(name) for name in MODULES if _package(name)}
    assert present <= set(ALLOWED_PACKAGES), present
    behavior = {name.split(".", 2)[2] for name in MODULES if _package(name) not in (None, "records")}
    behavior -= {"ports", "admission", "verification", "loop"}
    assert behavior == set(BEHAVIOR_INVENTORY)


def test_core_modules_follow_rules_a_and_b():
    for name, path in MODULES.items():
        source = path.read_text(encoding="utf-8")
        assert core_violations(source, name, path.name == "__init__.py") == [], name


PORTS_ANALYSIS = f"{CORE}.ports.analysis"
LOOP_REFERENCES = f"{CORE}.loop.references"


@pytest.mark.parametrize(
    "source, module",
    [
        # Package-form imports must not hide a forbidden dependency behind their package (Codex review of M2).
        ("from autonomous_mixing.core import loop", PORTS_ANALYSIS),
        ("from .. import loop", PORTS_ANALYSIS),
        ("from .. import admission", PORTS_ANALYSIS),
        ("import autonomous_mixing.core.loop", PORTS_ANALYSIS),
        ("from autonomous_mixing.core.loop import transitions", PORTS_ANALYSIS),
        ("from autonomous_mixing.core.loop.transitions import validate_gain_execution", PORTS_ANALYSIS),
        ("from autonomous_mixing import core", PORTS_ANALYSIS),
        ("import autonomous_mixing.core", PORTS_ANALYSIS),
        ("from autonomous_mixing.core import verification", f"{CORE}.admission.admission"),
        ("import numpy", PORTS_ANALYSIS),
        ("from autonomous_mixing.domain import project", LOOP_REFERENCES),
        ("from .... import cli", PORTS_ANALYSIS),
        ("from ..... import anything", PORTS_ANALYSIS),
    ],
)
def test_checker_rejects_forbidden_core_imports(source, module):
    assert core_violations(source, module, False), source


@pytest.mark.parametrize(
    "source, module",
    [
        ("import dataclasses", PORTS_ANALYSIS),
        ("from autonomous_mixing.core.records import decision", PORTS_ANALYSIS),
        ("from autonomous_mixing.core.records.decision import Decision", PORTS_ANALYSIS),
        ("from ..records import decision", PORTS_ANALYSIS),
        ("from . import decision", PORTS_ANALYSIS),
        ("from autonomous_mixing.core import admission", LOOP_REFERENCES),
        ("from .. import verification, ports", LOOP_REFERENCES),
        ("from autonomous_mixing.core.ports import decision", f"{CORE}.admission.admission"),
    ],
)
def test_checker_allows_permitted_core_imports(source, module):
    assert core_violations(source, module, False) == [], source


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
