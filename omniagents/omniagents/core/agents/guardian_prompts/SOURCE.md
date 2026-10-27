# Source

`policy_template.md` and `policy.md` are Codex's guardian prompts,
`codex-rs/prompts/templates/guardian/` in https://github.com/openai/codex
(commit 970111f6cb), Apache License 2.0.

Changed from the original, and why:

- `policy_template.md`, "Your Restrictions": the reviewer here can read files
  (`read_file`, `list_directory`) but has no command tool, so the line about
  running read-only commands says so.

`policy.md` is the default organization policy. A project or user replaces it
with `approvals.guardian_policy` and adds to it with
`approvals.guardian_extra_policy` (Codex's `auto_review.policy` and
`auto_review.extra_policy`).
