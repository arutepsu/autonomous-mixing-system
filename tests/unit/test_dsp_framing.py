"""Tests for the dist10 long-block sample framing (front-end contract revision 2, §2; Task 3A).

Fixture comparisons use the recorded dist10 captures in ``tests/fixtures/mpeg_psy_model2_dist10``: probe B
(``expected_long_mapping``) records call indices, window bounds, whether a window ends inside the clip and the
nonzero int16 count per window for every input; probe A (``expected``) records the calls of the whole-frame
inputs. Other inputs are small synthetic int16 arrays (unit-test signals, not audio material or evaluation data).
"""

import json
import pathlib

import numpy as np
import pytest

from autonomous_mixing.adapters.dsp_analysis.framing import (
    FIRST_ELIGIBLE_CALL,
    GRANULE_SAMPLES,
    LOOKBACK_SAMPLES,
    PROFILE_SAMPLE_RATE_HZ,
    WINDOW_SAMPLES,
    LongBlockFrames,
    frame_long_blocks,
)

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "mpeg_psy_model2_dist10"
FIXTURE_INPUTS = sorted(p.stem for p in (FIXTURE / "inputs").glob("*.raw"))
FS = 44100


def frame(samples) -> LongBlockFrames:
    return frame_long_blocks(np.asarray(samples, dtype=np.int16), sample_rate_hz=FS)


def fixture_samples(name: str) -> np.ndarray:
    return np.frombuffer((FIXTURE / "inputs" / f"{name}.raw").read_bytes(), dtype="<i2").astype(np.int16)


def fixture_records(folder: str, name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURE / folder / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]


def reference_window(samples: np.ndarray, call: int) -> np.ndarray:
    """Window of call ``call`` built sample by sample: stream positions outside [0, N) read as zero."""
    out = np.zeros(1024, dtype=np.int16)
    for i in range(1024):
        position = 576 * call - 768 + i
        if 0 <= position < samples.size:
            out[i] = samples[position]
    return out


# --- constants and shape ----------------------------------------------------------------------------


def test_profile_constants_are_the_dist10_layer_iii_values():
    assert (PROFILE_SAMPLE_RATE_HZ, GRANULE_SAMPLES, WINDOW_SAMPLES, LOOKBACK_SAMPLES) == (44100, 576, 1024, 768)
    assert FIRST_ELIGIBLE_CALL == 2


@pytest.mark.parametrize("n", [1, 575, 576, 577, 1152, 1407, 1408, 6536, 6912])
def test_one_call_per_started_granule_with_contract_bounds(n):
    result = frame(np.ones(n))
    calls = -(-n // 576)
    assert result.sample_count == n
    assert result.call.tolist() == list(range(calls))
    assert result.window_start.tolist() == [576 * k - 768 for k in range(calls)]
    assert result.window_end.tolist() == [576 * k + 256 for k in range(calls)]
    assert result.windows.shape == (calls, 1024)


def test_empty_clip_has_no_calls():
    result = frame(np.zeros(0))
    assert result.sample_count == 0
    for array in (result.call, result.window_start, result.window_end, result.eligible, result.nonzero_samples):
        assert array.shape == (0,)
    assert result.windows.shape == (0, 1024)


def test_result_dtypes():
    result = frame(np.ones(2000))
    for array in (result.call, result.window_start, result.window_end, result.nonzero_samples):
        assert array.dtype == np.int64
    assert result.eligible.dtype == np.bool_
    assert result.windows.dtype == np.int16


# --- window content ---------------------------------------------------------------------------------


@pytest.mark.parametrize("n", [1, 300, 576, 1407, 1408, 2000, 6536])
def test_windows_zero_fill_before_sample_zero_and_after_the_clip(n):
    samples = ((np.arange(n) % 2001) - 1000).astype(np.int16)  # nonzero almost everywhere, sign-varying
    result = frame(samples)
    for k in range(result.call.size):
        np.testing.assert_array_equal(result.windows[k], reference_window(samples, k))


def test_startup_windows_are_kept_with_their_leading_zeros():
    samples = np.arange(1, 2001).astype(np.int16)
    result = frame(samples)
    np.testing.assert_array_equal(result.windows[0, :768], 0)
    np.testing.assert_array_equal(result.windows[0, 768:], samples[:256])
    np.testing.assert_array_equal(result.windows[1, :192], 0)
    np.testing.assert_array_equal(result.windows[1, 192:], samples[:832])
    assert not result.eligible[:2].any()


def test_int16_extremes_are_carried_unchanged():
    samples = np.array([-32768, 32767] * 1000, dtype=np.int16)
    result = frame(samples)
    np.testing.assert_array_equal(result.windows[2], samples[384:1408])


# --- eligibility ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "eligible_calls"),
    [
        (1407, []),  # call 2's window ends at 1408 > N
        (1408, [2]),  # the shortest clip with an eligible frame
        (1983, [2]),
        (1984, [2, 3]),
        (6536, list(range(2, 11))),
        (6912, list(range(2, 12))),
    ],
)
def test_eligible_iff_call_at_least_two_and_window_inside_clip(n, eligible_calls):
    result = frame(np.ones(n))
    assert result.call[result.eligible].tolist() == eligible_calls
    expected = (result.call >= 2) & (result.window_end <= n)
    np.testing.assert_array_equal(result.eligible, expected)


