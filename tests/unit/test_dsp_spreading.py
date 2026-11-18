"""Tests for the dist10 spreading and tonality-input kernel (front-end contract §3.4; Task 3J).

Reference values are the Probe F capture in ``tests/fixtures/mpeg_psy_model2_dist10/expected_spreading`` (dist10's
s3_l matrix, ecb, ctb and the tonality-loop ratio values) with its inputs eb/cb from Probe E. The generated matrix is
computed with this platform's libm, so it is compared with the captured one within the verifier's 1e-15 relative
bound; spread values are compared exactly when the captured matrix is supplied, and within reported bounds with the
generated one. A separate report runs the package's full chain (no tolerance; burst call 4 and masker call 3
retained). Other inputs are small synthetic arrays (unit-test signals, not audio material or evaluation data).
"""

import json
import math
import pathlib
import tracemalloc
import warnings

import numpy as np
import pytest

from autonomous_mixing.adapters.dsp_analysis.framing import frame_long_blocks
from autonomous_mixing.adapters.dsp_analysis.long_spectrum import long_window_spectrum
from autonomous_mixing.adapters.dsp_analysis.partitions import dist10_partition_sums
from autonomous_mixing.adapters.dsp_analysis.short_spectrum import short_window_spectrum
from autonomous_mixing.adapters.dsp_analysis.spreading import (
    DIST10_LONG_BVAL_44100,
    LN_TO_LOG10,
    RATIO_FLOOR,
    SpreadTonalityInput,
    dist10_spread_tonality_input,
    dist10_spreading_matrix,
)
from autonomous_mixing.adapters.dsp_analysis.unpredictability import dist10_unpredictability

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "mpeg_psy_model2_dist10"
MATRIX_BOUND = 1e-15  # relative, libm rounding (verify_fixture.py)
RETAINED = (("burst_1k_a8192_onset2000", 4, 50), ("masker_1k_a8192", 3, 50))


def records(folder: str, name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURE / folder / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]


CAPTURED_S3 = json.loads((FIXTURE / "expected_spreading" / "s3_44100.json").read_text())
CASES = []
for _path in sorted((FIXTURE / "expected_spreading").glob("*.jsonl")):
    _partition = {r["call"]: r for r in records("expected_partition", _path.stem)}
    for _r in records("expected_spreading", _path.stem):
        CASES.append((_path.stem, _r, _partition[_r["call"]]))
IDS = [f"{name}-call{f['call']}" for name, f, _ in CASES]


def inputs(e: dict):
    return np.array([e["eb"]], dtype=np.float64), np.array([e["cb"]], dtype=np.float32)


# --- matrix ---------------------------------------------------------------------------------------------


def test_bval_constants_are_the_pinned_psy_data_values():
    assert len(DIST10_LONG_BVAL_44100) == 63
    assert list(DIST10_LONG_BVAL_44100) == [float(v) for v in CAPTURED_S3["psy_data_44100_long_bval"]]
    assert LN_TO_LOG10 == 0.2302585093 and RATIO_FLOOR == 0.01


def test_generated_matrix_matches_probe_f():
    generated, captured = dist10_spreading_matrix(), np.array(CAPTURED_S3["s3_l"])
    assert generated.shape == (63, 63) and generated.dtype == np.float64 and not generated.flags.writeable
    np.testing.assert_array_equal(generated == 0, captured == 0)
    nonzero = captured != 0
    assert np.max(np.abs(generated[nonzero] - captured[nonzero]) / captured[nonzero]) <= MATRIX_BOUND
    assert int((generated == captured).sum()) >= 3900  # most entries are bit-identical; the rest differ by libm


def test_matrix_is_asymmetric_with_rows_as_outputs():
    s3 = dist10_spreading_matrix()
    assert not np.array_equal(s3, s3.T)
    # Row 50 (output) spreads more weakly from higher inputs (slope x3) than from lower ones (slope x1.5).
    assert s3[50, 51] < s3[50, 49] and s3[50, 52] < s3[50, 48]
    assert np.allclose(np.diag(s3), 1.0, atol=1e-7)


def test_cutoff_is_tested_before_the_bump_is_added():
    s3 = dist10_spreading_matrix()
    for i in range(63):
        for j in range(63):
            tempx = (DIST10_LONG_BVAL_44100[i] - DIST10_LONG_BVAL_44100[j]) * (3.0 if j >= i else 1.5) + 0.474
            tempy = 15.811389 + 7.5 * tempx - 17.5 * math.sqrt(1.0 + tempx * tempx)
            assert (s3[i, j] == 0.0) == (tempy <= -60.0), (i, j)


