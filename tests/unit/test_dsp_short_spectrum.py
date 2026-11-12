"""Tests for the dist10 short-window spectral kernel (front-end contract §3.2; Task 3E).

Reference values are the Probe D capture in ``tests/fixtures/mpeg_psy_model2_dist10/expected_short`` (dist10
float32 reference): short window coefficients, the three windowed short blocks per call, pre-floor energy,
energy and phase on natural short bins 0-128. Bounds are the fixture verifier's: pre-floor energy within 1e-6 of
the block's peak bin energy, phase within 1e-4 rad (wrapped) on unfloored bins above 1e-6 of that peak. They
describe float32-reference vs float64-FFT arithmetic, not a scientific threshold. Other inputs are small synthetic
int16 windows (unit-test signals, not audio material or evaluation data).
"""

import json
import pathlib
import tracemalloc

import numpy as np
import pytest

from autonomous_mixing.adapters.dsp_analysis.framing import frame_long_blocks
from autonomous_mixing.adapters.dsp_analysis.short_spectrum import (
    SHORT_BLOCK_OFFSETS,
    SHORT_BLOCKS,
    SHORT_CONSUMED_BINS,
    SHORT_ENERGY_FLOOR,
    SHORT_FFT_SIZE,
    SHORT_FLOOR_BINS,
    ShortSpectrum,
    apply_short_window,
    dist10_short_window,
    short_window_spectrum,
)

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "mpeg_psy_model2_dist10"
SHORT = FIXTURE / "expected_short"
ENERGY_BOUND = 1e-6  # times the block's peak bin energy (verify_fixture.py)
STRONG = 1e-6
PHASE_BOUND = 1e-4  # rad, wrapped
FLOOR32 = np.float32(0.0005)


def captured_window() -> np.ndarray:
    return np.array(json.loads((SHORT / "window_short.json").read_text())["window_s"]).astype(np.float32)


def raw(name: str) -> np.ndarray:
    return np.frombuffer((FIXTURE / "inputs" / f"{name}.raw").read_bytes(), dtype="<i2").astype(np.int16)


