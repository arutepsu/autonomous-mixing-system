"""Compare dist10's partition thresholds (probe G) with reconstructions; print residuals per stage.

  A. captured ecb, tonality input and history through the threshold arithmetic          (the verifier checks it)
  B. the package's chain (Python spectra, cw, partition sums and spreading) for every call from call 0, through the
     same threshold arithmetic, with the nb_1/nb_2 history replayed from zero using the Python nb
For B: the worst residual of each stage, whether a difference changes the term that sets thr (qthr, nb, 2 nb_1 or
16 nb_2), the clamp side of tbb or the SNR term, and the full chain at the named cases masker call 3 / burst call 4,
partition 50. Uses the verifier's rules. Prints evidence for review; sets no tolerance. The threshold arithmetic here
is the verifier's reconstruction, not a package kernel (that is a later task), and nothing is band-mapped.

Usage: uv run python tests/fixtures/mpeg_psy_model2_dist10/capture/compare_threshold_inputs.py
"""

import json
import pathlib
import sys

import numpy as np

FIXTURE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FIXTURE / "capture"))

import compare_spreading_inputs as cs  # noqa: E402  (sibling capture script)
import verify_fixture as vf  # noqa: E402

from autonomous_mixing.adapters.dsp_analysis.spreading import dist10_spread_tonality_input  # noqa: E402

CASES = (("masker_1k_a8192", 3, 50), ("burst_1k_a8192_onset2000", 4, 50))
STAGES = (  # field, relative residual?
    ("ecb", True),
    ("tonality_input", False),
    ("tonality_raw", False),
    ("tonality", False),
    ("snr", False),
    ("nb", True),
    ("pre_echo_limit", True),
    ("thr", True),
)


def clamp_side(raw: float) -> str:
    return "0" if raw < 0.0 else "1" if raw > 1.0 else "interior"


def snr_term(minval: float, tbb: float) -> str:
    tonal = 29.0 * tbb + 6.0 * (1.0 - tbb)
    return "minval" if minval > tonal else "tie" if minval == tonal else "tonal"


def python_chain(name: str, calls: int, tables: dict) -> list[dict]:
    p = cs.python_partitions(name)
    spread = dist10_spread_tonality_input(p.eb, p.cb)
    nb_1, nb_2 = [0.0] * 63, [0.0] * 63
    out = []
    for call in range(calls):
        ecb = [float(v) for v in spread.ecb[call]]
        log_ratio = [float(v) for v in spread.log_ratio[call]]
        terms = vf.threshold_terms(ecb, log_ratio, tables)
        limit, thr = vf.pre_echo_threshold(terms["nb"], nb_1, nb_2, tables["qthr_l"])
        out.append({
            "call": call, "ecb": ecb, "tonality_input": log_ratio, **terms, "nb_1_before": nb_1, "nb_2_before": nb_2,
            "pre_echo_limit": limit, "thr": thr,
        })  # fmt: skip
        nb_1, nb_2 = terms["nb"], nb_1
    return out


