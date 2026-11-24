# R5 audited experimental agent composition — staged implementation plan

> For implementers: use a tests-first execution workflow. Only the individually approved increment may be implemented. Leave changes uncommitted for Codex review; no automatic commits or live calls.

**Status:** Proposed, planning only, 2026-11-23. The measurement and experimental diagnosis slices exist; inspect HEAD/status rather than assuming their commit state. This document does not authorize implementation or live/model trials.

**Goal:** Compose the real experimental P1 measurements, deterministic diagnosis and audited Claude decision adapter through the existing operator-assisted coordinator, with reproducible suspension/restart and fail-closed evidence checks.

**Architecture:** Keep one coordinator and the existing analyzer/router, diagnosis source, provider adapter, FL controller, export handoff and journals. Driver composition owns concrete dependencies. Small provenance records/one narrow decision-evidence port bridge the missing audit boundary; no second agent framework, controller or renderer.

**Technology:** Existing Python records/ports, existing SQLite stores/codecs, local content-addressed JSON evidence, pytest and labelled transport/device doubles. No new dependency or scientific threshold.

**Governing references:** 2026-11-20-diagnosis-and-decision-adapter.md (R5 and current-values continuation); 2026-11-23-experimental-fixed-support.md; 2026-11-23-experimental-masking-diagnosis-handoff.md; ADR-027, 033 through 040; existing R3/R4 request/exchange rules.

## 1. Approach and scope

Recommended: three reviewable increments, provenance first, real-component composition second, CLI/configuration last. A single large wiring patch would mix new evidence contracts, restart behavior and operator UX. A second adapter/control loop would duplicate existing responsibilities. Both alternatives are rejected for reviewability and the owner's no-overbuilding requirement.

The existing experimental thresholds stay explicit and unchanged (40 / 0.02 / 0.02 / 100 / 0.25 / 4). They remain uncalibrated engineering configuration. P2/P3 stay unavailable, legacy readiness stays readiness-only, and scientific qualified R2B is not claimed. Real runtime observations may produce a hypothesis, no candidate or incomplete coverage. Never fabricate a candidate to reach the write branch.

This is still operator-assisted: no automated installation, saving or exporting. No live FL/provider call, music analysis, study execution, threshold tuning, evaluation split change or requirement-status promotion occurs during implementation. Tests use disposable numerical WAVs, the real components and labelled provider/FL transports. Such tests do not establish musical quality or real model behavior.

## 2. Existing gaps verified in the code

- decision_round consumes DecisionMaker results without reading the R3/R4 exchange. Both successful proposals and provider failures need correlation before use/retry.
- DirectionalMaskingAnalyzer persists evidence files but exposes observation -> digest through an instance-local dictionary; that association does not survive restart in the run trace.
- ConfiguredDiagnosisSource now supports the experimental rule. It is not composed by the DAW runtime.
- daw start/continue deliberately refuse before opening anything. Their full run configuration/manifest construction was never implemented in Increment 6.
- The existing code has one durable run store and one separate decision-exchange store per run. There is no cross-store transaction and none will be claimed.
- calibrated_profile currently supports only Producer Edition v26.1.7 build5654/API45, metadata title Thesis Bass Bridge Test, insert2 named Bass, conversion fl-mixer-h125/v1, normalized0.62..0.86. It must not be widened or relabelled for HT01 by composition configuration.
- Mono P1 and the exact-dual-mono input profile exist. Exact-dual-mono is not arbitrary stereo fold-down. Native stereo level/peak guards remain native stereo. Incompatible current/candidate stems fail the input profile; nothing is converted to make the run pass.

## 3. Invariants throughout R5