def test_caller_cannot_change_the_matrix_the_kernel_uses():
    eb, cb = inputs(CASES[0][2])
    before = dist10_spread_tonality_input(eb, cb)
    leaked = dist10_spreading_matrix()
    leaked.flags.writeable = True
    leaked[:] = 0.0
    after = dist10_spread_tonality_input(eb, cb)
    np.testing.assert_array_equal(after.ecb, before.ecb)


# --- captured inputs ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "f", "e"), CASES, ids=IDS)
def test_exact_with_the_captured_matrix(name, f, e):
    result = dist10_spread_tonality_input(*inputs(e), spreading_matrix=np.array(CAPTURED_S3["s3_l"]))
    np.testing.assert_array_equal(result.ecb[0], np.array(f["ecb"], dtype=np.float32))
    assert result.ctb[0].tolist() == f["ctb"]
    assert result.divided[0].astype(int).tolist() == f["ratio_divided"]
    assert result.ratio[0].tolist() == f["ratio"]
    assert np.max(np.abs(result.log_ratio[0] - np.array(f["tonality_input"]))) <= 1e-15


@pytest.mark.parametrize(("name", "f", "e"), CASES, ids=IDS)
def test_close_with_the_generated_matrix(name, f, e):
    result = dist10_spread_tonality_input(*inputs(e))
    ecb_ref = np.array(f["ecb"], dtype=np.float32)
    ulps = np.abs(result.ecb[0].view(np.int32).astype(np.int64) - ecb_ref.view(np.int32).astype(np.int64))
    assert ulps.max() <= 1, ulps.max()  # libm-level matrix differences move ecb by at most one float32 ulp
    assert np.max(np.abs(result.ctb[0] - np.array(f["ctb"])) / np.abs(np.array(f["ctb"]) + 1e-300)) <= 1e-14
    assert np.max(np.abs(result.log_ratio[0] - np.array(f["tonality_input"]))) <= 1e-14  # observed <= 4.4e-16


def test_clamp_is_applied_on_captured_silence():
    name, f, e = next(c for c in CASES if c[0] == "silence_12granules" and c[1]["call"] == 2)
    result = dist10_spread_tonality_input(*inputs(e), spreading_matrix=np.array(CAPTURED_S3["s3_l"]))
    clamped = result.ratio[0] < RATIO_FLOOR
    assert clamped.sum() == 46
    np.testing.assert_allclose(result.log_ratio[0, clamped], math.log(0.01), atol=1e-15)


# --- synthetic cases --------------------------------------------------------------------------------------


def test_spreading_orientation_and_order_with_a_single_input_partition():
    eb = np.zeros((1, 63))
    cb = np.zeros((1, 63), dtype=np.float32)
    eb[0, 50], cb[0, 50] = 1000.0, np.float32(500.0)
    s3 = dist10_spreading_matrix()
    result = dist10_spread_tonality_input(eb, cb)
    np.testing.assert_array_equal(result.ecb[0], (s3[:, 50] * 1000.0).astype(np.float32))
    np.testing.assert_array_equal(result.ctb[0], s3[:, 50] * 500.0)
    transposed = (s3[50, :] * 1000.0).astype(np.float32)
    assert not np.array_equal(result.ecb[0], transposed)


def test_ecb_is_rounded_to_float32_after_every_term():
    # Output partition 0 receives eb[0] * s3[0][0] (about 2**24) and then 1.0-sized terms: float32 rounding after each
    # term drops them, double accumulation would keep them.
    s3 = np.zeros((63, 63))
    s3[0, :4] = 1.0
    eb = np.zeros((1, 63))
    eb[0, :4] = [16777216.0, 1.0, 1.0, 1.0]
    cb = np.zeros((1, 63), dtype=np.float32)
    result = dist10_spread_tonality_input(eb, cb, spreading_matrix=s3)
    assert result.ecb[0, 0] == np.float32(16777216.0)
    eb[0, :4] = [1.0, 1.0, 1.0, 16777216.0]  # ascending order: the small terms come first and survive
    assert dist10_spread_tonality_input(eb, cb, spreading_matrix=s3).ecb[0, 0] == np.float32(16777219.0)