def test_clip_shorter_than_1408_samples_keeps_its_calls_but_none_is_eligible():
    result = frame(np.ones(1407))
    assert result.call.tolist() == [0, 1, 2]
    assert not result.eligible.any()


# --- nonzero counts from the samples -----------------------------------------------------------------


def test_nonzero_count_is_exact_and_ignores_fill():
    samples = np.zeros(2000, dtype=np.int16)
    samples[[0, 255, 256, 1999]] = [1, -1, 7, -32768]
    result = frame(samples)
    # call 0 covers [-768, 256): samples 0 and 255; call 1 [-192, 832) adds 256. Sample 1999 lies after the last
    # call's window end (1984): only samples below 576 (calls - 1) + 256 are ever analysed.
    assert result.nonzero_samples.tolist() == [2, 3, 0, 0]
    expected = [int(np.count_nonzero(reference_window(samples, k))) for k in range(result.call.size)]
    assert result.nonzero_samples.tolist() == expected


def test_digital_silence_has_zero_nonzero_samples_everywhere():
    result = frame(np.zeros(6912))
    assert not result.nonzero_samples.any()
    assert not result.windows.any()


def test_a_single_minimum_amplitude_sample_counts_as_nonzero():
    samples = np.zeros(3000, dtype=np.int16)
    samples[1500] = 1
    result = frame(samples)
    assert result.nonzero_samples.tolist() == [0, 0, 0, 1, 0, 0]  # only call 3's window [960, 1984) holds 1500


# --- recorded dist10 capture ------------------------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURE_INPUTS)
def test_matches_probe_b_calls_bounds_eligibility_and_nonzero_counts(name):
    samples = fixture_samples(name)
    records = fixture_records("expected_long_mapping", name)
    result = frame(samples)
    assert result.call.tolist() == [r["call"] for r in records]
    assert result.window_start.tolist() == [r["window_start"] for r in records]
    assert result.window_end.tolist() == [r["window_end"] for r in records]
    assert result.nonzero_samples.tolist() == [r["window_nonzero_samples"] for r in records]
    assert {r["clip_samples"] for r in records} == {samples.size} == {result.sample_count}
    eligible = [r["call"] >= 2 and bool(r["window_end_within_clip"]) for r in records]
    assert result.eligible.tolist() == eligible


@pytest.mark.parametrize("name", sorted(p.stem for p in (FIXTURE / "expected").glob("*.jsonl")))
def test_matches_probe_a_call_indices(name):
    assert frame(fixture_samples(name)).call.tolist() == [r["call"] for r in fixture_records("expected", name)]


def test_fixture_covers_every_input():
    assert len(FIXTURE_INPUTS) == 9


def test_impulse_boundary_1983_reaches_call_3_and_1984_does_not():
    at_1983 = frame(fixture_samples("impulse_32767_at1983"))
    at_1984 = frame(fixture_samples("impulse_32767_at1984"))
    assert at_1983.nonzero_samples.tolist() == [0, 0, 0, 1, 1, 0]
    assert at_1984.nonzero_samples.tolist() == [0, 0, 0, 0, 1, 0]  # call 5 starts at 2112
    assert at_1983.windows[3, -1] == 32767  # last window position of call 3 is sample 1983
    assert at_1984.windows[4, 1984 - (576 * 4 - 768)] == 32767


def test_truncated_clip_equals_full_clip_until_its_window_passes_the_end():
    full = frame(fixture_samples("target_2k_a1024"))
    cut = frame(fixture_samples("target_2k_a1024_trunc6536"))
    assert cut.sample_count == 6536 and full.sample_count == 6912
    assert cut.call.tolist() == full.call.tolist() == list(range(12))
    np.testing.assert_array_equal(cut.windows[:11], full.windows[:11])
    np.testing.assert_array_equal(cut.windows[11, : 6536 - 5568], full.windows[11, : 6536 - 5568])
    np.testing.assert_array_equal(cut.windows[11, 6536 - 5568 :], 0)  # 56 fill positions
    assert 6592 - 6536 == 56
    assert cut.call[cut.eligible].tolist() == list(range(2, 11))
    assert full.call[full.eligible].tolist() == list(range(2, 12))


def test_silent_fixture_has_no_nonzero_sample_and_its_eligible_frames_are_known():
    result = frame(fixture_samples("silence_12granules"))
    assert not result.nonzero_samples.any()
    assert result.call[result.eligible].tolist() == list(range(2, 12))


