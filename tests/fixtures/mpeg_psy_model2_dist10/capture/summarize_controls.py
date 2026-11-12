"""Print the control-case numbers quoted in the front-end contract, computed from probe B's recorded values.

Margin = 10 log10(T / E) per band, with T = thm of the masker run and E = en of the target run on the same call.
Only calls k >= 2 are summarised (start-up calls 0-1 excluded, as proposed in the contract). This script derives
descriptive numbers for review; it sets no activity or action threshold.

Usage: python summarize_controls.py
"""

import json
import math
import pathlib

FIXTURE = pathlib.Path(__file__).resolve().parents[1]
FIRST_VALID_CALL = 2


def records(name: str) -> list[dict]:
    path = FIXTURE / "expected_long_mapping" / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()][FIRST_VALID_CALL:]


def margins(masker: str, target: str) -> list[list[float]]:
    return [
        [10.0 * math.log10(t / e) for t, e in zip(m["thm"], g["en"], strict=True)]
        for m, g in zip(records(masker), records(target), strict=True)
    ]


def describe(masker: str, target: str) -> None:
    grid = margins(masker, target)
    cells = [v for row in grid for v in row]
    positive = sum(v > 0 for v in cells)
    print(
        f"{masker} -> {target}: calls {FIRST_VALID_CALL}-{FIRST_VALID_CALL + len(grid) - 1}, "
        f"cells with T > E: {positive}/{len(cells)}, margin range {min(cells):.1f} .. {max(cells):.1f} dB"
    )
    print("  call 6 margins dB:", " ".join(f"{v:.1f}" for v in grid[6 - FIRST_VALID_CALL]))


def quiet_share(masker: str) -> None:
    row = records(masker)[6 - FIRST_VALID_CALL]
    share = [q / t for q, t in zip(row["thm_from_quiet_partitions"], row["thm"], strict=True)]
    above = [10.0 * math.log10(t / q) for t, q in zip(row["thm"], row["thm_quiet"], strict=True)]
    print(f"{masker} call 6: share of thm from quiet-limited partitions:", " ".join(f"{v:.2f}" for v in share))
    print(f"{masker} call 6: thm above threshold in quiet, dB:", " ".join(f"{v:.1f}" for v in above))


def main() -> None:
    silence = records("silence_12granules")[0]
    print("silence en (FFT floor) per band:", " ".join(f"{v:.4g}" for v in silence["en"]))
    print("silence thm (= threshold in quiet) per band:", " ".join(f"{v:.4g}" for v in silence["thm"]))
    print(
        "silence thm_signal / thm_quiet, max over bands:",
        f"{max(s / q for s, q in zip(silence['thm_signal'], silence['thm_quiet'], strict=True)):.3g}",
    )
    for target in ("target_2k_a1024", "target_2k_a10.24"):
        describe("silence_12granules", target)
    describe("masker_1k_a8192", "silence_12granules")
    describe("silence_12granules", "silence_12granules")
    for masker in ("masker_1k_a8192", "masker_1k_a81.92"):
        quiet_share(masker)
    burst = json.loads(
        (FIXTURE / "expected_long_mapping" / "burst_1k_a8192_onset2000.jsonl").read_text().splitlines()[4]
    )
    print(
        "burst call 4 (short path): pe",
        round(burst["pe"]),
        "band 6 en",
        f"{burst['en'][6]:.4g}",
        "thm",
        f"{burst['thm'][6]:.4g}",
        "thm_quiet",
        f"{burst['thm_quiet'][6]:.4g}",
        "nonzero samples",
        burst["window_nonzero_samples"],
    )


if __name__ == "__main__":
    main()
