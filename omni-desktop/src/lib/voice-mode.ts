/**
 * Which voice mode the user configured, as one derivation.
 *
 * Settings → AI → Voice is a tri-state, but it is stored across two keys:
 * `localVoiceEnabled` (settings store) and `voice_default` (models.json), each cleared when the
 * other is set. A consumer reading only `localVoiceEnabled` cannot tell
 * "hosted" from "off" — which is how Off stopped turning voice off.
 */
import type { ModelsConfig } from '@/shared/types';

export type VoiceMode = 'hosted' | 'local' | 'off';

export function configuredVoiceMode(localVoiceEnabled: boolean | undefined, models: ModelsConfig | null): VoiceMode {
  if (localVoiceEnabled) {
    return 'local';
  }
  return models?.voice_default ? 'hosted' : 'off';
}
