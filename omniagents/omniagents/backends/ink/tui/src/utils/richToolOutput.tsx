/**
 * Rich tool output renderers
 * Matches Bubbletea's rich output rendering exactly
 */

import React from "react";
import { Box, Text } from "ink";
import type { RichMetadata } from "../types/richMetadata.js";
import { TokyoNightTheme } from "../theme.js";

/**
 * Render rich tool output based on display type
 * Matches Bubbletea's renderRichToolResultWithWidth
 */
export const RichToolOutput = React.memo<{
  metadata: RichMetadata;
  plainOutput?: string;
}>(
  ({ metadata, plainOutput }) => {
    const { display_type, summary, preview, truncated, value } = metadata;

    const hasDetails =
      (display_type === "diff" && Boolean(value)) ||
      (display_type === "table" && Boolean(metadata.table)) ||
      (display_type !== "diff" && display_type !== "table" && Boolean(preview));

    // Default summary from plain output if not provided
    let summaryText = summary || "";
    if (!summaryText && plainOutput) {
      const lines = plainOutput.split("\n");
      if (lines.length > 0) {
        summaryText = lines[0];
        if (summaryText.length > 80) {
          summaryText = summaryText.slice(0, 77) + "...";
        }
      }
    }

    // Render based on display type
    switch (display_type) {
      case "diff":
        return (
          <Box flexDirection="column">
            <Text color={TokyoNightTheme.toolResult}>
              {"  └─ "}
              {summaryText}
            </Text>
            {hasDetails && <Text> </Text>}
            {value && <RenderDiffPreview value={value} />}
          </Box>
        );

      case "table":
        return (
          <Box flexDirection="column">
            <Text color={TokyoNightTheme.toolResult}>
              {"  └─ "}
              {summaryText}
            </Text>
            {hasDetails && <Text> </Text>}
            {metadata.table && (
              <RenderTable table={metadata.table} truncated={truncated} />
            )}
          </Box>
        );

      case "search_results":
        return (
          <Box flexDirection="column">
            <Text color={TokyoNightTheme.toolResult}>
              {"  └─ "}
              {summaryText}
            </Text>
            {hasDetails && <Text> </Text>}
            {preview && (
              <RenderSearchResults preview={preview} truncated={truncated} />
            )}
          </Box>
        );

      case "directory_listing":
        return (
          <Box flexDirection="column">
            <Text color={TokyoNightTheme.toolResult}>
              {"  └─ "}
              {summaryText}
            </Text>
            {hasDetails && <Text> </Text>}
            {preview && (
              <RenderDirectoryListing
                preview={preview}
                metadata={metadata.metadata}
                truncated={truncated}
              />
            )}
          </Box>
        );

      case "error":
        return (
          <Box flexDirection="column">
            <Text color={TokyoNightTheme.toolResult}>
              {"  └─ "}
              <Text color={TokyoNightTheme.error}>{summaryText}</Text>
            </Text>
            {hasDetails && <Text> </Text>}
            {preview && (
              <RenderErrorPreview preview={preview} truncated={truncated} />
            )}
          </Box>
        );

      case "command":
        return (
          <Box flexDirection="column">
            <Text color={TokyoNightTheme.toolResult}>
              {"  └─ "}
              {summaryText}
            </Text>
            {hasDetails && <Text> </Text>}
            {preview && (
              <RenderCommandOutput
                preview={preview}
                metadata={metadata.metadata}
                truncated={truncated}
              />
            )}
          </Box>
        );

      case "file_content":
        return (
          <Box flexDirection="column">
            <Text color={TokyoNightTheme.toolResult}>
              {"  └─ "}
              {summaryText}
            </Text>
            {hasDetails && <Text> </Text>}
            {preview && (
              <RenderFileContent
                preview={preview}
                metadata={metadata.metadata}
                truncated={truncated}
              />
            )}
          </Box>
        );

      case "web_content":
        return (
          <Box flexDirection="column">
            <Text color={TokyoNightTheme.toolResult}>
              {"  └─ "}
              {summaryText}
            </Text>
            {hasDetails && <Text> </Text>}
            {preview && (
              <RenderWebContent preview={preview} truncated={truncated} />
            )}
          </Box>
        );

      default:
        // Default: plain text rendering
        return (
          <Box flexDirection="column">
            <Text color={TokyoNightTheme.toolResult}>
              {"  └─ "}
              {summaryText}
            </Text>
            {hasDetails && <Text> </Text>}
            {preview && (
              <RenderPlainPreview preview={preview} truncated={truncated} />
            )}
          </Box>
        );
    }
  },
  (prev, next) => {
    // Only re-render if metadata or plainOutput changed
    return (
      prev.metadata === next.metadata && prev.plainOutput === next.plainOutput
    );
  },
);

