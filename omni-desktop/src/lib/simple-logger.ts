import type { LogEntry, WithTimestamp } from '@/shared/types';

export class SimpleLogger {
  private handler: (entry: WithTimestamp<LogEntry>) => void;

  constructor(handler: (entry: WithTimestamp<LogEntry>) => void) {
    this.handler = handler;
  }

  debug(message: string) {
    this.handler({ level: 'debug', message, timestamp: Date.now() });
  }
  info(message: string) {
    this.handler({ level: 'info', message, timestamp: Date.now() });
  }
  warn(message: string) {
    this.handler({ level: 'warn', message, timestamp: Date.now() });
  }
  error(message: string) {
    this.handler({ level: 'error', message, timestamp: Date.now() });
  }
}
