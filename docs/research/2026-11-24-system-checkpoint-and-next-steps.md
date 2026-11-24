# System checkpoint and next steps — 24 November 2026

## Purpose and verification scope

Owner requested a review of what exists now and a practical next-work plan. This is a repository/status and selected-test review, not a fresh line-by-line audit of every module. Starting HEAD is 32d49be (experimental deterministic diagnosis). R5A and the newer offline engineering/input-profile work are uncommitted and share some files; preserve both.

Fresh check in this review: all R5A test files plus float-ingress/offline-feedback tests passed with warnings as errors: **89 passed**. The latest research-log entry records an earlier isolated native-filesystem full run: **7,124 passed, four skipped**, with Ruff clean. That full count was not rerun in this checkpoint review. R5A's completion log separately records 15 faithful probes caught, 62 R5A tests and 7,097 full-suite passes before the offline additions. Distinguish those scopes/dates instead of treating test counts as musical validation.

## What exists

| Component | What is implemented | What the evidence does not establish |
| --- | --- | --- |
| Measurement front end | Reviewed dist10 kernels, full-clip history, framing/eligibility/silence, partition/band mapping and captured-reference checks | Perceptual masking, a universal numerical tolerance or musical representativeness |
| Input boundaries | Verified mono/exact dual-mono PCM16; explicit float64 ingress now maps verified DOUBLE to int16 with declared ties-to-even/saturation and count hashes | General stereo support, float/PCM equivalence or freedom from quantization sensitivity |
| Experimental evidence | Fixed baseline cells/policy, content-addressed manifests/grids and current measurements with honest unavailable outcomes | Scientifically qualified S0 or validated screening thresholds |
| Experimental diagnosis | Real deterministic source, exact scalar/source/settings bindings, robust-fraction/persistence rule and uncalibrated hypotheses | A gain instruction or proven audible masking |
| AI component | Allowlisted intent/evidence/action packet, strict structured parser, audited Claude transport/orchestration, failures and abstention | A real integrated model run or an autonomous decision making a better mix |
| Coordinator | Admission, budgets, feedback, verification, acceptance authority, recovery, manual-export suspension/continuation and durable restart contracts | Automatic DAW export/save, complete FL project rollback or arbitrary crash recovery |
| FL tool | Live research feasibility evidence for inspect, gain control, calibration and bounded dB commands; journaled runtime adapter/recovery tested on a simulated device | Calibration for arbitrary projects/inserts, or a live full agent/DAW loop |
| R5A provenance | Durable observation references; exact exchange gate before result use/retry; restart evidence checks; schema2 fail-closed compatibility | Multi-store atomicity or remote-model authenticity; production composition remains absent |
| Offline feedback engineering case | Real file renderer/analysis/integrity/verifier, labelled diagnosis/proposals, retained accepted-state recovery and repeatable records on synthetic signals | Real experimental diagnosis plus provider composition, research baselines or musical improvement |

The original manual trap pilot remains feasibility evidence with its recorded provenance/listening limitations. It is not a replacement for the planned reproducible model runs and controlled comparison.

## What remains missing

1. **Production composition (R5B):** the real experimental diagnosis, Claude adapter and evidence verifier have not been wired into one owned runtime context through the existing coordinator. The offline case still uses Diagnosis/Decider doubles even though its files/DSP/verifier are real.
2. **Operator run configuration and activation (R5C):** start/continue intentionally refuse. Provider-bound status conservatively reports provenance unavailable under the current operator configuration. Typed configuration, rehydration and resource ownership must be completed before activation.
3. **Actual development run:** no claim is available yet for a real provider choosing from actual diagnosis, acting through FL, then using verified feedback in a subsequent round.
4. **Project/profile compatibility:** live calibration remains restricted to the recorded build, title and Bass insert. Renaming another project to satisfy a title check is not a calibration extension. Arbitrary stereo and PCM24 remain outside the currently specified P1 ingress profiles. Configure eligible exports or separately review a profile change; do not convert silently.
5. **Research evidence:** current threshold values are engineering choices; numerical controls/C agreement do not validate listening effects. Fair agent/baseline comparison, meaningful costs and musical assessment remain future work.

## Main weaknesses and how to address them

- **Component verification exceeds integrated evidence.** Stop adding features and complete one bounded real-component vertical slice. Existing synthetic tests are useful; their labelled decisions must remain visible.
- **Scientific confidence is narrower than software confidence.** Preserve explicit uncalibrated wording. Do not tune the present values to force a diagnosis or infer perceptual improvement from an optimized margin.
- **Two workstreams now overlap in the dirty tree.** Review R5A against the new ingress/evidence schema4 before creating a checkpoint. Split commits only where each resulting state is independently coherent/green; do not blindly stage all changes or revert another contributor's files.
- **Status documents lag implementation.** The roadmap still describes production diagnosis as missing and qualification as the immediate prerequisite. Supersede those status statements: experimental diagnosis now exists, R5A is uncommitted, and the accepted current-values engineering continuation permits R5B without claiming scientific qualification. Historical dates/scope decisions remain historical.
- **Evaluation can become circular.** A lower model score is only a proxy outcome. Compare agent conditions using the same permitted evidence/actions/guards, and assess the producer goal/listening separately. Do not use hidden clean/manual references in runtime inputs.
- **Scope can grow indefinitely.** Gain-only remains the next vertical slice. No new EQ, dynamics, stereo tools, provider framework or detector family is required for it. P2/P3 scope is not silently withdrawn; revisit it after this slice works.

