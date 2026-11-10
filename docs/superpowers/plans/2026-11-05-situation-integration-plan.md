# Situation Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Execute sequentially in the current session; no subagents or parallel work for this task.

**Goal:** Connect diagnosis, capability and compact-history records to the immutable input of one decision call.

**Architecture:** Extend core.records.decision.Situation using existing lower-level record owners. No stage, adapter or run imports enter decision records. Existing outcome semantics and execution behavior remain unchanged.

**Tech Stack:** Existing Python >=3.11, frozen dataclasses, pytest, Ruff and uv; no dependencies added.

**Spec:** docs/superpowers/specs/2026-11-05-situation-integration-design.md (approved by owner in conversation on 2026-11-05).

## Global constraints

- Read CLAUDE.md, applicable AGENTS.md, engineering conventions and required project references before editing.
- Retain the approved lifecycle and uncertainty separation from the control-loop attempt contract.
- Sources and original WAVs remain immutable. No detector, scientific threshold, provider, actual loop or evaluation data.
- Records only import records/stdlib, remain immutable, and their dependency graph remains acyclic. Package initializers stay docstring-only.
- Existing outcome definitions, admit_action, preflight, renderer, budget behavior and run configuration checks are unchanged.
- This plan is approved for execution by the accompanying owner prompt; do not ask again because a previous draft lacked approval.
- Stop uncommitted and unpushed for independent review. Do not install tools or dependencies.

## Review focus

- Old fixtures with decision_calls=0 must become valid charged-call snapshots; do not weaken history validation.
- Historical superseded observations must stay valid history while failing if injected into current measurements.
- Empty diagnosis coverage or available actions must not imply sufficient evidence, success or permission.
- Scope containment and capability scope equality are different rules; retain exact endpoints and existing equality semantics.
- Situation cannot prove agreement with omitted authoritative run configuration, policy or renderer; document rather than fabricate that guarantee.

## Owned paths

Production: src/autonomous_mixing/core/records/decision.py only. Private local containment helper allowed; do not import another component's private helper.

Tests: new tests/unit/test_situation_integration.py; existing tests/unit/test_application_contracts.py, test_action_admission.py, test_execution_preflight.py for explicit fixture migration; tests/unit/test_public_contracts.py for the deliberate Situation constructor baseline extension; tests/architecture/test_core_records.py for exact dependency edges.

Docs: docs/architecture/overview.md in place; docs/research/research-log.md append only. This increment implements existing approved architecture; no new ADR unless an actual policy requirement demands one. Report that requirement before expanding file ownership.

Planning documents may only gain checkbox/status updates. Preserve other work. If fresh inventory finds another actual caller, report its path and required change before expanding scope.

## Task 1 — enriched decision input (one reviewable deliverable)

### Step 1: baseline and failing contract tests

- [ ] Record branch, HEAD and working-tree paths; preserve any feedback-record changes already present. Do not assume they were committed or commit them yourself.
- [ ] Read the approved spec and current Situation, diagnosis, capabilities, history, criteria, run configuration and architecture owners. Search src/tests for every Situation call and aliases.
- [ ] Run baseline full tests, lint, CLI help and lock check through uv run --locked / uv lock --check when available. Record actual tool paths and any unavailable checks. Existing repository Python is an allowed fallback without installation.
- [ ] Add focused tests for the six required fields appended after state, context, controller, observations, usage, limits: run_id: RunId; scope: AnalysisScope; criteria: AcceptanceCriteria; diagnosis: DiagnosisReport; capabilities: ActionCapabilityView; history: DecisionHistory. All have no defaults.
- [ ] Cover every acceptance case in the approved spec: positive first/later calls, preserved identities, types/omissions, project/revision/context/run mismatches, exact capability scope, every criteria collection's track/time/band boundaries, diagnosis evidence, current and historical source handling, explicit empty inputs, count/chain constraints.
- [ ] Run the focused tests and record expected failures before production implementation. These are contract-only metadata fixtures, not artificial musical demonstrations or evaluation results.

### Step 2: implementation and existing caller migration

- [ ] Add the six fields and type checks, retaining old checks. Use existing validate_diagnosis_references and validate_decision_history exactly as specified.
- [ ] Implement criteria selector containment locally: known tracks, subset of run tracks, contained time, and explicit contained band when outer band is restricted. No metric, unit or Nyquist claim without the missing specs.
- [ ] Check capabilities against state project/revision, context identity/version and exact scope; validate scope tracks. Do not regenerate or authorize it.
- [ ] Validate history against the independent run_id and current usage. No clock, charging, dispatch or termination policy added.
- [ ] Migrate the three existing caller test files with explicit valid metadata. Empty coverage may preserve tests unrelated to diagnosis; state clearly that it establishes no scientific conclusion. Preserve every original behavioral assertion and negative case intent.
- [ ] Extend only the Situation signature baseline. Leave other public signatures and unions unchanged.
- [ ] Add only capabilities, diagnosis, feedback and verification to decision's records-edge allowlist, alongside existing edges. Do not widen to all records or relax graph/inventory checks. No new public record names are needed.
- [ ] Run focused and migrated tests, architecture tests and public-contract tests.

### Step 3: verification, documentation and handoff

- [ ] Demonstrate regression sensitivity on the final code: temporarily omit capability revision equality; omit diagnosis reference validation; omit history validation; relax one criteria containment bound. Each selected test must fail. Restore byte-identically after every probe. Never leave mutations in the diff.
- [ ] Update overview with exact input fields, constructor API break, current-charge convention and deferred enforcement. Append one research-log entry with attributed evidence and limits; do not rewrite prior entries.
- [ ] Run uv run --locked pytest -q -rs; uv run --locked ruff check .; uv run --locked ruff format --check src tests; uv run --locked autonomous-mixing --help; uv lock --check. If uv is unavailable, use repository tools and explicitly report the missing lock check; dependency files must remain unchanged.
- [ ] Review the whole diff against owned paths. Prove other production modules, package initializers and dependency files unchanged. Confirm records acyclicity and no test assertions were removed to hide failures.
- [ ] Stop with changed paths, exact final constructor signature, red/green and mutation evidence, native versus injected evidence if relevant, final checks/skips, documentation changes, deviations, open gaps and git status. No commit, push or subsequent task.
