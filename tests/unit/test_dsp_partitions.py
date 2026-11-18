"""Tests for the dist10 long partition accumulator eb/cb (front-end contract §3.3; Task 3H).

Reference values are the Probe E capture in ``tests/fixtures/mpeg_psy_model2_dist10/expected_partition``:
``partition_energy`` and ``partition_cw`` (the exact inputs dist10's partition loop read) and ``eb``/``cb`` after
accumulation. With those inputs the kernel must agree exactly. A separate report runs the package's own spectra and
predictor (no tolerance; the burst call 4 partition 50 case is retained for the later threshold comparison).
Other inputs are small synthetic arrays (unit-test signals, not audio material or evaluation data).
"""

import json
import pathlib
import tracemalloc

import numpy as np
import pytest

from autonomous_mixing.adapters.dsp_analysis.framing import frame_long_blocks
from autonomous_mixing.adapters.dsp_analysis.long_spectrum import long_window_spectrum
from autonomous_mixing.adapters.dsp_analysis.partitions import (
    DIST10_LONG_PARTITION_LINES_44100,
    LONG_PARTITIONS,
    PartitionSums,
    dist10_long_partition_map,
    dist10_partition_sums,
)
from autonomous_mixing.adapters.dsp_analysis.short_spectrum import short_window_spectrum
from autonomous_mixing.adapters.dsp_analysis.unpredictability import dist10_unpredictability

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "mpeg_psy_model2_dist10"
PARTITION = FIXTURE / "expected_partition"
THRESHOLD_COMPARISON_CASE = ("burst_1k_a8192_onset2000", 4, 50)  # retained for the later threshold comparison


def records(name: str) -> list[dict]:
    return [json.loads(line) for line in (PARTITION / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]


CAPTURED = [(p.stem, r) for p in sorted(PARTITION.glob("*.jsonl")) for r in records(p.stem)]
IDS = [f"{name}-call{r['call']}" for name, r in CAPTURED]


def sums(energy, cw) -> PartitionSums:
    return dist10_partition_sums(np.asarray(energy, dtype=np.float32), np.asarray(cw, dtype=np.float64))


def zeros(calls: int = 1):
    return np.zeros((calls, 513), dtype=np.float32), np.zeros((calls, 513), dtype=np.float64)


# --- mapping ------------------------------------------------------------------------------------------


def test_table_constants_and_mapping_match_the_captured_reference():
    captured = json.loads((PARTITION / "mapping_44100.json").read_text())
    assert list(DIST10_LONG_PARTITION_LINES_44100) == captured["psy_data_44100_long_lines"]
    assert LONG_PARTITIONS == 63 and sum(DIST10_LONG_PARTITION_LINES_44100) == 465
    mapping = dist10_long_partition_map()
    assert mapping.shape == (513,) and mapping.tolist() == captured["partition_l"]
    assert not mapping.flags.writeable


def test_partition_0_holds_dc_and_bins_465_to_512():
    mapping = dist10_long_partition_map()
    assert np.flatnonzero(mapping == 0).tolist() == [0, *range(465, 513)]
    assert np.bincount(mapping, minlength=63)[0] == 49


def test_caller_cannot_change_the_mapping_the_kernel_uses():
    leaked = dist10_long_partition_map()
    leaked.flags.writeable = True
    leaked[:] = 5
    energy, cw = zeros()
    energy[0, 1] = 1.0
    assert sums(energy, cw).eb[0].tolist() == [0.0, 1.0] + [0.0] * 61


# --- exact agreement with Probe E ---------------------------------------------------------------------


@pytest.mark.parametrize(("name", "record"), CAPTURED, ids=IDS)
def test_exact_agreement_with_captured_probe_e_call(name, record):
    result = sums([record["partition_energy"]], [record["partition_cw"]])
    assert result.eb.dtype == np.float64 and result.cb.dtype == np.float32
    assert result.eb.shape == (1, 63) and result.cb.shape == (1, 63)
    assert result.eb[0].tolist() == record["eb"]
    np.testing.assert_array_equal(result.cb[0], np.array(record["cb"], dtype=np.float32))


def test_all_captured_calls_in_one_batch_agree_exactly():
    energy = np.array([r["partition_energy"] for _, r in CAPTURED], dtype=np.float32)
    cw = np.array([r["partition_cw"] for _, r in CAPTURED], dtype=np.float64)
    result = dist10_partition_sums(energy, cw)
    assert result.eb.tolist() == [r["eb"] for _, r in CAPTURED]
    np.testing.assert_array_equal(result.cb, np.array([r["cb"] for _, r in CAPTURED], dtype=np.float32))


# --- boundaries, Nyquist and order -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bin_", "partition"),
    [(0, 0), (1, 1), (14, 14), (15, 15), (16, 15), (17, 16), (40, 27), (41, 28), (446, 61), (447, 62), (464, 62),
     (465, 0), (511, 0), (512, 0)],
)  # fmt: skip
def test_each_bin_lands_in_its_partition(bin_, partition):
    energy, cw = zeros()
    energy[0, bin_], cw[0, bin_] = 2.0, 0.5
    result = sums(energy, cw)
    assert np.flatnonzero(result.eb[0]).tolist() == [partition]
    assert result.eb[0, partition] == 2.0 and result.cb[0, partition] == np.float32(1.0)


