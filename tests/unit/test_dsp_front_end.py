"""Tests for the composed dist10 clip-to-band front end (front-end contract §2-§3.6; Task 3O).

The entry point must equal the direct composition of the reviewed kernels (framing, long and short spectra,
unpredictability, partition sums, spreading, partition thresholds, long-band mapping) and carry the framer's
metadata. Reference metadata and the exact-silence values come from Probe B in
``tests/fixtures/mpeg_psy_model2_dist10/expected_long_mapping``; agreement of the band values with dist10 on active
clips is reported by ``capture/compare_band_margins.py`` and has no tolerance here. Other inputs are small synthetic
int16 arrays (unit-test signals, not audio material or evaluation data).
"""

import dataclasses
import json
import pathlib

import numpy as np
import pytest

from autonomous_mixing.adapters.dsp_analysis.bands import dist10_long_bands
from autonomous_mixing.adapters.dsp_analysis.framing import frame_long_blocks
from autonomous_mixing.adapters.dsp_analysis.front_end import ClipBandAnalysis, dist10_clip_to_bands
from autonomous_mixing.adapters.dsp_analysis.long_spectrum import long_window_spectrum
from autonomous_mixing.adapters.dsp_analysis.partitions import dist10_partition_sums
from autonomous_mixing.adapters.dsp_analysis.short_spectrum import short_window_spectrum
from autonomous_mixing.adapters.dsp_analysis.spreading import dist10_spread_tonality_input
from autonomous_mixing.adapters.dsp_analysis.thresholds import dist10_partition_thresholds
from autonomous_mixing.adapters.dsp_analysis.unpredictability import dist10_unpredictability

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "mpeg_psy_model2_dist10"
NAMES = sorted(p.stem for p in (FIXTURE / "expected_long_mapping").glob("*.jsonl"))
RATE = 44100
FIELDS = ("sample_count", "sample_rate_hz", "call", "window_start", "window_end", "eligible", "nonzero_samples",
          "en", "thm")  # fmt: skip


def raw(name: str) -> np.ndarray:
    return np.frombuffer((FIXTURE / "inputs" / f"{name}.raw").read_bytes(), dtype="<i2").astype(np.int16)


def probe_b(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURE / "expected_long_mapping" / f"{name}.jsonl").read_text().splitlines()]


def composed(samples: np.ndarray, first_threshold_call: int = 0):
    """Direct composition of the reviewed kernels; ``first_threshold_call`` > 0 drops earlier calls' history."""
    frames = frame_long_blocks(samples, sample_rate_hz=RATE)
    long, short = long_window_spectrum(frames.windows), short_window_spectrum(frames.windows)
    cw = dist10_unpredictability(long.energy, long.phase, short.energy, short.phase)
    sums = dist10_partition_sums(long.energy, cw)
    spread = dist10_spread_tonality_input(sums.eb, sums.cb)
    k = first_threshold_call
    thresholds = dist10_partition_thresholds(spread.ecb[k:], spread.log_ratio[k:])
    return frames, dist10_long_bands(sums.eb[k:], thresholds.thr)


RESULTS = {name: dist10_clip_to_bands(raw(name), sample_rate_hz=RATE) for name in NAMES}


def synthetic(n: int) -> np.ndarray:
    k = np.arange(n)
    return ((k * 7919) % 2001 - 1000).astype(np.int16)  # deterministic integer pattern, no audio material


# --- composition and metadata -----------------------------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_entry_point_equals_the_direct_composition(name):
    result = RESULTS[name]
    frames, bands = composed(raw(name))
    np.testing.assert_array_equal(result.en, bands.en)
    np.testing.assert_array_equal(result.thm, bands.thm)
    for field in ("call", "window_start", "window_end", "eligible", "nonzero_samples"):
        np.testing.assert_array_equal(getattr(result, field), getattr(frames, field))
    assert result.sample_count == frames.sample_count == raw(name).size and result.sample_rate_hz == RATE


@pytest.mark.parametrize("name", NAMES)
def test_metadata_matches_probe_b_on_every_call(name):
    result, b = RESULTS[name], probe_b(name)
    n = raw(name).size
    assert result.call.tolist() == [r["call"] for r in b]
    assert result.window_start.tolist() == [r["window_start"] for r in b]
    assert result.window_end.tolist() == [r["window_end"] for r in b]
    assert result.nonzero_samples.tolist() == [r["window_nonzero_samples"] for r in b]
    assert result.eligible.tolist() == [r["call"] >= 2 and 576 * r["call"] + 256 <= n for r in b]


def test_result_holds_exactly_the_contract_fields_and_no_windows_or_spectra():
    assert [f.name for f in dataclasses.fields(ClipBandAnalysis)] == list(FIELDS)
    result = RESULTS["masker_1k_a8192"]
    assert dataclasses.is_dataclass(result) and type(result).__dataclass_params__.frozen
    for name in ("en", "thm"):
        array = getattr(result, name)
        assert type(array) is np.ndarray and array.dtype == np.float64 and array.shape == (12, 21)
        assert not array.flags.writeable
    for name in ("call", "window_start", "window_end", "eligible", "nonzero_samples"):
        array = getattr(result, name)
        assert array.shape == (12,) and not array.flags.writeable
    assert result.eligible.dtype == np.bool_ and result.nonzero_samples.dtype == np.int64


# --- history -------------------------------------------------------------------------------------------------


