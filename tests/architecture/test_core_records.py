"""Architecture tests for ``autonomous_mixing.core.records`` (ADR-027; migration plan section 6).

Guarantee-ledger rows covered here: G1 for the records package (it imports only the standard library
and itself), the acyclic records module graph of the migration plan (section 3), G9 (every public
record is defined exactly once, in its listed module) and G11 (core records are frozen dataclasses
with immutable field types and no arrays; no dataclass is defined in ``core`` outside ``core.records``).
G4 for ``core``: importing the records loads only the standard library and ``core`` modules.
"""

import ast
import dataclasses
import importlib
import re
import subprocess
import sys
import typing
from pathlib import Path

import pytest
from import_resolver import module_files, resolve_imports

PKG = "autonomous_mixing"
CORE = f"{PKG}.core"
RECORDS = f"{CORE}.records"
SRC = Path(__file__).resolve().parents[2] / "src"
CORE_DIR = SRC / PKG / "core"
RECORDS_DIR = CORE_DIR / "records"

# Migration plan, section 3: each records module may import only the records modules listed for it.
ALLOWED_RECORD_IMPORTS = {
    "identifiers": {"invariants"},
    "invariants": set(),
    "artifacts": {"identifiers", "invariants"},
    "project": {"identifiers", "invariants", "artifacts"},
    "mix": {"identifiers", "invariants"},
    "context": {"identifiers", "invariants", "project"},
    "observation": {"identifiers", "invariants", "artifacts", "project"},
    "actions": {"identifiers", "invariants", "artifacts", "project"},
    "budgets": {"identifiers", "invariants"},
    "results": {"identifiers", "invariants"},
    "decision": {"identifiers", "invariants", "actions", "budgets", "context", "observation", "project"},
    "analysis": {"identifiers", "invariants", "observation", "project"},
    "verification": {"identifiers", "invariants", "observation", "project"},
    "rendering": {"identifiers", "invariants", "actions", "artifacts", "mix", "observation", "project"},
    "admission": {"identifiers", "invariants", "actions", "decision", "mix", "observation", "rendering"},
    "run": {
        "identifiers",
        "invariants",
        "actions",
        "budgets",
        "context",
        "decision",
        "observation",
        "project",
        "verification",
    },
}

# G9: the complete public inventory, owner module -> public names (type aliases included).
INVENTORY = {
    "identifiers": (
        "ProjectId",
        "TrackId",
        "ArtifactId",
        "ObservationId",
        "ContextId",
        "ActionId",
        "DecisionId",
        "CriteriaId",
        "ConditionId",
        "ValidationResultId",
        "RunId",
        "AttemptId",
        "ExecutionId",
        "CheckpointId",
        "Revision",
        "VersionRef",
    ),
    "invariants": (
        "require_text",
        "require_finite",
        "require_finite_result",
        "require_count",
        "require_utc",
        "require_instance",
        "require_tuple_of",
    ),
    "artifacts": ("ArtifactKind", "AudioArtifact"),
    "project": (
        "Track",
        "ProjectSnapshot",
        "ArtifactCheckpoint",
        "TrackGainState",
        "ProjectProcessingState",
        "ProjectState",
    ),
    "mix": (
        "SummingPolicy",
        "MixRecipe",
        "NumericalPrecision",
        "GainRule",
        "SummationOrder",
        "LevelPolicy",
        "OutputContainer",
        "SampleEncoding",
        "ProcessingSpec",
    ),
    "context": ("AssertionSource", "TrackRole", "ContextPreference", "MusicalContext"),
    "observation": (
        "TimeRange",
        "FrequencyRange",
        "AnalysisScope",
        "AnalysisSpec",
        "RenderSpec",
        "MeasuredScalar",
        "Unavailable",
        "Observation",
        "validate_observation_sources",
    ),
    "actions": (
        "Action",
        "SetTrackGain",
        "ExecutionStatus",
        "ExecutionIssue",
        "ExecutionReceipt",
        "validate_receipt_against_checkpoint",
        "derive_candidate_checkpoint",
        "RollbackStatus",
        "RollbackReceipt",
        "validate_rollback_references",
    ),
    "budgets": ("BudgetLimits", "BudgetUsage"),
    "results": ("Success", "PortFailureCode", "PortFailure", "PortResult"),
    "decision": (
        "ControllerIdentity",
        "ProposeAction",
        "AbstentionReason",
        "Abstain",
        "Decision",
        "DecisionRequest",
        "DecisionResponse",
    ),
    "analysis": ("AnalysisRequest", "AnalysisResponse"),
    "verification": (
        "MetricSelector",
        "ComparisonOperator",
        "ImprovementDirection",
        "BaselineReference",
        "TargetCondition",
        "ImprovementCondition",
        "AbsoluteGuard",
        "DeltaGuard",
        "ValidationCriteria",
        "AssessmentMode",
        "CheckStatus",
        "ValidationVerdict",
        "TargetStatus",
        "CheckResult",
        "ValidationResult",
    ),
    "rendering": (
        "ExecutionWorkspace",
        "CandidateIdentity",
        "ProcessingPlan",
        "ArtifactProcessingEvidence",
        "ProcessedCandidate",
        "ProcessingFailure",
        "ProcessingOutcome",
    ),
    "admission": (
        "TrackGainPermission",
        "ActionAdmissionPolicy",
        "ActionAdmissionRequest",
        "AdmissionRejectionCode",
        "AdmittedAction",
        "RejectedAction",
        "AdmissionResult",
        "StemBinding",
        "ExecutionRequest",
        "PreflightCode",
        "PreflightRejected",
        "MetadataReady",
        "PreflightResult",
    ),
    "run": (
        "RunConfiguration",
        "RunState",
        "RunOutcome",
        "FinalProjectState",
        "Run",
        "AttemptDisposition",
        "CorrectionAttempt",
    ),
}

