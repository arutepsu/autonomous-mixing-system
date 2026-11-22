# Diagnosis and AI Decision Adapter Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans or, when explicitly delegated, superpowers:subagent-driven-development to implement one approved increment at a time. This document authorizes no implementation.

**Status:** Staged plan and R0 method specification accepted by the owner. R1 descriptive measurements are implemented and committed at `0242c99`. Calibration has not run; production diagnosis and the provider adapter remain unimplemented. Execution order clarified on 2026-11-22 below.

**Goal:** Turn defensible measured evidence into deterministic diagnosis candidates, and let a provider-backed AI propose a bounded correction through the existing coordinator without gaining execution authority.

**Architecture:** Measurement produces observations; a pure deterministic method produces candidates and coverage; the AI interprets these alongside producer intent and permitted actions. Existing decision validation, admission, capability gates, verification and acceptance remain authoritative. Provider access and audit storage remain adapters, composed in the driver.

**Tech stack:** Existing Python/core records, reviewed NumPy DSP kernels and shared verified WAV reader; optional Anthropic SDK for the first provider; canonical JSON and local append-only exchange evidence. Core contains no SDK, NumPy, filesystem access or credentials.

**Spec and governing references:** Existing `docs/project/engineering-conventions.md`, `docs/research/methodology.md`, ADR-010, the requirements' scientific controls, and `docs/superpowers/specs/2026-11-11-mpeg-psychoacoustic-front-end-contract.md`. This proposal supplies the missing design; scientific policy choices in R0 must be approved before implementation depends on them.

## Global constraints

- Preserve the hidden-reference boundary: no clean/manual reference audio, labels, reference-derived targets, held-out results or evaluation-only paths enter runtime analysis, diagnosis, prompts or feedback.
- No fabricated diagnoses, synthetic production evidence, manual decision-file substitute or relabeling a level measurement as masking.
- No installations, provider calls, live DAW calls, saving or exporting in these implementation increments without separately authorized trials.
- Preserve the offline path and DAW safety rules. A model proposal never directly calls the DAW or renderer.
- Freeze methods, thresholds, profile, prompt, schema and evaluation split before held-out evaluation. Development pilots do not establish general thresholds.
- Each increment is separately reviewable and tests-first. Leave work uncommitted for review unless separately instructed.
- Keep unsupported families explicit. NoChange and an empty candidate list never establish success.

## Thesis focus and feasible evaluation — owner clarification, 2026-11-22

The primary contribution is the AI agent system: how it receives evidence and producer intent, chooses permitted tools, uses iteration/feedback, and verifies, accepts or recovers interventions. Measurement correctness and defensible mixing decisions are necessary foundations; a new generally validated psychoacoustic detector is not an additional thesis objective.

Prioritize a complete, bounded agent/tool/verification loop and reproducible comparisons over further optional infrastructure or a large detector-development study. Proposed tasks must state which research question they support and why their effort is proportionate to the bachelor-thesis schedule and available resources. Do not assume funding for paid assessors or access to professional engineers.

Separate three evidence claims:

- **Software correctness:** tested numerical behavior, valid data/provenance, bounded admitted actions and reliable handling of malformed responses, failures and recovery.
- **Mixing relevance:** an explicitly documented experimental measurement/diagnosis policy, producer intent, relevant technical objectives and guards. A safe command alone does not establish a good correction, and an optimized model score alone does not establish audibility or quality.
- **Agent evaluation:** controlled comparisons of unprocessed/deterministic/single-pass/iterative conditions, with recorded decisions, failures, regressions and costs. Listening can supplement these results; the owner's judgment is valid producer-intent evidence but is not independent perceptual validation.

Six calibration projects, exactly three listeners and the proposed 36/18 parameter searches were assistant-generated study proposals, not adopted requirements. An extended listener-based detector-calibration study is optional and must be justified separately. Volunteer assessors may support a feasible exploratory listening task; report their experience and claim limits honestly. Paid professionals and a fixed panel size are not prerequisites for software development or the core agent-system contribution.

This clarification does not authorize invented thresholds, fabricated diagnoses, hidden-reference access or claims of general mixing/perceptual superiority. The accepted method's sufficiency checks remain: a missing calibrated/approved policy must be represented truthfully. A smaller operational-policy/evaluation proposal still needs explicit method and claim review; dropping the optional study is not itself approval of new numerical values.

