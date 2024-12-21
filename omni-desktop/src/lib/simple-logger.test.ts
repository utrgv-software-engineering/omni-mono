import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { LogEntry, WithTimestamp } from '@/shared/types';

import { SimpleLogger } from './simple-logger';

describe('SimpleLogger', () => {
  let mockHandler: (entry: WithTimestamp<LogEntry>) => void;
  let logger: SimpleLogger;

  beforeEach(() => {
    mockHandler = vi.fn();
    logger = new SimpleLogger(mockHandler);
  });

  it('should log debug messages', () => {
    const message = 'debug message';
    logger.debug(message);
    expect(mockHandler).toHaveBeenCalledWith({
      level: 'debug',
      message,
      timestamp: expect.any(Number),
    });
  });

  it('should log info messages', () => {
    const message = 'info message';
    logger.info(message);
    expect(mockHandler).toHaveBeenCalledWith({
      level: 'info',
      message,
      timestamp: expect.any(Number),
    });
  });

  it('should log warn messages', () => {
    const message = 'warn message';
    logger.warn(message);
    expect(mockHandler).toHaveBeenCalledWith({
      level: 'warn',
      message,
      timestamp: expect.any(Number),
    });
  });

  it('should log error messages', () => {
    const message = 'error message';
    logger.error(message);
    expect(mockHandler).toHaveBeenCalledWith({
      level: 'error',
      message,
      timestamp: expect.any(Number),
    });
  });
});
