"""Compare dist10's long scale-factor-band values (probe B) and directional margins with the package's chain.

  A. captured eb/thr (probe H) through the band mapping                                   (the verifier checks it)
  B. the package's chain (framing, spectra, cw, partition sums, spreading, thresholds; history replayed from call 0
     for each clip) through the same mapping, against probe B's en/thm on every call; worst absolute and relative
     residual per quantity with input, call and band; partition 50 traced into band 16 at the tone call 3 and burst
     call 4 cases
  C. directional margins with the approved kernel (``directional_masking_margin_db``), probe B against the package
     chain, for the aligned 1 kHz masker / 2 kHz target pairs at both levels in both directions, on the valid frames
     of contract §2.2 (k >= 2 and 576 k + 256 <= N); exact-silence controls kept separate and flagged from the int16
     window, so the FFT energy floor is never read as target activity
The Python path is the production entry point ``dist10_clip_to_bands`` (Task 3O): its band values and its framer
metadata (``eligible``, ``nonzero_samples == 0``) are what B and C use. Independently, the lower-level kernels are
composed here to partition values (eb, thr) for every call; the verifier's own mapping of those must equal the entry
point's en/thm on every call, and they supply the partition-50 diagnostics. Prints evidence for review; sets no
tolerance.

Usage: uv run python tests/fixtures/mpeg_psy_model2_dist10/capture/compare_band_margins.py
"""

import json
import pathlib
import sys

import numpy as np

FIXTURE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FIXTURE / "capture"))

import verify_fixture as vf  # noqa: E402  (sibling capture script)

from autonomous_mixing.adapters.dsp_analysis.framing import frame_long_blocks  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.front_end import dist10_clip_to_bands  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.long_spectrum import long_window_spectrum  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.masking import directional_masking_margin_db  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.partitions import dist10_partition_sums  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.short_spectrum import short_window_spectrum  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.spreading import dist10_spread_tonality_input  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.thresholds import dist10_partition_thresholds  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.unpredictability import dist10_unpredictability  # noqa: E402

BANDS = json.loads((FIXTURE / "expected_band_inputs" / "bands_44100.json").read_text())
CASES = (("masker_1k_a8192", 3), ("burst_1k_a8192_onset2000", 4))
PARTITION, BAND = 50, 16  # partition 50 is interior to band 16 only (bu 49, bo 52)
PAIRS = (  # (masker clip, target clip): each pair in both directions
    ("masker_1k_a8192", "target_2k_a1024"),
    ("masker_1k_a81.92", "target_2k_a10.24"),
)
CONTROLS = (  # exact-silence controls, reported apart from the tone pairs
    ("silence_12granules", "target_2k_a1024"),
    ("silence_12granules", "target_2k_a10.24"),
    ("masker_1k_a8192", "silence_12granules"),
    ("silence_12granules", "silence_12granules"),
)


def raw_samples(name: str) -> np.ndarray:
    return np.frombuffer((FIXTURE / "inputs" / f"{name}.raw").read_bytes(), dtype="<i2").astype(np.int16)


def partition_values(name: str) -> tuple[np.ndarray, np.ndarray]:
    """eb and the final thr per call from the lower-level kernels, every call from call 0 (diagnostics only)."""
    frames = frame_long_blocks(raw_samples(name), sample_rate_hz=44100)
    long, short = long_window_spectrum(frames.windows), short_window_spectrum(frames.windows)
    cw = dist10_unpredictability(long.energy, long.phase, short.energy, short.phase)
    sums = dist10_partition_sums(long.energy, cw)
    spread = dist10_spread_tonality_input(sums.eb, sums.cb)
    return sums.eb, dist10_partition_thresholds(spread.ecb, spread.log_ratio).thr


def python_chain(name: str) -> dict:
    result = dist10_clip_to_bands(raw_samples(name), sample_rate_hz=44100)  # production entry point
    eb, thr = partition_values(name)
    for k in range(result.call.size):  # the verifier's independent mapping of the partition values must agree
        if result.en[k].tolist() != vf.map_long_bands(eb[k].tolist(), BANDS) or result.thm[k].tolist() != (
            vf.map_long_bands(thr[k].tolist(), BANDS)
        ):
            raise SystemExit(f"{name} call {k}: entry point differs from the verifier's mapping of the kernels' values")
    return {
        "eb": eb, "thr": thr, "en": result.en, "thm": result.thm,
        "eligible": np.array(result.eligible, dtype=bool), "silent_window": np.asarray(result.nonzero_samples) == 0,
    }  # fmt: skip


