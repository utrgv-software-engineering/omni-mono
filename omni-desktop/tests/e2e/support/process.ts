import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import { execFileSync, spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';

const delay = (ms: number) =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

const tail = (value: string, lineCount = 120) => value.split('\n').slice(-lineCount).join('\n');

function signalProcessTree(child: ChildProcessWithoutNullStreams, signal: NodeJS.Signals): void {
  try {
    if (process.platform === 'win32' || child.pid === undefined) {
      child.kill(signal);
    } else {
      process.kill(-child.pid, signal);
    }
  } catch (err) {
    const code = err instanceof Error && 'code' in err ? (err as NodeJS.ErrnoException).code : undefined;
    if (code !== 'ESRCH') {
      throw err;
    }
  }
}

/** Abruptly stop only this launched application's process tree. */
export function crashProcessTree(pid: number): void {
  if (process.platform === 'win32') {
    execFileSync('taskkill', ['/pid', String(pid), '/T', '/F']);
    return;
  }
  const parents = execFileSync('ps', ['-eo', 'pid=,ppid='], { encoding: 'utf8' })
    .trim()
    .split('\n')
    .map((line) => line.trim().split(/\s+/).map(Number));
  const targets = new Set([pid]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const [child, parent] of parents) {
      if (targets.has(parent) && !targets.has(child)) {
        targets.add(child);
        changed = true;
      }
    }
  }
  const signal = (target: number, value: NodeJS.Signals) => {
    try {
      process.kill(target, value);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ESRCH') {
        throw error;
      }
    }
  };
  // Freeze first so a dying child cannot trigger a graceful parent finalizer.
  for (const target of targets) {
    signal(target, 'SIGSTOP');
  }
  for (const target of targets) {
    signal(target, 'SIGKILL');
  }
}

/** `pid` and its live descendants, via /proc. Linux only. */
export function processTree(pid: number): number[] {
  const found = new Set<number>();
  const pending = [pid];
  while (pending.length) {
    const next = pending.pop()!;
    if (found.has(next)) {
      continue;
    }
    try {
      const children = readFileSync(`/proc/${next}/task/${next}/children`, 'utf8').trim().split(/\s+/);
      found.add(next);
      pending.push(...children.filter(Boolean).map(Number));
    } catch {
      /* A process may exit during the snapshot. */
    }
  }
  return [...found];
}

export function commandLine(pid: number): string[] {
  try {
    return readFileSync(`/proc/${pid}/cmdline`, 'utf8').split('\0').filter(Boolean);
  } catch {
    return [];
  }
}

/**
 * The `omni serve` hosts a launcher spawned. The desktop always passes
 * `--exit-on-stdin-close`, so the flag identifies them unambiguously.
 */
export function runtimePidsUnder(root: number): number[] {
  return processTree(root).filter((pid) => commandLine(pid).includes('--exit-on-stdin-close'));
}

export type ManagedProcess = {
  readonly pid: number | undefined;
  crashDescendant: (pid: number) => void;
  logs: () => string;
  stop: (signal?: NodeJS.Signals) => Promise<void>;
  waitForOutput: (pattern: RegExp, timeoutMs: number) => Promise<void>;
};

export function startProcess(input: {
  command: string;
  args: string[];
  cwd: string;
  env?: NodeJS.ProcessEnv;
  forwardOutput?: boolean;
}): ManagedProcess {
  const child = spawn(input.command, input.args, {
    cwd: input.cwd,
    env: { ...process.env, ...input.env },
    detached: process.platform !== 'win32',
    shell: process.platform === 'win32',
    stdio: ['ignore', 'pipe', 'pipe'],
  }) as ChildProcessWithoutNullStreams;
  let output = '';

  child.stdout.on('data', (chunk: Buffer) => {
    output += chunk.toString('utf-8');
    if (input.forwardOutput) {
      process.stdout.write(chunk);
    }
  });
  child.stderr.on('data', (chunk: Buffer) => {
    output += chunk.toString('utf-8');
    if (input.forwardOutput) {
      process.stderr.write(chunk);
    }
  });

  return {
    pid: child.pid,
    crashDescendant: (pid) => {
      const parents = new Map(
        execFileSync('ps', ['-eo', 'pid=,ppid='], { encoding: 'utf8' })
          .trim()
          .split('\n')
          .map((line) => line.trim().split(/\s+/).map(Number) as [number, number])
      );
      let ancestor = parents.get(pid);
      const seen = new Set<number>();
      while (ancestor && ancestor !== child.pid && !seen.has(ancestor)) {
        seen.add(ancestor);
        ancestor = parents.get(ancestor);
      }
      if (!child.pid || ancestor !== child.pid) {
        throw new Error('Refusing to crash a process outside this test launcher');
      }
      process.kill(pid, 'SIGKILL');
    },
    logs: () => output,
    stop: async (signal = 'SIGTERM') => {
      if (child.exitCode !== null || child.signalCode !== null) {
        return;
      }
      signalProcessTree(child, signal);
      await Promise.race([
        new Promise<void>((resolve) => {
          child.once('exit', () => resolve());
        }),
        delay(5_000).then(() => {
          if (child.exitCode === null && child.signalCode === null) {
            signalProcessTree(child, 'SIGKILL');
          }
        }),
      ]);
    },
    waitForOutput: async (pattern: RegExp, timeoutMs: number) => {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        if (pattern.test(output)) {
          return;
        }
        await delay(250);
      }
      throw new Error(`Timed out waiting for ${pattern}. Recent output:\n${tail(output)}`);
    },
  };
}

export async function killTcpPort(port: string): Promise<void> {
  if (process.platform === 'win32') {
    return;
  }

  for (const signal of ['-TERM', '-KILL']) {
    try {
      execFileSync('fuser', ['-k', signal, `${port}/tcp`], { stdio: 'ignore' });
    } catch (err) {
      void err;
    }
    await delay(500);
  }
}

export async function waitForHttpOk(url: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastError: unknown;

  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.status >= 200 && response.status < 500) {
        return;
      }
      lastError = new Error(`HTTP ${response.status}`);
    } catch (err) {
      lastError = err;
    }
    await delay(500);
  }

  throw new Error(
    `Timed out waiting for ${url}: ${lastError instanceof Error ? lastError.message : String(lastError)}`
  );
}