## Execution order clarification — 2026-11-22

The owner is creating the musical dataset in FL Studio, as planned. Dataset completion is not a prerequisite for every software increment. The earlier emphasis on completing calibration registration before further engineering was too broad; this section corrects that dependency without weakening scientific gates.

| Work stream | Can progress while the dataset is developed | Still requires musical data or later approval |
| --- | --- | --- |
| R3 packet, parser and audit | Build/test explicit producer-intent and action projection, response validation, provenance and failure records using labelled contract fixtures | No claims about an actual model run or scientific diagnosis from test fixtures |
| R4 provider adapter | Implement/test transport and failure mapping with a provider stand-in after R3 interfaces are reviewed | Live provider calls remain separately authorized; model performance needs real recorded trials |
| R2 diagnosis mechanics | Implement method/observation binding, coverage, insufficiency and abstention-compatible behavior, with an explicit missing-policy state | No actionable P1 candidate or qualified support without reviewed calibration and evidence |
| Numerical verification planning/tooling | Prepare separately reviewed numerical-control tooling and reproducibility checks using analytical test signals | New calibration experiments still require their registration and execution approval; controls are not musical evaluation data |
| Dataset creation | Owner continues FL Studio projects, source provenance, intended roles and compatible export planning | Musical qualification, policy selection and held-out evaluation wait for eligible data and their respective approvals |

Next engineering order: R3, then R4; R2 mechanics may be scheduled independently with calibration-dependent outcomes disabled. R5 can later test composition and failure/abstention paths; a tested wiring path is not an evidence-qualified autonomous correction trial. Do not enable live runs or claim successful diagnosis merely because adapters exist.

The proposed six-project corpus, listener panel and calibration search are still proposals for separate scientific/workload review. They are not conditions for writing the provider adapter. No software task, live call or experiment is executed or newly approved by this dependency clarification alone; individual implementation reviews remain.

## 1. Original planning-baseline inventory

This table records the baseline at `027bed7`, not the current implementation status. R1 subsequently added descriptive pair measurements/aggregation at `0242c99`; qualified support, calibration and production diagnosis are still missing. Current execution priorities are recorded above.

| Existing contract/component | Consequence for this design |
| --- | --- |
| `core/ports/coordinator_runtime.py::DiagnosisSource` and `core/records/coordinator_runtime.py::DiagnosisRequest` | Diagnosis receives selected state, scope and current observations. It has no producer context or acceptance criteria. Inject a versioned diagnosis policy; do not read undeclared context or invent thresholds from a goal. |
| `core/records/diagnosis.py` | Candidates require real observation IDs and matching family, scope, method and coverage. Coverage distinguishes assessed, incomplete and unavailable; uncertainty is qualitative or uncalibrated, not a probability. |
| `core/ports/decision.py`, `core/records/decision.py::Situation` | The decision receives context, observations, diagnosis, capabilities, criteria, budgets and prior-call history. Structural validation is not authorization. `Situation` contains source locations and is not itself a provider sandbox. |
| `core/admission/decision_gate.py` | Genuine abstention and NoChange already exist. Insufficient-evidence candidates cannot justify a proposed correction. Action admission and runtime gates still follow. |
| `core/records/analysis.py` and observation records | Jobs bind exact metrics, scope, specification and ordered sources. Observations carry a measured scalar or an unavailable result, not a frame-by-band array. Arrays stay inside the measurement adapter. |
| `adapters/dsp_analysis/file_analyzer.py` | Production peak/RMS measurements work on native channels. These are guards and level evidence, not masking, buildup or phase diagnoses. |
| `adapters/dsp_analysis/front_end.py`, `wav_input.py`, `masking.py` | Reviewed mono 44.1 kHz PCM_16 clip-to-band processing and directional margin arithmetic exist. Production pair measurement, aggregation, qualification and diagnosis calibration do not yet exist. |
| DAW coordinator, recovery, continuation and operator tools | Control and recovery infrastructure exists. Start/continue currently refuse because production diagnosis and decision dependencies are absent. Do not remove that refusal until genuine compatible composition is available. |

Planning baseline: clean tree at `027bed7` (`feat(cli): add DAW status and authorized operator recovery`). The legacy M0 overlap scripts and the FL pilot scripts are research evidence, not production implementations of these ports.

