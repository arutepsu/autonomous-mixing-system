# P1 experimental policy pilot and feasible scientific evaluation

**Status:** Owner authorized proceeding with the current values for explicitly experimental system development, 2026-11-23. A read-only pilot on frozen numerical evidence is complete; production implementation/integration remains pending. Values are not scientifically calibrated and no diagnosis, live/model run or evaluation split is activated.

**Purpose:** Test one explicit, limited model-risk heuristic, then return effort to agent/tool/feedback experiments. Avoid a broad parameter search, new adapter, mandatory corpus size or professional listener panel. A pilot can fail or remain insufficient; it must not silently become production qualification.

**Basis:** [approved structural proposal](p1-minimum-qualification-proposal.md), [fixed numerical controls](p1-small-numerical-controls-results.md), [selected independent C captures](p1-independent-reference-results.md), [accepted P1 method](../superpowers/specs/2026-11-20-p1-diagnosis-method.md), SR-02/SR-03/SR-10. This is a smaller proposed pilot, not adoption of the optional six-project/three-listener registration.

## 1. What the evidence establishes

- Existing and selected new C/Python outputs agree closely on recorded signals. The selected supported-band maximum margin discrepancy is 0.006586305084 dB; it is empirical, not a universal error bound.
- Deterministic pre-round sub-count perturbations produce large weak-band changes in both implementations. They are changed-input sensitivity, not backend approximation error. They do not measure every real export error or human perception.
- Exact-silent windows and quiet-floor thresholds cannot alone support an active-source risk interpretation. Conversely, a nonzero window and T>Q alone do not establish reliable band evidence.
- There is no agent musical-performance result yet. Numerical tests must not be counted as musical projects, listening evidence or an agent evaluation.

