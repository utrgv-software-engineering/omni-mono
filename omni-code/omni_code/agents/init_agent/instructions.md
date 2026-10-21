Generate a file named AGENTS.md that serves as a contributor guide for this repository.
Your goal is to produce a clear, concise, and well-structured document with descriptive headings and actionable explanations for each section.
Follow the outline below, but adapt as needed — add sections if relevant, and omit those that do not apply to this project.

Document Requirements

- Title the document "Repository Guidelines".
- Use Markdown headings (#, ##, etc.) for structure.
- Keep the document concise. 200-400 words is optimal.
- Keep explanations short, direct, and specific to this repository.
- Provide examples where helpful (commands, directory paths, naming patterns).
- Maintain a professional, instructional tone.

Recommended Sections

Project Structure & Module Organization

- Outline the project structure, including where the source code, tests, and assets are located.

Build, Test, and Development Commands

- List key commands for building, testing, and running locally (e.g., npm test, make build).
- Briefly explain what each command does.

Coding Style & Naming Conventions

- Specify indentation rules, language-specific style preferences, and naming patterns.
- Include any formatting or linting tools used.

Testing Guidelines

- Identify testing frameworks and coverage requirements.
- State test naming conventions and how to run tests.

Commit & Pull Request Guidelines

- Summarize commit message conventions found in the project's Git history.
- Outline pull request requirements (descriptions, linked issues, screenshots, etc.).

(Optional) Add other sections if relevant, such as Security & Configuration Tips, Architecture Overview, or Agent-Specific Instructions.

- Discover repo structure with list_directory and read_file. For finding files
  by pattern and searching contents, use execute_bash with `rg --files -g '<glob>'`
  and `rg <pattern>` — there are no glob/grep tools. `git log`/`git ls-files` are
  available the same way for history and convention questions.
- Apply changes using apply_patch or write_file. Deliver a final patch that writes/updates AGENTS.md.

Final response policy:
- Your final message must be EXACTLY one of the following single-line strings (no quotes, no code blocks, no extra text):
  - Created AGENTS.md
  - Updated AGENTS.md
  - Failed to create AGENTS.md
  - Failed to update AGENTS.md
- Choose based on what actually happened:
  - Use "Created AGENTS.md" only if you created a new AGENTS.md.
  - Use "Updated AGENTS.md" only if you modified an existing AGENTS.md.
  - If a create attempt did not apply or failed, use "Failed to create AGENTS.md".
  - If an update attempt did not apply or failed, use "Failed to update AGENTS.md".
