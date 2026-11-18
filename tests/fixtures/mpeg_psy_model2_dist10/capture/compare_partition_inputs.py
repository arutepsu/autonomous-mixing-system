"""Compare dist10's partition accumulators eb/cb (probe E) with reconstructions, and print the residuals.

Three reconstructions use the same accumulation as the verifier (``verify_fixture.accumulate_partitions``):
  A. captured energy + captured cw + captured mapping          (must be exact; the verifier checks it)
  B. Python long spectrum + Python cw                          (FFT and predictor precision together)
  C. captured energy + Python cw                               (isolates the effect of cw on cb; eb is exact)
Python spectra come from this package's framing, long/short spectra and predictor on the same fixture inputs, over
all calls of each clip (zero history at call 0). Residuals are reported per stratum of partition strength,
eb / max(eb) of that call: strong >= 1e-6, weak 1e-9..1e-6, very weak < 1e-9. Relative errors are |d| / |captured|.
This prints evidence for review; it sets no tolerance.

Usage: uv run python tests/fixtures/mpeg_psy_model2_dist10/capture/compare_partition_inputs.py
"""

import json
import pathlib
import sys

import numpy as np

FIXTURE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FIXTURE / "capture"))

import verify_fixture as vf  # noqa: E402  (sibling capture script)

from autonomous_mixing.adapters.dsp_analysis.framing import frame_long_blocks  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.long_spectrum import long_window_spectrum  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.short_spectrum import short_window_spectrum  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.unpredictability import dist10_unpredictability  # noqa: E402

STRATA = (("strong (>= 1e-6 of call max eb)", 1e-6), ("weak (1e-9 .. 1e-6)", 1e-9), ("very weak (< 1e-9)", 0.0))


def stratum(fraction: float) -> str:
    return next(label for label, low in STRATA if fraction >= low)


def main() -> None:
    mapping = json.loads((FIXTURE / "expected_partition" / "mapping_44100.json").read_text())["partition_l"]
    worst: dict[tuple[str, str, str], tuple[float, tuple, float, tuple]] = {}
    exact = 0
    for path in sorted((FIXTURE / "expected_partition").glob("*.jsonl")):
        name = path.stem
        raw = np.frombuffer((FIXTURE / "inputs" / f"{name}.raw").read_bytes(), dtype="<i2").astype(np.int16)
        windows = frame_long_blocks(raw, sample_rate_hz=44100).windows
        long, short = long_window_spectrum(windows), short_window_spectrum(windows)
        cw = dist10_unpredictability(long.energy, long.phase, short.energy, short.phase)
        for r in vf.records("expected_partition", name):
            call = r["call"]
            eb_ref, cb_ref = np.array(r["eb"]), np.array(r["cb"], dtype=np.float32).astype(np.float64)
            energy = np.array(r["partition_energy"], dtype=np.float32)
            a = vf.accumulate_partitions(energy, r["partition_cw"], mapping)
            exact += a[0].tolist() == r["eb"] and np.array_equal(a[1], np.array(r["cb"], dtype=np.float32))
            runs = {
                "B python spectra + python cw": vf.accumulate_partitions(long.energy[call], cw[call].tolist(), mapping),
                "C captured energy + python cw": vf.accumulate_partitions(energy, cw[call].tolist(), mapping),
            }
            peak = eb_ref.max()
            for label, (eb, cb) in runs.items():
                for b in range(63):
                    s = stratum(eb_ref[b] / peak)
                    for quantity, ref, got in (("eb", eb_ref[b], eb[b]), ("cb", cb_ref[b], float(cb[b]))):
                        key = (label, quantity, s)
                        d = abs(got - ref)
                        rel = d / abs(ref) if ref else 0.0
                        cur = worst.get(key, (0.0, None, 0.0, None))
                        if rel > cur[0]:
                            cur = (rel, (name, call, b), cur[2], cur[3])
                        if d > cur[2]:
                            cur = (cur[0], cur[1], d, (name, call, b))
                        worst[key] = cur
    total = sum(
        1 for p in (FIXTURE / "expected_partition").glob("*.jsonl") for _ in vf.records("expected_partition", p.stem)
    )
    print(f"A captured energy + captured cw: exact on {exact} of {total} calls")
    for key in sorted(worst):
        rel, at_rel, d, at_abs = worst[key]
        print(f"{key[0]:31s} {key[1]} {key[2]:31s} rel {rel:.2e} at {at_rel}  abs {d:.3e} at {at_abs}")


if __name__ == "__main__":
    main()