Hafezi and Reiss (2015) evaluate objective measures alongside listening tests and caution that existing objective masking measures may not quantify perceived musical masking well. That supports keeping our claims narrow; it does not supply the values proposed below. [AES primary source](https://aes.org/publications/elibrary/elibrary-page/?id=17637).

## 2. One candidate configuration — proposed, not validated

The following are the author's engineering choices for **one falsifiable development pilot**. They are not literature-derived auditory thresholds. The owner subsequently authorized using them provisionally for engineering; this does not establish scientific calibration. Testing them may show empty support, sensitivity or insufficient coverage. Keep every result; do not relax them until HT01 looks desirable.

| Component | Proposed candidate | Reason for proposing it / remaining limitation |
| --- | --- | --- |
| Geometric scope | Existing complete-window calls, declared interval and bands 1–20 | Reuses the reviewed method; no band/interval search. Missing band-0 coverage remains explicit. |
| Initial activity | Both original PCM windows nonzero | Exact exclusion only, not an audibility or meaningful activity level. |
| Initial band-energy screen | Both E_A and E_B at least **40 dB above** the existing per-band diagnostic rounding-noise reference (energy ratio ≥10,000) | A deliberately conservative screening candidate. The theoretical reference is not measured noise, and 40 dB is not established by the old descriptive bins or any observed maximum. Cross-band spreading may be excluded; broad musical validity is unproved. |
| Initial quiet elevation | `10*(log10(T_A)-log10(Q)) >= 0.02 dB` | Separate empirical reserve above quiet: about 3.7 times the largest selected threshold log discrepancy (about 0.005425 dB). The multiplier/reserve is an engineering choice, not a guarantee across inputs. |
| Robust exceedance | `M > 0.02 dB` | About three times the observed selected C/Python margin maximum. This is a **candidate numerical allowance**, not a perceptual threshold or sufficient protection from input quantization. The energy/sensitivity checks remain separate. |
| Minimum support | `size(S0) >= 100` | Prevents a tiny handful of cells authorizing a result. Chosen as a simple operational floor; overlapping cells are not independent samples and 100 has no power/statistical justification. |
| Exceedance fraction | At least 25% of the same S0 cells robustly exceed | A deliberately explicit operational choice, not a calibrated probability or musical-quality boundary. |
| Persistence | At least 4 consecutive eligible calls in one configured band | The existing hop convention gives `4*576/44100 ≈ 0.052245 s`; this is a grid statistic over overlapping windows, not a validated duration of audible masking. |

Every energy/quiet boundary is inclusive as written; robust exceedance is strict. Invalid or missing inputs are unavailable, never zero. Empty support is unavailable. A cell may have multiple exclusion reasons. No numeric confidence score is introduced.

The same candidate number 0.02 appears for two different roles: quiet elevation and margin exceedance. They retain separate policy fields, evidence and justifications. It does not follow that every fixed-support improvement of 0.02 dB is meaningful. Candidate sufficiency, correction objectives and improvement acceptance remain different contracts; this proposal sets no correction-acceptance threshold or producer preference.

**Important:** these values are a development hypothesis, not the conclusion of our existing measurements. They do not define a validated domain merely by being written down. No musical results have been computed under them. The earlier test permission alone did not authorize implementation. The later owner direction now authorizes preparing the explicitly experimental engineering path; it does not establish scientifically qualified thresholds or a perceptual diagnosis.

## 3. Bounded pilot using existing evidence first

After separate approval of this candidate test:

1. Freeze this exact configuration and the existing evidence hashes before evaluating it. Evaluate the single configuration, not a search over several gates.
2. Use all existing registered numerical cases, retaining the independent-capture label only where independent C values actually exist. Energy screening requires E_A and E_B; read them from the existing per-source grids, never infer E_A from T_A. No new signals or musical renders are required for this first check.
3. On every nominal pair/direction, report support count and exclusion reasons by band and case. Apply the later-state rules to all declared perturbations/zero cases on the same nominal cells. Report both input-driven changes and the numerical backend residuals separately.
4. Report all sign/robust-label changes, the maximum/median/individual margin changes, and whether the 0.02 dB allowance is exceeded **inside the proposed energy screen**. Do not present the old 19 dB outlier outside a screen as an automatic failure inside it, or assume the screen fixes it without measuring.
5. Explicitly measure coverage: which phases, bands, sustained/decay cases and energy combinations are retained, and which retained regimes have independent C data. If cases/bands disappear, state that the pilot does not establish those domains; do not pool surviving high-energy cells into a universal claim.
6. If a screened perturbation changes its robust label or exceeds the candidate allowance, or the retained domain lacks adequate evidence, mark this configuration **not established**. Record the case. Stop after the one configuration; no automatic widening, new grid search, larger deadband or favorable-region selection.
7. Only after reviewing the numerical pilot should a separately authorized descriptive application to HT01 examine support availability and relation to owner goals. The reference preference and injected amounts are not the success target. A numerical pass alone does not establish qualification for arbitrary music.

A completed pilot must conclude either "this limited configuration merits the next bounded development check" or "not established, with these exact gaps." It must not say "validated masking detector." A minimal read-only evidence calculation/report is sufficient; do not build a policy framework or modify production ports for this check.

## 4. Fixed support and later candidate handling

If a policy is later approved after those reviews, form S0 from the **actual starting runtime mix**, never a clean reference. Freeze exact cell IDs, sources, parameters and evidence before any AI action. Later candidates use the same S0 and denominator.

- Muting the target or leaving its supported energy regime makes comparison unavailable.
- A nonzero masker leaving its supported energy regime also makes comparison unavailable.
- Exact-silent/quiet-floor masker reductions may remain assessable only through the separately verified quiet-floor control; retain finite M and flags on S0 rather than deleting cells.
- Missing/changed support on restart refuses analysis rather than rebuilding it.

No candidate automatically implies a correct gain cut, and proxy reduction is not independent proof of musical improvement. Producer intent and native-channel level/headroom/retained-audio guards remain separate. Runtime inputs contain no clean/manual-reference audio, injection amount, condition-bearing identifier or preferred outcome.

## 5. Scientific foundation: more than more files

More **independent musical projects** improve evidence about generalization; extra versions/repeats of one project answer other questions. All versions of HT01 remain development-only. The already inspected trap pilot also remains development-only. Numerical controls are engineering checks, not corpus diversity.

Proposed feasible collection sequence, **not a mandatory sample-size claim or split assignment**:

1. Keep HT01 and the existing trap pilot for development. Do not count their modified exports as new projects or re-render them simply to enlarge the count.
2. Create **one genuinely new project** for later evaluation, reserve its split before inspecting algorithm/agent outcomes, and keep all its variants in that split. Distinct arrangement, notes/rhythm and source choices are more useful than renaming or changing the faders of HT01. Record any reused sounds; do not claim source-disjoint data if samples overlap.
3. Stay initially within the declared target problem, permitted gain tools and input profile. A different genre can broaden a later study, but one example in a genre does not establish cross-genre generalization.
4. Only if time allows, add another independent held-out project. No six/nine-project target or paid panel is adopted. A one-project held-out demonstration remains a limited case study, not a population estimate.

For each project retain aligned full-selection stems and native master, file hashes/format, declared origin/alignment, source/license provenance, producer intent and allowed/protected tracks. Current runtime manifests expose only permitted current artifacts and prospective goals. Clean/reference exports, injected changes and listening judgments remain private evaluation metadata. Exact-dual-mono P1 eligibility is checked, never assumed; genuine stereo remains native for level measures. Later live gain trials must have a matching reviewed DAW session/profile; a new project is not silently inside the old calibrated project-title scope.

Do not wait for a large dataset before wiring a development agent run. Compose and test the existing tools/loop on known development material first; keep reserved evaluation material untouched until the method and operational goals are frozen.

## 6. Evaluate the agent, not only the proxy

Plan fair comparisons on the same initial input, producer goal, permitted action range and guards:

- Unprocessed/no-action starting state.
- A deterministic, predeclared rule using only the same permitted runtime evidence; it must not read the hidden injection or clean reference.
- One-pass AI proposal/execution.
- Iterative agent with measured feedback.

Report actual calls, writes, manual export cost and elapsed time; iteration naturally costs more, so do not compare quality without reporting cost or pretend unequal budgets are identical. Additional repeats on the same project estimate model/run variability, not musical diversity. Fix model/version/request/parameters and record every raw exchange before interpreting results.

Primary agent/system evidence should include proposal validity, respected permissions/guards, correctly verified accepted edits, feedback-dependent revisions, rollback/refusal correctness, unresolved outcomes, trace completeness and cost. Scripted failure tests support reliability; actual FL/provider end-to-end development runs are still needed before claiming real autonomous operation. No offline simulation or repeated numerical calculation replaces that evidence.

For musical assessment, add a question not reducible to the optimized model margin: for example blind preference for the producer's kick/bass goal, with retained bass weight and overall balance assessed separately. Randomize condition order/labels, document a consistent level-matching/playback procedure, allow no preference/uncertainty and retain all judgments. Report the level-matched listening view separately from actual exported-level guards; do not normalize the analyzed/corrected artifacts to make them pass.

Professional paid listeners are not required for a modest exploratory case study. If available, volunteers provide a perspective beyond the producer; record their background and limitations. If only the author listens, clearly report an author/producer preference study with familiarity and expectation bias, not independent expert ground truth or broad perceptual validation. A different assessment criterion and an independent assessor are two different kinds of independence.

ITU guidance helps choose an appropriate subjective audio test; it does not make this small paired musical-preference procedure a standards-compliant MUSHRA experiment. [ITU-R BS.1283](https://www.itu.int/rec/R-REC-BS.1283/en), [ITU-R BS.1534](https://www.itu.int/rec/R-REC-BS.1534/en).

## 7. Priority and stopping rule

Recommended order: review/test this one numerical candidate; compose a real **development** agent run using genuinely available evidence; freeze the operational/evaluation configuration; then evaluate on reserved independent music with fair baselines and a non-circular assessment. Collect one new reserved project in parallel, not an open-ended music corpus.

If P1 remains insufficient, keep it descriptive rather than fabricating diagnosis. A separately reviewed level/constraint-grounded agent experiment is a legitimate fallback, using already verified level measurements and explicit producer objectives, not hidden-reference restoration. This would need its own diagnosis/composition task; the document does not silently change the accepted P1 plan or activate a substitute.

This proposal changes no requirement status, policy record, diagnosis or coordinator. Pilot approval, results review, qualification implementation and evaluation execution are separate. No threshold adoption follows from accepting the high-level structure alone.


## Current-values development decision (2026-11-23)

The owner requested building the system with the current thresholds even though their mixing suitability is uncertain. The [read-only current-policy pilot](p1-current-policy-pilot-results.md) used exactly these values, without a search or relaxation. No initial short-control support reaches 100 cells; this is an honest insufficient-evidence path, not evidence of a clean mix or a reason to fabricate a positive candidate. Proceed with versioned experimental measurement/diagnosis and existing-loop composition; reserve scientific calibration and musical-performance claims. Legacy geometric/qualified metric meanings must not be silently relabelled as validated.


## Measurement increment implemented (2026-11-23)

The existing preparation/analyzer now support the explicitly configured experimental policy and frozen baseline cells. No parameter was tuned or relaxed. The qualification remains uncalibrated; experimental metric IDs and evidence are separate from legacy qualified IDs. Too little support and unusable current energy produce unavailable measurements, not a clean-mix verdict. The earlier numerical pilot remains a historical result with its original source fingerprints; it was not rerun or rewritten for this helper relocation. This increment used disposable numerical test WAVs only. Positive experimental diagnosis and existing-loop composition are the next separate increments; no musical effectiveness or live end-to-end result follows from these implementation tests.