## 2. Scientific choice: P1 first, conditional on a measurement gate

**Recommendation:** P1 directional masking is the first family with sufficient numerical groundwork for an implementation increment. No family currently has an approved production defect detector. P1's first deliverable is reproducible model evidence and a calibrated, explicitly limited risk rule; not a claim that masking is audible or that a mix is improved.

P2 frequency buildup and P3 phase interaction remain UNAVAILABLE until their own measurement definitions, validity limits and diagnosis rules exist. Peak/RMS cannot fill this gap. A new generic level-problem family would change the research scope and is not proposed here.

### Limits that must travel with P1 evidence

- Positive margin means the target energy lies below the model threshold. It does not prove perceptual masking, causation by the masker, or musical undesirability.
- The threshold includes the threshold in quiet. Silent-masker or quiet-floor-only cells must not be presented as masker-caused evidence; define explicit qualification and report exclusions.
- Frame eligibility, exact silence, low-energy/quantization sensitivity and analysis history matter. Run every clip from call 0, then select eligible frames; never crop away startup history.
- The pinned dist10 mapping puts bins 465-512 into partition 0. Band 0 includes high-frequency energy despite its nominal low-frequency label. Do not describe it as an isolated bass band or silently repair it.
- The captured 0.005552 dB maximum margin difference is descriptive for those inputs, not a general numerical tolerance or a diagnosis threshold. Near-zero decisions need separate testing.
- Existing kernels accept mono 44.1 kHz PCM_16 only. Native stereo DAW exports, float and higher-bit-depth input are not supported by that profile. Preserve original stereo for native-channel guards.
- A declared timeline and equal frame counts do not prove physical alignment. The first corpus must have documented alignment provenance and matching timeline/sample geometry; unknown alignment is unavailable evidence.

### Approaches considered

1. **P1 measurement, then deterministic diagnosis, then provider composition (recommended).** Builds on reviewed numerical work without inventing a detector.
2. **Provider-only adapter first.** Useful as a separately testable engineering increment with labelled port fixtures, but cannot establish an autonomous mixing system or justify P1 actions by itself.
3. **Levels-only diagnosis to enable the loop sooner.** Rejected: the current problem-family contract has no generic level family, and mapping level deviation into P1/P2/P3 would fabricate evidence.

## 3. Evidence-to-candidate contract

Use an explicit, versioned `MaskingDiagnosisPolicy`, independent of producer preferences. It declares the supported profile, directional source roles, region/support definition, required metric set, minimum evidence sufficiency and calibrated candidate rule. Values require R0 approval; this proposal invents no numerical cutoffs.

Measurement proceeds on each ordered pair `(masker, target)` and declared time/band region:

Directional job identity is explicit: `scope.track_ids == (masker_track_id, target_track_id)`, and ordered source requirements and resulting artifact IDs use that same order. Do not sort this tuple. Existing job keys use the entire scope, so opposite directions remain distinct without changing core selectors. Test both directions together; set-based scope containment alone does not establish direction.

1. Verify sources through the existing shared reader and strict PCM_16 boundary. Confirm matching sample geometry and declared alignment provenance; refuse unsupported profiles rather than converting.
2. Run both complete clips through the reviewed front end. Retain call IDs, windows, eligibility and exact-silence metadata. Compute signed directional margins with `directional_masking_margin_db`.
3. Apply the approved eligibility/activity/quiet-floor/low-energy rule. Record reasons and counts for excluded cells. Do not omit adverse cells merely because the candidate changes their activity.
4. Emit a small scalar metric catalogue, with exact units and semantics fixed in R0: support count, qualified count, signed margin summary and exceedance count/fraction are candidates for this catalogue, not approved defaults. Zero qualified support is unavailable, not zero risk.
5. Retain the frame/band grid, qualification mask and support identity as versioned measurement evidence outside core; observations cite the exact sources, scope, analyzer and analysis specification. The policy/specification must identify the support-manifest digest so summaries cannot conceal changing denominators.
6. For before/candidate comparisons, freeze the comparison support from the initial runtime baseline or a predefined region, before corrections. This baseline is the supplied mix, not a hidden clean reference. If required cells cannot be evaluated later, report unavailable evidence rather than dropping them and claiming improvement. Report exploratory current-state summaries separately.

