/**
 * Rich tool output renderers
 * Matches Bubbletea's rich output rendering exactly
 */

import React from 'react';
import { Box, Text } from 'ink';
import type { RichMetadata } from '../types/richMetadata.js';
import { TokyoNightTheme } from '../theme.js';

/**
 * Render rich tool output based on display type
 * Matches Bubbletea's renderRichToolResultWithWidth
 */
export const RichToolOutput = React.memo<{ metadata: RichMetadata; plainOutput?: string }>(({ metadata, plainOutput }) => {
  const { display_type, summary, preview, truncated, value } = metadata;

  // Default summary from plain output if not provided
  let summaryText = summary || '';
  if (!summaryText && plainOutput) {
    const lines = plainOutput.split('\n');
    if (lines.length > 0) {
      summaryText = lines[0];
      if (summaryText.length > 80) {
        summaryText = summaryText.slice(0, 77) + '...';
      }
    }
  }

  // Render based on display type
  switch (display_type) {
    case 'diff':
      return (
        <Box flexDirection="column">
          <Text>  └─ {summaryText}</Text>
          {value && <RenderDiffPreview value={value} />}
        </Box>
      );

    case 'table':
      return (
        <Box flexDirection="column">
          <Text>  └─ {summaryText}</Text>
          {metadata.table && <RenderTable table={metadata.table} truncated={truncated} />}
        </Box>
      );

    case 'search_results':
      return (
        <Box flexDirection="column">
          <Text>  └─ {summaryText}</Text>
          {preview && <RenderSearchResults preview={preview} truncated={truncated} />}
        </Box>
      );

    case 'directory_listing':
      return (
        <Box flexDirection="column">
          <Text>  └─ {summaryText}</Text>
          {preview && <RenderDirectoryListing preview={preview} metadata={metadata.metadata} truncated={truncated} />}
        </Box>
      );

    case 'error':
      return (
        <Box flexDirection="column">
          <Text>  └─ <Text color={TokyoNightTheme.error}>{summaryText}</Text></Text>
          {preview && <RenderErrorPreview preview={preview} truncated={truncated} />}
        </Box>
      );

    default:
      // Default: plain text rendering
      return (
        <Box flexDirection="column">
          <Text>  └─ {summaryText}</Text>
          {preview && <RenderPlainPreview preview={preview} truncated={truncated} />}
        </Box>
      );
  }
}, (prev, next) => {
  // Only re-render if metadata or plainOutput changed
  return prev.metadata === next.metadata && prev.plainOutput === next.plainOutput;
});

/**
 * Render diff preview from value.diff_lines
 * Matches Bubbletea's renderDiffToolPreviewWithWidth
 */
const RenderDiffPreview = React.memo<{ value: Record<string, unknown> }>(({ value }) => {
  // Extract diff_lines from value
  const diffLines = value.diff_lines as string[] | undefined;
  if (!diffLines || diffLines.length === 0) {
    return null;
  }

  let oldLine = 0;
  let newLine = 0;
  let inHunk = false;

  return (
    <Box flexDirection="column" paddingLeft={5}>
      {diffLines.map((line, idx) => {
        const lineStr = line.replace(/\n$/, '');

        // Skip file headers
        if (lineStr.startsWith('+++') || lineStr.startsWith('---')) {
          return null;
        }

        // Hunk header
        if (lineStr.startsWith('@@')) {
          const matches = lineStr.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
          if (matches) {
            oldLine = parseInt(matches[1], 10);
            newLine = parseInt(matches[2], 10);
            inHunk = true;
          }
          return <Text key={idx} dimColor>{lineStr}</Text>;
        }

        if (!inHunk) {
          return <Text key={idx} dimColor>{lineStr}</Text>;
        }

        // Addition
        if (lineStr.startsWith('+') && !lineStr.startsWith('+++')) {
          const content = lineStr.slice(1);
          const result = (
            <Text key={idx} color={TokyoNightTheme.info}>
              {`${newLine.toString().padStart(4, ' ')} + ${content}`}
            </Text>
          );
          newLine++;
          return result;
        }

        // Deletion
        if (lineStr.startsWith('-') && !lineStr.startsWith('---')) {
          const content = lineStr.slice(1);
          const result = (
            <Text key={idx} color={TokyoNightTheme.error}>
              {`${oldLine.toString().padStart(4, ' ')} - ${content}`}
            </Text>
          );
          oldLine++;
          return result;
        }

        // Context
        const result = (
          <Text key={idx} dimColor>
            {`${newLine.toString().padStart(4, ' ')}   ${lineStr}`}
          </Text>
        );
        oldLine++;
        newLine++;
        return result;
      })}
    </Box>
  );
}, (prev, next) => prev.value === next.value);

