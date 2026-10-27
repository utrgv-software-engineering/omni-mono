import { type ChatHost, ChatHostProvider } from '@omniagents/chat-ui/host';
import type { ReactNode } from 'react';
import type { Root } from 'react-dom/client';

/** A host with no voice, no routines and silent notices, for component tests. */
export function testChatHost(overrides: Partial<ChatHost> = {}): ChatHost {
  return {
    notifyError: () => {},
    voiceMode: 'off',
    audioSettings: () => ({
      inputDeviceId: null,
      outputDeviceId: null,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    }),
    ...overrides,
  };
}

/** Renders everything given to `root` inside a `ChatHostProvider`. */
export function withChatHost(root: Root, host: ChatHost = testChatHost()): Root {
  return {
    render: (children: ReactNode) => root.render(<ChatHostProvider host={host}>{children}</ChatHostProvider>),
    unmount: () => root.unmount(),
  };
}
