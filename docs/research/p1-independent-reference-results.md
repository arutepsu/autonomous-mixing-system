# P1 selected independent-reference captures

Documented **23 November 2026**. Selection was frozen at `2026-11-22T19:39:09.264576+00:00`, before any new C result. Owner authorized comparison after the [small numerical-control results](p1-small-numerical-controls-results.md).

**Result:** The pinned C backend reproduces all old sentinel values exactly and closely agrees with the selected new Python cases. The largest observed supported-band margin difference increases to **0.006586305084 dB**; no C/Python sign disagreement occurs in the 4,000 selected supported cells. Independent C also reproduces the large input-perturbation effect. This expands numerical evidence for this selected subset, not a universal tolerance, perceptual validation or approved qualification policy.

## Selection fixed before capture

Ten pairs, both directions (20 direction-cases):

- Four adjacent boundary pairs: target amplitudes 4127/4128 at phases 0 and pi/2 from the already completed bounded search.
- Nominal plus perturbed sustained pair behind the previously largest internal sensitivity: `tone-16-0-nominal` and `tone-16-0-B-plus`.
- Nominal plus prior worst perturbed pair for each registered decay tau: 0.02 and 0.1 seconds, with B-plus in each selected case.

This is **outcome-informed development selection**: boundary points and within-family extremes were selected after Python sensitivity results, but before independent C results. It is not random, blind, held-out or representative validation of all 198 controls. Every selected existing count hash is frozen in `selection.json` (SHA-256 `e2a140a564a58c32fb60f557ad25c11b70432152b9af98bb69805e1dfb926f83`). No new signal was generated, and no pair was added or removed after seeing C differences.

The selection uses 11 unique source streams, two already present among the nine old sentinel streams. Thus the combined copied raw set has 18 unique hashes; counts of source/direction/cell rows must not be treated as independent sample sizes. All selected sources have 6,912 mono int16 counts at 44,100 Hz. Startup/history is retained from call 0.

## Backend and capture provenance

The preserved local Probe H binary exactly matches the original fixture provenance:

- Binary SHA-256: `cffe8ec22a74d886c19143c83ba1ef7550177d10347f9180c31bad30cc2ffb24`.
- Local image: `gcc@sha256:d47e63a2a4621ff4da4e0825bfe8c52c2e65e957333d7878e5c3a6eee2cd525e` (original pinned GCC 13.5.0 / x86_64 environment).
- Original source, patched probe, harness-source and table hashes were checked against the stored fixture provenance before reuse. The binary was reused unchanged, not replaced by a new implementation or rebuilt with different flags.

Docker Desktop was initially stopped; the existing installation was started and the pinned image was already available. No image pull, download, dependency installation or network access was used. Docker remains running. Each container used `--pull never --network none --read-only`, one CPU, a 512 MiB memory limit, dropped capabilities and no-new-privileges. Backend, exact inputs and capture script were read-only mounts; only the new private output directory was writable.

Two capture sets were executed, each with a sentinel phase followed by the selected-control phase. The first sentinel phase had to reproduce all original base fields of all **92 calls / nine inputs** exactly before selected capture was allowed. Both sets passed this check. All five emitted band-table arrays also match the original values exactly. Every output file within each numerical phase repeats byte-for-byte between capture sets, including raw stdout/stderr and table files. Command/completion records have different output paths and are not claimed byte-identical.

The raw stdout includes the documented `absthr` status line. The first private parser stopped because it initially attempted to parse that line as JSON. A second metadata check stopped on five source-text annotations that the stored bands JSON includes but C does not emit. These were reader/check-wrapper issues, not numerical mismatches: the documented stdout filter and exact comparison of the five actual emitted arrays were applied. The saved sentinel output was reused, no C/production source or tolerance changed, and selected capture had not yet run. Original wrapper versions and both errata are retained privately; no failed check was hidden or an unfavorable selected result discarded.

## Comparisons

The Python grids are the original frozen grids from the registered control run, not newly regenerated results. All original numerical-file hashes and source/input fingerprints still agree. New C `en`/`thm` are compared at every call and band, including startup and band 0. H's captured partition inputs map exactly to its band outputs through the existing verifier on all 132 selected source calls.

Primary margin comparisons use the **same reviewed cancellation-safe margin kernel on both backends' E/T arrays**. The Python margins are checked equal to the earlier frozen case arrays exactly. A separately retained scalar-logarithm comparison is an arithmetic cross-check; it is not substituted for the near-equality `log1p` path in production results.

Coverage: 2,772 source call-band rows per E/T quantity, of which 2,200 are eligible bands 1–20; 5,040 directional call-band rows, of which **4,000** are eligible bands 1–20. Per-cell residuals, source hashes and eligibility labels are retained. Opposite directions are distinct cases. Band 0 remains diagnostic only.

| Scope/quantity | Maximum observed discrepancy |
| --- | ---: |
| Selected E, eligible bands 1–20, relative | 0.001279350428 (about 0.128%) |
| Selected T, eligible bands 1–20, relative | 0.001248449234 (about 0.125%) |
| Selected M, eligible bands 1–20, absolute | **0.006586305084 dB** |
| Selected M sign disagreements | **0 / 4,000** |

The maximum M error is `boundary-0-4127`, reverse B→A, call 9, band 12: Python −6.314750399245 dB; C −6.308164094161 dB. It is a different call/band from the selected near-zero boundary point. This new maximum exceeds the old fixture observation of 0.005552104558 dB, illustrating why a finite prior maximum must not be treated as a guaranteed bound.

