# Claude prompt: control-loop increment 1

Work in `C:\uni\Bachelor-Thesis\autonomous-mixing-system` on the current branch. Implement only the pure budget-gate increment described below, then stop with an uncommitted diff for Codex review. Use one implementer serially; do not start parallel work or the next increment.

Read:
1. `CLAUDE.md` and `docs/project/engineering-conventions.md`.
2. `docs/superpowers/specs/2026-11-04-control-loop-lifecycle-design.md`.
3. `docs/superpowers/specs/2026-11-04-control-loop-attempt-contract.md`, especially sections 25–26 and 29.
4. `docs/superpowers/plans/2026-11-04-control-loop-budget-gate-plan.md` in full.

The owner approved the parent control-loop design. This request authorizes Task 1 of the budget-gate implementation plan only, including its record tests, behavior tests, architecture inventory updates and scoped documentation. The plan contains the exact signatures, enum values, reason compatibility, precedence and required tests. If any document is missing or a material contract conflict exists, report it before inventing requirements. The planning files may be uncommitted; preserve them and other unrelated work.

Implement the five new frozen/typed budget names in `core.records.budgets` and:

```python
check_budget(operation, *, limits, usage, elapsed_seconds) -> BudgetAssessment
```

in `core.loop.budgets`, with the exact annotated signature from the plan. Keep existing BudgetLimits/BudgetUsage signatures and validation unchanged. The function proposes a charge or returns a typed block; it must not charge live state, read a clock, reserve actual recovery capacity, dispatch calls, retry or perform I/O. A render needs unused recovery capacity; recovery is not blocked by exhausted unrelated counters.

Follow the plan test-first, preserving red/green evidence. Update ownership inventories narrowly; do not widen architectural allow lists, retire tests or add package exports. Run the focused, architecture/public-contract and full verification listed in the plan. Perform and restore the two temporary mutation probes. No dependency/environment installation, lockfile edits, commits or pushes.

The source lifecycle remains a requirement: OBSERVE → DIAGNOSE → PLAN / DECIDE → ACT → EVALUATE → STOP / ITERATE / ROLLBACK. This increment is enforcement groundwork, not a completed loop or research result. Implement no diagnosis, provider integration, port redesign, verifier, journal, filesystem preparation or additional action types.

Handoff with HEAD/status, exact files/interfaces, red/green and mutation evidence, observed checks/skips, deviations and remaining gaps. Separate checks you ran from any delegated reports. If uv is genuinely unavailable, follow the existing-venv fallback in the plan and report the missing lock check honestly. Stop for review.
