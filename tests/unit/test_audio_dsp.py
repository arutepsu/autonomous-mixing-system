"""Tests for the in-memory fixed-input gain and ordered summation kernels.

Small analytical arrays only: these check the ProcessingSpec numerical rules (float64, dB amplitude
gain from the fixed input, sequential summation, level preservation). No audio files are read or
written, and no perceptual or end-to-end claim is made.
"""

import warnings

import numpy as np
import pytest

from autonomous_mixing.adapters.rendering.dsp import apply_absolute_gain, sum_stems

# Non-default caller setting: every floating-point condition raises unless the kernel handles it locally.
HOSTILE = {"divide": "raise", "over": "raise", "under": "raise", "invalid": "raise"}


@pytest.fixture
def hostile_errstate():
    previous = np.seterr(**HOSTILE)
    try:
        yield np.geterr()
    finally:
        np.seterr(**previous)


def _a(rows) -> np.ndarray:
    return np.array(rows, dtype=np.float64)


def _snapshot(*arrays: np.ndarray) -> list[np.ndarray]:
    return [array.copy() for array in arrays]


def _assert_owned(result: np.ndarray, *inputs: np.ndarray) -> None:
    assert type(result) is np.ndarray and result.dtype == np.float64
    for array in inputs:
        assert not np.shares_memory(result, array)


# --- gain: numerical behaviour ---------------------------------------------------------


@pytest.mark.parametrize("rows", [[[0.5]], [[0.5, -0.25]], [[0.1], [-0.2], [0.3]], [[0.1, 0.2], [-0.3, -0.4]]])
def test_unity_gain_returns_equal_independently_owned_copy(rows):
    samples = _a(rows)
    result = apply_absolute_gain(samples, 0.0)
    assert result.shape == samples.shape
    np.testing.assert_array_equal(result, samples)
    _assert_owned(result, samples)


def test_integer_zero_gain_is_unity():
    samples = _a([[0.5, -0.5]])
    np.testing.assert_array_equal(apply_absolute_gain(samples, 0), samples)


@pytest.mark.parametrize("gain_db", [-12.0, 0.0, 20.0])
def test_silence_stays_silent(gain_db):
    samples = np.zeros((3, 2))
    np.testing.assert_array_equal(apply_absolute_gain(samples, gain_db), samples)


def test_plus_twenty_db_multiplies_by_ten():
    # 10 ** (20 / 20) == 10.0 exactly; both products are exact in float64.
    result = apply_absolute_gain(_a([[0.25, -0.5]]), 20)
    np.testing.assert_array_equal(result, _a([[2.5, -5.0]]))


def test_attenuation_matches_db_amplitude_rule_within_tolerance():
    # 10 ** (-6 / 20) = 0.501187233627272...; tolerance covers one or two float64 rounding steps.
    result = apply_absolute_gain(_a([[1.0], [-0.5]]), -6.0)
    np.testing.assert_allclose(result, _a([[0.5011872336272722], [-0.2505936168136361]]), rtol=1e-12, atol=0)


def test_repeated_gain_on_the_same_fixed_input_does_not_compound():
    samples = _a([[0.8, -0.4]])
    first = apply_absolute_gain(samples, -3.0)
    second = apply_absolute_gain(samples, -3.0)
    np.testing.assert_array_equal(first, second)
    np.testing.assert_allclose(first, samples * 10 ** (-3.0 / 20), rtol=1e-15, atol=0)
    assert not np.allclose(second, samples * 10 ** (-6.0 / 20), rtol=1e-6, atol=0)
    assert not np.shares_memory(first, second)


def test_gain_preserves_finite_values_outside_full_scale():
    result = apply_absolute_gain(_a([[3.0, -4.0]]), 20)
    np.testing.assert_array_equal(result, _a([[30.0, -40.0]]))


# --- gain: representability ------------------------------------------------------------


@pytest.mark.parametrize("gain_db", [7000.0, 10**305, 10**400, -(10**400)])
def test_unrepresentable_gain_factor_is_rejected(gain_db):
    with pytest.raises(ValueError):
        apply_absolute_gain(_a([[0.5]]), gain_db)


@pytest.mark.parametrize("gain_db", [-7000.0, -6600])
def test_gain_factor_rounding_to_zero_is_rejected_even_for_silence(gain_db):
    with pytest.raises(ValueError):
        apply_absolute_gain(np.zeros((1, 1)), gain_db)
    with pytest.raises(ValueError):
        apply_absolute_gain(_a([[0.5]]), gain_db)


