"""Structural guards for the owner-approved coordinator split; behavior lives in existing tests."""

import ast
import inspect
from pathlib import Path

from autonomous_mixing.core.loop.coordinator import run

LOOP = Path(inspect.getfile(run)).parent
OWNERS = {
    "runtime": ("gate", "record", "checkpoint", "promote"),
    "observation": ("inspect", "analyze", "diagnose"),
    "decision_round": ("decision_round", "evaluate_decision", "act"),
    "correction": ("render", "verify_candidate"),
    "recovery": ("recover_saved", "reconcile_unknown"),
    "acceptance_flow": ("accept", "finish_acceptance"),
    "feedback": ("add_feedback", "copied"),
    "completion": ("finalize",),
}


def test_workflow_functions_have_explicit_component_owners():
    for module, functions in OWNERS.items():
        path = LOOP / f"{module}.py"
        assert path.exists(), module
        defined = {n.name for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef)}
        assert set(functions) <= defined, module


def test_coordinator_contains_no_nested_workflow_closure_or_dictionary_context():
    tree = ast.parse((LOOP / "coordinator.py").read_text())
    for function in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        nested = [n.name for n in ast.walk(function) if isinstance(n, ast.FunctionDef) and n is not function]
        assert set(nested) <= {"remember"}, nested
    for path in LOOP.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            assert not (
                isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "ctx"
            ), path
            assert not isinstance(node, ast.Nonlocal) or node.names == ["latest"], path


def test_loop_module_graph_has_no_cycles():
    graph = {}
    for path in LOOP.glob("*.py"):
        graph[path.stem] = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("autonomous_mixing.core.loop."):
                graph[path.stem].add(node.module.split(".")[-1])

    def visit(name, active, done):
        assert name not in active, active + (name,)
        if name not in done:
            for target in graph.get(name, ()):
                visit(target, active + (name,), done)
            done.add(name)

    done = set()
    for name in graph:
        visit(name, (), done)


def test_public_entry_point_keeps_explicit_collaborators():
    assert list(inspect.signature(run).parameters) == [
        "request",
        "analyzer",
        "diagnosis_source",
        "decision_maker",
        "renderer",
        "integrity_inspector",
        "journal",
        "clock",
        "identities",
    ]