/**
 * Render diff preview from value.diff_lines
 * Matches Bubbletea's renderDiffToolPreviewWithWidth
 */
const RenderDiffPreview = React.memo<{ value: Record<string, unknown> }>(
  ({ value }) => {
    // Extract diff_lines from value
    const diffLines = value.diff_lines as string[] | undefined;
    if (!diffLines || diffLines.length === 0) {
      return null;
    }

    let oldLine = 0;
    let newLine = 0;
    let inHunk = false;

    return (
      <Box flexDirection="column" paddingLeft={4}>
        {diffLines.map((line, idx) => {
          const lineStr = line.replace(/\n$/, "");

          // Skip file headers
          if (lineStr.startsWith("+++") || lineStr.startsWith("---")) {
            return null;
          }

          // Hunk header
          if (lineStr.startsWith("@@")) {
            const matches = lineStr.match(
              /@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/,
            );
            if (matches) {
              oldLine = parseInt(matches[1], 10);
              newLine = parseInt(matches[2], 10);
              inHunk = true;
            }
            return (
              <Text key={idx} color={TokyoNightTheme.defaultText}>
                {lineStr}
              </Text>
            );
          }

          if (!inHunk) {
            return (
              <Text key={idx} color={TokyoNightTheme.defaultText}>
                {lineStr}
              </Text>
            );
          }

          // Addition
          if (lineStr.startsWith("+") && !lineStr.startsWith("+++")) {
            const content = lineStr.slice(1);
            const result = (
              <Text key={idx} color={TokyoNightTheme.info}>
                {`${newLine.toString().padStart(4, " ")} + ${content}`}
              </Text>
            );
            newLine++;
            return result;
          }

          // Deletion
          if (lineStr.startsWith("-") && !lineStr.startsWith("---")) {
            const content = lineStr.slice(1);
            const result = (
              <Text key={idx} color={TokyoNightTheme.error}>
                {`${oldLine.toString().padStart(4, " ")} - ${content}`}
              </Text>
            );
            oldLine++;
            return result;
          }

          // Context
          const result = (
            <Text key={idx} color={TokyoNightTheme.defaultText}>
              {`${newLine.toString().padStart(4, " ")}   ${lineStr}`}
            </Text>
          );
          oldLine++;
          newLine++;
          return result;
        })}
      </Box>
    );
  },
  (prev, next) => prev.value === next.value,
);

/**
 * Render table from table metadata
 * Matches Bubbletea's renderTableRichWithWidth (simplified)
 */
const RenderTable = React.memo<{
  table: Record<string, unknown>;
  truncated?: boolean;
}>(
  ({ table, truncated }) => {
    const rows = table.rows as Array<Array<unknown>> | undefined;
    if (!rows || rows.length === 0) {
      return null;
    }

    return (
      <Box flexDirection="column" paddingLeft={4}>
        {rows.slice(0, 10).map((row, idx) => (
          <Text key={idx} color={TokyoNightTheme.toolResult}>
            {row.map((cell) => String(cell)).join(", ")}
          </Text>
        ))}
        {truncated && rows.length > 10 && (
          <Text color={TokyoNightTheme.defaultText} italic>
            ... {rows.length - 10} more rows
          </Text>
        )}
      </Box>
    );
  },
  (prev, next) =>
    prev.table === next.table && prev.truncated === next.truncated,
);

/**
 * Render search results
 * Matches Bubbletea's formatSearchResults
 */
