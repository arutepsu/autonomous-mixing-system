# Engineering Conventions

Shared code-structure and coding rules for every contributor, human or AI assistant (Claude Code and Codex read this file through `CLAUDE.md`). The architecture these rules serve is decided in [ADR-027](../decisions/0027-hexagonal-control-loop-architecture.md) (Proposed). Research and documentation rules stay in `CLAUDE.md`, [`documentation-policy.md`](documentation-policy.md) and [ADR-006](../decisions/0006-research-first-development-policy.md).

Status: the rules below are the **target**. The current source tree still uses the domain/application/infrastructure layout (ADR-017, ADR-022) until the migration steps in ADR-027 land. During migration, do not add new modules to the old layout's compatibility facades or shims, and do not extend the old dependency matrices.

## Architecture in one paragraph
A modular monolith in hexagonal (ports-and-adapters) style. The `core` contains the mixing control loop, structured like a MAPE-K feedback loop (measure → diagnose → decide → admit → render → verify → accept or roll back), plus the pure rules it uses and the immutable records they exchange. Everything that touches audio samples, files or the AI provider is an `adapter` behind a port the core defines. `drivers` (CLI, experiment runner) build the adapters and start runs. `evaluation` holds the hidden reference and is used only by the experiment runner.

## Target layout
```
autonomous_mixing/
  core/
    records/        all core-facing business and port records, in modules named after the stage
                    or port they serve (ids, project, artifacts, mix, context, observation,
                    actions, budgets, results, decision, analysis, verification, rendering,
                    admission, run), plus the invariant checks records call in __post_init__
    ports/          protocols AudioAnalyzer, DecisionMaker, Renderer and their response checks
    admission/      is a proposal allowed: admit_action, metadata preflight
    verification/   did the change help without regressions (rules only)
    diagnosis/      problem candidates (only from its roadmap phase on)
    loop/           run(): stage order, budget enforcement, state transitions, rollback
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
2. Inside `core`, `core.records` imports nothing outside itself; `core.ports` imports only `core.records`; stage packages (`admission`, `verification`, `diagnosis`) import only `core.records` and `core.ports` (and the standard library). Only `core.loop` composes stages and ports.
3. `adapters` import only `core.records`, `core.ports`, third-party libraries and their own internals; never another adapter's internals and never `core.loop`.
4. Only `drivers.experiments` imports `evaluation` (SR-10, ADR-010). This rule is necessary, not sufficient: an import test is not a filesystem sandbox. Runtime inputs, decision inputs and runtime configuration must also contain no hidden-data fields or references, and adapters read only from explicitly supplied roots (ADR-023).
5. Inside `core`, data types are defined only in `core.records`; ports, stages and the loop define functions. A function that a record calls in `__post_init__` lives with the records. Ports exchange request and response types; their definitions live in `core.records`. Adapter-local implementation data (for example `VerifiedAudio`, `AllocatedWorkspace`, `WrittenWav`, resource limits) stays inside its adapter.
6. The modules of `core.records` form an acyclic import graph. A record used by several record modules gets its own module (for example `budgets`); cycles are never resolved through import order or local imports.

One boundary test enforces these rules. Do not add finer-grained matrices unless a concrete defect requires one.

## Immutability rules
These rules apply to core records. Adapter-local data follows its adapter's needs (it may hold NumPy arrays) but must not leak into core records.

1. Every core record is `@dataclass(frozen=True)` and validates itself in `__post_init__`; an invalid record cannot be constructed. `slots=True` is the target, added in a separate optional migration step; new records may use it already.
2. Record fields hold only immutable values: scalars, enums, other records, `tuple`, `frozenset`. No `list`, `dict`, `set` or NumPy arrays.
3. Audio samples never enter records. Records carry artifact references, metadata and checksums; sample arrays stay inside adapters. An array that must cross a port is marked read-only (`array.flags.writeable = False`).
4. New versions are derived with `dataclasses.replace(...)`, never by mutation. `object.__setattr__` is used only inside `__post_init__`.
5. Stage outcomes are union types (for example `Admitted | Rejected`) handled with `match` and `typing.assert_never` in the fallback branch.
6. Mutation is allowed only in a function's local scope (build a list, return a `tuple`) and inside adapters (I/O, NumPy). Rebinding a name, as in `state = candidate.state`, is not mutation.
7. Expected outcomes are typed results; programming and wiring errors raise `ValueError`.

Immutability is a precondition, not a guarantee: rollback still needs retained artifacts that pass integrity checks, correct selection of the last accepted state and handling of uncertain outcomes, and a run history shows what the decision maker saw only if the actual request and raw response are captured and persisted.

Not used: functional-programming libraries (`returns`, `pyrsistent`, `toolz`), monads, currying or effect systems.

## Vocabulary
Each term has exactly one meaning in code and documents.

| Term | Meaning | Where |
| --- | --- | --- |
| validation | Is an input or record well-formed? Structural checks. | everywhere (`__post_init__`, input checks) |
| verification | At runtime: did the change improve the target without making other measurements worse? Decides accept or roll back. | `core.verification` |
| evaluation | After a run: how does the result compare with hidden ground truth? Never at runtime. | `evaluation` |
| measurement / observation | Deterministic description of what is happening in the audio. | `core.records` (observations), `core.ports` (`AudioAnalyzer`), `adapters.dsp_analysis` |
| diagnosis | Which problems are plausible, with evidence strength (FR-05). | `core.diagnosis` |
| proposal | What the decision maker suggests; not permission to act. | `core.records` (decisions), `core.ports` (`DecisionMaker`) |
| admitted action | A proposal that passed admission and may be rendered. | `core.admission` |
| rendering | Producing candidate audio from an admitted action. | `adapters.rendering` |
| integrity check | Verifying that a WAV file matches its record (hash, size, format). | inside `adapters.rendering` |
| control loop | The deterministic orchestration in `core.loop` (ADR-004). | `core.loop` |
| agent | Only the AI decision component `decision_agent` and the thesis title (ADR-005). | `adapters.decision_agent` |

Do not name anything "model" (ambiguous with the LLM), and do not use "analysis" for diagnosis.

## Working with two assistants
Claude Code and Codex share files, not live state. They do not edit the same files at the same time ([agent-team README](../../agent-team/README.md)). Decisions are recorded in ADRs and the research log, never only in a chat session.