def test_startup_calls_are_processed_and_their_history_is_used():
    result = RESULTS["masker_1k_a8192"]
    b = probe_b("masker_1k_a8192")
    # calls 0 and 1: zero pre-echo history, so every partition threshold is threshold in quiet
    assert result.thm[0].tolist() == b[0]["thm_quiet"] and result.thm[1].tolist() == b[1]["thm_quiet"]
    assert not result.eligible[:2].any()
    # starting the threshold history at call 2 would change the established calls
    _, late = composed(raw("masker_1k_a8192"), first_threshold_call=2)
    assert not np.array_equal(late.thm[0], result.thm[2])
    assert not np.array_equal(late.thm[1], result.thm[3])


def test_identical_window_with_different_history():
    # Tone call 5 and burst call 5 analyse the same window; the burst's silent history keeps its threshold at quiet.
    tone, burst = RESULTS["masker_1k_a8192"], RESULTS["burst_1k_a8192_onset2000"]
    np.testing.assert_array_equal(tone.en[5], burst.en[5])
    assert burst.thm[5].tolist() == probe_b("burst_1k_a8192_onset2000")[5]["thm_quiet"]
    assert not np.array_equal(tone.thm[5], burst.thm[5])


# --- eligibility, truncation and silence ----------------------------------------------------------------------


@pytest.mark.parametrize(("n", "eligible"), [(1407, [False, False, False]), (1408, [False, False, True])])
def test_eligibility_boundary(n, eligible):
    result = dist10_clip_to_bands(synthetic(n), sample_rate_hz=RATE)
    assert result.call.tolist() == [0, 1, 2] and result.eligible.tolist() == eligible
    assert result.en.shape == result.thm.shape == (3, 21)  # ineligible calls stay in the result
    assert np.isfinite(result.en).all() and np.isfinite(result.thm).all()


def test_truncated_fixture_matches_the_full_clip_while_its_window_is_inside():
    trunc, full = RESULTS["target_2k_a1024_trunc6536"], RESULTS["target_2k_a1024"]
    assert trunc.sample_count == 6536 and trunc.call.size == full.call.size == 12
    assert np.flatnonzero(trunc.eligible).tolist() == list(range(2, 11))
    inside = trunc.window_end <= 6536
    assert inside.tolist() == [True] * 11 + [False]
    np.testing.assert_array_equal(trunc.en[inside], full.en[inside])
    np.testing.assert_array_equal(trunc.thm[inside], full.thm[inside])
    assert not np.array_equal(trunc.en[11], full.en[11])  # its window reaches the zero fill


def test_exact_silence_is_flagged_and_equals_probe_b():
    result, b = RESULTS["silence_12granules"], probe_b("silence_12granules")
    assert not result.nonzero_samples.any()  # exact silence from the samples, not from the FFT floor
    assert result.en.tolist() == [r["en"] for r in b]  # the FFT energy floor, not zero
    assert result.thm.tolist() == [r["thm"] for r in b] == [r["thm_quiet"] for r in b]
    assert (result.en > 0).all() and result.eligible.sum() == 10  # silent calls stay, with their metadata


def test_silent_and_active_calls_are_told_apart_by_nonzero_samples():
    result = RESULTS["burst_1k_a8192_onset2000"]  # silence, then a burst from sample 2000
    assert (result.nonzero_samples == 0).tolist() == [True] * 4 + [False] * 4
    np.testing.assert_array_equal(result.en[:4], RESULTS["silence_12granules"].en[:4])


# --- empty input and validation -------------------------------------------------------------------------------


def test_empty_clip_has_zero_calls():
    result = dist10_clip_to_bands(np.zeros(0, dtype=np.int16), sample_rate_hz=RATE)
    assert result.sample_count == 0 and result.sample_rate_hz == RATE
    for name in ("call", "window_start", "window_end", "eligible", "nonzero_samples"):
        assert getattr(result, name).shape == (0,)
    assert result.en.shape == result.thm.shape == (0, 21) and result.en.dtype == np.float64


def test_input_is_not_modified_and_byte_order_is_accepted():
    samples = synthetic(3000)
    before = samples.copy()
    result = dist10_clip_to_bands(samples, sample_rate_hz=RATE)
    np.testing.assert_array_equal(samples, before)
    swapped = dist10_clip_to_bands(samples.astype(">i2"), sample_rate_hz=RATE)
    np.testing.assert_array_equal(swapped.en, result.en)
    np.testing.assert_array_equal(swapped.thm, result.thm)


@pytest.mark.parametrize("rate", [48000, 32000, 44100.0, True, "44100", None])
def test_rejects_invalid_sample_rate(rate):
    with pytest.raises(ValueError, match="sample_rate_hz"):
        dist10_clip_to_bands(synthetic(1200), sample_rate_hz=rate)


@pytest.mark.parametrize(
    "samples",
    [
        np.zeros((2, 600), dtype=np.int16),
        np.zeros((600, 1), dtype=np.int16),
        np.zeros(600, dtype=np.float32),
        np.zeros(600, dtype=np.int32),
        np.zeros(600, dtype=np.uint16),
        [0] * 600,
        np.ma.masked_array(np.zeros(600, dtype=np.int16)),
    ],
)
def test_rejects_invalid_samples(samples):
    with pytest.raises(ValueError, match="samples"):
        dist10_clip_to_bands(samples, sample_rate_hz=RATE)


def test_sample_rate_is_keyword_only():
    with pytest.raises(TypeError):
        dist10_clip_to_bands(synthetic(600), RATE)  # type: ignore[misc]
