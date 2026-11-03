# Engineering Conventions

Shared code-structure and coding rules for every contributor, human or AI assistant (Claude Code and Codex read this file through `CLAUDE.md`). The architecture these rules serve is decided in [ADR-027](../decisions/0027-hexagonal-control-loop-architecture.md) (Proposed). Research and documentation rules stay in `CLAUDE.md`, [`documentation-policy.md`](documentation-policy.md) and [ADR-006](../decisions/0006-research-first-development-policy.md).

Status: the rules below are the **target**. The current source tree still uses the domain/application/infrastructure layout (ADR-017, ADR-022) until the migration steps in ADR-027 land. During migration, do not add new modules to the old layout's compatibility facades or shims, and do not extend the old dependency matrices.

## Architecture in one paragraph
A modular monolith in hexagonal (ports-and-adapters) style. The `core` contains the mixing control loop, structured like a MAPE-K feedback loop (measure → diagnose → decide → admit → render → verify → accept or roll back), plus the pure rules it uses and the immutable records they exchange. Everything that touches audio samples, files or the AI provider is an `adapter` behind a port the core defines. `drivers` (CLI, experiment runner) build the adapters and start runs. `evaluation` holds the hidden reference and is used only by the experiment runner.

## Target layout
```
autonomous_mixing/
  core/
    records/        shared immutable vocabulary (project state, artifacts, context, actions, run records)
    ports.py        AudioAnalyzer, DecisionMaker, Renderer
    measurement/    observation records and analysis specifications
    diagnosis/      problem candidates (only from its roadmap phase on)
    decision/       Situation (what the decision maker sees), Proposal = ProposeAction | Abstain
    admission/      is a proposal allowed: AdmissionPolicy, Admitted | Rejected
    verification/   did the change help without regressions: AcceptanceCriteria, Verdict
    loop/           run(): stage order, budgets, history, RunRecord
  adapters/
    dsp_analysis/   implements AudioAnalyzer (deterministic DSP)
    rendering/      implements Renderer (gain/mix, WAV I/O, workspaces, file integrity)
    decision_agent/ implements DecisionMaker with Claude (ADR-005 name)
    baselines/      other DecisionMaker implementations for comparison conditions
  drivers/
    cli.py
    experiments/    the only importer of evaluation
  evaluation/       hidden reference, injected-defect ground truth, research metrics
```

## Import rules
1. `core` imports nothing outside `core`: no NumPy, soundfile, provider SDK or filesystem access.
2. Inside `core`, stage packages import only `core.records` (and the standard library). Only `core.loop` composes stages and ports.
3. `adapters` import only `core.records`, `core.ports`, third-party libraries and their own internals; never another adapter's internals and never `core.loop`.
4. Only `drivers.experiments` imports `evaluation` (SR-10, ADR-010).

One boundary test enforces these rules. Do not add finer-grained matrices unless a concrete defect requires one.

## Immutability rules
1. Every record is `@dataclass(frozen=True, slots=True)` and validates itself in `__post_init__`; an invalid record cannot be constructed.
2. Record fields hold only immutable values: scalars, enums, other records, `tuple`, `frozenset`. No `list`, `dict`, `set` or NumPy arrays.
3. Audio samples never enter records. Records carry artifact references, metadata and checksums; sample arrays stay inside adapters. An array that must cross a port is marked read-only (`array.flags.writeable = False`).
4. New versions are derived with `dataclasses.replace(...)`, never by mutation. `object.__setattr__` is used only inside `__post_init__`.
5. Stage outcomes are union types (for example `Admitted | Rejected`) handled with `match` and `typing.assert_never` in the fallback branch.
6. Mutation is allowed only in a function's local scope (build a list, return a `tuple`) and inside adapters (I/O, NumPy). Rebinding a name, as in `state = candidate.state`, is not mutation.
7. Expected outcomes are typed results; programming and wiring errors raise `ValueError`.

Not used: functional-programming libraries (`returns`, `pyrsistent`, `toolz`), monads, currying or effect systems.

## Vocabulary
Each term has exactly one meaning in code and documents.

| Term | Meaning | Where |
| --- | --- | --- |
| validation | Is an input or record well-formed? Structural checks. | everywhere (`__post_init__`, input checks) |
| verification | At runtime: did the change improve the target without making other measurements worse? Decides accept or roll back. | `core.verification` |
| evaluation | After a run: how does the result compare with hidden ground truth? Never at runtime. | `evaluation` |
| measurement / observation | Deterministic description of what is happening in the audio. | `core.measurement`, `adapters.dsp_analysis` |
| diagnosis | Which problems are plausible, with evidence strength (FR-05). | `core.diagnosis` |
| proposal | What the decision maker suggests; not permission to act. | `core.decision` |
| admitted action | A proposal that passed admission and may be rendered. | `core.admission` |
| rendering | Producing candidate audio from an admitted action. | `adapters.rendering` |
| integrity check | Verifying that a WAV file matches its record (hash, size, format). | inside `adapters.rendering` |
| control loop | The deterministic orchestration in `core.loop` (ADR-004). | `core.loop` |
| agent | Only the AI decision component `decision_agent` and the thesis title (ADR-005). | `adapters.decision_agent` |

Do not name anything "model" (ambiguous with the LLM), and do not use "analysis" for diagnosis.

## Working with two assistants
Claude Code and Codex share files, not live state. They do not edit the same files at the same time ([agent-team README](../../agent-team/README.md)). Decisions are recorded in ADRs and the research log, never only in a chat session.