def test_zero_energy_branch_skips_the_division():
    eb = np.zeros((2, 63))
    cb = np.zeros((2, 63), dtype=np.float32)
    eb[1, 30], cb[1, 30] = 4.0, np.float32(2.0)
    with np.errstate(all="raise"):
        result = dist10_spread_tonality_input(eb, cb)
    assert not result.divided[0].any() and not result.ratio[0].any() and not result.log_ratio[0].any()
    divided = result.divided[1]
    assert np.array_equal(divided, result.ecb[1] != 0) and divided.any() and not divided.all()
    assert not result.log_ratio[1, ~divided].any()
    np.testing.assert_allclose(result.ratio[1, 30], 0.5, rtol=1e-7)


def test_ratio_below_floor_is_clamped_before_the_log():
    eb = np.zeros((1, 63))
    cb = np.zeros((1, 63), dtype=np.float32)
    eb[0, :] = 1.0
    cb[0, :] = np.float32(0.001)  # ratio 0.001 everywhere
    result = dist10_spread_tonality_input(eb, cb)
    assert np.all(result.ratio[0] < 0.01)
    np.testing.assert_allclose(result.log_ratio[0], math.log(0.01), rtol=0, atol=1e-15)


def test_no_upper_clamp():
    eb = np.ones((1, 63))
    cb = np.full((1, 63), np.float32(5.0))
    result = dist10_spread_tonality_input(eb, cb)
    assert np.all(result.ratio[0] > 4.9) and np.all(result.log_ratio[0] > math.log(4.9))


# --- outputs, memory and validation ---------------------------------------------------------------------


def test_outputs_types_read_only_and_inputs_unchanged():
    eb, cb = inputs(CASES[0][2])
    before = eb.copy(), cb.copy()
    result = dist10_spread_tonality_input(eb, cb)
    assert isinstance(result, SpreadTonalityInput)
    assert (result.ecb.dtype, result.ctb.dtype, result.divided.dtype) == (np.float32, np.float64, np.bool_)
    assert (result.ratio.dtype, result.log_ratio.dtype) == (np.float64, np.float64)
    for array in (result.ecb, result.ctb, result.divided, result.ratio, result.log_ratio):
        assert array.shape == (1, 63) and not array.flags.writeable
        assert not np.shares_memory(array, eb) and not np.shares_memory(array, cb)
    np.testing.assert_array_equal(eb, before[0])
    np.testing.assert_array_equal(cb, before[1])


def test_empty_input():
    result = dist10_spread_tonality_input(np.zeros((0, 63)), np.zeros((0, 63), dtype=np.float32))
    assert result.ecb.shape == (0, 63) and result.log_ratio.shape == (0, 63)


def test_chunk_boundaries_match_single_calls():
    rng = np.random.default_rng(9)
    eb = rng.random((600, 63)) * 1e6
    cb = (rng.random((600, 63)) * 1e5).astype(np.float32)
    result = dist10_spread_tonality_input(eb, cb)
    for k in (0, 255, 256, 511, 512, 599):
        single = dist10_spread_tonality_input(eb[k : k + 1], cb[k : k + 1])
        for name in ("ecb", "ctb", "divided", "ratio", "log_ratio"):
            np.testing.assert_array_equal(getattr(result, name)[k], getattr(single, name)[0])


def test_temporary_memory_does_not_grow_with_the_number_of_calls():
    rng = np.random.default_rng(10)

    def extra(calls: int) -> int:
        eb = rng.random((calls, 63))
        cb = rng.random((calls, 63)).astype(np.float32)
        tracemalloc.start()
        try:
            r = dist10_spread_tonality_input(eb, cb)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        return peak - sum(a.nbytes for a in (r.ecb, r.ctb, r.divided, r.ratio, r.log_ratio))

    extra(3)
    assert extra(20000) - extra(5000) < 1 << 20


def test_ecb_float32_overflow_is_rejected():
    eb = np.zeros((1, 63))
    eb[0, :] = 1e40  # partition sums can exceed float32 (513 x float32 max is about 1.7e41)
    with np.errstate(all="raise"), pytest.raises(ValueError, match=r"ecb exceeds the float32 range .*call 0"):
        dist10_spread_tonality_input(eb, np.zeros((1, 63), dtype=np.float32))


FLT_MAX = float(np.finfo(np.float32).max)