const RenderSearchResults = React.memo<{
  preview: string;
  truncated?: boolean;
}>(
  ({ preview, truncated }) => {
    const lines = preview.split("\n").slice(0, 10);

    return (
      <Box flexDirection="column" paddingLeft={4}>
        {lines.map((line, idx) => {
          // Filename line (e.g., "path/to/file: 3 matches")
          if (line.includes(": ") && line.includes("match")) {
            return (
              <Text key={idx} bold color={TokyoNightTheme.toolResult}>
                {line}
              </Text>
            );
          }

          // Match line (e.g., "Line 42: # TODO: implement")
          if (line.includes("Line ")) {
            const parts = line.split(":", 2);
            if (parts.length === 2) {
              return (
                <Text key={idx} color={TokyoNightTheme.toolResult}>
                  <Text color={TokyoNightTheme.defaultText}>{parts[0]}:</Text>
                  {parts[1]}
                </Text>
              );
            }
          }

          return (
            <Text key={idx} color={TokyoNightTheme.toolResult}>
              {line}
            </Text>
          );
        })}
        {truncated && (
          <Text color={TokyoNightTheme.defaultText} italic>
            ... more results
          </Text>
        )}
      </Box>
    );
  },
  (prev, next) =>
    prev.preview === next.preview && prev.truncated === next.truncated,
);

/**
 * Render directory listing
 * Tool provides: total_entries, entries_returned, file_count, dir_count, symlink_count, was_truncated
 */
const RenderDirectoryListing = React.memo<{
  preview: string;
  metadata?: Record<string, unknown>;
  truncated?: boolean;
}>(
  ({ preview, metadata, truncated }) => {
    const lines = preview.split("\n").slice(0, 15);
    const totalEntries = metadata?.total_entries as number | undefined;
    const wasTruncated = metadata?.was_truncated as boolean | undefined;
    const fileCount = metadata?.file_count as number | undefined;
    const dirCount = metadata?.dir_count as number | undefined;

    return (
      <Box flexDirection="column" paddingLeft={4}>
        {lines.map((line, idx) => {
          // Entry lines with numbering (e.g., "  1. src/")
          const entryMatch = line.match(/^\s*(\d+)\.\s+(.+)$/);
          if (entryMatch) {
            const entryNum = entryMatch[1];
            const entryName = entryMatch[2];
            const isDir = entryName.endsWith("/");
            const isSymlink = entryName.includes(" → ");

            return (
              <Text key={idx} color={TokyoNightTheme.toolResult}>
                <Text color={TokyoNightTheme.defaultText}>
                  {entryNum.padStart(3, " ")}.{" "}
                </Text>
                <Text
                  bold={isDir}
                  color={
                    isSymlink
                      ? TokyoNightTheme.info
                      : TokyoNightTheme.toolResult
                  }
                >
                  {entryName}
                </Text>
              </Text>
            );
          }

          // Directories (end with /)
          if (line.endsWith("/")) {
            return (
              <Text key={idx} bold color={TokyoNightTheme.toolResult}>
                {line}
              </Text>
            );
          }

          // Regular files/entries
          return (
            <Text key={idx} color={TokyoNightTheme.toolResult}>
              {line}
            </Text>
          );
        })}
        {(truncated || wasTruncated) && (
          <Text color={TokyoNightTheme.defaultText} italic>
            {totalEntries !== undefined
              ? `... ${totalEntries.toLocaleString()} total entries`
              : fileCount !== undefined && dirCount !== undefined
                ? `... ${fileCount} files, ${dirCount} directories total`
                : "... more entries"}
          </Text>
        )}
      </Box>
    );
  },
  (prev, next) =>
    prev.preview === next.preview &&
    prev.metadata === next.metadata &&
    prev.truncated === next.truncated,
);

/**
 * Render error preview with red styling
 */
const RenderErrorPreview = React.memo<{ preview: string; truncated?: boolean }>(
  ({ preview, truncated }) => {
    const lines = preview.split("\n").slice(0, 10);

    return (
      <Box flexDirection="column" paddingLeft={4}>
        {lines.map((line, idx) => (
          <Text key={idx} color={TokyoNightTheme.error}>
            {line}
          </Text>
        ))}
        {truncated && (
          <Text color={TokyoNightTheme.defaultText} italic>
            ... more error output
          </Text>
        )}
      </Box>
    );
  },
  (prev, next) =>
    prev.preview === next.preview && prev.truncated === next.truncated,
);

/**
 * Render plain text preview (default)
 */
const RenderPlainPreview = React.memo<{ preview: string; truncated?: boolean }>(
  ({ preview, truncated }) => {
    const lines = preview.split("\n").slice(0, 10);

    return (
      <Box flexDirection="column" paddingLeft={4}>
        {lines.map((line, idx) => (
          <Text key={idx} color={TokyoNightTheme.toolResult}>
            {line}
          </Text>
        ))}
        {truncated && (
          <Text color={TokyoNightTheme.defaultText} italic>
            ... more output
          </Text>
        )}
      </Box>
    );
  },
  (prev, next) =>
    prev.preview === next.preview && prev.truncated === next.truncated,
);

