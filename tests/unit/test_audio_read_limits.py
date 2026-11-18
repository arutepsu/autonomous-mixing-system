"""Tests for the shared read-limit record (plan 2026-11-10 shared-audio-read; ADR-028, Proposed).

The limits are resource bounds for one captured file and its decoded samples, not scientific thresholds.
Validation is the reader's existing exact-int rule; nothing touches the filesystem.
"""

import builtins
import dataclasses
import os
import sys

import numpy as np
import pytest

from autonomous_mixing.adapters.audio_io.limits import AudioReadLimits


class _IntSubclass(int):
    pass


def test_fields_are_exact_required_and_frozen():
    fields = dataclasses.fields(AudioReadLimits)
    assert [f.name for f in fields] == ["max_file_bytes", "max_decoded_bytes"]
    assert all(f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING for f in fields)
    limits = AudioReadLimits(1, 2)
    with pytest.raises(dataclasses.FrozenInstanceError):
        limits.max_file_bytes = 3
    with pytest.raises(TypeError):
        AudioReadLimits(1)


@pytest.mark.parametrize("value", [1, 2, sys.maxsize, 10**100], ids=["one", "two", "maxsize", "googol"])
def test_positive_builtin_ints_are_kept_unchanged(value):
    limits = AudioReadLimits(value, value)
    assert (limits.max_file_bytes, limits.max_decoded_bytes) == (value, value)
    assert type(limits.max_file_bytes) is int


@pytest.mark.parametrize(
    "value",
    [0, -1, -(10**100), True, False, 1.0, "1", None, np.int64(1), _IntSubclass(1)],
    ids=["zero", "negative", "large-negative", "true", "false", "float", "str", "none", "numpy-int", "int-subclass"],
)
@pytest.mark.parametrize("field", ["max_file_bytes", "max_decoded_bytes"])
def test_invalid_limits_are_rejected_with_the_reader_labels(field, value):
    values = {"max_file_bytes": 1, "max_decoded_bytes": 1, field: value}
    with pytest.raises(ValueError, match=f"^{field} must be a positive built-in int"):
        AudioReadLimits(**values)


def test_the_file_limit_is_checked_before_the_decoded_limit():
    with pytest.raises(ValueError, match="^max_file_bytes "):
        AudioReadLimits(0, 0)


def test_construction_performs_no_file_access(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("AudioReadLimits must not touch the filesystem")

    for name in ("stat", "lstat", "open", "scandir"):
        monkeypatch.setattr(os, name, forbidden)
    monkeypatch.setattr(builtins, "open", forbidden)
    assert AudioReadLimits(10, 20).max_decoded_bytes == 20