def test_every_boundary_against_the_table():
    starts = np.concatenate([[0], np.cumsum(DIST10_LONG_PARTITION_LINES_44100)])
    mapping = dist10_long_partition_map()
    for b in range(63):
        assert mapping[starts[b]] == b and (b == 0 or mapping[starts[b] - 1] == b - 1)
        assert mapping[starts[b + 1] - 1] == b


def test_nyquist_energy_accumulates_into_partition_0():
    energy, cw = zeros()
    energy[0, 0], energy[0, 512] = 1.0, 4.0
    cw[0, 0], cw[0, 512] = 0.25, 0.4
    result = sums(energy, cw)
    assert result.eb[0, 0] == 5.0 and result.cb[0, 0] == np.float32(0.25 + 1.6)
    assert not result.eb[0, 1:].any()


def test_cb_rounds_to_float32_after_each_bin_in_ascending_order():
    # Partition 61 covers bins 374..446. One large term first, then 72 terms of 1.0: with a float32 store after each
    # bin, 1e8 + 1 rounds back to 1e8 every time (float32 spacing 8 there), so cb stays 1e8.
    energy, cw = zeros()
    bins = np.flatnonzero(dist10_long_partition_map() == 61)
    assert bins.tolist() == list(range(374, 447))
    energy[0, bins] = 1.0
    energy[0, bins[0]] = 1e8
    cw[0, bins] = 1.0
    result = sums(energy, cw)
    assert result.cb[0, 61] == np.float32(1e8)
    descending = np.float32(0.0)
    for j in bins[::-1]:  # the same terms in descending order would give a different float32 result
        descending = np.float32(float(descending) + float(cw[0, j]) * float(energy[0, j]))
    assert descending != result.cb[0, 61]
    assert result.eb[0, 61] == 1e8 + 72.0  # eb accumulates in float64


def test_eb_accumulates_float32_energies_in_float64():
    energy, cw = zeros()
    energy[0, 0], energy[0, 465] = np.float32(16777216.0), np.float32(1.0)  # partition 0: DC (2**24), then bin 465
    result = sums(energy, cw)
    assert result.eb[0, 0] == 16777217.0  # float32 accumulation would round this to 2**24


# --- shapes, values and memory ---------------------------------------------------------------------------


FLT_MAX = float(np.finfo(np.float32).max)


def overflowing_partition_61(calls: int = 1, row: int = 0):
    energy, cw = zeros(calls)
    energy[row, 374:447] = np.float32(FLT_MAX)  # 73 bins of partition 61: their cb sum exceeds the float32 range
    cw[row, 374:447] = 1.0
    return energy, cw


