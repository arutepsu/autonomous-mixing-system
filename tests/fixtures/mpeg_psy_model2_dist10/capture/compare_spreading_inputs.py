"""Compare dist10's spread values ecb/ctb and the tonality ratio (probe F) with reconstructions; print residuals.

  A. captured eb/cb (probe E) through the captured s3_l                     (exact; the verifier checks it)
  B. the package's partition sums (Python spectra, cw and accumulator) through the same s3_l
  C. burst call 4 with captured eb/cb, replacing only input partition 50's cb by the Python value: which output
     partitions change, and by how much their ctb, ratio and tonality input move (isolates the Task 3G case)
Uses the verifier's spreading and ratio rules. Prints evidence for review; sets no tolerance.

Usage: uv run python tests/fixtures/mpeg_psy_model2_dist10/capture/compare_spreading_inputs.py
"""

import json
import math
import pathlib
import sys

import numpy as np

FIXTURE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FIXTURE / "capture"))

import verify_fixture as vf  # noqa: E402  (sibling capture script)

from autonomous_mixing.adapters.dsp_analysis.framing import frame_long_blocks  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.long_spectrum import long_window_spectrum  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.partitions import dist10_partition_sums  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.short_spectrum import short_window_spectrum  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.unpredictability import dist10_unpredictability  # noqa: E402

CASE = ("burst_1k_a8192_onset2000", 4, 50)


def ratio_chain(ecb: np.ndarray, ctb: list[float]):
    ratio = [ctb[b] / float(ecb[b]) for b in range(63)]  # every ecb is nonzero in the captures
    clamped = [0.01 if v < 0.01 else v for v in ratio]
    return np.array(ratio), np.array([math.log(v) for v in clamped])


def python_partitions(name: str):
    raw = np.frombuffer((FIXTURE / "inputs" / f"{name}.raw").read_bytes(), dtype="<i2").astype(np.int16)
    windows = frame_long_blocks(raw, sample_rate_hz=44100).windows
    long, short = long_window_spectrum(windows), short_window_spectrum(windows)
    cw = dist10_unpredictability(long.energy, long.phase, short.energy, short.phase)
    return dist10_partition_sums(long.energy, cw)


def main() -> None:
    s3 = np.array(json.loads((FIXTURE / "expected_spreading" / "s3_44100.json").read_text())["s3_l"])
    partition = {
        (p.stem, r["call"]): r
        for p in sorted((FIXTURE / "expected_partition").glob("*.jsonl"))
        for r in vf.records("expected_partition", p.stem)
    }
    spreading = {
        (p.stem, r["call"]): r
        for p in sorted((FIXTURE / "expected_spreading").glob("*.jsonl"))
        for r in vf.records("expected_spreading", p.stem)
    }
    exact = 0
    worst: dict[str, tuple] = {}
    python = {}
    for (name, call), f in sorted(spreading.items()):
        e = partition[(name, call)]
        ecb, ctb = vf.spread_partitions(e["eb"], np.array(e["cb"], dtype=np.float32), s3)
        exact += np.array_equal(ecb, np.array(f["ecb"], dtype=np.float32)) and ctb == f["ctb"]
        if name not in python:
            python[name] = python_partitions(name)
        p = python[name]
        ecb_p, ctb_p = vf.spread_partitions(p.eb[call], p.cb[call], s3)
        ratio_p, log_p = ratio_chain(ecb_p, ctb_p)
        refs = {
            "ecb": (np.array(f["ecb"], dtype=np.float32).astype(np.float64), ecb_p.astype(np.float64), True),
            "ctb": (np.array(f["ctb"]), np.array(ctb_p), True),
            "ratio": (np.array(f["ratio"]), ratio_p, True),
            "tonality input (log ratio)": (np.array(f["tonality_input"]), log_p, False),
        }
        for quantity, (ref, got, relative) in refs.items():
            diff = np.abs(got - ref)
            err = np.divide(diff, np.abs(ref), out=np.zeros_like(diff), where=ref != 0) if relative else diff
            b = int(np.argmax(err))
            if err[b] > worst.get(quantity, (0.0,))[0]:
                worst[quantity] = (float(err[b]), name, call, b, float(ref[b]))
    print(f"A captured eb/cb through captured s3_l: exact on {exact} of {len(spreading)} calls")
    for quantity, (err, name, call, b, ref) in worst.items():
        kind = "relative" if quantity != "tonality input (log ratio)" else "absolute"
        print(f"B worst {kind} {quantity}: {err:.3e} at {name} call {call} output partition {b} (captured {ref:.6g})")

    name, call, k = CASE
    f = spreading[(name, call)]
    p = python[name]
    ecb_p, ctb_p = vf.spread_partitions(p.eb[call], p.cb[call], s3)
    ratio_p, _ = ratio_chain(ecb_p, ctb_p)
    print(f"B at the retained case {CASE}: ctb relative {abs(ctb_p[k] - f['ctb'][k]) / f['ctb'][k]:.3e}, "
          f"ratio relative {abs(ratio_p[k] - f['ratio'][k]) / f['ratio'][k]:.3e}")  # fmt: skip
    e = partition[(name, call)]
    cb = np.array(e["cb"], dtype=np.float32)
    swapped = cb.copy()
    swapped[k] = python[name].cb[call, k]
    ecb0, ctb0 = vf.spread_partitions(e["eb"], cb, s3)
    ecb1, ctb1 = vf.spread_partitions(e["eb"], swapped, s3)
    ratio0, log0 = ratio_chain(ecb0, ctb0)
    ratio1, log1 = ratio_chain(ecb1, ctb1)
    changed = [b for b in range(63) if ctb1[b] != ctb0[b]]
    print(f"\nC {name} call {call}: input cb[{k}] {float(cb[k]):.9g} -> {float(swapped[k]):.9g} "
          f"(relative {abs(float(swapped[k]) - float(cb[k])) / float(cb[k]):.3e}); ecb unchanged: "
          f"{np.array_equal(ecb0, ecb1)}")  # fmt: skip
    print(f"  changed output partitions: {changed} (nonzero rows of s3_l column {k}: "
          f"{np.flatnonzero(s3[:, k]).tolist() == changed})")  # fmt: skip
    for b in changed:
        rel_ctb = abs(ctb1[b] - ctb0[b]) / abs(ctb0[b])
        print(f"  p{b:2d}  s3[{b}][{k}] {s3[b, k]:.3e}  ctb rel {rel_ctb:.3e}  ratio {ratio0[b]:.6f} -> {ratio1[b]:.6f}  "
              f"log ratio change {abs(log1[b] - log0[b]):.3e}")  # fmt: skip


if __name__ == "__main__":
    main()
