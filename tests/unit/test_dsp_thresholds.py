"""Tests for the dist10 partition-threshold kernel (front-end contract §3.5; Task 3L).

Reference values are the Probe G capture in ``tests/fixtures/mpeg_psy_model2_dist10/expected_threshold``: for every
call of the 1 kHz tone (calls 0-5) and the burst (calls 0-7), consecutive from start-up, the spread energy ``ecb``,
the tonality input, the clamped tonality, SNR, the float32 ``nb``, the pre-echo limit and ``thr``, and the tables in
``tables_44100.json``. Each clip is replayed from call 0 with the captured spread inputs, so the history is the
kernel's own. A separate report runs the package's full chain (no tolerance; tone call 3 and burst call 4,
partition 50 retained). Other inputs are small synthetic arrays (unit-test signals, not audio material or evaluation
data).
"""

import json
import math
import pathlib
import tracemalloc

import numpy as np
import pytest

from autonomous_mixing.adapters.dsp_analysis.framing import frame_long_blocks
from autonomous_mixing.adapters.dsp_analysis.long_spectrum import long_window_spectrum
from autonomous_mixing.adapters.dsp_analysis.partitions import dist10_partition_sums
from autonomous_mixing.adapters.dsp_analysis.short_spectrum import short_window_spectrum
from autonomous_mixing.adapters.dsp_analysis.spreading import dist10_spread_tonality_input
from autonomous_mixing.adapters.dsp_analysis.thresholds import (
    DIST10_LONG_MINVAL_44100,
    DIST10_LONG_NORM_44100,
    DIST10_LONG_QTHR_44100,
    LN_TO_LOG10,
    PartitionThresholds,
    dist10_partition_thresholds,
)
from autonomous_mixing.adapters.dsp_analysis.unpredictability import dist10_unpredictability

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "mpeg_psy_model2_dist10"
TABLES = json.loads((FIXTURE / "expected_threshold" / "tables_44100.json").read_text())
QTHR = np.array(TABLES["qthr_l"])
NAMED = (("masker_1k_a8192", 3, 50), ("burst_1k_a8192_onset2000", 4, 50))
SHORT_PATH_CALLS = {
    ("masker_1k_a8192", 0),
    ("masker_1k_a8192", 1),
    ("burst_1k_a8192_onset2000", 4),
    ("burst_1k_a8192_onset2000", 5),
}  # pe >= 1800 in expected_long_mapping
OUTPUTS = ("tonality", "snr", "nb", "pre_echo_limit", "thr")


def records(folder: str, name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURE / folder / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]


CLIPS = {
    p.stem: records("expected_threshold", p.stem) for p in sorted((FIXTURE / "expected_threshold").glob("*.jsonl"))
}
CASES = [(name, r) for name, calls in CLIPS.items() for r in calls]
IDS = [f"{name}-call{r['call']}" for name, r in CASES]


def clip_inputs(name: str):
    calls = CLIPS[name]
    assert [r["call"] for r in calls] == list(range(len(calls)))  # consecutive from call 0
    ecb = np.array([r["ecb"] for r in calls], dtype=np.float32)
    log_ratio = np.array([r["tonality_input"] for r in calls], dtype=np.float64)
    return ecb, log_ratio


REPLAYED = {name: dist10_partition_thresholds(*clip_inputs(name)) for name in CLIPS}