1. The AI proposes only. The existing evidence gate, admission, calibrated capability gate, budgets, verification, acceptance authority and recovery remain authoritative.
2. A matching COMPLETED/VALID_DECISION exchange must be established before any provider-backed decision reaches action admission. Equality includes the actual returned decision and exact Situation-derived request projection, not just run ID or observation IDs.
3. A correlated recorded provider failure can be retried only by the coordinator, as a fresh charged call. Pending/answered/missing/unreadable/mismatched exchanges never authorize an action or resend.
4. A local packet-size/configuration/audit failure with no completed exchange is recorded as an unverified local failure, never fabricated provider evidence or abstention, and stops without an automatic retry. No write is permitted.
5. Each experimental observation used by diagnosis/verification has a durable evidence reference before it is consumed. Missing evidence refuses progress; never regenerate a missing manifest or silently change fixed cells.
6. Preparation occurs once from the actual starting runtime mix, before constructing the immutable run request. Its timing/limits are recorded separately; it does not charge a nonexistent analysis call. Resume reloads, not prepares.
7. Run/store/evidence method/policy/input/diagnosis/prompt/schema/provider settings are fixed and compared across restart. New settings require a new run, not continuation.
8. Accepted authority survives recording/promotion failures. No new recovery path restores an accepted edit or resolves an unknown commit by guessing.
9. Runtime roots contain current production inputs/outputs only. Evaluation references, clean/manual targets and injected-condition metadata never enter the runtime configuration or provider projection. Public free-text remains explicitly public, not secretly sanitized.
10. No exactly-once network/device or multi-store atomicity claim. Interrupted states refuse continuation unless the existing recorded-suspension classification and the added evidence checks both pass.

## 4. R5A — Durable provenance and audited decision-use gate (first implementation)

### 4.1 Analysis evidence

Extend existing AnalysisResponse with trailing `evidence=()` and add a small immutable AnalysisEvidenceReference in the existing analysis records. A reference contains observation ID, evidence schema/version, content SHA-256, and prepared-plan SHA-256. It contains no absolute path. The configured local evidence root resolves `<sha256>.json`; do not build another evidence database.

The directional analyzer returns a reference for every P1 observation it produces; grouped observations may share one file. The router validates subresponses and merges references without cross-linking sources or losing associations. Its existing no-partial-success behavior remains. Level observations need no fabricated raw-grid file or P1 evidence reference; their observations/source hashes remain their current provenance.

Response validation requires references to name returned observation IDs uniquely, match their P1 analysis plan, and agree with the observation IDs and current source identities in the persisted file. The adapter checks file content hash/schema and its already-written payload. Core validates structural associations, never opens files. Reopening uses the same adapter-side verifier. All experimental P1 observations required by a provider run must have references before diagnosis or decision; empty legacy defaults preserve existing non-provider callers.

Store references as part of the already-recorded AnalysisResponse observation event, not through an independent driver append with a competing trace order. On restart traverse those recorded responses to recover associations. Content-addressed files and the prepared manifest must exist and verify before continuation; do not re-run DSP to reconstruct old evidence.

### 4.2 Audited use of a provider result

Use the existing DecisionAuditTrail for persistence. Add one narrow core port `DecisionEvidenceSource.verify(situation, result)` for binding a returned provider result to its recorded exchange. The concrete implementation belongs to the existing driver composition responsibility (daw_runtime.py or a cohesive driver helper only if it becomes unwieldy), not another provider adapter or framework.

Its successful typed result, DecisionEvidenceVerified, carries the exact Situation supplied, exact returned Success(DecisionResponse) or PortFailure, and the full DecisionExchangeRecord. Failure is typed DecisionEvidenceRejected with reason; do not turn it into model abstention. Core compares both returned inputs exactly to the call it made, checks call key/status/completion/result equality and records the evidence outcome before routing the result, charging an attempt or admitting an action.

The implementation reads by DecisionCallKey(run_id, situation.usage.decision_calls). It rebuilds the expected existing R4 request from the actual Situation and fixed ClaudeConfig, using the stored exchange ID/requested_at, then requires the complete request record to equal it (public bytes, packet data and withheld local binding included). It accepts VALID_DECISION only when its decision equals the returned result; for recorded failure it requires exact completed failure equality. Audit-read failure, a mismatched record or failed trace append blocks action. This is an adapter-checked provenance claim, not cryptographic authentication of a remote model.

