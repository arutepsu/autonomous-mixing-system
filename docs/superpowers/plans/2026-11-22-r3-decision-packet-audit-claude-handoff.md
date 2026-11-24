# R3 decision packet, strict parser and exchange audit — implementation handoff

> **For agentic workers:** Use superpowers:executing-plans, or subagent-driven-development when separately delegated. Implement tests first and leave the result uncommitted for Codex review.

**Goal:** Give the future provider a bounded, complete view of permitted runtime evidence and producer intent; validate its proposed decision; preserve reproducible public request/response evidence, including failures.

**Architecture:** New immutable packet/exchange records and an audit port in core. Explicit projection/parsing in `adapters/decision_agent`; local exchange persistence in `adapters/journal`. Existing decision, action-admission and verification contracts remain authoritative. No provider SDK, network or coordinator integration in R3.

**Tech stack:** Existing Python, canonical JSON, hashlib and local SQLite/record-codec conventions already used by the journal component. No new dependency by default.

**Spec:** R3 and sections 4-5 of `docs/superpowers/plans/2026-11-20-diagnosis-and-decision-adapter.md`, including the owner's thesis-focus and parallel-development clarifications. Respect the current engineering conventions, requirements FR-06/FR-07/FR-11/FR-12 and hidden-reference boundary SR-10.

## Starting tree and scope

HEAD was `0242c99` when this handoff was prepared. There are uncommitted documentation corrections and the reviewed offline acceptance-authority fix, including `test_offline_acceptance_exceptions.py`. Preserve them; do not reset, format unrelated files, or silently combine their behavior changes with R3. Read current repository status/instructions before editing.

The dataset is being developed in FL Studio. Neither a finished corpus nor paid/professional listeners are prerequisites for R3. Labelled unit-test contract data is allowed; fabricated production diagnosis is not. No code here establishes calibration, mixing quality or a real model's performance.

**Out of scope:** provider calls/SDK transport (R4), diagnosis generation/calibration (R2), core-loop/coordinator injection or CLI enablement (R5), media reads/rendering, DAW calls, installation, experimental runs and commits.

## Review focus

1. A plausible response cites the wrong revision, candidate or historical observation: reject before releasing a valid DecisionResponse.
2. Technical feedback/history leaks artifact paths or hidden data: explicit projection, not generic serialization, must control every outbound field.
3. Metadata or history is silently truncated: fail explicitly rather than change the decision problem.
4. Failed/malformed calls disappear from the evidence trail: request, response and validation records must have separate append-only phases.
5. Pending or conflicting calls become permission to resend: persist/validate call identity and never treat a replay as dispatch authorization.

## Task 1: Packet and exchange contracts

**Create:**
- `src/autonomous_mixing/core/records/decision_exchange.py`
- `src/autonomous_mixing/core/ports/decision_audit.py`
- `tests/unit/test_decision_exchange_contract.py`

Follow record immutability/import rules. Core records may contain validated immutable data/canonical JSON text, never mutable dict/list payloads, SDK objects, filesystem handles or audio arrays. Use configurable resource limits; they are engineering limits, not musical thresholds.

**Required interfaces:**
- `DecisionCallKey`: `(run_id, decision_call_ordinal)`, where the ordinal equals charged `Situation.usage.decision_calls` and is positive.
- `DecisionPacket`: exact versioned outbound data/schema text and its hash; local binding to the exact call/state/context/controller; immutable alias bindings and their fingerprint. Local-only bindings/aliases are not the outbound JSON.
- Pending exchange, recorded public response and completed exchange-reference records. Each phase binds the same call key, exchange ID and request fingerprint.
- `DecisionAuditTrail.begin`, `record_response`, `finish`, `read`, all with typed results. An unavailable record is not a stored/completed one.

Do not change `DecisionMaker.decide(Situation) -> PortResult[DecisionResponse]` or add an optional provenance field only to successful decisions. R4 uses the audit collaborator; R5 will require a matching completed reference before using a provider result.

The R3 audit request envelope must be able to retain the exact public application request text supplied by future R4, plus prompt/schema/provider-configuration identity. The data packet alone is not the entire API request. Do not invent provider headers or claim to capture server internals.