def test_ctb_float64_overflow_is_rejected():
    # Regression (Codex review): a supplied matrix with large finite entries can overflow ctb, which must not be inf.
    matrix = np.zeros((63, 63))
    matrix[7, 3] = 1e300
    cb = np.zeros((1, 63), dtype=np.float32)
    cb[0, 3] = np.float32(FLT_MAX)
    with (
        np.errstate(all="raise"),
        pytest.raises(ValueError, match=r"ctb exceeds the float64 range .*call 0.*partition 7"),
    ):
        dist10_spread_tonality_input(np.zeros((1, 63)), cb, spreading_matrix=matrix)


def test_ratio_float64_overflow_is_rejected():
    # A tiny nonzero ecb (float32 subnormal) under a huge finite ctb overflows ctb/ecb; it must not become inf.
    matrix = np.zeros((63, 63))
    matrix[0, 0] = 1e230
    eb = np.zeros((1, 63))
    eb[0, 0] = 1e-275  # ecb = float32(1e-45), the smallest subnormal: nonzero, so the division runs
    cb = np.zeros((1, 63), dtype=np.float32)
    cb[0, 0] = np.float32(FLT_MAX)  # ctb is about 3.4e268, finite
    with (
        np.errstate(all="raise"),
        pytest.raises(ValueError, match=r"ctb/ecb exceeds the float64 range .*call 0.*partition 0"),
    ):
        dist10_spread_tonality_input(eb, cb, spreading_matrix=matrix)


def both_accumulators_overflow():
    matrix = np.zeros((63, 63))
    matrix[4, 2] = 1e300
    eb = np.zeros((1, 63))
    eb[0, 2] = 1e300  # ecb overflows float32 and ctb overflows float64 in the same output partition
    cb = np.zeros((1, 63), dtype=np.float32)
    cb[0, 2] = np.float32(FLT_MAX)
    return eb, cb, matrix


def test_accumulator_overflow_is_reported_before_dividing_under_a_raising_policy():
    # Regression (Codex review): inf / inf in the division must not be reached; the accumulator check comes first.
    eb, cb, matrix = both_accumulators_overflow()
    with np.errstate(all="raise"), pytest.raises(ValueError, match=r"ecb exceeds the float32 range .*partition 4"):
        dist10_spread_tonality_input(eb, cb, spreading_matrix=matrix)


def test_accumulator_overflow_raises_no_division_warning():
    eb, cb, matrix = both_accumulators_overflow()
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # an "invalid value encountered in divide" warning would fail here
        with pytest.raises(ValueError, match="ecb exceeds the float32 range"):
            dist10_spread_tonality_input(eb, cb, spreading_matrix=matrix)


def test_ctb_overflow_with_finite_ecb_is_reported_as_ctb():
    # Output partition 9: ecb = s3[9][9] * eb[9] = 1 (finite), ctb = s3[9][1] * cb[1] = 1e300 * float32 max (inf).
    matrix = np.zeros((63, 63))
    matrix[9, 9], matrix[9, 1] = 1.0, 1e300
    eb = np.zeros((1, 63))
    eb[0, 9] = 1.0
    cb = np.zeros((1, 63), dtype=np.float32)
    cb[0, 1] = np.float32(FLT_MAX)
    with np.errstate(all="raise"), pytest.raises(ValueError, match=r"ctb exceeds the float64 range .*partition 9"):
        dist10_spread_tonality_input(eb, cb, spreading_matrix=matrix)


def test_overflow_reports_the_first_affected_call_in_a_later_chunk():
    matrix = np.zeros((63, 63))
    matrix[7, 3] = 1e300
    cb = np.zeros((1500, 63), dtype=np.float32)
    cb[1200, 3] = np.float32(FLT_MAX)
    with pytest.raises(ValueError, match=r"call 1200\b"):
        dist10_spread_tonality_input(np.zeros((1500, 63)), cb, spreading_matrix=matrix)


def test_largest_inputs_with_the_dist10_matrix_stay_finite():
    # With the default matrix (entries <= 1) ctb <= 63 x float32 max and the ratio <= that over the smallest nonzero
    # float32 ecb, both far below the float64 limit.
    eb = np.full((1, 63), 1e-300)  # every ecb rounds to float32 zero: no division
    cb = np.full((1, 63), np.float32(FLT_MAX))
    with np.errstate(all="raise"):
        result = dist10_spread_tonality_input(eb, cb)
    assert np.isfinite(result.ctb).all() and not result.divided.any()
    eb = np.full((1, 63), 2e-45)  # ecb near the float32 subnormal minimum: divisions run
    with np.errstate(all="raise"):
        result = dist10_spread_tonality_input(eb, cb)
    assert result.divided.all() and np.isfinite(result.ratio).all() and np.isfinite(result.log_ratio).all()