def main() -> None:
    tables = json.loads((FIXTURE / "expected_threshold" / "tables_44100.json").read_text())
    minval = tables["minval"]
    worst: dict[str, tuple] = {}
    changes = {"thr term": [], "tbb clamp side": [], "SNR term": []}
    chains = {}
    for path in sorted((FIXTURE / "expected_threshold").glob("*.jsonl")):
        captured = vf.records("expected_threshold", path.stem)
        chain = python_chain(path.stem, len(captured), tables)
        chains[path.stem] = (captured, chain)
        for g, p in zip(captured, chain, strict=True):
            for field, relative in STAGES:
                ref = np.array(g[field], dtype=np.float64)
                if field in ("ecb", "nb"):
                    ref = np.array(g[field], dtype=np.float32).astype(np.float64)
                got = np.array(p[field], dtype=np.float64)
                diff = np.abs(got - ref)
                err = np.divide(diff, np.abs(ref), out=np.zeros_like(diff), where=ref != 0) if relative else diff
                b = int(np.argmax(err))
                if err[b] > worst.get(field, (-1.0,))[0]:
                    worst[field] = (float(err[b]), path.stem, g["call"], b, float(ref[b]), float(got[b]))
            for b in range(63):
                where = (path.stem, g["call"], b)
                p_rec = {**p, "nb": p["nb"]}
                t_g, t_p = vf.limiting_term(g, tables["qthr_l"], b), vf.limiting_term(p_rec, tables["qthr_l"], b)
                if t_g != t_p:
                    changes["thr term"].append((*where, t_g, t_p))
                if clamp_side(g["tonality_raw"][b]) != clamp_side(p["tonality_raw"][b]):
                    changes["tbb clamp side"].append((*where, g["tonality_raw"][b], p["tonality_raw"][b]))
                if snr_term(minval[b], g["tonality"][b]) != snr_term(minval[b], p["tonality"][b]):
                    changes["SNR term"].append((*where, g["snr"][b], p["snr"][b]))
    total = sum(len(c) for c, _ in chains.values())
    print(f"A captured inputs and history: reconstruct exactly (verify_fixture.py, {total} calls)")
    print("B Python chain from call 0, history replayed from zero; worst residual per stage:")
    for field, relative in STAGES:
        err, name, call, b, ref, got = worst[field]
        kind = "relative" if relative else "absolute"
        print(
            f"  {field:15s} {kind} {err:.3e} at {name} call {call} partition {b} (captured {ref:.9g}, python {got:.9g})"
        )
    for label, items in changes.items():
        print(f"  {label} changes: {len(items)}")
        for item in items[:10]:
            print("    ", item)
    print("  smallest captured decision margins (a change needs a residual of at least this size):")
    margins = {"thr: winner vs runner-up (relative)": (np.inf,), "tbb: distance of raw to 0 or 1": (np.inf,),
               "SNR: tonal term vs minval (dB)": (np.inf,)}  # fmt: skip
    for name, (captured, _) in chains.items():
        for g in captured:
            for b in range(63):
                nb = float(np.float32(g["nb"][b]))
                limit_terms = sorted((nb, 2.0 * g["nb_1_before"][b], 16.0 * g["nb_2_before"][b]))
                candidates = sorted((tables["qthr_l"][b], limit_terms[0]), reverse=True)  # thr = max(qthr, limit)
                gaps = []
                if candidates[1] > 0:
                    gaps.append(candidates[0] / candidates[1] - 1.0)
                if limit_terms[0] > tables["qthr_l"][b] and limit_terms[0] > 0:  # the limit sets thr: its own runner-up
                    gaps.append(limit_terms[1] / limit_terms[0] - 1.0)
                gap = min(gaps) if gaps else np.inf
                if gap < margins["thr: winner vs runner-up (relative)"][0]:
                    margins["thr: winner vs runner-up (relative)"] = (gap, name, g["call"], b)
                raw = g["tonality_raw"][b]
                d = min(abs(raw), abs(raw - 1.0))
                if d < margins["tbb: distance of raw to 0 or 1"][0]:
                    margins["tbb: distance of raw to 0 or 1"] = (d, name, g["call"], b)
                tonal = 29.0 * g["tonality"][b] + 6.0 * (1.0 - g["tonality"][b])
                d = abs(tonal - minval[b])
                if 0 < d < margins["SNR: tonal term vs minval (dB)"][0]:
                    margins["SNR: tonal term vs minval (dB)"] = (d, name, g["call"], b)
    for label, (value, *where) in margins.items():
        print(f"    {label}: {value:.3e} at {tuple(where)}")
    print("    (SNR ties with gap 0, where tbb = 0 gives exactly minval = 6 dB, select equal values and are excluded)")

    print("\nnamed cases (captured -> python):")
    for name, call, b in CASES:
        g, p = chains[name][0][call], chains[name][1][call]
        print(f"  {name} call {call} partition {b}: thr set by {vf.limiting_term(g, tables['qthr_l'], b)} "
              f"(qthr {tables['qthr_l'][b]})")  # fmt: skip
        for field, relative in STAGES + (("nb_1_before", True), ("nb_2_before", True)):
            ref = g[field][b]
            if field in ("ecb", "nb"):
                ref = float(np.float32(ref))
            got = p[field][b]
            err = abs(got - ref) / abs(ref) if relative and ref else abs(got - ref)
            print(f"    {field:15s} {ref:.17g} -> {got:.17g}  ({'rel' if relative else 'abs'} {err:.3e})")


if __name__ == "__main__":
    main()