MUTABLE_TYPES = {"list", "dict", "set", "bytearray", "List", "Dict", "Set", "MutableMapping", "MutableSequence"}
ARRAY_PREFIXES = ("numpy", "np.", "ndarray")


def _top_level_definitions(path: Path) -> set[str]:
    names = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


RECORD_FILES = {name: path for name, path in module_files(SRC, RECORDS_DIR).items() if name != RECORDS}
KNOWN = frozenset(module_files(SRC, SRC / PKG))


def records_violations(source: str, module: str) -> list[str]:
    """Import violations of one records module: only the standard library and its planned siblings."""
    short = module.rsplit(".", 1)[1]
    violations = []
    for target in resolve_imports(source, module, False, KNOWN):
        if target is None:
            violations.append("relative import escapes the package")
        elif target.split(".")[0] in sys.stdlib_module_names:
            continue
        elif not target.startswith(RECORDS + "."):
            violations.append(f"outside the records modules: {target}")
        elif target.rsplit(".", 1)[1] not in ALLOWED_RECORD_IMPORTS[short]:
            violations.append(f"not a planned records dependency: {target}")
    return violations


def test_the_records_modules_are_exactly_the_planned_ones():
    assert {name.rsplit(".", 1)[1] for name in RECORD_FILES} == set(ALLOWED_RECORD_IMPORTS) == set(INVENTORY)


def test_records_import_only_the_standard_library_and_their_planned_records_modules():
    for name, path in RECORD_FILES.items():
        assert records_violations(path.read_text(encoding="utf-8"), name) == [], name


DECISION = f"{RECORDS}.decision"


@pytest.mark.parametrize(
    "source",
    [
        # Package-form imports must resolve to the child module (Codex review of M2).
        "from autonomous_mixing.core.records import run",
        "from . import run",
        "from autonomous_mixing.core import records",
        "from autonomous_mixing.core.records import Decision",
        "from .. import ports",
        "from ..loop import transitions",
        "import autonomous_mixing.core.records.run",
        "import numpy",
    ],
)
def test_records_checker_rejects_forbidden_imports(source):
    assert records_violations(source, DECISION), source


@pytest.mark.parametrize(
    "source",
    [
        "from . import budgets",
        "from .budgets import BudgetLimits",
        "from autonomous_mixing.core.records.project import ProjectState",
        "from autonomous_mixing.core.records import observation, context",
        "from dataclasses import dataclass",
    ],
)
def test_records_checker_allows_planned_imports(source):
    assert records_violations(source, DECISION) == [], source


def test_the_records_package_initializer_contains_only_a_docstring():
    # ADR-027 step M4: no convenience re-exports, so every record has exactly one import path (G6).
    body = ast.parse((RECORDS_DIR / "__init__.py").read_text(encoding="utf-8")).body
    assert len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
    assert isinstance(body[0].value.value, str)


# Public top-level names in records modules that are not records: the type variable of ``Success``.
NOT_RECORDS = {"T"}


def _records():
    """(name, object) for every inventoried record, read from its owning module."""
    for short, names in INVENTORY.items():
        module = importlib.import_module(f"{RECORDS}.{short}")
        for name in names:
            yield name, getattr(module, name)


def test_the_planned_records_graph_is_acyclic():
    state: dict[str, str] = {}

    def visit(node: str, path: list[str]) -> None:
        state[node] = "active"
        for nxt in sorted(ALLOWED_RECORD_IMPORTS[node]):
            assert state.get(nxt) != "active", f"cycle: {path + [nxt]}"
            if nxt not in state:
                visit(nxt, path + [nxt])
        state[node] = "done"

    for start in sorted(ALLOWED_RECORD_IMPORTS):
        if start not in state:
            visit(start, [start])


