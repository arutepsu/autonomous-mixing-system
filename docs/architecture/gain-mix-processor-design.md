# Gain/full-mix processor adapter — design draft (2026-11-03)

*Note (2026-11-04): this design page records ADR-026 in the names and paths of its time. Since ADR-027 (migration steps M3 and M5) the processor is `GainMixProcessor` in `autonomous_mixing.adapters.rendering.gain_mix`; `ProcessingPlan`, `ProcessedCandidate`, `ProcessingFailure`, `ArtifactProcessingEvidence`, `AudioProcessor` and `validate_processed_candidate` are now `RenderPlan`, `RenderedCandidate`, `RenderFailure`, `RenderEvidence`, `Renderer` and `validate_rendered_candidate`; `verify_resolved_wav_artifact` is `check_resolved_wav_integrity`; `infrastructure.audio`, `filesystem`, `workspaces` and `wav_output` are modules of `adapters.rendering`. Current behaviour, including the MD-6 output-location guard, is described in [`overview.md`](overview.md).*

**Status: Implemented** (2026-11-03) as [ADR-026](../decisions/0026-gain-mix-processor-adapter.md) (Proposed) in `infrastructure.processing`; evidence: `tests/unit/test_gain_mix_processor.py`. The owner approved D1–D5 as recommended on 2026-11-03. The behaviour summary lives in [`overview.md`](overview.md); this page keeps the design rationale. *(The earlier status "Planned. Design draft awaiting owner approval. Nothing here is implemented." is superseded.)*

Implementation note, not in the original draft: the wiring checks also reject a plan whose inputs disagree with the render settings, frame count or timeline start. Preflight normally guarantees this; without the check, a manually built plan would reach the writer's own argument errors after other files had been read.

## Purpose and traceability
This is the first implementation of the `AudioProcessor` port ([ADR-020](../decisions/0020-execution-contracts-and-metadata-preflight.md) §5). It turns a `ProcessingPlan` that has passed preflight into two verified WAV files on disk, a new target stem and a new full mix, plus a `ProcessedCandidate` record describing them. It composes components that already exist and are tested. It adds no new numerical behaviour.