def test_positive_subnormal_gain_factor_is_permitted(hostile_errstate):
    factor = 10 ** (-6300 / 20)  # about 1e-315, below the smallest normal float64
    assert 0 < factor < np.finfo(np.float64).tiny
    result = apply_absolute_gain(_a([[1.0, -1.0]]), -6300)
    np.testing.assert_array_equal(result, _a([[factor, -factor]]))
    assert np.geterr() == hostile_errstate


def test_sample_underflow_to_subnormal_or_zero_is_permitted(hostile_errstate):
    tiniest = 5e-324  # smallest positive subnormal float64
    result = apply_absolute_gain(_a([[1e-300, 0.0], [tiniest, -tiniest]]), -200)
    assert 0 < result[0, 0] < np.finfo(np.float64).tiny
    assert result[0, 1] == 0.0 and result[1, 0] == 0.0 and result[1, 1] == 0.0
    assert np.geterr() == hostile_errstate


@pytest.mark.parametrize("rows", [[[1e300]], [[0.0, -1e300]]])
def test_product_overflow_is_rejected(rows):
    with pytest.raises(ValueError):
        apply_absolute_gain(_a(rows), 200.0)


# --- gain: gain value contract ---------------------------------------------------------


@pytest.mark.parametrize(
    "gain_db",
    [
        True,
        False,
        "0",
        1j,
        None,
        float("nan"),
        float("inf"),
        float("-inf"),
        np.float64("nan"),
        np.array(0.0),
        np.array([0.0]),
        np.float32(0.0),
        np.int64(0),
        np.bool_(True),
    ],
    ids=repr,
)
def test_invalid_gain_values_are_rejected(gain_db):
    with pytest.raises(ValueError):
        apply_absolute_gain(_a([[0.5]]), gain_db)


@pytest.mark.parametrize(
    "gain_db",
    [
        np.float64(20.0),
        np.float64(0.0),
        np.float32(0.0),
        np.float16(0.0),
        np.longdouble(0.0),
        np.int64(0),
        np.int32(0),
        np.uint8(0),
        np.bool_(False),
    ],
    ids=lambda value: type(value).__name__,
)
def test_numpy_scalar_gains_are_rejected_including_float64(gain_db):
    samples = _a([[0.25, -0.5]])
    before = samples.copy()
    with pytest.raises(ValueError):
        apply_absolute_gain(samples, gain_db)
    np.testing.assert_array_equal(samples, before)


class _PlainInt(int):
    pass


class _PlainFloat(float):
    pass


class _ZeroDividingInt(int):
    """Would turn any gain into unity (10 ** 0.0) if its division were ever used."""

    calls: list[str] = []

    def __truediv__(self, other):
        type(self).calls.append("__truediv__")
        return 0.0


class _ConvertingFloat(float):
    calls: list[str] = []

    def __float__(self):
        type(self).calls.append("__float__")
        return 0.0


@pytest.mark.parametrize(
    "gain_db",
    [_PlainInt(0), _PlainInt(20), _PlainFloat(0.0), _PlainFloat(20.0)],
    ids=["int-0", "int-20", "float-0", "float-20"],
)
def test_plain_int_and_float_subclasses_are_rejected(gain_db):
    samples = _a([[0.25, -0.5]])
    before = samples.copy()
    with pytest.raises(ValueError):
        apply_absolute_gain(samples, gain_db)
    np.testing.assert_array_equal(samples, before)


@pytest.mark.parametrize(
    "subclass, value", [(_ZeroDividingInt, 7000), (_ConvertingFloat, 20.0)], ids=["truediv-int", "float-float"]
)
def test_rejected_subclasses_never_have_their_overrides_invoked(subclass, value):
    subclass.calls.clear()
    gain_db = subclass(value)
    samples = _a([[0.25, -0.5]])
    before = samples.copy()
    with pytest.raises(ValueError):
        apply_absolute_gain(samples, gain_db)
    assert subclass.calls == []
    np.testing.assert_array_equal(samples, before)


# --- sample array contract (shared by both operations) ---------------------------------


class _Subclass(np.ndarray):
    pass


NON_NATIVE = np.dtype(np.float64).newbyteorder()

with warnings.catch_warnings():
    warnings.simplefilter("ignore", PendingDeprecationWarning)  # np.matrix is the 2-D subclass case
    MATRIX = np.asmatrix(np.zeros((1, 1)))

