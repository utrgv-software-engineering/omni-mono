import { Checkbox, Flex, FormControl, FormHelperText, FormLabel } from '@invoke-ai/ui-library';
import { useStore } from '@nanostores/react';
import { memo, useCallback } from 'react';

import { persistedStoreApi } from '@/renderer/services/store';

export const SettingsModalServerMode = memo(() => {
  const { serverMode } = useStore(persistedStoreApi.$atom);
  const onChange = useCallback(() => {
    persistedStoreApi.setKey('serverMode', !persistedStoreApi.$atom.get().serverMode);
  }, []);

  return (
    <FormControl orientation="vertical">
      <Flex w="full" alignItems="center" justifyContent="space-between">
        <FormLabel>Server Mode</FormLabel>
        <Checkbox isChecked={serverMode} onChange={onChange} />
      </Flex>
      <FormHelperText>
        When enabled, the UI won&apos;t open when the app starts up. You must open the displayed URL in a web browser.
      </FormHelperText>
    </FormControl>
  );
});
SettingsModalServerMode.displayName = 'SettingsModalServerMode';
