/**
 * The experimental GUI-protocol operations this client opts into at
 * `initialize` (`capabilities.experimental_operations`).
 *
 * The server gates BOTH sides on this list: experimental requests reject
 * with -32013 unless granted, and experimental notifications are silently
 * withheld from the channel unless granted — so notification names belong
 * in the list too (omniagents/rpc/server.py, `experimental_grants`).
 *
 * This file is kept identical to the Ink client's
 * `backends/ink/tui/src/protocol/experimental.ts`, minus nothing: both
 * reference clients consume the same contract, and a capability one can
 * reach and the other cannot is a difference nobody chose.
 *
 * The `agent_host_*` control-plane operations are deliberately absent:
 * they authenticate with the launcher's control credential, which a
 * browser client never holds, so a grant would be inert.
 *
 * Two guards keep this file honest:
 * - compile time: every name must be a key of the generated method /
 *   notification maps, so a typo or a protocol regen that renames an
 *   operation fails `tsc`;
 * - test time: `experimental.test.ts` diffs these lists against
 *   `protocol/openrpc/inventory.json`, so a regen that adds or retires an
 *   experimental operation fails vitest until this file is updated.
 */

import type { RpcMethodMap, RpcNotificationMap } from '@omniagents/client/protocol/gui-v2'

export const EXPERIMENTAL_REQUESTS = [

  // Session control
  'fork_session',
  'set_session_hold',
  'queue_status',
  'enqueue_notification',
  'update_session_variables',
  'set_client_status',
  'disable_tool',
  'disable_mcp_server',
  'report_incident',
  'export_session',
  // Who reviews tool calls / plan completions for the session. Setting a
  // non-human reviewer is what turns the approval dialogs into the
  // `*_reviewed` notifications below.
  'set_session_approvals',
  'set_session_workflow',
  // Layered configuration
  'get_config',
  'validate_config',
  'write_config',
  // MCP content access
  'mcp_read_resource',
  'mcp_call_tool',
  'mcp_get_prompt',
  // Workspace filesystem watch & chunked transfer
  'fs_watch',
  'fs_unwatch',
  'fs_list',
  'fs_stat',
  'fs_download_open',
  'fs_download_read',
  'fs_download_close',
  'fs_upload_open',
  'fs_upload_chunk',
  'fs_upload_commit',
  'fs_upload_abort',
  // Workspace mutations
  'fs_delete',
  'fs_mkdir',
  'fs_rename',
  // Typed Git operations
  'git_list_repositories',
  'git_status',
  'git_diff',
  'git_log',
  'git_list_branches',
  'git_list_worktrees',
  'git_conflicts',
  'git_stage',
  'git_unstage',
  'git_discard',
  'git_commit',
  'git_checkout',
  'git_reset',
  'git_fetch',
  'git_pull',
  'git_push',
  // Digest-based status subscription: the server pushes
  // `git_status_changed` instead of the panel re-polling `git_status`.
  'git_status_watch',
  'git_status_unwatch',
  // Thread organization & lifecycle
  'list_threads',
  'search_threads',
  'update_thread',
  'list_thread_descendants',
  'export_thread',
  'purge_threads',
  // Durable plans & aggregated run diffs
  'get_plan',
  'get_run_diff',] as const satisfies readonly (keyof RpcMethodMap)[]

export const EXPERIMENTAL_NOTIFICATIONS = [

  'session_forked',
  'session_variables_changed',
  'fs_events',
  'fs_rescan_required',
  'fs_transfer_progress',
  'git_operation_progress',
  'git_status_changed',
  'thread_updated',
  'item_updated',
  // Delivered when a non-human reviewer settles a decision the TUI would
  // otherwise have prompted for. Granting them costs nothing while
  // nothing listens, and withholding the grant is what would make a
  // guardian-reviewed session look like one where nothing happened.
  'tool_approval_reviewed',
  'plan_completion_reviewed',] as const satisfies readonly (keyof RpcNotificationMap)[]

/** The full opt-in list sent as `capabilities.experimental_operations`. */
export const REQUESTED_EXPERIMENTAL_OPERATIONS: readonly string[] = [
  ...EXPERIMENTAL_REQUESTS,
  ...EXPERIMENTAL_NOTIFICATIONS,
]
