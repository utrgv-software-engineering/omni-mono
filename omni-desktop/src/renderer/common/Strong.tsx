import type { TextProps } from '@invoke-ai/ui-library';
import { Text } from '@invoke-ai/ui-library';
import type { PropsWithChildren } from 'react';

export const Strong = ({ children, ...textProps }: PropsWithChildren<TextProps>) => {
  return (
    <Text fontSize="md" as="span" fontWeight="semibold" {...textProps}>
      {children}
    </Text>
  );
};
