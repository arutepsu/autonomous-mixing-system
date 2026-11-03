# Autonomous Mixing System

Bachelor thesis research prototype: **Autonomous Detection and Correction of Mixing Problems Using an AI Agent**.

The system detects, corrects and verifies selected technical mixing problems; the AI agent (`decision_agent`, planned) is one decision-making component within it. Naming conventions: [ADR-005](docs/decisions/0005-system-oriented-naming.md).

**Status:** M0 descriptive two-stem analyzer (inputs currently labeled kick/bass, a known deviation under review; see [`docs/architecture/overview.md`](docs/architecture/overview.md)); autonomous corrections and perceptual masking detection are **not implemented**. Official project period: 2026-10-19 to 2027-01-18.

## Setup (Windows PowerShell)

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) first. This repository already has a `pyproject.toml`; do **not** run `uv init` in it.

```powershell
uv python install 3.11
uv sync --group dev
uv run autonomous-mixing --help
uv run pytest -q
uv run ruff check .
```

These commands validate the installation (package, CLI registration, unit tests, lint). The project does not ship demo audio; see [ADR-006](docs/decisions/0006-research-first-development-policy.md) for the development policy.

`uv.lock` pins the resolved dependency set and should be committed; after changing dependencies run `uv lock` and commit the updated lockfile. Use `uv sync --group dev --locked` to fail instead of silently re-resolving.

Optional future Claude SDK dependencies: `uv sync --group dev --extra agent`. Copy `.env.example` to `.env` for secrets and **never commit `.env`**; provider integration is not yet implemented.

## CLI

`autonomous-mixing analyze KICK.wav BASS.wav [--output PATH]` writes a JSON report (default `results/analysis.json`). Invalid or incompatible inputs (missing file, non-WAV container, unreadable file, fewer than 2048 frames, more than two channels, non-finite samples, differing sample rates or frame counts) are reported as a one-line `error: …` message on stderr with exit code 2.

*(2026-11-03, defect correction.)* A report destination that refers to either input file is rejected: after successful analysis and input validation, and before writing, the CLI compares filesystem identity (`os.stat` plus `os.path.samestat`, not path strings), so direct paths and relative/absolute, symbolic-link and hard-link aliases of an input are caught where the platform supports them. Rejection exits with code 2 and a one-line `error: …` message on stderr, without a traceback, and leaves the input bytes unchanged. If an existing destination or an input cannot be inspected, nothing is written; a nonexistent destination is allowed. New reports and replacement of an unrelated existing report work as before; arguments, report schema and extension handling are unchanged. This is a pre-write check, not a lock or race-free protection, and parent directories it created remain if the second check rejects. *(2026-11-03 follow-up.)* If creating the report's parent directory fails with `FileExistsError`, `NotADirectoryError`, `PermissionError` or `FileNotFoundError` (for example, a parent component is an existing file), the CLI exits with code 2 and `error: cannot create report directory …` instead of a traceback; no report is written and inputs and blocking files are unchanged. Other filesystem errors, including errors while writing the report, are not handled by this change, and created parent directories are not cleaned up. Details and evidence: [`docs/architecture/overview.md`](docs/architecture/overview.md#implemented-m0).

## Existing measurements and limitations

All values are descriptive measurements, **not** a perceptual masking measurement or validated defect detection.

- `rms_dbfs` and `peak_dbfs` are full-scale levels over all channels; silent signals report minus infinity as `null`.
- `low_band_power_proxy` is the per-frame sum of squared STFT magnitudes (SciPy default `spectrum` scaling, Hann window) in 30–200 Hz, averaged over frames; it is not normalized or calibrated signal power. With 2048-sample frames at 44.1 kHz the band contains only 8 frequency bins (≈21.5 Hz spacing).
- `temporal_coactivity_fraction` is the fraction of STFT frames in which both stems' **full-band** frame energy exceeds 10 % of that stem's own maximum frame energy; **not a validated masking metric**.
- Stereo stems are folded to mono (channel mean) before the STFT; phase cancellation in the fold-down is not detected.
- Synthesized examples do **not** provide perceptual ground truth.

Assumptions behind these parameters are listed in [`docs/research/methodology.md`](docs/research/methodology.md#assumptions-requiring-validation).

## Documentation map

This table is the canonical topic-ownership list; change rules are in [`docs/project/documentation-policy.md`](docs/project/documentation-policy.md).

| File | Authority |
| --- | --- |
| `CLAUDE.md` | Claude operating rules and navigation |
| `docs/research/project-scope.md` | Research questions, hypotheses, scope and exclusions |
| `docs/project/requirements.md` | Functional / nonfunctional requirements and acceptance criteria |
| `docs/architecture/overview.md` | Current components, boundaries and target architecture; per-component pages are added to `docs/architecture/` as components are built |
| `docs/research/methodology.md` | Evaluation design and scientific safeguards |
| `docs/research/dataset-plan.md` | Audio data creation, metadata and partitioning |
| `docs/project/roadmap.md` | Milestones, dates, priorities and exit criteria |
| `docs/decisions/` | Architectural decision records, one immutable file per ADR, indexed in [`docs/decisions/README.md`](docs/decisions/README.md) |
| `docs/research/research-log.md` | Append-only research and implementation observations |
| `docs/project/documentation-policy.md` | Which documents may change and how |

## Current development order

1. Validate M0 audio I/O and feature extraction.
2. Research a published masking-related methodology and define controlled cases.
3. Define typed detector output and independent evaluation criteria.
4. Implement bounded, reversible gain/EQ actions.
5. Introduce Claude as a structured action *proposer*, never as an audio processing tool.

See `docs/project/roadmap.md`. No FL Studio `.flp` project manipulation is required for the core experiment.