- [ ] Write red tests for call/state/context bindings, immutable payloads, invalid hashes, byte bounds and status invariants.
- [ ] Require response/finish phases to belong to an existing matching request. Completion cannot hide an unrecorded response when a response was received.
- [ ] Define typed completion categories for valid decision, invalid response and provider/recording failure; do not fabricate an Abstain for a broken call.
- [ ] Record locally assigned DecisionId/ActionId or the validated final decision in completion evidence so replay can recover exact identities, not merely similar semantics.
- [ ] Implement and run focused contract tests; update architecture/public-contract inventories explicitly.

## Task 2: Explicit provider input projection

**Create:** `adapters/decision_agent/__init__.py` (docstring only), `request.py`, and `tests/unit/test_decision_packet.py`.

**Interface:** `build_decision_packet(situation, *, limits, ...) -> DecisionPacket`, with any alias-strategy dependency explicit and versioned. Confirm the final signature in the implementation report.

Project only the approved fields:
- producer roles, qualitative preferences, protected tracks and assertion provenance;
- current scope, observations/units, unavailable reasons, diagnosis coverage/candidates/evidence and uncertainty;
- every applicable operational objective and guard threshold, including exact comparator/baseline semantics;
- advertised gain actions/bounds, protected targets, remaining budgets and absolute application-gain semantics;
- complete prior-call feedback needed to understand outcomes, including rejection, verification, acceptance, recovery, NoChange and abstention.

Aliases must remain stable for the same runtime entity across calls/revisions/restart. Use a documented deterministic run-scoped opaque mapping or persisted mapping, with collision/conflict validation; never renumber previous entities when new ones appear. Local alias maps bind typed source IDs but are not sent to the provider. Historical evidence is identifiable as historical; it cannot be cited as current evidence by the parser.

Do not use `asdict(Situation)` or the generic record codec as the outbound projector. Omit audio locations, project filenames, raw track filenames, root bindings, hashes/IDs encoding hidden condition labels and arbitrary technical error text containing paths. Project error codes and safe semantic feedback explicitly; preserve full errors locally in audit evidence where allowed. Treat explicitly supplied producer text as data, never policy instructions. Do not claim automatic secrecy if an owner deliberately places secret data in public preference text.

Preserve unknown roles and unsupported diagnosis. Empty available actions/candidates are not success. A packet must not invent evidence or numerical goals from a vague preference such as “cleaner” or “more dominant.”

- [ ] Write red tests for full criteria/guard/context/capability/history projection and stable aliases across reordered/extended history and restart.
- [ ] Add sentinel paths/hidden fields to source metadata and failure messages and inspect the actual outbound JSON. Verify no filesystem/provider/tool capability is granted.
- [ ] Test bounds/units and the difference between absolute application gain and physical DAW fader coordinates; normalized fader values are not a model action.
- [ ] Reject oversize input explicitly. No silent history/criteria/uncertainty truncation or fallback summary.
- [ ] Save prompt/data/schema identity separately from local call-binding identity; both must reproduce exactly.

## Task 3: Strict structured-response parsing

**Create:** `adapters/decision_agent/response.py` and `tests/unit/test_decision_response_parser.py`.

**Interface:** `parse_decision_response(public_response, *, packet, situation, identities, limits) -> PortResult[DecisionResponse]`.

The R3 input is the public decision text/envelope supplied by a future transport, not an SDK type. Provider stop-reason/HTTP mapping belongs to R4, but the parser must reject explicitly incomplete/refused envelopes rather than treating them as completed decisions.

Define a versioned discriminated schema:
- `propose_action`: exactly one supported SetTrackGain proposal (target alias and absolute gain in dB), nonempty current candidate/evidence aliases, expected effect and required justification/uncertainty/assumptions/limitations;
- `no_change`: explicit reason, with no implied verification or success;
- `abstain`: an existing AbstentionReason plus rationale; missing_context requires nonempty questions.

Bind project/revision/context/controller locally from the exact packet/Situation. Generate DecisionId and ActionId locally. The model may not supply/override those fields, policy, method, criteria or budgets. Check packet/local binding and alias map before interpreting the response. Invoke existing `validate_decision_response` on a successful constructed record.

