"""Tests for the directional masking-margin kernel (Ronan 2018, Eq. 7.12; directional-masking-margin plan, Task 1).

Inputs are small analytical float64 threshold and energy grids on a common (frames, bands) grid; they are
unit-test arrays, not psychoacoustic thresholds, audio or evaluation data. Analytical references come from
closed-form logarithms (Python ``math``, exact ``fractions`` where the ratio is not representable); the
tolerances cover float64 rounding of those references, not a scientific threshold.
"""

import math
from fractions import Fraction

import numpy as np
import pytest

from autonomous_mixing.adapters.dsp_analysis.masking import MaskingMarginGrid, directional_masking_margin_db

TINY = float(np.nextafter(0.0, 1.0))  # smallest positive subnormal, 2 ** -1074
HUGE = float(np.finfo(np.float64).max)
REL = 1e-12
DB_PER_LN = 10.0 / math.log(10.0)


def _ref_db(threshold: float, energy: float) -> float:
    """10 log10(T / E) from the exact rational ratio of the two float64 values."""
    ratio = Fraction(threshold) / Fraction(energy)
    excess = ratio - 1
    if abs(excess) < Fraction(1, 2):
        return DB_PER_LN * math.log1p(float(excess))
    exponent = ratio.numerator.bit_length() - ratio.denominator.bit_length()
    mantissa = float(ratio / Fraction(2) ** exponent)  # in [0.5, 2): representable without overflow
    return 10.0 * (math.log10(mantissa) + exponent * math.log10(2.0))


def margins(threshold, energy) -> MaskingMarginGrid:
    return directional_masking_margin_db(np.asarray(threshold, dtype=np.float64), np.asarray(energy, dtype=np.float64))


# --- analytical values ------------------------------------------------------------------------------


def test_a_non_square_grid_gives_signed_cellwise_margins():
    # two frames by three bands: equal, threshold twice energy, half, ten times, and two more equal cells
    threshold = np.array([[1.0, 2.0, 0.5], [10.0, 3.0, 7.0]])
    energy = np.array([[1.0, 1.0, 1.0], [1.0, 3.0, 7.0]])
    grid = directional_masking_margin_db(threshold, energy)
    assert grid.margin_db.shape == (2, 3) and grid.margin_db.dtype == np.float64
    assert grid.margin_db[0, 0] == 0.0 and grid.margin_db[1, 1] == 0.0 and grid.margin_db[1, 2] == 0.0
    assert grid.margin_db[0, 1] == pytest.approx(10.0 * math.log10(2.0), rel=REL) and grid.margin_db[0, 1] > 0
    assert grid.margin_db[0, 2] == pytest.approx(-10.0 * math.log10(2.0), rel=REL) and grid.margin_db[0, 2] < 0
    assert grid.margin_db[1, 0] == pytest.approx(10.0, rel=REL)
    assert not grid.zero_threshold.any() and not grid.zero_target_energy.any()


def test_the_input_roles_are_not_symmetric():
    threshold = np.array([[2.0, 10.0, 1.0]])
    energy = np.array([[1.0, 1.0, 4.0]])
    forward = directional_masking_margin_db(threshold, energy).margin_db
    reverse = directional_masking_margin_db(energy, threshold).margin_db
    np.testing.assert_allclose(forward, [[10 * math.log10(2), 10.0, -10 * math.log10(4)]], rtol=REL)
    np.testing.assert_allclose(reverse, -forward, rtol=REL)


def test_a_transposed_grid_is_not_broadcast():
    with pytest.raises(ValueError, match="shape"):
        directional_masking_margin_db(np.ones((2, 3)), np.ones((3, 2)))
    with pytest.raises(ValueError, match="shape"):
        directional_masking_margin_db(np.ones((2, 3)), np.ones((1, 3)))


@pytest.mark.parametrize("threshold, energy", [(TINY, HUGE), (HUGE, TINY)])
def test_the_extreme_float64_range_does_not_overflow_or_underflow(threshold, energy):
    with np.errstate(all="raise"):
        grid = margins([[threshold]], [[energy]])
    expected = _ref_db(threshold, energy)
    assert abs(expected) > 6000  # far outside any ratio representable as a float64
    assert grid.margin_db[0, 0] == pytest.approx(expected, rel=REL)


@pytest.mark.parametrize(
    "threshold, energy", [(np.nextafter(1e300, np.inf), 1e300), (1e300, np.nextafter(1e300, np.inf))]
)
def test_adjacent_values_near_1e300_keep_their_tiny_signed_margin(threshold, energy):
    grid = margins([[threshold]], [[energy]])
    expected = _ref_db(float(threshold), float(energy))
    assert 0 < abs(expected) < 1e-14  # lost entirely by a difference of two log10 values near 300
    assert grid.margin_db[0, 0] == pytest.approx(expected, rel=1e-9)
    assert math.copysign(1.0, grid.margin_db[0, 0]) == math.copysign(1.0, expected)


