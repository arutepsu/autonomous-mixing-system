"""Tests for the full-band sample-peak and pooled-channel RMS level kernels (file-audio-analyzer design).

Inputs are small analytical float64 arrays whose levels follow from the definitions in closed form; they
are unit-test signals, not musical material or evaluation data. Tolerances cover ordinary float64 rounding
of ``log10`` and the mean, not a scientific threshold.
"""

import math

import numpy as np
import pytest

from autonomous_mixing.adapters.dsp_analysis.levels import (
    DBFS,
    EMPTY_TIME_SELECTION,
    SILENT_SIGNAL,
    sample_peak_dbfs,
    sample_rms_dbfs,
)
from autonomous_mixing.core.records.observation import MeasuredScalar, Unavailable

KERNELS = (sample_peak_dbfs, sample_rms_dbfs)
REL = 1e-12


def _db(value: float) -> float:
    return 20.0 * math.log10(value)


def _value(result) -> float:
    assert isinstance(result, MeasuredScalar) and result.unit == DBFS
    return result.value


def test_constant_mono_signal_has_equal_peak_and_rms():
    samples = np.full((5, 1), 0.5)
    assert _value(sample_peak_dbfs(samples)) == pytest.approx(_db(0.5), rel=REL)
    assert _value(sample_rms_dbfs(samples)) == pytest.approx(_db(0.5), rel=REL)


def test_full_scale_square_wave_is_zero_dbfs():
    samples = np.array([[1.0], [-1.0], [1.0], [-1.0]])
    assert _value(sample_peak_dbfs(samples)) == 0.0
    assert _value(sample_rms_dbfs(samples)) == 0.0


def test_rms_pools_frames_and_channels_analytically():
    # mean square = (1 + 0 + 0.25 + 0.25) / 4 = 0.375
    samples = np.array([[1.0, 0.0], [0.5, -0.5]])
    assert _value(sample_peak_dbfs(samples)) == 0.0
    assert _value(sample_rms_dbfs(samples)) == pytest.approx(10.0 * math.log10(0.375), rel=REL)


def test_antiphase_stereo_does_not_cancel():
    samples = np.array([[0.5, -0.5], [-0.5, 0.5], [0.25, -0.25]])
    expected = 10.0 * math.log10((2 * 0.25 + 2 * 0.25 + 2 * 0.0625) / 6)
    assert _value(sample_rms_dbfs(samples)) == pytest.approx(expected, rel=REL)
    assert _value(sample_peak_dbfs(samples)) == pytest.approx(_db(0.5), rel=REL)


@pytest.mark.parametrize("kernel", KERNELS)
@pytest.mark.parametrize("channels", [1, 2])
def test_exact_silence_is_unavailable_not_a_floor(kernel, channels):
    samples = np.zeros((4, channels))
    samples[1, 0] = -0.0
    assert kernel(samples) == Unavailable(SILENT_SIGNAL)


@pytest.mark.parametrize("kernel", KERNELS)
def test_an_empty_selection_is_unavailable(kernel):
    assert kernel(np.zeros((0, 2))) == Unavailable(EMPTY_TIME_SELECTION)


def test_subnormal_samples_give_finite_levels():
    tiny = 5e-324  # smallest positive subnormal float64
    samples = np.array([[tiny, -tiny], [tiny, 0.0]])
    assert _value(sample_peak_dbfs(samples)) == pytest.approx(_db(tiny), rel=REL)
    assert _value(sample_rms_dbfs(samples)) == pytest.approx(_db(tiny) + 10.0 * math.log10(0.75), rel=REL)


def test_large_finite_samples_do_not_overflow_the_rms():
    big = 1e300  # big ** 2 overflows float64; peak scaling avoids forming it
    samples = np.array([[big], [0.0]])
    with np.errstate(over="raise"):
        assert _value(sample_rms_dbfs(samples)) == pytest.approx(_db(big) + 10.0 * math.log10(0.5), rel=REL)
    assert _value(sample_peak_dbfs(np.array([[np.finfo(np.float64).max]]))) == pytest.approx(
        _db(float(np.finfo(np.float64).max)), rel=REL
    )


@pytest.mark.parametrize(
    "samples, expected",
    [
        # squaring underflows: (1e-200) ** 2 is below the smallest float64
        (np.array([[1.0], [1e-200]]), 10.0 * math.log10(0.5)),
        # scaling underflows: 1e-300 / 1e300 is below the smallest float64
        (np.array([[1e300, 1e-300]]), _db(1e300) + 10.0 * math.log10(0.5)),
    ],
)
def test_rms_ignores_underflow_of_negligible_terms_under_a_raising_policy(samples, expected):
    with np.errstate(under="raise"):
        assert _value(sample_rms_dbfs(samples)) == pytest.approx(expected, rel=REL)
        assert np.geterr()["under"] == "raise"  # the caller's policy is restored


def test_values_above_full_scale_give_positive_dbfs_without_a_cap():
    samples = np.array([[4.0, -4.0]])
    assert _value(sample_peak_dbfs(samples)) == pytest.approx(_db(4.0), rel=REL)
    assert _value(sample_rms_dbfs(samples)) == pytest.approx(_db(4.0), rel=REL)


@pytest.mark.parametrize("kernel", KERNELS)
def test_inputs_are_left_unchanged(kernel):
    samples = np.array([[0.5, -0.25], [0.125, 1.0]])
    copy = samples.copy()
    kernel(samples)
    np.testing.assert_array_equal(samples, copy)
    samples.setflags(write=False)  # read-only arrays from the WAV reader are accepted
    kernel(samples)


@pytest.mark.parametrize("kernel", KERNELS)
@pytest.mark.parametrize(
    "samples",
    [
        [[0.5]],  # not an array
        np.array([[0.5]], dtype=np.float32),
        np.array([0.5, 0.25]),  # one-dimensional
        np.zeros((2, 3)),  # three channels
        np.zeros((2, 0)),
        np.array([[0.5], [np.nan]]),
        np.array([[np.inf, 0.0]]),
    ],
)
def test_the_input_contract_is_validated(kernel, samples):
    with pytest.raises(ValueError):
        kernel(samples)