def selected_calls() -> list[tuple[str, dict]]:
    out = []
    for path in sorted(SHORT.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if "short_energy" in record:
                out.append((path.stem, record))
    return out


SELECTED = selected_calls()
IDS = [f"{name}-call{r['call']}" for name, r in SELECTED]


def f32(values) -> np.ndarray:
    return np.array(values).astype(np.float32)


def windows_of(name: str) -> np.ndarray:
    return frame_long_blocks(raw(name), sample_rate_hz=44100).windows


def spectrum_of(name: str) -> ShortSpectrum:
    return short_window_spectrum(windows_of(name))


def captured_floor_mask(record: dict) -> np.ndarray:
    pre = f32(record["short_energy_prefloor"])
    return (pre.astype(np.float64) <= 0.0005) & (np.arange(SHORT_CONSUMED_BINS) < 128)


def floor_mask_report(name, call, kernel_pre, kernel_mask, captured_pre, captured_mask) -> str:
    lines = [f"{name} call {call}: short floor masks differ"]
    for block, k in zip(*np.nonzero(kernel_mask != captured_mask), strict=True):
        lines.append(
            f"  block {block} bin {k}: kernel floored={bool(kernel_mask[block, k])} "
            f"pre={float(kernel_pre[block, k]):.9g} (cutoff distance {float(kernel_pre[block, k]) - 0.0005:+.3g}); "
            f"dist10 floored={bool(captured_mask[block, k])} pre={float(captured_pre[block, k]):.9g}"
        )
    return "\n".join(lines)


# --- constants and window ---------------------------------------------------------------------------


def test_constants():
    assert (SHORT_FFT_SIZE, SHORT_BLOCKS, SHORT_CONSUMED_BINS, SHORT_FLOOR_BINS) == (256, 3, 129, 128)
    assert SHORT_BLOCK_OFFSETS == (256, 384, 512)
    assert SHORT_ENERGY_FLOOR == 0.0005


def test_window_equals_captured_coefficients_exactly():
    window = dist10_short_window()
    assert window.dtype == np.float32 and window.shape == (256,)
    np.testing.assert_array_equal(window, captured_window())
    assert not window.flags.writeable


def test_caller_cannot_change_the_window_the_kernel_uses():
    windows = windows_of("impulse_32767_at1983")
    before = short_window_spectrum(windows)
    leaked = dist10_short_window()
    leaked.flags.writeable = True
    leaked[:] = 0
    assert dist10_short_window() is not leaked
    np.testing.assert_array_equal(dist10_short_window(), captured_window())
    np.testing.assert_array_equal(short_window_spectrum(windows).energy_prefloor, before.energy_prefloor)


def test_window_has_the_half_sample_offset():
    window = dist10_short_window()
    assert window[0] == window[1] > 0 and window[128] == window[129]


# --- windowed blocks --------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "record"), SELECTED, ids=IDS)
def test_windowed_blocks_equal_capture_exactly(name, record):
    windows = windows_of(name)
    blocks = apply_short_window(windows[record["call"] : record["call"] + 1])
    assert blocks.dtype == np.float32 and blocks.shape == (1, 3, 256)
    np.testing.assert_array_equal(blocks[0], f32(record["short_windowed_samples"]))


def test_blocks_are_taken_at_offsets_256_384_512_of_the_long_window():
    window = np.arange(1024, dtype=np.int16)[None, :]
    blocks = apply_short_window(window)[0]
    for block, offset in enumerate((256, 384, 512)):
        expected = dist10_short_window() * np.arange(offset, offset + 256).astype(np.float32)
        np.testing.assert_array_equal(blocks[block], expected)


# --- captured calls: energy, floor and phase ----------------------------------------------------------


@pytest.mark.parametrize(("name", "record"), SELECTED, ids=IDS)
def test_prefloor_energy_within_bound_of_block_peak(name, record):
    kernel = spectrum_of(name).energy_prefloor[record["call"]]
    assert kernel.dtype == np.float32 and kernel.shape == (3, 129)
    captured = f32(record["short_energy_prefloor"])
    for block in range(3):
        peak = float(captured[block].max())
        error = np.abs(kernel[block].astype(np.float64) - captured[block].astype(np.float64))
        if peak == 0:
            assert not kernel[block].any()
        else:
            assert error.max() <= ENERGY_BOUND * peak, (block, error.max(), ENERGY_BOUND * peak)


@pytest.mark.parametrize(("name", "record"), SELECTED, ids=IDS)
def test_floor_mask_equals_probe_d(name, record):
    result = spectrum_of(name)
    call = record["call"]
    captured_mask = captured_floor_mask(record)
    report = floor_mask_report(
        name,
        call,
        result.energy_prefloor[call],
        result.floored[call],
        f32(record["short_energy_prefloor"]),
        captured_mask,
    )
    assert np.array_equal(result.floored[call], captured_mask), report


@pytest.mark.parametrize(("name", "record"), SELECTED, ids=IDS)
def test_post_floor_energy_and_floored_phase(name, record):
    result = spectrum_of(name)
    call = record["call"]
    energy, pre, mask, phase = (
        result.energy[call],
        result.energy_prefloor[call],
        result.floored[call],
        result.phase[call],
    )
    assert np.all(energy[mask] == FLOOR32) and np.all(phase[mask] == 0)
    np.testing.assert_array_equal(energy[~mask], pre[~mask])
    np.testing.assert_array_equal(energy[mask], f32(record["short_energy"])[mask])


@pytest.mark.parametrize(("name", "record"), SELECTED, ids=IDS)
def test_phase_matches_capture_on_strong_unfloored_bins(name, record):
    result = spectrum_of(name)
    phase = result.phase[record["call"]]
    assert phase.dtype == np.float32 and phase.shape == (3, 129)
    pre = f32(record["short_energy_prefloor"])
    energy = f32(record["short_energy"])
    captured = f32(record["short_phi"]).astype(np.float64)
    for block in range(3):
        strong = (pre[block] > STRONG * pre[block].max()) & (energy[block] == pre[block])
        error = np.abs((phase[block].astype(np.float64) - captured[block] + np.pi) % (2 * np.pi) - np.pi)
        assert np.all(error[strong] <= PHASE_BOUND), (block, error[strong].max() if strong.any() else 0)


# --- separate fixture cases ---------------------------------------------------------------------------


def test_silence_floors_bins_0_to_127_of_every_block_and_keeps_nyquist_zero():
    result = spectrum_of("silence_12granules")
    assert not result.energy_prefloor.any()
    assert result.floored[..., :128].all() and not result.floored[..., 128].any()
    assert np.all(result.energy[..., :128] == FLOOR32) and not result.energy[..., 128].any()
    assert not result.phase.any()


def test_impulse_boundary_reaches_blocks_0_and_1_of_call_4_only():
    result = spectrum_of("impulse_32767_at1983")
    window = dist10_short_window()
    # Call 3's long window ends at sample 1983, but its short blocks end at 1728: all three are silent.
    assert not result.energy_prefloor[3].any() and result.floored[3, :, :128].all()
    # Call 4: sample 1983 at block positions 191 (block 0) and 63 (block 1); block 2 starts at 2048.
    for block, position in ((0, 191), (1, 63)):
        v = float(window[position] * np.float32(32767))
        np.testing.assert_allclose(result.energy_prefloor[4, block].astype(np.float64), v * v, rtol=1e-6)
        assert not result.floored[4, block].any()
    assert not result.energy_prefloor[4, 2].any() and result.floored[4, 2, :128].all()


def test_quiet_tone_call_6_block_2_floors_exactly_bins_42_and_86():
    result = spectrum_of("target_2k_a10.24")
    assert np.flatnonzero(result.floored[6, 2]).tolist() == [42, 86]
    assert not result.floored[6, :2].any()
    assert np.all(result.energy[6, 2, [42, 86]] == FLOOR32) and np.all(result.phase[6, 2, [42, 86]] == 0)
    assert result.energy[6, 2, 128] == result.energy_prefloor[6, 2, 128] > 0.0005


def test_startup_calls_are_kept_in_order():
    windows = windows_of("masker_1k_a8192")
    result = short_window_spectrum(windows)
    assert result.energy.shape == (windows.shape[0], 3, 129)
    singles = [short_window_spectrum(windows[k : k + 1]).energy_prefloor[0] for k in range(windows.shape[0])]
    np.testing.assert_array_equal(result.energy_prefloor, np.stack(singles))
    assert result.floored[0, :, :128].all()  # call 0's short blocks lie before sample 0


# --- synthetic cutoff, Nyquist and zero-energy cases -------------------------------------------------------


def block_impulse(block: int, position: int, amplitude: int) -> np.ndarray:
    window = np.zeros((1, 1024), dtype=np.int16)
    window[0, 256 + 128 * block + position] = amplitude
    return window


def test_energy_just_above_cutoff_is_not_floored():
    result = short_window_spectrum(block_impulse(0, 3, 24))  # flat ~5.0998e-4 in block 0
    pre = result.energy_prefloor[0, 0].astype(np.float64)
    assert np.all((pre > 0.0005) & (pre < 0.000515))
    assert not result.floored[0, 0].any()
    np.testing.assert_array_equal(result.energy[0, 0], result.energy_prefloor[0, 0])


def test_energy_just_below_cutoff_is_floored_except_nyquist():
    # Position 4 of block 0 is long-window position 260, also position -124 of block 1 (outside): block 0 only.
    result = short_window_spectrum(block_impulse(0, 4, 12))  # flat ~4.8949e-4 in block 0
    pre = result.energy_prefloor[0, 0].astype(np.float64)
    assert np.all((pre < 0.0005) & (pre > 0.000485))
    assert result.floored[0, 0, :128].all() and not result.floored[0, 0, 128]
    assert result.energy[0, 0, 128] == result.energy_prefloor[0, 0, 128] < 0.0005
    assert not result.phase[0, 0].any()


def test_zero_energy_bins_are_floored_with_zero_phase_and_nyquist_stays_zero():
    result = short_window_spectrum(np.zeros((2, 1024), dtype=np.int16))
    assert result.floored[..., :128].all() and not result.floored[..., 128].any()
    assert not result.energy[..., 128].any() and not result.phase.any()


def test_nyquist_carries_energy_and_is_never_floored():
    result = short_window_spectrum(np.array([[1000, -1000] * 512], dtype=np.int16))
    pre = result.energy_prefloor[0]
    assert np.all(pre[:, 128] == pre.max(axis=1)) and not result.floored[0, :, 128].any()


def test_overlapping_blocks_share_samples():
    # Long-window position 400 lies in block 0 (position 144) and block 1 (position 16), not in block 2.
    window = np.zeros((1, 1024), dtype=np.int16)
    window[0, 400] = 30000
    blocks = apply_short_window(window)[0]
    assert [np.flatnonzero(b).tolist() for b in blocks] == [[144], [16], []]


# --- outputs, memory, inputs and validation -----------------------------------------------------------


def test_outputs_are_read_only_fresh_and_input_unchanged():
    windows = windows_of("target_2k_a1024").copy()
    before = windows.copy()
    result = short_window_spectrum(windows)
    np.testing.assert_array_equal(windows, before)
    for array in (result.energy_prefloor, result.energy, result.floored, result.phase):
        assert not array.flags.writeable and not np.shares_memory(array, windows)
    assert result.floored.dtype == np.bool_


def test_zero_calls():
    result = short_window_spectrum(np.zeros((0, 1024), dtype=np.int16))
    assert result.energy.shape == (0, 3, 129) and result.phase.shape == (0, 3, 129)


def test_many_calls_match_single_calls_across_chunk_boundaries():
    rng = np.random.default_rng(11)
    windows = rng.integers(-32768, 32768, size=(600, 1024)).astype(np.int16)
    result = short_window_spectrum(windows)
    for k in (0, 255, 256, 511, 512, 599):
        single = short_window_spectrum(windows[k : k + 1])
        np.testing.assert_array_equal(result.energy_prefloor[k], single.energy_prefloor[0])
        np.testing.assert_array_equal(result.phase[k], single.phase[0])
        np.testing.assert_array_equal(result.floored[k], single.floored[0])


def _extra_peak_bytes(windows: np.ndarray) -> int:
    tracemalloc.start()
    try:
        result = short_window_spectrum(windows)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak - sum(a.nbytes for a in (result.energy_prefloor, result.energy, result.floored, result.phase))


def test_temporary_memory_does_not_grow_with_the_number_of_calls():
    rng = np.random.default_rng(5)
    small = rng.integers(-30000, 30000, size=(1000, 1024)).astype(np.int16)
    large = rng.integers(-30000, 30000, size=(4000, 1024)).astype(np.int16)
    short_window_spectrum(small[:3])
    growth = _extra_peak_bytes(large) - _extra_peak_bytes(small)
    assert growth < 1 << 20, growth  # whole-array temporaries would add tens of MB for 3,000 more calls


def test_noncontiguous_read_only_and_byte_swapped_windows():
    windows = windows_of("masker_1k_a8192")
    expected = short_window_spectrum(windows.copy())
    for variant in (windows, windows.astype(windows.dtype.newbyteorder()), np.repeat(windows, 2, axis=0)[::2]):
        np.testing.assert_array_equal(short_window_spectrum(variant).energy_prefloor, expected.energy_prefloor)


def test_equality_is_identity():
    a = short_window_spectrum(np.zeros((1, 1024), dtype=np.int16))
    b = short_window_spectrum(np.zeros((1, 1024), dtype=np.int16))
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
        short_window_spectrum(windows)
    with pytest.raises(ValueError):
        apply_short_window(windows)
