/**
 * Complete address of a materialized AgentHost execution environment.
 *
 * The generation is part of the address: re-materializing an environment can
 * reuse its id with a newer generation, and Omniagents rejects stale callers
 * when this value is supplied. Workspace RPCs must carry all three fields.
 */
export type ExecutionTarget = {
  workspaceId: string;
  environmentId: string;
  environmentGeneration: number;
};

/**
 * Per-dispatch run intent: merged onto the conversation's own variables when
 * the run starts.
 */
export type RunOverrides = {
  /** Prepended to the column's existing additional_instructions. */
  additionalInstructions?: string;
  /** Session approval reviewer: 'auto' routes approvals to the guardian. */
  approvalsReviewer?: 'user' | 'auto';
  /** Approval policy override; replaces the column's default. */
  safeToolOverrides?: {
    safe_tool_names?: string[];
    safe_tool_patterns?: string[];
    safe_mcp_tools?: { server_label: string; tool_name: string }[];
  };
};
