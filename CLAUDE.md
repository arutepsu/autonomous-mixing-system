# Instructions for Claude

This repository is a bachelor-thesis research prototype, not a production DAW. Read `README.md`, `docs/research/project-scope.md`, `docs/architecture/overview.md`, `docs/project/requirements.md`, `docs/research/methodology.md`, `docs/project/documentation-policy.md` and `docs/project/engineering-conventions.md` before changing behavior.

## Priorities
1. Scientific validity, traceability and maintainability over demos ([ADR-006](docs/decisions/0006-research-first-development-policy.md)): no artificial end-to-end examples, generated example music or temporary features; every feature traces to a documented requirement; keep the architecture track-role-agnostic (not kick/bass-specific); implement problem-specific features (e.g. masking) only in their roadmap phase; no hardcoded musical assumptions or arbitrary thresholds. Never equate overlap features with proven perceptual masking.
2. Make small, testable changes with deterministic DSP and validated inputs.
3. The AI decision component only proposes actions; the deterministic control loop admits, renders and verifies them.
4. Keep original WAV files immutable. Never commit credentials or copyrighted stems.
5. Write regression tests for algorithm changes and run relevant tests after edits. Synthetic numerical signals are for unit tests only, never evaluation data. Validate changes via imports, CLI registration and tests, not demo runs.
6. Do not delete existing demo scripts or data without reviewing their dependencies and getting owner approval.

## Documentation rules
- `docs/research/research-log.md` is append-only: add timestamped entries, never rewrite earlier ones.
- ADRs live in `docs/decisions/`, one file each, indexed in its `README.md`. Never edit an accepted ADR; supersede it with a new one.
- For other docs, edit the relevant section in place while preserving unrelated details; state any superseded conclusions explicitly.
- Update `docs/project/roadmap.md` only for actual scope or status changes; do not mark work done without evidence.
- Put scientific uncertainties under "Open questions" rather than presenting guesses as facts.
- For architectural changes, add a new ADR file in `docs/decisions/` (template in its README) with rationale and consequences, and add it to the index.
- If documents conflict, point out the conflict; do not silently select one.
- Never regenerate all Markdown files as a side effect of implementing a feature.

## Architecture and code conventions ([ADR-027](docs/decisions/0027-hexagonal-control-loop-architecture.md), Accepted)
@docs/project/engineering-conventions.md

That file is the single source for package layout, import rules, immutability rules and vocabulary, shared with Codex. In short: hexagonal modular monolith; `core` holds the control loop, pure stage rules and immutable records; DSP, files and Claude are `adapters` behind `core.ports`; `drivers` start runs; only the experiment runner imports `evaluation`. Keep "validation", "verification" and "evaluation" distinct. Every package initializer contains only a docstring: import from modules.

## Naming ([ADR-005](docs/decisions/0005-system-oriented-naming.md))
Repository/distribution `autonomous-mixing-system`, import package `autonomous_mixing`, CLI `autonomous-mixing`. Use "agent" only for the AI decision component (`decision_agent`, planned) and in the thesis title; never rename the thesis title.

## Project commands
`uv sync --group dev` / `uv run pytest -q` / `uv run ruff check .` / `uv run autonomous-mixing --help`.
Never use `pip install` or `uv init` here; change dependencies via `uv add` and keep `uv.lock` in sync. Status labels for docs are defined in `docs/project/documentation-policy.md`.

## State
Updated 2026-11-22: controlled offline/DAW coordinators, bounded gain correction, verification/recovery, durable run records and descriptive level/P1 measurement components exist. R1 is committed at `0242c99`. R3 is committed at `6b08451`; R4 provider transport/orchestration is implemented with simulated responses, uncommitted for review. Production diagnosis and R5 composition are missing; CLI start/continue still refuse, and no live integrated agent trial has run. The legacy M0 `analyze` interface retains kick/bass labels; do not extend that assumption. Read `docs/project/roadmap.md` and the accepted diagnosis/decision-adapter plan for current status and R3/R4 priorities. The owner is developing the FL Studio dataset in parallel; paid assessors and the optional extended calibration study are not implementation prerequisites. Missing calibration must remain explicit; never invent qualified diagnosis. The ADR-027 architecture/import rules above remain binding.
