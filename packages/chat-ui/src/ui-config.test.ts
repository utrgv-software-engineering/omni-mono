import { describe, expect, it } from 'vitest';

import { resolveUiConfig } from './ui-config';

const resolve = (baseUrl: string) => resolveUiConfig({ baseUrl });

describe('resolveUiConfig', () => {
  it('reaches a runtime on its own port at the root', () => {
    const config = resolve('http://127.0.0.1:9000/?token=t');
    expect(config.basePath).toBe('');
    expect(config.wsBaseUrl).toBe('ws://127.0.0.1:9000/ws');
    expect(config.token).toBe('t');
  });

  it('keeps the desktop proxy prefix', () => {
    const config = resolve('http://localhost:3002/proxy/chat-uiUrl/');
    expect(config.wsBaseUrl).toBe('ws://localhost:3002/proxy/chat-uiUrl/ws');
    expect(config.wsRealtimeUrl).toBe('ws://localhost:3002/proxy/chat-uiUrl/ws/realtime');
    expect(resolve('http://localhost:3002/proxy/chat-uiUrl/index.html').basePath).toBe('/proxy/chat-uiUrl');
  });

  it('reaches a runtime mounted inside another server', () => {
    const config = resolve('https://studio.example/api/playground/agents/main/');
    expect(config.wsBaseUrl).toBe('wss://studio.example/api/playground/agents/main/ws');
    expect(config.resolvePath('/ws/terminal')).toBe('/api/playground/agents/main/ws/terminal');
    expect(config.resolvePath('/api/playground/agents/main/ws/terminal')).toBe(
      '/api/playground/agents/main/ws/terminal'
    );
  });
});

it('can disable an unavailable realtime endpoint without disabling chat', () => {
  const config = resolveUiConfig({ baseUrl: 'https://example.test/personal/?minimal=true', realtime: false });
  expect(config.wsRealtimeUrl).toBe('');
  expect(config.wsBaseUrl).toBe('wss://example.test/personal/ws');
  expect(config.minimal).toBe(true);
});
