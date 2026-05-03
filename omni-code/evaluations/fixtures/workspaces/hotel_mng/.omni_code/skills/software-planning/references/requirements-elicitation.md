# Requirements Elicitation

Detailed guidance for Phase 2 (Intent Chat) and the question discipline used throughout planning.

## Question Framework

For any planning task, work toward clarity on these six dimensions. Not all apply to every task — use judgment about which matter.

### 1. Goal
- What outcome does the user want?
- What problem are they solving?
- Why now? (Often reveals hidden constraints)

### 2. Success Criteria
- How will they know it works?
- What does "done" look like?
- Are there measurable targets (performance, coverage, adoption)?

### 3. Scope
- What is explicitly in scope?
- What is explicitly out of scope?
- Is this an MVP / v1 or a complete solution?
- Are there follow-up phases planned?

### 4. Constraints
- Performance requirements (latency, throughput, resource limits)
- Compatibility requirements (browser support, API versions, backward compat)
- Timeline or urgency
- Dependencies on other teams, services, or releases

### 5. Current State
- What exists today? What's working, what's broken?
- Has this been attempted before? What happened?
- Are there related systems or prior art to be aware of?

### 6. Preferences
- Naming, style, or pattern preferences
- Tradeoff leanings (e.g., "prefer simple over performant")
- Strong opinions about specific libraries, patterns, or approaches

## Tradeoff Templates

When presenting tradeoffs to the user, use this structure:

### Binary Tradeoff
> **[Decision point]**: We can optimize for **[A]** or **[B]**.
> - **Option A: [Name]** — [1-sentence description]. Better when [condition].
> - **Option B: [Name]** — [1-sentence description]. Better when [condition].
> - **Recommended: [A or B]** because [reason tied to this specific context].

### Multi-Option Tradeoff
> **[Decision point]**: There are a few approaches:
> 1. **[Name]** — [1-sentence description]. Pros: [X]. Cons: [Y].
> 2. **[Name]** — [1-sentence description]. Pros: [X]. Cons: [Y].
> 3. **[Name]** — [1-sentence description]. Pros: [X]. Cons: [Y].
> - **Recommended: [Option N]** because [reason].

### Common Tradeoff Pairs

These come up frequently in planning. Recognize them early:

- **Simplicity vs. flexibility**: Hardcode now vs. make configurable
- **Consistency vs. speed**: Follow existing patterns vs. use a better approach
- **Scope vs. timeline**: Build it right vs. build it fast
- **Safety vs. convenience**: Strict validation vs. permissive handling
- **Abstraction vs. directness**: Create a reusable layer vs. inline the logic
- **Migration vs. replacement**: Evolve the existing code vs. rewrite

## Presenting Options

When asking the user to choose:

1. **Always offer 2-4 options** — fewer than 2 isn't a choice; more than 4 is overwhelming
2. **Make options mutually exclusive** — the user should pick exactly one
3. **Always include a recommendation** — don't make the user decide without guidance
4. **Explain the recommendation** — tie it to the specific context, not generic best practices
5. **Keep options concrete** — "Use Redis for caching" not "Consider a caching layer"
6. **Don't include filler options** — every option should be genuinely viable

## Knowing When to Stop Asking

Stop asking questions and move to Phase 3 when:

- You can state the goal, success criteria, and scope without hedging
- All high-impact ambiguities are resolved
- Remaining unknowns are low-impact and can be decided with reasonable defaults
- The user is showing signs of "just build it" energy (respect this — record assumptions and move on)

## Anti-Patterns

- **Interrogation mode**: Asking too many questions in one turn. Batch related questions; max 3-4 per turn.
- **Obvious questions**: Asking things you could have discovered in Phase 1.
- **Leading questions**: Phrasing questions to steer toward your preferred answer. Present options neutrally.
- **Premature planning**: Jumping to implementation details before intent is clear.
- **Infinite clarification**: Continuing to ask when the user has given enough to work with. Use good defaults for the rest.