The deterministic diagnosis consumes these observations only. It checks complete evidence, methods, units, direction, current source bindings and support before applying the rule. It emits:

- ASSESSED with candidates when the approved rule is met;
- ASSESSED without candidates when supported measurements do not meet that rule, without claiming perceptual absence;
- INCOMPLETE with missing-information/limitations when prerequisites are missing; any candidate is explicitly insufficient and cannot authorize correction;
- UNAVAILABLE for unsupported families/profiles, with no invented candidate.

Each candidate names the exact contributing observations, pair/region, method and uncertainty. IDs come from the injected identity source, never from a provider. Fresh IDs on a later run do not change the deterministic classification, scopes or cited evidence content.

## 4. What the AI sees, and what it may return

### Explicit request projection

Never serialize `Situation` wholesale. Build a versioned allowlist packet containing:

- opaque aliases for current tracks, candidates, observations and relevant history items;
- producer track roles, preferences, protected tracks and their assertion provenance; unknown roles remain unknown;
- all applicable operational objectives and guard thresholds, with units, direction and scope; qualitative preferences stay qualitative;
- diagnosis coverage, candidates, measurements, unavailable reasons, uncertainty, excluded support and method limitations;
- available action types, exact track-specific bounds, units and coordinate semantics, plus budget remaining;
- prior proposals and their actual gate/verification/acceptance/recovery feedback, using the same aliases consistently across revisions.

`SetTrackGain` remains an absolute application gain with the existing baseline semantics. On the DAW path, distinguish that coordinate from the observed fader and the requested physical change. The AI does not calculate or authorize a normalized FL fader command.

Provide bounded public request data only: no filesystem paths, audio attachments, repository access, browser/tools, hidden references or grading answers. Producer text and names are data, not instructions that can override policy. An oversized packet fails explicitly; never silently truncate objectives, guard thresholds or feedback. If a compact history representation is later needed, version it and test semantic coverage before adoption.

### Structured response

The response DTO is a discriminated union: `propose_action`, `no_change`, or `abstain`. A proposal has exactly one supported action with parameters, existing candidate/evidence aliases, expected effect, concise public rationale, uncertainty, assumptions and limitations. Abstention uses the existing reasons and can request missing producer context. Do not request private chain-of-thought.

The adapter assigns decision/action IDs and binds run, state revision, context and controller locally. The model cannot choose those bindings or alter criteria, budgets, profiles, methods or evidence. Unknown aliases are invalid; no fuzzy matching or invented evidence.

Validation order: provider envelope/stop reason -> bounded strict JSON -> schema -> alias resolution and finite typed values -> core record construction -> `validate_decision_response` -> existing `assess_decision` -> ordinary action admission and runtime capability gate. Exact action authorization remains in the coordinator. Never clamp, repair, substitute a different action, or execute a provider proposal directly.

Reject extra keys, duplicate JSON keys, boolean-as-number parameters, NaN/infinity, missing fields, unrecognized variants, multiple proposals and incomplete output. A parser failure is not an AI abstention; NoChange is not target achievement.

## 5. Provider failures and reproducible evidence

Use one explicit provider request per `decide` invocation. Disable SDK automatic retries so the coordinator's existing retry policy and decision-call budget count actual attempts. A retry is a new recorded exchange; it is not assumed to reproduce a previous answer.

| Condition | Planned result |
| --- | --- |
| Valid model abstention or NoChange | Normal `DecisionResponse`, processed by the existing gate; no fabricated correction. |
| Malformed/truncated response, wrong alias, unsupported action or invalid core binding | `PortFailure(INVALID_RESPONSE, retryable=False)` with evidence reference retained; no automatic JSON repair. |
| Provider refusal | Recorded separately from model abstention; non-retryable provider failure with a specific reason in the audit record. No synthetic `Abstain`. |
| Transient timeout, rate limit or service unavailability | `PortFailure(UNAVAILABLE, retryable=True)` subject to coordinator retry and budget. Record that server execution/billing may be unknown. |
| Missing credentials, unsupported configured model/API profile or authentication failure | Configuration/unsupported or non-retryable unavailable result before a run when detectable; never substitute a different provider/model. |
| Audit storage unavailable before dispatch | No request sent. Refuse the call. |
| Response received but evidence cannot be durably recorded | No decision released for admission. Record an incomplete exchange where possible and fail closed. |
| Unexpected implementation exception | Preserve partial evidence where possible and propagate under the existing runtime contract; do not disguise it as a musical decision. |

