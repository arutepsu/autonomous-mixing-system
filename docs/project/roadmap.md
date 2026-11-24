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
4. UI is optional. Bounded, operator-assisted FL Studio integration (gain changes and verified manual exports) was adopted on 2026-11-18 as an optional path (D15, [ADR-034](../decisions/0034-bounded-operator-assisted-fl-studio-integration.md), Proposed). It is implemented in the coordinator through Increments 1–6 of the [coordinator-integration plan](../superpowers/plans/2026-11-18-daw-coordinator-integration.md) (ADR-035 to 040, Proposed; status updated 2026-11-20, superseding "not implemented in the coordinator"). The status (updated 2026-11-22): operator status and authorized recovery are implemented with review fixes committed at `027bed7`; autonomous runs are unavailable until production decision and diagnosis components exist. Live research trials of bridge inspection, gain writes, calibration and dB commands are recorded in the bridge/conversion documents; no live integrated agent trial has been run. It does not displace priorities 1–3. This supersedes "DAW integration and UI are optional".

## Current status — 2026-11-22

This dated checkpoint supersedes the M0-only current-status text. The calendar above is the original planning baseline, not evidence that its milestones were achieved. M4's complete production-agent loop is not yet met; this status correction does not invent revised dates or change the submission date.

| Area | Evidence-backed status |
| --- | --- |
| Descriptive measurements | Partial: native-channel level analyzer and mono 44.1 kHz PCM_16 P1 directional measurement/support adapters implemented; R1 reviewed and committed at `0242c99`. No qualified P1 diagnosis. |
| DAW/coordinator reliability | Partial: bounded control, recovery, export continuation and durable restart implemented/tested against a simulated FL host. Live research bridge/dB trials exist separately; no live integrated agent trial. |
| Production AI/diagnosis | Partial: R3 packet/parser/audit is committed; R4 Claude adapter is committed at `bb3e0a5` with simulated-response tests. R2A readiness diagnosis is implemented, review pending; qualified candidate generation and R5 composition remain missing. CLI start/continue still refuse. |
| Dataset and scientific evaluation | In development by the owner in FL Studio. Calibration has not run; no held-out results. The six-project/three-listener/grid-search study remains optional and unapproved. |
| Next engineering | Review R2A readiness diagnosis; obtain approved qualification/numerical policy and genuine qualified measurements for R2B, then R5 composition when genuine dependencies exist. Work can progress alongside dataset creation. |

See the [accepted staged plan and owner clarifications](../superpowers/plans/2026-11-20-diagnosis-and-decision-adapter.md). The thesis focuses on agent/tool/loop/verification behavior with trustworthy measurements and defensible mixing claims; no paid listener panel is assumed. P2/P3 remain research targets under the existing scope, not completed capabilities.

### Historical M0 checkpoint (retained)

2026-10-14: M0 "validated WAV reading" is met (input validation with tested error paths; 17 tests pass on a uv-synced Python 3.11 environment). M0 "reproducible feature report" is **partial**: output is deterministic, but the report still lacks part of the provenance metadata (FR-12 in `requirements.md`). M0 remains open.


### Current engineering checkpoint — 2026-11-24

This status supersedes the older engineering-status rows above, not the historical timetable or P2/P3 scope. Experimental fixed-support measurement and deterministic diagnosis are committed (62133c3 and 32d49be). R5A evidence/audit gating and the newer offline gain-feedback/float-ingress engineering case are implemented; the 24 November checkpoint review records verification and compatibility limits. The offline case uses labelled diagnosis/proposals; real diagnosis/provider production composition (R5B), operator run activation (R5C) and live integrated development validation remain pending. Current experimental thresholds are permitted for engineering development without a new calibration search; they are not scientifically qualified. CLI start/continue still refuse. See the [current-state review and next steps](../research/2026-11-24-system-checkpoint-and-next-steps.md). The next priority is a reviewed coherent checkpoint, then bounded real-component composition, not additional tools or a larger dataset prerequisite.
