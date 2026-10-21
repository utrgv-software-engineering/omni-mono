import { describe, expect, it } from 'vitest';

import { configuredVoiceMode } from '@/lib/voice-mode';
import type { ModelsConfig } from '@/shared/types';

const models = (voiceDefault: string | null): ModelsConfig => ({
  version: 3,
  default: null,
  voice_default: voiceDefault,
  providers: {},
});

describe('configuredVoiceMode', () => {
  it('is off when neither is set', () => {
    expect(configuredVoiceMode(undefined, null)).toBe('off');
    expect(configuredVoiceMode(false, models(null))).toBe('off');
  });

  it('is hosted when a voice model is configured', () => {
    expect(configuredVoiceMode(false, models('codex/gpt-realtime-1.5'))).toBe('hosted');
  });

  it('is local when local voice is on, even if a voice model is set', () => {
    expect(configuredVoiceMode(true, models(null))).toBe('local');
    expect(configuredVoiceMode(true, models('codex/gpt-realtime'))).toBe('local');
  });
});