Add a narrow `DecisionAuditTrail` port with `begin`, `record_response`, `finish` and `read` operations. Proposed immutable core records describe the pending exchange, recorded public response and completed exchange reference. The key is `(run_id, usage.decision_calls)`, already unique per charged coordinator decision call, plus a generated exchange ID. `Situation` includes the charge for this call. Store both the exact provider packet hash and the initiating runtime-call binding; do not send private state serialization to the provider.

The provider adapter must persist the request before dispatch, public response bytes before interpretation, and validation/failure classification before returning. The coordinator, configured with this collaborator for provider-backed decisions, requires a completed matching exchange reference before using the result and records the reference in its trace. Missing, mismatched or pending evidence blocks action. This covers failures as well as successes without adding an optional provenance field only to `DecisionResponse` or weakening generic `PortFailure`.

The exchange store is append-only and local, separate from the existing run/acceptance store, with its own explicit schema version. It retains exact UTF-8 request body, prompt/schema versions, alias map, body hashes, requested/resolved model IDs, provider/API and SDK versions, explicit generation parameters, UTC/elapsed times, provider request ID, stop reason, token usage and received public response body. Store safe error metadata, never credentials/auth headers. If no response arrived, record that fact, not an invented response. Reopening a pending exchange must not resubmit automatically.

Persist a bounded, explicitly defined public response envelope; do not claim access to server-internal bytes. A response exceeding the recording limit is rejected with its size/truncation status documented. Reproduction means replaying saved inputs and the recorded response through the same projection/parser/gates; identical fresh model output is not promised. Replay fixtures are evidence/test infrastructure, not a manual decision-file controller.