def reference(ecb: np.ndarray, log_ratio: np.ndarray):
    """Scalar transcription of l3psy.c ll. 602-616 with dist10's macros, for synthetic cross-checks."""
    nb_1, nb_2 = [0.0] * 63, [0.0] * 63
    out = {k: [] for k in OUTPUTS}
    for k in range(ecb.shape[0]):
        row = {key: [] for key in OUTPUTS}
        new_nb = []
        for b in range(63):
            t = -0.299 - 0.43 * float(log_ratio[k, b])
            t = 0.0 if 0.0 > t else t
            t = 1.0 if 1.0 < t else t
            tonal = 29.0 * t + 6.0 * (1.0 - t)
            snr = DIST10_LONG_MINVAL_44100[b] if DIST10_LONG_MINVAL_44100[b] > tonal else tonal
            nb = float(np.float32(float(ecb[k, b]) * DIST10_LONG_NORM_44100[b] * math.exp(-snr * LN_TO_LOG10)))
            two, sixteen = 2.0 * nb_1[b], 16.0 * nb_2[b]
            inner = two if two < sixteen else sixteen
            limit = nb if nb < inner else inner
            thr = DIST10_LONG_QTHR_44100[b] if DIST10_LONG_QTHR_44100[b] > limit else limit
            for key, value in zip(OUTPUTS, (t, snr, nb, limit, thr), strict=True):
                row[key].append(value)
            new_nb.append(nb)
        nb_1, nb_2 = new_nb, nb_1
        for key in OUTPUTS:
            out[key].append(row[key])
    return {key: np.array(v) for key, v in out.items()}


# --- tables ------------------------------------------------------------------------------------------------


def test_tables_are_the_pinned_psy_data_values():
    for constant, key, text in (
        (DIST10_LONG_MINVAL_44100, "minval", "minval"),
        (DIST10_LONG_QTHR_44100, "qthr_l", "qthr"),
        (DIST10_LONG_NORM_44100, "norm_l", "norm"),
    ):
        assert len(constant) == 63
        assert list(constant) == TABLES[key] == [float(s) for s in TABLES[f"psy_data_44100_long_{text}"]]
    assert LN_TO_LOG10 == 0.2302585093


# --- captured calls, replayed from call 0 ----------------------------------------------------------------


@pytest.mark.parametrize(("name", "r"), CASES, ids=IDS)
def test_replay_matches_probe_g_exactly(name, r):
    result, k = REPLAYED[name], r["call"]
    assert result.tonality[k].tolist() == r["tonality"]
    assert result.snr[k].tolist() == r["snr"]
    np.testing.assert_array_equal(result.nb[k], np.array(r["nb"], dtype=np.float32))
    assert result.pre_echo_limit[k].tolist() == r["pre_echo_limit"]
    assert result.thr[k].tolist() == r["thr"]


def test_replay_covers_startup_short_path_and_every_deciding_term():
    assert {(n, r["call"]) for n, r in CASES} >= SHORT_PATH_CALLS
    deciding = set()
    for name, r in CASES:
        result, k = REPLAYED[name], r["call"]
        nb = result.nb[k].astype(np.float64)
        two, sixteen = 2.0 * np.array(r["nb_1_before"]), 16.0 * np.array(r["nb_2_before"])
        for b in range(63):
            if QTHR[b] > result.pre_echo_limit[k, b]:
                deciding.add("qthr")
            else:
                deciding.add(min((nb[b], "nb"), (two[b], "2nb_1"), (sixteen[b], "16nb_2"))[1])
    assert deciding == {"qthr", "nb", "2nb_1", "16nb_2"}


def test_history_read_equals_the_captured_history():
    for name, calls in CLIPS.items():
        nb = REPLAYED[name].nb.astype(np.float64)
        for r in calls:
            k = r["call"]
            assert (nb[k - 1] if k >= 1 else np.zeros(63)).tolist() == r["nb_1_before"]
            assert (nb[k - 2] if k >= 2 else np.zeros(63)).tolist() == r["nb_2_before"]


def test_identical_window_with_different_history_gives_different_thresholds():
    # Tone call 5 and burst call 5 analyse the same window; only the history differs (silent for the burst).
    tone, burst = REPLAYED["masker_1k_a8192"], REPLAYED["burst_1k_a8192_onset2000"]
    np.testing.assert_array_equal(tone.nb[5], burst.nb[5])
    np.testing.assert_array_equal(burst.thr[5], QTHR)
    assert (tone.thr[5] > QTHR).sum() == 60


# --- rule-level synthetic cases ---------------------------------------------------------------------------

B = 40  # a partition with minval 0, qthr 0.045 and norm 0.548


