import type { ButtonProps } from '@invoke-ai/ui-library';
import { Box, Button } from '@invoke-ai/ui-library';

export const ButtonWithTruncatedLabel = ({ children, ...buttonProps }: ButtonProps) => {
  return (
    <Button {...buttonProps}>
      <Box
        as="span"
        noOfLines={1}
        overflow="hidden"
        wordBreak="break-all"
        textOverflow="ellipsis"
        whiteSpace="break-spaces"
      >
        {children}
      </Box>
    </Button>
  );
};