/**
 * Render table from table metadata
 * Matches Bubbletea's renderTableRichWithWidth (simplified)
 */
const RenderTable = React.memo<{ table: Record<string, unknown>; truncated?: boolean }>(({ table, truncated }) => {
  const rows = table.rows as Array<Array<unknown>> | undefined;
  if (!rows || rows.length === 0) {
    return null;
  }

  return (
    <Box flexDirection="column" paddingLeft={5}>
      {rows.slice(0, 10).map((row, idx) => (
        <Text key={idx}>
          {row.map(cell => String(cell)).join(', ')}
        </Text>
      ))}
      {truncated && rows.length > 10 && (
        <Text dimColor italic>... {rows.length - 10} more rows</Text>
      )}
    </Box>
  );
}, (prev, next) => prev.table === next.table && prev.truncated === next.truncated);

/**
 * Render search results
 * Matches Bubbletea's formatSearchResults
 */
const RenderSearchResults = React.memo<{ preview: string; truncated?: boolean }>(({ preview, truncated }) => {
  const lines = preview.split('\n').slice(0, 10);

  return (
    <Box flexDirection="column" paddingLeft={5}>
      {lines.map((line, idx) => {
        // Filename line (e.g., "path/to/file: 3 matches")
        if (line.includes(': ') && line.includes('match')) {
          return <Text key={idx} bold>{line}</Text>;
        }

        // Match line (e.g., "Line 42: # TODO: implement")
        if (line.includes('Line ')) {
          const parts = line.split(':', 2);
          if (parts.length === 2) {
            return (
              <Text key={idx}>
                <Text dimColor>{parts[0]}:</Text>
                {parts[1]}
              </Text>
            );
          }
        }

        return <Text key={idx}>{line}</Text>;
      })}
      {truncated && <Text dimColor italic>... more results</Text>}
    </Box>
  );
}, (prev, next) => prev.preview === next.preview && prev.truncated === next.truncated);

/**
 * Render directory listing
 * Matches Bubbletea's formatDirectoryListing
 */
const RenderDirectoryListing = React.memo<{
  preview: string;
  metadata?: Record<string, unknown>;
  truncated?: boolean;
}>(({
  preview,
  metadata,
  truncated
}) => {
  const lines = preview.split('\n').slice(0, 10);

  return (
    <Box flexDirection="column" paddingLeft={5}>
      {lines.map((line, idx) => {
        // Directories (end with /)
        if (line.endsWith('/')) {
          return <Text key={idx} bold>{line}</Text>;
        }

        // Executables
        if (line.includes('.exe') || line.includes('.sh') ||
            line.includes('.bat') || line.startsWith('*')) {
          return <Text key={idx}>{line}</Text>;
        }

        // Regular files
        return <Text key={idx}>{line}</Text>;
      })}
      {truncated && metadata && (
        <Text dimColor italic>
          ... {metadata.file_count} files, {metadata.dir_count} directories total
        </Text>
      )}
    </Box>
  );
}, (prev, next) => prev.preview === next.preview && prev.metadata === next.metadata && prev.truncated === next.truncated);

/**
 * Render error preview with red styling
 */
const RenderErrorPreview = React.memo<{ preview: string; truncated?: boolean }>(({ preview, truncated }) => {
  const lines = preview.split('\n').slice(0, 10);

  return (
    <Box flexDirection="column" paddingLeft={5}>
      {lines.map((line, idx) => (
        <Text key={idx} color={TokyoNightTheme.error}>{line}</Text>
      ))}
      {truncated && <Text dimColor italic>... more error output</Text>}
    </Box>
  );
}, (prev, next) => prev.preview === next.preview && prev.truncated === next.truncated);

/**
 * Render plain text preview (default)
 */
const RenderPlainPreview = React.memo<{ preview: string; truncated?: boolean }>(({ preview, truncated }) => {
  const lines = preview.split('\n').slice(0, 10);

  return (
    <Box flexDirection="column" paddingLeft={5}>
      {lines.map((line, idx) => (
        <Text key={idx} color={TokyoNightTheme.toolResult}>{line}</Text>
      ))}
      {truncated && <Text dimColor italic>... more output</Text>}
    </Box>
  );
}, (prev, next) => prev.preview === next.preview && prev.truncated === next.truncated);
