/**
 * Hook owning one hosted-voice session per agent surface (chat column / DM).
 *
 * Wraps `RealtimeVoiceController` with this surface's connection config from
 * UiConfig (`/ws/realtime` + token), so the same hook pairs correctly whether
 * it runs in the chat App or a resident's DM (`useUiConfig().session` is the
 * default pairing when the caller doesn't pass an explicit session id).
 */
import { useChatHost } from '@omniagents/chat-ui/host';
import { RealtimeVoiceController } from '@omniagents/chat-ui/services/realtime-voice';
import { useUiConfig } from '@omniagents/chat-ui/ui-config';
import { useCallback, useEffect, useState } from 'react';

export type RealtimeVoice = {
  actor: RealtimeVoiceController['actor'];
  /** Open a session paired to `sessionId` (falls back to the surface's handoff session). */
  open: (sessionId?: string) => void;
  close: () => void;
  toggleMute: () => void;
  interrupt: () => void;
  /** Route a typed message into the live voice session. False when not live. */
  sendText: (text: string) => boolean;
};

export function useRealtimeVoice(): RealtimeVoice {
  const { wsRealtimeUrl, token, debug, session } = useUiConfig();
  const { audioSettings } = useChatHost();
  const [controller] = useState(() => new RealtimeVoiceController());

  useEffect(() => () => controller.dispose(), [controller]);

  const open = useCallback(
    (sessionId?: string) => {
      controller.open({ url: wsRealtimeUrl, token, debug, audioSettings: audioSettings() }, sessionId ?? session);
    },
    [controller, wsRealtimeUrl, token, debug, session, audioSettings]
  );

  const close = useCallback(() => controller.close(), [controller]);
  const toggleMute = useCallback(() => controller.toggleMute(), [controller]);
  const interrupt = useCallback(() => controller.interrupt(), [controller]);
  const sendText = useCallback((text: string) => controller.sendText(text), [controller]);

  return { actor: controller.actor, open, close, toggleMute, interrupt, sendText };
}