def test_cb_float32_overflow_is_rejected():
    # Regression (Codex review): finite inputs whose cb sum exceeds float32 must not yield inf.
    with pytest.raises(ValueError, match=r"cb exceeds the float32 range .*call 0.*partition 61"):
        dist10_partition_sums(*overflowing_partition_61())


def test_cb_overflow_is_a_value_error_under_a_raising_numpy_policy():
    with np.errstate(all="raise"), pytest.raises(ValueError, match="float32 range"):
        dist10_partition_sums(*overflowing_partition_61())


def test_cb_overflow_reports_the_first_affected_call_in_a_later_chunk():
    with pytest.raises(ValueError, match=r"call 300\b"):
        dist10_partition_sums(*overflowing_partition_61(calls=400, row=300))


def test_float64_product_overflow_is_rejected():
    energy, cw = zeros()
    energy[0, 10], cw[0, 10] = np.float32(1e30), 1e300  # cw * energy overflows float64
    with np.errstate(all="raise"), pytest.raises(ValueError, match="float32 range"):
        dist10_partition_sums(energy, cw)


def test_largest_finite_cb_is_kept():
    energy, cw = zeros()
    energy[0, 1], cw[0, 1] = np.float32(FLT_MAX), 1.0  # one bin of partition 1 reaches FLT_MAX exactly
    energy[0, 2], cw[0, 2] = np.float32(FLT_MAX), 1.0
    energy[0, 3], cw[0, 3] = np.float32(FLT_MAX), 1.0
    with np.errstate(all="raise"):
        result = dist10_partition_sums(energy, cw)
    assert result.cb[0, 1] == np.float32(FLT_MAX) and np.isfinite(result.cb).all()
    assert result.eb[0, 1] == FLT_MAX  # eb is float64: 513 x FLT_MAX would still be finite


def test_eb_cannot_overflow_float64_for_valid_energies():
    energy, cw = zeros()
    energy[0, :] = np.float32(FLT_MAX)
    cw[0, :] = 0.0  # keeps cb at zero, isolating eb
    with np.errstate(all="raise"):
        result = dist10_partition_sums(energy, cw)
    assert np.isfinite(result.eb).all() and result.eb[0, 0] == 49 * FLT_MAX


def test_empty_input_gives_empty_outputs():
    result = sums(np.zeros((0, 513)), np.zeros((0, 513)))
    assert result.eb.shape == (0, 63) and result.cb.shape == (0, 63)


def test_outputs_are_read_only_fresh_and_inputs_unchanged():
    energy = np.random.default_rng(1).random((3, 513)).astype(np.float32)
    cw = np.random.default_rng(2).random((3, 513))
    before = energy.copy(), cw.copy()
    result = dist10_partition_sums(energy, cw)
    np.testing.assert_array_equal(energy, before[0])
    np.testing.assert_array_equal(cw, before[1])
    for array in (result.eb, result.cb):
        assert not array.flags.writeable
        assert not np.shares_memory(array, energy) and not np.shares_memory(array, cw)


def test_many_calls_match_single_calls_across_chunks():
    rng = np.random.default_rng(4)
    energy = (rng.random((600, 513)) * 1e6).astype(np.float32)
    cw = rng.random((600, 513))
    result = dist10_partition_sums(energy, cw)
    for k in (0, 255, 256, 511, 512, 599):
        single = dist10_partition_sums(energy[k : k + 1], cw[k : k + 1])
        np.testing.assert_array_equal(result.eb[k], single.eb[0])
        np.testing.assert_array_equal(result.cb[k], single.cb[0])


