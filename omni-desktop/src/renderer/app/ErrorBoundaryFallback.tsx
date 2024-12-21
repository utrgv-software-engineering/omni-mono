import { Button, Flex, Heading } from '@invoke-ai/ui-library';
import { memo } from 'react';
import type { FallbackProps } from 'react-error-boundary';
import { AssertionError } from 'tsafe';

const getMessage = (error: unknown) => {
  let errorMessage = '';
  if (error instanceof AssertionError) {
    errorMessage = error.originalMessage ?? error.message;
  } else if (error instanceof Error) {
    errorMessage = error.message;
  }
  return errorMessage || 'An unknown error occurred.';
};

export const ErrorBoundaryFallback = memo(({ error, resetErrorBoundary }: FallbackProps) => {
  return (
    <Flex flexDir="column" w="full" h="full" alignItems="center" justifyContent="center" gap={4}>
      <Heading>An error occurred.</Heading>
      <Heading size="sm" color="error.300">
        Error: {getMessage(error)}
      </Heading>
      <Button onClick={resetErrorBoundary} colorScheme="invokeYellow" mt={8}>
        Reset
      </Button>
    </Flex>
  );
});
ErrorBoundaryFallback.displayName = 'ErrorBoundaryFallback';
