/**
 * Optimized message list — the semantic "beat" transcript.
 *
 * Prose renders bright and full; agent activity renders as dim one-line
 * beats at the verb level ("⌕ read 6 files"), grouped by
 * components/transcript-model.ts. Errors, cancellations and the
 * currently-running call break out of aggregation into their own rows.
 * Ctrl+O (store.transcriptDetail) flips to the mechanical view: every
 * tool call as a full row with args and rich output.
 *
 * When mounted inside a <Scrollable> (the common case), uses VirtualizedList
 * to render only rows in the visible viewport. Off-viewport items become
 * a single spacer Box of the correct measured height so the parent
 * Scrollable's scroll geometry stays accurate.
 *
 * Visible "frozen" rows (not the tail, not running) are wrapped in
 * <StaticRender> so their Yoga layout is cached. Falls back to a plain map
 * when no viewport info is available (tests, outside a Scrollable).
 */

import React, { useCallback, useMemo } from "react";
import { Box, Text, StaticRender, useStdout } from "ink";
import Spinner from "ink-spinner";
import { useMessages, useTranscriptDetail } from "../store/appStore.js";
import type { Message, ToolCallInfo } from "../store/appStore.js";
import type { ConversationRunDiff } from "../types/conversation.js";
import { changeColor, changeGlyph } from "./review-model.js";
import { Theme } from "../theme.js";
import { formatToolArgumentsSmart } from "../utils/formatArguments.js";
import { renderMarkdownCached } from "../utils/markdownCache.js";
import { parseRichMetadata } from "../types/richMetadata.js";
import { RichToolOutput } from "../utils/richToolOutput.js";
import { VirtualizedList } from "./shared/VirtualizedList.js";
import { useScrollViewport } from "./Scrollable.js";
import {
  errorDetailLines,
  groupTranscript,
  rendersNothing,
  salientArg,
  spanLine,
  type BeatSegment,
  type TranscriptRow,
} from "./transcript-model.js";

/**
 * Rows that belong to the same activity block and therefore pack tight
 * against each other. A blocking review is one: it sits under the beat
 * whose call it stopped, exactly as an error sits under the call that
 * failed, and a blank line on either side breaks that nesting.
 */
function isBeatRow(row: TranscriptRow | undefined): boolean {
  return (
    row !== undefined &&
    (row.type === "beats" || row.type === "error" || row.type === "review")
  );
}

