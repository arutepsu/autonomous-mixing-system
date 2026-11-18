"""Tests for the dist10 long-window spectral kernel (front-end contract revision 3, §3.1; Task 3C).

Reference values are the Probe C capture in ``tests/fixtures/mpeg_psy_model2_dist10/expected_long_fft`` (dist10
float32 reference): window coefficients, windowed samples, pre-floor energy, energy and phase per natural bin.
Bounds are the fixture verifier's: pre-floor energy within 1e-6 of the call's peak bin energy, phase within 1e-4 rad
(wrapped) on bins above 1e-6 of the peak. They describe float32-reference vs float64-FFT arithmetic, not a
scientific threshold. Other inputs are small synthetic int16 windows (unit-test signals, not audio material).
"""

import json
import pathlib

import numpy as np
import pytest

from autonomous_mixing.adapters.dsp_analysis.framing import frame_long_blocks
from autonomous_mixing.adapters.dsp_analysis.long_spectrum import (
    CONSUMED_BINS,
    ENERGY_FLOOR,
    FLOOR_BINS,
    LONG_FFT_SIZE,
    PHASE_BINS,
    LongSpectrum,
    apply_long_energy_floor,
    apply_long_window,
    dist10_long_window,
    long_window_spectrum,
)

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "mpeg_psy_model2_dist10"
FFT = FIXTURE / "expected_long_fft"
ENERGY_BOUND = 1e-6  # times the call's peak bin energy (verify_fixture.py)
STRONG = 1e-6  # phase compared on bins above this fraction of the peak
PHASE_BOUND = 1e-4  # rad, wrapped
FLOOR32 = np.float32(0.0005)


def captured_window() -> np.ndarray:
    return np.array(json.loads((FFT / "window_long.json").read_text())["window"]).astype(np.float32)


def raw(name: str) -> np.ndarray:
    return np.frombuffer((FIXTURE / "inputs" / f"{name}.raw").read_bytes(), dtype="<i2").astype(np.int16)


