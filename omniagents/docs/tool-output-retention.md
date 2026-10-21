# Tool output retention

Locally executed FunctionTools are bounded at the final SDK tool-list boundary,
including custom tools, dynamically discovered MCP tools, sandbox function tools,
and realtime agents. Text has a 40,000 UTF-8-byte budget per result (roughly 10,000
English-text tokens, not an exact tokenizer limit). Images and files remain native
SDK content blocks and are not covered by this text budget. Provider-hosted tools
are outside this local invocation boundary.

Shell output, file reads and background log reads no longer clip individual lines.
Shell/log `max_output_chars` remains accepted for compatibility and selects a smaller
byte budget, with a 512-byte minimum for retrieval instructions. Ordinary file line
ranges and log line selection still apply: the retained artifact is the complete
text returned by that invocation, not the entire source file or unselected log.
Upstream MCP/server truncation cannot be recovered by this mechanism.

Oversized text is saved atomically on the runtime host before a head/tail preview
is returned. The preview and retrieval instructions fit the budget. Only shortened
text enters model history; `output_retention` UI metadata carries an opaque reference.
`read_tool_output(reference, offset, limit, query)` retrieves up to 8,000 characters
or finds a literal query with surrounding context. Offsets are Unicode character
positions, not byte offsets. Follow `next_offset` for pages and `next_search_offset`
for later matches. Retrieval never reruns the original command.

Files live next to the scoped history database under `tool_outputs/<owner hash>/`.
The owner hash includes product, agent and session even with a shared history path.
Directories are created with mode 0700, files with 0600, and references are UUIDs,
not paths. Data survives worker restart and sandbox teardown on the same runtime
host. Session deletion and the existing retention sweep remove its saved outputs.
There is no separate expiration timer. Multi-host deployments need a shared runtime
storage directory to make references available after moving to another host.

The `tool_outputs` capability exposes the authorized, thread-scoped
`tool_outputs.read` server function without requiring a running sandbox. Omni Code
agents enable it, and the launcher offers paging and literal search in tool details.
If saving fails, the preview explicitly reports that omitted text is unavailable;
it never claims that the original command is safe to rerun.

Deploy the framework and Omni Code capability updates with the launcher reader.
Old transcripts without retention metadata render normally; their previously
omitted text cannot be restored retroactively.

## Automatic result history and JavaScript

All locally dispatched function-tool return values are now captured automatically,
including small and structured results. QuickJS can list/get those results and read
retained text by range or literal search; no explicit store call is required.
The full value returned by a tool is separate from its bounded model/UI preview.
Underlying tool/provider capture limits still apply. Session deletion purges both
the result index and retained text. See [QuickJS orchestration](quickjs-orchestration.md).
