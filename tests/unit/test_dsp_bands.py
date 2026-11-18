"""Tests for the dist10 long scale-factor-band mapping kernel (front-end contract §3.6; Task 3N).

Reference values are Probe B's ``en``/``thm``/``thm_quiet`` in ``tests/fixtures/mpeg_psy_model2_dist10/
expected_long_mapping`` and the mapping inputs captured by Probe H in ``expected_band_inputs`` (every call of all
nine inputs, short-path calls included), with the band tables in ``bands_44100.json``. Other inputs are small
synthetic arrays (unit-test signals, not audio material or evaluation data).
"""

import json
import math
import pathlib
import tracemalloc
import warnings

import numpy as np
import pytest

from autonomous_mixing.adapters.dsp_analysis.bands import (
    DIST10_LONG_BO_44100,
    DIST10_LONG_BU_44100,
    DIST10_LONG_W1_44100,
    DIST10_LONG_W2_44100,
    LongBandValues,
    dist10_long_bands,
)

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "mpeg_psy_model2_dist10"
TABLES = json.loads((FIXTURE / "expected_band_inputs" / "bands_44100.json").read_text())
QTHR = json.loads((FIXTURE / "expected_threshold" / "tables_44100.json").read_text())["qthr_l"]


def records(folder: str, name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURE / folder / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]


NAMES = sorted(p.stem for p in (FIXTURE / "expected_band_inputs").glob("*.jsonl"))


def clip(name: str):
    h, b = records("expected_band_inputs", name), records("expected_long_mapping", name)
    assert [r["call"] for r in h] == [r["call"] for r in b] == list(range(len(b)))
    eb = np.array([r["band_eb"] for r in h], dtype=np.float64)
    thr = np.array([r["band_thr"] for r in h], dtype=np.float64)
    return eb, thr, b


def scalar_mapping(values, interior_first: bool = False) -> list[float]:
    """Transcription of l3psy.c ll. 654-660 for one call (endpoints first, then the interior ascending)."""
    out = []
    for sb in range(21):
        bu, bo = DIST10_LONG_BU_44100[sb], DIST10_LONG_BO_44100[sb]
        ends = DIST10_LONG_W1_44100[sb] * values[bu] + DIST10_LONG_W2_44100[sb] * values[bo]
        total = 0.0 if interior_first else ends
        for b in range(bu + 1, bo):
            total = total + values[b]
        out.append(total + ends if interior_first else total)
    return out


# --- tables -------------------------------------------------------------------------------------------------


def test_tables_are_the_pinned_psy_data_rows():
    assert list(DIST10_LONG_BU_44100) == TABLES["bu_l"] == [int(s) for s in TABLES["psy_data_44100_long_sb_bu"]]
    assert list(DIST10_LONG_BO_44100) == TABLES["bo_l"] == [int(s) for s in TABLES["psy_data_44100_long_sb_bo"]]
    assert list(DIST10_LONG_W1_44100) == TABLES["w1_l"] == [float(s) for s in TABLES["psy_data_44100_long_sb_w1"]]
    assert list(DIST10_LONG_W2_44100) == TABLES["w2_l"] == [float(s) for s in TABLES["psy_data_44100_long_sb_w2"]]


def test_band_zero_uses_the_recorded_bo_not_bu_plus_cbw():
    cbw = [int(s) for s in TABLES["psy_data_44100_long_sb_cbw"]]
    assert DIST10_LONG_BU_44100[0] + cbw[0] == 3 and DIST10_LONG_BO_44100[0] == 4  # the one exception to bo = bu + cbw
    assert all(DIST10_LONG_BO_44100[sb] == DIST10_LONG_BU_44100[sb] + cbw[sb] for sb in range(1, 21))
    eb = np.zeros((1, 63))
    eb[0, 3], eb[0, 4] = 10.0, 1000.0
    result = dist10_long_bands(eb, eb.copy())
    assert result.en[0, 0] == 1.0 * 0.0 + 0.056 * 1000.0 + 10.0  # partition 3 is interior to band 0, 4 its endpoint
    assert result.en[0, 1] == 0.944 * 1000.0 + 0.611 * 0.0


# --- captured calls -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_exact_against_probe_b_on_every_call(name):
    eb, thr, b = clip(name)
    result = dist10_long_bands(eb, thr)
    assert result.en.tolist() == [r["en"] for r in b]
    assert result.thm.tolist() == [r["thm"] for r in b]
    quiet = dist10_long_bands(np.tile(QTHR, (len(b), 1)), eb)
    assert quiet.en.tolist() == [r["thm_quiet"] for r in b]


