# Requirements Specification — v1.1

**Approved by the researcher on 2026-10-15** (consolidation recorded in `../research/research-log.md`). Supersedes the unapproved v1 draft of 2026-10-14; the ID mapping is in [Change history](#change-history). Amended to v1.1 on 2026-10-15 at the owner's instruction (hidden-reference boundary, [ADR-010](../decisions/0010-hidden-reference-boundary.md)).

Requirements state *what* the system must do, not *how*. Architectural choices are recorded separately as ADRs in [`../decisions/`](../decisions/README.md); none are implied by this document. Status labels follow [`documentation-policy.md`](documentation-policy.md). Priorities: **must**, needed to answer the research question; **should**, strongly supports validity or maintainability; **target**, pursued as the schedule permits (see the scope tiers in [`project-scope.md`](../research/project-scope.md)).

## 1. Objectives

Research questions (RQ1–RQ4) and hypotheses (H1–H3) are defined in [`project-scope.md`](../research/project-scope.md) and are not repeated here.

- **O1:** Answer the main research question and RQ1–RQ4 through a working prototype and a controlled evaluation.
- **O2:** Test H1–H3 against baselines, reporting negative, partial and failed outcomes as results.
- **O3:** Make only claims that are supported by measurements and evaluation evidence.

## 2. System context and scope

- **Inputs:** a *project* of aligned WAV stems (any number of tracks, each with an identifier and a declared role); project metadata (data split, variant); producer intent; analysis and experiment configuration. Hidden evaluation data (original reference audio and defect-injection ground truth) is stored separately and is not a runtime input (SR-10).
- **Outputs:** measurements; problem candidates; proposed, validated and executed corrections; corrected individual stems and the combined mix (rendered without modifying source audio); per-run records; aggregated evaluation results, which may join run records with hidden evaluation data after execution (SR-10).
- **External service:** the Claude API, used only by the AI agent to propose decisions (ADR-001, ADR-004).
- **Optional DAW:** FL Studio, operator-attended, through its MIDI scripting API for calibrated fader writes and reads (FR-19, FR-20; D15 decided 2026-11-18, ADR-034 Proposed).
- **Operator:** the researcher, via the CLI. In the optional DAW path the operator also confirms the project copy and routing, and saves and exports in FL Studio.
- **Problem families:** P1 masking, P2 resonance/buildup, P3 stereo/phase are research targets; the target and contingency scope is defined in `project-scope.md`.
- **Excluded:** model training; real-time or plugin operation; GUI; FL Studio `.flp` manipulation; unattended DAW operation and automated DAW saving or exporting; comprehensive automatic mixing or mastering; guaranteed perceptual superiority; multiple agents.

## 3. Functional requirements

| ID | Requirement | Prio | Verification | Trace | M0 status |
| --- | --- | --- | --- | --- | --- |
| FR-01 | Load a project of one or more mono or stereo WAV stems, each with a unique identifier and a declared role. | must | Tests load projects of 1, 2 and more than 2 tracks; duplicate or missing identifiers are rejected. | RQ1, multitrack constraint (ADR-006) | Partial: loads exactly two stems without identifiers or roles. Loading of standard WAV mono/stereo is tested (`tests/test_audio_io.py`); WAVEX/RF64 are accepted by the code but untested. |
| FR-02 | Verify that all tracks of a project are compatible for joint processing: readable WAV, supported channel count, finite samples, common sample rate and a common timeline (alignment). Incompatible input is never processed silently; it is rejected, or handled by an explicitly configured and recorded policy. The resampling and alignment policy is open decision D3; until D3 is decided, mismatches are rejected. | must | One test per compatibility rule; no output is written on rejection; the applied policy is recorded in the output. | RQ1, QR-05 | Partial: rejects unreadable/non-WAV files, < 2048 frames, > 2 channels, non-finite samples, and differing sample rate or frame count for a pair (tests exist). Alignment is assumed, not checked; the policy is not recorded. |
| FR-03 | Compute descriptive measurements per track using parameters from an explicit configuration that is recorded with the output. | must | Numeric tests against signals with analytically known values; the recorded configuration equals the configuration used. | RQ1 | Partial: RMS, peak and one band-power proxy; parameters hardcoded and only partly recorded; no numeric tests for non-trivial signals. |
| FR-04 | Compute interaction measurements for any declared group of tracks, independent of specific role names. | must | Results do not depend on the role labels used. | RQ1, multitrack constraint | Deviation: one temporal coactivity proxy for a fixed pair labeled kick/bass (ADR-006 known deviation). |
| FR-05 | Represent problem candidates with their type, affected tracks, supporting measurements, the detection method and version, and an explicit statement of evidence strength and uncertainty. The representation must not presuppose a calibrated numerical confidence: it must be able to express qualitative or uncalibrated uncertainty and insufficient evidence. Its form is open decision D11. | must | The uncertainty representation is documented before use; schema conformance tests; candidates with uncalibrated uncertainty are labeled as such. | RQ1 | Planned |
| FR-06 | The AI agent receives structured measurements, problem candidates, producer intent and iteration history, and returns structured correction proposals or an explicit no-action / insufficient-evidence outcome with a reason. | must | Malformed responses are detected and recorded; each outcome type is covered by tests that use a stand-in for the AI service. | RQ2 | Planned |
| FR-07 | Reject proposals that use non-permitted action types, out-of-bounds parameters, violate hard producer-intent constraints or conflict with each other, and record the rejection reason. | must | One test per rejection rule; rejected proposals are never executed. | RQ4, H3 | Planned |
| FR-08 | Provide a documented, finite set of parameterized correction actions for the problem families in scope. Every action has declared parameter bounds and is deterministic, non-destructive and reversible. Action types and bounds are architectural and methodological decisions (D5). | must | Each action has a documented definition and bounds; determinism and reversal tests per action; out-of-bounds parameters are rejected. | RQ2, QR-01 | Planned |
| FR-09 | After an executed correction, re-analyze the result and compare it with the last accepted state for the targeted problem and for side effects and regressions, at the level of individual stems and the combined mix. | must | Test scenarios for improvement, no change and regression. | RQ4, H2 | Planned |
| FR-10 | Accept, revert or stop according to criteria declared before the run. Each termination has a recorded, enumerated reason. Reverting restores the previously accepted result exactly. | must | Every stop reason is reachable in a test; output after reverting is identical to the previously accepted output. | RQ4 | Planned |
| FR-11 | Represent producer intent as hard constraints (always enforced) and preferences (provided to the decision process and evaluated). | must | A hard-constraint violation is never executed; preferences appear in the run record. Constraint types: D6. | Main RQ, RQ2, H3 | Planned |
| FR-12 | Persist a complete per-run record: runtime-visible inputs (no hidden evaluation data, SR-10), configuration, and for every iteration the measurements, candidates, proposals, validation results, executed corrections, verification outcomes and stop reason. | must | Field-completeness test; the record suffices to reproduce the final output without calling the AI service. | RQ4, QR-03 | Partial: M0 JSON report with input SHA-256 and STFT/band parameters; no run or iteration structure. |
| FR-13 | Support each problem family in scope with a documented problem definition, a measurement/detection method with a cited source, a defect-injection protocol and correction actions, at the depth defined in `project-scope.md`. Methods and complexity are determined per family after literature review and are expected to differ. | must for P1; target for P2, P3 | Per family: method source documented; FR-05 and FR-08 criteria met; achieved depth reported. | RQ1, RQ2 | Planned (no family has an approved method) |
| FR-15 | Execute the same project under each declared comparison condition (SR-01), selected by run configuration without code changes. | must | Tests select each condition by configuration only. | RQ3, H1, H2 | Planned |
| FR-16 | Execute all conditions over a data split, including repeated trials for non-deterministic conditions, and aggregate the results. | must | One invocation produces all run records and an aggregate; split membership is enforced. | SR-02, SR-05 | Planned |
| FR-17 | Render corrected individual stems and the combined mix for assessment and listening without modifying source audio. | must | Rendering is deterministic; source hashes are unchanged. | SR-04, QR-01 | Planned |
| FR-18 | Read project metadata and propagate the runtime-visible part (split, variant identifier, producer intent) into run records. Defect-injection parameters and other hidden evaluation data are kept out of runtime inputs and run records; they are linked to run records only by project and variant identifiers for post-run research evaluation (SR-10). | should | Runtime-visible metadata appears in every run record; hidden evaluation data fields are absent from run records. | SR-02, SR-07, SR-10 | Planned |

| FR-19 | Optionally apply an admitted absolute gain action to an owner-confirmed, calibrated FL Studio mixer insert through the parameter-control adapter, with a stable command ID. Report pre-write refusal, confirmed, off-target and uncertain outcomes separately. Recover failed or uncertain writes only by authorized exact-target fader restoration and explicit resolution, preserving unresolved commands and conversion blocks. Never treat a parameter change as candidate audio, and never claim a fader restoration as complete project rollback. A resolution may clear uncertainty but never establishes verified rollback, which requires retained-audio integrity and recorded, exact equality of both fader readings to the saved target. | should | Tests against the real adapter on a simulated host cover each outcome, stable-ID replay, recovery, preserved blocks and the separate rollback claims, including a resolution on evidence within tolerance that must not yield verified rollback; live trials are separately authorized and recorded. | D15, ADR-034, RQ4 | Partial (2026-11-20; supersedes the unintegrated/no-coordinator status): calibrated writes, authorized recovery and exact rollback are integrated in the coordinator, with durable run journals (ADR-035 to 039; tests `tests/unit/test_daw_coordinator.py`, `tests/unit/test_daw_recovery.py`, `tests/unit/test_run_restart*.py`). Operator status and two-phase recovery tooling are implemented but Increment 6 review is pending (ADR-040; `tests/unit/test_daw_cli.py`). Autonomous runs remain unavailable: production `DecisionMaker` and `DiagnosisSource` are missing, so CLI start/continue refuse up front. No live end-to-end validation. |
| FR-20 | After a confirmed DAW change, pause the run awaiting the operator's manual export, and resume only through an explicit continuation. The continuation verifies the exported files (hash, encoding, declared dimensions, exact coverage of every affected stem and the master) and observed DAW-state continuity immediately before and after that verification. Only verified exports form a candidate for analysis, verification and acceptance. Saving and exporting stay manual. | should | Tests: a pending export is never candidate-ready; a duplicate continuation replays its recorded result; a restart during the pause resumes without reapplying the write; a rejected import leads to recovery. | D15, ADR-034, RQ4, QR-01 | Partial (2026-11-20; supersedes the no-suspension/continuation/durable-journal status): the coordinator supports manual-export suspension, verified continuation, analysis/acceptance and process-restart handling (ADR-033, ADR-035 to 039; tests `tests/unit/test_daw_continuation.py`, `tests/unit/test_daw_export_handoff.py`, `tests/unit/test_run_restart*.py`). Native channels are preserved; level analysis is supported and mono masking remains outside this path. CLI start/continue remain unavailable pending production decision and diagnosis adapters; Increment 6 operator tooling review is pending. Saving/exporting stay manual; no live end-to-end validation. |

FR-14 (direct FL Studio `.flp` integration) is withdrawn as a requirement and listed under exclusions; the ID is not reused. FR-19 and FR-20 (2026-11-18) do not revive it: they cover scripting-API fader control and manual exports, not `.flp` manipulation.

## 4. Non-functional requirements

| ID | Requirement | Prio | Verification | Trace | M0 status |
| --- | --- | --- | --- | --- | --- |
| QR-01 | Source audio is never modified; all corrections are non-destructive. | must | Source hashes identical before and after every run. | Scope boundary | M0 writes no audio; not yet tested. |
| QR-02 | Analysis, detection, correction, validation and verification are deterministic and testable without network access or credentials. | must | Test suite passes offline without an API key; repeated runs give identical output. | ADR-001, ADR-004 | Partial: M0 analysis repeat-run determinism is tested within one process. |
| QR-03 | Records contain input hashes, configuration, code version and dependency versions, and for AI conditions the model identifier, request parameters, prompt version and raw requests/responses. | must | Field-presence test. | SR-05 | Partial: input hashes and part of the configuration only. |
| QR-04 | Iterations, actions, AI requests or cost, and runtime are bounded by configurable limits; limits are recorded and reaching one is a recorded stop reason. | must | One test per limit. | RQ4 | Planned |
| QR-05 | Defined, tested behavior for corrupt, unsupported and misaligned input. | must | Covered by FR-02 tests. | ADR-001 | Partial: corrupt and unsupported input tested; misalignment not detected. |
| QR-07 | Analysis, detection, correction, validation and verification do not depend on the AI provider (Claude is the initial provider). | should | These parts are testable without the provider SDK installed. | ADR-001 | Holds for M0 (no provider code). |
| QR-08 | Records and reports carry a schema version and can be read back. | should | Round-trip tests. | SR-02 | Partial: M0 report has `schema_version`; no read-back. |

QR-06 is moved to SR-08; the ID is not reused.

## 5. Scientific and evaluation requirements

| ID | Requirement | Prio | Verification | Trace |
| --- | --- | --- | --- | --- |
| SR-01 | Evaluation compares at least: B0 unprocessed, B1 deterministic/rule-based correction, B2 agent-guided single-pass, B3 agent-guided iterative. Each pairwise comparison used for a claim names its independent variable(s) and the factors held constant (inputs, measurements, action capabilities, validation). Remaining known confounders (e.g. information available to the decision process, number of decision opportunities, access to verification feedback, AI request volume) are documented and either controlled or reported as limitations. Equal action budgets alone do not establish comparability. Condition definitions: D7. | must | `methodology.md` lists, per comparison, the independent variable, controlled factors and known confounders before evaluation; condition configurations differ only in declared fields. | RQ3, H1, H2 |
| SR-02 | Projects are split into calibration and evaluation sets; all variants of one project stay in the same split. Thresholds and parameters are selected on calibration data only and frozen before evaluation. | must | Evaluation records reference a frozen configuration identifier recorded before the first evaluation run. | ADR-001, ADR-006 |
| SR-03 | **Non-circularity:** the measures used to report success are defined before evaluation; at least one assessment of improvement is independent of the objectives the decision process or verification optimizes; any overlap is declared. | must | `methodology.md` lists the reported measures and marks which are visible to the decision process or verification. | H1 |
| SR-04 | Technical improvement is assessed relative to B0, for individual stems and the combined mix, including side effects on non-target tracks, with loudness differences controlled (method: D9). | must | The same assessment procedure is applied to all conditions. | H1, H3 |
| SR-05 | **LLM variability:** non-deterministic conditions are repeated a fixed number of trials (D8) and the spread of outcomes is reported alongside central values. | must | Trial count recorded; results include dispersion. | RQ2, RQ3 |
| SR-06 | **Failures and rollback:** rejected proposals, reverted corrections, AI failures, limit stops and unresolved candidates are reported per condition; no run is excluded without a documented reason. | must | The aggregate contains these counts; exclusions are listed with reasons. | RQ4, H3 |
| SR-07 | Problem labels come from documented sources (defect-injection parameters per ADR-008, and any independent annotation). Perceptual claims require independent listening evidence; otherwise they are stated as limitations. | must | Label source recorded per project/variant. | RQ1 |
| SR-08 | Reports never present measurement overlap, spectral peaks or phase correlation as proof of audible masking, unwanted resonance or a stereo problem. | must | Report wording review; reports carry an explicit status field. | ADR-006 |
| SR-09 | Adherence to producer intent is measured: proposals rejected for hard-constraint violations and deviations from preferences. | must | Counts per condition. | H3 |
| SR-10 | **Hidden-reference boundary:** hidden evaluation data, i.e. original reference audio (clean/reference condition, any manual correction reference) and defect-injection ground truth (defect type, target tracks, parameters, seeds and labels derived from them), is unavailable to every condition's controller, to the controllers' decision inputs, and to runtime validation and verification. It is stored separately and used only by post-run research evaluation; a combined research report may join it with run records after execution. The ordinary action history of a run (FR-06, FR-12) is runtime information and is not hidden. Identifiers linking run records to hidden evaluation data must not encode injection details. | must | Tests show that decision inputs, runtime validation inputs and run records contain no hidden-data fields and that runtime configuration does not reference the hidden data; research reports are built only from completed run records. | H1, SR-01, SR-03, ADR-010 |

## 6. Operational and safety requirements

| ID | Requirement | Prio | Verification | Trace |
| --- | --- | --- | --- | --- |
| OP-01 | The AI agent cannot access files, execute processing or bypass validation; only validated corrections are executed. | must | Execution accepts only validated corrections (test); the agent's interface exposes proposals only. | ADR-001, ADR-004 |
| OP-02 | A run never overwrites the outputs of an earlier run. | must | Two consecutive runs leave two complete records. | QR-03 |
| OP-03 | Credentials are read only from the environment and never appear in records, logs or version control. | must | Test that records do not contain the key; `.env` is gitignored. | Security |
| OP-04 | Failures of the external AI service are retried a bounded number of times and then become a recorded outcome, not a crash. | must | Test with a stand-in service. | RQ4 |
| OP-05 | Copyrighted stems and credentials are never committed. | must | Repository review; ignore rules. | CLAUDE.md |

## 7. Traceability

| Objective | Requirements |
| --- | --- |
| RQ1 | FR-01–FR-05, FR-13, SR-07 |
| RQ2 | FR-06, FR-08, FR-11, FR-13, SR-05 |
| RQ3 / H2 | FR-09, FR-15, FR-16, SR-01, SR-05 |
| RQ4 / H3 | FR-07, FR-09–FR-12, FR-19, FR-20, QR-04, SR-06, SR-09, OP-01, OP-04 |
| H1 | SR-01–SR-04, FR-17 |
| Main RQ (producer intent) | FR-11, SR-09 |
| Essential constraints | QR-01–QR-03, QR-07, SR-02, SR-08, SR-10, OP-02, OP-03, OP-05 |

QR-07 is the only requirement motivated mainly by future flexibility; it is kept at *should* because it also enables offline testing.

## 8. M0 exit criteria

1. The CLI reads a valid project and saves structured, versioned output including the configuration used.
2. Incompatible input is rejected with understandable messages.
3. Unit tests pass, including numeric tests of the reported measurements.
4. All values are labeled as descriptive measurements, not validated defect detection.

Status 2026-10-15: not met under the v1.0 wording. Criterion 1 requires project (multitrack) input and full configuration recording; criterion 3 requires numeric tests that do not yet exist. Meeting them requires an approved implementation task.

## Change history

- **v1.2 (2026-11-18):** owner decision D15, ADR-034 (Proposed). New FR-19 (optional calibrated FL Studio fader control with separate outcomes and exact-target recovery) and FR-20 (manual export pause and verified continuation), both Planned. The system context gains the optional DAW and the operator's DAW steps. The exclusions gain unattended DAW operation and automated DAW saving or exporting. `.flp` manipulation stays excluded and FR-14 withdrawn. No other requirement changed.
- **v1.1 (2026-10-15):** owner instruction, ADR-010. New SR-10 (hidden-reference boundary). FR-18 no longer propagates defect-injection parameters into run records; FR-12 records runtime-visible inputs only; system-context inputs and outputs reworded; SR-10 added to the traceability table. No other requirement changed.
- **v1.0 (2026-10-15):** consolidated and approved. Rewritten to be technology-neutral: FR-01, FR-02, FR-04, FR-05, FR-08, FR-13 (multitrack input, open resampling/alignment policy, uncertainty without assumed calibration, action types deferred, per-family depth). Refined: FR-06 to FR-12. Provenance content of the old FR-12 moved to QR-03. FR-14 withdrawn (exclusion); QR-06 → SR-08. New: FR-15 to FR-18, QR-07, QR-08, SR-01 to SR-09, OP-01 to OP-05. Removed the unsupported "tested" claim for WAVEX/RF64 and replaced status notes with evidence-based M0 status.
- **v1 (2026-10-14):** initial unapproved draft.

## Open decisions and questions

| ID | Decision or question | Affects |
| --- | --- | --- |
| D2 | Depth required for P2 and P3 beyond the contingency minimum | FR-13, roadmap |
| D3 | Sample-rate policy (reject or resample) and alignment tolerance; decide before datasets are built | FR-02 |
| D4 | What the stems represent (e.g. pre- or post-fader exports) and how the combined mix is formed | FR-09, FR-17, SR-04 |
| D5 | Correction action types per problem family and the source of their parameter bounds | FR-08, FR-13 |
| D6 | Constraint and preference types in a producer-intent specification | FR-11, SR-09 |
| D7 | Definition of each comparison condition, including the B1 rule design, whether B2 receives verification, and any additional or ablation conditions | SR-01 |
| D8 | Number of repeated AI trials and sampling settings | SR-05 |
| D9 | Reported evaluation measures and loudness-control method | SR-03, SR-04 |
| D10 | Whether a listening assessment is conducted, and at what scale | SR-07 |
| D11 | Representation of detection uncertainty (after the M1 literature review) | FR-05 |
| D13 | Disposition of `scripts/generate_test_audio.py` and local synthetic files | Housekeeping |
| D15 | Whether to adopt operator-attended live parameter control of one calibrated FL Studio insert (scripting API, fader-only outcomes) as an optional capability with a new functional requirement. Proposal in [ADR-030](../decisions/0030-daw-parameter-control-boundary.md); `.flp` manipulation stays excluded and FR-14 withdrawn. Proposed 2026-11-18. **Decided 2026-11-18 (owner):** adopted for bounded, operator-assisted runtime integration, initially limited to gain changes and verified manual exports, with explicit DAW parameter state inside `ProjectState` (Option A), and with fader restoration distinguished from complete project rollback. Recorded in [ADR-034](../decisions/0034-bounded-operator-assisted-fl-studio-integration.md) (Proposed); FR-19 and FR-20 added. This supersedes the earlier "no requirement, scope or status changed". | Scope, exclusions, ADR-030, ADR-034 |

Resolved on 2026-10-15: D1 thesis title ("…Using an AI Agent"); D12 defect-injection policy (ADR-008); D14 approval recording (status line above plus a research-log entry). D2 and D4 are partly resolved: P1 is the end-to-end contingency minimum, and both individual stems and the combined mix must be assessable.
