# Research Scope — v1.0 (2026-10-15; revises v1 of 2026-10-14)

## Topic
Autonomous Detection and Correction of Mixing Problems Using an AI Agent.

(Corrected 2026-10-15; earlier versions wrote "Using AI Agents". The system uses one AI agent as its decision-making component, not as its entire architecture.)

## Main research question
To what extent can an AI agent autonomously detect, correct and verify selected technical mixing problems in multitrack music using deterministic audio analysis while respecting producer intent?

## Subquestions
- RQ1: How can selected mixing problems be represented as measurable observations?
- RQ2: Can an LLM agent choose appropriate actions from measurements, context and producer intent?
- RQ3: Does iterative correction outperform an otherwise comparable one-pass approach?
- RQ4: Can validation and rollback prevent unsafe or unsuccessful interventions?

## Working hypotheses (to test, not to assume)
- H1: On controlled cases, agent-guided corrections improve predetermined technical objectives.
- H2: Re-analysis and revision increase success or reduce unresolved defects versus one pass.
- H3: Constraints and verification reduce undesirable or out-of-intent interventions.

## Problem families (research targets)
All three families are planned research targets. Their scientific methods, the validity of measurable indicators and their implementation complexity are not yet known and are expected to differ; each is defined after literature review (requirement FR-13).
- **P1** Spectral/temporal masking between tracks; kick/bass is the primary use case, not an architectural assumption.
- **P2** Frequency buildup / resonance.
- **P3** Stereo / phase compatibility.

**Target scope:** P1, P2 and P3 addressed with the same research pipeline (detection, decision, correction, verification, evaluation).

**Contingency minimum:** P1 complete end-to-end, evaluated under all comparison conditions. This is the minimum scientifically meaningful result if the schedule does not allow P2 or P3 to reach the same depth; it is **not** a decision to abandon P2 or P3. The depth reached for each family is reported as achieved. The required depth for P2/P3 beyond the minimum is an open decision (D2 in `requirements.md`).

Superseded: v1 labeled P2 and P3 as "extension".

## Core boundaries
- Offline synchronized WAV stems from general multitrack projects (any number of tracks), manually declared track roles, Python DSP.
- Claude API proposes structured actions through a validated tool interface.
- Original audio remains unchanged; each action is reversible.
- No new foundation-model training, comprehensive automatic mixing/mastering, general genre classification, guaranteed perceptual superiority, or mandatory FL Studio project integration.
- Optional, operator-assisted FL Studio runtime path (D15 decided 2026-11-18; [ADR-034](../decisions/0034-bounded-operator-assisted-fl-studio-integration.md), Proposed). It covers gain changes on calibrated mixer inserts and verified manual exports only. Excluded: `.flp` manipulation, unattended operation, automated saving or exporting. The offline WAV path stays primary; the DAW path is not mandatory for the core experiment.

## Study genres
House/techno, trap/phonk/wave, and drum & bass; genre comparisons are exploratory unless data supports them.

## Open questions
- Whether results from the optional DAW path enter the main evaluation, and under which comparison conditions (ADR-034).
- Which published perceptual masking or multitrack-interaction methodology is feasible?
- How will defect labels and acceptance criteria be established independently?
- What is the smallest effective benchmark given creation/annotation time?
