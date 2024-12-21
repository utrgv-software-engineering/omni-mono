import type { SystemStyleObject } from '@invoke-ai/ui-library';
import { Box, Text } from '@invoke-ai/ui-library';
import Linkify from 'linkify-react';
import type { Opts as LinkifyOpts } from 'linkifyjs';
import { startCase } from 'lodash-es';

import { _afterEllipsisKeyframes } from '@/renderer/common/EllipsisLoadingText';
import type { Status } from '@/shared/types';

const sx: SystemStyleObject = {
  '&[data-active="true"]:after': _afterEllipsisKeyframes,
  a: {
    fontWeight: 'semibold',
  },
  'a:hover': {
    textDecoration: 'underline',
  },
};

const linkifyOptions: LinkifyOpts = {
  target: '_blank',
  rel: 'noopener noreferrer',
  validate: (value) => /^https?:\/\//.test(value),
};

type Props<T extends Status<string>> = {
  status: T;
  getIsActive: (status: T) => boolean;
  getMessage?: (status: T) => string;
};

export const LogViewerStatusIndicator = <T extends Status<string>>({
  status,
  getIsActive,
  getMessage = (status) => startCase(status.type),
}: Props<T>) => {
  return (
    <Box
      position="absolute"
      top={2}
      right={2}
      bg="base.900"
      borderRadius="base"
      userSelect="none"
      px={3}
      py={1}
      opacity={0.8}
      borderWidth={1}
      shadow="dark-lg"
    >
      <Text sx={sx} data-active={getIsActive(status)}>
        <Linkify options={linkifyOptions}>{getMessage(status)}</Linkify>
      </Text>
    </Box>
  );
};
