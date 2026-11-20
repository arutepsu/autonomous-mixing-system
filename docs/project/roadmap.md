# Roadmap — v1 (2026-10-14)

**Official start:** 2026-10-19. **Submission:** 2027-01-18. Preparation starts 2026-10-14.

| Time | Milestone | Exit condition |
| --- | --- | --- |
| Oct 14–18 | M0: descriptive analyzer | Validated WAV reading and reproducible feature report |
| Oct 19–25 | M1: scientific detector | Method literature review, controlled cases, structured candidate schema |
| Oct 26–Nov 1 | M2: DSP correction tools | Bounded gain/EQ and reversible writes |
| Nov 2–8 | M3: Claude adapter | Structured proposal and deterministic validation |
| Nov 9–22 | M4: end-to-end vertical slice | One complete kick/bass analyze–act–verify loop |
| Nov 23–Dec 13 | M5: extensions and calibration | Attempt resonance/phase; tune on calibration only |
| Dec 14–20 | M6: implementation freeze | Focused tests pass; experiment configuration frozen |
| Dec 21–Jan 3 | M7: main evaluation | Baselines executed; dataset/results archived |
| Jan 4–10 | M8: thesis draft | Complete substantive chapters |
| Jan 11–17 | M9: revision | Content, references, reproducibility and formatting verified |
| Jan 18 | Submission | Final thesis and required artifacts submitted |

## Priority order
1. Working kick/bass autonomous loop with independent validation and baselines.
2. Reliable evaluation and thesis writing.
3. Resonance/buildup (P2) and stereo/phase (P3) as planned research targets; P1 end-to-end is the contingency minimum, not a decision to drop P2/P3 (see `../research/project-scope.md`).
4. UI is optional. Bounded, operator-assisted FL Studio integration (gain changes and verified manual exports) was adopted on 2026-11-18 as an optional path (D15, [ADR-034](../decisions/0034-bounded-operator-assisted-fl-studio-integration.md), Proposed). It is implemented in the coordinator through Increments 1–6 of the [coordinator-integration plan](../superpowers/plans/2026-11-18-daw-coordinator-integration.md) (ADR-035 to 040, Proposed; status updated 2026-11-20, superseding "not implemented in the coordinator"). The status: operator tooling is ready (`daw status` and authorized `daw recover`); autonomous runs are unavailable until a decision maker and a diagnosis source exist; no live FL Studio trial has been run. It does not displace priorities 1–3. This supersedes "DAW integration and UI are optional".

## Current status
M0 starter runs descriptive analysis; formal scientific detector and Claude adapter are pending. No features may be reported complete without evidence.

2026-10-14: M0 "validated WAV reading" is met (input validation with tested error paths; 17 tests pass on a uv-synced Python 3.11 environment). M0 "reproducible feature report" is **partial**: output is deterministic, but the report still lacks part of the provenance metadata (FR-12 in `requirements.md`). M0 remains open.