@pytest.mark.parametrize(
    "threshold, energy",
    [(1.5, 1.0), (1.0, 1.5), (1.7, 1.0), (1.0 + 2**-52, 1.0), (3 * TINY, 2 * TINY), (2.0, 1.0), (1.0, 2.0)],
)
def test_near_and_far_ratios_match_the_exact_reference(threshold, energy):
    assert margins([[threshold]], [[energy]]).margin_db[0, 0] == pytest.approx(_ref_db(threshold, energy), rel=REL)


@pytest.mark.parametrize("value", [TINY, 1e-300, 1.0, 1e300, HUGE])
def test_exact_equality_is_zero_db(value):
    margin = margins([[value]], [[value]]).margin_db[0, 0]
    assert margin == 0.0 and math.copysign(1.0, margin) == 1.0


# --- zero cells -------------------------------------------------------------------------------------


def test_each_zero_combination_has_its_own_mask_and_a_nan_margin():
    threshold = np.array([[0.0, 1.0, 0.0, 2.0]])
    energy = np.array([[1.0, 0.0, -0.0, 1.0]])
    grid = directional_masking_margin_db(threshold, energy)
    np.testing.assert_array_equal(grid.zero_threshold, [[True, False, True, False]])
    np.testing.assert_array_equal(grid.zero_target_energy, [[False, True, True, False]])
    assert grid.zero_threshold.dtype == np.bool_ and grid.zero_target_energy.dtype == np.bool_
    assert np.isnan(grid.margin_db[0, :3]).all()
    assert grid.margin_db[0, 3] == pytest.approx(10 * math.log10(2.0), rel=REL)


def test_an_all_zero_grid_is_valid_under_raising_error_settings():
    with np.errstate(all="raise"):
        grid = margins(np.zeros((2, 3)), np.zeros((2, 3)))
    assert np.isnan(grid.margin_db).all() and grid.zero_threshold.all() and grid.zero_target_energy.all()


def test_positive_cells_beside_zeros_are_evaluated_under_raising_error_settings():
    threshold = np.array([[0.0, TINY, HUGE, 1.0], [5.0, 0.0, 1e300, TINY]])
    energy = np.array([[1.0, HUGE, TINY, 0.0], [5.0, 0.0, np.nextafter(1e300, 0.0), TINY]])
    with np.errstate(all="raise"):
        grid = directional_masking_margin_db(threshold, energy)
    finite = ~(grid.zero_threshold | grid.zero_target_energy)
    np.testing.assert_array_equal(finite, [[False, True, True, False], [True, False, True, True]])
    assert np.isfinite(grid.margin_db[finite]).all() and np.isnan(grid.margin_db[~finite]).all()


# --- shapes, storage and inputs ---------------------------------------------------------------------


def test_zero_frames_give_an_empty_result_of_the_same_shape():
    grid = margins(np.empty((0, 4)), np.empty((0, 4)))
    for array, dtype in (
        (grid.margin_db, np.float64),
        (grid.zero_threshold, np.bool_),
        (grid.zero_target_energy, np.bool_),
    ):
        assert array.shape == (0, 4) and array.dtype == dtype


def test_read_only_and_noncontiguous_inputs_are_supported_and_left_unchanged():
    base_t = np.array([[2.0, 9.0, 0.0, 9.0], [1.0, 9.0, 4.0, 9.0], [10.0, 9.0, 3.0, 9.0]])
    base_e = np.asfortranarray(np.array([[1.0, 0.0], [1.0, 2.0], [1.0, 3.0]]))
    threshold, energy = base_t[:, ::2], base_e  # strided view and Fortran-ordered array
    assert not threshold.flags.c_contiguous and not energy.flags.c_contiguous
    threshold.setflags(write=False)
    energy.setflags(write=False)
    copies = (base_t.copy(), base_e.copy())
    grid = directional_masking_margin_db(threshold, energy)
    np.testing.assert_allclose(grid.margin_db[:, 0], [10 * math.log10(2), 0.0, 10.0], rtol=REL)
    assert grid.zero_threshold[0, 1] and grid.zero_target_energy[0, 1]
    assert grid.margin_db[2, 1] == 0.0
    np.testing.assert_array_equal(base_t, copies[0])
    np.testing.assert_array_equal(base_e, copies[1])


def test_result_arrays_are_fresh_and_read_only():
    threshold, energy = np.array([[2.0, 0.0]]), np.array([[1.0, 0.0]])
    grid = directional_masking_margin_db(threshold, energy)
    for array in (grid.margin_db, grid.zero_threshold, grid.zero_target_energy):
        assert not np.shares_memory(array, threshold) and not np.shares_memory(array, energy)
        assert not array.flags.writeable
    assert not np.shares_memory(grid.zero_threshold, grid.zero_target_energy)
    with pytest.raises(AttributeError):
        grid.margin_db = np.zeros((1, 2))  # frozen