Make provider evidence requirement explicit in immutable run configuration/RunRequest through a trailing optional provider-run binding, not by runtime `isinstance(ClaudeDecisionMaker)` or a global flag. It freezes provider configuration, prompt/schema identities and the experimental measurement/diagnosis plans. Thread an optional evidence-source dependency through existing run/run_daw/resume_daw internals. A provider-bound run without it refuses before decision dispatch. Deterministic non-provider callers retain their existing behavior and do not need fake exchanges.

Do not change DecisionMaker.decide or expose credentials in core. Credentials remain a transport dependency. New trace payloads and configuration records are included in the existing codec/architecture inventories. No SDK/model/tool execution is added.

### 4.3 Persistence compatibility and crashes

Prefer new records/additive defaulted fields, but the strict codec checks exact field sets: defaults alone do NOT make old stored objects compatible. Decision: increment the run-store schema to version 2 for this changed record set; both newly created provider and deterministic stores use v2. Existing v1 files are refused as legacy/unsupported before decoding changed records or writing; status reports that classification read-only and recovery does not operate through the unsupported decoder. No auto-migration, rewriting old rows or relabelling them as provider-audited. Retain the old files for the matching historical software. The separate decision-exchange schema stays v1 unless its actual stored record shapes change; pin that with an old-exchange round-trip test. If that test exposes a shape change, bump its schema explicitly too rather than accepting partial old records. Existing fixtures/frozen research evidence remain unchanged.

No transaction spans audit and run stores. Crash after audit completion but before trace evidence is an interrupted run, not permission to execute after restart. Crash after request persistence/before response leaves the pending exchange intact and must not resend. A trace claiming evidence whose audit phase/file is absent refuses continuation. Status must report unavailable/unsupported rather than treating missing provider evidence as absence of an effect.

### 4.4 Tests first / acceptance

- Valid exchange permits use; absent/pending/answered/wrong run/ordinal/decision/provider/prompt/schema/packet/local binding denies it, with no apply/admission attempt.
- Same IDs but changed measurement, threshold, capability, producer intent or history is rejected by request reconstruction.
- Completed retryable failure causes exactly one fresh charged coordinator retry; mismatched/missing exchange causes no retry. Local oversized request/recording failure stays recorded and unverified, no fabricated exchange/abstention.
- Evidence persisted before diagnosis; restart recovers it; missing/changed grid or prepared manifest refuses without analysis regeneration. Multiple current artifacts/directions do not share another observation's evidence.
- Legacy deterministic runs and level observations remain usable without P1/provider evidence; provider runs cannot omit the binding or required evidence source.
- A crash test for each audit/run ordering boundary; repeat existing accepted-authority/recovery regressions.

R5A ends with contracts/gates and simulated tests. No driver production composition or CLI activation yet. A Proposed ADR records the narrow evidence port/trace and store-version decision; accepted ADRs stay untouched.

### 4.5 R5A implementation status (2026-11-23; uncommitted, for Codex review)

**Implemented as specified in 4.1–4.4, with ADR-041 (Proposed):**
- evidence references in `AnalysisResponse`, preserved by the router;
- analyzer re-verification without DSP, including semantic manifest comparison;
- `ProviderRunBinding` and the narrow `DecisionEvidenceSource` port (`verify`, `verify_run`), with the driver verifier;
- the coordinator gate that records evidence before use or retry;
- continuation provenance verification;
- run-store schema 2, with schema 1 refused.

The decision-exchange schema stays 1, pinned by a record-shape test.

**Deviations:**
- Unverifiable provider continuation raises `ValueError` before admission and effects, not a typed refusal.
- `daw status` reports provider-bound runs as `provider_evidence_unavailable`.

**Independent review:** interrupted by a usage limit; its three reported findings were fixed with regressions written first.

**Evidence:**
- **Full suite:** 7,097 passed, 4 skipped.
- **Mutation probes:** 15 of 15 caught.

R5B and R5C are not started, and `daw start`/`daw continue` remain unavailable.

## 5. R5B — Real-component programmatic composition

Extend the actual driver composition root. Accept typed validated input; keep external JSON parsing for R5C. Build/own these real components and resource lifetimes: verified preparation/manifest store, directional and level analyzers through the existing router, experimental ConfiguredDiagnosisSource, ClaudeDecisionMaker with real audit implementation, decision-evidence verifier, durable run store, integrity inspector, FL controller and existing export handoff.

