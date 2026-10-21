# Exploration Strategies

Detailed guidance for Phase 1 (Ground in the Environment). The goal is to build a mental model of the relevant codebase before engaging the user in conversation.

## What to Explore by Plan Type

### New Feature

1. **Entrypoints**: Where does similar functionality live? What's the nearest analogous feature?
2. **Data model**: Schemas, types, database tables, state management relevant to the feature area
3. **Interfaces**: Existing APIs, component props, function signatures in the affected area
4. **Conventions**: Naming patterns, file organization, error handling style, test patterns
5. **Dependencies**: What libraries/services are already available? What would need to be added?
6. **Configuration**: Feature flags, environment variables, config files that govern the area
7. **Tests**: Existing test patterns, fixtures, helpers, coverage in the affected area

### Bugfix

1. **The bug path**: Trace the code path where the bug manifests, starting from the symptom
2. **Recent changes**: Git history for files in the affected area (look for recent regressions)
3. **Related tests**: Existing tests that should have caught this — why didn't they?
4. **Error handling**: How does the current code handle the failure case?
5. **Dependencies**: Version constraints, known issues in upstream dependencies
6. **Reproduction**: Can you confirm the conditions under which the bug occurs?

### Refactor

1. **Current structure**: Map the modules, classes, or functions being refactored
2. **Callers/consumers**: Who depends on the code being changed? (grep for imports/usage)
3. **Test coverage**: What's tested today? What's the safety net for this change?
4. **Pain points**: Where is the current code fragile, duplicated, or hard to extend?
5. **Dependencies**: Circular dependencies, tight coupling, shared state
6. **CI/CD**: Build steps, deployment config, integration tests that exercise this code

## Exploration Order

Start broad, then narrow:

1. **Start with entrypoints**: Find the main file, route handler, CLI command, or component that's closest to the change. This orients you in the codebase.
2. **Follow data flow**: Trace how data enters, transforms, and exits the relevant subsystem. This reveals the real architecture.
3. **Check boundaries**: Look at interfaces between subsystems — API contracts, shared types, event schemas. These are where plans most often miss details.
4. **Inspect tests**: Existing tests reveal intended behavior, edge cases the original author considered, and the testing conventions you should follow.
5. **Review configuration**: Config files, environment variables, and feature flags often contain hidden constraints and defaults.

## Synthesizing Findings

Before engaging the user, organize what you've learned:

- **What you now know**: Concrete facts about the codebase relevant to the task
- **What you suspect but haven't confirmed**: Hypotheses that need one more search to verify
- **What you cannot discover**: True unknowns that require user input (preferences, business context, undocumented decisions)

This synthesis directly feeds Phase 2 — you'll know exactly which questions to ask and which to skip.

## Common Pitfalls

- **Exploring too broadly**: Stay focused on the area relevant to the task. Reading the entire codebase is not exploration; it's procrastination.
- **Stopping too early**: One file is rarely enough. Follow at least one level of imports/dependencies to understand the context.
- **Ignoring test files**: Tests are documentation. They tell you what behavior is expected and what edge cases matter.
- **Skipping config/CI**: These files contain constraints that aren't visible in application code.