def single_partition(nb_targets: list[float]) -> tuple[np.ndarray, np.ndarray]:
    """ecb rows that give exactly these float32 nb values at partition B for a pure-tone tonality input (tbb = 1)."""
    log_ratio = np.full((len(nb_targets), 63), math.log(0.01))  # raw tbb = 1.681 -> clamped 1 -> SNR 29 dB
    factor = DIST10_LONG_NORM_44100[B] * math.exp(-29.0 * LN_TO_LOG10)
    ecb = np.zeros((len(nb_targets), 63), dtype=np.float32)
    ecb[:, B] = [np.float32(v / factor) for v in nb_targets]
    return ecb, log_ratio


def test_startup_calls_use_zero_history_so_threshold_in_quiet_decides():
    ecb, log_ratio = single_partition([1e6, 1e6, 1e6])
    result = dist10_partition_thresholds(ecb, log_ratio)
    assert result.nb[0, B] > 1e5 and result.nb[1, B] > 1e5
    assert result.pre_echo_limit[0, B] == 0.0 and result.pre_echo_limit[1, B] == 0.0  # minimum(nb, 0, 0)
    assert result.thr[0, B] == QTHR[B] and result.thr[1, B] == QTHR[B]
    assert result.thr[2, B] == result.nb[2, B]  # established history: nb itself


def test_history_age_order_two_times_previous_and_sixteen_times_second_previous():
    # nb: call 0 = 100, call 1 = 1, call 2 = 1000. Correct: min(1000, 2 x 1, 16 x 100) = 2.
    # Swapped ages would give min(1000, 2 x 100, 16 x 1) = 16.
    ecb, log_ratio = single_partition([100.0, 1.0, 1000.0])
    result = dist10_partition_thresholds(ecb, log_ratio)
    nb = result.nb[:, B].astype(np.float64)
    assert result.pre_echo_limit[2, B] == 2.0 * nb[1]
    ecb, log_ratio = single_partition([1.0, 100.0, 1000.0])  # now 16 x nb(call 0) = 16 < 2 x nb(call 1) = 200
    result = dist10_partition_thresholds(ecb, log_ratio)
    assert result.pre_echo_limit[2, B] == 16.0 * float(result.nb[0, B])


def test_history_stores_the_unbounded_nb_not_the_threshold():
    # Call 1: nb = 0.001, below qthr (0.045), so thr = qthr. Call 2 must be limited by 2 x 0.001, not 2 x qthr.
    ecb, log_ratio = single_partition([1e6, 0.001, 1e6, 1e6])
    result = dist10_partition_thresholds(ecb, log_ratio)
    assert result.thr[1, B] == QTHR[B] and result.nb[1, B] < QTHR[B]
    assert result.pre_echo_limit[2, B] == 2.0 * float(result.nb[1, B])
    assert result.thr[2, B] == QTHR[B]  # the quiet floor lifts the limited value
    stored = float(result.nb[1, B])  # call 3 reads it as nb_2: 16 x nb, not 16 x qthr
    assert result.pre_echo_limit[3, B] == 16.0 * stored < 16.0 * QTHR[B]


def test_quiet_floor_is_applied_after_the_pre_echo_limit():
    # Call 2: nb large, history tiny -> limit tiny -> thr = qthr. Floor first would give min(max(qthr, nb), tiny).
    ecb, log_ratio = single_partition([1e-6, 1e-6, 1e6])
    result = dist10_partition_thresholds(ecb, log_ratio)
    assert result.pre_echo_limit[2, B] < 1e-5 < QTHR[B]
    assert result.thr[2, B] == QTHR[B]


def test_nb_is_stored_as_float32_before_limiting():
    rng = np.random.default_rng(3)
    ecb = (rng.random((40, 63)) * 1e7).astype(np.float32)
    log_ratio = rng.uniform(math.log(0.01), 1.0, (40, 63))
    result = dist10_partition_thresholds(ecb, log_ratio)
    assert result.nb.dtype == np.float32
    nb = result.nb.astype(np.float64)
    two = np.vstack([np.zeros((1, 63)), 2.0 * nb[:-1]])
    sixteen = np.vstack([np.zeros((2, 63)), 16.0 * nb[:-2]])
    np.testing.assert_array_equal(result.pre_echo_limit, np.minimum(nb, np.minimum(two, sixteen)))