def reference(name: str) -> dict:
    """Probe B values with independently derived eligibility: contract §2.2, k >= 2 and 576 k + 256 <= N, where N is
    the original clip length (the raw input; Probe B's recorded clip_samples must agree). Silence is Probe B's own
    recorded count of nonzero int16 samples in the window."""
    b = vf.records("expected_long_mapping", name)
    n = raw_samples(name).size
    if any(r["clip_samples"] != n for r in b):
        raise SystemExit(f"{name}: Probe B clip_samples differs from the raw input length {n}")
    return {
        "en": np.array([r["en"] for r in b]),
        "thm": np.array([r["thm"] for r in b]),
        "eligible": np.array([r["call"] >= 2 and 576 * r["call"] + 256 <= n for r in b]),
        "silent_window": np.array([r["window_nonzero_samples"] == 0 for r in b]),
        "long_path": np.array([bool(r["encoder_long_path"]) for r in b]),
    }


def check_metadata(names, chains, refs) -> None:
    """The framer's eligibility and silence flags must equal the reference's on every call of every input."""
    for name in names:
        p, r = chains[name], refs[name]
        for field in ("eligible", "silent_window"):
            got, ref = p[field], r[field]
            if got.shape != ref.shape:
                raise SystemExit(f"{name}: {field} has {got.size} calls in the framer and {ref.size} in Probe B")
            for k in np.flatnonzero(got != ref):
                raise SystemExit(f"{name} call {int(k)}: framer {field} {bool(got[k])}, reference {bool(ref[k])}")
    calls = sum(refs[name]["eligible"].size for name in names)
    print(f"entry-point eligibility and silence flags equal the reference on all {calls} calls of {len(names)} inputs")


def band_residuals(names, chains, refs, short_path_only: bool = False) -> None:
    worst, selected_calls, selected_cells = {}, 0, 0
    for name in names:
        p, r = chains[name], refs[name]
        rows = np.flatnonzero(~r["long_path"]) if short_path_only else np.arange(r["long_path"].size)
        if rows.size == 0:
            continue  # no short-path call in this input
        selected_calls += rows.size
        selected_cells += rows.size * r["en"].shape[1]
        for quantity in ("en", "thm"):
            ref, got = r[quantity], p[quantity]
            if ref.shape != got.shape:
                raise SystemExit(f"{name}: shape {got.shape} vs probe B {ref.shape}")
            ref, got = ref[rows], got[rows]  # selected rows only; rows[i] keeps the original call ID
            diff = np.abs(got - ref)
            for kind, err in (("absolute", diff), ("relative", diff / ref)):
                i, sb = np.unravel_index(int(np.argmax(err)), err.shape)
                key = (quantity, kind)
                if err[i, sb] > worst.get(key, (-1.0,))[0]:
                    k = int(rows[i])
                    worst[key] = (float(err[i, sb]), name, k, int(sb), float(ref[i, sb]), float(got[i, sb]),
                                  bool(r["long_path"][k]))  # fmt: skip
    scope = "short-path calls only" if short_path_only else f"every call of {len(names)} inputs"
    print(f"B Python chain through the band mapping, {scope}, against probe B:")
    if selected_calls == 0:
        print("  no calls selected")
        return
    for (quantity, kind), (err, name, k, sb, ref, got, long_path) in sorted(worst.items()):
        if err == 0.0:
            print(
                f"  {quantity} {kind:8s} exact agreement on all {selected_calls} selected calls ({selected_cells} cells)"
            )
            continue
        path = "long" if long_path else "short-path"
        print(
            f"  {quantity} {kind:8s} {err:.3e} at {name} call {k} ({path}) band {sb}: probe B {ref:.9g}, python {got:.9g}"
        )


def partition_50_cases(chains) -> None:
    bu, bo = BANDS["bu_l"][BAND], BANDS["bo_l"][BAND]
    print(f"\npartition {PARTITION} enters only band {BAND} (bu {bu}, bo {bo}; interior, weight 1):")
    for name, k in CASES:
        h = {r["call"]: r for r in vf.records("expected_band_inputs", name)}[k]
        b = vf.records("expected_long_mapping", name)[k]
        p = chains[name]
        print(f"  {name} call {k} (encoder {'long' if b['encoder_long_path'] else 'short'} path):")
        for label, captured, python, band_ref, band_got in (
            ("eb", h["band_eb"], p["eb"][k], b["en"][BAND], p["en"][k, BAND]),
            ("thr", h["band_thr"], p["thr"][k], b["thm"][BAND], p["thm"][k, BAND]),
        ):
            w1, w2 = BANDS["w1_l"][BAND], BANDS["w2_l"][BAND]
            terms = [w1 * captured[bu], captured[50], captured[51], w2 * captured[bo]]
            share = captured[PARTITION] / band_ref
            print(f"    {label}[{PARTITION}] {captured[PARTITION]:.9g} -> {python[PARTITION]:.9g} "
                  f"(rel {abs(python[PARTITION] - captured[PARTITION]) / captured[PARTITION]:.3e}); "
                  f"band {BAND} {band_ref:.9g} -> {band_got:.9g} (rel {abs(band_got - band_ref) / band_ref:.3e}); "
                  f"share of p{PARTITION} {share:.3f}; terms w1*p{bu}, p50, p51, w2*p{bo} = "
                  f"{', '.join(f'{v:.4g}' for v in terms)}")  # fmt: skip


