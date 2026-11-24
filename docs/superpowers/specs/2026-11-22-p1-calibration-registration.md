# P1 calibration registration

**Status:** Optional extended-study proposal; revision 3; 2026-11-22. The owner clarified that the thesis focuses on agents, tools, iteration and verification with a feasible scientific foundation. This six-project/three-listener/grid-search design is not adopted as the required development path. All numerical choices remain unapproved; no inputs generated, experiments run, parameters fitted or diagnosis implemented.

**Code baseline:** `0242c99dba54abcb59398833cf74b16c0b2fd0e6` (`feat(analysis): add descriptive P1 directional measurements and fixed support`). The registration and implementation handoff are separate from that commit.

**Governing method:** [accepted P1 specification](2026-11-20-p1-diagnosis-method.md), especially sections 4, 6 and 8. [Task boundary](../plans/2026-11-22-p1-calibration-registration-claude-handoff.md). Scientific controls: [methodology](../../research/methodology.md), [dataset plan](../../research/dataset-plan.md), SR-02/SR-03/SR-10 and ADR-010.

## Interpretation after owner clarification

This document preserves an optional extended calibration-study design for review; it does not impose its six-project corpus, three deciding listeners or 36/18 searches on the thesis. Its panel, annotation, search and execution-lock conditions apply only if that study is separately adopted. They are not universal prerequisites for the agent implementation or a later feasible evaluation design.

Numerical verification, truthful method limitations, relevant technical goals and safe bounded corrections remain necessary. Choosing not to run this optional study does not establish calibrated thresholds or permit qualified support/diagnosis to be fabricated. Any smaller operational-policy proposal must document its source or development justification, experimental status, validation and claim limits before activation.

Independent listening is supplementary unless a separately adopted research claim requires it. Volunteers can contribute to an exploratory task; their results must not be represented as expert ground truth or broad perceptual validation. No paid panel is assumed. The primary research/evaluation direction is recorded in the implementation plan's "Thesis focus and feasible evaluation" section.

## 1. What this registers, and what remains blocked

Four separate tracks:

| Track | Question | Permitted conclusion |
| --- | --- | --- |
| N: numerical reproduction | How far do Python T, E and M differ from the pinned independent dist10 reference on registered controls? | Empirical numerical agreement in the tested domain, not a universal error bound. |
| Q: qualification sensitivity | Which declared energy gates make small quantization perturbations sufficiently stable on those controls? | An experimentally screened domain and deadband, not an audibility threshold. |
| D: development policy selection | Which of 18 registered evidence-sufficiency configurations best agrees with independently collected development judgments? | A frozen, explicitly limited model-risk heuristic, or no acceptable policy. |
| E: later held-out evaluation | Does the frozen system preserve guards and help under independent assessment? | Reserved for a separately authorized study; no conclusion here. |

This proposal fixes rules and search choices for review. It does not approve their values as scientifically established. Registration approval, experiment authorization and policy approval are different gates (section 11).

**Prerequisites for empirical calibration, not general software blockers:** the owner is developing the FL Studio dataset as planned. The proposed musical calibration study still needs eligible corpus/split records, source provenance/rights evidence and any approved development-assessment panel. Numerical experiments need their reviewed runner and source verification. These gaps prevent declaring this study execution-ready; they do not prevent R3 packet/parser/audit work, R4 provider engineering with a transport stand-in, or R2 diagnosis mechanics that explicitly report missing calibrated policy. The existing pilot does not supply the missing calibration evidence by implication.

## 2. Source basis and limits

The [front-end contract](2026-11-11-mpeg-psychoacoustic-front-end-contract.md) owns the source trace and numerical details. It records Ronan's thesis section 7.2.4.2, equations 7.11-7.13: threshold from the accompanying source, threshold-to-target ratio, and a separate positive-margin aggregation using a 20 dB divisor. It does not establish our qualification gates, temporal rule, diagnosis cutoff or exact backend equivalence.

