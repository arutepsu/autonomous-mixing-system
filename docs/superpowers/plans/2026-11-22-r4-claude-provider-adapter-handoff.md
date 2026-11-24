# R4 Claude provider adapter — implementation handoff

> **For agentic workers:** Use superpowers:executing-plans; use subagents only when separately delegated. Implement tests first. Leave changes uncommitted for Codex review.

**Goal:** Implement the existing DecisionMaker port using one explicitly configured Claude Messages request, with the reviewed R3 packet, strict parser and mandatory exchange audit.

**Architecture:** Provider configuration, prompt and synchronous transport stay inside adapters/decision_agent. ClaudeDecisionMaker receives the transport, DecisionAuditTrail, identity source and clock explicitly. No concrete journal import, core-loop change or provider SDK type in core.

**Tech stack:** Existing Python and optional agent dependencies. Check the locked Anthropic SDK (currently 1.13.0) against its installed public interfaces before coding transport. No dependency upgrade merely to match current web examples.

**Spec:** docs/superpowers/plans/2026-11-20-diagnosis-and-decision-adapter.md, R4 and sections 4–5; the reviewed R3 implementation and handoff. Starting HEAD: 6b08451. Read current instructions/status first and preserve other contributors' changes.

## Global constraints

- Implement transport and validation with synthetic contract fixtures and simulated HTTP only. No real credentials, provider call, FL Studio call, installation, saving, exporting or commit.
- The unfinished owner-created dataset does not block R4. Calibration and production diagnosis remain separate. No fabricated diagnosis, decision-file substitute or level-as-masking shortcut.
- Preserve DecisionMaker.decide(Situation) -> PortResult[DecisionResponse]. R5 owns coordinator/audit-reference composition and CLI enablement. daw start/continue remain unavailable.
- The AI proposes. Existing admission, budgets, verification and acceptance retain authority. A valid JSON response is not an executed or accepted correction.
- Preserve hidden-reference isolation. Send only the R3 public packet, versioned instructions, response schema and explicit generation settings. Local binding, WithheldDetail, paths and evaluation data never enter the request.
- R3 designated public text remains verbatim; its suppliers must keep it public. Do not claim generic secret/path sanitization.
- No tools, MCP, browsing, file uploads, private thinking, conversation service, streaming or fallback model/provider in this slice.
- SDK optionality stays real: importing the package and running base CLI help must work without the agent extra. No credential/environment access at import time.

## Review focus

1. Hidden SDK retry or resend of a pending exchange causes an uncounted provider call.
2. Audited input differs from the application request actually dispatched, or the local binding leaks.
3. A plausible answer escapes before its response and completion are durably recorded.
4. Refusal, truncation, transport failure or malformed envelope is disguised as model abstention.
5. Replay generates different IDs or accepts changed situation/configuration under the same call key.

## File map

Create focused modules under src/autonomous_mixing/adapters/decision_agent/: config.py (explicit immutable configuration), prompt.py (versioned public request construction), transport.py (SDK boundary), claude.py (audit orchestration). Avoid a general multi-provider framework.

Create tests/unit/test_claude_transport.py and test_claude_decision_maker.py; extend existing R3 fixtures only where necessary. Update exact architecture inventories and isolation tests. Reuse request.py, response.py and DecisionAuditTrail without weakening their checks. Any necessary core audit-format extension must be minimal, explicitly documented and reviewed; do not hide missing evidence in an unbound side file.

## Task 1 — Explicit request configuration and prompt

Interfaces: adapter-local ClaudeConfig and a request builder consuming Situation through build_decision_packet(situation, limits=...). Produce a DecisionExchangeRequest with an explicit ProviderRequestIdentity, prompt/schema versions and complete public application request text.

- [ ] Write failing tests for missing/invalid model and API profile, timeout/token/byte limits, non-finite parameters, unsupported options and credentials appearing in serialized audit evidence.
- [ ] Implement validated explicit model/API/generation configuration. No silent latest-model default. Credentials are separate from serializable configuration and injected at construction/factory time.
- [ ] Version the prompt. Explain absolute application gain, evidence citations, all guards, NoChange and abstention. Request a concise public rationale/uncertainty, never private reasoning. Instructions cannot authorize unadvertised actions or assert calibrated masking diagnosis.
- [ ] Include the actual provider schema and complete request fields in the request fingerprint, not merely packet.payload_sha256. Preserve packet plus local binding unchanged locally.
- [ ] Test exact public projection, all producer criteria, current/history distinction, and absence of technical paths/local binding. Oversized input fails explicitly; no truncation.
- [ ] Run focused tests and confirm no R3 regression.

## Task 2 — Single-dispatch synchronous transport

Interfaces: a narrow adapter-local transport accepting the prepared public request and returning bounded received-response evidence or a typed transport failure. Define exact DTOs/signatures in the implementation report. SDK exceptions and objects do not cross this boundary.