@pytest.mark.parametrize(
    ("eb", "cb"),
    [
        (np.zeros((2, 63)), np.zeros((3, 63), dtype=np.float32)),
        (np.zeros((2, 62)), np.zeros((2, 62), dtype=np.float32)),
        (np.zeros((63,)), np.zeros((63,), dtype=np.float32)),
        (np.zeros((1, 63), dtype=np.float32), np.zeros((1, 63), dtype=np.float32)),
        (np.zeros((1, 63)), np.zeros((1, 63))),
        ([[0.0] * 63], np.zeros((1, 63), dtype=np.float32)),
        (np.full((1, 63), np.nan), np.zeros((1, 63), dtype=np.float32)),
        (np.full((1, 63), -1.0), np.zeros((1, 63), dtype=np.float32)),
        (np.zeros((1, 63)), np.full((1, 63), np.inf, dtype=np.float32)),
        (np.zeros((1, 63)), np.full((1, 63), -1.0, dtype=np.float32)),
        (np.ma.masked_array(np.zeros((1, 63))), np.zeros((1, 63), dtype=np.float32)),
    ],
)
def test_rejects_invalid_inputs(eb, cb):
    with pytest.raises(ValueError):
        dist10_spread_tonality_input(eb, cb)


@pytest.mark.parametrize(
    "matrix",
    [np.zeros((63, 62)), np.zeros((63, 63), dtype=np.float32), np.full((63, 63), np.nan), np.full((63, 63), -1.0)],
)
def test_rejects_invalid_matrices(matrix):
    with pytest.raises(ValueError):
        dist10_spread_tonality_input(np.zeros((1, 63)), np.zeros((1, 63), dtype=np.float32), spreading_matrix=matrix)


# --- full Python chain (report only) ---------------------------------------------------------------------


def python_chain(name: str) -> SpreadTonalityInput:
    raw = np.frombuffer((FIXTURE / "inputs" / f"{name}.raw").read_bytes(), dtype="<i2").astype(np.int16)
    windows = frame_long_blocks(raw, sample_rate_hz=44100).windows
    long, short = long_window_spectrum(windows), short_window_spectrum(windows)
    cw = dist10_unpredictability(long.energy, long.phase, short.energy, short.phase)
    sums = dist10_partition_sums(long.energy, cw)
    return dist10_spread_tonality_input(sums.eb, sums.cb)


def test_full_python_chain_residuals_are_reported_with_the_retained_cases():
    worst, retained = {}, {}
    for name in sorted({c[0] for c in CASES}):
        result = python_chain(name)
        for n, f, _ in CASES:
            if n != name:
                continue
            k = f["call"]
            refs = {
                "ecb": (np.array(f["ecb"], dtype=np.float32).astype(np.float64), result.ecb[k].astype(np.float64)),
                "ctb": (np.array(f["ctb"]), result.ctb[k]),
                "ratio": (np.array(f["ratio"]), result.ratio[k]),
            }
            for quantity, (ref, got) in refs.items():
                diff = np.abs(got - ref)
                rel = np.divide(diff, np.abs(ref), out=np.zeros_like(diff), where=ref != 0)
                b = int(np.argmax(rel))
                if rel[b] > worst.get(quantity, (0.0,))[0]:
                    worst[quantity] = (float(rel[b]), name, k, b)
            log_diff = np.abs(result.log_ratio[k] - np.array(f["tonality_input"]))
            b = int(np.argmax(log_diff))
            if log_diff[b] > worst.get("log ratio (absolute)", (0.0,))[0]:
                worst["log ratio (absolute)"] = (float(log_diff[b]), name, k, b)
            for case in RETAINED:
                if case[:2] == (name, k):
                    p = case[2]
                    retained[case] = (
                        abs(result.ratio[k, p] - f["ratio"][p]) / f["ratio"][p],
                        float(log_diff[p]),
                    )
    for quantity, (err, name, call, b) in worst.items():
        print(f"\nfull chain worst {quantity}: {err:.3e} at {name} call {call} output partition {b}")
    for case, (rel_ratio, log_abs) in retained.items():
        print(f"retained {case}: ratio relative {rel_ratio:.3e}, log ratio absolute {log_abs:.3e}")
    assert set(retained) == set(RETAINED)
    assert all(math.isfinite(v) for pair in retained.values() for v in pair)
