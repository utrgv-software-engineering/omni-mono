import { describe, expect, it, vi } from 'vitest';

import { createRuntimeTerminal, type TerminalSocket, terminalSocketUrl, TerminalStream } from './terminal';

type Listener = (event?: any) => void;

class FakeSocket implements TerminalSocket {
  readyState = 0;
  sent: Record<string, unknown>[] = [];
  closed = false;
  private listeners = new Map<string, Listener[]>();

  addEventListener(type: string, listener: Listener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  send(data: string): void {
    this.sent.push(JSON.parse(data));
  }
  close(): void {
    this.closed = true;
  }
  emit(type: string, event?: unknown): void {
    if (type === 'open') {
      this.readyState = 1;
    }
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}

const attach = { sessionId: 's1', terminalId: 't1', terminalToken: 'tok', path: '/ws/terminal' };
const frame = (bytes: number[]) => JSON.stringify({ type: 'output', data: btoa(String.fromCharCode(...bytes)) });

describe('TerminalStream', () => {
  it('attaches first, then sends UTF-8 input and resizes', () => {
    const socket = new FakeSocket();
    const stream = new TerminalStream(socket, attach, { onOutput: vi.fn(), onExit: vi.fn() });
    stream.write('dropped before open');
    socket.emit('open');
    stream.write('é');
    stream.resize(100, 30);
    expect(socket.sent).toEqual([
      { type: 'attach', session_id: 's1', terminal_id: 't1', terminal_token: 'tok' },
      { type: 'input', data: btoa('\xc3\xa9') },
      { type: 'resize', cols: 100, rows: 30 },
    ]);
  });

  it('decodes a character split across output frames', () => {
    const socket = new FakeSocket();
    const output: string[] = [];
    new TerminalStream(socket, attach, { onOutput: (text) => output.push(text), onExit: vi.fn() });
    socket.emit('open');
    // "✓" is e2 9c 93.
    socket.emit('message', { data: frame([0x6f, 0x6b, 0xe2]) });
    socket.emit('message', { data: frame([0x9c, 0x93]) });
    expect(output.join('')).toBe('ok✓');
  });

  it('reports the shell exit once', () => {
    const socket = new FakeSocket();
    const onExit = vi.fn();
    new TerminalStream(socket, attach, { onOutput: vi.fn(), onExit });
    socket.emit('open');
    socket.emit('message', { data: JSON.stringify({ type: 'exit', code: 3 }) });
    socket.emit('close', { code: 1000 });
    expect(onExit).toHaveBeenCalledTimes(1);
    expect(onExit).toHaveBeenCalledWith(3);
    expect(socket.closed).toBe(true);
  });

  it('reports a rejected socket as a failed exit', () => {
    const socket = new FakeSocket();
    const onExit = vi.fn();
    new TerminalStream(socket, attach, { onOutput: vi.fn(), onExit });
    socket.emit('close', { code: 4401 });
    expect(onExit).toHaveBeenCalledWith(1);
  });

  it('closes the terminal without reporting an exit', () => {
    const socket = new FakeSocket();
    const onExit = vi.fn();
    const stream = new TerminalStream(socket, attach, { onOutput: vi.fn(), onExit });
    socket.emit('open');
    stream.close();
    socket.emit('close', { code: 1000 });
    expect(socket.sent.at(-1)).toEqual({ type: 'close' });
    expect(socket.closed).toBe(true);
    expect(onExit).not.toHaveBeenCalled();
  });
});

describe('createRuntimeTerminal', () => {
  it('creates a terminal in the session and environment', async () => {
    const serverCall = vi.fn().mockResolvedValue({ terminal_id: 't1', terminal_token: 'tok', cwd: '/w' });
    const target = { workspaceId: 'w', environmentId: 'e', environmentGeneration: 2 };
    await expect(createRuntimeTerminal(serverCall, { sessionId: 's1', target, cols: 80, rows: 24 })).resolves.toEqual({
      sessionId: 's1',
      terminalId: 't1',
      terminalToken: 'tok',
      path: '/ws/terminal',
      cwd: '/w',
    });
    expect(serverCall).toHaveBeenCalledWith('terminal.create', { cols: 80, rows: 24 }, 's1', target);
  });

  it('rejects a reply without terminal credentials', async () => {
    const serverCall = vi.fn().mockResolvedValue({ terminal_id: 't1' });
    await expect(createRuntimeTerminal(serverCall, { sessionId: 's1', cols: 80, rows: 24 })).rejects.toThrow(
      'usable terminal'
    );
  });
});

describe('terminalSocketUrl', () => {
  it('keeps the host and drops the query', () => {
    expect(terminalSocketUrl('wss://h:1/ws?ticket=a', '/ws/terminal')).toBe('wss://h:1/ws/terminal');
  });
});
