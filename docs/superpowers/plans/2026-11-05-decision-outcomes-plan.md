# Decision Outcomes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Execute sequentially yourself; no subagents for this task.

**Goal:** Add explicit NoChange and independent action justification, enforce candidate references and require missing-context questions.

**Architecture:** Frozen decision records in core.records.decision; request-aware cross-references in core.ports.decision. Existing admission calls response validation; no new stage or loop is added.

**Tech Stack:** Existing Python >=3.11, pytest, Ruff, uv. No dependency changes.

**Spec:** docs/superpowers/specs/2026-11-05-decision-outcomes-design.md, approved schema 2026-11-05; read the whole document and sections 12–15 of the control-loop attempt contract.

## Global constraints

- Follow CLAUDE.md, applicable AGENTS.md and required project references; preserve unrelated work.
- Core stdlib/core only; records graph remains acyclic; package initializers stay docstring-only.
- No scientific method, threshold, musical assumptions, confidence calibration, hidden data or evaluation claims. Synthetic metadata fixtures are contract tests only.
- Current decisions/proposals remain proposals, not authorization. No semantic evidence gate, capability enforcement, execution, loop, driver, provider or persistent trace work.
- No installs, dependencies, commits or pushes. Stop for Codex review.
- The accompanying owner prompt approves execution; no additional plan-approval pause is needed.

## Review focus

- Missing-context positive fixtures must provide questions; do not weaken the new constructor rule to keep them passing.
- Proposals used with response validation must have candidates backed by the actual fixture observations, not merely correctly typed invented IDs.
- Selected candidate evidence is a subset requirement; extra evidence is allowed and unselected candidates impose no requirement.
- NoChange may be valid even when criteria are unmet; this increment assigns no success and performs no criteria evaluation.
- Diagnostic sufficiency and action justification stay separate; nonblank uncertainty text is not a confidence calibration or an execution gate.

## Owned paths

Production: src/autonomous_mixing/core/records/decision.py and src/autonomous_mixing/core/ports/decision.py only.

Tests: new tests/unit/test_decision_outcomes.py; existing tests/unit/test_action_decision.py, test_application_contracts.py, test_action_admission.py, test_execution_records.py, test_execution_preflight.py for proposal/context fixture migration and retained behavior; tests/unit/test_public_contracts.py for intentional contract assertions; tests/architecture/test_core_records.py for inventory additions only.

Docs: docs/architecture/overview.md (in-place), docs/research/research-log.md (append-only). This implements the approved lifecycle within existing ADR-027 architecture; no new architecture decision is proposed. If policy requires an ADR, report the exact requirement before expanding scope.

Planning documents: status/checkbox updates only. If aliases, additional callers or another required file are found, identify the path and reason before expanding ownership. No initializer or dependency files.

## Task 1 — decision outcome contract and references

### Step 1 — baseline and red tests

- [ ] Record actual branch/HEAD/status; preserve any uncommitted 2D work. Do not assume its commit exists or commit it yourself.
- [ ] Read approved spec and current records, validators, admission and test inventories. Search constructors and aliases, outcome assumptions and MISSING_CONTEXT fixtures.
- [ ] Run baseline tests, lint, formatting, CLI help and lock check; use uv run --locked when available. Repository tools are an allowed fallback without installation; explicitly report absent checks.
- [ ] Write focused tests for exact ActionJustification, ProposeAction and NoChange fields; wrong types, blanks, duplicates, required arguments, preserved values, frozen records, extended Decision union and all context cases.
- [ ] Write response tests for missing selected candidates, missing any selected candidate's evidence, multiple candidates, valid additional evidence, ignored unselected candidates, non-actions and unchanged controller/state/context checks. Test the lower validator retains its old signature and does not claim diagnosis validation.
- [ ] Add direct admission coverage for valid NoChange → NOT_AN_ACTION without bypassing earlier reference/policy checks.
- [ ] Run new tests before implementation and capture meaningful expected failures; distinguish fixture errors from intended red evidence.

### Step 2 — implementation and explicit fixture migration

- [ ] Implement the exact spec in the two owned production modules. No fallback defaults, new enums, outcome canonicalization, numeric confidence or natural-language heuristics.
- [ ] Add ActionJustification and NoChange to the records inventory; do not widen import edges or graph rules.
- [ ] Migrate each existing ProposeAction explicitly with candidate IDs and independent justification. For request-aware tests, supply labelled diagnosis candidates with matching observation provenance; preserve the focus of existing protection, bounds and preflight tests.
- [ ] Preserve previous assertion bodies except intentional outcome-contract changes. Positive enum coverage supplies questions for MISSING_CONTEXT; add rejection coverage for empty questions rather than deleting that case.
- [ ] Extend public-contract checks deliberately to pin the new constructors and Decision outcome union while retaining every unrelated baseline.
- [ ] Run focused, migrated, architecture and public-contract tests. Restore intended negative-test paths when stronger validation would otherwise make them fail for an unrelated fixture reason.

### Step 3 — sensitivity, documentation and verification

- [ ] On final code, temporarily remove each of: missing-context nonempty check; candidate existence check; selected candidate evidence-inclusion check. Demonstrate relevant tests fail, restore each file byte-identically, and rerun selected tests. Do not leave mutations in the working tree.
- [ ] Update overview with exact outcome semantics, constructor API break and deferred semantic gate. Append one evidence-attributed research-log entry; leave earlier entries unchanged. No requirement status changes.
- [ ] Run uv run --locked pytest -q -rs; uv run --locked ruff check .; uv run --locked ruff format --check src tests; uv run --locked autonomous-mixing --help; uv lock --check. Record actual results and platform skips; if uv unavailable, use existing repository tools and disclose the unrun lock check.
- [ ] Review scope and diff: other production files, package initializers, dependency files, enum strings and existing unrelated contracts unchanged. Preserve prior working-tree work.
- [ ] Return changed paths, exact signatures, red/green and mutation evidence, final checks, deliberate API changes, documentation, deviations and remaining gaps. Stop uncommitted before the semantic gate or next task.