## Ordered plan

### Step 0 — Stabilize this checkpoint

Review R5A plus the float-ingress/offline-feedback extension together for compatibility, especially evidence schema4, quantization provenance, strict legacy profiles and PEAK metadata canonicalization. Verify that the full-suite evidence covers the exact intended commit state. Preserve original media and historical numerical study outputs. Commit coherent reviewed increments; no automatic commit is requested here.

**Exit:** a known green revision, clear scope/provenance notes and no ambiguous partly committed interface changes. The historical R5A independent review was interrupted; report that limitation rather than calling it a completed independent audit.

### Step 1 — R5B bounded programmatic composition (next implementation task)

Extend the existing driver composition responsibility; one owned runtime context is enough. Compose actual preparation, analyzer/router, ConfiguredDiagnosisSource, ClaudeDecisionMaker, decision evidence verifier and stores with existing coordinator/FL/export/integrity components. Keep the current thresholds. Supply explicit producer intent, permissions, criteria, budgets and profiles; diagnosis thresholds never become acceptance defaults.

First acceptance tests: a genuine incomplete or assessed-no-candidate result with no fabricated action; and a genuine positive numerical test diagnosis with a labelled provider transport returning a bounded proposal, followed by one fake-device write, suspension, temporary export import, real verification and acceptance or exact recovery. External transport/clock/ID doubles are allowed; diagnosis/admission/verification must be real. Include a rejected candidate -> rollback -> feedback-dependent second proposal, plus missing audit/evidence, initial verification stop, and restart without resending.

R5B does not require a live provider call, new music, CLI activation or another adapter framework. Test signals remain engineering fixtures. The offline engineering case remains regression coverage, not a second product rendering objective.

**Exit:** one callable production composition path, exact frozen settings/evidence across continuation, explicit lifetime/cleanup, and tests using real internal components with labelled external transports. daw start/continue still refuse until Step2.

### Step 2 — R5C operator configuration and commands

Finalize/review a small versioned configuration schema, then connect start/continue to Step1. Validate roots, rights/provenance declarations, producer context, compatible audio/export profile, calibrated DAW identity, provider settings/credentials, explicit criteria and budgets before effects. Reopen stored requests rather than rebuilding from new flags. Print saved manual export instructions; never automate saving/exporting/installing.

**Exit:** operator commands safely start, suspend and continue eligible runs, reject drift/missing evidence/in-use stores, and preserve status/recovery behavior. This is implemented availability, not live validation.

### Step 3 — Separately authorized real development trial

Use one eligible development project, a recorded concrete producer goal and fixed criteria. Confirm the actual project's supported profile and calibration first, using a separately authorized trial where necessary. Record exact model/request settings, raw requests/responses, source/method hashes, actions, exports, feedback, guards and costs. A genuine abstention or rejection is a legitimate result. Do not force a cut or label a lower proxy score better mixing.

**Exit:** one reproducible real model/tool/verification trace, ideally including verified feedback/revision, with all failures and operator work recorded. A single development case supports feasibility, not general effectiveness.

### Step 4 — Small, defensible agent evaluation

Freeze system/policy/configuration before reserved music is analyzed for the main comparison. Compare unchanged baseline, a separately predeclared deterministic rule, one-pass AI and iterative AI under transparent action/evidence/guard/budget conditions. The deterministic baseline needs its own prospective rule; current scripted integration proposals are not a research baseline. Report calls, attempts, exports, operator work, time and acceptance/recovery reliability alongside model-score and listening results.

HT01 and its variants remain development material unless a prospective split explicitly says otherwise. Collect one additional independent project in parallel and reserve it; repeated gains/exports of HT01 are not independent music. No six-project corpus or paid three-listener panel is an implementation prerequisite. Volunteers are useful if available; author-only listening is exploratory and must name its bias and level-matching limitations.

**Exit:** bounded claims about agent/tool/loop behavior and observed musical outcomes, with held-out limits and no general detector claim.

## Work that can run in parallel

Write the implementation/method/provenance/limitations chapters now from actual artifacts; keep comparative results and conclusions pending. Continue source/license records and one reserved project's creation. No threshold search, extra correction tools or large dataset is needed before Step1.

## Decision for the next handoff

After stabilizing the current checkpoint, specialize the already proposed R5B section into a tests-first implementation handoff. Keep that handoff to real-component programmatic composition; defer CLI syntax, live runs, calibration extension and musical evaluation to their separate gates. No implementation is performed by this checkpoint document.


## Step1 checkpoint review completed — 24 November 2026

After the initial selected-test review above, the owner authorized review and commit of the combined current changes. A fresh independent read-only review completed and identified two provenance gaps: schema4 reopening accepted missing/invalid transform/count-hash metadata, and provider continuation did not enforce its frozen plan on historical P1 references. Five regressions failed first; both gaps were fixed, with a corrected coherent positive fixture. Both fix-removal probes are caught and source bytes were restored exactly. No further blocking findings were identified in that review.

Final corrected/formatted snapshot: **7,129 tests passed, four skipped** in the full native-filesystem run. Lint, formatting, fixture verification, CLI loading, lock and whitespace checks pass. These supersede the earlier selected-test-only verification scope for this checkpoint. The two overlapping workstreams are retained in one coherent commit; no live provider/FL action or R5B/C implementation is included. The next implementation gate remains R5B.