export const MessageListOptimized = React.memo(() => {
  const messages = useMessages();
  const detail = useTranscriptDetail();
  const { stdout } = useStdout();
  const terminalWidth = stdout?.columns ?? 80;
  const viewport = useScrollViewport();

  // The beat projection is a pure single pass; detail mode bypasses it
  // and renders every message mechanically.
  const rows = useMemo<TranscriptRow[]>(
    () =>
      detail
        ? messages
            .map((message, index) => ({
              type: "message" as const,
              key: `msg-${message.role}-${index}`,
              message,
            }))
            .filter((row) => !rendersNothing(row.message))
        : groupTranscript(messages),
    [messages, detail],
  );

  const renderItem = useCallback(
    (row: TranscriptRow, index: number) => (
      <TranscriptRowItem
        row={row}
        // Adjacent activity rows pack tightly; the blank line separates
        // activity blocks from prose on both sides.
        tight={isBeatRow(row) && isBeatRow(rows[index + 1])}
        // The last row's padding would separate it from nothing. The
        // enclosing column already owns the gap to whatever follows
        // (gap={1} in App's scroll region), so padding here too made the
        // transcript→"Thinking…" boundary 2 rows while every other
        // boundary was 1 — against that container's own stated 1-row
        // rhythm. Row padding separates rows; the container separates
        // blocks.
        isLast={index === rows.length - 1}
      />
    ),
    [rows],
  );

  const lastIndex = rows.length - 1;
  const isStaticItem = useCallback(
    (row: TranscriptRow, index: number) => {
      if (index === lastIndex) return false;
      if (row.type === "beats" && row.running.length > 0) return false;
      if (row.type === "message" && row.message.toolCall?.running === true) {
        return false;
      }
      return true;
    },
    [lastIndex],
  );

  const keyExtractor = useCallback((row: TranscriptRow) => row.key, []);

  if (rows.length === 0) {
    return null;
  }

  // Inter-row spacing is owned by TranscriptRowItem itself (paddingBottom),
  // not by either render path's container. This keeps the virtualized and
  // fallback paths in lockstep and — crucially — keeps the spacing inside the
  // border-box that ink's ResizeObserver measures (getComputedHeight), so the
  // virtualized path's offsets and head/tail spacers stay accurate.
  if (viewport && viewport.innerHeight > 0) {
    return (
      <VirtualizedList<TranscriptRow>
        data={rows}
        renderItem={renderItem}
        keyExtractor={keyExtractor}
        estimatedItemHeight={2}
        containerHeight={viewport.innerHeight}
        scrollTop={viewport.scrollTop}
        width={terminalWidth}
        isStaticItem={isStaticItem}
      />
    );
  }

  // Fallback: render everything, with per-item StaticRender for frozen rows.
  return (
    <Box flexDirection="column">
      {rows.map((row, index) => {
        const canFreeze = isStaticItem(row, index);
        const item = renderItem(row, index);

        if (!canFreeze) {
          return <React.Fragment key={row.key}>{item}</React.Fragment>;
        }

        return (
          <StaticRender
            key={row.key}
            width={terminalWidth}
            deps={[
              row.type,
              row.type === "message" ? row.message.content : row.key,
              row.type === "message" ? row.message.toolCall?.output : undefined,
              row.type === "message"
                ? row.message.toolCall?.metadata
                : undefined,
              terminalWidth,
            ]}
          >
            {() => item}
          </StaticRender>
        );
      })}
    </Box>
  );
});

const TranscriptRowItem = React.memo<{
  row: TranscriptRow;
  tight: boolean;
  isLast: boolean;
}>(({ row, tight, isLast }) => {
  const body = (() => {
    switch (row.type) {
      case "beats":
        return (
          <BeatsRow
            segments={row.segments}
            running={row.running}
            reviewed={row.reviewed}
          />
        );
      case "error":
        return <ErrorRow message={row.message} />;
      case "review":
        return <ReviewRow message={row.message} />;
      default:
        return <MessageItem message={row.message} />;
    }
  })();

  if (body === null) {
    return null;
  }
  return (
    <Box flexDirection="column" paddingBottom={tight || isLast ? 0 : 1}>
      {body}
    </Box>
  );
});

// Activity rows sit under a blank line after their message: a spinner
// leads while work is in flight, a plain two-space indent once settled.
// Continuation/detail lines indent two further.
const ACTIVITY_INDENT = "  ";
const DETAIL_INDENT = "    ";
const EXPAND_HINT = " (ctrl+o to expand)";

/**
 * One activity line per span.
 * Live:    `⠋ Read 2 files, 1 search · reading src/app.ts… (ctrl+o to expand)`
 * Settled: `● Read 2 files, 2 searches (ctrl+o to expand)`
 * The spinner becomes the `●` bullet in place — same column, dim like
 * the line it marks.
 */
const BeatsRow = React.memo<{
  segments: BeatSegment[];
  running: ToolCallInfo[];
  reviewed: number;
}>(({ segments, running, reviewed }) => {
  const line = spanLine(segments, running, reviewed);
  const live = running.length > 0;
  return (
    <Box flexDirection="row">
      {live ? (
        <Text color={Theme.stateActive}>
          <Spinner type="dots" />{" "}
        </Text>
      ) : (
        <Text color={Theme.textChrome}>● </Text>
      )}
      <Box flexGrow={1}>
        <Text color={Theme.textChrome} wrap="truncate-end">
          {line}
          {EXPAND_HINT}
        </Text>
      </Box>
    </Box>
  );
});