def test_temporary_memory_does_not_grow_with_the_number_of_calls():
    rng = np.random.default_rng(6)

    def extra(calls: int) -> int:
        energy = rng.random((calls, 513)).astype(np.float32)
        cw = rng.random((calls, 513))
        tracemalloc.start()
        try:
            result = dist10_partition_sums(energy, cw)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        return peak - result.eb.nbytes - result.cb.nbytes

    extra(3)
    assert extra(4000) - extra(1000) < 1 << 20


def test_equality_is_identity():
    a, b = sums(*zeros()), sums(*zeros())
    assert a == a and a != b


@pytest.mark.parametrize(
    ("energy", "cw"),
    [
        (np.zeros((2, 513), dtype=np.float32), np.zeros((3, 513))),  # call count mismatch
        (np.zeros((2, 512), dtype=np.float32), np.zeros((2, 512))),  # bins
        (np.zeros((513,), dtype=np.float32), np.zeros((513,))),  # not 2-D
        (np.zeros((2, 513), dtype=np.float64), np.zeros((2, 513))),  # energy dtype
        (np.zeros((2, 513), dtype=np.float32), np.zeros((2, 513), dtype=np.float32)),  # cw dtype
        ([[0.0] * 513], np.zeros((1, 513))),  # not an ndarray
        (np.full((1, 513), np.nan, dtype=np.float32), np.zeros((1, 513))),
        (np.full((1, 513), np.inf, dtype=np.float32), np.zeros((1, 513))),
        (np.full((1, 513), -1.0, dtype=np.float32), np.zeros((1, 513))),
        (np.zeros((1, 513), dtype=np.float32), np.full((1, 513), np.nan)),
        (np.zeros((1, 513), dtype=np.float32), np.full((1, 513), -0.5)),
        (np.ma.masked_array(np.zeros((1, 513), dtype=np.float32)), np.zeros((1, 513))),
    ],
)
def test_rejects_invalid_inputs(energy, cw):
    with pytest.raises(ValueError):
        dist10_partition_sums(energy, cw)


# --- Python spectra and predictor (report only) ----------------------------------------------------------


def python_chain(name: str) -> PartitionSums:
    raw = np.frombuffer((FIXTURE / "inputs" / f"{name}.raw").read_bytes(), dtype="<i2").astype(np.int16)
    windows = frame_long_blocks(raw, sample_rate_hz=44100).windows
    long, short = long_window_spectrum(windows), short_window_spectrum(windows)
    cw = dist10_unpredictability(long.energy, long.phase, short.energy, short.phase)
    return dist10_partition_sums(long.energy, cw)


def test_python_chain_residuals_are_reported_and_the_threshold_case_is_retained():
    worst = {}
    retained = None
    for name in sorted({n for n, _ in CAPTURED}):
        result = python_chain(name)
        for r in records(name):
            eb_ref, cb_ref = np.array(r["eb"]), np.array(r["cb"], dtype=np.float32).astype(np.float64)
            strong = eb_ref >= 1e-6 * eb_ref.max()
            for quantity, ref, got in (("eb", eb_ref, result.eb[r["call"]]), ("cb", cb_ref, result.cb[r["call"]])):
                diff = np.abs(got.astype(np.float64) - ref)
                rel = np.divide(diff, np.abs(ref), out=np.zeros_like(diff), where=ref != 0)
                b = int(np.argmax(np.where(strong, rel, -1.0)))
                if rel[b] > worst.get(quantity, (0.0,))[0]:
                    worst[quantity] = (float(rel[b]), name, r["call"], b)
                if (name, r["call"]) == THRESHOLD_COMPARISON_CASE[:2] and quantity == "cb":
                    retained = float(rel[THRESHOLD_COMPARISON_CASE[2]])
    for quantity, (rel, name, call, b) in worst.items():
        print(f"\nstrong-partition worst relative {quantity} residual {rel:.3e} at {name} call {call} partition {b}")
    print(f"retained threshold-comparison case {THRESHOLD_COMPARISON_CASE}: relative cb residual {retained:.3e}")
    assert retained is not None and np.isfinite(retained)