Per-family maxima across selected supported cells: sustained nominal/perturbed **0.005553048 dB**; tau 0.02 decay **0.003587847 dB**; tau 0.1 decay **0.005575313 dB**; boundary pairs **0.006586305 dB**. These are descriptive maxima, not separately validated operational allowances.

### Selected zero-boundary points

At call 6/band 6, A→B:

| Pair | Python M, dB | C M, dB | Absolute difference, dB |
| --- | ---: | ---: | ---: |
| boundary-0-4127 | +0.001505096453 | +0.001505472199 | 0.000000375746 |
| boundary-0-4128 | -0.000577644827 | -0.000577446106 | 0.000000198722 |
| boundary-1-4127 | +0.001686754808 | +0.001686858015 | 0.000000103208 |
| boundary-1-4128 | -0.000506329209 | -0.000506135661 | 0.000000193548 |

All four signs agree; the largest local difference is about 3.76e−7 dB. These points now have independent backend evidence, but four points in one selected band/call do not justify a global fine-grained deadband. A near-zero point having smaller error than another band's maximum is not contradictory: the cancellation-safe arithmetic and signal/energy regimes differ.

### Input sensitivity is also present in C

At the previously largest internal sensitivity point (`tone-16-0-B-plus`, B→A, call 3/band 4), nominal→perturbed M changes by:

- Python: **+18.997459625 dB**.
- Independent C: **+18.997980612 dB**.
- Difference between those effects: about **0.000520988 dB**.

Thus the approximately 19 dB change is not explained by Python/C implementation disagreement. It is the model's response to the declared changed count sequence in a weak band. Both endpoint margins at that particular case remain negative, as noted in the earlier report; it is not itself a zero-crossing or an audible-masking finding.

Across the three selected nominal/perturbed families, both directions and supported cells (1,200 dependent comparisons), C and Python each record **11 input-driven sign changes**, with no disagreement about which comparisons change sign. The largest discrepancy between their input-effect deltas is **0.003462652 dB** in the tau 0.1 reverse-direction decay, call 8/band 4. These counts cover only the selected subset, not all 167 sign changes from the earlier 19,200-comparison analysis.

## Evidence and reproduction

Private directory: `C:/uni/Bachelor-Thesis/test/p1-independent-capture/`.

- `selection.json`, `selection.sha256`, `pre-capture-hashes.json`: fixed subset, exact raw sources, selection rationale, binary/image/source snapshot and intent before capture.
- `backend/`, `inputs/`, `sentinels-inputs/`, `selected-inputs/`: verified existing backend and exact copies of already-frozen numerical counts. No musical audio or new waveform generation.
- `capture-1/`, `capture-2/`: recorded commands/results, raw C outputs and source/table data. Each set has sentinel verification and its own hashed completion inventory; each recorded set occupies about 1.62 MB.
- `execution-procedure.json`, `parser-erratum.json`, `table-metadata-erratum.json` and saved wrapper versions: exact execution/reader corrections described above.
- `source-residuals.json`: E/T residual rows, SHA-256 `42ba54941c29e0b81e4c7e56e348c4a74bfc54d050f619e3a3fc49141752d7cb`.
- `kernel-directional-residuals.json`: primary margin rows, SHA-256 `53ebe20babdf3252e837e7ce616ea570432055eea99dc7492e357eccd37fce98`.
- `kernel-summary.json`: primary summary, SHA-256 `b87ea00b2c627392312ef31ad32e7b8af9c47a09dbeee3a6aad7e3b183280263`.
- `sensitivity-agreement.json`: selected nominal/perturbed C/Python effects.
- `freeze_selection.py`, `run_capture.py`, `compare.py`, `compare_reviewed_kernel.py`: local reproduction/audit procedure. The scalar comparison and primary reviewed-kernel comparison are separately retained.

Commands actually used included:

```powershell
uv run --locked --extra agent python C:/uni/Bachelor-Thesis/test/p1-independent-capture/freeze_selection.py
uv run --locked --extra agent python C:/uni/Bachelor-Thesis/test/p1-independent-capture/run_capture.py 1
uv run --locked --extra agent python C:/uni/Bachelor-Thesis/test/p1-independent-capture/run_capture.py 2
uv run --locked --extra agent python C:/uni/Bachelor-Thesis/test/p1-independent-capture/compare_reviewed_kernel.py
```

The first capture command resumed its saved sentinel-check phase after the two wrapper corrections; it did not redo or replace C outputs. The paired capture output directories refuse ordinary reuse; preserve them. A fresh future reproduction requires a separate directory and the recorded source/input/image identities. Comparison scripts rehash inputs and prior outputs and refuse replacing different comparison results.

No repository product code or existing fixture was modified, and no product-suite result is claimed for this data-only task. The fixed control-source/capture fingerprint and all previous numerical results were verified unchanged afterward. No musical files were mounted or read, no provider/FL Studio operation or install occurred, and nothing was committed.

## What this now supports

There is independent evidence that the reviewed Python chain agrees closely with the pinned C backend on these selected difficult signals, including the sampled zero boundaries. Large weak-band sensitivity is present in both implementations. It should be handled by qualification and explicit uncertainty, rather than assumed to be a Python defect or hidden with a large margin deadband.

A conservative **experimental qualification-policy proposal** can now be drafted against the frozen numerical evidence, with its limited energy domain and uncertainties stated. It must still justify energy screening, a numerical/quiet-elevation allowance and evidence sufficiency before activation. The new maximum alone does not select an allowance; the 19 dB input change is not the backend-error allowance. Do not fit the preferred HT01 reference, claim all 198 controls independently validated, or claim perceptual accuracy. R2A remains INCOMPLETE; no S0, positive R2B candidate or R5 agent run is enabled.