def test_tonality_clamp_and_snr_floor():
    log_ratio = np.zeros((1, 63))
    log_ratio[0, :3] = [math.log(0.01), 0.0, 5.0]  # raw tbb 1.681 (-> 1), -0.299 (-> 0), -2.449 (-> 0)
    log_ratio[0, 3] = -0.5  # raw -0.084 (-> 0); minval 24.5 at partition 3 decides the SNR
    log_ratio[0, 20] = -1.5  # raw 0.346, interior; tonal 13.97 dB > minval 3
    result = dist10_partition_thresholds(np.ones((1, 63), dtype=np.float32), log_ratio)
    assert result.tonality[0, :4].tolist() == [1.0, 0.0, 0.0, 0.0]
    assert result.snr[0, 0] == 29.0 and result.snr[0, 1] == 24.5 and result.snr[0, 3] == 24.5
    t = -0.299 - 0.43 * -1.5
    assert result.tonality[0, 20] == t and result.snr[0, 20] == 29.0 * t + 6.0 * (1.0 - t)
    assert result.snr[0, 30] == 6.0  # minval 0, tbb 0 (log ratio 0.0, the reference's skipped-division value)


def test_matches_the_scalar_transcription_on_random_input():
    rng = np.random.default_rng(4)
    ecb = (rng.random((30, 63)) * 1e9).astype(np.float32)
    ecb[rng.random((30, 63)) < 0.2] = 0.0
    log_ratio = rng.uniform(math.log(0.01), 2.0, (30, 63))
    result, ref = dist10_partition_thresholds(ecb, log_ratio), reference(ecb, log_ratio)
    for key in OUTPUTS:
        got = getattr(result, key).astype(np.float64)
        if key == "nb":  # numpy exp against math.exp: at most one float32 ulp after the store
            ulps = np.abs(result.nb.view(np.int32).astype(np.int64) - ref["nb"].astype(np.float32).view(np.int32))
            assert ulps.max() <= 1
        elif key in ("tonality", "snr"):
            np.testing.assert_array_equal(got, ref[key])
        else:
            np.testing.assert_allclose(got, ref[key], rtol=2.0**-23, atol=0)


def test_independent_clips_start_from_zero_history():
    a = single_partition([1e6] * 5)
    b = single_partition([1e3] * 5)
    joined = dist10_partition_thresholds(np.vstack([a[0], b[0]]), np.vstack([a[1], b[1]]))
    alone = dist10_partition_thresholds(*b)
    assert alone.thr[0, B] == alone.thr[1, B] == QTHR[B]  # each call of the kernel is one clip
    assert joined.thr[5, B] != alone.thr[0, B]  # in one clip, b's first call is limited by a's history
    np.testing.assert_array_equal(dist10_partition_thresholds(*b).thr, alone.thr)  # no state between invocations
    for key in OUTPUTS[:3]:
        np.testing.assert_array_equal(getattr(joined, key)[5:], getattr(alone, key))


def test_chunk_boundaries_preserve_history():
    rng = np.random.default_rng(9)
    calls = 2600
    ecb = (rng.random((calls, 63)) * 1e6).astype(np.float32)
    log_ratio = rng.uniform(math.log(0.01), 1.0, (calls, 63))
    result = dist10_partition_thresholds(ecb, log_ratio)
    nb = result.nb.astype(np.float64)
    for k in (1023, 1024, 1025, 2047, 2048, 2049, calls - 1):
        expected = np.minimum(nb[k], np.minimum(2.0 * nb[k - 1], 16.0 * nb[k - 2]))
        np.testing.assert_array_equal(result.pre_echo_limit[k], expected)
        np.testing.assert_array_equal(result.thr[k], np.where(QTHR > expected, QTHR, expected))
        single = dist10_partition_thresholds(ecb[k - 2 : k + 1], log_ratio[k - 2 : k + 1])
        np.testing.assert_array_equal(single.thr[2], result.thr[k])