INVALID_SAMPLES = [
    pytest.param([[0.5]], id="list"),
    pytest.param(((0.5,),), id="tuple"),
    pytest.param(0.5, id="scalar"),
    pytest.param(np.float64(0.5), id="numpy-scalar"),
    pytest.param(np.zeros((1, 1)).view(_Subclass), id="subclass"),
    pytest.param(MATRIX, id="matrix"),
    pytest.param(np.ma.zeros((1, 1)), id="masked"),
    pytest.param(np.zeros((1, 1), dtype=np.float32), id="float32"),
    pytest.param(np.zeros((1, 1), dtype=np.int64), id="int64"),
    pytest.param(np.zeros((1, 1), dtype=np.complex128), id="complex128"),
    pytest.param(np.zeros((1, 1), dtype=bool), id="bool"),
    pytest.param(np.zeros((1, 1), dtype=object), id="object"),
    pytest.param(np.zeros((1, 1), dtype=NON_NATIVE), id="non-native-endian"),
    pytest.param(np.zeros(()), id="0-d"),
    pytest.param(np.zeros(4), id="1-d"),
    pytest.param(np.zeros((2, 1, 1)), id="3-d"),
    pytest.param(np.zeros((1, 0)), id="no-channels"),
    pytest.param(np.zeros((1, 3)), id="three-channels"),
    pytest.param(np.zeros((0, 1)), id="no-frames"),
    pytest.param(np.zeros((0, 2)), id="no-frames-stereo"),
    pytest.param(_a([[0.0, np.nan]]), id="nan"),
    pytest.param(_a([[np.inf]]), id="inf"),
    pytest.param(_a([[0.0], [-np.inf]]), id="-inf"),
]


@pytest.mark.parametrize("samples", INVALID_SAMPLES)
def test_gain_rejects_invalid_sample_arrays(samples):
    with pytest.raises(ValueError):
        apply_absolute_gain(samples, 0.0)


@pytest.mark.parametrize("samples", INVALID_SAMPLES)
def test_sum_rejects_invalid_sample_arrays_in_any_position(samples):
    with pytest.raises(ValueError):
        sum_stems((samples,))
    with pytest.raises(ValueError):
        sum_stems((np.zeros((1, 1)), samples))


# --- summation -------------------------------------------------------------------------


def test_single_stem_sum_returns_equal_independently_owned_copy():
    stem = _a([[0.5, -0.25], [0.125, 2.0]])
    result = sum_stems((stem,))
    np.testing.assert_array_equal(result, stem)
    _assert_owned(result, stem)


@pytest.mark.parametrize(
    "stems, expected",
    [
        ((_a([[0.25]]), _a([[0.5]])), _a([[0.75]])),
        ((_a([[0.5, -0.5]]), _a([[0.25, 0.25]]), _a([[-1.0, 2.0]])), _a([[-0.25, 1.75]])),
    ],
)
def test_sum_adds_mono_and_stereo_stems(stems, expected):
    result = sum_stems(stems)
    np.testing.assert_array_equal(result, expected)
    _assert_owned(result, *stems)


@pytest.mark.parametrize(
    "values, expected",
    [((2.0**53, 1.0, -(2.0**53)), 0.0), ((2.0**53, -(2.0**53), 1.0), 1.0)],
    ids=["absorbed-before-cancel", "cancel-before-add"],
)
def test_sum_is_sequential_in_tuple_order(values, expected):
    result = sum_stems(tuple(_a([[v]]) for v in values))
    assert result.shape == (1, 1) and result.dtype == np.float64
    assert result[0, 0] == expected


def test_finite_cancellation_and_over_range_values_are_preserved():
    result = sum_stems((_a([[3.0, 0.75]]), _a([[-3.0, 0.75]]), _a([[0.0, 1.5]])))
    np.testing.assert_array_equal(result, _a([[0.0, 3.0]]))


def test_overflowing_intermediate_sum_is_rejected_even_if_later_cancellation_is_finite(hostile_errstate):
    big = _a([[1e308]])
    with pytest.raises(ValueError):
        sum_stems((big, big, -big))
    assert np.geterr() == hostile_errstate


def test_same_array_may_appear_twice():
    stem = _a([[0.25, -1.5]])
    before = stem.copy()
    result = sum_stems((stem, stem))
    np.testing.assert_array_equal(result, _a([[0.5, -3.0]]))
    np.testing.assert_array_equal(stem, before)
    _assert_owned(result, stem)