def test_captured_calls_include_startup_and_short_path_calls():
    rows = [r for name in NAMES for r in records("expected_long_mapping", name)]
    assert len(rows) == 92
    assert sum(not r["encoder_long_path"] for r in rows) == 13  # encoder short-path calls, mapped all the same
    assert sum(r["call"] < 2 for r in rows) == 2 * len(NAMES)  # start-up calls 0 and 1 of every input


# --- rule-level cases -----------------------------------------------------------------------------------------


def test_each_partition_reaches_exactly_its_bands_with_its_weights():
    # One-hot partition energies: row p of the result is partition p's weight in every band. This fixes the endpoint
    # weights (w1 at bu, w2 at bo, not swapped), the interior range (bu + 1 .. bo - 1) and the unused partition 62.
    eye = np.eye(63)
    weights = dist10_long_bands(eye, eye.copy()).en
    for p in range(63):
        expected = np.zeros(21)
        for sb in range(21):
            bu, bo = DIST10_LONG_BU_44100[sb], DIST10_LONG_BO_44100[sb]
            if p == bu:
                expected[sb] += DIST10_LONG_W1_44100[sb]
            if p == bo:
                expected[sb] += DIST10_LONG_W2_44100[sb]
            if bu < p < bo:
                expected[sb] += 1.0
        np.testing.assert_array_equal(weights[p], expected, err_msg=f"partition {p}")
    assert not weights[62].any()  # partition 62 is never read
    assert weights[50].tolist() == [0.0] * 16 + [1.0] + [0.0] * 4  # interior to band 16 only
    assert weights[49, 15] == 0.450 and weights[49, 16] == 0.550  # shared endpoint: w2 of band 15, w1 of band 16


def test_partition_62_does_not_change_any_band():
    rng = np.random.default_rng(1)
    eb = rng.random((4, 63)) * 1e6
    changed = eb.copy()
    changed[:, 62] = 1e300
    np.testing.assert_array_equal(dist10_long_bands(changed, changed).en, dist10_long_bands(eb, eb).en)


def test_endpoints_are_summed_before_the_interior():
    # Band 16 (bu 49, bo 52, interior 50 and 51): w2 x eb[52] = 2**53 absorbs each +1.0 when the endpoints come first;
    # adding the interior first (1 + 1 = 2) would keep it.
    eb = np.zeros((1, 63))
    eb[0, 52] = 2.0**53 / 0.552
    eb[0, 50] = eb[0, 51] = 1.0
    got = dist10_long_bands(eb, eb.copy()).en[0, 16]
    assert got == scalar_mapping(eb[0])[16]
    assert got != scalar_mapping(eb[0], interior_first=True)[16]


def test_same_mapping_for_energy_and_threshold_and_calls_stay_in_order():
    rng = np.random.default_rng(2)
    eb, thr = rng.random((6, 63)) * 1e5, rng.random((6, 63)) * 1e3
    result = dist10_long_bands(eb, thr)
    for k in range(6):
        assert result.en[k].tolist() == scalar_mapping(eb[k])
        assert result.thm[k].tolist() == scalar_mapping(thr[k])
    reversed_ = dist10_long_bands(eb[::-1].copy(), thr[::-1].copy())
    np.testing.assert_array_equal(reversed_.en, result.en[::-1])


def test_chunk_boundaries_match_the_scalar_mapping():
    rng = np.random.default_rng(3)
    eb, thr = rng.random((2600, 63)) * 1e9, rng.random((2600, 63))
    result = dist10_long_bands(eb, thr)
    for k in (0, 1023, 1024, 1025, 2047, 2048, 2599):
        assert result.en[k].tolist() == scalar_mapping(eb[k])
        assert result.thm[k].tolist() == scalar_mapping(thr[k])


def test_temporary_memory_does_not_grow_with_the_number_of_calls():
    rng = np.random.default_rng(4)

    def extra(calls: int) -> int:
        eb, thr = rng.random((calls, 63)), rng.random((calls, 63))
        tracemalloc.start()
        try:
            r = dist10_long_bands(eb, thr)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        return peak - r.en.nbytes - r.thm.nbytes

    extra(3)
    assert extra(20000) - extra(5000) < 1 << 20


# --- outputs and validation -----------------------------------------------------------------------------------


