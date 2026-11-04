# Control Loop Budget Gate Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this single increment serially. Work only within the listed ownership. Follow test-first steps and leave the diff uncommitted for Codex review; the owner commits after review. Do not start the next increment.

**Goal:** Implement a pure, typed check of whether the next loop operation has budget, including the proposed immutable charge and the render recovery-capacity prerequisite.

**Architecture:** Core-facing enums and frozen outcomes live in `core.records.budgets`. One pure function in `core.loop.budgets` reads existing limits/usage and supplied elapsed time. It proposes a charge; it performs no dispatch, journal acknowledgement, recovery reservation or external operation.

**Tech stack:** Existing Python >=3.11, stdlib dataclasses/enums/typing, pytest, Ruff, uv. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-04-control-loop-lifecycle-design.md` and `docs/superpowers/specs/2026-11-04-control-loop-attempt-contract.md`, especially sections 25–26 and 29. The owner approved the combined design on 2026-11-04. Concrete names and signatures below are this increment's implementation decisions.

## Global constraints

- Preserve OBSERVE → DIAGNOSE → PLAN / DECIDE → ACT → EVALUATE → STOP / ITERATE / ROLLBACK as the standing lifecycle. This increment supplies enforcement groundwork, not a working loop.
- Follow ADR-027: stdlib-only core; all core data types in records; docstring-only package initializers; no compatibility aliases or package re-exports.
- Preserve the exact existing `BudgetLimits` and `BudgetUsage` fields, constructor signatures, validation behavior and allowance for over-limit recorded usage.
- Use existing domain-style invariant helpers. Do not change numeric/subclass acceptance globally or add numpy/provider/filesystem dependencies.
- Only acknowledged dispatches in a later orchestrator apply the proposed counter charge. A result object is not authorization, a security token or proof of dispatch.
- Limits are inclusive for counts. At equality no further unit can be charged. Elapsed time at or above its limit blocks a new operation.
- Check only the relevant operation's count; render also needs an unused recovery slot. Do not block recovery merely because decision/attempt/render counters are exhausted.
- Invalid input, non-finite/negative elapsed time or backward elapsed time raises `ValueError`. Expected exhaustion returns a typed block.
- No retry loop, clock read, side effect, dispatch-intent event, actual reservation, provider call, WAV processing, persistence, state-machine transition or port-signature change in this increment.
- Preserve unrelated working-tree changes. No commits, pushes, dependency installation or lockfile edits.

## Review focus

1. A zero remaining decision/render allowance must not prevent a recovery operation with its own remaining slot and time.
2. A render must be blocked before side effects if recovery capacity is exhausted; a grant must not increment the recovery count.
3. A recorded overrun must remain visible; never clamp usage or report a negative/earlier elapsed checkpoint.
4. A projected charge must increment exactly one count and leave the input records unchanged; it must not become an actual charge by side effect.
5. Large integer count limits must remain exact; no float conversion, hard cap or overflow workaround.

## Ownership

- Modify `src/autonomous_mixing/core/records/budgets.py`: add the records below; preserve existing records.
- Create `src/autonomous_mixing/core/loop/budgets.py`: `check_budget` only as public behavior; private helpers permitted if needed.
- Create `tests/unit/test_loop_budgets.py`: record invariants and budget behavior.
- Modify `tests/architecture/test_core_records.py`: add exactly the five new budget exports to its ownership inventory; no dependency broadening or retired checks.
- Modify `tests/architecture/test_core_layers.py`: add `"loop.budgets": ("check_budget",)` to `BEHAVIOR_INVENTORY`; preserve existing edge rules, counterexamples and real-tree scans.
- Modify `docs/architecture/overview.md`: a focused implemented-budget-gate note; retain that there is no runnable control loop yet.
- Modify `docs/research/research-log.md`: append one dated evidence/limitation entry; earlier entries remain untouched.
- Update this plan's task checkboxes as steps complete; no scope or contract changes without reporting the conflict.

No package initializer, existing port, action/decision/run record module, adapter, CLI, pyproject, lockfile, accepted ADR or other test is owned. If a material conflict requires another file, report it before expanding scope.

## Task 1: Typed pure budget gate

### Interfaces: records in `core.records.budgets`

`BudgetOperation(Enum)` has exactly these names/values:

| Name | Value | Counter |
| --- | --- | --- |
| DECISION_CALL | `decision_call` | `decision_calls` |
| ATTEMPT | `attempt` | `attempts` |
| RENDER_CALL | `render_call` | `render_calls` |
| RECOVERY_OPERATION | `recovery_operation` | `recovery_operations` |

`BudgetBlockReason(Enum)` has exactly these names/values:

| Name | Value |
| --- | --- |
| ELAPSED_TIME_LIMIT | `elapsed_time_limit` |
| DECISION_CALL_LIMIT | `decision_call_limit` |
| ATTEMPT_LIMIT | `attempt_limit` |
| RENDER_CALL_LIMIT | `render_call_limit` |
| RECOVERY_CAPACITY_LIMIT | `recovery_capacity_limit` |

Frozen dataclasses, with all fields required:

```python
BudgetGrant(operation: BudgetOperation, usage_after_charge: BudgetUsage)
BudgetBlock(operation: BudgetOperation, reason: BudgetBlockReason, usage: BudgetUsage)
BudgetAssessment = BudgetGrant | BudgetBlock
```

Constructors type-check all fields using the existing invariant convention and raise `ValueError` for wrong types. `BudgetBlock` additionally rejects a reason unrelated to its operation. ELAPSED_TIME_LIMIT is valid for every operation; each count reason is valid only for its corresponding operation; RECOVERY_CAPACITY_LIMIT is valid for RENDER_CALL and RECOVERY_OPERATION. No limits are embedded, so record construction alone does not establish that a charge/exhaustion actually follows from any supplied limits.

These five names extend the existing two-name budgets inventory to seven. No `__all__` or root exports are introduced.

### Interface: behavior in `core.loop.budgets`

```python
def check_budget(
    operation: BudgetOperation,
    *,
    limits: BudgetLimits,
    usage: BudgetUsage,
    elapsed_seconds: float,
) -> BudgetAssessment:
    ...
