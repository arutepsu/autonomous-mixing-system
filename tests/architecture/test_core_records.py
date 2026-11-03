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


def _module_files(root: Path) -> dict[str, Path]:
    files = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = path.relative_to(SRC).with_suffix("").parts
        files[".".join(parts[:-1] if parts[-1] == "__init__" else parts)] = path
    return files


def _imports(name: str, path: Path) -> set[str]:
    """Absolute module names imported by one module; relative imports are resolved."""
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


RECORD_FILES = {name: path for name, path in _module_files(RECORDS_DIR).items() if name != RECORDS}


def test_the_records_modules_are_exactly_the_planned_ones():
    assert {name.rsplit(".", 1)[1] for name in RECORD_FILES} == set(ALLOWED_RECORD_IMPORTS) == set(INVENTORY)


def test_records_import_only_the_standard_library_and_their_planned_records_modules():
    for name, path in RECORD_FILES.items():
        short = name.rsplit(".", 1)[1]
        for target in _imports(name, path):
            if target.split(".")[0] in sys.stdlib_module_names:
                continue
            assert target.startswith(RECORDS + "."), f"{name} imports {target}"
            assert target.rsplit(".", 1)[1] in ALLOWED_RECORD_IMPORTS[short], f"{name} imports {target}"


def test_the_records_interface_imports_only_records_modules():
    for target in _imports(RECORDS, RECORDS_DIR / "__init__.py"):
        assert target.startswith(RECORDS + ".") and target in RECORD_FILES, target


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
    records = importlib.import_module(RECORDS)
    listed = [name for names in INVENTORY.values() for name in names]
    assert len(listed) == len(set(listed))
    assert set(records.__all__) == set(listed)
    definitions = {short: _top_level_definitions(RECORDS_DIR / f"{short}.py") for short in INVENTORY}
    for short, names in INVENTORY.items():
        module = importlib.import_module(f"{RECORDS}.{short}")
        for name in names:
            owners = [other for other, defined in definitions.items() if name in defined]
            assert owners == [short], f"{name} defined in {owners}"
            assert getattr(records, name) is getattr(module, name), name


# Inherited exception to G11 (Codex review of M1): ``Success[T]`` is a generic container whose payload
# type is unconstrained, so a frozen ``Success`` can still hold a mutable payload. Its constructor is not
# changed during the move; port responses wrapped in it are records and checked here themselves.
UNCONSTRAINED_PAYLOAD_FIELDS = {("Success", "value")}


def _annotation_text(annotation: object) -> str:
    return annotation if isinstance(annotation, str) else repr(annotation)


def test_core_records_are_frozen_dataclasses_with_immutable_field_types():
    records = importlib.import_module(RECORDS)
    checked = 0
    for name in records.__all__:
        obj = getattr(records, name)
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


def test_only_the_documented_record_fields_have_an_unconstrained_payload_type():
    records = importlib.import_module(RECORDS)
    unconstrained = set()
    for name in records.__all__:
        obj = getattr(records, name)
        if not (isinstance(obj, type) and dataclasses.is_dataclass(obj)):
            continue
        hints = typing.get_type_hints(obj)
        for field in dataclasses.fields(obj):
            hint = hints.get(field.name, field.type)
            if isinstance(hint, typing.TypeVar) or hint in (typing.Any, object):
                unconstrained.add((name, field.name))
    assert unconstrained == UNCONSTRAINED_PAYLOAD_FIELDS


def test_no_dataclass_is_defined_in_core_outside_the_records_package():
    for name, path in _module_files(CORE_DIR).items():
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