def test_every_public_record_is_defined_exactly_once_in_its_listed_module():
    listed = [name for names in INVENTORY.values() for name in names]
    assert len(listed) == len(set(listed))
    definitions = {short: _top_level_definitions(RECORDS_DIR / f"{short}.py") for short in INVENTORY}
    for short, names in INVENTORY.items():
        # Completeness: every public definition of the module is inventoried, so G11 sees every record.
        public = {name for name in definitions[short] if not name.startswith("_")} - NOT_RECORDS
        assert public == set(names), short
        for name in names:
            owners = [other for other, defined in definitions.items() if name in defined]
            assert owners == [short], f"{name} defined in {owners}"


# Inherited exception to G11 (Codex review of M1): ``Success[T]`` is a generic container whose payload
# type is unconstrained, so a frozen ``Success`` can still hold a mutable payload. Its constructor is not
# changed during the move; port responses wrapped in it are records and checked here themselves.
UNCONSTRAINED_PAYLOAD_FIELDS = {("Success", "value")}


def _annotation_text(annotation: object) -> str:
    return annotation if isinstance(annotation, str) else repr(annotation)


def test_core_records_are_frozen_dataclasses_with_immutable_field_types():
    checked = 0
    for name, obj in _records():
        if not (isinstance(obj, type) and dataclasses.is_dataclass(obj)):
            continue
        checked += 1
        assert obj.__dataclass_params__.frozen, f"{name} must be frozen"
        hints = typing.get_type_hints(obj)
        for field in dataclasses.fields(obj):
            text = _annotation_text(hints.get(field.name, field.type))
            origin = typing.get_origin(hints.get(field.name))
            assert origin not in (list, dict, set), f"{name}.{field.name}: {text}"
            tokens = {token.rsplit(".", 1)[-1] for token in re.findall(r"[A-Za-z_][\w.]*", text)}
            assert not tokens & MUTABLE_TYPES, f"{name}.{field.name}: {text}"
            assert not any(prefix in text for prefix in ARRAY_PREFIXES), f"{name}.{field.name}: {text}"
    assert checked > 50, "the probe must see the record classes"


def _is_unconstrained(hint: object) -> bool:
    """True if ``hint`` or any type argument nested in it is a TypeVar, ``Any`` or ``object``."""
    if isinstance(hint, typing.TypeVar) or hint is typing.Any or hint is object:
        return True
    return any(_is_unconstrained(argument) for argument in typing.get_args(hint))


def unconstrained_fields(cls: type) -> set[str]:
    hints = typing.get_type_hints(cls)
    return {field.name for field in dataclasses.fields(cls) if _is_unconstrained(hints.get(field.name, field.type))}


def test_only_the_documented_record_fields_have_an_unconstrained_payload_type():
    found = set()
    for name, obj in _records():
        if isinstance(obj, type) and dataclasses.is_dataclass(obj):
            found |= {(name, field) for field in unconstrained_fields(obj)}
    assert found == UNCONSTRAINED_PAYLOAD_FIELDS


_T = typing.TypeVar("_T")


@pytest.mark.parametrize(
    "annotation",
    [
        typing.Any,
        object,
        _T,
        tuple[typing.Any, ...],  # nested cases missed before the Codex review of M2
        tuple[object, ...],
        tuple[_T, ...],
        frozenset[object],
        typing.Any | None,
        tuple[tuple[int, typing.Any], ...],
    ],
    ids=repr,
)
def test_unconstrained_check_finds_direct_and_nested_payloads(annotation):
    probe = dataclasses.make_dataclass("Probe", [("payload", annotation)], frozen=True)
    assert unconstrained_fields(probe) == {"payload"}


@pytest.mark.parametrize(
    "annotation", [int, str | None, tuple[int, ...], frozenset[str], tuple[tuple[int, str], ...]], ids=repr
)
def test_unconstrained_check_accepts_concrete_types(annotation):
    probe = dataclasses.make_dataclass("Probe", [("payload", annotation)], frozen=True)
    assert unconstrained_fields(probe) == set()


def test_no_dataclass_is_defined_in_core_outside_the_records_package():
    for name, path in module_files(SRC, CORE_DIR).items():
        if name == RECORDS or name.startswith(RECORDS + "."):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        decorated = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and any("dataclass" in ast.unparse(d) for d in node.decorator_list)
        ]
        assert decorated == [], f"{name} defines dataclasses {decorated}"


def test_importing_the_records_loads_only_the_standard_library_and_core_modules():
    script = (
        "import sys\nbefore = set(sys.modules)\n"
        f"import {RECORDS}\n"
        "print(chr(10).join(sorted(set(sys.modules) - before)))\n"
    )
    loaded = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True).stdout.split()
    assert loaded, "the probe must observe newly loaded modules"
    for name in loaded:
        ok = name.split(".")[0] in sys.stdlib_module_names or name in (PKG, CORE) or name.startswith(RECORDS)
        assert ok, name