- [ ] Inspect the locked SDK's supported Messages, structured-output, raw-response and HTTP injection interfaces. Record compatibility findings. Current docs are references, not proof of lockfile compatibility.
- [ ] Write red tests using an injected mock HTTP backend with the real SDK boundary, in addition to orchestration doubles. Assert exactly one HTTP attempt, no automatic retry and no redirect-following to another endpoint. Configure max_retries=0 explicitly.
- [ ] Pin the audit boundary before implementation: request_text is the complete UTF-8 public application body dispatched. Test the captured outgoing body against it. If SDK serialization changes bytes, capture its prepared body before dispatch using a supported HTTP seam; do not label a reconstructed body byte-identical. Resolve this before calling Task 3 complete.
- [ ] Use supported structured-output configuration with an explicitly versioned schema. If a provider-compatible schema needs transformation, record the actual transformed schema in the request and keep the R3 local validator authoritative. No auto-repair/re-prompt or SDK parse helper that interprets decisions before audit persistence.
- [ ] Bound timeout and request/response sizes. Timeout must respect the remaining runtime allowance; an exhausted/cancelled call dispatches nothing. Record the limitation of any timeout that cannot guarantee a total wall-clock deadline. Do not read arbitrarily large bodies before enforcing a claimed byte bound.
- [ ] Distinguish response evidence: retain exact received public provider body and its hash, plus exact selected decision-text bytes passed to the R3 parser. R3 body currently means decision text; keep the complete bounded provider envelope in fingerprinted response metadata (or a reviewed minimal audit extension). Never call SDK reserialization raw wire bytes. No authentication headers or unrestricted exception/header dump.
- [ ] Accept a normal complete envelope only under the explicitly supported stop/content profile. Reject unexpected tool blocks, empty/multiple incompatible content blocks, unknown stop reasons and malformed envelopes without silently dropping content. Record received evidence first. Token exhaustion is incomplete; documented refusal is refusal, never synthesized Abstain.
- [ ] Preserve requested/resolved model, provider request ID, SDK/API versions, stop reason, explicit settings, UTC/elapsed time and usage. Missing usage stays unknown, never zero. An engineering replay record supports traceability, not guaranteed identical model output.
- [ ] Test timeout/connect/rate-limit/server/auth/bad-request errors, partial or oversized bodies, missing metadata, refusal/truncation and malformed envelopes. Expected provider failures are typed; programming errors still propagate.

Failure mapping: transient connection/timeout/rate-limit/server failures may return UNAVAILABLE with retryable=True only after the exchange failure evidence is recorded. Authentication/configuration failures, refusal and invalid response are non-retryable. A NOT_RECEIVED record must not be blindly passed through the R3 parser if that would erase transport retryability. No retry/sleep/second dispatch inside decide; the coordinator owns separately charged retries.

## Task 3 — Audited ClaudeDecisionMaker

Interface: ClaudeDecisionMaker.decide(Situation) implements the existing port; inject audit, transport, clock, identities and limits. Consume parse_decision_response(response, request=request, situation=situation, identities=identities, limits=response_limits).

- [ ] Write failing integration tests with the real R3 exchange store on temporary storage, real packet builder/parser and labelled transport responses.
- [ ] Derive DecisionCallKey from run_id and already-charged usage.decision_calls. Check audit.read before creating a new exchange. A completed exact matching call returns its stored result/identities without network. Compare packet, local binding, prompt, schema and provider configuration; changed inputs conflict. Reuse original generated exchange ID/timestamp for replay validation instead of manufacturing a conflicting request.
- [ ] Pending or response-only exchanges never authorize resubmission. Unavailable reads fail closed. Do not add automatic incomplete-exchange recovery in R4.
- [ ] For a new exchange: build/validate request, begin, require permits_dispatch, dispatch once, record_response, interpret/classify, finish, return. Only a newly recorded matching PENDING request permits dispatch.
- [ ] On received malformed output, record public response before envelope/decision interpretation. Finish INVALID_RESPONSE before returning. For a provider failure, record NOT_RECEIVED or the received evidence as appropriate, then finish PROVIDER_FAILURE. No fake body or fake decision.
- [ ] Successful Proposal, NoChange and Abstain all require a stored VALID_DECISION completion containing the exact locally assigned decision/action IDs. A recording refusal/unknown at any phase releases no usable decision and permits no second dispatch. Return a non-retryable recording failure; preserve the pending/response evidence for later inspection.
- [ ] Validate acknowledgements against the exact expected record, not merely their success type. Unexpected exceptions propagate; never turn a programming error into a retryable provider outage.
- [ ] Test order explicitly: begin failure means zero sends; response persistence failure means zero parser/ID calls; finish failure means no returned decision. Test wrong acknowledgement/call/fingerprint, pending restart, completed replay, changed configuration, public refusal vs model abstention, and malformed/deep/huge-number responses through the real R3 parser.
- [ ] Run focused tests with warnings as errors.

## Task 4 — Verification and handoff evidence

- [ ] Update architecture import edges/isolation checks: decision_agent imports its own modules and core records/ports, never concrete journal, core admission/loop, rendering or device code. Optional SDK import is local to the transport factory/boundary.
- [ ] Run targeted mutation checks: remove pending gate; enable SDK retry; dispatch before begin; interpret before response recording; bypass finish acknowledgement; send local binding; accept changed packet on replay; treat refusal/truncation as complete. Each non-equivalent mutant must fail a permanent test. Restore source byte-identically.
- [ ] Run uv run --locked focused suites with -W error, then full suite; ruff check ., scoped format check, uv lock --check, CLI help and git diff --check. For SDK tests use the locked agent extra; report the actual command/environment and do not silently skip all real transport tests.
- [ ] Append a research-log entry and update the staged plan's R4 status. Keep R2 calibration/diagnosis and R5 composition unimplemented. Only add a proposed ADR if an actual new architectural decision needs one.
- [ ] Report changed files, exact interfaces/configuration, SDK/schema compatibility, red/green regressions, failure/replay behavior, request/response byte boundaries and remaining limits. Leave everything uncommitted. No live trial.

## Completion boundary

R4 is ready for review when the real provider transport has passed mock-HTTP tests and ClaudeDecisionMaker has passed real-audit/real-parser tests without any external call. This establishes implementation behavior only. It establishes neither model quality nor diagnosis validity, and it does not enable an autonomous mixing run.

References checked 2026-11-22: https://platform.claude.com/docs/en/api/sdks/python and https://platform.claude.com/docs/en/build-with-claude/structured-outputs. Verify the locked SDK source as well; the published docs can describe newer behavior.
