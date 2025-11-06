/**
 * Rich metadata types for tool outputs and approval dialogs
 * Matches Bubbletea's RichMetadata structure exactly
 */

export interface RichMetadata {
  display_type?: string;
  summary?: string;
  preview?: string;
  truncated?: boolean;
  metadata?: Record<string, unknown>;
  chart?: Record<string, unknown>;
  table?: Record<string, unknown>;
  value?: Record<string, unknown>; // Raw value payload (e.g., diff_lines)
}

/**
 * Parse rich metadata from tool result metadata
 * Matches Bubbletea's parseRichMetadata function
 */
export function parseRichMetadata(metadata?: Record<string, unknown>): RichMetadata | null {
  if (!metadata) {
    return null;
  }

  const rich: RichMetadata = {};

  if (typeof metadata.display_type === 'string') {
    rich.display_type = metadata.display_type;
  }

  if (typeof metadata.summary === 'string') {
    rich.summary = metadata.summary;
  }

  if (typeof metadata.preview === 'string') {
    rich.preview = metadata.preview;
  }

  if (typeof metadata.truncated === 'boolean') {
    rich.truncated = metadata.truncated;
  }

  if (metadata.metadata && typeof metadata.metadata === 'object') {
    rich.metadata = metadata.metadata as Record<string, unknown>;
  }

  if (metadata.chart && typeof metadata.chart === 'object') {
    rich.chart = metadata.chart as Record<string, unknown>;
  }

  if (metadata.table && typeof metadata.table === 'object') {
    rich.table = metadata.table as Record<string, unknown>;
  }

  if (metadata.value && typeof metadata.value === 'object') {
    rich.value = metadata.value as Record<string, unknown>;
  }

  // Only return if we have at least display_type
  return rich.display_type ? rich : null;
}