One named composition context/factory is sufficient; avoid a dependency-injection registry. Tests replace only the provider/device transports and clock/IDs, not diagnosis, observation, admission, verification or acceptance. Production credentials are supplied separately, never serialized or logged. Invalid roots/profile/criteria/provider settings are validated before MIDI connection, provider dispatch or fader writes. Explicitly close stores/transports in reverse order on failure.

Prepare from declared current baseline inputs, persist the result and freeze run binding before a decision. Build analysis definitions from exactly the verified plan and diagnosis metrics plus declared verification/guard definitions. Do not infer acceptance thresholds from diagnosis thresholds. Existing explicit RunConfiguration criteria remain the authority: a proxy target, its comparison/baseline and native-channel guard thresholds must all be supplied. The factory adds no musical target/default acceptance rule and never uses a hidden clean reference.

Use existing run_daw/resume_daw. Initial verification still runs first and may finish the run (including inconclusive/baseline unavailable) before any diagnosis/provider call. Do not bypass that gate to force an abstention demonstration; such a test must use a genuinely unmet but assessable explicit initial criterion. Same-process and new-process continuation rehydrate exact plans/configuration/evidence from the stored request, not current config guesses. Keep the complete context and policy stable through an intermediate accepted candidate and subsequent suspension. The existing export handoff resolves each current checkpoint with its current roots/protected files, not just the initial state.

Tests cover actual compatible numerical inputs: positive hypothesis and bounded proposal -> one FL write -> suspension -> manual-test export files -> import -> real analysis/verification -> acceptance or exact recovery. Also test incomplete/assessed-no-candidate -> genuine provider abstention; malformed/refused/timeout provider responses; denied action; rejected candidate -> verified rollback -> revised second decision; uncertain apply; commit-acknowledgement loss; accepted edit never restored; restart without a second dispatch. Assert public packet aliases/history/uncalibrated limitations and hidden-reference sentinels.

Baseline profiles/channel counts must match the native export contract. Exact-dual-mono stems are verified for every candidate; nonidentical stereo becomes unavailable/refused under the existing profile and recovery, never an implicit average. Initial incompatible inputs refuse before a provider call/write. The current calibrated insert restriction is preserved, even if a different project is available.

R5B completion means real software composition with labelled external doubles. daw start/continue still refuse; no live-model/FL success claim.

## 6. R5C — Validated operator configuration and CLI

Only after R5A/B review. Extend existing daw_runtime/daw_commands/cli. Keep existing status/recover functionality and read-only guarantees.

Add an explicit versioned run configuration containing typed initial state/current roots, DAW binding, compatible input/export profile, producer intent/roles/permissions, scope, experimental policy/regions, declared verification criteria/measurement definitions, budgets/retries, provider generation settings and local store/evidence/output directories. Prefer the existing exact core-record codec for complex records over a parallel serializer; a small allowlisted JSON envelope supplies operational paths/provider settings. Unknown keys and evaluation fields refuse. Secrets use the existing environment-injected transport mechanism, never the file. The finalized field schema/help examples are a separate R5C review artifact before coding; do not let a coding agent invent a broad configuration language.

`daw start`: validate inputs/settings, create fresh run/audit/workspaces exclusively, run exact existing fader start check and freeze plans/config. First-session declaration repeats the fresh inspected reading exactly, explicitly as an operator declaration, never adoption of unexplained journal history. Do not create an alternative way to bypass unresolved/block/latest-anchor checks.

`daw continue`: require stored suspension and attestation, ownership and exact fixed configuration; construct manifest only from declared output names, shared verifier and operator attestation. Reload the actual recorded request and bindings; refuse configuration drift before import/provider/write. Do not replace the stored request with a new one assembled from flags. Busy/incomplete/missing evidence/store states refuse. Duplicate continuation remains idempotent under existing rules.

Print manual export instructions from the saved handoff: output names, profile, interval and destination; no audio export/save automation. A prior v1 device cannot perform v2 restoration: preserve its tested safe refusal and unresolved outcome, and document the operational prerequisite rather than installing it automatically. Explicitly document needed live calibration for another project/insert; do not loosen calibrated_profile.