VALID = np.ones((2, 3))


@pytest.mark.parametrize(
    "bad",
    [
        [[1.0, 2.0]],  # not an array
        np.ones((2, 3), dtype=np.float32),
        np.ones((2, 3), dtype=np.int64),
        np.ones(3),  # one-dimensional
        np.ones((2, 3, 1)),
        np.ones((2, 0)),  # zero bands
        np.array([[1.0, -1.0, 1.0], [1.0, 1.0, 1.0]]),
        np.array([[1.0, -1e-300, 1.0], [1.0, 1.0, 1.0]]),
        np.array([[1.0, np.nan, 1.0], [1.0, 1.0, 1.0]]),
        np.array([[1.0, np.inf, 1.0], [1.0, 1.0, 1.0]]),
        np.array([[1.0, -np.inf, 1.0], [1.0, 1.0, 1.0]]),
    ],
)
@pytest.mark.parametrize("role", ["threshold", "energy"])
def test_invalid_inputs_are_rejected_in_either_role(bad, role):
    args = (bad, VALID) if role == "threshold" else (VALID, bad)
    with pytest.raises(ValueError):
        directional_masking_margin_db(*args)


@pytest.mark.parametrize("invalid", [-1.0, np.nan, np.inf])
@pytest.mark.parametrize("role", ["threshold", "energy"])
def test_invalid_values_paired_with_a_zero_are_still_rejected(invalid, role):
    bad, zero = np.array([[invalid]]), np.array([[0.0]])
    args = (bad, zero) if role == "threshold" else (zero, bad)
    with np.errstate(all="raise"), pytest.raises(ValueError):
        directional_masking_margin_db(*args)


def test_zero_frames_with_mismatched_bands_are_rejected():
    with pytest.raises(ValueError, match="shape"):
        directional_masking_margin_db(np.empty((0, 2)), np.empty((0, 3)))


# --- ndarray subclasses -----------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["threshold", "energy"])
@pytest.mark.parametrize(
    "masked",
    [
        np.ma.array([[1.0, np.nan]], mask=[[False, True]]),  # a hidden NaN that masked reductions would skip
        np.ma.array([[1.0, 2.0]], mask=[[False, False]]),  # even fully valid masked arrays
    ],
)
def test_masked_arrays_are_rejected_in_either_role(masked, role):
    other = np.ones((1, 2))
    args = (masked, other) if role == "threshold" else (other, masked)
    with np.errstate(all="raise"), pytest.raises(ValueError, match="masked"):
        directional_masking_margin_db(*args)


@pytest.mark.filterwarnings("ignore:the matrix subclass:PendingDeprecationWarning")  # raised by np.matrix itself
@pytest.mark.parametrize("role", ["threshold", "energy"])
def test_a_valid_matrix_is_normalized_to_a_plain_array(role):
    matrix = np.matrix([[2.0, 1.0, 0.0], [10.0, 4.0, 5.0]])
    plain = np.array([[1.0, 1.0, 3.0], [1.0, 1.0, 5.0]])
    threshold, energy = (matrix, plain) if role == "threshold" else (plain, matrix)
    grid = directional_masking_margin_db(threshold, energy)
    expected = margins(np.asarray(threshold), np.asarray(energy))
    for actual, reference in (
        (grid.margin_db, expected.margin_db),
        (grid.zero_threshold, expected.zero_threshold),
        (grid.zero_target_energy, expected.zero_target_energy),
    ):
        assert type(actual) is np.ndarray and actual.shape == (2, 3)
        np.testing.assert_array_equal(actual, reference)
    sign = 1.0 if role == "threshold" else -1.0
    assert grid.margin_db[0, 0] == pytest.approx(sign * 10 * math.log10(2.0), rel=REL)
    assert grid.margin_db[1, 2] == 0.0


def test_a_read_only_noncontiguous_subclass_view_is_supported():
    class Tagged(np.ndarray):
        pass

    base = np.array([[2.0, 9.0, 1.0, 9.0], [10.0, 9.0, 4.0, 9.0]]).view(Tagged)
    threshold = base[:, ::2]
    threshold.setflags(write=False)
    assert isinstance(threshold, Tagged) and not threshold.flags.c_contiguous
    grid = directional_masking_margin_db(threshold, np.ones((2, 2)))
    np.testing.assert_allclose(grid.margin_db, [[10 * math.log10(2), 0.0], [10.0, 10 * math.log10(4)]], rtol=REL)
    assert type(grid.margin_db) is np.ndarray
