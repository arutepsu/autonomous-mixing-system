"""Focused tests for the explicit numerical and encoding processing specification.

Configuration records only: no processing, audio or file encoding is performed or claimed.
"""

import dataclasses

import pytest

import autonomous_mixing.domain.project as project
from autonomous_mixing.domain.project import (
    GainRule,
    LevelPolicy,
    NumericalPrecision,
    OutputContainer,
    ProcessingSpec,
    SampleEncoding,
    SummationOrder,
    SummingPolicy,
)
from autonomous_mixing.domain.shared import VersionRef

REF = VersionRef("gain-mix-processing", "1")
FIELDS = {
    "reference": REF,
    "precision": NumericalPrecision.FLOAT64,
    "gain_rule": GainRule.DB_AMPLITUDE,
    "summation_order": SummationOrder.RECIPE_SEQUENTIAL,
    "level_policy": LevelPolicy.PRESERVE,
    "output_container": OutputContainer.WAV,
    "sample_encoding": SampleEncoding.FLOAT64,
}


def _spec(**overrides) -> ProcessingSpec:
    return ProcessingSpec(**{**FIELDS, **overrides})


@pytest.mark.parametrize(
    "enum, names",
    [
        (NumericalPrecision, ["FLOAT64"]),
        (GainRule, ["DB_AMPLITUDE"]),
        (SummationOrder, ["RECIPE_SEQUENTIAL"]),
        (LevelPolicy, ["PRESERVE"]),
        (OutputContainer, ["WAV"]),
        (SampleEncoding, ["FLOAT64"]),
    ],
)
def test_enums_have_exactly_the_initial_members(enum, names):
    assert [member.name for member in enum] == names


def test_valid_complete_specification():
    spec = _spec()
    assert {f.name for f in dataclasses.fields(ProcessingSpec)} == set(FIELDS)
    assert all(getattr(spec, name) == value for name, value in FIELDS.items())


def test_separately_constructed_equal_specifications_compare_equal_as_whole_values():
    assert _spec() == ProcessingSpec(**dict(FIELDS))
    assert _spec() != _spec(reference=VersionRef("gain-mix-processing", "2"))
    assert hash(_spec()) == hash(ProcessingSpec(**dict(FIELDS)))


@pytest.mark.parametrize(
    "field, value",
    [
        ("reference", "gain-mix-processing@1"),
        ("precision", "FLOAT64"),
        ("gain_rule", "DB_AMPLITUDE"),
        ("summation_order", "RECIPE_SEQUENTIAL"),
        ("level_policy", "PRESERVE"),
        ("output_container", "WAV"),
        ("sample_encoding", "FLOAT64"),
        ("precision", SampleEncoding.FLOAT64),  # a FLOAT64 of the wrong enum is not a precision
        ("sample_encoding", NumericalPrecision.FLOAT64),
        ("summation_order", SummingPolicy.SAMPLE_SUM),  # combination operation is not evaluation order
        ("gain_rule", None),
    ],
)
def test_every_field_requires_its_exact_type(field, value):
    with pytest.raises(ValueError):
        _spec(**{field: value})


@pytest.mark.parametrize("missing", sorted(FIELDS))
def test_every_field_is_required_without_defaults(missing):
    fields = {name: value for name, value in FIELDS.items() if name != missing}
    with pytest.raises(TypeError):
        ProcessingSpec(**fields)


def test_specification_is_frozen():
    spec = _spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.reference = VersionRef("other", "1")


def test_specification_is_owned_by_the_project_component():
    # Root compatibility-export identity is asserted in test_domain_component_compatibility.py,
    # the only test module allowed to import the root interface.
    for name in (
        "ProcessingSpec",
        "NumericalPrecision",
        "GainRule",
        "SummationOrder",
        "LevelPolicy",
        "OutputContainer",
        "SampleEncoding",
    ):
        obj = getattr(project, name)
        assert name in project.__all__
        assert obj.__module__ == "autonomous_mixing.core.records.mix", name  # moved in ADR-027 step M1
        assert obj is globals()[name]
