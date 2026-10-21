# subagent_workspace fixture

Empty workspace used by `evaluations/scenarios/subagent_workspace.yml`.
Exists to validate that the workspace_root the eval environment sets
flows through the agent's tools and (when enabled) into spawned workers
and the explore sub-agent — i.e. that `execute_bash` and worker tools
all land in *this* tempdir, not the eval host's cwd.
