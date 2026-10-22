# OmniAgents Studio – Evaluation Workspace Plan

## Goals
- Surface evaluation workflows directly in Studio via a new sidebar entry.
- Give project owners the ability to curate synthetic test coverage without leaving the UI.
- Prepare foundations for future dataset tooling (train/dev/test splits, exports) without blocking Dimensions/Scenarios delivery.

## Navigation & Layout
- **Sidebar**
  - Add `Evaluation` to `StudioSidebar` with icon parity (e.g., `BarChart3`).
  - Selecting it updates `Dashboard.activeSection` to `evaluation` and hides the conversation list panel.
- **Header**
  - `EvaluationHeader` consumes the shared Studio project context (populated from the manifest supplied when Studio starts); no manual switcher.
  - Shows a segmented control with `Dimensions`, `Scenarios`, `Datasets`, and displays a loading hint until the core project context is available.
- **Content Area**
  - `EvaluationView` switches between segment panels while preserving scroll state per segment.
  - Fetches dimensions once the shared Studio project context has loaded; caches under `['evaluation', 'dimensions']` since the project is implicit.

## Dimensions Segment
- **Data Source**: `evaluation.yml` living under the directory defined by `paths.evaluations` in `project.yml` (default `evaluations/evaluation.yml`). If missing or lacking a `synthetic_data_generation.dimensions` block, treat the set as empty.
- **Backend Contract**
  - `GET /api/evaluation/dimensions`
    - Uses `load_project` to resolve paths, then reads `evaluation.yml` in the evaluations directory.
    - Never falls back to `project.yml` for dimensions.
  - `PUT /api/evaluation/dimensions`
    - Writes the full dimensions array back to `evaluation.yml`, creating the file (and parent directories) if they do not exist.
    - Preserves other evaluation keys and writes a timestamped backup before overwriting.
- **UI Features**
  - Table with columns: Dimension Label, Key, Values Count, Last Edited.
  - Row expansion shows value descriptions and examples.
  - Create/Edit dialog supports:
    - Name (machine key, lowercase snake).
    - Label, description (optional).
    - Value editor (list with name/label/description/examples multiline textarea).
  - Delete prompts confirmation and updates store optimistically.
  - Change tracking banner warns about unsaved edits; save triggers PUT.
- **Implementation Notes**
  - Tool discovery now cleans up temporary modules so per-project tool packages (`tools.*`) do not collide with builtin ones. Future dynamic imports should follow the same pattern.
  - When adding new evaluation files, always update `paths.evaluations` so Studio resolves the correct directory.

## Scenarios Segment
- **Golden Scenarios**
  - Source: files discovered via helper (respecting `paths.evaluations`), e.g., `evaluations/scenarios.yml`.
  - Display count + last modified timestamp.
  - Allow inline edit (open external file link + future plan for direct editing).
- **Generated Sets**
  - Search `artifacts/eval/**/generated_scenarios.yml` with adjacent `manifest.json`.
  - Show cards with: set name, generated timestamp, source workflow, scenario count.
- **Annotated Sets**
  - Discover `annotated_scenarios.yml` under same artifact root.
  - Distinguish with badge (`Annotated`).
- **Promotion Workflow**
  - Row-level action `Promote to Golden` available for individual scenarios (not the entire set).
  - Backend endpoint should append selected scenario dict to the primary golden file (e.g., `evaluations/scenarios.yml`) maintaining YAML structure.
  - UI confirms success and refreshes both Golden list and source set (mark scenario as promoted to avoid duplicates).
- **Current Implementation**
  - `GET /api/evaluation/scenarios` returns:
    - Golden scenario sets parsed from `scenarios.yml` within the configured evaluations directory.
    - Generated/annotated sets discovered under `artifacts/eval/**/{generated,annotated}_scenarios.yml`, enriched with manifest metadata when present.
  - `POST /api/evaluation/scenarios/promote` promotes an individual generated/annotated scenario to `scenarios.yml`, backing up the file first and preventing duplicates by `id`/`name`.
  - Frontend disables promotion when a scenario already exists in the golden pool and invalidates cached data after a successful mutation.
- **Filtering & Search**
  - Text filter across scenario names/prompts.
  - Toggle to show only unpromoted scenarios in generated/annotated lists.

## Datasets Segment (Placeholder)
- Initial UI: empty state describing upcoming dataset organization tooling.
- Provide CTA button (disabled) for “Create Dataset Split” to signal roadmap.
- No backend calls yet; structure allows future endpoints without UI rewrite.

## Backend Extensions
- Extend `fastapi` app (`omniagents_studio/api.py`):
  - Ensure project resolution uses `load_project`; reuse YAML helpers for persistence.
  - Dimension helpers resolve evaluations directory, read/write `evaluation.yml`, back up existing files, never mutate `project.yml`.
  - Scenario helpers mirror compiler behaviour: gather sources via `paths.evaluations`, scan `artifacts/eval/**` for generated/annotated sets, and manage promotions into `scenarios.yml` with backups and duplicate checks.
- Serialization format: ensure responses normalized (ids, names, prompt, dimensions, origin metadata).

## Frontend Types & APIs
- Extend `src/types/index.ts` with:
  - `EvaluationDimension`, `DimensionValue`, `ScenarioSet`, `ScenarioListItem` interfaces.
  - Discriminated union for scenario set origin (`"golden" | "generated" | "annotated"`).
- Update `src/lib/api.ts` with fetchers wrapping new endpoints.
- React Query keys: `['evaluation', 'dimensions']`, `['evaluation', 'scenarios']` (project context is implicit via manifest).

## State Management
- Centralize project loading at the Studio level (shared context used by Playground, Agents, Evaluation).
- `EvaluationView` handles unavailable context by showing a loading state, then renders once the manifest-backed project is known.
- Continue using optimistic updates/invalidation for dimensions and scenario promotion (surface errors via console/logs until a toast system exists).

## Implementation Phases
1. **Backend groundwork**: helper utilities, GET endpoints, scenario discovery.
2. **Frontend scaffolding**: sidebar entry, route plumbing, header/segmented control.
3. **Dimensions MVP**: completed — adds CRUD backed by `evaluation.yml` with backups and implicit project context.
4. **Scenarios MVP**: completed — aggregates golden/generated/annotated sets, supports single-scenario promotion into `scenarios.yml`.
5. **Polish**: loading states, error handling, empty-state illustrations.
6. **Future**: dataset management, scenario editing inline, bulk promotion, diffing tools.

## Testing Strategy
- Unit test helpers that parse scenario files and append to golden set (ensure idempotency).
- Integration tests (FastAPI) for new endpoints covering validation and failure cases.
- Frontend Cypress/regression plan once existing suite available; otherwise manual QA checklist (project with synthetic data, multiple scenario files).

## Open Considerations
- Confirm canonical golden file path in project layout (default `evaluations/scenarios.yml`; fallback to first discovered file).
- Ensure future scenario endpoints mirror dimension behaviour: read/write within the evaluation directory, never `project.yml`, always back up previous content.
- Decide conflict resolution when promoting duplicates (either ignore or update existing entry; plan defaults to ignore).
- Determine long-term storage for promotion provenance (manifest notes, scenario tagging).