Retain exit0 for completed command/valid suspension, exit2 invalid configuration/arguments, exit4 operational refusal/in-use. Printed run outcome/termination is separate from process exit; do not present a suspended or abstained run as successful correction. Status reports stored provider/policy/evidence identities without network/MIDI.

Tests through main: valid config, every unknown key, absent credentials, incompatible profiles, wrong calibrated project/insert/build, drifted resume config, busy stores, correct manual instructions, unchanged status/recover behavior and no side effects on early refusal. No test-only mode, decision file or fabricated production dependency.

## 7. Verification, scientific boundaries and review gates

Each increment: red regression first; focused -W error; full locked suite; architecture/import/codec tests; lint/format, CLI help, lock and diff checks. Faithful probes target exact exchange comparison, pending replay, evidence mapping/file identity, policy persistence, missing evidence on resume, native-profile checks and accepted-authority preservation. Restore source byte-identically. Do not inflate probe counts with equivalent mutants.

Update only relevant status notes; research log append-only. Requirements remain Partial until separately reviewed scope/status evidence justifies change. No claim of calibrated masking, generally better mixing, model intelligence, real feedback benefit or live end-to-end capability from test transport responses.

Live development is R6, separately authorized after review, with licensed inputs, operator-assisted FL export and complete raw exchange records. Musical evaluation/data collection may continue in parallel; more music is not a prerequisite to implementing these contracts.

**Proposed approval:** adopt this three-increment order and begin R5A only after its provenance/store-version design is reviewed. R5B and R5C remain separately approved implementation gates. The plan contains no product code changes.


## Planning review

A fresh read-only builder check found no material unanswered questions for this staged design. The final schema decision above and initial-verification clarification are explicit planning decisions; R5C field syntax remains its own review artifact. Only this plan and an append-only research-log entry changed. No implementation tests were run for this documentation-only task.


## R5B implementation outcome — 2026-11-24 (uncommitted)

Section5 is implemented by `drivers.agent_runtime.DawAgentRuntime`, with ADR-042 Proposed. Real preparation/measurement, experimental diagnosis, audited Claude adapter and DAW coordinator are composed without changing CLI activation. Caller supplies a typed request factory consuming the verified plan/diagnosis definitions; all producer criteria and budgets remain explicit. Only external transports and clock/IDs are replaced in the numerical integration tests.

An additive `DawAgentBinding` freezes local operational settings without changing existing record shapes or store schemas. The per-run runtime folder owns plans/evidence/audit, separate from the run-store glob. Creation refuses existing artifacts; failures close resources and retain diagnostic files. Reopen reconstructs the exact recorded request, refuses configuration/evidence/manifest drift and never prepares or resends. Current accepted-checkpoint inventory supports a second suspension/export.

Fresh read-only review found one P2: native export timeline/render metadata were checked only after a provider decision. Four regressions failed first; creation now checks them before constructing external collaborators. Failure coverage also includes actual lost apply replies and acceptance acknowledgement loss through the real controllers/stores, unsupported stereo candidate recovery and missing-manifest refusal. These demonstrate engineering behavior only. Final commands and counts are recorded in the research log.

Rulings: a cohesive driver module extends the current composition responsibility; no new adapter or framework. The typed callback avoids prematurely implementing R5C's run JSON language. The additive binding avoids a needless incompatible schema bump. Failed startup leaves owned artifacts for diagnosis rather than automatically removing/reusing them. No changes to current experimental thresholds or requirements.

R5C remains the next reviewable schema/CLI increment; R6 live trials need separate authorization. `daw start`/`daw continue` still refuse. No live provider/device operation or musical-quality result is claimed.

## R5C detailed design — 2026-11-24 (planning only)

The R5C schema, CLI and test plan is in [2026-11-24-r5c-operator-cli.md](2026-11-24-r5c-operator-cli.md), Proposed, with an illustrative example file. It records the contradictions and decisions D1–D11 that need approval before coding. `daw start`/`daw continue` still refuse.