The first provider is Claude via the existing optional `agent` dependency group. Select an explicit supported model and API configuration at implementation review; do not freeze an unverified alias in this plan. Official documentation checked 2026-11-20: [structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [Messages API](https://platform.claude.com/docs/en/api/messages/create), [Python SDK](https://github.com/anthropics/anthropic-sdk-python). Structured output constrains syntax, not evidence truth or permission. Recheck the locked SDK/API compatibility when implementing; do not assume today's documentation matches the lockfile.

## 6. Review focus

1. A correction makes weak cells disappear: fixed-support tests must refuse denominator changes rather than report improvement (R1).
2. Stereo, quiet-floor-only evidence or the dist10 band-0 quirk gets called bass masking: qualification/profile/wording tests must prevent it (R0-R2).
3. Malformed or plausible-but-unbound output reaches admission: strict parsing, alias and revision tests must block it (R3-R4).
4. A failed provider call disappears or retries outside the budget: crash/audit/retry tests must preserve evidence and stop execution (R3-R5).
5. Reference data or permissive source paths leak through history: request projection and driver-root tests must demonstrate the hidden-reference boundary (R3-R5).

## 7. Separate increments and approval gates

### R0: Freeze the initial P1 method and input profile (documentation/research)

**Deliverable:** a short P1 measurement/diagnosis specification with a primary-source rationale and a development-only calibration protocol. It must choose scalar metrics, aggregation, activity/quiet-floor/noise qualification, support freezing, pair direction, alignment provenance and the criterion that makes evidence sufficient. Establish a numerical error allowance using captured and near-boundary cases before choosing a decision cutoff. Keep numerical error, model validity and audibility claims separate.

**Files:** create `docs/superpowers/specs/2026-11-20-p1-diagnosis-method.md`; update the front-end contract only with reviewed status/limitations and append the research log. Allocate any new ADR number after checking the index. No requirement adoption by implication.

- [ ] Trace metric and aggregation choices to primary literature and the pinned reference implementation; distinguish a source-supported formula from a new policy choice.
- [ ] Prefer already aligned native mono 44.1 kHz PCM_16 inputs for the first implementation. Any stereo-derived profile is a separate proposal with an immutable conversion recipe and validation; no automatic fallback.
- [ ] Define development/calibration and held-out sets before looking at outcomes. Existing trap-pilot thresholds remain case-specific evidence, not production defaults.
- [ ] Approve method/profile/limits before R1 or R2 treats them as executable policy. If calibration is not defensible, stop at descriptive measurements and honest abstention.

### R1: Production directional measurements, without diagnosis

**R0 lifecycle clarification for review:** support is prepared and persisted before constructing the immutable run request; its cost is separately recorded, not charged to a nonexistent analysis-call budget. See the R0 specification. R1 defines the preparation records/interfaces; R5 composes them.

**Files:** create `adapters/dsp_analysis/directional_analysis.py` and `analysis_router.py`; add the metric/plan records in `core/records/masking_measurement.py`; tests in `tests/unit/test_directional_analysis.py`. Update exact architecture inventories and the metric catalogue where already defined. Existing DSP kernels stay unchanged unless a separately reviewed defect is found.

**Interfaces:** an immutable `MaskingMeasurementPlan` binds ordered pair, profile, support and method/specification digest. `DirectionalMaskingAnalyzer.analyze(AnalysisRequest)` implements `AudioAnalyzer` for the approved jobs; `AnalysisRouter.analyze` partitions mixed level/P1 jobs and returns one correctly bound result without partial success. The router preserves each job's existing analyzer identity; it does not relabel level observations as composite/P1 output. Each subrequest retains `AnalysisSpec.ref`, only its selected `metric_ids` and definitions, and the original exact current-member root bindings. Validate the merged response against the original request.

- [ ] Write failing tests for captured directional arithmetic, ordered source reversal, exact silence, quiet-floor-only support, empty qualification, near-zero margins, late/truncated windows and history from call 0.
- [ ] Pin fixed-support identity across original/candidate states and forbid silent cell dropping. Test missing/changed cells, invalid source geometry/alignment declarations and incompatible encodings/channels.
- [ ] Implement verified reads and measurement aggregation only after the tests are red. Persist the diagnostic grid/masks as measurement evidence; expose scalar observations with correct unavailable reasons.
- [ ] Verify mixed-job coverage, both pair directions in one request, subrequest metric coverage, original per-job analyzer identities and merged-response validation. Keep existing peak/RMS results unchanged. Unknown jobs/profiles fail explicitly, and current-source binding cannot resolve to hidden/reference files.
- [ ] Review captured agreement and new numerical boundary tests. Deliver no `ProblemCandidate` in this increment.

### R2: Deterministic diagnosis and configured source

**Files:** create `core/records/masking_diagnosis.py`, `core/diagnosis/masking.py`, `drivers/diagnosis_runtime.py`, and `tests/unit/test_masking_diagnosis.py`; update architecture inventories.

**Interfaces:** `diagnose_masking(request: DiagnosisRequest, *, policy: MaskingDiagnosisPolicy, identities: IdentitySource) -> DiagnosisReport`; a driver-owned `ConfiguredDiagnosisSource` implements the existing port and delegates to this pure rule. The driver placement avoids an adapter importing a core diagnosis stage, which current dependency rules prohibit.

- [ ] Write failing tests for full evidence, below-rule evidence, unavailable/partial evidence, wrong units/specification/sources, stale revision, swapped direction and missing support identity.
- [ ] Require truthful P2/P3 coverage and no candidates under unavailable coverage. Insufficient evidence must not become an actionable candidate.
- [ ] Implement the approved R0 rule and versioned limitations. Tests compare repeat classification/evidence content while allowing fresh generated IDs.
- [ ] Validate every result using `validate_diagnosis_response`; test that producer preference text cannot change the measured diagnosis or its policy thresholds.
- [ ] Review scientific sufficiency separately from software correctness. Passing tests alone cannot approve a detector.

### R3: Provider packet, parser and audit contracts, without network

**Files:** create `core/records/decision_exchange.py`, `core/ports/decision_audit.py`, `adapters/decision_agent/request.py`, `response.py`, and `adapters/journal/decision_exchange.py`; tests in `tests/unit/test_decision_packet.py`, `test_decision_response_parser.py`, and `test_decision_exchange_store.py`. Use the repository's actual journal package layout, confirming the final storage module location during task review.

**Interfaces:** `build_decision_packet(Situation) -> DecisionPacket`; `parse_decision_response(public_response, *, packet, situation, identities) -> PortResult[DecisionResponse]`; `DecisionAuditTrail` as specified above. DTOs/immutable domain records live in records; provider SDK types never do.

- [ ] Write red tests for complete criteria/guard projection, role provenance, protected tracks, exact action coordinates and historical feedback.
- [ ] Use sentinel hidden-reference paths/labels and hostile producer text to prove the allowlist does not leak privileged material or grant tools. Also inspect the actual request payload; prompt instructions alone are not a boundary.
- [ ] Test every response variant plus unknown IDs, stale bindings, extra/duplicate keys, wrong numeric types, multiple outputs and malformed/truncated JSON. Genuine abstention and malformed output must remain distinct.
- [ ] Test request-before-dispatch ordering, public-response-before-parse ordering, duplicate/conflicting exchange keys, fingerprints, partial writes and pending exchanges after a crash.
- [ ] Specify the trace reference contract and durable codec additions here, before R5 changes the loop. Schema evolution is explicit; do not silently rewrite existing run stores.

**R3 implementation status (2026-11-22, Claude Code; uncommitted for Codex review).** Implemented per the R3 handoff (`2026-11-22-r3-decision-packet-audit-claude-handoff.md`). No provider call, SDK, network access or coordinator change.

- **Records and port:**
  - `core/records/decision_exchange.py`: call key, alias bindings, packet and its local binding, the three exchange phases, the completion categories and typed audit results.
  - `core/ports/decision_audit.py`: `DecisionAuditTrail` (`begin`, `record_response`, `finish`, `read`) and `permits_dispatch`.
- **Projection:** `adapters/decision_agent/request.py`, with the final signature `build_decision_packet(situation, *, limits: PacketLimits) -> DecisionPacket`.
  - Packet schema `autonomous_mixing.decision_packet@1`.
  - The alias strategy is fixed and versioned in the packet (`autonomous_mixing.alias.sha256_run_scoped@1`) rather than injected.
- **Parser:** `adapters/decision_agent/response.py`, with the final signature `parse_decision_response(public_response: RecordedDecisionResponse, *, request: DecisionExchangeRequest, situation, identities, limits: ResponseLimits) -> PortResult[DecisionResponse]`. *Review fix 2026-11-22: it took `packet=`; it now takes the recorded request.*
  - Response schema `autonomous_mixing.decision_response@1` (`RESPONSE_JSON_SCHEMA`).
- **Store:** `adapters/journal/decision_exchange.py`, `SqliteDecisionExchangeStore`: schema 1, one SQLite file per run, append-only.
- **Trace reference for R5 (specified, not wired):** R5 reads the exchange by `DecisionCallKey(run_id, usage.decision_calls)`. It uses a provider result only when the record is `COMPLETED` with `VALID_DECISION` and its decision equals the returned one. A pending, response-only, failed or missing exchange blocks action.
  - No run-store schema or record-codec change was needed: the codec registers the new core records automatically.
- **Deviations to review:**
  - The parser takes the recorded response record, which enforces "record before parse".
  - Track display names, free technical text and policy/renderer references are not projected (codes and enums only).
  - Wiring mismatches between packet and situation raise `ValueError` instead of returning a port failure.
- **Codex review fixes (2026-11-22), each with a regression written first:**
  1. **Unavailable reasons and coverage explanations** are no longer sent: the packet carries `"unavailable": true` and the coverage status. The text is kept verbatim in the local binding (`DecisionPacketBinding.withheld`).
  2. **Wrongly typed `type`, `reason` and `operation` values** are now `INVALID_RESPONSE` instead of a `TypeError`.
  3. **Content binding:** the parser re-projects the situation and requires the packet's payload and binding to match exactly. Changed criteria, measurements or withheld text behind unchanged IDs are refused.
  4. **Exchange binding:** the response must name the request's call, exchange ID and full request fingerprint, which is distinct from the packet-data hash. Each check runs before interpretation.
- **Second review fix (2026-11-22):** bounded JSON that exceeds the decoder's nesting depth, or holds an integer literal beyond the interpreter's digit limit, is now `INVALID_RESPONSE`. Only decoding errors are converted, and no global limit is raised.
- **Designated public content (no general sanitization claimed):** sent verbatim, with its suppliers responsible for keeping it public:
  - producer role labels, preferences and assertion sources;
  - diagnosis evidence statements;
  - metric, method and unit names;
  - earlier public decision text.

### R4: First provider adapter and failure mapping

**Files:** create `adapters/decision_agent/claude.py`, `transport.py`, and `tests/unit/test_claude_decision_maker.py`; adjust the optional dependency lock only if the approved API profile requires it.

**Interfaces:** `ClaudeDecisionMaker.decide(Situation) -> PortResult[DecisionResponse]` implements the existing decision port, with injected transport, audit trail, identity source and explicit provider configuration. A transport double is test-only, not a runtime decision source.

- [ ] Write red tests for a valid proposal/NoChange/abstention and all failure-table rows, including provider refusal, token exhaustion, timeout and response-recording failure.
- [ ] Assert one physical dispatch per invocation with SDK retries disabled; verify no dispatch when credentials/configuration or request persistence fail.
- [ ] Implement structured output using the locked supported API. Validate the envelope before parsing and run local validation even when provider schema mode succeeds.
- [ ] Record requested/resolved model, parameters and actual public response/error evidence. Prove secrets are absent and recorded responses can be replayed through validation.
- [ ] Keep live smoke tests opt-in and separately authorized. A stubbed response demonstrates engineering behavior, not model performance or thesis findings.

### R5: Composition through the existing coordinator

**Files:** modify the actual driver composition root (`drivers/daw_runtime.py` for the DAW path), `core/loop/decision_round.py` for the audit-reference requirement, and CLI/configuration modules only after reviewing their existing construction seams; add `tests/unit/test_agent_composition.py`. Continue to use existing offline coordinator and durable trace contracts rather than creating a second control loop.

- [ ] Write red integration tests using real measurement/diagnosis/projection/validation components and a labelled provider transport double. Exercise admission, verification, acceptance, abstention, provider failure and trace/audit mismatch.
- [ ] Inject the deterministic source and provider adapter. Require the matching completed exchange before a provider result can reach admission; correlate failures too. Offline deterministic baselines need no fabricated provider exchange.
- [ ] Keep the current unavailable-run guard until configuration selects genuine supported components, methods and input profiles. Unsupported P1 stereo DAW sessions still refuse; do not weaken the WAV/masking boundary merely to enable `daw start`.
- [ ] Validate run budgets/retries and persisted method/prompt/schema identities across suspension and restart. The audit-store/run-store relationship must fail closed if evidence is missing.
- [ ] Test allowed runtime roots independently from hidden evaluation roots. No tools, clean-reference binding, developer repository context or evaluation-only metric can enter the provider packet.
- [ ] Demonstrate a bounded end-to-end path on compatible input. Native stereo DAW P1 needs its separately approved profile before the corresponding live path is enabled. Save/export/install remain operator tasks.

### R6: Development trial and evaluation readiness (separate authorization)

- [ ] Record exact input rights/provenance, producer intent, calibration version, initial support, provider identity and every exchange. Do not use material whose license excludes the intended AI research use.
- [ ] Run an authorized development trial with complete request/response records, frozen criteria and no hidden-reference access. Include valid abstention and a rejected/failed proposal, not only a successful correction.
- [ ] Compare deterministic, single-step AI and iterative AI conditions under matched measurements, evidence, action space, budgets and verification. Record any unavoidable differences rather than attributing all improvement to iteration.
- [ ] Freeze the system for held-out evaluation. Separate model-margin improvement, guard preservation, operational reliability and independently collected listening judgments. Do not generalize from one mix or claim perceptual improvement from the optimized proxy alone.

## 8. Decisions proposed for approval

1. P1 first, conditional on R0; P2/P3 unavailable and no levels-as-diagnosis shortcut.
2. Native compatible mono inputs first. Stereo conversion/profile expansion is separate work, not an implicit part of the provider adapter.
3. Diagnosis remains deterministic and producer-independent; the AI receives intent and selects among evidence-backed, permitted actions.
4. Allowlist request projection and mandatory provider exchange evidence, including failures, before provider-backed execution is enabled.
5. One provider dispatch per charged decision call, with retry controlled by the coordinator and no fabricated abstention or repaired proposal.
6. Separate review gates for R0/R1 measurements, R2 diagnosis, R3/R4 provider engineering and R5 composition. Approval of this plan does not authorize live/model/evaluation runs.

No implementation, dependency installation, scientific threshold selection or requirement-status change is part of this planning task.

## Planning verification

A read-only builder check reviewed the proposed contract boundaries. Its two findings (directional job identity and mixed-analyzer subrequests) are incorporated above. Only this proposed plan was added; implementation tests were not run because no implementation changed.