def test_outputs_fresh_read_only_float64_and_inputs_unchanged():
    eb, thr, _ = clip("masker_1k_a8192")
    before = eb.copy(), thr.copy()
    result = dist10_long_bands(eb, thr)
    assert isinstance(result, LongBandValues)
    for array in (result.en, result.thm):
        assert type(array) is np.ndarray and array.dtype == np.float64 and array.shape == (12, 21)
        assert not array.flags.writeable
        assert not np.shares_memory(array, eb) and not np.shares_memory(array, thr)
    assert not np.shares_memory(result.en, result.thm)
    np.testing.assert_array_equal(eb, before[0])
    np.testing.assert_array_equal(thr, before[1])


def test_empty_input_keeps_the_band_shape():
    result = dist10_long_bands(np.zeros((0, 63)), np.zeros((0, 63)))
    assert result.en.shape == result.thm.shape == (0, 21)
    assert result.en.dtype == result.thm.dtype == np.float64


@pytest.mark.filterwarnings("ignore:the matrix subclass is not the recommended way:PendingDeprecationWarning")
def test_ndarray_subclasses_are_read_as_plain_arrays():  # np.matrix built on purpose
    eb, thr, b = clip("masker_1k_a8192")
    result = dist10_long_bands(np.asmatrix(eb), np.asmatrix(thr))
    assert type(result.en) is np.ndarray and result.en.tolist() == [r["en"] for r in b]


@pytest.mark.parametrize(
    ("eb", "thr"),
    [
        (np.zeros((2, 63)), np.zeros((3, 63))),
        (np.zeros((2, 62)), np.zeros((2, 62))),
        (np.zeros((2, 64)), np.zeros((2, 64))),
        (np.zeros((63,)), np.zeros((63,))),
        (np.zeros((1, 63), dtype=np.float32), np.zeros((1, 63))),
        (np.zeros((1, 63)), np.zeros((1, 63), dtype=np.float32)),
        ([[0.0] * 63], np.zeros((1, 63))),
        (np.full((1, 63), np.nan), np.zeros((1, 63))),
        (np.zeros((1, 63)), np.full((1, 63), np.inf)),
        (np.full((1, 63), -1.0), np.zeros((1, 63))),
        (np.zeros((1, 63)), np.full((1, 63), -1e-300)),
        (np.ma.masked_array(np.zeros((1, 63))), np.zeros((1, 63))),
    ],
)
def test_rejects_invalid_inputs(eb, thr):
    with pytest.raises(ValueError):
        dist10_long_bands(eb, thr)


def test_mapped_overflow_is_rejected_with_call_and_band():
    big = np.finfo(np.float64).max
    eb = np.zeros((1500, 63))
    eb[1200, 50] = eb[1200, 51] = big  # band 16 interior: finite inputs, infinite sum
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with np.errstate(all="raise"), pytest.raises(ValueError, match=r"en .*call 1200.*band 16"):
            dist10_long_bands(eb, np.zeros((1500, 63)))
    thr = np.zeros((1, 63))
    thr[0, 4] = big  # w1 of band 1 is 0.944 and w2 of band 0 is 0.056: no single product overflows
    thr[0, 5] = big  # band 1 interior: 0.944 big + big overflows
    with pytest.raises(ValueError, match=r"thm .*call 0.*band 1"):
        dist10_long_bands(np.zeros((1, 63)), thr)


@pytest.mark.parametrize("policy", ["raise", "warn", "ignore"])
def test_result_is_independent_of_the_underflow_policy(policy):
    # Regression (Codex review of Task 3N): a weighted endpoint of a tiny value rounds to a subnormal or to zero, as
    # IEEE double arithmetic in the reference does; the caller's NumPy underflow setting must not change the outcome.
    eb = np.zeros((2, 63))
    eb[0, :] = 1e-308  # w2 x 1e-308 (for example 0.056 x 1e-308) is subnormal
    eb[1, :] = 5e-324  # the smallest subnormal: w x 5e-324 rounds to 0 or stays 5e-324
    expected = [scalar_mapping(eb[k]) for k in range(2)]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with np.errstate(all=policy):
            result = dist10_long_bands(eb, eb.copy())
    assert result.en.tolist() == expected and result.thm.tolist() == expected


def test_largest_finite_values_without_overflow_stay_exact():
    eb = np.full((1, 63), np.finfo(np.float64).max / 8)  # every band sums at most 4 x (max / 8)
    with np.errstate(all="raise"):
        result = dist10_long_bands(eb, eb.copy())
    assert np.isfinite(result.en).all() and math.isfinite(float(result.thm.max()))
