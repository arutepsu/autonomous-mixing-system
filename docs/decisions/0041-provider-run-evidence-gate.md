# ADR-041 — Provider-run evidence gate and durable P1 associations (2026-11-23)

- Status: Proposed; R5A implementation, review pending.
- Source: owner approval of R5A in 2026-11-23-r5-agent-composition.md. No R5B/C or live/model action authorization.
- Decision: provider-backed runs explicitly carry immutable ProviderRunBinding (provider generation parameters, prompt/schema identities and the experimental diagnosis policy/measurement plan). An optional DecisionEvidenceSource is required for such runs; ordinary deterministic callers retain None and need no fabricated provider record. This is a declared composition contract, not runtime detection of arbitrary DecisionMaker implementations.
- Decision: the existing driver owns exact full-request reconstruction using R4. It reads the existing DecisionAuditTrail, compares the complete request (including public bytes and withheld local binding), and returns DecisionEvidenceVerified or a typed rejection. The core compares the proof's exact Situation/result, records it before using the result or charging an attempt, and only then preserves existing routing/retry/admission. Success stores the unwrapped DecisionResponse, failure stores PortFailure; no new unconstrained Success field is introduced.
- Decision: AnalysisResponse gains a trailing empty evidence tuple. P1 references contain observation, content hash, schema/version and prepared-manifest identity, never an absolute path. Directional analysis verifies its content-addressed file and manifest before returning and can verify them again without DSP. The existing router merges the references; the existing analysis trace event persists them. Level observations keep their existing provenance without invented P1 grid files.
- Decision: the same narrow evidence port has verify_run(snapshot), read-only across existing stores. It rechecks persisted P1 references and complete exchange/request/result/intent ordering before continuation. Missing or changed evidence refuses; it is never regenerated. An incomplete decision dispatch/proof/observation prefix does not count as complete evidence. Existing restart classification still exclusively decides resumability; verifying files alone never permits resume.
- Compatibility: strict record codecs check field sets, so defaulted fields do not migrate serialized data. New run stores use schema 2; schema1 is refused before decoding/writing, with unsupported status displayed read-only and recovery refused. No migration or row rewrite. Old files remain available for their matching historical software. Decision-exchange schema1 remains unchanged: its persisted record shapes are unchanged and old R3/R4 suites must pass.
- Rationale: persistence and decision-use authorization are distinct from projection, diagnosis, file rendering and coordinator composition. A small port/records and an existing-driver responsibility close that concrete boundary; there is no second loop, agent framework or evidence database.
- Consequences: provenance claims still rely on the configured adapters and local records; they do not authenticate the remote provider, physically align exports or prove perceptual masking. File verification is bounded/content-addressed but not race-free after its last check. Run/audit/file stores are not one atomic transaction. A completed audit without corresponding trace evidence after a crash never authorizes replay. Only local process-crash durability is tested, not power loss.
- Scope: configured thresholds/calibration unchanged. Production driver composition, JSON run configuration and CLI activation remain R5B/C. Existing CLI start/continue remain unavailable; status adds only safe unsupported/unavailable reporting. No provider/FL operations, installation, music study or criterion changes occur here.
- Supersedes: no accepted ADR. Extends the proposed R3 audit and ADR-039/040 persistence/operator behavior through explicit schema/versioning.

## Implementation notes (2026-11-23; uncommitted, for Codex review)

**Independent review.** A fresh read-only review was interrupted by a usage limit before it finished. Three findings it had already reported were fixed, each with a regression written first:
1. **Proof settings:** core compares the proof's provider, prompt and schema against the frozen run binding.
2. **Checkpoint caches:** observation caches must exactly equal the recorded analysis, values and alignment included.
3. **Status:** `daw status` reports a provider-bound run as `provider_evidence_unavailable` and claims no resumability, because R5A has no operator composition that opens every provenance store.

**Author review.** A correctly hashed evidence file still needed semantic comparison with its manifest. The analyzer now compares geometry, policy, frozen cells, noise reference and baseline source identities; seven regressions failed first.

**Final completeness pass against plan 4.1–4.4.** These regressions all passed on arrival, so they record covered behaviour rather than defects:
- changed intent, threshold, capability or history behind unchanged IDs;
- a local oversized request leaves no exchange;
- a response-recording failure stays pending and unverified;
- the decision-exchange schema-1 record-shape pin, with a round trip;
- continuation verification of recorded P1 evidence without regeneration.

**Gaps found by mutation probes.** Three probes first survived, so the decisions they exercised had no permanent test. Four regressions were added, which pass on the current code:
- an evidence source vouching for a foreign input;
- an evidence source vouching for a foreign result;
- a missing evidence reference for a returned P1 observation;
- a state source hash differing from the recorded evidence.

One further regression covers a provider continuation whose `verify_run` reports a problem: `resume_daw` refuses before admission, with no FL message or trace event.

**Deviation.** Provider continuation with unverifiable provenance raises `ValueError` before admission and any effect, instead of returning a typed `DawContinuationRefused`. It fails closed, but the operator-facing refusal shape remains R5C work.

**Evidence.**
- **R5A tests:** 62 passed (warnings as errors).
- **Wider focused set:** 437 passed.
- **Architecture:** 276 passed.
- **Full suite** (`uv run --locked --extra agent pytest -q`): 7,097 passed, 4 skipped.
- **Mutation probes:** 15 of 15 caught, rerun after final formatting; every file restored byte-identically.
- **Remaining checks:** the fixture verifier, ruff check and format, CLI help, lock check and diff check are clean.
- No provider call, FL operation or production composition.


## Checkpoint review — 2026-11-24

A fresh combined-checkpoint review found two additional provenance gaps. Schema4 evidence reopening now checks required transform/count-hash metadata, and read-only continuation validation enforces the frozen plan digest on recorded P1 associations before trusting the configured analyzer. Five regression cases failed before the fixes; removing either fix is caught. The positive restart fixture was corrected to bind the actual measured plan instead of a placeholder digest. No scientific policy, DSP kernel, CLI activation or calibration scope changes result. Status remains Proposed.