/** A failed or cancelled call — never aggregated, capped detail inline. */
/**
 * A reviewer BLOCKED something — the plot, so it gets its own row next
 * to errors rather than folding into the span. The rationale rides here
 * because this is the row with room for it: on a folded review there is
 * nowhere to put it and no reason to.
 */
const ReviewRow = React.memo<{ message: Message }>(({ message }) => {
  const review = message.review;
  if (!review) return null;
  // Only a blocking review stopped something. The beat renderer never
  // routes an allowing one here, but the mechanical view (ctrl+o) shows
  // every message, and painting an approval in the blocked colour said
  // the opposite of what happened.
  const blocked = review.blocking;
  return (
    <Box flexDirection="column">
      <Box flexDirection="row">
        <Text>{ACTIVITY_INDENT}</Text>
        <Box flexGrow={1} flexBasis={0} minWidth={0}>
          {/* One shield: a reviewer acted. The colour is the verdict —
              the same split Desktop draws with ShieldX vs ShieldCheck. */}
          <Text color={blocked ? Theme.stateFailed : Theme.stateDone}>
            {"⛊ "}
          </Text>
          <Text color={Theme.textPrimary} wrap="truncate-end">
            {review.line}
          </Text>
        </Box>
      </Box>
      {review.rationale && (
        <Box flexDirection="row">
          <Text>{ACTIVITY_INDENT}</Text>
          <Text>{"  "}</Text>
          <Box flexGrow={1} flexBasis={0} minWidth={0}>
            <Text color={Theme.textChrome} wrap="truncate-end">
              {review.rationale}
            </Text>
          </Box>
        </Box>
      )}
    </Box>
  );
});

const ErrorRow = React.memo<{ message: Message }>(({ message }) => {
  const toolCall = message.toolCall!;
  const cancelled = toolCall.output === "[Cancelled]";
  const salient = useMemo(() => salientArg(toolCall.input), [toolCall.input]);
  const detail = useMemo(
    () => (cancelled ? [] : errorDetailLines(toolCall)),
    [cancelled, toolCall],
  );
  return (
    <Box flexDirection="column">
      <Box flexDirection="row">
        <Text>{ACTIVITY_INDENT}</Text>
        <Box flexGrow={1}>
          <Text color={cancelled ? Theme.stateBlocked : Theme.stateFailed}>
            ✗{" "}
          </Text>
          <Text color={Theme.textPrimary}>{toolCall.tool}</Text>
          {salient && (
            <Text color={Theme.textChrome} wrap="truncate-end">
              {" "}
              {salient}
            </Text>
          )}
          {cancelled && (
            <Text color={Theme.stateBlocked} italic>
              {" "}
              · cancelled by user
            </Text>
          )}
        </Box>
      </Box>
      {detail.map((line, index) => (
        <Box key={index} flexDirection="row">
          <Text>{DETAIL_INDENT}</Text>
          <Text color={Theme.textSecondary} wrap="truncate-end">
            {line}
          </Text>
        </Box>
      ))}
    </Box>
  );
});

// Individual message memoized with deep equality check
const MessageItem = React.memo<{ message: Message }>(
  ({ message }) => {
    switch (message.role) {
      case "user":
        return <UserMessage message={message} />;
      case "assistant":
        return <AssistantMessage message={message} />;
      case "system":
        if (message.runDiff) return <RunDiffCard diff={message.runDiff} />;
        return <SystemMessage message={message} />;
      case "tool_call":
        // Tools can opt out of the conversation log by setting
        // ui_metadata.hidden = true (e.g. task_* tools rendered by
        // the docked Tasks panel instead).
        if (message.toolCall?.metadata?.hidden === true) {
          return null;
        }
        return <ToolCallMessage message={message} />;
      case "review":
        return <ReviewRow message={message} />;
      case "approval_request":
        // Approval requests shown in ApprovalDialog, not in message history
        return null;
      default:
        return null;
    }
  },
  (prev, next) => {
    // Deep equality check - only re-render if content actually changed
    if (prev.message.role !== next.message.role) return false;
    if (prev.message.content !== next.message.content) return false;

    // For tool calls, check if output changed
    if (prev.message.toolCall && next.message.toolCall) {
      return (
        prev.message.toolCall.output === next.message.toolCall.output &&
        prev.message.toolCall.running === next.message.toolCall.running &&
        prev.message.toolCall.metadata === next.message.toolCall.metadata
      );
    }

    // If one has toolCall and the other doesn't, they're different
    if (prev.message.toolCall !== next.message.toolCall) return false;

    return true;
  },
);

