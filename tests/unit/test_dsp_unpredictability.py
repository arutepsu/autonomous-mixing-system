"""Tests for the dist10 spectral unpredictability ``cw`` (front-end contract §3.2; Task 3F).

Two comparisons are kept separate:

1. Captured spectra (direct): the predictor is fed Probe D's own captured inputs (short energies and phases; long
   bins 0-5 reconstructed from the recorded float32 ``r_new``/``phi_new``) and compared with the captured ``cw``.
   This isolates the predictor arithmetic; the bound is the fixture verifier's 1e-12.
2. Python spectra (end to end): the predictor is fed this package's long and short spectra of the same fixture
   inputs, so FFT precision differences propagate. Reported separately for bins 0-5 and 6-205.

Other inputs are small synthetic arrays (unit-test signals, not audio material or evaluation data).
"""

import json
import math
import pathlib

import numpy as np
import pytest

from autonomous_mixing.adapters.dsp_analysis.framing import frame_long_blocks
from autonomous_mixing.adapters.dsp_analysis.long_spectrum import long_window_spectrum
from autonomous_mixing.adapters.dsp_analysis.short_spectrum import short_window_spectrum
from autonomous_mixing.adapters.dsp_analysis.unpredictability import (
    CONSTANT_LINE_START,
    CONSTANT_UNPREDICTABILITY,
    LONG_PREDICTOR_BINS,
    UNPREDICTABILITY_LINES,
    dist10_unpredictability,
    long_predictor_cw,
    short_bin_for_line,
    short_predictor_cw,
)

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "mpeg_psy_model2_dist10"
SHORT = FIXTURE / "expected_short"
DIRECT_BOUND = 1e-12  # verify_fixture.py cw reconstruction bound
FLOOR32 = np.float32(0.0005)


