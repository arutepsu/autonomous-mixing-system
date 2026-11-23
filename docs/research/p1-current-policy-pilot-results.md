# Current experimental P1 configuration — engineering pilot

Recorded 2026-11-23. The owner requested proceeding with the current thresholds to build the system, accepting that they may not yield good mixing. **This authorizes an explicitly experimental engineering direction, not a claim that the values are scientifically calibrated.** No production diagnosis or coordinator was changed in this data check.

## Frozen configuration and scope

One configuration only: both source band energies ≥40 dB above the diagnostic noise reference; quiet elevation ≥0.02 dB; robust M>0.02 dB; at least 100 frozen cells; robust fraction ≥25%; at least four consecutive calls in a band. The energy/quiet boundaries are inclusive and the robust comparison strict. No parameter was adjusted after examining results.

The configuration was saved before evaluating the existing 198 direction-cases. Initial support for each Q/Z family is formed from its nominal inputs and kept unchanged across variants; boundary inputs are separately described initial states, not nominal→perturbation comparisons. Original source grids provide both E_A and E_B; the masker's energy is not inferred from T_A. No new signal, DSP run, musical analysis, provider or FL Studio action occurred.

## Result

| Check | Observed |
| --- | ---: |
| Direction-case rows evaluated | 198 |
| Declared initial pair/direction support rows | 94 |
| Initial support sizes | 0, 2 or 40 cells |
| Initial rows meeting the 100-cell floor | **0** |
| Case rows inheriting empty initial support | 124 |
| Hypothetical candidate triggers | **0** |
| Margin/robust-label perturbation events on retained initial cells | 0 |

Of the 94 initial rows, 28 are empty, two contain two cells, and 64 contain 40 cells. The retained boundary cells occupy bands 4–7; the two decay rows add isolated cells in bands 9–10. Other supported bands retain no cells under this control set. These rows share sources and are not independent statistical cases.

**The absence of sensitivity events is not proof that the screen is reliable:** it removes almost all ordinary sustained/decay evidence. Sparse retained coverage is insufficient to establish a general energy domain. The remaining boundary cases have no nominal perturbation delta by construction and must not be counted as a successful sensitivity test.

The 100-cell floor is an operational evidence-sufficiency condition, not a condition every short numerical test must satisfy. Each generated clip is about 0.157 seconds, with only 200 geometric cells per direction; narrow tones can leave only a few bands after energy screening. The result does not prove that the same configuration will abstain on 30-second music, nor that it will produce sensible candidates there.

## Engineering consequence

Keep the values unchanged for the planned experimental system. Its honest behavior on these particular short controls is insufficient evidence / no candidate. Do not lower the cell floor, widen bands, drop energy checks or repeat a short clip simply to turn this pilot into a positive result.

The next implementation should support both valid abstention and a measured experimental-rule trigger when sufficient real bound evidence exists. Temporary longer analytical fixtures may exercise software branches in tests, but are engineering fixtures, not a new successful musical/calibration study. No hand-written diagnosis or decision-file substitute belongs in the runtime.

Scientific calibration, correct mixing, iteration benefit and generalization remain open. Threshold configuration and safe system behavior can be implemented while reporting those limits explicitly. No extra musical dataset is needed to implement/test those contracts.

## Evidence and checks

Private evidence: `C:/uni/Bachelor-Thesis/test/p1-policy-pilot/`.

- `configuration.json` preserves the initial freeze.
- `configuration-reader-corrected.json` is the freeze used by the completed read-only calculation; same numerical settings and input hashes.
- `evaluate.py`, `results.json` preserve the calculation and all initial cells/exclusions/outcomes.
- `reader-correction.json` and the original script preserve one field-name correction: saved case records use `nominal_id`, not `nominal`. The reader stopped at its first row; no policy score or value change preceded the correction.

Original numerical file hashes were checked before analysis. The script refuses overwriting differing results/configuration. Results SHA-256: `a43be0a665c93e0f9a89affea2ed90d2bd0ba0e11b24720afad0af9d16affabb`. No product suite was rerun or product-code result claimed for this data-only check. Everything remains uncommitted.