const UserMessage = React.memo<{ message: Message }>(
  ({ message }) => {
    const prefix = "> ";
    const prefixWidth = prefix.length;

    // Split on newlines and render one row per line, matching
    // AssistantMessage. A single <Text> here would collapse multi-line
    // content against the prefix column and look like whitespace was
    // stripped on submit.
    const lines = useMemo(
      () => (message.content ?? "").split("\n"),
      [message.content],
    );

    return (
      <Box flexDirection="column">
        {lines.map((line, index) => (
          <Box key={index} flexDirection="row">
            <Box width={prefixWidth}>
              {index === 0 && <Text color={Theme.accentUser}>{prefix}</Text>}
            </Box>
            <Box flexGrow={1}>
              <Text color={Theme.accentUser}>{line}</Text>
            </Box>
          </Box>
        ))}
      </Box>
    );
  },
  (prev, next) => prev.message.content === next.message.content,
);

const AssistantMessage = React.memo<{ message: Message }>(
  ({ message }) => {
    const prefix = "● ";
    const prefixWidth = prefix.length;

    // Use cached markdown renderer
    const renderedContent = useMemo(
      () => renderMarkdownCached(message.content),
      [message.content],
    );

    // Split by newlines to preserve line breaks in Ink
    const lines = useMemo(() => renderedContent.split("\n"), [renderedContent]);

    return (
      <Box flexDirection="column">
        {lines.map((line, index) => (
          <Box key={index} flexDirection="row">
            <Box width={prefixWidth}>
              {index === 0 && <Text color={Theme.accentAgent}>{prefix}</Text>}
            </Box>
            <Box flexGrow={1}>
              <Text color={Theme.accentAgent}>{line}</Text>
            </Box>
          </Box>
        ))}
      </Box>
    );
  },
  (prev, next) => prev.message.content === next.message.content,
);

/**
 * System events — compaction, resolved approvals, plan beats — share the
 * activity grammar: one dim row, truncated, skimmable or skippable.
 * (Previously these rendered zero rows: a silently compacted
 * conversation was invisible.)
 */
const SystemMessage = React.memo<{ message: Message }>(
  ({ message }) => {
    const firstLine = (message.content ?? "").split("\n")[0] ?? "";
    if (!firstLine.trim()) return null;
    return (
      <Box flexDirection="row">
        <Text>{ACTIVITY_INDENT}</Text>
        <Box flexGrow={1}>
          <Text color={Theme.textChrome} italic wrap="truncate-end">
            {firstLine}
          </Text>
        </Box>
      </Box>
    );
  },
  (prev, next) => prev.message.content === next.message.content,
);

const DIFF_CARD_FILES = 5;
const DIFF_CARD_PATH_MAX = 44;

function pathTail(path: string, max = DIFF_CARD_PATH_MAX): string {
  return path.length > max ? "…" + path.slice(-(max - 1)) : path;
}

