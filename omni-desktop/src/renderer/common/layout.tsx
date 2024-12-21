import type { FlexProps } from '@invoke-ai/ui-library';
import { Flex } from '@invoke-ai/ui-library';
import { memo } from 'react';

/**
 * All content should use this value for the CSS property to ensure consistent spacing.
 * This is a theme spacing token.
 */
const HEADER_HEIGHT = 48;

/**
 * All footers should use this value for the CSS property to ensure consistent spacing.
 * This is a theme spacing token.
 */
const FOOTER_HEIGHT = 10;

export const BodyContainer = memo((props: FlexProps) => {
  return (
    <Flex
      position="relative"
      w="full"
      h="full"
      flexDir="column"
      alignItems="center"
      p={4}
      gap={4}
      minH={0}
      {...props}
    />
  );
});
BodyContainer.displayName = 'BodyContainer';

export const BodyHeader = memo((props: FlexProps) => {
  return <Flex w="full" h={HEADER_HEIGHT} flexDir="column" alignItems="center" gap={4} {...props} />;
});
BodyHeader.displayName = 'BodyHeader';

export const BodyContent = memo((props: FlexProps) => {
  return <Flex w="full" h="full" flexDir="column" alignItems="center" gap={4} minH={0} {...props} />;
});
BodyContent.displayName = 'BodyContent';

export const BodyFooter = memo((props: FlexProps) => {
  return <Flex w="full" h={FOOTER_HEIGHT} alignItems="center" justifyContent="flex-end" gap={4} {...props} />;
});
BodyFooter.displayName = 'BodyFooter';
