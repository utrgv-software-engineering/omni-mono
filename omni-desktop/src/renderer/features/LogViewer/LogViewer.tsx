import type { SystemStyleObject } from '@invoke-ai/ui-library';
import { Box, Flex, IconButton, Text } from '@invoke-ai/ui-library';
import Linkify from 'linkify-react';
import type { Opts as LinkifyOpts } from 'linkifyjs';
import type { PropsWithChildren } from 'react';
import { memo, useCallback } from 'react';
import { PiCaretDownBold } from 'react-icons/pi';
import { assert } from 'tsafe';
import type { StickToBottomOptions } from 'use-stick-to-bottom';
import { useStickToBottom } from 'use-stick-to-bottom';

import type { LogEntry, WithTimestamp } from '@/shared/types';

const getKey = (entry: WithTimestamp<LogEntry>, index: number) => `${entry.timestamp}-${index}`;

// Styles for log entries and links in them
const sx: SystemStyleObject = {
  '[data-level="debug"]': {
    color: 'invokeBlue.200',
    a: {
      color: 'invokeBlue.100',
    },
  },
  '[data-level="info"]': {
    color: 'base.200',
    a: {
      color: 'base.100',
    },
  },
  '[data-level="warn"]': {
    color: 'warning.200',
    a: {
      color: 'warning.100',
    },
  },
  '[data-level="error"]': {
    color: 'error.200',
    a: {
      color: 'error.100',
    },
  },
  a: {
    fontWeight: 'extrabold',
  },
  'a:hover': {
    textDecoration: 'underline',
  },
};

const stickToBottomOptions: StickToBottomOptions = {
  damping: 1.3,
};

export const LogViewer = memo(({ logs, children }: PropsWithChildren<{ logs: WithTimestamp<LogEntry>[] }>) => {
  const { scrollRef, contentRef, isAtBottom, scrollToBottom } = useStickToBottom(stickToBottomOptions);

  const onClickScrollToBottom = useCallback(() => {
    scrollToBottom();
  }, [scrollToBottom]);

  return (
    <Box position="relative" w="full" h="full" borderWidth={1} borderRadius="base">
      <Box position="absolute" ref={scrollRef} inset={2} overflow="auto">
        <Flex ref={contentRef} flexDir="column" sx={sx}>
          {logs.map((e, i) => {
            const k = getKey(e, i);
            switch (e.level) {
              case 'debug':
                return <LogEntryDebug key={k} entry={e} />;
              case 'info':
                return <LogEntryInfo key={k} entry={e} />;
              case 'warn':
                return <LogEntryWarn key={k} entry={e} />;
              case 'error':
                return <LogEntryError key={k} entry={e} />;
              default:
                assert(false, 'Invalid log level');
            }
          })}
        </Flex>
      </Box>
      {children}
      {!isAtBottom && (
        <IconButton
          variant="ghost"
          aria-label="Scroll to Bottom"
          icon={<PiCaretDownBold />}
          position="absolute"
          bottom={2}
          right={2}
          onClick={onClickScrollToBottom}
        />
      )}
    </Box>
  );
});
LogViewer.displayName = 'LogViewer';

const linkifyOptions: LinkifyOpts = {
  target: '_blank',
  rel: 'noopener noreferrer',
  validate: (value) => /^https?:\/\//.test(value),
};

const LogEntryDebug = ({ entry }: { entry: WithTimestamp<LogEntry> }) => {
  return (
    <Text as="pre" fontFamily='"JetBrainsMonoNerdFont"' color="invokeBlue.200" data-level="debug">
      <Linkify options={linkifyOptions}>{entry.message}</Linkify>
    </Text>
  );
};
const LogEntryInfo = ({ entry }: { entry: WithTimestamp<LogEntry> }) => {
  return (
    <Text as="pre" fontFamily='"JetBrainsMonoNerdFont"' color="base.200" data-level="info">
      <Linkify options={linkifyOptions}>{entry.message}</Linkify>
    </Text>
  );
};
const LogEntryWarn = ({ entry }: { entry: WithTimestamp<LogEntry> }) => {
  return (
    <Text as="pre" fontFamily='"JetBrainsMonoNerdFont"' color="warning.200" data-level="warn">
      <Linkify options={linkifyOptions}>{entry.message}</Linkify>
    </Text>
  );
};
const LogEntryError = ({ entry }: { entry: WithTimestamp<LogEntry> }) => {
  return (
    <Text as="pre" fontFamily='"JetBrainsMonoNerdFont"' color="error.200" data-level="error">
      <Linkify options={linkifyOptions}>{entry.message}</Linkify>
    </Text>
  );
};