# --- immutability and input preservation ------------------------------------------------------------


def test_input_samples_are_unchanged_and_not_shared():
    samples = ((np.arange(3000) * 37) % 65536 - 32768).astype(np.int16)
    before = samples.copy()
    result = frame_long_blocks(samples, sample_rate_hz=FS)
    np.testing.assert_array_equal(samples, before)
    assert samples.flags.writeable
    assert not np.shares_memory(result.windows, samples)


def test_results_are_read_only():
    result = frame(np.ones(3000))
    for array in (
        result.call,
        result.window_start,
        result.window_end,
        result.eligible,
        result.nonzero_samples,
        result.windows,
    ):
        assert not array.flags.writeable
        with pytest.raises(ValueError):
            array[...] = 0


def test_read_only_and_noncontiguous_inputs_are_accepted():
    base = ((np.arange(6000) % 7) - 3).astype(np.int16)
    strided = base[::2]
    read_only = strided.copy()
    read_only.flags.writeable = False
    expected = frame(strided.copy())
    for samples in (strided, read_only):
        result = frame_long_blocks(samples, sample_rate_hz=FS)
        np.testing.assert_array_equal(result.windows, expected.windows)
        np.testing.assert_array_equal(result.nonzero_samples, expected.nonzero_samples)


def test_non_native_byte_order_int16_gives_the_same_frames():
    native = ((np.arange(2500) % 101) - 50).astype(np.int16)
    swapped = native.astype(native.dtype.newbyteorder())
    result = frame_long_blocks(swapped, sample_rate_hz=FS)
    np.testing.assert_array_equal(result.windows, frame(native).windows)
    assert result.windows.dtype == np.dtype(np.int16)


def test_ndarray_subclass_is_viewed_as_plain_array():
    class Tagged(np.ndarray):
        pass

    samples = np.arange(1, 1500, dtype=np.int16).view(Tagged)
    result = frame_long_blocks(samples, sample_rate_hz=FS)
    assert type(result.windows) is np.ndarray
    np.testing.assert_array_equal(result.windows, frame(np.arange(1, 1500)).windows)


def test_peak_allocation_stays_within_the_int16_windows_and_their_buffers():
    # Regression (Codex review): no per-sample index array and no other temporary of the window grid's size. The
    # bound allows the returned int16 windows, the zero-led int16 stream and fixed overhead; an int64 index for
    # every window sample alone would be four times the windows.
    import tracemalloc

    samples = np.ones(576 * 400, dtype=np.int16)
    frame(samples[:10])  # warm up imports and caches outside the measurement
    tracemalloc.start()
    try:
        result = frame_long_blocks(samples, sample_rate_hz=FS)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    windows_bytes = result.windows.nbytes  # 400 * 1024 * 2
    stream_bytes = (768 + 576 * 400) * 2
    assert peak < windows_bytes + stream_bytes + 64 * 1024
    assert result.windows.flags.c_contiguous and result.windows.flags.owndata


def test_equality_is_identity():
    a, b = frame(np.ones(10)), frame(np.ones(10))
    assert a == a and a != b


# --- invalid inputs ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "samples",
    [
        [1, 2, 3],  # not an ndarray
        np.ones(10, dtype=np.int32),
        np.ones(10, dtype=np.uint16),
        np.ones(10, dtype=np.float32),
        np.ones(10, dtype=np.float64),
        np.ones(10, dtype=np.int8),
        np.ones(10, dtype=np.bool_),
        np.ones((10, 1), dtype=np.int16),  # 2-D, even with one channel
        np.ones((2, 10), dtype=np.int16),
        np.array(5, dtype=np.int16),  # 0-D
    ],
)
def test_rejects_non_int16_or_non_mono_samples(samples):
    with pytest.raises(ValueError):
        frame_long_blocks(samples, sample_rate_hz=FS)


def test_rejects_masked_arrays():
    samples = np.ma.masked_array(np.ones(10, dtype=np.int16), mask=[True] + [False] * 9)
    with pytest.raises(ValueError, match="masked"):
        frame_long_blocks(samples, sample_rate_hz=FS)


@pytest.mark.parametrize("rate", [48000, 44099, 22050, 0, -44100, 44100.0, True, "44100", None])
def test_rejects_any_rate_other_than_the_profile_rate(rate):
    with pytest.raises(ValueError):
        frame_long_blocks(np.ones(10, dtype=np.int16), sample_rate_hz=rate)


def test_sample_rate_is_keyword_only():
    with pytest.raises(TypeError):
        frame_long_blocks(np.ones(10, dtype=np.int16), FS)  # type: ignore[misc]


def test_numpy_integer_rate_is_accepted():
    result = frame_long_blocks(np.ones(10, dtype=np.int16), sample_rate_hz=np.int64(44100))
    assert result.call.tolist() == [0]