def captured(name: str) -> dict[int, dict]:
    rows = [json.loads(line) for line in (SHORT / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]
    return {r["call"]: r for r in rows if "cw" in r}


def f32(values) -> np.ndarray:
    return np.array(values).astype(np.float32)


def energy_with_sqrt(r: np.ndarray) -> np.ndarray:
    """A float32 energy whose float32 sqrt is exactly ``r`` (Probe D records r_new, not the energy)."""
    energy = (r.astype(np.float64) ** 2).astype(np.float32)
    assert np.array_equal(np.sqrt(energy.astype(np.float64)).astype(np.float32), r)
    return energy


def captured_inputs(records: list[dict]):
    calls = len(records)
    long_energy = np.full((calls, 513), FLOOR32, dtype=np.float32)  # bins 6..512 do not enter cw
    long_phase = np.zeros((calls, 6), dtype=np.float32)
    short_energy = np.zeros((calls, 3, 129), dtype=np.float32)
    short_phase = np.zeros((calls, 3, 129), dtype=np.float32)
    for row, r in enumerate(records):
        long_energy[row, :6] = energy_with_sqrt(f32(r["long_history"]["r_new"]))
        long_phase[row] = f32(r["long_history"]["phi_new"])
        short_energy[row] = f32(r["short_energy"])
        short_phase[row] = f32(r["short_phi"])
    return long_energy, long_phase, short_energy, short_phase


def raw(name: str) -> np.ndarray:
    return np.frombuffer((FIXTURE / "inputs" / f"{name}.raw").read_bytes(), dtype="<i2").astype(np.int16)


def python_cw(name: str) -> np.ndarray:
    windows = frame_long_blocks(raw(name), sample_rate_hz=44100).windows
    long, short = long_window_spectrum(windows), short_window_spectrum(windows)
    return dist10_unpredictability(long.energy, long.phase, short.energy, short.phase)


# --- constants and mapping ---------------------------------------------------------------------------


def test_constants():
    assert (LONG_PREDICTOR_BINS, CONSTANT_LINE_START, UNPREDICTABILITY_LINES) == (6, 206, 513)
    assert CONSTANT_UNPREDICTABILITY == 0.4


def test_short_bin_mapping():
    assert [short_bin_for_line(j) for j in (6, 7, 8, 9, 10, 202, 205)] == [2, 2, 2, 2, 3, 51, 51]


# --- direct comparison with captured spectra ----------------------------------------------------------


@pytest.mark.parametrize(("name", "calls"), [("silence_12granules", 3), ("masker_1k_a8192", 5)])
def test_consecutive_calls_from_call_0_reproduce_captured_cw(name, calls):
    records = captured(name)
    assert sorted(records) == list(range(calls))  # complete prefix: zero history at call 0
    ordered = [records[k] for k in range(calls)]
    cw = dist10_unpredictability(*captured_inputs(ordered))
    expected = np.array([r["cw"] for r in ordered])
    assert cw.dtype == np.float64 and cw.shape == (calls, 513)
    assert np.max(np.abs(cw - expected)) <= DIRECT_BOUND


def single_call_cases():
    cases = []
    for name in ("impulse_32767_at1983", "burst_1k_a8192_onset2000", "target_2k_a10.24", "masker_1k_a8192"):
        for call, r in captured(name).items():
            cases.append(pytest.param(name, r, id=f"{name}-call{call}"))
    return cases


@pytest.mark.parametrize(("name", "record"), single_call_cases())
def test_single_call_calculation_with_recorded_history(name, record):
    # Calls without a complete captured prefix: only the single-call arithmetic is checked, from recorded history.
    h = record["long_history"]
    long_cw = long_predictor_cw(
        *(f32(h[key])[None, :] for key in ("r_new", "phi_new", "r_old", "r_oldest", "phi_old", "phi_oldest"))
    )
    short_cw = short_predictor_cw(f32(record["short_energy"])[None], f32(record["short_phi"])[None])
    expected = np.array(record["cw"])
    assert np.max(np.abs(long_cw[0] - expected[:6])) <= DIRECT_BOUND
    assert np.max(np.abs(short_cw[0] - expected[6:206])) <= DIRECT_BOUND


def test_recorded_history_slots_equal_the_previous_calls_values():
    records = captured("masker_1k_a8192")
    for k in range(5):
        h = records[k]["long_history"]
        previous = records[k - 1]["long_history"] if k >= 1 else None
        two_back = records[k - 2]["long_history"] if k >= 2 else None
        assert h["r_old"] == (previous["r_new"] if previous else [0] * 6)
        assert h["r_oldest"] == (two_back["r_new"] if two_back else [0] * 6)


# --- end-to-end comparison with Python spectra ----------------------------------------------------------


def worst(name: str, deviation: np.ndarray, calls: list[int], lines: slice) -> tuple[float, str, int, int]:
    part = deviation[:, lines]
    row, col = np.unravel_index(int(np.argmax(part)), part.shape)
    return float(part[row, col]), name, calls[row], int(col + (lines.start or 0))


END_TO_END = {}


def end_to_end():
    if not END_TO_END:
        for name in ("silence_12granules", "impulse_32767_at1983", "burst_1k_a8192_onset2000", "masker_1k_a8192",
                     "target_2k_a10.24"):  # fmt: skip
            records = captured(name)
            calls = sorted(records)
            cw = python_cw(name)[calls]
            deviation = np.abs(cw - np.array([records[k]["cw"] for k in calls]))
            END_TO_END[name] = (deviation, calls)
    return END_TO_END


def test_end_to_end_constant_lines_and_shape():
    for name, (deviation, _calls) in end_to_end().items():
        assert not deviation[:, 206:].any(), name


def test_end_to_end_long_and_short_deviations_are_reported():
    long_worst = max(worst(n, d, c, slice(0, 6)) for n, (d, c) in end_to_end().items())
    short_worst = max(worst(n, d, c, slice(6, 206)) for n, (d, c) in end_to_end().items())
    print(
        f"\nlong predictor worst |dcw| {long_worst[0]:.3e} at {long_worst[1]} call {long_worst[2]} bin {long_worst[3]}"
    )
    print(
        f"short predictor worst |dcw| {short_worst[0]:.3e} at {short_worst[1]} call {short_worst[2]} bin {short_worst[3]}"
    )
    assert math.isfinite(long_worst[0]) and math.isfinite(short_worst[0])


# --- silence history, boundaries, block order and clip independence ---------------------------------------


def floored_inputs(calls: int):
    return (
        np.full((calls, 513), FLOOR32, dtype=np.float32),
        np.zeros((calls, 6), dtype=np.float32),
        np.full((calls, 3, 129), FLOOR32, dtype=np.float32),
        np.zeros((calls, 3, 129), dtype=np.float32),
    )


def test_silence_history_gives_1_then_one_third_then_0():
    cw = dist10_unpredictability(*floored_inputs(5))
    np.testing.assert_allclose(cw[0, :6], 1.0, atol=1e-15)
    np.testing.assert_allclose(cw[1, :6], 1 / 3, atol=1e-15)
    np.testing.assert_allclose(cw[2:, :6], 0.0, atol=1e-15)
    assert not cw[:, 6:206].any() and np.all(cw[:, 206:] == 0.4)


def test_python_spectra_of_silence_follow_the_same_history():
    cw = python_cw("silence_12granules")
    np.testing.assert_allclose(cw[:3, :6], [[1.0] * 6, [1 / 3] * 6, [0.0] * 6], atol=1e-15)


def test_line_boundaries():
    long_energy, long_phase, short_energy, short_phase = floored_inputs(1)
    base = dist10_unpredictability(long_energy, long_phase, short_energy, short_phase)[0]
    for k, changed in ((1, []), (2, [6, 7, 8, 9]), (51, [202, 203, 204, 205]), (52, [])):
        moved = short_energy.copy()
        moved[0, 1, k] = np.float32(4.0)  # make block 1 deviate from the prediction at short bin k
        cw = dist10_unpredictability(long_energy, long_phase, moved, short_phase)[0]
        assert np.flatnonzero(cw != base).tolist() == changed, k
    # Long side: at call 0 the prediction is zero, so cw = 1 whatever the energy; change call 1 instead.
    long_energy, long_phase, short_energy, short_phase = floored_inputs(2)
    base = dist10_unpredictability(long_energy, long_phase, short_energy, short_phase)[1]
    moved_long = long_energy.copy()
    moved_long[1, 5], moved_long[1, 6] = np.float32(9.0), np.float32(9.0)
    cw = dist10_unpredictability(moved_long, long_phase, short_energy, short_phase)[1]
    assert np.flatnonzero(cw != base).tolist() == [5]  # long bin 6 is not a predictor input


def test_short_predictor_interpolates_block_1_from_blocks_0_and_2():
    energy = np.full((1, 3, 129), FLOOR32, dtype=np.float32)
    phase = np.zeros((1, 3, 129), dtype=np.float32)
    energy[0, :, 2] = [np.float32(4.0), np.float32(1.0), np.float32(9.0)]  # r = 2, 1, 3
    phase[0, :, 2] = [np.float32(0.25), np.float32(-1.0), np.float32(0.5)]
    r_prime, phi_prime = 2 * 2.0 - 3.0, 2 * float(np.float32(0.25)) - float(np.float32(0.5))
    r2, phi2 = 1.0, float(np.float32(-1.0))
    t1 = r2 * math.cos(phi2) - r_prime * math.cos(phi_prime)
    t2 = r2 * math.sin(phi2) - r_prime * math.sin(phi_prime)
    expected = math.sqrt(t1 * t1 + t2 * t2) / (r2 + abs(r_prime))
    lines = short_predictor_cw(energy, phase)[0]
    assert lines[0] == pytest.approx(expected, abs=1e-15) and np.all(lines[:4] == lines[0])
    swapped = energy[:, ::-1].copy(), phase[:, ::-1].copy()
    assert short_predictor_cw(*swapped)[0, 0] != pytest.approx(expected, abs=1e-6)


def test_identical_blocks_are_fully_predictable():
    energy = np.full((1, 3, 129), np.float32(2.5), dtype=np.float32)
    phase = np.full((1, 3, 129), np.float32(0.75), dtype=np.float32)
    assert np.max(np.abs(short_predictor_cw(energy, phase))) < 1e-15


def test_clips_are_independent_and_start_with_zero_history():
    inputs = floored_inputs(6)
    full = dist10_unpredictability(*inputs)
    tail = dist10_unpredictability(*(a[3:] for a in inputs))
    np.testing.assert_array_equal(tail, full[:3])  # a new clip restarts at zero history
    assert not np.array_equal(tail[0, :6], full[3, :6])
    again = dist10_unpredictability(*inputs)
    np.testing.assert_array_equal(again, full)


def test_long_history_uses_previous_and_two_back_calls():
    long_energy, long_phase, short_energy, short_phase = floored_inputs(4)
    r = np.array([1.0, 2.0, 5.0, 3.0])
    long_energy[:, 0] = (r**2).astype(np.float32)
    cw = dist10_unpredictability(long_energy, long_phase, short_energy, short_phase)
    for k in range(4):
        r_old = r[k - 1] if k >= 1 else 0.0
        r_oldest = r[k - 2] if k >= 2 else 0.0
        r_prime = 2 * r_old - r_oldest
        expected = abs(r[k] - r_prime) / (r[k] + abs(r_prime))  # all phases zero
        assert cw[k, 0] == pytest.approx(expected, abs=1e-15), k


def test_long_history_is_stored_as_float32():
    long_energy, long_phase, short_energy, short_phase = floored_inputs(2)
    long_energy[:, 0] = np.float32(2.0)  # sqrt(2) is not a float32
    long_phase[:, 0] = np.float32(0.1)
    cw = dist10_unpredictability(long_energy, long_phase, short_energy, short_phase)
    r = float(np.float32(math.sqrt(2.0)))
    phi = float(np.float32(0.1))
    r_prime, phi_prime = 2.0 * r, 2.0 * phi  # call 1: old = call 0, oldest = zero
    t1 = r * math.cos(phi) - r_prime * math.cos(phi_prime)
    t2 = r * math.sin(phi) - r_prime * math.sin(phi_prime)
    assert cw[1, 0] == math.sqrt(t1 * t1 + t2 * t2) / (r + abs(r_prime))


def test_zero_denominator_branch_returns_zero():
    # Unreachable from floored spectra (r_new >= sqrt(float32(0.0005))); reached here with zero energies.
    zeros = (
        np.zeros((2, 513), dtype=np.float32),
        np.zeros((2, 6), dtype=np.float32),
        np.zeros((2, 3, 129), dtype=np.float32),
        np.zeros((2, 3, 129), dtype=np.float32),
    )
    with np.errstate(all="raise"):
        cw = dist10_unpredictability(*zeros)
    assert not cw[:, :206].any() and np.all(cw[:, 206:] == 0.4)


# --- outputs and validation ---------------------------------------------------------------------------


def test_output_is_read_only_and_inputs_unchanged():
    inputs = [a.copy() for a in floored_inputs(3)]
    before = [a.copy() for a in inputs]
    cw = dist10_unpredictability(*inputs)
    assert not cw.flags.writeable
    for a, b in zip(inputs, before, strict=True):
        np.testing.assert_array_equal(a, b)
        assert not np.shares_memory(cw, a)


def random_inputs(calls: int, seed: int):
    rng = np.random.default_rng(seed)
    return (
        rng.random((calls, 513)).astype(np.float32),
        (rng.random((calls, 6)) * 6 - 3).astype(np.float32),
        rng.random((calls, 3, 129)).astype(np.float32),
        (rng.random((calls, 3, 129)) * 6 - 3).astype(np.float32),
    )


def test_history_carries_across_chunk_boundaries():
    # Regression (Codex review): calls are processed in chunks; the two-slot history must continue across them.
    inputs = random_inputs(600, seed=8)
    chunked = dist10_unpredictability(*inputs)
    long_rows = []
    r = np.sqrt(inputs[0][:, :6].astype(np.float64)).astype(np.float32).astype(np.float64)
    phi = inputs[1].astype(np.float64)
    for k in range(600):  # closed form: old = call k - 1, oldest = call k - 2, zero before call 0
        r_old, p_old = (r[k - 1], phi[k - 1]) if k >= 1 else (np.zeros(6), np.zeros(6))
        r_oldest, p_oldest = (r[k - 2], phi[k - 2]) if k >= 2 else (np.zeros(6), np.zeros(6))
        r_prime, phi_prime = 2.0 * r_old - r_oldest, 2.0 * p_old - p_oldest
        t1 = r[k] * np.cos(phi[k]) - r_prime * np.cos(phi_prime)
        t2 = r[k] * np.sin(phi[k]) - r_prime * np.sin(phi_prime)
        long_rows.append(np.sqrt(t1 * t1 + t2 * t2) / (r[k] + np.abs(r_prime)))
    np.testing.assert_array_equal(chunked[:, :6], np.array(long_rows))
    for k in (255, 256, 257, 511, 512, 599):  # short lines depend on the call alone
        single = short_predictor_cw(inputs[2][k : k + 1], inputs[3][k : k + 1])[0]
        np.testing.assert_array_equal(chunked[k, 6:206], single)


def test_temporary_memory_does_not_grow_with_the_number_of_calls():
    # Regression (Codex review): memory beyond the (calls, 513) float64 output is bounded by the chunk size.
    import tracemalloc

    small, large = random_inputs(1000, seed=1), random_inputs(4000, seed=2)
    dist10_unpredictability(*random_inputs(3, seed=3))

    def extra(inputs) -> int:
        tracemalloc.start()
        try:
            cw = dist10_unpredictability(*inputs)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        return peak - cw.nbytes

    growth = extra(large) - extra(small)
    assert growth < 1 << 20, growth  # whole-array temporaries grew by about 21 MB for 3,000 more calls


def test_zero_calls():
    assert dist10_unpredictability(*(a[:0] for a in floored_inputs(1))).shape == (0, 513)


def replace(index: int, value):
    inputs = list(floored_inputs(2))
    inputs[index] = value
    return inputs


@pytest.mark.parametrize(
    "inputs",
    [
        replace(0, np.full((3, 513), FLOOR32, dtype=np.float32)),  # call count mismatch
        replace(1, np.zeros((3, 6), dtype=np.float32)),
        replace(2, np.full((3, 3, 129), FLOOR32, dtype=np.float32)),
        replace(3, np.zeros((1, 3, 129), dtype=np.float32)),
        replace(0, np.full((2, 512), FLOOR32, dtype=np.float32)),  # shapes
        replace(1, np.zeros((2, 5), dtype=np.float32)),
        replace(2, np.full((2, 2, 129), FLOOR32, dtype=np.float32)),
        replace(3, np.zeros((2, 3, 128), dtype=np.float32)),
        replace(0, np.full((2, 513), 0.0005, dtype=np.float64)),  # dtype
        replace(2, [[[0.0005] * 129] * 3] * 2),  # not an ndarray
        replace(0, np.full((2, 513), np.nan, dtype=np.float32)),  # values
        replace(1, np.full((2, 6), np.inf, dtype=np.float32)),
        replace(2, np.full((2, 3, 129), -1.0, dtype=np.float32)),
        replace(3, np.full((2, 3, 129), np.nan, dtype=np.float32)),
        replace(0, np.ma.masked_array(np.full((2, 513), FLOOR32, dtype=np.float32))),
    ],
)
def test_rejects_invalid_inputs(inputs):
    with pytest.raises(ValueError):
        dist10_unpredictability(*inputs)
