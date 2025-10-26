# Agent Workflow Notes

1. Check which branch you are on before making changes. Use `git status -sb` (shows branch + tracking) and `git rev-parse --abbrev-ref HEAD` if you only need the branch name.
2. Confirm the working tree is clean. If `git status -sb` prints any paths beyond the branch header, treat the tree as dirty.
3. When the tree is dirty:
   - Do **not** delete, reset, checkout, or otherwise touch files you did not modify. A recurring mistake is trying to clean “unrelated” files and overwriting the user’s work—do not repeat it.
   - Do **not** stage, commit, push, or open PRs unless the user explicitly instructs you to resolve the existing changes.
   - Surface the situation to the user and wait for guidance.
4. If you began on a clean tree and have completed a coherent scope of work, you may suggest staging, committing, opening PRs, bumping versions, or publishing—follow the project’s conventions.
5. When unsure about untracked or modified files, ask the user before running any Git operation that could overwrite their work.
6. Run the Black formatter on all touched Python files before every commit (`black <paths>`).

## GitHub CLI Examples
- Push the current branch: `git push --set-upstream origin <branch>`
- Open a pull request: `gh pr create --title "<title>" --body "<body>"`
- Read the current PR: `gh pr view`
- Read a specific PR: `gh pr view <number>`