Reject invalid UTF-8, duplicate JSON keys, extra keys, Markdown/fenced prose, multiple objects/proposals, trailing data, NaN/Inf, bool-as-number gain, bad numeric types, duplicate/unknown/historical-only references, wrong variants and unsupported operations. No JSON repair, fuzzy alias matching, clamping, action substitution or invented evidence. Keep valid but unapproved proposals separate from admitted actions; the existing coordinator gates still decide evidence sufficiency, protection and runtime permission.

- [ ] Write red tests for all three valid outcomes, missing context and each malformed/reference/binding class above.
- [ ] Prove a structurally valid model proposal is never an execution receipt, admitted action or permission to call a tool.
- [ ] Test insufficient evidence without changing the policy/flag or fabricating a replacement candidate; use labelled fixtures only.
- [ ] Record exact identity assignment for audit replay; never ask for private chain-of-thought, only concise public justification.
- [ ] Invalid responses return non-retryable INVALID_RESPONSE. Unexpected programming errors still propagate according to port conventions.

## Task 4: Local append-only exchange evidence

**Create:** `adapters/journal/decision_exchange.py` and `tests/unit/test_decision_exchange_store.py`. Keep the store in the existing journal component; reuse its public record codec/settings patterns where compatible rather than inventing a general persistence framework.

Use one local SQLite exchange store per run, separate from the run/acceptance database; explicit schema version 1, append-only phases and fingerprints checked on reads. Follow existing local process-crash/ownership conventions without claiming network-storage or power-loss guarantees. Do not alter existing run-store rows/schema or silently migrate them.

Store: exact request text, alias/local binding, prompt/schema versions, requested/resolved provider/model/configuration identity when supplied, UTC/elapsed timing, safe request/stop/usage metadata, exact received public response/error facts and final parser/result classification. Credentials and authorization headers are excluded. No response received is its own recorded fact, not a made-up empty valid response.

`begin` persists pending before a future dispatch; `record_response` persists received public bytes before parsing; `finish` completes the interpretation. Store both valid and invalid outcomes. Limit encoded sizes before writes, with symmetric reader bounds. Failure to persist means no usable success. A pending exchange after crash is incomplete and must not be automatically resubmitted.

- [ ] Write red tests for round trips, tampering, partial/failed writes, bounds, schema mismatch, duplicate identical requests and conflicting reuse of call/exchange IDs.
- [ ] Identical phase replay is idempotent; different content conflicts. A completed exchange replays recorded evidence/results without a second provider dispatch; a pending one is not dispatch permission.
- [ ] Test failures before/after commit acknowledgement without exposing uncommitted rows; follow the existing verified-reopen pattern.
- [ ] Test closing/reopening and reconstructing the same public request/response/validation outcome, including locally assigned IDs. Never recreate missing evidence from a plausible response.
- [ ] Keep successful and failed calls discoverable by `(run_id, decision_call_ordinal)` for R5's future mandatory check.

## Task 5: Review and verification

- [ ] Update architecture inventories and any record-codec allowlist explicitly; no adapter imports core admission/loop stages or another component's internals. Decision projection/parsing may use core records/ports, not admission rules.
- [ ] Run new focused tests with warnings as errors; regression-check existing decision/gate/history, journal/codec and architecture tests.
- [ ] Run full locked suite, ruff check/format, CLI help, lock consistency and diff checks.
- [ ] Use a small set of meaningful mutation probes: omit a guard/history item; permit a historical alias; accept duplicate/extra keys; overwrite an audit phase; complete without required response evidence; replay a pending request as dispatch permission. Restore byte-identically or use isolated in-memory mutations.
- [ ] Update the owning plan and append the research log with actual checks, scope and limitations. Propose an ADR only for a genuine architecture decision, preserving accepted ADRs.
- [ ] Return exact files/interfaces, test results, audit replay evidence and deviations. Leave changes uncommitted for review.

**Completion boundary:** a tested packet/parser/audit foundation ready for R4. No real Claude answer, calibrated masking diagnosis, live mixing correction or autonomous-run capability is claimed.
