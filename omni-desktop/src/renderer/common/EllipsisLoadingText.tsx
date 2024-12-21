import type { SystemStyleObject, TextProps } from '@invoke-ai/ui-library';
import { keyframes, Text } from '@invoke-ai/ui-library';

const ellipsisKeyframes = keyframes`
    0%  { clip-path: inset(0 100% 0 0); }
    25% { clip-path: inset(0 66.6% 0 0); }
    50% { clip-path: inset(0 33.3% 0 0); }
    75% { clip-path: inset(0 0 0 0); }`;

export const _afterEllipsisKeyframes: SystemStyleObject = {
  display: 'inline-block',
  animation: `${ellipsisKeyframes} 1s steps(1,end) infinite`,
  content: '"…"',
};

export const EllipsisLoadingText = (props: TextProps) => {
  return <Text _after={_afterEllipsisKeyframes} {...props} />;
};