/**
 * Render command (bash) output
 * Shows exit code status and timing information
 */
const RenderCommandOutput = React.memo<{
  preview: string;
  metadata?: Record<string, unknown>;
  truncated?: boolean;
}>(
  ({ preview, metadata, truncated }) => {
    const lines = preview.split("\n").slice(0, 15);
    const success = metadata?.success as boolean | undefined;
    const exitCode = metadata?.exit_code as number | undefined;
    const wallTimeMs = metadata?.wall_time_ms as number | undefined;
    const wasTruncated = metadata?.was_truncated as boolean | undefined;
    const charsTruncated = metadata?.chars_truncated as number | undefined;

    return (
      <Box flexDirection="column" paddingLeft={4}>
        {/* Status line */}
        {exitCode !== undefined && (
          <Text color={TokyoNightTheme.defaultText}>
            <Text
              color={success ? TokyoNightTheme.info : TokyoNightTheme.error}
            >
              Exit: {exitCode}
            </Text>
            {wallTimeMs !== undefined && (
              <Text color={TokyoNightTheme.defaultText}> ({wallTimeMs}ms)</Text>
            )}
            {wasTruncated && charsTruncated && (
              <Text color={TokyoNightTheme.defaultText}>
                {" "}
                [{charsTruncated.toLocaleString()} chars truncated]
              </Text>
            )}
          </Text>
        )}
        {/* Output lines */}
        {lines.map((line, idx) => {
          // Highlight stderr lines
          if (line.startsWith("[stderr]")) {
            return (
              <Text key={idx} color={TokyoNightTheme.warning}>
                {line}
              </Text>
            );
          }
          return (
            <Text key={idx} color={TokyoNightTheme.toolResult}>
              {line}
            </Text>
          );
        })}
        {truncated && (
          <Text color={TokyoNightTheme.defaultText} italic>
            ... more output
          </Text>
        )}
      </Box>
    );
  },
  (prev, next) =>
    prev.preview === next.preview &&
    prev.metadata === next.metadata &&
    prev.truncated === next.truncated,
);

/**
 * Render file content with line numbers
 * Tool provides: total_file_lines, lines_returned, lines_truncated_count, start_line, end_line
 */
const RenderFileContent = React.memo<{
  preview: string;
  metadata?: Record<string, unknown>;
  truncated?: boolean;
}>(
  ({ preview, truncated }) => {
    const lines = preview.split("\n").slice(0, 15);
    return (
      <Box flexDirection="column" paddingLeft={4}>
        {/* Content with line numbers preserved from L{n}: format */}
        {lines.map((line, idx) => {
          // Lines already have L{n}: prefix from the tool
          const lineNumMatch = line.match(/^L(\d+):/);
          if (lineNumMatch) {
            const lineNum = lineNumMatch[1];
            const content = line.slice(lineNumMatch[0].length);
            return (
              <Text key={idx} color={TokyoNightTheme.toolResult}>
                <Text color={TokyoNightTheme.defaultText}>
                  {lineNum.padStart(4, " ")}
                </Text>
                <Text color={TokyoNightTheme.toolResult}>{content}</Text>
              </Text>
            );
          }
          return (
            <Text key={idx} color={TokyoNightTheme.toolResult}>
              {line}
            </Text>
          );
        })}
        {truncated && (
          <Text color={TokyoNightTheme.defaultText} italic>
            ... more content
          </Text>
        )}
      </Box>
    );
  },
  (prev, next) =>
    prev.preview === next.preview &&
    prev.metadata === next.metadata &&
    prev.truncated === next.truncated,
);

/**
 * Render web content (fetched page)
 */
const RenderWebContent = React.memo<{
  preview: string;
  truncated?: boolean;
}>(
  ({ preview, truncated }) => {
    const lines = preview.split("\n").slice(0, 12);

    return (
      <Box flexDirection="column" paddingLeft={4}>
        {lines.map((line, idx) => (
          <Text key={idx} color={TokyoNightTheme.toolResult}>
            {line}
          </Text>
        ))}
        {truncated && (
          <Text color={TokyoNightTheme.defaultText} italic>
            ... more content
          </Text>
        )}
      </Box>
    );
  },
  (prev, next) =>
    prev.preview === next.preview && prev.truncated === next.truncated,
);