def test_temporary_memory_does_not_grow_with_the_number_of_calls():
    rng = np.random.default_rng(10)

    def extra(calls: int) -> int:
        ecb = rng.random((calls, 63)).astype(np.float32)
        log_ratio = rng.random((calls, 63))
        tracemalloc.start()
        try:
            r = dist10_partition_thresholds(ecb, log_ratio)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        return peak - sum(getattr(r, key).nbytes for key in OUTPUTS)

    extra(3)
    assert extra(20000) - extra(5000) < 1 << 20


def test_float32_underflow_is_not_an_error_under_a_raising_policy():
    ecb = np.full((3, 63), np.float32(1e-45))  # the smallest float32 subnormal: nb rounds to zero
    with np.errstate(all="raise"):
        result = dist10_partition_thresholds(ecb, np.zeros((3, 63)))
    assert not result.nb.any()
    np.testing.assert_array_equal(result.thr, np.tile(QTHR, (3, 1)))


def test_largest_spread_energy_stays_finite():
    ecb = np.full((3, 63), np.finfo(np.float32).max, dtype=np.float32)
    with np.errstate(all="raise"):
        result = dist10_partition_thresholds(ecb, np.full((3, 63), math.log(0.01)))
    assert np.isfinite(result.nb).all() and np.isfinite(result.thr).all()  # norm <= 0.951 and SNR >= 0 dB


# --- outputs and validation -------------------------------------------------------------------------------


def test_outputs_types_read_only_and_inputs_unchanged():
    ecb, log_ratio = clip_inputs("masker_1k_a8192")
    before = ecb.copy(), log_ratio.copy()
    result = dist10_partition_thresholds(ecb, log_ratio)
    assert isinstance(result, PartitionThresholds)
    assert [getattr(result, key).dtype for key in OUTPUTS] == [np.float64, np.float64, np.float32, np.float64,
                                                              np.float64]  # fmt: skip
    for key in OUTPUTS:
        array = getattr(result, key)
        assert array.shape == ecb.shape and not array.flags.writeable
        assert not np.shares_memory(array, ecb) and not np.shares_memory(array, log_ratio)
    np.testing.assert_array_equal(ecb, before[0])
    np.testing.assert_array_equal(log_ratio, before[1])


def test_empty_input():
    result = dist10_partition_thresholds(np.zeros((0, 63), dtype=np.float32), np.zeros((0, 63)))
    assert all(getattr(result, key).shape == (0, 63) for key in OUTPUTS)


@pytest.mark.parametrize(
    ("ecb", "log_ratio"),
    [
        (np.zeros((2, 63), dtype=np.float32), np.zeros((3, 63))),
        (np.zeros((2, 62), dtype=np.float32), np.zeros((2, 62))),
        (np.zeros((63,), dtype=np.float32), np.zeros((63,))),
        (np.zeros((1, 63)), np.zeros((1, 63))),
        (np.zeros((1, 63), dtype=np.float32), np.zeros((1, 63), dtype=np.float32)),
        ([[0.0] * 63], np.zeros((1, 63))),
        (np.full((1, 63), np.nan, dtype=np.float32), np.zeros((1, 63))),
        (np.full((1, 63), np.inf, dtype=np.float32), np.zeros((1, 63))),
        (np.full((1, 63), -1.0, dtype=np.float32), np.zeros((1, 63))),
        (np.zeros((1, 63), dtype=np.float32), np.full((1, 63), np.nan)),
        (np.zeros((1, 63), dtype=np.float32), np.full((1, 63), -np.inf)),
        (np.ma.masked_array(np.zeros((1, 63), dtype=np.float32)), np.zeros((1, 63))),
    ],
)
def test_rejects_invalid_inputs(ecb, log_ratio):
    with pytest.raises(ValueError):
        dist10_partition_thresholds(ecb, log_ratio)


