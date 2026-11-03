"""Architecture tests for the domain component packages and the application layer (ADR-017).

Contains the test-only import checker ``find_import_violations``. It resolves absolute, relative and
from-package imports against the real source tree and enforces exact package-segment boundaries, the
approved component dependency matrix, public-interface-only access across components, and the
compatibility exceptions for the root interface and legacy shims.
"""

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
DOMAIN = "autonomous_mixing.domain"
APPLICATION = "autonomous_mixing.application"
COMPONENTS = ("shared", "project", "context", "observation", "action", "decision", "validation", "run")
ALLOWED = {
    "shared": frozenset(),
    "project": frozenset({"shared"}),
    "context": frozenset({"shared", "project"}),
    "observation": frozenset({"shared", "project"}),
    "action": frozenset({"shared", "project"}),
    "decision": frozenset({"shared", "project", "context", "observation", "action"}),
    "validation": frozenset({"shared", "project", "observation"}),
    "run": frozenset({"shared", "project", "context", "observation", "action", "decision", "validation"}),
}
# ADR-027 migration step M1: domain and application records moved here; the new rules for this
# package are enforced by tests/architecture/test_core_records.py.
RECORDS = "autonomous_mixing.core.records"
COMPATIBILITY_SURFACES = frozenset({DOMAIN, f"{DOMAIN}.values", f"{DOMAIN}.execution"})


def _source_modules(root: Path) -> dict[str, Path]:
    """Map every module under ``root`` (recursively, caches excluded) to its file."""
    modules = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = path.relative_to(SRC).with_suffix("").parts
        name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        modules[name] = path
    return modules


ALL_MODULES = _source_modules(SRC / "autonomous_mixing")
KNOWN = frozenset(ALL_MODULES)


