import { Spinner } from '@omniagents/ui/spinner';
import { memo } from 'react';

export const LoaderFullScreen = memo(() => {
  return (
    <div className="absolute inset-0 flex items-center justify-center">
      <Spinner className="opacity-50" />
    </div>
  );
});
LoaderFullScreen.displayName = 'LoaderFullScreen';