class _CleanLookingRows(np.ndarray):
    """An ndarray subclass whose slices look clean (zeros), hiding its real values from slice-based checks."""

    def __getitem__(self, key):
        return np.zeros_like(np.asarray(self)[key])


@pytest.mark.filterwarnings("ignore:the matrix subclass is not the recommended way:PendingDeprecationWarning")
def test_ndarray_subclasses_are_normalized_before_validation_and_computation():  # np.matrix built on purpose
    # Regression (Codex review of Task 3L): subclasses must be read as their plain ndarray data.
    ecb, log_ratio = clip_inputs("masker_1k_a8192")
    plain = REPLAYED["masker_1k_a8192"]
    as_matrix = dist10_partition_thresholds(np.asmatrix(ecb), np.asmatrix(log_ratio))  # '*' is matmul on np.matrix
    for key in OUTPUTS:
        assert type(getattr(as_matrix, key)) is np.ndarray
        np.testing.assert_array_equal(getattr(as_matrix, key), getattr(plain, key))
    bad_ecb, bad_log = ecb.copy(), log_ratio.copy()
    bad_ecb[2, 7], bad_log[3, 9] = np.float32(np.nan), -np.inf
    with pytest.raises(ValueError, match="ecb must be finite"):
        dist10_partition_thresholds(bad_ecb.view(_CleanLookingRows), log_ratio)
    with pytest.raises(ValueError, match="log_ratio must be finite"):
        dist10_partition_thresholds(ecb, bad_log.view(_CleanLookingRows))
    negative = ecb.copy()
    negative[1, 1] = -1.0
    with pytest.raises(ValueError, match="ecb must be non-negative"):
        dist10_partition_thresholds(negative.view(_CleanLookingRows), log_ratio)


# --- full Python chain (report only) ---------------------------------------------------------------------


def python_chain(name: str) -> PartitionThresholds:
    raw = np.frombuffer((FIXTURE / "inputs" / f"{name}.raw").read_bytes(), dtype="<i2").astype(np.int16)
    windows = frame_long_blocks(raw, sample_rate_hz=44100).windows
    long, short = long_window_spectrum(windows), short_window_spectrum(windows)
    cw = dist10_unpredictability(long.energy, long.phase, short.energy, short.phase)
    sums = dist10_partition_sums(long.energy, cw)
    spread = dist10_spread_tonality_input(sums.eb, sums.cb)
    return dist10_partition_thresholds(spread.ecb, spread.log_ratio)


def test_full_python_chain_residuals_are_reported_with_the_named_cases():
    worst, named, decisions = {}, {}, 0
    for name, calls in CLIPS.items():
        result = python_chain(name)
        for r in calls:
            k = r["call"]
            for key in OUTPUTS:
                ref = np.array(r[key], dtype=np.float32 if key == "nb" else np.float64).astype(np.float64)
                got = getattr(result, key)[k].astype(np.float64)
                diff = np.abs(got - ref)
                err = (
                    diff
                    if key in ("tonality", "snr")
                    else np.divide(diff, ref, out=np.zeros_like(diff), where=ref != 0)
                )
                b = int(np.argmax(err))
                if err[b] > worst.get(key, (-1.0,))[0]:
                    worst[key] = (float(err[b]), name, k, b)
            decisions += int(np.sum((QTHR > np.array(r["pre_echo_limit"])) != (QTHR > result.pre_echo_limit[k])))
            for case in NAMED:
                if case[:2] == (name, k):
                    p = case[2]
                    named[case] = (float(r["thr"][p]), float(result.thr[k, p]))
    for key, (err, name, call, b) in worst.items():
        kind = "absolute" if key in ("tonality", "snr") else "relative"
        print(f"\nfull chain worst {key} ({kind}): {err:.3e} at {name} call {call} partition {b}")
    for case, (ref, got) in named.items():
        print(f"named {case}: thr captured {ref:.9g}, python {got:.9g}, relative {abs(got - ref) / ref:.3e}")
    print(f"quiet-floor decisions changed: {decisions}")
    assert set(named) == set(NAMED)
    assert all(math.isfinite(v) for pair in named.values() for v in pair)