[Ronan's thesis record](https://qmro.qmul.ac.uk/xmlui/handle/123456789/55527) was not readable through the browser in this task. The existing trace is reused, not represented as a new independent full-text check. Before registration lock, retain the cited pages, bibliographic identity and source-file hash in the research evidence store, and confirm the transcription. Do not use the withdrawn related arXiv submission as validation.

[Hafezi and Reiss (2015), publisher abstract](https://aes.org/publications/elibrary/elibrary-page/?id=17637) distinguishes objective measurement from listening assessment and cautions about agreement of existing objective measures with perceived masking. This supports separate assessment, not a numerical threshold for our method. Checked 2026-11-22; no fresh full-paper reading is claimed.

All constants below not supplied by the numerical profile are **project proposals**. The pilot's 0.005552 dB residual and 10/20/40 dB descriptive noise groups are not calibration results for this protocol.

## 3. Corpus inventory: facts versus plans

Inventory in this task read documentation and fixture metadata only; it did not verify new audio hashes, inspect sample contents, audition music or access held-out outcomes.

| Item documented in repository | Known | Registration disposition |
| --- | --- | --- |
| Nine dist10 fixture inputs | Synthetic numerical signals, mono int16 at 44.1 kHz; recipes/hashes in `tests/fixtures/mpeg_psy_model2_dist10/inputs/inputs.json`; capture provenance in `provenance.json` | Numerical controls only. Never musical evaluation data. |
| Owner's trap kick/808 development mix | The manual trial report records 44.1 kHz PCM_16 stereo exports, 302,400 frames; a prior exploratory mono conversion and gain trials exist | Already inspected development material, never held-out. Native exports fail this mono profile. The reports do not establish source-sample rights for the proposed study. Do not promote derived mono files into this profile. |
| Six calibration projects | Dataset plan proposes two projects in each of three genre families | Target, not six verified projects. No corpus rows or hashes invented. |
| Nine held-out projects | Dataset plan proposes three projects per genre family | Target, not a frozen available split. No audio or outcome inspection authorized here. |

Proposed musical registration: six development projects (two house/techno, two trap/phonk/wave, two drum & bass), each supplying one producer-declared ordered pair of native aligned mono stems and an intended 30-second excerpt. All related versions remain in the same song-level partition. Genre counts are a pilot design choice, not a power calculation.

All self-created compositions still require documented provenance for third-party samples. Record ownership/license source, terms/version/date, intended-use confirmation and any restrictions as evidence facts. Do not infer permission from availability, purchase, or inclusion in a project. Missing or conflicting evidence blocks inclusion; no legal conclusion is fabricated by this document.

### Manifests to complete before registration lock

- **Public runtime manifest:** opaque project/track IDs, source roles and assertion provenance, producer intent, native profile, current source hashes, declared sample origin, selected pair and predeclared interval/bands. No defect label or injection information in IDs or names.
- **Research-only corpus manifest:** underlying song/creator, rights-evidence reference, all related versions, development/held-out assignment, split author/time and manifest hash. If controlled defects or manual references exist, their identities/parameters stay here.
- **Development annotation manifest:** item IDs, rater metadata/protocol, ratings and label resolution. Not a runtime input.
- **Numerical manifest:** recipe ID, input hash, generator/reference build identity, seed where used, expected control relationship, trace files and their hashes.

Do not infer a held-out split after seeing results. Existing pilot music remains development even if renamed. Unavailable corpus slots remain unfilled; replacing a project after viewing its scores requires a documented amendment, not silent substitution.

## 4. Fixed profile and observations

Native mono 44,100 Hz PCM_16; counts unchanged; no normalization, resampling, fold-down, bit-depth conversion or multi-masker sum. Both full clips run from call 0. Calls satisfy `k >= 2` and `576*k+256 <= N`; requested intervals retain complete windows only. Keep startup/short-path values as numerical diagnostics, not qualified observation support.

Every direction has `scope.track_ids == (masker, target)` and identically ordered sources. Regions use bands 1-20, all together for the first development-policy search. Band 0 remains descriptive because its reproduced mapping includes high-frequency bins. This excludes complete low-bass coverage; no claim that the region is the whole audible spectrum.

Compare T and E separately, and `M=10*(log10(T)-log10(E))` in dB. Q is the pinned qthr table mapped through the existing band kernel. Retain raw signed values, exact silence and quiet-floor flags even when qualification fails. No 20 dB clipping and no positive-only denominator.

The current R1 analyzer supplies only `p1.raw.*` observations on U; qualified metrics return Unavailable. No experiment may label that output as S0 or a diagnosis. Future runner/qualified-support implementation needs separate review before execution.

## 5. Numerical-control recipes (future execution only)

All generated material here is analytical test signal data, not music for performance evaluation. Generation and new reference instrumentation are future implementation, not commands executed in this task.

Common generated-control length `N=27,648` samples unless a row says otherwise. Convert real count values by round-half-to-even, then reject any out-of-int16 value rather than clip. Save little-endian int16 plus exact byte hashes. Use binary64 recipe evaluation; record library/platform versions and retain input bytes so reproduction does not depend on cross-platform transcendental bit identity.

| ID | Registered recipe/coverage | Expected relationship, not a diagnosis label |
| --- | --- | --- |
| N0 | All nine unchanged fixture inputs and their stored captures | Verify stored hashes and reproduce captured intermediate quantities; separate captured-input margin arithmetic from complete-chain error. |
| N1 | `A*sin(2*pi*k*n/1024)` as masker; target `r*A*sin(2*pi*j*n/1024+phi)`; `k in {23,46,205}`, `j in {k,k+1}`, `A in {16,256,2048}`, `r in {0.1,1,10}`, `phi in {0,pi/2}` | 108 pairs, both directions. Frequency, phase and level coverage; no claim that margin is invariant to common gain. |
| N2 | Exact silence; four bursts `x[n]=8192*sin(2*pi*23*(n-s)/1024)` for `s<=n<s+4608`, zero elsewhere, `s in {0,1983,1984,2000}`; two impulses `x[s]=32767`, zero elsewhere, `s in {1983,1984}`; three decays `x[n]=8192*exp(-(n-2000)/(tau*44100))*sin(2*pi*23*(n-2000)/1024)` for `2000<=n<N`, zero before, `tau in {0.02,0.1,0.5}` | Carrier phase resets to zero at each onset; all endpoints are sample-index bounds. Pair each nonsilent control with the full-clip phase-zero k=46 tone at amplitude 1024 and with silence, both directions; retain silence/silence too. |
| N3 | Two independent deterministic broadband streams: 32-bit LCG `s_next=(1664525*s+1013904223) mod 2**32`, starting seeds 20261122 and 20261123, `u=2*s_next/2**32-1`; amplitudes `{0.25,1,4,16,64,256,1024,8192}` independently on masker/target | 64 broadband pairs; both directions. The PRNG is a reproducibility recipe, not a psychoacoustic assumption. |
| N4 | Prefixes of N2 burst controls at `N in {1407,1408,2560,6536,6912}`; interval boundaries at exact sample times and adjacent non-sample times | Eligibility, full-window containment and unavailable results at boundaries; valid prefixes retain full processing history. |
| N5 | Near-zero margin controls for each band 1-20, described below | Test signs/deadband; never infer a global tolerance from a gain-step size. |

N5 is a bounded deterministic reference search, registered before execution: choose the smallest natural bin with maximal band-mapping weight for each band from the pinned tables (ties to lower bin). Fix a same-bin masker amplitude 8192 and phase 0; target phase 0. Use reference call 24. Test target amplitudes 1 and 30000; if these do not bracket M=0, retain the unbracketed case and report the band uncovered. Otherwise bisect integer target amplitude for at most 15 midpoint evaluations, with lower midpoint on ties. Stop at adjacent amplitudes or exact M=0. Retain every query, endpoint and sign; capture both adjacent endpoints and their four quantization variants. A failed bracket or a nonmonotone comparison is not repaired by searching another band/frequency. Missing required band coverage blocks numerical lock until a reviewed amendment.

### Quantization variants

For every N1/N3 real-valued waveform x, and all three N2 decays and all retained N5 endpoints, store four representations: `round(x)`, `round(x+0.5)`, `round(x-0.5)` and `round(x+0.5*(-1)**n)`. Apply the same variant index to both sources; retain all four and both directions. These are deliberately bounded quantization perturbations, not a claim about actual dither or independent uniform error. Each variant is native int16 input to the same reference and Python chains.

No music is generated. No production input conversion profile is created. All control generation, reference patch changes and runner code require their own reviewed implementation before the experiment is authorized.

### Independent reference

Use the pinned dist10 archive/table identities in fixture `provenance.json`, with its documented observation-only probes, compiler flags and image `gcc@sha256:d47e63a2a4621ff4da4e0825bfe8c52c2e65e957333d7878e5c3a6eee2cd525e`. Existing reproduction commands are in the fixture README. Extend only observation/capture plumbing to accept registered controls; record/review the patch and binary hashes before running. The Python implementation must not manufacture its own expected reference.

Capture T/E for every call, Q and available threshold-stage fields. For sign/error work, form an independent scalar reference margin from captured T/E; do not call the production margin kernel to produce expected values. Rerun independent captures twice and require identical input/output hashes. A mismatch invalidates the capture, not just the disagreeing cells.

## 6. Numerical and qualification selection rules

### 6.1 Quiet-elevation allowance

On all finite positive thresholds in registered controls/bands 1-20, compute `eT=max(abs(10*log10(T_python/T_reference)))`. Set the proposed allowance

`delta_Q = ceil_0.001(2*eT + 0.000001) dB`.

Here `ceil_0.001` rounds upward to the next 0.001 dB. The factor two and additive allowance are conservative project choices, not statistical confidence bounds. If `delta_Q > 0.1 dB`, numerical lock fails; do not raise the cap after seeing data. Quiet-floor qualification uses `10*log10(T_python/Q) > delta_Q`, not exact equality as a new calibrated predicate.

### 6.2 Quantization-reference energy and gate candidates

Map per-bin expected noise energy `(1/12)*sum(window**2)` through all natural bins 0-512 and the existing partition/band mapping. Include Nyquist. Denote the resulting positive vector R[b]. This is a declared diagnostic reference, not realized error or audibility.

Propose `gamma_target, gamma_masker in {10,20,30,40,50,60} dB`: 36 ordered gate pairs, lexicographic order. Nominal qualification uses original round(x) cells with both source windows nonzero, band 1-20, `10*log10(E_target/R)>gamma_target`, `10*log10(E_masker/R)>gamma_masker`, and T above Q by delta_Q. All comparisons are strict. These trial values extend, but are not justified by, the pilot's descriptive grouping.

For each pair, freeze its nominal qualifying control-cell list. Evaluate those same cells in every perturbation; do not drop a cell because another variant crosses a gate or changes a sign. A non-finite/undefined margin or silent target in any variant fails the pair. Exact-silent-masker controls are separate quiet-floor controls, not sufficient positive risk evidence.

Compute on these cells:

- `eM=max(abs(M_python-M_reference))` across all variants;
- `delta_numeric=ceil_0.001(2*eM+0.000001)`;
- `spread=max(max_variant(M_reference)-min_variant(M_reference))` per cell, then its maximum.

A gate pair passes only if delta_numeric <= 0.1 dB, spread <= 0.25 dB, and every band 1-20 has at least 100 nominal eligible cells from at least two distinct recipe IDs. Counts are coverage checks, not independent samples or a statistical sample-size justification. Report T/E residual distributions too; do not only report the maximum M error.

Select the passing pair with smallest `gamma_target+gamma_masker`; ties to smaller gamma_target, then gamma_masker. This is the least restrictive registered gate under the test constraints. If none passes, qualification is not established. Do not expand the grid or caps within this registration.

The robust margin allowance is `delta_M=delta_numeric+0.25 dB` for the selected pair: numerical allowance plus the registered perturbation allowance. This is an empirical, conservative operational deadband within tested conditions; it is not a perceptual threshold or a guarantee for every recording. Preserve numerical values and all rejected gate results.

### 6.3 Domain and confirmation

Freeze an operational domain with the selected gate pair: native profile, bands 1-20, exact window eligibility, and, for each band, the closed minima/maxima of `(10*log10(E_target/R), 10*log10(E_masker/R), 10*log10(T/Q))` across its nominal qualifying control cells. Require the box bounds to be finite. This axis-aligned envelope is an empirical screening definition, not proof that every waveform within the box has equivalent numerical behavior.

After the eligible musical corpus is fixed, perform the following confirmation before releasing its annotations to the N/Q operator:

1. Process the full registered source clips from call 0 through reference and Python chains; select the 48 intended item regions afterward. Do not crop away their prefixes. Freeze nominal cells satisfying the selected energy/quiet gates and the control-derived per-band box. Record every excluded cell/reason. The corpus metadata fixes source-length bounds; a future input longer than the largest confirmed source is outside this initial domain.
2. Recheck these exact same cells with all four quantization variants from section 5, using the decoded input counts as x. For each item require finite margins on every variant; `max(abs(M_python-M_reference)) <= delta_numeric/2`; threshold residual <= delta_Q/2; and maximum pairwise reference-margin spread <=0.25 dB. These are deliberately conservative confirmation tests. A variant leaving the nominal gate is retained, not dropped.
3. An item is numerically assessable only with at least 100 frozen nominal cells. Require at least six of eight assessable items per project and at least 100 confirmed cells in every band 1-20 pooled across all six projects. These are coverage gates, not independent-sample or perceptual sample-size claims. Report per-project and per-band counts, including exclusions.
4. Any cap violation on a tested frozen cell, or failure of the required coverage, stops progression. Do not switch to the second-best gate pair, widen the box, choose other excerpts or alter thresholds within this registration. A reviewed amendment and new confirmation allocation are required.

If confirmation passes, the frozen domain is the control-derived box plus the registered profile/geometry and confirmed source-length limit, with explicit restrictions to this development evidence. Musical confirmation does not expand its energy bounds. Qualification outside this domain is unavailable. Record all T/E residuals as well as M; no claim of general waveform validity or perceptual calibration follows.

Values 0.1/0.25 dB, 100 cells and the grid above are engineering-screening proposals requiring approval. No empirical basis or measured passing rate is claimed here.

## 7. Optional extended development assessment and policy search (not adopted)

### 7.1 Items and independent judgments (proposal)

Once six eligible projects exist, each contributes a producer-selected 30-second excerpt, declared pair and producer-intent statement before analysis. The excerpt starts at an integral sample position. Subdivide it into four fixed nonoverlapping 7.5-second intervals. Evaluate both directions over bands 1-20: eight items/project, 48 intended items. No automatic hotspot/region search.

Use exactly three preregistered deciding listeners with self-reported mixing/listening experience, excluding the project creator. Any additional listeners are exploratory and do not change the deciding labels. Missing deciding ratings remain unresolved; replacing a deciding listener after outcomes are seen requires amendment. Record roles and experience; do not call them experts without supporting qualifications. Provide a randomized order (seed 20261124), opaque IDs, the producer's role/intent statement and the target solo plus the two-source mixture. Hide model outputs, parameter choices, baseline/candidate names and any clean/manual references.

Listening stimuli are derived from the actual musical stems, not synthetic evaluation music. Present the two-source linear mixture at its original relative levels using one recorded common attenuation per project to prevent clipping, applied equally to solos and mixture. Do not use the FL master as though it necessarily equals that sum. This is a two-source development task, not a claim to represent a processed full multitrack mix. Do not level-match the target independently to alter its relation to the masker.

Question: "Relative to the supplied intent, is the target difficult to distinguish in this mixture because of the other source?" Use ratings 1 definitely no, 2 probably no, 3 uncertain, 4 probably yes, 5 definitely yes, plus cannot assess. Among the three deciding ratings, define positive as at least two ratings 4/5, negative as at least two ratings 1/2, otherwise unresolved. An item missing any deciding rating is unresolved. With three votes these categories cannot both apply. Report disagreement and all ratings, not just the label. The full-band listening task and excluded band-0 coverage may disagree; retain those cases and report that limitation, never remove them based on detector failure.

A project requires at least six resolved items with at least two positive and two negative labels; otherwise the proposed balanced-accuracy selection cannot run. This is a preregistered feasibility gate, not an assurance the corpus will satisfy it. Do not change the excerpts after labels are seen to create balance; amend the design transparently if it fails.

### 7.2 Eighteen candidate policies

After numerical/qualification lock, propose:

- `n_min in {100,500}` cells;
- `p_min in {0.10,0.25,0.50}`;
- `duration_min_seconds in {2,4,8} * 576/44100` (approximately 26,52,104 ms; exact hop fractions are authoritative).

Total 2*3*3=18 configurations, ordered ascending n_min, then p_min, then duration. These are explicit project hypotheses, not published auditory constants. Apply the accepted rule unchanged: sufficient bound evidence; support count >= n_min; fraction with M>delta_M >= p_min; longest same-band consecutive robust run >= the duration. Gaps break runs. No smoothing or gap bridging.

The selected gamma/deadband is fixed for all 18; it is not re-tuned by annotations. Each policy prepares S0 on each initial excerpt under that frozen qualification and retains it for later correction comparisons.

### 7.3 Score, tie rule and fixed stop

Score each configuration per project using availability-penalized balanced accuracy: `TPR_score=TP/N_positive`, `TNR_score=TN/N_negative`, score `(TPR_score+TNR_score)/2`. Class denominators contain every resolved human label, including items with an unavailable model prediction. The final objective is the unweighted mean of the six project scores. An unavailable prediction earns no correct credit; it is not an actual positive diagnosis. Report ordinary assessed confusion counts and abstentions separately, alongside these penalized rates.

A policy is admissible for selection only if every project has available predictions on >=80% of its resolved items and `FPR_penalty=1-TNR_score <=0.25`, and the macro balanced accuracy is >=0.60. These are proposed feasibility thresholds, not significance tests. All-positive/all-negative behavior and unassessed-everything cannot win under these requirements. If no configuration qualifies, no production policy is selected.

Choose the admissible policy with highest macro balanced accuracy; exact ties select larger n_min, then larger p_min, then longer duration (more conservative evidence). Finish after all 18, even if an early result looks favorable. No new trial, alternative region, annotator subset or expanded grid is allowed without a new protocol revision. No fit or adaptation occurs on a held-out song; these six projects are development only. This is not an unbiased performance estimate after selecting the best of 18.

The independent assessment objective prevents circularly choosing a detector solely for reducing its own proxy. Nevertheless, a small development panel cannot establish a generally valid perceptual detector. Approval, if any, is for a limited model-risk heuristic with explicit disagreement and missing coverage.

## 8. Qualified support, identity and correction comparisons

Lifecycle after separate implementation approval: verified initial inputs -> geometric U -> qualification under the selected, versioned policy -> persisted exact S0 cell list -> canonical manifest/digest -> bound analysis specification -> final RunRequest. Never mutate the request after its fixed configuration is constructed.

Changing policy requires new prepared support and identity. R1's bound specification and manifest checks remain; geometric support must never be written under qualified metric IDs. The subsequent S0/preparation/qualified-observation changes are a new reviewed implementation slice, not assumed to exist.

Keep all S0 cells through before/candidate comparisons. Target silence or an out-of-domain low-energy/non-finite result makes the qualified comparison unavailable, not improved. An exactly silent masker is a separately verified quiet-floor control; a valid reduction to it retains finite margins and flags, rather than dropping cells. Nonzero low-energy masker uncertainty is not silently treated as exact silence. No denominator shrinkage, replacing missing values by zero or revised support after seeing a candidate.

Record the longest robust run from the measurement adapter as a scalar, bound to the same support/deadband/specification; scalar core diagnosis must not reconstruct temporal persistence from a median. Empty/unsupported evidence yields incomplete/unavailable coverage, never a fabricated problem or success.

## 9. Hidden-reference and role boundaries

Runtime permitted roots contain only the supplied current stems and explicitly permitted derived measurement evidence. Calibration annotations, split metadata, controlled-defect parameters and clean/manual references live in a separate research store with no runtime root binding. IDs and producer descriptions must not encode labels or injection settings.

Development annotations may select a frozen policy offline. Only policy values, their version and method/claim provenance may enter runtime; labels and reference-derived targets may not. Known analytical controls used to verify arithmetic are not concealed musical reference material and must be labelled as numerical controls.

Future tests must put sentinel strings/paths in hidden manifests, reference names and annotations, then inspect actual diagnosis/provider packets and root-resolution attempts. Passing requires no hidden data or path resolution. A prompt instruction is not isolation. Provider access is unnecessary for calibration and forbidden in this task.

## 10. Failure, reproduction and costs

Register an immutable experiment manifest and all approvals before execution; retain inputs/recipe hashes, reference patch/compiler/image hashes, lockfile/code hashes, commands, environment, UTC times, raw grids, Q/flags/support, residuals, every gate/policy score and failure reason. Link source/annotation evidence without exporting hidden data into runtime records.

Reproduction means replaying the numerical pipeline and scoring stored public study data under its permitted research access; no provider or FL Studio is needed. Musical judgments themselves are observations, not guaranteed repeatable outputs. Store exact public annotation forms/order and results.

One mechanical rerun of a failed capture is permitted after the cause is recorded and without changing inputs or parameters; retain the first failure. A semantic change to code, reference, waveform, support, annotations, search, selection rule or split requires amendment and review. Invalid/incomplete runs produce no selected policy. Never retain only the best run.

Record preparation, reference capture, analysis and scoring costs separately. There is no existing counted analysis-operation budget. Before execution approve resource limits and storage locations explicitly, with enough space for the bounded manifests/evidence; R1 byte limits have not been sized for full songs. Use local protected research storage, preserve original media and report all unavailable evidence.

## 11. Gates and owner/reviewer decisions

| Gate | Required evidence | Current state |
| --- | --- | --- |
| G0 protocol review | Approve or revise the proposed recipes, 36 gate pairs, numerical caps, 18-policy search and independent assessment procedure | Proposed |
| G1 registration lock | Source-page check; eligible input/rights/split manifests; panel and stimuli protocol; implemented/reviewed runner; exact resource settings; all existing input/manifest/code hashes fixed and new generator recipes fixed | Blocked by missing evidence, no invented rows |
| G2 execution authorization | Explicit authorization for the locked numerical/development experiments | Not requested by this documentation task |
| G3 numerical/qualification lock | Complete controls and development confirmation, full trial log, caps/coverage passed, domain/values reviewed | Not run |
| G4 policy freeze | All 18 scored, independent scientific review, selected parameters or explicit no-policy result, fixed claim wording | Not run |
| G5 held-out evaluation | Separate frozen system/design and explicit authorization; no changes selected from held-out outcomes | Not authorized |

New deterministic control bytes cannot have a measured hash before they are generated. Lock their generator, seeds and recipe IDs at G1; after separate authorization, materialize them at the start of G2 and record exact hashes before processing either chain. Reject a repeated materialization whose bytes differ; do not change the recipe to match a favorable result. Existing musical inputs and stored fixtures have their hashes verified at G1.

The sequencing of G1/G3 needs a two-stage lock: numerical recipes, gate search, corpus and annotation procedure are fixed at G1; successful N/Q results at G3 fill only the preregistered derived quantities. They do not permit changing the D search or annotation rules. Keep annotations sealed from the N/Q operator until its confirmation and values are locked.

Decisions proposed: retain the native-mono first profile; use 36 qualification pairs with explicit empirical caps; use the 18-rule search and conservative tie rule; recruit three independent listeners for a six-song two-source development pilot; accept a no-policy outcome; keep all held-out work separate. Corpus identities, rights evidence, panel availability and resources still require factual completion before registration can lock.

## 12. Checks performed for this draft

Read the accepted method/handoff, dataset plan, methodology, R1 catalogue, fixture metadata/provenance and existing pilot reports. Checked the publisher's primary abstract; the thesis record remained inaccessible. No audio was loaded or auditioned, no new source hashes were verified, no control generated and no experiment/test/provider/DAW run performed. This is a proposed protocol, not calibrated evidence.

## 13. Draft review record

A read-only builder check identified unspecified waveform details, a non-operational musical-domain confirmation gate and ambiguous voting with larger panels. This revision fixes samplewise control recipes, nominal-cell confirmation/caps/coverage and exactly three deciding listeners. A self-review also separated penalized selection rates from actual diagnoses and distinguished preregistered generator recipes from input hashes measured on later materialization. No results were computed.

## 14. Revision 2 — dependency clarification

The owner clarified that dataset creation in FL Studio is ongoing as planned. Calibration and evaluation readiness depend on that material; software construction does not universally depend on a finished corpus. The implementation plan now schedules provider packet/audit and provider-adapter engineering independently, with labelled tests and no live calls. Production qualified support and actionable diagnosis remain gated on actual calibration. This revision does not adopt the proposed sample size, listener effort, search values or experiment execution.

## 15. Revision 3 — thesis focus and workload

The owner confirmed that trustworthy mixing decisions are required, while the central thesis topics are the agent, tools, loop and verification. The assistant's fixed six-project/three-listener/search-grid proposal is therefore retained only as an optional extended study, not a required implementation or thesis milestone. No paid professionals are assumed. This scope clarification does not approve another parameter set or relax numerical/evidence correctness.
