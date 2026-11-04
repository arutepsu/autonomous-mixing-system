"""Focused tests for the versioned full-mix recipe (domain project component)."""

import dataclasses

import pytest

from autonomous_mixing.core.records.identifiers import TrackId, VersionRef
from autonomous_mixing.core.records.mix import MixRecipe, SummingPolicy

REF = VersionRef("full-mix-recipe", "1")


def _recipe(*tracks: str, policy=SummingPolicy.SAMPLE_SUM) -> MixRecipe:
    return MixRecipe(REF, tuple(TrackId(t) for t in tracks), policy)


def test_summing_policy_has_exactly_sample_sum():
    assert [policy.name for policy in SummingPolicy] == ["SAMPLE_SUM"]


def test_recipe_keeps_logical_contributors_in_their_given_order():
    recipe = _recipe("t3", "t1", "t2")
    assert recipe.track_ids == (TrackId("t3"), TrackId("t1"), TrackId("t2"))
    assert recipe.summing_policy is SummingPolicy.SAMPLE_SUM
    assert recipe.reference == REF
    assert {f.name for f in dataclasses.fields(MixRecipe)} == {"reference", "track_ids", "summing_policy"}


@pytest.mark.parametrize(
    "build",
    [
        lambda: _recipe(),
        lambda: _recipe("t1", "t1"),
        lambda: MixRecipe(REF, [TrackId("t1")], SummingPolicy.SAMPLE_SUM),
        lambda: MixRecipe(REF, ("t1",), SummingPolicy.SAMPLE_SUM),
        lambda: _recipe("t1", policy="SAMPLE_SUM"),
        lambda: MixRecipe("full-mix-recipe@1", (TrackId("t1"),), SummingPolicy.SAMPLE_SUM),
    ],
    ids=["empty", "duplicate", "mutable", "untyped-track", "untyped-policy", "untyped-reference"],
)
def test_recipe_rejects_invalid_contributors_and_types(build):
    with pytest.raises(ValueError):
        build()


def test_recipe_has_no_implied_defaults():
    with pytest.raises(TypeError):
        MixRecipe(REF, (TrackId("t1"),))  # no default summing policy
    with pytest.raises(dataclasses.FrozenInstanceError):
        _recipe("t1").track_ids = ()