- Requirements it contributes to, without completing any: FR-08 (gain action, deterministic and non-destructive), FR-17 (rendered stems and mix without modifying the source), QR-01 (sources untouched), QR-02 (deterministic and offline), OP-02 (no overwriting earlier outputs).
- Roadmap: the gain part of M2 ("bounded gain/EQ and reversible writes"). EQ is out of scope.
- Audit findings: it narrows F3 and F4 (see [Effect on audit findings](#effect-on-audit-findings)). F2 is unaffected.

## What it composes
| Step | Existing component | Reference |
| --- | --- | --- |
| Check input files against their hash and metadata, then decode | `infrastructure.audio.verify_resolved_wav_artifact` | ADR-023 |
| Apply gain to the fixed input | `infrastructure.audio.apply_absolute_gain` | ADR-019, ADR-021 |
| Sum the mix in recipe order | `infrastructure.audio.sum_stems` | ADR-021 |
| Write the output exclusively and read it back | `infrastructure.wav_output.write_wav_output` | ADR-025 |
| Output folder (allocated by the caller, see D1) | `infrastructure.workspaces.AllocatedWorkspace` | ADR-024 |

## Decisions for the owner
Each decision lists the recommendation first.

### D1 — How the processor receives its output folder and input locations
`process(plan)` takes only the plan. But allocating an output folder needs the `ProjectState`, the configured `WorkspaceRoot` and the protected-location records, and reading inputs needs each input's configured root. The plan carries none of these.

- **(Recommended) A — bind a pre-allocated workspace at construction.** The caller (the future execution service) allocates the workspace and constructs one processor per execution: `GainMixProcessor(workspace: AllocatedWorkspace, limits: ProcessingLimits)`. The input locations come from `workspace.request.protected_locations`. Allocation already requires that set to cover every current audio member and fixed baseline, so it contains every input the plan needs. The port and `ProcessingPlan` stay unchanged, and allocation stays with the component that holds the state and the full inventory.
- B — extend `ProcessingPlan` with the state, root and locations, and allocate inside `process`. This changes an application contract (ADR-020/021) and puts infrastructure records (`ResolvedArtifactLocation`) into the application layer, which ADR-022 forbids.
- C — pass the extra context as a second `process` argument. This changes the port.

### D2 — Failure classification
- **(Recommended)**
  - Wiring errors are programming errors and raise plain `ValueError` before any file access. Examples: a wrong argument type, a plan whose workspace or state does not match the bound workspace, a missing input location.
  - Expected failures return `ProcessingFailure(FAILED, ExecutionIssue(code, message))`. These are input verification or resolution errors, DSP results that cannot be represented (for example a sum that overflows) and `WavOutputError`. The message names any retained file.
  - Unexpected exceptions propagate unchanged, and the caller records `UNCERTAIN`.

  This follows the convention of every existing infrastructure component. The processor never returns `UNCERTAIN` itself: when it returns, it knows no candidate was produced.
- Alternative: return `UNCERTAIN` for writer failures that happen after a file was created (`created_file` set).

### D3 — Output names and locations
- **(Recommended)** Fixed filenames `target.wav` and `mix.wav`. There is one fresh workspace per execution, so the names are unique, and they satisfy the writer's `[A-Za-z0-9_-]+\.wav` rule. Artifact IDs are not used as filenames: their text is not guaranteed to match that rule, and the ID is already in the record.
- The output `AudioArtifact.location` is `"<directory_token>/<filename>"`, relative to the output root. A later `resolve_artifact_location(artifact, root=<output root path>)` then finds it. Because of this, an output can become a protected input of a later execution with no extra mapping.

### D4 — Processor identity and environment provenance
- **(Recommended)** Define a module constant `GAIN_MIX_PROCESSOR = VersionRef("autonomous_mixing.gain_mix_processor", "1")` and do not let callers configure it, so a caller cannot misattribute outputs. Increase the version whenever numerical or file behaviour changes.
- Environment provenance (Python, NumPy, soundfile and libsndfile versions) stays **out of scope**. There is no field for it in `ArtifactProcessingEvidence`, and adding one changes an application contract. It belongs to the run record (QR-03, FR-12). F3 stays open for this item.

### D5 — Resource limits
- **(Recommended)** `ProcessingLimits(max_file_bytes, max_decoded_bytes)`: both required, positive built-in `int`, with no defaults. They apply per artifact to every read and to both writes, and they are passed through unchanged to the verifier and the writer.
- Peak memory is roughly (number of non-target stems + 3) × the decoded size of one stem. The limits do not bound it. This is recorded as a limit, not handled.

## Behaviour (with D1–D5 as recommended)

### Construction (no filesystem access)
- `workspace` must be an `AllocatedWorkspace` and `limits` a `ProcessingLimits`; otherwise a plain `ValueError` is raised.
- The processor satisfies `AudioProcessor` structurally (`Protocol`, no inheritance). A static assignment test checks this.

### `process(plan)`, in order; the first failure wins
1. **Binding checks** (`ValueError`, no file access):
   - `plan` is a `ProcessingPlan`.
   - `plan.workspace == workspace.request.workspace`.
   - `workspace.request.state` has project `plan.project_id` and snapshot revision `plan.before_revision`.
   - Every input the plan needs — `fixed_target_input` and each current stem except the obsolete target stem — has exactly one location record keyed by `(project_id, artifact_id)` whose `artifact` **equals** the plan's record.
2. **Verify every input before writing anything.** `verify_resolved_wav_artifact` runs for the fixed target input, then for each non-target current stem in recipe order.
   - The obsolete current target stem is neither read nor summed (ADR-019).
   - On `ArtifactVerificationError` or `LocationResolutionError`, the processor returns `FAILED` with code `input_verification_failed`, and the message names the artifact and the underlying code. No output exists yet.
3. **Compute in memory.**
   - `target = apply_absolute_gain(fixed.samples, plan.requested_gain_db)`.
   - `mix = sum_stems(...)` over the stems in `recipe.track_ids` order, with `target` in place of the target track.
   - A `ValueError` from a kernel (a result that cannot be represented) returns `FAILED` with code `processing_failed`. No output exists yet.
4. **Write the target, then the mix,** each with `write_wav_output(workspace, name, samples, render_spec=plan.render_spec, processing_spec=plan.processing_spec, ...)`.
   - A `WavOutputError` returns `FAILED` with code `output_failed:<writer code>`.
   - If the mix write fails, `target.wav` stays on disk and the message names it. Nothing is deleted or retried (ADR-024/025).
5. **Build the records.**
   - Each output gets an `AudioArtifact` with:
     - the candidate ID, `plan.project_id` and the candidate revision;
     - kind STEM with `(target_track_id,)`, or kind MIX with `recipe.track_ids`;
     - the location from D3;
     - the timeline start of the fixed input;
     - rate and channels from `render_spec`, and the frame count written;
     - `content_hash = WrittenWav.sha256` (the bytes read back);
     - `render_spec = plan.render_spec.ref`.
   - Evidence for the target cites `(fixed input id,)`.
   - Evidence for the mix cites the new target ID followed by the non-target stem IDs in recipe order. Its new-target input is the in-memory array, and the writer's bit-exact readback confirmed it equals the file.
   - Both evidence records carry `input_hashes_verified=True` and `finite_samples=True` (the verifier, the kernels and the writer all reject non-finite values), `peak_linear = max|samples|`, `processor = GAIN_MIX_PROCESSOR` and `processing_spec = plan.processing_spec`.
6. **Return** `ProcessedCandidate`. The processor does not call `validate_processed_candidate` itself. The caller does, as the port specifies, so the check stays independent of the adapter.

### What it never does
It never chooses or changes the gain, reads hidden evaluation data, opens the obsolete target stem or the current mix, writes outside the bound workspace, deletes or renames anything, retries, normalizes, clips or limits.

## Placement and import rules (new ADR-022 table rows)
| Module | Allowed source imports |
| --- | --- |
| `autonomous_mixing.infrastructure.processing` | re-exports of `gain_mix` only |
| `infrastructure.processing.gain_mix` | standard library, NumPy, the public interfaces of `domain.project`, `domain.shared`, `domain.action` and `application.execution`, and the `audio`, `filesystem`, `workspaces` and `wav_output` facades. Not soundfile directly, other application components, component internals, M0, the CLI or providers. |

This adds a second infrastructure→application dependency (after `workspaces.allocation`), scoped to the `application.execution` public interface. That is the port's own package, so it is the minimum an adapter needs. Importing `audio`, `filesystem`, `workspaces` or `wav_output` must not load `processing`. The boundary tests and the fresh-process probes get matching cases.

## Tests (unit, tiny synthetic arrays in `tmp_path`; not evaluation data)
- **Semantics:**
  - Outputs equal `apply_absolute_gain` and `sum_stems` applied directly, bit for bit, for mono and stereo.
  - Non-target stems are not gained again.
  - The obsolete target stem is never opened: it is corrupted on disk and the run still succeeds.
  - Repeating the same plan in a fresh workspace gives identical samples. Hashes are not compared, because of the PEAK chunk (ADR-025).
- **Contract:**
  - The result passes `validate_processed_candidate`.
  - Each output `AudioArtifact` passes `verify_resolved_wav_artifact` against the output root.
  - Input file hashes are unchanged after the run (QR-01).
- **Binding errors:** foreign workspace, wrong state revision, missing location, and a location whose artifact differs from the plan's. Each raises `ValueError` before any file access, and the workspace stays empty.
- **Failures:**
  - An input hash mismatch returns `FAILED` and leaves no output files.
  - A kernel overflow returns `FAILED` and leaves no output files.
  - An existing `target.wav` returns `FAILED` with `output_failed:collision`.
  - An injected mix-write failure returns `FAILED` with `target.wav` retained and named.
  - Unexpected exceptions propagate unchanged.
- **Boundaries:** the new import rows, fresh-process import order, and the structural `AudioProcessor` assignment.

## Effect on audit findings
- **F3 (narrowed, not closed):** adapter evidence is produced for real for the first time, and processor identity is fixed. Environment provenance remains open (D4).
- **F4 (narrowed, not closed):** the adapter enforces that the executor writes only through the exclusive writer into a workspace allocated with protected-overlap checks. Still missing: a complete history inventory, parent pinning, race-free confinement and locks.
- **F2:** unaffected.

## Limits and open questions
- Input bytes are verified at capture time only. A file changed after capture is not detected (as stated in ADR-023).
- Output hashes are not reproducible across runs, though samples are (PEAK chunk, ADR-025). Whether the acceptance stage should compare samples instead of hashes is an **open question** for F2.
- No two-output transaction: a failure between the two writes leaves a lone `target.wav`.
- Memory is not bounded overall (D5).
- No execution service, receipt or candidate-state assembly is added. A connected loop still needs that service, M1 detection and the M3 decision adapter.
- No requirement changes status, and no perceptual or research-performance result follows.
