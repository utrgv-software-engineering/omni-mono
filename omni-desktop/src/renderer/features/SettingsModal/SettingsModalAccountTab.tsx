import { memo } from 'react';

import { Card, CardContent } from '@/renderer/ds/ui/card';
import { Field, FieldContent, FieldDescription, FieldLabel } from '@/renderer/ds/ui/field';
import { SettingsPane, SettingsSection } from '@/renderer/features/SettingsModal/SettingsLayout';
import { isElectron } from '@/renderer/services/ipc';

export const SettingsModalAccountTab = memo(() => (
  <SettingsPane>
    <SettingsSection title="Profile">
      <Card>
        <CardContent>
          <Field>
            <FieldContent>
              <FieldLabel>{isElectron ? 'Local profile' : 'Server profile'}</FieldLabel>
              <FieldDescription>
                {isElectron
                  ? 'No account is required. Your projects and settings are stored on this computer.'
                  : 'No account is required. Your projects and settings are stored on this server.'}
              </FieldDescription>
            </FieldContent>
          </Field>
        </CardContent>
      </Card>
    </SettingsSection>
  </SettingsPane>
));
SettingsModalAccountTab.displayName = 'SettingsModalAccountTab';
