import { Flex, Spinner } from '@invoke-ai/ui-library';
import { memo } from 'react';

export const LoaderFullScreen = memo(() => {
  return (
    <Flex position="absolute" inset={0} alignItems="center" justifyContent="center">
      <Spinner size="xl" opacity={0.5} />
    </Flex>
  );
});
LoaderFullScreen.displayName = 'LoaderFullScreen';