def selected_calls() -> list[tuple[str, dict]]:
    out = []
    for path in sorted(FFT.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if "energy" in record:
                out.append((path.stem, record))
    return out


SELECTED = selected_calls()
IDS = [f"{name}-call{r['call']}" for name, r in SELECTED]


def f32(values) -> np.ndarray:
    return np.array(values).astype(np.float32)


def frames_of(name: str):
    return frame_long_blocks(raw(name), sample_rate_hz=44100)


def spectrum_of(name: str) -> LongSpectrum:
    return long_window_spectrum(frames_of(name).windows)


def captured_floor_mask(record: dict) -> np.ndarray:
    pre = f32(record["energy_prefloor"])[:CONSUMED_BINS]
    return (pre.astype(np.float64) <= 0.0005) & (np.arange(CONSUMED_BINS) < 512)


def floor_mask_report(name: str, call: int, kernel_pre, kernel_mask, captured_pre, captured_mask) -> str:
    lines = [f"{name} call {call}: floor masks differ"]
    for k in np.flatnonzero(kernel_mask != captured_mask):
        lines.append(
            f"  bin {k}: kernel floored={bool(kernel_mask[k])} pre={float(kernel_pre[k]):.9g} "
            f"(cutoff distance {float(kernel_pre[k]) - 0.0005:+.3g}); dist10 floored={bool(captured_mask[k])} "
            f"pre={float(captured_pre[k]):.9g} (cutoff distance {float(captured_pre[k]) - 0.0005:+.3g})"
        )
    return "\n".join(lines)


# --- constants and window ---------------------------------------------------------------------------


def test_constants():
    assert (LONG_FFT_SIZE, CONSUMED_BINS, FLOOR_BINS, PHASE_BINS) == (1024, 513, 512, 6)
    assert ENERGY_FLOOR == 0.0005


def test_window_equals_captured_coefficients_exactly():
    window = dist10_long_window()
    assert window.dtype == np.float32 and window.shape == (1024,)
    np.testing.assert_array_equal(window, captured_window())
    assert not window.flags.writeable


def test_caller_cannot_change_the_window_the_kernel_uses():
    # Regression (Codex review): the returned window is a fresh copy, so re-enabling writes on it is harmless.
    windows = frames_of("impulse_32767_at1983").windows
    before = long_window_spectrum(windows)
    leaked = dist10_long_window()
    leaked.flags.writeable = True
    leaked[:] = 0
    assert dist10_long_window() is not leaked
    np.testing.assert_array_equal(dist10_long_window(), captured_window())
    after = long_window_spectrum(windows)
    np.testing.assert_array_equal(after.energy_prefloor, before.energy_prefloor)
    expected = captured_window() * windows[3].astype(np.float32)
    np.testing.assert_array_equal(apply_long_window(windows[3:4])[0], expected)


def test_window_is_the_half_sample_offset_hann_not_a_standard_one():
    window = dist10_long_window()
    assert window[0] == window[1] > 0 and window[512] == window[513]
    i = np.arange(1024)
    assert not np.array_equal(window, (0.5 * (1 - np.cos(2 * np.pi * i / 1024))).astype(np.float32))


# --- windowed samples -------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "record"), SELECTED, ids=IDS)
def test_windowed_samples_equal_capture_exactly(name, record):
    windows = frames_of(name).windows
    windowed = apply_long_window(windows[record["call"] : record["call"] + 1])
    assert windowed.dtype == np.float32
    np.testing.assert_array_equal(windowed[0], f32(record["windowed_samples"]))


def test_windowed_samples_are_float32_products():
    windows = np.array([[-32768, 32767, 1, -1] * 256], dtype=np.int16)
    expected = dist10_long_window() * windows[0].astype(np.float32)
    np.testing.assert_array_equal(apply_long_window(windows)[0], expected)


# --- captured calls: energy, floor and phase ----------------------------------------------------------


@pytest.mark.parametrize(("name", "record"), SELECTED, ids=IDS)
def test_prefloor_energy_within_bound_of_peak(name, record):
    result = spectrum_of(name)
    captured = f32(record["energy_prefloor"])
    peak = float(captured.max())
    kernel = result.energy_prefloor[record["call"]]
    assert kernel.dtype == np.float32 and kernel.shape == (513,)
    error = np.abs(kernel.astype(np.float64) - captured[:513].astype(np.float64))
    if peak == 0:
        assert not kernel.any()
    else:
        assert error.max() <= ENERGY_BOUND * peak, f"max error {error.max():.3g} vs bound {ENERGY_BOUND * peak:.3g}"


@pytest.mark.parametrize(("name", "record"), SELECTED, ids=IDS)
def test_floor_mask_equals_probe_c(name, record):
    result = spectrum_of(name)
    call = record["call"]
    captured_mask = captured_floor_mask(record)
    kernel_mask = result.floored[call]
    report = floor_mask_report(
        name, call, result.energy_prefloor[call], kernel_mask, f32(record["energy_prefloor"]), captured_mask
    )
    assert np.array_equal(kernel_mask, captured_mask), report


@pytest.mark.parametrize(("name", "record"), SELECTED, ids=IDS)
def test_post_floor_energy_takes_the_floor_value_exactly_where_floored(name, record):
    result = spectrum_of(name)
    call = record["call"]
    energy, pre, mask = result.energy[call], result.energy_prefloor[call], result.floored[call]
    assert energy.dtype == np.float32
    assert np.all(energy[mask] == FLOOR32)
    np.testing.assert_array_equal(energy[~mask], pre[~mask])
    captured = f32(record["energy"])[:513]
    np.testing.assert_array_equal(energy[mask], captured[mask])  # the floor value matches dist10 bit for bit


@pytest.mark.parametrize(("name", "record"), SELECTED, ids=IDS)
def test_retained_phase_matches_capture_on_strong_bins(name, record):
    result = spectrum_of(name)
    call = record["call"]
    phase = result.phase[call]
    assert phase.dtype == np.float32 and phase.shape == (6,)
    captured_pre = f32(record["energy_prefloor"])
    captured_phi = f32(record["phi"])[:6].astype(np.float64)
    strong = captured_pre[:6] > STRONG * captured_pre.max()
    error = np.abs((phase.astype(np.float64) - captured_phi + np.pi) % (2 * np.pi) - np.pi)
    assert np.all(error[strong] <= PHASE_BOUND), error
    floored = result.floored[call, :6]
    assert np.all(phase[floored] == 0) and np.all(captured_phi[floored] == 0)


# --- separate fixture cases ---------------------------------------------------------------------------


def test_silence_floors_bins_0_to_511_and_keeps_nyquist_zero():
    result = spectrum_of("silence_12granules")
    assert not result.energy_prefloor.any()
    assert result.floored[:, :512].all() and not result.floored[:, 512].any()
    assert np.all(result.energy[:, :512] == FLOOR32) and not result.energy[:, 512].any()
    assert not result.phase.any()


def test_impulse_boundary_1983_in_call_3_and_1984_not():
    at_1983, at_1984 = spectrum_of("impulse_32767_at1983"), spectrum_of("impulse_32767_at1984")
    v = float(dist10_long_window()[1023] * np.float32(32767))  # window position 1023 of call 3 is sample 1983
    np.testing.assert_allclose(at_1983.energy_prefloor[3].astype(np.float64), v * v, rtol=1e-6)
    assert not at_1983.floored[3].any()
    assert not at_1984.energy_prefloor[3].any() and at_1984.floored[3, :512].all()
    assert not at_1984.floored[4].any()  # the 1984 impulse arrives in call 4 (window position 448)


def test_quiet_tone_floors_exactly_bins_169_and_170_at_call_6():
    result = spectrum_of("target_2k_a10.24")
    assert np.flatnonzero(result.floored[6]).tolist() == [169, 170]
    assert np.all(result.energy_prefloor[6, [169, 170]] < 0.0005)
    loud = spectrum_of("target_2k_a1024")
    assert not loud.floored[6].any()


def test_startup_calls_are_kept_in_order():
    frames = frames_of("masker_1k_a8192")
    result = long_window_spectrum(frames.windows)
    assert result.energy.shape == (frames.call.size, 513)
    one_by_one = [long_window_spectrum(frames.windows[k : k + 1]).energy_prefloor[0] for k in range(frames.call.size)]
    np.testing.assert_array_equal(result.energy_prefloor, np.stack(one_by_one))
    assert result.energy_prefloor[0].any()  # call 0 holds the first 256 samples behind 768 zeros


# --- synthetic cutoff, Nyquist and zero-energy cases -------------------------------------------------------


def impulse_window(position: int, amplitude: int) -> np.ndarray:
    window = np.zeros((1, 1024), dtype=np.int16)
    window[0, position] = amplitude
    return window


def test_energy_just_above_cutoff_is_not_floored():
    result = long_window_spectrum(impulse_window(12, 18))  # flat energy ~5.0162e-4 in every bin
    pre = result.energy_prefloor[0]
    assert np.all(pre.astype(np.float64) > 0.0005) and np.all(pre.astype(np.float64) < 0.000502)
    assert not result.floored.any()
    np.testing.assert_array_equal(result.energy, result.energy_prefloor)


def test_energy_just_below_cutoff_is_floored_except_nyquist():
    result = long_window_spectrum(impulse_window(35, 2))  # flat energy ~4.9830e-4 in every bin
    pre = result.energy_prefloor[0]
    assert np.all(pre.astype(np.float64) < 0.0005) and np.all(pre.astype(np.float64) > 0.000498)
    assert result.floored[0, :512].all() and not result.floored[0, 512]
    assert np.all(result.energy[0, :512] == FLOOR32)
    assert result.energy[0, 512] == pre[512] < 0.0005  # Nyquist keeps its sub-floor energy
    assert not result.phase.any()  # floored bins 0-5: retained phase set to zero


def test_tiny_nonzero_energy_is_floored_and_its_phase_zeroed():
    result = long_window_spectrum(impulse_window(5, 1))  # v = w[5] ~ 2.1e-4, energy ~ 4.5e-8
    assert result.floored[0, :512].all() and not result.floored[0, 512]
    assert result.energy[0, 512] > 0
    assert not result.phase.any()


def test_zero_energy_bins_are_floored_with_zero_phase_and_nyquist_stays_zero():
    result = long_window_spectrum(np.zeros((2, 1024), dtype=np.int16))
    assert not result.energy_prefloor.any()
    assert result.floored[:, :512].all() and not result.floored[:, 512].any()
    assert np.all(result.energy[:, 512] == 0)
    assert not result.phase.any()


def test_nyquist_is_never_floored_but_carries_energy():
    alternating = np.array([[1000, -1000] * 512], dtype=np.int16)
    result = long_window_spectrum(alternating)
    pre = result.energy_prefloor[0]
    assert pre[512] == pre.max() and not result.floored[0, 512]


def test_strong_low_bins_keep_their_phase():
    window = impulse_window(3, 30000)  # v = w[3] * 30000 ~ 0.2; energy ~0.04, phase -2 pi k 3 / 1024
    result = long_window_spectrum(window)
    assert not result.floored[0].any()
    expected = np.angle(np.exp(-2j * np.pi * np.arange(6) * 3 / 1024))
    np.testing.assert_allclose(result.phase[0].astype(np.float64), expected, atol=1e-6)


# --- floor function -------------------------------------------------------------------------------------


def test_floor_predicate_widens_float32_and_compares_with_0_0005():
    below = np.nextafter(FLOOR32, np.float32(0))  # 4.99999966e-4 <= 0.0005
    energies = np.zeros((1, 513), dtype=np.float32)
    energies[0, :4] = [below, FLOOR32, np.float32(0.0), np.float32(1.0)]
    energies[0, 512] = below
    floored_energy, mask = apply_long_energy_floor(energies)
    assert mask[0, :4].tolist() == [True, False, True, False]  # float32(0.0005) is above 0.0005 as a double
    assert not mask[0, 512]
    assert floored_energy[0, 0] == FLOOR32 and floored_energy[0, 1] == FLOOR32
    assert floored_energy[0, 512] == below
    assert floored_energy.dtype == np.float32


def test_floor_requires_float32_energies_on_513_bins():
    with pytest.raises(ValueError):
        apply_long_energy_floor(np.zeros((1, 513), dtype=np.float64))
    with pytest.raises(ValueError):
        apply_long_energy_floor(np.zeros((1, 512), dtype=np.float32))


# --- outputs, inputs and validation -------------------------------------------------------------------


def test_outputs_are_read_only_and_input_unchanged():
    windows = frames_of("target_2k_a1024").windows.copy()
    before = windows.copy()
    result = long_window_spectrum(windows)
    np.testing.assert_array_equal(windows, before)
    for array in (result.energy_prefloor, result.energy, result.phase, result.floored):
        assert not array.flags.writeable
        assert not np.shares_memory(array, windows)
    assert result.floored.dtype == np.bool_


def test_zero_calls():
    result = long_window_spectrum(np.zeros((0, 1024), dtype=np.int16))
    assert result.energy.shape == (0, 513) and result.phase.shape == (0, 6) and result.floored.shape == (0, 513)


def test_many_calls_match_single_calls_across_chunk_boundaries():
    rng = np.random.default_rng(7)
    windows = rng.integers(-32768, 32768, size=(600, 1024)).astype(np.int16)
    result = long_window_spectrum(windows)
    for k in (0, 255, 256, 511, 512, 599):
        single = long_window_spectrum(windows[k : k + 1])
        np.testing.assert_array_equal(result.energy_prefloor[k], single.energy_prefloor[0])
        np.testing.assert_array_equal(result.phase[k], single.phase[0])


def _extra_peak_bytes(function, argument, outputs) -> int:
    import tracemalloc

    tracemalloc.start()
    try:
        result = function(argument)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak - sum(array.nbytes for array in outputs(result))


def test_temporary_memory_does_not_grow_with_the_number_of_calls():
    # Regression (Codex review): the floor runs per chunk, so memory beyond the outputs is bounded by the chunk
    # size. A whole-array floor (float64 widening, mask and replacement) would add over 10 bytes per bin per call.
    rng = np.random.default_rng(3)
    small = rng.integers(-30000, 30000, size=(1000, 1024)).astype(np.int16)
    large = rng.integers(-30000, 30000, size=(4000, 1024)).astype(np.int16)
    long_window_spectrum(small[:3])  # warm up outside the measurement

    def spectrum_outputs(result):
        return (result.energy_prefloor, result.energy, result.floored, result.phase)

    growth = _extra_peak_bytes(long_window_spectrum, large, spectrum_outputs) - _extra_peak_bytes(
        long_window_spectrum, small, spectrum_outputs
    )
    assert growth < 1 << 20, growth  # 3,000 more calls: whole-array flooring would add about 15 MB

    def floor_outputs(result):
        return result

    small_pre = np.array(long_window_spectrum(small).energy_prefloor)
    large_pre = np.array(long_window_spectrum(large).energy_prefloor)
    growth = _extra_peak_bytes(apply_long_energy_floor, large_pre, floor_outputs) - _extra_peak_bytes(
        apply_long_energy_floor, small_pre, floor_outputs
    )
    assert growth < 1 << 20, growth


def test_noncontiguous_read_only_and_byte_swapped_windows():
    windows = frames_of("masker_1k_a8192").windows
    expected = long_window_spectrum(windows.copy())
    swapped = windows.astype(windows.dtype.newbyteorder())
    strided = np.repeat(windows, 2, axis=0)[::2]
    for variant in (windows, swapped, strided):
        result = long_window_spectrum(variant)
        np.testing.assert_array_equal(result.energy_prefloor, expected.energy_prefloor)


def test_equality_is_identity():
    a = long_window_spectrum(np.zeros((1, 1024), dtype=np.int16))
    b = long_window_spectrum(np.zeros((1, 1024), dtype=np.int16))
    assert a == a and a != b


@pytest.mark.parametrize(
    "windows",
    [
        [[0] * 1024],
        np.zeros((1, 1024), dtype=np.float32),
        np.zeros((1, 1024), dtype=np.int32),
        np.zeros((1, 1023), dtype=np.int16),
        np.zeros((1024,), dtype=np.int16),
        np.zeros((1, 1, 1024), dtype=np.int16),
        np.ma.masked_array(np.zeros((1, 1024), dtype=np.int16)),
    ],
)
def test_rejects_invalid_windows(windows):
    with pytest.raises(ValueError):
        long_window_spectrum(windows)
    with pytest.raises(ValueError):
        apply_long_window(windows)