// The run's receipt: what this turn changed, as a card instead of a
// log line. Diffstat headline, the first few files, and the breadcrumb
// to the Review tab (which opens on exactly this diff).
const RunDiffCard = React.memo<{ diff: ConversationRunDiff }>(
  ({ diff }) => {
    const shown = diff.files.slice(0, DIFF_CARD_FILES);
    const extra = diff.files.length - shown.length;
    // No gutter indent: the border occupies the glyph column, so the
    // card's interior text sits at the same column as every other
    // row's text (after "● ").
    return (
      <Box flexDirection="row">
        <Box
          flexDirection="column"
          alignSelf="flex-start"
          paddingX={1}
          borderStyle="round"
          borderColor={Theme.borderAmbient}
        >
          <Box>
            <Text color={Theme.textPrimary} bold>
              {diff.stats.files_changed} file
              {diff.stats.files_changed === 1 ? "" : "s"} edited
            </Text>
            <Text color={Theme.stateDone}>
              {"  "}+{diff.stats.additions}
            </Text>
            <Text color={Theme.stateFailed}> −{diff.stats.deletions}</Text>
          </Box>
          {shown.map((file) => (
            <Box key={file.path}>
              <Text color={changeColor(file.change_type)}>
                {changeGlyph(file.change_type)}{" "}
              </Text>
              <Text color={Theme.textSecondary}>{pathTail(file.path)}</Text>
              <Text color={Theme.stateDone}>
                {"  "}+{file.additions}
              </Text>
              <Text color={Theme.stateFailed}> −{file.deletions}</Text>
            </Box>
          ))}
          {extra > 0 && <Text color={Theme.textChrome}>… {extra} more</Text>}
          <Text color={Theme.textChrome} italic>
            tab review
          </Text>
        </Box>
      </Box>
    );
  },
  (prev, next) =>
    prev.diff.item_id === next.diff.item_id &&
    prev.diff.revision === next.diff.revision,
);

const ToolCallMessage = React.memo<{ message: Message }>(
  ({ message }) => {
    if (!message.toolCall) return null;

    const { tool, input, output, metadata } = message.toolCall;
    const prefix = "● ";
    const prefixWidth = prefix.length;

    // Memoize expensive operations
    const argsPreview = useMemo(
      () => (input ? formatToolArgumentsSmart(input, 60) : ""),
      [input],
    );

    const richMetadata = useMemo(
      () => (metadata ? parseRichMetadata(metadata) : null),
      [metadata],
    );

    // Handle cancelled tool calls
    if (output === "[Cancelled]") {
      return (
        <Box flexDirection="column">
          <Box flexDirection="row">
            <Box width={prefixWidth}>
              <Text color={Theme.accentAgent}>{prefix}</Text>
            </Box>
            <Box flexGrow={1}>
              <Text bold color={Theme.accentAgent}>
                {tool}
              </Text>
              <Text color={Theme.textChrome}> ({argsPreview})</Text>
            </Box>
          </Box>
          <Box>
            <Text color={Theme.stateBlocked} italic>
              {"  └─ Cancelled by user"}
            </Text>
          </Box>
        </Box>
      );
    }

    return (
      <Box flexDirection="column">
        <Box flexDirection="row">
          <Box width={prefixWidth}>
            <Text color={Theme.accentAgent}>{prefix}</Text>
          </Box>
          <Box flexGrow={1}>
            <Text bold color={Theme.accentAgent}>
              {tool}
            </Text>
            <Text color={Theme.textChrome}> ({argsPreview})</Text>
          </Box>
        </Box>
        {output && (
          <Box>
            {richMetadata ? (
              <RichToolOutput metadata={richMetadata} plainOutput={output} />
            ) : (
              <Text color={Theme.textSecondary}>
                {"  └─ "}
                {output.length > 200 ? output.slice(0, 200) + "..." : output}
              </Text>
            )}
          </Box>
        )}
      </Box>
    );
  },
  (prev, next) => {
    // Comprehensive equality check including metadata
    if (!prev.message.toolCall || !next.message.toolCall) {
      return prev.message.toolCall === next.message.toolCall;
    }

    const prevTool = prev.message.toolCall;
    const nextTool = next.message.toolCall;

    return (
      prevTool.call_id === nextTool.call_id &&
      prevTool.output === nextTool.output &&
      prevTool.running === nextTool.running &&
      JSON.stringify(prevTool.metadata) === JSON.stringify(nextTool.metadata)
    );
  },
);