def _within(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")


def _component(name: str) -> str | None:
    parts = name.split(".")
    if len(parts) >= 3 and parts[:2] == ["autonomous_mixing", "domain"] and parts[2] in COMPONENTS:
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


def _rule(source: str, target: str | None, relative: bool, known_modules: frozenset[str]) -> str | None:
    if target is None:
        return "unresolvable relative import"
    if target.split(".")[0] != "autonomous_mixing":
        if not relative and target.split(".")[0] in sys.stdlib_module_names:
            return None
        return "third-party import"
    if target not in known_modules:
        return "unresolved import"
    source_component, target_component = _component(source), _component(target)
    source_is_compatibility = source in COMPATIBILITY_SURFACES
    if source_component is not None and source_component == target_component:
        return None  # same-component internals
    if _within(source, APPLICATION) and _within(target, APPLICATION):
        return None
    if _within(target, RECORDS):
        return None  # domain and application modules re-export or use the moved records
    if _within(target, APPLICATION):
        return "domain must not import application"
    if target in COMPATIBILITY_SURFACES:
        return None if source_is_compatibility else "compatibility surface used outside compatibility code"
    if target_component is None:
        return "foreign layer import"
    if target != f"{DOMAIN}.{target_component}":
        return "non-public component internals"
    if source_is_compatibility or _within(source, APPLICATION):
        return None
    if source_component is not None:
        return None if target_component in ALLOWED[source_component] else "dependency matrix"
    return "foreign layer import"


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


def find_component_cycles(edges: dict[str, set[str]]) -> list[list[str]]:
    """Return dependency cycles (as node paths) in a component graph; empty if acyclic."""
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


# --- checker tests -------------------------------------------------------------------------

FIXTURE_MODULES = KNOWN | {
    f"{DOMAIN}.project.extra",
    "autonomous_mixing.domain_extra",
    "autonomous_mixing.application_extra",
}


def _violations(source: str, module: str, *, is_package: bool = False) -> tuple[str, ...]:
    return find_import_violations(source, module, is_package, FIXTURE_MODULES)


@pytest.mark.parametrize(
    "source, module",
    [
        ("import math", f"{DOMAIN}.project.scope"),
        ("from dataclasses import dataclass", f"{DOMAIN}.run.attempt"),
        ("from autonomous_mixing.domain.project.snapshot import ProjectSnapshot", f"{DOMAIN}.project.scope"),
        ("from .snapshot import ProjectSnapshot", f"{DOMAIN}.project.scope"),
        ("from . import snapshot", f"{DOMAIN}.project.scope"),
        ("from .tracks import Track", f"{DOMAIN}.project"),
        ("from autonomous_mixing.domain.shared import TrackId", f"{DOMAIN}.project.tracks"),
        ("from autonomous_mixing.domain.project import ArtifactCheckpoint", f"{DOMAIN}.observation.provenance"),
        ("from autonomous_mixing.domain import project", f"{DOMAIN}.observation.provenance"),
        ("import autonomous_mixing.domain.project", f"{DOMAIN}.action.execution"),
        ("from ..project import ArtifactCheckpoint", f"{DOMAIN}.action.execution"),
        ("from autonomous_mixing.domain.decision import Decision", f"{DOMAIN}.run.references"),
        ("from autonomous_mixing.domain.observation import MeasuredScalar", f"{DOMAIN}.validation.criteria"),
        ("from autonomous_mixing.domain.project import AnalysisScope", f"{APPLICATION}.contracts"),
        ("from autonomous_mixing.domain.shared import require_text", f"{APPLICATION}.results"),
        ("from ..contracts import AnalysisRequest", f"{APPLICATION}.ports.analysis"),
        ("from autonomous_mixing.domain.action import ExecutionReceipt", f"{DOMAIN}.execution"),
        ("from autonomous_mixing.domain.project import ArtifactCheckpoint", DOMAIN),
    ],
)
def test_checker_allows_permitted_imports(source, module):
    is_package = module in {f"{DOMAIN}.project", DOMAIN}
    assert _violations(source, module, is_package=is_package) == ()


@pytest.mark.parametrize(
    "source, module, rule",
    [
        (
            "from autonomous_mixing.domain.project.checkpoint import ArtifactCheckpoint",
            f"{DOMAIN}.observation.provenance",
            "public",
        ),
        ("from autonomous_mixing.domain.project import checkpoint", f"{DOMAIN}.action.execution", "public"),
        ("from autonomous_mixing.domain.action import ExecutionReceipt", f"{DOMAIN}.project.checkpoint", "matrix"),
        ("from autonomous_mixing.domain.run import Run", f"{DOMAIN}.observation.observation", "matrix"),
        ("from autonomous_mixing.domain import run", f"{DOMAIN}.observation.observation", "matrix"),
        ("import autonomous_mixing.domain.run as run_component", f"{DOMAIN}.validation.result", "matrix"),
        ("from ..run import Run", f"{DOMAIN}.decision.decision", "matrix"),
        ("from autonomous_mixing.domain.decision import Decision", f"{DOMAIN}.validation.consistency", "matrix"),
        ("from autonomous_mixing.domain.project.scope import AnalysisScope", f"{APPLICATION}.contracts", "public"),
        ("from autonomous_mixing.domain import Decision", f"{APPLICATION}.contracts", "compatibility"),
        ("from autonomous_mixing.domain.values import require_text", f"{APPLICATION}.results", "compatibility"),
        (
            "from autonomous_mixing.domain.execution import ArtifactCheckpoint",
            f"{DOMAIN}.observation.provenance",
            "compatibility",
        ),
        ("from autonomous_mixing.domain import TrackId", f"{DOMAIN}.project.tracks", "compatibility"),
        ("from autonomous_mixing.application import Success", f"{DOMAIN}.run.run", "application"),
        # Look-alike packages exist in the fixture module set but are not the domain/application namesakes.
        ("from autonomous_mixing.domain_extra import x", f"{DOMAIN}.project.scope", "layer"),
        ("import autonomous_mixing.application_extra", f"{APPLICATION}.contracts", "layer"),
        ("from autonomous_mixing.domain.missing import x", f"{DOMAIN}.run.run", "unresolved"),
        ("from autonomous_mixing.audio.io import load_stem", f"{DOMAIN}.project.artifacts", "layer"),
        ("from autonomous_mixing import cli", f"{APPLICATION}.contracts", "layer"),
        ("import numpy", f"{DOMAIN}.observation.observation", "third-party"),
        ("import anthropic", f"{APPLICATION}.contracts", "third-party"),
        ("from ...audio.io import load_stem", f"{DOMAIN}.project", "layer"),
        ("from ..audio.io import load_stem", f"{DOMAIN}.project", "unresolved"),
        ("from ........ import x", f"{DOMAIN}.project.scope", "unresolvable"),
        ("from ..action import ExecutionReceipt", f"{DOMAIN}.project.checkpoint", "matrix"),
    ],
)
def test_checker_rejects_forbidden_imports(source, module, rule):
    is_package = module == f"{DOMAIN}.project"
    violations = _violations(source, module, is_package=is_package)
    assert violations, source
    assert any(rule in v for v in violations), violations


def test_checker_treats_known_attribute_imports_from_a_public_interface_as_public_access():
    # ``nonexistent_module`` is not a known module, so it is treated as an attribute of the public interface.
    assert (
        _violations("from autonomous_mixing.domain.project import nonexistent_module", f"{DOMAIN}.action.execution")
        == ()
    )


def test_checker_resolves_nested_package_init_relative_imports():
    assert _violations("from .checkpoint import ArtifactCheckpoint", f"{DOMAIN}.project", is_package=True) == ()
    assert _violations("from ..action import Action", f"{DOMAIN}.project", is_package=True) != ()
    assert _violations("from ..project import Track", f"{DOMAIN}.context", is_package=True) == ()


def test_cycle_detection_finds_a_cycle_fixture_and_accepts_the_matrix():
    assert find_component_cycles({"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": set()}) != []
    assert find_component_cycles({name: set(deps) for name, deps in ALLOWED.items()}) == []


# --- real source tree ----------------------------------------------------------------------


def _scanned(prefix: str) -> dict[str, Path]:
    return {name: path for name, path in ALL_MODULES.items() if _within(name, prefix)}


def test_recursive_scan_discovers_moved_and_nested_modules():
    names = set(_scanned(DOMAIN)) | set(_scanned(APPLICATION))
    for expected in (
        f"{DOMAIN}.project.checkpoint",
        f"{DOMAIN}.action.execution",
        f"{DOMAIN}.run.attempt",
        f"{DOMAIN}.shared.invariants",
        f"{DOMAIN}.validation.consistency",
        f"{APPLICATION}.ports.analysis",
        f"{APPLICATION}.ports",
        # Execution-contract stage (ADR-020).
        f"{DOMAIN}.project.mix",
        f"{APPLICATION}.execution_contracts",
        f"{APPLICATION}.execution_preflight",
        f"{APPLICATION}.ports.processing",
        # Application component packages; the legacy application modules above remain as facades.
        f"{APPLICATION}.shared.results",
        f"{APPLICATION}.observation.contracts",
        f"{APPLICATION}.decision.validation",
        f"{APPLICATION}.admission.admission",
        f"{APPLICATION}.execution.identity",
        f"{APPLICATION}.execution.preflight",
        f"{APPLICATION}.execution.ports",
    ):
        assert expected in names, expected
    assert not [p for p in ALL_MODULES.values() if "__pycache__" in p.parts]


@pytest.mark.parametrize("prefix", [DOMAIN, APPLICATION])
def test_real_domain_and_application_modules_have_no_import_violations(prefix):
    problems = []
    for name, path in _scanned(prefix).items():
        problems += find_import_violations(path.read_text(encoding="utf-8"), name, path.name == "__init__.py", KNOWN)
    assert problems == []


def test_real_component_graph_follows_the_matrix_and_is_acyclic():
    edges: dict[str, set[str]] = {name: set() for name in COMPONENTS}
    for name, path in _scanned(DOMAIN).items():
        source_component = _component(name)
        if source_component is None:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                target = _component(node.module)
                if target and target != source_component:
                    edges[source_component].add(target)
    for component, deps in edges.items():
        assert deps <= ALLOWED[component], (component, deps - ALLOWED[component])
    assert find_component_cycles(edges) == []
    assert "run" not in set().union(*(edges[c] for c in COMPONENTS if c != "run"))


def test_only_compatibility_tests_import_root_or_legacy_domain_surfaces():
    allowed_files = {"test_domain_component_compatibility.py", "test_domain_component_boundaries.py"}
    offenders = []
    for path in sorted((SRC.parent / "tests").rglob("*.py")):
        if "__pycache__" in path.parts or path.name in allowed_files:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                targets = [node.module or ""]
            elif isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            else:
                continue
            for target in targets:
                if target in COMPATIBILITY_SURFACES and not (
                    target == DOMAIN
                    and isinstance(node, ast.ImportFrom)
                    and all(alias.name in COMPONENTS for alias in node.names)
                ):
                    offenders.append(f"{path.name}: {target}")
    assert offenders == []


def test_domain_never_loads_application_or_third_party_modules_at_runtime():
    import subprocess

    script = (
        "import sys\nbefore = set(sys.modules)\n"
        + "".join(f"import autonomous_mixing.domain.{c}\n" for c in COMPONENTS)
        + "import autonomous_mixing.domain, autonomous_mixing.domain.values, autonomous_mixing.domain.execution\n"
        "print('\\n'.join(sorted(set(sys.modules) - before)))\n"
    )
    loaded = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True).stdout.split()
    for name in loaded:
        ok = name.split(".")[0] in sys.stdlib_module_names or name == "autonomous_mixing" or _within(name, DOMAIN)
        ok = ok or name == "autonomous_mixing.core" or _within(name, RECORDS)  # ADR-027 step M1
        assert ok, name