@pytest.mark.parametrize(
    "stems",
    [(), [], [np.zeros((1, 1))], np.zeros((1, 1)), (s for s in (np.zeros((1, 1)),)), None],
    ids=["empty-tuple", "empty-list", "list", "bare-array", "generator", "none"],
)
def test_sum_requires_a_nonempty_tuple(stems):
    with pytest.raises(ValueError):
        sum_stems(stems)


def test_sum_rejects_tuple_subclasses():
    class Stems(tuple):
        pass

    with pytest.raises(ValueError):
        sum_stems(Stems((np.zeros((1, 1)),)))


@pytest.mark.parametrize(
    "shapes",
    [((1, 1), (1, 2)), ((2, 1), (1, 1)), ((2, 2), (2, 2), (3, 2))],
    ids=["mono-stereo", "frames", "third-stem"],
)
def test_sum_rejects_mismatched_shapes(shapes):
    with pytest.raises(ValueError):
        sum_stems(tuple(np.zeros(shape) for shape in shapes))


# --- memory layouts, immutability and caller settings ----------------------------------


LAYOUTS = ["readonly", "row-slice", "column-slice", "negative-stride"]


def _layout(name: str) -> tuple[np.ndarray, np.ndarray]:
    """Return (complete backing array, view passed to the kernel) for one valid but unusual layout."""
    backing = _a([[0.5, -0.25], [0.75, 0.125], [-1.5, 2.0], [0.0625, -0.0625]])
    if name == "readonly":
        backing.setflags(write=False)
        return backing, backing
    if name == "row-slice":
        return backing, backing[::2]
    if name == "column-slice":
        return backing, backing[:, 1:]
    return backing, backing[::-1, ::-1]


@pytest.mark.parametrize("name", LAYOUTS)
def test_gain_accepts_valid_layouts_without_touching_backing_storage(name):
    backing, samples = _layout(name)
    (before,) = _snapshot(backing)
    flags = (samples.flags.writeable, backing.flags.writeable)
    result = apply_absolute_gain(samples, 20)
    np.testing.assert_array_equal(result, samples * 10.0)
    np.testing.assert_array_equal(backing, before)
    assert (samples.flags.writeable, backing.flags.writeable) == flags
    _assert_owned(result, samples, backing)
    with pytest.raises(ValueError):
        apply_absolute_gain(samples, 7000.0)
    np.testing.assert_array_equal(backing, before)


@pytest.mark.parametrize("name", LAYOUTS)
def test_sum_accepts_valid_layouts_without_touching_backing_storage(name):
    backing, samples = _layout(name)
    (before,) = _snapshot(backing)
    result = sum_stems((samples, samples))
    np.testing.assert_array_equal(result, samples * 2.0)
    np.testing.assert_array_equal(backing, before)
    _assert_owned(result, samples, backing)


def test_inputs_are_unchanged_after_failures():
    big = _a([[1e308, 0.5]])
    other = _a([[1e308, 0.25]])
    before = _snapshot(big, other)
    with pytest.raises(ValueError):
        apply_absolute_gain(big, 20)
    with pytest.raises(ValueError):
        sum_stems((big, other, -other))
    with pytest.raises(ValueError):
        sum_stems((big, np.zeros((2, 2))))
    for array, snapshot in zip((big, other), before):
        np.testing.assert_array_equal(array, snapshot)


def test_caller_numpy_error_settings_are_restored_after_success_and_failure(hostile_errstate):
    apply_absolute_gain(_a([[1e-300]]), -200)
    sum_stems((_a([[1.0]]), _a([[-1.0]])))
    assert np.geterr() == hostile_errstate
    for call in (
        lambda: apply_absolute_gain(_a([[1e300]]), 200),
        lambda: apply_absolute_gain(_a([[1.0]]), -7000.0),
        lambda: sum_stems((_a([[1e308]]), _a([[1e308]]))),
        lambda: sum_stems((_a([[np.nan]]),)),
    ):
        with pytest.raises(ValueError):
            call()
        assert np.geterr() == hostile_errstate


def test_default_caller_settings_are_restored_too():
    previous = np.geterr()
    apply_absolute_gain(_a([[1e-300]]), -200)
    with pytest.raises(ValueError):
        sum_stems((_a([[1e308]]), _a([[1e308]])))
    assert np.geterr() == previous