```

Contract, in order:

1. Type-check operation, limits and usage; validate supplied elapsed time with `require_finite`. Require `elapsed_seconds >= 0` and `elapsed_seconds >= usage.elapsed_seconds`. As with existing records, finite int/float inputs follow the shared helper's acceptance; do not silently impose kernel-style exact-type rules.
2. Build a new observed usage checkpoint with the supplied elapsed value and all original counts. Never mutate either input record. Do not clamp over-limit counts or elapsed time.
3. If elapsed time is at or above `limits.max_elapsed_seconds`, return `BudgetBlock(operation, ELAPSED_TIME_LIMIT, observed_usage)`.
4. If the operation's current count is at or above its corresponding limit, return its count block with observed usage. For recovery this reason is RECOVERY_CAPACITY_LIMIT.
5. For RENDER_CALL only, if `usage.recovery_operations >= limits.max_recovery_operations`, return RECOVERY_CAPACITY_LIMIT. A simultaneous exhausted render count wins at step 4; elapsed exhaustion wins over both.
6. Otherwise return `BudgetGrant` with new `usage_after_charge`: increment only the operation's counter by exactly one, carry other counts over exactly and use the observed elapsed checkpoint. For render, do not increment recovery or attempt counts.

The returned grant is a **proposal**. The future orchestrator will record its intent, acknowledge it, apply its charged usage and recheck cancellation/time before dispatch. This helper neither tracks a held recovery reservation nor bypasses the future recheck. Recovery capacity is a precondition only.

### Step 1 — Baseline and record tests

- [x] Read both spec documents, this whole plan, `CLAUDE.md`, engineering conventions and the existing budget/architecture tests. Record HEAD and working-tree status; preserve unrelated work. No worktree/branch manipulation or environment changes are required by this prompt.
- [x] In the new test file, write record tests importing only the proposed records. Pin enum names/values, required field types, immutability and the permitted operation/reason combinations. Pin that existing BudgetLimits/BudgetUsage constructors are unchanged.
- [x] Run `uv run --locked pytest tests/unit/test_loop_budgets.py -q`; observe missing-record collection failure before implementation.
- [x] Add the five names in `core.records.budgets` and its exact architecture inventory entries. Run the focused record tests and `uv run --locked pytest tests/architecture/test_core_records.py -q`; require exit 0.

### Step 2 — Budget behavior tests and implementation

- [x] Add behavior tests, then import the missing `check_budget` to establish a red run. Tests must cover every case below; organize with parametrization where it improves clarity.

Use ordinary test fixtures such as count limit `1` and elapsed limit `10.0`; these are software-boundary values, not scientific thresholds:
- For all four operations, current count `0` with limit `1` grants a projection with count `1`; the second check using projected usage blocks that operation. All other counters stay unchanged.
- For all four operations, zero count limit and recorded count above limit block correctly.
- Elapsed `9.0` grants when other conditions pass; elapsed `10.0` and `11.0` return ELAPSED_TIME_LIMIT with the actual elapsed value. Time wins if a count and recovery capacity are also exhausted.
- A RENDER_CALL with unused render allowance but zero/used-up recovery capacity returns RECOVERY_CAPACITY_LIMIT. With capacity it grants only a render increment. When both render count and recovery capacity are exhausted, RENDER_CALL_LIMIT wins.
- RECOVERY_OPERATION can grant when decision, attempt and render usage already exceed their limits. Conversely, a free decision/attempt does not require render/recovery capacity; check only its own counter and elapsed time.
- Invalid operation/limits/usage types raise ValueError. Elapsed bool/string/None/NaN/infinity/negative values and a finite value lower than recorded elapsed raise ValueError. Equal recorded elapsed is valid if below deadline.
- Every result retains the supplied operation. Blocked usage updates elapsed without adding a counter. Input limits/usage remain exactly unchanged for grants and blocks.
- Existing over-limit usage is never normalized. A grant for a different operation preserves those unrelated overruns.
- With a count limit `10**100`, usage `10**100 - 1` grants exactly `10**100`; the next check blocks. Do not convert counts to float.

- [x] Implement `check_budget` minimally using only stdlib and core record modules. Use exhaustive enum handling; expected input errors are not swallowed.
- [x] Add `loop.budgets` to the existing behavior inventory. Run focused and architecture tests; require exit 0.
- [x] Perform two temporary mutation checks: remove the render recovery-capacity guard; change inclusive count exhaustion so equality is allowed. Each must fail a behavioral test. Restore the file and rerun focused tests. Do not retain mutations or add mutation tooling/dependencies.

### Step 3 — Documentation, verification and handoff

- [x] Update the overview in place and append the research-log entry with actual evidence. State that this is pure budget projection only: no live counters/dispatch, retry engine, actual recovery reservation, deadline interruption, full loop or completed requirement claim. No new ADR is needed to implement the approved rule within the existing architecture.
- [x] Run:

```text
uv run --locked pytest tests/unit/test_loop_budgets.py -q
uv run --locked pytest tests/architecture tests/unit/test_public_contracts.py -q
uv run --locked pytest -q -rs
uv run --locked ruff check .
uv run --locked ruff format --check src tests
uv run --locked autonomous-mixing --help
uv lock --check
```

- [x] Confirm old BudgetLimits/BudgetUsage signatures and every existing package initializer remain unchanged; inspect the diff and prove dependency files unchanged. Do not format unrelated pre-existing files (including the previously noted script formatting issue).
- [x] If uv genuinely cannot be located, use the repository's existing `.venv` Python/Ruff/CLI for equivalent checks and report that the uv lock check was not rerun; verify lockfile unchanged. Do not install tooling or dependencies for this task.
- [x] Stop with an uncommitted diff. Handoff: HEAD/status, exact files and signatures, red/green and mutation evidence, actual verification results/skips, deviations and remaining requirement gaps. Do not start retries, additional records or the next increment.

## Subsequent increments: sequence only, not execution instructions

1. Decision input/outcome and feedback contracts: Situation additions, capabilities, diagnosis coverage, No Change and context-request routing; retry/termination data.
2. Measurement-subject/assessment input records and the pure runtime verifier using the approved baseline/check/verdict tables.
3. Rendering request/root-binding, integrity, journal/event/completion contracts and controlled implementations of the logical fault model.
4. The serial loop itself: stage order, accounting, bounded retries, acceptance/reconciliation and complete NON_DURABLE report with controlled ports.
5. Separate integrations for real analysis/diagnosis, provider calls, per-attempt filesystem rendering and durable storage/replay. Preserve scientific-method and hidden-reference requirements.

Each gets its own exact plan/prompt after review of the preceding increment; none is authorized by Task 1 above.