def margins(t: np.ndarray, e: np.ndarray, frames: list[int]) -> np.ndarray:
    return directional_masking_margin_db(t[frames], e[frames]).margin_db


def direction(masker: str, target: str, chains, refs) -> dict:
    # calls eligible for both stems (framer metadata, checked equal to the reference by check_metadata)
    frames = np.flatnonzero(chains[masker]["eligible"] & chains[target]["eligible"]).tolist()
    ref = margins(refs[masker]["thm"], refs[target]["en"], frames)
    got = margins(chains[masker]["thm"], chains[target]["en"], frames)
    err = np.abs(got - ref)
    i, sb = np.unravel_index(int(np.argmax(err)), err.shape)
    k = frames[i]
    flips = [
        (frames[a], int(c), float(ref[a, c]), float(got[a, c]))
        for a, c in zip(*np.nonzero(np.sign(ref) != np.sign(got)))
    ]
    nearest = np.unravel_index(int(np.argmin(np.abs(ref))), ref.shape)
    silent_ref = refs[target]["silent_window"][frames]
    silent_got = chains[target]["silent_window"][frames]
    return {
        "frames": frames, "max_err": float(err[i, sb]), "at": (k, int(sb)),
        "T": (float(refs[masker]["thm"][k, sb]), float(chains[masker]["thm"][k, sb])),
        "E": (float(refs[target]["en"][k, sb]), float(chains[target]["en"][k, sb])),
        "margin": (float(ref[i, sb]), float(got[i, sb])), "flips": flips,
        "nearest_zero": (frames[nearest[0]], int(nearest[1]), float(ref[nearest]), float(got[nearest])),
        "exceed": (int((ref > 0).sum()), int((got > 0).sum()), ref.size),
        "silent_target_frames": (int(silent_ref.sum()), bool(np.array_equal(silent_ref, silent_got))),
        "range": (float(ref.min()), float(ref.max())),
    }  # fmt: skip


def report_direction(label: str, masker: str, target: str, d: dict) -> None:
    k, sb = d["at"]
    print(f"  {label}: {masker} -> {target}, valid frames {d['frames'][0]}-{d['frames'][-1]} ({len(d['frames'])} x 21)")
    print(f"    margin range (probe B) {d['range'][0]:.2f} .. {d['range'][1]:.2f} dB; cells T > E: probe B {d['exceed'][0]}, "
          f"python {d['exceed'][1]} of {d['exceed'][2]}")  # fmt: skip
    print(f"    largest |error| {d['max_err']:.3e} dB at frame {k} band {sb}: margin {d['margin'][0]:.6f} -> "
          f"{d['margin'][1]:.6f} dB, T {d['T'][0]:.9g} -> {d['T'][1]:.9g}, E {d['E'][0]:.9g} -> {d['E'][1]:.9g}")  # fmt: skip
    nk, nsb, nr, ng = d["nearest_zero"]
    print(f"    sign changes: {len(d['flips'])} {d['flips'][:5]}; cell nearest 0 dB: frame {nk} band {nsb}, "
          f"{nr:.4f} -> {ng:.4f} dB")  # fmt: skip
    count, same = d["silent_target_frames"]
    print(f"    exactly silent target windows among valid frames: {count} (python flag equals probe B: {same})")


def main() -> None:
    names = sorted(p.stem for p in (FIXTURE / "expected_long_mapping").glob("*.jsonl"))
    chains = {name: python_chain(name) for name in names}
    refs = {name: reference(name) for name in names}
    check_metadata(names, chains, refs)
    print("A captured eb/thr (probe H) through the mapping: exact on every probe B call (verify_fixture.py)")
    band_residuals(names, chains, refs)
    band_residuals(names, chains, refs, short_path_only=True)
    partition_50_cases(chains)
    print("\nC directional margins, probe B against the package chain (history from call 0 per clip):")
    for masker, target in PAIRS:
        for a, b in ((masker, target), (target, masker)):
            report_direction("pair", a, b, direction(a, b, chains, refs))
    print("  exact-silence controls (target energy is the FFT floor wherever its window is silent):")
    for masker, target in CONTROLS:
        report_direction("control", masker, target, direction(masker, target, chains, refs))


if __name__ == "__main__":
    main()
